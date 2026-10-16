/**
 * electron-builder afterPack 钩子
 * 作用：打包完成后，用 DFM-desktop/frontend 的最新构建产物
 *      替换 resources/backend/_internal/data/dist 里的前端页面，
 *      保证安装包里的后端托管的是桌面端专用前端（无端口工具）。
 */
const fs = require('fs')
const path = require('path')

exports.default = async function afterPack(context) {
  const desktopDist = path.join(__dirname, 'frontend', 'dist')
  if (!fs.existsSync(path.join(desktopDist, 'index.html'))) {
    throw new Error(`[afterPack] 缺少前端构建产物 ${desktopDist}，请先执行 cd frontend && npm run build`)
  }
  const target = path.join(
    context.appOutDir, 'resources', 'backend', '_internal', 'data', 'dist'
  )
  if (!fs.existsSync(path.dirname(target))) {
    throw new Error(`[afterPack] 后端资源目录不存在：${path.dirname(target)}（检查 extraResources 配置）`)
  }
  fs.rmSync(target, { recursive: true, force: true })
  fs.cpSync(desktopDist, target, { recursive: true })
  console.log(`[afterPack] 已替换后端前端产物 -> ${target}`)
}
