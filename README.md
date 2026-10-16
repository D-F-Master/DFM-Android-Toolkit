# DFM-Android-Toolkit

uTools 插件 + 本地服务端的一体化 Android 调试工具箱，通过 ADB 对 Android 设备进行设备管理、应用管理、文件管理、投屏与日志调试，并附带端口管理能力。

## 项目结构

```
DFM-Android-Toolkit/
├── DFM-frontend/        # 前端：uTools 插件 (Vue 3 + Vite)
│   ├── src/              #   源码
│   ├── public/           #   插件配置 (plugin.json) 与预加载脚本
│   └── upxs/             #   插件安装包
└── DFM-service/     # 后端：本地服务端 (Python FastAPI)
    ├── routes/           #   API 路由 (adb_utils / port_utils / common)
    ├── utils/            #   工具函数 (tool_manager / app_label_utils)
    ├── lib/              #   内置 adb / scrcpy / aapt 工具包
    └── main.py           #   服务端入口
```

## 组件说明

### DFM-frontend — uTools 插件（前端）

- **技术栈**：Vue 3 + Vite，集成 CodeMirror 6 与 ECharts
- **运行方式**：作为 uTools 插件加载，通过 HTTP 调用本地服务端
- **开发调试**：
  ```bash
  cd DFM-frontend
  npm install
  npm run dev
  npm run build   # 构建产物用于打包 upx
  ```

### DFM-service — 本地服务端（后端）

- **技术栈**：Python 3.9+ / FastAPI
- **监听地址**：`http://localhost:3000`（接口文档：`http://localhost:3000/docs`）
- **内置工具**：`lib/` 下自带 adb、scrcpy、aapt，无需配置系统环境变量；缺失时自动回退到系统 PATH
- **启动**：
  ```bash
  cd DFM-service
  pip install -r requirements.txt
  python main.py
  ```
- **打包为 exe**：
  ```bash
  pyinstaller main.spec
  ```

## 主要功能

- **设备管理**：设备列表、设备信息、连接状态
- **应用管理**：安装/卸载、启动/停止、应用列表、提取 APK
- **文件管理**：浏览设备文件系统、上传/下载、创建/删除文件夹、文件编辑
- **控制调试**：按键模拟、音量控制、Scrcpy 投屏、设备日志查看、批量 Shell 命令
- **端口管理**（仅 Windows）：端口占用检查、进程终止、端口扫描、Telnet 测试

## API 约定

- 服务地址：`http://localhost:3000`
- 统一使用 **POST** 方法
- 统一返回格式：
  ```json
  { "code": 200, "message": "success", "data": {} }
  ```
- 详细接口见后端 `README.md` 或启动后的 Swagger UI

## 使用流程

1. 启动后端：`python DFM-service/main.py`（或运行打包好的 `ylx-utools-server.exe`）
2. 在 uTools 中加载插件（开发模式下将 `DFM-frontend/dist` 目录作为插件目录）
3. 插件通过 `http://localhost:3000` 与后端通信
4. 手机开启 USB 调试并授权后即可使用

## 许可证

本项目仅供学习和内部使用。
