# -*- coding: utf-8 -*-
"""健康检查与元信息。"""
from datetime import datetime, timedelta, timezone

import db
from fastapi import APIRouter, Depends

from app import deps
from app.deps import cfg, dstr, logger, ok
from app.schemas import Envelope, HealthData, MetaData

router = APIRouter(tags=["健康检查 / 元信息"])


@router.get("/health", response_model=Envelope[HealthData], summary="健康检查")
def health():
    """DB 连通性 + 行业资金流最新交易日。

    **不**用 `Depends(get_conn)`：依赖注入在进入函数体之前就会 connect()，
    MySQL 不可达时依赖阶段直接抛异常 → 500，except 分支永远不可达
    （审查报告 5.1.1）。这里把「取连接 + 两条查询」全纳入同一 try，
    失败时仍返回 HTTP 200 与 `db=down`。
    """
    conn = None
    try:
        conn = deps.acquire_conn()
        db.query_one(conn, "SELECT 1")
        r = db.query_one(conn, "SELECT MAX(trade_date) FROM t_industry_flow")
        return ok({"status": "up", "db": "up",
                   "latestTradeDate": dstr(r[0]) if r and r[0] is not None else None})
    except Exception as e:  # noqa: BLE001
        logger().warning("HEALTH_DB_DOWN %s: %s", type(e).__name__, e)
        return ok({"status": "down", "db": "down", "latestTradeDate": None})
    finally:
        if conn is not None:
            deps.release_conn(conn)


@router.get("/meta", response_model=Envelope[MetaData], summary="元信息")
def meta(conn=Depends(deps.get_conn)):
    rng = db.query_one(conn, "SELECT MIN(trade_date), MAX(trade_date) FROM t_moneyflow")
    lvl = {r[0]: r[1] for r in db.query(
        conn, "SELECT level, COUNT(*) FROM t_industry GROUP BY level")}
    n_stock = db.query_one(conn, "SELECT COUNT(*) FROM t_stock_basic")[0]
    now = datetime.now(timezone(timedelta(hours=cfg.tz_offset_hours)))
    return ok({
        "dataStart": dstr(rng[0]) if rng else None,
        "dataEnd": dstr(rng[1]) if rng else None,
        "industryCount": {"l1": lvl.get(1, 0), "l2": lvl.get(2, 0), "l3": lvl.get(3, 0)},
        "stockCount": n_stock,
        # 原实现恒为 null；现将生成时间如实回填（Asia/Shanghai）
        "generatedAt": now.strftime("%Y-%m-%d %H:%M:%S"),
        "source": "tushare-compatible-proxy",
    })
