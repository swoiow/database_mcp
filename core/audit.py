"""Query audit log (JSONL, append-only).

Every MCP tool call executed through the gateway factory is recorded:
who (user), where (endpoint), what (tool + SQL hash/preview + tables),
how long, how many rows, and errors. This is the basis for incident
forensics and compliance; metering.py aggregates the same events for
usage/billing views.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


def _audit_path() -> Path:
    return Path(os.environ.get("DBMCP_AUDIT_PATH", "audit.log"))


_lock = threading.Lock()


def _sql_hash(sql: Optional[str]) -> Optional[str]:
    if not sql:
        return None
    return hashlib.sha256(sql.encode("utf-8")).hexdigest()[:16]


def audit_event(
    *,
    tool: str,
    user_id: str,
    username: str,
    endpoint_id: str,
    endpoint_alias: str,
    sql: Optional[str] = None,
    tables: Optional[List[str]] = None,
    rows: int = 0,
    duration_ms: int = 0,
    cached: bool = False,
    error: Optional[str] = None,
) -> None:
    """Append one audit record (sync, lock-guarded; safe to call from async code)."""
    record: Dict[str, Any] = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime()),
        "tool": tool,
        "user_id": user_id,
        "username": username,
        "endpoint_id": endpoint_id,
        "endpoint_alias": endpoint_alias,
        "sql_hash": _sql_hash(sql),
        "sql_preview": (sql[:300] if sql else None),
        "tables": tables or [],
        "rows": rows,
        "duration_ms": duration_ms,
        "cached": cached,
        "error": (str(error)[:300] if error else None),
    }
    line = json.dumps(record, ensure_ascii=False)
    with _lock:
        with open(_audit_path(), "a", encoding="utf-8") as f:
            f.write(line + "\n")
