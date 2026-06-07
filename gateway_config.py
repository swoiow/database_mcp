"""Gateway configuration models and persistence.

Three-layer model:
  Connection  = credentials (admin-level, all tables)
  Endpoint    = MCP exposure layer (references a connection, carries allowed_tables)
  ACL         = user -> endpoint binding

Persisted via pickle for compact binary storage.
"""
from __future__ import annotations

import pickle
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class ConnectionMeta(BaseModel):
    """Database credentials. Admin-level, no table restrictions."""
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


class EndpointMeta(BaseModel):
    """MCP exposure layer. References a connection, optionally restricts tables.

    allowed_tables:
      []  = all tables (no restriction, inherits connection's full access)
      ["table1", "schema.table2"] = whitelist only those tables
    """
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    alias: str = Field(..., description="Unique endpoint alias, used in URL path")
    connection_id: str = Field(..., description="References a ConnectionMeta.id")
    allowed_tables: List[str] = Field(
        default_factory=list,
        description=(
            "Table whitelist. Empty = all tables. "
            "Formats: 'table', 'db.table', 'schema.table'."
        ),
    )
    description: str = ""
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def table_restricted(self) -> bool:
        return len(self.allowed_tables) > 0

    def is_table_allowed(self, table: str, scope: Optional[str] = None) -> bool:
        """Check if a table is in this endpoint's whitelist."""
        if not self.table_restricted:
            return True

        table_lower = table.lower().strip()
        scope_lower = (scope or "").lower().strip()

        for entry in self.allowed_tables:
            entry_lower = entry.lower().strip()
            if entry_lower == table_lower:
                return True
            if "." in entry_lower:
                e_scope, e_table = entry_lower.split(".", 1)
                if e_table == table_lower:
                    if not scope_lower or e_scope == scope_lower:
                        return True
        return False

    def filter_tables(self, tables: List[str], scope: Optional[str] = None) -> List[str]:
        if not self.table_restricted:
            return tables
        return [t for t in tables if self.is_table_allowed(t, scope)]


class UserMeta(BaseModel):
    """Gateway user (token-based auth)."""
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    username: str = Field(..., description="Unique username")
    token: str = Field(default_factory=lambda: uuid.uuid4().hex)
    is_admin: bool = False
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class ACLEntry(BaseModel):
    """ACL: which user can access which endpoint."""
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    user_id: str
    endpoint_id: str


class GatewayConfig(BaseModel):
    """Top-level config persisted to disk."""
    connections: List[ConnectionMeta] = Field(default_factory=list)
    endpoints: List[EndpointMeta] = Field(default_factory=list)
    users: List[UserMeta] = Field(default_factory=list)
    acl: List[ACLEntry] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

_DEFAULT_PATH = Path("gateway_data.pkl")
_PKL_PROTOCOL = pickle.HIGHEST_PROTOCOL


class ConfigStore:
    """Pickle-file backed config store."""

    def __init__(self, path: Path | str | None = None) -> None:
        self._path = Path(path) if path else _DEFAULT_PATH
        self._data = self._load()

    def _load(self) -> GatewayConfig:
        if self._path.exists():
            try:
                with open(self._path, "rb") as f:
                    raw = pickle.load(f)
                # Support both dict and GatewayConfig payloads
                if isinstance(raw, GatewayConfig):
                    return raw
                if isinstance(raw, dict):
                    return GatewayConfig.model_validate(raw)
            except (pickle.UnpicklingError, ValueError, EOFError, Exception):
                pass
        return GatewayConfig()

    def _save(self) -> None:
        with open(self._path, "wb") as f:
            pickle.dump(self._data, f, protocol=_PKL_PROTOCOL)

    @property
    def data(self) -> GatewayConfig:
        return self._data

    # ------------------------------------------------------------------
    # Connections
    # ------------------------------------------------------------------

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
        ep_ids = {e.id for e in self._data.endpoints if e.connection_id == conn_id}
        self._data.endpoints = [e for e in self._data.endpoints if e.connection_id != conn_id]
        self._data.acl = [a for a in self._data.acl if a.endpoint_id not in ep_ids]
        self._save()

    # ------------------------------------------------------------------
    # Endpoints
    # ------------------------------------------------------------------

    def list_endpoints(self, connection_id: Optional[str] = None) -> List[EndpointMeta]:
        eps = self._data.endpoints
        if connection_id:
            eps = [e for e in eps if e.connection_id == connection_id]
        return eps

    def get_endpoint(self, endpoint_id: str) -> Optional[EndpointMeta]:
        for e in self._data.endpoints:
            if e.id == endpoint_id:
                return e
        return None

    def get_endpoint_by_alias(self, alias: str) -> Optional[EndpointMeta]:
        for e in self._data.endpoints:
            if e.alias == alias:
                return e
        return None

    def add_endpoint(self, ep: EndpointMeta) -> EndpointMeta:
        if self.get_endpoint_by_alias(ep.alias):
            raise ValueError(f"Endpoint alias '{ep.alias}' already exists")
        if not self.get_connection(ep.connection_id):
            raise ValueError(f"Connection '{ep.connection_id}' not found")
        self._data.endpoints.append(ep)
        self._save()
        return ep

    def update_endpoint(self, endpoint_id: str, patch: Dict[str, Any]) -> EndpointMeta:
        ep = self.get_endpoint(endpoint_id)
        if not ep:
            raise ValueError(f"Endpoint '{endpoint_id}' not found")
        for k, v in patch.items():
            if k in ("id", "created_at"):
                continue
            setattr(ep, k, v)
        self._save()
        return ep

    def delete_endpoint(self, endpoint_id: str) -> None:
        self._data.endpoints = [e for e in self._data.endpoints if e.id != endpoint_id]
        self._data.acl = [a for a in self._data.acl if a.endpoint_id != endpoint_id]
        self._save()

    def resolve_endpoint(self, ep: EndpointMeta):
        """Return (ConnectionMeta, EndpointMeta) or raise."""
        conn = self.get_connection(ep.connection_id)
        if not conn:
            raise ValueError(f"Endpoint '{ep.id}' references missing connection '{ep.connection_id}'")
        return conn, ep

    def get_endpoint_path(self, ep: EndpointMeta) -> str:
        """URL path for this endpoint based on connection's db_type."""
        conn = self.get_connection(ep.connection_id)
        db_type = conn.db_type if conn else "unknown"
        return f"/{db_type}/{ep.alias}"

    # ------------------------------------------------------------------
    # Users
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # ACL
    # ------------------------------------------------------------------

    def list_acl(self, user_id: Optional[str] = None) -> List[ACLEntry]:
        entries = self._data.acl
        if user_id:
            entries = [a for a in entries if a.user_id == user_id]
        return entries

    def add_acl(self, user_id: str, endpoint_id: str) -> ACLEntry:
        if not self.get_user(user_id):
            raise ValueError(f"User '{user_id}' not found")
        if not self.get_endpoint(endpoint_id):
            raise ValueError(f"Endpoint '{endpoint_id}' not found")
        for a in self._data.acl:
            if a.user_id == user_id and a.endpoint_id == endpoint_id:
                return a
        entry = ACLEntry(user_id=user_id, endpoint_id=endpoint_id)
        self._data.acl.append(entry)
        self._save()
        return entry

    def remove_acl(self, acl_id: str) -> None:
        self._data.acl = [a for a in self._data.acl if a.id != acl_id]
        self._save()

    def check_access(self, user_id: str, endpoint_id: str) -> bool:
        return any(
            a.user_id == user_id and a.endpoint_id == endpoint_id
            for a in self._data.acl
        )

    def get_user_endpoints(self, user_id: str) -> List[EndpointMeta]:
        ep_ids = {a.endpoint_id for a in self._data.acl if a.user_id == user_id}
        return [e for e in self._data.endpoints if e.id in ep_ids]


# Global singleton
store: Optional[ConfigStore] = None


def get_store() -> ConfigStore:
    global store
    if store is None:
        store = ConfigStore()
    return store
