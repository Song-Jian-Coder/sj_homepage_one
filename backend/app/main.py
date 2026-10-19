# -*- coding: utf-8 -*-
"""申万行业资金流看板 · FastAPI 应用装配（/api/v1）。

统一响应：{"code":0,"msg":"ok","data":...,"traceId":"..."}
金额单位：亿元（t_industry_flow 已为亿元；t_moneyflow 为万元，读取时换算）

本文件只负责装配：中间件、异常处理、路由注册、静态托管。
业务 SQL 在 app/services/，响应模型在 app/schemas.py，公共依赖在 app/deps.py。
（拆分原因见审查报告 5.4：原先单文件 546 行承载 13 路由 + SQL + 鉴权 + 静态挂载，
任何查询都无法被单测。）
"""
import json
import os
import time
import traceback
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

import db
from app import deps
from app.deps import (ADMIN_KEY_ENABLED, ApiError, CODE_INTERNAL, cfg, client_ip,
                      logger)
from app.routers import admin as r_admin
from app.routers import health as r_health
from app.routers import industries as r_industries
from app.routers import stocks as r_stocks


@asynccontextmanager
async def lifespan(_app):
    lg = logger()
    lg.info("API_START config=%s", json.dumps(cfg.masked(), ensure_ascii=False))
    for w in cfg.warnings():
        lg.warning("CONFIG_WARN %s", w)
        print("[WARN] " + w, flush=True)
    if not ADMIN_KEY_ENABLED:
        lg.warning("CONFIG_WARN 管理接口已禁用（ADMIN_KEY 未配置或仍为默认值）")
    yield
    db.reset_pool()


app = FastAPI(title="申万行业资金流看板 API", version="1.1.0", lifespan=lifespan)

# CORS：前端与 API 同源托管，不需要放开全网。默认只允许本机开发端口，
# 可用 CORS_ORIGINS 覆盖（审查报告 5.2.5：原为 allow_origins=["*"]）。
app.add_middleware(
    CORSMiddleware,
    allow_origins=cfg.cors_origins,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["X-Admin-Key", "Content-Type"],
    max_age=600,
)


@app.middleware("http")
async def access_log(request: Request, call_next):
    """traceId + 访问日志。

    审查报告 A2/A4：traceId 每个响应都生成却从不写日志，客户端拿到也无从对账。
    这里为每个请求生成 traceId，写进响应头 X-Trace-Id、写进响应体、并落日志。
    """
    tid = deps.new_trace_id()
    request.state.trace_id = tid          # request.state 落在 scope 上，异常处理器同样可见
    token = deps.trace_id_var.set(tid)
    t0 = time.perf_counter()
    try:
        resp = await call_next(request)
    except Exception:
        deps.logger().error(
            "ACCESS trace=%s method=%s path=%s status=500 dur=%.0fms ip=%s",
            tid, request.method, request.url.path,
            (time.perf_counter() - t0) * 1000, client_ip(request))
        raise
    finally:
        deps.trace_id_var.reset(token)
    dur = (time.perf_counter() - t0) * 1000
    resp.headers["X-Trace-Id"] = tid
    resp.headers["X-Content-Type-Options"] = "nosniff"
    deps.logger().info("ACCESS trace=%s method=%s path=%s status=%s dur=%.0fms ip=%s",
                       tid, request.method, request.url.path, resp.status_code,
                       dur, client_ip(request))
    return resp


@app.middleware("http")
async def cache_policy(request: Request, call_next):
    """前端单文件项目：HTML / JS / CSS 每次必须回源校验。

    背景（2026-10-19）：StaticFiles 只发 ETag + Last-Modified，不含 Cache-Control，
    浏览器对这类响应走「启发式缓存」，于是改完主题后使用者仍看到旧页面
    （表现为 fundflow 顶部停在「模拟数据」占位、三页配色不变），排查成本极高。
    这里补 no-cache（可缓存，但每次使用前必须校验），命中 ETag 时是 304，开销可忽略。
    """
    resp = await call_next(request)
    ct = resp.headers.get("content-type", "")
    if ct.startswith("text/html"):
        # no-cache：可缓存，但每次使用前必须校验（命中 ETag 是 304，开销可忽略）
        resp.headers["Cache-Control"] = "no-cache"
    elif ct.startswith(("text/javascript", "application/javascript", "text/css")):
        # no-store：脚本/样式每次重新下载。本地服务没有带宽成本，但能杜绝
        # 「改了 JS 浏览器仍用旧副本」——2026-10-19 实际发生：旧 common.js 缺
        # themeGet，fundflow 在模块级自执行函数里抛错，整页脚本终止、数据不加载。
        resp.headers["Cache-Control"] = "no-store"
    return resp


@app.exception_handler(ApiError)
async def api_error_handler(request: Request, exc: ApiError):
    """可预期的业务错误：HTTP 200 + {"code":40001,...}（沿用既有契约）。"""
    return JSONResponse(status_code=200, content=deps.err(exc.code, exc.msg))


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    """未捕获异常：响应体固定文案 + traceId，细节只进服务端日志。

    审查报告 5.2.1：原实现 `err(50000, str(exc))` 会把 pymysql 原文
    （含 'root'@'localhost'、表名、SQL 片段、文件路径）回给客户端，
    直接违反 docs/01 §6.7「响应脱敏」。
    """
    tid = getattr(request.state, "trace_id", None) or deps.new_trace_id()
    deps.logger().error(
        "UNHANDLED trace=%s method=%s path=%s exc=%s\n%s",
        tid, request.method, request.url.path, type(exc).__name__, traceback.format_exc())
    # 注意：本处理器位于最外层 ServerErrorMiddleware，响应不再经过 access_log
    # 中间件，故 X-Trace-Id 必须在这里显式设置（否则 500 是唯一没有该响应头的响应）。
    return JSONResponse(status_code=500,
                        headers={"X-Trace-Id": tid},
                        content={"code": CODE_INTERNAL, "msg": "internal error",
                                 "data": None, "traceId": tid})


# ───────────────────────── 路由注册 ─────────────────────────
app.include_router(r_health.router, prefix="/api/v1")
app.include_router(r_industries.router, prefix="/api/v1")
app.include_router(r_stocks.router, prefix="/api/v1")
app.include_router(r_admin.router, prefix="/api/v1")


# ───────────────────────── 静态前端托管 ─────────────────────────
# 审查报告 6.1：原先 StaticFiles(html=True) 挂在 /，于是 `GET /` 返回
# backend/static/index.html —— 那是与本项目无关的个人主页，而且一个链接都不指向看板，
# 使用者没有入口。现在显式把 / 重定向到看板主页面；
# 静态目录仍挂载在后（/api/v1 与 / 路由优先匹配）。
_STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "static")


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/industry.html")


if os.path.isdir(_STATIC_DIR):
    app.mount("/", StaticFiles(directory=_STATIC_DIR, html=True), name="static")
