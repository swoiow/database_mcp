"""E2E: core business flows against real MySQL / PostgreSQL.

Covers: real SELECT returns seeded rows, L2 table-scope denial,
read-only guard blocks writes (table left intact), L1 auth, tools list.
"""
import pytest

from conftest import MYSQL_CFG, PG_CFG, need_mysql, need_pg, _tool_payload


def _num(x) -> float:
    return float(x)


# ---------------------------------------------------------------- MySQL ---

@need_mysql
def test_mysql_tools_list(gw):
    ep, token, base = gw.setup("mysql", MYSQL_CFG, ["orders"])
    sid = gw.session(base, token)
    resp = gw.mcp(base, token, "tools/list", {}, session_id=sid)
    names = [t["name"] for t in resp["result"]["tools"]]
    assert {"get_all_schemas", "get_tables", "get_table_schema", "execute_sql"} <= set(names)


@need_mysql
def test_mysql_select_returns_seeded_rows(gw):
    ep, token, base = gw.setup("mysql", MYSQL_CFG, ["orders"])
    sid = gw.session(base, token)
    is_err, payload = _tool_payload(gw.call_tool(
        base, token, sid, "execute_sql",
        {"sql": "SELECT id, amount, status FROM orders ORDER BY id"}))
    assert not is_err, payload
    assert payload["row_count"] == 3
    amounts = [_num(r["amount"]) for r in payload["rows"]]
    assert amounts == [99.5, 20.0, 150.0]
    assert payload["rows"][0]["status"] == "paid"


@need_mysql
def test_mysql_scope_deny(gw):
    # endpoint scoped to orders only; users must be rejected (L2)
    ep, token, base = gw.setup("mysql", MYSQL_CFG, ["orders"])
    sid = gw.session(base, token)
    is_err, payload = _tool_payload(gw.call_tool(
        base, token, sid, "execute_sql", {"sql": "SELECT * FROM users"}))
    assert is_err


@need_mysql
def test_mysql_write_blocked_and_table_intact(gw):
    ep, token, base = gw.setup("mysql", MYSQL_CFG, ["orders", "users"])
    sid = gw.session(base, token)
    for sql in ("DELETE FROM orders", "INSERT INTO orders (user_id, amount) VALUES (1, 1.00)",
                "DROP TABLE orders"):
        is_err, _ = _tool_payload(gw.call_tool(
            base, token, sid, "execute_sql", {"sql": sql}))
        assert is_err, sql
    # table untouched
    is_err, payload = _tool_payload(gw.call_tool(
        base, token, sid, "execute_sql", {"sql": "SELECT COUNT(*) AS n FROM orders"}))
    assert not is_err, payload
    assert _num(payload["rows"][0]["n"]) == 3


@need_mysql
def test_mysql_l1_no_token_403(gw):
    ep, token, base = gw.setup("mysql", MYSQL_CFG, ["orders"])
    r = gw.c.post(base + "/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
    assert r.status_code == 403


# ------------------------------------------------------------ PostgreSQL ---

@need_pg
def test_pgsql_select_returns_seeded_rows(gw):
    ep, token, base = gw.setup("pgsql", PG_CFG, ["orders"])
    sid = gw.session(base, token)
    is_err, payload = _tool_payload(gw.call_tool(
        base, token, sid, "execute_sql",
        {"sql": "SELECT id, amount, status FROM orders ORDER BY id"}))
    assert not is_err, payload
    assert payload["row_count"] == 3
    assert [_num(r["amount"]) for r in payload["rows"]] == [99.5, 20.0, 150.0]


@need_pg
def test_pgsql_get_tables(gw):
    ep, token, base = gw.setup("pgsql", PG_CFG, [])
    sid = gw.session(base, token)
    is_err, payload = _tool_payload(gw.call_tool(
        base, token, sid, "get_tables", {"schema": "public"}))
    assert not is_err, payload
    names = payload if isinstance(payload, list) else payload.get("tables", [])
    assert {"users", "orders"} <= set(names)


@need_pg
def test_pgsql_scope_deny(gw):
    ep, token, base = gw.setup("pgsql", PG_CFG, ["public.orders"])
    sid = gw.session(base, token)
    is_err, _ = _tool_payload(gw.call_tool(
        base, token, sid, "execute_sql", {"sql": "SELECT * FROM public.users"}))
    assert is_err
