# -*- coding: utf-8 -*-
"""个股路由。"""
from fastapi import APIRouter, Depends, Path, Query

from app import deps
from app.deps import CODE_NOT_FOUND, err, ok
from app.schemas import Envelope, SearchData, StockFlowData
from app.services import stock as svc

router = APIRouter(tags=["个股"])


@router.get("/stocks/{ts_code}/flow", response_model=Envelope[StockFlowData],
            summary="个股资金流序列")
def stock_flow(
    ts_code: str = Path(..., description="如 600519.SH"),
    conn=Depends(deps.get_conn),
    start: str = Query("", description="起始交易日 YYYYMMDD / YYYY-MM-DD"),
    end: str = Query(""),
):
    d = svc.stock_flow(conn, ts_code, start, end)
    if d is None:
        return err(CODE_NOT_FOUND, "stock not found: " + ts_code)
    return ok(d)


@router.get("/stocks/search", response_model=Envelope[SearchData], summary="股票搜索")
def search(
    conn=Depends(deps.get_conn),
    # 必填：原实现写 Query("", min_length=1)，OpenAPI 里显示「可选、默认空串」，
    # 与 docs/03 的「必填（至少 1 字符）」不符（审查报告 5.7.2）。
    # 注：实测运行期本来就是 422（默认值也参与校验），此处只是把契约写对。
    q: str = Query(..., min_length=1, description="股票代码或名称关键词，必填"),
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100,
                           description="单页条数；命中总数见 data.total，不受此值截断"),
):
    return ok(svc.search(conn, q, page, page_size))
