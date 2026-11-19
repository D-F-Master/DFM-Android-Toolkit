"""
MySQL MCP 服务端实现，暴露六个工具：
- add_mysql_connection：新增并持久化 MySQL 连接配置
- list_mysql_connections：列出已保存的连接（不含密码）
- list_databases：返回连接中的所有库
- list_tables：返回指定库中的全部表
- execute_select：执行只读 SELECT 并返回数据（行数上限）
- describe_table：返回指定表的字段结构

配置持久化到 config/connections.json（密码明文，仅限本机使用）。
每次工具调用独立建连、用完即关，无会话状态；SELECT 走流式游标，避免大结果集一次性入内存。
安全约束：execute_select 只接受单条 SELECT，拒绝分号堆叠、注释与 INTO OUTFILE/DUMPFILE；
建议为 MCP 配置只读账号。

使用官方 MCP SDK 2.x (MCPServer)。默认 stdio 传输；
带 --port 参数启动时使用 Streamable HTTP（供 DFM MCP 管理工具拉起，端点 http://127.0.0.1:<port>/mcp）。
"""
import argparse
import json
import os
import re
import sys
import threading
import time
import urllib.request
from datetime import date, datetime, time as dtime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pymysql
from pymysql.cursors import SSDictCursor

from mcp.server.mcpserver import MCPServer

try:
    from importlib.metadata import version as pkg_version
except ImportError:
    from importlib_metadata import version as pkg_version

# ========== 心跳上报（DFM MCP 管理工具） ==========
SERVER_NAME = "DFM_MCP_mysql_service"   # 与 manifest.json 的 name 保持一致
SERVER_VERSION = "0.1.0"                 # 与 manifest.json 的 version 保持一致
MANAGER_URL = os.environ.get("DFM_MCP_MANAGER_URL", "http://localhost:3000")

# ========== 连接与结果集边界 ==========
CONNECT_TIMEOUT = 5      # TCP 连接超时（秒）
READ_TIMEOUT = 30        # 读/写超时（秒）
MAX_ROWS_DEFAULT = 200   # SELECT 默认返回行数上限
MAX_ROWS_LIMIT = 1000    # SELECT 允许的最大行数上限


def _report_heartbeat(port: int):
    """每 10 秒向管理端上报一次心跳，携带版本、mcp SDK 版本与端口"""
    def loop():
        while True:
            try:
                payload = json.dumps({
                    "name": SERVER_NAME,
                    "version": SERVER_VERSION,
                    "mcpVersion": pkg_version("mcp"),
                    "port": port,
                }).encode("utf-8")
                req = urllib.request.Request(
                    MANAGER_URL + "/api/mcp/heartbeat",
                    data=payload,
                    headers={"Content-Type": "application/json"},
                )
                urllib.request.urlopen(req, timeout=3)
            except Exception:
                pass  # 管理端未启动时静默跳过
            time.sleep(10)

    threading.Thread(target=loop, daemon=True).start()


# ========== 连接配置持久化（参考 DFM_MCP_mail_service 的 config/*.json 风格） ==========

def _base_dir() -> Path:
    """PyInstaller 冻结后 __file__ 指向临时解压目录，改用 exe 所在目录保证配置持久"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent


CONFIG_DIR = _base_dir() / "config"
CONFIG_FILE = CONFIG_DIR / "connections.json"
CONFIG_DIR.mkdir(parents=True, exist_ok=True)
_config_lock = threading.Lock()   # 防并发 add 覆盖配置文件

_PUBLIC_KEYS = ("id", "name", "host", "port", "user", "database", "charset")


def _read_items_unlocked() -> list[dict[str, Any]]:
    if not CONFIG_FILE.exists():
        return []
    with CONFIG_FILE.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError("config/connections.json 根节点必须是数组")
    return data


def _write_items_unlocked(items: list[dict[str, Any]]):
    with CONFIG_FILE.open("w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


def list_connections() -> list[dict[str, Any]]:
    """列出连接配置，剔除 password"""
    with _config_lock:
        items = _read_items_unlocked()
    return [{k: item.get(k) for k in _PUBLIC_KEYS} for item in items]


def add_connection(cfg: dict[str, Any]) -> bool:
    """新增连接；id 已存在返回 False"""
    with _config_lock:
        items = _read_items_unlocked()
        if any(item.get("id") == cfg["id"] for item in items):
            return False
        items.append(cfg)
        _write_items_unlocked(items)
    return True


def get_connection(connection_id: str) -> dict[str, Any] | None:
    with _config_lock:
        items = _read_items_unlocked()
    for item in items:
        if item.get("id") == connection_id:
            return item
    return None


# ========== 公共校验与转换 ==========

def _guard(**fields) -> dict[str, Any] | None:
    """必填字符串非空校验：任一为空返回 code=400 错误信封，全部通过返回 None"""
    for name, value in fields.items():
        if not isinstance(value, str) or not value.strip():
            return {"code": 400, "message": f"参数错误：{name} 不能为空", "data": None}
    return None


def _quote_identifier(name: str) -> str:
    """库/表名加反引号并转义内部反引号；拒绝空值与含控制字符的名字"""
    if not isinstance(name, str) or not name.strip():
        raise ValueError("库名/表名不能为空")
    n = name.strip()
    if "\x00" in n or "\n" in n or "\r" in n:
        raise ValueError("库名/表名包含非法控制字符")
    return "`" + n.replace("`", "``") + "`"


def _jsonable(v: Any) -> Any:
    """把 datetime/Decimal/bytes 等 MySQL 类型转成可 JSON 序列化的值"""
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    if isinstance(v, (datetime, date, dtime)):
        return v.isoformat()
    if isinstance(v, timedelta):
        return str(v)
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, (bytes, bytearray)):
        b = bytes(v)
        try:
            return b.decode("utf-8")
        except UnicodeDecodeError:
            return b.hex()
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if isinstance(v, dict):
        return {str(k): _jsonable(x) for k, x in v.items()}
    return str(v)


def _validate_readonly_select(sql: str) -> tuple[bool, str]:
    """只读 SELECT 静态校验：返回 (是否通过, 规范化 SQL 或错误信息)"""
    if not isinstance(sql, str) or not sql.strip():
        return False, "参数错误：sql 不能为空"
    s = sql.strip()
    if s.endswith(";"):
        s = s[:-1].rstrip()
    if ";" in s:
        return False, "安全限制：只允许单条 SELECT 语句，检测到分号堆叠"
    if "--" in s or "#" in s or "/*" in s or "*/" in s:
        return False, "安全限制：SELECT 语句不允许包含注释（-- / # / /* */）"
    parts = s.split(None, 1)
    first = parts[0].upper() if parts else ""
    if first != "SELECT":
        return False, f"安全限制：只允许 SELECT 语句开头，收到 {first!r}"
    if re.search(r"\bINTO\s+(OUTFILE|DUMPFILE)\b", s, re.IGNORECASE):
        return False, "安全限制：SELECT 不允许写入服务器文件（INTO OUTFILE / DUMPFILE）"
    return True, s


def _db_call(connection_id: str, database: str, action) -> dict[str, Any]:
    """取配置 → 独立建连 → 执行 action(cursor) → 关闭连接；统一兜底异常为错误信封"""
    err = _guard(connectionId=connection_id)
    if err:
        return err
    cfg = get_connection(connection_id)
    if cfg is None:
        return {"code": 404, "message": f"未找到连接配置: {connection_id}，请先调用 add_mysql_connection", "data": None}
    conn = None
    try:
        conn = pymysql.connect(
            host=cfg.get("host", ""),
            port=int(cfg.get("port", 3306)),
            user=cfg.get("user", ""),
            password=cfg.get("password", ""),
            database=cfg.get("database") or None,
            charset=cfg.get("charset") or "utf8mb4",
            connect_timeout=CONNECT_TIMEOUT,
            read_timeout=READ_TIMEOUT,
            write_timeout=READ_TIMEOUT,
            cursorclass=SSDictCursor,   # 流式游标，fetchmany 截断后丢弃剩余行
        )
        with conn.cursor() as cur:
            if database:
                cur.execute("USE " + _quote_identifier(database))
            data = action(cur)
        return {"code": 200, "message": "success", "data": data}
    except ValueError as e:
        return {"code": 400, "message": f"参数错误：{e}", "data": None}
    except pymysql.Error as e:
        return {"code": 500, "message": f"MySQL 错误：{e}", "data": None}
    except OSError as e:
        return {"code": -1, "message": f"无法连接 MySQL：{e}", "data": None}
    except Exception as e:
        return {"code": -1, "message": f"查询失败：{e}", "data": None}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _build_server() -> MCPServer:
    """构建并注册全部工具的 MCPServer 实例"""
    server = MCPServer(
        name="DFM-MCP-MySQL",
        version=SERVER_VERSION,
        instructions="MySQL 只读查询 MCP 服务：先 add_mysql_connection 保存连接，再用 connectionId 查库/表/表结构与执行 SELECT。"
                     "所有工具返回统一信封 {code,message,data}，code=200 成功，404=连接 id 不存在，500=MySQL 错误。"
                     "密码明文保存在 config/connections.json，仅限本机使用；建议配置只读账号。",
    )

    # --- 工具 1：保存连接 ---
    @server.tool(name="add_mysql_connection", title="新增 MySQL 连接",
                 description="保存 MySQL 连接配置（ip/端口/用户名/密码）到连接列表并持久化；后续接口用 id 引用。保存时不校验连通性")
    def _add_mysql_connection(
        id: str,
        host: str,
        user: str,
        password: str,
        port: int = 3306,
        name: str = "",
        database: str = "",
        charset: str = "utf8mb4",
    ) -> dict[str, Any]:
        err = _guard(id=id, host=host, user=user)
        if err:
            return err
        if not isinstance(password, str):
            return {"code": 400, "message": "参数错误：password 必须是字符串", "data": None}
        if not isinstance(port, int) or isinstance(port, bool) or port < 1 or port > 65535:
            return {"code": 400, "message": f"参数错误：port 必须是 1-65535 的整数，收到 {port!r}", "data": None}
        cfg = {
            "id": id.strip(),
            "name": (name.strip() or id.strip()),
            "host": host.strip(),
            "port": port,
            "user": user.strip(),
            "password": password,
            "database": (database or "").strip(),
            "charset": (charset or "utf8mb4").strip() or "utf8mb4",
        }
        try:
            ok = add_connection(cfg)
        except (OSError, ValueError, json.JSONDecodeError) as e:
            return {"code": 500, "message": f"保存连接配置失败：{e}", "data": None}
        if not ok:
            return {"code": 400, "message": f"连接 id 已存在，无法重复添加：{cfg['id']}", "data": None}
        return {"code": 200, "message": "success", "data": {k: cfg[k] for k in _PUBLIC_KEYS}}

    # --- 工具 2：列出连接（配套需求 1 的「方便后续调用」） ---
    @server.tool(name="list_mysql_connections", title="列出 MySQL 连接",
                 description="列出已保存的 MySQL 连接（不含密码），返回 id 供后续接口引用")
    def _list_mysql_connections() -> dict[str, Any]:
        try:
            items = list_connections()
        except (OSError, ValueError, json.JSONDecodeError) as e:
            return {"code": 500, "message": f"读取连接配置失败：{e}", "data": None}
        return {"code": 200, "message": "success", "data": {"connections": items, "total": len(items)}}

    # --- 工具 3：所有库 ---
    @server.tool(name="list_databases", title="列出所有库",
                 description="返回 MySQL 连接中的所有数据库（SHOW DATABASES 原样返回，含 information_schema 等系统库，不做过滤）")
    def _list_databases(connectionId: str) -> dict[str, Any]:
        def action(cur):
            cur.execute("SHOW DATABASES")
            return [r["Database"] for r in cur.fetchall()]
        return _db_call(connectionId, "", action)

    # --- 工具 4：指定库的全部表 ---
    @server.tool(name="list_tables", title="列出库中的表",
                 description="返回指定库中的全部表名；database 为空时使用连接配置里的默认库（未指定库会返回 MySQL 错误）")
    def _list_tables(connectionId: str, database: str = "") -> dict[str, Any]:
        def action(cur):
            if database:
                cur.execute("SHOW TABLES FROM " + _quote_identifier(database))
                db = database
            else:
                cur.execute("SELECT DATABASE() AS db")
                db = cur.fetchone()["db"]
                cur.execute("SHOW TABLES")
            tables = [list(r.values())[0] for r in cur.fetchall()]
            return {"database": db, "tables": tables, "tableCount": len(tables)}
        return _db_call(connectionId, database, action)

    # --- 工具 5：只读 SELECT ---
    @server.tool(name="execute_select", title="执行 SELECT 查询",
                 description="执行单条只读 SELECT 并返回数据（默认最多 200 行，上限 1000）；拒绝写语句、多语句、注释与 INTO OUTFILE/DUMPFILE；database 可覆盖连接默认库")
    def _execute_select(
        connectionId: str,
        sql: str,
        database: str = "",
        maxRows: int = MAX_ROWS_DEFAULT,
    ) -> dict[str, Any]:
        ok, checked = _validate_readonly_select(sql)
        if not ok:
            return {"code": 400, "message": checked, "data": None}
        if not isinstance(maxRows, int) or isinstance(maxRows, bool) or maxRows < 1 or maxRows > MAX_ROWS_LIMIT:
            return {"code": 400, "message": f"参数错误：maxRows 必须是 1-{MAX_ROWS_LIMIT} 的整数，收到 {maxRows!r}", "data": None}

        def action(cur):
            cur.execute(checked)
            columns = [d[0] for d in (cur.description or [])]
            raw = cur.fetchmany(maxRows + 1)
            truncated = len(raw) > maxRows
            rows = [{k: _jsonable(v) for k, v in r.items()} for r in raw[:maxRows]]
            return {"columns": columns, "rows": rows, "rowCount": len(rows),
                    "truncated": truncated, "maxRows": maxRows}
        return _db_call(connectionId, database, action)

    # --- 工具 6：表结构 ---
    @server.tool(name="describe_table", title="获取表结构",
                 description="返回指定表的字段结构（字段名/类型/可空/键/默认值/额外/注释/字符集/排序规则）；database 为空时使用连接默认库")
    def _describe_table(connectionId: str, table: str, database: str = "") -> dict[str, Any]:
        err = _guard(table=table)
        if err:
            return err

        def action(cur):
            cur.execute(
                "SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_KEY, COLUMN_DEFAULT, "
                "EXTRA, COLUMN_COMMENT, CHARACTER_SET_NAME, COLLATION_NAME "
                "FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = COALESCE(NULLIF(%s, ''), DATABASE()) AND TABLE_NAME = %s "
                "ORDER BY ORDINAL_POSITION",
                (database or "", table),
            )
            columns = []
            for r in cur.fetchall():
                columns.append({
                    "field": r["COLUMN_NAME"],
                    "type": r["COLUMN_TYPE"],
                    "nullable": r["IS_NULLABLE"],
                    "key": r["COLUMN_KEY"],
                    "default": _jsonable(r["COLUMN_DEFAULT"]),
                    "extra": r["EXTRA"],
                    "comment": r["COLUMN_COMMENT"],
                    "charset": r["CHARACTER_SET_NAME"],
                    "collation": r["COLLATION_NAME"],
                })
            return {"database": database or None, "table": table,
                    "columns": columns, "columnCount": len(columns)}
        return _db_call(connectionId, database, action)

    return server


def main():
    """主入口：默认 stdio 传输；带 --port 时用 Streamable HTTP 供管理工具拉起"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=None, help="指定端口则以 Streamable HTTP 运行")
    args = parser.parse_args()

    server = _build_server()
    if args.port is None:
        # transport 默认 'stdio'，最适合桌面客户端（Claude Desktop/Cursor 等）
        server.run()
    else:
        _report_heartbeat(args.port)
        server.run("streamable-http", host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
