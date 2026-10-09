"""Usage metering (R2).

Aggregates per (user, endpoint, tool): call counts, rows returned,
errors, total latency. Persisted to metering.json (atomic write,
throttled flush) so restarts don't lose history. Served via
GET /admin/api/metering for showback / chargeback.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict


def _metering_path() -> Path:
    return Path(os.environ.get("DBMCP_METERING_PATH", "metering.json"))


def _atomic_write_json(path: Path, data: Dict[str, Any]) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _blank_stats() -> Dict[str, Any]:
    return {"calls": 0, "rows": 0, "errors": 0, "total_ms": 0}


class Metering:
    def __init__(self, flush_interval: int = 60) -> None:
        self._lock = threading.Lock()
        self._flush_interval = flush_interval
        self._last_flush = 0.0
        self._data: Dict[str, Any] = {"users": {}, "endpoints": {}, "tools": {}}
        self._load()

    # ------------------------------------------------------------------
    def _load(self) -> None:
        p = _metering_path()
        if p.exists():
            try:
                raw = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    for k in ("users", "endpoints", "tools"):
                        if isinstance(raw.get(k), dict):
                            self._data[k] = raw[k]
            except (ValueError, OSError):
                pass

    def flush(self) -> None:
        with self._lock:
            payload = {
                "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime()),
                **self._data,
            }
            try:
                _atomic_write_json(_metering_path(), payload)
                self._last_flush = time.time()
            except OSError:
                pass

    def _maybe_flush(self) -> None:
        if time.time() - self._last_flush >= self._flush_interval:
            self.flush()

    # ------------------------------------------------------------------
    def record(
        self,
        *,
        user_id: str,
        username: str,
        endpoint_id: str,
        endpoint_alias: str,
        tool: str,
        duration_ms: int,
        rows: int = 0,
        cached: bool = False,
        error: bool = False,
    ) -> None:
        with self._lock:
            u = self._data["users"].setdefault(user_id, {"username": username, **_blank_stats()})
            u["username"] = username or u.get("username", "")
            e = self._data["endpoints"].setdefault(
                endpoint_id, {"alias": endpoint_alias, **_blank_stats()}
            )
            e["alias"] = endpoint_alias or e.get("alias", "")
            t = self._data["tools"].setdefault(tool, _blank_stats())
            for s in (u, e, t):
                s["calls"] += 1
                s["rows"] += rows
                s["total_ms"] += duration_ms
                if error:
                    s["errors"] += 1
            if cached:
                for s in (u, e, t):
                    s["cache_hits"] = s.get("cache_hits", 0) + 1
        self._maybe_flush()

    def summary(self) -> Dict[str, Any]:
        with self._lock:
            return json.loads(json.dumps(self._data))  # deep copy


# Process-wide singleton used by the MCP factory.
metering = Metering()
