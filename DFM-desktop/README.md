# DFM-desktop

DFM-Android-Toolkit 的独立桌面端应用（安装版）。用 **Electron** 壳包住已有的前后端项目，前后端代码零改动：

```
┌────────────────────────────────────┐
│  Electron 窗口 (DFM-desktop)        │  ← 加载 http://127.0.0.1:3000
└──────────┬─────────────────────────┘
           │ spawn（退出时整树清理）
┌──────────▼─────────────────────────┐
│  resources/backend/                │  ← 打包进安装包的 DFM-service
│  ylx-utools-server.exe (server 模式)│     含 adb / scrcpy / aapt
│  _internal/data/dist = 桌面端专用前端│  ← afterPack 钩子替换
└────────────────────────────────────┘
```

## 目录结构

```
DFM-desktop/
├── main.js                  # Electron 主进程：拉起后端 → 等就绪 → 开窗口 → 退出清理 → 启动查更新
├── package.json             # 含 electron-builder 打包配置
├── build/icon.png           # 应用图标（由 scripts/make-icon.ps1 生成）
├── frontend/                # 桌面端专用前端（仅 ADB 工具，无端口工具）
├── scripts/
│   ├── sync-frontend.js     # 开发用：前端产物同步到 DFM-service（源码开发模式）
│   ├── afterPack.js         # 打包钩子：用桌面端前端替换后端包内前端
│   └── make-icon.ps1        # 从 DFM-frontend/logo.png 生成 256x256 图标
├── update-server/           # 更新服务（零依赖，部署在你的服务器上）
└── release/                 # 打包产物（gitignore）
    ├── DFM-Android-Toolkit Setup 2.0.0.exe   # NSIS 安装包
    ├── DFM-Android-Toolkit Setup 2.0.0.exe.blockmap
    └── latest.yml                            # 更新清单
```

## 功能范围

- **只包含 ADB 工具**（设备管理、应用管理、文件管理、控制调试），端口工具已按要求移除
- 前端是 `DFM-frontend` 的独立副本（`frontend/`），原 uTools 插件项目不受影响

## 更新机制

1. `main.js` 在应用启动后调用一次 `autoUpdater.checkForUpdates()`
2. 有新版本 → 后台自动下载 → 弹窗「立即重启 / 稍后自动安装」；无网络/服务器未部署时静默降级
3. 发布地址配置在 `package.json` → `build.publish.url`（当前是占位 `http://localhost:8080/updates/`，**部署更新服务后改成你的服务器地址**）

更新服务见 `update-server/README.md`。

## 打包发布流程

```bash
cd DFM-desktop

# 0. 前提：DFM-service 的后端 exe 已存在（dist/ylx-utools-server/）
#    如后端源码有改动，用 DFM-service/.venv 的 PyInstaller 重新打包 main.spec

# 1. 构建桌面端前端
cd frontend && npm install && npm run build && cd ..

# 2. 打安装包（国内网络建议带镜像变量）
$env:ELECTRON_MIRROR='https://npmmirror.com/mirrors/electron/'
$env:ELECTRON_BUILDER_BINARIES_MIRROR='https://npmmirror.com/mirrors/electron-builder-binaries/'
npm run pack          # 产物在 release/：Setup exe + blockmap + latest.yml

# 3. 发布：把 release/ 里的 exe、blockmap、latest.yml 传到服务器 update-server/data/updates/
#    并把 build.publish.url 改为服务器地址后重新打包
```

## 开发调试

```bash
cd DFM-desktop
npm install
npm run frontend:build   # 构建桌面端前端
npm run sync             # 同步前端产物到 DFM-service（源码/exe 两个位置）
npm start                # 启动：优先用 DFM-service 里已打包的后端 exe
```

## 排查

- 应用日志写入 `%TEMP%\dfm-desktop.log`（启动、更新检查、退出全程记录）
- 后端启动失败会弹错误框（端口占用等），不会静默失败
- 打包 exe 以无控制台方式运行时强制 UTF-8 stdio（`PYTHONIOENCODING` + main.py 内 reconfigure），避免 GBK 编码崩溃

## 行为细节

- 端口与 `DFM-service/conf/conf.json` 的 `PORT: 3000` 一致，改动需同步 `main.js` 的 `PORT`
- 关闭窗口即退出，后端进程树（taskkill /T /F）连同 adb 子进程一并清理
- 后端监听 `0.0.0.0`，局域网内其他机器也能访问 API，如需收紧可改 `conf.json` 的 `HOST`
