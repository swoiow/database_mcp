# E2E tests

End-to-end tests against **real** MySQL / PostgreSQL. They exercise the
full stack: admin API → endpoint mount → MCP streamable HTTP
(initialize → tools/list → tools/call) → real query results.

## Run locally

```bash
docker compose -f .github/docker-compose.e2e.yml up -d --wait
pytest tests/e2e/ -q
docker compose -f .github/docker-compose.e2e.yml down -v
```

Passwords are plaintext inside the compose file on purpose — throwaway
test data only.

## Without docker

The tests probe `127.0.0.1:3306` / `127.0.0.1:5432` first and **skip**
cleanly when a database is not reachable, so plain `pytest tests/`
still passes. Because containers need time to start (image pull,
entrypoint initdb scripts), each probe retries until the database
accepts connections, up to `E2E_WAIT_TIMEOUT` seconds (default 90).
Connection details can be overridden:

```
E2E_MYSQL_HOST / E2E_MYSQL_PORT / E2E_MYSQL_USER / E2E_MYSQL_PASSWORD / E2E_MYSQL_DB
E2E_PG_HOST    / E2E_PG_PORT    / E2E_PG_USER    / E2E_PG_PASSWORD    / E2E_PG_DB
E2E_WAIT_TIMEOUT   # seconds to wait for a DB to become reachable (default 90)
```

## Layout

- `initdb/mysql/init.sql`, `initdb/postgres/init.sql` — seed schema + data,
  mounted into `/docker-entrypoint-initdb.d/` by the compose file
- `conftest.py` — DB reachability probes, gateway TestClient fixture
  (isolated `gateway_data.json` / audit / metering in a tmp dir),
  and a `Gateway` helper (admin API + MCP calls)
- `test_e2e_core.py` — core flows: seeded SELECT, L2 scope denial,
  read-only guard, L1 auth, tools list
