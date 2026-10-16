# DFM-desktop

DFM-Android-Toolkit 的独立桌面端应用。用 **Electron** 壳包住已有的前后端项目，前后端代码零改动：

```
┌─────────────────────────────┐
│  Electron 窗口 (DFM-desktop) │  ← 加载 http://127.0.0.1:3000
└──────────┬──────────────────┘
           │
┌──────────▼──────────────────┐
│  DFM-service (FastAPI)       │  ← 托管前端 SPA + 提供 /api/*
│  data/dist = 前端构建产物     │
└─────────────────────────────┘
```

## 目录结构

```
DFM-desktop/
├── main.js                  # Electron 主进程：拉起后端 → 等就绪 → 开窗口 → 退出时清理
├── package.json
└── scripts/
    └── sync-frontend.js     # 把 DFM-frontend/dist 同步到 DFM-service/data/dist
```

## 技术选型

| 项 | 选择 | 理由 |
|---|---|---|
| 桌面壳 | Electron 33 | 前端是纯 Web SPA（Vue3+Vite），后端 FastAPI 已自带 SPA 静态托管（html_config.py），Electron 直接加载 `http://127.0.0.1:3000` 即可，前后端零改动 |
| 后端 | 复用 DFM-service | 优先用已打包的 `dist/ylx-utools-server/ylx-utools-server.exe`（内置 adb/scrcpy）；缺失时回退 `.venv` 的 python 跑源码 |
| 不选 Tauri | — | 需要额外安装 Rust 工具链，且收益为零（页面本就由本地服务端提供） |

## 使用步骤

```bash
cd DFM-desktop

# 1. 安装 Electron
npm install

# 2. 同步最新前端产物到后端（前提：DFM-frontend 已 npm run build）
npm run sync

# 3. 启动桌面端
npm start
```

## 行为说明

- **启动**：自动启动后端 → 轮询 `http://127.0.0.1:3000`（最长等 60s，打包 exe 首次启动较慢）→ 就绪后打开 1050×700 窗口
- **失败**：后端超时未就绪会弹出错误对话框（提示端口占用等排查方向），不会静默失败
- **退出**：关闭窗口即退出，后端进程树（taskkill /T /F）一并清理，端口释放
- **端口**：与 `DFM-service/conf/conf.json` 的 `PORT: 3000` 保持一致，改动需同步修改 `main.js` 中的 `PORT` 常量

## 注意

- 后端监听 `0.0.0.0`，局域网内其他机器也能访问 API，如需收紧可改 `conf.json` 的 `HOST`
- 原 uTools 插件入口（DFM-frontend）不受影响，桌面端只是新增的承载方式
