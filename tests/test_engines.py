"""Core unit tests: engine cache fingerprint (P0-4)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.engines import engine_fingerprint


def test_fingerprint_stable():
    a = {"host": "h", "port": "3306", "user": "u", "password": "p", "db_name": "d"}
    assert engine_fingerprint("mysql", a) == engine_fingerprint("mysql", dict(a))


def test_fingerprint_sensitive_to_params():
    a = {"host": "h", "port": "3306", "user": "u", "password": "p", "db_name": "d"}
    assert engine_fingerprint("mysql", a) != engine_fingerprint("mysql", {**a, "password": "q"})
    assert engine_fingerprint("mysql", a) != engine_fingerprint("pgsql", a)


def test_fingerprint_tolerates_none():
    a = {"host": "h", "port": None, "user": "u", "password": None, "db_name": ""}
    engine_fingerprint("mysql", a)  # must not raise
