# DFM 更新服务

零依赖（纯 Node 内置模块）的静态更新文件服务，配合客户端 electron-updater 的 generic provider 使用。

## 本地运行

```bash
cd update-server
npm start          # 或 node server.js，默认端口 8080，可用 PORT 环境变量覆盖
```

## 目录结构

```
update-server/
├── server.js          # 服务本体
└── data/updates/      # 更新文件目录（首次启动自动创建）
    ├── latest.yml                                 # electron-builder 打包时生成
    ├── DFM-Android-Toolkit-Setup-2.0.0.exe        # 安装包
    └── DFM-Android-Toolkit-Setup-2.0.0.exe.blockmap  # 增量更新用（如有）
```

## 发布新版本流程

1. 提高版本号：改 `DFM-desktop/package.json` 的 `version`，同时把 `build.publish.url` 改为你的服务器地址（如 `http://your-server:8080/updates/`）
2. 打包：`cd DFM-desktop && npm run pack`，产物在 `DFM-desktop/release/`
3. 把 `release/` 里的 `.exe`、`.exe.blockmap`、`latest.yml` 三个文件上传到服务器 `update-server/data/updates/`
4. 在服务器上运行 `npm start`（建议用 pm2 / NSSM 守护）

## 客户端行为

- 应用每次启动检查一次更新（`main.js` 的 `setupUpdater`）
- 有新版本 → 后台自动下载 → 弹窗询问「立即重启 / 稍后自动安装」
- 选「稍后」则用户退出应用时自动安装
- 检查失败（无网络/服务器未部署）静默忽略，不影响使用

## 安全说明

- 服务端做了路径遍历防护（只允许 `/updates/` 前缀且解析后不越出数据目录）
- 生产部署建议加 HTTPS（套 Nginx/Caddy 反代即可，客户端 publish.url 写 https 地址）
