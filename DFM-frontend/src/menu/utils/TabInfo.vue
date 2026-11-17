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
const { API_BASE, activeTab, deviceInfo, files, formatBytes, hideProgressNotification, loading, selectedDevice, showConfirm, showMessage, showNotification, showProgressNotification } = ctx
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
                      <button class="feature-btn" :disabled="importingCert" @click="importCaCert">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="18" height="18">
                          <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>
                          <path d="M9 12l2 2 4-4"/>
                        </svg>
                        <span>{{ importingCert ? '导入中…' : '导入证书' }}</span>
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
</template>
