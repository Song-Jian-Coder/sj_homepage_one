# -*- coding: utf-8 -*-
"""契约快照工具：把 13 个端点的响应落成 JSON，用于改造前后逐字段对比。

用法（在 backend/ 目录下，或任意目录用 --backend 指定）：
    python tools/contract-snapshot.py dump out.json
    python tools/contract-snapshot.py diff before.json after.json [--alias]

为什么存在（审查报告 5.7.1/5.8）：本项目唯一的人可读契约是 docs/03，
它已被实现甩开过 7 处；而 /openapi.json 现在虽有响应模型，却不含**取值**。
改动 API 前后各 dump 一次再 diff，可以把「有意改动」和「无意回归」分开。

--alias：把已知的旧 snake_case 键（sw_code/parent_code/l1_name）归一成 camelCase
再比对，便于把「键名清理」这类有意改动排除掉，只看剩下的真实差异。
"""
import argparse
import json
import os
import sys

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND)

# 只读用例：不含任何写操作；管理接口只走「无密钥 / 非法 task」路径（不会触发采集）
CASES = [
    ("GET", "/", None),
    ("GET", "/index.html", None),
    ("GET", "/industry.html", None),
    ("GET", "/nope-not-exist.html", None),
    ("GET", "/api/v1/health", None),
    ("GET", "/api/v1/meta", None),
    ("GET", "/api/v1/industries", {"level": 1, "page_size": 3}),
    ("GET", "/api/v1/industries", {"level": 2, "page_size": 3}),
    ("GET", "/api/v1/industries", {"level": 2, "page_size": 2, "q": "半导体"}),
    ("GET", "/api/v1/industries", {"level": 2, "page_size": 3, "l1": "电子"}),
    ("GET", "/api/v1/industries", {"level": 3, "page_size": 3}),
    ("GET", "/api/v1/industries", {"level": 2, "page": 2, "page_size": 3}),
    ("GET", "/api/v1/industries/tree", None),
    ("GET", "/api/v1/industries/801081", None),
    ("GET", "/api/v1/industries/801156", None),
    ("GET", "/api/v1/industries/999999", None),
    ("GET", "/api/v1/industries/801081/members", None),
    ("GET", "/api/v1/industries/801081/members", {"page_size": 5}),
    ("GET", "/api/v1/industries/801156/members", {"direct": 1}),
    ("GET", "/api/v1/industries/801081/members", {"as_of": "20260924"}),
    ("GET", "/api/v1/industries/801081/members", {"as_of": "2026-09-24"}),
    ("GET", "/api/v1/industries/801156/members", {"as_of": "2026-09-24", "direct": 1}),
    ("GET", "/api/v1/industries/999999/members", None),
    ("GET", "/api/v1/industries/801081/flow", None),
    ("GET", "/api/v1/industries/801081/flow", {"start": "2026-09-01", "end": "2026-09-30"}),
    ("GET", "/api/v1/industries/801081/flow", {"start": "abc"}),
    ("GET", "/api/v1/industries/801081/flow", {"start": "2026-09-30", "end": "2026-09-01"}),
    ("GET", "/api/v1/industries/999999/flow", None),
    ("GET", "/api/v1/industries/801081/stocks-flow", {"limit": 3}),
    ("GET", "/api/v1/industries/999999/stocks-flow", None),
    ("GET", "/api/v1/stocks/600519.SH/flow", None),
    ("GET", "/api/v1/stocks/600519.SH/flow", {"start": "2026-09-01"}),
    ("GET", "/api/v1/stocks/999999.SH/flow", None),
    ("GET", "/api/v1/stocks/search", None),
    ("GET", "/api/v1/stocks/search", {"q": ""}),
    ("GET", "/api/v1/stocks/search", {"q": "茅台"}),
    ("GET", "/api/v1/stocks/search", {"q": "000504"}),
    ("GET", "/api/v1/stocks/search", {"q": "银", "page_size": 3}),
    ("GET", "/api/v1/stocks/search", {"q": "不存在的名字xyz"}),
    ("GET", "/api/v1/strength", None),
    ("GET", "/api/v1/admin/log", None),
    ("POST", "/api/v1/admin/fetch", None),
    ("POST", "/api/v1/admin/fetch", {"task": "nope"}),
]

ALIASES = {"sw_code": "swCode", "parent_code": "parentCode", "l1_name": "l1Name"}
MAX_DIFFS = 4000


def _admin_key():
    """从 backend/.env 读取真实 ADMIN_KEY（不打印），用于管理接口的只读分支。"""
    p = os.path.join(BACKEND, ".env")
    if not os.path.exists(p):
        return ""
    for line in open(p, encoding="utf-8").read().splitlines():
        if line.strip().startswith("ADMIN_KEY="):
            return line.split("=", 1)[1].strip()
    return ""


def dump(out_path):
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app, raise_server_exceptions=False)
    admin_key = _admin_key()
    out = {}
    cases = list(CASES)
    # 带正确密钥的只读/校验分支（task 非法 → 40001，不会真的触发采集）
    cases.append(("POST", "/api/v1/admin/fetch", {"task": "nope", "__key__": "right"}))
    cases.append(("GET", "/api/v1/admin/log", {"lines": 3, "__key__": "right"}))

    for method, url, params in cases:
        p = dict(params) if params else None
        hdr = {"X-Admin-Key": "wrong"}
        if p and "__key__" in p:
            p.pop("__key__")
            hdr = {"X-Admin-Key": admin_key}
            if not p:
                p = None
        label = method + " " + url + (
            "?" + json.dumps(params, sort_keys=True, ensure_ascii=False) if params else "")
        if params and "__key__" in params:
            label = label.replace('"__key__": "right"', "key=right")
        try:
            if method == "POST":
                r = client.post(url, json=p or {}, headers=hdr)
            else:
                r = client.get(url, params=p, headers=hdr)
        except Exception as e:  # noqa: BLE001
            out[label] = {"__error__": repr(e)}
            continue
        if "json" in r.headers.get("content-type", ""):
            try:
                body = r.json()
            except Exception:  # noqa: BLE001
                body = {"__raw__": r.text[:200]}
        else:
            body = {"__content_type__": r.headers.get("content-type", "").split(";")[0],
                    "__len__": len(r.text), "__head__": r.text[:200].replace("\n", " ")}
        if isinstance(body, dict) and "traceId" in body:
            body["traceId"] = "<trace>"
        out[label] = {"__status__": r.status_code, "body": body}

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1, sort_keys=True)
    print("cases:", len(out), "->", out_path)


def _normalize(o):
    if isinstance(o, dict):
        d = {k: _normalize(v) for k, v in o.items()}
        for old, new in ALIASES.items():
            if old in d:
                d.setdefault(new, d.pop(old))
        return d
    if isinstance(o, list):
        return [_normalize(x) for x in o]
    return o


def _walk(a, b, path, diffs):
    if type(a) is not type(b) and not (isinstance(a, (int, float)) and isinstance(b, (int, float))):
        diffs.append((path, a, b))
        return
    if isinstance(a, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a:
                diffs.append((path + "." + k, "<absent>", b[k]))
            elif k not in b:
                diffs.append((path + "." + k, a[k], "<absent>"))
            else:
                _walk(a[k], b[k], path + "." + k, diffs)
    elif isinstance(a, list):
        if len(a) != len(b):
            diffs.append((path + ".__len__", len(a), len(b)))
        for i in range(min(len(a), len(b))):
            _walk(a[i], b[i], "%s[%d]" % (path, i), diffs)
    elif a != b:
        diffs.append((path, a, b))


def _short(v):
    s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
    return s if len(s) <= 120 else s[:117] + "..."


def diff(before_path, after_path, alias):
    before = json.load(open(before_path, encoding="utf-8"))
    after = json.load(open(after_path, encoding="utf-8"))
    if alias:
        before, after = _normalize(before), _normalize(after)
    changed = 0
    for k in sorted(set(before) | set(after)):
        if k not in before:
            print("[ONLY-AFTER ] " + k)
            changed += 1
            continue
        if k not in after:
            print("[ONLY-BEFORE] " + k)
            changed += 1
            continue
        diffs = []
        _walk(before[k], after[k], "", diffs)
        if not diffs:
            continue
        changed += 1
        print("### " + k)
        for p, x, y in diffs[:MAX_DIFFS]:
            print("    %-46s %s  ->  %s" % (p, _short(x), _short(y)))
        if len(diffs) > MAX_DIFFS:
            print("    ... 还有 %d 条" % (len(diffs) - MAX_DIFFS))
    print("")
    print("cases: before=%d after=%d，有差异的用例=%d" % (len(before), len(after), changed))


def main():
    ap = argparse.ArgumentParser(description="API 契约快照 / 差异比对")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d1 = sub.add_parser("dump", help="抓取当前实现的响应快照")
    d1.add_argument("out")
    d2 = sub.add_parser("diff", help="比对两份快照")
    d2.add_argument("before")
    d2.add_argument("after")
    d2.add_argument("--alias", action="store_true",
                    help="把旧 snake_case 键归一为 camelCase 后再比")
    a = ap.parse_args()
    if a.cmd == "dump":
        dump(a.out)
    else:
        diff(a.before, a.after, a.alias)


if __name__ == "__main__":
    main()
