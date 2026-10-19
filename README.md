# 申万行业资金流看板

静态看板 + FastAPI + MySQL 8.0 + 一个 Python ETL。前端三页由后端同源托管，
主题默认**明亮**，`?theme=dark` 或顶栏按钮可切暗色。

```
industry.html   行业树 / 搜索 / 成分股 / 四档资金流 / 趋势图
fundflow.html   二级行业资金流热力图 + 排行表
admin.html      数据管理控制台（需 ADMIN_KEY）
```

## 快速开始

前置：Windows + Python 3.11+ + Node.js 18+（只跑后端验证可用 24+）+ MySQL 8.0 已启动。

> **以下命令都在仓库根 `C:\[AI工作区]\biancheng` 下执行。**
> 如果你当前在 `backend` 目录（跑 ETL 时常见），把 `scripts\` 换成 `..\scripts\`，
> 例如 `powershell -ExecutionPolicy Bypass -File ..\scripts\bootstrap.ps1`。
> 脚本内部用 `$PSScriptRoot` 定位仓库，与当前目录无关 —— 只是**传入的脚本路径**要写对。

```powershell
# 1. 首次：创建 backend\.venv 并安装依赖（含 pytest）
powershell -ExecutionPolicy Bypass -File scripts\bootstrap.ps1

# 2. 配置：仅当 backend\.env 不存在时才复制模板（已存在会被覆盖掉真实凭据！）
if (-not (Test-Path backend\.env)) { Copy-Item .env.example backend\.env }
# 然后编辑 backend\.env，填 DB_PASSWORD（必填）与 ADMIN_KEY（要用管理台才填）
notepad backend\.env
```
# 3. 启动（后台运行，等健康检查通过后打印地址）
powershell -ExecutionPolicy Bypass -File scripts\start.ps1
```

打开 <http://127.0.0.1:8000/industry.html>。停止：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\stop.ps1
```

`start.ps1` 会依次验证：解释器 → 运行期依赖 → `backend\.env` → 端口占用
（若已被本项目的 uvicorn 占用则直接复用，被其它进程占用则拒绝启动）→ 启动 → 等 `/api/v1/health`。
换端口用 `-Port 8080`，要看启动日志用 `-Foreground`。

## 目录结构

```
backend/
  app/                 FastAPI 装配（main）+ 依赖（deps）+ 响应模型（schemas）
    routers/           health / industries / stocks / admin
    services/          SQL 与聚合、60s TTL 缓存、ETL 触发与审计
  static/              前端三页 + common.js（无构建步骤）
  etl.py               ETL 入口：all | meta | members | moneyflow | aggregate | incremental
  config.py db.py tushare_client.py schema.sql
  tests/               离线套件（默认）/ 连库集成套件（RUN_INTEGRATION=1）
  tools/               前端与数据自检工具（见下）
scripts/
  bootstrap.ps1        建 venv + 装依赖
  start.ps1 / stop.ps1 启停（后台用 serve.py 脱离终端）
  serve.py             用 subprocess + DETACHED_PROCESS 拉起 uvicorn，stdio 写 .run\api.stdout.log
  check.ps1            一键验收：健康 / 契约 / 静态 / 前端工具 / 主题 / 浏览器回归 / pytest
docs/                  01-09 设计、接口、审查、修复、流程图与主题落地报告
.run/                  运行时 PID 与 uvicorn 日志（已 gitignore）
```

## 前后端怎么连的

- **同源托管**：`app/main.py` 把 `backend/static` 挂在 `/`，`/` 307 到 `/industry.html`。
  前端用相对路径 `/api/v1/...`，不需要配置 base URL。
- **双击本地文件**：`common.js` 的 `apiBase()` 检测 `file:` 协议时回落到 `http://127.0.0.1:8000`；
  正常由后端托管时同源。
- **统一信封**：所有响应是 `{"code":0,"msg":"ok","data":...,"traceId":"..."}`，
  业务错误也是 **HTTP 200 + code≠0**（如 40401 行业不存在、40102 管理密钥无效）；
  每个响应带 `X-Trace-Id`，可用于对日志。
- **金额单位**：接口一律返回**亿元**；`t_moneyflow` 原始单位是万元，在 SQL 层换算。

## 主题（明亮默认 / 暗色可选）

- 优先级：`?theme=` > `localStorage['sw-theme']` > 默认 `light`。
- 三页在 CSS 之前各有一段内联脚本设置 `<html data-theme>`，避免暗色用户闪白（FOUC）。
- `common.js` 第 7 节提供 `themeGet / themeSet / themeToggle / onThemeChange`，同时挂在 `global` 与 `SW.*`。
- 明亮值写在 `:root`，暗色只在 `html[data-theme="dark"]` 下覆盖 —— 改色只动这两块。
- 脚本里需要随主题变的颜色（热力色阶、趋势图）通过 `themeGet()` 取值并 `onThemeChange()` 重绘，
  不要写死色值。

## 验证

```powershell
# 全量验收（26 项，含真实 Edge 的主题 60 项 + 浏览器回归 24 项 + pytest）
powershell -ExecutionPolicy Bypass -File scripts\check.ps1

# 不装浏览器环境下只跑接口 / 静态 / 前端工具 / pytest
powershell -ExecutionPolicy Bypass -File scripts\check.ps1 -SkipBrowser

# 连库集成套件一起跑
powershell -ExecutionPolicy Bypass -File scripts\check.ps1 -Full
```

单项工具（在 `backend/` 下执行）：

| 工具 | 作用 |
|---|---|
| `node tools/syntax-check.js` | 三页内联 JS 语法（默认三页，可传文件名） |
| `node tools/check-globals.js` | 页面引用的全局函数是否都由 common.js 提供 |
| `node tools/common-unit-test.js` | common.js 单元测试（27 项） |
| `node tools/xss-check.js` | 7 个 XSS 载荷的转义验证 |
| `node tools/theme-check.js` | **主题端到端**：默认明亮 / `?theme=dark` / 优先级 / 持久化 / 1440+390 溢出 / 运行期异常（60 项） |
| `node tools/regress-industry.js` | industry.html 真实浏览器回归（24 项） |
| `python tools/contract-snapshot.py` | 接口契约快照 |
| `python tools/etl-selfcheck.py` | 数据库行数与完整性自检 |

> `theme-check.js` / `regress-industry.js` 需要后端已启动 + 本机 Edge；
> 它们用临时 profile（`%TEMP%\biancheng-edge-profile`），不会污染日常浏览器配置。

## ETL 与数据

```powershell
cd backend
python etl.py incremental     # 只补缺失交易日（日常）
python etl.py all             # 全量重建
python etl.py moneyflow --days 5
```

- 数据源是 Tushare 兼容代理（`TS_ENDPOINT`），token 取 `TS_TOKEN` 或 `TS_TOKEN_FILE` 指定的 CSV。
- 采集按页推进：**offset 必须按服务端实际返回行数前进**，否则会静默截断。
- 退出码：0 成功 / 1 配置或连接错误 / 2 部分失败 / 3 有截断或落库异常。
- 改 `etl.py` 立即生效（ETL 是子进程）；改 `app/**` 必须重启 uvicorn。
- 管理台「全量更新 / 增量更新」走 `POST /api/v1/admin/fetch`，需要 `X-Admin-Key`。

## 配置（backend\.env）

模板见 `.env.example`。最常改的几项：

| 变量 | 说明 |
|---|---|
| `DB_HOST/DB_PORT/DB_USER/DB_PASSWORD/DB_NAME` | MySQL 连接 |
| `TS_ENDPOINT` / `TS_TOKEN_FILE` | 数据源与 token |
| `MONEYFLOW_DAYS` | 资金流主窗口交易日数（默认 40，本机实际 242） |
| `API_CACHE_TTL` | 聚合接口进程内缓存秒数，0 关闭 |
| `ADMIN_KEY` | 留空或默认值时管理接口**一律拒绝**（fail closed） |
| `ADMIN_IP_ALLOWLIST` / `ADMIN_RATE_LIMIT_PER_MIN` | 管理接口白名单与限流 |
| `CORS_ORIGINS` | 默认只放开本机 8000 |

## 常见问题

**端口被占 / 启动失败**
`start.ps1` 只复用命令行含 `uvicorn app.main:app` 的进程；其它进程占用会直接报错，不会杀。
用 `-Port` 换端口，或 `-Force` 停掉本项目旧实例。

**`Start-Process` / `& python` 报「找不到文件」**
受限 shell（计划任务、部分沙箱）里 `PATHEXT` 可能被裁剪，PowerShell 会把 `.exe` 当文档打开。
`scripts/serve.py` 已绕开这一点（用 `subprocess` + `DETACHED_PROCESS`）；若仍报错，
在启动前执行 `$env:PATHEXT='.COM;.EXE;.BAT;.CMD'`。

**pytest 未安装**
运行 `scripts\bootstrap.ps1`；它会把依赖装进 `backend\.venv`，`start.ps1` / `check.ps1` 会优先使用它。

**页面显示「后端未连接」**
确认 `http://127.0.0.1:8000/api/v1/health` 返回 `code:0`。DB 挂了 health 仍返回 200 但 `db=down`。

**health 返回 `db=down` / 日志里 `1045 Access denied ... (using password: NO)`**
`backend\.env` 的 `DB_PASSWORD` 为空或不对。排查顺序：

1. `start.ps1` 现在会在 `db=down` 时直接以退出码 1 结束并打印 `api_run.log` 尾部，不再误报 `Healthy`。
2. **如果 `backend\.env` 与 `.env.example` 内容一模一样**，说明它被模板覆盖过 ——
   `Copy-Item` 会保留源文件时间戳，从 mtime 看不出来。原 `DB_PASSWORD` / `ADMIN_KEY` / `TS_TOKEN_FILE`
   已丢失，需重新填写（`TS_TOKEN_FILE` 可填 `C:\Users\<你>\jsdata_tk.csv`）。
   以后复制模板请用：`if (-not (Test-Path backend\.env)) { Copy-Item .env.example backend\.env }`。
3. `ADMIN_KEY` 留空 = 管理接口一律拒绝（fail closed），这是**预期行为**，不是故障。

**怕再被覆盖？**
`backend\.env` 填好后备份一份（`.env.bak` 已在 `.gitignore` 里，不会被提交）：

```powershell
Copy-Item backend\.env backend\.env.bak
```

或直接备份到仓库外（更保险）：`Copy-Item backend\.env "$env:USERPROFILE\.env.biancheng.bak"`。

`ADMIN_KEY` 需要随机串时用：

```powershell
-join ((48..57)+(65..90)+(97..122) | Get-Random -Count 32 | ForEach-Object {[char]$_})
```

**端口被占用 `10048`**
先 `scripts\stop.ps1`（只停本项目的 uvicorn）。若提示端口被非本项目进程占用，用 `-Port` 换端口。
`start.ps1` 会主动检测并复用已在运行的本项目实例，重复执行是幂等的。

**改了 CSS 但浏览器还是旧的**
后端已对 HTML/JS/CSS 统一发 `Cache-Control: no-cache`（每次回源校验，命中 ETag 时 304），
正常情况下普通刷新即可拿到新版本。若仍是旧的：`Ctrl+F5` 强制刷新，或 DevTools → Application →
Clear site data。若整个站点都像“旧版本”（例如 fundflow 顶栏写着「模拟数据」、主题切不动），
先确认地址是 `http://127.0.0.1:8000/...` 而不是浏览器里残留的旧标签页，再确认改的是
`backend/static/`（根目录副本不参与运行）。

## 回滚

- 主题：删三页 `<head>` 里的防闪烁内联脚本 + `.theme-sw` 按钮及其绑定 + `html[data-theme="dark"]` 块，
  把 `:root` 换回原暗色值；`common.js` 的主题模块是纯新增，不删也不影响原逻辑。
- 工程化脚本：`scripts/` 与 `README.md` 是纯新增，删除即可，不影响后端与前端运行。
- 数据：`backend/schema.sql` 建表；ETL 可重复执行（幂等 upsert），回滚数据用备份或重跑 `etl.py all`。

## 相关文档

| 文档 | 内容 |
|---|---|
| `docs/01-架构设计.md` | 分层与数据流 |
| `docs/02-模块说明.md` | 模块职责 |
| `docs/03-接口文档.md` | 13 个接口的参数与响应 |
| `docs/05-审查报告_2026-10-06.md` | 安全与契约问题清单 |
| `docs/07-前端修改报告_2026-10-06.md` | 前端修复 |
| `docs/08-项目流程图.md` | 端到端流程图 |
| `docs/09-明亮主题落地报告_2026-10-19.md` | 明亮主题（B 方案）落地细节 |
| `docs/10-可执行化落地报告_2026-10-19.md` | 本次“接后端 + 可执行化”的改动与验证 |
| `docs/11-本地运行操作手册.txt` | 从零到浏览器看到完整界面的纯文本步骤（含排障与速查卡） |
