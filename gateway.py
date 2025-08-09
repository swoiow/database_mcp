from fastapi import FastAPI
from server_mysql import mcp as mysql_mcp
from server_pgsql import mcp as pgsql_mcp

app = FastAPI(title="DB-MCP-Gateway")
app.mount("/mysql", mysql_mcp.app)
app.mount("/pgsql", pgsql_mcp.app)

@app.get("/")
async def root() -> dict:
    return {"message": "DB-MCP gateway. Use /mysql or /pgsql endpoints."}
