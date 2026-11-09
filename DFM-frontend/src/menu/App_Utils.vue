<script lang="ts" setup>
import axios from 'axios'
import { onMounted, onUnmounted, onBeforeUnmount, ref, computed, nextTick, watch } from 'vue';
import ModalDialog from '../components/ModalDialog.vue';
import FileEditorDialog from '../components/FileEditorDialog.vue';
import FileImageViewer from '../components/FileImageViewer.vue';
import * as echarts from 'echarts/core';
import { LineChart, BarChart, GaugeChart } from 'echarts/charts';
import { GridComponent, TooltipComponent, LegendComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import { useAppConfig } from '../composables/useAppConfig';

// 按需注册 ECharts 模块，避免全量引入导致打包体积膨胀
echarts.use([LineChart, BarChart, GaugeChart, GridComponent, TooltipComponent, LegendComponent, CanvasRenderer]);

const emit = defineEmits(['back']);

const API_BASE = ref('http://localhost:3000');

// ===== 全局配置 =====
const { config: appConfig, isFeatureOn } = useAppConfig();

// ===== Tab 注册表（key 对应 config.json 的 features.utilsTabs 键） =====
const TAB_ITEMS = [
  { key: 'info',    label: '设备信息' },
  { key: 'apps',    label: '应用管理' },
  { key: 'files',   label: '文件管理' },
  { key: 'keys',    label: '设备投屏' },
  { key: 'batch',   label: '批量操作' },
  { key: 'script',  label: '模拟脚本' },
  { key: 'serial',  label: '模拟串口' },
  { key: 'monitor', label: '性能监控' }
];

// 按配置过滤后的 Tab（未配置的默认显示）
const visibleTabs = computed(() =>
  TAB_ITEMS.filter(tab => isFeatureOn(`utilsTabs.${tab.key}`))
);

const activeTab = ref('info');

// 配置异步加载后，若当前 Tab 被关掉，自动跳到第一个可见 Tab
watch(visibleTabs, (tabs) => {
  if (tabs.length && !tabs.some(t => t.key === activeTab.value)) {
    activeTab.value = tabs[0].key;
  }
});

const loadConfig = async () => {
  try {
    const response = await fetch('/config.json');
    if (response.ok) {
      const config = await response.json();
      if (config.apiBase) {
        API_BASE.value = config.apiBase;
      }
    }
  } catch (error) {
    console.error('加载配置失败:', error);
  }
};

interface Device {
  deviceId: string;
  model?: string;
  status?: string;
}

interface AppInfo {
  packageName: string;
  appName?: string;
  versionName?: string;
  versionCode?: string;
}

interface FileInfo {
  name: string;
  path: string;
  isDirectory: boolean;
  size?: string;
  permissions?: string;
  mtime?: string;
}

// 文件列表排序：目录优先，再按所选字段（名称/大小/时间）
type FileSortKey = 'name' | 'size' | 'time'
const fileSortKey = ref<FileSortKey>('name')
const fileSortAsc = ref(true)
const sortedFiles = computed(() => {
  const list = [...files.value]
  const dirFirst = (a: FileInfo, b: FileInfo) => (a.isDirectory === b.isDirectory ? 0 : a.isDirectory ? -1 : 1)
  const cmp: Record<FileSortKey, (a: FileInfo, b: FileInfo) => number> = {
    name: (a, b) => a.name.localeCompare(b.name, 'zh-CN'),
    size: (a, b) => (Number(a.size) || 0) - (Number(b.size) || 0),
    time: (a, b) => (a.mtime || '').localeCompare(b.mtime || '')
  }
  list.sort((a, b) => dirFirst(a, b) || (fileSortAsc.value ? cmp[fileSortKey.value](a, b) : cmp[fileSortKey.value](b, a)))
  return list
})
const toggleFileSort = (key: FileSortKey) => {
  if (fileSortKey.value === key) {
    fileSortAsc.value = !fileSortAsc.value
  } else {
    fileSortKey.value = key
    fileSortAsc.value = true
  }
}

// 文件多选：勾选的条目路径集合（用于批量删除/移动）
const selectedFiles = ref<Set<string>>(new Set())
const selectedFileCount = computed(() => selectedFiles.value.size)
const fileAllSelected = computed(() => files.value.length > 0 && files.value.every(f => selectedFiles.value.has(f.path)))
const fileSomeSelected = computed(() => selectedFiles.value.size > 0 && !fileAllSelected.value)

const toggleFileSelection = (path: string) => {
  const next = new Set(selectedFiles.value)
  if (next.has(path)) {
    next.delete(path)
  } else {
    next.add(path)
  }
  selectedFiles.value = next
}

const toggleSelectAllFiles = () => {
  if (fileAllSelected.value) {
    selectedFiles.value = new Set()
  } else {
    const next = new Set(selectedFiles.value)
    files.value.forEach(f => next.add(f.path))
    selectedFiles.value = next
  }
}

const clearFileSelection = () => {
  selectedFiles.value = new Set()
}

interface DeviceInfo {
  deviceId: string;
  status: string;
  model?: string;
  version?: string;
  manufacturer?: string;
  deviceName?: string;
  displayVersion?: string;
  androidVersion?: string;
  kernelVersion?: string;
  buildDate?: string;
  codename?: string;
  unlockStatus?: string;
  sn?: string;
  wifiMac?: string;
  ethMac?: string;
}

const enterAction = ref({})
const devices = ref<Device[]>([])
const selectedDevice = ref('')
const deviceInfo = ref<DeviceInfo | null>(null)
const loading = ref(false)
const apps = ref<AppInfo[]>([])
const appLoading = ref(false)
const files = ref<FileInfo[]>([])
const fileLoading = ref(false)
const currentPath = ref('/sdcard')
const route = ref('')
const wirelessIp = ref('192.168.0.')
const wirelessStatus = ref('')

// 批量操作相关
const batchDeviceInfos = ref<Array<{
  deviceId: string;
  model?: string;
  sn?: string;
  status?: string;
}>>([])
const batchSelectedDevices = ref<Set<string>>(new Set())
const batchLoading = ref(false)
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

const loadBatchDeviceInfos = async () => {
  if (devices.value.length === 0) {
    batchDeviceInfos.value = []
    return
  }
  batchLoading.value = true
  try {
    const infos = await Promise.all(
      devices.value.map(async (d) => {
        try {
          const res = await axios.post(`${API_BASE.value}/api/device/sn`, { deviceId: d.deviceId })
          if (res.data.code === 200 && res.data.data) {
            return {
              deviceId: d.deviceId,
              model: d.model,
              sn: res.data.data.sn || '未知',
              status: d.status
            }
          }
        } catch (e) {
          console.error(`获取设备 ${d.deviceId} 信息失败:`, e)
        }
        return { deviceId: d.deviceId, model: d.model, sn: '未知', status: d.status }
      })
    )
    batchDeviceInfos.value = infos
    // 清理已断开设备的选中状态
    const deviceIds = new Set(infos.map(i => i.deviceId))
    const newSelected = new Set<string>()
    batchSelectedDevices.value.forEach(id => {
      if (deviceIds.has(id)) newSelected.add(id)
    })
    batchSelectedDevices.value = newSelected
  } finally {
    batchLoading.value = false
  }
}

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
const batchRemoteCodecFromSps = (config: Uint8Array): string => {
  const hex = (n: number) => n.toString(16).padStart(2, '0')
  for (let i = 0; i + 4 < config.length; i++) {
    let pos = -1
    if (config[i] === 0 && config[i + 1] === 0 && config[i + 2] === 0 && config[i + 3] === 1) {
      pos = i + 4
    } else if (config[i] === 0 && config[i + 1] === 0 && config[i + 2] === 1) {
      pos = i + 3
    }
    if (pos > 0 && pos + 3 < config.length && (config[pos] & 0x1F) === 7) {
      return `avc1.${hex(config[pos + 1])}${hex(config[pos + 2])}${hex(config[pos + 3])}`
    }
  }
  return 'avc1.640028'
}

const batchRemoteIsKeyframe = (data: Uint8Array): boolean => {
  // Annex-B：扫描 NALU 起始码（00 00 01 / 00 00 00 01），任一 NAL 类型为 5（IDR）即关键帧
  if (data.length < 5) return false
  for (let i = 0; i + 4 < data.length; i++) {
    if (data[i] === 0 && data[i + 1] === 0 && data[i + 2] === 1) {
      if ((data[i + 3] & 0x1F) === 5) return true
    } else if (data[i] === 0 && data[i + 1] === 0 && data[i + 2] === 0 && data[i + 3] === 1) {
      if ((data[i + 4] & 0x1F) === 5) return true
    }
  }
  return false
}

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

const concatU8 = (a: Uint8Array, b: Uint8Array): Uint8Array => {
  const out = new Uint8Array(a.length + b.length)
  out.set(a, 0)
  out.set(b, a.length)
  return out
}

// 画面适配交给 CSS（见 .batch-remote-tile-body / .batch-remote-canvas）。
// 不做 JS 测高：投屏区是 flex:1 且卡片高度又影响它，测高会形成自反馈，
// 高度一路上涨或读到 0，表现为画面被裁 / 高度不适配。

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

const scriptCanvasRef = ref<HTMLCanvasElement | null>(null)
const scriptStreaming = ref(false)
const scriptWsStatus = ref('未连接')
const scriptRecording = ref(false)
const scriptSteps = ref<Array<{
  type: string;
  x1?: number; y1?: number;
  x2?: number; y2?: number;
  duration?: number;
  text?: string;
  apkName?: string;
  path?: string;
  packageName?: string;
  activity?: string;
  keycode?: number | string;
  action?: string;
  stream?: string;
  template?: string;
  threshold?: number;
  timeout?: number;
  interval: number;
  count: number;
}>>([])
const scriptRunning = ref(false)
// 每类操作的默认配置（录制时快照进步骤）
const scriptOpConfig = ref<Record<'tap' | 'swipe' | 'longpress', { interval: number; count: number }>>({
  tap: { interval: 500, count: 1 },
  swipe: { interval: 500, count: 1 },
  longpress: { interval: 500, count: 1 },
})
const scriptRepeat = ref(1)
const scriptSaving = ref(false)

let scriptWs: WebSocket | null = null
let scriptDecoder: VideoDecoder | null = null
let scriptCodecConfig: Uint8Array | null = null
let scriptMeta = { width: 1080, height: 2340 }
let scriptPointerActive = false
let scriptRemotePressing = false
let scriptPressStart = { x: 0, y: 0, time: 0 }
let scriptLongPressTimer: any = null
let scriptMoved = false

const SCRIPT_LONGPRESS_MS = 600

const scriptStepLabel = (step: any): string => {
  if (step.type === 'tap') return `点击 (${step.x1}, ${step.y1})`
  if (step.type === 'longpress') return `长按 (${step.x1}, ${step.y1}) ${step.duration}ms`
  if (step.type === 'input') return `输入「${step.text ?? ''}」`
  if (step.type === 'swipe') return `滑动 (${step.x1}, ${step.y1}) → (${step.x2}, ${step.y2})`
  if (step.type === 'installApk') return `安装APK ${step.apkName || '（未选择）'}`
  if (step.type === 'deleteFolder') return `删除文件夹 ${step.path || '（未填写）'}`
  if (step.type === 'startApp') return step.activity ? `启动 ${step.packageName}/${step.activity}` : `启动 ${step.packageName || '（未填写包名）'}`
  if (step.type === 'stopApp') return `停止 ${step.packageName || '（未填写包名）'}`
  if (step.type === 'uninstallApp') return `卸载 ${step.packageName || '（未填写包名）'}`
  if (step.type === 'keyevent') return `按键 ${scriptKeyName(step.keycode)}`
  if (step.type === 'volume') return `音量${{ up: '+', down: '-', mute: '静音' }[step.action as string] || ''}`
  if (step.type === 'reboot') return '重启设备'
  if (step.type === 'waitFor') return `等待组件 ${step.template || '（未选择模板）'}（阈值${step.threshold ?? 0.85}）`
  return step.type
}

// 常用按键（下拉选择用，存值仍是数字键值）
const SCRIPT_COMMON_KEYS: Array<{ code: number; name: string }> = [
  { code: 4, name: '返回' },
  { code: 3, name: '主页' },
  { code: 187, name: '多任务' },
  { code: 26, name: '电源/锁屏' },
  { code: 24, name: '音量+' },
  { code: 25, name: '音量-' },
  { code: 164, name: '静音键' },
  { code: 82, name: '菜单' },
  { code: 23, name: '方向键·确认' },
  { code: 19, name: '方向键·上' },
  { code: 20, name: '方向键·下' },
  { code: 21, name: '方向键·左' },
  { code: 22, name: '方向键·右' },
  { code: 66, name: '回车' },
  { code: 67, name: '退格' },
  { code: 84, name: '搜索' },
]

const scriptKeyName = (keycode: number | string | undefined): string => {
  const hit = SCRIPT_COMMON_KEYS.find(k => k.code === Number(keycode))
  return hit ? `${hit.name}（${hit.code}）` : `键值 ${keycode ?? '?'}`
}

const scriptKeyPresetOf = (step: any): string => {
  const hit = SCRIPT_COMMON_KEYS.find(k => k.code === Number(step.keycode))
  return hit ? String(hit.code) : 'custom'
}

const onScriptKeyPresetChange = (step: any, value: string | number) => {
  // el-select 的 change 直接传选中值（原生 select 事件在别处使用）
  step.keycode = value === 'custom' ? '' : Number(value)
}

// 设备动作类步骤（走 adb，不依赖画面录制）
const SCRIPT_ADB_STEPS: Array<{ type: string; label: string; icon: string }> = [
  { type: 'installApk', label: '安装APK', icon: '📦' },
  { type: 'deleteFolder', label: '删除文件夹', icon: '🗑️' },
  { type: 'startApp', label: '启动应用', icon: '🚀' },
  { type: 'stopApp', label: '停止应用', icon: '⏹️' },
  { type: 'uninstallApp', label: '卸载应用', icon: '🧹' },
  { type: 'keyevent', label: '模拟按键', icon: '🔘' },
  { type: 'volume', label: '音量控制', icon: '🔊' },
  { type: 'reboot', label: '重启设备', icon: '🔄' },
]

// 已上传的 APK 列表（安装APK步骤的下拉数据源）
const scriptApkOptions = ref<Array<{ name: string; size: number }>>([])
const scriptApkLoading = ref(false)

const fetchScriptApks = async () => {
  scriptApkLoading.value = true
  try {
    const res = await axios.post(`${API_BASE.value}/api/device/pipeline/list-apks`)
    if (res.data.code === 200) scriptApkOptions.value = res.data.data || []
  } catch {
    /* APK 列表拉取失败不阻断脚本编辑 */
  } finally {
    scriptApkLoading.value = false
  }
}

// 应用列表（启动/停止/卸载步骤的下拉数据源；中文名 -> 包名映射）
const scriptAppPackages = ref<string[]>([])
const scriptAppNames = ref<Record<string, string>>({})
const scriptAppLoading = ref(false)

const fetchScriptApps = async () => {
  if (!selectedDevice.value) return
  scriptAppLoading.value = true
  try {
    const res = await axios.post(`${API_BASE.value}/api/device/script/app-names`, { deviceId: selectedDevice.value })
    if (res.data.code === 200) {
      const map = res.data.data || {}
      scriptAppNames.value = map
      scriptAppPackages.value = Object.keys(map)
    }
  } catch {
    /* 应用列表拉取失败不阻断脚本编辑 */
  } finally {
    scriptAppLoading.value = false
  }
}

// 模板图列表（等待组件步骤的下拉数据源）
const scriptTemplateOptions = ref<Array<{ name: string; width: number; height: number }>>([])
const scriptTemplateLoading = ref(false)

const fetchScriptTemplates = async () => {
  scriptTemplateLoading.value = true
  try {
    const res = await axios.post(`${API_BASE.value}/api/device/script/list-templates`)
    if (res.data.code === 200) scriptTemplateOptions.value = res.data.data || []
  } catch {
    /* 模板列表拉取失败不阻断脚本编辑 */
  } finally {
    scriptTemplateLoading.value = false
  }
}

// 设备动作步骤的默认参数
const scriptAdbStepDefaults = (type: string): Record<string, any> => {
  switch (type) {
    case 'installApk': return { apkName: scriptApkOptions.value[0]?.name || '' }
    case 'deleteFolder': return { path: '/sdcard/' }
    case 'startApp': return { packageName: '', activity: '' }
    case 'stopApp':
    case 'uninstallApp': return { packageName: '' }
    case 'keyevent': return { keycode: 4 }
    case 'volume': return { action: 'up', stream: 'media' }
    default: return {}
  }
}

// ---------- 画面上的步骤标记 ----------

// 是否在投屏画面上标出脚本步骤位置
const scriptShowMarkers = ref(false)

// 拖拽编辑：当前被拖动的标记 { stepIndex, part }，part 为 start/end
const scriptDragging = ref<{ stepIndex: number; part: 'start' | 'end' } | null>(null)
// 正在编辑配置的步骤索引（展开 interval/count 编辑项）
const scriptEditingStep = ref<number | null>(null)

// 步骤标记颜色（按类型区分）
const SCRIPT_MARKER_COLORS: Record<string, string> = {
  tap: '#4caf50',
  swipe: '#2196f3',
  longpress: '#ff9800',
}

// 在画面上绘制脚本步骤标记（画布坐标系即设备分辨率）
const scriptDrawMarkers = (ctx: CanvasRenderingContext2D) => {
  // 框选中的矩形（虚线高亮）：必须在早退之前画，
  // 否则「显示步骤」关闭或步骤为空时拖框没有任何视觉反馈
  if (scriptCropRegion.value) {
    const r = scriptCropRegion.value
    const left = Math.min(r.x1, r.x2)
    const top = Math.min(r.y1, r.y2)
    const w = Math.abs(r.x2 - r.x1)
    const h = Math.abs(r.y2 - r.y1)
    ctx.save()
    ctx.strokeStyle = '#ff9800'
    ctx.lineWidth = 3
    ctx.setLineDash([10, 6])
    ctx.strokeRect(left, top, w, h)
    ctx.restore()
  }

  if (!scriptShowMarkers.value || scriptSteps.value.length === 0) return

  const radius = Math.max(18, Math.round(scriptMeta.width / 40))
  const fontSize = Math.max(14, Math.round(scriptMeta.width / 45))

  scriptSteps.value.forEach((step, idx) => {
    // 输入步骤与设备动作步骤没有坐标，画面上不画标记（序号也就不占位）
    if (step.type === 'input' || SCRIPT_ADB_STEPS.some(a => a.type === step.type)) return
    const color = SCRIPT_MARKER_COLORS[step.type] || '#4caf50'
    const x = step.x1
    const y = step.y1
    // 选中的步骤加粗描边，便于辨认
    const isSelected = scriptEditingStep.value === idx
    const isDragging = scriptDragging.value?.stepIndex === idx
    const lineWidth = Math.max(2, Math.round(scriptMeta.width / 300)) * (isSelected ? 2 : 1)

    // 点击/长按：画圆圈
    ctx.save()
    ctx.strokeStyle = color
    ctx.lineWidth = lineWidth
    ctx.beginPath()
    ctx.arc(x, y, radius, 0, Math.PI * 2)
    ctx.stroke()

    // 正在拖动的点：外圈光晕提示可抓取
    if (isDragging && scriptDragging.value?.part === 'start') {
      ctx.globalAlpha = 0.35
      ctx.fillStyle = color
      ctx.beginPath()
      ctx.arc(x, y, radius * 1.8, 0, Math.PI * 2)
      ctx.fill()
      ctx.globalAlpha = 1
    }

    // 滑动：起点圆圈 + 起点到终点的箭头
    if (step.type === 'swipe' && typeof step.x2 === 'number' && typeof step.y2 === 'number') {
      const x2 = step.x2
      const y2 = step.y2
      ctx.globalAlpha = 0.85
      ctx.beginPath()
      ctx.arc(x2, y2, radius, 0, Math.PI * 2)
      ctx.stroke()

      // 正在拖动终点时的光晕
      if (isDragging && scriptDragging.value?.part === 'end') {
        ctx.globalAlpha = 0.35
        ctx.fillStyle = color
        ctx.beginPath()
        ctx.arc(x2, y2, radius * 1.8, 0, Math.PI * 2)
        ctx.fill()
        ctx.globalAlpha = 0.85
      }

      // 箭头线
      ctx.beginPath()
      ctx.moveTo(x, y)
      ctx.lineTo(x2, y2)
      ctx.stroke()

      // 箭头头部
      const angle = Math.atan2(y2 - y, x2 - x)
      const headLen = radius * 0.9
      ctx.beginPath()
      ctx.moveTo(x2, y2)
      ctx.lineTo(x2 - headLen * Math.cos(angle - Math.PI / 6), y2 - headLen * Math.sin(angle - Math.PI / 6))
      ctx.moveTo(x2, y2)
      ctx.lineTo(x2 - headLen * Math.cos(angle + Math.PI / 6), y2 - headLen * Math.sin(angle + Math.PI / 6))
      ctx.stroke()
      ctx.globalAlpha = 1
    }
    ctx.restore()

    // 序号标签
    const label = String(idx + 1)
    ctx.save()
    ctx.font = `bold ${fontSize}px sans-serif`
    ctx.textAlign = 'center'
    ctx.textBaseline = 'middle'
    const tagR = radius * 0.85
    const tagX = x + radius * 1.1
    const tagY = y - radius * 1.1
    ctx.fillStyle = color
    ctx.beginPath()
    ctx.arc(tagX, tagY, tagR, 0, Math.PI * 2)
    ctx.fill()
    ctx.fillStyle = '#fff'
    ctx.fillText(label, tagX, tagY)
    ctx.restore()
  })
}

// 视频帧绘制后叠加标记
const scriptDrawFrame = (ctx: CanvasRenderingContext2D, frame: any) => {
  ctx.drawImage(frame, 0, 0)
  // 缓存一份画面供重绘使用（frame 随即被 close，不能直接持有）
  scriptCacheFrame(frame)
  scriptDrawMarkers(ctx)
}

// 缓存当前画面到离屏 canvas
let scriptFrameCanvas: HTMLCanvasElement | null = null
const scriptCacheFrame = (frame: any) => {
  if (!scriptFrameCanvas) {
    scriptFrameCanvas = document.createElement('canvas')
  }
  if (scriptFrameCanvas.width !== frame.displayWidth || scriptFrameCanvas.height !== frame.displayHeight) {
    scriptFrameCanvas.width = frame.displayWidth
    scriptFrameCanvas.height = frame.displayHeight
  }
  const fctx = scriptFrameCanvas.getContext('2d')!
  fctx.drawImage(frame, 0, 0)
}

// 开关切换或步骤变化时重绘标记（画面静止时没有新帧，需要手动重画）
const scriptRedrawMarkers = () => {
  const cv = scriptCanvasRef.value
  if (!cv || !scriptFrameCanvas) return
  const ctx = cv.getContext('2d')!
  ctx.drawImage(scriptFrameCanvas, 0, 0)
  scriptDrawMarkers(ctx)
}

// 标记命中半径（与绘制半径一致，另加一点余量便于点中）
const scriptMarkerHitRadius = () => Math.max(18, Math.round(scriptMeta.width / 40))

// 命中检测：找出点 (x, y) 命中的标记
// 滑动步骤有两个可拖动点（起点/终点），后添加的优先命中
const scriptHitTest = (x: number, y: number): { stepIndex: number; part: 'start' | 'end' } | null => {
  if (!scriptShowMarkers.value) return null
  const radius = scriptMarkerHitRadius()
  // 容差按触屏考虑，放宽到 1.5 倍半径
  const tolerance = radius * 1.5

  for (let idx = scriptSteps.value.length - 1; idx >= 0; idx--) {
    const step = scriptSteps.value[idx]
    // 输入步骤与设备动作步骤没有坐标，不参与命中
    if (step.type === 'input' || SCRIPT_ADB_STEPS.some(a => a.type === step.type)) continue
    const d1 = Math.hypot(x - step.x1, y - step.y1)
    if (d1 <= tolerance) return { stepIndex: idx, part: 'start' }
    if (step.type === 'swipe' && typeof step.x2 === 'number' && typeof step.y2 === 'number') {
      const d2 = Math.hypot(x - step.x2, y - step.y2)
      if (d2 <= tolerance) return { stepIndex: idx, part: 'end' }
    }
  }
  return null
}

// 移动标记到新坐标
const scriptMoveMarker = (target: { stepIndex: number; part: 'start' | 'end' }, x: number, y: number) => {
  const step = scriptSteps.value[target.stepIndex]
  if (!step) return
  if (target.part === 'end' && step.type === 'swipe') {
    step.x2 = x
    step.y2 = y
  } else {
    step.x1 = x
    step.y1 = y
  }
}

// ---------- 框选模板（等待组件出现步骤） ----------

// 框选模式：开启后在画面上拖框，松开即把框内画面存为模板
const scriptCropMode = ref(false)
// 正在框选的区域（设备坐标）
const scriptCropRegion = ref<{ x1: number; y1: number; x2: number; y2: number } | null>(null)

const toggleCropMode = () => {
  scriptCropMode.value = !scriptCropMode.value
  scriptCropRegion.value = null
  if (scriptCropMode.value) {
    showNotification('框选模式：在画面上拖出组件所在区域，松开保存为模板', 'info')
  }
  scriptRedrawMarkers()
}

// 松开鼠标：把框选区域从缓存帧裁出，上传后端保存为模板
const finishCropRegion = async () => {
  const region = scriptCropRegion.value
  scriptCropRegion.value = null
  scriptCropMode.value = false
  if (!region || !scriptFrameCanvas) return

  const left = Math.min(region.x1, region.x2)
  const top = Math.min(region.y1, region.y2)
  const w = Math.abs(region.x2 - region.x1)
  const h = Math.abs(region.y2 - region.y1)
  // 太小基本是误触：留在框选模式里让用户重框，不退出
  if (w < 12 || h < 12) {
    scriptCropMode.value = true
    showNotification('框选区域太小，请重新框住完整的组件图案', 'error')
    scriptRedrawMarkers()
    return
  }

  try {
    // 裁剪到临时 canvas，导出 base64 PNG（坐标基于缓存帧 = 当前画面）
    const crop = document.createElement('canvas')
    crop.width = w
    crop.height = h
    crop.getContext('2d')!.drawImage(scriptFrameCanvas, left, top, w, h, 0, 0, w, h)
    const dataUrl = crop.toDataURL('image/png')

    showPrompt('请输入模板名称', async (name?: string) => {
      if (!name || !name.trim()) { scriptRedrawMarkers(); return }
      try {
        const response = await axios.post(`${API_BASE.value}/api/device/script/save-template`, {
          name: name.trim(),
          image: dataUrl,
        })
        if (response.data.code === 200) {
          const d = response.data.data
          showNotification(`模板「${d.name}」已保存（${d.width}x${d.height}）`, 'success')
          // 模板就绪后立即加一个等待组件步骤引用它
          addScriptStep({
            type: 'waitFor',
            template: d.name,
            threshold: 0.85,
            timeout: 15,
            interval: 500,
            count: 1,
          })
          fetchScriptTemplates()
        } else {
          showNotification('保存模板失败: ' + (response.data.message || '未知错误'), 'error')
        }
      } catch (error: any) {
        showNotification('保存模板失败: ' + error.message, 'error')
      } finally {
        scriptRedrawMarkers()
      }
    }, `template_${Date.now() % 10000}`, '保存模板')
  } catch (error: any) {
    showNotification('裁剪画面失败: ' + error.message, 'error')
    scriptRedrawMarkers()
  }
}

// ---------- 屏幕流（复用 /ws/remote，单设备） ----------

const scriptHandleBinary = (buf: ArrayBuffer) => {
  // 收到任何数据都算连接活着（半开 TCP 检测的喂狗信号）
  scriptLastDataAt = Date.now()
  const view = new DataView(buf)
  const kind = view.getUint8(1)
  const payload = new Uint8Array(buf, 2)
  const cv = scriptCanvasRef.value
  if (!cv) return

  if (kind === 1) {
    scriptCodecConfig = payload
    if (scriptDecoder) {
      try { scriptDecoder.close() } catch { /* ignore */ }
    }
    scriptDecoder = new VideoDecoder({
      output: (frame) => {
        if (cv.width !== frame.displayWidth) cv.width = frame.displayWidth
        if (cv.height !== frame.displayHeight) cv.height = frame.displayHeight
        const ctx = cv.getContext('2d')!
        scriptDrawFrame(ctx, frame)
        scriptMeta = { width: frame.displayWidth, height: frame.displayHeight }
        frame.close()
      },
      error: (e) => { scriptWsStatus.value = `解码错误: ${e.message}` },
    })
    const codec = batchRemoteCodecFromSps(payload)
    try {
      scriptDecoder.configure({ codec, optimizeForLatency: true })
    } catch { /* codec 异常时等错误回调报 */ }
  } else if (kind === 2) {
    const dec = scriptDecoder
    if (dec && dec.state === 'configured') {
      const isKey = batchRemoteIsKeyframe(payload)
      const data = isKey && scriptCodecConfig ? concatU8(scriptCodecConfig, payload) : payload
      const chunk = new EncodedVideoChunk({
        type: isKey ? 'key' : 'delta',
        timestamp: performance.now() * 1000,
        data,
      })
      try { dec.decode(chunk) } catch { /* 队列满时丢弃 */ }
    }
  }
}

const startScriptStream = () => {
  if (!selectedDevice.value) return
  // 存活检测：旧连接引用还在但已不是 OPEN（close 事件异步未触发、或后端 session 已死），
  // 直接强制清理，否则这里 return 会导致切设备/重连时画面永久卡死
  if (scriptWs && scriptWs.readyState !== WebSocket.OPEN && scriptWs.readyState !== WebSocket.CONNECTING) {
    try { scriptWs.close() } catch { /* ignore */ }
    scriptWs = null
  }
  if (scriptWs) return

  scriptWsStatus.value = '连接中'
  const wsUrl = API_BASE.value.replace(/^http/, 'ws') + '/ws/remote'
  const thisWs = new WebSocket(wsUrl)
  scriptWs = thisWs
  thisWs.binaryType = 'arraybuffer'
  thisWs.onopen = () => {
    // 连上了就重置重连计数
    scriptReconnectAttempts = 0
    thisWs.send(JSON.stringify({ type: 'start', devices: [selectedDevice.value] }))
  }
  thisWs.onmessage = (ev) => {
    if (typeof ev.data === 'string') {
      const msg = JSON.parse(ev.data)
      if (msg.type === 'ready' && msg.devices?.length) {
        scriptMeta = { width: msg.devices[0].width, height: msg.devices[0].height }
        scriptStreaming.value = true
        scriptWsStatus.value = '实时'
      } else if (msg.type === 'meta') {
        scriptMeta = { width: msg.width, height: msg.height }
        scriptStreaming.value = true
        scriptWsStatus.value = '实时'
      } else if (msg.type === 'error') {
        scriptWsStatus.value = `错误: ${msg.message}`
        // 后端报错（设备断开/会话失败等）通常是致命的，主动断开触发重连流程
        try { thisWs.close() } catch { /* ignore */ }
      }
    } else {
      scriptHandleBinary(ev.data as ArrayBuffer)
    }
  }
  thisWs.onclose = () => {
    // 只有当前连接断开才算真断开：切设备时旧 ws 的 close 是异步触发的，
    // 可能晚于新 ws 创建，不能把新连接的引用/状态误清掉
    if (scriptWs === thisWs) {
      scriptWs = null
      scriptStreaming.value = false
      // 主动停流（切页签/切设备）不重连；其余（网络波动、后端报错）自动重连
      if (shouldStreamBeActive()) {
        scheduleScriptReconnect()
      } else {
        scriptWsStatus.value = '未连接'
      }
    }
  }
}

// 屏幕流是否应该处于激活状态（决定断线后要不要自动重连）
const shouldStreamBeActive = (): boolean => {
  return activeTab.value === 'script' && !!selectedDevice.value && !scriptManualStop
}

// 自动重连：指数退避（1s 起步，翻倍，上限 15s），连接成功后归零
let scriptReconnectTimer: ReturnType<typeof setTimeout> | null = null
let scriptReconnectAttempts = 0
let scriptManualStop = false

const scheduleScriptReconnect = () => {
  if (scriptReconnectTimer) return
  const delay = Math.min(1000 * Math.pow(2, scriptReconnectAttempts), 15000)
  scriptReconnectAttempts++
  scriptWsStatus.value = `连接中断，${Math.round(delay / 1000)}s 后重连（第 ${scriptReconnectAttempts} 次）`
  scriptReconnectTimer = setTimeout(() => {
    scriptReconnectTimer = null
    if (shouldStreamBeActive()) startScriptStream()
  }, delay)
}

const stopScriptStream = () => {
  // 主动停流：抑制自动重连
  scriptManualStop = true
  if (scriptReconnectTimer) { clearTimeout(scriptReconnectTimer); scriptReconnectTimer = null }
  if (scriptWs) {
    try { scriptWs.send(JSON.stringify({ type: 'stop' })) } catch { /* ignore */ }
    try { scriptWs.close() } catch { /* ignore */ }
    scriptWs = null
  }
  if (scriptDecoder) {
    try { scriptDecoder.close() } catch { /* ignore */ }
    scriptDecoder = null
  }
  scriptCodecConfig = null
  scriptStreaming.value = false
  scriptWsStatus.value = '未连接'
  scriptRecording.value = false
  // 流断开后不再接管键盘，避免打字继续发往已关闭的连接
  scriptReleaseKeyboard()
}

// 主动启动入口：清除手动停止标记
const beginScriptStream = () => {
  scriptManualStop = false
  startScriptStream()
}

// ---------- 半开 TCP 检测：流"活着"但 10 秒没有任何数据 → 强制断开触发重连 ----------
let scriptLastDataAt = 0
let scriptStallTimer: ReturnType<typeof setInterval> | null = null

const startStallWatch = () => {
  if (scriptStallTimer) return
  scriptLastDataAt = Date.now()
  scriptStallTimer = setInterval(() => {
    if (!scriptWs || !scriptStreaming.value) {
      scriptLastDataAt = Date.now()
      return
    }
    if (Date.now() - scriptLastDataAt > 10000) {
      // 数据流卡死：显示提示并强断，onclose 会触发自动重连
      scriptWsStatus.value = '画面超过 10 秒无数据，重连中...'
      try { scriptWs.close() } catch { /* ignore */ }
    }
  }, 2000)
}

const stopStallWatch = () => {
  if (scriptStallTimer) { clearInterval(scriptStallTimer); scriptStallTimer = null }
}

// 进入/离开 Tab 时开关屏幕流
watch(activeTab, async (tab) => {
  if (tab === 'script') {
    await nextTick()
    startStallWatch()
    if (selectedDevice.value) beginScriptStream()
    // 进入脚本页时准备安装APK / 等待组件 / 应用下拉的数据
    if (scriptApkOptions.value.length === 0) fetchScriptApks()
    fetchScriptTemplates()
    fetchScriptApps()
  } else {
    stopScriptStream()
    stopStallWatch()
  }
})

// 标记开关切换 / 步骤增减时重绘画面标记
watch(scriptShowMarkers, () => {
  nextTick(() => scriptRedrawMarkers())
})
watch(scriptSteps, () => {
  nextTick(() => scriptRedrawMarkers())
}, { deep: true })

// 切换设备时重连屏幕流
watch(selectedDevice, async (deviceId) => {
  stopScriptStream()
  // 清掉旧设备残留画面，避免新流接入前显示上一台设备的内容
  const cv = scriptCanvasRef.value
  if (cv) {
    const ctx = cv.getContext('2d')
    ctx?.clearRect(0, 0, cv.width, cv.height)
  }
  if (deviceId && activeTab.value === 'script') {
    await nextTick()
    beginScriptStream()
  }
})

// ---------- 录制：点/拖/长按自动分类；非录制：触控直接注入设备 ----------

const canvasPoint = (ev: PointerEvent) => {
  const cv = scriptCanvasRef.value!
  const rect = cv.getBoundingClientRect()
  return {
    x: Math.round((ev.clientX - rect.left) * scriptMeta.width / rect.width),
    y: Math.round((ev.clientY - rect.top) * scriptMeta.height / rect.height),
  }
}

// 非录制模式下把触控事件直接同步到设备（走 mirror 通道，同批量投屏）
const scriptSendTouch = (action: number, p: { x: number; y: number }) => {
  if (scriptWs && scriptWs.readyState === WebSocket.OPEN) {
    scriptWs.send(JSON.stringify({
      type: 'mirror',
      event: {
        kind: 'touch', action,
        x: p.x, y: p.y,
        w: scriptMeta.width, h: scriptMeta.height,
      },
    }))
  }
}

// 把一段文字发到设备（走 mirror 的 text 通道，后端 session.inject_text）
const scriptSendText = (text: string) => {
  if (!text) return
  if (scriptWs && scriptWs.readyState === WebSocket.OPEN) {
    scriptWs.send(JSON.stringify({ type: 'mirror', event: { kind: 'text', text } }))
  }
}

// ---------- 录制时捕获电脑键盘输入 ----------

// 点过画面后接管键盘，敲的字实时发到设备并记入步骤
const scriptKeyboardActive = ref(false)
// 连续输入的字符合并到同一个输入步骤（不是每个字母一步）
let scriptTypingStep: { text: string } | null = null
let scriptTypingIndex = -1
let scriptTypingTimer: any = null

// 结束当前输入步骤的合并窗口
const scriptFlushTyping = () => {
  clearTimeout(scriptTypingTimer)
  scriptTypingTimer = null
  scriptTypingStep = null
  scriptTypingIndex = -1
}

// 退出键盘接管状态
const scriptReleaseKeyboard = () => {
  scriptKeyboardActive.value = false
  scriptFlushTyping()
}

const scriptOnKeyDown = (ev: KeyboardEvent) => {
  if (!scriptKeyboardActive.value || !scriptStreaming.value) return
  // 正在页面上的输入框里打字时不拦截（右侧输入框、保存弹窗等），否则会吞掉正常输入
  const el = ev.target as HTMLElement | null
  if (el) {
    const tag = el.tagName
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || el.isContentEditable) return
  }
  // 只处理可见字符与退格/回车，其余（方向键、F1 等）交给默认行为
  const key = ev.key
  const isBackspace = key === 'Backspace'
  const isEnter = key === 'Enter'
  const isPrintable = key.length === 1 && !ev.ctrlKey && !ev.metaKey && !ev.altKey

  if (!isPrintable && !isBackspace && !isEnter) return

  // 吞掉默认行为，避免触发页面快捷键（Ctrl 组合已在上面排除）
  ev.preventDefault()

  if (isBackspace) {
    // 退格发给设备；录制时同步删掉步骤里最后一个字符
    scriptSendKey('backspace')
    if (!scriptRecording.value) return
    // 定位被编辑的输入步骤：优先用正在合并的那一步，否则退回列表里最后一个输入步骤。
    // 否则打完字停顿超过合并窗口（1.5s）再按退格，步骤不会变化——这在实际使用中最常见。
    let idx = scriptTypingIndex
    if (
      idx < 0 ||
      !scriptSteps.value[idx] ||
      scriptSteps.value[idx].type !== 'input'
    ) {
      idx = -1
      for (let i = scriptSteps.value.length - 1; i >= 0; i--) {
        if (scriptSteps.value[i].type === 'input') { idx = i; break }
      }
    }
    if (idx < 0) return
    const target = scriptSteps.value[idx]
    target.text = (target.text || '').slice(0, -1)
    // 把删除后的这一步继续接回合并窗口，后续输入仍追加到它后面
    scriptTypingStep = target
    scriptTypingIndex = idx
    // 步骤文字被删空了就移除这一步
    if (!target.text) {
      scriptSteps.value.splice(idx, 1)
      scriptFlushTyping()
      return
    }
    // 接回后同样按停顿判定结束，避免状态一直挂着
    clearTimeout(scriptTypingTimer)
    scriptTypingTimer = setTimeout(() => { scriptTypingStep = null; scriptTypingIndex = -1 }, 1500)
    return
  }

  if (isEnter) {
    scriptSendKey('enter')
    scriptFlushTyping()
    return
  }

  // 可打印字符：发到设备
  scriptSendText(key)

  if (scriptRecording.value) {
    if (!scriptTypingStep) {
      // 新建一个输入步骤；后续连续字符都追加进来
      const step = {
        type: 'input' as const,
        x1: 0, y1: 0,
        text: key,
        interval: scriptOpConfig.value.tap.interval,
        count: 1,
      }
      scriptSteps.value.push(step)
      scriptTypingIndex = scriptSteps.value.length - 1
      scriptTypingStep = step as any
      showNotification(`已记录第 ${scriptSteps.value.length} 步：${scriptStepLabel(step)}`, 'info')
    } else {
      scriptTypingStep.text += key
    }
    // 停顿一段时间后认为这段输入结束，下次输入另起一步
    clearTimeout(scriptTypingTimer)
    scriptTypingTimer = setTimeout(() => { scriptTypingStep = null; scriptTypingIndex = -1 }, 1500)
  }
}

// 特殊键：走 key 通道（后端 inject_keycode）
const scriptSendKey = (name: 'backspace' | 'enter') => {
  if (!scriptWs || scriptWs.readyState !== WebSocket.OPEN) return
  // Android KeyEvent：BACKSPACE=67，ENTER=66
  const code = name === 'backspace' ? 67 : 66
  scriptWs.send(JSON.stringify({ type: 'mirror', event: { kind: 'key', keycode: code, action: 0 } }))
  scriptWs.send(JSON.stringify({ type: 'mirror', event: { kind: 'key', keycode: code, action: 1 } }))
}


const scriptOnPointerDown = (ev: PointerEvent) => {
  if (!scriptStreaming.value) return
  ev.preventDefault()
  const p = canvasPoint(ev)

  // 框选模板模式：拖框裁剪画面当作「等待组件出现」的模板，不注入设备
  if (scriptCropMode.value) {
    try { scriptCanvasRef.value?.setPointerCapture(ev.pointerId) } catch { /* ignore */ }
    scriptCropRegion.value = { x1: p.x, y1: p.y, x2: p.x, y2: p.y }
    return
  }

  // 点画面即接管键盘：之后敲电脑键盘的文字会实时发到设备并在录制时记为输入步骤
  scriptKeyboardActive.value = true
  // 点别处意味着上一段输入结束
  scriptFlushTyping()

  // 显示步骤时优先命中标记：命中则进入拖拽编辑，不注入设备也不记录新步骤
  if (scriptShowMarkers.value && !scriptRecording.value) {
    const hit = scriptHitTest(p.x, p.y)
    if (hit) {
      try { scriptCanvasRef.value?.setPointerCapture(ev.pointerId) } catch { /* ignore */ }
      scriptDragging.value = hit
      scriptEditingStep.value = hit.stepIndex
      return
    }
  }

  if (!scriptRecording.value) {
    // 远控模式：直接注入
    try { scriptCanvasRef.value?.setPointerCapture(ev.pointerId) } catch { /* ignore */ }
    scriptRemotePressing = true
    scriptSendTouch(0, p)
    return
  }
  // 录制模式：事件照常注入设备（记录的同时真实生效），并开始判定手势类型
  try { scriptCanvasRef.value?.setPointerCapture(ev.pointerId) } catch { /* ignore */ }
  scriptRemotePressing = true
  scriptSendTouch(0, p)
  scriptPointerActive = true
  scriptMoved = false
  scriptPressStart = { x: p.x, y: p.y, time: Date.now() }
  // 长按计时：600ms 未移动且未松开 → 长按步骤
  scriptLongPressTimer = setTimeout(() => {
    if (scriptPointerActive && !scriptMoved) {
      scriptPointerActive = false
      addScriptStep({
        type: 'longpress',
        x1: p.x, y1: p.y,
        duration: SCRIPT_LONGPRESS_MS + 400,
        ...scriptOpConfig.value.longpress,
      })
    }
  }, SCRIPT_LONGPRESS_MS)
}

const scriptOnPointerMove = (ev: PointerEvent) => {
  if (!scriptStreaming.value) return
  const p = canvasPoint(ev)

  // 框选中：更新框选区域并重绘
  if (scriptCropMode.value && scriptCropRegion.value) {
    scriptCropRegion.value.x2 = p.x
    scriptCropRegion.value.y2 = p.y
    scriptRedrawMarkers()
    return
  }

  // 拖拽标记：更新坐标并重绘
  if (scriptDragging.value) {
    scriptMoveMarker(scriptDragging.value, p.x, p.y)
    scriptRedrawMarkers()
    return
  }

  if (!scriptRemotePressing) return
  // 移动事件注入（远控与录制模式都需要，录制时靠它让设备产生真实滑动）
  if (!scriptRecording.value || scriptPointerActive) scriptSendTouch(2, p)
  if (!scriptPointerActive) return
  const dist = Math.hypot(p.x - scriptPressStart.x, p.y - scriptPressStart.y)
  if (dist > 30) {
    scriptMoved = true
    clearTimeout(scriptLongPressTimer)
  }
}

const scriptOnPointerUp = (ev: PointerEvent) => {
  if (!scriptStreaming.value) return
  const p = canvasPoint(ev)

  // 框选结束：裁剪画面保存为模板
  if (scriptCropMode.value && scriptCropRegion.value) {
    finishCropRegion()
    return
  }

  // 拖拽结束
  if (scriptDragging.value) {
    const moved = scriptSteps.value[scriptDragging.value.stepIndex]
    scriptDragging.value = null
    if (moved) {
      showNotification(
        `已调整第 ${scriptEditingStep.value! + 1} 步：${scriptStepLabel(moved)}`,
        'info'
      )
    }
    return
  }

  // 抬起事件注入（远控与录制模式都需要）
  if (scriptRemotePressing) scriptSendTouch(1, p)
  scriptRemotePressing = false
  if (!scriptRecording.value) return
  if (!scriptPointerActive) return
  clearTimeout(scriptLongPressTimer)
  scriptPointerActive = false
  const elapsed = Date.now() - scriptPressStart.time
  if (scriptMoved) {
    // 拖动 → 滑动步骤
    addScriptStep({
      type: 'swipe',
      x1: scriptPressStart.x, y1: scriptPressStart.y,
      x2: p.x, y2: p.y,
      ...scriptOpConfig.value.swipe,
    })
  } else if (elapsed < SCRIPT_LONGPRESS_MS) {
    // 快速点按 → 点击步骤
    addScriptStep({
      type: 'tap',
      x1: scriptPressStart.x, y1: scriptPressStart.y,
      ...scriptOpConfig.value.tap,
    })
  }
  // elapsed >= 600ms 且未移动的长按已在 timer 里记录
}

// 指针被中断（取消/离开窗口）：静默结束拖拽；框选中丢弃当前框但保留模式
const scriptOnPointerCancel = () => {
  if (scriptCropMode.value) {
    scriptCropRegion.value = null
    scriptRedrawMarkers()
    return
  }
  if (scriptDragging.value) {
    scriptDragging.value = null
    return
  }
  scriptRemotePressing = false
  scriptPointerActive = false
  clearTimeout(scriptLongPressTimer)
}

const addScriptStep = (step: any) => {
  scriptSteps.value.push(step)
  showNotification(`已记录第 ${scriptSteps.value.length} 步：${scriptStepLabel(step)}`, 'info')
}

// 添加设备动作类步骤（安装APK/删除文件夹/应用管理/按键/音量/重启）
const addScriptAdbStep = (type: string) => {
  if (type === 'installApk' && scriptApkOptions.value.length === 0) {
    fetchScriptApks()
  }
  // 应用管理类步骤用应用列表做下拉：进脚本页时可能列表还是旧的（刚卸载过应用），刷新一次
  if (type === 'startApp' || type === 'stopApp' || type === 'uninstallApp') {
    fetchScriptApps()
  }
  addScriptStep({
    type,
    ...scriptAdbStepDefaults(type),
    interval: 500,
    count: 1,
  })
}

const removeScriptStep = (idx: number) => {
  scriptSteps.value.splice(idx, 1)
}

const clearScriptSteps = () => {
  scriptSteps.value = []
}

// 批量设置所有步骤的执行间隔（毫秒）
const setAllIntervals = () => {
  if (scriptSteps.value.length === 0) return
  showPrompt(
    '请输入执行间隔(ms)，应用到全部步骤',
    (input?: string) => {
      const n = parseInt(input || '', 10)
      if (isNaN(n) || n < 0) {
        showNotification('请输入不小于 0 的整数', 'error')
        return
      }
      const capped = Math.min(n, 600000)
      for (const s of scriptSteps.value) {
        s.interval = capped
      }
      showNotification(`已将 ${scriptSteps.value.length} 个步骤的间隔统一设为 ${capped}ms`, 'success')
    },
    String(scriptSteps.value[0]?.interval ?? 500),
    '统一间隔'
  )
}

// 点击步骤：选中并展开执行参数（再点一次收起）
const toggleStepEdit = (idx: number) => {
  scriptEditingStep.value = scriptEditingStep.value === idx ? null : idx
}

// 拖拽排序步骤：原生 draggable，拖到某一步上方即插入到该位置
const scriptStepDragFrom = ref<number | null>(null)
const scriptStepDragOver = ref<number | null>(null)

const onScriptStepDragStart = (idx: number, ev: DragEvent) => {
  scriptStepDragFrom.value = idx
  if (ev.dataTransfer) {
    ev.dataTransfer.effectAllowed = 'move'
    // Firefox 需要 setData 才会真正进入拖拽状态
    ev.dataTransfer.setData('text/plain', String(idx))
  }
}

const onScriptStepDragOver = (idx: number, ev: DragEvent) => {
  ev.preventDefault()
  if (ev.dataTransfer) ev.dataTransfer.dropEffect = 'move'
  scriptStepDragOver.value = idx
}

const onScriptStepDragLeave = (idx: number) => {
  if (scriptStepDragOver.value === idx) {
    scriptStepDragOver.value = null
  }
}

const onScriptStepDrop = (idx: number, ev: DragEvent) => {
  ev.preventDefault()
  const from = scriptStepDragFrom.value
  scriptStepDragOver.value = null
  if (from === null || from === idx) {
    scriptStepDragFrom.value = null
    return
  }
  const arr = scriptSteps.value
  const moved = arr.splice(from, 1)[0]
  arr.splice(idx, 0, moved)
  // 同步修正展开的编辑索引，使其继续跟随被移动的那一步
  if (scriptEditingStep.value !== null) {
    if (scriptEditingStep.value === from) {
      scriptEditingStep.value = idx
    } else if (from < scriptEditingStep.value && idx >= scriptEditingStep.value) {
      scriptEditingStep.value -= 1
    } else if (from > scriptEditingStep.value && idx <= scriptEditingStep.value) {
      scriptEditingStep.value += 1
    }
  }
  showNotification(`已将步骤移动到第 ${idx + 1} 位`, 'info')
  scriptStepDragFrom.value = null
}

const onScriptStepDragEnd = () => {
  scriptStepDragFrom.value = null
  scriptStepDragOver.value = null
}

const toggleRecording = () => {
  if (!scriptStreaming.value) {
    showMessage('屏幕流未连接，无法录制', '提示')
    return
  }
  scriptRecording.value = !scriptRecording.value
  if (scriptRecording.value) {
    showNotification('录制已开始：单击记录点击，拖动记录滑动，长按记录长按', 'info')
  }
}

// ---------- 保存 / 执行 / 重复 ----------

const saveScript = async () => {
  if (scriptSteps.value.length === 0) {
    showMessage('没有步骤可保存，请先录制', '提示')
    return
  }
  showPrompt('请输入脚本名称', async (name?: string) => {
    if (!name || !name.trim()) return
    scriptSaving.value = true
    try {
      const response = await axios.post(`${API_BASE.value}/api/device/script/save`, {
        name: name.trim(),
        repeat: scriptRepeat.value,
        steps: scriptSteps.value,
        videoWidth: scriptMeta.width,
        videoHeight: scriptMeta.height,
      })
      if (response.data.code === 200) {
        showNotification(`脚本「${response.data.data.name}」已保存`, 'success')
      } else {
        showNotification('保存失败: ' + (response.data.message || '未知错误'), 'error')
      }
    } catch (error: any) {
      showNotification('保存失败: ' + error.message, 'error')
    } finally {
      scriptSaving.value = false
    }
  }, '', '保存脚本')
}

// ---------- 导入脚本 ----------

// 把已载入的步骤应用到当前脚本面板
const applyImportedSteps = (steps: any[], repeat: number, name: string) => {
  scriptSteps.value = steps.map((s: any) => ({
    type: s.type,
    x1: s.x1,
    y1: s.y1,
    x2: s.x2,
    y2: s.y2,
    duration: s.duration,
    text: s.text,
    apkName: s.apkName,
    path: s.path,
    packageName: s.packageName,
    activity: s.activity,
    keycode: s.keycode,
    action: s.action,
    stream: s.stream,
    template: s.template,
    threshold: s.threshold,
    timeout: s.timeout,
    interval: s.interval ?? 0,
    count: s.count ?? 1,
  }))
  scriptRepeat.value = repeat
  showNotification(`已导入脚本「${name}」，共 ${steps.length} 个步骤`, 'success')
}

// ---------- 导出脚本包（zip：脚本 JSON + 引用的 APK/模板图） ----------

const scriptExporting = ref(false)

const exportScriptZip = async () => {
  if (scriptSteps.value.length === 0) {
    showMessage('没有步骤可导出，请先录制或导入脚本', '提示')
    return
  }
  showPrompt('请输入要导出的脚本名称（需已保存过）', async (name?: string) => {
    if (!name || !name.trim()) return
    scriptExporting.value = true
    try {
      const response = await axios.post(`${API_BASE.value}/api/device/script/export-zip`, { name: name.trim() })
      if (response.data.code === 200) {
        const d = response.data.data
        const parts = [`脚本「${d.path}」导出成功`, `大小 ${formatBytes(d.size)}`]
        if (d.apkNames?.length) parts.push(`含 APK：${d.apkNames.join('、')}`)
        if (d.templateNames?.length) parts.push(`含模板图：${d.templateNames.join('、')}`)
        showNotification(parts.join('\n'), 'success')
      } else {
        showNotification('导出失败: ' + (response.data.message || '未知错误'), 'error')
      }
    } catch (error: any) {
      showNotification('导出失败: ' + (error?.response?.data?.message || error.message), 'error')
    } finally {
      scriptExporting.value = false
    }
  }, '', '导出脚本包')
}

// 从导出 zip 包一键导入：脚本 + 资源一次到位
const importScriptFromZip = () => {
  const input = document.createElement('input')
  input.type = 'file'
  input.accept = '.zip,application/zip'
  input.onchange = async () => {
    const file = input.files?.[0]
    if (!file) return
    scriptSaving.value = true
    try {
      const form = new FormData()
      form.append('script', file)
      const response = await axios.post(`${API_BASE.value}/api/device/script/import-zip`, form)
      if (response.data.code !== 200) {
        showNotification('导入失败: ' + (response.data.message || '未知错误'), 'error')
        return
      }
      const d = response.data.data
      // 导入后读回标准化的脚本载入面板
      const loaded = await axios.post(`${API_BASE.value}/api/device/script/load`, { name: d.name })
      if (loaded.data.code !== 200) {
        showNotification('导入后读取失败: ' + (loaded.data.message || '未知错误'), 'error')
        return
      }
      applyImportedSteps(loaded.data.data.steps, loaded.data.data.repeat, loaded.data.data.name)
      const extras: string[] = []
      if (d.apkNames?.length) extras.push(`APK ${d.apkNames.length} 个`)
      if (d.templateNames?.length) extras.push(`模板图 ${d.templateNames.length} 张`)
      showNotification(
        `已导入脚本「${d.name}」（${d.stepCount} 个步骤${extras.length ? '，含' + extras.join('和') : ''}）${d.overwritten ? '，已覆盖同名脚本' : ''}`,
        'success'
      )
    } catch (error: any) {
      showNotification('导入失败: ' + (error?.response?.data?.message || error.message), 'error')
    } finally {
      scriptSaving.value = false
    }
  }
  input.click()
}

// 从已保存脚本列表中载入
const importScriptFromList = async () => {
  try {
    const response = await axios.post(`${API_BASE.value}/api/device/script/list`)
    if (response.data.code !== 200) {
      showNotification('获取脚本列表失败: ' + (response.data.message || '未知错误'), 'error')
      return
    }
    const scripts = response.data.data || []
    // 过滤掉自动生成的临时脚本（current_xxx），它们是执行过程产生的中间产物
    const items = scripts
      .filter((s: any) => !String(s.name).startsWith('current_') && !String(s.name).startsWith('_current_'))
      .map((s: any) => ({
        value: s.name,
        label: s.name,
        desc: `${s.stepCount} 个步骤 · 重复 ${s.repeat} 轮`,
      }))

    showList(items, async (name?: string) => {
      if (!name) return
      try {
        scriptSaving.value = true
        const loaded = await axios.post(`${API_BASE.value}/api/device/script/load`, { name })
        if (loaded.data.code !== 200) {
          showNotification('读取脚本失败: ' + (loaded.data.message || '未知错误'), 'error')
          return
        }
        applyImportedSteps(
          loaded.data.data.steps,
          loaded.data.data.repeat,
          loaded.data.data.name
        )
      } catch (error: any) {
        showNotification('读取脚本失败: ' + error.message, 'error')
      } finally {
        scriptSaving.value = false
      }
    }, '选择已保存的脚本', '还没有保存过脚本', '载入')
  } catch (error: any) {
    showNotification('获取脚本列表失败: ' + error.message, 'error')
  }
}

// 导入按钮：弹出选择（导出 zip 包 / 已保存列表）
const openImportMenu = () => {
  showList(
    [
      { value: 'zip', label: '从导出包（zip）导入', desc: '脚本 + 引用的 APK/模板图一次导入' },
      { value: 'saved', label: '从已保存脚本载入', desc: '载入 data/scripts 下已有的脚本' },
    ],
    (choice?: string) => {
      if (choice === 'zip') {
        importScriptFromZip()
      } else if (choice === 'saved') {
        importScriptFromList()
      }
    },
    '导入脚本',
    '',
    '确定'
  )
}

// 执行当前步骤：直接把步骤发给后端执行，不落盘（不再产生 current_xxx.json 临时文件）
const runCurrentSteps = async () => {
  if (!selectedDevice.value) {
    showMessage('请先选择设备', '提示')
    return
  }
  if (scriptSteps.value.length === 0) {
    showMessage('没有步骤可执行，请先录制', '提示')
    return
  }
  if (scriptRunning.value) {
    showNotification('已有脚本在执行中', 'error')
    return
  }
  scriptRunning.value = true
  try {
    // 后端同步执行：接口返回时脚本已跑完，失败原因直接在 message 里
    const response = await axios.post(`${API_BASE.value}/api/device/script/run-steps`, {
      deviceId: selectedDevice.value,
      steps: scriptSteps.value,
      repeat: scriptRepeat.value,
      videoWidth: scriptMeta.width,
      videoHeight: scriptMeta.height,
    }, { timeout: 600000 })
    if (response.data.code !== 200) {
      showNotification(response.data.message || '执行失败', 'error')
      return
    }
    const d = response.data.data || {}
    showNotification(`执行完成：${d.stepCount ?? scriptSteps.value.length} 步 × ${d.repeat ?? scriptRepeat.value} 轮`, 'success')
  } catch (error: any) {
    showNotification('执行失败: ' + (error?.response?.data?.message || error.message), 'error')
  } finally {
    scriptRunning.value = false
  }
}

// 停止当前设备上正在执行的脚本
const stopScriptRun = async () => {
  if (!selectedDevice.value) return
  try {
    const response = await axios.post(`${API_BASE.value}/api/device/script/stop-run`, {
      deviceId: selectedDevice.value,
    })
    if (response.data.code === 200) {
      showNotification(response.data.message || '已发送停止请求', 'info')
    } else {
      showNotification('停止失败: ' + (response.data.message || '未知错误'), 'error')
    }
  } catch (error: any) {
    showNotification('停止失败: ' + error.message, 'error')
  }
}

// 重复按钮：输入轮数后执行当前步骤
const repeatRun = () => {
  showPrompt('请输入重复执行的轮数', (input?: string) => {
    const n = parseInt(input || '', 10)
    if (!n || n < 1) {
      showNotification('请输入大于 0 的整数', 'error')
      return
    }
    scriptRepeat.value = n
    runCurrentSteps()
  }, '', '重复执行')
}

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
const modalVisible = ref(false)
const modalType = ref('message') // message, confirm, prompt, list
const modalTitle = ref('')
const modalMessage = ref('')
const modalPlaceholder = ref('')
const modalConfirmText = ref('确定')
const modalVariant = ref<'success' | 'error' | 'info'>('success')
const modalProgress = ref(false)
const modalCallback = ref<Function | null>(null)
// list 类型弹窗的数据
const modalListItems = ref<Array<{ value: string; label: string; desc?: string }>>([])
const modalEmptyText = ref('')

const fetchDevices = async () => {
  loading.value = true
  try {
    const response = await axios.post(`${API_BASE.value}/api/devices`)
    if (response.data.code === 200) {
      devices.value = response.data.data || []
      if (devices.value.length > 0 && !selectedDevice.value) {
        selectDevice(devices.value[0].deviceId)
      }
      loadBatchDeviceInfos()
      autoInstallNewDevices()
    }
  } catch (error) {
    console.error('获取设备列表失败:', error)
  } finally {
    loading.value = false
  }
}

const fetchDeviceInfo = async (deviceId: string) => {
  try {
    const response = await axios.post(`${API_BASE.value}/api/device/info`, {
      deviceId: deviceId
    })
    if (response.data.code === 200 && response.data.data) {
      // 查找设备的实际状态
      const device = devices.value.find(d => d.deviceId === deviceId)
      const status = device?.status === 'device' ? '已连接' : device?.status === 'offline' ? '离线' : device?.status || '未知'
      
      deviceInfo.value = {
        deviceId: deviceId,
        status: status,
        ...response.data.data
      }
    }
  } catch (error) {
    console.error('获取设备信息失败:', error)
  }
}

const startScrcpy = async () => {
  if (!selectedDevice.value) return
  
  try {
    const response = await axios.post(`${API_BASE.value}/api/device/scrcpy`, {
      deviceId: selectedDevice.value
    })
    if (response.data.code !== 200) {
      showMessage(response.data.message || '启动投屏失败')
    }
  } catch (error) {
    console.error('启动投屏失败:', error)
    showMessage('启动投屏失败，请确保 scrcpy 已安装并添加到系统环境变量')
  }
}

const fetchApps = async (deviceId: string) => {
  appLoading.value = true
  try {
    const response = await axios.post(`${API_BASE.value}/api/device/apps`, {
      deviceId: deviceId
    })
    if (response.data.code === 200) {
      const packageNames = response.data.data || []
      
      // 为每个应用获取详细信息
      const appDetailsPromises = packageNames.map(async (pkg: string) => {
        try {
          const infoResponse = await axios.post(`${API_BASE.value}/api/device/app/info`, {
            deviceId: deviceId,
            packageName: pkg
          })
          if (infoResponse.data.code === 200 && infoResponse.data.data) {
            return infoResponse.data.data
          }
        } catch (error) {
          console.error(`获取应用 ${pkg} 详细信息失败:`, error)
        }
        // 如果获取失败，返回默认信息
        return {
          packageName: pkg,
          appName: pkg,
          versionName: '未知',
          versionCode: ''
        }
      })
      
      apps.value = await Promise.all(appDetailsPromises)
    }
  } catch (error) {
    console.error('获取应用列表失败:', error)
  } finally {
    appLoading.value = false
  }
}

const fetchFiles = async (deviceId: string, path: string = '/sdcard') => {
  fileLoading.value = true
  // 目录切换/刷新/换设备后，上一目录的勾选作废
  selectedFiles.value = new Set()
  try {
    const response = await axios.post(`${API_BASE.value}/api/device/files/browse`, {
      deviceId: deviceId,
      path: path
    })
    if (response.data.code === 200) {
      files.value = response.data.data?.files || []
      currentPath.value = response.data.data?.currentPath || path
    } else {
      showMessage('获取文件列表失败: ' + (response.data.message || '未知错误'))
    }
  } catch (error: any) {
    console.error('获取文件列表失败:', error)
    showMessage('获取文件列表失败: ' + (error.message || '网络异常'))
  } finally {
    fileLoading.value = false
  }
}

const exportFile = async (file: { path: string; name: string; isDirectory: boolean }) => {
  if (!selectedDevice.value) {
    showMessage('请先选择设备')
    return
  }

  // 固定保存到 D:/log 目录
  const localPath = `D:/log/${file.name}`

  console.log('导出文件:', {
    deviceId: selectedDevice.value,
    remotePath: file.path,
    localPath: localPath
  })

  try {
    const response = await axios.post(`${API_BASE.value}/api/device/files/download`, {
      deviceId: selectedDevice.value,
      remotePath: file.path,
      localPath: localPath
    })

    console.log('导出响应:', response.data)

    // 后端返回格式: {code: 200, message: 'success', data: null}
    if (response.data.code === 200) {
      showMessage(`${file.isDirectory ? '文件夹' : '文件'}导出成功\n保存位置: ${localPath}`)
    } else {
      showMessage(`${file.isDirectory ? '文件夹' : '文件'}导出失败: ` + (response.data.message || '未知错误'))
      console.error('导出失败:', response.data)
    }
  } catch (error: any) {
    console.error('导出异常:', error)
    showMessage('导出失败: ' + error.message)
  }
}

const importFile = async () => {
  if (!selectedDevice.value) {
    showMessage('请先选择设备')
    return
  }
  
  const input = document.createElement('input')
  input.type = 'file'
  input.multiple = true
  
  input.onchange = async (e: Event) => {
    const target = e.target as HTMLInputElement
    const picked = Array.from(target.files || [])
    if (picked.length === 0) return
    
    // 逐个上传（adb push 本身串行，进度通知逐个显示），任一失败继续后面的文件
    let okCount = 0
    const failed: string[] = []
    const startedAt = Date.now()
    for (const file of picked) {
      const formData = new FormData()
      formData.append('file', file)
      formData.append('deviceId', selectedDevice.value)
      formData.append('remotePath', currentPath.value + '/')
      
      showProgressNotification(`正在导入 ${file.name}（${formatBytes(file.size)}）… ${okCount + 1}/${picked.length}`)
      try {
        const response = await axios.post(
          `${API_BASE.value}/api/device/files/upload`,
          formData,
          { headers: { 'Content-Type': 'multipart/form-data' } }
        )
        if (response.data.code === 200) {
          okCount++
        } else {
          failed.push(`${file.name}: ${response.data.message || '未知错误'}`)
        }
      } catch (error) {
        console.error('导入文件失败:', error)
        failed.push(`${file.name}: 网络异常`)
      }
    }
    hideProgressNotification()
    if (failed.length === 0) {
      showNotification(`已导入 ${okCount} 个文件，耗时 ${Math.round((Date.now() - startedAt) / 1000)}s`)
    } else {
      showNotification(`导入完成 ${okCount}/${picked.length}，失败：\n${failed.join('\n')}`, 'error')
    }
    fetchFiles(selectedDevice.value, currentPath.value)
  }
  
  input.click()
}

// ==================== 文本文件在线编辑 ====================
// 链路：download 拉到本地临时文件 → preload 的 services.readFile 读文本 →
//       编辑后构造 File 走 upload 覆盖回设备原路径（全程无需新后端接口）
const EDITABLE_EXTS = ['txt', 'log', 'json', 'xml', 'html', 'htm', 'css', 'js', 'mjs', 'cjs', 'ts', 'md', 'markdown', 'py', 'conf', 'cfg', 'ini', 'sh', 'prop', 'properties', 'csv', 'yml', 'yaml', 'sql', 'java', 'c', 'cpp', 'h', 'gradle', 'env']
const EDIT_MAX_BYTES = 10 * 1024 * 1024 // 10MB，超过不支持在线编辑

const editorVisible = ref(false)
const editorFileName = ref('')
const editorInitialContent = ref('')
const editorLoading = ref(false)
const editorLoadError = ref('')
const editorSaving = ref(false)
// 当前编辑的目标信息（远端路径、本地临时路径）
let editorTarget: { remotePath: string; localPath: string } | null = null

const isEditableTextFile = (file: { name: string; isDirectory: boolean; size?: string }) => {
  if (file.isDirectory) return false
  const ext = (file.name.split('.').pop() || '').toLowerCase()
  if (!EDITABLE_EXTS.includes(ext)) return false
  const sizeNum = Number(file.size)
  if (!isNaN(sizeNum) && sizeNum > EDIT_MAX_BYTES) return false
  return true
}

// 文件不可编辑的具体原因，用于禁用按钮的悬浮提示
const editDisabledReason = (file: { name: string; isDirectory: boolean; size?: string }): string => {
  if (file.isDirectory) return '目录不支持编辑'
  const ext = (file.name.split('.').pop() || '').toLowerCase()
  if (!EDITABLE_EXTS.includes(ext)) return `不支持编辑 .${ext} 类型的文件`
  const sizeNum = Number(file.size)
  if (!isNaN(sizeNum) && sizeNum > EDIT_MAX_BYTES) {
    return `文件过大（${formatBytes(sizeNum)}），超过 ${formatBytes(EDIT_MAX_BYTES, 0)} 不支持在线编辑，可先导出`
  }
  return '不支持编辑'
}

const openFileEditor = async (file: { path: string; name: string; size?: string }) => {
  if (!selectedDevice.value) return
  if (!isEditableTextFile(file)) {
    showMessage('该文件类型或大小不支持在线编辑')
    return
  }

  editorFileName.value = file.name
  editorInitialContent.value = ''
  editorLoadError.value = ''
  editorLoading.value = true
  editorVisible.value = true

  try {
    // 读取链路：优先走后端 /api/device/files/read（不依赖 uTools，浏览器也能用）；
    // 后端未部署该接口时，回落到 download + preload readFile 的旧链路（需 uTools）
    let content: string | null = null
    try {
      const readResp = await axios.post(`${API_BASE.value}/api/device/files/read`, {
        deviceId: selectedDevice.value,
        remotePath: file.path
      })
      if (readResp.data.code === 200 && readResp.data.data?.content !== undefined) {
        content = readResp.data.data.content
      } else if (readResp.data.code !== 404) {
        // 接口存在但读取失败（权限/二进制等），直接报错不再回落
        throw new Error(readResp.data.message || '读取文件失败')
      }
    } catch (readError: any) {
      // 404 = 后端还没有该接口，走回落；其他错误视为真实读取失败
      const isNotFound = readError?.response?.status === 404
        || readResp_dataCodeIs404(readError)
      if (!isNotFound) throw readError
    }

    if (content === null) {
      // ---- 回落链路：download 到本地 + preload 读文件（需 uTools）----
      if (!(window as any).services?.readFile) {
        throw new Error('后端不支持在线读取，且未检测到 uTools 环境（preload 未加载），无法编辑')
      }
      const ext = file.name.includes('.') ? file.name.split('.').pop() : 'txt'
      const localPath = `D:/log/.tmp_edit_${Date.now()}.${ext}`
      const dlResp = await axios.post(`${API_BASE.value}/api/device/files/download`, {
        deviceId: selectedDevice.value,
        remotePath: file.path,
        localPath
      })
      if (dlResp.data.code !== 200) {
        throw new Error(dlResp.data.message || '下载文件失败')
      }
      const text = (window as any).services.readFile(localPath)
      content = typeof text === 'string' ? text : String(text)
    }

    editorInitialContent.value = content
    editorTarget = { remotePath: file.path }
  } catch (error: any) {
    console.error('打开文件编辑失败:', error)
    editorLoadError.value = `无法读取文件: ${error.message}（可能是权限不足或二进制文件）`
  } finally {
    editorLoading.value = false
  }
}

// 判断后端返回体里的 code 是否为 404（表示接口未实现）
const readResp_dataCodeIs404 = (error: any): boolean => {
  return error?.response?.data?.code === 404
}

const handleEditorSave = async (newContent: string) => {
  if (!selectedDevice.value || !editorTarget) return
  editorSaving.value = true
  try {
    // 3. 用字符串构造 File 走现有上传接口，覆盖回原路径
    const blob = new Blob([newContent], { type: 'text/plain' })
    const formData = new FormData()
    formData.append('file', blob, editorFileName.value)
    formData.append('deviceId', selectedDevice.value)
    const dir = editorTarget.remotePath.substring(0, editorTarget.remotePath.lastIndexOf('/') + 1) || '/'
    formData.append('remotePath', dir)

    const response = await axios.post(
      `${API_BASE.value}/api/device/files/upload`,
      formData,
      { headers: { 'Content-Type': 'multipart/form-data' } }
    )
    if (response.data.code === 200) {
      showMessage('保存成功，已覆盖设备上的文件')
      editorVisible.value = false
      editorTarget = null
      fetchFiles(selectedDevice.value, currentPath.value)
    } else {
      showMessage('保存失败: ' + (response.data.message || '未知错误'))
    }
  } catch (error: any) {
    console.error('保存文件失败:', error)
    showMessage('保存失败: ' + error.message)
  } finally {
    editorSaving.value = false
  }
}

const handleEditorClose = () => {
  editorVisible.value = false
  editorTarget = null
}

// ==================== 图片在线查看 ====================
// 链路：后端 /api/device/files/base64 读出 base64 → 前端拼 data: URL 交给弹窗预览
const IMAGE_EXTS = ['png', 'jpg', 'jpeg', 'gif', 'bmp', 'webp']
const VIDEO_EXTS = ['mp4', 'webm', 'mkv', '3gp', 'mov']
const IMAGE_MAX_BYTES = 20 * 1024 * 1024 // 与后端 IMAGE_PREVIEW_MAX_BYTES 保持一致

// 预览类型：图片（缩放/拖拽查看）或视频（浏览器播放器），都不支持返回 ''
const previewKindOf = (file: { name: string; isDirectory: boolean; size?: string }): 'image' | 'video' | '' => {
  if (file.isDirectory) return ''
  const ext = (file.name.split('.').pop() || '').toLowerCase()
  if (IMAGE_EXTS.includes(ext)) return 'image'
  if (VIDEO_EXTS.includes(ext)) return 'video'
  return ''
}

const imageViewerVisible = ref(false)
const imageViewerName = ref('')
const imageViewerSrc = ref('')
const imageViewerLoading = ref(false)
const imageViewerError = ref('')
const imageViewerSize = ref(0)
const imageViewerKind = ref<'image' | 'video'>('image')
// 多图导航：当前目录里可预览的图片列表 + 当前下标
const imageViewerList = ref<Array<{ path: string; name: string; size?: string }>>([])
const imageViewerIndex = ref(0)
const imageViewerTotal = computed(() => imageViewerList.value.length)

// 文件是否可预览（类型 + 大小上限）
const isViewableFile = (file: { name: string; isDirectory: boolean; size?: string }) => {
  if (!previewKindOf(file)) return false
  const sizeNum = Number(file.size)
  return isNaN(sizeNum) || sizeNum <= IMAGE_MAX_BYTES
}

// 图片可预览判定（保留给可能的外部引用；主流程走 previewKindOf）
const isViewableImageFile = (file: { name: string; isDirectory: boolean; size?: string }) => {
  return previewKindOf(file) === 'image' && isViewableFile(file)
}

// 图片/视频不可预览的具体原因，用于禁用按钮的悬浮提示
const viewDisabledReason = (file: { name: string; isDirectory: boolean; size?: string }): string => {
  if (file.isDirectory) return '目录不支持预览'
  const kind = previewKindOf(file)
  if (!kind) return '不支持预览该类型的文件'
  const sizeNum = Number(file.size)
  if (!isNaN(sizeNum) && sizeNum > IMAGE_MAX_BYTES) {
    return `文件过大（${formatBytes(sizeNum)}），超过 ${formatBytes(IMAGE_MAX_BYTES, 0)} 不支持在线预览，可先导出`
  }
  return '不支持预览'
}

const openImageViewer = async (file: { path: string; name: string; size?: string }) => {
  if (!selectedDevice.value) return
  if (!isViewableFile(file)) {
    showMessage('该文件类型或大小不支持在线预览')
    return
  }

  // 收集当前目录的可预览文件（图片+视频），用于上一个/下一个导航
  imageViewerList.value = files.value.filter((f: any) => isViewableFile(f))
  imageViewerIndex.value = Math.max(0, imageViewerList.value.findIndex((f: any) => f.path === file.path))

  await loadViewerFile(file)
}

const loadViewerFile = async (file: { path: string; name: string; size?: string }) => {
  if (!selectedDevice.value) return

  const kind = previewKindOf(file) || 'image'
  imageViewerKind.value = kind
  imageViewerName.value = file.name
  imageViewerSrc.value = ''
  imageViewerError.value = ''
  imageViewerSize.value = Number(file.size) || 0
  imageViewerLoading.value = true
  imageViewerVisible.value = true

  try {
    const resp = await axios.post(`${API_BASE.value}/api/device/files/base64`, {
      deviceId: selectedDevice.value,
      remotePath: file.path
    })
    if (resp.data.code === 200 && resp.data.data?.base64) {
      const ext = (file.name.split('.').pop() || '').toLowerCase()
      let mime: string
      if (kind === 'video') {
        // mkv/3gp 浏览器支持有限，但仍按真实类型给，播不了由 <video> 原生报错
        mime = ext === 'mkv' ? 'video/x-matroska' : ext === 'mov' ? 'video/quicktime' : `video/${ext}`
      } else {
        mime = ext === 'jpg' ? 'image/jpeg' : `image/${ext}`
      }
      imageViewerSrc.value = `data:${mime};base64,${resp.data.data.base64}`
      if (resp.data.data.size) imageViewerSize.value = resp.data.data.size
    } else {
      throw new Error(resp.data.message || '读取文件失败')
    }
  } catch (error: any) {
    console.error('打开预览失败:', error)
    imageViewerError.value = `无法读取文件: ${error.message}`
  } finally {
    imageViewerLoading.value = false
  }
}

// 上一个/下一个导航（循环）
const handleImageViewerNav = (index: number) => {
  const file = imageViewerList.value[index]
  if (file) {
    imageViewerIndex.value = index
    loadViewerFile(file)
  }
}

const handleImageViewerClose = () => {
  imageViewerVisible.value = false
  // 释放 data URL 引用，尽快让 GC 回收大字符串
  imageViewerSrc.value = ''
}

const selectDevice = (deviceId: string) => {
  selectedDevice.value = deviceId
  fetchDeviceInfo(deviceId)
  fetchApps(deviceId)
  fetchFiles(deviceId, '/sdcard')
}

const installApkFile = async (file: File) => {
  if (!selectedDevice.value) return

  const formData = new FormData()
  formData.append('apk', file)
  formData.append('deviceId', selectedDevice.value)

  try {
    const response = await axios.post(
      `${API_BASE.value}/api/device/app/install`,
      formData,
      { headers: { 'Content-Type': 'multipart/form-data' } }
    )
    if (response.data.code === 200) {
      showMessage('应用安装成功')
      fetchApps(selectedDevice.value)
    } else {
      showMessage('应用安装失败: ' + (response.data.message || '未知错误'))
    }
  } catch (error) {
    console.error('安装应用失败:', error)
    showMessage('安装应用失败')
  }
}

const installApp = async (event: Event) => {
  if (!selectedDevice.value) return
  
  const input = document.createElement('input')
  input.type = 'file'
  input.accept = '.apk'
  
  input.onchange = async (e: Event) => {
    const target = e.target as HTMLInputElement
    const file = target.files?.[0]
    if (!file) return
    
    await installApkFile(file)
  }
  
  input.click()
}

// 全页拖入 APK 安装：选中设备后，页面任意位置都可拖入（仅应用管理页生效）
const apkDragOver = ref(false)

// 拖拽经过页面：仅拖的是文件、已选设备且当前在应用管理页时亮提示
const onPageDragOver = (event: DragEvent) => {
  if (activeTab.value !== 'apps') return
  const types = event.dataTransfer?.types
  if (!types || !Array.from(types).includes('Files') || !selectedDevice.value) return
  apkDragOver.value = true
}

const onApkDragLeave = (event: DragEvent) => {
  // 移动到页面内部的子元素也会触发父级 dragleave，
  // relatedTarget 还在页面内时不算真正离开，避免提示反复闪烁
  const el = event.currentTarget as HTMLElement
  if (event.relatedTarget && el.contains(event.relatedTarget as Node)) return
  apkDragOver.value = false
}

const onApkDrop = async (event: DragEvent) => {
  apkDragOver.value = false
  // 只在应用管理页生效，其他页面忽略拖入
  if (activeTab.value !== 'apps') return
  if (!selectedDevice.value) {
    showNotification('请先选择一个设备再拖入 APK', 'error')
    return
  }

  const file = event.dataTransfer?.files?.[0]
  if (!file) return
  if (!file.name.toLowerCase().endsWith('.apk')) {
    showNotification('仅支持拖入 .apk 文件', 'error')
    return
  }
  await installApkFile(file)
}

const uninstallApp = async (packageName: string) => {
  if (!selectedDevice.value) return

  try {
    const response = await axios.post(`${API_BASE.value}/api/device/app/uninstall`, {
      deviceId: selectedDevice.value,
      packageName: packageName
    })
    if (response.data.code === 200) {
      fetchApps(selectedDevice.value)
    }
  } catch (error) {
    console.error('卸载应用失败:', error)
  }
}

// ==================== APK 仓库（应用管理页） ====================

const apkRepoVisible = ref(false)
const apkRepoUploading = ref(false)
const apkRepoOptions = ref<Array<{ name: string; size: number }>>([])
const apkRepoLoading = ref(false)
const apkRepoInputEl = ref<HTMLInputElement | null>(null)

const fetchApkRepo = async () => {
  apkRepoLoading.value = true
  try {
    const res = await axios.post(`${API_BASE.value}/api/device/pipeline/list-apks`)
    if (res.data.code === 200) apkRepoOptions.value = res.data.data || []
  } catch {
    /* 列表拉取失败不阻断界面 */
  } finally {
    apkRepoLoading.value = false
  }
}

const openApkRepo = () => {
  apkRepoVisible.value = true
  fetchApkRepo()
}

const pickRepoApk = () => apkRepoInputEl.value?.click()

const onRepoApkChosen = async (event: Event) => {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file) return
  apkRepoUploading.value = true
  try {
    const form = new FormData()
    form.append('apk', file)
    const response = await axios.post(`${API_BASE.value}/api/device/pipeline/upload-apk`, form)
    if (response.data.code === 200) {
      await fetchApkRepo()
    } else {
      showNotification(response.data.message || '上传失败', 'error')
    }
  } catch (error: any) {
    showNotification('上传失败: ' + error.message, 'error')
  } finally {
    apkRepoUploading.value = false
  }
}

// 从仓库安装到当前设备
const installFromRepo = async (name: string) => {
  if (!selectedDevice.value) {
    showNotification('请先在左侧选择设备', 'error')
    return
  }
  try {
    const response = await axios.post(`${API_BASE.value}/api/device/pipeline/install-apk-to-device`, {
      deviceId: selectedDevice.value,
      apkName: name,
    })
    if (response.data.code === 200) {
      showNotification(`${name} 安装成功`, 'success')
      fetchApps(selectedDevice.value)
    } else {
      showNotification('安装失败: ' + (response.data.message || '未知错误'), 'error')
    }
  } catch (error: any) {
    showNotification('安装失败: ' + (error?.response?.data?.message || error.message), 'error')
  }
}

const removeRepoApk = async (name: string) => {
  if (!confirm(`确定删除 APK 素材 ${name} 吗？`)) return
  try {
    const response = await axios.post(`${API_BASE.value}/api/device/pipeline/delete-apk`, { name })
    if (response.data.code === 200) {
      await fetchApkRepo()
    } else {
      showNotification(response.data.message || '删除失败', 'error')
    }
  } catch (error: any) {
    showNotification('删除失败: ' + error.message, 'error')
  }
}

const formatRepoSize = (size: number): string => {
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(0)} KB`
  return `${(size / 1024 / 1024).toFixed(1)} MB`
}

const startApp = async (packageName: string) => {
  if (!selectedDevice.value) return
  
  try {
    await axios.post(`${API_BASE.value}/api/device/app/start`, {
      deviceId: selectedDevice.value,
      packageName: packageName
    })
  } catch (error) {
    console.error('启动应用失败:', error)
  }
}

const stopApp = async (packageName: string) => {
  if (!selectedDevice.value) return
  
  try {
    await axios.post(`${API_BASE.value}/api/device/app/stop`, {
      deviceId: selectedDevice.value,
      packageName: packageName
    })
  } catch (error) {
    console.error('停止应用失败:', error)
  }
}

const extractApk = async (packageName: string, appName?: string) => {
  if (!selectedDevice.value) return
  
  const input = document.createElement('input') as HTMLInputElement & { webkitdirectory: boolean; directory: boolean }
  input.type = 'file'
  input.webkitdirectory = true
  input.directory = true
  
  input.onchange = async (e: Event) => {
    const target = e.target as HTMLInputElement
    
    if (!target.files || target.files.length === 0) {
        showMessage('请选择一个目录')
        return
      }
      
      const selectedFile = target.files![0] as File & { path: string }
      const filePath = selectedFile.path
      const localDir = filePath.substring(0, filePath.lastIndexOf('\\') + 1)
      
      if (!localDir) {
        showMessage('无法获取目录路径')
        return
      }
      
      const safeAppName = appName?.replace(/[^a-zA-Z0-9]/g, '_') || packageName.replace(/[^a-zA-Z0-9]/g, '_')
      const localPath = `${localDir}${safeAppName}.apk`
      
      try {
        const normalizedLocalPath = localPath.replace(/\\/g, '/')
        
        const response = await axios.post(`${API_BASE.value}/api/device/app/extract`, {
          deviceId: selectedDevice.value,
          packageName: packageName,
          localPath: normalizedLocalPath
        })
        
        if (response.data.code === 200) {
          showMessage('APK提取成功')
        } else {
          showMessage('APK提取失败: ' + (response.data.message || '未知错误'))
        }
      } catch (error: any) {
        console.error('提取APK失败:', error)
        showMessage('提取APK失败: ' + error.message)
      }
  }
  
  input.click()
}

const navigateTo = (path: string) => {
  if (!selectedDevice.value) return
  fetchFiles(selectedDevice.value, path)
}

const goBack = () => {
  if (!selectedDevice.value) return
  
  if (currentPath.value === '/' || currentPath.value === '/sdcard') return
  
  const lastSlashIndex = currentPath.value.lastIndexOf('/')
  const parentPath = lastSlashIndex <= 0 ? '/' : currentPath.value.substring(0, lastSlashIndex)
  fetchFiles(selectedDevice.value, parentPath)
}

const createFolder = async () => {
  if (!selectedDevice.value) return
  
  showPrompt('请输入文件夹名称:', async (folderName: string) => {
    if (!folderName) return
    
    const folderPath = `${currentPath.value}/${folderName}`
    
    try {
      const response = await axios.post(`${API_BASE.value}/api/device/files/mkdir`, {
        deviceId: selectedDevice.value,
        path: folderPath
      })
      if (response.data.code === 200) {
        showMessage('文件夹创建成功')
        fetchFiles(selectedDevice.value, currentPath.value)
      } else {
        showMessage('文件夹创建失败: ' + (response.data.message || '未知错误'))
      }
    } catch (error: any) {
      console.error('创建文件夹失败:', error)
      showMessage('文件夹创建失败: ' + error.message)
    }
  }, '', '创建文件夹')
}

const createFile = async () => {
  if (!selectedDevice.value) return
  
  showPrompt('请输入文件名称:', async (fileName: string) => {
    if (!fileName) return
    
    const filePath = `${currentPath.value}/${fileName}`
    
    try {
      const response = await axios.post(`${API_BASE.value}/api/device/files/create`, {
        deviceId: selectedDevice.value,
        path: filePath
      })
      if (response.data.code === 200) {
        showMessage('文件创建成功')
        fetchFiles(selectedDevice.value, currentPath.value)
      } else {
        showMessage('文件创建失败: ' + (response.data.message || '未知错误'))
      }
    } catch (error: any) {
      console.error('创建文件失败:', error)
      showMessage('创建文件失败: ' + error.message)
    }
  }, '', '创建文件')
}

const deleteFile = async (path: string, isDirectory: boolean) => {
  if (!selectedDevice.value) return

  showConfirm(`确定要删除${isDirectory ? '文件夹' : '文件'}吗？`, async () => {
    try {
      const response = await axios.post(`${API_BASE.value}/api/device/files/delete`, {
        deviceId: selectedDevice.value,
        path: path
      })
      if (response.data.code === 200) {
        showMessage('删除成功')
        fetchFiles(selectedDevice.value, currentPath.value)
      } else {
        showMessage('删除失败: ' + (response.data.message || '未知错误'))
      }
    } catch (error: any) {
      console.error('删除失败:', error)
      showMessage('删除失败: ' + (error.message || '网络异常'))
    }
  }, '确认删除')
}

// 重命名/移动文件或文件夹
const renameFile = (file: { path: string; name: string }) => {
  if (!selectedDevice.value) return

  showPrompt(
    `输入新名称或完整新路径（当前：${file.path}）`,
    async (newPath: string) => {
      const trimmed = (newPath || '').trim()
      if (!trimmed) {
        showMessage('新路径不能为空')
        return
      }
      try {
        const response = await axios.post(`${API_BASE.value}/api/device/files/rename`, {
          deviceId: selectedDevice.value,
          path: file.path,
          newPath: trimmed
        })
        if (response.data.code === 200) {
          showMessage('重命名成功')
          fetchFiles(selectedDevice.value, currentPath.value)
        } else {
          showMessage('重命名失败: ' + (response.data.message || '未知错误'))
        }
      } catch (error: any) {
        console.error('重命名失败:', error)
        showMessage('重命名失败: ' + (error.message || '网络异常'))
      }
    },
    file.name,
    '重命名/移动'
  )
}

// 批量删除选中的文件/文件夹
const batchDeleteSelected = () => {
  if (!selectedDevice.value || selectedFileCount.value === 0) return

  const paths = [...selectedFiles.value]
  showConfirm(`确定要删除选中的 ${paths.length} 项吗？（删除后不可恢复）`, async () => {
    let okCount = 0
    const failed: string[] = []
    for (const p of paths) {
      try {
        const response = await axios.post(`${API_BASE.value}/api/device/files/delete`, {
          deviceId: selectedDevice.value,
          path: p
        })
        if (response.data.code === 200) {
          okCount++
        } else {
          failed.push(`${p}: ${response.data.message || '未知错误'}`)
        }
      } catch (error: any) {
        failed.push(`${p}: ${error.message || '网络异常'}`)
      }
    }
    if (failed.length === 0) {
      showMessage(`已删除 ${okCount} 项`)
    } else {
      showMessage(`删除完成 ${okCount}/${paths.length}，失败：\n${failed.join('\n')}`, '删除结果')
    }
    fetchFiles(selectedDevice.value, currentPath.value)
  }, '确认删除')
}

// 批量移动选中的文件/文件夹到指定目录
const batchMoveSelected = () => {
  if (!selectedDevice.value || selectedFileCount.value === 0) return

  const paths = [...selectedFiles.value]
  showPrompt(
    `输入目标目录路径（如 /sdcard/DCIM），选中的 ${paths.length} 项将移动到该目录下`,
    async (destDir: string) => {
      const dir = (destDir || '').trim()
      if (!dir) {
        showMessage('目标目录不能为空')
        return
      }
      const base = dir.replace(/\/+$/, '')
      let okCount = 0
      const failed: string[] = []
      for (const p of paths) {
        const name = p.substring(p.lastIndexOf('/') + 1)
        try {
          const response = await axios.post(`${API_BASE.value}/api/device/files/rename`, {
            deviceId: selectedDevice.value,
            path: p,
            newPath: `${base}/${name}`
          })
          if (response.data.code === 200) {
            okCount++
          } else {
            failed.push(`${name}: ${response.data.message || '未知错误'}`)
          }
        } catch (error: any) {
          failed.push(`${name}: ${error.message || '网络异常'}`)
        }
      }
      if (failed.length === 0) {
        showMessage(`已移动 ${okCount} 项到 ${base}`)
      } else {
        showMessage(`移动完成 ${okCount}/${paths.length}，失败：\n${failed.join('\n')}`, '移动结果')
      }
      fetchFiles(selectedDevice.value, currentPath.value)
    },
    currentPath.value,
    '移动到目录'
  )
}

const simulateKey = async (key: string) => {
  if (!selectedDevice.value) return
  
  try {
    await axios.post(`${API_BASE.value}/api/device/key`, {
      deviceId: selectedDevice.value,
      key: key
    })
  } catch (error) {
    console.error('按键模拟失败:', error)
  }
}

const volumeUp = async () => {
  if (!selectedDevice.value) return
  
  try {
    await axios.post(`${API_BASE.value}/api/device/volume`, {
      deviceId: selectedDevice.value,
      action: 'up',
      stream: 'media'
    })
  } catch (error) {
    console.error('音量增加失败:', error)
  }
}

const volumeDown = async () => {
  if (!selectedDevice.value) return
  
  try {
    await axios.post(`${API_BASE.value}/api/device/volume`, {
      deviceId: selectedDevice.value,
      action: 'down',
      stream: 'media'
    })
  } catch (error) {
    console.error('音量减小失败:', error)
  }
}

const muteVolume = async () => {
  if (!selectedDevice.value) return
  
  try {
    await axios.post(`${API_BASE.value}/api/device/volume`, {
      deviceId: selectedDevice.value,
      action: 'mute',
      stream: 'media'
    })
  } catch (error) {
    console.error('静音失败:', error)
  }
}

const restartDevice = async () => {
  if (!selectedDevice.value) return
  
  showConfirm('确定要重启设备吗？', async () => {
    try {
      await axios.post(`${API_BASE.value}/api/command`, {
        command: `adb -s ${selectedDevice.value} reboot`
      })
      showMessage('设备正在重启...')
    } catch (error) {
      console.error('重启设备失败:', error)
      showMessage('重启设备失败')
    }
  }, '确认重启')
}

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

const connectWireless = async () => {
  if (!wirelessIp.value) {
    wirelessStatus.value = '请输入设备IP地址'
    return
  }
  
  try {
    wirelessStatus.value = '正在连接...'
    await axios.post(`${API_BASE.value}/api/command`, {
      command: 'adb start-server'
    })
    
    const target = wirelessIp.value.includes(':') ? wirelessIp.value.trim() : `${wirelessIp.value.trim()}:5555`
    const response = await axios.post(`${API_BASE.value}/api/command`, {
      command: `adb connect ${target}`,
    })
    
    if (response.data.code === 200) {
      wirelessStatus.value = '连接成功'
      fetchDevices()
    } else {
      wirelessStatus.value = '连接失败: ' + (response.data.message || '未知错误')
    }
  } catch (error) {
    console.error('无线连接失败:', error)
    wirelessStatus.value = '连接失败: ' + (error instanceof Error ? error.message : String(error))
  }
}

const goHome = () => {
  emit('back');
};

// 弹窗方法
const showMessage = (message: string, title: string = '提示') => {
  modalType.value = 'message'
  modalTitle.value = title
  modalMessage.value = message
  modalVisible.value = true
  modalCallback.value = null
};

const showConfirm = (message: string, callback: Function, title: string = '确认') => {
  modalType.value = 'confirm'
  modalTitle.value = title
  modalMessage.value = message
  modalVisible.value = true
  modalCallback.value = callback
};

const showPrompt = (message: string, callback: Function, placeholder: string = '', title: string = '输入') => {
  modalType.value = 'prompt'
  modalTitle.value = title
  modalMessage.value = message
  modalPlaceholder.value = placeholder
  modalVisible.value = true
  modalCallback.value = callback
};

const showList = (
  items: Array<{ value: string; label: string; desc?: string }>,
  callback: Function,
  title: string = '选择',
  emptyText: string = '暂无数据',
  confirmText: string = '确定'
) => {
  modalType.value = 'list'
  modalTitle.value = title
  modalMessage.value = ''
  modalListItems.value = items
  modalEmptyText.value = emptyText
  modalConfirmText.value = confirmText
  modalVisible.value = true
  modalCallback.value = callback
};

const showNotification = (message: string, variant: 'success' | 'error' | 'info' = 'success') => {
  modalType.value = 'notification'
  modalVariant.value = variant
  modalProgress.value = false
  modalMessage.value = message
  modalVisible.value = true
};

// 长耗时操作的右上角进度通知：不定进度（adb push 走管道拿不到百分比），不自动消失
const showProgressNotification = (message: string) => {
  modalType.value = 'notification'
  modalVariant.value = 'info'
  modalProgress.value = true
  modalMessage.value = message
  modalVisible.value = true
};

const hideProgressNotification = () => {
  if (modalType.value === 'notification' && modalProgress.value) {
    modalProgress.value = false
    modalVisible.value = false
  }
};

const handleModalConfirm = (value?: string) => {
  if (modalCallback.value) {
    modalCallback.value(value)
  }
  modalVisible.value = false
};

const handleModalCancel = () => {
  modalVisible.value = false
};

// 复制文本到剪贴板
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

const confirmDisconnect = (deviceId: string) => {
  showConfirm('确定要断开设备连接吗？', async () => {
    try {
      const response = await axios.post(`${API_BASE.value}/api/device/disconnect`, {
        deviceId: deviceId
      })
      if (response.data.code === 200) {
        showNotification('设备已断开')
        fetchDevices()
      } else {
        showMessage('断开失败: ' + (response.data.message || '未知错误'))
      }
    } catch (error) {
      console.error('断开设备失败:', error)
      showMessage('断开设备失败')
    }
  })
};

// 复制设备信息：把卡片上展示的 9 项拼成「标签：值」逐行文本复制到剪贴板
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
const disconnectAllLoading = ref(false)
const confirmDisconnectAll = () => {
  showConfirm('确定要断开全部网络设备的 ADB 连接吗？', async () => {
    disconnectAllLoading.value = true
    try {
      const response = await axios.post(`${API_BASE.value}/api/device/disconnect/all`, {})
      if (response.data.code === 200) {
        const data = response.data.data
        if (!data || data.total === 0) {
          showNotification('没有网络连接的设备', 'info')
        } else if (data.failedCount > 0) {
          showNotification(`断开完成：成功 ${data.successCount} 个，失败 ${data.failedCount} 个`, 'error')
        } else {
          showNotification(`已断开全部网络设备（共 ${data.successCount} 个）`)
        }
        fetchDevices()
      } else {
        showMessage('断开失败: ' + (response.data.message || '未知错误'))
      }
    } catch (error) {
      console.error('断开全部设备失败:', error)
      showMessage('断开全部设备失败')
    } finally {
      disconnectAllLoading.value = false
    }
  }, '确认断开')
}

// ==================== 性能监控 ====================
// 接口: POST /api/device/monitor（静态指标）、POST /api/device/monitor/rate（采样速率）
// 原则：未进入该 Tab 不发任何请求；进入后静态指标低频刷新，速率指标按 2s 轮询且防止请求堆积

interface CpuCoreFreq {
  core: number;
  currentKhz: number | null;
}

interface MonitorSnapshot {
  cpu: {
    coreCount: number;
    maxFreqKhz: number | null;
    cores: CpuCoreFreq[];
  } | null;
  memory: {
    totalBytes: number;
    usedBytes: number;
    availableBytes: number;
    freeBytes?: number;
    cachedBytes?: number;
    usagePercent: number;
  } | null;
  disk: {
    mountPoint: string;
    totalBytes: number;
    usedBytes: number;
    availableBytes: number;
    usagePercent: number;
  } | null;
}

interface MonitorRate {
  duration: number;
  cpu: { usagePercent: number } | null;
  network: Array<{
    name: string;
    rxBytes: number;
    txBytes: number;
    rxBytesPerSec: number;
    txBytesPerSec: number;
  }>;
}

const RATE_POLL_INTERVAL = 2000;   // 速率轮询间隔，接口文档建议不小于 2s
const SNAPSHOT_INTERVAL = 8000;    // 静态指标刷新间隔，变化慢
const RATE_HISTORY_MAX = 30;       // 折线图保留的采样点数

const monitorSnapshot = ref<MonitorSnapshot | null>(null)
const monitorRate = ref<MonitorRate | null>(null)
const monitorLoading = ref(false)      // 首次加载
const monitorError = ref('')
const monitorActive = ref(false)       // 是否停留在性能监控 Tab（懒加载开关）
const rateHistory = ref<Array<{ time: string; cpu: number; rx: number; tx: number }>>([])

// 第一行卡片视图切换：chart = CPU/网络折线图，top = top 命令进程列表
type RateViewMode = 'chart' | 'top'
const rateViewMode = ref<RateViewMode>('chart')

interface TopProcess {
  pid: number;
  user: string;
  stat: string;
  cpuPercent: number;
  memPercent: number;
  command: string;
}
const topProcesses = ref<TopProcess[]>([])
let topRequesting = false              // 防止 top 请求堆积

// 拉取 top 进程快照（仅 top 视图时轮询，防堆积方式与速率轮询一致）
const fetchMonitorTop = async () => {
  if (!selectedDevice.value || topRequesting) return
  topRequesting = true
  try {
    const response = await axios.post(`${API_BASE.value}/api/device/monitor/top`, {
      deviceId: selectedDevice.value
    })
    if (response.data.code === 200 && response.data.data) {
      topProcesses.value = response.data.data.processes || []
      monitorError.value = ''
    } else {
      monitorError.value = response.data.message || '获取 Top 进程失败'
    }
  } catch (error: any) {
    console.error('获取 Top 进程失败:', error)
    monitorError.value = `获取 Top 进程失败: ${error.message}`
  } finally {
    topRequesting = false
  }
}

// 视图切换时：进入 top 立即拉一次，回到 chart 清空 top 数据（下次进入重新拉）
watch(rateViewMode, async (mode) => {
  if (mode === 'top') {
    await fetchMonitorTop()
  } else {
    topProcesses.value = []
  }
})

let snapshotTimer: ReturnType<typeof setInterval> | null = null
let rateTimer: ReturnType<typeof setTimeout> | null = null
let rateRequesting = false             // 防止上一次请求未返回就发起下一次

const snapshotChartEl = ref<HTMLDivElement | null>(null)
const rateChartEl = ref<HTMLDivElement | null>(null)
const coreChartEl = ref<HTMLDivElement | null>(null)
let snapshotChart: echarts.ECharts | null = null
let rateChart: echarts.ECharts | null = null
let coreChart: echarts.ECharts | null = null

const isDarkMode = () => window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches

// 字节按 1024 进制换算
const formatBytes = (bytes?: number | null, digits = 2): string => {
  if (bytes === null || bytes === undefined || isNaN(bytes)) return '—'
  if (bytes < 1024) return `${bytes} B`
  const units = ['KB', 'MB', 'GB', 'TB']
  let value = bytes / 1024
  let i = 0
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024
    i++
  }
  return `${value.toFixed(digits)} ${units[i]}`
}

// 速率按 1024 进制换算，带 /s
const formatRate = (bytesPerSec?: number | null): string => {
  if (bytesPerSec === null || bytesPerSec === undefined || isNaN(bytesPerSec)) return '—'
  return `${formatBytes(bytesPerSec, 1)}/s`
}

// kHz 转 MHz
const formatFreq = (khz?: number | null): string => {
  if (khz === null || khz === undefined || isNaN(khz)) return '—'
  return `${(khz / 1000).toFixed(0)} MHz`
}

const formatPercent = (value?: number | null): string => {
  if (value === null || value === undefined || isNaN(value)) return '—'
  return `${value.toFixed(1)}%`
}

// 内存/磁盘使用率颜色：低绿、中橙、高红
const usageColor = (percent?: number | null): string => {
  if (percent === null || percent === undefined || isNaN(percent)) return '#bdbdbd'
  if (percent >= 90) return '#f44336'
  if (percent >= 70) return '#ff9800'
  return '#4caf50'
}

const chartTextColor = () => (isDarkMode() ? '#e0e0e0' : '#333333')
const chartAxisColor = () => (isDarkMode() ? '#555555' : '#e0e0e0')
const chartSplitColor = () => (isDarkMode() ? '#40434a' : '#f0f0f0')

// 环形仪表盘：内存 / 磁盘使用率
const buildGaugeOption = (percent: number, color: string, label: string) => ({
  series: [{
    type: 'gauge',
    startAngle: 90,
    endAngle: -270,
    radius: '92%',
    center: ['50%', '50%'],
    pointer: { show: false },
    progress: {
      show: true,
      overlap: false,
      roundCap: true,
      clip: false,
      itemStyle: { color }
    },
    axisLine: {
      lineStyle: { width: 10, color: [[1, isDarkMode() ? '#3a3d45' : '#eef1f4']] }
    },
    splitLine: { show: false },
    axisTick: { show: false },
    axisLabel: { show: false },
    data: [{ value: Number(percent.toFixed(1)), name: label }],
    detail: {
      valueAnimation: true,
      offsetCenter: [0, 0],
      fontSize: 18,
      fontWeight: 'bold',
      color: chartTextColor(),
      formatter: (v: number) => `${v.toFixed(1)}%`
    },
    title: {
      offsetCenter: [0, '32%'],
      fontSize: 11,
      color: isDarkMode() ? '#999999' : '#8a94a6'
    }
  }]
})

// 每核当前频率柱状图
const buildCoreOption = (cores: CpuCoreFreq[], maxFreqKhz: number | null) => ({
  grid: { left: 44, right: 12, top: 16, bottom: 24 },
  tooltip: {
    trigger: 'axis',
    axisPointer: { type: 'shadow' },
    formatter: (params: any) => {
      const p = Array.isArray(params) ? params[0] : params
      return `核心 ${p.name}<br/>当前频率: ${formatFreq(p.value)}`
    }
  },
  xAxis: {
    type: 'category',
    data: cores.map(c => `核${c.core}`),
    axisLine: { lineStyle: { color: chartAxisColor() } },
    axisTick: { show: false },
    axisLabel: { color: isDarkMode() ? '#999999' : '#888888', fontSize: 11 }
  },
  yAxis: {
    type: 'value',
    name: 'MHz',
    nameTextStyle: { color: isDarkMode() ? '#999999' : '#888888', fontSize: 10 },
    max: maxFreqKhz ? Math.ceil(maxFreqKhz / 1000) : undefined,
    axisLine: { show: false },
    axisTick: { show: false },
    splitLine: { lineStyle: { color: chartSplitColor() } },
    axisLabel: { color: isDarkMode() ? '#999999' : '#888888', fontSize: 10 }
  },
  series: [{
    type: 'bar',
    data: cores.map(c => (c.currentKhz === null || c.currentKhz === undefined ? 0 : Number((c.currentKhz / 1000).toFixed(0)))),
    barMaxWidth: 36,
    itemStyle: { color: '#4caf50', borderRadius: [4, 4, 0, 0] },
    label: {
      show: true,
      position: 'top',
      fontSize: 10,
      color: isDarkMode() ? '#999999' : '#888888',
      formatter: (p: any) => (p.value ? `${p.value}` : '—')
    }
  }]
})

// CPU 占用率 + 上下行速率折线图
const buildRateOption = (history: Array<{ time: string; cpu: number; rx: number; tx: number }>) => {
  const textColor = isDarkMode() ? '#999999' : '#888888'
  const toKb = (v: number) => Number((v / 1024).toFixed(1))
  return {
    grid: { left: 52, right: 52, top: 34, bottom: 24 },
    tooltip: {
      trigger: 'axis',
      formatter: (params: any) => {
        const list = Array.isArray(params) ? params : [params]
        const head = list[0]?.axisValue ?? ''
        const lines = list.map((p: any) => {
          if (p.seriesName === 'CPU') return `CPU: ${p.value}%`
          return `${p.seriesName}: ${p.value} KB/s`
        })
        return [head, ...lines].join('<br/>')
      }
    },
    legend: {
      data: ['CPU', '下载', '上传'],
      top: 0,
      itemWidth: 12,
      itemHeight: 8,
      textStyle: { color: textColor, fontSize: 11 }
    },
    xAxis: {
      type: 'category',
      boundaryGap: false,
      data: history.map(h => h.time),
      axisLine: { lineStyle: { color: chartAxisColor() } },
      axisTick: { show: false },
      axisLabel: { color: textColor, fontSize: 10 }
    },
    yAxis: [
      {
        type: 'value',
        name: 'CPU %',
        min: 0,
        max: 100,
        nameTextStyle: { color: textColor, fontSize: 10 },
        axisLine: { show: false },
        axisTick: { show: false },
        splitLine: { lineStyle: { color: chartSplitColor() } },
        axisLabel: { color: textColor, fontSize: 10 }
      },
      {
        type: 'value',
        name: 'KB/s',
        min: 0,
        nameTextStyle: { color: textColor, fontSize: 10 },
        axisLine: { show: false },
        axisTick: { show: false },
        splitLine: { show: false },
        axisLabel: { color: textColor, fontSize: 10 }
      }
    ],
    series: [
      {
        name: 'CPU',
        type: 'line',
        smooth: true,
        showSymbol: false,
        yAxisIndex: 0,
        data: history.map(h => h.cpu),
        lineStyle: { width: 2, color: '#4caf50' },
        itemStyle: { color: '#4caf50' }
      },
      {
        name: '下载',
        type: 'line',
        smooth: true,
        showSymbol: false,
        yAxisIndex: 1,
        data: history.map(h => toKb(h.rx)),
        lineStyle: { width: 2, color: '#2196f3' },
        itemStyle: { color: '#2196f3' }
      },
      {
        name: '上传',
        type: 'line',
        smooth: true,
        showSymbol: false,
        yAxisIndex: 1,
        data: history.map(h => toKb(h.tx)),
        lineStyle: { width: 2, color: '#ff9800' },
        itemStyle: { color: '#ff9800' }
      }
    ]
  }
}

const renderSnapshotCharts = () => {
  const snap = monitorSnapshot.value
  if (!snap) return
  if (snapshotChartEl.value && !snapshotChart) {
    snapshotChart = echarts.init(snapshotChartEl.value)
  }
  if (snapshotChart) {
    const memPercent = snap.memory?.usagePercent ?? 0
    const diskPercent = snap.disk?.usagePercent ?? 0
    snapshotChart.setOption({
      series: [
        { ...buildGaugeOption(memPercent, usageColor(memPercent), '内存').series[0], center: ['25%', '50%'] },
        { ...buildGaugeOption(diskPercent, usageColor(diskPercent), '磁盘').series[0], center: ['75%', '50%'] }
      ]
    }, true)
  }
  if (coreChartEl.value && !coreChart) {
    coreChart = echarts.init(coreChartEl.value)
  }
  if (coreChart) {
    coreChart.setOption(buildCoreOption(snap.cpu?.cores ?? [], snap.cpu?.maxFreqKhz ?? null), true)
  }
}

const renderRateChart = () => {
  if (rateChartEl.value && !rateChart) {
    rateChart = echarts.init(rateChartEl.value)
  }
  if (rateChart) {
    rateChart.setOption(buildRateOption(rateHistory.value), true)
  }
}

const resizeMonitorCharts = () => {
  snapshotChart?.resize()
  rateChart?.resize()
  coreChart?.resize()
}

// 多网卡时取流量最大的那个作为展示口径，摘要与折线图保持一致
const activeNic = computed(() => {
  const list = monitorRate.value?.network || []
  return list.reduce<MonitorRate['network'][number] | null>((acc, cur) => {
    if (!acc) return cur
    return (cur.rxBytesPerSec + cur.txBytesPerSec) > (acc.rxBytesPerSec + acc.txBytesPerSec) ? cur : acc
  }, null)
})

// 静态指标：内存 / CPU 频率 / 磁盘
const fetchMonitorSnapshot = async () => {
  if (!selectedDevice.value) return
  try {
    const response = await axios.post(`${API_BASE.value}/api/device/monitor`, {
      deviceId: selectedDevice.value
    })
    if (response.data.code === 200 && response.data.data) {
      monitorSnapshot.value = response.data.data
      monitorError.value = ''
      await nextTick()
      renderSnapshotCharts()
    } else {
      monitorError.value = response.data.message || '获取性能快照失败'
    }
  } catch (error: any) {
    console.error('获取性能快照失败:', error)
    monitorError.value = `获取性能快照失败: ${error.message}`
  }
}

// 速率指标：CPU 占用率 + 网络上下行。用标志位防止请求堆积
const fetchMonitorRate = async () => {
  if (!selectedDevice.value || rateRequesting) return
  rateRequesting = true
  try {
    const response = await axios.post(`${API_BASE.value}/api/device/monitor/rate`, {
      deviceId: selectedDevice.value,
      duration: 1
    })
    if (response.data.code === 200 && response.data.data) {
      const data: MonitorRate = response.data.data
      monitorRate.value = data
      monitorError.value = ''
      const net = activeNic.value
      const now = new Date()
      const time = `${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}:${String(now.getSeconds()).padStart(2, '0')}`
      const next = rateHistory.value.concat([{
        time,
        cpu: data.cpu?.usagePercent ?? 0,
        rx: net?.rxBytesPerSec ?? 0,
        tx: net?.txBytesPerSec ?? 0
      }])
      rateHistory.value = next.length > RATE_HISTORY_MAX ? next.slice(next.length - RATE_HISTORY_MAX) : next
      await nextTick()
      renderRateChart()
    } else {
      monitorError.value = response.data.message || '获取实时速率失败'
    }
  } catch (error: any) {
    console.error('获取实时速率失败:', error)
    monitorError.value = `获取实时速率失败: ${error.message}`
  } finally {
    rateRequesting = false
  }
}

// 速率/top 轮询：上一次请求返回后再排下一次，天然防止堆积。
// top 视图下折线数据停止累积，改为轮询 top 进程快照。
const scheduleRatePoll = () => {
  if (rateTimer) {
    clearTimeout(rateTimer)
    rateTimer = null
  }
  if (!monitorActive.value || !selectedDevice.value) return
  rateTimer = setTimeout(async () => {
    rateTimer = null
    if (!monitorActive.value || !selectedDevice.value) return
    if (rateViewMode.value === 'top') {
      await fetchMonitorTop()
    } else {
      await fetchMonitorRate()
    }
    scheduleRatePoll()
  }, RATE_POLL_INTERVAL)
}

const startMonitor = async () => {
  if (monitorActive.value) return
  monitorActive.value = true
  if (!selectedDevice.value) return
  monitorLoading.value = true
  monitorError.value = ''
  rateHistory.value = []
  try {
    await fetchMonitorSnapshot()
    await fetchMonitorRate()
    await nextTick()
    renderSnapshotCharts()
    renderRateChart()
  } finally {
    monitorLoading.value = false
    scheduleRatePoll()
    if (!snapshotTimer) {
      snapshotTimer = setInterval(() => {
        if (monitorActive.value) fetchMonitorSnapshot()
      }, SNAPSHOT_INTERVAL)
    }
  }
}

const stopMonitor = () => {
  monitorActive.value = false
  rateRequesting = false
  topRequesting = false
  if (rateTimer) {
    clearTimeout(rateTimer)
    rateTimer = null
  }
  if (snapshotTimer) {
    clearInterval(snapshotTimer)
    snapshotTimer = null
  }
}

const refreshMonitor = async () => {
  if (!monitorActive.value) return
  monitorLoading.value = true
  monitorError.value = ''
  try {
    await fetchMonitorSnapshot()
    await fetchMonitorRate()
    await nextTick()
    renderSnapshotCharts()
    renderRateChart()
  } finally {
    monitorLoading.value = false
  }
}

const disposeMonitorCharts = () => {
  snapshotChart?.dispose()
  rateChart?.dispose()
  coreChart?.dispose()
  snapshotChart = null
  rateChart = null
  coreChart = null
}

onMounted(async () => {
  await loadConfig();
  
  // 组件挂载时自动刷新设备信息
  fetchDevices();
  
  if (window.utools) {
    window.utools.onPluginEnter((action) => {
      route.value = action.code
      enterAction.value = action
      fetchDevices()
    })
    window.utools.onPluginOut((isKill) => {
      route.value = ''
    })
  }
})

// 懒加载：只有真正切到性能监控 Tab 才发起请求，离开立即停止轮询并销毁图表
// 注意：面板是 v-if，离开 Tab 后 DOM 会被销毁，图表实例必须一并释放，否则再次进入会操作已脱离文档的节点
watch(activeTab, async (tab) => {
  if (tab === 'monitor') {
    await nextTick()
    startMonitor()
    resizeMonitorCharts()
  } else {
    stopMonitor()
    disposeMonitorCharts()
  }
})

// 监控中切换设备时，重置历史并立即拉取新设备数据
watch(selectedDevice, async (deviceId) => {
  if (!monitorActive.value) return
  rateHistory.value = []
  topProcesses.value = []
  monitorSnapshot.value = null
  monitorRate.value = null
  disposeMonitorCharts()
  if (!deviceId) return
  await nextTick()
  await refreshMonitor()
  scheduleRatePoll()
})

onMounted(() => {
  window.addEventListener('resize', resizeMonitorCharts)
  window.addEventListener('keydown', scriptOnKeyDown)
})

onUnmounted(() => {
  // 组件销毁时务必停掉轮询并释放图表实例
  window.removeEventListener('resize', resizeMonitorCharts)
  window.removeEventListener('keydown', scriptOnKeyDown)
  stopMonitor()
  disposeMonitorCharts()
  // 关闭批量投屏会话
  stopBatchRemote()
  // 关闭模拟脚本屏幕流（含重连定时器与卡顿看门狗）
  stopScriptStream()
  stopStallWatch()
  // 停止持续读取串口（0x03）循环
  stopSerialRead()
})

// ---------- 模拟串口 ----------

interface SerialPort {
  device: string
  majorMinor: string
}

const serialPorts = ref<SerialPort[]>([])
const serialPortsLoading = ref(false)
const serialToolDeployed = ref(true)
const serialDevice = ref('/dev/ttyS3')
const serialMode = ref<'text' | 'hex'>('text')
const serialPayload = ref('HELLO-1234')
const serialEnd = ref('none')
const serialBaud = ref('')
const serialLoop = ref(true)
const serialSending = ref(false)
const serialOutput = ref('')
const serialMessage = ref('')
const serialMessageType = ref<'ok' | 'err' | ''>('')
const serialStreamRunning = ref(false)
const serialStreamPid = ref('')
const serialStreamLog = ref('')
const serialStreamBusy = ref(false)

// 串口会话（探针 0x04 打开 / 0x03 取增量 / 0x05 发送 / 0x07 关闭）
const serialReadBaud = ref('9600')
const serialReadParity = ref('none')
const serialReadDataBits = ref('8')
const serialReadStopBits = ref('1')
const serialReadView = ref<'hex' | 'text'>('hex')
const serialSendMode = ref<'text' | 'hex'>('text')
const serialSendPayload = ref('HELLO')
const serialSendTimes = ref('1')
const serialSendInterval = ref('500')
const serialSessionOpened = ref(false)
const serialReading = ref(false)
let serialReadLoopId = 0

// 切换设备后重新拉取串口列表
watch(selectedDevice, async (deviceId) => {
  serialPorts.value = []
  serialStreamRunning.value = false
  serialStreamPid.value = ''
  serialStreamLog.value = ''
  if (!deviceId) return
  await nextTick()
  await loadSerialPorts()
})

// 首次进入串口页时加载端口列表
watch(activeTab, async (tab) => {
  if (tab === 'serial' && selectedDevice.value && serialPorts.value.length === 0) {
    await nextTick()
    await loadSerialPorts()
  }
})

const loadSerialPorts = async () => {
  if (!selectedDevice.value) return
  serialPortsLoading.value = true
  try {
    const res = await axios.post(`${API_BASE.value}/api/device/serial/ports`, {
      deviceId: selectedDevice.value
    })
    if (res.data.code === 200) {
      serialPorts.value = res.data.data.ports || []
      serialToolDeployed.value = !!res.data.data.toolDeployed
      // 默认选中第一个串口
      if (serialPorts.value.length && !serialPorts.value.some(p => p.device === serialDevice.value)) {
        serialDevice.value = serialPorts.value[0].device
      }
    } else {
      serialMessage.value = res.data.message || '获取串口列表失败'
      serialMessageType.value = 'err'
    }
  } catch (e: any) {
    serialMessage.value = e?.response?.data?.message || '获取串口列表失败'
    serialMessageType.value = 'err'
  } finally {
    serialPortsLoading.value = false
  }
}

const deploySerialTool = async () => {
  if (!selectedDevice.value) return
  serialSending.value = true
  try {
    const res = await axios.post(`${API_BASE.value}/api/device/serial/deploy`, {
      deviceId: selectedDevice.value
    })
    if (res.data.code === 200) {
      serialMessage.value = '串口工具已部署到设备'
      serialMessageType.value = 'ok'
      serialToolDeployed.value = true
    } else {
      serialMessage.value = res.data.message || '部署失败'
      serialMessageType.value = 'err'
    }
  } catch (e: any) {
    serialMessage.value = e?.response?.data?.message || '部署失败'
    serialMessageType.value = 'err'
  } finally {
    serialSending.value = false
  }
}

const sendSerialData = async () => {
  if (!selectedDevice.value) return
  serialSending.value = true
  serialMessage.value = ''
  try {
    const res = await axios.post(`${API_BASE.value}/api/device/serial/send`, {
      deviceId: selectedDevice.value,
      device: serialDevice.value,
      mode: serialMode.value,
      payload: serialPayload.value,
      end: serialEnd.value,
      baud: serialBaud.value ? Number(serialBaud.value) : null,
      loop: serialLoop.value
    })
    const data = res.data.data || {}
    serialOutput.value = data.output || ''
    if (res.data.code === 200) {
      serialMessage.value = `发送成功（${data.sentBytes || 0} 字节）`
      serialMessageType.value = 'ok'
    } else {
      serialMessage.value = res.data.message || '发送失败'
      serialMessageType.value = 'err'
    }
  } catch (e: any) {
    serialMessage.value = e?.response?.data?.message || '发送失败'
    serialMessageType.value = 'err'
    serialOutput.value = ''
  } finally {
    serialSending.value = false
  }
}

const serialStream = async (action: 'start' | 'stop' | 'status') => {
  if (!selectedDevice.value) return
  serialStreamBusy.value = true
  try {
    const res = await axios.post(`${API_BASE.value}/api/device/serial/stream`, {
      deviceId: selectedDevice.value,
      device: serialDevice.value,
      baud: serialBaud.value ? Number(serialBaud.value) : 115200,
      loop: serialLoop.value,
      action
    })
    const data = res.data.data || {}
    serialStreamRunning.value = !!data.running
    serialStreamPid.value = data.pid || ''
    serialStreamLog.value = data.log || ''
    if (action !== 'status') {
      serialMessage.value = res.data.message || ''
      serialMessageType.value = res.data.code === 200 ? 'ok' : 'err'
    }
  } catch (e: any) {
    serialMessage.value = e?.response?.data?.message || '操作失败'
    serialMessageType.value = 'err'
  } finally {
    serialStreamBusy.value = false
  }
}

// 组 0x04 打开串口会话参数；串口未选时写入提示并返回 null
const buildSerialOpenPayload = (): Record<string, unknown> | null => {
  if (!serialDevice.value) {
    serialMessage.value = '请先选择串口设备'
    serialMessageType.value = 'err'
    return null
  }
  return {
    serial: serialDevice.value,
    baud: Number(serialReadBaud.value),
    parity: serialReadParity.value,
    dataBits: Number(serialReadDataBits.value),
    stopBits: Number(serialReadStopBits.value)
  }
}

// 向探针发一条串口命令并解出业务 data；失败写入状态提示并返回 null
const postSerialCommand = async (cmd: number, data: Record<string, unknown>, label: string): Promise<Record<string, any> | null> => {
  try {
    const res = await axios.post(`${API_BASE.value}/api/probe/message`, {
      deviceId: selectedDevice.value,
      cmd,
      data
    }, { timeout: 200000 })
    const d = res.data
    if (d?.code !== 200 || d?.data?.connected !== true) {
      serialMessage.value = `${label}失败：${d?.message || '通讯失败'}`
      serialMessageType.value = 'err'
      return null
    }
    const r = d.data.response || {}
    if (r.code !== 200) {
      serialMessage.value = `${label}失败：${r.message || '设备返回错误'}`
      serialMessageType.value = 'err'
      return null
    }
    return r.data || {}
  } catch (e: any) {
    serialMessage.value = `${label}失败：${e?.response?.data?.message || e?.message || '请求异常'}`
    serialMessageType.value = 'err'
    return null
  }
}

// 执行日志追加一行，最多保留最近 100 行
const appendSerialReadLine = (line: string) => {
  const lines = serialOutput.value ? serialOutput.value.split('\n') : []
  lines.push(line)
  if (lines.length > 100) lines.splice(0, lines.length - 100)
  serialOutput.value = lines.join('\n')
}

// 记录一轮失败（日志行 + 状态提示）
const failSerialReadLine = (count: number, msg: string) => {
  appendSerialReadLine(`#${count} ${new Date().toLocaleTimeString()} 错误：${msg}`)
  serialMessage.value = `持续读取中（第 ${count} 次失败：${msg}）`
  serialMessageType.value = 'err'
}

// 单轮读取：从会话缓冲取增量字节并按 Hex/Txt 原样追加；true 成功 / false 失败 / null 会话已关闭需停止
const readSerialData = async (count: number, token: number): Promise<boolean | null> => {
  if (!serialSessionOpened.value) return null
  const rd = await postSerialCommand(0x03, {}, '串口读取')
  if (serialReadLoopId !== token) return false
  if (!rd) {
    failSerialReadLine(count, serialMessage.value)
    return false
  }
  const bytes = Number(rd.bytes ?? 0)
  if (bytes > 0) {
    const shown = serialReadView.value === 'text' ? (rd.text ?? '') : (rd.hex ?? '')
    appendSerialReadLine(`#${count} ${new Date().toLocaleTimeString()} [Read][${serialReadView.value === 'text' ? 'Txt' : 'Hex'}] ${shown}`)
  }
  return true
}

const stopSerialRead = async () => {
  serialReading.value = false
  serialReadLoopId += 1
  if (serialSessionOpened.value) {
    serialSessionOpened.value = false
    const closed = await postSerialCommand(0x07, {}, '关闭串口会话')
    if (closed) {
      appendSerialReadLine(`${new Date().toLocaleTimeString()} 串口已关闭`)
    }
  }
}

// 开启后连续读取：成功立即下一轮，失败等 1 秒；关闭 / 切设备 / 切方案后停发新请求
const runSerialReadLoop = async (token: number) => {
  const deviceAtStart = selectedDevice.value
  let count = 0
  while (
    serialReading.value &&
    serialReadLoopId === token &&
    serialScheme.value === 'read' &&
    selectedDevice.value === deviceAtStart
  ) {
    count += 1
    const ok = await readSerialData(count, token)
    if (ok === null) {
      serialReading.value = false
      return
    }
    if (!serialReading.value || serialReadLoopId !== token) break
    if (!ok) {
      await new Promise(resolve => setTimeout(resolve, 1000))
    }
  }
  if (serialReadLoopId === token) {
    serialReading.value = false
    serialMessage.value = `已停止持续读取（共 ${count} 次）`
    serialMessageType.value = 'ok'
  }
}

// 按钮：打开串口并开启持续读 / 关闭读取并关闭串口会话
const toggleSerialRead = async () => {
  if (serialReading.value) {
    stopSerialRead()
    return
  }
  if (!selectedDevice.value) return
  const openData = buildSerialOpenPayload()
  if (!openData) return
  serialOutput.value = ''
  const opened = await postSerialCommand(0x04, openData, '打开串口会话')
  if (!opened) return
  serialSessionOpened.value = true
  appendSerialReadLine(`${new Date().toLocaleTimeString()} 串口已打开：${opened.serial || serialDevice.value} ${opened.baud || ''} ${opened.parity || ''}/${opened.dataBits || ''}/${opened.stopBits || ''}`)
  serialReadLoopId += 1
  const token = serialReadLoopId
  serialReading.value = true
  serialMessage.value = '持续读取中...'
  serialMessageType.value = 'ok'
  runSerialReadLoop(token)
}

// 单次 / 循环发送：mode=text|hex，times=0 表示持续到手动停止
const sendSerialSessionData = async () => {
  if (!selectedDevice.value) {
    serialMessage.value = '请先在左侧选择设备'
    serialMessageType.value = 'err'
    return
  }
  if (!serialSessionOpened.value) {
    serialMessage.value = '请先打开串口会话'
    serialMessageType.value = 'err'
    return
  }
  const payload = serialSendPayload.value
  if (!payload.trim()) {
    serialMessage.value = '发送内容不能为空'
    serialMessageType.value = 'err'
    return
  }
  const times = Number(serialSendTimes.value)
  const interval = Number(serialSendInterval.value)
  if (!Number.isInteger(times) || times < 0 || times > 10000) {
    serialMessage.value = '发送次数必须是 0~10000 的整数（0=持续到手动停止）'
    serialMessageType.value = 'err'
    return
  }
  if (!Number.isInteger(interval) || interval < 0 || interval > 60000) {
    serialMessage.value = '循环间隔必须是 0~60000 的整数（毫秒）'
    serialMessageType.value = 'err'
    return
  }
  const r = await postSerialCommand(0x05, {
    mode: serialSendMode.value,
    payload,
    times,
    interval
  }, '串口发送')
  if (!r) return
  appendSerialReadLine(`${new Date().toLocaleTimeString()} [Send][${serialSendMode.value === 'text' ? 'Txt' : 'Hex'}] ${payload}`)
  serialMessage.value = r.loop ? `循环发送中（${times || '持续'} 次 / ${interval}ms）` : '发送成功'
  serialMessageType.value = 'ok'
}

const stopSerialSessionSend = async () => {
  const r = await postSerialCommand(0x05, { stop: true }, '停止循环发送')
  if (!r) return
  appendSerialReadLine(`${new Date().toLocaleTimeString()} [Send] 循环发送已停止`)
  serialMessage.value = '循环发送已停止'
  serialMessageType.value = 'ok'
}

// ---------- 模拟串口：方案 B（pty 替换 + 按键注入） ----------

const serialScheme = ref<'loop' | 'pty' | 'read'>('loop')

// pty 状态
const ptyRunning = ref(false)
const ptyPid = ref('')
const ptyNodeType = ref('')
const ptyAppHolds = ref(false)
const ptyLeftover = ref('')
const ptyLog = ref('')
const ptyBusy = ref(false)

// 按键
const keyList = ref<Array<{ name: string; value: number; label?: string; desc?: string }>>([])
const keyInjectBusy = ref(false)
const keyInjectOutput = ref('')

// 按键名 -> 协议帧（55 | 03 | 05 | KEY | CRC，CRC=(0x05+KEY)&0xFF）
const buildKeyFrame = (keyValue: number) => {
  const bytes = [0x55, 0x03, 0x05, keyValue & 0xFF, (0x05 + keyValue) & 0xFF]
  return bytes.map(b => b.toString(16).padStart(2, '0').toUpperCase()).join('')
}

// pty 设备节点（与方案 A 独立，避免混淆）
const ptyDevice = ref('/dev/ttyS7')

const loadKeyList = async () => {
  try {
    const res = await axios.post(`${API_BASE.value}/api/device/serial/keys/list`, {
      deviceId: selectedDevice.value || ''
    })
    if (res.data.code === 200) {
      keyList.value = res.data.data.keys || []
    }
  } catch (e) {
    console.error('获取按键列表失败:', e)
  }
}

const loadPtyStatus = async () => {
  if (!selectedDevice.value) return
  try {
    const res = await axios.post(`${API_BASE.value}/api/device/serial/pty/status`, {
      deviceId: selectedDevice.value,
      device: ptyDevice.value
    })
    if (res.data.code === 200) {
      const d = res.data.data
      ptyRunning.value = !!d.running
      ptyPid.value = d.pid || ''
      ptyNodeType.value = d.nodeType || ''
      ptyAppHolds.value = !!d.appHolds
      ptyLeftover.value = d.leftoverMount || ''
      ptyLog.value = d.log || ''
    }
  } catch (e: any) {
    serialMessage.value = e?.response?.data?.message || '查询 pty 状态失败'
    serialMessageType.value = 'err'
  }
}

const deployPtyTools = async () => {
  if (!selectedDevice.value) return
  ptyBusy.value = true
  try {
    const res = await axios.post(`${API_BASE.value}/api/device/serial/pty/deploy`, {
      deviceId: selectedDevice.value
    })
    serialMessage.value = res.data.code === 200 ? 'pty 工具已部署到设备' : (res.data.message || '部署失败')
    serialMessageType.value = res.data.code === 200 ? 'ok' : 'err'
  } catch (e: any) {
    serialMessage.value = e?.response?.data?.message || '部署失败'
    serialMessageType.value = 'err'
  } finally {
    ptyBusy.value = false
  }
}

const controlPty = async (action: 'start' | 'stop') => {
  if (!selectedDevice.value) return
  ptyBusy.value = true
  serialMessage.value = ''
  try {
    const res = await axios.post(`${API_BASE.value}/api/device/serial/pty/control`, {
      deviceId: selectedDevice.value,
      device: ptyDevice.value,
      action,
      restartApp: true
    })
    const d = res.data.data || {}
    ptyRunning.value = !!d.running
    ptyPid.value = d.pid || ''
    ptyNodeType.value = d.nodeType || ''
    ptyAppHolds.value = !!d.appHolds
    ptyLeftover.value = d.leftoverMount || ''
    ptyLog.value = d.log || ''
    serialMessage.value = res.data.message || ''
    serialMessageType.value = res.data.code === 200 ? 'ok' : 'err'
  } catch (e: any) {
    serialMessage.value = e?.response?.data?.message || '操作失败'
    serialMessageType.value = 'err'
  } finally {
    ptyBusy.value = false
  }
}

// 按键序列：每次点击追加一个，同一个键可以重复按多次
const keySequence = ref<string[]>([])

const appendKey = (keyName: string) => {
  keySequence.value.push(keyName)
}

const removeKeyAt = (index: number) => {
  keySequence.value.splice(index, 1)
}

const clearKeySequence = () => {
  keySequence.value = []
}

// 序列对应的显示文字（数字键直接显示数字，符号键显示功能名）
const keyFunctionText = (keyName: string) => {
  const found = keyList.value.find(k => k.name === keyName)
  return found?.label || keyName
}

const applyKeys = async () => {
  if (!selectedDevice.value) return
  if (keySequence.value.length === 0) {
    serialMessage.value = '请先点击按键添加序列'
    serialMessageType.value = 'err'
    return
  }
  keyInjectBusy.value = true
  serialMessage.value = ''
  try {
    const res = await axios.post(`${API_BASE.value}/api/device/serial/keys`, {
      deviceId: selectedDevice.value,
      device: ptyDevice.value,
      keys: keySequence.value.join(','),
      interval: 1500
    })
    const d = res.data.data || {}
    keyInjectOutput.value = (d.frames || []).join(', ')
    if (res.data.code === 200) {
      serialMessage.value = res.data.message || '按键已注入'
      serialMessageType.value = 'ok'
      ptyRunning.value = !!d.running
      ptyPid.value = d.pid || ''
      ptyNodeType.value = d.nodeType || ''
      ptyAppHolds.value = !!d.appHolds
    } else {
      serialMessage.value = res.data.message || '按键注入失败'
      serialMessageType.value = 'err'
    }
  } catch (e: any) {
    serialMessage.value = e?.response?.data?.message || '按键注入失败'
    serialMessageType.value = 'err'
  } finally {
    keyInjectBusy.value = false
  }
}

// 切换方案 / 首次进入时加载按键表
watch(serialScheme, async (scheme) => {
  if (scheme !== 'pty') return
  if (keyList.value.length === 0) await loadKeyList()
  if (selectedDevice.value) await loadPtyStatus()
})

// ===== 探针状态指示（每台设备一个圆点，放在断开按钮左边） =====
interface ProbeState {
  status: 'idle' | 'busy' | 'ok' | 'fail';
  message: string;
}

const probeStates = ref<Record<string, ProbeState>>({});
const probeLoadingMap = ref<Record<string, boolean>>({});
// 只对刷新时新出现的设备自动安装，已处理过的不重复装
const probeKnownDevices = new Set<string>();

const probeStateOf = (id: string): ProbeState =>
  probeStates.value[id] || { status: 'idle', message: '未检测' };

const setProbeState = (id: string, state: ProbeState) => {
  probeStates.value = { ...probeStates.value, [id]: state };
};

const setProbeBusy = (id: string, value: boolean) => {
  const next = { ...probeLoadingMap.value };
  if (value) next[id] = true;
  else delete next[id];
  probeLoadingMap.value = next;
};

const runProbe = async (deviceId: string, install: boolean) => {
  if (!deviceId || probeLoadingMap.value[deviceId]) return;
  setProbeBusy(deviceId, true);
  setProbeState(deviceId, { status: 'busy', message: install ? '安装通讯中...' : '检测中...' });
  try {
    const url = install ? '/api/probe/connect' : '/api/probe/check';
    // 后端安装超时 180s + 连接等待 15s，前端不能在后端还没返回前就判失败
    const res = await axios.post(`${API_BASE.value}${url}`, { deviceId }, { timeout: 200000 });
    const d = res.data;
    const ok = d?.code === 200 && d?.data?.connected === true;
    setProbeState(deviceId, { status: ok ? 'ok' : 'fail', message: d?.message || '通讯失败' });
  } catch (e: any) {
    setProbeState(deviceId, { status: 'fail', message: e?.message || '请求失败' });
  } finally {
    setProbeBusy(deviceId, false);
  }
};

const retryProbe = (deviceId: string) => {
  runProbe(deviceId, true);
};

const autoInstallNewDevices = () => {
  const current = new Set(devices.value.map(d => d.deviceId));
  for (const id of Array.from(probeKnownDevices)) {
    if (!current.has(id)) probeKnownDevices.delete(id);
  }
  for (const d of devices.value) {
    if (!d.deviceId || probeKnownDevices.has(d.deviceId)) continue;
    probeKnownDevices.add(d.deviceId);
    runProbe(d.deviceId, true);
  }
};
</script>
<style scoped>
.sidebar {
  display: flex;
  flex-direction: column;
  width: 250px;
  height: 100%;
}

.device-list-container {
  flex: 1;
  overflow-y: auto;
  margin-bottom: 8px;
}

.probe-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  flex-shrink: 0;
  background-color: #999;
  cursor: pointer;
  transition: background-color 0.2s;
}

.probe-dot.ok {
  background-color: #4caf50;
}

.probe-dot.fail {
  background-color: #f44336;
}

.probe-dot.busy {
  background-color: #ff9800;
}

.probe-dot:hover {
  box-shadow: 0 0 0 3px rgba(0, 0, 0, 0.1);
}

.file-list {
  border: 1px solid #ccc;
  max-height: calc(100vh - 180px);
  overflow-y: auto;
}

.wireless-connect {
  margin-top: auto;
  border-top: 1px solid #e0e0e0;
}

.wireless-input-group {
  display: flex;
  gap: 8px;
  margin-top: 8px;
}

.wireless-input {
  width: 150px;
  height: 32px;
  padding: 0 8px;
  border: 1px solid #ddd;
  border-radius: 4px;
  font-size: 14px;
}

.wireless-button {
  height: 32px;
  padding: 0 16px;
  background-color: #4caf50;
  color: white;
  border: none;
  border-radius: 4px;
  cursor: pointer;
  font-size: 14px;
}

.wireless-button:hover {
  background-color: #45a049;
}

.wireless-status {
  margin-top: 8px;
  font-size: 12px;
  color: #666;
  min-height: 16px;
}

.back-button {
  position: absolute;
  top: 10px;
  left: 10px;
  width: 36px;
  height: 36px;
  padding: 0;
  background: rgba(128, 128, 128, 0.3);
  color: white;
  border: none;
  border-radius: 50%;
  cursor: pointer;
  z-index: 100;
  font-size: 18px;
  line-height: 36px;
  text-align: center;
  transition: background 0.3s ease;
}

.back-button:hover {
  background: rgba(128, 128, 128, 0.8);
}

.app {
  position: relative;
  width: 100%;
  height: 100vh;
  min-height: 544px;
}

/* ===== 投屏页面卡片布局 ===== */
.keys-page {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.scrcpy-card {
  background: #fff;
  border-radius: 10px;
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.08);
  overflow: hidden;
}

.card-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 16px;
  font-size: 14px;
  font-weight: 600;
  color: #333;
  border-bottom: 1px solid #f0f0f0;
  background: #fafafa;
}

.card-icon {
  display: flex;
  align-items: center;
  color: #4caf50;
}

/* 卡片头部右侧操作按钮（如设备信息「复制」） */
.card-header-action {
  margin-left: auto;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 4px 10px;
  border: 1px solid #e0e0e0;
  border-radius: 6px;
  background: #fff;
  color: #555;
  font-size: 12px;
  font-weight: 500;
  font-family: inherit;
  cursor: pointer;
  transition: all 0.2s ease;
}

.card-header-action:hover:not(:disabled) {
  background: #e8f5e9;
  border-color: #a5d6a7;
  color: #2e7d32;
}

.card-header-action:active:not(:disabled) {
  background: #c8e6c9;
}

.card-header-action:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.card-body {
  padding: 16px;
}

/* ===== 投屏启动按钮 ===== */
.scrcpy-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  width: 100%;
  padding: 10px;
  font-size: 14px;
  font-weight: 600;
  font-family: inherit;
  color: #fff;
  background: linear-gradient(135deg, #4caf50, #43a047);
  border: none;
  border-radius: 8px;
  cursor: pointer;
  transition: all 0.3s ease;
  box-shadow: 0 4px 16px rgba(76, 175, 80, 0.3);
}

.scrcpy-btn:hover {
  transform: translateY(-2px);
  box-shadow: 0 6px 20px rgba(76, 175, 80, 0.4);
  background: linear-gradient(135deg, #43a047, #388e3c);
}

.scrcpy-btn:active {
  transform: translateY(0);
  box-shadow: 0 2px 8px rgba(76, 175, 80, 0.3);
}

/* ===== 按键网格 ===== */
.key-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 6px;
  margin-bottom: 10px;
}

.key-btn {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 10px 12px;
  background: #f5f7fa;
  border: 1px solid #e8eaed;
  border-radius: 8px;
  cursor: pointer;
  font-size: 12px;
  font-weight: 500;
  color: #444;
  font-family: inherit;
  transition: all 0.25s ease;
}

.key-btn:hover {
  background: #e8f5e9;
  border-color: #a5d6a7;
  color: #2e7d32;
  transform: translateY(-1px);
  box-shadow: 0 3px 10px rgba(76, 175, 80, 0.12);
}

.key-btn:active {
  transform: translateY(0);
  background: #c8e6c9;
}

.key-btn svg {
  color: #666;
}

.key-btn:hover svg {
  color: #4caf50;
}

/* ===== 重启按钮 ===== */
.restart-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 5px;
  width: 100%;
  padding: 6px;
  background: #fff3f3;
  border: 1px solid #ffcdd2;
  border-radius: 8px;
  cursor: pointer;
  font-size: 13px;
  font-weight: 500;
  color: #c62828;
  font-family: inherit;
  transition: all 0.25s ease;
}

.restart-btn:hover {
  background: #ffebee;
  border-color: #ef9a9a;
  transform: translateY(-1px);
  box-shadow: 0 3px 10px rgba(198, 40, 40, 0.12);
}

.restart-btn:active {
  transform: translateY(0);
}

/* ===== 音量控制行 ===== */
.volume-row {
  display: grid;
  grid-template-columns: 1fr 1fr 1fr;
  gap: 6px;
}

.vol-btn {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 3px;
  padding: 8px 6px;
  background: #f5f7fa;
  border: 1px solid #e8eaed;
  border-radius: 8px;
  cursor: pointer;
  font-size: 12px;
  font-weight: 500;
  color: #444;
  font-family: inherit;
  transition: all 0.25s ease;
}

.vol-btn:hover {
  background: #e3f2fd;
  border-color: #90caf9;
  color: #1565c0;
  transform: translateY(-1px);
  box-shadow: 0 3px 10px rgba(33, 150, 243, 0.12);
}

.vol-btn:active {
  transform: translateY(0);
  background: #bbdefb;
}

.vol-btn.mute-btn:hover {
  background: #fff3e0;
  border-color: #ffcc80;
  color: #e65100;
  box-shadow: 0 3px 10px rgba(255, 152, 0, 0.12);
}

/* ===== 设备信息页 ===== */
.info-grid-2col {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 2px 12px;
}

.info-item-2col {
  padding: 6px 0;
  border-bottom: 1px solid #f5f5f5;
  min-width: 0;
}

.info-item-2col:nth-last-child(-n+2) {
  border-bottom: none;
}

.info-label {
  display: block;
  font-size: 11px;
  color: #999;
  margin-bottom: 2px;
}

.info-value {
  display: block;
  font-size: 13px;
  color: #333;
  word-break: break-all;
}

.copyable-value {
  cursor: pointer;
  padding: 2px 6px;
  margin: -2px -6px;
  border-radius: 4px;
  transition: all 0.2s ease;
  display: inline-block;
  position: relative;
}

.copyable-value:hover:not(.disabled) {
  background-color: #e3f2fd;
  color: #1565c0;
}

.copyable-value:hover:not(.disabled)::after {
  content: '📋';
  margin-left: 4px;
  font-size: 12px;
}

.copyable-value:active:not(.disabled) {
  background-color: #bbdefb;
}

.copyable-value.disabled {
  cursor: not-allowed;
  color: #999;
}

.status-badge {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 10px;
  font-size: 12px;
  font-weight: 500;
}

.status-badge.connected {
  background: #e8f5e9;
  color: #2e7d32;
}

.status-badge.locked {
  background: #fff3e0;
  color: #e65100;
}

.loading-text {
  color: #999;
  font-size: 13px;
}

/* ===== 功能区按钮 ===== */
.feature-grid {
  display: grid;
  grid-template-columns: 1fr 1fr 1fr 1fr;
  gap: 8px;
}

.feature-btn {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 14px 8px;
  background: #f5f7fa;
  border: 1px solid #e8eaed;
  border-radius: 8px;
  cursor: pointer;
  font-size: 12px;
  font-weight: 500;
  color: #444;
  font-family: inherit;
  transition: all 0.25s ease;
}

.feature-btn:hover {
  background: #e8f5e9;
  border-color: #a5d6a7;
  color: #2e7d32;
  transform: translateY(-1px);
  box-shadow: 0 3px 10px rgba(76, 175, 80, 0.12);
}

.feature-btn:active {
  transform: translateY(0);
  background: #c8e6c9;
}

.feature-btn.danger:hover {
  background: #fff3e0;
  border-color: #ffcc80;
  color: #e65100;
  box-shadow: 0 3px 10px rgba(255, 152, 0, 0.12);
}

/* 抓取中状态：红色提示，悬停显示"结束抓取" */
.feature-btn.capturing {
  background: #ffebee;
  border-color: #ef9a9a;
  color: #c62828;
  animation: capturePulse 1.6s ease-in-out infinite;
}

.feature-btn.capturing svg {
  color: #c62828;
}

.capture-stop-label {
  display: none;
}

.feature-btn.capturing:hover {
  background: #ffcdd2;
  border-color: #e57373;
  color: #b71c1c;
  animation: none;
  transform: translateY(-1px);
}

.feature-btn.capturing:hover .capture-stop-label {
  display: inline;
}

.feature-btn.capturing:hover .capture-status-label {
  display: none;
}

@keyframes capturePulse {
  0%, 100% { box-shadow: 0 0 0 0 rgba(239, 83, 80, 0.25); }
  50% { box-shadow: 0 0 0 5px rgba(239, 83, 80, 0.08); }
}

.feature-btn svg {
  color: #666;
}

.feature-btn:hover svg {
  color: #4caf50;
}

.feature-btn.danger:hover svg {
  color: #e65100;
}

/* ===== 应用管理页 ===== */
.card-action-btn {
  margin-left: auto;
  padding: 4px 12px;
  background: #4caf50;
  color: #fff;
  border: none;
  border-radius: 6px;
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  font-family: inherit;
  transition: all 0.2s ease;
}

.card-action-btn:hover {
  background: #43a047;
  transform: translateY(-1px);
}

.card-action-btn:active {
  transform: translateY(0);
}

/* APK 全页拖拽安装覆盖层 */
.apk-drop-overlay {
  position: fixed;
  inset: 0;
  z-index: 9999;
  background: rgba(46, 125, 50, 0.15);
  border: 3px dashed #4caf50;
  display: flex;
  align-items: center;
  justify-content: center;
  /* 覆盖层不接收鼠标事件：不干扰 dragleave/drop 的事件序列 */
  pointer-events: none;
}

.apk-drop-overlay-box {
  padding: 24px 48px;
  background: transparent;
  border: none;
  /* 文字颜色与覆盖层背景同色系，描边用原有深绿，营造浮雕效果 */
  color: #d7e3d7;
  -webkit-text-stroke: 1.5px #1b5e20;
  font-size: 48px;
  font-weight: 800;
  letter-spacing: 4px;
  text-shadow:
    -2px -2px 2px rgba(255, 255, 255, 0.75),
    2px 2px 3px rgba(27, 94, 32, 0.45);
}

.app-list {
  max-height: calc(100vh - 140px);
  overflow-y: auto;
}

.app-item {
  display: flex;
  align-items: center;
  padding: 10px 16px;
  border-top: 1px solid #f0f0f0;
  transition: background 0.2s;
}

.app-item:first-child {
  border-top: none;
}

.app-item:hover {
  background: #f8f9fb;
}

.app-item-info {
  flex: 1;
  min-width: 0;
}

.app-item-name {
  font-size: 13px;
  font-weight: 600;
  color: #333;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.app-item-desc {
  font-size: 11px;
  color: #999;
  margin-top: 2px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.app-item-actions {
  display: flex;
  gap: 4px;
  flex-shrink: 0;
  margin-left: 8px;
}

.app-act-btn {
  width: 28px;
  height: 28px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: 1px solid #e8eaed;
  border-radius: 6px;
  background: #fff;
  cursor: pointer;
  color: #888;
  transition: all 0.2s ease;
}

.app-act-btn:hover {
  border-color: #a5d6a7;
  background: #e8f5e9;
}

.app-act-btn.start:hover {
  border-color: #a5d6a7;
  background: #e8f5e9;
  color: #2e7d32;
}

.app-act-btn.stop:hover {
  border-color: #90caf9;
  background: #e3f2fd;
  color: #1565c0;
}

.app-act-btn.extract:hover {
  border-color: #ffcc80;
  background: #fff3e0;
  color: #e65100;
}

.app-act-btn.uninstall:hover {
  border-color: #ef9a9a;
  background: #ffebee;
  color: #c62828;
}

/* ===== 文件管理页 ===== */
.file-path-text {
  font-size: 13px;
  font-weight: 500;
  color: #555;
  flex: 1;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  margin-right: 8px;
}

.file-toolbar {
  display: flex;
  gap: 4px;
  padding: 8px 12px;
  border-bottom: 1px solid #f0f0f0;
  background: #fafafa;
}

.toolbar-btn {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 5px 10px;
  background: #fff;
  border: 1px solid #e8eaed;
  border-radius: 6px;
  cursor: pointer;
  font-size: 12px;
  color: #555;
  font-family: inherit;
  transition: all 0.2s ease;
}

.toolbar-btn:hover {
  background: #e8f5e9;
  border-color: #a5d6a7;
  color: #2e7d32;
}

.toolbar-btn:active {
  background: #c8e6c9;
}

.toolbar-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.toolbar-btn:disabled:hover {
  background: #fff;
  border-color: #e8eaed;
  color: #555;
  transform: none;
}

.file-item {
  display: flex;
  align-items: center;
  padding: 8px 12px;
  border-top: 1px solid #f5f5f5;
  cursor: pointer;
  transition: background 0.2s;
}

.file-item:first-child {
  border-top: none;
}

.file-item:hover {
  background: #f0f7f0;
}

.file-item-icon {
  flex-shrink: 0;
  margin-right: 8px;
  font-size: 16px;
}

/* 文件多选：勾选列与批量操作条 */
.file-item-check {
  flex-shrink: 0;
  width: 24px;
  margin-right: 4px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.file-item-check input[type="checkbox"] {
  cursor: pointer;
  accent-color: #2e7d32;
}

.file-batch-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 12px;
  border-bottom: 1px solid #e8f5e9;
  background: #f0f7f0;
}

.file-batch-info {
  font-size: 12px;
  font-weight: 500;
  color: #2e7d32;
}

.toolbar-btn.danger:hover {
  background: #ffebee;
  border-color: #ef9a9a;
  color: #c62828;
}

.file-item-name {
  flex: 1;
  font-size: 13px;
  color: #333;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  min-width: 0;
}

.file-item-name.is-dir {
  font-weight: 500;
  color: #1565c0;
}

.file-item-size {
  flex-shrink: 0;
  font-size: 11px;
  color: #999;
  margin: 0 8px;
  font-family: 'Consolas', 'Monaco', monospace;
  min-width: 52px;
  text-align: right;
}

/* 文件列表排序表头与修改时间列 */
.file-list-header {
  display: flex;
  align-items: center;
  padding: 6px 12px;
  border-bottom: 1px solid #f0f0f0;
  font-size: 11px;
  color: #999;
  user-select: none;
}

.file-sortable {
  cursor: pointer;
}

.file-sortable:hover {
  color: #1565c0;
}

.file-sortable.sort-active {
  color: #1565c0;
  font-weight: 500;
}

.file-item-time {
  flex-shrink: 0;
  font-size: 11px;
  color: #999;
  font-family: 'Consolas', 'Monaco', monospace;
  min-width: 110px;
  text-align: right;
}

.device-item {
  display: flex !important;
  align-items: center;
  gap: 8px;
}

.device-item-info {
  flex: 1;
  min-width: 0;
  cursor: pointer;
}

/* 断开全部：设备列表末尾的常驻按钮，宽度与设备项一致 */
.disconnect-all-btn {
  display: block;
  width: 100%;
  box-sizing: border-box;
  margin-top: 8px;
  padding: 8px 10px;
  font-size: 13px;
  font-weight: 500;
  font-family: inherit;
  background-color: #f44336;
  color: white;
  border: none;
  border-radius: 4px;
  cursor: pointer;
  transition: background-color 0.2s;
  flex-shrink: 0; /* 列表滚动时按钮不被压缩，始终完整显示在末尾 */
}

.disconnect-all-btn:hover:not(:disabled) {
  background-color: #d32f2f;
}

.disconnect-all-btn:disabled {
  filter: grayscale(1);
  cursor: not-allowed;
}

.disconnect-btn {
  flex-shrink: 0;
  width: 24px;
  height: 24px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: 1px solid transparent;
  border-radius: 4px;
  background: transparent;
  color: #e53935;
  cursor: pointer;
  transition: all 0.2s ease;
}

.disconnect-btn:hover {
  background: #ffebee;
  border-color: #ef9a9a;
}

.file-item-actions {
  display: flex;
  gap: 4px;
  flex-shrink: 0;
  margin-left: 8px;
}

button.file-act-btn {
  width: 26px;
  height: 26px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: 1px solid transparent;
  border-radius: 4px;
  background: transparent;
  cursor: pointer;
  font-size: 13px;
  color: #333 !important;
  transition: all 0.2s ease;
}

button.file-act-btn.export:hover {
  background: #e8f5e9;
  border-color: #a5d6a7;
}

button.file-act-btn.delete:hover {
  background: #ffebee;
  border-color: #ef9a9a;
}

/* ===== 批量操作页面 ===== */
.tab-content {
  height: auto;
  flex: 1;
  min-height: 0;
}

/* 模拟脚本 Tab 需要确定高度的父级链，让 script-page/layout 能撑满剩余空间 */
.tab-content:has(> .script-page) {
  height: 100%;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

/* 批量操作 Tab 同理：需要确定高度，投屏区才能在剩余空间内滚而不撑高整页 */
.tab-content:has(> .batch-page) {
  height: 100%;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.batch-page {
  display: flex;
  flex-direction: column;
  height: 100%;
  /* 底部留白与左右两侧的 10px 保持一致 */
  padding-bottom: 10px;
  gap: 12px;
  /* 整页不滚动：设备区与投屏区在剩余空间内自行分配，投影区内部滚 */
  min-height: 0;
}

.batch-card {
  background: #fff;
  border-radius: 10px;
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.08);
  overflow: hidden;
  display: flex;
  flex-direction: column;
  /* 不写死 height:100%：与下方投屏区共享剩余空间，避免撑高整页 */
  flex: 1 1 auto;
  min-height: 0;
}

.batch-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.batch-header-left {
  display: flex;
  align-items: center;
  gap: 8px;
}

.batch-selected-info {
  font-size: 13px;
  font-weight: 400;
  color: #4caf50;
}

.batch-content {
  display: flex;
  flex-direction: column;
  flex: 1;
  overflow: hidden;
}

/* 上半部分：设备列表 - flex:1 占据剩余空间 */
.batch-device-list {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  border-bottom: 1px solid #f0f0f0;
  min-height: 0;
}

.batch-list-header {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 16px;
  font-size: 13px;
  font-weight: 600;
  color: #666;
  background: #fafafa;
  border-bottom: 1px solid #f0f0f0;
}

.batch-list-header input[type="checkbox"] {
  width: 16px;
  height: 16px;
  cursor: pointer;
}

.batch-list-body {
  flex: 1;
  overflow-y: auto;
}

.batch-device-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 16px;
  font-size: 13px;
  color: #333;
  border-bottom: 1px solid #f5f5f5;
  cursor: pointer;
  transition: background 0.15s;
}

.batch-device-row:hover {
  background: #f5f9ff;
}

.batch-device-row.is-selected {
  background: #e8f5e9;
}

.batch-device-row input[type="checkbox"] {
  width: 16px;
  height: 16px;
  cursor: pointer;
}

.col-device-id {
  flex: 1.5;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
}

.col-model {
  flex: 1.5;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
}

.col-sn {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
  font-family: monospace;
  font-size: 12px;
}

.batch-checkbox-label {
  display: flex;
  align-items: center;
  gap: 6px;
  cursor: pointer;
}

.batch-loading,
.batch-empty {
  padding: 24px;
  text-align: center;
  color: #999;
  font-size: 13px;
}

.model-filter-input {
  width: 90px;
  padding: 3px 8px;
  margin-top: 4px;
  border: 1px solid #d9d9d9;
  border-radius: 4px;
  font-size: 12px;
  outline: none;
  transition: border-color 0.2s;
}

.model-filter-input:focus {
  border-color: #4caf50;
}

/* 下半部分：操作按钮区 + 结果区 - 高度由内容决定，不强制拉伸 */
.batch-action-area {
  flex: 0 0 auto;
  padding: 16px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: flex-start;
  gap: 12px;
  background: #fafafa;
  overflow: hidden;
}

/* 按钮区 - 高度由按钮内容决定，不拉伸 */
.batch-action-grid {
  flex: none;
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 12px;
  width: 100%;
  max-width: 600px;
}

button.batch-action-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 10px 12px;
  font-size: 13px;
  font-weight: 500;
  color: #333;
  background: #fff;
  border: 1px solid #d9d9d9;
  border-radius: 6px;
  cursor: pointer;
  transition: all 0.2s ease;
}

button.batch-action-btn:hover:not(:disabled) {
  background: #e8f5e9;
  border-color: #4caf50;
  color: #2e7d32;
}

button.batch-action-btn:disabled {
  background: #f5f5f5;
  color: #bbb;
  border-color: #eee;
  cursor: not-allowed;
}

.batch-action-placeholder {
  color: #bbb;
  font-size: 14px;
}

/* 批量 Shell 执行结果 - 显示时给合适的高度，不参与外层比例 */
.batch-shell-results {
  flex: none;
  width: 100%;
  max-width: 800px;
  max-height: 260px;
  padding: 12px;
  background: #fff;
  border: 1px solid #e8e8e8;
  border-radius: 6px;
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
}

.shell-results-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-bottom: 8px;
  margin-bottom: 8px;
  border-bottom: 1px solid #f0f0f0;
  font-size: 13px;
  font-weight: 600;
  color: #333;
}

.shell-close-btn {
  background: none;
  border: none;
  font-size: 18px;
  color: #999;
  cursor: pointer;
  padding: 0 4px;
  line-height: 1;
}

.shell-close-btn:hover {
  color: #666;
}

.shell-results-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  flex: 1;
  overflow-y: auto;
  min-height: 0;
  margin-top: 8px;
}

.shell-result-item {
  padding: 8px;
  background: #fafafa;
  border-radius: 4px;
  border-left: 3px solid #d9d9d9;
}

.shell-result-item:nth-child(odd) {
  background: #f5f5f5;
}

.shell-success {
  border-left-color: #4caf50;
}

.shell-failed {
  border-left-color: #f44336;
}

.shell-result-device {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 6px;
  font-size: 12px;
}

.shell-status-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
  border-radius: 50%;
  font-size: 10px;
  font-weight: bold;
  color: #fff;
}

.shell-success .shell-status-icon {
  background: #4caf50;
}

.shell-failed .shell-status-icon {
  background: #f44336;
}

.shell-device-id {
  font-weight: 500;
  color: #333;
  word-break: break-all;
}

/* ===== 批量投屏画面区 ===== */
.batch-remote-area {
  padding: 12px 16px;
  background: #fafafa;
  border-radius: 8px;
  /* 与设备区共享剩余空间；画面高度已按宽高比自适应，不会撑高整页 */
  flex: 1 1 auto;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

.batch-remote-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
  font-size: 13px;
  font-weight: 600;
  color: #333;
  flex: 0 0 auto;
}

.batch-remote-status {
  padding: 2px 10px;
  border-radius: 10px;
  background: #eceff1;
  color: #78909c;
  font-size: 11px;
  font-weight: 500;
}

.batch-remote-status.live {
  background: #e8f5e9;
  color: #2e7d32;
}

.batch-remote-error {
  margin-bottom: 10px;
  padding: 6px 10px;
  border-radius: 6px;
  background: #fff8e1;
  color: #b26a00;
  font-size: 12px;
}

.batch-remote-grid {
  display: grid;
  gap: 10px;
  /* 画面超出可视区时在网格内部滚动，不影响整页布局 */
  overflow-y: auto;
  min-height: 0;
  flex: 1 1 auto;
  /* 行高按可用高度均分（1fr），卡片再被撑满整行，
     这样 tile-body 的 flex:1 有确定高度可依，画面才能完整适配 */
  grid-auto-rows: 1fr;
}

.batch-remote-tile {
  position: relative;
  background: #000;
  border-radius: 8px;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  /* 行高 1fr 下允许收缩，避免内容把行撑破 */
  min-height: 0;
}

.batch-remote-tile.source {
  outline: 2px solid #4caf50;
}

.batch-remote-tile.dead {
  opacity: 0.4;
}

.batch-remote-tile-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 4px 8px;
  background: rgba(255, 255, 255, 0.92);
  font-size: 11px;
}

.batch-remote-tile-name {
  font-weight: 600;
  color: #333;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.batch-remote-tile-diag {
  margin-left: 6px;
  font-weight: 400;
  color: #999;
  font-size: 10px;
}

button.batch-remote-tile-tag {
  flex-shrink: 0;
  padding: 2px 10px;
  border: 1px solid #e0e0e0;
  border-radius: 10px;
  background: #fff;
  color: #555;
  font-size: 10px;
  font-weight: 600;
  cursor: pointer;
}

button.batch-remote-tile-tag:hover:not(:disabled) {
  background: #e8f5e9;
  border-color: #4caf50;
  color: #2e7d32;
}

button.batch-remote-tile-tag.active,
button.batch-remote-tile-tag:disabled.active {
  background: #4caf50;
  border-color: #4caf50;
  color: #fff;
  cursor: default;
}

.batch-remote-tile-tag.active {
  background: #4caf50;
  color: #fff;
}

/* 画面容器：撑满卡片剩余空间（卡片高度由网格行均分，见 .batch-remote-grid）。
   高度完全由布局分配，不再依赖 JS 测高，因此不存在自反馈。 */
.batch-remote-tile-body {
  position: relative;
  flex: 1 1 auto;
  min-height: 0;
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
}

/* canvas 的 width/height 属性是视频原始像素，不能当显示尺寸用。
   这里铺满容器再用 object-fit: contain 等比收进框内：
   竖屏 9:16 与横屏 16:9 都完整显示，不裁切上下或左右。 */
.batch-remote-canvas {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: contain;
  background: #2a3040;
  touch-action: none;
  user-select: none;
  -webkit-user-drag: none;
}

/* 主控画面可操作（十字光标）；从机画面单击可切主控（手型光标） */
.batch-remote-tile.source .batch-remote-canvas {
  cursor: crosshair;
}

.batch-remote-tile:not(.source) .batch-remote-canvas {
  cursor: pointer;
}

.batch-remote-tile-loading {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #888;
  font-size: 12px;
  background: rgba(0, 0, 0, 0.85);
  pointer-events: none;
}

.shell-output-section {
  margin-top: 4px;
}

.shell-output-label {
  font-size: 11px;
  color: #888;
  margin-bottom: 2px;
  font-weight: 500;
}

.shell-output {
  margin: 0;
  padding: 6px 8px;
  background: #fff;
  border: 1px solid #e8e8e8;
  border-radius: 3px;
  font-size: 11px;
  font-family: 'Consolas', 'Monaco', 'Courier New', monospace;
  color: #333;
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 100px;
  overflow-y: auto;
  line-height: 1.4;
}

.shell-error .shell-output {
  background: #fff8f8;
  border-color: #ffd6d6;
}

.shell-error-text {
  color: #c62828;
}

/* ===== 性能监控页面 ===== */
.monitor-error {
  margin-bottom: 12px;
  padding: 8px 12px;
  background: #ffebee;
  border: 1px solid #ffcdd2;
  border-radius: 6px;
  color: #c62828;
  font-size: 13px;
}

.monitor-usage {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.gauge-chart-wide {
  width: 100%;
  height: 190px;
}

/* 性能监控第二行：CPU 频率 | 内存/磁盘 两列布局 */
.monitor-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
  align-items: stretch;
}

.gauge-chart {
  width: 100%;
  height: 160px;
}

.gauge-detail-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}

.gauge-detail {
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding: 10px 12px;
  background: #f8f9fb;
  border: 1px solid #eef1f4;
  border-radius: 8px;
  font-size: 12px;
  color: #666;
}

.detail-title {
  font-size: 13px;
  font-weight: 600;
  color: #333;
  margin-bottom: 2px;
}

.monitor-empty {
  color: #999;
  font-size: 12px;
}

.monitor-live-tag {
  margin-left: auto;
  padding: 2px 8px;
  border-radius: 10px;
  background: #e8f5e9;
  color: #2e7d32;
  font-size: 11px;
  font-weight: 500;
}

/* ===== 视图切换开关（折线图 / TOP进程） ===== */
.rate-view-switch {
  margin-left: auto;
  display: inline-flex;
  border: 1px solid #e0e0e0;
  border-radius: 8px;
  overflow: hidden;
}

.rate-view-switch button {
  padding: 3px 10px;
  border: none;
  background: transparent;
  color: #888;
  font-size: 11px;
  cursor: pointer;
  transition: all 0.2s ease;
}

.rate-view-switch button + button {
  border-left: 1px solid #e0e0e0;
}

.rate-view-switch button.active {
  background: #4caf50;
  color: #fff;
}

/* ===== TOP 进程表格 ===== */
.top-table-wrap {
  max-height: 260px;
  overflow-y: auto;
  border: 1px solid #eef1f4;
  border-radius: 8px;
}

.top-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}

.top-table th,
.top-table td {
  padding: 6px 10px;
  text-align: left;
  white-space: nowrap;
}

.top-table thead th {
  position: sticky;
  top: 0;
  background: #f8f9fb;
  color: #666;
  font-weight: 600;
  border-bottom: 1px solid #eef1f4;
}

.top-table tbody tr {
  border-bottom: 1px solid #f5f6f8;
}

.top-table tbody tr:last-child {
  border-bottom: none;
}

.top-cpu-high {
  color: #f44336;
  font-weight: 600;
}

.top-cmd {
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 260px;
}

.monitor-freq-info {
  margin-left: auto;
  font-size: 12px;
  font-weight: 400;
  color: #888;
}

.rate-summary {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 8px;
  margin-bottom: 12px;
}

.rate-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 8px 10px;
  background: #f8f9fb;
  border: 1px solid #eef1f4;
  border-radius: 8px;
}

.rate-label {
  font-size: 11px;
  color: #999;
}

.rate-value {
  font-size: 15px;
  font-weight: 600;
  color: #333;
}

.rate-value.cpu {
  color: #4caf50;
}

.rate-value.down {
  color: #2196f3;
}

.rate-value.up {
  color: #ff9800;
}

.rate-chart {
  width: 100%;
  height: 220px;
}

.core-chart {
  width: 100%;
  height: 180px;
}

@media (max-width: 720px) {
  .monitor-row {
    grid-template-columns: 1fr;
  }

  .gauge-detail-row {
    grid-template-columns: 1fr;
  }

  .rate-summary {
    grid-template-columns: repeat(2, 1fr);
  }
}

@media (prefers-color-scheme: dark) {
  .gauge-detail,
  .rate-item {
    background: #33363e;
    border-color: #40434a;
    color: #b0b0b0;
  }

  .detail-title,
  .rate-value {
    color: #e0e0e0;
  }

  .monitor-error {
    background: #3a2426;
    border-color: #5c3234;
    color: #ef9a9a;
  }

  .monitor-live-tag {
    background: #2e3d31;
    color: #81c784;
  }

  .monitor-freq-info {
    color: #999;
  }
}

/* ==================== 模拟脚本页 ==================== */
/* 脚本页专属：撑满 tab-content 高度（普通 keys-page 是自然流式布局） */
.script-page {
  height: 100%;
}

.script-layout {
  display: flex;
  gap: 12px;
  /* 撑满 Tab 内容区剩余高度（不用 vh 魔法数字，避免底部空隙），
     min-height 只防极小窗口下被压扁 */
  flex: 1;
  min-height: 380px;
  padding-bottom: 10px;
  box-sizing: border-box;
}

.script-screen-panel {
  flex: 1;
  display: flex;
  flex-direction: column;
  background: #fff;
  border: 1px solid #e8eaed;
  border-radius: 12px;
  overflow: hidden;
  /* 面板高度稳定，内部 canvas 不随内容波动 */
  min-width: 0;
}

.script-screen-header {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 14px;
  border-bottom: 1px solid #e8eaed;
  font-size: 13px;
  font-weight: 600;
  color: #333;
}

.script-ws-status {
  margin-left: auto;
  font-size: 12px;
  color: #999;
}

.script-ws-status.live {
  color: #2e7d32;
}

.script-rec-tag {
  color: #e53935;
  font-size: 12px;
  font-weight: 700;
  animation: capturePulse 1.2s ease-in-out infinite;
}

/* 键盘接管提示：点过画面后出现，提示可直接敲键盘打字 */
.script-kbd-tag {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 2px 8px;
  border-radius: 10px;
  background: #e8f5e9;
  color: #2e7d32;
  font-size: 11px;
  font-weight: 500;
}

.script-kbd-exit {
  padding: 0 4px;
  border: none;
  background: transparent;
  color: #2e7d32;
  font-size: 11px;
  font-family: inherit;
  text-decoration: underline;
  cursor: pointer;
}

/* ===== 画面步骤标记开关 ===== */
.script-switch {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  cursor: pointer;
  user-select: none;
  font-size: 12px;
  font-weight: 500;
  color: #888;
  transition: color 0.25s ease;
}

.script-switch input {
  position: absolute;
  opacity: 0;
  width: 0;
  height: 0;
}

.script-switch-track {
  position: relative;
  display: inline-block;
  width: 32px;
  height: 18px;
  border-radius: 9px;
  background: #d9d9d9;
  transition: background 0.25s ease;
}

.script-switch-track::after {
  content: '';
  position: absolute;
  top: 2px;
  left: 2px;
  width: 14px;
  height: 14px;
  border-radius: 50%;
  background: #fff;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.2);
  transition: transform 0.25s ease;
}

.script-switch.on {
  color: #2e7d32;
}

.script-switch.on .script-switch-track {
  background: #4caf50;
}

.script-switch.on .script-switch-track::after {
  transform: translateX(14px);
}

.script-switch input:focus-visible + .script-switch-track {
  box-shadow: 0 0 0 3px rgba(76, 175, 80, 0.25);
}

.script-screen-body {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #1a1f2e;
  position: relative;
  overflow: hidden;
  /* 锁最小高度，canvas 在剩余空间内等比缩放，不触发布局抖动 */
  min-height: 0;
}

.script-canvas {
  /* 视口内完整可见：宽高都限制在容器内，保持比例 */
  max-width: 100%;
  max-height: 100%;
  width: auto;
  height: auto;
  object-fit: contain;
  cursor: pointer;
}

.script-canvas.recording {
  cursor: crosshair;
  outline: 2px solid #e53935;
  outline-offset: -2px;
}

/* 显示步骤时可拖拽标记 */
.script-canvas.editing-markers {
  cursor: grab;
}

.script-canvas.editing-markers.dragging {
  cursor: grabbing;
}

.script-screen-placeholder {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  color: rgba(255, 255, 255, 0.6);
  font-size: 14px;
}

.script-side-panel {
  width: 300px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
  min-height: 0;
}

/* 页面够宽时：录制功能区与脚本步骤区左右两列 */
@media (min-width: 1600px) {
  .script-side-panel {
    width: 620px;
    flex-direction: row;
    align-items: stretch;
  }

  .script-side-panel .script-section {
    flex: 1;
    min-width: 0;
  }
}

.script-section {
  background: #fff;
  border: 1px solid #e8eaed;
  border-radius: 12px;
  padding: 12px;
}

.script-record-btn {
  width: 100%;
  padding: 10px;
  border: none;
  border-radius: 8px;
  font-size: 14px;
  font-weight: 600;
  font-family: inherit;
  cursor: pointer;
  color: #fff;
  background: #e53935;
  transition: all 0.2s ease;
}

.script-record-btn:hover {
  background: #d32f2f;
}

.script-record-btn.active {
  background: #c62828;
  animation: capturePulse 1.2s ease-in-out infinite;
}

.script-config-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 6px;
  font-size: 12px;
  color: #555;
}

.script-config-row:last-of-type {
  margin-bottom: 0;
}

.script-config-input {
  width: 90px;
  padding: 4px 8px;
  border: 1px solid #dfe3e8;
  border-radius: 6px;
  font-size: 12px;
  font-family: inherit;
  text-align: right;
}

/* 设备动作步骤的文本/下拉配置（宽度更大，左对齐） */
.script-config-text,
.script-config-select {
  flex: 1;
  min-width: 0;
  max-width: 62%;
  padding: 4px 8px;
  border: 1px solid #dfe3e8;
  border-radius: 6px;
  font-size: 12px;
  font-family: inherit;
  background: #fff;
}

/* Element Plus 配置控件：占满行内剩余宽度 */
.ep-config-select,
.ep-config-input {
  flex: 1;
  min-width: 0;
  max-width: 68%;
}

.ep-config-select {
  width: 68%;
}

.ep-config-num {
  width: 110px;
}

/* 添加设备动作步骤按钮组 */
.script-add-steps {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  margin-top: 8px;
}

.script-add-label {
  font-size: 12px;
  color: #888;
  margin-right: 2px;
}

.script-add-btn {
  padding: 4px 9px;
  border: 1px dashed #bdbdbd;
  border-radius: 6px;
  background: #fff;
  font-size: 12px;
  font-family: inherit;
  color: #555;
  cursor: pointer;
  transition: all 0.15s ease;
}

.script-add-btn:hover:not(:disabled) {
  border-color: #4caf50;
  color: #2e7d32;
  background: #f6fbf6;
}

.script-add-btn:disabled {
  opacity: 0.4;
  cursor: default;
}

/* 框选模式进行中 */
.script-add-btn.cropping {
  border-style: solid;
  border-color: #ff9800;
  color: #e65100;
  background: #fff3e0;
}

.script-config-hint {
  margin-top: 6px;
  font-size: 11px;
  color: #999;
}

.script-list-section {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
  /* 列表区不收缩到底部按钮被挤出可视区 */
  overflow: hidden;
}

/* 步骤列表弹性占满剩余空间，底栏（保存/执行/重复）始终贴底 */
.script-list-section .script-actions {
  margin-top: auto;
  padding-top: 10px;
}

.script-list-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 13px;
  font-weight: 600;
  color: #333;
  margin-bottom: 8px;
}

.script-header-actions {
  display: flex;
  gap: 6px;
}

.script-mini-btn {
  padding: 3px 10px;
  border: 1px solid #e8eaed;
  border-radius: 6px;
  background: #fff;
  font-size: 12px;
  font-family: inherit;
  color: #666;
  cursor: pointer;
}

.script-mini-btn:hover:not(:disabled) {
  border-color: #ef9a9a;
  color: #c62828;
}

.script-mini-btn:disabled {
  opacity: 0.4;
  cursor: default;
}

.script-steps-list {
  flex: 1;
  overflow-y: auto;
  min-height: 0;
  /* 步骤多时内部滚动，列表不把面板和底栏撑走 */
  scrollbar-width: thin;
}

.script-steps-list::-webkit-scrollbar {
  width: 6px;
}

.script-steps-list::-webkit-scrollbar-thumb {
  background: #d0d5db;
  border-radius: 3px;
}

.script-empty {
  padding: 24px 8px;
  text-align: center;
  color: #999;
  font-size: 12px;
}

.script-step-item {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  padding: 7px 8px;
  border-radius: 6px;
  font-size: 12px;
  cursor: pointer;
  transition: background 0.12s ease, box-shadow 0.12s ease, opacity 0.12s ease;
}

.script-step-item:hover {
  background: #f5f7fa;
}

.script-step-item.dragging {
  opacity: 0.45;
}

.script-step-item.drag-over {
  background: #e3f2fd;
  box-shadow: inset 0 2px 0 0 #1976d2;
}

.script-step-item.selected {
  background: #e8f5e9;
  box-shadow: inset 0 0 0 1px #a5d6a7;
}

/* 展开的单步执行参数 */
.script-step-config {
  flex-basis: 100%;
  margin-top: 6px;
  padding: 10px;
  background: #f8faf8;
  border: 1px solid #e0e8e0;
  border-radius: 8px;
}

.script-step-idx {
  flex-shrink: 0;
  width: 20px;
  height: 20px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #e8f5e9;
  color: #2e7d32;
  border-radius: 50%;
  font-size: 11px;
  font-weight: 700;
}

.script-step-desc {
  flex: 1;
  color: #333;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.script-step-meta {
  flex-shrink: 0;
  color: #999;
  font-size: 11px;
}

.script-step-del {
  flex-shrink: 0;
  width: 20px;
  height: 20px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: none;
  border-radius: 50%;
  background: transparent;
  color: #bbb;
  font-size: 14px;
  cursor: pointer;
}

.script-step-del:hover {
  background: #ffebee;
  color: #c62828;
}

.script-actions {
  display: flex;
  gap: 8px;
  margin-top: 10px;
}

.script-action-btn {
  flex: 1;
  padding: 8px 0;
  border: none;
  border-radius: 8px;
  background: #4caf50;
  color: #fff;
  font-size: 13px;
  font-weight: 600;
  font-family: inherit;
  cursor: pointer;
  transition: all 0.2s ease;
}

.script-action-btn:hover:not(:disabled) {
  background: #43a047;
}

.script-action-btn:disabled {
  opacity: 0.5;
  cursor: default;
}

/* 执行中的停止按钮 */
.script-stop-btn {
  background: #f44336;
}

.script-stop-btn:hover:not(:disabled) {
  background: #d32f2f;
}

/* 模拟脚本页 暗色模式 */
@media (prefers-color-scheme: dark) {
  .script-step-item:hover {
    background: #33363e;
  }

  .script-step-item.selected {
    background: #2e3d31;
    box-shadow: inset 0 0 0 1px #66bb6a;
  }

  .script-step-desc {
    color: #e0e0e0;
  }

  .script-step-config {
    background: #33363e;
    border-color: #40434a;
  }

  .script-config-text,
  .script-config-select {
    background: #3a3d44;
    border-color: #4a4d55;
    color: #e0e0e0;
  }

  .script-add-btn {
    background: #33363e;
    border-color: #4a4d55;
    color: #bbb;
  }

  .script-add-btn:hover:not(:disabled) {
    border-color: #66bb6a;
    color: #81c784;
    background: #2e3d31;
  }
}

/* ==================== APK 仓库弹窗（应用管理页） ==================== */
.apps-header-btns {
  margin-left: auto;
  display: flex;
  gap: 8px;
}

.apk-repo-mask {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 200;
}

.apk-repo-dialog {
  width: 560px;
  max-width: 92vw;
  max-height: 70vh;
  display: flex;
  flex-direction: column;
  background: #fff;
  border-radius: 12px;
  box-shadow: 0 12px 48px rgba(0, 0, 0, 0.25);
  overflow: hidden;
}

.apk-repo-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  font-size: 15px;
  font-weight: 600;
  color: #333;
  background: #fafafa;
  border-bottom: 1px solid #f0f0f0;
}

.apk-repo-close {
  width: 26px;
  height: 26px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: #888;
  cursor: pointer;
  font-size: 13px;
}

.apk-repo-close:hover {
  background: #ffebee;
  color: #c62828;
}

.apk-repo-body {
  padding: 14px 16px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  overflow: hidden;
}

.apk-repo-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}

.apk-repo-hint {
  font-size: 12px;
  color: #999;
}

.apk-repo-list {
  flex: 1;
  min-height: 120px;
  max-height: 340px;
  overflow-y: auto;
  border: 1px solid #eef1f4;
  border-radius: 8px;
}

.apk-repo-empty {
  padding: 32px;
  text-align: center;
  color: #999;
  font-size: 13px;
}

.apk-repo-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 12px;
  border-bottom: 1px solid #f5f6f8;
  font-size: 13px;
}

.apk-repo-item:last-child {
  border-bottom: none;
}

.apk-repo-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: #333;
}

.apk-repo-size {
  flex-shrink: 0;
  font-size: 12px;
  color: #888;
}

.apk-repo-install {
  flex-shrink: 0;
  padding: 3px 12px;
  font-size: 12px;
  background: #fff;
  color: #2e7d32;
  border: 1px solid #a5d6a7;
  border-radius: 4px;
  cursor: pointer;
}

.apk-repo-install:hover {
  background: #e8f5e9;
}

.apk-repo-del {
  flex-shrink: 0;
  padding: 3px 10px;
  font-size: 12px;
  background: #fff;
  color: #c62828;
  border: 1px solid #ef9a9a;
  border-radius: 4px;
  cursor: pointer;
}

.apk-repo-del:hover {
  background: #ffebee;
}

@media (prefers-color-scheme: dark) {
  .apk-repo-dialog {
    background: #2b2e35;
  }

  .apk-repo-head {
    background: #33363e;
    border-bottom-color: #40434a;
    color: #e0e0e0;
  }

  .apk-repo-list {
    border-color: #40434a;
  }

  .apk-repo-item {
    border-bottom-color: #383b42;
    color: #ddd;
  }

  .apk-repo-name {
    color: #e0e0e0;
  }

  .apk-repo-install,
  .apk-repo-del {
    background: #33363e;
  }

  .apk-repo-install {
    color: #81c784;
    border-color: #4a6b4d;
  }

  .apk-repo-del {
    color: #ef9a9a;
    border-color: #6b4a4a;
  }
}

/* ==================== 模拟串口页 ==================== */
.serial-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 12px;
}

.serial-label {
  font-size: 13px;
  color: #666;
  white-space: nowrap;
}

.serial-select,
.serial-input {
  height: 32px;
  padding: 0 10px;
  border: 1px solid #ddd;
  border-radius: 6px;
  font-size: 13px;
  font-family: inherit;
  background: #fff;
  color: #333;
  transition: border-color 0.25s ease, box-shadow 0.25s ease;
}

.serial-select:focus,
.serial-input:focus {
  outline: none;
  border-color: #4caf50;
  box-shadow: 0 0 0 3px rgba(76, 175, 80, 0.1);
}

.serial-select-sm,
.serial-input-sm {
  width: 90px;
}

.serial-mode-group {
  display: flex;
  gap: 4px;
}

.serial-mode-btn {
  padding: 6px 16px;
  border: 1px solid #e8eaed;
  border-radius: 6px;
  background: #f5f7fa;
  color: #555;
  font-size: 13px;
  font-family: inherit;
  cursor: pointer;
  transition: all 0.2s ease;
}

.serial-mode-btn:hover {
  background: #e8f5e9;
  border-color: #a5d6a7;
  color: #2e7d32;
}

.serial-mode-btn.active {
  background: #4caf50;
  border-color: #4caf50;
  color: #fff;
}

.serial-check {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: #666;
  cursor: pointer;
}

.serial-mini-btn {
  padding: 6px 12px;
  border: 1px solid #e8eaed;
  border-radius: 6px;
  background: #f5f7fa;
  color: #555;
  font-size: 12px;
  font-family: inherit;
  cursor: pointer;
  transition: all 0.2s ease;
}

.serial-mini-btn:hover:not(:disabled) {
  background: #e8f5e9;
  border-color: #a5d6a7;
  color: #2e7d32;
}

.serial-mini-btn:disabled {
  opacity: 0.5;
  cursor: default;
}

.serial-textarea {
  width: 100%;
  box-sizing: border-box;
  min-height: 88px;
  padding: 10px 12px;
  margin-bottom: 12px;
  border: 1px solid #ddd;
  border-radius: 8px;
  font-size: 13px;
  font-family: 'Consolas', 'Monaco', monospace;
  resize: vertical;
  transition: border-color 0.25s ease, box-shadow 0.25s ease;
}

.serial-textarea:focus {
  outline: none;
  border-color: #4caf50;
  box-shadow: 0 0 0 3px rgba(76, 175, 80, 0.1);
}

.serial-actions {
  display: flex;
  gap: 10px;
  margin-bottom: 10px;
}

.serial-stream-btn {
  height: 40px;
  padding: 0 22px;
  background: #2196f3;
  color: #fff;
  border: none;
  border-radius: 8px;
  cursor: pointer;
  font-size: 14px;
  font-weight: 600;
  font-family: inherit;
  transition: all 0.35s cubic-bezier(0.16, 1, 0.3, 1);
  display: inline-flex;
  align-items: center;
  gap: 6px;
  white-space: nowrap;
  box-shadow: 0 4px 14px -4px rgba(33, 150, 243, 0.3);
}

.serial-stream-btn:hover:not(:disabled) {
  background: #1976d2;
  transform: translateY(-2px);
}

.serial-stream-btn.active {
  background: #f44336;
  box-shadow: 0 4px 14px -4px rgba(244, 67, 54, 0.3);
}

.serial-stream-btn:disabled {
  background: #bdbdbd;
  box-shadow: none;
  cursor: not-allowed;
  transform: none;
}

.serial-msg {
  margin-bottom: 10px;
  padding: 8px 12px;
  border-radius: 6px;
  font-size: 13px;
}

.serial-msg.ok {
  background: #e8f5e9;
  color: #2e7d32;
}

.serial-msg.err {
  background: #ffebee;
  color: #c62828;
}

.serial-stream-state {
  margin-bottom: 10px;
  font-size: 12px;
  color: #1976d2;
}

.serial-tool-missing {
  margin-left: auto;
  padding: 2px 8px;
  border-radius: 10px;
  background: #fff3e0;
  color: #ef6c00;
  font-size: 11px;
  font-weight: 500;
}

/* ===== 方案切换 ===== */
.serial-scheme-group {
  display: flex;
  gap: 8px;
  margin-bottom: 14px;
  padding-bottom: 12px;
  border-bottom: 1px solid #f0f0f0;
}

.serial-scheme-btn {
  flex: 1;
  padding: 9px 12px;
  border: 1px solid #e8eaed;
  border-radius: 8px;
  background: #f5f7fa;
  color: #555;
  font-size: 13px;
  font-weight: 500;
  font-family: inherit;
  cursor: pointer;
  transition: all 0.25s ease;
}

.serial-scheme-btn:hover {
  background: #e8f5e9;
  border-color: #a5d6a7;
  color: #2e7d32;
}

.serial-scheme-btn.active {
  background: #4caf50;
  border-color: #4caf50;
  color: #fff;
  font-weight: 600;
}

.serial-input-lg {
  width: 200px;
}

.serial-hint {
  font-size: 12px;
  color: #999;
  line-height: 1.6;
}

.serial-hint-block {
  display: block;
  margin-bottom: 14px;
  padding: 10px 12px;
  background: #fff8e1;
  border: 1px solid #ffe082;
  border-radius: 8px;
  line-height: 1.6;
}

/* ===== pty 状态条 ===== */
.pty-state {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 18px;
  margin-bottom: 14px;
  padding: 10px 14px;
  background: #f8f9fb;
  border: 1px solid #eef1f4;
  border-radius: 8px;
  font-size: 12px;
  color: #666;
}

.pty-state-item b.ok {
  color: #2e7d32;
}

.pty-state-item b.warn {
  color: #ef6c00;
}

.pty-state-item.err {
  color: #c62828;
}

/* ===== 按键序列 ===== */
.serial-key-grid {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 8px;
}

.serial-key-btn {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 4px;
  padding: 10px 6px;
  background: #f5f7fa;
  border: 1px solid #e8eaed;
  border-radius: 8px;
  cursor: pointer;
  font-family: inherit;
  transition: all 0.25s ease;
}

.serial-key-btn:hover:not(:disabled) {
  background: #e8f5e9;
  border-color: #a5d6a7;
  transform: translateY(-1px);
  box-shadow: 0 3px 10px rgba(76, 175, 80, 0.12);
}

.serial-key-btn:active:not(:disabled) {
  transform: translateY(0);
  background: #c8e6c9;
}

.serial-key-btn:disabled {
  opacity: 0.5;
  cursor: default;
}

.serial-key-btn-label {
  font-size: 13px;
  font-weight: 600;
  color: #444;
}

.serial-key-btn-value {
  font-size: 10px;
  color: #999;
}

/* 已输入的按键序列 */
.serial-seq-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  min-height: 34px;
  padding: 8px 10px;
  margin-bottom: 10px;
  background: #f8f9fb;
  border: 1px solid #eef1f4;
  border-radius: 8px;
}

.serial-seq-empty {
  font-size: 12px;
  color: #999;
}

.serial-seq-item {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 3px 6px 3px 10px;
  background: #e8f5e9;
  border: 1px solid #a5d6a7;
  border-radius: 14px;
  font-size: 12px;
  font-weight: 500;
  color: #2e7d32;
}

.serial-seq-item .serial-seq-idx {
  font-size: 10px;
  color: #66bb6a;
  font-weight: 700;
}

.serial-seq-del {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
  border: none;
  border-radius: 50%;
  background: transparent;
  color: #81c784;
  font-size: 13px;
  line-height: 1;
  cursor: pointer;
  transition: all 0.2s ease;
}

.serial-seq-del:hover {
  background: #ffebee;
  color: #c62828;
}

.serial-output {
  margin: 0 0 10px;
  padding: 10px 12px;
  background: #1a1f2e;
  border-radius: 8px;
  color: #c8d3e0;
  font-size: 12px;
  line-height: 1.6;
  font-family: 'Consolas', 'Monaco', monospace;
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 220px;
  overflow-y: auto;
}

@media (prefers-color-scheme: dark) {
  .serial-select,
  .serial-input {
    background: #3a3d45;
    border-color: #555;
    color: #e0e0e0;
  }

  .serial-label,
  .serial-check {
    color: #b0b0b0;
  }

  .serial-mode-btn,
  .serial-mini-btn,
  .serial-scheme-btn {
    background: #3a3d45;
    border-color: #555;
    color: #ccc;
  }

  .serial-mode-btn.active,
  .serial-scheme-btn.active {
    background: #66bb6a;
    border-color: #66bb6a;
    color: #1a1d23;
  }

  .serial-textarea {
    background: #3a3d45;
    border-color: #555;
    color: #e0e0e0;
  }

  .serial-msg.ok {
    background: #2e3d31;
    color: #81c784;
  }

  .serial-msg.err {
    background: #3a2426;
    color: #ef9a9a;
  }

  .pty-state {
    background: #33363e;
    border-color: #40434a;
  }

  .script-switch {
    color: #999;
  }

  .script-switch.on {
    color: #81c784;
  }

  .script-switch-track {
    background: #4a4d55;
  }

  .script-switch.on .script-switch-track {
    background: #66bb6a;
  }

  .serial-key-btn {
    background: #3a3d45;
    border-color: #555;
  }

  .serial-key-btn:hover:not(:disabled) {
    background: #2e3d31;
    border-color: #66bb6a;
  }

  .serial-key-btn-label {
    color: #e0e0e0;
  }

  .serial-seq-list {
    background: #33363e;
    border-color: #40434a;
  }

  .serial-seq-item {
    background: #2e3d31;
    border-color: #66bb6a;
    color: #81c784;
  }

  .serial-seq-del:hover {
    background: #3a2426;
    color: #ef9a9a;
  }
}

</style>
<template>
  <div
    class="app"
    @dragover.prevent="onPageDragOver"
    @dragleave="onApkDragLeave"
    @drop.prevent="onApkDrop($event)"
  >
    <!-- 全页拖入 APK 提示覆盖层（已选设备时显示） -->
    <div v-if="apkDragOver" class="apk-drop-overlay">
      <div class="apk-drop-overlay-box">松开鼠标安装 APK</div>
    </div>
    <button class="back-button" @click="goHome">&lt;</button>
    <main>
      <div class="container">
        <div class="sidebar">
          <div class="device-header">
            <h2>设备管理</h2>
            <button class="refresh-button" @click="fetchDevices">刷新</button>
          </div>
          <div class="device-list-container">
            <div v-if="loading">加载设备中...</div>
            <div v-else-if="devices.length === 0">无设备连接</div>
            <template v-else>
              <div
                v-for="device in devices"
                :key="device.deviceId"
                class="device-item"
                :class="{ active: selectedDevice === device.deviceId }"
              >
                <div class="device-item-info" @click="selectDevice(device.deviceId)">
                  <div class="device-id">{{ device.deviceId }}</div>
                  <div class="device-info" v-if="device.model">{{ device.model }}</div>
                  <div class="device-status" :class="{ 'status-offline': device.status === 'offline' }">
                    {{ device.status === 'device' ? '已连接' : device.status === 'offline' ? '离线' : device.status }}
                  </div>
                </div>
                <span
                  class="probe-dot"
                  :class="probeStateOf(device.deviceId).status"
                  :title="`探针: ${probeStateOf(device.deviceId).message}（点击安装通讯）`"
                  @click.stop="retryProbe(device.deviceId)"
                ></span>
                <button class="disconnect-btn" @click.stop="confirmDisconnect(device.deviceId)" title="断开连接">
                  <svg viewBox="0 0 1024 1024" width="12" height="12" fill="currentColor">
                    <path d="M816.952281 614.064456l-45.118858-45.118857L895.99028 447.98866a223.99433 223.99433 0 0 0-316.791981-316.791981l-124.156857 120.956938-45.118858-45.438849 122.556898-122.236906a287.99271 287.99271 0 0 1 407.029697 407.029697zM288.00567 1023.974081a287.99271 287.99271 0 0 1-203.514849-491.507559l122.556898-122.556898 45.118858 45.118858-122.556898 122.556898A223.99433 223.99433 0 0 0 448.00162 895.977321l122.556898-122.556898 45.118858 45.118858-122.556898 122.556898A285.752767 285.752767 0 0 1 288.00567 1023.974081zM137.257486 182.555379L182.50434 137.276525l255.897523 255.897523-45.246855 45.246854zM585.214147 630.544039l45.246854-45.246854 255.897523 255.897522-45.246855 45.246855z"/>
                  </svg>
                </button>
              </div>
            </template>
            <!-- 断开全部：始终固定在设备列表末尾，宽度与设备项一致 -->
            <button class="disconnect-all-btn" @click="confirmDisconnectAll" :disabled="disconnectAllLoading">
              {{ disconnectAllLoading ? '断开中...' : '断开全部' }}
            </button>
          </div>

          <div class="wireless-connect">
            <h3>无线连接</h3>
            <div class="wireless-input-group">
              <input
                type="text"
                v-model="wirelessIp"
                placeholder="输入设备IP地址（端口可选，默认5555）"
                class="wireless-input"
              >
              <button class="wireless-button" @click="connectWireless">连接</button>
            </div>
            <div v-if="wirelessStatus" class="wireless-status">
              {{ wirelessStatus }}
            </div>
          </div>
        </div>
        <div class="content">
          <div v-if="!selectedDevice">请选择一个设备</div>
          <div v-else style="height: 100%; display: flex; flex-direction: column; overflow: hidden;">
            <div class="tabs">
              <button
                v-for="tab in visibleTabs"
                :key="tab.key"
                class="tab-button"
                :class="{ active: activeTab === tab.key }"
                @click="activeTab = tab.key"
              >
                {{ tab.label }}
              </button>
            </div>
            
            <div class="tab-content">
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
              
              <div v-if="activeTab === 'apps'" class="keys-page">
                <div class="scrcpy-card">
                  <div class="card-header">
                    <span class="card-icon">
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="18" height="18">
                        <rect x="4" y="2" width="16" height="20" rx="2"/>
                        <line x1="8" y1="6" x2="16" y2="6"/>
                        <line x1="8" y1="10" x2="16" y2="10"/>
                        <line x1="8" y1="14" x2="12" y2="14"/>
                      </svg>
                    </span>
                    <span>应用管理</span>
                    <div class="apps-header-btns">
                      <button class="card-action-btn" @click="openApkRepo">🗄️ APK仓库</button>
                      <button class="card-action-btn" @click="installApp">+ 安装</button>
                    </div>
                  </div>
                  <div
                    class="card-body"
                    style="padding: 0;"
                  >
                    <div v-if="appLoading" class="loading-text" style="padding: 24px; text-align: center;">加载应用列表中...</div>
                    <div v-else-if="apps.length === 0" class="loading-text" style="padding: 24px; text-align: center;">无应用</div>
                    <div v-else class="app-list">
                      <div v-for="app in apps" :key="app.packageName" class="app-item">
                        <div class="app-item-info">
                          <div class="app-item-name">{{ app.appName }}</div>
                          <div class="app-item-desc">{{ app.packageName }} · v{{ app.versionName || '?' }}</div>
                        </div>
                        <div class="app-item-actions">
                          <button class="app-act-btn start" @click="startApp(app.packageName)" title="启动">
                            <svg viewBox="0 0 24 24" fill="currentColor" width="12" height="12"><polygon points="5 3 19 12 5 21 5 3"/></svg>
                          </button>
                          <button class="app-act-btn stop" @click="stopApp(app.packageName)" title="停止">
                            <svg viewBox="0 0 24 24" fill="currentColor" width="12" height="12"><rect x="6" y="6" width="12" height="12"/></svg>
                          </button>
                          <button class="app-act-btn extract" @click="extractApk(app.packageName, app.appName)" title="提取APK">
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="12" height="12"><path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
                          </button>
                          <button class="app-act-btn uninstall" @click="uninstallApp(app.packageName)" title="卸载">
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="12" height="12"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2"/></svg>
                          </button>
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
              
              <div v-if="activeTab === 'files'" class="keys-page">
                <div class="scrcpy-card">
                  <div class="card-header">
                    <span class="card-icon">
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="18" height="18">
                        <path d="M22 19a2 2 0 01-2 2H4a2 2 0 01-2-2V5a2 2 0 012-2h5l2 3h9a2 2 0 012 2z"/>
                      </svg>
                    </span>
                    <span class="file-path-text">{{ currentPath }}</span>
                    <button class="card-action-btn" @click="navigateTo('/sdcard')">根目录</button>
                  </div>
                  <div class="card-body" style="padding: 0;">
                    <div class="file-toolbar">
                      <button class="toolbar-btn" @click="fetchFiles(selectedDevice, currentPath)" title="刷新">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14">
                          <polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 11-2.12-9.36L23 10"/>
                        </svg>
                        刷新
                      </button>
                      <button class="toolbar-btn" @click="goBack" title="返回上一级" :disabled="currentPath === '/' || currentPath === '/sdcard'">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14">
                          <polyline points="15 18 9 12 15 6"/>
                        </svg>
                        返回
                      </button>
                      <button class="toolbar-btn" @click="createFolder" title="创建文件夹">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14">
                          <line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>
                        </svg>
                        新建文件夹
                      </button>
                      <button class="toolbar-btn" @click="createFile" title="创建文件">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14">
                          <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="12" y1="12" x2="12" y2="18"/><line x1="9" y1="15" x2="15" y2="15"/>
                        </svg>
                        新建文件
                      </button>
                      <button class="toolbar-btn" @click="importFile" title="导入文件">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14">
                          <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>
                        </svg>
                        导入
                      </button>
                    </div>
                    <div v-if="selectedFileCount > 0" class="file-batch-bar">
                      <span class="file-batch-info">已选 {{ selectedFileCount }} 项</span>
                      <button class="toolbar-btn danger" @click="batchDeleteSelected" title="删除选中的文件/文件夹">
                        <svg viewBox="0 0 1024 1024" width="14" height="14" fill="currentColor">
                          <path d="M202.666667 256h-42.666667a32 32 0 0 1 0-64h704a32 32 0 0 1 0 64H266.666667v565.333333a53.333333 53.333333 0 0 0 53.333333 53.333334h384a53.333333 53.333333 0 0 0 53.333333-53.333334V352a32 32 0 0 1 64 0v469.333333c0 64.8-52.533333 117.333333-117.333333 117.333334H320c-64.8 0-117.333333-52.533333-117.333333-117.333334V256z m224-106.666667a32 32 0 0 1 0-64h170.666666a32 32 0 0 1 0 64H426.666667z m-32 288a32 32 0 0 1 64 0v256a32 32 0 0 1-64 0V437.333333z m170.666666 0a32 32 0 0 1 64 0v256a32 32 0 0 1-64 0V437.333333z"/>
                        </svg>
                        删除选中
                      </button>
                      <button class="toolbar-btn" @click="batchMoveSelected" title="把选中的文件/文件夹移动到指定目录">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14">
                          <polyline points="15 10 20 15 15 20"/><path d="M4 4v7a4 4 0 0 0 4 4h12"/>
                        </svg>
                        移动到…
                      </button>
                      <button class="toolbar-btn" @click="clearFileSelection" title="取消选择">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14">
                          <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
                        </svg>
                        取消选择
                      </button>
                    </div>
                    <div v-if="fileLoading" class="loading-text" style="padding: 24px; text-align: center; border-top: 1px solid #f0f0f0;">加载文件列表中...</div>
                    <div v-else-if="files.length === 0" class="loading-text" style="padding: 24px; text-align: center; border-top: 1px solid #f0f0f0;">无文件</div>
                    <div v-else class="file-list">
                      <div class="file-list-header" @click.prevent>
                        <div class="file-item-check">
                          <input type="checkbox" :checked="fileAllSelected" :indeterminate="fileSomeSelected" @click.stop @change="toggleSelectAllFiles" title="全选/取消全选" />
                        </div>
                        <div class="file-item-icon"></div>
                        <div class="file-item-name file-sortable" :class="{ 'sort-active': fileSortKey === 'name' }" @click.stop="toggleFileSort('name')" title="按名称排序">名称 {{ fileSortKey === 'name' ? (fileSortAsc ? '↑' : '↓') : '' }}</div>
                        <div class="file-item-size file-sortable" :class="{ 'sort-active': fileSortKey === 'size' }" @click.stop="toggleFileSort('size')" title="按大小排序">大小 {{ fileSortKey === 'size' ? (fileSortAsc ? '↑' : '↓') : '' }}</div>
                        <div class="file-item-actions file-sortable" :class="{ 'sort-active': fileSortKey === 'time' }" @click.stop="toggleFileSort('time')" title="按修改时间排序">时间 {{ fileSortKey === 'time' ? (fileSortAsc ? '↑' : '↓') : '' }}</div>
                      </div>
                      <div v-for="file in sortedFiles" :key="file.path" class="file-item" @click="file.isDirectory && navigateTo(file.path)">
                        <div class="file-item-check" @click.stop>
                          <input type="checkbox" :checked="selectedFiles.has(file.path)" @change="toggleFileSelection(file.path)" title="选择该项" />
                        </div>
                        <div class="file-item-icon">{{ file.isDirectory ? '📁' : '📄' }}</div>
                        <div class="file-item-name" :class="{ 'is-dir': file.isDirectory }">{{ file.name }}</div>
                        <div class="file-item-size">
                          <template v-if="!file.isDirectory">{{ formatBytes(Number(file.size)) }}</template>
                          <template v-else-if="file.size">共 {{ file.size }} 项</template>
                        </div>
                        <div class="file-item-time">{{ file.mtime || '' }}</div>
                        <div class="file-item-actions">
                          <button v-if="previewKindOf(file) === 'image'" class="file-act-btn export" @click.stop="openImageViewer(file)" title="查看图片">
                            <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2">
                              <rect x="3" y="3" width="18" height="18" rx="2"/>
                              <circle cx="8.5" cy="8.5" r="1.5"/>
                              <polyline points="21 15 16 10 5 21"/>
                            </svg>
                          </button>
                          <button v-else-if="previewKindOf(file) === 'video'" class="file-act-btn export" @click.stop="openImageViewer(file)" title="播放视频">
                            <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2">
                              <rect x="2" y="4" width="20" height="16" rx="2"/>
                              <polygon points="10 9 15 12 10 15 10 9" fill="currentColor" stroke="none"/>
                            </svg>
                          </button>
                          <button v-else-if="!file.isDirectory" class="file-act-btn export" disabled :title="viewDisabledReason(file)" style="opacity:0.35;">
                            <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2">
                              <rect x="3" y="3" width="18" height="18" rx="2"/>
                              <circle cx="8.5" cy="8.5" r="1.5"/>
                              <polyline points="21 15 16 10 5 21"/>
                            </svg>
                          </button>
                          <button v-if="isEditableTextFile(file)" class="file-act-btn export" @click.stop="openFileEditor(file)" title="编辑">
                            <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2">
                              <path d="M17 3a2.828 2.828 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5L17 3z"/>
                            </svg>
                          </button>
                          <button v-else-if="!file.isDirectory" class="file-act-btn export" disabled :title="editDisabledReason(file)" style="opacity:0.35;">
                            <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2">
                              <path d="M17 3a2.828 2.828 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5L17 3z"/>
                            </svg>
                          </button>
                          <button class="file-act-btn export" @click.stop="renameFile(file)" title="重命名/移动">
                            <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2">
                              <path d="M11 4H4a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-5"/>
                              <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/>
                            </svg>
                          </button>
                          <button class="file-act-btn export" @click.stop="exportFile(file)" title="导出">
                            <svg viewBox="0 0 1024 1024" width="14" height="14" fill="currentColor">
                              <path d="M853.333333 853.333333a42.666667 42.666667 0 0 1 0 85.333334H170.666667a42.666667 42.666667 0 0 1 0-85.333334h682.666666zM512 85.504a42.666667 42.666667 0 0 1 42.666667 42.666667v515.370666l204.373333-204.373333a42.666667 42.666667 0 0 1 63.914667 56.277333l-3.584 4.010667-277.376 277.546667a42.666667 42.666667 0 0 1-56.32 3.584l-4.010667-3.541334-277.12-276.650666a42.666667 42.666667 0 0 1 56.234667-63.957334l4.010666 3.541334L469.333333 644.096V128.170667a42.666667 42.666667 0 0 1 42.666667-42.666667z"/>
                            </svg>
                          </button>
                          <button class="file-act-btn delete" @click.stop="deleteFile(file.path, file.isDirectory)" title="删除">
                            <svg viewBox="0 0 1024 1024" width="14" height="14" fill="currentColor">
                              <path d="M202.666667 256h-42.666667a32 32 0 0 1 0-64h704a32 32 0 0 1 0 64H266.666667v565.333333a53.333333 53.333333 0 0 0 53.333333 53.333334h384a53.333333 53.333333 0 0 0 53.333333-53.333334V352a32 32 0 0 1 64 0v469.333333c0 64.8-52.533333 117.333333-117.333333 117.333334H320c-64.8 0-117.333333-52.533333-117.333333-117.333334V256z m224-106.666667a32 32 0 0 1 0-64h170.666666a32 32 0 0 1 0 64H426.666667z m-32 288a32 32 0 0 1 64 0v256a32 32 0 0 1-64 0V437.333333z m170.666666 0a32 32 0 0 1 64 0v256a32 32 0 0 1-64 0V437.333333z"/>
                            </svg>
                          </button>
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
              
              <div v-if="activeTab === 'keys'" class="keys-page">
                <!-- 卡片1: 投屏控制 -->
                <div class="scrcpy-card">
                  <div class="card-header">
                    <span class="card-icon">
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="18" height="18">
                        <rect x="2" y="3" width="20" height="14" rx="2"/>
                        <line x1="8" y1="21" x2="16" y2="21"/>
                        <line x1="12" y1="17" x2="12" y2="21"/>
                      </svg>
                    </span>
                    <span>投屏控制</span>
                  </div>
                  <div class="card-body">
                    <button class="scrcpy-btn" @click="startScrcpy">
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="22" height="22">
                        <polygon points="5 3 19 12 5 21 5 3" fill="currentColor" stroke="none"/>
                      </svg>
                      启动投屏
                    </button>
                  </div>
                </div>

                <!-- 卡片2: 按键模拟 -->
                <div class="scrcpy-card">
                  <div class="card-header">
                    <span class="card-icon">
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="18" height="18">
                        <rect x="4" y="2" width="16" height="20" rx="2"/>
                        <line x1="8" y1="6" x2="16" y2="6"/>
                        <line x1="12" y1="6" x2="12" y2="2"/>
                      </svg>
                    </span>
                    <span>按键模拟</span>
                  </div>
                  <div class="card-body">
                    <div class="key-grid">
                      <button class="key-btn" @click="simulateKey('back')">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="18" height="18">
                          <polyline points="15 18 9 12 15 6"/>
                        </svg>
                        <span>返回</span>
                      </button>
                      <button class="key-btn" @click="simulateKey('home')">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="18" height="18">
                          <path d="M3 12l9-9 9 9"/><path d="M5 10v8a1 1 0 001 1h12a1 1 0 001-1v-8"/>
                        </svg>
                        <span>主页</span>
                      </button>
                      <button class="key-btn" @click="simulateKey('recent')">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="18" height="18">
                          <rect x="3" y="4" width="18" height="6" rx="1"/>
                          <rect x="3" y="14" width="18" height="6" rx="1"/>
                        </svg>
                        <span>多任务</span>
                      </button>
                      <button class="key-btn" @click="simulateKey('power')">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="18" height="18">
                          <path d="M12 2v10"/><path d="M18.36 6.64a9 9 0 11-12.72 0"/>
                        </svg>
                        <span>锁屏</span>
                      </button>
                    </div>
                    <button class="restart-btn" @click="restartDevice">
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="16" height="16">
                        <polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 11-2.12-9.36L23 10"/>
                      </svg>
                      重启设备
                    </button>
                  </div>
                </div>

                <!-- 卡片3: 音量控制 -->
                <div class="scrcpy-card">
                  <div class="card-header">
                    <span class="card-icon">
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="18" height="18">
                        <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/>
                        <path d="M19.07 4.93a10 10 0 010 14.14M15.54 8.46a5 5 0 010 7.07"/>
                      </svg>
                    </span>
                    <span>音量控制</span>
                  </div>
                  <div class="card-body">
                    <div class="volume-row">
                      <button class="vol-btn" @click="volumeUp">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="20" height="20">
                          <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/>
                          <line x1="23" y1="9" x2="17" y2="15"/><line x1="17" y1="9" x2="23" y2="15"/>
                        </svg>
                        <span>音量+</span>
                      </button>
                      <button class="vol-btn mute-btn" @click="muteVolume">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="20" height="20">
                          <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/>
                          <line x1="23" y1="9" x2="17" y2="15"/><line x1="17" y1="9" x2="23" y2="15"/>
                        </svg>
                        <span>静音</span>
                      </button>
                      <button class="vol-btn" @click="volumeDown">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="20" height="20">
                          <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/>
                          <line x1="22" y1="12" x2="16" y2="12"/>
                        </svg>
                        <span>音量-</span>
                      </button>
                    </div>
                  </div>
                </div>
              </div>

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

              <div v-if="activeTab === 'script'" class="keys-page script-page">
                <div class="script-layout">
                  <!-- 左：屏幕流 -->
                  <div class="script-screen-panel">
                    <div class="script-screen-header">
                      <span>屏幕预览</span>
                      <span class="script-ws-status" :class="{ live: scriptStreaming }">{{ scriptWsStatus }}</span>
                      <span v-if="scriptRecording" class="script-rec-tag">● 录制中</span>
                      <span v-if="scriptKeyboardActive" class="script-kbd-tag" title="点击画面空白处可退出键盘接管">
                        ⌨ 键盘已接管，可直接打字
                        <button class="script-kbd-exit" @click.stop="scriptReleaseKeyboard">退出</button>
                      </span>
                      <label class="script-switch" :class="{ on: scriptShowMarkers }">
                        <input type="checkbox" v-model="scriptShowMarkers" />
                        <span class="script-switch-track"></span>
                        <span class="script-switch-text">显示步骤</span>
                      </label>
                    </div>
                    <div class="script-screen-body">
                      <canvas
                        ref="scriptCanvasRef"
                        class="script-canvas"
                        :class="{
                          recording: scriptRecording,
                          'editing-markers': scriptShowMarkers && !scriptRecording,
                          dragging: !!scriptDragging
                        }"
                        @pointerdown="scriptOnPointerDown"
                        @pointermove="scriptOnPointerMove"
                        @pointerup="scriptOnPointerUp"
                        @pointercancel="scriptOnPointerCancel"
                      ></canvas>
                      <div v-if="!scriptStreaming" class="script-screen-placeholder">
                        {{ selectedDevice ? '正在连接屏幕流...' : '请先选择设备' }}
                      </div>
                    </div>
                  </div>

                  <!-- 右：控制区 -->
                  <div class="script-side-panel">
                    <!-- 上：录制控制 + 添加设备动作步骤 -->
                    <div class="script-section">
                      <button class="script-record-btn" :class="{ active: scriptRecording }" @click="toggleRecording">
                        {{ scriptRecording ? '■ 结束录制' : '● 开始录制' }}
                      </button>
                      <div class="script-add-steps">
                        <span class="script-add-label">添加步骤</span>
                        <button
                          v-for="a in SCRIPT_ADB_STEPS"
                          :key="a.type"
                          class="script-add-btn"
                          :disabled="scriptRunning"
                          @click="addScriptAdbStep(a.type)"
                        >{{ a.icon }} {{ a.label }}</button>
                        <button
                          class="script-add-btn"
                          :class="{ cropping: scriptCropMode }"
                          :disabled="!scriptStreaming || scriptRunning"
                          title="开启后在画面上框住组件，松开保存为模板并添加等待步骤"
                          @click="toggleCropMode"
                        >{{ scriptCropMode ? '✕ 取消框选' : '👁 等待组件(框选)' }}</button>
                      </div>
                    </div>

                    <!-- 下：脚本列表 + 底部按钮 -->
                    <div class="script-section script-list-section">
                      <div class="script-list-header">
                        <span>脚本步骤（{{ scriptSteps.length }}）</span>
                        <div class="script-header-actions">
                          <button
                            class="script-mini-btn"
                            @click="setAllIntervals"
                            :disabled="scriptSteps.length === 0 || scriptRunning"
                            title="把所有步骤的执行间隔统一设置为同一个值"
                          >统一间隔</button>
                          <button
                            class="script-mini-btn"
                            @click="openImportMenu"
                            :disabled="scriptSaving || scriptRunning"
                          >导入</button>
                          <button class="script-mini-btn" @click="clearScriptSteps" :disabled="scriptSteps.length === 0">清空</button>
                        </div>
                      </div>
                      <div class="script-steps-list">
                        <div v-if="scriptSteps.length === 0" class="script-empty">
                          录制模式下在左侧屏幕上操作即可记录步骤
                        </div>
                        <div
                          v-for="(step, idx) in scriptSteps"
                          :key="idx"
                          class="script-step-item"
                          :class="{
                            selected: scriptEditingStep === idx,
                            'drag-over': scriptStepDragOver === idx && scriptStepDragFrom !== idx,
                            dragging: scriptStepDragFrom === idx,
                          }"
                          draggable="true"
                          @click="toggleStepEdit(idx)"
                          @dragstart="onScriptStepDragStart(idx, $event)"
                          @dragover="onScriptStepDragOver(idx, $event)"
                          @dragleave="onScriptStepDragLeave(idx)"
                          @drop="onScriptStepDrop(idx, $event)"
                          @dragend="onScriptStepDragEnd"
                        >
                          <span class="script-step-idx">{{ idx + 1 }}</span>
                          <span class="script-step-desc">{{ scriptStepLabel(step) }}</span>
                          <span class="script-step-meta">{{ step.interval }}ms × {{ step.count }}次</span>
                          <button class="script-step-del" @click.stop="removeScriptStep(idx)">×</button>

                          <!-- 选中后展开该步的执行参数 -->
                          <div v-if="scriptEditingStep === idx" class="script-step-config" @click.stop>
                            <div v-if="step.type === 'input'" class="script-config-row">
                              <span>输入内容</span>
                              <el-input v-model="step.text" class="ep-config-input" />
                            </div>
                            <!-- 等待组件步骤的专属参数 -->
                            <div v-if="step.type === 'waitFor'" class="script-config-row">
                              <span>模板</span>
                              <el-select v-model="step.template" class="ep-config-select" placeholder="选择模板" :loading="scriptTemplateLoading">
                                <el-option v-for="t in scriptTemplateOptions" :key="t.name" :value="t.name" :label="`${t.name}（${t.width}x${t.height}）`" />
                              </el-select>
                            </div>
                            <div v-if="step.type === 'waitFor'" class="script-config-row">
                              <span>匹配阈值</span>
                              <el-input-number v-model="step.threshold" class="ep-config-num" :min="0.5" :max="1" :step="0.05" :precision="2" controls-position="right" />
                            </div>
                            <div v-if="step.type === 'waitFor'" class="script-config-row">
                              <span>超时(秒)</span>
                              <el-input-number v-model="step.timeout" class="ep-config-num" :min="1" :max="600" controls-position="right" />
                            </div>
                            <!-- 设备动作类步骤的专属参数 -->
                            <div v-if="step.type === 'installApk'" class="script-config-row">
                              <span>APK 文件</span>
                              <el-select v-model="step.apkName" class="ep-config-select" placeholder="选择 APK" :loading="scriptApkLoading">
                                <el-option v-for="a in scriptApkOptions" :key="a.name" :value="a.name" :label="a.name" />
                              </el-select>
                            </div>
                            <div v-if="step.type === 'deleteFolder'" class="script-config-row">
                              <span>文件夹路径</span>
                              <el-input v-model="step.path" class="ep-config-input" placeholder="/sdcard/xxx" />
                            </div>
                            <div v-if="step.type === 'startApp' || step.type === 'stopApp' || step.type === 'uninstallApp'" class="script-config-row">
                              <span>应用</span>
                              <el-select
                                v-model="step.packageName"
                                class="ep-config-select"
                                placeholder="选择应用"
                                filterable
                                :loading="scriptAppLoading"
                              >
                                <el-option v-for="p in scriptAppPackages" :key="p" :value="p" :label="scriptAppNames[p] || p" />
                              </el-select>
                            </div>
                            <div v-if="step.type === 'keyevent'" class="script-config-row">
                              <span>按键</span>
                              <el-select
                                :model-value="scriptKeyPresetOf(step)"
                                class="ep-config-select"
                                @change="onScriptKeyPresetChange(step, $event)"
                              >
                                <el-option v-for="k in SCRIPT_COMMON_KEYS" :key="k.code" :value="String(k.code)" :label="`${k.name}（${k.code}）`" />
                                <el-option value="custom" label="自定义键值..." />
                              </el-select>
                            </div>
                            <div v-if="step.type === 'keyevent' && scriptKeyPresetOf(step) === 'custom'" class="script-config-row">
                              <span>键值</span>
                              <el-input-number v-model.number="step.keycode" class="ep-config-num" :min="0" :max="65535" controls-position="right" />
                            </div>
                            <div v-if="step.type === 'volume'" class="script-config-row">
                              <span>音量动作</span>
                              <el-select v-model="step.action" class="ep-config-select">
                                <el-option value="up" label="音量+" />
                                <el-option value="down" label="音量-" />
                                <el-option value="mute" label="静音" />
                              </el-select>
                            </div>
                            <div v-if="step.type === 'volume' && step.action === 'mute'" class="script-config-row">
                              <span>音源</span>
                              <el-select v-model="step.stream" class="ep-config-select">
                                <el-option value="media" label="媒体" />
                                <el-option value="ring" label="铃声" />
                                <el-option value="alarm" label="闹钟" />
                                <el-option value="notification" label="通知" />
                              </el-select>
                            </div>
                            <div class="script-config-row">
                              <span>执行间隔(ms)</span>
                              <el-input-number v-model="step.interval" class="ep-config-num" :min="0" :max="600000" controls-position="right" />
                            </div>
                            <div class="script-config-row">
                              <span>执行次数</span>
                              <el-input-number v-model="step.count" class="ep-config-num" :min="1" :max="99" controls-position="right" />
                            </div>
                            <div v-if="step.type === 'input'" class="script-config-hint">
                              输入到设备当前聚焦的输入框，通常放在一次点击之后
                            </div>
                            <div v-else-if="step.type === 'waitFor'" class="script-config-hint">
                              反复截屏搜索模板，组件出现才继续下一步；超时未出现判失败
                            </div>
                            <div v-else-if="SCRIPT_ADB_STEPS.some(a => a.type === step.type)" class="script-config-hint">
                              设备动作步骤直接走 adb 命令，无需屏幕录制；可放在任意位置
                            </div>
                            <div v-else class="script-config-hint">
                              开启「显示步骤」后可在画面上拖动该步骤的位置
                            </div>
                          </div>
                        </div>
                      </div>
                      <div class="script-actions">
                        <button class="script-action-btn" @click="saveScript" :disabled="scriptSaving">保存</button>
                        <button class="script-action-btn" @click="runCurrentSteps" :disabled="scriptRunning || scriptSaving">执行</button>
                        <button v-if="scriptRunning" class="script-action-btn script-stop-btn" @click="stopScriptRun">停止</button>
                        <button v-if="!scriptRunning" class="script-action-btn" @click="repeatRun" :disabled="scriptSaving">重复</button>
                        <button class="script-action-btn" @click="exportScriptZip" :disabled="scriptExporting || scriptSaving" title="导出为 zip 包（脚本 + 引用的 APK/模板图），拷到其他电脑导入即可用">导出</button>
                      </div>
                    </div>
                  </div>
                </div>
              </div>

              <div v-if="activeTab === 'serial'" class="keys-page">
                <!-- 模拟串口页 -->
                <div class="scrcpy-card">
                  <div class="card-header">
                    <span class="card-icon">
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="18" height="18">
                        <circle cx="12" cy="12" r="3"/>
                        <path d="M19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 010 2.83 2 2 0 01-2.83 0l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 01-2 2 2 2 0 01-2-2v-.09A1.65 1.65 0 008 19.4a1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 01-2.83 0 2 2 0 010-2.83l.06-.06a1.65 1.65 0 00.33-1.82 1.65 1.65 0 00-1.51-1H3a2 2 0 01-2-2 2 2 0 012-2h.09A1.65 1.65 0 004.6 9a1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 010-2.83 2 2 0 012.83 0l.06.06a1.65 1.65 0 001.82.33H9a1.65 1.65 0 001-1.51V3a2 2 0 012-2 2 2 0 012 2v.09a1.65 1.65 0 001 1.51 1.65 1.65 0 001.82-.33l.06-.06a2 2 0 012.83 0 2 2 0 010 2.83l-.06.06a1.65 1.65 0 00-.33 1.82V9a1.65 1.65 0 001.51 1H21a2 2 0 012 2 2 2 0 01-2 2z"/>
                      </svg>
                    </span>
                    <span>模拟串口</span>
                    <span v-if="serialToolDeployed" class="monitor-live-tag">工具已就绪</span>
                    <span v-else class="serial-tool-missing">工具未部署</span>
                  </div>
                  <div class="card-body">
                    <!-- 方案切换 -->
                    <div class="serial-scheme-group">
                      <button
                        class="serial-scheme-btn"
                        :class="{ active: serialScheme === 'loop' }"
                        @click="serialScheme = 'loop'"
                      >串口写入</button>
                      <button
                        class="serial-scheme-btn"
                        :class="{ active: serialScheme === 'pty' }"
                        @click="serialScheme = 'pty'"
                      >键盘模拟</button>
                      <button
                        class="serial-scheme-btn"
                        :class="{ active: serialScheme === 'read' }"
                        @click="serialScheme = 'read'"
                      >串口会话</button>
                    </div>
                    <div class="serial-hint">
                      串口写入：向设备串口发送数据，通过 UART 内部回环让读取该串口的程序收到。
                      键盘模拟：把串口替换为 pty，向实体键盘协议注入按键帧。
                      串口会话：通过探针 0x04 打开设备串口持续读、0x03 取增量、0x05 发送、0x07 关闭，需设备已 root 且串口节点可读写。
                    </div>
                  </div>
                </div>

                <!-- ============ 方案 A：串口写入 ============ -->
                <template v-if="serialScheme === 'loop'">
                <!-- 卡片: 串口选择 -->
                <div class="scrcpy-card">
                  <div class="card-header">
                    <span class="card-icon">
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="18" height="18">
                        <rect x="2" y="3" width="20" height="14" rx="2"/>
                        <line x1="8" y1="21" x2="16" y2="21"/>
                        <line x1="12" y1="17" x2="12" y2="21"/>
                      </svg>
                    </span>
                    <span>串口设备</span>
                  </div>
                  <div class="card-body">
                    <div class="serial-row">
                      <select v-model="serialDevice" class="serial-select" :disabled="serialPortsLoading">
                        <option v-for="p in serialPorts" :key="p.device" :value="p.device">
                          {{ p.device }}{{ p.majorMinor ? ` (${p.majorMinor})` : '' }}
                        </option>
                      </select>
                      <button class="serial-mini-btn" @click="loadSerialPorts" :disabled="serialPortsLoading">刷新</button>
                      <button
                        class="serial-mini-btn"
                        @click="deploySerialTool"
                        :disabled="serialSending"
                      >部署工具</button>
                    </div>

                    <div v-if="serialPortsLoading" class="loading-text">加载串口列表中...</div>
                    <div v-else-if="serialPorts.length === 0" class="loading-text">该设备未发现串口节点</div>
                  </div>
                </div>

                <!-- 卡片: 数据发送 -->
                <div class="scrcpy-card">
                  <div class="card-header">
                    <span class="card-icon">
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="18" height="18">
                        <polyline points="4 17 10 11 4 5"/>
                        <line x1="12" y1="19" x2="20" y2="19"/>
                      </svg>
                    </span>
                    <span>数据发送</span>
                  </div>
                  <div class="card-body">
                    <!-- 载荷配置 -->
                    <div class="serial-row">
                      <label class="serial-label">载荷类型</label>
                      <div class="serial-mode-group">
                        <button
                          class="serial-mode-btn"
                          :class="{ active: serialMode === 'text' }"
                          @click="serialMode = 'text'"
                        >文本</button>
                        <button
                          class="serial-mode-btn"
                          :class="{ active: serialMode === 'hex' }"
                          @click="serialMode = 'hex'"
                        >HEX</button>
                      </div>
                      <label class="serial-label">波特率</label>
                      <input
                        type="text"
                        v-model="serialBaud"
                        class="serial-input serial-input-sm"
                        placeholder="继承"
                      />
                      <label class="serial-label">结束符</label>
                      <select v-model="serialEnd" class="serial-select serial-select-sm">
                        <option value="none">无</option>
                        <option value="cr">CR</option>
                        <option value="lf">LF</option>
                        <option value="crlf">CRLF</option>
                      </select>
                    </div>

                    <div class="serial-row">
                      <label class="serial-check">
                        <input type="checkbox" v-model="serialLoop" />
                        <span>开启 UART 内部回环（让读取该串口的程序收到数据）</span>
                      </label>
                    </div>

                    <textarea
                      v-model="serialPayload"
                      class="serial-textarea"
                      :placeholder="serialMode === 'hex' ? '例：01 03 00 00 或 5503050207' : '例：HELLO-1234，支持 \\r \\n \\t \\\\ \\0 \\xHH'"
                      spellcheck="false"
                    ></textarea>

                    <div class="serial-actions">
                      <button
                        class="check-button"
                        @click="sendSerialData"
                        :disabled="serialSending || !selectedDevice"
                      >{{ serialSending ? '发送中...' : '发送一次' }}</button>
                      <button
                        class="serial-stream-btn"
                        :class="{ active: serialStreamRunning }"
                        @click="serialStream(serialStreamRunning ? 'stop' : 'start')"
                        :disabled="serialStreamBusy || !selectedDevice"
                      >{{ serialStreamRunning ? '停止持续发送' : '开始持续发送' }}</button>
                    </div>

                    <div v-if="serialStreamRunning" class="serial-stream-state">
                      持续发送运行中（pid {{ serialStreamPid }}）
                    </div>
                  </div>
                </div>
                </template>

                <!-- ============ 方案 B：键盘模拟 ============ -->
                <template v-else-if="serialScheme === 'pty'">
                <!-- 卡片: pty 替换 -->
                <div class="scrcpy-card">
                  <div class="card-header">
                    <span class="card-icon">
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="18" height="18">
                        <path d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16"/>
                        <path d="M4 20l4.586-4.586a2 2 0 012.828 0L16 20"/>
                        <path d="M15 7h3a2 2 0 012 2v9a2 2 0 01-2 2h-3"/>
                        <rect x="2" y="10" width="4" height="6" rx="1"/>
                      </svg>
                    </span>
                    <span>键盘模拟 · pty 替换</span>
                  </div>
                  <div class="card-body">
                    <div class="serial-row">
                      <label class="serial-label">目标串口</label>
                      <input
                        type="text"
                        v-model="ptyDevice"
                        class="serial-input serial-input-lg"
                        placeholder="/dev/ttyS7"
                      />
                      <button class="serial-mini-btn" @click="loadPtyStatus" :disabled="ptyBusy">查询状态</button>
                      <button class="serial-mini-btn" @click="deployPtyTools" :disabled="ptyBusy">部署工具</button>
                    </div>

                    <!-- pty 状态 -->
                    <div class="pty-state">
                      <span class="pty-state-item">
                        节点类型：
                        <b :class="ptyNodeType === 'pty' ? 'ok' : (ptyNodeType === 'serial' ? 'warn' : '')">
                          {{ ptyNodeType === 'pty' ? 'pty（已替换）' : (ptyNodeType === 'serial' ? '真实串口' : '未知') }}
                        </b>
                      </span>
                      <span class="pty-state-item">注入进程：{{ ptyRunning ? '运行中 ' + ptyPid : '未运行' }}</span>
                      <span class="pty-state-item">App 持有：{{ ptyAppHolds ? '是' : '否' }}</span>
                      <span v-if="ptyLeftover" class="pty-state-item err">残留挂载：{{ ptyLeftover }}</span>
                    </div>

                    <div class="serial-actions">
                      <button
                        class="serial-stream-btn"
                        :class="{ active: ptyRunning }"
                        @click="controlPty(ptyRunning ? 'stop' : 'start')"
                        :disabled="ptyBusy || !selectedDevice"
                      >{{ ptyBusy ? '处理中...' : (ptyRunning ? '停止 pty 替换' : '启动 pty 替换') }}</button>
                    </div>

                    <div class="serial-hint serial-hint-block">
                      提示：pty 替换对已打开的串口无效，启动时会自动重启目标 App，
                      让它重新打开串口拿到 pty。启动后再点按键注入不会重启 App。
                      停止后设备节点自动恢复为真实串口。
                    </div>
                  </div>
                </div>

                <!-- 卡片: 按键注入 -->
                <div class="scrcpy-card">
                  <div class="card-header">
                    <span class="card-icon">
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="18" height="18">
                        <rect x="2" y="6" width="20" height="12" rx="2"/>
                        <path d="M6 10h.01M10 10h.01M14 10h.01M18 10h.01M8 14h8"/>
                      </svg>
                    </span>
                    <span>按键注入</span>
                  </div>
                  <div class="card-body">
                    <!-- 序列显示 -->
                    <div class="serial-seq-list">
                      <span v-if="keySequence.length === 0" class="serial-seq-empty">
                        点击下方按键即可依次加入序列，可重复点击同一个键
                      </span>
                      <span
                        v-for="(k, idx) in keySequence"
                        :key="idx"
                        class="serial-seq-item"
                      >
                        <span class="serial-seq-idx">{{ idx + 1 }}</span>
                        <span>{{ keyFunctionText(k) }}</span>
                        <button class="serial-seq-del" @click="removeKeyAt(idx)">×</button>
                      </span>
                    </div>

                    <div class="serial-key-grid">
                      <button
                        v-for="k in keyList"
                        :key="k.name"
                        class="serial-key-btn"
                        :disabled="keyInjectBusy || !selectedDevice"
                        @click="appendKey(k.name)"
                        :title="`${k.desc || k.name} · 键值 ${k.value} · 帧 ${buildKeyFrame(k.value)}`"
                      >
                        <span class="serial-key-btn-label">{{ k.label || k.name }}</span>
                        <span class="serial-key-btn-value">{{ k.desc || k.name }}</span>
                      </button>
                    </div>

                    <div class="serial-actions">
                      <button
                        class="check-button"
                        @click="applyKeys"
                        :disabled="keyInjectBusy || !selectedDevice || keySequence.length === 0"
                      >{{ keyInjectBusy ? '注入中...' : '注入序列' }}</button>
                      <button
                        class="serial-mini-btn"
                        @click="clearKeySequence"
                        :disabled="keyInjectBusy || keySequence.length === 0"
                      >清空序列</button>
                    </div>
                  </div>
                </div>
                </template>

                <!-- ============ 方案 C：串口会话 ============ -->
                <template v-else-if="serialScheme === 'read'">
                <!-- 卡片: 串口会话 -->
                <div class="scrcpy-card">
                  <div class="card-header">
                    <span class="card-icon">
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="18" height="18">
                        <polyline points="4 17 10 11 4 5"/>
                        <line x1="12" y1="19" x2="20" y2="19"/>
                      </svg>
                    </span>
                    <span>串口会话</span>
                  </div>
                  <div class="card-body">
                    <div class="serial-row">
                      <label class="serial-label">串口设备</label>
                      <select v-model="serialDevice" class="serial-select" :disabled="serialPortsLoading">
                        <option v-for="p in serialPorts" :key="p.device" :value="p.device">
                          {{ p.device }}{{ p.majorMinor ? ` (${p.majorMinor})` : '' }}
                        </option>
                      </select>
                      <button class="serial-mini-btn" @click="loadSerialPorts" :disabled="serialPortsLoading">刷新</button>
                    </div>

                    <div v-if="serialPortsLoading" class="loading-text">加载串口列表中...</div>
                    <div v-else-if="serialPorts.length === 0" class="loading-text">该设备未发现串口节点</div>

                    <div class="serial-row">
                      <label class="serial-label">波特率</label>
                      <select v-model="serialReadBaud" class="serial-select">
                        <option value="1200">1200</option>
                        <option value="2400">2400</option>
                        <option value="4800">4800</option>
                        <option value="9600">9600</option>
                        <option value="19200">19200</option>
                        <option value="38400">38400</option>
                        <option value="57600">57600</option>
                        <option value="115200">115200</option>
                        <option value="230400">230400</option>
                        <option value="460800">460800</option>
                        <option value="921600">921600</option>
                        <option value="1500000">1500000</option>
                        <option value="3000000">3000000</option>
                      </select>
                      <label class="serial-label">校验位</label>
                      <select v-model="serialReadParity" class="serial-select">
                        <option value="none">None</option>
                        <option value="odd">Odd</option>
                        <option value="even">Even</option>
                      </select>
                      <label class="serial-label">数据位</label>
                      <select v-model="serialReadDataBits" class="serial-select">
                        <option value="5">5</option>
                        <option value="6">6</option>
                        <option value="7">7</option>
                        <option value="8">8</option>
                      </select>
                      <label class="serial-label">停止位</label>
                      <select v-model="serialReadStopBits" class="serial-select">
                        <option value="1">1</option>
                        <option value="2">2</option>
                      </select>
                      <label class="serial-label">显示</label>
                      <select v-model="serialReadView" class="serial-select">
                        <option value="hex">Hex</option>
                        <option value="text">Txt</option>
                      </select>
                    </div>

                    <div class="serial-row">
                      <label class="serial-label">发送格式</label>
                      <select v-model="serialSendMode" class="serial-select">
                        <option value="text">Txt</option>
                        <option value="hex">Hex</option>
                      </select>
                      <label class="serial-label">发送内容</label>
                      <input v-model="serialSendPayload" class="serial-input" placeholder="Txt 原样 / Hex 如 01 0A FF" />
                      <label class="serial-label">次数(0=持续)</label>
                      <input type="number" min="0" max="10000" v-model="serialSendTimes" class="serial-input serial-input-sm" />
                      <label class="serial-label">间隔(ms)</label>
                      <input type="number" min="0" max="60000" v-model="serialSendInterval" class="serial-input serial-input-sm" />
                    </div>

                    <div class="serial-actions">
                      <button
                        class="check-button"
                        @click="toggleSerialRead"
                        :disabled="!selectedDevice"
                      >{{ serialReading ? '关闭串口' : '打开串口并持续读' }}</button>
                      <button
                        class="serial-mini-btn"
                        @click="sendSerialSessionData"
                        :disabled="!selectedDevice || !serialSessionOpened"
                      >发送</button>
                      <button
                        class="serial-mini-btn"
                        @click="stopSerialSessionSend"
                        :disabled="!selectedDevice || !serialSessionOpened"
                      >停止循环发送</button>
                    </div>

                    <div class="serial-hint serial-hint-block">
                      提示：打开串口后设备端后台持续读，页面轮询 0x03 取增量字节，按 Hex/Txt 原样追加到下方「执行日志」（最多保留最近 100 行），不做帧解析。
                      发送支持 Txt/Hex、单次或按次数/间隔循环；循环由设备端后台线程执行，点「停止循环发送」结束。
                      点「关闭串口」会停止读取与循环发送并关闭设备端会话；切换设备、切换方案或离开页面只停止页面轮询。
                    </div>
                  </div>
                </div>
                </template>

                <!-- 卡片: 执行日志 -->
                <div v-if="serialOutput || serialStreamLog || keyInjectOutput || ptyLog || serialMessage" class="scrcpy-card">
                  <div class="card-header">
                    <span class="card-icon">
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="18" height="18">
                        <polyline points="4 17 10 11 4 5"/>
                        <line x1="12" y1="19" x2="20" y2="19"/>
                      </svg>
                    </span>
                    <span>执行日志</span>
                  </div>
                  <div class="card-body">
                    <div v-if="serialMessage" class="serial-msg" :class="serialMessageType">
                      {{ serialMessage }}
                    </div>
                    <pre v-if="serialOutput" class="serial-output">{{ serialOutput }}</pre>
                    <pre v-if="serialStreamLog" class="serial-output">{{ serialStreamLog }}</pre>
                    <pre v-if="keyInjectOutput" class="serial-output">{{ keyInjectOutput }}</pre>
                    <pre v-if="ptyLog" class="serial-output">{{ ptyLog }}</pre>
                  </div>
                </div>
              </div>

              <div v-if="activeTab === 'monitor'" class="keys-page">
                <!-- 第一行: CPU 实时占用率与网络速率 -->
                <div class="scrcpy-card">
                  <div class="card-header">
                    <span class="card-icon">
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="18" height="18">
                        <polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/>
                      </svg>
                    </span>
                    <span>CPU 占用率 / 网络速率</span>
                    <div class="rate-view-switch">
                      <button :class="{ active: rateViewMode === 'chart' }" @click="rateViewMode = 'chart'">折线图</button>
                      <button :class="{ active: rateViewMode === 'top' }" @click="rateViewMode = 'top'">TOP进程</button>
                    </div>
                  </div>
                  <div class="card-body">
                    <template v-if="rateViewMode === 'chart'">
                    <div class="rate-summary">
                      <div class="rate-item">
                        <span class="rate-label">CPU 占用率</span>
                        <span class="rate-value cpu">{{ formatPercent(monitorRate?.cpu?.usagePercent) }}</span>
                      </div>
                      <div class="rate-item">
                        <span class="rate-label">下载速率</span>
                        <span class="rate-value down">{{ formatRate(activeNic?.rxBytesPerSec) }}</span>
                      </div>
                      <div class="rate-item">
                        <span class="rate-label">上传速率</span>
                        <span class="rate-value up">{{ formatRate(activeNic?.txBytesPerSec) }}</span>
                      </div>
                      <div class="rate-item">
                        <span class="rate-label">累计收发</span>
                        <span class="rate-value">
                          ↓{{ formatBytes(activeNic?.rxBytes) }} / ↑{{ formatBytes(activeNic?.txBytes) }}
                        </span>
                      </div>
                    </div>
                    <div ref="rateChartEl" class="rate-chart" v-show="rateViewMode === 'chart'"></div>
                    <div v-if="monitorRate && (!monitorRate.network || monitorRate.network.length === 0)" class="monitor-empty">
                      暂无网络流量
                    </div>
                    </template>
                    <template v-else>
                      <div class="top-table-wrap">
                        <table class="top-table">
                          <thead>
                            <tr>
                              <th>PID</th>
                              <th>用户</th>
                              <th>状态</th>
                              <th>CPU%</th>
                              <th>内存%</th>
                              <th>进程</th>
                            </tr>
                          </thead>
                          <tbody>
                            <tr v-if="topProcesses.length === 0">
                              <td colspan="6" class="monitor-empty">{{ monitorLoading ? '加载中...' : (monitorError || '暂无数据') }}</td>
                            </tr>
                            <tr v-for="p in topProcesses" :key="p.pid">
                              <td>{{ p.pid }}</td>
                              <td>{{ p.user }}</td>
                              <td>{{ p.stat }}</td>
                              <td :class="{ 'top-cpu-high': p.cpuPercent >= 50 }">{{ p.cpuPercent }}%</td>
                              <td>{{ p.memPercent }}%</td>
                              <td class="top-cmd" :title="p.command">{{ p.command }}</td>
                            </tr>
                          </tbody>
                        </table>
                      </div>
                    </template>
                  </div>
                </div>

                <!-- 第二行: 左右两列（CPU 频率 | 内存 / 磁盘） -->
                <div class="monitor-row">
                  <!-- CPU 频率 -->
                  <div class="scrcpy-card" v-if="monitorSnapshot && monitorSnapshot.cpu && monitorSnapshot.cpu.cores.length > 0">
                    <div class="card-header">
                      <span class="card-icon">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="18" height="18">
                          <rect x="4" y="4" width="16" height="16" rx="2"/>
                          <line x1="4" y1="10" x2="20" y2="10"/>
                          <line x1="10" y1="4" x2="10" y2="20"/>
                        </svg>
                      </span>
                      <span>CPU 频率</span>
                      <span class="monitor-freq-info">
                        {{ monitorSnapshot.cpu.coreCount }} 核 · 最高 {{ formatFreq(monitorSnapshot.cpu.maxFreqKhz) }}
                      </span>
                    </div>
                    <div class="card-body">
                      <div ref="coreChartEl" class="core-chart"></div>
                    </div>
                  </div>

                  <!-- 内存 / 磁盘 -->
                  <div class="scrcpy-card">
                    <div class="card-header">
                      <span class="card-icon">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="18" height="18">
                          <rect x="4" y="4" width="16" height="16" rx="2"/>
                          <rect x="9" y="9" width="6" height="6"/>
                          <line x1="9" y1="1" x2="9" y2="4"/><line x1="15" y1="1" x2="15" y2="4"/>
                          <line x1="9" y1="20" x2="9" y2="23"/><line x1="15" y1="20" x2="15" y2="23"/>
                          <line x1="20" y1="9" x2="23" y2="9"/><line x1="20" y1="14" x2="23" y2="14"/>
                          <line x1="1" y1="9" x2="4" y2="9"/><line x1="1" y1="14" x2="4" y2="14"/>
                        </svg>
                      </span>
                      <span>内存 / 磁盘</span>
                      <button class="card-action-btn" @click="refreshMonitor" :disabled="monitorLoading">
                        {{ monitorLoading ? '刷新中...' : '刷新' }}
                      </button>
                    </div>
                    <div class="card-body">
                      <div v-if="monitorError" class="monitor-error">{{ monitorError }}</div>
                      <div v-if="monitorSnapshot" class="monitor-usage">
                        <div ref="snapshotChartEl" class="gauge-chart"></div>
                        <div class="gauge-detail-row">
                          <div class="gauge-detail">
                            <div class="detail-title">内存</div>
                            <template v-if="monitorSnapshot.memory">
                              <span>已用 {{ formatBytes(monitorSnapshot.memory.usedBytes) }}</span>
                              <span>可用 {{ formatBytes(monitorSnapshot.memory.availableBytes) }}</span>
                              <span>总计 {{ formatBytes(monitorSnapshot.memory.totalBytes) }}</span>
                              <span v-if="monitorSnapshot.memory.cachedBytes !== undefined">
                                页缓存 {{ formatBytes(monitorSnapshot.memory.cachedBytes) }}
                              </span>
                            </template>
                            <span v-else class="monitor-empty">内存数据不可用</span>
                          </div>
                          <div class="gauge-detail">
                            <div class="detail-title">磁盘</div>
                            <template v-if="monitorSnapshot.disk">
                              <span>挂载点 {{ monitorSnapshot.disk.mountPoint }}</span>
                              <span>已用 {{ formatBytes(monitorSnapshot.disk.usedBytes) }}</span>
                              <span>可用 {{ formatBytes(monitorSnapshot.disk.availableBytes) }}</span>
                              <span>总计 {{ formatBytes(monitorSnapshot.disk.totalBytes) }}</span>
                            </template>
                            <span v-else class="monitor-empty">该设备无 /data 分区，磁盘数据不可用</span>
                          </div>
                        </div>
                      </div>
                      <div v-else-if="monitorLoading" class="loading-text">加载性能数据中...</div>
                      <div v-else class="loading-text">暂无性能数据</div>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </main>
    
    <!-- 弹窗组件 -->
    <ModalDialog
      :visible="modalVisible"
      :type="modalType"
      :title="modalTitle"
      :message="modalMessage"
      :placeholder="modalPlaceholder"
      :confirm-text="modalConfirmText"
      :variant="modalVariant"
      :progress="modalProgress"
      :list-items="modalListItems"
      :empty-text="modalEmptyText"
      @close="modalVisible = false"
      @confirm="handleModalConfirm"
      @cancel="handleModalCancel"
    />

    <!-- 文本文件编辑器 -->
    <FileEditorDialog
      :visible="editorVisible"
      :file-name="editorFileName"
      :initial-content="editorInitialContent"
      :loading="editorLoading"
      :load-error="editorLoadError"
      :saving="editorSaving"
      @save="handleEditorSave"
      @close="handleEditorClose"
    />

    <!-- 图片/视频查看器 -->
    <FileImageViewer
      :visible="imageViewerVisible"
      :file-name="imageViewerName"
      :src="imageViewerSrc"
      :loading="imageViewerLoading"
      :load-error="imageViewerError"
      :file-size="imageViewerSize"
      :kind="imageViewerKind"
      v-model:index="imageViewerIndex"
      v-model:total="imageViewerTotal"
      @nav="handleImageViewerNav"
      @close="handleImageViewerClose"
    />

    <!-- APK 仓库弹窗（应用管理页） -->
    <div v-if="apkRepoVisible" class="apk-repo-mask" @click.self="apkRepoVisible = false">
      <div class="apk-repo-dialog">
        <div class="apk-repo-head">
          <span>APK 仓库</span>
          <button class="apk-repo-close" @click="apkRepoVisible = false">✕</button>
        </div>
        <div class="apk-repo-body">
          <div class="apk-repo-actions">
            <button class="card-action-btn" :disabled="apkRepoUploading" @click="pickRepoApk">
              {{ apkRepoUploading ? '上传中...' : '⬆ 上传 APK' }}
            </button>
            <span class="apk-repo-hint">文件存放于服务端 data\apk\ 目录{{ selectedDevice ? `，安装目标：${selectedDevice}` : '，安装前请先选择设备' }}</span>
          </div>
          <div class="apk-repo-list">
            <div v-if="apkRepoLoading" class="apk-repo-empty">加载中...</div>
            <div v-else-if="apkRepoOptions.length === 0" class="apk-repo-empty">仓库为空，先上传 APK</div>
            <div v-for="a in apkRepoOptions" :key="a.name" class="apk-repo-item">
              <span class="apk-repo-name" :title="a.name">📦 {{ a.name }}</span>
              <span class="apk-repo-size">{{ formatRepoSize(a.size) }}</span>
              <button class="apk-repo-install" @click="installFromRepo(a.name)">安装</button>
              <button class="apk-repo-del" @click="removeRepoApk(a.name)">删除</button>
            </div>
          </div>
        </div>
      </div>
      <input ref="apkRepoInputEl" type="file" accept=".apk" style="display:none" @change="onRepoApkChosen">
    </div>
  </div>
</template>