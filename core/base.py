from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncConnection


class BaseDriver(ABC):
    """
    Database driver interface (bilingual)
    数据库驱动接口（中英双语）

    必备能力 | Required:
    - init_engine: 初始化异步引擎
    - ensure_connection: 连接探活
    - get_all_schemas: 返回 schema/db -> tables -> columns 的紧凑清单
    - get_tables: 返回指定 schema/db 下所有表
    - get_table_schema: 返回单表字段与元数据
    - run_select_json: 只读 SELECT，返回 JSON（columns/rows）
    """

    @abstractmethod
    async def init_engine(
        self,
        host: str,
        user: str,
        password: str,
        db_name: Optional[str],
        port: Optional[str],
    ) -> AsyncEngine: ...

    @abstractmethod
    async def ensure_connection(self, conn: AsyncConnection) -> None: ...

    @abstractmethod
    async def get_all_schemas(self, conn: AsyncConnection) -> Dict[str, Any]: ...

    @abstractmethod
    async def get_tables(self, conn: AsyncConnection, scope: Optional[str]) -> List[str]: ...

    @abstractmethod
    async def get_table_schema(
        self, conn: AsyncConnection, scope: Optional[str], table: str
    ) -> Dict[str, Any]: ...

    @abstractmethod
    async def run_select_json(self, conn: AsyncConnection, sql: str, max_rows: int) -> Dict[str, Any]: ...
