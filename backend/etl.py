# -*- coding: utf-8 -*-
"""P0 ETL：建表 + 同步分类/成分股/资金流 + 聚合行业资金流。

用法：python etl.py  （在 backend 目录或指定绝对路径运行均可）
日志写入 config.log_file（默认 backend/etl_run.log）。
"""
import sys
import time
import traceback
from datetime import date, timedelta

from config import Config
from tushare_client import TushareClient, DataSourceError
import db

MF_NUM = ["buy_sm_vol", "buy_sm_amount", "sell_sm_vol", "sell_sm_amount",
          "buy_md_vol", "buy_md_amount", "sell_md_vol", "sell_md_amount",
          "buy_lg_vol", "buy_lg_amount", "sell_lg_vol", "sell_lg_amount",
          "buy_elg_vol", "buy_elg_amount", "sell_elg_vol", "sell_elg_amount",
          "net_mf_vol", "net_mf_amount"]
MF_COLS = ["ts_code", "trade_date"] + MF_NUM
MF_INSERT = (
    "INSERT INTO t_moneyflow (" + ",".join(MF_COLS) + ") VALUES ("
    + ",".join(f"%({c})s" for c in MF_COLS) + ") ON DUPLICATE KEY UPDATE "
    + ",".join(f"{c}=VALUES({c})" for c in MF_NUM) + ", updated_at=NOW()"
)


class Logger:
    def __init__(self, path):
        self.f = open(path, "w", encoding="utf-8")

    def log(self, msg):
        line = f"[{time.strftime('%H:%M:%S')}] {msg}"
        print(line, flush=True)
        self.f.write(line + "\n")
        self.f.flush()

    def close(self):
        self.f.close()


def num(v):
    if v is None or v == "" or v == "None":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def d(v):
    """date 字符串转 DATE，空值返回 None（in_date 用默认旧日期）"""
    if v is None or v == "" or v == "None":
        return None
    s = str(v).strip()
    return s if len(s) == 8 and s.isdigit() else None


def sync_trade_cal(client, conn, log):
    today = date.today()
    start = (today - timedelta(days=400)).strftime("%Y%m%d")
    end = today.strftime("%Y%m%d")
    rows, _ = client.call("trade_cal", exchange="SSE", start_date=start, end_date=end, is_open="1")
    data = [{"cal_date": r["cal_date"], "exchange": r["exchange"],
             "is_open": 1, "pretrade_date": d(r.get("pretrade_date"))} for r in rows]
    n = db.executemany(conn, (
        "INSERT INTO t_trade_cal (cal_date, exchange, is_open, pretrade_date) "
        "VALUES (%(cal_date)s, %(exchange)s, %(is_open)s, %(pretrade_date)s) "
        "ON DUPLICATE KEY UPDATE is_open=VALUES(is_open), pretrade_date=VALUES(pretrade_date)"
    ), data)
    conn.commit()
    log.log(f"trade_cal 写入 {n} 行（区间 {start}~{end}）")


def sync_industry(client, conn, log):
    # 拉取 L1/L2/L3
    raw = {}
    for lvl in ("L1", "L2", "L3"):
        rows, _ = client.call("index_classify", level=lvl, src="SW2021")
        raw[lvl] = rows
        log.log(f"index_classify {lvl}: {len(rows)} 行")
    # 建立 industry_code -> sw_code 与 name 映射
    code2sw = {}
    name_by_sw = {}
    for lvl in ("L1", "L2", "L3"):
        for r in raw[lvl]:
            sw = r["index_code"].replace(".SI", "")
            code2sw[r["industry_code"]] = sw
            name_by_sw[sw] = r["industry_name"]
    # 组装层级
    lvl_int = {"L1": 1, "L2": 2, "L3": 3}
    data = []
    for lvl in ("L1", "L2", "L3"):
        for r in raw[lvl]:
            sw = r["index_code"].replace(".SI", "")
            lv = lvl_int[lvl]
            parent_sw = code2sw.get(r["parent_code"]) if r["parent_code"] != "0" else None
            l1_name = None
            if lv == 1:
                l1_name = r["industry_name"]
            elif lv == 2 and parent_sw:
                l1_name = name_by_sw.get(parent_sw)
            elif lv == 3 and parent_sw:
                gp = _parent_sw(raw, code2sw, parent_sw)
                l1_name = name_by_sw.get(gp)
            data.append({"sw_code": sw, "name": r["industry_name"], "level": lv,
                         "parent_code": parent_sw, "industry_code": r["industry_code"],
                         "l1_name": l1_name, "is_pub": r.get("is_pub")})
    n = db.executemany(conn, (
        "INSERT INTO t_industry (sw_code, name, level, parent_code, industry_code, l1_name, is_pub) "
        "VALUES (%(sw_code)s, %(name)s, %(level)s, %(parent_code)s, %(industry_code)s, %(l1_name)s, %(is_pub)s) "
        "ON DUPLICATE KEY UPDATE name=VALUES(name), level=VALUES(level), parent_code=VALUES(parent_code), "
        "industry_code=VALUES(industry_code), l1_name=VALUES(l1_name), is_pub=VALUES(is_pub), updated_at=NOW()"
    ), data)
    conn.commit()
    log.log(f"t_industry 写入 {n} 行")


def _parent_sw(raw, code2sw, l2_sw):
    """给定 L2 的 sw_code，返回其 L1 的 sw_code"""
    for r in raw["L2"]:
        if r["index_code"].replace(".SI", "") == l2_sw:
            return code2sw.get(r["parent_code"])
    return None


def sync_stock_basic(client, conn, log):
    rows, _ = client.call("stock_basic")
    data = [{"ts_code": r["ts_code"], "symbol": r.get("symbol"), "name": r.get("name"),
             "area": r.get("area"), "industry": r.get("industry"), "market": r.get("market"),
             "list_date": d(r.get("list_date"))} for r in rows]
    n = db.executemany(conn, (
        "INSERT INTO t_stock_basic (ts_code, symbol, name, area, industry, market, list_date) "
        "VALUES (%(ts_code)s, %(symbol)s, %(name)s, %(area)s, %(industry)s, %(market)s, %(list_date)s) "
        "ON DUPLICATE KEY UPDATE symbol=VALUES(symbol), name=VALUES(name), area=VALUES(area), "
        "industry=VALUES(industry), market=VALUES(market), list_date=VALUES(list_date), updated_at=NOW()"
    ), data)
    conn.commit()
    log.log(f"t_stock_basic 写入 {n} 行")


def sync_members(client, conn, log, level=2):
    codes = [r[0] for r in db.query(conn, "SELECT sw_code FROM t_industry WHERE level=%s ORDER BY sw_code", (level,))]
    log.log(f"index_member 同步 L{level} 行业 {len(codes)} 个 …")
    total = 0
    fails = 0
    for i, code in enumerate(codes):
        try:
            rows, _ = client.call("index_member", index_code=code + ".SI")
        except Exception as e:  # noqa: BLE001
            fails += 1
            if fails <= 5:
                log.log(f"  {code} index_member 失败: {e}")
            continue
        data = []
        for r in rows:
            in_d = d(r.get("in_date")) or "19900101"
            data.append({"index_code": code, "con_code": r["con_code"],
                         "in_date": in_d, "out_date": d(r.get("out_date")),
                         "is_new": r.get("is_new")})
        if data:
            db.executemany(conn, (
                "INSERT INTO t_industry_member (index_code, con_code, in_date, out_date, is_new) "
                "VALUES (%(index_code)s, %(con_code)s, %(in_date)s, %(out_date)s, %(is_new)s) "
                "ON DUPLICATE KEY UPDATE out_date=VALUES(out_date), is_new=VALUES(is_new), updated_at=NOW()"
            ), data)
            total += len(data)
        if (i + 1) % 40 == 0:
            conn.commit()
            log.log(f"  members 进度 {i+1}/{len(codes)}，累计 {total} 行")
    conn.commit()
    log.log(f"t_industry_member 写入 {total} 行（失败 {fails} 个）")


def sync_moneyflow(client, conn, log, days):
    ds = [r[0] for r in db.query(conn, (
        "SELECT cal_date FROM t_trade_cal WHERE is_open=1 AND cal_date <= CURDATE() "
        "ORDER BY cal_date DESC LIMIT %s"), (days,))]
    log.log(f"moneyflow 同步 {len(ds)} 个交易日（{ds[-1]} ~ {ds[0]}）…")
    total = 0
    zero_days = []
    for i, d_ in enumerate(ds):
        ds_str = d_.strftime("%Y%m%d") if hasattr(d_, "strftime") else str(d_)
        try:
            rows, has_more = client.call("moneyflow", trade_date=ds_str)
        except Exception as e:  # noqa: BLE001
            log.log(f"  {d_} 失败: {e}")
            continue
        if has_more:
            log.log(f"  !! {d_} has_more=True，可能被截断，需分页")
        if not rows:
            zero_days.append(d_)
            continue
        data = [{"ts_code": r["ts_code"], "trade_date": r["trade_date"],
                 **{c: num(r.get(c)) for c in MF_NUM}} for r in rows]
        db.executemany(conn, MF_INSERT, data)
        conn.commit()
        total += len(data)
        if (i + 1) % 10 == 0:
            log.log(f"  moneyflow 进度 {i+1}/{len(ds)}，累计 {total} 行")
    log.log(f"t_moneyflow 写入 {total} 行（无数据交易日 {len(zero_days)} 个: {zero_days[-6:] if zero_days else '无'}）")


def _agg_level(conn, level):
    codes = [r[0] for r in db.query(conn, "SELECT sw_code FROM t_industry WHERE level=%s ORDER BY sw_code", (level,))]
    sql = (
        "INSERT INTO t_industry_flow (sw_code, level, trade_date, sm, md, lg, elg, main, net, turnover, member_count) "
        "SELECT %(sw_code)s, %(level)s, mf.trade_date, "
        "ROUND(SUM(mf.buy_sm_amount - mf.sell_sm_amount)/10000, 4), "
        "ROUND(SUM(mf.buy_md_amount - mf.sell_md_amount)/10000, 4), "
        "ROUND(SUM(mf.buy_lg_amount - mf.sell_lg_amount)/10000, 4), "
        "ROUND(SUM(mf.buy_elg_amount - mf.sell_elg_amount)/10000, 4), "
        "ROUND(SUM((mf.buy_lg_amount - mf.sell_lg_amount) + (mf.buy_elg_amount - mf.sell_elg_amount))/10000, 4), "
        "ROUND(SUM(mf.net_mf_amount)/10000, 4), "
        "ROUND(SUM(mf.buy_sm_amount + mf.buy_md_amount + mf.buy_lg_amount + mf.buy_elg_amount)/10000, 4), "
        "COUNT(DISTINCT mf.ts_code) "
        "FROM t_moneyflow mf JOIN t_industry_member m ON m.con_code = mf.ts_code "
        "WHERE m.index_code = %(sw_code)s AND m.in_date <= mf.trade_date "
        "AND (m.out_date IS NULL OR m.out_date > mf.trade_date) "
        "GROUP BY mf.trade_date "
        "ON DUPLICATE KEY UPDATE sm=VALUES(sm), md=VALUES(md), lg=VALUES(lg), elg=VALUES(elg), "
        "main=VALUES(main), net=VALUES(net), turnover=VALUES(turnover), "
        "member_count=VALUES(member_count), updated_at=NOW()"
    )
    n = 0
    for i, code in enumerate(codes):
        n += db.execute(conn, sql, {"sw_code": code, "level": level})
        if (i + 1) % 50 == 0:
            conn.commit()
    conn.commit()
    return n


def aggregate_flow(conn, log):
    for lv in (2, 3):
        n = _agg_level(conn, lv)
        log.log(f"t_industry_flow L{lv} 聚合 {n} 行")
    # L1 = 二级子行业求和
    n = db.execute(conn, (
        "INSERT INTO t_industry_flow (sw_code, level, trade_date, sm, md, lg, elg, main, net, turnover, member_count) "
        "SELECT i.parent_code, 1, f.trade_date, "
        "ROUND(SUM(f.sm),4), ROUND(SUM(f.md),4), ROUND(SUM(f.lg),4), ROUND(SUM(f.elg),4), "
        "ROUND(SUM(f.main),4), ROUND(SUM(f.net),4), ROUND(SUM(f.turnover),4), SUM(f.member_count) "
        "FROM t_industry_flow f JOIN t_industry i ON i.sw_code = f.sw_code "
        "WHERE i.level=2 AND i.parent_code IS NOT NULL "
        "GROUP BY i.parent_code, f.trade_date "
        "ON DUPLICATE KEY UPDATE sm=VALUES(sm), md=VALUES(md), lg=VALUES(lg), elg=VALUES(elg), "
        "main=VALUES(main), net=VALUES(net), turnover=VALUES(turnover), "
        "member_count=VALUES(member_count), updated_at=NOW()"
    ))
    conn.commit()
    log.log(f"t_industry_flow L1 聚合 {n} 行")


def summary(conn, log):
    log.log("=== 入库汇总 ===")
    for t in ("t_trade_cal", "t_industry", "t_stock_basic", "t_industry_member",
              "t_moneyflow", "t_industry_flow"):
        r = db.query_one(conn, f"SELECT COUNT(*) FROM {t}")
        log.log(f"  {t}: {r[0]} 行")
    r = db.query_one(conn, "SELECT MIN(trade_date), MAX(trade_date) FROM t_moneyflow")
    log.log(f"  t_moneyflow 日期范围: {r[0]} ~ {r[1]}")
    r = db.query_one(conn, "SELECT MAX(trade_date) FROM t_industry_flow")
    log.log(f"  t_industry_flow 最新日期: {r[0]}")
    rows = db.query(conn, (
        "SELECT sw_code, trade_date, net, main, turnover, member_count FROM t_industry_flow "
        "WHERE trade_date=(SELECT MAX(trade_date) FROM t_industry_flow) ORDER BY ABS(net) DESC LIMIT 5"))
    log.log("  最新交易日净流入 Top5（亿元）:")
    for row in rows:
        log.log(f"    {row[0]} {row[1]} net={row[2]} main={row[3]} turnover={row[4]} members={row[5]}")


def main():
    cfg = Config()
    log = Logger(cfg.log_file)
    log.log(f"P0 ETL 启动 endpoint={cfg.endpoint} token_len={len(cfg.token)} db={cfg.db_host}:{cfg.db_port}/{cfg.db_name}")
    if not cfg.token:
        log.log("FATAL: 未读取到 token")
        return 2
    conn = None
    try:
        conn = db.connect(cfg)
        # 建库建表
        with open(__import__("os").path.join(__import__("os").path.dirname(__file__), "schema.sql"),
                  encoding="utf-8") as f:
            sqls = [s.strip() for s in f.read().split(";") if s.strip()]
        for s in sqls:
            db.execute(conn, s)
        conn.commit()
        log.log("schema 建表完成")

        client = TushareClient(cfg.endpoint, cfg.token)
        sync_trade_cal(client, conn, log)
        sync_industry(client, conn, log)
        sync_stock_basic(client, conn, log)
        sync_members(client, conn, log, level=2)
        sync_members(client, conn, log, level=3)
        sync_moneyflow(client, conn, log, cfg.moneyflow_days)
        aggregate_flow(conn, log)
        summary(conn, log)
        log.log("P0 ETL 完成")
        return 0
    except Exception:
        log.log("FATAL: " + traceback.format_exc())
        return 1
    finally:
        if conn:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
        log.close()


if __name__ == "__main__":
    sys.exit(main())
