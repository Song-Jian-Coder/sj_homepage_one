# -*- coding: utf-8 -*-
"""纯单元测试：不连数据库、不发网络请求。

覆盖审查报告 5.1.5 / 5.1.6 / 5.2.2 / 5.1.2 / 5.1.3 / 5.3.6 的修复点。
"""
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import etl  # noqa: E402
from app import deps  # noqa: E402
from app.services import admin as admin_svc  # noqa: E402
from app.services.cache import TTLCache  # noqa: E402


# ───────────────────────── 5.1.5 数值兜底 ─────────────────────────
@pytest.mark.parametrize("raw,expect", [
    (None, None), ("", None), ("abc", None), (0, 0.0), ("1.25", 1.25), (-3, -3.0),
])
def test_fnum(raw, expect):
    assert deps.fnum(raw) == expect


def test_fsum_ignores_none_but_not_to_zero():
    # 关键：NULL 不能被静默当成 0（原 _win_sum 的 `or 0` 语义）
    assert deps.fsum([1, None, 2]) == 3
    assert deps.fsum([None, None]) is None
    assert deps.fsum([]) is None
    assert deps.fsum([0.1, 0.2]) == 0.3


# ───────────────────────── 5.1.6 日期校验 ─────────────────────────
@pytest.mark.parametrize("raw,expect", [
    ("2026-09-24", "20260924"), ("20260924", "20260924"), (None, None), ("", None),
])
def test_norm_date_ok(raw, expect):
    assert deps.norm_date(raw) == expect


@pytest.mark.parametrize("raw", ["abc", "2026-13-01", "20261301", "2026-02-30", "2026092",
                                 "202609244", " 2026-1-1 "])
def test_norm_date_rejects(raw):
    with pytest.raises(deps.ApiError) as ei:
        deps.norm_date(raw)
    assert ei.value.code == deps.CODE_BAD_PARAM


def test_norm_range_rejects_reversed():
    with pytest.raises(deps.ApiError) as ei:
        deps.norm_range("2026-09-30", "2026-09-01")
    assert ei.value.code == deps.CODE_BAD_PARAM
    assert deps.norm_range("2026-09-01", "2026-09-30") == ("20260901", "20260930")


# ───────────────────────── 响应外壳 ─────────────────────────
def test_envelope_shape_and_trace_id_is_12_hex():
    j = deps.ok({"a": 1})
    assert set(j) == {"code", "msg", "data", "traceId"}
    assert j["code"] == 0 and j["msg"] == "ok" and j["data"] == {"a": 1}
    assert len(j["traceId"]) == 12 and int(j["traceId"], 16) >= 0
    e = deps.err(40401, "nope")
    assert e["code"] == 40401 and e["data"] is None


# ───────────────────────── 5.2.2 鉴权 ─────────────────────────
class _FakeRequest:
    def __init__(self, ip="127.0.0.1", path="/api/v1/admin/log"):
        from types import SimpleNamespace
        self.client = SimpleNamespace(host=ip)
        self.url = SimpleNamespace(path=path)


@pytest.fixture
def admin_key(monkeypatch):
    monkeypatch.setattr(deps, "ADMIN_KEY", "s3cret-key")
    monkeypatch.setattr(deps, "ADMIN_KEY_ENABLED", True)
    monkeypatch.setattr(deps, "_ADMIN_KEY_BYTES", b"s3cret-key")
    monkeypatch.setattr(deps.cfg, "admin_ip_allowlist", [])
    monkeypatch.setattr(deps.cfg, "admin_rate_limit_per_min", 0)
    deps.reset_rate_limit()
    yield "s3cret-key"
    deps.reset_rate_limit()


def test_admin_guard_disabled_branch(monkeypatch):
    """未配置 ADMIN_KEY → 一律拒绝（fail closed）。"""
    monkeypatch.setattr(deps, "ADMIN_KEY_ENABLED", False)
    r = deps.admin_guard(_FakeRequest(), "anything")
    assert r is not None and r["code"] == deps.CODE_UNAUTHORIZED


def test_admin_guard_accepts_correct_key(admin_key):
    assert deps.admin_guard(_FakeRequest(), admin_key) is None


@pytest.mark.parametrize("bad", ["", "wrong", "s3cret-ke", "s3cret-key ", "密钥"])
def test_admin_guard_rejects_wrong_key(admin_key, bad):
    r = deps.admin_guard(_FakeRequest(), bad)
    assert r is not None and r["code"] == deps.CODE_UNAUTHORIZED


def test_admin_guard_ip_allowlist(admin_key, monkeypatch):
    monkeypatch.setattr(deps.cfg, "admin_ip_allowlist", ["10.0.0.1"])
    r = deps.admin_guard(_FakeRequest(ip="127.0.0.1"), admin_key)
    assert r is not None and r["code"] == deps.CODE_IP_DENIED
    assert deps.admin_guard(_FakeRequest(ip="10.0.0.1"), admin_key) is None


def test_admin_guard_rate_limit(admin_key, monkeypatch):
    monkeypatch.setattr(deps.cfg, "admin_rate_limit_per_min", 3)
    for _ in range(3):
        assert deps.admin_guard(_FakeRequest(), admin_key) is None
    r = deps.admin_guard(_FakeRequest(), admin_key)
    assert r is not None and r["code"] == deps.CODE_RATE_LIMITED


# ───────────────────────── 5.1.2 采集闸门 ─────────────────────────
def test_fetch_gate_is_exclusive():
    gate = admin_svc.FetchGate()
    assert gate.running is False
    assert gate.acquire() is True
    assert gate.running is True
    assert gate.acquire() is False        # 第二个并发请求必须被拒
    gate.release()
    assert gate.running is False
    assert gate.acquire() is True         # 释放后可再次占用
    gate.release()


def test_fetch_gate_release_is_idempotent():
    gate = admin_svc.FetchGate()
    gate.release()
    assert gate.running is False


# ───────────────────────── 5.1.3 通用分页 ─────────────────────────
class _FakeLog:
    def __init__(self):
        self.lines = []

    def log(self, msg):
        self.lines.append(msg)


class _FakeClient:
    def __init__(self, pages):
        self.pages = list(pages)
        self.calls = []

    def call(self, api, max_rows=None, **params):
        self.calls.append({"api": api, "max_rows": max_rows, "params": dict(params)})
        return self.pages.pop(0)


def test_call_paged_single_page():
    client = _FakeClient([([{"a": 1}], False)])
    rep = etl.RunReport(_FakeLog())
    rows, truncated = etl._call_paged(client, rep.log, rep, "stock_basic")
    assert rows == [{"a": 1}] and truncated is False
    assert rep.problems == [] and rep.rows_fetched == 1
    assert client.calls[0]["params"]["offset"] == 0


def test_call_paged_follows_has_more_with_advancing_offset():
    client = _FakeClient([
        ([{"a": 1}, {"a": 2}], True),
        ([{"a": 3}], False),
    ])
    rep = etl.RunReport(_FakeLog())
    rows, truncated = etl._call_paged(client, rep.log, rep, "stock_basic", page_size=2)
    assert [r["a"] for r in rows] == [1, 2, 3]
    assert truncated is False
    # offset 按「本页实回行数」推进，不是固定步长
    assert [c["params"]["offset"] for c in client.calls] == [0, 2]


def test_call_paged_flags_zero_rows_with_has_more():
    """has_more=True 却回 0 行 → 契约异常，必须告警且不能被当成成功。"""
    client = _FakeClient([([{"a": 1}], True), ([], True)])
    rep = etl.RunReport(_FakeLog())
    rows, truncated = etl._call_paged(client, rep.log, rep, "stock_basic", page_size=1)
    assert truncated is True
    assert rep.problems and "分页未消化完" in rep.problems[0]


def test_call_paged_flags_page_cap():
    """永远 has_more=True → 跑飞护栏必须告警（5.1.3：has_more 未处理要非零退出）。"""
    pages = [([{"a": i}], True) for i in range(etl.MAX_FETCH_PAGES)]
    client = _FakeClient(pages)
    rep = etl.RunReport(_FakeLog())
    rows, truncated = etl._call_paged(client, rep.log, rep, "stock_basic", page_size=1)
    assert truncated is True and len(rows) == etl.MAX_FETCH_PAGES
    assert rep.problems


def test_run_report_dedups_problems():
    rep = etl.RunReport(_FakeLog())
    rep.problem("x")
    rep.problem("x")
    assert rep.problems == ["x"]


# ───────────────────────── 5.3.6 日志读取 ─────────────────────────
def test_read_log_tail_single_pass_and_offset(tmp_path):
    p = tmp_path / "etl_run.log"
    p.write_text("\n".join("line%d" % i for i in range(1, 11)), encoding="utf-8")
    tail = admin_svc.read_log_tail(str(p), 3)
    assert tail["exists"] is True
    assert tail["total"] == 10
    assert tail["lines"] == ["line8", "line9", "line10"]
    win = admin_svc.read_log_tail(str(p), 2, offset=2)
    assert win["total"] == 10 and win["lines"] == ["line2", "line3"]
    empty = admin_svc.read_log_tail(str(tmp_path / "nope.log"), 5)
    assert empty == {"exists": False, "total": 0, "mtime": None, "lines": []}


# ───────────────────────── TTL 缓存 ─────────────────────────
def test_ttl_cache_disabled_is_passthrough():
    c = TTLCache(0)
    c.set("k", {"v": 1})
    assert c.get("k") is None


def test_ttl_cache_hits_returns_copy_and_expires():
    c = TTLCache(0.15)
    c.set("k", {"v": 1})
    first = c.get("k")
    assert first == {"v": 1}
    first["v"] = 999                      # 改副本不得污染缓存
    assert c.get("k") == {"v": 1}
    time.sleep(0.2)
    assert c.get("k") is None


# ───────────── ETL 单日采集链路（2026-10-17 实跑回归）─────────────
# 事故：sync_moneyflow 既传位置参数 ds_str、又传 trade_date=ds_str，
# _fetch_day_paged 内部 `**extra` 展开后 TypeError: got multiple values for
# keyword argument 'trade_date'，7 个交易日全部失败且被逐日 try/except 吞成
# 「单日失败」。原测试只覆盖 _call_paged，从未走通 sync_moneyflow → 落库。
class _FixedClient:
    """每次调用都返回同一页（单日数据只有一页）。"""

    def __init__(self, rows):
        self.rows = list(rows)
        self.calls = []

    def call(self, api, max_rows=None, **params):
        self.calls.append({"api": api, "max_rows": max_rows, "params": dict(params)})
        return list(self.rows), False


def _one_day_rows():
    return [{"ts_code": "000001.SZ", "trade_date": "20261016",
             **{c: 1.0 for c in etl.MF_NUM}}]


def test_fetch_day_paged_passes_trade_date_exactly_once():
    client = _FixedClient(_one_day_rows())
    rep = etl.RunReport(_FakeLog())
    rows, truncated = etl._fetch_day_paged(client, rep.log, rep, "20261016", 6000,
                                           api="moneyflow")
    assert truncated is False and len(rows) == 1
    assert client.calls[0]["params"]["trade_date"] == "20261016"
    assert client.calls[0]["params"]["offset"] == 0
    # 调用方多传一次也不得抛 TypeError（原事故就是这一行）
    rows2, _ = etl._fetch_day_paged(client, rep.log, rep, "20261016", 6000,
                                    api="moneyflow", trade_date="20261016")
    assert len(rows2) == 1
    assert client.calls[-1]["params"]["trade_date"] == "20261016"


def test_sync_moneyflow_full_mode_writes_the_day():
    """走通 sync_moneyflow → _fetch_day_paged → executemany 全链路。"""
    from datetime import date
    from conftest import FakeConn

    conn = FakeConn([
        ("FROM t_trade_cal WHERE is_open=1 AND cal_date <= CURDATE()", [(date(2026, 10, 16),)]),
        ("SELECT MAX(trade_date) FROM t_moneyflow", [(date(2026, 10, 15),)]),
    ])
    client = _FixedClient(_one_day_rows())
    log = _FakeLog()
    rep = etl.RunReport(log)

    written = etl.sync_moneyflow(client, conn, log, rep, days=1, mode="full",
                                 page_size=6000)

    assert written == ["20261016"]          # 原事故：返回 []，且 rep.failed_days 有 1 条
    assert rep.failed_days == []
    assert rep.problems == []
    assert rep.rows_fetched == 1 and rep.rows_written == 1
    inserts = [args for sql, args in conn.sql_seen if sql.startswith("INSERT INTO t_moneyflow")]
    assert len(inserts) == 1 and len(inserts[0]) == 1
    assert inserts[0][0]["trade_date"] == "20261016"
    assert client.calls[0]["params"]["trade_date"] == "20261016"
