# -*- coding: utf-8 -*-
"""渲染「项目总览流程图」PNG（只用 Pillow，无外部依赖）。

用法（cwd = backend）：
    python tools/render-flow.py                       # 输出 docs/images/项目总览流程图.png
    python tools/render-flow.py --out 某路径.png

设计约定：
  - 坐标系用「设计像素」，统一乘 S=2 超采样，再按 1x 尺寸落盘 → 文字锐利、体积小；
  - 只依赖 Windows 自带字体（msyh.ttc / msyhbd.ttc），换机器改 FONT_* 即可；
  - 图与 docs/08-项目流程图.md 里的 mermaid 源保持一致，改结构时两边同步。
"""
import argparse
import os

from PIL import Image, ImageDraw, ImageFont

S = 2                                   # 超采样倍数
W, H = 1090, 920                        # 设计尺寸

FONT_REG = r"C:\Windows\Fonts\msyh.ttc"
FONT_BOLD = r"C:\Windows\Fonts\msyhbd.ttc"

BG = "#FFFFFF"
INK = "#111827"
SUB = "#475569"
MUTED = "#6B7280"
ARROW = "#64748B"
DASH = "#B91C1C"

THEME = {                               # (填充, 描边/强调色)
    "src": ("#FFF6E8", "#D98F1F"),
    "etl": ("#EAF2FF", "#2F6FE0"),
    "db":  ("#F3EFFC", "#6D4FD0"),
    "api": ("#E7F6F5", "#0E8F8F"),
    "web": ("#FDEDF2", "#CE4A78"),
}

BAND_X0, BAND_X1 = 150, 900             # 泳道矩形
LANE_CX = 82                            # 左侧泳道标题中心


def p(v):
    return int(round(v * S))


def font(size, bold=False):
    return ImageFont.truetype(FONT_BOLD if bold else FONT_REG, int(size * S))


def ctext(d, cx, cy, lines, f, fill, gap=1.35, anchor_mid=True):
    """以 (cx, cy) 为中心竖排多行文字（行距用设计像素，f.size 是放大后的）。"""
    if isinstance(lines, str):
        lines = [lines]
    lh = f.size * gap / S
    total = lh * len(lines)
    y = cy - total / 2 + lh / 2
    for ln in lines:
        d.text((p(cx), p(y)), ln, font=f, fill=fill, anchor="mm")
        y += lh


def ltext(d, x, cy, lines, f, fill, gap=1.35):
    """左对齐、垂直居中的多行文字。"""
    if isinstance(lines, str):
        lines = [lines]
    lh = f.size * gap / S
    total = lh * len(lines)
    y = cy - total / 2 + lh / 2
    for ln in lines:
        d.text((p(x), p(y)), ln, font=f, fill=fill, anchor="lm")
        y += lh


def band(d, y0, y1, key, title_lines):
    fill, edge = THEME[key]
    d.rounded_rectangle([p(BAND_X0), p(y0), p(BAND_X1), p(y1)],
                        radius=p(12), fill=fill, outline=edge, width=p(1.6))
    ctext(d, LANE_CX, (y0 + y1) / 2, title_lines, font(18, True), edge, gap=1.25)


def chip(d, x0, y0, x1, y1, edge, title, body=None, tsize=16, bsize=13, fill="#FFFFFF"):
    d.rounded_rectangle([p(x0), p(y0), p(x1), p(y1)],
                        radius=p(8), fill=fill, outline=edge, width=p(1.4))
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    if body:
        tf = font(tsize, True)
        bf = font(bsize)
        th = tf.size * 1.3 / S
        bh = bf.size * 1.35 * len(body) / S
        y = cy - (th + bh) / 2
        d.text((p(cx), p(y + th / 2)), title, font=tf, fill=INK, anchor="mm")
        ctext(d, cx, y + th + bh / 2, body, bf, SUB, gap=1.35)
    else:
        ctext(d, cx, cy, title if isinstance(title, list) else [title], font(tsize, True), INK, gap=1.3)


def arrow_v(d, x, y0, y1, color=ARROW, width=2.4, head=9):
    """竖直箭头（y0 → y1，可上可下）。"""
    d.line([p(x), p(y0), p(x), p(y1)], fill=color, width=p(width))
    s = 1 if y1 > y0 else -1
    d.polygon([(p(x), p(y1)), (p(x - head / 2), p(y1 - s * head)),
               (p(x + head / 2), p(y1 - s * head))], fill=color)


def arrow_double_v(d, x, y0, y1, color=ARROW, width=2.4, head=9):
    """两端带箭头的竖直线（只画两个箭头，不画中间的多余箭尖）。"""
    d.line([p(x), p(y0 + head), p(x), p(y1 - head)], fill=color, width=p(width))
    d.polygon([(p(x), p(y0)), (p(x - head / 2), p(y0 + head)), (p(x + head / 2), p(y0 + head))], fill=color)
    d.polygon([(p(x), p(y1)), (p(x - head / 2), p(y1 - head)), (p(x + head / 2), p(y1 - head))], fill=color)


def label(d, cx, cy, text, size=13.5, fill=MUTED, pad=7):
    f = font(size)
    w = f.getlength(text) / S
    h = f.size / S
    d.rounded_rectangle([p(cx - w / 2 - pad), p(cy - h / 2 - pad * 0.6),
                         p(cx + w / 2 + pad), p(cy + h / 2 + pad * 0.6)],
                        radius=p(6), fill="#FFFFFF", outline="#E2E8F0", width=p(1))
    d.text((p(cx), p(cy)), text, font=f, fill=fill, anchor="mm")


def dashed_path(d, pts, color=DASH, width=1.8, dash=9, gap=6):
    """沿折线画虚线。"""
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        dist = ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5
        if not dist:
            continue
        ux, uy = (x1 - x0) / dist, (y1 - y0) / dist
        t = 0.0
        while t < dist:
            e = min(t + dash, dist)
            d.line([p(x0 + ux * t), p(y0 + uy * t), p(x0 + ux * e), p(y0 + uy * e)],
                   fill=color, width=p(width))
            t = e + gap


def build():
    img = Image.new("RGB", (p(W), p(H)), BG)
    d = ImageDraw.Draw(img)

    # ── 标题 ──
    d.text((p(W / 2), p(34)), "申万行业资金流看板 · 项目总览流程",
           font=font(28, True), fill=INK, anchor="mm")
    d.text((p(W / 2), p(66)),
           "数据源 → ETL 落库 → MySQL → FastAPI → 静态页；管理台可反向触发采集（红色虚线）",
           font=font(14), fill=MUTED, anchor="mm")

    # ── 泳道 ──
    y_b1 = (100, 190)
    y_b2 = (235, 375)
    y_b3 = (420, 560)
    y_b4 = (605, 745)
    y_b5 = (790, 870)

    band(d, *y_b1, "src", ["①", "数据源"])
    band(d, *y_b2, "etl", ["②", "ETL", "采集"])
    band(d, *y_b3, "db", ["③", "MySQL 8.0", "stock_data"])
    band(d, *y_b4, "api", ["④", "API", "服务"])
    band(d, *y_b5, "web", ["⑤", "前端"])

    # ① 数据源
    chip(d, 175, 110, 875, 180, THEME["src"][1],
         "Tushare 兼容代理   http://221.204.19.233:7172",
         ["moneyflow · index_member · trade_cal · stock_basic",
          "单页硬顶 6000 行 → 必须按 has_more / offset 翻页，否则静默截断"],
         tsize=18, bsize=13)

    # ② ETL：6 个子步骤
    steps = [("① schema", "建表"), ("② meta", "日历+分类"), ("③ stock", "股票基础"),
             ("④ member", "成分股"), ("⑤ moneyflow", "逐日资金流"), ("⑥ aggregate", "L1/L2/L3 聚合")]
    cw, gap = 112, 9.6
    x = 175
    for title, body in steps:
        chip(d, x, 253, x + cw, 313, THEME["etl"][1], title, [body], tsize=14.5, bsize=12)
        x += cw + gap
    d.text((p(525), p(330)), "幂等 upsert（ON DUPLICATE KEY UPDATE） · 空洞自愈 · 每 10 个交易日提交一次",
           font=font(13), fill=SUB, anchor="mm")
    d.text((p(525), p(355)), "审计写 t_fetch_log · 日志 etl_run.log · 退出码 0=成功 / 1=致命 / 2=无 token / 3=有失败项",
           font=font(13), fill=SUB, anchor="mm")

    # ③ 存储：7 张表
    tabs = [("t_moneyflow", "个股资金流 131 万行"), ("t_industry_flow", "行业聚合 10.3 万行"),
            ("t_industry_member", "成分股 1.58 万行"), ("t_stock_basic", "股票基础 5,571"),
            ("t_industry", "申万三级 511"), ("t_trade_cal", "交易日历 277"),
            ("t_fetch_log", "采集审计")]
    tw, tgap = 170, 11
    for i, (name, note) in enumerate(tabs):
        row, col = divmod(i, 4)
        cx0 = 175 + col * (tw + tgap)
        cy0 = 440 + row * 62
        chip(d, cx0, cy0, cx0 + tw, cy0 + 50, THEME["db"][1], name, [note], tsize=14, bsize=11.5)

    # ④ API
    api = [("中间件", ["traceId 注入", "ACCESS 日志 + X-Trace-Id"]),
           ("routers", ["/health  /industries", "/stocks  /admin"]),
           ("services", ["60s TTL 缓存 · 连接池(8)", "FetchGate 防重 · 错误信封"])]
    aw, agap = 226, 11
    for i, (title, body) in enumerate(api):
        ax0 = 175 + i * (aw + agap)
        chip(d, ax0, 623, ax0 + aw, 700, THEME["api"][1], title, body, tsize=15, bsize=12)
    d.text((p(525), p(727)), "FastAPI + uvicorn 127.0.0.1:8000 · 一律 HTTP 200 + {code,msg,data} 信封",
           font=font(12.5), fill=SUB, anchor="mm")

    # ⑤ 前端
    pages = [("industry.html", "行业看板"), ("admin.html", "管理台"), ("fundflow.html", "原型页")]
    pw, pgap = 226, 11
    for i, (name, note) in enumerate(pages):
        px0 = 175 + i * (pw + pgap)
        chip(d, px0, 805, px0 + pw, 855, THEME["web"][1], name, [note], tsize=15, bsize=12)

    # ── 主链箭头 ──
    arrow_v(d, 525, y_b1[1], y_b2[0] - 2)
    label(d, 525, (y_b1[1] + y_b2[0]) / 2, "① 分页拉取（POST api_name / token / params / max_rows）")

    arrow_v(d, 525, y_b2[1], y_b3[0] - 2)
    label(d, 525, (y_b2[1] + y_b3[0]) / 2, "② 幂等写入（upsert，可重复跑）")

    arrow_double_v(d, 525, y_b3[1] + 2, y_b4[0] - 2)
    label(d, 525, (y_b3[1] + y_b4[0]) / 2, "③ 参数化只读 SQL → 结果集（连接池复用）")

    arrow_double_v(d, 525, y_b4[1] + 2, y_b5[0] - 2)
    label(d, 525, (y_b4[1] + y_b5[0]) / 2, "④ HTTP 请求 → {code,msg,data} 信封 + X-Trace-Id")

    # ── 管理台触发采集（红色虚线，走右侧通道）──
    dashed_path(d, [(900, 830), (925, 830), (925, 305), (902, 305)])
    d.polygon([(p(902), p(305)), (p(913), p(300)), (p(913), p(310))], fill=DASH)
    rl = ["管理台 /admin/fetch", "→ spawn etl.py 子进程", "反向触发 ②", "",
          "FetchGate + t_fetch_log", "360 分钟内重复 → 42901"]
    rf = font(11.5)
    lh = rf.size * 1.5 / S
    wmax = max(rf.getlength(t) for t in rl) / S
    cxr = 941 + wmax / 2                       # 让文字块整体落在虚线右侧
    d.rounded_rectangle([p(941), p(560 - lh * len(rl) / 2 - 9),
                         p(cxr + wmax / 2 + 9), p(560 + lh * len(rl) / 2 + 9)],
                        radius=p(8), fill="#FFFFFF", outline="#F2C9C9", width=p(1))
    ctext(d, cxr, 560, rl, rf, DASH, gap=1.5)

    # ── 页脚 ──
    d.text((p(W / 2), p(897)),
           "日志与审计：api_run.log（API 访问）· etl_run.log（ETL 进度）· t_fetch_log（采集留痕）"
           "　　静态页由 FastAPI StaticFiles 托管，/ → 307 /industry.html",
           font=font(12), fill=MUTED, anchor="mm")

    return img


def main():
    ap = argparse.ArgumentParser()
    here = os.path.dirname(os.path.abspath(__file__))
    default = os.path.join(here, "..", "..", "docs", "images", "项目总览流程图.png")
    ap.add_argument("--out", default=os.path.normpath(default))
    args = ap.parse_args()

    img = build().resize((W, H), Image.LANCZOS)
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    img.save(args.out, "PNG", optimize=True)
    print("saved: %s  (%dx%d, %.1f KB)" % (args.out, img.width, img.height,
                                           os.path.getsize(args.out) / 1024))


if __name__ == "__main__":
    main()
