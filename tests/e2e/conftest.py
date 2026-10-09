"""E2E fixtures: real MySQL/PostgreSQL + gateway TestClient.

Databases are expected at 127.0.0.1:3306 / 127.0.0.1:5432 (see
.github/docker-compose.e2e.yml); connection details can be overridden
with E2E_MYSQL_* / E2E_PG_* env vars.

Skips cleanly when a database is not reachable, so `pytest tests/`
still passes without docker running.
"""
import asyncio
import json
import os
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))

MYSQL_CFG = {
    "host": os.environ.get("E2E_MYSQL_HOST", "127.0.0.1"),
    "port": int(os.environ.get("E2E_MYSQL_PORT", "3306")),
    "user": os.environ.get("E2E_MYSQL_USER", "e2e"),
    "password": os.environ.get("E2E_MYSQL_PASSWORD", "e2e"),
    "database": os.environ.get("E2E_MYSQL_DB", "e2e"),
}
PG_CFG = {
    "host": os.environ.get("E2E_PG_HOST", "127.0.0.1"),
    "port": int(os.environ.get("E2E_PG_PORT", "5432")),
    "user": os.environ.get("E2E_PG_USER", "e2e"),
    "password": os.environ.get("E2E_PG_PASSWORD", "e2e"),
    "database": os.environ.get("E2E_PG_DB", "e2e"),
}


def _mysql_up_once() -> bool:
    try:
        import pymysql
        conn = pymysql.connect(connect_timeout=3, **MYSQL_CFG)
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
        conn.close()
        return True
    except Exception:
        return False


def _pg_up_once() -> bool:
    try:
        import asyncpg

        async def go():
            conn = await asyncpg.connect(timeout=3, **PG_CFG)
            await conn.fetchval("SELECT 1")
            await conn.close()

        asyncio.run(go())
        return True
    except Exception:
        return False


def _wait_for_db(check, timeout: int = 90, interval: int = 2) -> bool:
    """Retry until the database accepts connections or the timeout expires.

    Containers need time to start (image pull, entrypoint initdb scripts);
    a single probe at collection time would flake or skip incorrectly.
    Timeout is tunable via E2E_WAIT_TIMEOUT (seconds).
    """
    import time
    deadline = time.monotonic() + timeout
    while True:
        if check():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(interval)


_WAIT_TIMEOUT = int(os.environ.get("E2E_WAIT_TIMEOUT", "90"))
dbs_up = {
    "mysql": _wait_for_db(_mysql_up_once, timeout=_WAIT_TIMEOUT),
    "postgres": _wait_for_db(_pg_up_once, timeout=_WAIT_TIMEOUT),
}

need_mysql = pytest.mark.skipif(not dbs_up["mysql"], reason="MySQL not reachable (start .github/docker-compose.e2e.yml)")
need_pg = pytest.mark.skipif(not dbs_up["postgres"], reason="PostgreSQL not reachable (start .github/docker-compose.e2e.yml)")


class Gateway:
    """Thin driver over the gateway admin API + MCP streamable HTTP."""

    def __init__(self, client):
        self.c = client
        self._n = 0

    def _alias(self, prefix: str) -> str:
        self._n += 1
        return f"{prefix}-{self._n}"

    # -- admin API ------------------------------------------------------
    def add_connection(self, db_type: str, cfg: dict) -> dict:
        r = self.c.post("/admin/api/connections", json={
            "alias": self._alias(f"e2e-{db_type}"), "db_type": db_type,
            "host": cfg["host"], "port": str(cfg["port"]),
            "user": cfg["user"], "password": cfg["password"],
            "db_name": cfg["database"],
        })
        assert r.status_code == 201, r.text
        return r.json()

    def add_endpoint(self, conn_id: str, db_type: str, tables: list) -> dict:
        r = self.c.post("/admin/api/endpoints", json={
            "alias": self._alias("ep"), "connection_id": conn_id,
            "description": "e2e", "allowed_tables": tables,
        })
        assert r.status_code == 201, r.text
        ep = r.json()
        ep["_db_type"] = db_type
        return ep

    def add_user(self, username: str = "e2e-user") -> dict:
        r = self.c.post("/admin/api/users", json={"username": self._alias(username)})
        assert r.status_code == 201, r.text
        return r.json()

    def grant(self, user_id: str, endpoint_id: str) -> None:
        r = self.c.post("/admin/api/acl", params={"user_id": user_id, "endpoint_id": endpoint_id})
        assert r.status_code == 201, r.text

    def setup(self, db_type: str, cfg: dict, tables: list):
        """Full stack: connection + scoped endpoint + user + ACL. Returns (ep, token, base)."""
        conn = self.add_connection(db_type, cfg)
        ep = self.add_endpoint(conn["id"], db_type, tables)
        user = self.add_user()
        self.grant(user["id"], ep["id"])
        return ep, user["token"], f"/{db_type}/{ep['alias']}"

    # -- MCP streamable HTTP --------------------------------------------
    def _headers(self, token: str, session_id: str | None = None) -> dict:
        h = {
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Host": "localhost:8000",
        }
        if session_id:
            h["Mcp-Session-Id"] = session_id
        return h

    def mcp(self, base: str, token: str, method: str, params: dict,
            session_id: str | None = None, req_id: int = 1) -> dict:
        r = self.c.post(base + "/mcp",
                        json={"jsonrpc": "2.0", "id": req_id, "method": method, "params": params},
                        headers=self._headers(token, session_id))
        assert r.status_code == 200, f"{r.status_code}: {r.text[:200]}"
        m = re.search(r"^data: (.*)$", r.text, re.M)
        assert m, f"no SSE data in response: {r.text[:200]}"
        return json.loads(m.group(1))

    def session(self, base: str, token: str) -> str:
        r = self.c.post(
            base + "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                  "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                             "clientInfo": {"name": "e2e", "version": "1"}}},
            headers=self._headers(token),
        )
        assert r.status_code == 200, r.text[:200]
        sid = r.headers.get("mcp-session-id")
        assert sid, "no session id returned"
        return sid

    def call_tool(self, base: str, token: str, sid: str, name: str, args: dict) -> dict:
        return self.mcp(base, token, "tools/call",
                        {"name": name, "arguments": args}, session_id=sid)


def _tool_payload(resp: dict):
    """Unpack a tools/call response -> (is_error, parsed_json_or_text)."""
    result = resp["result"]
    if result.get("isError"):
        content = result.get("content", [{}])
        text = content[0].get("text", "") if content else ""
        return True, text
    content = result.get("content", [{}])
    text = content[0].get("text", "") if content else ""
    try:
        return False, json.loads(text)
    except (ValueError, TypeError):
        return False, text


@pytest.fixture(scope="module")
def gw(tmp_path_factory):
    if not any(dbs_up.values()):
        pytest.skip("no e2e database reachable; start .github/docker-compose.e2e.yml")
    tmp = tmp_path_factory.mktemp("e2e")
    saved_env = dict(os.environ)
    os.environ["DBMCP_AUDIT_PATH"] = str(tmp / "audit.log")
    os.environ["DBMCP_METERING_PATH"] = str(tmp / "metering.json")
    import gateway_config
    gateway_config.store = gateway_config.ConfigStore(path=str(tmp / "gateway_data.json"))
    try:
        import gateway
        from fastapi.testclient import TestClient
        with TestClient(gateway.app) as client:
            yield Gateway(client)
    finally:
        os.environ.clear()
        os.environ.update(saved_env)
        gateway_config.store = None
