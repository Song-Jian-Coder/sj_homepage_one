# -*- coding: utf-8 -*-
"""P0 数据校验：个股 + 行业资金流抽查"""
from config import Config
import db

cfg = Config()
out = []
conn = db.connect(cfg)
conn.select_db(cfg.db_name)

def q(sql, args=None):
    with conn.cursor() as cur:
        cur.execute(sql, args)
        return cur.fetchall()

out.append("=== 个股资金流 600519.SH 贵州茅台（近3日） ===")
for r in q("SELECT trade_date, net_mf_amount, buy_lg_amount, sell_lg_amount, buy_elg_amount, sell_elg_amount "
           "FROM t_moneyflow WHERE ts_code='600519.SH' ORDER BY trade_date DESC LIMIT 3"):
    out.append(f"  {r[0]} net={r[1]} lg={r[2]}-{r[3]} elg={r[4]}-{r[5]} (万元)")

out.append("")
out.append("=== 行业资金流 801081 半导体（近5日，亿元） ===")
for r in q("SELECT trade_date, sm, md, lg, elg, main, net, turnover, member_count "
           "FROM t_industry_flow WHERE sw_code='801081' ORDER BY trade_date DESC LIMIT 5"):
    out.append(f"  {r[0]} sm={r[1]} md={r[2]} lg={r[3]} elg={r[4]} main={r[5]} net={r[6]} turnover={r[7]} members={r[8]}")

out.append("")
out.append("=== 行业树抽查（电子 L2 → 半导体 详情） ===")
for r in q("SELECT sw_code, name, level, parent_code, l1_name FROM t_industry WHERE sw_code IN ('801080','801081','850131')"):
    out.append(f"  {r[0]} {r[1]} level={r[2]} parent={r[3]} l1={r[4]}")

out.append("")
out.append("=== 当前成分股数（半导体，时点 2026-09-24） ===")
n = q("SELECT COUNT(*) FROM t_industry_member WHERE index_code='801081' AND in_date<='2026-09-24' "
      "AND (out_date IS NULL OR out_date>'2026-09-24')")
out.append(f"  {n[0][0]} 只")

conn.close()
open(r"C:\Users\57424\AppData\Local\Temp\verify_out.txt", "w", encoding="utf-8").write("\n".join(out))
print("DONE")
