"""Tushare 兼容代理适配器。

契约（已实测）：
  POST {endpoint}   body = {"api_name","token","params","max_rows"}
  响应 = {"code":0,"msg":"","data":{"fields":[...],"items":[[...]],"has_more":false}}
"""
import time
import requests


class DataSourceError(RuntimeError):
    pass


class TushareClient:
    def __init__(self, endpoint, token, timeout=30, max_retries=3, qps_delay=0.15):
        self.endpoint = endpoint.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.max_retries = max_retries
        self.qps_delay = qps_delay
        self.session = requests.Session()

    def call(self, api_name, **params):
        """返回 (rows: list[dict], has_more: bool)"""
        payload = {"api_name": api_name, "token": self.token,
                   "params": params, "max_rows": 6000}
        last_err = None
        for attempt in range(self.max_retries):
            try:
                r = self.session.post(self.endpoint, json=payload, timeout=self.timeout)
                r.raise_for_status()
                j = r.json()
                if j.get("code") != 0:
                    raise DataSourceError(f"{api_name}: code={j.get('code')} msg={j.get('msg')}")
                d = j.get("data") or {}
                fields = d.get("fields") or []
                items = d.get("items") or []
                rows = [dict(zip(fields, it)) for it in items]
                return rows, bool(d.get("has_more"))
            except Exception as e:  # noqa: BLE001
                last_err = e
                if attempt < self.max_retries - 1:
                    time.sleep(2 ** attempt)
        raise last_err
