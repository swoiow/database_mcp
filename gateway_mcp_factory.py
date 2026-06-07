"""Dynamic MCP server factory (SDK 1.27+).

Each endpoint gets its own MCP server. The connection provides credentials
(full admin access). The endpoint's allowed_tables controls the MCP-level
visibility — table-level scoping happens here, not at the connection.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from mcp.server.fastmcp import FastMCP

from drivers.mysql_driver import MySQLDriver
from drivers.pgsql_driver import PGSQLDriver
from gateway_config import ConnectionMeta, EndpointMeta
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
# MySQL
# ---------------------------------------------------------------------------

def _create_mysql_mcp(conn: ConnectionMeta, ep: EndpointMeta) -> FastMCP:
    driver = _mysql_driver
    conn_args = _build_conn_args(conn)

    mcp = FastMCP(f"DB-MCP-MySQL-{ep.alias}")

    for name, text_md in MYSQL_PROMPTS.items():
        mcp.add_resource(
            uri=f"mcp://mysql/{ep.id}/prompts/{name}",
            description=f"MySQL prompt ({ep.alias}): {name}",
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

    @mcp.tool(name="get_all_schemas", description="List databases and tables/columns (filtered by endpoint scope).")
    async def get_all_schemas() -> Dict[str, Any]:
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

    @mcp.tool(name="get_tables", description="List tables (filtered by endpoint scope).")
    async def get_tables(database: Optional[str] = None) -> List[str]:
        async with await _connect() as cn:
            raw = await driver.get_tables(cn, database or conn.db_name)
        return ep.filter_tables(raw, database or conn.db_name)

    @mcp.tool(name="get_table_schema", description="Describe a table (blocked if outside endpoint scope).")
    async def get_table_schema(table: str, database: Optional[str] = None) -> Dict[str, Any]:
        scope = database or conn.db_name
        if ep.table_restricted and not ep.is_table_allowed(table, scope):
            raise ValueError(f"Access denied: table '{table}' is not in the allowed list.")
        async with await _connect() as cn:
            return await driver.get_table_schema(cn, scope, table)

    @mcp.tool(name="execute_sql", description="Execute read-only SELECT (validated against endpoint scope).")
    async def execute_sql(sql: str, max_rows: int = 2000) -> Dict[str, Any]:
        if ep.table_restricted:
            _validate_sql_tables(sql, ep)
        async with await _connect() as cn:
            return await driver.run_select_json(cn, sql, max_rows)

    return mcp


# ---------------------------------------------------------------------------
# PostgreSQL
# ---------------------------------------------------------------------------

def _create_pgsql_mcp(conn: ConnectionMeta, ep: EndpointMeta) -> FastMCP:
    driver = _pgsql_driver
    conn_args = _build_conn_args(conn)

    mcp = FastMCP(f"DB-MCP-PGSQL-{ep.alias}")

    for name, text_md in PG_PROMPTS.items():
        mcp.add_resource(
            uri=f"mcp://pgsql/{ep.id}/prompts/{name}",
            description=f"PostgreSQL prompt ({ep.alias}): {name}",
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

    @mcp.tool(name="get_all_schemas", description="List schemas and tables/columns (filtered by endpoint scope).")
    async def get_all_schemas() -> Dict[str, Any]:
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

    @mcp.tool(name="get_tables", description="List tables (filtered by endpoint scope).")
    async def get_tables(schema: Optional[str] = None) -> List[str]:
        if not schema:
            raise ValueError("schema is required for PostgreSQL")
        async with await _connect() as cn:
            raw = await driver.get_tables(cn, schema)
        return ep.filter_tables(raw, schema)

    @mcp.tool(name="get_table_schema", description="Describe a table (blocked if outside endpoint scope).")
    async def get_table_schema(table: str, schema: Optional[str] = None) -> Dict[str, Any]:
        if not schema:
            raise ValueError("schema is required for PostgreSQL")
        if ep.table_restricted and not ep.is_table_allowed(table, schema):
            raise ValueError(f"Access denied: table '{table}' is not in the allowed list.")
        async with await _connect() as cn:
            return await driver.get_table_schema(cn, schema, table)

    @mcp.tool(name="execute_sql", description="Execute read-only SELECT (validated against endpoint scope).")
    async def execute_sql(sql: str, max_rows: int = 2000) -> Dict[str, Any]:
        if ep.table_restricted:
            _validate_sql_tables(sql, ep)
        async with await _connect() as cn:
            return await driver.run_select_json(cn, sql, max_rows)

    return mcp


# ---------------------------------------------------------------------------
# SQL validation
# ---------------------------------------------------------------------------

_SQL_KEYWORDS = {
    "select", "where", "group", "order", "having", "limit", "offset",
    "union", "intersect", "except", "on", "as", "and", "or", "not",
    "in", "exists", "between", "like", "is", "null", "true", "false",
    "case", "when", "then", "else", "end", "asc", "desc", "inner",
    "left", "right", "outer", "cross", "natural", "using",
    "distinct", "all", "any", "some", "values", "set", "into",
    "update", "delete", "insert", "create", "drop", "alter", "with",
}


def _validate_sql_tables(sql: str, ep: EndpointMeta) -> None:
    """Extract table references from SQL and validate against endpoint's whitelist."""
    if not ep.table_restricted:
        return

    upper = sql.upper().strip()
    if not upper.startswith("SELECT") and not upper.startswith("WITH"):
        raise ValueError("Only SELECT or WITH statements are allowed.")

    # Match table references after FROM / JOIN
    pattern = r'(?:FROM|JOIN)\s+([a-zA-Z_"][\w"]*(?:\.[\w"]+)?)'
    matches = re.findall(pattern, sql, re.IGNORECASE)

    referenced = set()
    for m in matches:
        # Strip backticks / double quotes
        clean = m.strip("`\"").lower()
        if clean not in _SQL_KEYWORDS:
            referenced.add(clean)

    if not referenced:
        raise ValueError(
            "Could not verify table access for this SQL. "
            "Simplify the query or contact an admin."
        )

    for ref in referenced:
        parts = ref.split(".", 1)
        table_name = parts[-1]
        scope = parts[0] if len(parts) == 2 else None
        if not ep.is_table_allowed(table_name, scope):
            raise ValueError(
                f"Access denied: table '{ref}' is not in the allowed list."
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
