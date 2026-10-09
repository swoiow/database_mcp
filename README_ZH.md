# DB-MCP Gateway

把 MySQL / PostgreSQL 以 [MCP](https://modelcontextprotocol.io/) 协议暴露给 AI Agent 的网关。
一个网关可管理多个数据库连接，每个连接可再拆出多个 MCP 端点，按用户、按表做细粒度授权。

## 功能

### 数据库接入
- 支持 MySQL 与 PostgreSQL，连接信息集中管理（新增 / 编辑 / 删除 / 批量 JSON 导入）
- 连接密码写后不可读：所有查询接口统一返回 `***`，编辑时留空即不修改

### MCP 端点
- 一个连接可暴露多个端点，每个端点独立挂载为 MCP Server
- 双传输协议同端口：SSE（`/sse` + `/messages`）与 Streamable HTTP（`/mcp`）
- 每个端点暴露 5 个工具：`get_all_schemas`（列库）、`get_tables`（列出表）、`get_table_schema`（表结构）、`execute_sql`（只读查询）、内置 prompt

### 两级权限
- **L1 用户鉴权**：每个用户持有独立 token，MCP 请求必须携带（`Authorization: Bearer` 头或 `?token=` 参数）；无 token / 未授权一律 403
- **L2 表级 scope**：每个端点可配置表白名单（`table` 或 `schema.table`），基于 SQL AST 解析校验，引号表名、字符串里的关键字不会误杀；越权查询直接拒绝并记入审计

### 只读 SQL 守卫
- `execute_sql` 仅允许纯 SELECT；基于 sqlglot AST 拦截带修改语义的 CTE、`SELECT INTO`、`INTO OUTFILE`、堆叠语句等绕过手法

### 管理后台
- 可视化管理连接 / 端点 / 用户 / ACL（含批量分配），支持搜索、过滤、排序、分页
- 深色模式；操作均有二次确认，失败会明确提示原因

### 可观测性
- **审计日志**：每次工具调用记一条 JSONL（用户、端点、SQL 哈希与预览、涉及表、行数、耗时、错误）
- **用量计量**：按用户 / 端点 / 工具聚合调用数、行数、错误数、延迟，`GET /admin/api/metering` 查询，落盘不丢失

### 运行时保护
- 限流（默认每用户每端点 120 次/分钟）、查询超时（默认 30 秒）、`max_rows` 服务端硬上限（默认 10000），均为环境变量可调
- 查询缓存：工具调用支持 `use_cache` / `ttl` 参数
- Admin API 支持 `ADMIN_TOKEN` 鉴权；公网部署可通过 `MCP_ALLOWED_HOSTS` 配置 SDK 的 DNS rebinding 保护白名单

### 工程化
- 配置 JSON 持久化（原子写入 + 线程锁），旧版 pickle 自动迁移备份
- 38 个单元测试，GitHub Actions 在 Python 3.14 / Ubuntu 自动运行

## 快速开始

```bash
pip install -r requirements.lock
export ADMIN_TOKEN=<你的管理员令牌>
python gateway.py          # 管理后台：http://127.0.0.1:8000/admin
```

在管理后台依次创建：连接 → 端点 → 用户 → ACL 授权，然后把端点的 MCP 路径和用户令牌配给 Agent 即可。

更详细的部署与环境变量说明见英文 [README.md](README.md)。
