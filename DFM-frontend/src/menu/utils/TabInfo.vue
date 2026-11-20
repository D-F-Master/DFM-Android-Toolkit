<script lang="ts" setup>
import { inject, ref, computed, watch, onMounted, onUnmounted, nextTick } from 'vue'
import axios from 'axios'
import * as echarts from 'echarts/core'
import { LineChart, BarChart, GaugeChart } from 'echarts/charts'
import { GridComponent, TooltipComponent, LegendComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import { useAppConfig } from '../../composables/useAppConfig'
import { useProbeSettings } from '../../composables/useProbeSettings'
import SqliteDbViewer from '../../components/SqliteDbViewer.vue'

echarts.use([LineChart, BarChart, GaugeChart, GridComponent, TooltipComponent, LegendComponent, CanvasRenderer])

const ctx = inject<any>('app-utils')!
const { API_BASE, activeTab, deviceInfo, fetchDeviceInfo, files, formatBytes, hideProgressNotification, loading, selectedDevice, showConfirm, showMessage, showNotification, showProgressNotification } = ctx
const showDeviceLogs = async () => {
  if (!selectedDevice.value) return

  try {
    const response = await axios.post(`${API_BASE.value}/api/device/logs`, {
      deviceId: selectedDevice.value
    })
    if (response.data.code !== 200) {
      showMessage('启动日志窗口失败: ' + (response.data.message || '未知错误'))
    }
  } catch (error: any) {
    console.error('启动日志窗口失败:', error)
    showMessage('启动日志窗口失败: ' + error.message)
  }
}

const openDeviceShell = async () => {
  if (!selectedDevice.value) return

  try {
    const response = await axios.post(`${API_BASE.value}/api/device/shell`, {
      deviceId: selectedDevice.value
    })
    if (response.data.code !== 200) {
      showMessage('启动 shell 窗口失败: ' + (response.data.message || '未知错误'))
    }
  } catch (error: any) {
    console.error('启动 shell 窗口失败:', error)
    showMessage('启动 shell 窗口失败: ' + error.message)
  }
}

const capturingLogs = ref(false)

const startLogCapture = async () => {
  if (!selectedDevice.value || capturingLogs.value) return

  try {
    const response = await axios.post(`${API_BASE.value}/api/device/logs/capture/start`, {
      deviceId: selectedDevice.value
    })
    if (response.data.code === 200) {
      capturingLogs.value = true
      showNotification(`开始抓取日志：${response.data.data.path}`, 'success')
    } else {
      showNotification('开始抓取失败: ' + (response.data.message || '未知错误'), 'error')
    }
  } catch (error: any) {
    console.error('开始抓取日志失败:', error)
    showNotification('开始抓取日志失败: ' + error.message, 'error')
  }
}

const stopLogCapture = async () => {
  if (!selectedDevice.value || !capturingLogs.value) return

  try {
    const response = await axios.post(`${API_BASE.value}/api/device/logs/capture/stop`, {
      deviceId: selectedDevice.value
    })
    if (response.data.code === 200) {
      showNotification(`日志已保存：${response.data.data.path}`, 'success')
    } else {
      showNotification('结束抓取失败: ' + (response.data.message || '未知错误'), 'error')
    }
  } catch (error: any) {
    console.error('结束抓取日志失败:', error)
    showNotification('结束抓取日志失败: ' + error.message, 'error')
  } finally {
    capturingLogs.value = false
  }
}

// 清理设备内存
const cleaningMemory = ref(false)

const cleanMemory = async () => {
  if (!selectedDevice.value || cleaningMemory.value) return

  cleaningMemory.value = true
  try {
    const response = await axios.post(`${API_BASE.value}/api/device/memory/clean`, {
      deviceId: selectedDevice.value
    })
    if (response.data.code === 200) {
      const freed = response.data.data.freedBytes
      showNotification(
        freed > 0 ? `清理完成，释放了 ${formatBytes(freed)} 内存` : '清理完成',
        'success'
      )
    } else {
      showNotification('清理失败: ' + (response.data.message || '未知错误'), 'error')
    }
  } catch (error: any) {
    console.error('清理内存失败:', error)
    showNotification('清理内存失败: ' + error.message, 'error')
  } finally {
    cleaningMemory.value = false
  }
}

const rebootToLoader = async () => {
  if (!selectedDevice.value) return

  showConfirm('确定要重启到 fastboot 模式吗？', async () => {
    try {
      await axios.post(`${API_BASE.value}/api/command`, {
        command: `adb -s ${selectedDevice.value} reboot loader`
      })
      showMessage('设备正在重启到 fastboot 模式...')
    } catch (error) {
      console.error('重启到 fastboot 模式失败:', error)
      showMessage('重启到 fastboot 模式失败')
    }
  }, '确认重启')
}

// 导入 CA 证书到「信任的凭据 → 用户」
const importingCert = ref(false)

const importCaCert = async () => {
  if (!selectedDevice.value) {
    showMessage('请先选择设备')
    return
  }
  if (importingCert.value) return

  const input = document.createElement('input')
  input.type = 'file'
  input.accept = '.crt,.cer,.pem'

  input.onchange = async (e: Event) => {
    const target = e.target as HTMLInputElement
    const file = target.files?.[0]
    if (!file) return
    if (file.size === 0) {
      showNotification('证书文件为空', 'error')
      return
    }

    importingCert.value = true
    showProgressNotification('正在导入证书...')
    let resultMessage = ''
    let resultVariant: 'success' | 'error' = 'success'
    try {
      const formData = new FormData()
      formData.append('deviceId', selectedDevice.value)
      formData.append('file', file)

      const response = await axios.post(
        `${API_BASE.value}/api/device/cert/install`,
        formData,
        { headers: { 'Content-Type': 'multipart/form-data' } }
      )
      if (response.data.code === 200) {
        const data = response.data.data || {}
        if (data.alreadyInstalled) {
          resultMessage = '该证书已安装（指纹相同），无需重复导入'
        } else {
          const lines = [
            `证书已导入：${data.fileName}`,
            '请到「设置 → 信任的凭据 → 用户」核对，界面可能不显示但系统信任库已生效'
          ]
          if (data.warnings && data.warnings.length) {
            lines.push('注意：' + data.warnings.join('；'))
          }
          resultMessage = lines.join('\n')
        }
        if (selectedDevice.value) fetchDeviceInfo(selectedDevice.value)
      } else {
        resultMessage = '导入证书失败: ' + (response.data.message || '未知错误')
        resultVariant = 'error'
      }
    } catch (error: any) {
      console.error('导入证书失败:', error)
      resultMessage = '导入证书失败: ' + error.message
      resultVariant = 'error'
    } finally {
      hideProgressNotification()
      // 让 visible 先真正变 false，否则 ModalDialog 的 watch 不会启动 4 秒自动关闭
      await nextTick()
      if (resultMessage) {
        showNotification(resultMessage, resultVariant)
      }
      importingCert.value = false
    }
  }

  input.click()
}

// ==================== 证书仓库（data/certs） ====================
// 交互：点「证书仓库」弹列表 → 点证书直接导入设备；弹窗右上角「导入证书」把本机证书存进仓库
const certRepoVisible = ref(false)
const certRepoLoading = ref(false)
const certRepoOptions = ref<Array<{ name: string; size: number }>>([])
const certRepoUploading = ref(false)
const certRepoInstalling = ref<string>('')
const certRepoInputEl = ref<HTMLInputElement | null>(null)

const fetchCertRepo = async () => {
  certRepoLoading.value = true
  try {
    const response = await axios.post(`${API_BASE.value}/api/device/cert/repo/list`)
    if (response.data.code === 200) {
      certRepoOptions.value = response.data.data || []
    } else {
      showNotification('读取证书仓库失败: ' + (response.data.message || '未知错误'), 'error')
    }
  } catch (error: any) {
    console.error('读取证书仓库失败:', error)
    showNotification('读取证书仓库失败: ' + error.message, 'error')
  } finally {
    certRepoLoading.value = false
  }
}

const openCertRepo = () => {
  certRepoVisible.value = true
  fetchCertRepo()
}

const closeCertRepo = () => {
  certRepoVisible.value = false
}

// 把本机证书保存进仓库（不上设备）
const onCertChosenForRepo = async (e: Event) => {
  const target = e.target as HTMLInputElement
  const file = target.files?.[0]
  target.value = ''
  if (!file) return
  if (certRepoUploading.value) return

  certRepoUploading.value = true
  try {
    const formData = new FormData()
    formData.append('cert', file)
    const response = await axios.post(
      `${API_BASE.value}/api/device/cert/repo/upload`,
      formData,
      { headers: { 'Content-Type': 'multipart/form-data' } }
    )
    if (response.data.code === 200) {
      showNotification(`证书已保存到仓库：${response.data.data?.name || file.name}`, 'success')
      fetchCertRepo()
    } else {
      showNotification('保存证书失败: ' + (response.data.message || '未知错误'), 'error')
    }
  } catch (error: any) {
    console.error('保存证书失败:', error)
    showNotification('保存证书失败: ' + error.message, 'error')
  } finally {
    certRepoUploading.value = false
  }
}

// 从仓库直接导入到当前设备（复用 /device/cert/install 的核心安装逻辑）
const installCertFromRepo = async (name: string) => {
  if (!selectedDevice.value) {
    showNotification('请先在左侧选择设备', 'error')
    return
  }
  if (certRepoInstalling.value) return

  certRepoInstalling.value = name
  showProgressNotification('正在导入证书...')
  let resultMessage = ''
  let resultVariant: 'success' | 'error' = 'success'
  try {
    const response = await axios.post(`${API_BASE.value}/api/device/cert/install-from-repo`, {
      deviceId: selectedDevice.value,
      certName: name
    })
    if (response.data.code === 200) {
      const data = response.data.data || {}
      if (data.alreadyInstalled) {
        resultMessage = '该证书已安装（指纹相同），无需重复导入'
      } else {
        const lines = [
          `证书已导入：${data.fileName}`,
          '请到「设置 → 信任的凭据 → 用户」核对，界面可能不显示但系统信任库已生效'
        ]
        if (data.warnings && data.warnings.length) {
          lines.push('注意：' + data.warnings.join('；'))
        }
        resultMessage = lines.join('\n')
      }
      if (selectedDevice.value) fetchDeviceInfo(selectedDevice.value)
    } else {
      resultMessage = '导入证书失败: ' + (response.data.message || '未知错误')
      resultVariant = 'error'
    }
  } catch (error: any) {
    console.error('导入证书失败:', error)
    resultMessage = '导入证书失败: ' + error.message
    resultVariant = 'error'
  } finally {
    hideProgressNotification()
    await nextTick()
    if (resultMessage) {
      showNotification(resultMessage, resultVariant)
    }
    certRepoInstalling.value = ''
  }
}

const removeCertFromRepo = (name: string) => {
  showConfirm(`确认删除仓库中的证书「${name}」？`, async () => {
    try {
      const response = await axios.post(`${API_BASE.value}/api/device/cert/repo/delete`, { certName: name })
      if (response.data.code === 200) {
        showNotification('证书已删除', 'success')
        fetchCertRepo()
      } else {
        showNotification('删除证书失败: ' + (response.data.message || '未知错误'), 'error')
      }
    } catch (error: any) {
      console.error('删除证书失败:', error)
      showNotification('删除证书失败: ' + error.message, 'error')
    }
  })
}

const formatCertSize = (size: number) => {
  if (!size) return '0 B'
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / 1024 / 1024).toFixed(2)} MB`
}

const copyToClipboard = async (text: string, label: string) => {
  if (!text || text === '未知') {
    showNotification(`${label}为空，无法复制`, 'error')
    return
  }
  try {
    // 优先使用 navigator.clipboard API
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text)
    } else {
      // 降级方案：使用 textarea + execCommand
      const textarea = document.createElement('textarea')
      textarea.value = text
      textarea.style.position = 'fixed'
      textarea.style.top = '-1000px'
      textarea.style.left = '-1000px'
      textarea.style.opacity = '0'
      document.body.appendChild(textarea)
      textarea.select()
      document.execCommand('copy')
      document.body.removeChild(textarea)
    }
    showNotification(`${label}已复制：${text}`, 'success')
  } catch (error) {
    console.error('复制失败:', error)
    showNotification(`复制${label}失败`, 'error')
  }
};

const copyDeviceInfo = async () => {
  const info = deviceInfo.value
  if (!info) {
    showNotification('设备信息尚未加载，无法复制', 'error')
    return
  }
  const rows: Array<[string, string]> = [
    ['设备状态', info.status || '未知'],
    ['设备序列号', info.deviceId || '未知'],
    ['设备名称', info.deviceName || info.model || '未知'],
    ['安卓版本', info.androidVersion || info.version || '未知'],
    ['版本信息', info.displayVersion || '未知'],
    ['构建日期', info.buildDate || '未知'],
    ['SN号', info.sn || '未知'],
    ['WiFi MAC', info.wifiMac || '未知'],
    ['以太网 MAC', info.ethMac || '未知'],
  ]
  // 值里可能含换行（如构建日期），压成空格避免破坏「一行一项」的结构
  const text = rows.map(([label, value]) => `${label}：${String(value).replace(/\s*\r?\n\s*/g, ' ').trim()}`).join('\n')
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text)
    } else {
      // 降级方案：与 copyToClipboard 保持一致
      const textarea = document.createElement('textarea')
      textarea.value = text
      textarea.style.position = 'fixed'
      textarea.style.top = '-1000px'
      textarea.style.left = '-1000px'
      textarea.style.opacity = '0'
      document.body.appendChild(textarea)
      textarea.select()
      document.execCommand('copy')
      document.body.removeChild(textarea)
    }
    showNotification('已复制设备信息到粘贴板')
  } catch (error) {
    console.error('复制设备信息失败:', error)
    showNotification('复制设备信息失败', 'error')
  }
};

// 断开全部网络设备连接（仅断开 IP:端口 形式的网络设备，USB 设备不受影响）

// ===== 设置全局 HTTP 代理（抓包用，即时生效）=====
const proxyDialogVisible = ref(false)
const proxyHost = ref('')
const proxyPort = ref('3000')
const proxyBusy = ref(false)
const PROXY_HOST_KEY = 'dfm_device_proxy_host'
const PROXY_PORT_KEY = 'dfm_device_proxy_port'

// 代理状态展示：已开启时带上实际代理地址
const proxyStatusText = computed(() => {
  const status = deviceInfo.value?.proxyStatus || '未开启'
  if (status === '已开启' && deviceInfo.value?.proxyAddress) {
    return `${status}（${deviceInfo.value.proxyAddress}）`
  }
  return status
})

const openProxyDialog = () => {
  if (!selectedDevice.value) {
    showMessage('请先选择设备')
    return
  }
  try {
    proxyHost.value = localStorage.getItem(PROXY_HOST_KEY) || '192.168.2.93'
    proxyPort.value = localStorage.getItem(PROXY_PORT_KEY) || '3000'
  } catch (e) {
    proxyHost.value = '192.168.2.93'
    proxyPort.value = '3000'
  }
  proxyDialogVisible.value = true
}

const closeProxyDialog = () => {
  proxyDialogVisible.value = false
}

const rememberProxy = () => {
  try {
    localStorage.setItem(PROXY_HOST_KEY, proxyHost.value.trim())
    localStorage.setItem(PROXY_PORT_KEY, proxyPort.value.trim())
  } catch (e) {
    // localStorage 不可用时忽略，不影响设置代理本身
  }
}

const applyProxy = async () => {
  if (!selectedDevice.value || proxyBusy.value) return
  const host = proxyHost.value.trim()
  const port = Number(proxyPort.value)
  if (!host) {
    showNotification('请输入代理地址', 'error')
    return
  }
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    showNotification('代理端口必须是 1-65535 之间的整数', 'error')
    return
  }
  proxyBusy.value = true
  try {
    const response = await axios.post(`${API_BASE.value}/api/device/proxy/set`, {
      deviceId: selectedDevice.value,
      host,
      port
    })
    if (response.data.code === 200) {
      rememberProxy()
      const applied = response.data.data?.proxy || `${host}:${port}`
      showNotification(`代理已设置：${applied}`, 'success')
      proxyDialogVisible.value = false
      if (selectedDevice.value) fetchDeviceInfo(selectedDevice.value)
    } else {
      showNotification('设置代理失败: ' + (response.data.message || '未知错误'), 'error')
    }
  } catch (error: any) {
    console.error('设置代理失败:', error)
    showNotification('设置代理失败: ' + error.message, 'error')
  } finally {
    proxyBusy.value = false
  }
}

const clearProxy = async () => {
  if (!selectedDevice.value || proxyBusy.value) return
  proxyBusy.value = true
  try {
    const response = await axios.post(`${API_BASE.value}/api/device/proxy/clear`, {
      deviceId: selectedDevice.value
    })
    if (response.data.code === 200) {
      showNotification('代理已清除（全局 http_proxy 已复位）', 'success')
      proxyDialogVisible.value = false
      if (selectedDevice.value) fetchDeviceInfo(selectedDevice.value)
    } else {
      showNotification('清除代理失败: ' + (response.data.message || '未知错误'), 'error')
    }
  } catch (error: any) {
    console.error('清除代理失败:', error)
    showNotification('清除代理失败: ' + error.message, 'error')
  } finally {
    proxyBusy.value = false
  }
}
</script>

<template>
              <div v-if="activeTab === 'info'" class="keys-page">
                <!-- 卡片1: 设备信息 -->
                <div class="scrcpy-card">
                  <div class="card-header">
                    <span class="card-icon">
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="18" height="18">
                        <rect x="4" y="4" width="16" height="16" rx="2"/>
                        <line x1="4" y1="10" x2="20" y2="10"/>
                        <line x1="10" y1="4" x2="10" y2="20"/>
                      </svg>
                    </span>
                    <span>设备信息</span>
                    <button
                      class="card-header-action"
                      :disabled="!deviceInfo"
                      @click="copyDeviceInfo"
                      title="复制设备信息到剪贴板"
                    >
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14">
                        <rect x="9" y="9" width="12" height="12" rx="2"/>
                        <path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/>
                      </svg>
                      <span>复制</span>
                    </button>
                  </div>
                  <div class="card-body">
                    <div v-if="deviceInfo" class="info-grid-2col">
                      <div class="info-item-2col">
                        <span class="info-label">设备状态</span>
                        <span class="info-value"><span class="status-badge connected">{{ deviceInfo.status }}</span></span>
                      </div>
                      <div class="info-item-2col">
                        <span class="info-label">设备序列号</span>
                        <span class="info-value">{{ deviceInfo.deviceId }}</span>
                      </div>
                      <div class="info-item-2col">
                        <span class="info-label">设备名称</span>
                        <span class="info-value">{{ deviceInfo.deviceName || deviceInfo.model || '未知' }}</span>
                      </div>
                      <div class="info-item-2col">
                        <span class="info-label">安卓版本</span>
                        <span class="info-value">{{ deviceInfo.androidVersion || deviceInfo.version || '未知' }}</span>
                      </div>
                      <div class="info-item-2col">
                        <span class="info-label">版本信息</span>
                        <span class="info-value">{{ deviceInfo.displayVersion || '未知' }}</span>
                      </div>
                      <div class="info-item-2col">
                        <span class="info-label">构建日期</span>
                        <span class="info-value">{{ deviceInfo.buildDate || '未知' }}</span>
                      </div>
                      <div class="info-item-2col">
                        <span class="info-label">SN号</span>
                        <span
                          class="info-value copyable-value"
                          :class="{ disabled: !deviceInfo.sn }"
                          @click="copyToClipboard(deviceInfo.sn, 'SN号')"
                          title="点击复制"
                        >
                          {{ deviceInfo.sn || '未知' }}
                        </span>
                      </div>
                      <div class="info-item-2col">
                        <span class="info-label">WiFi MAC</span>
                        <span
                          class="info-value copyable-value"
                          :class="{ disabled: !deviceInfo.wifiMac }"
                          @click="copyToClipboard(deviceInfo.wifiMac, 'WiFi MAC')"
                          title="点击复制"
                        >
                          {{ deviceInfo.wifiMac || '未知' }}
                        </span>
                      </div>
                      <div class="info-item-2col">
                        <span class="info-label">以太网 MAC</span>
                        <span
                          class="info-value copyable-value"
                          :class="{ disabled: !deviceInfo.ethMac }"
                          @click="copyToClipboard(deviceInfo.ethMac, '以太网 MAC')"
                          title="点击复制"
                        >
                          {{ deviceInfo.ethMac || '未知' }}
                        </span>
                      </div>
                      <div class="info-item-2col">
                        <span class="info-label">导入证书名称</span>
                        <span class="info-value copyable-value"
                          :class="{ disabled: !deviceInfo.certName || deviceInfo.certName === '无导入证书' }"
                          @click="copyToClipboard(deviceInfo.certName, '导入证书名称')"
                          title="点击复制"
                        >
                          {{ deviceInfo.certName || '无导入证书' }}
                        </span>
                      </div>
                      <div class="info-item-2col">
                        <span class="info-label">代理状态</span>
                        <span class="info-value">{{ proxyStatusText }}</span>
                      </div>
                    </div>
                    <div v-else class="loading-text">加载设备信息中...</div>
                  </div>
                </div>

                <!-- 卡片2: 功能区 -->
                <div class="scrcpy-card">
                  <div class="card-header">
                    <span class="card-icon">
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="18" height="18">
                        <circle cx="12" cy="12" r="3"/>
                        <path d="M19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 010 2.83 2 2 0 01-2.83 0l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 01-2 2 2 2 0 01-2-2v-.09A1.65 1.65 0 009 19.4a1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 01-2.83 0 2 2 0 010-2.83l.06-.06A1.65 1.65 0 004.68 15a1.65 1.65 0 00-1.51-1H3a2 2 0 01-2-2 2 2 0 012-2h.09A1.65 1.65 0 004.6 9a1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 010-2.83 2 2 0 012.83 0l.06.06A1.65 1.65 0 009 4.68a1.65 1.65 0 001-1.51V3a2 2 0 012-2 2 2 0 012 2v.09a1.65 1.65 0 001 1.51 1.65 1.65 0 001.82-.33l.06-.06a2 2 0 012.83 0 2 2 0 010 2.83l-.06.06a1.65 1.65 0 00-.33 1.82V9a1.65 1.65 0 001.51 1H21a2 2 0 012 2 2 2 0 01-2 2h-.09a1.65 1.65 0 00-1.51 1z"/>
                      </svg>
                    </span>
                    <span>功能区</span>
                  </div>
                  <div class="card-body">
                    <div class="feature-grid">
                      <button class="feature-btn" @click="showDeviceLogs">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="18" height="18">
                          <polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/>
                        </svg>
                        <span>实时日志</span>
                      </button>
                      <button class="feature-btn" @click="openDeviceShell">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="18" height="18">
                          <polyline points="16 18 22 12 16 6"/>
                          <polyline points="8 6 2 12 8 18"/>
                        </svg>
                        <span>Shell窗口</span>
                      </button>
                      <button
                        class="feature-btn"
                        :class="{ capturing: capturingLogs }"
                        @click="capturingLogs ? stopLogCapture() : startLogCapture()"
                      >
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="18" height="18">
                          <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/>
                          <polyline points="7 10 12 15 17 10"/>
                          <line x1="12" y1="15" x2="12" y2="3"/>
                        </svg>
                        <span v-if="!capturingLogs">导出日志</span>
                        <template v-else>
                          <span class="capture-status-label">抓取中...</span>
                          <span class="capture-stop-label">结束抓取</span>
                        </template>
                      </button>
                      <button class="feature-btn" :disabled="cleaningMemory" @click="cleanMemory">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="18" height="18">
                          <path d="M3 6h18"/><path d="M8 6V4a2 2 0 012-2h4a2 2 0 012 2v2"/>
                          <path d="M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6"/>
                          <line x1="10" y1="11" x2="10" y2="17"/><line x1="14" y1="11" x2="14" y2="17"/>
                        </svg>
                        <span>{{ cleaningMemory ? '清理中…' : '清理内存' }}</span>
                      </button>
                      <button class="feature-btn" :disabled="importingCert" @click="openCertRepo">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="18" height="18">
                          <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>
                          <path d="M9 12l2 2 4-4"/>
                        </svg>
                        <span>{{ importingCert ? '导入中…' : '导入证书' }}</span>
                      </button>
                      <button class="feature-btn" @click="openProxyDialog">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="18" height="18">
                          <path d="M12 2a10 10 0 100 20 10 10 0 000-20z"/>
                          <line x1="2" y1="12" x2="22" y2="12"/>
                          <path d="M12 2a15 15 0 010 20M12 2a15 15 0 000 20"/>
                        </svg>
                        <span>设置代理</span>
                      </button>
                      <button class="feature-btn danger" @click="rebootToLoader">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="18" height="18">
                          <polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 11-2.12-9.36L23 10"/>
                        </svg>
                        <span>reboot loader</span>
                      </button>
                    </div>
                  </div>
                </div>
              </div>

              <!-- 设置全局 HTTP 代理弹窗 -->
              <div v-if="proxyDialogVisible" class="proxy-overlay" @click.self="closeProxyDialog">
                <div class="proxy-modal">
                  <div class="proxy-modal-header">
                    <h3>设置代理</h3>
                    <button class="proxy-close" @click="closeProxyDialog">&times;</button>
                  </div>
                  <div class="proxy-modal-body">
                    <p class="proxy-hint">设置全局 HTTP 代理，即时生效，用于抓包。清除后恢复直连。</p>
                    <label class="proxy-field">
                      <span>代理地址</span>
                      <input v-model="proxyHost" type="text" class="proxy-input" placeholder="例如 192.168.2.93">
                    </label>
                    <label class="proxy-field">
                      <span>端口</span>
                      <input v-model="proxyPort" type="number" class="proxy-input" placeholder="例如 3000" min="1" max="65535">
                    </label>
                  </div>
                  <div class="proxy-modal-footer">
                    <button class="proxy-btn proxy-btn-clear" :disabled="proxyBusy" @click="clearProxy">清除代理</button>
                    <button class="proxy-btn proxy-btn-cancel" :disabled="proxyBusy" @click="closeProxyDialog">取消</button>
                    <button class="proxy-btn proxy-btn-apply" :disabled="proxyBusy" @click="applyProxy">
                      {{ proxyBusy ? '处理中…' : '设置代理' }}
                    </button>
                  </div>
                </div>
              </div>

              <!-- 证书仓库弹窗：列表点选直接导入设备，右上角导入按钮保存证书进仓库 -->
              <div v-if="certRepoVisible" class="cert-repo-overlay" @click.self="closeCertRepo">
                <div class="cert-repo-modal">
                  <div class="cert-repo-header">
                    <h3>证书仓库</h3>
                    <div class="cert-repo-header-btns">
                      <button class="cert-repo-upload-btn" :disabled="certRepoUploading" @click="certRepoInputEl?.click()">
                        {{ certRepoUploading ? '保存中…' : '⬆ 导入证书' }}
                      </button>
                      <button class="cert-repo-close" @click="closeCertRepo">&times;</button>
                    </div>
                  </div>
                  <p class="cert-repo-hint">点击证书直接导入当前设备{{ selectedDevice ? `（${selectedDevice}）` : '，请先在左侧选择设备' }}；「导入证书」按钮把本机 PEM 证书保存到仓库（DFM-service/data/certs）。</p>
                  <div class="cert-repo-list">
                    <div v-if="certRepoLoading" class="cert-repo-empty">加载中...</div>
                    <div v-else-if="certRepoOptions.length === 0" class="cert-repo-empty">仓库为空，点击右上角「导入证书」保存 PEM 证书（.crt/.cer/.pem）</div>
                    <div v-for="cert in certRepoOptions" :key="cert.name" class="cert-repo-item">
                      <div class="cert-repo-info" @click="installCertFromRepo(cert.name)">
                        <div class="cert-repo-name" :title="cert.name">{{ cert.name }}</div>
                        <div class="cert-repo-size">{{ formatCertSize(cert.size) }}</div>
                      </div>
                      <div class="cert-repo-actions">
                        <button class="cert-repo-btn install" :disabled="!!certRepoInstalling" @click="installCertFromRepo(cert.name)">
                          {{ certRepoInstalling === cert.name ? '导入中…' : '导入设备' }}
                        </button>
                        <button class="cert-repo-btn del" :disabled="!!certRepoInstalling" @click="removeCertFromRepo(cert.name)">删除</button>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
              <input ref="certRepoInputEl" type="file" accept=".crt,.cer,.pem" style="display: none" @change="onCertChosenForRepo">
</template>

<style scoped>
.proxy-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background-color: rgba(0, 0, 0, 0.5);
  display: flex;
  justify-content: center;
  align-items: center;
  z-index: 1000;
}
.proxy-modal {
  background-color: white;
  border-radius: 8px;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
  width: 400px;
  max-width: 90%;
  animation: proxyFadeIn 0.3s ease;
}
@keyframes proxyFadeIn {
  from { opacity: 0; transform: translateY(-20px); }
  to { opacity: 1; transform: translateY(0); }
}
.proxy-modal-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 16px 20px;
  border-bottom: 1px solid #e8e8e8;
}
.proxy-modal-header h3 {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  color: #333;
}
.proxy-close {
  background: none;
  border: none;
  font-size: 20px;
  cursor: pointer;
  color: #999;
  padding: 0;
  width: 24px;
  height: 24px;
}
.proxy-modal-body {
  padding: 20px;
}
.proxy-hint {
  margin: 0 0 16px;
  font-size: 13px;
  color: #666;
  line-height: 1.5;
}
.proxy-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-bottom: 14px;
}
.proxy-field span {
  font-size: 13px;
  color: #555;
}
.proxy-input {
  width: 100%;
  padding: 8px 12px;
  border: 1px solid #d9d9d9;
  border-radius: 4px;
  font-size: 14px;
  box-sizing: border-box;
}
.proxy-input:focus {
  outline: none;
  border-color: #1890ff;
  box-shadow: 0 0 0 2px rgba(24, 144, 255, 0.2);
}
.proxy-modal-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding: 16px 20px;
  border-top: 1px solid #e8e8e8;
  background-color: #fafafa;
  border-radius: 0 0 8px 8px;
}
.proxy-btn {
  padding: 6px 16px;
  border: 1px solid #d9d9d9;
  border-radius: 4px;
  font-size: 14px;
  cursor: pointer;
  transition: all 0.3s;
}
.proxy-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
.proxy-btn-clear {
  margin-right: auto;
  background-color: white;
  color: #e53935;
  border-color: #e53935;
}
.proxy-btn-cancel {
  background-color: white;
  color: #333;
}
.proxy-btn-apply {
  background-color: #1890ff;
  border-color: #1890ff;
  color: white;
}
.proxy-btn-apply:hover:not(:disabled) {
  background-color: #40a9ff;
}
@media (prefers-color-scheme: dark) {
  .proxy-modal { background-color: #2d2f36; }
  .proxy-modal-header { border-bottom-color: #40434a; }
  .proxy-modal-header h3 { color: #e0e0e0; }
  .proxy-hint, .proxy-field span { color: #aaa; }
  .proxy-input { background-color: #3a3d45; border-color: #555; color: #e0e0e0; }
  .proxy-modal-footer { background-color: #33363e; border-top-color: #40434a; }
  .proxy-btn-cancel, .proxy-btn-clear { background-color: #3a3d45; color: #ccc; }
}

/* ===== 证书仓库弹窗 ===== */
.cert-repo-overlay {
  position: fixed;
  inset: 0;
  background-color: rgba(0, 0, 0, 0.5);
  display: flex;
  justify-content: center;
  align-items: center;
  z-index: 1000;
}
.cert-repo-modal {
  width: 560px;
  max-width: 90vw;
  max-height: 70vh;
  display: flex;
  flex-direction: column;
  background-color: white;
  border-radius: 12px;
  box-shadow: 0 8px 30px rgba(0, 0, 0, 0.25);
  overflow: hidden;
}
.cert-repo-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 16px;
  border-bottom: 1px solid #e0e0e0;
}
.cert-repo-header h3 {
  margin: 0;
  font-size: 16px;
  color: #333;
}
.cert-repo-header-btns {
  display: flex;
  align-items: center;
  gap: 10px;
}
.cert-repo-upload-btn {
  padding: 6px 12px;
  border: 1px solid #1890ff;
  border-radius: 6px;
  background-color: #1890ff;
  color: white;
  font-size: 13px;
  cursor: pointer;
}
.cert-repo-upload-btn:hover:not(:disabled) {
  background-color: #40a9ff;
  border-color: #40a9ff;
}
.cert-repo-upload-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
.cert-repo-close {
  border: none;
  background: none;
  font-size: 22px;
  line-height: 1;
  color: #888;
  cursor: pointer;
}
.cert-repo-hint {
  margin: 0;
  padding: 10px 16px;
  font-size: 12px;
  color: #888;
}
.cert-repo-list {
  flex: 1;
  overflow-y: auto;
  padding: 8px 16px 16px;
}
.cert-repo-empty {
  padding: 24px 0;
  text-align: center;
  color: #999;
  font-size: 13px;
}
.cert-repo-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 12px;
  border-radius: 8px;
}
.cert-repo-item:hover {
  background-color: #f5f7fa;
}
.cert-repo-info {
  flex: 1;
  min-width: 0;
  cursor: pointer;
}
.cert-repo-name {
  font-size: 14px;
  color: #333;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.cert-repo-size {
  margin-top: 2px;
  font-size: 12px;
  color: #999;
}
.cert-repo-actions {
  display: flex;
  gap: 8px;
  flex-shrink: 0;
}
.cert-repo-btn {
  padding: 5px 10px;
  border: none;
  border-radius: 6px;
  font-size: 12px;
  cursor: pointer;
}
.cert-repo-btn.install {
  background-color: #e8f5e9;
  color: #2e7d32;
}
.cert-repo-btn.install:hover:not(:disabled) {
  background-color: #c8e6c9;
}
.cert-repo-btn.del {
  background-color: #ffebee;
  color: #c62828;
}
.cert-repo-btn.del:hover:not(:disabled) {
  background-color: #ffcdd2;
}
.cert-repo-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
@media (prefers-color-scheme: dark) {
  .cert-repo-modal { background-color: #2d2f36; }
  .cert-repo-header { border-bottom-color: #40434a; }
  .cert-repo-header h3 { color: #e0e0e0; }
  .cert-repo-close { color: #aaa; }
  .cert-repo-hint { color: #999; }
  .cert-repo-item:hover { background-color: #3a3d45; }
  .cert-repo-name { color: #e0e0e0; }
  .cert-repo-empty { color: #888; }
  .cert-repo-btn.install { background-color: #1b3a22; color: #66bb6a; }
  .cert-repo-btn.install:hover:not(:disabled) { background-color: #255030; }
  .cert-repo-btn.del { background-color: #3a2426; color: #ef9a9a; }
  .cert-repo-btn.del:hover:not(:disabled) { background-color: #4a2c2f; }
}
</style>
