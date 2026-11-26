# DFM_MCP_mysql_service（DFM-MCP-MySQL）

MySQL 只读查询 MCP 服务：保存连接列表 → 查库 / 查表 / 查表结构 / 执行 SELECT。
与 `DFM_MCP_mail_service`、`DFM_MCP_device_service` 同结构，可上传到 DFM 前端「MCP 服务」页安装。

## 安装

把 `manifest.json`、`server.py`、`requirements.txt`、`README.md` 打包为 zip 后在「MCP 服务」页上传：

```powershell
Compress-Archive -Path manifest.json, server.py, requirements.txt, README.md -DestinationPath ..\DFM_MCP_mysql_service.zip -Force
```

依赖：`mcp==2.3.0`、`pymysql==1.1.1`（纯 Python，无编译依赖），Python ≥ 3.10。

## 工具（6 个）

所有工具返回统一信封 `{code, message, data}`：200 成功、400 参数/安全拒绝、404 连接 id 不存在、500 MySQL 错误、-1 无法连接或配置异常。

| 工具 | 参数 | 返回 data |
|---|---|---|
| `add_mysql_connection` | `id, host, user, password, port=3306, name="", database="", charset=utf8mb4` | 新增连接的公开字段（**不含密码**） |
| `list_mysql_connections` | 无 | `{connections: [...], total}`，不含密码 |
| `list_databases` | `connectionId` | 库名数组（SHOW DATABASES 原样，含系统库） |
| `list_tables` | `connectionId, database=""` | `{database, tables, tableCount}` |
| `execute_select` | `connectionId, sql, database="", maxRows=200` | `{columns, rows, rowCount, truncated, maxRows}` |
| `describe_table` | `connectionId, table, database=""` | `{database, table, columns, columnCount}`，字段含 field/type/nullable/key/default/extra/comment/charset/collation |

## 使用流程

1. `add_mysql_connection` 保存连接（**保存时不校验连通性**，数据库离线时也能先登记）。
2. `list_mysql_connections` 拿到 `id`，后续所有接口用 `connectionId` 引用，不必重复传账号密码。
3. `list_databases` → `list_tables(database=...)` → `describe_table(table=...)` → `execute_select(sql=...)`。
   `database` 不传时用连接配置里的默认库；传了则 `USE` 该库。

## 安全约束（重要）

- `execute_select` **只接受单条 SELECT**：拒绝分号堆叠、`--` / `#` / `/* */` 注释、非 SELECT 开头的语句、`INTO OUTFILE` / `INTO DUMPFILE`。PyMySQL 连接不启用 MULTI_STATEMENTS，服务端再兜一层。
- 合法 SQL 中若字符串字面量恰含注释符（如 `SELECT 'a--b'`）会被误拒，改写 SQL 规避；此误伤为安全优先的取舍。
- 以 `WITH` 开头的 CTE、`SHOW` / `EXPLAIN` 等非 SELECT 起始语句同样被拒绝（保守取舍，语句形态校验不解析 SQL 语法）。
- 每次调用独立建连、用完关闭；SELECT 走 `SSDictCursor` 流式游标 + `fetchmany(maxRows+1)` 截断，默认 200 行、上限 1000 行，超出返回 `truncated: true`。不会改写用户 SQL 加 LIMIT。
- `datetime` / `date` / `time` / `timedelta` / `Decimal` 转成字符串，`bytes` 优先 UTF-8 解码、失败回退 hex 字符串。
- **建议给 MCP 配只读账号**：静态校验只防语句形态，挡不住 SELECT 内调用写库存储函数、`LOAD_FILE()` 读服务器文件、`FOR UPDATE` 加锁等；权限应由数据库账号兜底。
- **密码明文**保存在 `config/connections.json`（与 `DFM_MCP_mail_service` 的 mailboxes.json 一致），该文件已被项目 `.gitignore` 忽略，仅限本机使用。风险等级：中。

## 边界与已知限制

- 连接超时 5s、读写超时 30s；认证失败 / 端口不可达 / 无权限均返回 `code=500` 或 `-1` 错误信封，不崩溃。
- `list_databases` 不过滤 `information_schema` / `mysql` / `performance_schema` / `sys`。
- 未指定 `database` 且连接无默认库时，`list_tables` 返回 MySQL 的 No database selected 错误。
- 配置格式：`config/connections.json` 为数组，元素含 `id/name/host/port/user/password/database/charset`；手工编辑后根节点必须是数组。
- 按 YAGNI **未实现**：删除/修改连接、保存时测试连通性、`SHOW CREATE TABLE` 级 DDL/索引、系统库过滤、密码加密。

## 运行

```bash
python server.py              # stdio（Claude Desktop / Cursor 等）
python server.py --port 3010  # Streamable HTTP（MCP 管理工具拉起）
```

带 `--port` 时每 10s 向 `DFM_MCP_MANAGER_URL`（默认 http://localhost:3000）POST /api/mcp/heartbeat 上报 name/version/mcpVersion/port，管理端未启动静默跳过。
