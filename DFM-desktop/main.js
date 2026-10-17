const { app, BrowserWindow, dialog } = require('electron')
const { spawn } = require('child_process')
const { autoUpdater } = require('electron-updater')
const http = require('http')
const path = require('path')
const fs = require('fs')
const os = require('os')

// 文件日志：打包环境下 stdout 不可见，写临时文件便于排查
const LOG_FILE = path.join(os.tmpdir(), 'dfm-desktop.log')
function log(msg) {
  try {
    fs.appendFileSync(LOG_FILE, `${new Date().toISOString()} ${msg}\n`)
  } catch (_) { /* 忽略日志失败 */ }
}

// 与 DFM-service/conf/conf.json 保持一致
const HOST = '127.0.0.1'
const PORT = 3000
const BASE_URL = `http://${HOST}:${PORT}`
// 后端健康检查超时（毫秒）：打包 exe 首次解压较慢，给足时间
const BACKEND_TIMEOUT_MS = 60_000

const SERVICE_EXE = path.join(process.resourcesPath, 'backend', 'ylx-utools-server.exe')
const DEV_SERVICE_EXE = path.join(__dirname, '..', 'DFM-service', 'dist', 'ylx-utools-server', 'ylx-utools-server.exe')
const DEV_SERVICE_DIR = path.join(__dirname, '..', 'DFM-service')

let backendProc = null
let quitting = false
let mainWindow = null

/* ==================== 更新 ====================
 * 发布地址在打包时注入：
 *   electron-builder --publish never 构建
 *   服务器地址确定后，设置环境变量 DFM_UPDATE_URL 再打包，或直接改 package.json 的 publish.url
 * 更新流程：启动后检查一次 -> 有更新则后台下载 -> 下载完成提示用户重启安装
 */
function setupUpdater() {
  autoUpdater.autoDownload = true
  autoUpdater.autoInstallOnAppQuit = true

  autoUpdater.on('update-downloaded', (info) => {
    const { dialog } = require('electron')
    dialog.showMessageBox(mainWindow, {
      type: 'info',
      title: '更新就绪',
      message: `新版本 ${info.version} 已下载完成，是否立即重启安装？`,
      buttons: ['立即重启', '稍后自动安装'],
      defaultId: 0,
    }).then(({ response }) => {
      if (response === 0) {
        quitting = true
        autoUpdater.quitAndInstall()
      }
      // 选"稍后"则 app quit 时由 autoInstallOnAppQuit 自动安装
    })
  })

  autoUpdater.on('error', (err) => {
    // 静默记录：无网络/服务器未部署/本地开发都会走到这里，不打扰用户
    log(`updater error: ${err.message}`)
    console.error('[updater]', err.message)
  })

  // 启动后检查一次更新（失败静默，不影响使用）
  autoUpdater.checkForUpdates().catch((e) => {
    log(`updater check failed: ${e.message}`)
  })
}

/* ==================== 后端进程管理 ==================== */

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

/** 后端子进程环境：强制 UTF-8 IO，避免 Python 在无控制台时按 GBK 编码输出 emoji 崩溃 */
function backendEnv() {
  return { ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1' }
}

/** 启动后端进程，返回使用的启动方式描述 */
function startBackend() {
  if (!app.isPackaged) {
    // 开发模式：优先用仓库里已打包的后端 exe，其次回退源码
    if (fs.existsSync(DEV_SERVICE_EXE)) {
      backendProc = spawn(DEV_SERVICE_EXE, [], {
        cwd: path.dirname(DEV_SERVICE_EXE),
        env: backendEnv(),
        stdio: 'ignore',
        windowsHide: true,
      })
      return 'dev packaged exe'
    }
    const venvPython = path.join(DEV_SERVICE_DIR, '.venv', 'Scripts', 'python.exe')
    const pythonExe = fs.existsSync(venvPython) ? venvPython : 'python'
    backendProc = spawn(pythonExe, ['main.py', '--mode', 'server'], {
      cwd: DEV_SERVICE_DIR,
      env: backendEnv(),
      stdio: 'ignore',
      windowsHide: true,
    })
    return `dev python (${path.basename(pythonExe)})`
  }
  // 打包模式：后端 exe 解压到 resources/backend/
  backendProc = spawn(SERVICE_EXE, [], {
    cwd: path.dirname(SERVICE_EXE),
    env: backendEnv(),
    stdio: 'ignore',
    windowsHide: true,
  })
  return 'packaged backend'
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

/* ==================== 窗口 ==================== */

function createWindow() {
  mainWindow = new BrowserWindow({
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
  mainWindow.on('page-title-updated', (e) => e.preventDefault())
  mainWindow.on('closed', () => { mainWindow = null })
  mainWindow.loadURL(BASE_URL)
}

/* ==================== 生命周期 ==================== */

app.whenReady().then(async () => {
  log(`app ready, packaged=${app.isPackaged}, exe=${process.execPath}`)
  setupUpdater()
  try {
    const mode = startBackend()
    log(`backend started via ${mode}, waiting for ${BASE_URL} ...`)
    await waitForBackend(BACKEND_TIMEOUT_MS)
    log('backend ready, creating window')
    createWindow()
    log('window created')
  } catch (err) {
    log(`startup failed: ${err.stack || err.message}`)
    dialog.showErrorBox(
      'DFM-Android-Toolkit 启动失败',
      `后端服务 ${BASE_URL} 在 ${BACKEND_TIMEOUT_MS / 1000}s 内未就绪。\n\n` +
      `错误信息：${err.message}\n\n` +
      `请确认：\n` +
      `1. 端口 ${PORT} 未被其他程序占用\n` +
      `2. DFM-service 后端可正常启动（安装目录 resources/backend 或开发环境）`
    )
    app.quit()
  }
})

app.on('before-quit', () => {
  log('before-quit')
  quitting = true
})

app.on('window-all-closed', () => {
  log('window-all-closed')
  // 非 macOS 下关窗即退出；macOS 保持常规行为
  if (process.platform !== 'darwin') {
    app.quit()
  }
})

app.on('quit', (event, exitCode) => {
  log(`quit, exitCode=${exitCode}`)
  stopBackend()
})
