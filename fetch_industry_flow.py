# -*- coding: utf-8 -*-
"""
申万二级行业资金流 —— 单个行业真实数据验证
目标行业：801081 半导体（申万2021版二级）

数据流：
  1. index_member  取半导体全部成分股（is_new='Y'）
  2. moneyflow     按交易日拉全市场资金流（242 天）
  3. 过滤出成分股，按小/中/大/超大单分别聚合
  4. 输出 JSON，结构对齐网页现有格式

资金流口径（Tushare moneyflow，单位：万元）：
  net_mf_amount = 净流入总额（官方口径）
  各档净额 = buy_X_amount - sell_X_amount
    sm  小单   md  中单   lg  大单   elg 超大单
  主力净额 = lg + elg（业界常用）
"""
import os, sys, json, time

TOKEN = os.environ.get("TS_TOKEN", "").strip()
ENDPOINT = os.environ.get("TS_ENDPOINT", "").strip()
if not TOKEN:
    print("ERROR: TS_TOKEN not set"); sys.exit(1)

import requests

S = requests.Session()
BASE = ENDPOINT or "http://221.204.19.233:7172"

def api(name, **params):
    """调用 sxsc-tushare 的只读查询接口"""
    payload = {"api_name": name, "token": TOKEN, "params": params, "max_rows": 6000}
    r = S.post(BASE + "/query", json=payload, timeout=60)
    r.raise_for_status()
    j = r.json()
    if not j.get("ok", True) and j.get("error"):
        raise RuntimeError(f"{name}: {j['error']}")
    return j

TARGET = "801081.SI"          # 半导体
TARGET_NAME = "半导体"

print("=" * 66)
print(f"申万二级行业资金流验证 · {TARGET_NAME} ({TARGET})")
print("=" * 66)

# ── 1. 取成分股 ──
print("\n[1/4] 取成分股 …")
res = api("index_member", index_code=TARGET)
rows = res.get("rows", [])
print(f"  返回 {len(rows)} 条（含历史调出）")

# 只保留当前成分（is_new == 'Y' 且 out_date 为空）
members = [r["con_code"] for r in rows
           if str(r.get("is_new", "")).upper() == "Y" and not r.get("out_date")]
members = sorted(set(members))
print(f"  当前成分股：{len(members)} 只")
print(f"  样例：{', '.join(members[:8])} …")

# ── 2. 取交易日 ──
print("\n[2/4] 取近一年交易日 …")
cal = api("trade_cal", exchange="SSE", start_date="20250922", end_date="20260921", is_open="1")
days = sorted(r["cal_date"] for r in cal.get("rows", []))
print(f"  交易日 {len(days)} 天：{days[0]} ~ {days[-1]}")

# ── 3. 逐日拉全市场资金流，过滤成分股 ──
print("\n[3/4] 拉取每日资金流 …")
memset = set(members)

# 聚合容器: date -> {sm, md, lg, elg, net, amt}
agg = {}
t0 = time.time()
fail = []

for i, d in enumerate(days):
    try:
        r = api("moneyflow", trade_date=d)
    except Exception as e:
        fail.append((d, str(e)[:60])); continue

    day = {"buy_sm":0.0,"sell_sm":0.0,"buy_md":0.0,"sell_md":0.0,
           "buy_lg":0.0,"sell_lg":0.0,"buy_elg":0.0,"sell_elg":0.0,"net":0.0}
    cnt = 0
    for row in r.get("rows", []):
        if row["ts_code"] not in memset:
            continue
        cnt += 1
        day["buy_sm"]  += float(row.get("buy_sm_amount")  or 0)
        day["sell_sm"] += float(row.get("sell_sm_amount") or 0)
        day["buy_md"]  += float(row.get("buy_md_amount")  or 0)
        day["sell_md"] += float(row.get("sell_md_amount") or 0)
        day["buy_lg"]  += float(row.get("buy_lg_amount")  or 0)
        day["sell_lg"] += float(row.get("sell_lg_amount") or 0)
        day["buy_elg"] += float(row.get("buy_elg_amount") or 0)
        day["sell_elg"]+= float(row.get("sell_elg_amount")or 0)
        day["net"]     += float(row.get("net_mf_amount")  or 0)
    day["cnt"] = cnt
    agg[d] = day

    if (i+1) % 20 == 0 or i == len(days)-1:
        el = time.time() - t0
        print(f"  进度 {i+1}/{len(days)}  用时 {el:.0f}s  预计剩余 {el/(i+1)*(len(days)-i-1):.0f}s")

print(f"  完成，用时 {time.time()-t0:.0f}s" + (f"，失败 {len(fail)} 天" if fail else ""))

# ── 4. 计算各档净额并输出 ──
print("\n[4/4] 聚合与计算 …")

series = []
cum = {"sm":0.0,"md":0.0,"lg":0.0,"elg":0.0,"net":0.0}
for d in days:
    a = agg.get(d)
    if not a or a["cnt"] == 0:
        continue
    sm  = a["buy_sm"]  - a["sell_sm"]
    md  = a["buy_md"]  - a["sell_md"]
    lg  = a["buy_lg"]  - a["sell_lg"]
    elg = a["buy_elg"] - a["sell_elg"]
    net = a["net"]                          # 官方净流入
    main = lg + elg                         # 主力 = 大单 + 超大单
    for k, v in (("sm",sm),("md",md),("lg",lg),("elg",elg),("net",net)):
        cum[k] += v
    series.append({
        "d": d,
        "sm": round(sm/10000, 4),           # 万元 → 亿元
        "md": round(md/10000, 4),
        "lg": round(lg/10000, 4),
        "elg": round(elg/10000, 4),
        "main": round(main/10000, 4),
        "net": round(net/10000, 4),
        "cumNet": round(cum["net"]/10000, 4),
        "cumMain": round((cum["lg"]+cum["elg"])/10000, 4),
        "cnt": a["cnt"],
    })

out = {
    "industry": {"code": TARGET, "name": TARGET_NAME, "level": "L2", "parent": "电子"},
    "members": members,
    "memberCount": len(members),
    "range": {"start": days[0], "end": days[-1], "days": len(series)},
    "series": series,
    "generatedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
    "source": "sxsc-tushare / moneyflow + index_member (SW2021)",
}

with open("semiconductor_flow.json", "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)

# ── 汇总打印 ──
tot = {k: sum(s[k] for s in series) for k in ("sm","md","lg","elg","main","net")}
print("\n" + "=" * 66)
print(f"{TARGET_NAME} · 近 {len(series)} 个交易日资金流汇总（亿元）")
print("=" * 66)
print(f"  小单净额    {tot['sm']:>12,.2f}")
print(f"  中单净额    {tot['md']:>12,.2f}")
print(f"  大单净额    {tot['lg']:>12,.2f}")
print(f"  超大单净额  {tot['elg']:>12,.2f}")
print(f"  ─────────────────────────────")
print(f"  主力(大+超大){tot['main']:>11,.2f}")
print(f"  官方净流入  {tot['net']:>12,.2f}")
print(f"\n  成分股 {len(members)} 只 · 数据点 {len(series)} 天")
print(f"  最新日期 {series[-1]['d']}：净流入 {series[-1]['net']:+.2f}亿，主力 {series[-1]['main']:+.2f}亿")
print(f"\n  已写入 semiconductor_flow.json")
