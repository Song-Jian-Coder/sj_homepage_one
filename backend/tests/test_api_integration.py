# -*- coding: utf-8 -*-
"""连接真实 MySQL 的回归测试（需 RUN_INTEGRATION=1）。

分层说明见 conftest.py。这里刻意分成两类：
- **不变量断言**：换库、刷新数据后依然成立（如 directMemberCount <= memberCount）；
- `@pytest.mark.snapshot`：钉死 2026-09-30 数据快照，只在本机数据未变时有效，
  可用 `RUN_INTEGRATION=1 pytest -m "not snapshot"` 跳过。

原 `tests/test_api.py` 的 14 条断言全部在此保留（快照类原样、其余改写成不变量），
不再有「干净 checkout 上必红」的问题。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from conftest import requires_integration  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

pytestmark = requires_integration

client = TestClient(app)


# ───────────────────────── 基础 ─────────────────────────
def test_health_up():
    j = client.get("/api/v1/health").json()
    assert j["code"] == 0
    assert j["data"]["status"] == "up" and j["data"]["db"] == "up"
    assert j["data"]["latestTradeDate"]


def test_meta_invariants():
    j = client.get("/api/v1/meta").json()
    d = j["data"]
    assert j["code"] == 0
    assert d["stockCount"] > 5000
    assert d["industryCount"]["l1"] == 31
    assert d["industryCount"]["l2"] > d["industryCount"]["l1"]
    assert d["industryCount"]["l3"] > d["industryCount"]["l2"]
    assert d["dataStart"] <= d["dataEnd"]
    assert d["generatedAt"] is not None          # 5.7.12：原为恒 null


# ───────────────────────── 行业列表 ─────────────────────────
def test_industries_invariants():
    j = client.get("/api/v1/industries", params={"level": 2, "page_size": 1000}).json()
    assert j["code"] == 0 and j["data"]["total"] > 100
    lst = j["data"]["list"]
    assert all(set(x) >= {"swCode", "name", "level", "l1Name", "memberCount",
                          "directMemberCount", "topStocks", "todayNet", "todayMain",
                          "main5d", "main10d", "net5d", "net10d", "turnover"} for x in lst)
    assert all(x["directMemberCount"] <= x["memberCount"] for x in lst)
    assert all(x["level"] == 2 for x in lst)
    assert all(len(x["topStocks"]) <= 4 for x in lst)
    # 5 日窗口是 10 日窗口的子集，符号不可能因此翻转出更大的绝对值集合
    assert all(x["net5d"] is not None and x["net10d"] is not None for x in lst)
    assert all(isinstance(x["net5d"], float) and isinstance(x["net10d"], float) for x in lst)


def test_industries_l3_direct_equals_total():
    j = client.get("/api/v1/industries", params={"level": 3, "page_size": 1000}).json()
    assert all(x["directMemberCount"] == x["memberCount"] for x in j["data"]["list"])


def test_industries_l1_filter_and_search():
    j = client.get("/api/v1/industries", params={"level": 2, "l1": "电子", "page_size": 1000}).json()
    assert j["data"]["total"] > 0
    assert all(x["l1Name"] == "电子" for x in j["data"]["list"])

    j2 = client.get("/api/v1/industries", params={"level": 2, "q": "半导体"}).json()
    assert j2["data"]["total"] == 1
    assert j2["data"]["list"][0]["name"] == "半导体"


def test_industries_pagination_echo():
    a = client.get("/api/v1/industries", params={"level": 2, "page": 1, "page_size": 3}).json()
    b = client.get("/api/v1/industries", params={"level": 2, "page": 2, "page_size": 3}).json()
    assert a["data"]["page"] == 1 and a["data"]["page_size"] == 3
    assert a["data"]["total"] == b["data"]["total"]
    assert [x["swCode"] for x in a["data"]["list"]] != [x["swCode"] for x in b["data"]["list"]]


# ───────────────────────── 行业树 ─────────────────────────
def test_tree_structure_and_key_cleanup():
    j = client.get("/api/v1/industries/tree").json()
    assert j["code"] == 0
    l1s = j["data"]
    assert len(l1s) == 31
    seen = set()
    for l1 in l1s:
        assert l1["level"] == 1 and l1["parentCode"] is None
        # 5.7.7：不再泄漏 snake_case 别名键
        assert "sw_code" not in l1 and "l1_name" not in l1 and "parent_code" not in l1
        for l2 in l1["children"]:
            assert l2["level"] == 2 and l2["parentCode"] == l1["swCode"]
            assert l2["l1Name"] == l1["name"]
            for l3 in l2["children"]:
                assert l3["level"] == 3 and l3["parentCode"] == l2["swCode"]
                assert l3["children"] == []
                seen.add(l3["swCode"])
    # 346 个三级行业应全部挂在树上
    assert len(seen) == 346


# ───────────────────────── 成分股 ─────────────────────────
def test_members_default_direct_asof_matrix():
    base = client.get("/api/v1/industries/801156/members", params={"page_size": 100}).json()
    direct = client.get("/api/v1/industries/801156/members",
                        params={"direct": 1, "page_size": 100}).json()
    asof = client.get("/api/v1/industries/801156/members",
                      params={"as_of": "2026-09-24", "page_size": 100}).json()
    asof_direct = client.get("/api/v1/industries/801156/members",
                             params={"as_of": "20260924", "direct": 1, "page_size": 100}).json()
    assert base["code"] == 0
    assert direct["data"]["total"] <= base["data"]["total"]
    assert asof["data"]["asOf"] == "2026-09-24"          # 5.7.6：回显统一 YYYY-MM-DD
    assert asof_direct["data"]["asOf"] == "2026-09-24"
    assert asof_direct["data"]["total"] <= asof["data"]["total"]
    # 三个成分股均带 inDate/outDate
    for r in (base, direct, asof):
        for x in r["data"]["list"]:
            assert set(x) == {"tsCode", "name", "inDate", "outDate"}
            assert x["inDate"] is not None


def test_members_l3_direct_equals_default():
    a = client.get("/api/v1/industries/851564/members", params={"direct": 1, "page_size": 100}).json()
    b = client.get("/api/v1/industries/851564/members", params={"page_size": 100}).json()
    assert a["data"]["total"] == b["data"]["total"]


def test_members_unknown_industry_40401():
    j = client.get("/api/v1/industries/999999/members").json()
    assert j["code"] == 40401 and j["data"] is None


# ───────────────────────── 资金流 ─────────────────────────
def test_flow_series_shape_and_unit_consistency():
    j = client.get("/api/v1/industries/801081/flow").json()
    assert j["code"] == 0 and len(j["data"]["series"]) > 100
    pt = j["data"]["series"][-1]
    assert set(pt) == {"d", "sm", "md", "lg", "elg", "main", "net", "turnover", "memberCount"}
    assert len(pt["d"]) == 10 and pt["d"][4] == "-"
    # 单位一致性：列表的 todayNet 应等于序列末点的 net（同源、同单位）
    row = [x for x in client.get("/api/v1/industries",
                                 params={"level": 2, "q": "半导体"}).json()["data"]["list"]][0]
    assert row["todayNet"] == pt["net"]
    assert row["todayMain"] == pt["main"]


def test_flow_exists_for_all_three_levels():
    """5.7.4 回归：docs/03 曾称「仅二级行业有数据」，实测一/二/三级都有。

    这句错误结论导致前端对用户显示「该层级资金流暂未聚合」。
    """
    import db
    from config import Config
    cfg = Config()
    conn = db.connect(cfg, database=cfg.db_name)
    rows = dict(db.query(conn, "SELECT level, COUNT(*) FROM t_industry_flow GROUP BY level"))
    conn.close()
    assert set(rows) == {1, 2, 3}
    assert all(v > 0 for v in rows.values()), rows


def test_flow_date_window_param():
    j = client.get("/api/v1/industries/801081/flow",
                   params={"start": "2026-09-01", "end": "2026-09-30"}).json()
    ds = [x["d"] for x in j["data"]["series"]]
    assert ds and all("2026-09-01" <= d <= "2026-09-30" for d in ds)


def test_stocks_flow_ranked_and_404():
    j = client.get("/api/v1/industries/801081/stocks-flow", params={"limit": 20}).json()
    assert j["code"] == 0 and len(j["data"]["list"]) == 20
    nets = [x["net"] for x in j["data"]["list"]]
    assert nets == sorted(nets, reverse=True)
    assert all(set(x) == {"tsCode", "name", "net", "main", "turnover"} for x in j["data"]["list"])
    bad = client.get("/api/v1/industries/999999/stocks-flow").json()
    assert bad["code"] == 40401


def test_stock_flow_and_404():
    j = client.get("/api/v1/stocks/600519.SH/flow").json()
    assert j["code"] == 0 and j["data"]["name"] == "贵州茅台"
    assert len(j["data"]["series"]) > 100
    assert set(j["data"]["series"][-1]) == {"d", "sm", "md", "lg", "elg", "main", "net", "turnover"}
    assert client.get("/api/v1/stocks/999999.SH/flow").json()["code"] == 40401


# ───────────────────────── 搜索 ─────────────────────────
def test_search_reports_industry_and_l3():
    j = client.get("/api/v1/stocks/search", params={"q": "茅台"}).json()
    row = j["data"]["list"][0]
    assert row["name"] == "贵州茅台"
    assert row["industry"] == "白酒Ⅱ" and row["hasL3"] is True

    n = client.get("/api/v1/stocks/search", params={"q": "000504"}).json()["data"]["list"][0]
    assert n["tsCode"] == "000504.SZ"
    assert n["l3Code"] is None and n["hasL3"] is False
    assert n["industryCode"] == "801156"


def test_search_total_not_truncated_by_page():
    """5.7.2 / 6.1 回归：宽泛查询的命中总数必须如实返回（前端据此提示「还有 N 条」）。"""
    j = client.get("/api/v1/stocks/search", params={"q": "银", "page_size": 10}).json()
    assert j["data"]["total"] > 10
    assert len(j["data"]["list"]) == 10


# ───────────────────────── 强度 ─────────────────────────
def test_strength_matches_definition():
    j = client.get("/api/v1/strength").json()
    assert j["code"] == 0 and len(j["data"]["list"]) > 100
    for x in j["data"]["list"]:
        if x["turnover"] and x["turnover"] > 0:
            assert x["strength"] == pytest.approx(round(x["net"] / x["turnover"], 6))


# ───────────────────────── 钉死快照（本机数据未变时才成立）─────────────────────────
@pytest.mark.snapshot
def test_snapshot_meta_counts():
    d = client.get("/api/v1/meta").json()["data"]
    assert d["industryCount"] == {"l1": 31, "l2": 134, "l3": 346}
    assert d["dataEnd"] == "2026-09-30"


@pytest.mark.snapshot
def test_snapshot_members_and_direct():
    m = {x["swCode"]: x for x in client.get(
        "/api/v1/industries", params={"level": 2, "page_size": 1000}).json()["data"]["list"]}
    assert m["801156"]["memberCount"] == 51
    assert m["801156"]["directMemberCount"] == 7

    d = client.get("/api/v1/industries/801156/members",
                   params={"direct": 1, "page_size": 100}).json()["data"]
    assert d["total"] == 7
    assert "000504.SZ" in {x["tsCode"] for x in d["list"]}
    assert client.get("/api/v1/industries/801156/members",
                      params={"page_size": 100}).json()["data"]["total"] == 51
    assert client.get("/api/v1/industries/801081/members",
                      params={"as_of": "2026-09-24"}).json()["data"]["total"] == 180


@pytest.mark.snapshot
def test_snapshot_search_l3_name():
    row = client.get("/api/v1/stocks/search", params={"q": "000001"}).json()["data"]["list"][0]
    assert row["l3Code"] == "857831" and row["l3Name"] == "股份制银行Ⅲ"
