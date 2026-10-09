"""Dynamic MCP server factory (SDK 1.27+).

Each endpoint gets its own MCP server. The connection provides credentials
(full admin access). The endpoint's allowed_tables controls the MCP-level
visibility — table-level scoping happens here, not at the connection.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional

from mcp.server.fastmcp import FastMCP

from core.audit import audit_event
from core.cache import TTLCache, mk_cache_key
from core.ctx import get_ctx
from core.engines import get_engine
from core.metering import metering
from core.ratelimit import ratelimiter
from core.sqlguard import extract_tables
from drivers.mysql_driver import MySQLDriver
from drivers.pgsql_driver import PGSQLDriver
from gateway_config import ConnectionMeta, EndpointMeta
from prompts.mysql_prompts import MYSQL_PROMPTS
from prompts.pgsql_prompts import PG_PROMPTS


logger = logging.getLogger("gateway.mcp_factory")

_mysql_driver = MySQLDriver()
_pgsql_driver = PGSQLDriver()


# ---------------------------------------------------------------------------
# Runtime guards (env-configurable)
# ---------------------------------------------------------------------------
_QUERY_TIMEOUT = int(os.environ.get("DBMCP_QUERY_TIMEOUT", "30") or 30)
_MAX_ROWS_HARD = int(os.environ.get("DBMCP_MAX_ROWS", "10000") or 10000)
_CACHE_ENABLED_DEFAULT = os.environ.get("DBMCP_CACHE_ENABLED", "false").lower() == "true"
_CACHE_TTL_DEFAULT = int(os.environ.get("DBMCP_CACHE_TTL", "60") or 60)

_tool_cache = TTLCache(maxsize=1024)


def _clamp_rows(n: int) -> int:
    return max(1, min(int(n or 0), _MAX_ROWS_HARD))


async def _run_tool(
    tool: str,
    ep: EndpointMeta,
    cache_args: Dict[str, Any],
    fn: Callable[[], Awaitable[Any]],
    *,
    dialect: str = "mysql",
    use_cache: bool = False,
    ttl: int = 60,
) -> Any:
    """Cross-cutting wrapper for every MCP tool: rate limit -> cache ->
    execute (timeout) -> audit + metering.

    Identity comes from the L1 auth middleware (core.ctx); standalone
    servers report as anonymous.
    """
    ctx = get_ctx()
    user_id = ctx.get("user_id") or "anonymous"
    username = ctx.get("username") or "anonymous"

    if not ratelimiter.check(f"{user_id}:{ep.id}"):
        raise ValueError(
            f"Rate limit exceeded ({ratelimiter.per_minute}/min for this endpoint)."
        )

    cache_key = mk_cache_key(f"{ep.id}.{tool}", cache_args) if use_cache else None
    if cache_key:
        hit = await _tool_cache.get(cache_key)
        if hit is not None:
            metering.record(user_id=user_id, username=username,
                            endpoint_id=ep.id, endpoint_alias=ep.alias,
                            tool=tool, duration_ms=0, cached=True)
            return hit

    start = time.monotonic()
    try:
        out = await fn()
    except Exception as e:
        dur = int((time.monotonic() - start) * 1000)
        metering.record(user_id=user_id, username=username,
                        endpoint_id=ep.id, endpoint_alias=ep.alias,
                        tool=tool, duration_ms=dur, error=True)
        audit_event(tool=tool, user_id=user_id, username=username,
                    endpoint_id=ep.id, endpoint_alias=ep.alias,
                    sql=cache_args.get("sql"), duration_ms=dur,
                    error=e)
        raise
    dur = int((time.monotonic() - start) * 1000)
    rows = out.get("row_count", 0) if isinstance(out, dict) else 0
    if cache_key:
        await _tool_cache.set(cache_key, out, ttl)
    metering.record(user_id=user_id, username=username,
                    endpoint_id=ep.id, endpoint_alias=ep.alias,
                    tool=tool, duration_ms=dur, rows=rows)
    sql = cache_args.get("sql")
    audit_event(tool=tool, user_id=user_id, username=username,
                endpoint_id=ep.id, endpoint_alias=ep.alias,
                sql=sql,
                tables=[t for _, t in extract_tables(sql, dialect)] if sql else None,
                rows=rows, duration_ms=dur)
    return out


def _build_conn_args(conn: ConnectionMeta) -> Dict[str, str]:
    return {
        "host": conn.host,
        "port": conn.port or ("3306" if conn.db_type == "mysql" else "5432"),
        "user": conn.user,
        "password": conn.password,
        "db_name": conn.db_name,
    }


async def _connect_cached(driver: Any, db_type: str, conn_args: Dict[str, str]) -> Any:
    """Borrow a connection from the process-wide cached engine.

    Previously each tool call built a brand-new AsyncEngine and never
    disposed it -- leaking pools/threads on every query.
    """
    engine = await get_engine(driver, db_type, conn_args)
    cn = await engine.connect()
    await driver.ensure_connection(cn)
    return cn


# ---------------------------------------------------------------------------
# MySQL
# ---------------------------------------------------------------------------

def _create_mysql_mcp(conn: ConnectionMeta, ep: EndpointMeta) -> FastMCP:
    driver = _mysql_driver
    conn_args = _build_conn_args(conn)

    mcp = FastMCP(f"DB-MCP-MySQL-{ep.alias}")

    def _prompt_reader(text: str):
        def _read() -> str:
            return text
        return _read

    for name, text_md in MYSQL_PROMPTS.items():
        # NOTE: FastMCP.add_resource() takes a Resource object (the old
        # uri=/text= kwargs never existed in SDK 1.27+); use the decorator.
        mcp.resource(
            f"mcp://mysql/{ep.id}/prompts/{name}",
            description=f"MySQL prompt ({ep.alias}): {name}",
            mime_type="text/markdown",
        )(_prompt_reader(text_md))

    async def _connect() -> Any:
        return await _connect_cached(driver, conn.db_type, conn_args)

    @mcp.tool(name="mysql_get_builtin_prompt", description="Get MySQL built-in prompt by name.")
    def get_prompt(name: str) -> str:
        if name not in MYSQL_PROMPTS:
            raise ValueError(f"Unknown prompt: {name}")
        return MYSQL_PROMPTS[name]

    @mcp.tool(name="get_all_schemas", description="List databases and tables/columns (filtered by endpoint scope).")
    async def get_all_schemas(use_cache: bool = _CACHE_ENABLED_DEFAULT,
                              ttl: int = _CACHE_TTL_DEFAULT) -> Dict[str, Any]:
        async def _do() -> Dict[str, Any]:
            async with await _connect() as cn:
                raw = await driver.get_all_schemas(cn)
            if not ep.table_restricted:
                return raw
            filtered: Dict[str, Any] = {}
            for db_name, db_val in raw.items():
                tables = db_val.get("tables", {})
                kept = {
                    t: cols for t, cols in tables.items()
                    if ep.is_table_allowed(t, db_name)
                }
                if kept:
                    filtered[db_name] = {"tables": kept}
            return filtered
        return await _run_tool("get_all_schemas", ep, {}, _do,
                               use_cache=use_cache, ttl=ttl)

    @mcp.tool(name="get_tables", description="List tables (filtered by endpoint scope).")
    async def get_tables(database: Optional[str] = None,
                         use_cache: bool = _CACHE_ENABLED_DEFAULT,
                         ttl: int = _CACHE_TTL_DEFAULT) -> List[str]:
        scope = database or conn.db_name
        async def _do() -> List[str]:
            async with await _connect() as cn:
                raw = await driver.get_tables(cn, scope)
            return ep.filter_tables(raw, scope)
        return await _run_tool("get_tables", ep, {"database": scope}, _do,
                               use_cache=use_cache, ttl=ttl)

    @mcp.tool(name="get_table_schema", description="Describe a table (blocked if outside endpoint scope).")
    async def get_table_schema(table: str, database: Optional[str] = None,
                               use_cache: bool = _CACHE_ENABLED_DEFAULT,
                               ttl: int = _CACHE_TTL_DEFAULT) -> Dict[str, Any]:
        scope = database or conn.db_name
        async def _do() -> Dict[str, Any]:
            if ep.table_restricted and not ep.is_table_allowed(table, scope):
                raise ValueError(f"Access denied: table '{table}' is not in the allowed list.")
            async with await _connect() as cn:
                return await driver.get_table_schema(cn, scope, table)
        return await _run_tool("get_table_schema", ep,
                               {"table": table, "database": scope}, _do,
                               use_cache=use_cache, ttl=ttl)

    @mcp.tool(name="execute_sql", description="Execute read-only SELECT (validated against endpoint scope).")
    async def execute_sql(sql: str, max_rows: int = 2000,
                          use_cache: bool = _CACHE_ENABLED_DEFAULT,
                          ttl: int = _CACHE_TTL_DEFAULT) -> Dict[str, Any]:
        max_rows = _clamp_rows(max_rows)
        async def _do() -> Dict[str, Any]:
            if ep.table_restricted:
                _validate_sql_tables(sql, ep, "mysql")
            async with await _connect() as cn:
                return await asyncio.wait_for(
                    driver.run_select_json(cn, sql, max_rows),
                    timeout=_QUERY_TIMEOUT)
        return await _run_tool("execute_sql", ep,
                               {"sql": sql, "max_rows": max_rows}, _do,
                               dialect="mysql",
                               use_cache=use_cache, ttl=ttl)

    return mcp


# ---------------------------------------------------------------------------
# PostgreSQL
# ---------------------------------------------------------------------------

def _create_pgsql_mcp(conn: ConnectionMeta, ep: EndpointMeta) -> FastMCP:
    driver = _pgsql_driver
    conn_args = _build_conn_args(conn)

    mcp = FastMCP(f"DB-MCP-PGSQL-{ep.alias}")

    def _prompt_reader(text: str):
        def _read() -> str:
            return text
        return _read

    for name, text_md in PG_PROMPTS.items():
        mcp.resource(
            f"mcp://pgsql/{ep.id}/prompts/{name}",
            description=f"PostgreSQL prompt ({ep.alias}): {name}",
            mime_type="text/markdown",
        )(_prompt_reader(text_md))

    async def _connect() -> Any:
        return await _connect_cached(driver, conn.db_type, conn_args)

    @mcp.tool(name="pgsql_get_builtin_prompt", description="Get PostgreSQL built-in prompt by name.")
    def get_prompt(name: str) -> str:
        if name not in PG_PROMPTS:
            raise ValueError(f"Unknown prompt: {name}")
        return PG_PROMPTS[name]

    @mcp.tool(name="get_all_schemas", description="List schemas and tables/columns (filtered by endpoint scope).")
    async def get_all_schemas(use_cache: bool = _CACHE_ENABLED_DEFAULT,
                              ttl: int = _CACHE_TTL_DEFAULT) -> Dict[str, Any]:
        async def _do() -> Dict[str, Any]:
            async with await _connect() as cn:
                raw = await driver.get_all_schemas(cn)
            if not ep.table_restricted:
                return raw
            filtered: Dict[str, Any] = {}
            for schema_name, schema_val in raw.items():
                tables = schema_val.get("tables", {})
                kept = {
                    t: cols for t, cols in tables.items()
                    if ep.is_table_allowed(t, schema_name)
                }
                if kept:
                    filtered[schema_name] = {"tables": kept}
            return filtered
        return await _run_tool("get_all_schemas", ep, {}, _do, dialect="postgres",
                               use_cache=use_cache, ttl=ttl)

    @mcp.tool(name="get_tables", description="List tables (filtered by endpoint scope).")
    async def get_tables(schema: Optional[str] = None,
                         use_cache: bool = _CACHE_ENABLED_DEFAULT,
                         ttl: int = _CACHE_TTL_DEFAULT) -> List[str]:
        if not schema:
            raise ValueError("schema is required for PostgreSQL")
        async def _do() -> List[str]:
            async with await _connect() as cn:
                raw = await driver.get_tables(cn, schema)
            return ep.filter_tables(raw, schema)
        return await _run_tool("get_tables", ep, {"schema": schema}, _do,
                               dialect="postgres", use_cache=use_cache, ttl=ttl)

    @mcp.tool(name="get_table_schema", description="Describe a table (blocked if outside endpoint scope).")
    async def get_table_schema(table: str, schema: Optional[str] = None,
                               use_cache: bool = _CACHE_ENABLED_DEFAULT,
                               ttl: int = _CACHE_TTL_DEFAULT) -> Dict[str, Any]:
        if not schema:
            raise ValueError("schema is required for PostgreSQL")
        async def _do() -> Dict[str, Any]:
            if ep.table_restricted and not ep.is_table_allowed(table, schema):
                raise ValueError(f"Access denied: table '{table}' is not in the allowed list.")
            async with await _connect() as cn:
                return await driver.get_table_schema(cn, schema, table)
        return await _run_tool("get_table_schema", ep,
                               {"table": table, "schema": schema}, _do,
                               dialect="postgres", use_cache=use_cache, ttl=ttl)

    @mcp.tool(name="execute_sql", description="Execute read-only SELECT (validated against endpoint scope).")
    async def execute_sql(sql: str, max_rows: int = 2000,
                          use_cache: bool = _CACHE_ENABLED_DEFAULT,
                          ttl: int = _CACHE_TTL_DEFAULT) -> Dict[str, Any]:
        max_rows = _clamp_rows(max_rows)
        async def _do() -> Dict[str, Any]:
            if ep.table_restricted:
                _validate_sql_tables(sql, ep, "postgres")
            async with await _connect() as cn:
                return await asyncio.wait_for(
                    driver.run_select_json(cn, sql, max_rows),
                    timeout=_QUERY_TIMEOUT)
        return await _run_tool("execute_sql", ep,
                               {"sql": sql, "max_rows": max_rows}, _do,
                               dialect="postgres",
                               use_cache=use_cache, ttl=ttl)

    return mcp


# ---------------------------------------------------------------------------
# SQL validation (L2: endpoint table scope, AST-based)
# ---------------------------------------------------------------------------

def _validate_sql_tables(sql: str, ep: EndpointMeta, dialect: str) -> None:
    """Validate every referenced table against the endpoint whitelist.

    Uses sqlglot AST extraction (see core.sqlguard.extract_tables) instead
    of the old FROM|JOIN regex, which mis-parsed quoted identifiers and
    string literals. Read-only enforcement itself lives in the drivers
    (core.sqlguard.ensure_readonly_select).
    """
    if not ep.table_restricted:
        return
    refs = extract_tables(sql, dialect)
    if not refs:
        raise ValueError(
            "Could not verify table access for this SQL. "
            "Simplify the query or contact an admin."
        )
    for scope, table in refs:
        if not ep.is_table_allowed(table, scope):
            raise ValueError(
                f"Access denied: table '{table}' is not in the allowed list."
            )


# ---------------------------------------------------------------------------
# Public factory
# ---------------------------------------------------------------------------

def create_mcp_for_endpoint(conn: ConnectionMeta, ep: EndpointMeta) -> FastMCP:
    """Create an MCP server for an endpoint. Connection = credentials, endpoint = scope."""
    if conn.db_type == "mysql":
        return _create_mysql_mcp(conn, ep)
    elif conn.db_type == "pgsql":
        return _create_pgsql_mcp(conn, ep)
    else:
        raise ValueError(f"Unsupported db_type: {conn.db_type}")
