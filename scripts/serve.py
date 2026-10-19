# -*- coding: utf-8 -*-
"""后台启动器：把 uvicorn 变成脱离当前终端的独立进程。

为什么不在 PowerShell 里直接 Start-Process / ProcessStartInfo：
  1) 受限 shell（计划任务、某些沙箱）里 Start-Process 会因 shell 关联解析失败报
     「系统找不到指定的文件」；
  2) 子进程若继承 PowerShell 的 stdout 管道，调用方（CI、本仓库的 tools/*.js 包装脚本）
     读管道时会一直等到 uvicorn 退出才拿到 EOF —— 表现为「启动命令挂死」；
  3) 事件式日志重定向（add_OutputDataReceived）在 PS 5.1 无 runspace 的线程上会抛
     PSInvalidOperationException。

做法：用 subprocess + DETACHED_PROCESS 启动 uvicorn，stdio 直接指向日志文件，
再把 PID 打到 stdout 交给调用方。启动器自己立即退出，终端只拿到一行 PID。

用法：
    python scripts/serve.py --host 127.0.0.1 --port 8000
输出（成功）：
    <pid>
日志：
    backend/.run/api.stdout.log（追加）
退出码：
    0 已启动；2 启动即崩溃（附日志尾部）；1 参数/路径错误
"""
import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BACKEND = REPO / "backend"
RUN_DIR = REPO / ".run"
LOG = RUN_DIR / "api.stdout.log"

# Windows 进程创建标志
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NO_WINDOW = 0x08000000


def tail(path: Path, lines: int = 40) -> str:
    try:
        data = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(data[-lines:])


def main() -> int:
    ap = argparse.ArgumentParser(description="start uvicorn detached")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--app", default="app.main:app")
    ap.add_argument("--log-level", default="info")
    ap.add_argument("--settle", type=float, default=1.2,
                    help="seconds to wait before deciding the process survived startup")
    args = ap.parse_args()

    if not (BACKEND / "app" / "main.py").is_file():
        print(f"backend not found: {BACKEND}", file=sys.stderr)
        return 1

    RUN_DIR.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, "-m", "uvicorn", args.app,
           "--host", args.host, "--port", str(args.port),
           "--log-level", args.log_level]

    flags = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    if os.name == "nt":
        flags |= CREATE_NO_WINDOW

    # 追加写：保留上一次启动的现场，便于失败后排查
    with open(LOG, "ab", buffering=0) as logf:
        logf.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} "
                   f"serve.py start {' '.join(cmd[1:])} =====\n".encode("utf-8"))
        proc = subprocess.Popen(
            cmd,
            cwd=str(BACKEND),
            stdin=subprocess.DEVNULL,
            stdout=logf,
            stderr=subprocess.STDOUT,
            close_fds=True,
            creationflags=flags if os.name == "nt" else 0,
        )

    time.sleep(args.settle)
    if proc.poll() is not None:
        print(f"uvicorn exited during startup with code {proc.returncode}", file=sys.stderr)
        print(f"--- {LOG} (tail) ---", file=sys.stderr)
        print(tail(LOG), file=sys.stderr)
        return 2

    print(proc.pid)
    return 0


if __name__ == "__main__":
    sys.exit(main())
