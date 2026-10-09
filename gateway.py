"""DB-MCP Gateway - main entrypoint.

Three-layer model:
  Connection = database credentials (admin-level, all tables)
  Endpoint   = MCP exposure layer (references a connection + optional allowed_tables)
  ACL        = user -> endpoint binding

Mounts one MCP sub-app per endpoint under:
  /{db_type}/{endpoint_alias}/sse|messages|mcp

Config persisted to gateway_data.json (see gateway_config.py).
"""
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Dict, Optional

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.applications import Starlette

from core.ctx import request_ctx
from core.metering import metering
from gateway_api import admin_router, user_router
from gateway_config import ConfigStore, get_store, UserMeta
from gateway_mcp_factory import create_mcp_for_endpoint
from mcp.server.transport_security import TransportSecuritySettings


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("gateway")

# mount path -> (kind, sub-app); each endpoint gets 4 mounts:
#   /{db_type}/{alias}      SSE (/sse, /messages)
#   /{db_type}/{alias}/mcp  Streamable HTTP
#   /{db_type}/{id}         SSE (by id)
#   /{db_type}/{id}/mcp     Streamable HTTP (by id)
_mounted_apps: Dict[str, object] = {}


# ---------------------------------------------------------------------------
# Per-endpoint sub-apps: one Starlette serving SSE (/sse, /messages) and
# Streamable HTTP (/mcp), mounted at /{db_type}/{alias} and /{db_type}/{id}.
# NOTE: Starlette does NOT propagate lifespan to mounted sub-apps, so the
# streamable session manager is started/stopped here at the gateway level.
# ---------------------------------------------------------------------------
_mcp_runners: Dict[str, tuple] = {}  # endpoint id -> (runner task, stop event)


def _transport_security() -> Optional[TransportSecuritySettings]:
    """Build SDK transport-security settings from env, if configured.

    The MCP SDK enables DNS-rebinding protection by default and only
    allow-lists localhost-style hosts. Serving the gateway on a public
    domain requires extending the allow-list, e.g.::

        MCP_ALLOWED_HOSTS="db.example.com:*"

    (mcp 1.x used ``FASTMCP_TRANSPORT_SECURITY__ALLOWED_HOSTS`` for this;
    mcp 2.x removed env-var configuration, so the gateway reads it and
    passes ``TransportSecuritySettings`` explicitly.)
    """
    raw = os.environ.get("MCP_ALLOWED_HOSTS", "").strip()
    if not raw:
        return None
    hosts = [h.strip() for h in raw.split(",") if h.strip()]
    origins = []
    for h in hosts:
        origins.append("https://" + h)
        origins.append("http://" + h)
    return TransportSecuritySettings(allowed_hosts=hosts, allowed_origins=origins)


def _build_endpoint_app(mcp) -> Starlette:
    # NOTE (mcp 2.x): sse_app()'s default message_path is "/messages/"
    # (trailing slash); pass "/messages" explicitly to keep the v1 URL.
    sec = _transport_security()
    sse_sub = mcp.sse_app(message_path="/messages",
                          transport_security=sec)  # routes: /sse, /messages (no lifespan needs)
    http_sub = mcp.streamable_http_app(
        transport_security=sec)  # routes: /mcp (default path)
    return Starlette(routes=[*sse_sub.routes, *http_sub.routes])


async def _session_runner(ep_id: str, mcp, stop: asyncio.Event) -> None:
    # The task group inside session_manager.run() must be entered and exited
    # in the SAME task; a dedicated runner task guarantees that no matter
    # which task created/dropped the endpoint.
    async with mcp.session_manager.run():
        await stop.wait()


async def _ensure_session_manager(ep_id: str, mcp) -> None:
    if ep_id not in _mcp_runners:
        stop = asyncio.Event()
        task = asyncio.create_task(_session_runner(ep_id, mcp, stop))
        _mcp_runners[ep_id] = (task, stop)


async def _drop_session_manager(ep_id: str) -> None:
    entry = _mcp_runners.pop(ep_id, None)
    if entry is not None:
        task, stop = entry
        stop.set()
        try:
            await task
        except asyncio.CancelledError:
            pass


async def _sync_mounts(app: FastAPI, store: ConfigStore) -> None:
    global _mounted_apps

    desired: Dict[str, str] = {}   # mount path -> endpoint id
    built: Dict[str, object] = {}  # endpoint id -> combined sub-app (one MCPServer per endpoint)
    for ep in store.list_endpoints():
        conn = store.get_connection(ep.connection_id)
        if not conn:
            continue
        for base in (store.get_endpoint_path(ep), f"/{conn.db_type}/{ep.id}"):
            desired[base] = ep.id
        if all(p in _mounted_apps for p, eid in desired.items() if eid == ep.id):
            continue  # already mounted, nothing to rebuild
        try:
            mcp = create_mcp_for_endpoint(conn, ep)
            sub = _build_endpoint_app(mcp)  # calls streamable_http_app() -> creates session manager
            await _ensure_session_manager(ep.id, mcp)
            built[ep.id] = sub
        except Exception:
            logger.exception("Failed to create MCP for endpoint %s", ep.alias)
            continue

    # Remove stale
    for path in [p for p in _mounted_apps if p not in desired]:
        ep_id = _mounted_apps[path]
        logger.info("Unmounting: %s", path)
        app.routes = [r for r in app.routes if not _route_matches(r, path)]
        del _mounted_apps[path]
        if ep_id not in _mounted_apps.values():
            await _drop_session_manager(ep_id)

    # Add new
    for path, ep_id in desired.items():
        if path in _mounted_apps:
            continue
        sub = built.get(ep_id)
        if sub is None:
            continue
        app.mount(path, sub)
        _mounted_apps[path] = ep_id
        logger.info("Mounted MCP: %s (sse + streamable http)", path)


def _route_matches(route, prefix: str) -> bool:
    if hasattr(route, "path") and hasattr(route, "app"):
        return route.path.rstrip("/") == prefix.rstrip("/")
    return False


@asynccontextmanager
async def lifespan(app: FastAPI):
    store = get_store()
    if not store.list_users():
        store.add_user(UserMeta(username="admin", is_admin=True))
        logger.info("Created default admin user")
    if not os.environ.get("ADMIN_TOKEN"):
        logger.warning(
            "ADMIN_TOKEN is not set: /admin/api is UNPROTECTED. "
            "Set the ADMIN_TOKEN env var to require 'Authorization: Bearer <token>'."
        )
    await _sync_mounts(app, store)
    yield
    for ep_id in list(_mcp_runners):
        await _drop_session_manager(ep_id)
    metering.flush()


def _extract_user_token(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return (request.query_params.get("token") or "").strip()


async def mcp_access_middleware(request: Request, call_next):
    """L1 permission: MCP endpoint access requires a user token with ACL.

    Two-level model (gateway-owned admin queries):
      L1 (here)    : token -> endpoint ACL. No token / no grant -> 403.
      L2 (factory) : endpoint table scope enforced per tool call.

    Identity is published via core.ctx.request_ctx for audit/metering/
    rate limiting downstream.
    """
    parts = request.url.path.strip("/").split("/")
    if len(parts) >= 2 and parts[0] in ("mysql", "pgsql"):
        slug = parts[1]
        store = get_store()
        ep = store.get_endpoint(slug) or store.get_endpoint_by_alias(slug)
        conn = store.get_connection(ep.connection_id) if ep else None
        if ep is not None and conn is not None and conn.db_type == parts[0]:
            token = _extract_user_token(request)
            user = store.get_user_by_token(token) if token else None
            if not user or not store.check_access(user.id, ep.id):
                return JSONResponse(
                    {"detail": "Forbidden: a user token with access to this endpoint is required."},
                    status_code=403,
                )
            ctx_token = request_ctx.set({
                "user_id": user.id,
                "username": user.username,
                "endpoint_id": ep.id,
                "endpoint_alias": ep.alias,
            })
            try:
                return await call_next(request)
            finally:
                request_ctx.reset(ctx_token)
    return await call_next(request)


app = FastAPI(title="DB-MCP-Gateway", lifespan=lifespan)
app.state.regen_routes = False

app.include_router(admin_router)
app.include_router(user_router)

# L1 permission enforcement for MCP endpoints (registered after app creation).
app.middleware("http")(mcp_access_middleware)

_static_dir = Path(__file__).parent / "static"
if _static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")


@app.get("/admin", response_class=HTMLResponse)
async def admin_page():
    html_path = _static_dir / "admin.html"
    if html_path.exists():
        return HTMLResponse(html_path.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>Admin UI not found</h1>", status_code=404)


@app.get("/")
async def root() -> dict:
    return {
        "service": "DB-MCP-Gateway",
        "endpoints_count": len(get_store().list_endpoints()),
        "docs": {
            "admin_ui": "/admin",
            "user_discovery": "/api/endpoints?token=YOUR_TOKEN",
            "mcp_sse": "/{db_type}/{endpoint_alias}/sse (Authorization: Bearer <user token>)",
            "mcp_http": "/{db_type}/{endpoint_alias}/mcp (Authorization: Bearer <user token>)",
        },
    }


@app.post("/admin/reload")
async def reload_mounts():
    await _sync_mounts(app, get_store())
    return {"mounted": list(_mounted_apps.keys())}


@app.middleware("http")
async def route_regen_middleware(request: Request, call_next):
    response = await call_next(request)
    if getattr(request.app.state, "regen_routes", False):
        request.app.state.regen_routes = False
        await _sync_mounts(request.app, get_store())
    return response


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
