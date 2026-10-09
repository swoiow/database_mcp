"""Per-request context (user identity + endpoint) for the gateway.

The L1 auth middleware (gateway.py) sets this for every request that hits
an MCP endpoint mount. MCP tools read it for audit / metering / rate
limiting. Standalone servers never set it -> identity is "anonymous".
"""
from __future__ import annotations

from contextvars import ContextVar
from typing import Dict


request_ctx: ContextVar[Dict[str, str]] = ContextVar("dbmcp_request_ctx", default={})


def get_ctx() -> Dict[str, str]:
    return request_ctx.get() or {}
