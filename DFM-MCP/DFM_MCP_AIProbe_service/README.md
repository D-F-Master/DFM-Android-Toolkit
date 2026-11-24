# DFM_MCP_AIProbe_service

AI 探针 MCP 服务：经设备探针 [DFMProbe](../../DFM-probe)（由 [DFM-service](../../DFM-service)
通过 21104 帧协议转发）读取设备当前页面的无障碍树、点击指定组件、获取当前屏幕截图，
让 MCP 客户端（DFM MCP 管理工具 / Claude Desktop / Cursor 等）可以做「看界面 → 点组件 → 再截图确认」的闭环。

## 前提

- **DFM-service 已运行**（默认 `http://127.0.0.1:3000`，本服务只转发 HTTP，不拉起服务）；
  地址可用环境变量 `DFM_SERVICE_URL` 覆盖。
- **目标设备已安装并运行 DFMProbe**，且无障碍服务已开启（否则 0x0C/0x0D 会被设备端拒绝）。
- Python ≥ 3.10（mcp SDK 要求）；依赖仅 `mcp==2.3.0`（见 `requirements.txt`）。

## 安装到 MCP 管理工具

把 `manifest.json`、`server.py`、`requirements.txt`、`README.md` 打包为 zip，
在 DFM 前端「MCP 服务」页上传安装（独立 venv，自动分配 3010~3050 端口）：

```powershell
Compress-Archive -Path manifest.json, server.py, requirements.txt, README.md -DestinationPath ..\DFM_MCP_AIProbe_service.zip -Force
```

## 工具清单（3 个）

所有工具返回 DFM-service 统一信封 `{code, message, data}`：`code=200` 成功；
`code=400` 参数错误；`code=450` 探针不在线；`code=500` 设备端返回错误；
`code=-1` 表示无法连接 DFM-service。

| 工具 | 转发 | 说明 |
| --- | --- | --- |
| `get_accessibility_tree` | `/api/probe/message` cmd=`0x0C` | 返回 `package` / `nodeCount` / `truncated` / `root`，节点含 `class`·`text`·`desc`·`id`·`bounds`·`clickable`·`scrollable`·`enabled`·`children` |
| `click_component` | `/api/probe/message` cmd=`0x0D` | 参数 `id` / `text` / `desc` 至少填一项，设备端按填写的项匹配并执行 ACTION_CLICK；返回 `clicked` 与实际命中的三项 |
| `get_screenshot` | `/api/probe/message` cmd=`0x09` + `/api/device/files/base64` | 设备端 screencap → 服务端读回 base64 → 保存为本地 PNG，返回 `localPath` / `localSize` / `devicePath` / `via` |

典型串联：`get_screenshot` 看当前界面 → `get_accessibility_tree` 拿到可点节点的 `id`/`text`/`desc`
→ `click_component` 点击 → 再次 `get_screenshot` 确认。

## 运行方式

```bash
# stdio（桌面客户端直连）
python server.py

# Streamable HTTP（MCP 管理工具拉起）
python server.py --port 3010
```

带 `--port` 启动时每 10 秒向 `DFM_MCP_MANAGER_URL`（默认 `http://localhost:3000`）
上报心跳（name / version / mcpVersion / port），管理端未启动时静默跳过。

## 截图落盘位置

默认 `D:\log\screenshots\{deviceId}\screenshot_{时间}.png`，`deviceId` 中的 `:` 会替换为 `_`
（如 `192.168.0.60:5555` → `192.168.0.60_5555`）。可用环境变量
`DFM_AIPROBE_SCREENSHOT_DIR` 覆盖根目录。

## 已知边界

- `get_screenshot` 只返回**本地路径**，base64 不进入响应；读取阶段超时 600 秒。
- 截图文件不在设备端删除（与探针 0x09 行为一致，设备侧留在
  `Android/data/com.dfm.probe/files/` 下，由设备端自行轮转）。
- 同一设备的探针请求在 DFM-service 侧串行化，长时间脚本任务会占锁，此时返回 450「设备忙」。
- 无障碍服务的开关不在本服务范围：未开启时返回 500，提示信息来自设备端。