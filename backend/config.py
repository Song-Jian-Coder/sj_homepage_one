# -*- coding: utf-8 -*-
"""集中配置。所有可调项一律走环境变量（backend/.env），不在代码里硬编码凭据。"""
import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

TOKEN_FILE = os.getenv("TS_TOKEN_FILE", r"C:\Users\57424\jsdata_tk.csv")

# 与本项目语义绑定的默认值（集中在此，避免散落在 SQL/路由里）
DEFAULT_MONEYFLOW_DAYS = 40
DEFAULT_MONEYFLOW_MAX_ROWS = 6000      # = 数据源实测硬顶，见 tushare_client.PAGE_LIMIT
DEFAULT_MONEYFLOW_GAP_MAX_DAYS = 50
DEFAULT_API_CACHE_TTL = 60             # 秒；0 = 关闭缓存
DEFAULT_ADMIN_RATE_LIMIT = 30          # 次/分钟/客户端 IP
DEFAULT_FETCH_STALE_MINUTES = 360      # 超过该时长的 running 审计行视为崩溃残留
DEFAULT_DB_POOL_SIZE = 8
DEFAULT_TZ_OFFSET_HOURS = 8
DEFAULT_START_DATE = "19900101"        # 上市/纳入日的缺失兜底值


def _read_token():
    t = os.getenv("TS_TOKEN", "").strip()
    if t:
        return t
    p = Path(TOKEN_FILE)
    if p.exists():
        for line in reversed(p.read_text(encoding="utf-8").strip().splitlines()):
            line = line.strip()
            if line and line.lower() != "token":
                return line
    return ""


def _int(v, default):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _str(name, default):
    """字符串配置：空串视为「未配置」并回退默认值。

    必要性：`.env` 里写 `LOG_FILE=`（键存在但值为空）时 `os.getenv` 会返回 ""
    而不是默认值——直接拿它去 open() 会炸。模板里留空的项因此必须是安全的。
    """
    v = os.getenv(name)
    return v.strip() if v and v.strip() else default


def _list(v, default):
    items = [x.strip() for x in (v or "").split(",") if x.strip()]
    return items if items else list(default)


class Config:
    def __init__(self):
        # ── 数据源 ──
        self.endpoint = _str("TS_ENDPOINT", "http://221.204.19.233:7172").rstrip("/")
        self.token = _read_token()

        # ── MySQL ──
        self.db_host = _str("DB_HOST", "127.0.0.1")
        self.db_port = _int(os.getenv("DB_PORT"), 3306)
        self.db_user = _str("DB_USER", "root")
        self.db_password = os.getenv("DB_PASSWORD", "")
        self.db_name = _str("DB_NAME", "stock_data")
        # 连接池与超时：原实现每请求新建连接且无 timeout，DB 卡死会拖垮整个进程
        self.db_pool_size = max(1, _int(os.getenv("DB_POOL_SIZE"), DEFAULT_DB_POOL_SIZE))
        self.db_connect_timeout = _int(os.getenv("DB_CONNECT_TIMEOUT"), 5)
        self.db_read_timeout = _int(os.getenv("DB_READ_TIMEOUT"), 20)
        self.db_write_timeout = _int(os.getenv("DB_WRITE_TIMEOUT"), 20)
        # 会话时区：trade_date 的语义是 Asia/Shanghai，必须与进程时区一致，
        # 否则主机跑 UTC 时盘后运行会把当日交易日判成未来日而空转（见 ETL 时区说明）。
        self.session_time_zone = _str("DB_TIME_ZONE", "+08:00")
        self.tz_offset_hours = _int(os.getenv("TZ_OFFSET_HOURS"), DEFAULT_TZ_OFFSET_HOURS)

        # ── 采集 ──
        self.moneyflow_days = _int(os.getenv("MONEYFLOW_DAYS"), DEFAULT_MONEYFLOW_DAYS)
        # 翻页步长：必须等于服务端硬顶（实测固定 6000 行/次）。
        # 若设得比 6000 大，offset 步长会超过服务端实回行数，导致每页漏数据。
        # 实测单日约 5572 行，故当前 1 页即可取完；超 6000 只/日时自动翻页。
        self.moneyflow_max_rows = _int(os.getenv("MONEYFLOW_MAX_ROWS"), DEFAULT_MONEYFLOW_MAX_ROWS)
        # 空洞回填单轮上限（护栏，防止日历/库不一致时一次拉爆配额）
        self.moneyflow_gap_max_days = _int(
            os.getenv("MONEYFLOW_GAP_MAX_DAYS"), DEFAULT_MONEYFLOW_GAP_MAX_DAYS)
        self.log_file = _str("LOG_FILE", str(BASE_DIR / "etl_run.log"))
        # 审计行超时：API 进程崩溃会留下 status='running' 的孤儿行
        self.fetch_stale_minutes = _int(os.getenv("FETCH_STALE_MINUTES"), DEFAULT_FETCH_STALE_MINUTES)

        # ── API ──
        self.api_log_file = _str("API_LOG_FILE", str(BASE_DIR / "api_run.log"))
        self.cache_ttl = _int(os.getenv("API_CACHE_TTL"), DEFAULT_API_CACHE_TTL)
        # CORS：前端与 API 同源托管，默认只放开本机开发端口，不再 allow_origins=["*"]
        self.cors_origins = _list(os.getenv("CORS_ORIGINS"),
                                  ("http://127.0.0.1:8000", "http://localhost:8000"))
        # 管理接口 IP 白名单：留空 = 不限制（默认）；配置后仅这些 IP 可用
        self.admin_ip_allowlist = _list(os.getenv("ADMIN_IP_ALLOWLIST"), ())
        self.admin_rate_limit_per_min = _int(
            os.getenv("ADMIN_RATE_LIMIT_PER_MIN"), DEFAULT_ADMIN_RATE_LIMIT)

    # ---- 便于日志/诊断，绝不回显凭据 ----
    def masked(self):
        return {
            "endpoint": self.endpoint,
            "endpoint_scheme": self.endpoint.split("://", 1)[0],
            "token_len": len(self.token),
            "db": f"{self.db_host}:{self.db_port}/{self.db_name}",
            "db_user": self.db_user,
            "db_password_set": bool(self.db_password),
            "moneyflow_days": self.moneyflow_days,
            "moneyflow_max_rows": self.moneyflow_max_rows,
            "moneyflow_gap_max_days": self.moneyflow_gap_max_days,
            "db_pool_size": self.db_pool_size,
            "session_time_zone": self.session_time_zone,
            "tz_offset_hours": self.tz_offset_hours,
            "cache_ttl": self.cache_ttl,
        }

    def warnings(self):
        """启动期安全/配置告警（不阻断启动，但必须让人看见）。"""
        w = []
        if self.endpoint.startswith("http://"):
            w.append("TS_ENDPOINT 为明文 http://，token 在链路中可被截获；"
                     "建议换官方 HTTPS 或给代理套隧道/HTTPS 网关")
        if not self.token:
            w.append("未读取到数据源 token，ETL 不可用（API 只读接口不受影响）")
        if not self.db_password:
            w.append("DB_PASSWORD 为空，数据库使用空口令连接")
        return w
