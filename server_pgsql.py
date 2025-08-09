import os
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncConnection

from core.cache import TTLCache, mk_cache_key
from drivers.pgsql_driver import PGSQLDriver
from prompts.pgsql_prompts import PG_PROMPTS

"""
PostgreSQL 专用 MCP Server
- 独立进程，独立提示词
- 可选 TTL 缓存（全局开关 + 每次调用开关）
"""

load_dotenv()

DB_HOST = os.getenv("PG_HOST", "localhost")
DB_PORT = os.getenv("PG_PORT", "5432")
DB_USER = os.getenv("PG_USER", "")
DB_PASSWORD = os.getenv("PG_PASSWORD", "")
DB_NAME = os.getenv("PG_DB", "")

CACHE_ENABLED_DEFAULT = os.getenv("DBMCP_CACHE_ENABLED", "false").lower() == "true"
CACHE_TTL_DEFAULT = int(os.getenv("DBMCP_CACHE_TTL", "60"))
cache = TTLCache(maxsize=512)

driver = PGSQLDriver()
mcp = FastMCP("DB-MCP-PGSQL", host=None)

# ---------- 资源：PostgreSQL 提示词 ----------
for name, text_md in PG_PROMPTS.items():
    mcp.add_resource(
        uri=f"mcp://pgsql/prompts/{name}",
        description=f"PostgreSQL built-in prompt: {name}",
        mime_type="text/markdown",
        text=text_md,
    )

# ---------- 数据模型 ----------
class ConnInput(BaseModel):
    host: str = Field(default=DB_HOST, description="Host / 主机")
    port: Optional[str] = Field(default=DB_PORT, description="Port / 端口")
    user: Optional[str] = Field(default=DB_USER, description="User / 用户名")
    password: Optional[str] = Field(default=DB_PASSWORD, description="Password / 密码")
    db_name: Optional[str] = Field(default=DB_NAME, description="Database name / 数据库名")
    use_cache: bool = Field(default=CACHE_ENABLED_DEFAULT, description="Enable cache for this call / 是否启用缓存")
    ttl: int = Field(default=CACHE_TTL_DEFAULT, description="Cache TTL seconds / 缓存秒数")


class GetTablesInput(ConnInput):
    schema: Optional[str] = Field(default=None, description="Target schema / 目标 schema（必传）")


class GetTableSchemaInput(ConnInput):
    schema: Optional[str] = Field(default=None, description="Target schema / 目标 schema（必传）")
    table: str = Field(..., description="Table name / 表名")


class ExecuteSQLInput(ConnInput):
    sql: str = Field(..., description="Only SELECT or WITH / 仅允许 SELECT 或 WITH")
    max_rows: int = Field(default=2000, description="Row limit / 最大返回行数")


async def _connect(input: ConnInput) -> AsyncConnection:
    engine = await driver.init_engine(
        host=input.host, user=input.user or "", password=input.password or "", db_name=input.db_name, port=input.port
    )
    conn = await engine.connect()
    await driver.ensure_connection(conn)
    return conn


# ---------- 工具 ----------
@mcp.tool(
    name="pgsql_get_builtin_prompt",
    description="Get PostgreSQL built-in prompt by name. 获取 PostgreSQL 内置提示词（analysis/sql_rules/react）。",
)
def pgsql_get_builtin_prompt(name: str) -> str:
    if name not in PG_PROMPTS:
        raise ValueError(f"Unknown prompt name: {name}")
    return PG_PROMPTS[name]


@mcp.tool(
    name="get_all_schemas",
    description="List schemas and compact tables/columns map. 列出非系统 schema 与紧凑的表/字段清单。",
)
async def get_all_schemas(input: ConnInput) -> Dict[str, Any]:
    key = mk_cache_key("pgsql.get_all_schemas", input.model_dump())
    if input.use_cache:
        hit = await cache.get(key)
        if hit is not None:
            return hit
    async with await _connect(input) as conn:
        out = await driver.get_all_schemas(conn)
    if input.use_cache:
        await cache.set(key, out, input.ttl)
    return out


@mcp.tool(
    name="get_tables",
    description="List tables under a schema. 列出指定 schema 下的所有表。",
)
async def get_tables(input: GetTablesInput) -> List[str]:
    if not input.schema:
        raise ValueError("schema is required / 必须提供 schema")
    payload = input.model_dump()
    key = mk_cache_key("pgsql.get_tables", payload)
    if input.use_cache:
        hit = await cache.get(key)
        if hit is not None:
            return hit
    async with await _connect(input) as conn:
        out = await driver.get_tables(conn, input.schema)
    if input.use_cache:
        await cache.set(key, out, input.ttl)
    return out


@mcp.tool(
    name="get_table_schema",
    description="Describe a table. 获取单表结构（字段/类型/可空/默认/注释）。",
)
async def get_table_schema(input: GetTableSchemaInput) -> Dict[str, Any]:
    if not input.schema:
        raise ValueError("schema is required / 必须提供 schema")
    payload = input.model_dump()
    key = mk_cache_key("pgsql.get_table_schema", payload)
    if input.use_cache:
        hit = await cache.get(key)
        if hit is not None:
            return hit
    async with await _connect(input) as conn:
        out = await driver.get_table_schema(conn, input.schema, input.table)
    if input.use_cache:
        await cache.set(key, out, input.ttl)
    return out


@mcp.tool(
    name="execute_sql",
    description="Execute read-only SELECT (JSON). 执行只读 SELECT（自动 LIMIT，返回 JSON）。",
)
async def execute_sql(input: ExecuteSQLInput) -> Dict[str, Any]:
    payload = {k: v for k, v in input.model_dump().items() if k != "password"}
    key = mk_cache_key("pgsql.execute_sql", payload)
    if input.use_cache:
        hit = await cache.get(key)
        if hit is not None:
            return hit
    async with await _connect(input) as conn:
        out = await driver.run_select_json(conn, input.sql, input.max_rows)
    if input.use_cache:
        await cache.set(key, out, input.ttl)
    return out


if __name__ == "__main__":
    # uvicorn server_pgsql:mcp.app --host 0.0.0.0 --port 8002
    mcp.run()
