FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
RUN apt-get update && apt-get install -y build-essential default-libmysqlclient-dev && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY core core
COPY drivers drivers
COPY prompts prompts
COPY server_mysql.py server_pgsql.py gateway.py ./

# 默认环境变量（按需覆盖）
ENV DBMCP_CACHE_ENABLED="false" \
    DBMCP_CACHE_TTL="60" \
    MYSQL_HOST="localhost" MYSQL_PORT="3306" MYSQL_USER="" MYSQL_PASSWORD="" MYSQL_DB="" \
    PG_HOST="localhost" PG_PORT="5432" PG_USER="" PG_PASSWORD="" PG_DB=""

EXPOSE 8000 8001 8002

# 通过环境变量选择启动哪个 Server（mysql|pgsql|gateway）
ARG TARGET=gateway
ENV TARGET=${TARGET}

CMD [ "bash", "-lc", "if [ \"$TARGET\" = \"mysql\" ]; then uvicorn server_mysql:mcp.app --host 0.0.0.0 --port 8001; elif [ \"$TARGET\" = \"pgsql\" ]; then uvicorn server_pgsql:mcp.app --host 0.0.0.0 --port 8002; else uvicorn gateway:app --host 0.0.0.0 --port 8000; fi" ]
