import os
import sqlite3
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter
from pydantic import BaseModel

from config import logger
from routes.adb_utils import execute_adb_command

router = APIRouter(
    prefix="/api",
    tags=["SQLite数据库"]
)

DOWNLOAD_TIMEOUT = 300
PUSH_TIMEOUT = 300

SQLITE_HEADER = b"SQLite format 3\x00"

# 数据库文件拉到本地后的缓存根目录
SQLITE_CACHE_DIR = os.path.join("D:", os.sep, "log", "sqlite_view")

# sessionId -> 会话信息 {localPath, remotePath, deviceId, fileName}
_sqlite_sessions: Dict[str, dict] = {}


# ========== 请求模型 ==========

class SqliteOpenRequest(BaseModel):
    deviceId: str
    remotePath: str


class SqliteSessionRequest(BaseModel):
    sessionId: str


class SqliteTableRequest(BaseModel):
    sessionId: str
    table: str


class SqliteQueryRequest(BaseModel):
    sessionId: str
    table: str
    page: int = 1
    pageSize: int = 50
    keyword: str = ""


class SqliteInsertRequest(BaseModel):
    sessionId: str
    table: str
    values: Dict[str, Any]


class SqliteUpdateRequest(BaseModel):
    sessionId: str
    table: str
    rowid: int
    values: Dict[str, Any]


class SqliteDeleteRequest(BaseModel):
    sessionId: str
    table: str
    rowid: int


# ========== 内部工具 ==========

def _quote_ident(name: str) -> str:
    """SQL 标识符（表名/列名）安全引用，防注入"""
    return '"' + str(name).replace('"', '""') + '"'


def _pull_file(device_id: str, remote_path: str, local_path: str, timeout: int) -> dict:
    os.makedirs(os.path.dirname(local_path), exist_ok=True)
    return execute_adb_command(
        f'adb -s {device_id} pull "{remote_path}" "{local_path}"',
        timeout=timeout
    )


def _remote_exists(device_id: str, path: str) -> bool:
    result = execute_adb_command(f'adb -s {device_id} shell ls "{path}"', timeout=10)
    if not result.get("success"):
        return False
    output = (result.get("stdout") or "") + (result.get("stderr") or "")
    return "No such file" not in output


def _list_tables(local_path: str) -> List[dict]:
    """列出所有用户表及行数"""
    conn = sqlite3.connect(local_path)
    try:
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
        names = [r[0] for r in cur.fetchall()]
        tables = []
        for name in names:
            try:
                count = conn.execute(f"SELECT COUNT(*) FROM {_quote_ident(name)}").fetchone()[0]
            except sqlite3.Error:
                count = None
            tables.append({"name": name, "count": count})
        return tables
    finally:
        conn.close()


def _get_session(session_id: str) -> Optional[dict]:
    return _sqlite_sessions.get(session_id)


def _get_columns(conn: sqlite3.Connection, table: str) -> List[dict]:
    cur = conn.execute(f"PRAGMA table_info({_quote_ident(table)})")
    return [
        {"name": r[1], "type": r[2] or "", "notnull": bool(r[3]), "pk": bool(r[5])}
        for r in cur.fetchall()
    ]


def _check_table(conn: sqlite3.Connection, table: str) -> Optional[str]:
    """校验表存在，返回错误信息或 None"""
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name = ?", (table,)
    )
    if cur.fetchone() is None:
        return f"表 {table} 不存在"
    return None


def _json_safe(value: Any) -> Any:
    """把 sqlite 返回值转成 JSON 可序列化的形式"""
    if isinstance(value, bytes):
        return f"<BLOB {len(value)}B>"
    return value


# ========== 接口 ==========

@router.post("/device/sqlite/open")
def sqlite_open(request: SqliteOpenRequest):
    """拉取设备上的 db 文件到本地缓存，打开并列出表"""
    if not request.deviceId or not request.remotePath:
        return {"code": 400, "message": "deviceId 和 remotePath 不能为空", "data": None}

    remote = request.remotePath
    file_name = remote.rstrip('/').rsplit('/', 1)[-1]
    session_id = uuid.uuid4().hex
    local_path = os.path.join(SQLITE_CACHE_DIR, session_id, file_name)

    # 拉取主文件
    result = _pull_file(request.deviceId, remote, local_path, DOWNLOAD_TIMEOUT)
    if not result.get("success"):
        return {"code": 500, "message": f"拉取文件失败: {result.get('error', '未知错误')}", "data": None}

    # 校验是 SQLite 数据库（不是加密库也不是别的二进制）
    try:
        with open(local_path, "rb") as f:
            header = f.read(16)
    except OSError as e:
        return {"code": 500, "message": f"读取本地文件失败: {e}", "data": None}
    if header != SQLITE_HEADER:
        try:
            os.remove(local_path)
        except OSError:
            pass
        return {"code": 500, "message": "该文件不是 SQLite 数据库（可能是加密库 SQLCipher 或其他格式）", "data": None}

    # 顺带拉取 -wal / -shm（存在时），否则本地副本看不到最新写入
    wal_pulled = []
    for suffix in ("-wal", "-shm"):
        if _remote_exists(request.deviceId, remote + suffix):
            side_result = _pull_file(request.deviceId, remote + suffix, local_path + suffix, DOWNLOAD_TIMEOUT)
            if side_result.get("success"):
                wal_pulled.append(suffix)

    try:
        tables = _list_tables(local_path)
    except sqlite3.Error as e:
        try:
            os.remove(local_path)
        except OSError:
            pass
        return {"code": 500, "message": f"无法打开数据库: {e}", "data": None}

    _sqlite_sessions[session_id] = {
        "localPath": local_path,
        "remotePath": remote,
        "deviceId": request.deviceId,
        "fileName": file_name,
    }
    logger.info(f"SQLite session opened: {session_id}, remote={remote}, tables={len(tables)}")
    return {
        "code": 200,
        "message": "success",
        "data": {
            "sessionId": session_id,
            "fileName": file_name,
            "remotePath": remote,
            "localPath": local_path,
            "walPulled": wal_pulled,
            "tables": tables,
        }
    }


@router.post("/device/sqlite/tables")
def sqlite_tables(request: SqliteSessionRequest):
    """刷新本地库的表列表（增删改后行数会变，不重新拉设备文件）"""
    info = _get_session(request.sessionId)
    if not info:
        return {"code": 404, "message": "会话不存在或已失效，请重新打开数据库", "data": None}
    try:
        tables = _list_tables(info["localPath"])
    except sqlite3.Error as e:
        return {"code": 500, "message": f"读取表列表失败: {e}", "data": None}
    return {"code": 200, "message": "success", "data": {"tables": tables}}


@router.post("/device/sqlite/query")
def sqlite_query(request: SqliteQueryRequest):
    """分页查询表数据，附带 keyword 全列模糊筛选"""
    info = _get_session(request.sessionId)
    if not info:
        return {"code": 404, "message": "会话不存在或已失效，请重新打开数据库", "data": None}

    page = max(1, request.page)
    page_size = min(max(1, request.pageSize), 500)

    conn = sqlite3.connect(info["localPath"])
    conn.text_factory = lambda b: b.decode("utf-8", errors="replace")
    try:
        err = _check_table(conn, request.table)
        if err:
            return {"code": 400, "message": err, "data": None}
        qt = _quote_ident(request.table)
        columns = _get_columns(conn, request.table)
        if not columns:
            return {"code": 400, "message": f"表 {request.table} 无可读列", "data": None}

        # rowid 支持：WITHOUT ROWID 表无法用 rowid 定位，禁用编辑/删除
        has_rowid = True
        try:
            conn.execute(f"SELECT rowid FROM {qt} LIMIT 1")
        except sqlite3.OperationalError:
            has_rowid = False

        where = ""
        params: List[Any] = []
        if request.keyword:
            conds = [f"CAST({_quote_ident(c['name'])} AS TEXT) LIKE ?" for c in columns]
            where = " WHERE " + " OR ".join(conds)
            params = [f"%{request.keyword}%"] * len(conds)

        total = conn.execute(f"SELECT COUNT(*) FROM {qt}{where}", params).fetchone()[0]

        select_prefix = "rowid AS __rid__, *" if has_rowid else "*"
        cur = conn.execute(
            f"SELECT {select_prefix} FROM {qt}{where} LIMIT ? OFFSET ?",
            params + [page_size, (page - 1) * page_size]
        )
        col_names = [d[0] for d in cur.description]
        rows = []
        for raw in cur.fetchall():
            row = dict(zip(col_names, [_json_safe(v) for v in raw]))
            rows.append(row)

        return {
            "code": 200,
            "message": "success",
            "data": {
                "columns": columns,
                "rows": rows,
                "hasRowid": has_rowid,
                "total": total,
                "page": page,
                "pageSize": page_size,
            }
        }
    except sqlite3.Error as e:
        return {"code": 500, "message": f"查询失败: {e}", "data": None}
    finally:
        conn.close()


@router.post("/device/sqlite/insert")
def sqlite_insert(request: SqliteInsertRequest):
    """插入一行，values: {列名: 值}，None 表示 NULL"""
    info = _get_session(request.sessionId)
    if not info:
        return {"code": 404, "message": "会话不存在或已失效，请重新打开数据库", "data": None}
    if not request.values:
        return {"code": 400, "message": "values 不能为空", "data": None}

    conn = sqlite3.connect(info["localPath"])
    try:
        err = _check_table(conn, request.table)
        if err:
            return {"code": 400, "message": err, "data": None}
        columns = {c["name"] for c in _get_columns(conn, request.table)}
        unknown = [k for k in request.values if k not in columns]
        if unknown:
            return {"code": 400, "message": f"列不存在: {', '.join(unknown)}", "data": None}

        cols = [_quote_ident(k) for k in request.values.keys()]
        placeholders = ", ".join(["?"] * len(cols))
        sql = f"INSERT INTO {_quote_ident(request.table)} ({', '.join(cols)}) VALUES ({placeholders})"
        conn.execute(sql, list(request.values.values()))
        conn.commit()
        return {"code": 200, "message": "success", "data": None}
    except sqlite3.Error as e:
        return {"code": 500, "message": f"插入失败: {e}", "data": None}
    finally:
        conn.close()


@router.post("/device/sqlite/update")
def sqlite_update(request: SqliteUpdateRequest):
    """按 rowid 更新一行"""
    info = _get_session(request.sessionId)
    if not info:
        return {"code": 404, "message": "会话不存在或已失效，请重新打开数据库", "data": None}
    if not request.values:
        return {"code": 400, "message": "values 不能为空", "data": None}

    conn = sqlite3.connect(info["localPath"])
    try:
        err = _check_table(conn, request.table)
        if err:
            return {"code": 400, "message": err, "data": None}
        columns = {c["name"] for c in _get_columns(conn, request.table)}
        unknown = [k for k in request.values if k not in columns]
        if unknown:
            return {"code": 400, "message": f"列不存在: {', '.join(unknown)}", "data": None}

        assignments = ", ".join(f"{_quote_ident(k)} = ?" for k in request.values.keys())
        sql = f"UPDATE {_quote_ident(request.table)} SET {assignments} WHERE rowid = ?"
        params = list(request.values.values()) + [request.rowid]
        cur = conn.execute(sql, params)
        conn.commit()
        if cur.rowcount == 0:
            return {"code": 404, "message": "未找到该行（可能已被删除）", "data": None}
        return {"code": 200, "message": "success", "data": None}
    except sqlite3.Error as e:
        return {"code": 500, "message": f"更新失败: {e}", "data": None}
    finally:
        conn.close()


@router.post("/device/sqlite/delete")
def sqlite_delete(request: SqliteDeleteRequest):
    """按 rowid 删除一行"""
    info = _get_session(request.sessionId)
    if not info:
        return {"code": 404, "message": "会话不存在或已失效，请重新打开数据库", "data": None}

    conn = sqlite3.connect(info["localPath"])
    try:
        err = _check_table(conn, request.table)
        if err:
            return {"code": 400, "message": err, "data": None}
        cur = conn.execute(
            f"DELETE FROM {_quote_ident(request.table)} WHERE rowid = ?", (request.rowid,)
        )
        conn.commit()
        if cur.rowcount == 0:
            return {"code": 404, "message": "未找到该行（可能已被删除）", "data": None}
        return {"code": 200, "message": "success", "data": None}
    except sqlite3.Error as e:
        return {"code": 500, "message": f"删除失败: {e}", "data": None}
    finally:
        conn.close()


@router.post("/device/sqlite/push")
def sqlite_push(request: SqliteSessionRequest):
    """把本地修改后的 db 文件推回设备原路径"""
    info = _get_session(request.sessionId)
    if not info:
        return {"code": 404, "message": "会话不存在或已失效，请重新打开数据库", "data": None}

    result = execute_adb_command(
        f'adb -s {info["deviceId"]} push "{info["localPath"]}" "{info["remotePath"]}"',
        timeout=PUSH_TIMEOUT
    )
    if result.get("success"):
        logger.info(f"SQLite push back: {info['localPath']} -> {info['remotePath']}")
        return {"code": 200, "message": "success", "data": None}
    return {
        "code": 500,
        "message": f"推回设备失败: {result.get('error', '未知错误')}（若目标是应用私有目录，需要 root 或 run-as 权限）",
        "data": None
    }
