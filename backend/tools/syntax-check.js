/* 前端语法校验
 *
 * 从 <script> 标签中抽出内联 JS，交给 `node --check` 做语法检查。
 * 页面是单个大 HTML，没有构建步骤，这是唯一能自动拦住「语法写错直接白屏」
 * 的检查手段。
 *
 * 用法：
 *   node backend/tools/syntax-check.js                    # 默认校验 static/industry.html
 *   node backend/tools/syntax-check.js static/fundflow.html
 *   node backend/tools/syntax-check.js static/*.html      # 由 shell 展开
 *
 * 退出码：0 全部通过；1 有文件语法失败（可直接用于 CI）。
 *
 * 注意：只做语法检查，不做运行时/接口校验。改动页面后请同时跑
 * backend/tools/regress-industry.js（需要本地服务与 Edge）。
 */
const fs = require('fs');
const os = require('os');
const path = require('path');
const { execFileSync } = require('child_process');

const STATIC_DIR = path.join(__dirname, '..', 'static');

const targets = (process.argv.slice(2).length
  ? process.argv.slice(2)
  : ['industry.html']
).map(f => path.resolve(STATIC_DIR, f));

const tmp = path.join(os.tmpdir(), 'biancheng-syntax-check.js');
let failed = 0;

for (const file of targets) {
  const label = path.relative(path.join(__dirname, '..', '..'), file);

  if (!fs.existsSync(file)) {
    console.log(`SKIP  ${label} —— 文件不存在`);
    continue;
  }

  const src = fs.readFileSync(file, 'utf8');
  const blocks = [...src.matchAll(/<script[^>]*>([\s\S]*?)<\/script>/g)].map(m => m[1]);

  if (!blocks.length) {
    console.log(`SKIP  ${label} —— 没有内联 <script>`);
    continue;
  }

  fs.writeFileSync(tmp, blocks.join('\n;\n'), 'utf8');

  try {
    execFileSync(process.execPath, ['--check', tmp], { stdio: 'pipe' });
    console.log(`PASS  ${label}  (${blocks.length} 个 script 块, ${Buffer.byteLength(src, 'utf8')} B)`);
  } catch (e) {
    failed++;
    console.log(`FAIL  ${label}  (${blocks.length} 个 script 块, ${Buffer.byteLength(src, 'utf8')} B)`);
    console.log(String(e.stderr || e.stdout || e.message).split('\n').slice(0, 15).join('\n'));
  }
}

fs.rmSync(tmp, { force: true });

if (failed) {
  console.log(`\n${failed} 个文件语法检查失败`);
  process.exit(1);
}
console.log('\n全部通过');
