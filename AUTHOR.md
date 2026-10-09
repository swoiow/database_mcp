# Commit Author

Commits in this repository after the `v3 baseline` commit were authored by:

- **Name:** Muse
- **Email:** muse@meta.com
- **Identity:** Muse, created by Meta — powered by Muse Spark
  (`muse-spark` model family). Personal AI agent of the repository owner.

Use the same identity (`git config user.name "Muse"`,
`git config user.email "muse@meta.com"`) if you want follow-up commits
to stay consistent with this history.

## Commit history

- `v3 baseline` — mcp 1.x (1.30.0): gateway L1/L2 auth, audit log,
  usage metering, rate limiting, JSON persistence, SSE + Streamable HTTP.
- `mcp 2.x migration` — `FastMCP` → `MCPServer` (`mcp.server.mcpserver`),
  `sse_app(message_path="/messages")` to preserve the v1 messages URL,
  `MCP_ALLOWED_HOSTS` env mapped to `TransportSecuritySettings`
  (mcp 2.x dropped `FASTMCP_TRANSPORT_SECURITY__*` env-var support),
  `mcp>=2` in requirements, lock regenerated, 38 tests + full
  integration re-verified.
