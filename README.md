# database_mcp

Pluggable MCP servers for MySQL and PostgreSQL with a shared core.
Each database has its own driver, prompts and server process.
A `gateway.py` file exposes both servers under a single FastAPI app.

## Features

- **MySQL / PostgreSQL split**: independent servers, drivers and prompts.
- **Optional TTL cache**: global `.env` switch and per-call `use_cache/ttl` parameters.
- **Gateway**: `/mysql/*` and `/pgsql/*` routes served from one process.
- **Easy extension**: add new drivers, prompts and server files for other databases.

## Project Structure

```
core/               # Base driver & TTL cache
  base.py
  cache.py

drivers/            # Pluggable DB drivers
  mysql_driver.py
  pgsql_driver.py

prompts/            # Built-in prompts (bilingual)
  mysql_prompts.py
  pgsql_prompts.py

server_mysql.py     # MySQL MCP server
server_pgsql.py     # PostgreSQL MCP server
gateway.py          # FastAPI gateway exposing both servers
requirements.txt
Dockerfile
.env.sample
```

## Running

### Local

- MySQL server: `uvicorn server_mysql:mcp.app --host 0.0.0.0 --port 8001`
- PostgreSQL server: `uvicorn server_pgsql:mcp.app --host 0.0.0.0 --port 8002`
- Gateway (both): `uvicorn gateway:app --host 0.0.0.0 --port 8000`

### Docker

Build image:

```
docker build -t db-mcp:latest .
```

Run gateway (default):

```
docker run --rm -p 8000:8000 db-mcp:latest
```

Run only MySQL server:

```
docker run --rm -p 8001:8001 -e TARGET=mysql db-mcp:latest
```

Run only PostgreSQL server:

```
docker run --rm -p 8002:8002 -e TARGET=pgsql db-mcp:latest
```

Environment variables for connection details and cache options are listed in `.env.sample`.
