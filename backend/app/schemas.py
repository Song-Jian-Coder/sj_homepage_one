# -*- coding: utf-8 -*-
"""响应模型（Pydantic v2）。

存在的意义（审查报告 5.7.1）：全项目原先没有任何 `response_model`，
/openapi.json 只描述路径与参数、响应体完全未定义，前端只能靠读 HTML 猜字段。
这里的字段名与既有 JSON 契约逐字对齐（camelCase），并由 `/openapi.json` 暴露。

约定：
- 金额单位统一「亿元、4 位小数」，日期统一 `YYYY-MM-DD`；
- DB 列可空的字段一律 Optional，避免出现 NULL 时响应校验失败变 500。
"""
from typing import Generic, List, Optional, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class Envelope(BaseModel, Generic[T]):
    """统一响应外壳：code=0 表示成功；业务错误 code != 0 且 HTTP 仍为 200。"""

    code: int = Field(0, description="0=成功；其它为业务错误码")
    msg: str = Field("ok", description="错误描述；成功时为 ok")
    data: Optional[T] = Field(None, description="业务数据；出错时为 null")
    traceId: str = Field("", description="12 位请求追踪号，同时写入服务端日志")


# ───────────── 1. 健康检查 ─────────────
class HealthData(BaseModel):
    status: str = Field(..., description="up / down")
    db: str = Field(..., description="数据库连通性：up / down")
    latestTradeDate: Optional[str] = Field(None, description="行业资金流最新交易日 YYYY-MM-DD")


# ───────────── 2. 元信息 ─────────────
class IndustryCount(BaseModel):
    l1: int = 0
    l2: int = 0
    l3: int = 0


class MetaData(BaseModel):
    dataStart: Optional[str] = None
    dataEnd: Optional[str] = None
    industryCount: IndustryCount
    stockCount: int
    generatedAt: Optional[str] = Field(None, description="本次响应生成时间 YYYY-MM-DD HH:MM:SS")
    source: str = "tushare-compatible-proxy"


# ───────────── 3. 行业列表 ─────────────
class IndustryListItem(BaseModel):
    swCode: str
    name: str
    level: int = Field(..., description="1/2/3 级")
    l1Name: Optional[str] = None
    memberCount: Optional[int] = Field(None, description="当前成分股数（时点 = 最新）")
    directMemberCount: Optional[int] = Field(
        None, description="直属成分股数：挂在自身且未被任一下级子行业覆盖")
    topStocks: List[str] = Field(default_factory=list, description="按代码序的前 4 只成分股名")
    todayNet: Optional[float] = Field(None, description="最新交易日净流入（亿元）")
    todayMain: Optional[float] = Field(None, description="最新交易日主力净流入（亿元）")
    main5d: Optional[float] = Field(None, description="最近 5 个交易日主力净流入合计（亿元）")
    main10d: Optional[float] = Field(None, description="最近 10 个交易日主力净流入合计（亿元）")
    net5d: Optional[float] = Field(None, description="最近 5 个交易日净流入合计（亿元）")
    net10d: Optional[float] = Field(None, description="最近 10 个交易日净流入合计（亿元）")
    turnover: Optional[float] = Field(None, description="最新交易日成交额（亿元）")


class IndustryListData(BaseModel):
    list: List[IndustryListItem]
    total: int
    page: int
    page_size: int


# ───────────── 4. 行业树 ─────────────
class TreeNode(BaseModel):
    swCode: str
    name: str
    level: int
    parentCode: Optional[str] = Field(None, description="上级行业代码；一级为 null")
    l1Name: Optional[str] = None
    memberCount: Optional[int] = None
    directMemberCount: Optional[int] = None
    children: List["TreeNode"] = Field(default_factory=list, description="仅 1/2 级有子节点")


TreeNode.model_rebuild()


# ───────────── 5. 行业详情 ─────────────
class IndustrySummary(BaseModel):
    todayNet: Optional[float] = None
    net5d: Optional[float] = None
    net10d: Optional[float] = None
    todayMain: Optional[float] = None
    main5d: Optional[float] = None
    main10d: Optional[float] = None
    turnover: Optional[float] = None


class IndustryDetailData(BaseModel):
    swCode: str
    name: str
    level: int
    l1Code: Optional[str] = Field(None, description="所属一级行业代码；仅二级非空")
    l1Name: Optional[str] = None
    memberCount: Optional[int] = None
    summary: IndustrySummary


# ───────────── 6. 成分股 ─────────────
class MemberItem(BaseModel):
    tsCode: str
    name: Optional[str] = None
    inDate: Optional[str] = None
    outDate: Optional[str] = None


class MemberListData(BaseModel):
    asOf: Optional[str] = Field(None, description="时点参数回显，YYYY-MM-DD；未传为 null")
    list: List[MemberItem]
    total: int
    page: int
    page_size: int


# ───────────── 7/8. 资金流序列 ─────────────
class IndustryFlowPoint(BaseModel):
    d: str = Field(..., description="交易日 YYYY-MM-DD")
    sm: Optional[float] = None
    md: Optional[float] = None
    lg: Optional[float] = None
    elg: Optional[float] = None
    main: Optional[float] = None
    net: Optional[float] = None
    turnover: Optional[float] = None
    memberCount: Optional[int] = Field(
        None, description="当日有资金流数据的成员数（停牌/新股不计，故可能小于 memberCount）")


class IndustryFlowData(BaseModel):
    swCode: str
    name: str
    level: int
    series: List[IndustryFlowPoint]


class StockFlowPoint(BaseModel):
    d: str
    sm: Optional[float] = None
    md: Optional[float] = None
    lg: Optional[float] = None
    elg: Optional[float] = None
    main: Optional[float] = None
    net: Optional[float] = None
    turnover: Optional[float] = None


class StockFlowData(BaseModel):
    tsCode: str
    name: Optional[str] = None
    industry: Optional[str] = Field(None, description="所属二级行业名；无则回退 t_stock_basic.industry")
    industryCode: Optional[str] = None
    series: List[StockFlowPoint]


# ───────────── 9. 股票搜索 ─────────────
class SearchItem(BaseModel):
    tsCode: str
    name: Optional[str] = None
    industry: Optional[str] = None
    industryCode: Optional[str] = Field(None, description="所属二级行业代码")
    l3Code: Optional[str] = Field(None, description="所属三级行业代码；null = 未细分到三级")
    l3Name: Optional[str] = None
    hasL3: bool = Field(..., description="是否有三级行业归属")


class SearchData(BaseModel):
    list: List[SearchItem]
    total: int = Field(..., description="命中总数（不受 page_size 截断）")
    page: int
    page_size: int


# ───────────── 9b. 成分股资金流排行 ─────────────
class StockFlowRankItem(BaseModel):
    tsCode: str
    name: Optional[str] = None
    net: Optional[float] = None
    main: Optional[float] = None
    turnover: Optional[float] = None


class IndustryStocksFlowData(BaseModel):
    swCode: str
    list: List[StockFlowRankItem]


# ───────────── 9c. 全市场强度 ─────────────
class StrengthItem(BaseModel):
    swCode: str
    name: str
    l1Name: Optional[str] = None
    net: Optional[float] = Field(None, description="全历史累计净流入（亿元）")
    turnover: Optional[float] = Field(None, description="全历史累计成交额（亿元）")
    strength: Optional[float] = Field(None, description="net / turnover（全历史口径）")


class StrengthData(BaseModel):
    list: List[StrengthItem]


# ───────────── 10. 管理接口 ─────────────
class AdminFetchData(BaseModel):
    task: str
    status: str = Field(..., description="accepted")


class AdminLogData(BaseModel):
    exists: bool
    total: int = Field(..., description="日志总行数")
    mtime: Optional[str] = None
    lines: List[str]
