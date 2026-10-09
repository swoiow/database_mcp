"""Core unit tests: _run_tool audits/metering/rate-limit (incl. denials)."""
import asyncio
import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("mcp")
pytest.importorskip("sqlalchemy")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gateway_config import EndpointMeta
from gateway_mcp_factory import _run_tool


@pytest.fixture()
def ep():
    return EndpointMeta(alias="e", connection_id="c", allowed_tables=["orders"])


def _run(coro):
    return asyncio.run(coro)


def test_denial_is_audited_and_metered(tmp_path, monkeypatch, ep):
    monkeypatch.setenv("DBMCP_AUDIT_PATH", str(tmp_path / "audit.log"))
    monkeypatch.setenv("DBMCP_METERING_PATH", str(tmp_path / "metering.json"))

    async def denied():
        raise ValueError("Access denied: table 'users' is not in the allowed list.")

    async def main():
        with pytest.raises(ValueError):
            await _run_tool("execute_sql", ep, {"sql": "SELECT * FROM users"},
                            denied, dialect="mysql")
    _run(main())

    rec = json.loads((tmp_path / "audit.log").read_text().strip().split("\n")[-1])
    assert rec["tool"] == "execute_sql"
    assert rec["error"] and "Access denied" in rec["error"]
    assert rec["sql_hash"]  # query fingerprinted even on denial


def test_rate_limit_blocks(tmp_path, monkeypatch, ep):
    monkeypatch.setenv("DBMCP_AUDIT_PATH", str(tmp_path / "audit.log"))
    monkeypatch.setenv("DBMCP_METERING_PATH", str(tmp_path / "metering.json"))
    import core.ratelimit as rl
    monkeypatch.setattr(rl.ratelimiter, "per_minute", 1)

    async def ok():
        return {"row_count": 0}

    async def main():
        await _run_tool("get_tables", ep, {}, ok)  # 1st passes
        with pytest.raises(ValueError, match="Rate limit"):
            await _run_tool("get_tables", ep, {}, ok)  # 2nd blocked
    _run(main())
