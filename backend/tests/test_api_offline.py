# -*- coding: utf-8 -*-
"""端点层离线测试：用依赖注入的假连接覆盖分支，不连数据库。

覆盖审查报告 5.1.1（/health 降级可达）/ 5.1.2（42901 真可达）/ 5.1.10（404 语义）
/ 5.1.6（参数校验）/ 5.2.1（500 响应脱敏）/ 5.7.1（OpenAPI 有响应模型）。
"""
import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from conftest import FakeConn, fake_conn_dep  # noqa: E402

from app import deps  # noqa: E402
from app.main import app  # noqa: E402
from app.services import admin as admin_svc  # noqa: E402

client = TestClient(app)                            # 不吞异常（默认）
loose = TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def _clean_state():
    """每个用例前后都释放采集闸门、清空限流计数、清掉依赖覆盖。"""
    admin_svc.fetch_gate.release()
    deps.reset_rate_limit()
    yield
    admin_svc.fetch_gate.release()
    deps.reset_rate_limit()
    app.dependency_overrides.clear()


@pytest.fixture
def admin_key(monkeypatch):
    monkeypatch.setattr(deps, "ADMIN_KEY", "test-key")
    monkeypatch.setattr(deps, "ADMIN_KEY_ENABLED", True)
    monkeypatch.setattr(deps, "_ADMIN_KEY_BYTES", b"test-key")
    monkeypatch.setattr(deps.cfg, "admin_ip_allowlist", [])
    monkeypatch.setattr(deps.cfg, "admin_rate_limit_per_min", 0)
    return "test-key"


def use_conn(conn):
    app.dependency_overrides[deps.get_conn] = fake_conn_dep(conn)


# ───────────────────────── /health（5.1.1）─────────────────────────
def test_health_reports_down_when_db_unreachable(monkeypatch):
    def boom():
        raise RuntimeError("(2003, \"Can't connect to MySQL server on '127.0.0.1'\")")
    monkeypatch.setattr(deps, "acquire_conn", boom)
    r = client.get("/api/v1/health")
    assert r.status_code == 200                     # 必须仍返回 200，而不是 500
    j = r.json()
    assert j["code"] == 0
    assert j["data"] == {"status": "down", "db": "down", "latestTradeDate": None}
    assert "MySQL" not in r.text                    # 细节不能回给客户端


def test_health_reports_up(monkeypatch):
    import datetime
    conn = FakeConn([
        ("SELECT 1", [(1,)]),
        ("MAX(trade_date) FROM t_industry_flow", [(datetime.date(2026, 9, 30),)]),
    ])
    monkeypatch.setattr(deps, "acquire_conn", lambda: conn)
    monkeypatch.setattr(deps, "release_conn", lambda c: None)
    j = client.get("/api/v1/health").json()
    assert j["data"] == {"status": "up", "db": "up", "latestTradeDate": "2026-09-30"}


# ───────────────────────── 参数校验（5.1.6）─────────────────────────
@pytest.mark.parametrize("params,expect_type", [({}, "missing"), ({"q": ""}, "string_too_short")])
def test_search_requires_q(params, expect_type):
    """改造前后都是 422；契约差异在于 OpenAPI 现在如实标注 required。

    注：审查报告 5.7.2 推断「不带 q 会执行 LIKE '%%' 返回全部 5,571 只」，
    实测不成立（FastAPI 对默认值也做 min_length 校验，一样 422）——
    故此项只改契约表述，不改行为。
    """
    r = client.get("/api/v1/stocks/search", params=params)
    assert r.status_code == 422
    assert r.json()["detail"][0]["type"] == expect_type


def test_invalid_date_is_40001_not_500():
    use_conn(FakeConn([("FROM t_industry WHERE sw_code=", [("801081", "半导体", 2)])]))
    j = client.get("/api/v1/industries/801081/flow", params={"start": "abc"}).json()
    assert j["code"] == deps.CODE_BAD_PARAM
    assert "invalid date" in j["msg"]


def test_reversed_range_is_40001():
    use_conn(FakeConn([("FROM t_industry WHERE sw_code=", [("801081", "半导体", 2)])]))
    j = client.get("/api/v1/industries/801081/flow",
                   params={"start": "2026-09-30", "end": "2026-09-01"}).json()
    assert j["code"] == deps.CODE_BAD_PARAM and "invalid range" in j["msg"]


# ───────────────────────── 404 语义（5.1.10）─────────────────────────
def test_members_of_unknown_industry_is_40401():
    use_conn(FakeConn([("FROM t_industry WHERE sw_code=", [])]))
    j = client.get("/api/v1/industries/999999/members").json()
    assert j["code"] == deps.CODE_NOT_FOUND and j["data"] is None


def test_stocks_flow_of_unknown_industry_is_40401():
    use_conn(FakeConn([("FROM t_industry WHERE sw_code=", [])]))
    j = client.get("/api/v1/industries/999999/stocks-flow").json()
    assert j["code"] == deps.CODE_NOT_FOUND and j["data"] is None


# ───────────────────────── 管理接口 ─────────────────────────
def test_admin_requires_key():
    use_conn(FakeConn())
    for url, method in (("/api/v1/admin/log", "get"), ("/api/v1/admin/fetch", "post")):
        r = getattr(client, method)(url) if method == "get" else client.post(url, json={})
        assert r.json()["code"] == deps.CODE_UNAUTHORIZED
    # 错误密钥同样拒绝（常量时间比较路径）
    r = client.post("/api/v1/admin/fetch", json={}, headers={"X-Admin-Key": "nope"})
    assert r.json()["code"] == deps.CODE_UNAUTHORIZED


def test_admin_unknown_task_is_40001(admin_key):
    use_conn(FakeConn())
    j = client.post("/api/v1/admin/fetch", json={"task": "nope"},
                    headers={"X-Admin-Key": admin_key}).json()
    assert j["code"] == deps.CODE_BAD_PARAM and "unknown task" in j["msg"]


def test_admin_fetch_accepts_then_rejects_concurrent(admin_key, monkeypatch):
    """5.1.2 核心回归：42901 必须真的可达，且闸门由 worker 释放。"""
    use_conn(FakeConn([("FROM t_fetch_log", [])]))
    started = []

    def fake_spawn(task, on_done=None):
        started.append(task)
        # 模拟 worker 仍在跑：不释放闸门

    monkeypatch.setattr(admin_svc, "spawn_worker", fake_spawn)
    h = {"X-Admin-Key": admin_key}
    j1 = client.post("/api/v1/admin/fetch", json={"task": "incremental"}, headers=h).json()
    assert j1["code"] == 0 and j1["data"] == {"task": "incremental", "status": "accepted"}
    j2 = client.post("/api/v1/admin/fetch", json={"task": "incremental"}, headers=h).json()
    assert j2["code"] == deps.CODE_FETCH_RUNNING
    assert started == ["incremental"]              # 第二次没有真的再起线程
    admin_svc.fetch_gate.release()
    j3 = client.post("/api/v1/admin/fetch", json={"task": "meta"}, headers=h).json()
    assert j3["code"] == 0


def test_admin_fetch_rejects_when_audit_row_running(admin_key):
    """跨进程护栏：t_fetch_log 里还有未结束的 running 行 → 42901。"""
    use_conn(FakeConn([("FROM t_fetch_log", [(7, "incremental", "2026-10-06 10:00:00")])]))
    j = client.post("/api/v1/admin/fetch", json={"task": "incremental"},
                    headers={"X-Admin-Key": admin_key}).json()
    assert j["code"] == deps.CODE_FETCH_RUNNING
    assert "audit id=7" in j["msg"]
    assert admin_svc.fetch_gate.running is False   # 被 DB 拦下时不得占住进程内闸门


def test_admin_log_requires_key_but_returns_lines_with_it(admin_key, tmp_path, monkeypatch):
    log = tmp_path / "etl_run.log"
    log.write_text("[10:00:00] a\n[10:00:01] b\n", encoding="utf-8")
    monkeypatch.setattr(deps.cfg, "log_file", str(log))
    use_conn(FakeConn())
    j = client.get("/api/v1/admin/log", params={"lines": 1},
                   headers={"X-Admin-Key": admin_key}).json()
    assert j["code"] == 0
    assert j["data"]["exists"] is True and j["data"]["total"] == 2
    assert j["data"]["lines"] == ["[10:00:01] b"]


# ───────────────────────── 500 脱敏（5.2.1）─────────────────────────
def test_unhandled_exception_is_sanitized(monkeypatch):
    leak = "(1045, \"Access denied for user 'root'@'localhost' (using password: YES)\")"

    def boom(*a, **k):
        raise RuntimeError(leak)

    from app.routers import industries as ind_router
    monkeypatch.setattr(ind_router.svc, "list_industries_cached", boom)

    r = loose.get("/api/v1/industries")
    assert r.status_code == 500
    assert leak not in r.text and "root" not in r.text and "1045" not in r.text
    j = r.json()
    assert j["code"] == deps.CODE_INTERNAL and j["msg"] == "internal error"
    assert j["data"] is None
    # traceId 与响应头一致，服务端日志里能凭它对账
    assert j["traceId"] == r.headers["x-trace-id"]
    assert len(j["traceId"]) == 12


# ───────────────────────── 路由/静态/重定向 ─────────────────────────
def test_root_redirects_to_dashboard():
    r = loose.get("/", follow_redirects=False)
    assert r.status_code in (302, 307)
    assert r.headers["location"] == "/industry.html"


def test_unknown_api_path_is_404():
    assert loose.get("/api/v1/nope").status_code == 404


def test_trace_id_header_matches_body():
    use_conn(FakeConn([("FROM t_industry WHERE sw_code=", [])]))
    r = client.get("/api/v1/industries/999999/members")
    assert r.json()["traceId"] == r.headers["x-trace-id"]


# ───────────────────────── 5.7.1 OpenAPI 有响应模型 ─────────────────────────
def test_openapi_documents_response_schemas():
    spec = app.openapi()
    paths = spec["paths"]
    assert len(paths) == 13
    schemas = spec["components"]["schemas"]
    # 13 个端点全部声明了 200 响应体模型（改造前一个都没有）
    for path, ops in paths.items():
        for method, op in ops.items():
            if method not in ("get", "post"):
                continue
            ok200 = op["responses"].get("200", {})
            assert "content" in ok200, "%s %s 缺响应模型" % (method, path)
            assert "schema" in ok200["content"]["application/json"]
    for name in ("IndustryListData", "TreeNode", "SearchData", "AdminLogData"):
        assert name in schemas
