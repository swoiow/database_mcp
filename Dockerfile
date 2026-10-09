FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
RUN apt-get update && apt-get install -y build-essential default-libmysqlclient-dev && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# NOTE: gateway mode needs gateway_*.py + static/ too (previously missing
# -> ImportError on startup).
COPY core core
COPY drivers drivers
COPY prompts prompts
COPY static static
COPY gateway.py gateway_api.py gateway_config.py gateway_mcp_factory.py ./
COPY server_mysql.py server_pgsql.py ./

# 默认环境变量（按需覆盖）
# ADMIN_TOKEN: 设置后 /admin/api 需要 Authorization: Bearer <token>；不设置则无保护（启动时告警）
ENV ADMIN_TOKEN="" \
    DBMCP_CACHE_ENABLED="false" \
    DBMCP_CACHE_TTL="60" \
    MYSQL_HOST="localhost" MYSQL_PORT="3306" MYSQL_USER="" MYSQL_PASSWORD="" MYSQL_DB="" \
    PG_HOST="localhost" PG_PORT="5432" PG_USER="" PG_PASSWORD="" PG_DB=""

EXPOSE 8000 8001 8002

# 通过环境变量选择启动哪个 Server（mysql|pgsql|gateway）
# NOTE: standalone 入口是模块级 `app = mcp.sse_app()`（FastMCP 没有 `.app` 属性，
# 旧写法 server_mysql:mcp.app 启动即 AttributeError）。
ARG TARGET=gateway
ENV TARGET=${TARGET}

CMD [ "bash", "-lc", "if [ \"$TARGET\" = \"mysql\" ]; then uvicorn server_mysql:app --host 0.0.0.0 --port 8001; elif [ \"$TARGET\" = \"pgsql\" ]; then uvicorn server_pgsql:app --host 0.0.0.0 --port 8002; else uvicorn gateway:app --host 0.0.0.0 --port 8000; fi" ]
