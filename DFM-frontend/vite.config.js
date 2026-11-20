import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// ws 库收到非法 WebSocket 帧（如对端发送保留状态码 1005 的关闭帧）时，会在该连接
// 实例上 emit 'error'。vite 6.4.2 对 vite-ping 探测连接不挂 error 监听（上游 main
// 亦未修，vitejs/vite#11991 只修了 TCP socket 级），导致整个 dev server 进程崩溃。
// 进程级兜底：只放行 ws 帧协议类错误（ws 已自行关闭该连接，不影响其他客户端），
// 其余错误保持原有崩溃行为。
process.on('uncaughtException', (err) => {
  if (err && typeof err.code === 'string' && err.code.startsWith('WS_ERR_')) {
    console.warn(`[vite] ignored websocket protocol error: ${err.message}`)
    return
  }
  console.error(err)
  process.exit(1)
})

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [vue()],
  base: './',
  server: {
    port: 5175,
    // uTools 开发插件以 http://localhost:5175 加载；Node 24 下默认只绑定 ::1，
    // Chromium 解析到 127.0.0.1 时连不上会白屏。host: true 让 Node 监听 :: 双栈，
    // 同时覆盖 127.0.0.1 与 ::1。
    host: true,
  },
  resolve: {
    dedupe: [
      // CodeMirror 6 的 instanceof 检查要求全项目只有一份 @codemirror/state / view，
      // 否则报 "Unrecognized extension value in extension set"
      '@codemirror/state',
      '@codemirror/view',
      '@codemirror/language',
      'vue'
    ]
  },
  optimizeDeps: {
    // 把 CodeMirror 共享底层包纳入 dev 预构建，避免语言包各自内嵌一份 state 副本
    include: [
      'vue-codemirror',
      '@codemirror/state',
      '@codemirror/view',
      '@codemirror/language',
      '@codemirror/commands',
      '@codemirror/autocomplete',
      '@codemirror/search',
      '@codemirror/lint',
      '@codemirror/theme-one-dark',
      '@codemirror/lang-javascript',
      '@codemirror/lang-json',
      '@codemirror/lang-xml',
      '@codemirror/lang-css',
      '@codemirror/lang-html',
      '@codemirror/lang-markdown',
      '@codemirror/lang-python'
    ]
  }
})