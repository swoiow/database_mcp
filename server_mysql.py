"""MySQL MCP Server (SDK 1.27+).

Standalone mode: `python server_mysql.py` -> SSE on port 8001.
Gateway mode: imported by gateway_mcp_factory.py as needed.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel, Field

from core.cache import mk_cache_key, TTLCache
from core.engines import connect_checked
from drivers.mysql_driver import MySQLDriver
from prompts.mysql_prompts import MYSQL_PROMPTS


load_dotenv()

DB_HOST = os.getenv("MYSQL_HOST", "localhost")
DB_PORT = os.getenv("MYSQL_PORT", "3306")
DB_USER = os.getenv("MYSQL_USER", "")
DB_PASSWORD = os.getenv("MYSQL_PASSWORD", "")
DB_NAME = os.getenv("MYSQL_DB", "")

CACHE_ENABLED_DEFAULT = os.getenv("DBMCP_CACHE_ENABLED", "false").lower() == "true"
CACHE_TTL_DEFAULT = int(os.getenv("DBMCP_CACHE_TTL", "60"))
cache = TTLCache(maxsize=512)

driver = MySQLDriver()
mcp = MCPServer("DB-MCP-MySQL")


# ---------- Resources: prompts ----------
def _prompt_reader(text: str):
    def _read() -> str:
        return text

    return _read


for name, text_md in MYSQL_PROMPTS.items():
    # NOTE: MCPServer.add_resource() takes a Resource object (the old
    # uri=/text= kwargs never existed in SDK 1.27+); use the decorator.
    mcp.resource(
        f"mcp://mysql/prompts/{name}",
        description=f"MySQL built-in prompt: {name}",
        mime_type="text/markdown",
    )(_prompt_reader(text_md))


# ---------- Data models ----------
class ConnInput(BaseModel):
    host: str = Field(default=DB_HOST, description="Host")
    port: Optional[str] = Field(default=DB_PORT, description="Port")
    user: Optional[str] = Field(default=DB_USER, description="User")
    password: Optional[str] = Field(default=DB_PASSWORD, description="Password")
    db_name: Optional[str] = Field(default=DB_NAME, description="Database name")
    use_cache: bool = Field(default=CACHE_ENABLED_DEFAULT, description="Enable cache")
    ttl: int = Field(default=CACHE_TTL_DEFAULT, description="Cache TTL seconds")


class GetTablesInput(ConnInput):
    database: Optional[str] = Field(default=None, description="Target database")


class GetTableSchemaInput(ConnInput):
    database: Optional[str] = Field(default=None, description="Target database")
    table: str = Field(..., description="Table name")


class ExecuteSQLInput(ConnInput):
    sql: str = Field(..., description="Only SELECT or WITH")
    max_rows: int = Field(default=2000, description="Row limit")


def _connect(input: ConnInput) -> Any:
    # Returns an async context manager (core.engines.connect_checked);
    # call sites use `async with _connect(input) as conn:` (no await).
    conn_args = {
        "host": input.host, "user": input.user or "",
        "password": input.password or "", "db_name": input.db_name,
        "port": input.port,
    }
    return connect_checked(driver, "mysql", conn_args)


# ---------- Tools ----------
@mcp.tool(name="mysql_get_builtin_prompt", description="Get MySQL built-in prompt by name.")
def mysql_get_builtin_prompt(name: str) -> str:
    if name not in MYSQL_PROMPTS:
        raise ValueError(f"Unknown prompt name: {name}")
    return MYSQL_PROMPTS[name]


@mcp.tool(name="get_all_schemas", description="List databases and compact tables/columns map.")
async def get_all_schemas(input: ConnInput) -> Dict[str, Any]:
    key = mk_cache_key("mysql.get_all_schemas", input.model_dump())
    if input.use_cache:
        hit = await cache.get(key)
        if hit is not None:
            return hit
    async with _connect(input) as conn:
        out = await driver.get_all_schemas(conn)
    if input.use_cache:
        await cache.set(key, out, input.ttl)
    return out


@mcp.tool(name="get_tables", description="List tables under a database.")
async def get_tables(input: GetTablesInput) -> List[str]:
    key = mk_cache_key("mysql.get_tables", input.model_dump())
    if input.use_cache:
        hit = await cache.get(key)
        if hit is not None:
            return hit
    async with _connect(input) as conn:
        out = await driver.get_tables(conn, input.database or input.db_name)
    if input.use_cache:
        await cache.set(key, out, input.ttl)
    return out


@mcp.tool(name="get_table_schema", description="Describe a table.")
async def get_table_schema(input: GetTableSchemaInput) -> Dict[str, Any]:
    key = mk_cache_key("mysql.get_table_schema", input.model_dump())
    if input.use_cache:
        hit = await cache.get(key)
        if hit is not None:
            return hit
    async with _connect(input) as conn:
        out = await driver.get_table_schema(conn, input.database or input.db_name, input.table)
    if input.use_cache:
        await cache.set(key, out, input.ttl)
    return out


@mcp.tool(name="execute_sql", description="Execute read-only SELECT (JSON).")
async def execute_sql(input: ExecuteSQLInput) -> Dict[str, Any]:
    payload = {k: v for k, v in input.model_dump().items() if k != "password"}
    key = mk_cache_key("mysql.execute_sql", payload)
    if input.use_cache:
        hit = await cache.get(key)
        if hit is not None:
            return hit
    async with _connect(input) as conn:
        out = await driver.run_select_json(conn, input.sql, input.max_rows)
    if input.use_cache:
        await cache.set(key, out, input.ttl)
    return out


# ASGI app for uvicorn/Docker (`uvicorn server_mysql:app`).
# (MCPServer has no `.app` attribute; the old Dockerfile target `mcp.app`
# crashed with AttributeError.)
app = mcp.sse_app(message_path="/messages")  # mcp 2.x defaults to "/messages/"; keep v1 URL

if __name__ == "__main__":
    import uvicorn


    uvicorn.run(app, host="0.0.0.0", port=8001)
