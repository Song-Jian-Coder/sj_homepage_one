# -*- coding: utf-8 -*-
"""行业相关路由（13 条路由中的 7 条）。"""
from fastapi import APIRouter, Depends, Path, Query

from app import deps
from app.deps import CODE_NOT_FOUND, err, ok
from app.schemas import (Envelope, IndustryDetailData, IndustryFlowData,
                         IndustryListData, IndustryStocksFlowData, MemberListData,
                         StrengthData, TreeNode)
from app.services import industry as svc

router = APIRouter(tags=["行业"])


@router.get("/industries", response_model=Envelope[IndustryListData], summary="行业列表")
def industries(
    conn=Depends(deps.get_conn),
    level: int = Query(2, ge=1, le=3, description="1/2/3 级"),
    l1: str = Query("", description="按所属一级行业名过滤"),
    q: str = Query("", description="名称或代码模糊匹配"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=1000),
):
    return ok(svc.list_industries_cached(conn, level, l1, q, page, page_size))


# 注意：/industries/tree 必须注册在 /industries/{sw_code} 之前，否则会被后者吃掉。
@router.get("/industries/tree", response_model=Envelope[list[TreeNode]], summary="行业树")
def tree(conn=Depends(deps.get_conn)):
    return ok(svc.tree_cached(conn))


@router.get("/industries/{sw_code}", response_model=Envelope[IndustryDetailData],
            summary="行业详情")
def industry_detail(sw_code: str = Path(..., description="行业代码，如 801081"),
                    conn=Depends(deps.get_conn)):
    d = svc.detail(conn, sw_code)
    if d is None:
        return err(CODE_NOT_FOUND, "industry not found: " + sw_code)
    return ok(d)


@router.get("/industries/{sw_code}/members", response_model=Envelope[MemberListData],
            summary="行业成分股")
def members(
    sw_code: str = Path(...),
    conn=Depends(deps.get_conn),
    as_of: str = Query("", description="时点 YYYYMMDD 或 YYYY-MM-DD；缺省为当前有效成分"),
    direct: int = Query(0, ge=0, le=1,
                        description="1=只看直属成分股（二级行业的「未细分到三级」）"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
):
    """成分股列表。行业不存在时返回 40401（原实现返回 code=0 + 空列表，
    前端无法区分「行业不存在」与「行业存在但暂无成分股」，见审查报告 5.1.10）。"""
    d = svc.members(conn, sw_code, as_of, direct, page, page_size)
    if d is None:
        return err(CODE_NOT_FOUND, "industry not found: " + sw_code)
    return ok(d)


@router.get("/industries/{sw_code}/flow", response_model=Envelope[IndustryFlowData],
            summary="行业资金流序列")
def industry_flow(
    sw_code: str = Path(...),
    conn=Depends(deps.get_conn),
    start: str = Query("", description="起始交易日 YYYYMMDD / YYYY-MM-DD"),
    end: str = Query(""),
):
    """一/二/三级行业都有资金流数据（L3 存在源端无成分股的真实稀疏，见 docs/03 §3.7）。"""
    d = svc.flow(conn, sw_code, start, end)
    if d is None:
        return err(CODE_NOT_FOUND, "industry not found: " + sw_code)
    return ok(d)


@router.get("/industries/{sw_code}/stocks-flow",
            response_model=Envelope[IndustryStocksFlowData],
            summary="行业成分股资金流排行（区间累计）")
def industry_stocks_flow(
    sw_code: str = Path(...),
    conn=Depends(deps.get_conn),
    start: str = Query(""),
    end: str = Query(""),
    limit: int = Query(100, ge=1, le=500),
):
    d = svc.stocks_flow(conn, sw_code, start, end, limit)
    if d is None:
        return err(CODE_NOT_FOUND, "industry not found: " + sw_code)
    return ok(d)


@router.get("/strength", response_model=Envelope[StrengthData],
            summary="全市场二级行业强度（全历史累计口径）")
def market_strength(conn=Depends(deps.get_conn)):
    return ok(svc.strength_cached(conn))
