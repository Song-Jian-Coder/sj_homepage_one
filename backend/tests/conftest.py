# -*- coding: utf-8 -*-
"""pytest 公共设施：假连接、集成测试开关。

分层约定（审查报告 5.6 P0）：
- `test_unit.py`         纯函数 / ETL 分页 / 鉴权 / 缓存，**不连库**；
- `test_api_offline.py`  端点层分支（依赖注入假连接），**不连库**；
- `test_api_integration.py` 连真实库的回归，需 `RUN_INTEGRATION=1`。
  其中钉死生产快照的用例标 `@pytest.mark.snapshot`，可用
  `RUN_INTEGRATION=1 pytest -m "not snapshot"` 只跑不变量断言。

默认（不设 RUN_INTEGRATION）只跑前两层，任何机器上都能全绿。
"""
import os
import sys

import pytest

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

RUN_INTEGRATION = os.getenv("RUN_INTEGRATION", "").strip().lower() not in ("", "0", "false", "no")

requires_integration = pytest.mark.skipif(
    not RUN_INTEGRATION,
    reason="需要真实 MySQL 与已采集数据：设置 RUN_INTEGRATION=1 后重跑")


def pytest_configure(config):
    config.addinivalue_line("markers", "snapshot: 钉死某日生产数据的快照断言（换库/刷新数据即失效）")


# ───────────────────────── 假连接 ─────────────────────────
class FakeCursor:
    def __init__(self, conn, dict_mode=False):
        self.conn = conn
        self.dict_mode = dict_mode
        self.result = []

    def execute(self, sql, args=None):
        self.conn.sql_seen.append((" ".join(sql.split()), args))
        self.result = self.conn.answer(sql, args)

    def fetchone(self):
        return self.result[0] if self.result else None

    def fetchall(self):
        return self.result

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeConn:
    """按 SQL 子串应答的假连接。

    `rules` 形如 [("FROM t_industry WHERE sw_code=", [{"sw_code": "801081"}])]；
    未命中任何规则直接断言失败——避免「以为覆盖了某个分支、其实 SQL 已经变了」。
    """

    def __init__(self, rules=None):
        self.rules = list(rules or [])
        self.sql_seen = []

    def cursor(self, cursor_class=None):
        return FakeCursor(self, cursor_class is not None)

    def answer(self, sql, args=None):
        s = " ".join(sql.split())
        for pattern, value in self.rules:
            if pattern in s:
                return value(sql, args) if callable(value) else value
        raise AssertionError("FakeConn 未配置该 SQL：%s" % s)

    # 连接协议里其余被调用到的方法
    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass

    def ping(self, reconnect=False):
        pass


@pytest.fixture
def fake_conn():
    return FakeConn()


def fake_conn_dep(conn):
    """构造一个把 get_conn 依赖替换成给定假连接的 override。"""
    def _dep():
        yield conn
    return _dep
