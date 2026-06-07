"""Dynamic MCP server factory.

Creates per-connection MCP instances with pre-bound connection params,
so callers only need to invoke tools without supplying credentials.
Uses MCP SDK 1.27+ API: FastMCP -> .sse_app() / .streamable_http_app().
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from mcp.server.fastmcp import FastMCP

from drivers.mysql_driver import MySQLDriver
from drivers.pgsql_driver import PGSQLDriver
from gateway_config import ConnectionMeta
from prompts.mysql_prompts import MYSQL_PROMPTS
from prompts.pgsql_prompts import PG_PROMPTS


logger = logging.getLogger("gateway.mcp_factory")

_mysql_driver = MySQLDriver()
_pgsql_driver = PGSQLDriver()


def _build_conn_args(conn: ConnectionMeta) -> Dict[str, str]:
    return {
        "host": conn.host,
        "port": conn.port or ("3306" if conn.db_type == "mysql" else "5432"),
        "user": conn.user,
        "password": conn.password,
        "db_name": conn.db_name,
    }


# ---------------------------------------------------------------------------
# MySQL MCP builder
# ---------------------------------------------------------------------------

def _create_mysql_mcp(conn: ConnectionMeta) -> FastMCP:
    driver = _mysql_driver
    conn_args = _build_conn_args(conn)

    mcp = FastMCP(f"DB-MCP-MySQL-{conn.alias}")

    # Resources: built-in prompts
    for name, text_md in MYSQL_PROMPTS.items():
        mcp.add_resource(
            uri=f"mcp://mysql/{conn.id}/prompts/{name}",
            description=f"MySQL prompt ({conn.alias}): {name}",
            mime_type="text/markdown",
            text=text_md,
        )

    async def _connect() -> Any:
        engine = await driver.init_engine(**conn_args)
        cn = await engine.connect()
        await driver.ensure_connection(cn)
        return cn

    @mcp.tool(name="mysql_get_builtin_prompt", description="Get MySQL built-in prompt by name.")
    def get_prompt(name: str) -> str:
        if name not in MYSQL_PROMPTS:
            raise ValueError(f"Unknown prompt: {name}")
        return MYSQL_PROMPTS[name]

    @mcp.tool(name="get_all_schemas", description="List databases and compact tables/columns map.")
    async def get_all_schemas() -> Dict[str, Any]:
        async with await _connect() as cn:
            return await driver.get_all_schemas(cn)

    @mcp.tool(name="get_tables", description="List tables under a database.")
    async def get_tables(database: Optional[str] = None) -> List[str]:
        async with await _connect() as cn:
            return await driver.get_tables(cn, database or conn.db_name)

    @mcp.tool(name="get_table_schema", description="Describe a table structure.")
    async def get_table_schema(table: str, database: Optional[str] = None) -> Dict[str, Any]:
        async with await _connect() as cn:
            return await driver.get_table_schema(cn, database or conn.db_name, table)

    @mcp.tool(name="execute_sql", description="Execute read-only SELECT (JSON).")
    async def execute_sql(sql: str, max_rows: int = 2000) -> Dict[str, Any]:
        async with await _connect() as cn:
            return await driver.run_select_json(cn, sql, max_rows)

    return mcp


# ---------------------------------------------------------------------------
# PostgreSQL MCP builder
# ---------------------------------------------------------------------------

def _create_pgsql_mcp(conn: ConnectionMeta) -> FastMCP:
    driver = _pgsql_driver
    conn_args = _build_conn_args(conn)

    mcp = FastMCP(f"DB-MCP-PGSQL-{conn.alias}")

    for name, text_md in PG_PROMPTS.items():
        mcp.add_resource(
            uri=f"mcp://pgsql/{conn.id}/prompts/{name}",
            description=f"PostgreSQL prompt ({conn.alias}): {name}",
            mime_type="text/markdown",
            text=text_md,
        )

    async def _connect() -> Any:
        engine = await driver.init_engine(**conn_args)
        cn = await engine.connect()
        await driver.ensure_connection(cn)
        return cn

    @mcp.tool(name="pgsql_get_builtin_prompt", description="Get PostgreSQL built-in prompt by name.")
    def get_prompt(name: str) -> str:
        if name not in PG_PROMPTS:
            raise ValueError(f"Unknown prompt: {name}")
        return PG_PROMPTS[name]

    @mcp.tool(name="get_all_schemas", description="List schemas and compact tables/columns map.")
    async def get_all_schemas() -> Dict[str, Any]:
        async with await _connect() as cn:
            return await driver.get_all_schemas(cn)

    @mcp.tool(name="get_tables", description="List tables under a schema.")
    async def get_tables(schema: Optional[str] = None) -> List[str]:
        if not schema:
            raise ValueError("schema is required for PostgreSQL")
        async with await _connect() as cn:
            return await driver.get_tables(cn, schema)

    @mcp.tool(name="get_table_schema", description="Describe a table structure.")
    async def get_table_schema(table: str, schema: Optional[str] = None) -> Dict[str, Any]:
        if not schema:
            raise ValueError("schema is required for PostgreSQL")
        async with await _connect() as cn:
            return await driver.get_table_schema(cn, schema, table)

    @mcp.tool(name="execute_sql", description="Execute read-only SELECT (JSON).")
    async def execute_sql(sql: str, max_rows: int = 2000) -> Dict[str, Any]:
        async with await _connect() as cn:
            return await driver.run_select_json(cn, sql, max_rows)

    return mcp


# ---------------------------------------------------------------------------
# Public factory
# ---------------------------------------------------------------------------

def create_mcp_for_connection(conn: ConnectionMeta) -> FastMCP:
    """Create a pre-configured MCP server for a given connection."""
    if conn.db_type == "mysql":
        return _create_mysql_mcp(conn)
    elif conn.db_type == "pgsql":
        return _create_pgsql_mcp(conn)
    else:
        raise ValueError(f"Unsupported db_type: {conn.db_type}")
