# -*- coding: utf-8 -*-
"""MySQL 访问层：连接（含超时/会话时区）、极简连接池、游标封装。

关于 `execute()` 返回值（原实现未说明，导致 ETL 日志把行数记成 2 倍）：
    pymysql 的 `cursor.rowcount` = MySQL 的 affected_rows。在
    `INSERT ... ON DUPLICATE KEY UPDATE` 下语义是「新增计 1、更新计 2、未变计 0」，
    **不等于写入行数**。需要真实行数时用 `table_count()` 前后取差，
    或使用 `write_stats()`（返回 inserted/updated/affected）。
"""
import queue
import threading
from contextlib import contextmanager

import pymysql
from pymysql.cursors import DictCursor


def connect(cfg, autocommit=False, database=None):
    """新建一条连接（ETL 用；API 走 Pool）。

    database=None 时不预选库：ETL 首次运行要靠 schema.sql 里的
    `CREATE DATABASE IF NOT EXISTS` 建库，预选库会直接连不上。
    """
    conn = pymysql.connect(
        host=cfg.db_host, port=cfg.db_port, user=cfg.db_user,
        password=cfg.db_password, charset="utf8mb4", autocommit=autocommit,
        database=database,
        connect_timeout=getattr(cfg, "db_connect_timeout", 5),
        read_timeout=getattr(cfg, "db_read_timeout", 20),
        write_timeout=getattr(cfg, "db_write_timeout", 20),
    )
    # 会话时区与 trade_date 语义（Asia/Shanghai）保持一致。
    # 用数字偏移量（+08:00）而非命名时区，无需服务器装载 tz 表。
    tz = getattr(cfg, "session_time_zone", None)
    if tz:
        try:
            with conn.cursor() as cur:
                cur.execute("SET time_zone = %s", (tz,))
        except Exception:  # noqa: BLE001  权限不足时不影响主流程，仅由调用方计时区
            pass
    return conn


class Pool:
    """极简线程安全连接池（LIFO，上限 max_size，借出前 ping 探活）。

    目的：修掉「每请求新建连接、无 connect/read timeout」——DB 亚健康时
    单请求会无限阻塞并拖垮 uvicorn 工作线程。
    """

    def __init__(self, cfg, max_size=8, wait_timeout=10):
        self._cfg = cfg
        self._max = max(1, int(max_size))
        self._wait = wait_timeout
        self._q = queue.LifoQueue(maxsize=self._max)
        self._lock = threading.Lock()
        self._size = 0
        self._closed = False

    # ---- 统计（供健康检查/测试观察）----
    @property
    def size(self):
        with self._lock:
            return self._size

    @property
    def idle(self):
        return self._q.qsize()

    def acquire(self):
        if self._closed:
            raise RuntimeError("connection pool is closed")
        while True:
            try:
                conn = self._q.get_nowait()
            except queue.Empty:
                with self._lock:
                    can_create = self._size < self._max and not self._closed
                    if can_create:
                        self._size += 1
                if can_create:
                    try:
                        return connect(self._cfg, database=self._cfg.db_name)
                    except Exception:
                        with self._lock:
                            self._size -= 1
                        raise
                try:
                    conn = self._q.get(timeout=self._wait)
                except queue.Empty:
                    raise RuntimeError("connection pool exhausted (timeout %ss)" % self._wait)
            # 探活：坏连接丢弃后重试；ping(reconnect=True) 不会在事务中途自动重连
            try:
                conn.ping(reconnect=True)
                return conn
            except Exception:  # noqa: BLE001
                self._discard(conn)

    def release(self, conn):
        if conn is None:
            return
        try:
            conn.rollback()          # 清理未提交事务，避免脏状态回池
        except Exception:  # noqa: BLE001
            self._discard(conn)
            return
        try:
            self._q.put_nowait(conn)
        except queue.Full:           # 理论上不可达（size 上限 == 队列容量）
            self._discard(conn)

    def _discard(self, conn):
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
        with self._lock:
            self._size = max(0, self._size - 1)

    def close_all(self):
        self._closed = True
        while True:
            try:
                self._discard(self._q.get_nowait())
            except queue.Empty:
                break

    @contextmanager
    def borrow(self):
        conn = self.acquire()
        try:
            yield conn
        finally:
            self.release(conn)


_pool = None
_pool_lock = threading.Lock()


def get_pool(cfg, max_size=None):
    """进程内单例连接池（懒建）。"""
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                _pool = Pool(cfg, max_size or getattr(cfg, "db_pool_size", 8))
    return _pool


def reset_pool():
    """测试用：丢弃单例池。"""
    global _pool
    with _pool_lock:
        if _pool is not None:
            _pool.close_all()
        _pool = None


# ───────────────────────── 游标封装 ─────────────────────────
def execute(conn, sql, args=None):
    """执行写 SQL，返回 affected_rows（语义见模块 docstring，不是写入行数）。"""
    with conn.cursor() as cur:
        cur.execute(sql, args)
        return cur.rowcount


def executemany(conn, sql, rows):
    """批量写，返回 affected_rows（语义同上）。"""
    with conn.cursor() as cur:
        cur.executemany(sql, rows)
        return cur.rowcount


def query(conn, sql, args=None):
    with conn.cursor() as cur:
        cur.execute(sql, args)
        return cur.fetchall()


def query_one(conn, sql, args=None):
    with conn.cursor() as cur:
        cur.execute(sql, args)
        return cur.fetchone()


def query_dict(conn, sql, args=None):
    """返回 list[dict]，键为列名（只读查询用）"""
    with conn.cursor(DictCursor) as cur:
        cur.execute(sql, args)
        return cur.fetchall()


def table_count(conn, table):
    """真实行数（用于把 ODKU 的 affected_rows 换算成 inserted/updated）。"""
    if not table.replace("_", "").isalnum():
        raise ValueError("illegal table name: %r" % (table,))
    return query_one(conn, "SELECT COUNT(*) FROM `%s`" % table)[0]


def write_stats(conn, table, sql, args=None):
    """执行一条 ODKU 写语句，返回 (inserted, updated, affected)。

    inserted = 执行后行数 - 执行前行数；updated = affected - inserted。
    注意：只适用于「不会删行」的语句，且 COUNT(*) 有成本，仅用于聚合这类
    低频大批量语句。
    """
    before = table_count(conn, table)
    affected = execute(conn, sql, args)
    after = table_count(conn, table)
    inserted = max(0, after - before)
    updated = max(0, affected - inserted)
    return inserted, updated, affected
