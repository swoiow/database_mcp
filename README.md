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
  run independently with `.env` config (SDK 1.27+).
- **Optional TTL cache**: per-call `use_cache/ttl` parameters.

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

drivers/
  mysql_driver.py       # MySQL driver (SQLAlchemy + aiomysql)
  pgsql_driver.py       # PostgreSQL driver (SQLAlchemy + asyncpg)

prompts/
  mysql_prompts.py      # MySQL built-in prompts (bilingual)
  pgsql_prompts.py      # PostgreSQL built-in prompts (bilingual)

static/
  admin.html            # Admin management UI

gateway_data.pkl        # Persisted config (pickle)
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
uvicorn server_mysql:mcp.sse_app --host 0.0.0.0 --port 8001
```

### Standalone PostgreSQL

```bash
uvicorn server_pgsql:mcp.sse_app --host 0.0.0.0 --port 8002
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
Check `gateway_data.pkl` or the Users tab for the token.

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
table scope info.

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
| `execute_sql`                  | Execute read-only SELECT (table references validated)          |
| `{db_type}_get_builtin_prompt` | Get built-in analysis/sql_rules/react prompt                   |

## Persistence

Config is stored in `gateway_data.pkl` (pickle format). The file is
created automatically on first write. To reset, delete the file and
restart.

## Requirements

- Python 3.12+
- mcp >= 1.27 (SDK with `FastMCP.sse_app()` / `FastMCP.streamable_http_app()`)
- fastapi, uvicorn, sqlalchemy, aiomysql, asyncpg, python-dotenv, pydantic
