"""Core unit tests: usage metering + rate limiter (R2 / P1-2)."""
import sys
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.metering import Metering
from core.ratelimit import RateLimiter


@pytest.fixture()
def metering(tmp_path, monkeypatch):
    monkeypatch.setenv("DBMCP_METERING_PATH", str(tmp_path / "metering.json"))
    m = Metering(flush_interval=3600)  # no auto-flush during test
    yield m
    m.flush()


def test_metering_aggregates(metering):
    metering.record(user_id="u1", username="alice", endpoint_id="e1",
                    endpoint_alias="orders", tool="execute_sql",
                    duration_ms=120, rows=50)
    metering.record(user_id="u1", username="alice", endpoint_id="e1",
                    endpoint_alias="orders", tool="execute_sql",
                    duration_ms=80, rows=10, error=True)
    s = metering.summary()
    u = s["users"]["u1"]
    assert u["calls"] == 2 and u["rows"] == 60 and u["errors"] == 1
    assert u["total_ms"] == 200
    assert s["endpoints"]["e1"]["alias"] == "orders"
    assert s["tools"]["execute_sql"]["calls"] == 2


def test_metering_persists(tmp_path, monkeypatch):
    monkeypatch.setenv("DBMCP_METERING_PATH", str(tmp_path / "m.json"))
    m = Metering(flush_interval=3600)
    m.record(user_id="u9", username="bob", endpoint_id="e9",
             endpoint_alias="x", tool="get_tables", duration_ms=5)
    m.flush()
    m2 = Metering(flush_interval=3600)
    assert m2.summary()["users"]["u9"]["calls"] == 1


def test_ratelimit_allows_then_blocks():
    rl = RateLimiter(per_minute=3)
    assert all(rl.check("k") for _ in range(3))
    assert not rl.check("k")  # 4th within the minute -> blocked
    assert rl.check("other-key")  # independent bucket


def test_ratelimit_window_slides():
    rl = RateLimiter(per_minute=1)
    assert rl.check("k")
    assert not rl.check("k")
    # fake age-out by rewinding timestamps
    dq = rl._hits["k"]
    dq[0] -= 61
    assert rl.check("k")
