import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

TOKEN_FILE = os.getenv("TS_TOKEN_FILE", r"C:\Users\57424\jsdata_tk.csv")


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


class Config:
    def __init__(self):
        self.endpoint = os.getenv("TS_ENDPOINT", "http://221.204.19.233:7172").rstrip("/")
        self.token = _read_token()
        self.db_host = os.getenv("DB_HOST", "127.0.0.1")
        self.db_port = _int(os.getenv("DB_PORT"), 3306)
        self.db_user = os.getenv("DB_USER", "root")
        self.db_password = os.getenv("DB_PASSWORD", "")
        self.db_name = os.getenv("DB_NAME", "stock_data")
        self.moneyflow_days = _int(os.getenv("MONEYFLOW_DAYS"), 40)
        self.log_file = os.getenv("LOG_FILE", str(BASE_DIR / "etl_run.log"))
