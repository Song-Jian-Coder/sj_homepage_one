/* ═══════════ 真实资金流接入（覆盖上方 mock 定义）═══════════ */
const API_BASE = (location.protocol === 'file:') ? 'http://127.0.0.1:8000' : '';
let DATA_DATE = null;

async function apiGet(path){
  const r = await fetch(API_BASE + path).then(x => x.json());
  if(r.code !== 0) throw new Error(r.msg || '数据接口错误');
  return r.data;
}

function cumulate(arr){
  let c = 0;
  arr.forEach(x => { c += (x.v || 0); x.cum = c; });
  return arr;
}

/* 6 位代码 -> ts_code（带交易所后缀）；已带后缀则原样返回 */
function tsCode(code){
  if(!code) return code;
  if(/\.(SH|SZ|BJ)$/.test(code)) return code;
  if(/^[69]/.test(code)) return code + '.SH';
  if(/^[023]/.test(code)) return code + '.SZ';
  return code + '.BJ';
}
/* ts_code -> 6 位代码 */
function bareCode(code){
  return (code || '').replace(/\.(SH|SZ|BJ)$/, '');
}

/* 个股资金流序列（真实） */
async function genSeries(code, name, days){
  const ck = code + '|' + days;
  if(_seriesCache.has(ck)) return _seriesCache.get(ck);
  const p = (async () => {
    try {
      const d = await apiGet('/api/v1/stocks/' + tsCode(code) + '/flow');
      return cumulate((d.series || []).slice(-days).map(x => ({d:x.d, v:x.net ?? 0})));
    } catch(e){ return []; }
  })();
  _seriesCache.set(ck, p);
  return p;
}

/* 行业资金流（真实；二级已聚合，一级/三级暂未聚合） */
async function aggregate(comp, days){
  const code = (comp && comp[0]) ? (comp[0].l2c || comp[0].l3c || comp[0].l1c) : null;
  if(!code) return [];
  try {
    const d = await apiGet('/api/v1/industries/' + code + '/flow');
    return cumulate((d.series || []).slice(-days).map(x => ({d:x.d, v:x.net ?? 0})));
  } catch(e){ return []; }
}

/* 成交额基准：真实数据下由 turnover 直接提供，此函数保留兼容 */
function amountBaseOf(code, name, days){ return 0; }

/* 四档资金流（真实） */
async function genQuad(code, name, days){
  const ck = code + '|q|' + days;
  if(_quadCache.has(ck)) return _quadCache.get(ck);
  const p = (async () => {
    try {
      const d = await apiGet('/api/v1/stocks/' + tsCode(code) + '/flow');
      const base = (d.series || []).slice(-days);
      let acc = {sm:0, md:0, lg:0, elg:0};
      return base.map(x => {
        acc.sm += (x.sm||0); acc.md += (x.md||0); acc.lg += (x.lg||0); acc.elg += (x.elg||0);
        return { d:x.d, sm:+((x.sm||0).toFixed(4)), md:+((x.md||0).toFixed(4)),
                 lg:+((x.lg||0).toFixed(4)), elg:+((x.elg||0).toFixed(4)),
                 main:+(((x.lg||0)+(x.elg||0)).toFixed(4)), retail:+(((x.sm||0)+(x.md||0)).toFixed(4)),
                 cumSm:+acc.sm.toFixed(4), cumMd:+acc.md.toFixed(4),
                 cumLg:+acc.lg.toFixed(4), cumElg:+acc.elg.toFixed(4),
                 cumMain:+((acc.lg+acc.elg).toFixed(4)) };
      });
    } catch(e){ return []; }
  })();
  _quadCache.set(ck, p);
  return p;
}

/* 全市场强度（真实，预热后同步返回） */
const _rankMap = {map: new Map()};
function marketRank(){ return _rankMap.map; }

/* ── 行业详情 ── */
async function renderIndustryBody(){
  const {level, code, name} = curIndustry;
  const days = cur.days;
  const box = document.getElementById('dBody');
  box.innerHTML = '<div class="empty">正在加载真实资金流…</div>';

  let series = [], turnover = 0;
  if(level === 'l2'){
    try {
      const d = await apiGet('/api/v1/industries/' + code + '/flow');
      const raw = (d.series || []).slice(-days);
      series = cumulate(raw.map(x => ({d:x.d, v:x.net ?? 0})));
      turnover = raw.reduce((s,x)=>s+(x.turnover||0), 0);
    } catch(e){ series = []; }
  }

  let compHtml = '', topCount = 0;
  if(level === 'l2'){
    try {
      const sf = await apiGet('/api/v1/industries/' + code + '/stocks-flow?limit=40');
      topCount = (sf.list || []).length;
      compHtml = (sf.list || []).map((s,i) =>
        '<button class="comp-item" data-code="' + bareCode(s.tsCode) + '" data-name="' + esc(s.name) + '">' +
          '<span class="comp-rank">' + (i+1) + '</span>' +
          '<span class="comp-code">' + s.tsCode + '</span>' +
          '<span class="comp-name">' + esc(s.name) + '</span>' +
          '<span class="comp-val ' + cls(s.net) + '">' + yi(s.net) + '</span>' +
        '</button>').join('');
    } catch(e){ compHtml = ''; }
  }

  const net = series.reduce((s,d)=>s+d.v,0);
  const inflowDays = series.filter(d=>d.v>0).length;
  const best = series.reduce((a,b)=>Math.abs(b.v)>Math.abs(a.v)?b:a, {d:'', v:0, cum:0});
  const maxCum = Math.max(0, ...series.map(d=>d.cum));
  const minCum = Math.min(0, ...series.map(d=>d.cum));
  const ranges = [[20,'近1月'],[60,'近3月'],[120,'近6月'],[242,'近1年']];
  const rangeBtns = ranges.map(([n,label])=>'<button data-days="'+n+'" aria-pressed="'+(days===n)+'">'+label+'</button>').join('');

  const note = level === 'l2'
    ? '<div class="data-note">✅ 真实数据' + (DATA_DATE ? ' · 数据日期 ' + DATA_DATE : '') + '　·　不构成投资建议</div>'
    : '<div class="data-note">⚠️ 该层级资金流暂未聚合（当前仅二级行业已接入），请下钻到二级行业查看。</div>';

  box.innerHTML =
    note +
    '<div class="range-bar">' +
      '<span class="lbl">时间范围</span>' +
      '<div class="seg" id="rangeSegI">' + rangeBtns + '</div>' +
      '<span class="lbl" style="margin-left:auto">' + series.length + ' 个交易日</span>' +
    '</div>' +
    '<div class="sum">' +
      '<div class="sum-c"><div class="sum-l">区间净流入合计</div><div class="sum-v ' + cls(net) + '">' + yi(net) + '</div></div>' +
      '<div class="sum-c"><div class="sum-l">净流入天数</div><div class="sum-v">' + inflowDays + ' <span style="font-size:13px;color:var(--text-dim)">/ ' + series.length + ' 天</span></div></div>' +
      '<div class="sum-c"><div class="sum-l">单日最大净流入</div><div class="sum-v ' + cls(best.v) + '">' + yi(best.v) + '</div></div>' +
      '<div class="sum-c"><div class="sum-l">累计峰值 / 谷值</div><div class="sum-v" style="font-size:15px"><span class="up">' + yi(maxCum) + '</span> <span style="color:var(--text-dim)">/</span> <span class="down">' + yi(minCum) + '</span></div></div>' +
    '</div>' +
    metricBlock(metrics(series, turnover), days, {isIndustry:true, code:code}) +
    chartHtml(series, days) +
    '<div class="sec-t">成分股区间净流入排行（前 ' + topCount + ' 只）</div>' +
    '<div class="comp-list">' + (compHtml || '<div class="empty">暂无数据</div>') + '</div>';

  bindChart(series);
}

async function openIndustry(level, code, name){
  const comp = stocksOf(level, code);
  if(!comp.length) return;
  curIndustry = {level, code, name, comp};
  const lvName = level==='l1' ? '一级行业' : (level==='l2' ? '二级行业' : '三级行业');
  const parent = comp[0];
  document.getElementById('dTitle').textContent = name;
  document.getElementById('dMeta').innerHTML =
    '<span class="mode-badge">' + lvName + '</span><span class="num">' + code + '</span><span>成分股 ' + comp.length + ' 只</span><span style="color:var(--border-strong)">|</span>' +
    (level==='l3' ? '<span>'+parent.l1+'</span><span style="color:var(--text-dim)">›</span><span>'+parent.l2+'</span>' : '<span>'+parent.l1+'</span>');
  document.getElementById('quadBtn').hidden = true;
  await renderIndustryBody();
  _lastFocus = document.activeElement;
  document.getElementById('drawer').classList.add('open');
  document.getElementById('scrim').classList.add('open');
  document.getElementById('dClose').focus();
}

/* ── 个股详情 ── */
async function renderBody(){
  const s = ALL_STOCKS.find(x=>x.code===cur.code) || {l1:'',l2:'',l3:'',l1c:'',l2c:'',l3c:''};
  const box = document.getElementById('dBody');
  box.innerHTML = '<div class="empty">正在加载真实资金流…</div>';

  let data = [], turnover = 0;
  try {
    const d = await apiGet('/api/v1/stocks/' + tsCode(cur.code) + '/flow');
    const raw = (d.series || []).slice(-cur.days);
    data = cumulate(raw.map(x => ({d:x.d, v:x.net ?? 0})));
    turnover = raw.reduce((s,x)=>s+(x.turnover||0), 0);
  } catch(e){ data = []; }

  const net = data.reduce((s,d)=>s+d.v,0);
  const inflowDays = data.filter(d=>d.v>0).length;
  const best = data.reduce((a,b)=>Math.abs(b.v)>Math.abs(a.v)?b:a, {d:'',v:0,cum:0});
  const maxCum = Math.max(0, ...data.map(d=>d.cum));
  const minCum = Math.min(0, ...data.map(d=>d.cum));
  const ranges = [[20,'近1月'],[60,'近3月'],[120,'近6月'],[242,'近1年']];
  const rangeBtns = ranges.map(([n,label])=>'<button data-days="'+n+'" aria-pressed="'+(cur.days===n)+'">'+label+'</button>').join('');

  box.innerHTML =
    '<div class="data-note">✅ 真实数据' + (DATA_DATE ? ' · 数据日期 ' + DATA_DATE : '') + '　·　不构成投资建议</div>' +
    '<div class="range-bar">' +
      '<span class="lbl">时间范围</span>' +
      '<div class="seg" id="rangeSeg">' + rangeBtns + '</div>' +
      '<span class="lbl" style="margin-left:auto">' + data.length + ' 个交易日</span>' +
    '</div>' +
    '<div class="sum">' +
      '<div class="sum-c"><div class="sum-l">区间净流入合计</div><div class="sum-v ' + cls(net) + '">' + yi(net) + '</div></div>' +
      '<div class="sum-c"><div class="sum-l">净流入天数</div><div class="sum-v">' + inflowDays + ' <span style="font-size:13px;color:var(--text-dim)">/ ' + data.length + ' 天</span></div></div>' +
      '<div class="sum-c"><div class="sum-l">单日最大净流入</div><div class="sum-v ' + cls(best.v) + '">' + yi(best.v) + '</div></div>' +
      '<div class="sum-c"><div class="sum-l">累计峰值 / 谷值</div><div class="sum-v" style="font-size:15px"><span class="up">' + yi(maxCum) + '</span> <span style="color:var(--text-dim)">/</span> <span class="down">' + yi(minCum) + '</span></div></div>' +
    '</div>' +
    metricBlock(metrics(data, turnover), cur.days) +
    chartHtml(data, cur.days) +
    '<div class="tree-path">' +
      '<div class="tp-t">所属行业层级</div>' +
      '<div class="tp-row"><span class="tp-k">一级</span><span class="tp-v">' + s.l1 + ' <em>' + s.l1c + '</em></span></div>' +
      '<div class="tp-row"><span class="tp-k">二级</span><span class="tp-v">' + s.l2 + ' <em>' + s.l2c + '</em></span></div>' +
      '<div class="tp-row"><span class="tp-k">三级</span><span class="tp-v">' + s.l3 + ' <em>' + s.l3c + '</em></span></div>' +
    '</div>';

  bindChart(data);
}

async function openStock(code, name){
  curIndustry = null;
  const s = ALL_STOCKS.find(x=>x.code===code);
  if(!s) return;
  const days = cur.days;
  cur = {code, name, days};
  document.getElementById('dTitle').textContent = name;
  document.getElementById('dMeta').innerHTML =
    '<span class="num">' + code + '</span><span>' + exName(s.ex) + '</span><span style="color:var(--border-strong)">|</span>' +
    '<span>' + s.l1 + '</span><span style="color:var(--text-dim)">›</span><span>' + s.l2 + '</span><span style="color:var(--text-dim)">›</span><span>' + s.l3 + '</span>';
  document.getElementById('quadBtn').hidden = false;
  await renderBody();
  _lastFocus = document.activeElement;
  document.getElementById('drawer').classList.add('open');
  document.getElementById('scrim').classList.add('open');
  document.getElementById('dClose').focus();
}

/* ── 四档 ── */
async function openQuad(code, name){
  const s = ALL_STOCKS.find(x=>x.code===code) || {};
  quadCtx = {code, name};
  const days = cur.days || 120;
  const data = await genQuad(code, name, days);
  document.getElementById('qTitle').textContent = name + ' · 四档资金流';
  document.getElementById('qMeta').innerHTML =
    '<span class="num">' + code + '</span>' +
    '<span>' + (s.l1||'') + '</span><span style="color:var(--text-dim)">›</span>' +
    '<span>' + (s.l2||'') + '</span><span style="color:var(--text-dim)">›</span>' +
    '<span>' + (s.l3||'') + '</span>' +
    '<span style="color:var(--border-strong)">|</span>' +
    '<span>' + data.length + ' 个交易日 · 真实数据</span>';
  renderQuadBody(data);
  document.getElementById('qModal').classList.add('open');
  document.getElementById('qScrim').classList.add('open');
  document.getElementById('qClose').focus();
}

/* ── 预热元数据 + 全市场强度 ── */
(function(){
  apiGet('/api/v1/meta').then(d => { if(d.dataEnd) DATA_DATE = d.dataEnd; }).catch(()=>{});
  apiGet('/api/v1/strength').then(res => {
    (res.list || []).forEach(x => _rankMap.map.set(x.swCode, {name:x.name, net:x.net, strength:x.strength}));
  }).catch(()=>{});
})();
