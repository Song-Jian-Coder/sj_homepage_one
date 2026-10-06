/* common.js 单元测试
 *
 * 覆盖：转义（含属性上下文与引号/反引号）、金额格式、阈值判定、
 *       FastAPI 422 detail 的压平、代码归一。
 *
 * 用法：
 *   node backend/tools/common-unit-test.js            # 默认测 ../static/common.js
 *   node backend/tools/common-unit-test.js <路径>      # 指定 common.js
 *
 * 退出码：0 全部通过；1 有失败（可直接用于 CI）。
 *
 * 存在的意义：审查报告 8.12 / 9.3-1 把「抽一个 common.js 并在三页复用」
 * 定为投入产出比最高的一步。这个文件让那一步**可被自动验证** ——
 * 转义函数一旦被改坏，这里立刻红。
 */
const path = require('path');

const target = process.argv[2] || path.join(__dirname, '..', 'static', 'common.js');
require(path.resolve(target));
const S = global.SW;
if (!S) {
  console.log('FAIL  未能从 ' + target + ' 载入 SW 导出');
  process.exit(1);
}
const cases = [];
function eq(name, got, want){ cases.push([name, got === want, got, want]); }
// esc：文本 + 属性（含引号与反引号）
eq('esc-lt', S.esc('<img src=x onerror=alert(1)>'), '&lt;img src=x onerror=alert(1)&gt;');
eq('esc-quote', S.esc('a" onmouseover="x'), 'a&quot; onmouseover=&quot;x');
eq('esc-squote', S.esc("a' b"), 'a&#39; b');
eq('esc-backtick', S.esc('a`b'), 'a&#96;b');
eq('esc-null', S.esc(null), '');
eq('esc-undef', S.esc(undefined), '');
eq('esc-amp', S.esc('&amp;'), '&amp;amp;');
// fnum
eq('fnum-null', S.fnum(null), null);
eq('fnum-null-dflt', S.fnum(null, 0), 0);
eq('fnum-str', S.fnum('1.5'), 1.5);
eq('fnum-nan', S.fnum(NaN, 0), 0);
// 金额格式
eq('yi-pos', S.yi(1.234), '+1.23亿');
eq('yi-neg', S.yi(-1.235), '-1.24亿');
eq('yi-null', S.yi(null), '+0.00亿');
eq('pct', S.pctStr(12.345), '+12.35%');
// 阈值统一
eq('cls-up', S.cls(0.006), 'up');
eq('cls-flat', S.cls(0.004), 'flat');
eq('cls-down', S.cls(-0.006), 'down');
eq('clsAdmin-pos', S.clsAdmin(0.006), 'pos');
eq('clsAdmin-null', S.clsAdmin(null), 'flat');
eq('threshold', S.THRESHOLD, 0.005);
// 422 detail
eq('detail422', S.detailText([{loc:['query','q'],msg:'String should have at least 1 character',type:'string_too_short'}]), 'q: String should have at least 1 character');
// tsCode
eq('tsCode-6', S.tsCode('600519'), '600519.SH');
eq('tsCode-0', S.tsCode('000001'), '000001.SZ');
eq('tsCode-keep', S.tsCode('600519.SH'), '600519.SH');
eq('bareCode', S.bareCode('600519.SH'), '600519');
eq('thousands', S.thousands(5571), (5571).toLocaleString('zh-CN'));

let pass = 0, fail = 0;
for (const [n, ok, got, want] of cases){
  if (ok) { pass++; } else { fail++; console.log('FAIL ' + n + '  got=' + JSON.stringify(got) + ' want=' + JSON.stringify(want)); }
}
console.log('common.js 单元测试: pass=' + pass + ' fail=' + fail + (fail ? '' : '  ✅ 全部通过'));
process.exit(fail ? 1 : 0);
