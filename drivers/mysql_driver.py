from __future__ import annotations
from typing import Any, Dict, List, Optional
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncConnection, create_async_engine
from sqlalchemy import text
from core.base import BaseDriver

SYSTEM_DBS = {"information_schema", "performance_schema", "mysql", "sys"}

class MySQLDriver(BaseDriver):
    """MySQL driver using SQLAlchemy + aiomysql
    MySQL 驱动（SQLAlchemy + aiomysql）
    """

    def _build_url(
        self, host: str, user: str, password: str, db_name: Optional[str], port: Optional[str]
    ) -> str:
        p = port or "3306"
        auth = f"{user}:{password}@" if user or password else ""
        db = f"/{db_name}" if db_name else ""
        return f"mysql+aiomysql://{auth}{host}:{p}{db}"

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
        sql = "SELECT schema_name FROM information_schema.schemata ORDER BY schema_name;"
        schemas = [r[0] for r in (await conn.execute(text(sql))).fetchall()]
        out: Dict[str, Any] = {}
        for s in schemas:
            if s in SYSTEM_DBS:
                continue
            out[s] = {"tables": {}}
            for t in await self.get_tables(conn, s):
                out[s]["tables"][t] = await self._get_table_columns(conn, s, t)
        return out

    async def get_tables(self, conn: AsyncConnection, scope: Optional[str]) -> List[str]:
        db = scope
        if not db:
            # fallback to current database
            db = (await conn.execute(text("SELECT DATABASE()"))).scalar_one()
        sql = """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = :db AND table_type='BASE TABLE'
        ORDER BY table_name;
        """
        rows = await conn.execute(text(sql), {"db": db})
        return [r[0] for r in rows.fetchall()]

    async def _get_table_columns(self, conn: AsyncConnection, db: str, table: str) -> List[str]:
        sql = """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = :db AND table_name = :t
        ORDER BY ordinal_position;
        """
        rows = await conn.execute(text(sql), {"db": db, "t": table})
        return [r[0] for r in rows.fetchall()]

    async def get_table_schema(self, conn: AsyncConnection, scope: Optional[str], table: str) -> Dict[str, Any]:
        db = scope
        if not db:
            db = (await conn.execute(text("SELECT DATABASE()"))).scalar_one()
        sql = """
        SELECT
            column_name,
            column_type,
            is_nullable,
            column_default,
            column_comment
        FROM information_schema.columns
        WHERE table_schema = :db AND table_name = :t
        ORDER BY ordinal_position;
        """
        rs = await conn.execute(text(sql), {"db": db, "t": table})
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
        return {"database": db, "table": table, "columns": out}

    def _append_limit_if_missing(self, sql: str, max_rows: int) -> str:
        s = sql.strip().rstrip(";")
        if " limit " in s.lower():
            return s
        return f"{s} LIMIT {max_rows}"

    async def run_select_json(self, conn: AsyncConnection, sql: str, max_rows: int) -> Dict[str, Any]:
        s = sql.strip()
        head = (s.split(None, 1)[0] if s else "").lower()
        if head != "select" and not s.lower().startswith("with "):
            raise ValueError("MySQL driver only allows SELECT / WITH. 仅允许 SELECT 或 WITH。")
        stmt = text(self._append_limit_if_missing(s, max_rows))
        rs = await conn.execute(stmt)
        rows = rs.fetchall()
        cols = rs.keys()
        data = [dict(zip(cols, r)) for r in rows]
        return {"columns": list(cols), "rows": data, "row_count": len(data)}
