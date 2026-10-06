# -*- coding: utf-8 -*-
"""进程内 TTL 缓存。

用途（审查报告 5.3.3）：/industries 每请求读三级×10 天行业资金流
（≈5,110 行）+ 全量成分表（15,769 行）只为返回 20 行，且无任何缓存。
这里给「读多写少、数据每天才更新一次」的聚合类查询加 60s TTL。
TTL 由 API_CACHE_TTL 控制，<=0 即完全关闭（行为退回改造前）。
"""
import copy
import threading
import time

from app.deps import cfg

_MAX_ENTRIES = 256


class TTLCache:
    def __init__(self, ttl):
        self.ttl = ttl
        self._d = {}
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get(self, key):
        if self.ttl <= 0:
            return None
        now = time.time()
        with self._lock:
            item = self._d.get(key)
            if not item:
                self.misses += 1
                return None
            expire, val = item
            if expire <= now:
                self._d.pop(key, None)
                self.misses += 1
                return None
            self.hits += 1
            return copy.deepcopy(val)

    def set(self, key, val):
        if self.ttl <= 0:
            return
        with self._lock:
            if len(self._d) >= _MAX_ENTRIES:
                # 简单的容量护栏：清掉已过期项，仍满则整体清空（避免无界增长）
                now = time.time()
                for k in [k for k, (exp, _) in self._d.items() if exp <= now]:
                    self._d.pop(k, None)
                if len(self._d) >= _MAX_ENTRIES:
                    self._d.clear()
            self._d[key] = (time.time() + self.ttl, val)

    def clear(self):
        with self._lock:
            self._d.clear()


cache = TTLCache(cfg.cache_ttl)


def cached(name, key, fn):
    """按 (name, key...) 取缓存；未命中则计算并写入。返回深拷贝。"""
    k = (name,) + tuple(key)
    hit = cache.get(k)
    if hit is not None:
        return hit
    val = fn()
    cache.set(k, val)
    return val
