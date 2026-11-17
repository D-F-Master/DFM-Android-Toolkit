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
const { API_BASE, SCRIPT_COMMON_KEYS, activeTab, batchDeviceInfos, batchLoading, batchRemoteCodecFromSps, batchRemoteIsKeyframe, batchSelectedDevices, concatU8, devices, fetchDevices, files, loading, modalCallback, modalConfirmText, modalMessage, modalPlaceholder, modalTitle, modalType, modalVariant, modalVisible, showList, showMessage } = ctx
const batchModelFilter = ref('')

const batchFilteredDevices = computed(() => {
  if (!batchModelFilter.value.trim()) {
    return batchDeviceInfos.value
  }
  const keyword = batchModelFilter.value.trim().toLowerCase()
  return batchDeviceInfos.value.filter(d => d.model?.toLowerCase().includes(keyword))
})

const batchSelectAllChecked = computed(() => {
  const filtered = batchFilteredDevices.value
  if (filtered.length === 0) return false
  return filtered.every(d => batchSelectedDevices.value.has(d.deviceId))
})

const toggleBatchDevice = (deviceId: string) => {
  const next = new Set(batchSelectedDevices.value)
  if (next.has(deviceId)) {
    next.delete(deviceId)
  } else {
    next.add(deviceId)
  }
  batchSelectedDevices.value = next
}

const toggleAllBatchDevices = () => {
  const visibleIds = batchFilteredDevices.value.map(d => d.deviceId)
  if (visibleIds.length > 0 && visibleIds.every(id => batchSelectedDevices.value.has(id))) {
    batchSelectedDevices.value = new Set()
  } else {
    const next = new Set(batchSelectedDevices.value)
    visibleIds.forEach(id => next.add(id))
    batchSelectedDevices.value = next
  }
}

const selectedBatchCount = computed(() => batchSelectedDevices.value.size)

// ===== 批量投屏（嵌入批量操作页） =====
const batchRemoteActive = ref(false)
const batchRemoteConnecting = ref(false)
const batchRemoteCards = ref<Array<{ deviceId: string; index: number; name: string; width: number; height: number; ready: boolean; frames: number; diag: string; codec?: string }>>([])
const batchRemoteSource = ref('')
const batchRemoteStatus = ref('')
const batchRemoteWsStatus = ref('未连接')
let batchRemoteWs: WebSocket | null = null
let batchRemoteConfigs: Record<number, Uint8Array> = {}
let batchRemoteDecoders: Record<number, VideoDecoder> = {}
const batchRemoteCanvasRefs = ref<any[]>([])

const setBatchRemoteCanvasRef = (el: any, index: number) => {
  if (el) batchRemoteCanvasRefs.value[index] = el
}

const setBatchRemoteSource = (id: string) => {
  batchRemoteSource.value = id
}

const batchRemoteGridCols = computed(() => {
  const n = batchRemoteCards.value.length
  if (n <= 1) return 1
  if (n <= 4) return 2
  return 3
})

const batchRemoteSendMirror = (event: any) => {
  if (batchRemoteWs && batchRemoteWs.readyState === WebSocket.OPEN) {
    batchRemoteWs.send(JSON.stringify({ type: 'mirror', event }))
  }
}

// 从机画面点击切主控的位移阈值（按下与抬起几乎不动才算点击，避免拖动误触）
const BATCH_REMOTE_CLICK_THRESHOLD = 8
let batchRemotePressPos: Record<string, { x: number; y: number }> = {}

const batchRemoteOnPointer = (ev: PointerEvent, card: any) => {
  if (!card) return
  // 主控判定按 deviceId（卡片渲染顺序与帧索引无关）
  const isSource = card.deviceId === batchRemoteSource.value
  // canvas 以帧索引为键，与解码输出保持一致
  const cv = batchRemoteCanvasRefs.value[card.index]

  // ---- 非主控画面：单击（几乎无位移）切换主控，拖动不做任何事 ----
  if (!isSource) {
    if (ev.type === 'pointerdown') {
      batchRemotePressPos[card.deviceId] = { x: ev.clientX, y: ev.clientY }
    } else if (ev.type === 'pointerup') {
      const p = batchRemotePressPos[card.deviceId]
      delete batchRemotePressPos[card.deviceId]
      if (p) {
        const dx = ev.clientX - p.x
        const dy = ev.clientY - p.y
        if (Math.sqrt(dx * dx + dy * dy) < BATCH_REMOTE_CLICK_THRESHOLD) {
          setBatchRemoteSource(card.deviceId)
        }
      }
    }
    return
  }

  // ---- 主控画面：同步触摸到其他设备 ----
  // 先发事件再捕获指针：确保 pointerdown 一定发出去（capture 失败也不影响）
  const action = ev.type === 'pointerdown' ? 0 : ev.type === 'pointerup' ? 1 : 2
  if (ev.type === 'pointerdown' || ev.type === 'pointerup') {
    const rect = cv ? cv.getBoundingClientRect() : null
    if (rect && rect.width > 0) {
      const x = (ev.clientX - rect.left) * card.width / rect.width
      const y = (ev.clientY - rect.top) * card.height / rect.height
      batchRemoteSendMirror({ kind: 'touch', action, x: Math.round(x), y: Math.round(y), w: card.width, h: card.height })
    }
    if (ev.type === 'pointerdown' && cv) {
      try { cv.setPointerCapture(ev.pointerId) } catch { /* ignore */ }
    }
    return
  }
  // pointermove
  if (!cv) return
  const rect = cv.getBoundingClientRect()
  if (rect.width === 0 || rect.height === 0) return
  const x = (ev.clientX - rect.left) * card.width / rect.width
  const y = (ev.clientY - rect.top) * card.height / rect.height
  batchRemoteSendMirror({ kind: 'touch', action, x: Math.round(x), y: Math.round(y), w: card.width, h: card.height })
}

const batchRemoteHandleJson = (msg: any) => {
  if (msg.type === 'meta') {
    // index 来自后端帧索引，不能依赖 meta 到达顺序（多设备并行启动会乱序）
    const exists = batchRemoteCards.value.some(c => c.deviceId === msg.deviceId)
    if (!exists) {
      batchRemoteCards.value.push({
        deviceId: msg.deviceId, index: msg.index ?? batchRemoteCards.value.length,
        name: msg.name,
        width: msg.width, height: msg.height, ready: true,
        frames: 0, diag: '',
      })
    }
    batchRemoteStatus.value = ''
  } else if (msg.type === 'ready') {
    batchRemoteActive.value = true
    batchRemoteConnecting.value = false
    batchRemoteWsStatus.value = '运行中'
    const okIds = new Set((msg.devices || []).map((d: any) => d.deviceId))
    batchRemoteCards.value = batchRemoteCards.value.filter(d => okIds.has(d.deviceId))
    // ready 是权威设备清单：补救可能丢失的 meta（否则该设备所有帧会被丢弃）
    for (const d of msg.devices || []) {
      if (!batchRemoteCards.value.some(c => c.deviceId === d.deviceId)) {
        batchRemoteCards.value.push({
          deviceId: d.deviceId, index: d.index,
          name: d.name, width: d.width, height: d.height, ready: true,
          frames: 0, diag: '',
        })
      }
    }
  } else if (msg.type === 'error') {
    batchRemoteStatus.value = `${msg.deviceId || ''}: ${msg.message}`
    batchRemoteConnecting.value = false
  } else if (msg.type === 'closed') {
    const card = batchRemoteCards.value.find(d => d.deviceId === msg.deviceId)
    if (card) card.ready = false
  }
}

/**
 * 从 config 包（SPS/PPS）里解析 SPS，拼出正确的 codec 字符串
 * avc1.PPCCLL：profile_idc、constraint_flags、level_idc
 * 硬编码档位在设备编码器档位不同时会 configure 失败，导致该设备黑屏
 */
const batchRemoteDrawFrame = (index: number, frame: VideoFrame) => {
  const card = batchRemoteCards.value.find(c => c.index === index)
  const cv = batchRemoteCanvasRefs.value[index]
  if (card) card.frames++
  if (!cv) {
    if (card) card.diag = '无画布元素'
    frame.close()
    return
  }
  if (cv.width !== frame.displayWidth) cv.width = frame.displayWidth
  if (cv.height !== frame.displayHeight) cv.height = frame.displayHeight
  const ctx = cv.getContext('2d')!
  ctx.drawImage(frame, 0, 0)
  frame.close()
}

const batchRemoteHandleBinary = (buf: ArrayBuffer) => {
  const view = new DataView(buf)
  const index = view.getUint8(0)
  const kind = view.getUint8(1)
  const payload = new Uint8Array(buf, 2)
  // 按后端帧索引定位卡片，canvas/decoder 也统一用该索引为键
  const card = batchRemoteCards.value.find(c => c.index === index)
  if (!card) return

  if (kind === 1) {
    // config 包（SPS/PPS，Annex-B 起始码格式）：缓存下来拼在关键帧前喂给解码器
    batchRemoteConfigs[index] = payload
    if (batchRemoteDecoders[index]) {
      try { batchRemoteDecoders[index].close() } catch { /* ignore */ }
    }
    batchRemoteDecoders[index] = new VideoDecoder({
      output: (frame) => batchRemoteDrawFrame(index, frame),
      error: (e) => {
        card.diag = `解码失败: ${e.message}`
        batchRemoteStatus.value = `${card.name} 解码错误: ${e.message}`
      },
    })
    // 不传 description：Annex-B 模式；codec 档位从 SPS 实测值推导
    const codec = batchRemoteCodecFromSps(payload)
    try {
      batchRemoteDecoders[index].configure({ codec, optimizeForLatency: true })
      card.codec = codec
    } catch (e: any) {
      card.diag = `初始化失败(${codec}): ${e.message || e}`
      batchRemoteStatus.value = `${card.name} 解码器初始化失败 (${codec}): ${e.message || e}`
    }
  } else if (kind === 2) {
    const dec = batchRemoteDecoders[index]
    if (dec && dec.state === 'configured') {
      // 关键帧前拼接 config（SPS/PPS），保证随时接入都能解码
      const cfg = batchRemoteConfigs[index]
      const isKey = batchRemoteIsKeyframe(payload)
      const data = isKey && cfg ? concatU8(cfg, payload) : payload
      const chunk = new EncodedVideoChunk({
        type: isKey ? 'key' : 'delta',
        timestamp: performance.now() * 1000,
        data,
      })
      try { dec.decode(chunk) } catch { /* 队列满时丢弃 */ }
    }
  }
}

const startBatchRemote = () => {
  if (batchSelectedDevices.value.size === 0) {
    showMessage('请先勾选要投屏的设备', '批量投屏')
    return
  }
  batchRemoteConnecting.value = true
  batchRemoteStatus.value = '正在建立会话...'
  batchRemoteCards.value = []
  batchRemoteConfigs = {}
  batchRemoteDecoders = {}
  batchRemoteSource.value = [...batchSelectedDevices.value][0]

  const wsUrl = API_BASE.value.replace(/^http/, 'ws') + '/ws/remote'
  batchRemoteWs = new WebSocket(wsUrl)
  batchRemoteWs.binaryType = 'arraybuffer'
  batchRemoteWsStatus.value = '连接中'

  batchRemoteWs.onopen = () => {
    batchRemoteWs!.send(JSON.stringify({
      type: 'start',
      devices: [...batchSelectedDevices.value],
      source: batchRemoteSource.value,
    }))
  }

  batchRemoteWs.onmessage = (ev) => {
    if (typeof ev.data === 'string') {
      batchRemoteHandleJson(JSON.parse(ev.data))
    } else {
      batchRemoteHandleBinary(ev.data as ArrayBuffer)
    }
  }

  batchRemoteWs.onclose = () => {
    batchRemoteWsStatus.value = '已断开'
    batchRemoteActive.value = false
    batchRemoteConnecting.value = false
  }

  batchRemoteWs.onerror = () => {
    batchRemoteStatus.value = 'WebSocket 连接失败，请确认后端已启动'
    batchRemoteConnecting.value = false
  }
}

const stopBatchRemote = () => {
  if (batchRemoteWs) {
    try { batchRemoteWs.send(JSON.stringify({ type: 'stop' })) } catch { /* ignore */ }
    batchRemoteWs.close()
    batchRemoteWs = null
  }
  Object.values(batchRemoteDecoders).forEach(d => { try { d.close() } catch { /* ignore */ } })
  batchRemoteDecoders = {}
  batchRemoteConfigs = {}
  batchRemoteCards.value = []
  batchRemoteActive.value = false
  batchRemoteConnecting.value = false
  batchRemoteWsStatus.value = '未连接'
  batchRemoteStatus.value = ''
}

// ==================== 模拟脚本 ====================

const batchRebootLoading = ref(false)
const batchInstallLoading = ref(false)

// 批量 Shell 相关
const batchShellLoading = ref(false)
const batchShellResults = ref<Array<{
  deviceId: string;
  success: boolean;
  stdout: string;
  stderr: string;
}>>([])
const showBatchShellResults = ref(false)

const batchShellCommand = async () => {
  const deviceIds = Array.from(batchSelectedDevices.value)
  if (deviceIds.length === 0) {
    showMessage('请先选择设备')
    return
  }

  // 使用 prompt 弹窗输入命令
  modalType.value = 'prompt'
  modalTitle.value = '批量 Shell 命令'
  modalMessage.value = '请输入要执行的 shell 命令'
  modalPlaceholder.value = '例如: ls -la /sdcard'
  modalConfirmText.value = '执行'
  modalVariant.value = 'info'
  modalCallback.value = async (command?: string) => {
    if (!command || !command.trim()) {
      showMessage('请输入命令', '错误')
      return
    }

    batchShellLoading.value = true
    showBatchShellResults.value = false
    try {
      const response = await axios.post(`${API_BASE.value}/api/device/shell/batch`, {
        deviceIds,
        command: command.trim()
      })

      if (response.data.code === 200) {
        const data = response.data.data
        batchShellResults.value = data.results || []
        showBatchShellResults.value = true
        showMessage(`批量执行完成：成功 ${data.successCount} 个，失败 ${data.failedCount} 个`, '批量 Shell')
      } else {
        showMessage(response.data.message || '批量执行失败', '错误')
      }
    } catch (error: any) {
      console.error('批量 Shell 执行失败:', error)
      showMessage(`批量执行失败: ${error.message}`, '错误')
    } finally {
      batchShellLoading.value = false
      modalVisible.value = false
    }
  }
  modalVisible.value = true
}

// 批量按键：选按键后对所有勾选设备执行 input keyevent（复用批量 Shell 接口）
const batchKeyLoading = ref(false)
const batchKeyevent = async () => {
  const deviceIds = Array.from(batchSelectedDevices.value)
  if (deviceIds.length === 0) {
    showMessage('请先选择设备')
    return
  }

  showList(
    SCRIPT_COMMON_KEYS.map(k => ({ value: String(k.code), label: k.name, desc: `keyevent ${k.code}` })),
    async (value?: string) => {
      if (!value) return
      batchKeyLoading.value = true
      try {
        const response = await axios.post(`${API_BASE.value}/api/device/shell/batch`, {
          deviceIds,
          command: `input keyevent ${Number(value)}`
        })
        if (response.data.code === 200) {
          const data = response.data.data
          showMessage(`批量按键完成：成功 ${data.successCount} 个，失败 ${data.failedCount} 个`, '批量按键')
        } else {
          showMessage(response.data.message || '批量按键失败', '错误')
        }
      } catch (error: any) {
        console.error('批量按键失败:', error)
        showMessage(`批量按键失败: ${error.message}`, '错误')
      } finally {
        batchKeyLoading.value = false
      }
    },
    '批量按键',
    '暂无按键',
    '执行'
  )
}

const batchReboot = async () => {
  const deviceIds = Array.from(batchSelectedDevices.value)
  if (deviceIds.length === 0) {
    showMessage('请先选择设备')
    return
  }

  batchRebootLoading.value = true
  try {
    const response = await axios.post(`${API_BASE.value}/api/device/reboot/batch`, { deviceIds })
    if (response.data.code === 200) {
      showMessage(response.data.message || '重启命令已发送', '批量重启')

      // 延迟刷新设备列表
      setTimeout(() => {
        fetchDevices()
      }, 2000)
    } else {
      showMessage(response.data.message || '批量重启失败', '错误')
    }
  } catch (error: any) {
    console.error('批量重启失败:', error)
    showMessage(`批量重启失败: ${error.message}`, '错误')
  } finally {
    batchRebootLoading.value = false
  }
}

const batchInstallApp = async () => {
  const deviceIds = Array.from(batchSelectedDevices.value)
  if (deviceIds.length === 0) {
    showMessage('请先选择设备')
    return
  }

  const input = document.createElement('input')
  input.type = 'file'
  input.accept = '.apk'

  input.onchange = async (e: Event) => {
    const target = e.target as HTMLInputElement
    const file = target.files?.[0]
    if (!file) return

    batchInstallLoading.value = true
    try {
      const formData = new FormData()
      formData.append('apk', file)
      formData.append('deviceIds', deviceIds.join(','))

      const response = await axios.post(
        `${API_BASE.value}/api/device/app/install/batch`,
        formData,
        { headers: { 'Content-Type': 'multipart/form-data' } }
      )

      if (response.data.code === 200) {
        showMessage(response.data.message || '批量安装命令已发送', '批量安装')
      } else {
        showMessage(response.data.message || '批量安装失败', '错误')
      }
    } catch (error: any) {
      console.error('批量安装失败:', error)
      showMessage(`批量安装失败: ${error.message}`, '错误')
    } finally {
      batchInstallLoading.value = false
    }
  }

  input.click()
}

// 弹窗状态

onUnmounted(() => { stopBatchRemote() })

</script>

<template>
              <div v-if="activeTab === 'batch'" class="batch-page">
                <div class="batch-card">
                  <div class="card-header batch-header">
                    <div class="batch-header-left">
                      <span class="card-icon">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="18" height="18">
                          <rect x="3" y="3" width="7" height="7" rx="1"/>
                          <rect x="14" y="3" width="7" height="7" rx="1"/>
                          <rect x="3" y="14" width="7" height="7" rx="1"/>
                          <rect x="14" y="14" width="7" height="7" rx="1"/>
                        </svg>
                      </span>
                      <span>批量操作</span>
                    </div>
                    <span class="batch-selected-info">已选择 {{ selectedBatchCount }} 个设备</span>
                  </div>
                  <div class="batch-content">
                    <!-- 上半部分：设备列表 (60%) -->
                    <div class="batch-device-list">
                      <div class="batch-list-header">
                        <label class="batch-checkbox-label">
                          <input type="checkbox" :checked="batchSelectAllChecked" @change="toggleAllBatchDevices" />
                        </label>
                        <span class="col-device-id">设备ID</span>
                        <span class="col-model">
                          型号
                          <input type="text" v-model="batchModelFilter" class="model-filter-input" placeholder="筛选型号..." />
                        </span>
                        <span class="col-sn">SN号</span>
                      </div>
                      <div v-if="batchLoading" class="batch-loading">加载设备信息中...</div>
                      <div v-else-if="batchFilteredDevices.length === 0" class="batch-empty">{{ batchDeviceInfos.length === 0 ? '暂无可用设备' : '无匹配设备' }}</div>
                      <div v-else class="batch-list-body">
                        <label v-for="device in batchFilteredDevices" :key="device.deviceId" class="batch-device-row" :class="{ 'is-selected': batchSelectedDevices.has(device.deviceId) }">
                          <input type="checkbox" :checked="batchSelectedDevices.has(device.deviceId)" @change="toggleBatchDevice(device.deviceId)" />
                          <span class="col-device-id" :title="device.deviceId">{{ device.deviceId }}</span>
                          <span class="col-model">{{ device.model || '未知' }}</span>
                          <span class="col-sn">{{ device.sn || '未知' }}</span>
                        </label>
                      </div>
                    </div>
                    <!-- 下半部分：操作按钮区 (40%) -->
                    <div class="batch-action-area">
                      <div class="batch-action-grid">
                        <button class="batch-action-btn" @click="batchReboot" :disabled="batchRebootLoading">
                          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="16" height="16">
                            <polyline points="23 4 23 10 17 10"/>
                            <path d="M20.49 15a9 9 0 11-2.12-9.36L23 10"/>
                          </svg>
                          <span>{{ batchRebootLoading ? '重启中...' : '批量重启' }}</span>
                        </button>
                        <button class="batch-action-btn" @click="batchInstallApp" :disabled="batchInstallLoading">
                          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="16" height="16">
                            <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/>
                            <polyline points="7 10 12 15 17 10"/>
                            <line x1="12" y1="15" x2="12" y2="3"/>
                          </svg>
                          <span>{{ batchInstallLoading ? '安装中...' : '批量安装' }}</span>
                        </button>
                        <button class="batch-action-btn" @click="batchShellCommand" :disabled="batchShellLoading">
                          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="16" height="16">
                            <polyline points="4 17 10 11 4 5"/>
                            <line x1="12" y1="19" x2="20" y2="19"/>
                          </svg>
                          <span>{{ batchShellLoading ? '执行中...' : '批量Shell' }}</span>
                        </button>
                        <button class="batch-action-btn" @click="batchRemoteActive ? stopBatchRemote() : startBatchRemote()" :disabled="batchRemoteConnecting && !batchRemoteActive">
                          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="16" height="16">
                            <rect x="2" y="4" width="20" height="14" rx="2"/>
                            <line x1="8" y1="21" x2="16" y2="21"/>
                            <line x1="12" y1="18" x2="12" y2="21"/>
                          </svg>
                          <span>{{ batchRemoteConnecting && !batchRemoteActive ? '连接中...' : batchRemoteActive ? '结束投屏' : '批量投屏' }}</span>
                        </button>
                        <button class="batch-action-btn" @click="batchKeyevent" :disabled="batchKeyLoading">
                          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="16" height="16">
                            <rect x="4" y="2" width="16" height="20" rx="2"/>
                            <line x1="8" y1="6" x2="16" y2="6"/>
                            <line x1="12" y1="6" x2="12" y2="2"/>
                          </svg>
                          <span>{{ batchKeyLoading ? '执行中...' : '批量按键' }}</span>
                        </button>
                      </div>

                      <!-- Shell 执行结果 -->
                      <div v-if="showBatchShellResults && batchShellResults.length > 0" class="batch-shell-results">
                        <div class="shell-results-header">
                          <span>批量 Shell 执行结果</span>
                          <button class="shell-close-btn" @click="showBatchShellResults = false">×</button>
                        </div>
                        <div class="shell-results-list">
                          <div v-for="result in batchShellResults" :key="result.deviceId" class="shell-result-item">
                            <div class="shell-result-device">
                              <span class="shell-status-icon" :class="{ 'shell-success': result.success, 'shell-failed': !result.success }">
                                {{ result.success ? '✓' : '✗' }}
                              </span>
                              <span class="shell-device-id">{{ result.deviceId }}</span>
                            </div>
                            <div v-if="result.stdout" class="shell-output-section">
                              <div class="shell-output-label">标准输出:</div>
                              <pre class="shell-output">{{ result.stdout }}</pre>
                            </div>
                            <div v-if="result.stderr" class="shell-output-section shell-error">
                              <div class="shell-output-label">错误输出:</div>
                              <pre class="shell-output shell-error-text">{{ result.stderr }}</pre>
                            </div>
                            <div v-if="!result.success && !result.stderr" class="shell-output-section shell-error">
                              <div class="shell-output-label">执行失败</div>
                            </div>
                          </div>
                        </div>
                      </div>
                    </div>
                  </div>
                </div>

                <!-- 批量投屏画面区（会话建立后显示） -->
                <div v-if="batchRemoteActive || batchRemoteCards.length > 0" class="batch-remote-area">
                  <div class="batch-remote-header">
                    <span>批量投屏（主控画面操作同步到其他设备 · 单击从机画面或点「设为主控」切换主控）</span>
                    <span class="batch-remote-status" :class="{ live: batchRemoteActive }">{{ batchRemoteWsStatus }}</span>
                  </div>
                  <div v-if="batchRemoteStatus" class="batch-remote-error">{{ batchRemoteStatus }}</div>
                  <div class="batch-remote-grid" :style="{ gridTemplateColumns: `repeat(${batchRemoteGridCols}, 1fr)` }">
                    <div
                      v-for="card in batchRemoteCards"
                      :key="card.deviceId"
                      class="batch-remote-tile"
                      :class="{ source: card.deviceId === batchRemoteSource, dead: !card.ready }"
                    >
                      <div class="batch-remote-tile-header">
                        <span class="batch-remote-tile-name">
                          {{ card.name }}
                          <span class="batch-remote-tile-diag">
                            idx{{ card.index }} · {{ card.frames }}帧{{ card.codec ? ' · ' + card.codec : '' }}{{ card.diag ? ' · ' + card.diag : '' }}
                          </span>
                        </span>
                        <button
                          class="batch-remote-tile-tag"
                          :class="{ active: card.deviceId === batchRemoteSource }"
                          :disabled="card.deviceId === batchRemoteSource"
                          @click.stop="setBatchRemoteSource(card.deviceId)"
                        >
                          {{ card.deviceId === batchRemoteSource ? '★ 主控' : '设为主控' }}
                        </button>
                      </div>
                      <!-- 画面容器：撑满卡片剩余空间，canvas 用 object-fit:contain 等比完整显示 -->
                      <div class="batch-remote-tile-body">
                        <canvas
                          :ref="(el) => setBatchRemoteCanvasRef(el, card.index)"
                          class="batch-remote-canvas"
                          @pointerdown.stop="batchRemoteOnPointer($event, card)"
                          @pointermove.stop="batchRemoteOnPointer($event, card)"
                          @pointerup.stop="batchRemoteOnPointer($event, card)"
                          @pointercancel.stop="batchRemoteOnPointer($event, card)"
                        ></canvas>
                        <div v-if="!card.ready" class="batch-remote-tile-loading">等待画面...</div>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
</template>
