/* 主题端到端验证：明亮（默认）/ 暗色（?theme=dark）/ 按钮切换 / localStorage 持久化
 *
 * 为什么需要这个工具：
 *   docs/09 的明亮主题改造是「三页 × 两主题 × 两视口」的视觉改动，靠人眼截图验收，
 *   无法在 CI / 交接时重复执行。本工具用 CDP 驱动真实 Edge，把报告 §4.1 的验收口径
 *   变成可断言、可退出码化的检查：
 *     - 默认（无参数、无 localStorage）必须是明亮；
 *     - ?theme=dark 必须切到暗色，且优先于 localStorage；
 *     - 点按钮切换后必须持久化，刷新（重新导航）后仍保持；
 *     - 明亮/暗色的实际背景亮度必须相反（防「改了变量但没生效」）；
 *     - 1440 / 390 两档视口无横向溢出；
 *     - 全程无 JS 运行期异常。
 *
 * 用法（需先启动后端，见 scripts/start.ps1）：
 *   node backend/tools/theme-check.js
 *   BASE=http://127.0.0.1:8000 node backend/tools/theme-check.js
 *   EDGE=C:\...\msedge.exe node backend/tools/theme-check.js
 *
 * 退出码：0 全部通过；1 有失败（可直接用于 CI）。
 *
 * 说明：本脚本不修改任何页面文件，只读 DOM / CSSOM；localStorage 在每个场景前后清理，
 * 不会污染使用者浏览器（使用独立临时 profile，见 EDGE_PROFILE）。
 */
const http = require('http');
const os = require('os');
const path = require('path');
const { spawn } = require('child_process');

const BASE = (process.env.BASE || 'http://127.0.0.1:8000').replace(/\/$/, '');
const WS_URL = 'http://127.0.0.1:9222';
const EDGE = process.env.EDGE ||
  'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';
/* 与 regress-industry.js 共用临时 profile；不要写进仓库（曾生成 170MB 缓存目录）。 */
const PROFILE = process.env.EDGE_PROFILE ||
  path.join(os.tmpdir(), 'biancheng-edge-profile');

const PAGES = [
  { name: 'industry.html', wait: 'window.TREE_READY === true || (typeof TREE !== "undefined" && TREE.length > 0)' },
  { name: 'fundflow.html', wait: 'document.querySelectorAll(".tile").length > 0' },
  { name: 'admin.html',    wait: 'document.readyState === "complete"' }
];

const LIGHT_BG = 'rgb(251, 252, 253)';
const DARK_BG  = 'rgb(10, 11, 13)';

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }
function httpJson(p) {
  return new Promise((res, rej) => {
    http.get(WS_URL + p, r => {
      let d = ''; r.on('data', c => d += c);
      r.on('end', () => { try { res(JSON.parse(d)); } catch (e) { rej(e); } });
    }).on('error', rej);
  });
}

class CDP {
  constructor(ws) {
    this.ws = ws; this.id = 0; this.waits = new Map(); this.evs = new Map();
    ws.addEventListener('message', ev => {
      const m = JSON.parse(ev.data);
      if (m.id && this.waits.has(m.id)) {
        const { res, rej } = this.waits.get(m.id); this.waits.delete(m.id);
        m.error ? rej(new Error(JSON.stringify(m.error))) : res(m.result);
      } else if (m.method && this.evs.has(m.method)) {
        this.evs.get(m.method).forEach(f => f(m.params));
      }
    });
  }
  send(method, params = {}) {
    const id = ++this.id;
    return new Promise((res, rej) => {
      this.waits.set(id, { res, rej });
      this.ws.send(JSON.stringify({ id, method, params }));
      setTimeout(() => {
        if (this.waits.has(id)) { this.waits.delete(id); rej(new Error('timeout: ' + method)); }
      }, 30000);
    });
  }
  on(method, fn) { if (!this.evs.has(method)) this.evs.set(method, []); this.evs.get(method).push(fn); }
  async eval(expr) {
    const r = await this.send('Runtime.evaluate', {
      expression: `Promise.resolve(${expr}).then(v => JSON.stringify(v === undefined ? null : v)).catch(e => JSON.stringify({ __err: String(e && e.message || e) }))`,
      awaitPromise: true, returnByValue: true
    });
    if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails));
    const v = r.result.value;
    return v === undefined || v === null ? undefined : JSON.parse(v);
  }
}

const results = [];
function check(name, pass, detail) {
  results.push({ name, pass, detail: detail || '' });
  console.log((pass ? '✅' : '❌') + ' ' + name + (detail ? ('  → ' + detail) : ''));
}
function skip(name, why) {
  results.push({ name, pass: true, skipped: true, detail: why });
  console.log('⏭️  ' + name + '  → ' + why + '（跳过）');
}

/* 亮度：把 "rgb(r, g, b)" 换成 0-255 感知亮度，用于断言「两主题确实不同」 */
function luminanceOf(rgbStr) {
  const m = /rgba?\((\d+),\s*(\d+),\s*(\d+)/.exec(String(rgbStr || ''));
  if (!m) return null;
  return Math.round(0.2126 * +m[1] + 0.7152 * +m[2] + 0.0722 * +m[3]);
}

(async () => {
  /* 0) 后端可达性：主题检查依赖真实托管页面，避免脚本自己起静态服务导致口径不符 */
  try {
    const h = await new Promise((res, rej) => {
      http.get(BASE + '/api/v1/health', r => {
        let d = ''; r.on('data', c => d += c); r.on('end', () => res({ status: r.statusCode, body: d }));
      }).on('error', rej);
    });
    const j = JSON.parse(h.body);
    check('后端可达', h.status === 200 && j.code === 0, `${BASE}/api/v1/health → db=${j.data && j.data.db}`);
  } catch (e) {
    console.error(`\n❌ 无法连接后端 ${BASE}：${e.message}`);
    console.error('   先运行 scripts/start.ps1（或 python -m uvicorn app.main:app --port 8000），再执行本脚本。');
    process.exit(1);
  }

  /* 1) 启动 Edge（无头 + CDP） */
  const edge = spawn(EDGE, [
    '--headless=new', '--remote-debugging-port=9222', '--disable-gpu',
    '--no-first-run', '--no-default-browser-check', '--disable-extensions',
    '--user-data-dir=' + PROFILE, 'about:blank'
  ], { stdio: 'ignore', detached: false });

  let tabs = null;
  for (let i = 0; i < 50; i++) {
    try { tabs = await httpJson('/json/list'); if (tabs && tabs.length) break; } catch (e) { /* 等端口就绪 */ }
    await sleep(400);
  }
  if (!tabs) { console.error('❌ 无法连接 Edge CDP（' + EDGE + '）'); try { edge.kill(); } catch (e) {} process.exit(1); }

  const target = tabs.find(t => t.type === 'page');
  const ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((res, rej) => {
    ws.addEventListener('open', res, { once: true });
    ws.addEventListener('error', () => rej(new Error('ws error')), { once: true });
  });
  const cdp = new CDP(ws);

  const errors = [];
  await cdp.send('Runtime.enable');
  await cdp.send('Page.enable');
  /* 必须禁用缓存：来回导航的是同一批 URL，Edge 会复用 profile 里的 HTML/CSS，
     改完样式重跑仍是旧规则（实测：修完 390 溢出后 scrollWidth 仍报 514）。 */
  await cdp.send('Network.enable');
  await cdp.send('Network.setCacheDisabled', { cacheDisabled: true });
  await cdp.on('Runtime.exceptionThrown', p => {
    errors.push(p.exceptionDetails && p.exceptionDetails.exception
      ? p.exceptionDetails.exception.description
      : JSON.stringify(p.exceptionDetails));
  });
  await cdp.on('Runtime.consoleAPICalled', p => {
    if (p.type === 'error') {
      errors.push((p.args || []).map(a => String(a.value !== undefined ? a.value : (a.description || ''))).join(' '));
    }
  });

  async function setViewport(width, height) {
    await cdp.send('Emulation.setDeviceMetricsOverride', {
      width, height, deviceScaleFactor: 1, mobile: false
    });
  }

  async function navigate(url, waitExpr) {
    await cdp.send('Page.navigate', { url });
    for (let i = 0; i < 60; i++) {
      try { if (await cdp.eval('document.readyState') === 'complete') break; } catch (e) { /* 导航中 context 重建 */ }
      await sleep(200);
    }
    if (waitExpr) {
      for (let i = 0; i < 60; i++) {
        try { if (await cdp.eval(waitExpr)) break; } catch (e) { /* 同上 */ }
        await sleep(300);
      }
    }
    await sleep(500);
  }

  /* 读取当前页面主题相关状态 */
  async function probe() {
    return cdp.eval(`(() => {
      const de = document.documentElement;
      const cs = getComputedStyle(de);
      const bs = getComputedStyle(document.body);
      const btn = document.getElementById('themeSw');
      const bgVar = cs.getPropertyValue('--bg').trim();
      const textVar = cs.getPropertyValue('--text').trim();
      const tile = document.querySelector('.tile');
      return {
        dataTheme: de.getAttribute('data-theme'),
        colorScheme: de.style.colorScheme || cs.colorScheme || '',
        bodyBg: bs.backgroundColor,
        bgVar: bgVar,
        textVar: textVar,
        btnText: btn ? btn.textContent.replace(/\\s+/g, ' ').trim() : '',
        btnPressed: btn ? btn.getAttribute('aria-pressed') : null,
        stored: (() => { try { return localStorage.getItem('sw-theme'); } catch (e) { return '<err>'; } })(),
        scrollWidth: document.documentElement.scrollWidth,
        clientWidth: document.documentElement.clientWidth,
        tileBg: tile ? getComputedStyle(tile).backgroundColor : null,
        tileFg: tile ? getComputedStyle(tile).color : null,
        hasThemeSw: !!btn
      };
    })()`);
  }

  async function clickToggle() {
    await cdp.eval(`(() => { const b = document.getElementById('themeSw'); if (!b) return false; b.click(); return true; })()`);
    await sleep(700);
  }

  async function clearThemeStorage() {
    await cdp.eval(`(() => { try { localStorage.removeItem('sw-theme'); } catch (e) {} return 1; })()`);
  }

  console.log('\n══════ 主题端到端验证（明亮为默认 / ?theme=dark 开关）══════');
  await setViewport(1440, 900);

  for (const page of PAGES) {
    const url = BASE + '/' + page.name;
    console.log('\n【' + page.name + '】');

    /* ── A. 默认必须是明亮 ── */
    await navigate(url, page.wait);
    await clearThemeStorage();
    await navigate(url, page.wait);            // 清完再导航，模拟「首次访问」
    const a = await probe();
    check(`${page.name} 默认主题为 light`, a.dataTheme === 'light', `data-theme=${a.dataTheme}`);
    check(`${page.name} 默认无 localStorage 残留`, a.stored === null, `sw-theme=${a.stored}`);
    check(`${page.name} 存在主题按钮`, a.hasThemeSw === true, a.hasThemeSw ? `文案「${a.btnText}」` : '缺 #themeSw');
    check(`${page.name} color-scheme 跟随明亮`, String(a.colorScheme).indexOf('dark') < 0, `color-scheme=${a.colorScheme}`);
    const lightLum = luminanceOf(a.bodyBg);
    check(`${page.name} 明亮背景确为浅色`, lightLum !== null && lightLum > 200,
      `body bg=${a.bodyBg}（亮度 ${lightLum}，期望 ${LIGHT_BG}）`);
    check(`${page.name} 无横向溢出 @1440`, a.scrollWidth <= a.clientWidth + 1,
      `scrollWidth=${a.scrollWidth} clientWidth=${a.clientWidth}`);

    /* ── B. ?theme=dark 必须切暗，且优先于 localStorage ── */
    await navigate(url + '?theme=dark', page.wait);
    const b = await probe();
    const darkLum = luminanceOf(b.bodyBg);
    check(`${page.name} ?theme=dark 生效`, b.dataTheme === 'dark', `data-theme=${b.dataTheme}`);
    check(`${page.name} color-scheme 跟随暗色`, String(b.colorScheme).indexOf('dark') >= 0, `color-scheme=${b.colorScheme}`);
    check(`${page.name} 暗色背景确为深色`, darkLum !== null && darkLum < 40,
      `body bg=${b.bodyBg}（亮度 ${darkLum}，期望 ${DARK_BG}）`);
    check(`${page.name} 两主题背景确实不同`, lightLum !== null && darkLum !== null && lightLum > darkLum,
      `light=${lightLum} / dark=${darkLum}`);
    check(`${page.name} 按钮文案随主题变化`, a.btnText && b.btnText && a.btnText !== b.btnText,
      `light「${a.btnText}」/ dark「${b.btnText}」`);
    /* ?theme= 高于 localStorage：先写入 light，再用 ?theme=dark 访问，必须仍是 dark */
    await cdp.eval(`(() => { try { localStorage.setItem('sw-theme', 'light'); } catch (e) {} return 1; })()`);
    await navigate(url + '?theme=dark', page.wait);
    const b2 = await probe();
    check(`${page.name} ?theme= 优先于 localStorage`, b2.dataTheme === 'dark',
      `localStorage=light 时访问 ?theme=dark → data-theme=${b2.dataTheme}`);

    /* ── C. 按钮切换 + 持久化 ── */
    await navigate(url, page.wait);            // 此处 localStorage=light
    await clickToggle();                        // light → dark
    let c = await probe();
    check(`${page.name} 点击按钮切到暗色`, c.dataTheme === 'dark', `data-theme=${c.dataTheme}`);
    check(`${page.name} 切换后写入 localStorage`, c.stored === 'dark', `sw-theme=${c.stored}`);
    await navigate(url, page.wait);            // 刷新后应保持暗色
    c = await probe();
    check(`${page.name} 刷新后保持暗色`, c.dataTheme === 'dark', `data-theme=${c.dataTheme}`);
    await clickToggle();                        // dark → light
    c = await probe();
    check(`${page.name} 再点切回明亮`, c.dataTheme === 'light', `data-theme=${c.dataTheme}`);
    check(`${page.name} 切回后 localStorage=light`, c.stored === 'light', `sw-theme=${c.stored}`);

    /* ── D. fundflow 热力色阶必须按主题取值（JS 侧，不是纯 CSS）── */
    if (page.name === 'fundflow.html' && b.tileBg) {
      /* 只断言背景不同：色阶前几档是深字、后几档白字，同一档在两主题下前景可能相同，
         但底色必须随 TILE_STEPS 变化（09 §1.4）。 */
      check('fundflow 热力色阶按主题取值', b.tileBg !== a.tileBg,
        `light ${a.tileBg}/${a.tileFg} → dark ${b.tileBg}/${b.tileFg}`);
    } else if (page.name === 'fundflow.html') {
      skip('fundflow 热力色阶按主题取值', '未渲染出 .tile（无数据或接口未就绪）');
    }

    /* ── E. 移动端 390 无横向溢出 ── */
    await setViewport(390, 844);
    await navigate(url, page.wait);
    const m = await probe();
    check(`${page.name} 无横向溢出 @390`, m.scrollWidth <= m.clientWidth + 1,
      `scrollWidth=${m.scrollWidth} clientWidth=${m.clientWidth}`);
    check(`${page.name} 移动端仍为明亮`, m.dataTheme === 'light', `data-theme=${m.dataTheme}`);
    await setViewport(1440, 900);

    await clearThemeStorage();
  }

  /* ── F. 全局：无运行期异常 ── */
  console.log('\n【运行期】');
  check('无 JS 运行期异常', errors.length === 0, errors.slice(0, 3).join(' || ') || '无');

  const pass = results.filter(r => r.pass).length;
  const failed = results.length - pass;
  console.log('\n══════════════════════════════════════');
  console.log(`  合计 ${results.length} 项 · 通过 ${pass} · 失败 ${failed}`);
  console.log('══════════════════════════════════════');
  results.filter(r => !r.pass).forEach(r => console.log('  ❌ ' + r.name + ' → ' + r.detail));

  try { ws.close(); } catch (e) { /* 已关闭 */ }
  try { edge.kill(); } catch (e) { /* 已退出 */ }
  if (results.length === 0) {
    console.error('  ⚠️  没有任何断言被执行，视为失败');
    process.exit(1);
  }
  process.exit(failed === 0 ? 0 : 1);
})().catch(e => { console.error('主题检查脚本异常:', e && e.message); process.exit(1); });
