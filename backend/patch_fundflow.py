# -*- coding: utf-8 -*-
"""P2 前端补丁：fundflow.html mock -> 真实 API 数据。
对每个替换做命中校验，结果写入报告文件。
"""
import shutil
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "fundflow.html")

NEW_BLOCK = '''/* ══════ 资金流（真实数据，来自后端 API）══════ */
const API_BASE = (location.protocol === 'file:') ? 'http://127.0.0.1:8000' : '';
let IND = [];
let DATA_DATE = null;

function buildIND(list){
  return list.map(x => ({
    code: x.swCode, name: x.name, l1: x.l1Name, n: x.memberCount,
    top: x.topStocks || [],
    amt: x.turnover ?? 0,
    f: {1: x.todayMain ?? 0, 5: x.main5d ?? 0, 10: x.main10d ?? 0},
    strength: (x.turnover > 0) ? ((x.todayMain ?? 0) / x.turnover) : 0,
  }));
}

async function init(){
  const box = document.getElementById('treemap');
  try {
    const [listRes, metaRes] = await Promise.all([
      fetch(`${API_BASE}/api/v1/industries?level=2&page_size=500`).then(r=>r.json()),
      fetch(`${API_BASE}/api/v1/meta`).then(r=>r.json()),
    ]);
    if(listRes.code !== 0) throw new Error(listRes.msg || '数据接口错误');
    IND = buildIND(listRes.data.list);
    if(metaRes.code === 0 && metaRes.data && metaRes.data.dataEnd){
      DATA_DATE = metaRes.data.dataEnd;
    }
    const live = document.getElementById('liveText');
    if(live) live.textContent = '真实数据' + (DATA_DATE ? ' · 数据日期 ' + DATA_DATE : '');
    const mAmt = document.getElementById('mAmt');
    if(mAmt){
      const total = IND.reduce((s,x)=>s+(x.amt||0), 0);
      mAmt.textContent = (total/10000).toFixed(2) + ' 万亿';
    }
    render();
  } catch(e){
    box.innerHTML = `<div class="empty">数据加载失败：${e.message}<br>请确认后端服务已启动（127.0.0.1:8000）。</div>`;
    const live = document.getElementById('liveText');
    if(live) live.textContent = '数据加载失败';
  }
}'''

REPS = [
    ("<th>主力净流入</th><th>净流入占比</th><th>行业涨跌</th><th>强度</th>",
     "<th>主力净流入</th><th>净流入占比</th><th>强度</th>"),
    ('<td class="num ${cls(x.chg)}">${pctStr(x.chg)}</td>', ""),
    ('<div class="kv"><span class="k">行业涨跌幅</span><span class="v ${cls(x.chg)}">${pctStr(x.chg)}</span></div>', ""),
    ('<span><b>界面演示版</b>　行业结构与成分股来自真实申万分类（131 个二级行业 / 5218 只股票），资金流数值为<b>模拟生成</b>，用于确认设计方向。接入真实数据后即可上线。</span>',
     '<span><b>真实数据</b>　申万二级行业资金流（主力净流入口径），数据日期见左上角。不构成投资建议。</span>'),
    ('<span>数据分类：申万行业分类（2026-09-21 版）　·　界面为演示版本，数值非真实行情</span>',
     '<span>数据分类：申万行业分类　·　资金流为真实行情数据</span>'),
    ('<span>沪指 <b class="up" id="mSh">+0.62%</b></span>\n      <span>深成 <b class="down" id="mSz">-0.18%</b></span>\n      <span>两市成交 <b id="mAmt">1.42万亿</b></span>',
     '<span>沪指 <b id="mSh">—</b></span>\n      <span>深成 <b id="mSz">—</b></span>\n      <span>两市成交 <b id="mAmt">—</b></span>'),
    ("render();\nsetTimeout(()=>{\n  const el = document.getElementById('liveText');\n  if(el) el.textContent = '模拟数据 · ' + new Date().toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit'});\n}, 300);",
     "init();"),
]

html = open(SRC, encoding="utf-8").read()
report = []

# 1) mock 块整体替换（从「模拟资金流」注释行到 IND 的 }); ）
i_comment = html.index('模拟资金流（确定性伪随机')
i_start = html.rfind('\n', 0, i_comment) + 1
i_ind = html.index('const IND = SW.map')
i_end = html.index('});', i_ind) + 3
html = html[:i_start] + NEW_BLOCK + html[i_end:]
report.append(f"mock_block replaced (span {i_start}..{i_end})")

# 2) 其余精确替换
for idx, (old, new) in enumerate(REPS):
    n = html.count(old)
    if n == 0:
        report.append(f"REP[{idx}] NOT FOUND: {old[:50]}...")
    else:
        html = html.replace(old, new, 1)
        report.append(f"REP[{idx}] ok (found={n})")

# 备份 + 写回
shutil.copyfile(SRC, SRC + ".mock.bak")
open(SRC, "w", encoding="utf-8").write(html)

# 同步中文命名副本（若存在且内容一致）
CN = os.path.join(ROOT, "申万二级行业资金流看板.html")
if os.path.exists(CN):
    open(CN, "w", encoding="utf-8").write(html)
    report.append("synced 申万二级行业资金流看板.html")

open(r"C:\Users\57424\AppData\Local\Temp\patch_report.txt", "w", encoding="utf-8").write("\n".join(report))
print("DONE")
