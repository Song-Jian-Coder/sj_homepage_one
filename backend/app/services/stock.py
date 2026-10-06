# -*- coding: utf-8 -*-
"""个股相关查询：个股资金流序列、股票搜索。"""
import db
from app.deps import dstr, fnum, norm_range


def stock_flow(conn, ts_code, start, end):
    """个股资金流序列。股票不存在返回 None（由路由转 40401）。"""
    sb = db.query_dict(conn, "SELECT ts_code, name, industry FROM t_stock_basic WHERE ts_code=%s",
                       (ts_code,))
    if not sb:
        return None
    # 所属 L2 行业（数源保证每只股票最多一个活跃 L2，实测 5,191/5,191 均为 1）
    ind = db.query_one(conn, """
        SELECT i.sw_code, i.name FROM t_industry_member m
        JOIN t_industry i ON i.sw_code = m.index_code
        WHERE m.con_code=%s AND m.out_date IS NULL AND i.level=2 LIMIT 1
    """, (ts_code,))
    s, e = norm_range(start, end)
    where = ["ts_code = %s"]
    args = [ts_code]
    if s:
        where.append("trade_date >= %s")
        args.append(s)
    if e:
        where.append("trade_date <= %s")
        args.append(e)
    rows = db.query_dict(conn, """
        SELECT trade_date,
          ROUND((buy_sm_amount - sell_sm_amount)/10000, 4) AS sm,
          ROUND((buy_md_amount - sell_md_amount)/10000, 4) AS md,
          ROUND((buy_lg_amount - sell_lg_amount)/10000, 4) AS lg,
          ROUND((buy_elg_amount - sell_elg_amount)/10000, 4) AS elg,
          ROUND(((buy_lg_amount - sell_lg_amount)
               + (buy_elg_amount - sell_elg_amount))/10000, 4) AS main,
          ROUND(net_mf_amount/10000, 4) AS net,
          ROUND((buy_sm_amount + buy_md_amount
               + buy_lg_amount + buy_elg_amount)/10000, 4) AS turnover
        FROM t_moneyflow WHERE """ + " AND ".join(where) + " ORDER BY trade_date", args)
    series = [{"d": dstr(x["trade_date"]), "sm": fnum(x["sm"]), "md": fnum(x["md"]),
               "lg": fnum(x["lg"]), "elg": fnum(x["elg"]), "main": fnum(x["main"]),
               "net": fnum(x["net"]), "turnover": fnum(x["turnover"])}
              for x in rows]
    return {"tsCode": ts_code, "name": sb[0]["name"],
            "industry": ind[1] if ind else sb[0]["industry"],
            "industryCode": ind[0] if ind else None, "series": series}


def search(conn, q, page, page_size):
    """股票搜索（代码或名称模糊匹配）。

    原实现每行 4 个关联子查询：page_size=100 时一次请求就是 400 次子查询。
    这里改为「一次 join + 条件聚合」。等价性依据（已实测）：
    每只股票最多 1 个活跃 L2、1 个活跃 L3（5,191 只全部为 1），
    故 MAX(CASE ...) 与原先的 `LIMIT 1` 子查询取值一致。
    """
    like = "%" + q + "%"
    total = db.query_one(conn, (
        "SELECT COUNT(*) FROM t_stock_basic WHERE ts_code LIKE %s OR name LIKE %s"),
        (like, like))[0]
    rows = db.query_dict(conn, """
        SELECT s.ts_code, s.name, s.industry,
          MAX(CASE WHEN i.level = 2 THEN i.sw_code END) AS industry_code,
          MAX(CASE WHEN i.level = 2 THEN i.name    END) AS industry_name,
          MAX(CASE WHEN i.level = 3 THEN i.sw_code END) AS l3_code,
          MAX(CASE WHEN i.level = 3 THEN i.name    END) AS l3_name
        FROM t_stock_basic s
        LEFT JOIN t_industry_member m
               ON m.con_code = s.ts_code AND m.out_date IS NULL
        LEFT JOIN t_industry i
               ON i.sw_code = m.index_code AND i.level IN (2, 3)
        WHERE s.ts_code LIKE %s OR s.name LIKE %s
        GROUP BY s.ts_code, s.name, s.industry
        ORDER BY s.ts_code LIMIT %s OFFSET %s
    """, (like, like, page_size, (page - 1) * page_size))
    # l3Code/l3Name 为 null 表示该股票没有三级行业归属（数源未提供），
    # 前端应把它渲染到所属二级行业的「未细分到三级」分组下。
    lst = [{"tsCode": r["ts_code"], "name": r["name"],
            "industry": r["industry_name"] or r["industry"],
            "industryCode": r["industry_code"],
            "l3Code": r["l3_code"], "l3Name": r["l3_name"],
            "hasL3": r["l3_code"] is not None} for r in rows]
    return {"list": lst, "total": total, "page": page, "page_size": page_size}
