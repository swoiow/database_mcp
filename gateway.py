"""DB-MCP Gateway - main entrypoint.

Three-layer model:
  Connection = database credentials (admin-level, all tables)
  Endpoint   = MCP exposure layer (references a connection + optional allowed_tables)
  ACL        = user -> endpoint binding

Mounts one MCP sub-app per endpoint under:
  /{db_type}/{endpoint_alias}/sse|messages|mcp

Config persisted to gateway_data.json.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Dict

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from gateway_api import admin_router, user_router
from gateway_config import ConfigStore, get_store, UserMeta
from gateway_mcp_factory import create_mcp_for_endpoint


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("gateway")

_mounted_apps: Dict[str, object] = {}


def _sync_mounts(app: FastAPI, store: ConfigStore) -> None:
    global _mounted_apps

    desired: Dict[str, str] = {}
    for ep in store.list_endpoints():
        conn = store.get_connection(ep.connection_id)
        if not conn:
            continue
        path = store.get_endpoint_path(ep)
        desired[path] = ep.alias
        desired[f"/{conn.db_type}/{ep.id}"] = ep.id

    # Remove stale
    for path in [p for p in _mounted_apps if p not in desired]:
        logger.info("Unmounting: %s", path)
        app.routes = [r for r in app.routes if not _route_matches(r, path)]
        del _mounted_apps[path]

    # Add new
    for path in desired:
        if path in _mounted_apps:
            continue
        slug = path.rsplit("/", 1)[-1]
        ep = store.get_endpoint(slug) or store.get_endpoint_by_alias(slug)
        if not ep:
            continue
        conn = store.get_connection(ep.connection_id)
        if not conn:
            continue
        try:
            mcp = create_mcp_for_endpoint(conn, ep)
            sub = mcp.sse_app()
            app.mount(path, sub)
            _mounted_apps[path] = sub
            logger.info("Mounted MCP: %s (tables: %s)", path, ep.allowed_tables or "ALL")
        except Exception:
            logger.exception("Failed to mount %s", path)


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
    _sync_mounts(app, store)
    yield


app = FastAPI(title="DB-MCP-Gateway", lifespan=lifespan)
app.state.regen_routes = False

app.include_router(admin_router)
app.include_router(user_router)

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
        "endpoints_count": len(_mounted_apps) // 2 if _mounted_apps else 0,
        "docs": {
            "admin_ui": "/admin",
            "user_discovery": "/api/endpoints?token=YOUR_TOKEN",
            "mcp_sse": "/{db_type}/{endpoint_alias}/sse",
        },
    }


@app.post("/admin/reload")
async def reload_mounts():
    _sync_mounts(app, get_store())
    return {"mounted": list(_mounted_apps.keys())}


@app.middleware("http")
async def route_regen_middleware(request: Request, call_next):
    response = await call_next(request)
    if getattr(request.app.state, "regen_routes", False):
        request.app.state.regen_routes = False
        _sync_mounts(request.app, get_store())
    return response


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
