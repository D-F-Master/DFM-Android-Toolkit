# DFM-service README

`DFM-service` 是 DFM-Android-Toolkit 的后端服务，基于 FastAPI，同时承载两种形态：

- **server 模式**：直接运行 Uvicorn，给 uTools 插件 / 外部调用方提供 HTTP + WebSocket 接口。
- **desktop 模式**：后台线程启动同一套服务，前台用 `pywebview` 打开本地页面（桌面壳）。

本文件是 `DFM-service` 的**结构说明**；具体 HTTP/WebSocket 接口明细见 `DFM-service-API文档_v2.md`。

---

## 1. 技术栈与入口

- Python 3.10+（依赖见 `DFM-service/requirements.txt`）
- FastAPI / Uvicorn / Pydantic
- `pywebview`（desktop 模式）
- PyInstaller 打包配置：`main.spec`、`main-onefile.spec`
- 外部工具：`lib/adb`、`lib/aapt`、`lib/scrcpy-win64-v3.3.3`、`lib/cfr.jar`

入口文件：`DFM-service/main.py`

```bash
cd DFM-service
pip install -r requirements.txt

# 无桌面版：阻塞运行 HTTP 服务（默认）
python main.py --mode server

# 桌面版：后台起服务 + 前台 webview 窗口
python main.py --mode desktop
```

> 注意：`main.py` 内部使用 `parser.parse_args(["--mode", "server"] + sys.argv[1:])`，因此不传 `--mode` 时默认就是 `server`。

`run.bat` 当前直接拉起打包后的 `D:\uTools\service\dist\ylx-utools-server\ylx-utools-server.exe --mode server`，部署到其他机器时需要按实际路径调整。

---

## 2. 目录结构

```text
DFM-service/
├─ main.py                    # 应用入口：创建 app、按领域注册路由、CLI 启动
├─ conf/
│  └─ conf.json               # VERSION / HOST / PORT / MCP_PYTHON_PATH
├─ config/                    # 应用装配与基础设施
│  ├─ __init__.py             # 统一导出 logger / create_app / get_conf / TaskQueue
│  ├─ app_config.py           # FastAPI 实例、CORS 中间件、lifespan
│  ├─ conf_config.py          # 读取 conf/conf.json
│  ├─ html_config.py          # dist 静态资源与 SPA 兜底路由（必须最后注册）
│  ├─ logger_config.py        # 日志配置，输出到 log/
│  ├─ queue_config.py         # 任务队列 lifespan
│  └─ tool_check.py           # 启动时的外部工具可用性检查
├─ routes/                    # 业务路由；main.py 中按领域分组导入和注册
│  ├─ adb_utils.py            # ADB 设备 / 应用 / 文件 / 屏幕 / 输入（/api）
│  ├─ port_utils.py           # 端口检查 / 占用 / 扫描 / portproxy（/api）
│  ├─ ip_utils.py             # IP 扫描 / 校验 / 批量连接（/api）
│  ├─ jar_diff.py             # jar 包对比（/api/jardiff）
│  ├─ jar_edit.py             # jar 反编译 / 源码编辑 / 回写（/api/jaredit）
│  ├─ sqlite_utils.py         # 设备 SQLite 读写（/api）
│  ├─ remote_ws.py            # WebSocket：/ws/remote
│  ├─ script.py               # 脚本 / 文件夹 / 模板 / 运行控制（/api）
│  ├─ serial.py               # 串口 / pty / 按键映射（/api）
│  ├─ pipeline.py             # 生产流水线：APK / 抓图 / 跑脚本（/api）
│  ├─ probe.py                # 探针能力，与 DFM-probe 通讯（/api）
│  ├─ mcp_utils.py            # MCP 服务模板 / 上传 / 启停 / 日志（/api）
│  ├─ notify.py               # 邮件通知（/api/notify）
│  ├─ common.py               # 通用工具，含隐藏接口 /api/cp/decode（/api）
│  └─ test_routes.py          # 测试接口（/test）
├─ utils/                     # 路径解析、应用名缓存、jar diff、markdown 导出等
├─ lib/                       # adb / aapt / scrcpy / cfr.jar 等外部工具
├─ data/                      # 运行期数据（见下）
├─ public/                    # logo.ico / logo.png
├─ requirements.txt
├─ main.spec / main-onefile.spec
└─ run.bat
```

`routes/batch_remote.py`、`routes/vision.py` 是没有 endpoint 的 helper 模块：`batch_remote.py` 提供 `DeviceSession`，`vision.py` 提供 `H264Decoder` / 抓屏能力，由 `remote_ws.py`、`script.py`、`pipeline.py` 使用；`main.py` 不为它们单独注册路由。

---

## 3. 路由分组与注册顺序

`main.py` 按领域集中导入并注册路由，同类接口保持相邻。新增路由时应插入对应分组，**不要随意追加到文件末尾**，尤其不能插到静态兜底之后。

| 分组 | 路由模块 | 前缀 / 入口 | 覆盖范围 |
| --- | --- | --- | --- |
| 1. 设备与网络工具 | `adb_utils.py`、`port_utils.py`、`ip_utils.py` | `/api` | 设备连接、应用管理、文件、端口、IP |
| 2. 文件解析与数据库 | `jar_diff.py`、`jar_edit.py`、`sqlite_utils.py` | `/api/jardiff`、`/api/jaredit`、`/api` | jar 对比/编辑、SQLite |
| 3. 自动化与脚本 | `remote_ws.py`、`script.py`、`serial.py`、`pipeline.py` | `/ws/remote`、`/api` | 远程 WebSocket、脚本、串口、流水线 |
| 4. 探针 | `probe.py` | `/api` | 相机 / 消息 / 脚本导入与执行等 Probe 能力 |
| 5. MCP 与通知 | `mcp_utils.py`、`notify.py` | `/api`、`/api/notify` | MCP 服务管理、邮件通知 |
| 6. 通用与测试 | `common.py`、`test_routes.py` | `/api`、`/test` | 辅助工具、测试接口 |
| 7. 静态页面（兜底） | `config/html_config.py` | `/`、`/{full_path:path}` | 前端 `data/dist` 静态资源与 SPA 回退 |

### 必须遵守的注册顺序约束

1. `html_config` 的 `/{full_path:path}` 是 SPA 兜底路由，**必须最后注册**，否则会吞掉后面的 API 路由。
2. `jar_edit_router` **必须注册在 `html_config` 之前**，否则 `/api/jaredit` 下的下载接口会被兜底路由拦截。
3. `remote_ws.py` 使用 `APIRouter()` 无前缀，WebSocket 路径是完整的 `/ws/remote`，不要误加 `/api` 前缀。

---

## 4. 配置与运行期数据

### 配置

- 配置文件：`conf/conf.json`，由 `config/conf_config.get_conf()` 读取并缓存。
- 关键字段：
  - `VERSION`：版本号，也用于桌面窗口标题。
  - `HOST` / `PORT`：Uvicorn 监听地址与端口，默认 `0.0.0.0:3000`。
  - `MCP_PYTHON_PATH`：启动 MCP 子服务使用的 Python 解释器路径。
- `utils/tool_manager.py` 负责路径解析：
  - 开发环境：`DFM-service/data`、`DFM-service/conf`
  - PyInstaller 打包后：优先 `_internal/data`、`_internal/conf`

### 数据目录

`data/` 是运行期数据目录，主要子目录：

```text
data/
├─ apk/            # 下发给设备的 APK，含 DFMProbe.apk
├─ app_labels.json # aapt 提取的应用名缓存
├─ camhook.so      # 探针摄像头 hook 库
├─ certs/          # 证书
├─ dist/           # 前端构建产物，html_config 从这里提供静态页面
├─ mcp_servers/    # MCP 服务模板 / 安装副本
├─ media/          # 脚本媒体资源
├─ pipelines/      # 流水线文件
├─ runtime/        # 运行时临时文件
├─ scripts/        # 脚本存储
└─ templates/      # 模板文件
```

> `DFM-probe/build.ps1` 打包完成后会把 `DFM-probe/DFMProbe.apk` 复制到 `DFM-service/data/apk/DFMProbe.apk`；服务端 `get_probe_apk_path` 从该路径安装到设备，已存在则覆盖。

---

## 5. 结构规范约定

- **路由扩展**：先在 `main.py` 找到对应领域分组，再导入 / 注册；不要把所有新路由都堆到文件末尾。
- **前缀稳定**：已有路由前缀（`api`、`api/jardiff`、`api/jaredit`、`api/notify`、`test`、`ws/remote`）不要随意改动，避免破坏前端调用。
- **兜底唯一**：静态 SPA 兜底只保留在 `config/html_config.py`，且始终最后注册。
- **文档同步**：
  - HTTP/WebSocket 接口变化 → `docx/DFM-service-API文档_v2.md`
  - 目录结构 / 路由分组 / 启动方式变化 → 本文件 + `docx/项目架构总览.md`
  - `docx/` 是项目唯一文档目录，不要在 `DFM-service/` 下再放说明文档。
