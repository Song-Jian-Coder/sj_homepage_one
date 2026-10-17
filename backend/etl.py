# -*- coding: utf-8 -*-
"""P0 ETL：建表 + 同步分类/成分股/资金流 + 聚合行业资金流。

用法：python etl.py [all|meta|members|moneyflow|aggregate|incremental]
  all(默认) 全量 / meta 仅分类基础 / members 仅成分股+聚合(补漏用) /
  moneyflow 仅资金流+聚合 / aggregate 仅聚合 /
  incremental 仅补缺失日期（含历史空洞自愈，最快）
日志写入 config.log_file（默认 backend/etl_run.log），
审计写入表 t_fetch_log（task_name/status/start_at/end_at/rows_fetched/rows_written/error_msg）。

退出码：
  0 = 成功；1 = 致命异常；2 = 未读到 token；3 = 跑完但有失败项
      （单日采集失败 / 翻页 has_more 未能消化 / 成分股最终失败）。
  API 的 /admin/fetch 会读取 t_fetch_log 判断是否仍有任务在跑（跨进程护栏）。

资金流相关环境变量：
  MONEYFLOW_DAYS        主窗口交易日数（默认 40）
  MONEYFLOW_MAX_ROWS    单页行数上限（默认 6000，= 数据源实测硬顶）
  MONEYFLOW_GAP_MAX_DAYS 空洞回填单轮上限（默认 50）
"""
import os
import sys
import time
import traceback
from datetime import date, datetime, timedelta, timezone

from config import Config
from tushare_client import PAGE_LIMIT, TushareClient
import db

MF_NUM = ["buy_sm_vol", "buy_sm_amount", "sell_sm_vol", "sell_sm_amount",
          "buy_md_vol", "buy_md_amount", "sell_md_vol", "sell_md_amount",
          "buy_lg_vol", "buy_lg_amount", "sell_lg_vol", "sell_lg_amount",
          "buy_elg_vol", "buy_elg_amount", "sell_elg_vol", "sell_elg_amount",
          "net_mf_vol", "net_mf_amount"]
MF_COLS = ["ts_code", "trade_date"] + MF_NUM
# 残缺日判据：当日行数 < max(上一交易日行数, 近 7 个交易日均值) × 该比率 → 判为缺失。
# 真数据合法最低比率为 99.347%，故取 99.2% 留余量（详见 _scan_gaps 注释）。
MF_ROW_DEFICIT_RATIO = 0.992
# 单日/单接口翻页上限（服务端硬顶 6000 行/页，正常单日 1 页；仅作契约异常时的跑飞护栏）
MAX_FETCH_PAGES = 50
# 聚合的「窗口内交易日」数量超过该值就直接全量重算（IN 列表过长没有收益）
AGG_INLINE_MAX_DAYS = 30

MF_INSERT = (
    "INSERT INTO t_moneyflow (" + ",".join(MF_COLS) + ") VALUES ("
    + ",".join(f"%({c})s" for c in MF_COLS) + ") ON DUPLICATE KEY UPDATE "
    + ",".join(f"{c}=VALUES({c})" for c in MF_NUM) + ", updated_at=NOW()"
)


class Logger:
    """ETL 文本日志（管理台读尾部展示进度）。

    注意：以 "w" 打开同一路径，因此**必须**保证同一时刻只有一个 ETL 在跑。
    现在的护栏有两层：API 侧 FetchGate + t_fetch_log 的 running 行（跨进程）。
    """

    def __init__(self, path):
        self.f = open(path, "w", encoding="utf-8")

    def log(self, msg):
        line = f"[{time.strftime('%H:%M:%S')}] {msg}"
        print(line, flush=True)
        self.f.write(line + "\n")
        self.f.flush()

    def close(self):
        self.f.close()


class RunReport:
    """一次采集的累计统计与「失败项」收集，用于日志与退出码。"""

    def __init__(self, log):
        self.log = log
        self.rows_fetched = 0
        self.rows_written = 0
        self.failed_days = []
        self.problems = []

    def problem(self, msg):
        if msg not in self.problems:
            self.problems.append(msg)
        self.log.log("  !! " + msg)


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


def today_cn(cfg):
    """Asia/Shanghai 的「今天」。

    原实现用 `date.today()`（进程本地时区）与 MySQL `CURDATE()`（会话时区）混用；
    主机跑 UTC 时盘后运行会把当日交易日判为未来日而空转一天。
    现统一：进程侧按 cfg.tz_offset_hours 计算，DB 侧由 db.connect 设 SET time_zone。
    """
    return datetime.now(timezone(timedelta(hours=cfg.tz_offset_hours))).date()


def _call_paged(client, log, rep, api, page_size=None, **params):
    """通用分页拉取：返回 (rows, truncated)。

    修掉审查报告 5.1.3：trade_cal / index_classify / stock_basic / index_member
    原先都用 `rows, _ = client.call(...)` 把 has_more 直接丢弃。
    实测 t_stock_basic 已 5,571 行 = 服务端 6000 硬顶的 93%，再上市约 430 只
    就会静默截断（搜索/成分股名缺失、行业聚合金额偏低，日志却完全看不出来）。

    offset 步长改为「本页实回行数」：服务端若少回（< limit）也不会跳行。
    """
    page_size = page_size or PAGE_LIMIT
    rows, offset, truncated = [], 0, False
    for _ in range(MAX_FETCH_PAGES):
        part, has_more = client.call(api, max_rows=page_size,
                                     limit=page_size, offset=offset, **params)
        rows.extend(part)
        rep.rows_fetched += len(part)
        if not has_more:
            break
        if not part:
            # has_more=True 却返回 0 行：契约异常，避免 offset 不前进导致死循环
            truncated = True
            log.log(f"  !! {api}{params} has_more=True 但本页 0 行，终止翻页（offset={offset}）")
            break
        offset += len(part)
    else:
        truncated = True
        log.log(f"  !! {api}{params} 连续翻满 {MAX_FETCH_PAGES} 页仍 has_more=True，"
                f"已取 {len(rows)} 行后放弃（防跑飞）")
    if truncated:
        rep.problem(f"{api}{params} 分页未消化完（has_more 仍为真，可能被静默截断）")
    return rows, truncated


def _fetch_day_paged(client, log, rep, ds_str, page_size, api="moneyflow", **extra):
    """按 offset 翻页拉取单日数据（P2 截断防护）。

    接口契约（实测，见 tushare_client.call 注释）：
      - 服务端忽略请求里的 max_rows，硬顶固定 6000 行/次；
      - 超限返回 has_more=True，必须带 offset 再取下一页（offset = 跳过前 N 条，精确）；
      - 末页 has_more=False。

    历史上这里在 has_more=True 时不带 offset 重发同一个请求，只会反复拿到同一批
    6000 行，靠翻页护栏空转 50 次——等于没有分页能力。现改为累加 offset。

    trade_date 由本函数的 ds_str 参数决定，**不接受**由 extra 传入：
    2026-10-17 的实跑就栽在这里——sync_moneyflow 既传位置参数 ds_str，又传
    trade_date=ds_str，`**extra` 展开后直接 TypeError，7 个交易日全军覆没
    （错误还被逐日 try/except 吞成「单日失败」，只有翻日志才看得见）。
    现改为显式构造 params 并覆盖，调用方多传一次也不会炸。
    """
    params = dict(extra)
    params["trade_date"] = ds_str
    return _call_paged(client, log, rep, api, page_size=page_size, **params)


# ───────────────────────── 审计（t_fetch_log）─────────────────────────
def audit_start(conn, task):
    """插入一条 running 审计行，返回 id（失败返回 None）。

    修掉审查报告 A1：t_fetch_log 建了表但从来没有任何写入方，
    可观测性设计（docs/01 §8）形同虚设，日志是唯一的审计来源。
    """
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO t_fetch_log (task_name, status, start_at) "
                        "VALUES (%s, 'running', NOW())", (task,))
            rid = cur.lastrowid
        conn.commit()
        return rid
    except Exception as e:  # noqa: BLE001
        conn.rollback()
        print("[WARN] 写 t_fetch_log 失败（采集继续）: %s" % e, flush=True)
        return None


def audit_finish(conn, rid, status, rows_fetched, rows_written, error_msg=None):
    if rid is None:
        return
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE t_fetch_log SET status=%s, end_at=NOW(), rows_fetched=%s, "
                        "rows_written=%s, error_msg=%s WHERE id=%s",
                        (status, rows_fetched, rows_written,
                         (error_msg or "")[:2000] or None, rid))
        conn.commit()
    except Exception as e:  # noqa: BLE001
        conn.rollback()
        print("[WARN] 更新 t_fetch_log 失败: %s" % e, flush=True)


def sync_trade_cal(client, conn, log, rep, cfg):
    today = today_cn(cfg)
    start = (today - timedelta(days=400)).strftime("%Y%m%d")
    end = today.strftime("%Y%m%d")
    rows, _ = _call_paged(client, log, rep, "trade_cal",
                          exchange="SSE", start_date=start, end_date=end, is_open="1")
    data = [{"cal_date": r["cal_date"], "exchange": r["exchange"],
             "is_open": 1, "pretrade_date": d(r.get("pretrade_date"))} for r in rows]
    n = db.executemany(conn, (
        "INSERT INTO t_trade_cal (cal_date, exchange, is_open, pretrade_date) "
        "VALUES (%(cal_date)s, %(exchange)s, %(is_open)s, %(pretrade_date)s) "
        "ON DUPLICATE KEY UPDATE is_open=VALUES(is_open), pretrade_date=VALUES(pretrade_date)"
    ), data)
    conn.commit()
    rep.rows_written += len(data)
    log.log(f"trade_cal 提交 {len(data)} 行 / {len(rows)} 行取回（affected={n}，"
            f"含更新重复计数）；区间 {start}~{end}")


def sync_industry(client, conn, log, rep):
    # 拉取 L1/L2/L3
    raw = {}
    for lvl in ("L1", "L2", "L3"):
        rows, _ = _call_paged(client, log, rep, "index_classify", level=lvl, src="SW2021")
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
        "VALUES (%(sw_code)s, %(name)s, %(level)s, %(parent_code)s, %(industry_code)s, "
        "%(l1_name)s, %(is_pub)s) "
        "ON DUPLICATE KEY UPDATE name=VALUES(name), level=VALUES(level), parent_code=VALUES(parent_code), "
        "industry_code=VALUES(industry_code), l1_name=VALUES(l1_name), is_pub=VALUES(is_pub), updated_at=NOW()"
    ), data)
    conn.commit()
    rep.rows_written += len(data)
    log.log(f"t_industry 提交 {len(data)} 行（affected={n}）")


def _parent_sw(raw, code2sw, l2_sw):
    """给定 L2 的 sw_code，返回其 L1 的 sw_code"""
    for r in raw["L2"]:
        if r["index_code"].replace(".SI", "") == l2_sw:
            return code2sw.get(r["parent_code"])
    return None


def sync_stock_basic(client, conn, log, rep):
    # 该接口已实测达服务端 6000 行硬顶的 93%，必须翻页
    rows, _ = _call_paged(client, log, rep, "stock_basic")
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
    rep.rows_written += len(data)
    log.log(f"t_stock_basic 提交 {len(data)} 行（affected={n}）；"
            f"服务端硬顶 {PAGE_LIMIT} 行/次，已翻页拉全")


def sync_members(client, conn, log, rep, level=2):
    """同步某层级全部行业的成分股。

    - 源端返回空 → 计入 empty（该行业在数据源确实没有成分，不是失败）
    - 请求失败 → 收集后统一重扫一轮（40203 已由 TushareClient 内部退避）
    - 第二轮仍失败 → 计入 rep.problem（影响退出码 3）
    """
    codes = [r[0] for r in db.query(
        conn, "SELECT sw_code FROM t_industry WHERE level=%s ORDER BY sw_code", (level,))]
    log.log(f"index_member 同步 L{level} 行业 {len(codes)} 个 …")
    state = {"total": 0}
    empty = []

    def one_pass(pending, tag):
        still = []
        for i, code in enumerate(pending):
            try:
                rows, _ = _call_paged(client, log, rep, "index_member", index_code=code + ".SI")
            except Exception as e:  # noqa: BLE001
                still.append(code)
                if len(still) <= 5:
                    log.log(f"  {code} index_member 失败: {e}")
                continue
            if not rows:
                empty.append(code)
                continue
            data = []
            for r in rows:
                in_d = d(r.get("in_date")) or "19900101"
                data.append({"index_code": code, "con_code": r["con_code"],
                             "in_date": in_d, "out_date": d(r.get("out_date")),
                             "is_new": r.get("is_new")})
            affected = db.executemany(conn, (
                "INSERT INTO t_industry_member (index_code, con_code, in_date, out_date, is_new) "
                "VALUES (%(index_code)s, %(con_code)s, %(in_date)s, %(out_date)s, %(is_new)s) "
                "ON DUPLICATE KEY UPDATE out_date=VALUES(out_date), is_new=VALUES(is_new), updated_at=NOW()"
            ), data)
            state["total"] += len(data)
            rep.rows_written += len(data)
            if (i + 1) % 40 == 0:
                conn.commit()
                log.log(f"  members[{tag}] 进度 {i+1}/{len(pending)}，累计 {state['total']} 行"
                        f"（affected={affected} 为上一批）")
        conn.commit()
        return still

    failed = one_pass(codes, "第1轮")
    if failed:
        log.log(f"  第1轮后仍有 {len(failed)} 个失败，等待 60s 后重扫 …")
        time.sleep(60)
        failed = one_pass(failed, "第2轮")

    log.log(f"t_industry_member 提交 {state['total']} 行"
            f"（源端空结果 {len(empty)} 个，最终失败 {len(failed)} 个）")
    if empty:
        log.log(f"  源端无成分数据（数据源限制）{len(empty)} 个: "
                + ", ".join(empty[:40]) + (" …" if len(empty) > 40 else ""))
    if failed:
        rep.problem(f"t_industry_member L{level} 最终仍失败 {len(failed)} 个: "
                    + ", ".join(failed[:20]))
    return state["total"]


def sync_moneyflow(client, conn, log, rep, days, mode="full",
                   page_size=6000, gap_max_days=50, cfg=None):
    """同步个股资金流。返回本次**实际写入的交易日**列表（供增量聚合使用）。

    mode="full"        —— 拉最近 days 个交易日（覆盖式）
    mode="incremental" —— ① 补齐晚于库内最新日期的交易日；
                          ② 每次无条件全量比对交易日历，回填「日历有、库内无」
                             的普通交易日（历史空洞自愈）；
                          ③ 顺带回填行数显著低于近期水平的残缺日（疑似静默截断）。

    空洞判定口径（避免误判）：
      - 只认 t_trade_cal 中 is_open=1 的日期，减少代理日历与交易日历不一致带来的误判；
      - 扫描下界取「库内最早交易日」，不早于此（绝不无限回溯到 1990 年代）；
      - 当日行数为 0 且附近有数据 → 判为「整天缺失」；
      - 行数低于「上一交易日行数」或「近 7 个交易日行数均值」的 99.2%（MF_ROW_DEFICIT_RATIO）
        判为「部分缺失/疑似截断」，同样覆盖式回填。
        阈值依据真数据实测：242 天内合法日最低比率为 99.347%（连跌渐进出清），
        故取 99.2%，零误报；可检出约 0.8% 以上的缺失，更小的缺失需人工对账发现。

    退出码/失败上抛（审查报告 5.1.8）：逐日失败原先只写日志、不影响退出码，
    调用方无法知道「有 N 天没采到」。现在失败日计入 rep.failed_days 与 rep.problems。
    """
    # ① 主窗口：日历中已收盘的最近 days 个交易日（降序），
    #    下界不早于库内最早交易日（覆盖窗口即采集窗口）
    all_days = [r[0] for r in db.query(conn, (
        "SELECT cal_date FROM t_trade_cal WHERE is_open=1 AND cal_date <= CURDATE() "
        "ORDER BY cal_date DESC LIMIT %s"), (days,))]

    latest = db.query_one(conn, "SELECT MAX(trade_date) FROM t_moneyflow")
    latest = latest[0] if latest else None

    if mode == "incremental":
        ds = [x for x in all_days if latest is None or x > latest]
        if latest is None:
            log.log("增量模式：库中无资金流数据，按最近窗口全量拉取")
        else:
            log.log(f"增量模式：库中最新 {latest}，新交易日 {len(ds)} 个")
        # ② + ③ 空洞/残缺扫描（扫描下界 = 库内最早交易日，而非主窗口，见 _scan_gaps）
        ds = _scan_gaps(conn, log, ds, all_days, latest, gap_max_days)
    else:
        ds = all_days

    if not ds:
        log.log("moneyflow 无需同步（数据已是最新，且无空洞）")
        return []

    log.log(f"moneyflow 同步 {len(ds)} 个交易日（{ds[-1]} ~ {ds[0]}）…")
    total = written = 0
    zero_days, failed, truncated_days, written_dates = [], [], [], []
    pending = 0                                          # 待提交的交易日数
    for i, d_ in enumerate(ds):
        ds_str = d_.strftime("%Y%m%d") if hasattr(d_, "strftime") else str(d_)
        try:
            rows, truncated = _fetch_day_paged(client, log, rep, ds_str, page_size,
                                               api="moneyflow")
            if truncated:
                truncated_days.append(ds_str)
            if not rows:
                zero_days.append(ds_str)
                continue
            data = [{"ts_code": r["ts_code"], "trade_date": r["trade_date"],
                     **{c: num(r.get(c)) for c in MF_NUM}} for r in rows]
            db.executemany(conn, MF_INSERT, data)
            total += len(data)
            rep.rows_written += len(data)
            written += 1
            written_dates.append(ds_str)
            pending += 1
        except Exception as e:  # noqa: BLE001
            conn.rollback()                              # 丢弃半写批次，保持批次边界干净
            failed.append(f"{ds_str}({str(e)[:80]})")
            log.log(f"  {d_} 失败: {e}")
            pending = 0
            continue
        # 每 10 个交易日提交一次（原实现逐日 commit，242 天 = 242 次 fsync）
        if pending >= 10:
            conn.commit()
            pending = 0
            log.log(f"  moneyflow 进度 {i+1}/{len(ds)}，累计 {total} 行")
    if pending:
        conn.commit()
    log.log(f"t_moneyflow 提交 {total} 行 / {written} 个交易日"
            f"（无数据交易日 {len(zero_days)} 个: {zero_days[:6] if zero_days else '无'}）")
    if truncated_days:
        log.log(f"  !! 疑似截断交易日 {len(truncated_days)} 个: {truncated_days[:6]}")
    if failed:
        rep.failed_days = failed
        rep.problem(f"最终失败交易日 {len(failed)} 个: {failed[:6]}（重跑 incremental 会自动回填）")
    return written_dates


def _scan_gaps(conn, log, ds, all_days, latest, gap_max_days):
    """全量比对交易日历，找出需要回填的交易日（P1 空洞自愈）。

    返回合并且去重（降序）后的待处理交易日列表。

    扫描窗口 = [库内最早交易日, 主窗口最新日]。
    原实现用 `min(all_days)` 作下界，而 all_days 受 MONEYFLOW_DAYS 限制，
    于是扫描范围恰好等于主窗口，**更早的历史空洞永远不会被发现**
    （审查报告 5.1.7），与 docstring 声称的「不再受回溯窗口限制」不符。
    """
    if latest is None:                       # 库空 → 主窗口即全量，无需扫
        return ds
    if not all_days:
        return ds
    db_min_row = db.query_one(conn, "SELECT MIN(trade_date) FROM t_moneyflow")
    db_min = db_min_row[0] if db_min_row else None
    win_min = min(db_min, min(all_days)) if db_min else min(all_days)
    win_max = max(all_days)
    # 注意：不要用「关联子查询」统计每个日历日的近 60 日行数——EXPLAIN 显示
    # 它会对每个日历日做一次全表扫描（type=ALL, rows=609910），243 天即上亿行读，
    # 实测直接把 incremental 卡死在 10 分钟以上仍无输出。
    # 这里改为预聚合 + 窗口帧（窗口在 WHERE is_open=1 之后求值，故按「交易日」滑动）：
    #   expected    上一交易日行数
    #   recent_total 之前 61 个交易日行数合计（用于区分「真空洞」与「代理自身空日」）
    #   baseline7   之前 7 个交易日行数均值，作为近期应有水平
    # 注：MySQL 8.0 没有 MEDIAN()（那是 MariaDB 的函数），故用窗口 AVG。
    rows = db.query(conn, """
        SELECT c.cal_date,
               COALESCE(m.n, 0) AS n,
               COALESCE(SUM(COALESCE(m.n, 0)) OVER (
                   ORDER BY c.cal_date ROWS BETWEEN 61 PRECEDING AND 1 PRECEDING), 0) AS recent_total,
               COALESCE(LAG(COALESCE(m.n, 0)) OVER (ORDER BY c.cal_date), 0) AS expected,
               COALESCE(AVG(COALESCE(m.n, 0)) OVER (
                   ORDER BY c.cal_date ROWS BETWEEN 7 PRECEDING AND 1 PRECEDING), 0) AS baseline7
        FROM t_trade_cal c
        LEFT JOIN (SELECT trade_date, COUNT(*) AS n FROM t_moneyflow GROUP BY trade_date) m
               ON m.trade_date = c.cal_date
        WHERE c.is_open = 1
          AND c.cal_date >= %s AND c.cal_date <= %s
        ORDER BY c.cal_date DESC
    """, (win_min, win_max))

    missing, partial = [], []
    for cal_date, n, recent_total, expected, baseline7 in rows:
        if n == 0:
            if recent_total > 0:             # 附近有数据 → 是真空洞，不是代理自带的空日
                missing.append(cal_date)
            continue
        # 行数低于最近应有水平 → 判为「静默截断」的残缺日，一并覆盖式回填（幂等）。
        # 阈值取值依据（真数据实测，非拍脑袋）：
        #   合法日行数 / max(上一日, 近7日均值) 的最低比率为 99.347%（2026-04-28，
        #   连跌渐进出清所致）；故 99.2% 在真实下限之外留 0.15pt 余量，零误报，
        #   同时能抓住约 0.8% 以上的缺失（含行级缺失 1 行，如 5549/5550=99.98%）。
        # 注意：AVG()/SUM() 在 MySQL 返回 DECIMAL，pymysql 映射为 decimal.Decimal，
        # 与 float 直接运算会抛 TypeError，故显式转 float。
        base = float(max(expected or 0, baseline7 or 0))
        if base > 0 and n < base * MF_ROW_DEFICIT_RATIO:
            partial.append((cal_date, n, base))
            missing.append(cal_date)

    if partial:
        log.log(f"  !! 疑似截断/残缺交易日 {len(partial)} 个"
                f"（行数 < 上一日或近 7 日均值的 {MF_ROW_DEFICIT_RATIO:.1%}）: "
                + ", ".join(f"{c}({n}/{int(b)})" for c, n, b in partial[:8]))

    if not missing:
        log.log(f"  空洞扫描：{len(rows)} 个交易日全齐（扫描区间 {win_min} ~ {win_max}）")
        return ds

    missing = sorted(set(missing), reverse=True)
    log.log(f"  !! 空洞扫描发现 {len(missing)} 个待回填交易日: "
            f"{[str(x) for x in missing[:10]]}{' …' if len(missing) > 10 else ''}")
    if len(missing) > gap_max_days:
        log.log(f"  !! 超过单轮上限 {gap_max_days} 天，本轮只回填最近的 {gap_max_days} 天，"
                f"剩余 {len(missing) - gap_max_days} 天待下次运行")
        missing = missing[:gap_max_days]
    return sorted(set(ds) | set(missing), reverse=True)


def _agg_level(conn, level, log, dates=None):
    """按 level 聚合 t_industry_flow。返回 (inserted, updated, affected)。

    dates 为 None → 全量重算该层级全部行业；
    dates 为列表 → 只重算这些交易日（增量），空列表直接跳过。

    审查报告 5.3.4：原实现每次 480 条 INSERT...SELECT 全量重算，
    实测一次「无需同步」的 incremental 仍耗时 44 秒。现在增量模式下
    只处理本次真正写入的交易日；没有新数据时直接跳过。
    """
    if dates is not None and not dates:
        log.log(f"t_industry_flow L{level}：本次无新增/回填交易日，跳过重算")
        return 0, 0, 0

    codes = [r[0] for r in db.query(
        conn, "SELECT sw_code FROM t_industry WHERE level=%s ORDER BY sw_code", (level,))]
    # 全位置参数（pymysql 不允许命名参数与 %s 混用）：sw_code 出现两次（SELECT 与 WHERE）
    sql = (
        "INSERT INTO t_industry_flow (sw_code, level, trade_date, sm, md, lg, elg, main, net, turnover, member_count) "
        "SELECT %s, %s, mf.trade_date, "
        "ROUND(SUM(mf.buy_sm_amount - mf.sell_sm_amount)/10000, 4), "
        "ROUND(SUM(mf.buy_md_amount - mf.sell_md_amount)/10000, 4), "
        "ROUND(SUM(mf.buy_lg_amount - mf.sell_lg_amount)/10000, 4), "
        "ROUND(SUM(mf.buy_elg_amount - mf.sell_elg_amount)/10000, 4), "
        "ROUND(SUM((mf.buy_lg_amount - mf.sell_lg_amount) + (mf.buy_elg_amount - mf.sell_elg_amount))/10000, 4), "
        "ROUND(SUM(mf.net_mf_amount)/10000, 4), "
        "ROUND(SUM(mf.buy_sm_amount + mf.buy_md_amount + mf.buy_lg_amount + mf.buy_elg_amount)/10000, 4), "
        "COUNT(DISTINCT mf.ts_code) "
        "FROM t_moneyflow mf JOIN t_industry_member m ON m.con_code = mf.ts_code "
        "WHERE m.index_code = %s AND m.in_date <= mf.trade_date "
        "AND (m.out_date IS NULL OR m.out_date > mf.trade_date) "
    )
    tail_args = []
    if dates:
        sql += "AND mf.trade_date IN (" + ",".join(["%s"] * len(dates)) + ") "
        tail_args = list(dates)
    sql += (
        "GROUP BY mf.trade_date "
        "ON DUPLICATE KEY UPDATE sm=VALUES(sm), md=VALUES(md), lg=VALUES(lg), elg=VALUES(elg), "
        "main=VALUES(main), net=VALUES(net), turnover=VALUES(turnover), "
        "member_count=VALUES(member_count), updated_at=NOW()"
    )
    # ODKU 的 affected_rows 不是写入行数（新增计 1、更新计 2、未变计 0），
    # 故用 COUNT(*) 前后差还原「真实新增」，再反推「真实更新」（审查报告 5.1.4）。
    before = db.table_count(conn, "t_industry_flow")
    affected = 0
    for i, code in enumerate(codes):
        affected += db.execute(conn, sql, [code, level, code] + tail_args)
        if (i + 1) % 50 == 0:
            conn.commit()
    conn.commit()
    after = db.table_count(conn, "t_industry_flow")
    inserted = max(0, after - before)
    updated = max(0, affected - inserted)
    return inserted, updated, affected


def aggregate_flow(conn, log, rep, dates=None):
    """聚合行业资金流。dates=None 全量重算；dates=[] 跳过（见 _agg_level）。"""
    for lv in (2, 3):
        ins, upd, aff = _agg_level(conn, lv, log, dates)
        if aff or ins:
            log.log(f"t_industry_flow L{lv}：真实新增 {ins} 行 / 真实更新 {upd} 行"
                    f"（affected={aff}，MySQL ODKU 语义：新增计 1、更新计 2、未变计 0）")
        else:
            log.log(f"t_industry_flow L{lv}：无需重算")
    if dates is not None and not dates:
        log.log("t_industry_flow L1：无新增交易日，跳过重算")
        return
    # L1 = 二级子行业求和
    sql = (
        "INSERT INTO t_industry_flow (sw_code, level, trade_date, sm, md, lg, elg, main, net, turnover, member_count) "
        "SELECT i.parent_code, 1, f.trade_date, "
        "ROUND(SUM(f.sm),4), ROUND(SUM(f.md),4), ROUND(SUM(f.lg),4), ROUND(SUM(f.elg),4), "
        "ROUND(SUM(f.main),4), ROUND(SUM(f.net),4), ROUND(SUM(f.turnover),4), SUM(f.member_count) "
        "FROM t_industry_flow f JOIN t_industry i ON i.sw_code = f.sw_code "
        "WHERE i.level=2 AND i.parent_code IS NOT NULL "
    )
    if dates:
        sql += "AND f.trade_date IN (" + ",".join(["%s"] * len(dates)) + ") "
    sql += (
        "GROUP BY i.parent_code, f.trade_date "
        "ON DUPLICATE KEY UPDATE sm=VALUES(sm), md=VALUES(md), lg=VALUES(lg), elg=VALUES(elg), "
        "main=VALUES(main), net=VALUES(net), turnover=VALUES(turnover), "
        "member_count=VALUES(member_count), updated_at=NOW()"
    )
    before = db.table_count(conn, "t_industry_flow")
    affected = db.execute(conn, sql, list(dates) if dates else None)
    conn.commit()
    after = db.table_count(conn, "t_industry_flow")
    inserted = max(0, after - before)
    log.log(f"t_industry_flow L1：真实新增 {inserted} 行 / 真实更新 "
            f"{max(0, affected - inserted)} 行（affected={affected}）")


def summary(conn, log):
    log.log("=== 入库汇总 ===")
    for t in ("t_trade_cal", "t_industry", "t_stock_basic", "t_industry_member",
              "t_moneyflow", "t_industry_flow", "t_fetch_log"):
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


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    task = (argv[0].strip().lower() if argv and argv[0].strip() else "all")
    if task not in ("all", "meta", "members", "moneyflow", "aggregate", "incremental"):
        task = "all"
    cfg = Config()
    log = Logger(cfg.log_file)
    rep = RunReport(log)
    log.log(f"P0 ETL 启动 task={task} endpoint={cfg.endpoint} "
            f"token_len={len(cfg.token)} db={cfg.db_host}:{cfg.db_port}/{cfg.db_name}")
    log.log(f"时区：进程 tz_offset=+{cfg.tz_offset_hours}h（today={today_cn(cfg)}）"
            f"，MySQL 会话 time_zone={cfg.session_time_zone}")
    for w in cfg.warnings():
        log.log("WARN: " + w)
    if not cfg.token:
        log.log("FATAL: 未读取到 token")
        log.close()
        return 2
    conn = None
    audit_id = None
    status = "failed"
    err_msg = None
    code = 1
    try:
        conn = db.connect(cfg)
        # 建库建表（DDL 与采集耦合在同一入口属已知设计债，见 docs/06 未做项）
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema.sql"),
                  encoding="utf-8") as f:
            sqls = [s.strip() for s in f.read().split(";") if s.strip()]
        for s in sqls:
            db.execute(conn, s)
        conn.commit()
        log.log("schema 建表完成")

        audit_id = audit_start(conn, task)
        log.log(f"审计行 t_fetch_log id={audit_id} status=running")

        client = TushareClient(cfg.endpoint, cfg.token)
        log.log(f"节流 {client.qps_delay}s/次（≤{int(60 / max(client.qps_delay, 0.001))} 次/分钟）"
                f" · 40203 退避 {client.rate_limit_wait}s×n · 最多重试 {client.max_retries} 次")

        written_days = None
        if task in ("all", "meta"):
            sync_trade_cal(client, conn, log, rep, cfg)
            sync_industry(client, conn, log, rep)
            sync_stock_basic(client, conn, log, rep)
        if task in ("all", "members"):
            sync_members(client, conn, log, rep, level=2)
            sync_members(client, conn, log, rep, level=3)
        mf_kw = {"page_size": cfg.moneyflow_max_rows, "gap_max_days": cfg.moneyflow_gap_max_days,
                 "cfg": cfg}
        if task in ("all", "moneyflow"):
            log.log(f"主窗口 MONEYFLOW_DAYS={cfg.moneyflow_days} 个交易日（覆盖式重拉）")
            written_days = sync_moneyflow(client, conn, log, rep, cfg.moneyflow_days,
                                          mode="full", **mf_kw)
        elif task == "incremental":
            # 增量：先补分类基础（幂等、很快），再补缺失资金流（含空洞回填），最后聚合
            sync_trade_cal(client, conn, log, rep, cfg)
            written_days = sync_moneyflow(client, conn, log, rep, cfg.moneyflow_days,
                                          mode="incremental", **mf_kw)
        if task in ("all", "members", "moneyflow", "aggregate", "incremental"):
            # 增量：只重算真正写入过的交易日；全量/成分股变更：全量重算（成分变了则全历史都受影响）
            if task == "incremental" and written_days is not None:
                dates = None if len(written_days) > AGG_INLINE_MAX_DAYS else written_days
                log.log(f"聚合范围：增量 {len(written_days)} 个交易日"
                        if dates is not None else "聚合范围：全量（写入日过多，等同全量重算）")
            else:
                dates = None
            aggregate_flow(conn, log, rep, dates)
        summary(conn, log)
        code = 3 if rep.problems else 0
        status = "partial" if rep.problems else "success"
        log.log(f"P0 ETL 完成（task={task}）取回 {rep.rows_fetched} 行 / 提交 {rep.rows_written} 行"
                f"；退出码 {code}")
        if rep.problems:
            for p in rep.problems:
                log.log("  未消化问题: " + p)
    except Exception:
        err_msg = traceback.format_exc()
        log.log("FATAL: " + err_msg)
    finally:
        if conn is not None:
            try:
                if audit_id is not None:
                    audit_finish(conn, audit_id, status, rep.rows_fetched,
                                 rep.rows_written, err_msg)
            except Exception:  # noqa: BLE001
                pass
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
        log.close()
    if err_msg is not None:
        return 1
    return code


if __name__ == "__main__":
    sys.exit(main())
