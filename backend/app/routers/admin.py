# -*- coding: utf-8 -*-
"""管理接口（全部需要 X-Admin-Key；未配置 ADMIN_KEY 时一律拒绝）。"""
from typing import Optional

from fastapi import APIRouter, Body, Depends, Header, Query, Request

from app import deps
from app.deps import (CODE_BAD_PARAM, CODE_FETCH_RUNNING, admin_guard, cfg, err,
                      logger, ok)
from app.schemas import AdminFetchData, AdminLogData, Envelope
from app.services import admin as svc

router = APIRouter(tags=["管理"], prefix="/admin")

VALID_TASKS = ("all", "meta", "members", "moneyflow", "aggregate", "incremental")


@router.post("/fetch", response_model=Envelope[AdminFetchData], summary="触发一次采集")
def trigger_fetch(
    request: Request,
    x_admin_key: str = Header(""),
    task: str = Body("all", embed=True),
    conn=Depends(deps.get_conn),
):
    """异步触发 ETL，立即返回 accepted。

    并发护栏（审查报告 5.1.2，已修）：
    1. 进程内 `FetchGate`（请求线程原子占用，worker 结束时释放）；
    2. 跨进程兜底：`t_fetch_log` 中仍有未结束的 running 行则拒绝
       （stale 超过 FETCH_STALE_MINUTES 的孤儿行会被忽略）。
    """
    e = admin_guard(request, x_admin_key)
    if e:
        return e
    if task not in VALID_TASKS:
        return err(CODE_BAD_PARAM,
                   "unknown task: %s（可选 %s）" % (task, "/".join(VALID_TASKS)))

    stale = svc.db_fetch_running(conn, cfg.fetch_stale_minutes)
    if stale:
        logger().warning("FETCH_DENY trace=%s reason=db_running id=%s task=%s start_at=%s",
                         deps.cur_trace_id(), stale[0], stale[1], stale[2])
        return err(CODE_FETCH_RUNNING,
                   "fetch already running (audit id=%s task=%s start_at=%s)"
                   % (stale[0], stale[1], stale[2]))

    if not svc.fetch_gate.acquire():
        logger().warning("FETCH_DENY trace=%s reason=in_process_gate", deps.cur_trace_id())
        return err(CODE_FETCH_RUNNING, "fetch already running")

    try:
        svc.spawn_worker(task)
    except Exception:  # noqa: BLE001  线程创建失败必须放开闸门，否则永久拒服务
        svc.fetch_gate.release()
        raise
    logger().info("FETCH_ACCEPT trace=%s task=%s", deps.cur_trace_id(), task)
    return ok({"task": task, "status": "accepted"})


@router.get("/log", response_model=Envelope[AdminLogData], summary="ETL 日志尾部")
def admin_log(
    request: Request,
    x_admin_key: str = Header(""),
    lines: int = Query(200, ge=1, le=2000),
    offset: Optional[int] = Query(None, ge=1, description="从第几行开始取（1-based，增量拉取用）"),
):
    e = admin_guard(request, x_admin_key)
    if e:
        return e
    return ok(svc.read_log_tail(cfg.log_file, lines, offset))
