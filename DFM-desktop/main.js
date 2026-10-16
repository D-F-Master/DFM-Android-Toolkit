/**
 * DFM-desktop 主进程
 * 职责：
 *   1. 启动 DFM-service 后端（优先用已打包的 ylx-utools-server.exe，缺失时回退 python main.py）
 *   2. 等后端 http://127.0.0.1:3000 可达后，加载后端托管的 SPA 页面
 *   3. 应用退出时清理后端进程，释放端口
 */
const { app, BrowserWindow, dialog } = require('electron')
const { spawn } = require('child_process')
const http = require('http')
const path = require('path')
const fs = require('fs')

// 与 DFM-service/conf/conf.json 保持一致
const HOST = '127.0.0.1'
const PORT = 3000
const BASE_URL = `http://${HOST}:${PORT}`
// 后端健康检查超时（毫秒）：打包 exe 首次解压较慢，给足时间
const BACKEND_TIMEOUT_MS = 60_000

const SERVICE_DIR = path.join(__dirname, '..', 'DFM-service')
const SERVICE_EXE = path.join(SERVICE_DIR, 'dist', 'ylx-utools-server', 'ylx-utools-server.exe')

let backendProc = null
let quitting = false

/** 探测后端是否就绪 */
function waitForBackend(timeoutMs) {
  const deadline = Date.now() + timeoutMs
  return new Promise((resolve, reject) => {
    const attempt = () => {
      const req = http.get(`${BASE_URL}/`, { timeout: 3000 }, (res) => {
        res.resume()
        resolve()
      })
      req.on('error', retry)
      req.on('timeout', () => {
        req.destroy()
        retry(new Error('request timeout'))
      })
    }
    const retry = (err) => {
      if (Date.now() >= deadline) {
        reject(err)
      } else {
        setTimeout(attempt, 500)
      }
    }
    attempt()
  })
}

/** 启动后端进程，返回使用的启动方式描述 */
function startBackend() {
  if (fs.existsSync(SERVICE_EXE)) {
    backendProc = spawn(SERVICE_EXE, [], {
      cwd: path.dirname(SERVICE_EXE),
      stdio: 'ignore',
      windowsHide: true,
    })
    return 'packaged exe'
  }
  // 回退：开发环境用 venv 里的 python 跑源码
  const venvPython = path.join(SERVICE_DIR, '.venv', 'Scripts', 'python.exe')
  const pythonExe = fs.existsSync(venvPython) ? venvPython : 'python'
  backendProc = spawn(pythonExe, ['main.py', '--mode', 'server'], {
    cwd: SERVICE_DIR,
    stdio: 'ignore',
    windowsHide: true,
  })
  return `python (${path.basename(pythonExe)})`
}

/** 终止后端进程树（exe 是 PyInstaller onedir，需整树清理） */
function stopBackend() {
  if (!backendProc || backendProc.exitCode !== null || backendProc.killed) return
  if (process.platform === 'win32') {
    // taskkill /T 连子进程一起杀，/F 强制
    spawn('taskkill', ['/PID', String(backendProc.pid), '/T', '/F'], { stdio: 'ignore', windowsHide: true })
  } else {
    backendProc.kill()
  }
}

function createWindow() {
  const win = new BrowserWindow({
    title: 'DFM-Android-Toolkit',
    width: 1050,
    height: 700,
    minWidth: 1050,
    minHeight: 700,
    resizable: true,
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
    },
  })
  // 去掉窗口标题里的 " — Electron" 后缀
  win.on('page-title-updated', (e) => e.preventDefault())
  win.loadURL(BASE_URL)
}

app.whenReady().then(async () => {
  try {
    const mode = startBackend()
    console.log(`[DFM-desktop] backend started via ${mode}, waiting for ${BASE_URL} ...`)
    await waitForBackend(BACKEND_TIMEOUT_MS)
    createWindow()
  } catch (err) {
    dialog.showErrorBox(
      'DFM-Android-Toolkit 启动失败',
      `后端服务 ${BASE_URL} 在 ${BACKEND_TIMEOUT_MS / 1000}s 内未就绪。\n\n` +
      `错误信息：${err.message}\n\n` +
      `请确认：\n` +
      `1. 端口 ${PORT} 未被其他程序占用\n` +
      `2. DFM-service 后端可正常启动（dist/ylx-utools-server 或源码环境）`
    )
    app.quit()
  }
})

app.on('before-quit', () => {
  quitting = true
})

app.on('window-all-closed', () => {
  // 非 macOS 下关窗即退出；macOS 保持常规行为
  if (process.platform !== 'darwin') {
    app.quit()
  }
})

app.on('quit', () => {
  if (!quitting) quitting = true
  stopBackend()
})
