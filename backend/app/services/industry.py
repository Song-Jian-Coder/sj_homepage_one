# -*- coding: utf-8 -*-
"""行业相关查询：列表 / 树 / 详情 / 成分股 / 资金流 / 成分股排行 / 全市场强度。

性能取舍（审查报告 5.3.3）：
- 「最近 N 个交易日」的 5 日/10 日合计由 SQL 聚合下推，不再把整窗原始行拉进 Python；
- `topStocks` 用 `ROW_NUMBER()` 在 SQL 内取每行业前 4 只，不再取回全部成员名再切片；
- 列表/树/强度走 60s TTL 缓存。
"""
from datetime import datetime

import db
from app.deps import dstr, fnum, fsum, norm_date, norm_range
from app.services.cache import cached

# 5 日/10 日窗口：取最近 10 个有行业资金流的交易日
WINDOW_DAYS = 10

# 「直属成分股」：挂在行业自身、且未被其任一下级子行业覆盖的成分股。
# 用途：二级行业下的「未细分到三级」股票（例如 000504.SZ 只挂 801156 医疗服务，
# 没有任何三级归属）。三级行业无子节点，故其 directMemberCount == memberCount。
_DIRECT_SQL = """
    SELECT c.sw_code, COUNT(DISTINCT m.con_code) AS n
    FROM t_industry c
    JOIN t_industry_member m ON m.index_code = c.sw_code AND m.out_date IS NULL
    WHERE {scope}
      AND NOT EXISTS (
        SELECT 1 FROM t_industry ch
        JOIN t_industry_member mc ON mc.index_code = ch.sw_code AND mc.out_date IS NULL
        WHERE ch.parent_code = c.sw_code AND mc.con_code = m.con_code)
    GROUP BY c.sw_code
"""


def _window(conn):
    """最近 WINDOW_DAYS 个交易日（降序）。"""
    return [r[0] for r in db.query(conn, (
        "SELECT DISTINCT trade_date FROM t_industry_flow "
        "ORDER BY trade_date DESC LIMIT %s"), (WINDOW_DAYS,))]


def _sum_or_zero(row, key):
    """窗口合计；无数据行时为 0.0（与改造前 `round(0,4)` 的数值一致，但类型统一为 float）。"""
    if not row:
        return 0.0
    return fnum(row.get(key)) or 0.0


def list_industries(conn, level, l1, q, page, page_size):
    where = ["i.level = %s"]
    args = [level]
    if l1:
        where.append("i.l1_name = %s")
        args.append(l1)
    if q:
        where.append("(i.name LIKE %s OR i.sw_code LIKE %s)")
        args += ["%" + q + "%", "%" + q + "%"]
    cond = " AND ".join(where)

    total = db.query_one(conn, "SELECT COUNT(*) FROM t_industry i WHERE " + cond, args)[0]

    win = _window(conn)
    latest = win[0] if win else None

    # 最新交易日的当日值：原实现用相关子查询 (SELECT MAX(trade_date) ...) 作为 join 条件，
    # 这里换成标量参数，执行计划稳定且少一次子查询。
    rows = db.query_dict(conn, """
        SELECT i.sw_code, i.name, i.level, i.l1_name,
               f.net AS today_net, f.main AS today_main, f.turnover,
               (SELECT COUNT(*) FROM t_industry_member m
                 WHERE m.index_code = i.sw_code AND m.out_date IS NULL) AS member_count
        FROM t_industry i
        LEFT JOIN t_industry_flow f
          ON f.sw_code = i.sw_code AND f.trade_date = %s
        WHERE """ + cond + """
        ORDER BY i.sw_code
        LIMIT %s OFFSET %s
    """, [latest] + args + [page_size, (page - 1) * page_size])

    direct_map, top_map, win_map = {}, {}, {}
    if rows:
        codes = [r["sw_code"] for r in rows]
        fmt = ",".join(["%s"] * len(codes))

        # 直属成分股数（只统计本页代码，避免全表 15,769 行白读）
        dr = db.query_dict(conn, _DIRECT_SQL.format(scope="c.sw_code IN (" + fmt + ")"), codes)
        direct_map = {r["sw_code"]: r["n"] for r in dr}

        # 每个行业 top 4 成分股名（SQL 内取前 4）
        tr = db.query_dict(conn, """
            SELECT index_code, name FROM (
              SELECT m.index_code, s.name,
                     ROW_NUMBER() OVER (PARTITION BY m.index_code ORDER BY m.con_code) AS rn
              FROM t_industry_member m JOIN t_stock_basic s ON s.ts_code = m.con_code
              WHERE m.out_date IS NULL AND m.index_code IN (""" + fmt + """)
            ) AS t WHERE t.rn <= 4
        """, codes)
        for r in tr:
            top_map.setdefault(r["index_code"], []).append(r["name"])

        # 5 日/10 日合计（SQL 内聚合，只取本页代码）
        if win:
            d5 = win[min(5, len(win)) - 1]
            wr = db.query_dict(conn, """
                SELECT sw_code,
                  ROUND(SUM(CASE WHEN trade_date >= %s THEN main END), 4) AS main5d,
                  ROUND(SUM(main), 4) AS main10d,
                  ROUND(SUM(CASE WHEN trade_date >= %s THEN net END), 4) AS net5d,
                  ROUND(SUM(net), 4) AS net10d
                FROM t_industry_flow
                WHERE trade_date >= %s AND sw_code IN (""" + fmt + """)
                GROUP BY sw_code
            """, [d5, d5, win[-1]] + codes)
            win_map = {r["sw_code"]: r for r in wr}

    lst = []
    for r in rows:
        code = r["sw_code"]
        w = win_map.get(code)
        lst.append({
            "swCode": code, "name": r["name"], "level": r["level"],
            "l1Name": r["l1_name"], "memberCount": r["member_count"],
            "directMemberCount": direct_map.get(code, 0),
            "topStocks": top_map.get(code, [])[:4],
            "todayNet": fnum(r["today_net"]),
            "todayMain": fnum(r["today_main"]),
            "main5d": _sum_or_zero(w, "main5d"),
            "main10d": _sum_or_zero(w, "main10d"),
            "net5d": _sum_or_zero(w, "net5d"),
            "net10d": _sum_or_zero(w, "net10d"),
            "turnover": fnum(r["turnover"]),
        })
    return {"list": lst, "total": total, "page": page, "page_size": page_size}


def tree(conn):
    rows = db.query_dict(conn, (
        "SELECT sw_code, name, level, parent_code, l1_name FROM t_industry "
        "ORDER BY level, sw_code"))
    counts = {r["sw_code"]: r["n"] for r in db.query_dict(conn,
              "SELECT index_code AS sw_code, COUNT(*) AS n FROM t_industry_member "
              "WHERE out_date IS NULL GROUP BY index_code")}
    direct = {r["sw_code"]: r["n"] for r in db.query_dict(conn, _DIRECT_SQL.format(scope="1=1"))}

    by_code = {}
    out = []
    for r in rows:
        node = {
            "swCode": r["sw_code"], "name": r["name"], "level": r["level"],
            "parentCode": r["parent_code"], "l1Name": r["l1_name"],
            "memberCount": counts.get(r["sw_code"], 0),
            "directMemberCount": direct.get(r["sw_code"], 0),
            "children": [],
        }
        by_code[r["sw_code"]] = node
        if r["level"] == 1:
            out.append(node)
    for r in rows:
        if r["level"] == 1:
            continue
        parent = by_code.get(r["parent_code"])
        if parent is not None:
            parent["children"].append(by_code[r["sw_code"]])
    return out


def detail(conn, sw_code):
    r = db.query_dict(conn, (
        "SELECT sw_code, name, level, parent_code, l1_name FROM t_industry WHERE sw_code=%s"),
        (sw_code,))
    if not r:
        return None
    r = r[0]
    mc = db.query_one(conn, (
        "SELECT COUNT(*) FROM t_industry_member "
        "WHERE index_code=%s AND out_date IS NULL"), (sw_code,))[0]

    win = _window(conn)
    rows = []
    if win:
        rows = db.query_dict(conn, """
            SELECT trade_date, net, main, turnover FROM t_industry_flow
            WHERE sw_code=%s AND trade_date >= %s
        """, (sw_code, win[-1]))
    by_date = {x["trade_date"]: x for x in rows}

    # today* 用「该行业自身最新一行」，与改造前一致（行业数据滞后的极端情形下不回退成 null）
    latest = db.query_dict(conn, (
        "SELECT net, main, turnover FROM t_industry_flow "
        "WHERE sw_code=%s ORDER BY trade_date DESC LIMIT 1"), (sw_code,))
    latest = latest[0] if latest else {}

    def _win(key, n):
        return fsum([by_date[t][key] for t in win[:n] if t in by_date])

    return {
        "swCode": r["sw_code"], "name": r["name"], "level": r["level"],
        "l1Code": r["parent_code"] if r["level"] == 2 else None,
        "l1Name": r["l1_name"], "memberCount": mc,
        "summary": {
            "todayNet": fnum(latest.get("net")),
            "net5d": _win("net", 5),
            "net10d": _win("net", 10),
            "todayMain": fnum(latest.get("main")),
            "main5d": _win("main", 5),
            "main10d": _win("main", 10),
            "turnover": fnum(latest.get("turnover")),
        },
    }


def members(conn, sw_code, as_of, direct, page, page_size):
    """成分股列表。行业不存在返回 None（由路由转 40401）。

    direct=1 时只返回「直属成分股」：挂在 sw_code 自身、且未被其任一下级子行业覆盖的股票。
    用于二级行业的「未细分到三级」分组（这批股票在数源就没有三级归属）。
    三级行业无子节点，direct=1 与默认结果一致。
    """
    if not db.query_one(conn, "SELECT 1 FROM t_industry WHERE sw_code=%s", (sw_code,)):
        return None

    base = ("FROM t_industry_member m LEFT JOIN t_stock_basic s ON s.ts_code = m.con_code "
            "WHERE m.index_code = %s")
    args = [sw_code]
    as_of_out = None
    d = norm_date(as_of)
    if d:
        as_of_out = dstr(datetime.strptime(d, "%Y%m%d").date())   # 回显统一 YYYY-MM-DD
        cond = base + " AND m.in_date <= %s AND (m.out_date IS NULL OR m.out_date > %s)"
        args += [d, d]
        if direct:
            cond += (" AND NOT EXISTS (SELECT 1 FROM t_industry c"
                     " JOIN t_industry_member mc ON mc.index_code = c.sw_code"
                     "   AND mc.in_date <= %s AND (mc.out_date IS NULL OR mc.out_date > %s)"
                     " WHERE c.parent_code = %s AND mc.con_code = m.con_code)")
            args += [d, d, sw_code]
    else:
        cond = base + " AND m.out_date IS NULL"
        if direct:
            cond += (" AND NOT EXISTS (SELECT 1 FROM t_industry c"
                     " JOIN t_industry_member mc ON mc.index_code = c.sw_code AND mc.out_date IS NULL"
                     " WHERE c.parent_code = %s AND mc.con_code = m.con_code)")
            args.append(sw_code)

    total = db.query_one(conn, "SELECT COUNT(*) " + cond, args)[0]
    rows = db.query_dict(conn, """
        SELECT m.con_code, s.name, m.in_date, m.out_date
        """ + cond + " ORDER BY m.con_code LIMIT %s OFFSET %s",
        args + [page_size, (page - 1) * page_size])
    lst = [{"tsCode": r["con_code"], "name": r["name"],
            "inDate": dstr(r["in_date"]), "outDate": dstr(r["out_date"])} for r in rows]
    return {"asOf": as_of_out, "list": lst, "total": total,
            "page": page, "page_size": page_size}


def flow(conn, sw_code, start, end):
    r = db.query_dict(conn, "SELECT sw_code, name, level FROM t_industry WHERE sw_code=%s",
                      (sw_code,))
    if not r:
        return None
    s, e = norm_range(start, end)
    where = ["sw_code = %s"]
    args = [sw_code]
    if s:
        where.append("trade_date >= %s")
        args.append(s)
    if e:
        where.append("trade_date <= %s")
        args.append(e)
    cond = " AND ".join(where)
    rows = db.query_dict(conn, """
        SELECT trade_date, sm, md, lg, elg, main, net, turnover, member_count
        FROM t_industry_flow WHERE """ + cond + " ORDER BY trade_date", args)
    series = [{"d": dstr(x["trade_date"]),
               "sm": fnum(x["sm"]), "md": fnum(x["md"]), "lg": fnum(x["lg"]),
               "elg": fnum(x["elg"]), "main": fnum(x["main"]), "net": fnum(x["net"]),
               "turnover": fnum(x["turnover"]), "memberCount": x["member_count"]}
              for x in rows]
    return {"swCode": r[0]["sw_code"], "name": r[0]["name"], "level": r[0]["level"],
            "series": series}


def stocks_flow(conn, sw_code, start, end, limit):
    """行业成分股资金流排行。行业不存在返回 None（由路由转 40401）。

    注意：这是**区间累计**口径，与 /industries 的 todayNet（当日值）同名不同义，
    见 docs/03 §3.9。
    """
    if not db.query_one(conn, "SELECT 1 FROM t_industry WHERE sw_code=%s", (sw_code,)):
        return None
    s, e = norm_range(start, end)
    where = ["m.index_code = %s", "m.in_date <= mf.trade_date",
             "(m.out_date IS NULL OR m.out_date > mf.trade_date)"]
    args = [sw_code]
    if s:
        where.append("mf.trade_date >= %s")
        args.append(s)
    if e:
        where.append("mf.trade_date <= %s")
        args.append(e)
    rows = db.query_dict(conn, """
        SELECT m.con_code AS ts_code, s.name,
          ROUND(SUM(mf.net_mf_amount)/10000, 4) AS net,
          ROUND(SUM((mf.buy_lg_amount - mf.sell_lg_amount)
                  + (mf.buy_elg_amount - mf.sell_elg_amount))/10000, 4) AS main,
          ROUND(SUM(mf.buy_sm_amount + mf.buy_md_amount
                  + mf.buy_lg_amount + mf.buy_elg_amount)/10000, 4) AS turnover
        FROM t_industry_member m
        JOIN t_moneyflow mf ON mf.ts_code = m.con_code
        LEFT JOIN t_stock_basic s ON s.ts_code = m.con_code
        WHERE """ + " AND ".join(where) + """
        GROUP BY m.con_code, s.name
        ORDER BY net DESC
        LIMIT %s
    """, args + [limit])
    lst = [{"tsCode": r["ts_code"], "name": r["name"], "net": fnum(r["net"]),
            "main": fnum(r["main"]), "turnover": fnum(r["turnover"])} for r in rows]
    return {"swCode": sw_code, "list": lst}


def strength(conn):
    """全市场二级行业强度：net / turnover，**全历史累计**口径（见 docs/03 §3.10）。"""
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
        t = fnum(r["turnover"]) or 0.0
        net = fnum(r["net"]) or 0.0
        lst.append({"swCode": r["sw_code"], "name": r["name"], "l1Name": r["l1_name"],
                    "net": net, "turnover": t,
                    "strength": round(net / t, 6) if t > 0 else None})
    return {"list": lst}


# ───────────────────────── 带缓存的门面 ─────────────────────────
def list_industries_cached(conn, level, l1, q, page, page_size):
    return cached("industries", (level, l1, q, page, page_size),
                  lambda: list_industries(conn, level, l1, q, page, page_size))


def tree_cached(conn):
    return cached("tree", (), lambda: tree(conn))


def strength_cached(conn):
    return cached("strength", (), lambda: strength(conn))
