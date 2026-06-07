"""DB-MCP Gateway - main entrypoint.

Manages database connections, users, ACL, and dynamically mounts
per-connection MCP servers under:
  /{db_type}/{alias}/sse   (SSE transport)
  /{db_type}/{alias}/mcp   (Streamable HTTP transport)
  /{db_type}/{id}/...      (same, by connection ID)

Supports unlimited connections. Admin UI at /admin.
Config persisted to gateway_data.json.

SDK: mcp 1.27+ with FastMCP -> .sse_app() / .streamable_http_app()
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
from gateway_mcp_factory import create_mcp_for_connection


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("gateway")

# mount_path -> Starlette sub-app
_mounted_apps: Dict[str, object] = {}


def _sync_mounts(app: FastAPI, store: ConfigStore) -> None:
    """Rebuild all MCP sub-app mounts based on current config."""
    global _mounted_apps

    desired: Dict[str, str] = {}
    for conn in store.list_connections():
        desired[f"/{conn.db_type}/{conn.alias}"] = f"{conn.db_type}:{conn.alias}"
        desired[f"/{conn.db_type}/{conn.id}"] = f"{conn.db_type}:{conn.id}"

    # Remove stale mounts
    for path in [p for p in _mounted_apps if p not in desired]:
        logger.info("Unmounting: %s", path)
        app.routes = [r for r in app.routes if not _route_matches(r, path)]
        del _mounted_apps[path]

    # Add new mounts
    for path in desired:
        if path in _mounted_apps:
            continue
        slug = path.rsplit("/", 1)[-1]
        conn = store.get_connection(slug) or store.get_connection_by_alias(slug)
        if not conn:
            continue
        try:
            mcp = create_mcp_for_connection(conn)
            sub = mcp.sse_app()
            app.mount(path, sub)
            _mounted_apps[path] = sub
            logger.info("Mounted MCP: %s", path)
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
        logger.info("Created default admin user (check gateway_data.json for token)")
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
        "mounted_connections": len(_mounted_apps) // 2 if _mounted_apps else 0,
        "endpoints": {
            "admin_ui": "/admin",
            "admin_api": "/admin/api/{resource}",
            "user_discovery": "/api/endpoints?token=YOUR_TOKEN",
            "mcp_sse": "/{db_type}/{alias}/sse",
            "mcp_messages": "/{db_type}/{alias}/messages",
            "mcp_streamable_http": "/{db_type}/{alias}/mcp",
        },
    }


@app.post("/admin/reload")
async def reload_mounts():
    store = get_store()
    _sync_mounts(app, store)
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
