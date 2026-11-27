# DFM-Android-Toolkit

uTools 插件 + Electron 桌面壳 + FastAPI 本地服务端 + Android 探针 APK 的一体化 Android 调试工具箱：通过 ADB 对 Android 设备进行设备管理、应用管理、文件管理、批量投屏与日志调试，支持串口透传会话、jar 对比/改码、SQLite 查看、MCP 服务管理，并附带端口/IP 工具与生产流水线功能。

> 项目结构与实现的详细说明见 [项目架构总览.md](项目架构总览.md)（含组件交互图、API 清单、已知注意事项）。

## 项目结构

```
DFM-Android-Toolkit/
├── DFM-frontend/        # 前端：uTools 插件 (Vue 3 + Vite) —— 主前端
│   ├── src/             #   源码（App.vue 切页，无 vue-router）
│   ├── public/          #   插件配置 (plugin.json)、preload 脚本、config.json
│   └── upxs/            #   插件安装包
├── DFM-service/         # 后端：本地服务端 (Python FastAPI) —— 唯一后端
│   ├── routes/          #   API 路由（adb/port/ip/pipeline/probe/jar/sqlite/mcp 等）
│   ├── utils/           #   工具函数（tool_manager / app_label_utils / jar_diff_core）
│   ├── lib/             #   内置 adb / scrcpy / aapt 工具包 + cfr.jar
│   ├── conf/            #   conf.json（HOST/PORT/VERSION）
│   ├── data/            #   数据与静态托管目录（data/dist 为前端构建产物）
│   └── main.py          #   服务端入口
├── DFM-desktop/         # 桌面端：Electron 壳，拉起 service 并托管分叉版前端
│   ├── main.js          #   入口（拉起后端、等就绪、开窗口）
│   ├── frontend/        #   独立前端副本（与 DFM-frontend 已分叉）
│   └── update-server/   #   更新服务器（静态文件服务，端口 8080）
├── DFM-probe/           # Android 探针 APK：无界面常驻 agent，TCP :21104
│   ├── java/            #   源码（StartReceiver + ProbeService）
│   ├── pc/              #   PC 端联调脚本 probe_test.py
│   ├── build.ps1        #   构建脚本（aapt2 + javac + d8 + apksigner）
│   └── 接口文档.md       #   探针命令字与能力明细
├── DFM-ui/              # 组件库演示页（无后端依赖，纯静态站）
├── probe与service通讯协议.md   # 探针二进制帧协议（AA 55 帧 + CRC16 + ACK）
└── AGENTS.md            # AI 协作工作约定
```

## 组件说明

### DFM-frontend — uTools 插件（主前端）

- **技术栈**：Vue 3 + Vite，集成 CodeMirror 6 与 ECharts
- **功能页**：ADB 工具（设备/应用/文件/按键/批量/脚本/串口/监控）、端口工具、生产工具、jar 包工具（对比/改码）、MCP 服务管理、设置
- **运行方式**：作为 uTools 插件加载，通过 HTTP 调用本地服务端（基址来自 `public/config.json`）
- **开发调试**：
  ```bash
  cd DFM-frontend
  npm install
  npm run dev      # Vite，端口 5175
  npm run build    # 构建产物用于打包 upx（自动把 ESM 改为 defer 脚本，uTools 禁止 ESM）
  ```
- **结构与分层**：目录结构、通用样式、composable 拆分与新增功能约定见 [DFM-frontend-README.md](DFM-frontend-README.md)。

### DFM-service — 本地服务端（后端）

- **技术栈**：Python 3.9+ / FastAPI
- **监听地址**：`conf/conf.json` 配置，默认 `0.0.0.0:3000`（接口文档：`http://localhost:3000/docs`）
- **内置工具**：`lib/` 下自带 adb、scrcpy、aapt，无需配置系统环境变量；缺失时自动回退到系统 PATH
- **静态托管**：前端构建产物放 `data/dist`，由服务端 SPA 兜底路由托管（桌面端窗口即加载该页面）
- **结构与路由分组**：见 `DFM-service-README.md`（目录职责、按领域分组的路由注册顺序）
- **启动**：
  ```bash
  cd DFM-service
  pip install -r requirements.txt
  python main.py                # 默认 server 模式
  python main.py --mode desktop # pywebview 窗口模式
  ```
- **打包为 exe**：
  ```bash
  pyinstaller main.spec         # onedir，产物 ylx-utools-server
  ```

### DFM-desktop — Electron 桌面端

- 桌面壳不改业务代码：启动时拉起 DFM-service 后端（打包模式跑内置 exe，开发模式优先找 `../DFM-service/dist` 的 exe），轮询 `http://127.0.0.1:3000/` 就绪后开窗加载后端托管页面，退出时整树清理后端进程
- 自带 `update-server/` 静态更新服务器（latest.yml + 安装包，配合 electron-updater）
- **打包**：
  ```bash
  cd DFM-desktop
  npm install
  npm run pack        # NSIS 安装包输出到 release/
  ```
- 注意：`frontend/` 是独立副本且已与 DFM-frontend 分叉（桌面端未含 MCP 管理页）

### DFM-probe — Android 探针 APK

- 无界面常驻 agent：普通服务 + 静态监听线程监听设备 `0.0.0.0:21104`（通知栏不显示；服务被系统回收/进程被杀后由 60s 看门狗自动拉起），PC 通过 `adb forward` 或局域网直连，用 `AA 55` 二进制帧协议（CRC16-Modbus + ACK 停等重传）做远程验证
- 能力：TTS 语音朗读、设备详细信息采集（串口节点/摄像头/外设）、串口透传会话（打开/取增量/单次与循环发送/关闭，需 root）
- 激活：`adb shell am broadcast -n com.dfm.probe/.StartReceiver --include-stopped-packages`（另有开机自启 + 60s 看门狗保活）
- 服务端对应接口见 `DFM-service/routes/probe.py`（安装 APK / forward / 转发消息），APK 位于 `DFM-service/data/apk/DFMProbe.apk`
- **构建**：
  ```powershell
  cd DFM-probe
  powershell -File build.ps1    # 产物 DFMProbe.apk；debug.keystore 保证可覆盖安装，勿删
  ```

### DFM-ui — 组件库演示页

- 从 DFM-frontend 提取的通用组件（Button / ModalDialog / FileEditorDialog）的浏览器演示站，零后端依赖，仅供组件预览与用法参考

## 主要功能

- **设备管理**：设备列表、设备信息、连接状态、批量 Shell 命令
- **应用管理**：安装/卸载、启动/停止、应用列表、提取 APK、aapt 应用名识别
- **文件管理**：浏览设备文件系统、上传/下载、创建/删除文件夹、文件编辑、图片预览、SQLite 数据库查看与增删改查
- **控制调试**：按键模拟、音量控制、Scrcpy 投屏（单设备弹窗 + WebSocket 批量投屏）、设备日志查看、模拟脚本回放、模拟串口
- **jar 包工具**：jar/apk 对比（CFR 反编译 diff）、在线改码（javac 重编译替换）
- **MCP 服务管理**：MCP server 模板生成、安装（独立 venv）、启停、日志
- **探针**：TTS 朗读、设备信息采集、串口透传会话
- **生产流水线**：APK 素材库、批量 run-script、增量日志
- **端口/IP 工具**（仅 Windows）：端口占用检查、进程终止、端口扫描、Telnet 测试、portproxy 管理、局域网 IP 扫描与批量 adb connect

## API 约定

- 服务地址：`http://localhost:3000`，路由前缀 `/api`
- 主要接口为 **POST**；另有 GET（`jardiff` / `jaredit` / `test`）与 WebSocket（`/ws/remote`）
- 统一返回格式（信封靠约定，个别接口为 `{success, message, data}`）：
  ```json
  { "code": 200, "message": "success", "data": {} }
  ```
- WebSocket：`/ws/remote` 批量投屏（JSON 控制帧 + 二进制 H.264 流）
- 详细接口见 `DFM-service-API文档_v2.md`、`DFM-probe_接口文档.md`、`probe与service通讯协议.md`，或启动后的 Swagger UI

## 使用流程

任选一种入口：

**方式一：uTools 插件**
1. 启动后端：`python DFM-service/main.py`（或运行打包好的 `ylx-utools-server.exe`）
2. 在 uTools 中加载插件（开发模式下将 `DFM-frontend/dist` 目录作为插件目录）
3. 关键字 `DFM-Android-Toolkit` 唤起，手机开启 USB 调试并授权后即可使用

**方式二：桌面端**
1. 安装 `DFM-desktop` 打包的安装包（或开发模式直接 `npm start`）
2. 桌面端自动拉起后端并打开界面

**探针（可选）**：需要远程 TTS/串口验证时，通过页面安装 DFMProbe.apk 并连接

## 安全提示

后端无鉴权、CORS 全开且监听 `0.0.0.0`，探针端口亦无鉴权——仅限可信内网环境使用，不要暴露到公网。

## 许可证

本项目仅供学习和内部使用。
