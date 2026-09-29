"""Tushare 兼容代理适配器。

契约（已实测）：
  POST {endpoint}   body = {"api_name","token","params","max_rows"}
  响应 = {"code":0,"msg":"","data":{"fields":[...],"items":[[...]],"has_more":false}}

限流契约（已实测）：
  超过接口频率限制返回 code=40203（如 index_member：每分钟最多 300 次）。
  本模块内置「请求间隔节流」+「40203 专项退避重试」，避免批量任务
  （L2 + L3 共 480 次 index_member）撞限流后被静默丢弃。
"""
import time
import requests

RATE_LIMIT_CODE = 40203
DEFAULT_QPS_DELAY = 0.25        # 秒/次 → 上限 240 次/分钟（低于 300 的限制）
DEFAULT_RATE_LIMIT_WAIT = 15    # 命中 40203 后的基准退避秒数（15s/30s/45s…）


class DataSourceError(RuntimeError):
    """数据源返回 code != 0，不可重试或重试已耗尽。"""


class RateLimitError(DataSourceError):
    """数据源限流（code=40203），可退避重试。"""


class TushareClient:
    def __init__(self, endpoint, token, timeout=30, max_retries=5,
                 qps_delay=DEFAULT_QPS_DELAY, rate_limit_wait=DEFAULT_RATE_LIMIT_WAIT):
        self.endpoint = endpoint.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.max_retries = max_retries
        self.qps_delay = qps_delay
        self.rate_limit_wait = rate_limit_wait
        self.session = requests.Session()
        self._last_call = 0.0

    def _throttle(self):
        """保证任意两次请求间隔 >= qps_delay，从源头避免撞频率限制。"""
        if self.qps_delay > 0:
            gap = time.time() - self._last_call
            if gap < self.qps_delay:
                time.sleep(self.qps_delay - gap)

    def call(self, api_name, **params):
        """返回 (rows: list[dict], has_more: bool)。

        - 每次请求前节流（间隔 >= qps_delay）
        - code=40203 → 退避 rate_limit_wait*(attempt+1) 秒后重试
        - 其它异常 → 指数退避 2**attempt 秒后重试
        """
        payload = {"api_name": api_name, "token": self.token,
                   "params": params, "max_rows": 6000}
        last_err = None
        for attempt in range(self.max_retries):
            self._throttle()
            try:
                r = self.session.post(self.endpoint, json=payload, timeout=self.timeout)
                self._last_call = time.time()
                r.raise_for_status()
                j = r.json()
                code = j.get("code")
                if code == RATE_LIMIT_CODE:
                    raise RateLimitError(
                        f"{api_name}: code={code} msg={j.get('msg')}"
                        f"（第 {attempt + 1}/{self.max_retries} 次）")
                if code != 0:
                    raise DataSourceError(f"{api_name}: code={code} msg={j.get('msg')}")
                d = j.get("data") or {}
                fields = d.get("fields") or []
                items = d.get("items") or []
                rows = [dict(zip(fields, it)) for it in items]
                return rows, bool(d.get("has_more"))
            except RateLimitError as e:
                last_err = e
                if attempt < self.max_retries - 1:
                    time.sleep(self.rate_limit_wait * (attempt + 1))
            except Exception as e:  # noqa: BLE001
                last_err = e
                if attempt < self.max_retries - 1:
                    time.sleep(2 ** attempt)
        raise last_err
