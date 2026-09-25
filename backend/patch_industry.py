# -*- coding: utf-8 -*-
"""把 industry_patch.js 插入 industry.html / 中文副本 的 </script> 之前。"""
import os
import shutil

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATCH = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "industry_patch.js"), encoding="utf-8").read()

report = []
for name in ["industry.html", "申万行业分类浏览.html"]:
    path = os.path.join(ROOT, name)
    if not os.path.exists(path):
        report.append(f"{name}: 不存在，跳过")
        continue
    html = open(path, encoding="utf-8").read()
    if "真实资金流接入" in html:
        report.append(f"{name}: 已打过补丁，跳过")
        continue
    if "</script>" not in html:
        report.append(f"{name}: 未找到 </script>")
        continue
    shutil.copyfile(path, path + ".mock.bak")
    html = html.replace("</script>", PATCH + "\n</script>", 1)
    open(path, "w", encoding="utf-8").write(html)
    report.append(f"{name}: 已插入（原文件备份为 .mock.bak）")

open(r"C:\Users\57424\AppData\Local\Temp\patch_industry_report.txt", "w", encoding="utf-8").write("\n".join(report))
print("DONE")
