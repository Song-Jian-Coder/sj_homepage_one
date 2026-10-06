/* 全局引用检查
 *
 * 用途：把三个页面的内联 JS 里「看起来是全局函数调用」的标识符抓出来，
 *       与 common.js 实际导出的名字对比，提前发现「改完少挂一个全局」
 *       这类运行期错误（页面不会白屏，而是静默变成错误空态）。
 *
 * 由来：审查报告 8.12 建议把 esc/API 封装抽成 common.js 共享。
 *       实施时我两次踩到「只挂了 SW.xxx、忘了挂 global.xxx」，
 *       页面报 `apiGet is not defined` / `escAttr is not defined`。
 *       这个脚本就是那次教训的护栏。
 *
 * 用法：
 *   node backend/tools/check-globals.js                        # 默认检查 static/common.js + 三个在线页面
 *   node backend/tools/check-globals.js <static目录> a.html b.html
 *
 * 退出码：0 无缺失；1 存在缺失引用。
 */
const fs = require('fs');
const path = require('path');

const STATIC = process.argv[2] || path.join(__dirname, '..', 'static');
const common = fs.readFileSync(path.join(STATIC, 'common.js'), 'utf8');
const commonNoComment = stripJsComments(common);

/* 只有**真正挂到全局**的名字才算数：
   · `global.X = ...`（页面能直接调用 X）
   · `global.SW = {...}` 里的键（页面通过 SW.X 调用）
   注意：**不能**把 IIFE 内部的 `function foo(){}` 声明算进来 ——
   那些名字在 IIFE 作用域里，页面根本拿不到。把声明算作导出会
   让本脚本失去意义（负向测试：注释掉 global.escAttr 后仍报「无缺失」）。 */
const exported = new Set();
for (const m of commonNoComment.matchAll(/global\.([A-Za-z_$][\w$]*)\s*=/g)) exported.add(m[1]);

const swKeys = new Set();
const swBlock = commonNoComment.match(/global\.SW\s*=\s*\{([\s\S]*?)\};/);
if (swBlock) for (const m of swBlock[1].matchAll(/([A-Za-z_$][\w$]*)\s*:/g)) swKeys.add(m[1]);

// `exported` 里的 'SW' 是对象本身，不算可调用名
const callable = new Set([...exported].filter(n => n !== 'SW'));

console.log('common.js 导出（global，可调用）: ' + [...callable].sort().join(', '));
console.log('common.js 导出（SW.*）          : ' + [...swKeys].sort().join(', '));
console.log('');

// 这些是浏览器/JS 内置或页面自己定义的，不算缺失
const BUILTIN = new Set(['fetch','JSON','Object','Array','String','Number','Math','Date','Map','Set',
  'Promise','Error','console','document','window','location','localStorage','sessionStorage',
  'setTimeout','clearTimeout','setInterval','clearInterval','requestAnimationFrame','innerWidth',
  'alert','parseInt','parseFloat','isFinite','isNaN','encodeURIComponent','decodeURIComponent',
  'URLSearchParams','Event','WebSocket','RegExp','Symbol','Intl','performance','navigator','history',
  'XMLHttpRequest','structuredClone','queueMicrotask','getComputedStyle']);

const pages = process.argv.slice(3).length
  ? process.argv.slice(3)
  : ['industry.html', 'fundflow.html', 'admin.html'];
let problems = 0;

/* CSS/模板里的函数名噪音：这些出现在 JS 字符串（构造 style / SVG 属性）中，
   形如 rgba(...) / translateX(...) / var(...)，不是 JS 调用。 */
const CSS_NOISE = new Set(['rgba','rgb','hsl','hsla','var','calc','min','max','clamp',
  'translate','translateX','translateY','translate3d','scale','scaleX','scaleY','rotate',
  'skewX','skewY','matrix','blur','brightness','contrast','saturate','grayscale','drop-shadow',
  'cubic-bezier','linear-gradient','radial-gradient','conic-gradient','repeat','minmax',
  'fit-content','url','format','local','not','and','or','round','mod','rem','env','attr',
  'perspective','opacity','invert','sepia','hue-rotate','steps','polygon','circle','ellipse',
  'inset','path','rect','media','supports','child','nth-child','first-child','last-child',
  'focus','hover','active','before','after','placeholder','selection','backdrop','viewport']);

/* 去掉 JS 注释后再扫描，避免「说明文字里提到的已删函数名」被判成缺失引用。
   （实测：删掉 genSeries/aggregate 后，解释性注释里仍写着它们的名字。） */
function stripJsComments(src){
  // 逐字符扫描，跳过字符串字面量，避免误删字符串里的 // 或 /* */
  let out = '', i = 0, n = src.length;
  let state = 'code';           // code | sq | dq | tpl | line | block | re
  while (i < n){
    const c = src[i], c2 = src[i+1];
    if (state === 'code'){
      if (c === '/' && c2 === '/'){ state = 'line'; i += 2; continue; }
      if (c === '/' && c2 === '*'){ state = 'block'; i += 2; continue; }
      if (c === "'") state = 'sq';
      else if (c === '"') state = 'dq';
      else if (c === '`') state = 'tpl';
      out += c; i++; continue;
    }
    if (state === 'line'){ if (c === '\n'){ state = 'code'; out += c; } i++; continue; }
    if (state === 'block'){ if (c === '*' && c2 === '/'){ state = 'code'; i += 2; } else i++; continue; }
    if (state === 'sq' || state === 'dq' || state === 'tpl'){
      out += c;
      if (c === '\\'){ out += (src[i+1] || ''); i += 2; continue; }
      if ((state === 'sq' && c === "'") || (state === 'dq' && c === '"') || (state === 'tpl' && c === '`')) state = 'code';
      i++; continue;
    }
    out += c; i++;
  }
  return out;
}
for (const page of pages) {
  const src = fs.readFileSync(path.join(STATIC, page), 'utf8');
  const rawJs = [...src.matchAll(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/g)].map(m => m[1]).join('\n;\n');
  const js = stripJsComments(rawJs);

  // 页面自己定义的函数/变量
  const declared = new Set();
  for (const m of js.matchAll(/\bfunction\s+([A-Za-z_$][\w$]*)\s*\(/g)) declared.add(m[1]);
  for (const m of js.matchAll(/\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)/g)) declared.add(m[1]);
  for (const m of js.matchAll(/\b(?:const|let|var)\s*\{([^}]*)\}\s*=/g))
    m[1].split(',').forEach(x => { const n = x.split(':').pop().trim(); if (n) declared.add(n); });
  for (const m of js.matchAll(/\bclass\s+([A-Za-z_$][\w$]*)/g)) declared.add(m[1]);

  // 所有「裸标识符 + (」形式的调用
  const called = new Set();
  for (const m of js.matchAll(/(?<![.\w$])([A-Za-z_$][\w$]*)\s*\(/g)) called.add(m[1]);

  // 所有 SW.xxx 引用
  const swRefs = new Set();
  for (const m of js.matchAll(/\bSW\.([A-Za-z_$][\w$]*)/g)) swRefs.add(m[1]);

  const missing = [];
  for (const n of called) {
    if (BUILTIN.has(n) || declared.has(n) || callable.has(n) || CSS_NOISE.has(n)) continue;
    // 关键字/运算符伪匹配
    if (['if','for','while','switch','catch','return','typeof','function','new','do','else','in','of','delete','void','instanceof','case','throw','yield','await','async'].includes(n)) continue;
    missing.push(n);
  }
  const missingSW = [...swRefs].filter(k => !swKeys.has(k));

  if (missing.length || missingSW.length) {
    problems++;
    console.log('❌ ' + page);
    if (missing.length)   console.log('   未定义的裸函数调用: ' + missing.sort().join(', '));
    if (missingSW.length) console.log('   SW 上不存在的成员 : ' + missingSW.sort().join(', '));
  } else {
    console.log('✅ ' + page + ' —— 无缺失的全局引用');
  }
}
process.exit(problems ? 1 : 0);
