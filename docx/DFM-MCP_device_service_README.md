# DFM_MCP_device_service

设备管理 MCP 服务：封装 [DFM-service](../../DFM-service) 的 ADB 设备管理接口，
让 MCP 客户端（DFM MCP 管理工具 / Claude Desktop / Cursor 等）可以直接查询设备、
管理应用、传输文件、模拟按键、监控性能与抓取日志。

## 前提

- **DFM-service 已运行**（默认 `http://127.0.0.1:3000`，本服务只转发 HTTP，不拉起服务）；
  地址可用环境变量 `DFM_SERVICE_URL` 覆盖。
- Python ≥ 3.10（mcp SDK 要求）；依赖仅 `mcp==2.3.0`（见 `requirements.txt`）。

## 安装到 MCP 管理工具

把 `manifest.json`、`server.py`、`requirements.txt`、`README.md` 打包为 zip，
在 DFM 前端「MCP 服务」页上传安装（独立 venv，自动分配 3010~3050 端口）：

```powershell
Compress-Archive -Path manifest.json, server.py, requirements.txt, README.md -DestinationPath ..\DFM_MCP_device_service.zip -Force
```

## 工具清单（25 个）

所有工具返回 DFM-service 统一信封 `{code, message, data}`：`code=200` 成功；
`code=-1` 表示无法连接 DFM-service；其余见 `DFM-service-API文档_v2.md`。

| 组 | 工具 | 说明 |
| --- | --- | --- |
| 设备 | `list_devices` / `get_device_info` / `run_adb_command` | 设备列表 / 详细信息 / 任意 ADB 命令（慎用） |
| 应用 | `list_apps` / `get_app_info` / `install_app` / `uninstall_app` / `start_app` / `stop_app` / `extract_apk` | 第三方应用列表 / 版本信息 / 本地 APK 安装 / 卸载 / 启动（activity 可选）/ force-stop / 提取 APK |
| 文件 | `browse_files` / `read_text_file` / `upload_file` / `download_file` / `make_device_dir` / `delete_device_file` | 浏览 / 读文本全文 / 上传 / 下载 / 建目录 / 删除（危险，慎用） |
| 按键音量 | `send_keyevent` / `send_basic_key` / `control_volume` / `get_volume_status` | keycode / 常用按键 / up·down·mute·unmute / 音量状态 |
| 性能 | `get_performance_snapshot` / `get_monitor_rate` / `get_cpu_top` | 内存·CPU 频率·磁盘快照 / CPU 占用率·网络速率（duration 1-5 秒）/ Top 10 进程 |
| 日志抓取 | `capture_logcat_start` / `capture_logcat_stop` | 抓前台应用 logcat 到 `D:\log\{SN}\` / 停止并返回路径与大小 |

## 运行方式

```bash
# stdio（桌面客户端直连）
python server.py

# Streamable HTTP（MCP 管理工具拉起）
python server.py --port 3010
```

带 `--port` 启动时每 10 秒向 `DFM_MCP_MANAGER_URL`（默认 `http://localhost:3000`）
上报心跳（name / version / mcpVersion / port），管理端未启动时静默跳过。

## 已知边界

- `install_app` / `upload_file` 需要本地文件真实存在，超时 600 秒（大 APK）。
- `read_text_file` 仅适合文本文件，1MB 实测约 0.2 秒，更大文件用 `download_file`。
- `get_monitor_rate` 会占用一个 adb 连接采样 1-5 秒，勿高频并发调用。
- DFM-service 不可达时工具返回 `{"code":-1,...}` 错误信封，不会崩溃。
