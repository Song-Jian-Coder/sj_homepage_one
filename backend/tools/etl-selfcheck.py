# -*- coding: utf-8 -*-
"""ETL 自检：**不发起真实采集、不写业务表**，验证 ETL 的关键行为。

用法（在 backend/ 目录下）：
    python tools/etl-selfcheck.py            # 结果写 etl_selfcheck.log
    python tools/etl-selfcheck.py out.txt

为什么存在：真实 ETL 会消耗数据源配额并写库，不适合当回归门槛。
这里用「只读路径 + EXPLAIN + 值等价性比对」覆盖改造后的关键逻辑：

1. incremental 在「无新交易日」时必须短路，且**不发起任何数据源请求**；
2. `_scan_gaps` 的扫描下界必须取库内最早交易日（而非受 MONEYFLOW_DAYS 限制的主窗口）；
3. 聚合增量路径 `dates=[]` 完全跳过（不产生任何 SQL，不写库）；
4. 聚合 SQL（含日期 IN 过滤）用 `EXPLAIN` 验证语法与**占位符/参数个数一致**；
5. **值等价性**：把聚合 SQL 的 SELECT 部分单独跑一遍，与 `t_industry_flow` 里
   已存的同一 `(sw_code, trade_date)` 行逐字段比对（含 L1 的「二级求和」公式）；
6. 审计写入：插 1 行 `t_fetch_log` → 回填 → 按 id 删除，前后行数必须一致（自清理）。

退出码：0 = 全部通过；1 = 有失败项。
"""
import os
import sys

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND)

import db  # noqa: E402
import etl  # noqa: E402
from config import Config  # noqa: E402


class _Log:
    def __init__(self):
        self.lines = []

    def log(self, m):
        self.lines.append(m)


class _ExplodingClient:
    """任何调用都算失败：用来证明「无新数据时不发请求」。"""

    def __init__(self):
        self.calls = 0

    def call(self, *a, **k):
        self.calls += 1
        raise AssertionError("不应发起数据源请求: %s %s" % (a, k))


def run(out_path):
    lines, flags = [], []

    def check(cond, msg):
        flags.append(bool(cond))
        lines.append(("  PASS  " if cond else "  FAIL  ") + msg)

    cfg = Config()
    conn = db.connect(cfg, database=cfg.db_name)
    log = _Log()
    try:
        mf_min, mf_max = db.query_one(
            conn, "SELECT MIN(trade_date), MAX(trade_date) FROM t_moneyflow")
        lines.append("== 0. 前置事实 ==")
        lines.append("  t_moneyflow 范围: %s ~ %s" % (mf_min, mf_max))
        lines.append("  MONEYFLOW_DAYS=%s  CURDATE=%s"
                     % (cfg.moneyflow_days, db.query_one(conn, "SELECT CURDATE()")[0]))

        # 1. incremental 无新数据
        lines.append("== 1. incremental 无新交易日短路 ==")
        client = _ExplodingClient()
        rep = etl.RunReport(log)
        written = etl.sync_moneyflow(client, conn, log, rep, cfg.moneyflow_days,
                                     mode="incremental", page_size=cfg.moneyflow_max_rows,
                                     gap_max_days=cfg.moneyflow_gap_max_days, cfg=cfg)
        check(client.calls == 0, "未发起任何数据源请求（calls=%d）" % client.calls)
        check(written == [], "待聚合交易日为空（无新数据时聚合可跳过）")
        check(rep.problems == [], "无失败项：problems=%r" % (rep.problems,))
        for m in log.lines:
            lines.append("    log| " + m)

        # 2. 空洞扫描下界
        lines.append("== 2. 空洞扫描下界 = 库内最早交易日 ==")
        log2 = _Log()
        all_days = [r[0] for r in db.query(conn, (
            "SELECT cal_date FROM t_trade_cal WHERE is_open=1 AND cal_date <= CURDATE() "
            "ORDER BY cal_date DESC LIMIT %s"), (cfg.moneyflow_days,))]
        gaps = etl._scan_gaps(conn, log2, [], all_days, mf_max, cfg.moneyflow_gap_max_days)
        check(gaps == [], "当前库无空洞：返回 %r" % (gaps,))
        for m in log2.lines:
            lines.append("    log| " + m)
        check(any(("扫描区间 %s" % mf_min) in m for m in log2.lines),
              "扫描下界为库内最早交易日 %s（改造前为 min(all_days)=%s）"
              % (mf_min, min(all_days)))

        # 3. 聚合增量跳过
        lines.append("== 3. 聚合增量路径 dates=[] 跳过 ==")
        before = db.table_count(conn, "t_industry_flow")
        log3 = _Log()
        etl.aggregate_flow(conn, log3, etl.RunReport(log3), [])
        after = db.table_count(conn, "t_industry_flow")
        check(before == after, "t_industry_flow 行数未变（%s -> %s）" % (before, after))
        check(sum("跳过" in m for m in log3.lines) >= 3, "L2/L3/L1 三层都跳过")

        # 4 + 5. EXPLAIN 与值等价性
        lines.append("== 4/5. 聚合 SQL：EXPLAIN + 值等价性 ==")
        win = [r[0] for r in db.query(conn, (
            "SELECT DISTINCT trade_date FROM t_industry_flow ORDER BY trade_date DESC LIMIT 2"))]
        for level in (2, 3):
            seen = {}
            orig = db.execute

            def fake_execute(c, s, args=None, _seen=seen):
                _seen["sql"], _seen["args"] = s, args
                return 0

            db.execute = fake_execute
            try:
                etl._agg_level(conn, level, _Log(), win)
            finally:
                db.execute = orig
            sql, args = seen.get("sql"), seen.get("args")
            if not sql:
                check(False, "L%d 未生成聚合 SQL" % level)
                continue
            check(sql.startswith("INSERT INTO t_industry_flow"), "L%d 为 INSERT...SELECT" % level)
            check(sql.count("%s") == len(args),
                  "L%d 占位符 %d 个 == 参数 %d 个" % (level, sql.count("%s"), len(args)))
            check("mf.trade_date IN (" in sql, "L%d 含增量日期过滤" % level)
            cur = conn.cursor()
            cur.execute("EXPLAIN " + sql, args)
            plan = cur.fetchall()
            cur.close()
            check(len(plan) > 0, "L%d EXPLAIN 通过（%d 行计划）" % (level, len(plan)))

            # 5) 值等价性：把 INSERT...SELECT 的 SELECT 片段抽出来单跑，
            #    与库内已存行逐字段比对（等价于「重算一次，但不写库」）。
            code = args[0]
            body = sql[sql.index("SELECT "):sql.index("ON DUPLICATE KEY UPDATE")]
            body = body.replace("SELECT %s, %s, mf.trade_date,", "SELECT mf.trade_date,")
            got = db.query(conn, body, list(args[2:]))
            stored = db.query(conn, (
                "SELECT trade_date, sm, md, lg, elg, main, net, turnover, member_count "
                "FROM t_industry_flow WHERE sw_code=%s AND level=%s AND trade_date IN (%s,%s) "
                "ORDER BY trade_date"), [code, level] + list(win))
            same = len(got) == len(stored) and len(stored) > 0 and all(
                all(str(a) == str(b) for a, b in zip(g[1:], s[1:])) for g, s in zip(got, stored))
            check(same, "L%d 重算值与库内已存值逐字段一致（code=%s, %d 行）"
                  % (level, code, len(stored)))
            if not same:
                lines.append("    got   : %r" % (got[:2],))
                lines.append("    stored: %r" % (stored[:2],))

        # L1 公式等价性（二级求和）
        l1_sql = """SELECT i.parent_code, f.trade_date, ROUND(SUM(f.sm),4), ROUND(SUM(f.md),4),
                    ROUND(SUM(f.lg),4), ROUND(SUM(f.elg),4), ROUND(SUM(f.main),4),
                    ROUND(SUM(f.net),4), ROUND(SUM(f.turnover),4), SUM(f.member_count)
                    FROM t_industry_flow f JOIN t_industry i ON i.sw_code = f.sw_code
                    WHERE i.level=2 AND i.parent_code IS NOT NULL AND f.trade_date IN (%s,%s)
                    GROUP BY i.parent_code, f.trade_date"""
        calc = db.query(conn, l1_sql, list(win))
        stored_l1 = {(r[0], r[1]): r[2:] for r in db.query(conn, (
            "SELECT sw_code, trade_date, sm, md, lg, elg, main, net, turnover, member_count "
            "FROM t_industry_flow WHERE level=1 AND trade_date IN (%s,%s)"), list(win))}
        same_l1 = len(calc) > 0 and all(
            (r[0], r[1]) in stored_l1 and all(str(a) == str(b) for a, b in
                                              zip(r[2:], stored_l1[(r[0], r[1])]))
            for r in calc)
        check(same_l1, "L1「二级子行业求和」公式与库内已存值一致（%d 个一级行业×日期）" % len(calc))
        conn.rollback()

        # 6. 审计写入自清理
        lines.append("== 6. t_fetch_log 审计写入（自清理）==")
        n_before = db.table_count(conn, "t_fetch_log")
        rid = etl.audit_start(conn, "__selfcheck__")
        row = db.query_one(conn, "SELECT task_name, status FROM t_fetch_log WHERE id=%s", (rid,))
        check(row == ("__selfcheck__", "running"), "audit_start 写入 running 行 id=%s" % rid)
        etl.audit_finish(conn, rid, "success", 123, 45, None)
        row2 = db.query_one(conn, "SELECT status, end_at, rows_fetched, rows_written "
                                  "FROM t_fetch_log WHERE id=%s", (rid,))
        check(row2[0] == "success" and row2[1] is not None
              and row2[2] == 123 and row2[3] == 45, "audit_finish 回填 status/end_at/rows")
        db.execute(conn, "DELETE FROM t_fetch_log WHERE id=%s", (rid,))
        conn.commit()
        n_after = db.table_count(conn, "t_fetch_log")
        check(n_before == n_after, "自清理完成，行数 %s -> %s" % (n_before, n_after))
    finally:
        conn.close()

    ok = sum(flags)
    lines.append("")
    lines.append("RESULT: %d/%d 通过" % (ok, len(flags)))
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("PASS %d / %d -> %s" % (ok, len(flags), out_path))
    return 0 if ok == len(flags) else 1


if __name__ == "__main__":
    default_out = os.path.join(BACKEND, "etl_selfcheck.log")
    sys.exit(run(sys.argv[1] if len(sys.argv) > 1 else default_out))
