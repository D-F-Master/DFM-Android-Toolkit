/**
 * DFM 更新服务
 * 纯 Node 内置模块实现，零依赖，部署在任何有 Node 的机器上：
 *   node server.js
 *
 * 目录约定（相对本文件）：
 *   data/updates/        <- 放安装包 exe + latest.yml
 * 客户端 electron-updater 会请求：
 *   GET {baseUrl}latest.yml
 *   GET {baseUrl}DFM-Android-Toolkit-Setup-x.y.z.exe
 *
 * 部署后把地址（如 http://your-server:8080/updates/）填进
 * DFM-desktop/package.json 的 build.publish.url，重新打包即可。
 */
const http = require('http')
const fs = require('fs')
const path = require('path')

const PORT = process.env.PORT || 8080
const DATA_DIR = path.join(__dirname, 'data')
const UPDATES_DIR = path.join(DATA_DIR, 'updates')

const MIME = {
  '.yml': 'text/yaml; charset=utf-8',
  '.yaml': 'text/yaml; charset=utf-8',
  '.exe': 'application/octet-stream',
  '.zip': 'application/zip',
  '.blockmap': 'application/octet-stream',
  '.json': 'application/json; charset=utf-8',
}

if (!fs.existsSync(UPDATES_DIR)) {
  fs.mkdirSync(UPDATES_DIR, { recursive: true })
  console.log(`[update-server] 已创建更新文件目录: ${UPDATES_DIR}`)
}

const server = http.createServer((req, res) => {
  // 只处理 /updates/ 前缀，其他一律 404
  const urlPath = decodeURIComponent((req.url || '/').split('?')[0])
  if (!urlPath.startsWith('/updates/')) {
    res.writeHead(404, { 'Content-Type': 'text/plain; charset=utf-8' })
    res.end('Not Found')
    return
  }

  // 防路径遍历：解析后必须仍在 UPDATES_DIR 内
  const rel = urlPath.slice('/updates/'.length)
  const filePath = path.resolve(UPDATES_DIR, rel)
  if (!filePath.startsWith(UPDATES_DIR + path.sep) && filePath !== UPDATES_DIR) {
    res.writeHead(403)
    res.end('Forbidden')
    return
  }

  fs.stat(filePath, (err, stat) => {
    if (err || !stat.isFile()) {
      res.writeHead(404, { 'Content-Type': 'text/plain; charset=utf-8' })
      res.end('Not Found')
      return
    }
    res.writeHead(200, {
      'Content-Type': MIME[path.extname(filePath).toLowerCase()] || 'application/octet-stream',
      'Content-Length': stat.size,
      'Cache-Control': 'no-cache',
    })
    fs.createReadStream(filePath).pipe(res)
  })
})

server.listen(PORT, () => {
  console.log(`[update-server] listening on http://0.0.0.0:${PORT}`)
  console.log(`[update-server] 把安装包 exe 和 latest.yml 放到: ${UPDATES_DIR}`)
})
