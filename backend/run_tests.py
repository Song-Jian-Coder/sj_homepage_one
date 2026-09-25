# -*- coding: utf-8 -*-
"""运行测试并把结果写入文件（绕过 DSH 对原生 stdout 的吞没）"""
import os
import sys
import traceback

BACKEND = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BACKEND)

out = []
try:
    import pytest
    out.append("pytest=" + pytest.__version__)
except ImportError:
    out.append("pytest=MISSING")

# 直接运行 test_api 里的 test_* 函数（与 pytest 断言一致）
try:
    import importlib.util
    spec = importlib.util.spec_from_file_location("test_api", os.path.join(BACKEND, "tests", "test_api.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    fns = sorted(f for f in dir(m) if f.startswith("test_"))
    passed = failed = 0
    for fn in fns:
        try:
            getattr(m, fn)()
            out.append("PASS  " + fn)
            passed += 1
        except Exception as e:  # noqa: BLE001
            out.append("FAIL  " + fn + " :: " + str(e))
            failed += 1
    out.append(f"RESULT passed={passed} failed={failed} total={len(fns)}")
except Exception:  # noqa: BLE001
    out.append("IMPORT_ERROR:\n" + traceback.format_exc())

open(r"C:\Users\57424\AppData\Local\Temp\run_tests.txt", "w", encoding="utf-8").write("\n".join(out))
print("DONE")
