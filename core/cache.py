from __future__ import annotations
import asyncio
import time
import json
from typing import Any, Dict, Tuple, Callable, Awaitable, Optional

class TTLCache:
    """Simple async-safe TTL cache (in-process)
    简单的进程内异步安全 TTL 缓存
    """
    def __init__(self, maxsize: int = 256):
        self._maxsize = maxsize
        self._store: Dict[str, Tuple[float, Any]] = {}
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> Optional[Any]:
        async with self._lock:
            v = self._store.get(key)
            if not v:
                return None
            exp, data = v
            if exp < time.time():
                self._store.pop(key, None)
                return None
            return data

    async def set(self, key: str, value: Any, ttl: int) -> None:
        async with self._lock:
            if len(self._store) >= self._maxsize:
                # naive eviction: drop oldest
                oldest = min(self._store.items(), key=lambda kv: kv[1][0])[0]
                self._store.pop(oldest, None)
            self._store[key] = (time.time() + ttl, value)

def mk_cache_key(func: str, payload: Dict[str, Any]) -> str:
    return f"{func}:{json.dumps(payload, sort_keys=True, ensure_ascii=False)}"
