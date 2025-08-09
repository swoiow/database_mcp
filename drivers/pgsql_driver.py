from __future__ import annotations
from typing import Any, Dict, List, Optional
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncConnection, create_async_engine
from sqlalchemy import text
from core.base import BaseDriver

class PGSQLDriver(BaseDriver):
    """PostgreSQL driver using SQLAlchemy + asyncpg
    PostgreSQL 驱动（SQLAlchemy + asyncpg）
    """

    def _build_url(
        self, host: str, user: str, password: str, db_name: Optional[str], port: Optional[str]
    ) -> str:
        p = port or "5432"
        auth = f"{user}:{password}@" if user or password else ""
        db = f"/{db_name}" if db_name else ""
        return f"postgresql+asyncpg://{auth}{host}:{p}{db}"

    async def init_engine(
        self, host: str, user: str, password: str, db_name: Optional[str], port: Optional[str]
    ) -> AsyncEngine:
        return create_async_engine(
            self._build_url(host, user, password, db_name, port),
            pool_size=10,
            max_overflow=20,
            pool_recycle=1800,
        )

    async def ensure_connection(self, conn: AsyncConnection) -> None:
        await conn.exec_driver_sql("SELECT 1")

    async def get_all_schemas(self, conn: AsyncConnection) -> Dict[str, Any]:
        sql = """
        SELECT schema_name
        FROM information_schema.schemata
        WHERE schema_name NOT IN ('pg_catalog','information_schema')
        ORDER BY schema_name;
        """
        schemas = [r[0] for r in (await conn.execute(text(sql))).fetchall()]
        out: Dict[str, Any] = {}
        for s in schemas:
            out[s] = {"tables": {}}
            for t in await self.get_tables(conn, s):
                out[s]["tables"][t] = await self._get_table_columns(conn, s, t)
        return out

    async def get_tables(self, conn: AsyncConnection, scope: Optional[str]) -> List[str]:
        if not scope:
            raise ValueError("schema is required for PostgreSQL / PostgreSQL 必须提供 schema")
        sql = """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = :s AND table_type='BASE TABLE'
        ORDER BY table_name;
        """
        rows = await conn.execute(text(sql), {"s": scope})
        return [r[0] for r in rows.fetchall()]

    async def _get_table_columns(self, conn: AsyncConnection, schema: str, table: str) -> List[str]:
        sql = """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = :s AND table_name = :t
        ORDER BY ordinal_position;
        """
        rows = await conn.execute(text(sql), {"s": schema, "t": table})
        return [r[0] for r in rows.fetchall()]

    async def get_table_schema(self, conn: AsyncConnection, scope: Optional[str], table: str) -> Dict[str, Any]:
        if not scope:
            raise ValueError("schema is required for PostgreSQL / PostgreSQL 必须提供 schema")
        sql = """
        SELECT
            c.column_name,
            c.data_type,
            c.is_nullable,
            c.column_default,
            pgd.description
        FROM information_schema.columns c
        LEFT JOIN pg_catalog.pg_statio_all_tables st
          ON st.schemaname = c.table_schema
         AND st.relname = c.table_name
        LEFT JOIN pg_catalog.pg_description pgd
          ON pgd.objoid = st.relid
         AND pgd.objsubid = c.ordinal_position
        WHERE c.table_schema = :s AND c.table_name = :t
        ORDER BY c.ordinal_position;
        """
        rs = await conn.execute(text(sql), {"s": scope, "t": table})
        rows = rs.fetchall()
        out = [
            {
                "name": r[0],
                "type": r[1],
                "nullable": r[2],
                "default": r[3],
                "comment": r[4],
            }
            for r in rows
        ]
        return {"schema": scope, "table": table, "columns": out}

    def _append_limit_if_missing(self, sql: str, max_rows: int) -> str:
        s = sql.strip().rstrip(";")
        if " limit " in s.lower():
            return s
        return f"{s} LIMIT {max_rows}"

    async def run_select_json(self, conn: AsyncConnection, sql: str, max_rows: int) -> Dict[str, Any]:
        s = sql.strip()
        head = (s.split(None, 1)[0] if s else "").lower()
        if head != "select" and not s.lower().startswith("with "):
            raise ValueError("PostgreSQL driver only allows SELECT / WITH. 仅允许 SELECT 或 WITH。")
        stmt = text(self._append_limit_if_missing(s, max_rows))
        rs = await conn.execute(stmt)
        rows = rs.fetchall()
        cols = rs.keys()
        data = [dict(zip(cols, r)) for r in rows]
        return {"columns": list(cols), "rows": data, "row_count": len(data)}
