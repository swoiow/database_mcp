"""Core unit tests: AST-based read-only guard + table extraction."""
import sys
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.sqlguard import ensure_readonly_select, extract_tables


# --- read-only enforcement (P0-5) ---

@pytest.mark.parametrize("sql,dialect", [
    ("SELECT a, b FROM t WHERE x > 1 LIMIT 10", "mysql"),
    ("WITH cte AS (SELECT 1 AS x) SELECT * FROM cte", "postgres"),
    ("select * from `orders` o join `items` i on o.id = i.oid", "mysql"),
    ("SELECT * FROM t WHERE name = 'FROM fake'", "mysql"),  # string literal must not confuse
])
def test_legit_select_allowed(sql, dialect):
    ensure_readonly_select(sql, dialect)  # must not raise


@pytest.mark.parametrize("sql,dialect", [
    ("WITH x AS (DELETE FROM t RETURNING *) SELECT * FROM x", "postgres"),  # data-modifying CTE
    ("SELECT * INTO newtab FROM t", "postgres"),  # SELECT INTO
    ("SELECT a INTO OUTFILE '/tmp/x' FROM t", "mysql"),  # INTO OUTFILE
    ("SELECT 1; DROP TABLE users", "mysql"),  # stacked
    ("DELETE FROM t WHERE 1=1", "postgres"),
    ("UPDATE t SET a = 1", "mysql"),
    ("INSERT INTO t VALUES (1)", "mysql"),
    ("", "mysql"),
])
def test_write_or_stacked_rejected(sql, dialect):
    with pytest.raises(ValueError):
        ensure_readonly_select(sql, dialect)


# --- table extraction for L2 scope validation (P1-3) ---

def test_extract_simple():
    assert extract_tables("SELECT a FROM orders", "mysql") == [(None, "orders")]


def test_extract_qualified_and_join():
    refs = extract_tables(
        "SELECT * FROM shop.orders o JOIN shop.items i ON o.id = i.oid", "mysql")
    assert (None, "orders") not in refs  # qualified -> scope set
    assert ("shop", "orders") in refs
    assert ("shop", "items") in refs


def test_extract_quoted_with_space():
    # old regex missed this -> fail-closed false positive; AST handles it
    refs = extract_tables('SELECT * FROM "my schema"."my table"', "postgres")
    assert ("my schema", "my table") in refs


def test_extract_ignores_cte_names():
    # CTE names are not real tables and must not require whitelist entries
    refs = extract_tables(
        "WITH cte AS (SELECT 1 AS x) SELECT * FROM cte JOIN real_t ON true",
        "postgres")
    assert (None, "real_t") in refs
    assert all(t != "cte" for _, t in refs)


def test_extract_ignores_string_literals():
    refs = extract_tables("SELECT * FROM t WHERE note = 'FROM fake_table'", "mysql")
    assert refs == [(None, "t")]
