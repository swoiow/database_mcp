"""Gateway configuration models and persistence.

Manages database connections, users, and ACL rules.
Persisted to a local JSON file.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class ConnectionMeta(BaseModel):
    """A single database connection definition."""
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    alias: str = Field(..., description="Human-readable alias, unique per db_type")
    db_type: str = Field(..., description="mysql or pgsql")
    host: str = "localhost"
    port: str = ""
    user: str = ""
    password: str = ""
    db_name: str = ""
    extra: Dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class UserMeta(BaseModel):
    """A gateway user (token-based auth)."""
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    username: str = Field(..., description="Unique username")
    token: str = Field(default_factory=lambda: uuid.uuid4().hex)
    is_admin: bool = False
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class ACLEntry(BaseModel):
    """ACL: which user can access which connection."""
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    user_id: str
    connection_id: str


class GatewayConfig(BaseModel):
    """Top-level config persisted to disk."""
    connections: List[ConnectionMeta] = Field(default_factory=list)
    users: List[UserMeta] = Field(default_factory=list)
    acl: List[ACLEntry] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

_DEFAULT_PATH = Path("gateway_data.json")


class ConfigStore:
    """Synchronous JSON-file backed config store."""

    def __init__(self, path: Path | str | None = None) -> None:
        self._path = Path(path) if path else _DEFAULT_PATH
        self._data = self._load()

    # -- low-level IO --

    def _load(self) -> GatewayConfig:
        if self._path.exists():
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
                return GatewayConfig.model_validate(raw)
            except (json.JSONDecodeError, ValueError):
                pass
        return GatewayConfig()

    def _save(self) -> None:
        self._path.write_text(
            self._data.model_dump_json(indent=2),
            encoding="utf-8",
        )

    @property
    def data(self) -> GatewayConfig:
        return self._data

    def save(self) -> None:
        self._save()

    # -- connections --

    def list_connections(self, db_type: Optional[str] = None) -> List[ConnectionMeta]:
        conns = self._data.connections
        if db_type:
            conns = [c for c in conns if c.db_type == db_type]
        return conns

    def get_connection(self, conn_id: str) -> Optional[ConnectionMeta]:
        for c in self._data.connections:
            if c.id == conn_id:
                return c
        return None

    def get_connection_by_alias(self, alias: str) -> Optional[ConnectionMeta]:
        for c in self._data.connections:
            if c.alias == alias:
                return c
        return None

    def add_connection(self, conn: ConnectionMeta) -> ConnectionMeta:
        # check alias uniqueness within same db_type
        existing = self.get_connection_by_alias(conn.alias)
        if existing and existing.db_type == conn.db_type:
            raise ValueError(f"Alias '{conn.alias}' already exists for {conn.db_type}")
        self._data.connections.append(conn)
        self._save()
        return conn

    def update_connection(self, conn_id: str, patch: Dict[str, Any]) -> ConnectionMeta:
        conn = self.get_connection(conn_id)
        if not conn:
            raise ValueError(f"Connection '{conn_id}' not found")
        for k, v in patch.items():
            if k in ("id", "created_at"):
                continue
            setattr(conn, k, v)
        conn.updated_at = datetime.now(timezone.utc).isoformat()
        self._save()
        return conn

    def delete_connection(self, conn_id: str) -> None:
        self._data.connections = [c for c in self._data.connections if c.id != conn_id]
        # cascade ACL
        self._data.acl = [a for a in self._data.acl if a.connection_id != conn_id]
        self._save()

    # -- users --

    def list_users(self) -> List[UserMeta]:
        return self._data.users

    def get_user(self, user_id: str) -> Optional[UserMeta]:
        for u in self._data.users:
            if u.id == user_id:
                return u
        return None

    def get_user_by_token(self, token: str) -> Optional[UserMeta]:
        for u in self._data.users:
            if u.token == token:
                return u
        return None

    def add_user(self, user: UserMeta) -> UserMeta:
        for u in self._data.users:
            if u.username == user.username:
                raise ValueError(f"Username '{user.username}' already exists")
        self._data.users.append(user)
        self._save()
        return user

    def delete_user(self, user_id: str) -> None:
        self._data.users = [u for u in self._data.users if u.id != user_id]
        self._data.acl = [a for a in self._data.acl if a.user_id != user_id]
        self._save()

    def regenerate_token(self, user_id: str) -> str:
        user = self.get_user(user_id)
        if not user:
            raise ValueError(f"User '{user_id}' not found")
        user.token = uuid.uuid4().hex
        self._save()
        return user.token

    # -- ACL --

    def list_acl(self, user_id: Optional[str] = None) -> List[ACLEntry]:
        entries = self._data.acl
        if user_id:
            entries = [a for a in entries if a.user_id == user_id]
        return entries

    def add_acl(self, user_id: str, connection_id: str) -> ACLEntry:
        # validate
        if not self.get_user(user_id):
            raise ValueError(f"User '{user_id}' not found")
        if not self.get_connection(connection_id):
            raise ValueError(f"Connection '{connection_id}' not found")
        # duplicate check
        for a in self._data.acl:
            if a.user_id == user_id and a.connection_id == connection_id:
                return a
        entry = ACLEntry(user_id=user_id, connection_id=connection_id)
        self._data.acl.append(entry)
        self._save()
        return entry

    def remove_acl(self, acl_id: str) -> None:
        self._data.acl = [a for a in self._data.acl if a.id != acl_id]
        self._save()

    def check_access(self, user_id: str, connection_id: str) -> bool:
        return any(
            a.user_id == user_id and a.connection_id == connection_id
            for a in self._data.acl
        )

    def get_user_connections(self, user_id: str) -> List[ConnectionMeta]:
        conn_ids = {a.connection_id for a in self._data.acl if a.user_id == user_id}
        return [c for c in self._data.connections if c.id in conn_ids]

    def get_connection_endpoint(self, conn: ConnectionMeta) -> str:
        """Return the MCP endpoint path for a connection, e.g. /pgsql/abc123."""
        return f"/{conn.db_type}/{conn.id}"


# Global singleton (will be initialized by gateway)
store: Optional[ConfigStore] = None


def get_store() -> ConfigStore:
    global store
    if store is None:
        store = ConfigStore()
    return store
