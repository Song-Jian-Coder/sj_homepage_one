# -*- coding: utf-8 -*-
"""申万行业资金流看板 · FastAPI 接口层（/api/v1）

统一响应：{"code":0,"msg":"ok","data":...,"traceId":"..."}
金额单位：亿元（t_industry_flow 已为亿元；t_moneyflow 为万元，读取时换算）
"""
import os
import sys
import uuid
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI, Depends, Query, Path, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

import db
from config import Config

cfg = Config()

app = FastAPI(title="申万行业资金流看板 API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

ADMIN_KEY = os.getenv("ADMIN_KEY", "change-me-admin-key")


def _trace():
    return uuid.uuid4().hex[:12]


def ok(data):
    return {"code": 0, "msg": "ok", "data": data, "traceId": _trace()}


def err(code, msg):
    return {"code": code, "msg": msg, "data": None, "traceId": _trace()}


def get_conn():
    c = db.connect(cfg)
    c.select_db(cfg.db_name)
    try:
        yield c
    finally:
        c.close()


def dstr(v):
    """date -> YYYY-MM-DD"""
    if v is None:
        return None
    return v.strftime("%Y-%m-%d") if hasattr(v, "strftime") else str(v)


def norm_date(s):
    """YYYYMMDD / YYYY-MM-DD -> YYYYMMDD"""
    if not s:
        return None
    return s.replace("-", "")


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    return JSONResponse(status_code=500, content=err(50000, str(exc)))


# ───────────────────────── 1. 健康检查 ─────────────────────────
@app.get("/api/v1/health")
def health(conn=Depends(get_conn)):
    try:
        db.query_one(conn, "SELECT 1")
        db_up = "up"
    except Exception:  # noqa: BLE001
        db_up = "down"
    r = db.query_one(conn, "SELECT MAX(trade_date) FROM t_industry_flow")
    return ok({"status": "up" if db_up == "up" else "down", "db": db_up,
               "latestTradeDate": dstr(r[0]) if r else None})


# ───────────────────────── 2. 元信息 ─────────────────────────
@app.get("/api/v1/meta")
def meta(conn=Depends(get_conn)):
    rng = db.query_one(conn, "SELECT MIN(trade_date), MAX(trade_date) FROM t_moneyflow")
    lvl = {r[0]: r[1] for r in db.query(conn, "SELECT level, COUNT(*) FROM t_industry GROUP BY level")}
    n_stock = db.query_one(conn, "SELECT COUNT(*) FROM t_stock_basic")[0]
    return ok({
        "dataStart": dstr(rng[0]) if rng else None,
        "dataEnd": dstr(rng[1]) if rng else None,
        "industryCount": {"l1": lvl.get(1, 0), "l2": lvl.get(2, 0), "l3": lvl.get(3, 0)},
        "stockCount": n_stock,
        "generatedAt": None,
        "source": "tushare-compatible-proxy",
    })


# ───────────────────────── 3. 行业列表 ─────────────────────────
@app.get("/api/v1/industries")
def industries(
    conn=Depends(get_conn),
    level: int = Query(2, ge=1, le=3),
    l1: str = Query(""),
    q: str = Query(""),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=1000),
):
    where = ["i.level = %s"]
    args = [level]
    if l1:
        where.append("i.l1_name = %s")
        args.append(l1)
    if q:
        where.append("(i.name LIKE %s OR i.sw_code LIKE %s)")
        args += [f"%{q}%", f"%{q}%"]
    cond = " AND ".join(where)

    total = db.query_one(conn, f"SELECT COUNT(*) FROM t_industry i WHERE {cond}", args)[0]

    # 最近 10 个交易日的资金流窗口，用于 5日/10日汇总
    win = [r[0] for r in db.query(conn, "SELECT DISTINCT trade_date FROM t_industry_flow ORDER BY trade_date DESC LIMIT 10")]
    fmap = {}
    if win:
        _fr = db.query_dict(conn, "SELECT sw_code, trade_date, net, main, turnover FROM t_industry_flow WHERE trade_date >= %s", (win[-1],))
        _tmp = {}
        for _f in _fr:
            _tmp.setdefault(_f["sw_code"], {})[_f["trade_date"]] = _f
        fmap = _tmp

    rows = db.query_dict(conn, f"""
        SELECT i.sw_code, i.name, i.level, i.l1_name, i.is_pub,
               f.net AS today_net, f.main AS today_main, f.turnover,
               (SELECT COUNT(*) FROM t_industry_member m
                 WHERE m.index_code = i.sw_code AND m.out_date IS NULL) AS member_count
        FROM t_industry i
        LEFT JOIN t_industry_flow f
          ON f.sw_code = i.sw_code
         AND f.trade_date = (SELECT MAX(trade_date) FROM t_industry_flow)
        WHERE {cond}
        ORDER BY i.sw_code
        LIMIT %s OFFSET %s
    """, args + [page_size, (page - 1) * page_size])

    # 每个行业 top 4 成分股名
    top_map = {}
    if rows:
        codes = [r["sw_code"] for r in rows]
        fmt = ",".join(["%s"] * len(codes))
        tr = db.query_dict(conn, f"""
            SELECT m.index_code, s.name
            FROM t_industry_member m JOIN t_stock_basic s ON s.ts_code = m.con_code
            WHERE m.out_date IS NULL AND m.index_code IN ({fmt})
            ORDER BY m.index_code, m.con_code
        """, codes)
        for r in tr:
            top_map.setdefault(r["index_code"], []).append(r["name"])

    def _win_sum(code, key, n):
        d = fmap.get(code, {})
        return round(sum(float(d[t].get(key) or 0) for t in win[:n] if t in d), 4)

    lst = []
    for r in rows:
        lst.append({
            "swCode": r["sw_code"], "name": r["name"], "level": r["level"],
            "l1Name": r["l1_name"], "memberCount": r["member_count"],
            "topStocks": top_map.get(r["sw_code"], [])[:4],
            "todayNet": float(r["today_net"]) if r["today_net"] is not None else None,
            "todayMain": float(r["today_main"]) if r["today_main"] is not None else None,
            "main5d": _win_sum(r["sw_code"], "main", 5),
            "main10d": _win_sum(r["sw_code"], "main", 10),
            "net5d": _win_sum(r["sw_code"], "net", 5),
            "net10d": _win_sum(r["sw_code"], "net", 10),
            "turnover": float(r["turnover"]) if r["turnover"] is not None else None,
        })
    return ok({"list": lst, "total": total, "page": page, "page_size": page_size})


# ───────────────────────── 4. 行业树 ─────────────────────────
@app.get("/api/v1/industries/tree")
def tree(conn=Depends(get_conn)):
    rows = db.query_dict(conn, "SELECT sw_code, name, level, parent_code, l1_name FROM t_industry ORDER BY level, sw_code")
    counts = {r["sw_code"]: r["n"] for r in db.query_dict(conn,
              "SELECT index_code AS sw_code, COUNT(*) AS n FROM t_industry_member WHERE out_date IS NULL GROUP BY index_code")}
    by_code = {r["sw_code"]: r for r in rows}
    out = []
    for r in rows:
        r["memberCount"] = counts.get(r["sw_code"], 0)
        r["children"] = []
        r["swCode"], r["name"] = r["sw_code"], r["name"]
        if r["level"] == 1:
            out.append(r)
        else:
            p = by_code.get(r["parent_code"])
            if p:
                p.setdefault("children", []).append(r)
    return ok(out)


# ───────────────────────── 5. 行业详情 ─────────────────────────
@app.get("/api/v1/industries/{sw_code}")
def industry_detail(sw_code: str = Path(...), conn=Depends(get_conn)):
    r = db.query_dict(conn, "SELECT sw_code, name, level, parent_code, l1_name FROM t_industry WHERE sw_code=%s", (sw_code,))
    if not r:
        return err(40401, f"industry not found: {sw_code}")
    r = r[0]
    mc = db.query_one(conn, "SELECT COUNT(*) FROM t_industry_member WHERE index_code=%s AND out_date IS NULL", (sw_code,))[0]
    fl = db.query_dict(conn, """
        SELECT net, main, turnover FROM t_industry_flow
        WHERE sw_code=%s ORDER BY trade_date DESC LIMIT 10
    """, (sw_code,))
    latest = fl[0] if fl else {}
    net5 = sum(x["net"] for x in fl[:5]) if fl else None
    net10 = sum(x["net"] for x in fl[:10]) if fl else None
    return ok({
        "swCode": r["sw_code"], "name": r["name"], "level": r["level"],
        "l1Code": r["parent_code"] if r["level"] == 2 else None,
        "l1Name": r["l1_name"], "memberCount": mc,
        "summary": {
            "todayNet": float(latest["net"]) if latest.get("net") is not None else None,
            "net5d": float(net5) if net5 is not None else None,
            "net10d": float(net10) if net10 is not None else None,
            "todayMain": float(latest["main"]) if latest.get("main") is not None else None,
            "turnover": float(latest["turnover"]) if latest.get("turnover") is not None else None,
        },
    })


# ───────────────────────── 6. 行业成分股 ─────────────────────────
@app.get("/api/v1/industries/{sw_code}/members")
def members(
    sw_code: str = Path(...),
    conn=Depends(get_conn),
    as_of: str = Query(""),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
):
    base = "FROM t_industry_member m LEFT JOIN t_stock_basic s ON s.ts_code = m.con_code WHERE m.index_code = %s"
    args = [sw_code]
    if as_of:
        d = norm_date(as_of)
        cond = base + " AND m.in_date <= %s AND (m.out_date IS NULL OR m.out_date > %s)"
        args += [d, d]
        as_of_out = as_of
    else:
        cond = base + " AND m.out_date IS NULL"
        as_of_out = None
    total = db.query_one(conn, f"SELECT COUNT(*) {cond}", args)[0]
    rows = db.query_dict(conn, f"""
        SELECT m.con_code, s.name, m.in_date, m.out_date
        {cond} ORDER BY m.con_code LIMIT %s OFFSET %s
    """, args + [page_size, (page - 1) * page_size])
    lst = [{"tsCode": r["con_code"], "name": r["name"],
            "inDate": dstr(r["in_date"]), "outDate": dstr(r["out_date"])} for r in rows]
    return ok({"asOf": as_of_out, "list": lst, "total": total, "page": page, "page_size": page_size})


# ───────────────────────── 7. 行业资金流 ─────────────────────────
@app.get("/api/v1/industries/{sw_code}/flow")
def industry_flow(
    sw_code: str = Path(...),
    conn=Depends(get_conn),
    start: str = Query(""),
    end: str = Query(""),
):
    r = db.query_dict(conn, "SELECT sw_code, name, level FROM t_industry WHERE sw_code=%s", (sw_code,))
    if not r:
        return err(40401, f"industry not found: {sw_code}")
    where = ["sw_code = %s"]
    args = [sw_code]
    if start:
        where.append("trade_date >= %s"); args.append(norm_date(start))
    if end:
        where.append("trade_date <= %s"); args.append(norm_date(end))
    cond = " AND ".join(where)
    rows = db.query_dict(conn, f"""
        SELECT trade_date, sm, md, lg, elg, main, net, turnover, member_count
        FROM t_industry_flow WHERE {cond} ORDER BY trade_date
    """, args)
    series = [{"d": dstr(x["trade_date"]),
               "sm": float(x["sm"]) if x["sm"] is not None else None,
               "md": float(x["md"]) if x["md"] is not None else None,
               "lg": float(x["lg"]) if x["lg"] is not None else None,
               "elg": float(x["elg"]) if x["elg"] is not None else None,
               "main": float(x["main"]) if x["main"] is not None else None,
               "net": float(x["net"]) if x["net"] is not None else None,
               "turnover": float(x["turnover"]) if x["turnover"] is not None else None,
               "memberCount": x["member_count"]} for x in rows]
    return ok({"swCode": r[0]["sw_code"], "name": r[0]["name"], "level": r[0]["level"], "series": series})


# ───────────────────────── 8. 个股资金流 ─────────────────────────
@app.get("/api/v1/stocks/{ts_code}/flow")
def stock_flow(
    ts_code: str = Path(...),
    conn=Depends(get_conn),
    start: str = Query(""),
    end: str = Query(""),
):
    sb = db.query_dict(conn, "SELECT ts_code, name, industry FROM t_stock_basic WHERE ts_code=%s", (ts_code,))
    if not sb:
        return err(40401, f"stock not found: {ts_code}")
    # 所属 L2 行业
    ind = db.query_one(conn, """
        SELECT i.sw_code, i.name FROM t_industry_member m
        JOIN t_industry i ON i.sw_code = m.index_code
        WHERE m.con_code=%s AND m.out_date IS NULL AND i.level=2 LIMIT 1
    """, (ts_code,))
    where = ["ts_code = %s"]
    args = [ts_code]
    if start:
        where.append("trade_date >= %s"); args.append(norm_date(start))
    if end:
        where.append("trade_date <= %s"); args.append(norm_date(end))
    cond = " AND ".join(where)
    rows = db.query_dict(conn, f"""
        SELECT trade_date,
          ROUND((buy_sm_amount - sell_sm_amount)/10000, 4) AS sm,
          ROUND((buy_md_amount - sell_md_amount)/10000, 4) AS md,
          ROUND((buy_lg_amount - sell_lg_amount)/10000, 4) AS lg,
          ROUND((buy_elg_amount - sell_elg_amount)/10000, 4) AS elg,
          ROUND(((buy_lg_amount - sell_lg_amount) + (buy_elg_amount - sell_elg_amount))/10000, 4) AS main,
          ROUND(net_mf_amount/10000, 4) AS net,
          ROUND((buy_sm_amount + buy_md_amount + buy_lg_amount + buy_elg_amount)/10000, 4) AS turnover
        FROM t_moneyflow WHERE {cond} ORDER BY trade_date
    """, args)
    series = [{"d": dstr(x["trade_date"]), "sm": float(x["sm"]), "md": float(x["md"]),
               "lg": float(x["lg"]), "elg": float(x["elg"]),
               "main": float(x["main"]), "net": float(x["net"]),
               "turnover": float(x["turnover"]) if x["turnover"] is not None else None} for x in rows]
    return ok({"tsCode": ts_code, "name": sb[0]["name"],
               "industry": ind[1] if ind else sb[0]["industry"],
               "industryCode": ind[0] if ind else None, "series": series})


# ───────────────────────── 9. 股票搜索 ─────────────────────────
@app.get("/api/v1/stocks/search")
def search(
    conn=Depends(get_conn),
    q: str = Query("", min_length=1),
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
):
    like = f"%{q}%"
    total = db.query_one(conn, "SELECT COUNT(*) FROM t_stock_basic WHERE ts_code LIKE %s OR name LIKE %s", (like, like))[0]
    rows = db.query_dict(conn, """
        SELECT s.ts_code, s.name, s.industry,
          (SELECT i.sw_code FROM t_industry_member m JOIN t_industry i ON i.sw_code = m.index_code
            WHERE m.con_code = s.ts_code AND m.out_date IS NULL AND i.level = 2 LIMIT 1) AS industry_code,
          (SELECT i.name FROM t_industry_member m JOIN t_industry i ON i.sw_code = m.index_code
            WHERE m.con_code = s.ts_code AND m.out_date IS NULL AND i.level = 2 LIMIT 1) AS industry_name
        FROM t_stock_basic s
        WHERE s.ts_code LIKE %s OR s.name LIKE %s
        ORDER BY s.ts_code LIMIT %s OFFSET %s
    """, (like, like, page_size, (page - 1) * page_size))
    lst = [{"tsCode": r["ts_code"], "name": r["name"],
            "industry": r["industry_name"] or r["industry"],
            "industryCode": r["industry_code"]} for r in rows]
    return ok({"list": lst, "total": total, "page": page, "page_size": page_size})


# ───────────────────────── 9b. 行业成分股资金流排行 ─────────────────────────
@app.get("/api/v1/industries/{sw_code}/stocks-flow")
def industry_stocks_flow(
    sw_code: str = Path(...),
    conn=Depends(get_conn),
    start: str = Query(""),
    end: str = Query(""),
    limit: int = Query(100, ge=1, le=500),
):
    where = ["m.index_code = %s", "m.in_date <= mf.trade_date",
             "(m.out_date IS NULL OR m.out_date > mf.trade_date)"]
    args = [sw_code]
    if start:
        where.append("mf.trade_date >= %s"); args.append(norm_date(start))
    if end:
        where.append("mf.trade_date <= %s"); args.append(norm_date(end))
    cond = " AND ".join(where)
    rows = db.query_dict(conn, f"""
        SELECT m.con_code AS ts_code, s.name,
          ROUND(SUM(mf.net_mf_amount)/10000, 4) AS net,
          ROUND(SUM((mf.buy_lg_amount - mf.sell_lg_amount) + (mf.buy_elg_amount - mf.sell_elg_amount))/10000, 4) AS main,
          ROUND(SUM(mf.buy_sm_amount + mf.buy_md_amount + mf.buy_lg_amount + mf.buy_elg_amount)/10000, 4) AS turnover
        FROM t_industry_member m
        JOIN t_moneyflow mf ON mf.ts_code = m.con_code
        LEFT JOIN t_stock_basic s ON s.ts_code = m.con_code
        WHERE {cond}
        GROUP BY m.con_code, s.name
        ORDER BY net DESC
        LIMIT %s
    """, args + [limit])
    lst = [{"tsCode": r["ts_code"], "name": r["name"],
            "net": float(r["net"]) if r["net"] is not None else None,
            "main": float(r["main"]) if r["main"] is not None else None,
            "turnover": float(r["turnover"]) if r["turnover"] is not None else None} for r in rows]
    return ok({"swCode": sw_code, "list": lst})


# ───────────────────────── 9c. 全市场二级行业强度 ─────────────────────────
@app.get("/api/v1/strength")
def market_strength(conn=Depends(get_conn)):
    rows = db.query_dict(conn, """
        SELECT f.sw_code, i.name, i.l1_name,
          ROUND(SUM(f.net), 4) AS net,
          ROUND(SUM(f.turnover), 4) AS turnover
        FROM t_industry_flow f
        JOIN t_industry i ON i.sw_code = f.sw_code
        WHERE f.level = 2
        GROUP BY f.sw_code, i.name, i.l1_name
    """)
    lst = []
    for r in rows:
        t = float(r["turnover"] or 0)
        net = float(r["net"] or 0)
        lst.append({"swCode": r["sw_code"], "name": r["name"], "l1Name": r["l1_name"],
                    "net": net, "turnover": t,
                    "strength": round(net / t, 6) if t > 0 else None})
    return ok({"list": lst})


# ───────────────────────── 10. 触发采集（管理） ─────────────────────────
_fetching = threading.Lock()


@app.post("/api/v1/admin/fetch")
def trigger_fetch(x_admin_key: str = Header(""), task: str = "all"):
    if x_admin_key != ADMIN_KEY:
        return err(40102, "invalid admin key")
    if not _fetching.acquire(blocking=False):
        return err(42901, "fetch already running")
    try:
        import etl
        threading.Thread(target=etl.main, daemon=True).start()
        return ok({"task": task, "status": "accepted"})
    finally:
        _fetching.release()


# 静态前端托管（挂载在所有路由之后，/api/v1 优先匹配）
_static_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")
if os.path.isdir(_static_dir):
    app.mount("/", StaticFiles(directory=_static_dir, html=True), name="static")
