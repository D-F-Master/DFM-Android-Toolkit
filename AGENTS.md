# 项目工作约定（后续会话自动生效）

## 服务管理
- **不要启动、停止或重启任何服务**（包括 DFM-service 的 uvicorn/FastAPI 服务、前端 dev server）。
- 服务的启动和关闭由用户本人管理。
- 开发/调试需要验证接口时：询问用户是否已在运行服务，或由用户自行启动后再测；不要自行拉起服务进程。
- 允许的例外：临时测试脚本（如用 TestClient/独立测试端口验证代码逻辑）在验证完立即清理，不算"启动服务"。

## probe APK 分发
- `DFM-probe/build.ps1` 打包完成后，必须把 `DFM-probe/DFMProbe.apk` 复制一份到 `DFM-service/data/apk/DFMProbe.apk`（服务端 `get_probe_apk_path` 从该路径安装到设备），目标已存在则直接覆盖。
