"""Management API routes for the DB-MCP Gateway.

Three-layer model:
  Connection  = credentials (admin)
  Endpoint    = MCP exposure (table scope lives here)
  ACL         = user -> endpoint
"""
from __future__ import annotations

import hmac
import logging
import os
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from core.metering import metering
from gateway_config import (ACLEntry, ConfigStore, ConnectionMeta, EndpointMeta, get_store, UserMeta)


logger = logging.getLogger("gateway.api")


# ---------------------------------------------------------------------------
# Admin authentication
# ---------------------------------------------------------------------------
# Set the ADMIN_TOKEN env var to protect /admin/api. Clients (incl. the
# bundled admin UI) must send:  Authorization: Bearer <ADMIN_TOKEN>
# If ADMIN_TOKEN is unset, the admin API stays open for backwards
# compatibility, but a loud warning is logged at startup (see gateway.py).

def _expected_admin_token() -> str:
    return os.environ.get("ADMIN_TOKEN", "")


async def require_admin(request: Request) -> None:
    expected = _expected_admin_token()
    if not expected:
        return
    auth = request.headers.get("authorization", "")
    token = auth[7:] if auth.lower().startswith("bearer ") else ""
    if not token or not hmac.compare_digest(token, expected):
        raise HTTPException(
            status_code=401,
            detail="Admin authentication required. Send 'Authorization: Bearer <ADMIN_TOKEN>'.",
        )


# ---------------------------------------------------------------------------
# Password masking
# ---------------------------------------------------------------------------
# Connection passwords are write-only: accepted on create/update, but never
# returned by any read API (previously GET /connections leaked them in plaintext).

class ConnectionPublic(BaseModel):
    """Connection as returned by read APIs: password always masked."""
    id: str
    alias: str
    db_type: str
    host: str = ""
    port: str = ""
    user: str = ""
    password: str = Field(default="***", description="Masked; write-only on create/update")
    db_name: str = ""
    extra: Dict[str, Any] = Field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""


def _public_conn(conn: ConnectionMeta) -> ConnectionPublic:
    data = conn.model_dump()
    data["password"] = "***"
    return ConnectionPublic(**data)


admin_router = APIRouter(
    prefix="/admin/api",
    tags=["admin"],
    dependencies=[Depends(require_admin)],
)
user_router = APIRouter(prefix="/api", tags=["user"])


def _store() -> ConfigStore:
    return get_store()


def _regen(request: Request) -> None:
    if hasattr(request.app, "state"):
        request.app.state.regen_routes = True


# =========================================================================
# Connections CRUD
# =========================================================================

@admin_router.get("/connections", response_model=List[ConnectionPublic])
def list_connections(db_type: Optional[str] = None):
    return [_public_conn(c) for c in _store().list_connections(db_type)]


@admin_router.get("/connections/{conn_id}", response_model=ConnectionPublic)
def get_connection(conn_id: str):
    conn = _store().get_connection(conn_id)
    if not conn:
        raise HTTPException(404, "Connection not found")
    return _public_conn(conn)


@admin_router.post("/connections", response_model=ConnectionPublic, status_code=201)
def create_connection(body: ConnectionMeta, request: Request):
    try:
        return _public_conn(_store().add_connection(body))
    except ValueError as e:
        raise HTTPException(400, str(e))


@admin_router.patch("/connections/{conn_id}", response_model=ConnectionPublic)
def update_connection(conn_id: str, body: Dict[str, Any], request: Request):
    try:
        return _public_conn(_store().update_connection(conn_id, body))
    except ValueError as e:
        raise HTTPException(404, str(e))


@admin_router.delete("/connections/{conn_id}", status_code=204)
def delete_connection(conn_id: str, request: Request):
    _store().delete_connection(conn_id)
    _regen(request)


@admin_router.post("/connections/batch", response_model=List[ConnectionPublic], status_code=201)
def batch_create_connections(items: List[ConnectionMeta], request: Request):
    results = []
    for item in items:
        try:
            results.append(_public_conn(_store().add_connection(item)))
        except ValueError:
            pass
    if results:
        _regen(request)
    if not results:
        raise HTTPException(400, "All items failed")
    return results


# =========================================================================
# Endpoints CRUD
# =========================================================================

@admin_router.get("/endpoints", response_model=List[EndpointMeta])
def list_endpoints(connection_id: Optional[str] = None):
    return _store().list_endpoints(connection_id)


@admin_router.get("/endpoints/{ep_id}", response_model=EndpointMeta)
def get_endpoint(ep_id: str):
    ep = _store().get_endpoint(ep_id)
    if not ep:
        raise HTTPException(404, "Endpoint not found")
    return ep


@admin_router.post("/endpoints", response_model=EndpointMeta, status_code=201)
def create_endpoint(body: EndpointMeta, request: Request):
    try:
        ep = _store().add_endpoint(body)
        _regen(request)
        return ep
    except ValueError as e:
        raise HTTPException(400, str(e))


@admin_router.patch("/endpoints/{ep_id}", response_model=EndpointMeta)
def update_endpoint(ep_id: str, body: Dict[str, Any], request: Request):
    try:
        ep = _store().update_endpoint(ep_id, body)
        _regen(request)
        return ep
    except ValueError as e:
        raise HTTPException(404, str(e))


@admin_router.delete("/endpoints/{ep_id}", status_code=204)
def delete_endpoint(ep_id: str, request: Request):
    _store().delete_endpoint(ep_id)
    _regen(request)


# =========================================================================
# Users CRUD
# =========================================================================

@admin_router.get("/users", response_model=List[UserMeta])
def list_users():
    return _store().list_users()


@admin_router.post("/users", response_model=UserMeta, status_code=201)
def create_user(body: UserMeta):
    try:
        return _store().add_user(body)
    except ValueError as e:
        raise HTTPException(400, str(e))


@admin_router.delete("/users/{user_id}", status_code=204)
def delete_user(user_id: str):
    _store().delete_user(user_id)


@admin_router.post("/users/{user_id}/regen-token", response_model=UserMeta)
def regenerate_token(user_id: str):
    try:
        _store().regenerate_token(user_id)
        return _store().get_user(user_id)
    except ValueError as e:
        raise HTTPException(404, str(e))


# =========================================================================
# ACL (user -> endpoint)
# =========================================================================

@admin_router.get("/acl", response_model=List[ACLEntry])
def list_acl(user_id: Optional[str] = None):
    return _store().list_acl(user_id)


@admin_router.post("/acl", response_model=ACLEntry, status_code=201)
def create_acl(user_id: str, endpoint_id: str, request: Request):
    try:
        entry = _store().add_acl(user_id, endpoint_id)
        _regen(request)
        return entry
    except ValueError as e:
        raise HTTPException(400, str(e))


@admin_router.post("/acl/batch", response_model=List[ACLEntry], status_code=201)
def batch_create_acl(items: List[Dict[str, str]], request: Request):
    results = []
    for item in items:
        try:
            results.append(_store().add_acl(item["user_id"], item["endpoint_id"]))
        except ValueError:
            pass
    if results:
        _regen(request)
    return results


@admin_router.delete("/acl/{acl_id}", status_code=204)
def delete_acl(acl_id: str, request: Request):
    _store().remove_acl(acl_id)
    _regen(request)


# =========================================================================
# Metering (usage / showback)
# =========================================================================

@admin_router.get("/metering")
def get_metering():
    """Aggregated usage: calls/rows/errors/latency per user, endpoint, tool."""
    return metering.summary()


# =========================================================================
# User-facing: discover endpoints by token
# =========================================================================

@user_router.get("/endpoints")
def list_my_endpoints(token: str):
    user = _store().get_user_by_token(token)
    if not user:
        raise HTTPException(403, "Invalid token")
    eps = _store().get_user_endpoints(user.id)
    return {
        "user": user.username,
        # L1 access: every MCP request must carry this token, either as
        #   Authorization: Bearer <token>   (preferred)
        # or as a ?token= query parameter.
        "auth": "Send your token as 'Authorization: Bearer <token>' header on every MCP request.",
        "endpoints": [
            {
                "endpoint_id": ep.id,
                "alias": ep.alias,
                "description": ep.description,
                "allowed_tables": ep.allowed_tables or "(all)",
                "mcp_sse": _store().get_endpoint_path(ep) + "/sse",
                "mcp_messages": _store().get_endpoint_path(ep) + "/messages",
                "mcp_http": _store().get_endpoint_path(ep) + "/mcp",
            }
            for ep in eps
        ],
    }
