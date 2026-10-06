# -*- coding: utf-8 -*-
"""公共依赖：配置、日志、统一响应、连接、鉴权、字段规整、错误码。

响应外壳统一为 {"code":0,"msg":"ok","data":...,"traceId":"..."}。
业务错误的 HTTP 状态码仍为 200（沿用既有契约），只有未捕获异常返回 500。
"""
import hmac
import logging
import os
import threading
import time
import uuid
from collections import deque
from contextvars import ContextVar
from datetime import datetime

from fastapi import Request

import db
from config import Config

cfg = Config()

# ───────────────────────── 错误码 ─────────────────────────
CODE_OK = 0
CODE_BAD_PARAM = 40001          # 参数校验失败（含非法日期、start > end）
CODE_IP_DENIED = 40301          # 管理接口来源 IP 不在白名单
CODE_NOT_FOUND = 40401          # 资源不存在
CODE_UNAUTHORIZED = 40102       # 管理密钥缺失/错误/管理接口禁用
CODE_FETCH_RUNNING = 42901      # 已有采集任务在跑
CODE_RATE_LIMITED = 42902       # 管理接口触发频率超限
CODE_INTERNAL = 50000           # 服务端内部错误（响应体不含细节）

DEFAULT_ADMIN_KEY = "change-me-admin-key"
ADMIN_KEY = os.getenv("ADMIN_KEY", "").strip()
# 未配置 / 仍为默认值 → 管理接口一律拒绝（fail closed），避免「默认密钥」被随意调用
ADMIN_KEY_ENABLED = bool(ADMIN_KEY) and ADMIN_KEY != DEFAULT_ADMIN_KEY
_ADMIN_KEY_BYTES = ADMIN_KEY.encode("utf-8")

if not ADMIN_KEY_ENABLED:
    print("[WARN] ADMIN_KEY 未设置或仍为默认值 —— 管理接口 /api/v1/admin/* 已禁用。"
          "请在 backend/.env 中设置 ADMIN_KEY（随机长字符串）后重启服务。", flush=True)


class ApiError(Exception):
    """可预期的业务错误：由异常处理器统一转成 {code,msg,data:null}，HTTP 200。"""

    def __init__(self, code, msg):
        super().__init__(msg)
        self.code = code
        self.msg = msg


# ───────────────────────── 日志 ─────────────────────────
_LOG = None
_LOG_LOCK = threading.Lock()


def logger():
    """API 运行日志（文件）。traceId 会随每条访问/异常日志落盘，便于客户端对账。"""
    global _LOG
    if _LOG is None:
        with _LOG_LOCK:
            if _LOG is None:
                lg = logging.getLogger("sjhomepage.api")
                lg.setLevel(logging.INFO)
                lg.propagate = False
                if not lg.handlers:
                    try:
                        os.makedirs(os.path.dirname(cfg.api_log_file), exist_ok=True)
                        fh = logging.FileHandler(cfg.api_log_file, encoding="utf-8")
                        fh.setFormatter(logging.Formatter(
                            "%(asctime)s %(levelname)s %(message)s"))
                        lg.addHandler(fh)
                    except Exception:  # noqa: BLE001  日志不可用时不能拖垮服务
                        lg.addHandler(logging.NullHandler())
                _LOG = lg
    return _LOG


# ───────────────────────── traceId ─────────────────────────
# 请求中间件写入，路由内的 ok()/err() 读取；500 处理器用 request.state 兜底。
trace_id_var = ContextVar("trace_id", default=None)


def new_trace_id():
    return uuid.uuid4().hex[:12]


def cur_trace_id():
    return trace_id_var.get() or new_trace_id()


# ───────────────────────── 响应外壳 ─────────────────────────
def ok(data):
    return {"code": CODE_OK, "msg": "ok", "data": data, "traceId": cur_trace_id()}


def err(code, msg):
    return {"code": code, "msg": msg, "data": None, "traceId": cur_trace_id()}


# ───────────────────────── 字段规整 ─────────────────────────
def dstr(v):
    """date/datetime -> YYYY-MM-DD（响应内日期格式统一）"""
    if v is None:
        return None
    return v.strftime("%Y-%m-%d") if hasattr(v, "strftime") else str(v)


def norm_date(s):
    """YYYYMMDD / YYYY-MM-DD -> YYYYMMDD。非法直接 40001，不让脏值进 MySQL。

    原实现只做 replace("-","")，`?start=abc` 会带着 'abc' 去比较 DATE 列，
    MySQL 报 1292 未捕获 → 500 且（改造前）把 SQL 报错原文回给客户端。
    """
    if s is None:
        return None
    t = str(s).strip()
    if not t:
        return None
    t = t.replace("-", "")
    if len(t) != 8 or not t.isdigit():
        raise ApiError(CODE_BAD_PARAM, "invalid date: %r（期望 YYYYMMDD 或 YYYY-MM-DD）" % (s,))
    try:
        datetime.strptime(t, "%Y%m%d")
    except ValueError:
        raise ApiError(CODE_BAD_PARAM, "invalid date: %r（不是合法日历日）" % (s,))
    return t


def norm_range(start, end):
    """校验并返回 (start, end)：均为 YYYYMMDD 或 None；start > end → 40001。"""
    s, e = norm_date(start), norm_date(end)
    if s and e and s > e:
        raise ApiError(CODE_BAD_PARAM, "invalid range: start(%s) > end(%s)" % (s, e))
    return s, e


def fnum(v):
    """Decimal/str/None -> float|None。NULL 保持 None，绝不静默当 0。"""
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def fsum(values, ndigits=4):
    """忽略 None 求和；全为 None 时返回 None。

    统一三处不一致的兜底写法（原先分别是逐个判断、裸 float() 会 500、
    以及 `or 0` 把 NULL 当 0 静默失真）。
    """
    xs = [fnum(v) for v in values]
    xs = [x for x in xs if x is not None]
    if not xs:
        return None
    return round(sum(xs), ndigits)


# ───────────────────────── 连接 ─────────────────────────
def acquire_conn():
    """从池里取一条连接（/health 等需要「连接失败也可控」的场景直接用）。"""
    return db.get_pool(cfg).acquire()


def release_conn(conn):
    db.get_pool(cfg).release(conn)


def get_conn():
    """FastAPI 依赖：池化连接，请求结束归还。"""
    conn = acquire_conn()
    try:
        yield conn
    finally:
        release_conn(conn)


# ───────────────────────── 管理接口鉴权 ─────────────────────────
_rate_lock = threading.Lock()
_rate_hits = {}


def _rate_ok(ip, per_min):
    """管理接口按客户端 IP 的简单滑窗限流（进程内）。"""
    if per_min <= 0:
        return True
    now = time.time()
    with _rate_lock:
        q = _rate_hits.setdefault(ip, deque())
        while q and now - q[0] > 60.0:
            q.popleft()
        if len(q) >= per_min:
            return False
        q.append(now)
        return True


def reset_rate_limit():
    """测试用。"""
    with _rate_lock:
        _rate_hits.clear()


def client_ip(request):
    return request.client.host if request.client else "-"


def admin_guard(request, x_admin_key):
    """返回 None = 放行；否则返回错误响应体。

    顺序：功能开关 → IP 白名单 → 限流 → 常量时间密钥比较。
    """
    if not ADMIN_KEY_ENABLED:
        return err(CODE_UNAUTHORIZED,
                   "admin disabled: ADMIN_KEY 未在 backend/.env 配置或仍为默认值")
    ip = client_ip(request)
    if cfg.admin_ip_allowlist and ip not in cfg.admin_ip_allowlist:
        logger().warning("ADMIN_DENY ip=%s path=%s reason=ip_not_allowed", ip, request.url.path)
        return err(CODE_IP_DENIED, "admin ip not allowed: %s" % ip)
    if not _rate_ok(ip, cfg.admin_rate_limit_per_min):
        logger().warning("ADMIN_DENY ip=%s path=%s reason=rate_limit", ip, request.url.path)
        return err(CODE_RATE_LIMITED, "admin rate limit exceeded (%s/min)"
                   % cfg.admin_rate_limit_per_min)
    # 常量时间比较；比较 bytes 以容忍任意非 ASCII 输入（str 会被 compare_digest 拒绝）
    if not hmac.compare_digest(str(x_admin_key).encode("utf-8"), _ADMIN_KEY_BYTES):
        return err(CODE_UNAUTHORIZED, "invalid admin key")
    return None
