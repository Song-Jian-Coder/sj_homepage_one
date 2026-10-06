# -*- coding: utf-8 -*-
"""管理接口支撑：采集互斥、日志尾部读取、ETL 触发。"""
import os
import threading
import time
from collections import deque

import db
from app.deps import cfg


class FetchGate:
    """采集互斥闸门（进程内）。

    修掉审查报告 5.1.2：原实现把锁 acquire() 之后立刻在 finally 里 release()，
    锁只覆盖「创建线程」一瞬，并发两次 POST 会同时跑两个 ETL，
    并互相截断同一份 etl_run.log（Logger 用 "w" 打开）。

    现在改为：请求线程原子地「检查 + 置位」，由 worker 在 finally 里复位。
    跨进程场景另有 t_fetch_log 的 running 行兜底（见 db_fetch_running）。
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._event = threading.Event()

    @property
    def running(self):
        return self._event.is_set()

    def acquire(self):
        """原子地占用；已被占用返回 False。"""
        with self._lock:
            if self._event.is_set():
                return False
            self._event.set()
            return True

    def release(self):
        with self._lock:
            self._event.clear()


fetch_gate = FetchGate()


def db_fetch_running(conn, stale_minutes):
    """跨进程兜底：审计表里是否还有「未结束的采集」。

    stale_minutes 用于忽略 API 进程崩溃后残留的 status='running' 孤儿行。
    """
    row = db.query_one(conn, (
        "SELECT id, task_name, start_at FROM t_fetch_log "
        "WHERE status = 'running' "
        "  AND start_at > DATE_SUB(NOW(), INTERVAL %s MINUTE) "
        "ORDER BY id DESC LIMIT 1"), (int(stale_minutes),))
    return row


def read_log_tail(path, lines, offset=None):
    """读取 ETL 日志。

    - offset=None：返回最后 lines 行（管理台默认视图）；
    - offset=N  ：返回第 N 行起的 lines 行（1-based，供前端增量拉取，避免
      「200 行后冻结」；见审查报告 6.1 P2）。

    单次遍历完成「计数 + 取样」——原实现把整个文件读两遍（取尾部一遍、逐行计数一遍）。
    """
    if not os.path.exists(path):
        return {"exists": False, "total": 0, "mtime": None, "lines": []}
    total = 0
    tail = deque(maxlen=lines)
    window = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for ln in f:
            total += 1
            if offset is None:
                tail.append(ln.rstrip("\n"))
            elif offset <= total < offset + lines:
                window.append(ln.rstrip("\n"))
    return {
        "exists": True,
        "total": total,
        "mtime": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(os.path.getmtime(path))),
        "lines": list(tail) if offset is None else window,
    }


def run_etl_task(task):
    """在工作线程里跑一次 ETL。测试通过 monkeypatch 本函数避免真实采集。"""
    import etl
    return etl.main([task])


def spawn_worker(task, on_done=None):
    """启动采集线程；线程结束时释放闸门。"""
    def _target():
        try:
            run_etl_task(task)
        except Exception:  # noqa: BLE001  ETL 自身已记日志，这里只保证闸门一定释放
            import traceback
            from app.deps import logger
            logger().error("FETCH_WORKER_CRASH task=%s\n%s", task, traceback.format_exc())
        finally:
            fetch_gate.release()
            if on_done is not None:
                on_done()

    th = threading.Thread(target=_target, daemon=True, name="etl-%s" % task)
    th.start()
    return th


def log_gate_state():
    from app.deps import logger
    logger().info("FETCH_GATE running=%s", fetch_gate.running)


__all__ = ["fetch_gate", "db_fetch_running", "read_log_tail", "run_etl_task",
           "spawn_worker", "cfg"]
