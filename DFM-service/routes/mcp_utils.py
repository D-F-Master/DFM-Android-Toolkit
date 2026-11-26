"""
MCP 服务管理模块
提供 MCP server 模板项目生成、zip 包上传安装（独立 venv 依赖隔离）、
启动/停止管理、心跳上报与版本信息维护。

约定：
- 每个安装的 server 目录结构：data/mcp_servers/<name>/{manifest.json, server.py, ...}
- 传输协议为 MCP 标准的 Streamable HTTP，每个 server 独立端口（3010~3050）
- server 运行时定期向管理端上报心跳，携带版本与 mcp SDK 版本
"""
import io
import json
import os
import re
import shutil
import socket
import subprocess
import threading
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import APIRouter, File, UploadFile
from pydantic import BaseModel

from config import logger, get_conf
from utils import get_data_path

router = APIRouter(
    prefix="/api",
    tags=["MCP服务管理"]
)

# ========== 常量 ==========

# server 端口分配范围（避开主服务 3000）
MCP_PORT_MIN = 3010
MCP_PORT_MAX = 3050

# 心跳超时时间（秒），超过视为离线
HEARTBEAT_TIMEOUT = 30

# server venv 要求的最低 Python 版本（官方 mcp SDK 要求 3.10+）
MCP_PYTHON_MIN = (3, 10)

# server 名称仅允许字母/数字/下划线/中划线
_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")

# Windows 隐藏子进程控制台窗口
_CREATE_NO_WINDOW = 0x08000000


# ========== 运行时状态（内存态，服务重启后按进程存活情况重建） ==========

_install_states: Dict[str, dict] = {}   # name -> {status: installing|ready|install_failed, error}
_processes: Dict[str, dict] = {}        # name -> {proc, port, pid, startedAt}
_heartbeats: Dict[str, dict] = {}       # name -> {last, version, mcpVersion, port}
_state_lock = threading.Lock()


# ========== 请求模型 ==========

class CreateTemplateRequest(BaseModel):
    name: str
    path: str


class ServerNameRequest(BaseModel):
    name: str


class SetPortRequest(BaseModel):
    name: str
    port: int


class McpLogRequest(BaseModel):
    name: str
    lines: int = 200
    type: str = "server"   # server=运行日志 | install=安装日志


class HeartbeatRequest(BaseModel):
    name: str
    version: Optional[str] = None
    mcpVersion: Optional[str] = None
    port: Optional[int] = None


# ========== 内部工具 ==========

def _servers_root() -> Path:
    """已安装 server 的根目录：data/mcp_servers"""
    root = Path(get_data_path()) / "mcp_servers"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _server_dir(name: str) -> Path:
    return _servers_root() / name


def _read_manifest(server_dir: Path) -> Optional[dict]:
    """读取并校验 manifest.json，非法返回 None"""
    manifest_path = server_dir / "manifest.json"
    if not manifest_path.is_file():
        return None
    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(manifest, dict):
        return None
    name = manifest.get("name")
    if not isinstance(name, str) or not _NAME_RE.match(name):
        return None
    return manifest


def _conf_mcp_python() -> Optional[str]:
    """conf.json 里显式配置的 MCP Python 路径（可选键 MCP_PYTHON_PATH）"""
    try:
        path = get_conf().get("MCP_PYTHON_PATH")
    except Exception:
        return None
    if path and os.path.isfile(path):
        return path
    return None


def _find_mcp_python() -> Optional[str]:
    """
    寻找可用于创建 server venv 的 Python（>= 3.10）。
    优先级：内置运行时（data/runtime/python310，打包自带）> conf.json 的 MCP_PYTHON_PATH > 常见安装目录自动探测（取最高版本）。
    """
    # 内置便携运行时（随包分发，目标机无需安装 Python）
    bundled = Path(get_data_path()) / "runtime" / "python310" / "python.exe"
    if bundled.is_file():
        return str(bundled)

    conf_path = _conf_mcp_python()
    if conf_path:
        return conf_path

    candidates: List[Path] = []
    local_app = os.environ.get("LOCALAPPDATA")
    search_roots = []
    if local_app:
        search_roots.append(Path(local_app) / "Programs" / "Python")
    search_roots.append(Path("C:") / os.sep / "Program Files")
    search_roots.append(Path("C:") / os.sep / "Program Files (x86)")

    for root in search_roots:
        if root.is_dir():
            candidates.extend(root.glob("Python3*/python.exe"))

    # 按目录名里的版本号从高到低尝试
    def _version_key(p: Path):
        return _parse_python_dir_version(p.parent.name)

    for python in sorted(candidates, key=_version_key, reverse=True):
        if _version_key(python) >= MCP_PYTHON_MIN:
            return str(python)
    return None


def _parse_python_dir_version(text: str) -> tuple:
    """
    解析 Python 安装目录名里的版本号：
    "Python3.9" -> (3, 9)，"Python39" -> (3, 9)，"Python311" -> (3, 11)，"Python3" -> (3, 0)
    """
    m = re.search(r"Python(\d+)(?:\.(\d+))?", text)
    if not m:
        return (0, 0)
    major, minor = m.group(1), m.group(2)
    if minor is not None:
        return (int(major), int(minor))
    if len(major) == 1:
        return (int(major), 0)
    # "Python39" 是 3.9，"Python311" 是 3.11（官方安装目录命名约定）
    return (int(major[0]), int(major[1:]))


def _venv_python(server_dir: Path) -> Path:
    return server_dir / "venv" / "Scripts" / "python.exe"


def _port_free(port: int) -> bool:
    """用 socket 试绑验证端口是否空闲（未被任何程序占用）"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def _manifest_port(manifest: dict) -> Optional[int]:
    """读取 manifest 里配置的固定端口，非法/未配置返回 None"""
    port = manifest.get("port")
    if isinstance(port, bool) or not isinstance(port, int):
        return None
    return port


def _is_running(name: str) -> bool:
    info = _processes.get(name)
    if not info:
        return False
    return info["proc"].poll() is None


def _heartbeat_online(name: str) -> bool:
    hb = _heartbeats.get(name)
    if not hb or not hb.get("last"):
        return False
    try:
        last = datetime.fromisoformat(hb["last"])
        return (datetime.now() - last).total_seconds() <= HEARTBEAT_TIMEOUT
    except ValueError:
        return False


def _find_pid_by_port(port: int) -> Optional[int]:
    """用 netstat 查找监听指定 TCP 端口的进程 PID（找不到返回 None）"""
    try:
        result = subprocess.run(
            ["netstat", "-ano", "-p", "TCP"],
            capture_output=True,
            timeout=10,
            creationflags=_CREATE_NO_WINDOW,
        )
        for line in result.stdout.decode("gbk", errors="replace").splitlines():
            parts = line.split()
            # 形如: TCP  127.0.0.1:3013  0.0.0.0:0  LISTENING  50932
            if (
                len(parts) >= 5
                and parts[0] == "TCP"
                and parts[3] == "LISTENING"
                and parts[1].rsplit(":", 1)[-1] == str(port)
            ):
                return int(parts[4])
    except Exception as e:
        logger.error(f"查找端口 {port} 监听进程失败: {e}")
    return None


def _stop_process(name: str, port: Optional[int] = None) -> None:
    """终止 server 进程树（Windows 下 taskkill 整树清理，与 adb_utils 一致）

    管理端重启等场景下进程未被 _processes 跟踪但心跳仍在线时，
    按固定端口兜底找到监听进程并终止，否则停止接口返回成功但进程仍在运行。
    """
    info = _processes.get(name)
    pid = info["proc"].pid if info and info["proc"].poll() is None else None
    if pid is None and port is not None:
        pid = _find_pid_by_port(port)
    if pid is not None:
        try:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                timeout=10,
                creationflags=_CREATE_NO_WINDOW,
            )
            logger.info(f"MCP server {name} 进程已终止: pid={pid}")
        except Exception as e:
            logger.error(f"停止 MCP server {name} 失败: {e}")
    with _state_lock:
        _processes.pop(name, None)
        _heartbeats.pop(name, None)


def _install_worker(name: str, server_dir: Path) -> None:
    """后台线程：为 server 创建独立 venv 并安装依赖，结果写入 _install_states"""
    log_path = server_dir / "install.log"
    try:
        python_exe = _find_mcp_python()
        if not python_exe:
            raise RuntimeError(
                "未找到 Python 3.10+（官方 mcp SDK 要求）。"
                "请安装 Python 3.10+，或在 conf.json 中配置 MCP_PYTHON_PATH 指向其 python.exe"
            )

        venv_dir = server_dir / "venv"
        steps = [
            [python_exe, "-m", "venv", str(venv_dir)],
        ]
        venv_python = _venv_python(server_dir)
        requirements = server_dir / "requirements.txt"
        if requirements.is_file():
            steps.append([str(venv_python), "-m", "pip", "install", "-r", str(requirements)])

        with open(log_path, "w", encoding="utf-8", errors="replace") as log:
            for cmd in steps:
                log.write(f"\n> {' '.join(cmd)}\n")
                log.flush()
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    timeout=1800,
                    creationflags=_CREATE_NO_WINDOW,
                )
                log.write(result.stdout.decode("utf-8", errors="replace"))
                log.write(result.stderr.decode("utf-8", errors="replace"))
                log.flush()
                if result.returncode != 0:
                    raise RuntimeError(f"命令执行失败(exit {result.returncode}): {cmd[0]} ...")

        with _state_lock:
            _install_states[name] = {"status": "ready", "error": None}
        logger.info(f"MCP server {name} 依赖安装完成")
    except Exception as e:
        logger.error(f"MCP server {name} 依赖安装失败: {e}")
        with _state_lock:
            _install_states[name] = {"status": "install_failed", "error": str(e)}


def _safe_extract_zip(zf: zipfile.ZipFile, target: Path) -> None:
    """防路径穿越地解压 zip 到 target 目录"""
    target.mkdir(parents=True, exist_ok=True)
    for info in zf.infolist():
        name = info.filename.replace("\\", "/")
        if name.startswith("/") or (len(name) > 1 and name[1] == ":"):
            raise ValueError(f"zip 内包含绝对路径成员: {info.filename}")
        parts = [p for p in name.split("/") if p not in ("", ".")]
        if any(p == ".." for p in parts):
            raise ValueError(f"zip 内包含路径穿越成员: {info.filename}")
        if not parts:
            continue
        dest = target.joinpath(*parts)
        if info.is_dir() or name.endswith("/"):
            dest.mkdir(parents=True, exist_ok=True)
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(info) as src, open(dest, "wb") as dst:
            shutil.copyfileobj(src, dst)


def _locate_zip_root(zf: zipfile.ZipFile) -> Optional[str]:
    """
    定位 zip 内 manifest.json 所在的顶层前缀。
    支持 manifest.json 在根目录，或在唯一的一层目录内。
    """
    names = zf.namelist()
    if "manifest.json" in names:
        return ""
    top_dirs = {n.split("/")[0] for n in names if "/" in n}
    if len(top_dirs) == 1:
        prefix = top_dirs.pop() + "/"
        if prefix + "manifest.json" in names:
            return prefix
    return None


# ========== 模板内容 ==========

_TEMPLATE_MANIFEST = """{
  "name": "__NAME__",
  "version": "1.0.0",
  "description": "基于模板创建的 MCP server",
  "entry": "server:mcp",
  "requirements": "requirements.txt",
  "port": 3010,
  "tools": [
    { "name": "echo", "description": "回显输入文本" }
  ]
}
"""

_TEMPLATE_SERVER = '''# -*- coding: utf-8 -*-
"""
__NAME__ - MCP server (Streamable HTTP)
基于官方 mcp SDK 的 FastMCP。启动：python server.py --port 3010
"""
import argparse
import json
import os
import threading
import time
import urllib.request

from mcp.server.fastmcp import FastMCP

try:
    from importlib.metadata import version as pkg_version
except ImportError:
    from importlib_metadata import version as pkg_version

# ========== 心跳上报 ==========
SERVER_NAME = "__NAME__"      # 与 manifest.json 的 name 保持一致
SERVER_VERSION = "1.0.0"      # 与 manifest.json 的 version 保持一致
MANAGER_URL = os.environ.get("DFM_MCP_MANAGER_URL", "http://localhost:3000")


def _report_heartbeat():
    """每 10 秒向管理端上报一次心跳，携带版本与 mcp SDK 版本"""
    def loop():
        while True:
            try:
                payload = json.dumps({
                    "name": SERVER_NAME,
                    "version": SERVER_VERSION,
                    "mcpVersion": pkg_version("mcp"),
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


# ========== MCP 工具定义 ==========
mcp = FastMCP(SERVER_NAME, host="127.0.0.1")


@mcp.tool()
def echo(text: str) -> str:
    """回显输入文本"""
    return text


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=3010)
    args = parser.parse_args()
    _report_heartbeat()
    mcp.settings.port = args.port
    mcp.run(transport="streamable-http")
'''

_TEMPLATE_REQUIREMENTS = """mcp>=1.2.0,<2
"""

_TEMPLATE_README = """# __NAME__

基于 DFM MCP 管理工具模板创建的 MCP server（官方 mcp SDK / Streamable HTTP）。

## 开发

```bash
# 使用 Python 3.10+
python -m venv venv
venv\\Scripts\\python.exe -m pip install -r requirements.txt
venv\\Scripts\\python.exe server.py --port 3010
```

MCP 端点：`http://127.0.0.1:<port>/mcp`

## 工具定义规范

- 在 server.py 中用 `@mcp.tool()` 定义工具
- 每新增/修改一个工具，同步更新 manifest.json 的 `tools` 列表（name + description）
- 工具 description 要写清楚用途与参数含义，供 AIAgent 理解调用

## 打包上传

把项目内容打成 zip（manifest.json 必须在 zip 根目录，或包的唯一一层目录内）：

```bash
# 在项目目录内执行（不要把 venv 打进去）
& "C:\\Program Files\\PowerShell\\7\\pwsh.exe" -NoProfile -Command "Compress-Archive -Path manifest.json, server.py, requirements.txt, README.md -DestinationPath ..\\__NAME__.zip -Force"
```

然后在 MCP 管理工具页上传该 zip 完成安装。
"""


def _write_template_files(target: Path, name: str) -> None:
    target.mkdir(parents=True, exist_ok=True)
    (target / "manifest.json").write_text(
        _TEMPLATE_MANIFEST.replace("__NAME__", name), encoding="utf-8"
    )
    (target / "server.py").write_text(
        _TEMPLATE_SERVER.replace("__NAME__", name), encoding="utf-8"
    )
    (target / "requirements.txt").write_text(_TEMPLATE_REQUIREMENTS, encoding="utf-8")
    (target / "README.md").write_text(
        _TEMPLATE_README.replace("__NAME__", name), encoding="utf-8"
    )


# ========== 接口 ==========

@router.post("/mcp/create-template")
def create_template(request: CreateTemplateRequest):
    """在指定目录下生成 MCP server 模板项目"""
    name = request.name.strip()
    if not _NAME_RE.match(name):
        return {"code": 400, "message": "项目名称仅允许字母、数字、下划线、中划线", "data": None}
    base_path = Path(request.path)
    if not base_path.is_absolute():
        return {"code": 400, "message": "path 必须是绝对路径", "data": None}
    target = base_path / name
    if target.exists():
        return {"code": 400, "message": f"目录已存在: {target}", "data": None}
    try:
        _write_template_files(target, name)
        logger.info(f"生成 MCP 模板项目: {target}")
        return {
            "code": 200,
            "message": "success",
            "data": {"path": str(target), "name": name},
        }
    except Exception as e:
        logger.error(f"生成 MCP 模板项目失败: {e}")
        return {"code": 500, "message": f"生成模板项目失败: {str(e)}", "data": None}


@router.post("/mcp/upload")
async def upload_server(package: UploadFile = File(..., description="MCP server 安装包(.zip)")):
    """
    上传并安装 MCP server zip 包：
    校验 manifest → 解压到 data/mcp_servers/<name> → 后台创建独立 venv 并安装依赖
    """
    try:
        raw = await package.read()
        try:
            zf = zipfile.ZipFile(io.BytesIO(raw))
        except zipfile.BadZipFile:
            return {"code": 400, "message": "不是有效的 zip 文件", "data": None}

        with zf:
            prefix = _locate_zip_root(zf)
            if prefix is None:
                return {"code": 400, "message": "包内未找到 manifest.json", "data": None}
            try:
                manifest = json.loads(zf.read(prefix + "manifest.json").decode("utf-8-sig"))
            except (UnicodeDecodeError, json.JSONDecodeError) as e:
                return {"code": 400, "message": f"manifest.json 解析失败: {str(e)}", "data": None}
            if not isinstance(manifest, dict):
                return {"code": 400, "message": "manifest.json 根节点必须是对象", "data": None}

            name = manifest.get("name")
            version = manifest.get("version")
            if not isinstance(name, str) or not _NAME_RE.match(name):
                return {"code": 400, "message": "manifest.name 缺失或含非法字符", "data": None}
            if not isinstance(version, str) or not version:
                return {"code": 400, "message": "manifest.version 缺失", "data": None}

            # 已在运行则先停止（覆盖升级场景）
            if _is_running(name):
                _stop_process(name)

            target = _server_dir(name)
            if target.exists():
                shutil.rmtree(target)

            try:
                _safe_extract_zip(zf, target)
            except ValueError as e:
                return {"code": 400, "message": str(e), "data": None}

        with _state_lock:
            _install_states[name] = {"status": "installing", "error": None}
        threading.Thread(
            target=_install_worker,
            args=(name, target),
            daemon=True,
        ).start()

        logger.info(f"MCP server {name} v{version} 已上传，开始后台安装")
        return {
            "code": 200,
            "message": "success",
            "data": {"name": name, "version": version, "installStatus": "installing"},
        }
    except Exception as e:
        logger.error(f"MCP server 上传安装失败: {e}")
        return {"code": 500, "message": f"上传安装失败: {str(e)}", "data": None}


@router.post("/mcp/list")
def list_servers():
    """列出全部已安装 server 的状态（安装/运行/心跳/版本）"""
    try:
        result: List[dict] = []
        for entry in sorted(_servers_root().iterdir()):
            if not entry.is_dir():
                continue
            manifest = _read_manifest(entry)
            if manifest is None:
                continue
            name = manifest["name"]
            install_state = _install_states.get(name, {"status": "ready", "error": None})
            running = _is_running(name) or _heartbeat_online(name)
            proc_info = _processes.get(name)
            hb = _heartbeats.get(name) or {}
            # 固定端口（manifest 配置）优先，回退运行时端口（心跳可覆盖未配置场景）
            port = _manifest_port(manifest) or (proc_info or {}).get("port") or hb.get("port")
            result.append({
                "name": name,
                "version": manifest.get("version"),
                "description": manifest.get("description"),
                "mcpVersion": hb.get("mcpVersion"),
                "installStatus": install_state["status"],
                "installError": install_state.get("error"),
                "running": running,
                "port": port,
                "pid": (proc_info or {}).get("pid"),
                "lastHeartbeat": hb.get("last"),
                "endpoint": f"http://127.0.0.1:{port}/mcp" if port and running else None,
            })
        return {"code": 200, "message": "success", "data": result}
    except Exception as e:
        logger.error(f"获取 MCP server 列表失败: {e}")
        return {"code": 500, "message": f"获取列表失败: {str(e)}", "data": None}


@router.post("/mcp/start")
def start_server(request: ServerNameRequest):
    """启动指定 server：以 manifest 配置的固定端口、独立 venv 的 python 子进程运行"""
    name = request.name.strip()
    server_dir = _server_dir(name)
    if not server_dir.is_dir():
        return {"code": 404, "message": f"未安装的 server: {name}", "data": None}
    if _is_running(name):
        info = _processes[name]
        return {
            "code": 200,
            "message": "已在运行",
            "data": {"name": name, "port": info["port"], "pid": info["pid"]},
        }
    install_state = _install_states.get(name, {"status": "ready", "error": None})
    if install_state["status"] == "installing":
        return {"code": 400, "message": "依赖安装中，请稍后再试", "data": None}
    if install_state["status"] == "install_failed":
        return {
            "code": 400,
            "message": f"依赖安装失败，无法启动: {install_state.get('error')}",
            "data": None,
        }
    venv_python = _venv_python(server_dir)
    if not venv_python.is_file():
        return {"code": 400, "message": "venv 不存在，请重新上传安装", "data": None}
    manifest = _read_manifest(server_dir)
    if manifest is None:
        return {"code": 400, "message": "manifest.json 缺失或非法", "data": None}

    # 固定端口：由用户在 manifest.json 的 port 字段配置（服务列表「端口」按钮可改）
    port = _manifest_port(manifest)
    if port is None or not (MCP_PORT_MIN <= port <= MCP_PORT_MAX):
        return {
            "code": 400,
            "message": f"manifest.json 未配置有效固定端口（{MCP_PORT_MIN}~{MCP_PORT_MAX}），请先在服务列表设置端口",
            "data": None,
        }
    # 与其他运行中的 MCP 服务冲突
    with _state_lock:
        used = {info["port"] for n, info in _processes.items() if n != name}
    if port in used:
        return {"code": 400, "message": f"端口 {port} 已被其他运行中的 MCP 服务占用", "data": None}
    # 被系统中其他程序占用
    if not _port_free(port):
        return {"code": 400, "message": f"端口 {port} 已被其他程序占用，请更换端口", "data": None}

    logs_dir = server_dir / "logs"
    logs_dir.mkdir(exist_ok=True)
    log_path = logs_dir / "server.log"
    try:
        log_file = open(log_path, "a", encoding="utf-8", errors="replace")
        proc = subprocess.Popen(
            [str(venv_python), "server.py", "--port", str(port)],
            cwd=str(server_dir),
            stdout=log_file,
            stderr=subprocess.STDOUT,
            creationflags=_CREATE_NO_WINDOW,
        )
    except Exception as e:
        logger.error(f"启动 MCP server {name} 失败: {e}")
        return {"code": 500, "message": f"启动失败: {str(e)}", "data": None}

    with _state_lock:
        _processes[name] = {
            "proc": proc,
            "port": port,
            "pid": proc.pid,
            "startedAt": datetime.now().isoformat(timespec="seconds"),
        }
    logger.info(f"MCP server {name} 已启动: port={port} pid={proc.pid}")
    return {
        "code": 200,
        "message": "success",
        "data": {
            "name": name,
            "port": port,
            "pid": proc.pid,
            "endpoint": f"http://127.0.0.1:{port}/mcp",
        },
    }


@router.post("/mcp/stop")
def stop_server(request: ServerNameRequest):
    """停止指定 server 进程树"""
    name = request.name.strip()
    if not _processes.get(name) and not _heartbeat_online(name):
        return {"code": 400, "message": f"server {name} 未在运行", "data": None}
    # 固定端口用于兜底：进程未被跟踪（管理端重启后心跳仍在线）时按端口终止
    manifest = _read_manifest(_server_dir(name))
    port = (_manifest_port(manifest) if manifest else None) or (_heartbeats.get(name) or {}).get("port")
    _stop_process(name, port)
    logger.info(f"MCP server {name} 已停止")
    return {"code": 200, "message": "success", "data": {"name": name}}


@router.post("/mcp/set-port")
def set_server_port(request: SetPortRequest):
    """设置 server 的固定端口（写入 manifest.json 的 port 字段），重复/占用/运行中则拒绝"""
    name = request.name.strip()
    port = request.port
    server_dir = _server_dir(name)
    if not server_dir.is_dir():
        return {"code": 404, "message": f"未安装的 server: {name}", "data": None}
    manifest = _read_manifest(server_dir)
    if manifest is None:
        return {"code": 400, "message": "manifest.json 缺失或非法", "data": None}
    if _is_running(name) or _heartbeat_online(name):
        return {"code": 400, "message": "服务运行中，请先停止再修改端口", "data": None}
    if not (MCP_PORT_MIN <= port <= MCP_PORT_MAX):
        return {"code": 400, "message": f"端口超出允许范围（{MCP_PORT_MIN}~{MCP_PORT_MAX}）", "data": None}
    # 端口重复检测：与其他已安装 server 的固定端口
    for entry in _servers_root().iterdir():
        if not entry.is_dir():
            continue
        other = _read_manifest(entry)
        other_name = other.get("name") if other else None
        if other_name and other_name != name and _manifest_port(other) == port:
            return {"code": 400, "message": f"端口 {port} 已被服务 {other_name} 使用", "data": None}
    # 端口占用检测：被系统中其他程序占用
    if not _port_free(port):
        return {"code": 400, "message": f"端口 {port} 已被其他程序占用，请更换端口", "data": None}
    manifest["port"] = port
    try:
        with open(server_dir / "manifest.json", "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"MCP server {name} 写入端口失败: {e}")
        return {"code": 500, "message": f"写入端口失败: {str(e)}", "data": None}
    logger.info(f"MCP server {name} 固定端口已设置为 {port}")
    return {"code": 200, "message": "success", "data": {"name": name, "port": port}}


@router.post("/mcp/uninstall")
def uninstall_server(request: ServerNameRequest):
    """卸载指定 server：先停止进程，再删除安装目录"""
    name = request.name.strip()
    server_dir = _server_dir(name)
    if not server_dir.is_dir():
        return {"code": 404, "message": f"未安装的 server: {name}", "data": None}
    if _is_running(name):
        _stop_process(name)
    install_state = _install_states.get(name)
    if install_state and install_state["status"] == "installing":
        return {"code": 400, "message": "依赖安装中，无法卸载，请等待安装完成或失败后再试", "data": None}
    try:
        shutil.rmtree(server_dir)
    except Exception as e:
        logger.error(f"卸载 MCP server {name} 失败: {e}")
        return {"code": 500, "message": f"卸载失败: {str(e)}", "data": None}
    with _state_lock:
        _install_states.pop(name, None)
        _processes.pop(name, None)
        _heartbeats.pop(name, None)
    logger.info(f"MCP server {name} 已卸载")
    return {"code": 200, "message": "success", "data": {"name": name}}


@router.post("/mcp/heartbeat")
def heartbeat(request: HeartbeatRequest):
    """MCP server 心跳上报（由模板生成的 server 每 10 秒调用一次）"""
    name = request.name.strip()
    if not name:
        return {"code": 400, "message": "name 不能为空", "data": None}
    with _state_lock:
        _heartbeats[name] = {
            "last": datetime.now().isoformat(timespec="seconds"),
            "version": request.version,
            "mcpVersion": request.mcpVersion,
            "port": request.port,
        }
    return {"code": 200, "message": "success", "data": None}


@router.post("/mcp/logs")
def get_logs(request: McpLogRequest):
    """查看 server 日志尾部（type: server=运行日志 | install=安装日志）"""
    name = request.name.strip()
    log_file = _server_dir(name) / ("logs/server.log" if request.type == "server" else "install.log")
    if not log_file.is_file():
        return {"code": 404, "message": "日志文件不存在", "data": None}
    try:
        with open(log_file, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        return {
            "code": 200,
            "message": "success",
            "data": {"name": name, "type": request.type, "logs": "".join(lines[-request.lines:])},
        }
    except Exception as e:
        return {"code": 500, "message": f"读取日志失败: {str(e)}", "data": None}
