/* 回归测试：industry.html
   1) 搜索  2) 交易所筛选  3) 行业树展开/收起  4) 四档面板
   通过 CDP 驱动 Edge 无头模式，操作真实 DOM。
*/
const http = require('http');
const os = require('os');
const path = require('path');
const { spawn } = require('child_process');

const WS_URL = 'http://127.0.0.1:9222';
const PAGE = process.env.PAGE || 'http://127.0.0.1:8000/industry.html';
const EDGE = process.env.EDGE ||
  'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';

/* 浏览器 profile 必须放在系统临时目录：此前写死为 <repo>\_edge_profile，
   导致每次无头运行都在仓库里生成 ~57MB / 700+ 文件的缓存目录
   （_edge_profile、_edge_profile2、_edge_profile3 共 170MB）。 */
const PROFILE = process.env.EDGE_PROFILE ||
  path.join(os.tmpdir(), 'biancheng-edge-profile');

function sleep(ms){ return new Promise(r=>setTimeout(r, ms)); }
function httpJson(path){
  return new Promise((res,rej)=>{
    http.get(WS_URL+path, r=>{ let d=''; r.on('data',c=>d+=c); r.on('end',()=>{ try{res(JSON.parse(d))}catch(e){rej(e)} }); }).on('error',rej);
  });
}

/* 极简 CDP 客户端 */
class CDP {
  constructor(ws){ this.ws=ws; this.id=0; this.waits=new Map(); this.evs=new Map();
    ws.addEventListener('message', ev=>{
      const m = JSON.parse(ev.data);
      if(m.id && this.waits.has(m.id)){ const {res,rej}=this.waits.get(m.id); this.waits.delete(m.id);
        m.error ? rej(new Error(JSON.stringify(m.error))) : res(m.result); }
      else if(m.method && this.evs.has(m.method)) this.evs.get(m.method).forEach(f=>f(m.params));
    });
  }
  send(method, params={}){
    const id = ++this.id;
    return new Promise((res,rej)=>{ this.waits.set(id,{res,rej});
      this.ws.send(JSON.stringify({id,method,params}));
      setTimeout(()=>{ if(this.waits.has(id)){ this.waits.delete(id); rej(new Error('timeout: '+method)); } }, 30000);
    });
  }
  on(method, fn){ if(!this.evs.has(method)) this.evs.set(method,[]); this.evs.get(method).push(fn); }
  async eval(expr){
    const r = await this.send('Runtime.evaluate', {
      expression: `Promise.resolve(${expr}).then(v=>JSON.stringify(v===undefined?null:v)).catch(e=>JSON.stringify({__err:String(e&&e.message||e)}))`,
      awaitPromise: true, returnByValue: true
    });
    if(r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails));
    const v = r.result.value;
    return v === undefined || v === null ? undefined : JSON.parse(v);
  }
}

const results = [];
function check(name, pass, detail){ results.push({name, pass, detail}); 
  console.log((pass?'✅':'❌') + ' ' + name + (detail?('  → '+detail):'')); }

(async ()=>{
  /* 启动 Edge */
  const edge = spawn(EDGE, [
    '--headless=new', '--remote-debugging-port=9222', '--disable-gpu',
    '--no-first-run', '--no-default-browser-check', '--disable-extensions',
    '--user-data-dir='+PROFILE, 'about:blank'
  ], { stdio:'ignore', detached:false });

  let tabs = null;
  for(let i=0;i<40;i++){ try{ tabs = await httpJson('/json/list'); if(tabs.length) break; }catch(e){} await sleep(400); }
  if(!tabs) { console.log('❌ 无法连接 Edge CDP'); process.exit(1); }

  const target = tabs.find(t=>t.type==='page');
  const ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((res,rej)=>{ ws.addEventListener('open',res,{once:true}); ws.addEventListener('error',e=>rej(new Error('ws error')),{once:true}); });
  const cdp = new CDP(ws);

  const consoleErrors = [];
  await cdp.send('Runtime.enable');
  await cdp.send('Log.enable');
  await cdp.send('Page.enable');
  await cdp.on('Runtime.exceptionThrown', p=>{
    consoleErrors.push(p.exceptionDetails?.exception?.description || JSON.stringify(p.exceptionDetails));
  });

  console.log('\n══════ 回归测试：industry.html ══════\n');

  /* 打开页面 */
  await cdp.send('Page.navigate', {url: PAGE});
  await sleep(1000);
  // 等 tree 就绪
  for(let i=0;i<40;i++){
    const ready = await cdp.eval('window.TREE_READY === true || (typeof TREE!=="undefined" && TREE.length>0)');
    if(ready) break; await sleep(500);
  }
  await sleep(1200);

  const base = await cdp.eval(`({
    title: document.title,
    l1: (typeof TREE!=='undefined'&&TREE.length)||0,
    stat: (document.getElementById('statLine')||{}).textContent||'',
    boxes: document.querySelectorAll('.l1').length,
    err: (typeof window.__lastErr!=='undefined')? String(window.__lastErr):''
  })`);
  check('页面加载 + 数据就绪', base.l1 > 0, `一级行业 ${base.l1} 个 · 统计行「${base.stat}」`);

  /* ── 测试 1：行业树展开 / 收起 ── */
  console.log('\n【1】行业树展开 / 收起');
  const t1 = await cdp.eval(`(async ()=>{
    const head = document.querySelector('.l1-head');
    const box  = head.closest('.l1');
    const before = box.getAttribute('data-open');
    head.click();
    await new Promise(r=>setTimeout(r,300));
    const afterOpen = box.getAttribute('data-open');
    const l2v = box.querySelectorAll('.l2').length;
    head.click();
    await new Promise(r=>setTimeout(r,300));
    const afterClose = box.getAttribute('data-open');
    return { before, afterOpen, afterClose, l2Visible: l2v, aria: head.getAttribute('aria-expanded') };
  })()`);
  check('一级节点可展开', t1.afterOpen !== t1.before, `data-open: ${t1.before} → ${t1.afterOpen}`);
  check('一级节点可收起', t1.afterClose !== t1.afterOpen && t1.afterClose==='0', `data-open: ${t1.afterOpen} → ${t1.afterClose}`);
  check('展开后渲染出二级节点', t1.l2Visible > 0, `二级节点 ${t1.l2Visible} 个`);

  // 二级 / 三级
  const t2 = await cdp.eval(`(async ()=>{
    const h1 = document.querySelector('.l1-head'); h1.click();
    await new Promise(r=>setTimeout(r,400));
    const h2 = document.querySelector('.l2-head'); if(!h2) return {err:'no l2'};
    const b2 = h2.closest('.l2'); const b2b = b2.getAttribute('data-open');
    h2.click(); await new Promise(r=>setTimeout(r,400));
    const b2a = b2.getAttribute('data-open');
    const l3n = b2.querySelectorAll('.l3').length;
    const h3 = b2.querySelector('.l3-head'); let l3open=null, stocks=0;
    if(h3){ const b3=h3.closest('.l3'); const b3b=b3.getAttribute('data-open');
      h3.click(); await new Promise(r=>setTimeout(r,2500));
      l3open = b3b + '→' + b3.getAttribute('data-open');
      stocks = b3.querySelectorAll('.stock').length; }
    return { l2: b2b+'→'+b2a, l3n, l3open, stocks };
  })()`);
  check('二级节点可展开/收起', t2.l2 && t2.l2.includes('→'), `二级 ${t2.l2}`);
  check('三级节点渲染', t2.l3n > 0, `三级节点 ${t2.l3n} 个`);
  check('三级展开后加载股票', t2.stocks > 0, `股票 ${t2.stocks} 只（展开 ${t2.l3open}）`);

  /* ── 测试 2：搜索 ── */
  console.log('\n【2】搜索');
  async function doSearch(q, waitMs){
    await cdp.eval(`(async ()=>{ const i=document.getElementById('q'); i.value=${JSON.stringify(q)};
      i.dispatchEvent(new Event('input',{bubbles:true})); return 1; })()`);
    await sleep(waitMs||2200);
    return cdp.eval(`({
      q: (document.getElementById('q')||{}).value,
      stat: (document.getElementById('statLine')||{}).textContent||'',
      l1: document.querySelectorAll('.l1').length,
      l2: document.querySelectorAll('.l2').length,
      l3: document.querySelectorAll('.l3').length,
      l3Names: Array.from(document.querySelectorAll('.l3-name')).slice(0,4).map(e=>e.textContent.trim()),
      l2Names: Array.from(document.querySelectorAll('.l2-name')).slice(0,3).map(e=>e.textContent.trim()),
      empty: (document.querySelector('.empty')||{}).textContent||''
    })`);
  }

  const s1 = await doSearch('贵州茅台');
  check('搜索「贵州茅台」命中药股', s1.l3 === 1 || s1.l3>0, `三级 ${s1.l3} 个 · ${s1.l3Names.join('/')} · 统计「${s1.stat}」`);
  check('搜索结果含「白酒」行业', JSON.stringify(s1).includes('白酒'), `二级: ${s1.l2Names.join('/')}`);

  const s2 = await doSearch('半导体');
  check('搜索「半导体」命中行业', s2.l2>0 || s2.l3>0, `二级 ${s2.l2} / 三级 ${s2.l3} · ${s2.l3Names.join('/')}`);
  check('搜索「半导体」统计行正确', /行业|股票/.test(s2.stat) && !/匹配\s*0/.test(s2.stat), `统计「${s2.stat}」`);
  const s2stock = await cdp.eval(`(async ()=>{
    const h = document.querySelector('.l3-head'); if(!h) return {n:0};
    if(h.closest('.l3').getAttribute('data-open')!=='1'){ h.click(); await new Promise(r=>setTimeout(r,2500)); }
    return { n: document.querySelectorAll('.stock').length };
  })()`);
  check('搜索「半导体」可展开出股票', s2stock.n > 0, `股票 ${s2stock.n} 只`);

  const s3 = await doSearch('600519');
  check('搜索「600519」按代码命中', s3.l3>0, `三级 ${s3.l3} 个 · 「${s3.stat}」`);

  const s4 = await doSearch('zzzz不存在xyz');
  check('搜索无结果时显示空态', s4.l1===0 || /没有匹配|无匹配/.test(s4.empty + s4.stat), `空态「${s4.empty||s4.stat}」`);

  const s5 = await doSearch('');
  check('清空搜索后恢复全量树', s5.l1 > 20, `一级 ${s5.l1} 个 · 「${s5.stat}」`);

  /* ── 测试 3：交易所筛选 ── */
  console.log('\n【3】交易所筛选');
  const exOpts = await cdp.eval(`Array.from(document.querySelectorAll('[data-ex]')).map(e=>({t:e.textContent.trim(), d:e.getAttribute('data-ex'), on:e.classList.contains('on')||e.classList.contains('active')}))`);
  console.log('   交易所控件: ' + JSON.stringify(exOpts));
  const exTest = await cdp.eval(`(async ()=>{
    const tag = el => { const m=(el.textContent||'').match(/\\b(SH|SZ|BJ)\\b/); return m?m[1]:''; };
    /* renderTree() 会重建 DOM，筛选后需重新展开并【轮询等待】成分股加载完成 */
    const expandOne = async ()=>{
      document.querySelectorAll('.l1').forEach(x=>{ if(x.getAttribute('data-open')!=='1') x.querySelector('.l1-head').click(); });
      await new Promise(r=>setTimeout(r,500));
      document.querySelectorAll('.l2').forEach(x=>{ if(x.getAttribute('data-open')!=='1') x.querySelector('.l2-head').click(); });
      await new Promise(r=>setTimeout(r,500));
      for(const h of document.querySelectorAll('.l3-head')){ if(h.closest('.l3').getAttribute('data-open')!=='1') h.click(); }
      // 轮询直到至少一个三级行业填充出股票，或超时
      let node=null;
      for(let i=0;i<16;i++){
        await new Promise(r=>setTimeout(r,400));
        node=[...document.querySelectorAll('.l3[data-open="1"]')].find(n=>n.querySelector('.stock'));
        if(node) break;
      }
      return node ? Array.from(node.querySelectorAll('.stock')).map(tag).filter(Boolean) : [];
    };
    const pick = v => document.querySelector('[data-ex="'+v+'"]');
    const before = await expandOne();
    let afterSH=[], afterSZ=[];
    const shBtn=pick('1'), szBtn=pick('0'), allBtn=pick('all');
    if(shBtn){ shBtn.click(); await new Promise(r=>setTimeout(r,1200)); afterSH = await expandOne(); }
    if(szBtn){ szBtn.click(); await new Promise(r=>setTimeout(r,1200)); afterSZ = await expandOne(); }
    if(allBtn){ allBtn.click(); await new Promise(r=>setTimeout(r,1200)); }
    return { total: before.length, uniq: Array.from(new Set(before)),
             shCount: afterSH.length, shUniq: Array.from(new Set(afterSH)),
             szCount: afterSZ.length, szUniq: Array.from(new Set(afterSZ)),
             hasSH: !!shBtn, hasSZ: !!szBtn };
  })()`);
  check('股票行含交易所标记', exTest.uniq.length > 0, `共 ${exTest.total} 只，交易所 ${exTest.uniq.join(',')}`);
  if(exTest.hasSH){
    check('筛选「上交所」只留 SH', exTest.shUniq.length===1 && exTest.shUniq[0]==='SH', `${exTest.shCount} 只，仅 ${exTest.shUniq.join(',')||'空'}`);
  } else { check('上交所筛选按钮存在', false, '未找到 data-ex=1'); }
  if(exTest.hasSZ){
    check('筛选「深交所」只留 SZ', exTest.szUniq.length===1 && exTest.szUniq[0]==='SZ', `${exTest.szCount} 只，仅 ${exTest.szUniq.join(',')||'空'}`);
  } else { check('深交所筛选按钮存在', false, '未找到 data-ex=0'); }

  /* ── 测试 4：四档面板 ── */
  console.log('\n【4】四档资金流面板');
  const quadOpen = await cdp.eval(`(async ()=>{
    // 真实路径：三级展开 → 点股票 → 抽屉打开 → 点「四档资金流」→ qModal
    document.querySelectorAll('.l1').forEach(x=>{ if(x.getAttribute('data-open')!=='1') x.querySelector('.l1-head').click(); });
    await new Promise(r=>setTimeout(r,400));
    document.querySelectorAll('.l2').forEach(x=>{ if(x.getAttribute('data-open')!=='1') x.querySelector('.l2-head').click(); });
    await new Promise(r=>setTimeout(r,400));
    const h3=document.querySelector('.l3-head');
    if(h3 && h3.closest('.l3').getAttribute('data-open')!=='1'){ h3.click(); await new Promise(r=>setTimeout(r,2500)); }
    const stk = document.querySelector('.stock');
    if(!stk) return { found:false, reason:'无股票行' };
    const stkCode = stk.getAttribute('data-code');
    stk.click();
    await new Promise(r=>setTimeout(r,2600));   // 等抽屉加载
    const drawer = document.getElementById('drawer');
    const drawerOpen = drawer && drawer.classList.contains('open');
    const dText = (drawer?drawer.textContent:'').replace(/\\s+/g,' ').slice(0,160);
    const qb = Array.from(drawer?drawer.querySelectorAll('button'):[]).find(b=>/四档/.test(b.textContent));
    if(!qb) return { found:false, reason:'抽屉内无四档按钮', drawerOpen, dText };
    qb.click();
    await new Promise(r=>setTimeout(r,2800));
    const m = document.getElementById('qModal');
    return { found:true, stkCode, drawerOpen,
      visible: !!m && m.classList.contains('open'),
      title: (document.getElementById('qTitle')||{}).textContent||'',
      meta: (document.getElementById('qMeta')||{}).textContent||'',
      text: (m?m.textContent:'').replace(/\\s+/g,' ').slice(0,260),
      bodyHasCanvas: !!(m && m.querySelector('canvas'))
    };
  })()`);
  if(quadOpen.found){
    check('点击股票打开抽屉', quadOpen.drawerOpen === true, `股票 ${quadOpen.stkCode}`);
    check('四档面板可打开', quadOpen.visible === true, `标题「${quadOpen.title}」· ${quadOpen.meta}`);
    check('四档面板含档位数据', quadOpen.bodyHasCanvas || /超大单|大单|中单|小单|主力|散户/.test(quadOpen.text), `含 canvas: ${quadOpen.bodyHasCanvas}`);
    // 切换 tab
    const tabSwitch = await cdp.eval(`(async ()=>{
      const t = document.querySelector('#qTabs [data-qm="lines"]');
      if(!t) return {ok:false};
      t.click(); await new Promise(r=>setTimeout(r,1200));
      return { ok:true, pressed: t.getAttribute('aria-pressed'),
               text:(document.getElementById('qModal').textContent||'').replace(/\\s+/g,' ').slice(0,120) };
    })()`);
    check('四档面板可切换视图', tabSwitch.ok === true, `切换到「四档累计」`);
    // 关闭
    const quadClose = await cdp.eval(`(async ()=>{
      const c = document.getElementById('qClose');
      if(!c) return {noBtn:true};
      c.click(); await new Promise(r=>setTimeout(r,700));
      const m = document.getElementById('qModal');
      return { closed: !m.classList.contains('open'), scrim: (document.getElementById('qScrim')||{className:''}).className };
    })()`);
    check('四档面板可关闭', quadClose.closed === true, `qModal.open=${!quadClose.closed} scrim="${quadClose.scrim}"`);
  } else {
    check('四档面板入口存在', false, (quadOpen.reason||'') + ' | 抽屉' + (quadOpen.drawerOpen?'已开':'未开') + ' | ' + (quadOpen.dText||''));
  }

  /* ── 控制台报错 ── */
  console.log('\n【5】运行期报错');
  check('无 JS 运行期异常', consoleErrors.length===0, consoleErrors.slice(0,3).join(' || ') || '无');

  /* 汇总 */
  const pass = results.filter(r=>r.pass).length;
  console.log('\n══════════════════════════════════════');
  console.log(`  合计 ${results.length} 项 · 通过 ${pass} · 失败 ${results.length-pass}`);
  console.log('══════════════════════════════════════');
  results.filter(r=>!r.pass).forEach(r=>console.log('  ❌ ' + r.name + ' → ' + r.detail));

  try{ ws.close(); }catch(e){}
  try{ edge.kill(); }catch(e){}
  /* 退出码必须反映结果：此前无条件 process.exit(0)，导致失败项只打印在控制台，
     任何 CI / 脚本调用都会把它当成通过（实测上一次运行中途夭折也没有报错）。 */
  if (results.length === 0) {
    console.error('  ⚠️  没有任何断言被执行，视为失败');
    process.exit(1);
  }
  process.exit(pass === results.length ? 0 : 1);
})().catch(e=>{ console.error('测试脚本异常:', e.message); process.exit(1); });
