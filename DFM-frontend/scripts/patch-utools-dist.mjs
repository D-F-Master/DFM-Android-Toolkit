// uTools 插件以 file:// 加载 dist/index.html，Chromium 禁止 file:// 下的 ES module，
// 会把 #app 留空（白屏）。把 Vite 产物改成经典脚本后，插件和 http://localhost:3000 都能正常渲染。
import { readFileSync, writeFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

const dist = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', 'dist')
const htmlPath = path.join(dist, 'index.html')
let html = readFileSync(htmlPath, 'utf8')

const tags = [...html.matchAll(/<script type="module" crossorigin src="([^"]+)"><\/script>/g)]
if (tags.length === 0) throw new Error('未找到 Vite 的 module script 标签，构建产物格式可能已变化')

for (const [, src] of tags) {
  const jsPath = path.join(dist, src.replace(/^\.\//, ''))
  const js = readFileSync(jsPath, 'utf8')
  if (/\bimport\s*\(/.test(js) || /\bimport\.meta\b/.test(js) || /\bexport\s*\{/.test(js)) {
    throw new Error(`${src} 含 ESM 专用语法，不能转成经典脚本`)
  }
  const patched = js.replace(/export default (\$?\w+)\(\);\s*$/, '$1();')
  if (patched === js) throw new Error(`${src} 末尾未找到 "export default X();"，构建产物格式可能已变化`)
  writeFileSync(jsPath, patched, 'utf8')
}

html = html
  .replace(/<script type="module" crossorigin /g, '<script defer ')
  .replace(/<link rel="stylesheet" crossorigin /g, '<link rel="stylesheet" ')
writeFileSync(htmlPath, html, 'utf8')
console.log(`[patch-utools-dist] ${tags.length} 个脚本已转为经典脚本`)
