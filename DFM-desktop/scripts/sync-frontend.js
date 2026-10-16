/**
 * 同步脚本：把 DFM-desktop/frontend 的最新构建产物复制到 DFM-service，
 * 供后端 html_config.py 托管，桌面端窗口即可看到最新前端页面。
 *
 * 用法：npm run sync
 * 前提：frontend 已执行过 npm run build（存在 dist/）
 */
const fs = require('fs')
const path = require('path')

const root = path.join(__dirname, '..') // DFM-desktop
const SRC = path.join(root, 'frontend', 'dist')
const SERVICE_DIR = path.join(root, '..', 'DFM-service')
const DST = path.join(SERVICE_DIR, 'data', 'dist')

if (!fs.existsSync(path.join(SRC, 'index.html'))) {
  console.error(`[sync] 未找到前端构建产物：${SRC}`)
  console.error('[sync] 请先在 DFM-desktop/frontend 目录执行：npm install && npm run build')
  process.exit(1)
}

fs.rmSync(DST, { recursive: true, force: true })
fs.cpSync(SRC, DST, { recursive: true })
console.log(`[sync] 已同步前端产物：${SRC} -> ${DST}`)

// 打包版 exe 的资源查找优先走 _internal/data（见 tool_manager.py get_data_path），
// 开发模式用打包 exe 启动时托管的是这里的前端，需要一并覆盖
const EXE_DST = path.join(SERVICE_DIR, 'dist', 'ylx-utools-server', '_internal', 'data', 'dist')
if (fs.existsSync(path.dirname(EXE_DST))) {
  fs.rmSync(EXE_DST, { recursive: true, force: true })
  fs.cpSync(SRC, EXE_DST, { recursive: true })
  console.log(`[sync] 已同步前端产物：${SRC} -> ${EXE_DST}`)
}
