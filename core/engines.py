"""Process-wide async engine cache.

Before: every MCP tool call created a brand-new AsyncEngine
(`create_async_engine(pool_size=10, ...)`) and never disposed it --
leaking connection pools / background threads on every query.

Now: one cached engine per connection fingerprint (+ event loop),
with bounded size and dispose-on-evict.
"""
from __future__ import annotations

import asyncio
import hashlib
from typing import Any, Dict

from sqlalchemy.ext.asyncio import AsyncEngine


_engine_cache: Dict[str, AsyncEngine] = {}
_engine_lock = asyncio.Lock()
_ENGINE_CACHE_MAX = 64


def engine_fingerprint(db_type: str, conn_args: Dict[str, Any]) -> str:
    raw = "|".join([
        str(db_type or ""),
        str(conn_args.get("host") or ""),
        str(conn_args.get("port") or ""),
        str(conn_args.get("user") or ""),
        str(conn_args.get("password") or ""),
        str(conn_args.get("db_name") or ""),
    ])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def get_engine(driver: Any, db_type: str, conn_args: Dict[str, Any]) -> AsyncEngine:
    """Return a cached engine for these connection args, creating it on first use."""
    key = f"{engine_fingerprint(db_type, conn_args)}@{id(asyncio.get_running_loop())}"
    async with _engine_lock:
        engine = _engine_cache.get(key)
        if engine is None:
            if len(_engine_cache) >= _ENGINE_CACHE_MAX:
                old_key, old_engine = next(iter(_engine_cache.items()))
                del _engine_cache[old_key]
                await old_engine.dispose()
            engine = await driver.init_engine(**conn_args)
            _engine_cache[key] = engine
        return engine


async def drop_engines() -> None:
    """Dispose all cached engines (e.g. on shutdown)."""
    async with _engine_lock:
        for engine in _engine_cache.values():
            await engine.dispose()
        _engine_cache.clear()
