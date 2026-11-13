import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

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