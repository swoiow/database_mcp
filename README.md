# DB-MCP Gateway

A multi-database MCP gateway with table-level access control.

Manage MySQL and PostgreSQL connections through a web admin UI, create
scoped MCP endpoints, and assign users via token-based ACL.

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    DB-MCP Gateway                       │
│                                                         │
│  Connection ─── credentials (admin, all tables)         │
│       │                                                 │
│       ├── Endpoint ─── MCP exposure + table scope       │
│       │     e.g. /mysql/orders-api (tables: orders,     │
│       │           order_items)                          │
│       │                                                 │
│       └── Endpoint ─── MCP exposure (all tables)        │
│             e.g. /mysql/full-access                     │
│                                                         │
│  ACL: User ──► Endpoint                                 │
│  (token-based, one user can access multiple endpoints)  │
│                                                         │
│  Admin UI: /admin                                       │
│  User discovery: /api/endpoints?token=xxx               │
└─────────────────────────────────────────────────────────┘
```

Three-layer model:

- **Connection** — database credentials. Admin-level, no restrictions.
- **Endpoint** — MCP exposure layer. References one connection. Optionally
  restricts which tables are visible via `allowed_tables` whitelist.
  Empty whitelist = all tables.
- **ACL** — binds a user to an endpoint. Users authenticate by token.

One connection can back multiple endpoints with different table scopes.

## Permission model (gateway-owned)

The gateway holds the admin DB credentials and executes every query
itself. Permissions are enforced at two levels, both in the gateway:

- **L1 — endpoint access**: every request to an MCP endpoint
  (`/{db_type}/{alias}/sse|/messages|/mcp`) must carry a user token,
  either as `Authorization: Bearer <token>` (preferred) or `?token=`.
  The gateway checks the token → endpoint ACL; no grant → 403.
- **L2 — table scope**: per tool call, referenced tables are extracted
  from the SQL AST (sqlglot) and checked against the endpoint's
  `allowed_tables`; `execute_sql` additionally rejects anything that is
  not a single pure `SELECT` (blocks data-modifying CTEs,
  `SELECT INTO`, `INTO OUTFILE`, stacked queries).

## Features

- **Multi-database**: unlimited MySQL and PostgreSQL connections.
- **Table-level scoping**: each endpoint can whitelist specific tables.
  `get_all_schemas` / `get_tables` filter results; `get_table_schema`
  blocks unauthorized tables; `execute_sql` validates table references.
- **Web admin UI**: light/dark theme, pagination, search, batch import,
  bulk ACL assignment. Accessible at `/admin`.
- **Token-based user auth**: each user gets a hex token. Users query
  `/api/endpoints?token=xxx` to discover their available MCP servers.
- **Dynamic mounting**: endpoints are mounted/unmounted on config change
  without restart.
- **Standalone servers**: `server_mysql.py` and `server_pgsql.py` can
  run independently with `.env` config (SDK 1.27+, v1 API).
- **Optional TTL cache**: per-call `use_cache/ttl` parameters (gateway
  and standalone).
- **Two transports**: SSE (`/sse`, `/messages`) and Streamable HTTP
  (`/mcp`) mounted per endpoint.
- **Audit log**: every MCP tool call appended as JSONL (`audit.log`).
- **Usage metering**: per user / endpoint / tool counters
  (`GET /admin/api/metering`, persisted to `metering.json`).
- **Runtime guards**: query timeout, server-side `max_rows` cap,
  per-(user, endpoint) rate limiting (all env-configurable).
- **AST read-only guard**: sqlglot-based, blocks data-modifying CTEs,
  `SELECT INTO`, `INTO OUTFILE`, stacked queries.

## Project Structure

```
gateway.py              # FastAPI gateway entrypoint
gateway_config.py       # Models + pickle persistence
gateway_api.py          # Admin + user API routes
gateway_mcp_factory.py  # Dynamic per-endpoint MCP server creation

server_mysql.py         # Standalone MySQL MCP server (SDK 1.27+)
server_pgsql.py         # Standalone PostgreSQL MCP server (SDK 1.27+)

core/
  base.py               # Abstract driver interface
  cache.py              # In-process TTL cache
  sqlguard.py           # AST read-only guard + table extraction (sqlglot)
  engines.py            # Process-wide async engine cache
  ctx.py                # Per-request context (L1 identity for tools)
  audit.py              # JSONL query audit log
  metering.py           # Usage counters (persisted JSON)
  ratelimit.py          # Sliding-window rate limiter

tests/                  # pytest: guard, scope, persistence, auth, metering

drivers/
  mysql_driver.py       # MySQL driver (SQLAlchemy + aiomysql)
  pgsql_driver.py       # PostgreSQL driver (SQLAlchemy + asyncpg)

prompts/
  mysql_prompts.py      # MySQL built-in prompts (bilingual)
  pgsql_prompts.py      # PostgreSQL built-in prompts (bilingual)

static/
  admin.html            # Admin management UI

gateway_data.json       # Persisted config (JSON; legacy .pkl auto-migrated)
requirements.txt
Dockerfile
.env.sample
```

## Quick Start

### Gateway mode

```bash
pip install -r requirements.txt
uvicorn gateway:app --host 0.0.0.0 --port 8000
```

Open `http://localhost:8000/admin` to configure connections, endpoints,
users, and ACL.

### Standalone MySQL

```bash
uvicorn server_mysql:app --host 0.0.0.0 --port 8001
```

### Standalone PostgreSQL

```bash
uvicorn server_pgsql:app --host 0.0.0.0 --port 8002
```

Standalone servers read connection info from `.env` (see `.env.sample`).

### Docker

```bash
docker build -t db-mcp:latest .

# Gateway (default)
docker run --rm -p 8000:8000 db-mcp:latest

# MySQL only
docker run --rm -p 8001:8001 -e TARGET=mysql db-mcp:latest

# PostgreSQL only
docker run --rm -p 8002:8002 -e TARGET=pgsql db-mcp:latest
```

## Admin UI

Visit `/admin`. The UI has four tabs:

1. **Connections** — add/edit/delete database connections. Batch import
   via JSON array. Search and filter by type.
2. **Endpoints** — create MCP endpoints backed by a connection. Set
   `allowed_tables` to restrict table access (one per line, supports
   `table`, `db.table`, `schema.table`). Leave empty for all tables.
3. **Users** — create users, view/regenerate tokens.
4. **ACL** — assign endpoints to users. Bulk assign supported.

First startup creates a default `admin` user automatically.
Check the Users tab for the token.

### Securing the admin API

Set `ADMIN_TOKEN` before starting the gateway:

```bash
export ADMIN_TOKEN="a-long-random-string"
uvicorn gateway:app --host 0.0.0.0 --port 8000
```

When set, every `/admin/api/*` call must carry
`Authorization: Bearer <ADMIN_TOKEN>` (the bundled admin UI has a token
box in the top bar and stores it in `localStorage`). Without it the
server logs a warning and leaves the admin API open (dev only).

Connection passwords are write-only: accepted on create/update, but
read APIs always return `"password": "***"`. To change a password, edit
the connection and type the new one (blank = keep unchanged).

## MCP Endpoints

Each endpoint exposes SSE and Streamable HTTP transports:

```
SSE:              /{db_type}/{endpoint_alias}/sse
SSE messages:     /{db_type}/{endpoint_alias}/messages
Streamable HTTP:  /{db_type}/{endpoint_alias}/mcp

Also accessible by endpoint ID:
SSE:              /{db_type}/{endpoint_id}/sse
```

### User endpoint discovery

```
GET /api/endpoints?token=USER_TOKEN
```

Returns the list of endpoints the user can access, with MCP paths and
table scope info. Every MCP request to those paths must then carry the
token as `Authorization: Bearer <token>` (or `?token=`).

### Table scoping example

Connection `prod-mysql` points to a MySQL instance with full access.
You create three endpoints:

| Endpoint Alias | Connection | allowed_tables           | Use case       |
|----------------|------------|--------------------------|----------------|
| `orders-api`   | prod-mysql | `orders`, `order_items`  | Order service  |
| `users-api`    | prod-mysql | `users`, `user_profiles` | User service   |
| `full-access`  | prod-mysql | *(empty = all)*          | Internal admin |

Assign `orders-api` to user A, `users-api` to user B, `full-access`
to user C. Each gets their own MCP server URL.

## API Reference

### Admin API (`/admin/api`)

| Method | Path                      | Description                   |
|--------|---------------------------|-------------------------------|
| GET    | `/connections`            | List connections              |
| POST   | `/connections`            | Create connection             |
| PATCH  | `/connections/{id}`       | Update connection             |
| DELETE | `/connections/{id}`       | Delete connection (+ cascade) |
| POST   | `/connections/batch`      | Batch create                  |
| GET    | `/endpoints`              | List endpoints                |
| POST   | `/endpoints`              | Create endpoint               |
| PATCH  | `/endpoints/{id}`         | Update endpoint               |
| DELETE | `/endpoints/{id}`         | Delete endpoint (+ cascade)   |
| GET    | `/users`                  | List users                    |
| POST   | `/users`                  | Create user                   |
| DELETE | `/users/{id}`             | Delete user (+ cascade)       |
| POST   | `/users/{id}/regen-token` | Regenerate token              |
| GET    | `/acl`                    | List ACL rules                |
| POST   | `/acl`                    | Create ACL rule               |
| POST   | `/acl/batch`              | Batch create ACL              |
| DELETE | `/acl/{id}`               | Delete ACL rule               |
| GET    | `/metering`               | Usage counters (users/endpoints/tools) |
| POST   | `/admin/reload`           | Resync MCP mounts             |

### User API (`/api`)

| Method | Path                   | Description                   |
|--------|------------------------|-------------------------------|
| GET    | `/endpoints?token=xxx` | Discover accessible endpoints |

## MCP Tools

Each endpoint exposes these tools (names vary by db_type):

| Tool                           | Description                                                    |
|--------------------------------|----------------------------------------------------------------|
| `get_all_schemas`              | List databases/schemas with tables/columns (filtered by scope) |
| `get_tables`                   | List tables (filtered by scope)                                |
| `get_table_schema`             | Describe a table (blocked if outside scope)                    |
| `execute_sql`                  | Execute read-only SELECT (AST-validated, table references checked) |
| `{db_type}_get_builtin_prompt` | Get built-in analysis/sql_rules/react prompt                   |

## Persistence

Config is stored in `gateway_data.json` (JSON, atomic writes). A legacy
`gateway_data.pkl` is auto-migrated on first load (backed up as
`gateway_data.pkl.bak`). To reset, delete the JSON file and restart.

## Observability

- **Audit**: `audit.log` (JSONL) — one record per MCP tool call:
  timestamp, user, endpoint, tool, SQL hash + preview, tables, rows,
  duration, cache hit, error. Set `DBMCP_AUDIT_PATH` to relocate.
- **Metering**: `GET /admin/api/metering` returns aggregated
  calls/rows/errors/latency per user, endpoint and tool (persisted to
  `metering.json`, `DBMCP_METERING_PATH` to relocate).

## Runtime guards

| Env var | Default | Meaning |
|---------|---------|---------|
| `DBMCP_QUERY_TIMEOUT` | `30` | tool-call timeout, seconds |
| `DBMCP_MAX_ROWS` | `10000` | hard server-side cap for `execute_sql` `max_rows` |
| `DBMCP_RATE_LIMIT_PER_MIN` | `120` | tool calls per (user, endpoint) per minute |
| `MCP_ALLOWED_HOSTS` | *(empty)* | comma-separated `host:port` allow-list for SDK DNS-rebinding protection (public-domain deployments) |

> **Production note (Streamable HTTP):** the MCP SDK enables DNS-rebinding
> protection by default and only allows `localhost` / `127.0.0.1` / `[::1]`
> (with port). If you serve the gateway on a public domain, extend the
> allow-list via env (mcp 2.x no longer reads
> `FASTMCP_TRANSPORT_SECURITY__*` env vars, so the gateway maps it for you):
> `MCP_ALLOWED_HOSTS="db.example.com:*"`.
> SSE transport is unaffected.

## Tests

```bash
pip install -r requirements.lock pytest
pytest tests/ -q
```

Covers: read-only SQL guard + table extraction, L2 table scope,
JSON persistence + pickle migration, admin auth + password masking,
metering, rate limiter, engine fingerprint.

## Requirements

- Python 3.12+
- mcp >= 2 (v2 API with `MCPServer`; v1's `FastMCP` was renamed)
- fastapi, uvicorn, sqlalchemy, aiomysql, asyncpg, python-dotenv,
  pydantic, sqlglot
- Pinned lock file: `requirements.lock` (`pip install -r requirements.lock`)
