"""Management API routes for the DB-MCP Gateway.

Provides REST endpoints to manage connections, users, and ACL,
plus a token-authenticated endpoint for users to discover their MCP endpoints.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request

from gateway_config import (ACLEntry, ConfigStore, ConnectionMeta, get_store, UserMeta)


logger = logging.getLogger("gateway.api")

admin_router = APIRouter(prefix="/admin/api", tags=["admin"])
user_router = APIRouter(prefix="/api", tags=["user"])


def _store() -> ConfigStore:
    return get_store()


def _regen_app(request: Request) -> None:
    gateway = request.app
    if hasattr(gateway, "state"):
        gateway.state.regen_routes = True


# =========================================================================
# Connections CRUD
# =========================================================================

@admin_router.get("/connections", response_model=List[ConnectionMeta])
def list_connections(db_type: Optional[str] = None):
    return _store().list_connections(db_type)


@admin_router.get("/connections/{conn_id}", response_model=ConnectionMeta)
def get_connection(conn_id: str):
    conn = _store().get_connection(conn_id)
    if not conn:
        raise HTTPException(404, "Connection not found")
    return conn


@admin_router.post("/connections", response_model=ConnectionMeta, status_code=201)
def create_connection(body: ConnectionMeta, request: Request):
    try:
        conn = _store().add_connection(body)
        _regen_app(request)
        return conn
    except ValueError as e:
        raise HTTPException(400, str(e))


@admin_router.patch("/connections/{conn_id}", response_model=ConnectionMeta)
def update_connection(conn_id: str, body: Dict[str, Any], request: Request):
    try:
        conn = _store().update_connection(conn_id, body)
        _regen_app(request)
        return conn
    except ValueError as e:
        raise HTTPException(404, str(e))


@admin_router.delete("/connections/{conn_id}", status_code=204)
def delete_connection(conn_id: str, request: Request):
    _store().delete_connection(conn_id)
    _regen_app(request)


@admin_router.post("/connections/batch", response_model=List[ConnectionMeta], status_code=201)
def batch_create_connections(items: List[ConnectionMeta], request: Request):
    """Batch create connections. Supports bulk-add for large fleets."""
    results = []
    errors = []
    for item in items:
        try:
            results.append(_store().add_connection(item))
        except ValueError as e:
            errors.append({"alias": item.alias, "error": str(e)})
    if results:
        _regen_app(request)
    if errors and not results:
        raise HTTPException(400, {"message": "All items failed", "errors": errors})
    return results


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
# ACL
# =========================================================================

@admin_router.get("/acl", response_model=List[ACLEntry])
def list_acl(user_id: Optional[str] = None):
    return _store().list_acl(user_id)


@admin_router.post("/acl", response_model=ACLEntry, status_code=201)
def create_acl(user_id: str, connection_id: str, request: Request):
    try:
        entry = _store().add_acl(user_id, connection_id)
        _regen_app(request)
        return entry
    except ValueError as e:
        raise HTTPException(400, str(e))


@admin_router.post("/acl/batch", response_model=List[ACLEntry], status_code=201)
def batch_create_acl(items: List[Dict[str, str]], request: Request):
    """Batch create ACL rules: [{"user_id": "...", "connection_id": "..."}, ...]"""
    results = []
    for item in items:
        try:
            results.append(_store().add_acl(item["user_id"], item["connection_id"]))
        except ValueError:
            pass
    if results:
        _regen_app(request)
    return results


@admin_router.delete("/acl/{acl_id}", status_code=204)
def delete_acl(acl_id: str, request: Request):
    _store().remove_acl(acl_id)
    _regen_app(request)


# =========================================================================
# User-facing: discover endpoints by token
# =========================================================================

@user_router.get("/endpoints")
def list_my_endpoints(token: str):
    """Given a user token, return the list of MCP endpoints this user can access."""
    user = _store().get_user_by_token(token)
    if not user:
        raise HTTPException(403, "Invalid token")
    conns = _store().get_user_connections(user.id)
    return {
        "user": user.username,
        "endpoints": [
            {
                "connection_id": c.id,
                "alias": c.alias,
                "db_type": c.db_type,
                "host": c.host,
                "db_name": c.db_name,
                "mcp_sse": f"/{c.db_type}/{c.alias}/sse",
                "mcp_messages": f"/{c.db_type}/{c.alias}/messages",
                "mcp_http": f"/{c.db_type}/{c.alias}/mcp",
                "mcp_sse_id": f"/{c.db_type}/{c.id}/sse",
                "mcp_messages_id": f"/{c.db_type}/{c.id}/messages",
                "mcp_http_id": f"/{c.db_type}/{c.id}/mcp",
            }
            for c in conns
        ],
    }
