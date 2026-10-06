# -*- coding: utf-8 -*-
"""申万行业资金流看板 · 后端包。

`config` 与 `db` 是仓库既有的顶层模块（ETL 脚本也直接 import 它们），
故在此统一做一次 sys.path 引导，取代原先散落在 main.py / tests 里的
`sys.path.insert(...)`。彻底包化（pyproject + 相对导入）属于未完成的
打包改造，见 docs/06 报告「未做项」。
"""
import os as _os
import sys as _sys

_BACKEND_DIR = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _BACKEND_DIR not in _sys.path:
    _sys.path.insert(0, _BACKEND_DIR)
