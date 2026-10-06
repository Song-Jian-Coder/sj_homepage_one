# -*- coding: utf-8 -*-
"""P0 数据校验：个股 + 行业资金流抽查（只读）。

用法：python verify.py [out.log]
输出默认写到本脚本同目录的 verify_out.log（不再写死本机绝对路径，且 *.log 已 gitignore）。
"""
import os
import sys

from config import Config
import db

cfg = Config()
conn = db.connect(cfg, database=cfg.db_name)
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "verify_out.log")
out = []


def q(sql, args=None):
    return db.query(conn, sql, args)


out.append("=== 个股资金流 600519.SH 贵州茅台（近3日） ===")
for r in q("SELECT trade_date, net_mf_amount, buy_lg_amount, sell_lg_amount, buy_elg_amount, sell_elg_amount "
           "FROM t_moneyflow WHERE ts_code='600519.SH' ORDER BY trade_date DESC LIMIT 3"):
    out.append(f"  {r[0]} net={r[1]} lg={r[2]}-{r[3]} elg={r[4]}-{r[5]} (万元)")

out.append("")
out.append("=== 行业资金流 801081 半导体（近5日，亿元） ===")
for r in q("SELECT trade_date, sm, md, lg, elg, main, net, turnover, member_count "
           "FROM t_industry_flow WHERE sw_code='801081' ORDER BY trade_date DESC LIMIT 5"):
    out.append(f"  {r[0]} sm={r[1]} md={r[2]} lg={r[3]} elg={r[4]} main={r[5]} net={r[6]} "
               f"turnover={r[7]} members={r[8]}")

out.append("")
out.append("=== 行业树抽查（电子 L2 → 半导体 详情） ===")
for r in q("SELECT sw_code, name, level, parent_code, l1_name FROM t_industry "
           "WHERE sw_code IN ('801080','801081','850131')"):
    out.append(f"  {r[0]} {r[1]} level={r[2]} parent={r[3]} l1={r[4]}")

out.append("")
out.append("=== 各层级资金流行数（校验「一/二/三级都有数据」）===")
for r in q("SELECT level, COUNT(*) FROM t_industry_flow GROUP BY level ORDER BY level"):
    out.append(f"  L{r[0]}: {r[1]} 行")

out.append("")
out.append("=== 各层级成分股行数（L1 应为 0：成分只挂在 L2/L3）===")
for r in q("SELECT i.level, COUNT(*) FROM t_industry_member m "
           "JOIN t_industry i ON i.sw_code=m.index_code GROUP BY i.level ORDER BY i.level"):
    out.append(f"  L{r[0]}: {r[1]} 行")

out.append("")
out.append("=== 当前成分股数（半导体，时点 2026-09-24） ===")
n = q("SELECT COUNT(*) FROM t_industry_member WHERE index_code='801081' AND in_date<='2026-09-24' "
      "AND (out_date IS NULL OR out_date>'2026-09-24')")
out.append(f"  {n[0][0]} 只")

out.append("")
out.append("=== 重复活跃成员检查（应为 0 条）===")
dup = q("SELECT index_code, con_code, COUNT(*) c FROM t_industry_member WHERE out_date IS NULL "
        "GROUP BY index_code, con_code HAVING c>1 LIMIT 5")
out.append(f"  {dup if dup else '无重复'}")

out.append("")
out.append("=== 采集审计（t_fetch_log 最近 5 条）===")
for r in q("SELECT id, task_name, status, start_at, end_at, rows_fetched, rows_written, error_msg "
           "FROM t_fetch_log ORDER BY id DESC LIMIT 5"):
    out.append(f"  {r}")

conn.close()
with open(OUT, "w", encoding="utf-8") as f:
    f.write("\n".join(out))
print("DONE ->", OUT)
