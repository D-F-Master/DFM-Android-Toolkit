/**
 * 同步脚本：把 DFM-frontend 的最新构建产物复制到 DFM-service/data/dist，
 * 供后端 html_config.py 托管，桌面端窗口即可看到最新前端页面。
 *
 * 用法：npm run sync
 * 前提：DFM-frontend 已执行过 npm run build（存在 dist/）
 */
const fs = require('fs')
const path = require('path')

const root = path.join(__dirname, '..')
const SRC = path.join(root, 'DFM-frontend', 'dist')
const DST = path.join(root, 'DFM-service', 'data', 'dist')

if (!fs.existsSync(path.join(SRC, 'index.html'))) {
  console.error(`[sync] 未找到前端构建产物：${SRC}`)
  console.error('[sync] 请先在 DFM-frontend 目录执行：npm install && npm run build')
  process.exit(1)
}

fs.rmSync(DST, { recursive: true, force: true })
fs.cpSync(SRC, DST, { recursive: true })
console.log(`[sync] 已同步前端产物：${SRC} -> ${DST}`)
