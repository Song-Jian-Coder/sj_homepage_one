# -*- coding: utf-8 -*-
"""跑测试并把结果写进文件（本机 pwsh 会吞掉原生 stdout）。

用法（backend 目录下）：
    python run_tests.py                 # 只跑离线层（不连库）
    python run_tests.py out.txt         # 指定输出文件
    RUN_INTEGRATION=1 python run_tests.py   # 追加真实库回归

输出路径默认写到本脚本同目录的 test_run.log（*.log 已在 .gitignore 中），
不再写死 C:\\Users\\... 这类本机绝对路径（审查报告 5.4）。
"""
import os
import subprocess
import sys

BACKEND = os.path.dirname(os.path.abspath(__file__))
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(BACKEND, "test_run.log")

# ⚠️ 必须传**相对**路径 "tests"：本项目工作区路径含 `[` `]`（`C:\[AI工作区]\...`），
# 而 pytest 会把路径里的 `[]` 当成参数化语法，直接报
# "ERROR: path cannot contain [] parametrization"。cwd 已切到 BACKEND，故用相对路径。
TESTS_REL = "tests"

try:
    import pytest  # noqa: F401
    HAVE_PYTEST = True
except ImportError:
    HAVE_PYTEST = False

out = []
out.append("RUN_INTEGRATION=%s" % os.getenv("RUN_INTEGRATION", "(unset)"))
if HAVE_PYTEST:
    cmd = [sys.executable, "-m", "pytest", TESTS_REL, "-v", "--no-header",
           "-p", "no:cacheprovider"]
    out.append("$ (cwd=%s) %s" % (BACKEND, " ".join(cmd)))
    p = subprocess.run(cmd, cwd=BACKEND, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    out.append(p.stdout or "")
    if p.stderr:
        out.append("--- STDERR ---\n" + p.stderr)
    out.append("EXIT=%d" % p.returncode)
else:
    # 无 pytest 时的降级路径：直接执行各测试模块里的 test_* 函数
    out.append("pytest 未安装，改用内置降级执行器（只跑不带 fixture 的用例）")
    import importlib.util
    import traceback
    TESTS = os.path.join(BACKEND, TESTS_REL)
    passed = failed = skipped = 0
    for fname in sorted(os.listdir(TESTS)):
        if not fname.startswith("test_") or not fname.endswith(".py"):
            continue
        spec = importlib.util.spec_from_file_location(fname[:-3], os.path.join(TESTS, fname))
        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
        except Exception:
            out.append("IMPORT_ERROR %s:\n%s" % (fname, traceback.format_exc()))
            failed += 1
            continue
        for fn in sorted(f for f in dir(mod) if f.startswith("test_")):
            try:
                getattr(mod, fn)()
                out.append("PASS  %s::%s" % (fname, fn))
                passed += 1
            except Exception as e:  # noqa: BLE001
                err = str(e)
                if "fixture" in err or "positional argument" in err:
                    out.append("SKIP  %s::%s (需要 pytest fixture)" % (fname, fn))
                    skipped += 1
                else:
                    out.append("FAIL  %s::%s :: %s" % (fname, fn, err))
                    failed += 1
    out.append("RESULT passed=%d failed=%d skipped=%d" % (passed, failed, skipped))

with open(OUT, "w", encoding="utf-8") as f:
    f.write("\n".join(out))
print("DONE ->", OUT)
