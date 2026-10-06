/* XSS 端到端验证（真实浏览器）
 *
 * 用途：把带载荷的字符串送进**页面真实使用的转义入口**，分别在
 *       「文本上下文」与「属性上下文」下渲染，确认它们变成文本而不是被解析成元素；
 *       并用「未转义对照组」证明这些载荷本身确实是有效的攻击串
 *       （否则测试没有说服力 —— 可能只是选了一堆无害字符串）。
 *
 * 由来：审查报告 6.2.1 把「存储型 XSS → 管理密钥失窃链」列为 P0，
 *       但该结论是**靠静态阅读得出的**（报告 §10.9 自陈「用静态阅读代替一次运行」
 *       是本项目两轮各犯一次的错误）。这个脚本把那条结论变成可复现的实验。
 *
 * 前置：后端已在 127.0.0.1:8000 运行；本机装有 Edge。
 * 用法：
 *   node backend/tools/xss-check.js
 *   PAGE=http://127.0.0.1:8000/fundflow.html node backend/tools/xss-check.js
 *
 * 退出码：0 全部载荷被安全转义；1 存在未转义风险或出现未捕获异常。
 */
const http = require('http');
const os = require('os');
const path = require('path');
const { spawn } = require('child_process');

const WS_URL = 'http://127.0.0.1:9222';
const EDGE = process.env.EDGE ||
  'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';
const PROFILE = path.join(os.tmpdir(), 'biancheng-xss-' + Date.now());
const PAGE = process.env.PAGE || 'http://127.0.0.1:8000/industry.html';

function sleep(ms){ return new Promise(r=>setTimeout(r, ms)); }
function httpJson(p){ return new Promise((res,rej)=>{ http.get(WS_URL+p, r=>{ let d=''; r.on('data',c=>d+=c); r.on('end',()=>{ try{res(JSON.parse(d))}catch(e){rej(e)} }); }).on('error',rej); }); }
class CDP {
  constructor(ws){ this.ws=ws; this.id=0; this.waits=new Map(); this.evs=new Map();
    ws.addEventListener('message', ev=>{ const m=JSON.parse(ev.data);
      if(m.id && this.waits.has(m.id)){ const {res,rej}=this.waits.get(m.id); this.waits.delete(m.id);
        m.error?rej(new Error(JSON.stringify(m.error))):res(m.result); }
      else if(m.method && this.evs.has(m.method)) this.evs.get(m.method).forEach(f=>f(m.params)); }); }
  send(m,p={}){ const id=++this.id;
    return new Promise((res,rej)=>{ this.waits.set(id,{res,rej}); this.ws.send(JSON.stringify({id,method:m,params:p}));
      setTimeout(()=>{ if(this.waits.has(id)){ this.waits.delete(id); rej(new Error('timeout: '+m)); } },30000); }); }
  on(m,f){ if(!this.evs.has(m)) this.evs.set(m,[]); this.evs.get(m).push(f); }
  async eval(e){ const r=await this.send('Runtime.evaluate',{
      expression:`Promise.resolve(${e}).then(v=>JSON.stringify(v===undefined?null:v)).catch(e=>JSON.stringify({__err:String(e&&e.message||e)}))`,
      awaitPromise:true, returnByValue:true });
    if(r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails));
    const v=r.result.value; return (v===undefined||v===null)?undefined:JSON.parse(v); }
}

/* 载荷：若被当成 HTML 解析，会创建一个带 id 的元素或触发 window.__xss */
const PAYLOADS = [
  '<img src=x onerror="window.__xss=1" id="p1">',
  '"><img src=x onerror="window.__xss=2" id="p2">',
  "'><svg onload=\"window.__xss=3\" id=\"p3\">",
  '<script>window.__xss=4<\/script>',
  '<iframe src="javascript:window.__xss=5" id="p5"></iframe>',
  '&lt;img src=x onerror="window.__xss=6"&gt;',
  '`;window.__xss=7;//'
];

(async ()=>{
  const edge = spawn(EDGE, ['--headless=new','--remote-debugging-port=9222','--disable-gpu',
    '--no-first-run','--no-default-browser-check','--disable-extensions',
    '--user-data-dir='+PROFILE,'about:blank'], { stdio:'ignore' });
  let tabs=null;
  for(let i=0;i<40;i++){ try{ tabs=await httpJson('/json/list'); if(tabs.length) break; }catch(e){} await sleep(400); }
  const target=tabs.find(t=>t.type==='page');
  const ws=new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((res,rej)=>{ ws.addEventListener('open',res,{once:true}); ws.addEventListener('error',()=>rej(new Error('ws')),{once:true}); });
  const cdp=new CDP(ws);
  const exceptions=[];
  await cdp.send('Runtime.enable'); await cdp.send('Page.enable');
  cdp.on('Runtime.exceptionThrown', p=>exceptions.push(p.exceptionDetails?.exception?.description||''));
  await cdp.send('Page.navigate',{url:PAGE});
  for(let i=0;i<40;i++){ const r=await cdp.eval('(typeof TREE_READY!=="undefined") && TREE_READY===true'); if(r) break; await sleep(500); }
  await sleep(1200);

  let pass=0, fail=0;
  const results = await cdp.eval(`(async ()=>{
    const payloads = ${JSON.stringify(PAYLOADS)};
    const out = [];
    delete window.__xss;
    for (let i=0;i<payloads.length;i++){
      const p = payloads[i];
      const rec = { i, payload: p, injectedEls: 0, attrInjectedEls: 0, escAttrRound: null };
      // 1) 文本上下文：页面真实的转义入口
      const d = document.createElement('div');
      d.innerHTML = '<span class="probe">' + esc(p) + '</span>';
      document.body.appendChild(d);
      rec.injectedEls = d.querySelectorAll('img,svg,iframe,script').length;
      // 2) 属性上下文
      const d2 = document.createElement('div');
      d2.innerHTML = '<span data-x="' + escAttr(p) + '" class="probe2"></span>';
      document.body.appendChild(d2);
      const s2 = d2.querySelector('span.probe2');
      rec.escAttrRound = s2 ? s2.getAttribute('data-x') : null;
      rec.attrInjectedEls = d2.querySelectorAll('img,svg,iframe,script').length;
      d.remove(); d2.remove();
      out.push(rec);
    }
    // 等一拍，给任何可能被执行的载荷机会去设置探针
    await new Promise(r=>setTimeout(r,600));
    return { records: out, xssFlag: window.__xss === undefined ? null : window.__xss };
  })()`);

  // 对照组单独跑：证明这些载荷在**未转义**时确实是有效的（否则测试没有说服力）
  const control = await cdp.eval(`(async ()=>{
    const payloads = ${JSON.stringify(PAYLOADS)};
    const res = [];
    for (const p of payloads){
      const d = document.createElement('div');
      d.innerHTML = p;
      res.push(d.querySelectorAll('img,svg,iframe,script').length > 0);
      d.remove();
    }
    return res;
  })()`);

  console.log('载荷总数: ' + results.records.length + '\n');
  for (const r of results.records){
    const textSafe = r.injectedEls === 0;
    const attrSafe = r.attrInjectedEls === 0;
    const roundTrip = r.escAttrRound === r.payload;   // 转义后取回应还原为原字符串
    const rawWouldInject = control[r.i];              // 对照：未转义确实会注入
    const ok = textSafe && attrSafe && roundTrip;
    if (ok) pass++; else fail++;
    console.log((ok?'✅':'❌') + ' #' + r.i + ' ' + JSON.stringify(r.payload).slice(0,58));
    console.log('     文本上下文注入元素=' + r.injectedEls + '  属性上下文注入元素=' + r.attrInjectedEls +
                '  往返一致=' + roundTrip + '  (未转义对照是否注入=' + rawWouldInject + ')');
  }
  console.log('\nwindow.__xss = ' + JSON.stringify(results.xssFlag) + '   （null = 无载荷被执行）');
  console.log('未捕获异常: ' + (exceptions.length ? exceptions.join(' | ') : '无'));

  const allSafe = fail === 0 && results.xssFlag === null && exceptions.length === 0;
  console.log('\n════ 结论: ' + (allSafe ? '全部载荷被安全转义 ✅' : '存在未转义风险 ❌') + ' ════');
  try{ edge.kill(); }catch(e){}
  process.exit(allSafe ? 0 : 1);
})();
