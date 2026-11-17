<script lang="ts" setup>
import axios from 'axios'
import { onMounted, onUnmounted, onBeforeUnmount, ref, computed, nextTick, watch, provide } from 'vue';
import ModalDialog from '../components/ModalDialog.vue';
import FileEditorDialog from '../components/FileEditorDialog.vue';
import FileImageViewer from '../components/FileImageViewer.vue';
import SqliteDbViewer from '../components/SqliteDbViewer.vue';
import * as echarts from 'echarts/core';
import { LineChart, BarChart, GaugeChart } from 'echarts/charts';
import { GridComponent, TooltipComponent, LegendComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import { useAppConfig } from '../composables/useAppConfig';
import TabInfo from './utils/TabInfo.vue';
import TabApps from './utils/TabApps.vue';
import TabFiles from './utils/TabFiles.vue';
import TabKeys from './utils/TabKeys.vue';
import TabBatch from './utils/TabBatch.vue';
import TabScript from './utils/TabScript.vue';
import TabSerial from './utils/TabSerial.vue';
import TabMonitor from './utils/TabMonitor.vue';
import TabProbe from './utils/TabProbe.vue';
import { useProbeSettings } from '../composables/useProbeSettings';

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
  { key: 'monitor', label: '性能监控' },
  { key: 'probe',   label: '探针工具' }
];

// 按配置过滤后的 Tab（未配置的默认显示）
const visibleTabs = computed(() =>
  TAB_ITEMS.filter(tab => isFeatureOn(`utilsTabs.${tab.key}`))
);

const activeTab = ref('info');

// 当前 Tab 被关掉（含初始化与配置加载后）时自动跳到第一个可见 Tab，避免内容区空白
watch(visibleTabs, (tabs) => {
  if (tabs.length && !tabs.some(t => t.key === activeTab.value)) {
    activeTab.value = tabs[0].key;
  }
}, { immediate: true });

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
// SQLite 数据库查看：当前正在查看的设备文件（null = 文件列表模式）
const dbViewerFile = ref<{ path: string; name: string } | null>(null)
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
const scriptScreenBodyRef = ref<HTMLElement | null>(null)
const scriptLayoutRef = ref<HTMLElement | null>(null)
const scriptPreviewWidth = ref(0)
const scriptSideTwoCol = ref(false)
let scriptPreviewObserver: ResizeObserver | null = null
const scriptStreaming = ref(false)
const scriptWsStatus = ref('未连接')
const scriptRecording = ref(false)
// 画面校验：录制中开启后，每次在画面上的操作都追加一个等待组件步骤
const scriptVerify = ref(false)
const SCRIPT_VERIFY_THRESHOLD = 0.6
// 裁块放大到 300：点击点常落在纯色区域，太小容易因低纹理导致匹配分数塌掉
const SCRIPT_VERIFY_CROP = 300
let scriptVerifySeq = 0
let scriptVerifyPending: string | null = null
let scriptVerifyQueue: Promise<void> = Promise.resolve()
// 录制结束（含屏幕流断开）时关掉校验，避免下次录制沿用上次的开关状态
watch(scriptRecording, (on) => {
  if (on) return
  scriptVerify.value = false
  scriptVerifyPending = null
})
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
  kind?: string;
  negate?: boolean;
  onTrue?: { action: string; target?: number };
  onFalse?: { action: string; target?: number };
  command?: string;
  expected?: string;
  targetTime?: string;
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

// 右侧留白够放两列时，控制区改成左右两列并占满留白；不足则保持一列 300px。
// 620px 是两列下限：每列不窄于现在一列时的 300px（含 10px 间距），沿用原 1600px 媒体查询的取值。
const syncScriptSideLayout = () => {
  const layout = scriptLayoutRef.value
  if (!layout || layout.clientWidth <= 0 || scriptPreviewWidth.value <= 0) return
  // -12 对应 .script-layout 的 gap；预览面板宽度含左右各 1px 边框
  const free = layout.clientWidth - scriptPreviewWidth.value - 12
  const twoCol = free >= 620
  if (scriptSideTwoCol.value !== twoCol) scriptSideTwoCol.value = twoCol
}

// 竖屏时画面被高度限制而变窄，面板若还占满剩余宽度，左右就会空出大片深色底（黑框）。
// 按「画面区高度 × 画面宽高比」给面板定宽，让它刚好包住画面；+2 补面板左右各 1px 边框。
const syncScriptPreviewWidth = () => {
  const body = scriptScreenBodyRef.value
  if (!body || body.clientHeight <= 0 || scriptMeta.height <= 0) return
  const w = Math.ceil(body.clientHeight * scriptMeta.width / scriptMeta.height) + 2
  if (scriptPreviewWidth.value !== w) scriptPreviewWidth.value = w
  syncScriptSideLayout()
}

// 画面尺寸变化（切设备 / 横竖屏切换）时同步刷新面板宽度；逐帧调用靠 early return 避免无谓重排
const setScriptMeta = (width: number, height: number) => {
  if (scriptMeta.width === width && scriptMeta.height === height) return
  scriptMeta = { width, height }
  syncScriptPreviewWidth()
}

// 面板宽度随画面区尺寸变化；脚本页用 v-if 挂载，切换 Tab 时要重新挂观察器
watch(activeTab, async (tab) => {
  scriptPreviewObserver?.disconnect()
  scriptPreviewObserver = null
  if (tab !== 'script') return
  await nextTick()
  syncScriptPreviewWidth()
  if (scriptScreenBodyRef.value) {
    scriptPreviewObserver = new ResizeObserver(syncScriptPreviewWidth)
    scriptPreviewObserver.observe(scriptScreenBodyRef.value)
    // 窗口只变宽不变高时画面区尺寸不变，需额外观察整行布局才能刷新两列状态
    if (scriptLayoutRef.value) scriptPreviewObserver.observe(scriptLayoutRef.value)
  }
}, { immediate: true })

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
  if (step.type === 'tts') return `语音「${step.text ?? ''}」`
  if (step.type === 'waitFor') return `等待组件 ${step.template || '（未选择模板）'}（阈值${step.threshold ?? 0.85}）`
  if (step.type === 'condition') {
    const prefix = step.negate ? '判断非' : '判断'
    if (step.kind === 'appInstalled') return `${prefix} 应用已安装 ${step.packageName || '（未填包名）'}`
    if (step.kind === 'template') return `${prefix} 组件出现 ${step.template || '（未选模板）'}`
    if (step.kind === 'shell') return `${prefix} Shell 输出含「${step.expected || '（未填）'}」`
    if (step.kind === 'time') return `${prefix} 时刻到 ${step.targetTime || '（未填）'}`
    return `${prefix} 条件（未设置）`
  }
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

// 设备动作类步骤（走 adb 或探针帧，不依赖画面录制）
const SCRIPT_ADB_STEPS: Array<{ type: string; label: string; icon: string }> = [
  { type: 'installApk', label: '安装APK', icon: '📦' },
  { type: 'deleteFolder', label: '删除文件夹', icon: '🗑️' },
  { type: 'startApp', label: '启动应用', icon: '🚀' },
  { type: 'stopApp', label: '停止应用', icon: '⏹️' },
  { type: 'uninstallApp', label: '卸载应用', icon: '🧹' },
  { type: 'keyevent', label: '模拟按键', icon: '🔘' },
  { type: 'volume', label: '音量控制', icon: '🔊' },
  { type: 'reboot', label: '重启设备', icon: '🔄' },
  { type: 'tts', label: 'TTS语音', icon: '🗣️' },
]

// 手势类步骤（手动添加：坐标默认屏幕中心，开启「显示步骤」后在画面上拖动标记调整）
const SCRIPT_GESTURE_STEPS: Array<{ type: string; label: string; icon: string }> = [
  { type: 'tap', label: '点击', icon: '👆' },
  { type: 'longpress', label: '长按', icon: '✊' },
  { type: 'swipe', label: '滑动', icon: '↔️' },
  { type: 'input', label: '输入文本', icon: '⌨️' },
]

// 步骤文本变量：保存的是占位符，后端执行时才展开，仅对「输入文字」「TTS 朗读文本」生效
const SCRIPT_VARIABLE_HELP = `输入框支持的关键字：
{执行次数} → 当前第几次执行
{执行轮数} → 当前第几轮
{n位随机数} → n 位随机数字（n 取 1~18，如 {6位随机数}）`

// 判断步骤：四种条件与分支动作
const SCRIPT_CONDITION_KINDS: Array<{ kind: string; label: string }> = [
  { kind: 'appInstalled', label: '应用已安装' },
  { kind: 'template', label: '组件出现' },
  { kind: 'shell', label: 'Shell 输出包含' },
  { kind: 'time', label: '设备时刻已到' },
]
const SCRIPT_BRANCH_ACTIONS: Array<{ action: string; label: string }> = [
  { action: 'continue', label: '继续下一步' },
  { action: 'goto', label: '跳到第 N 步' },
  { action: 'stop', label: '终止脚本' },
  { action: 'fail', label: '判定失败' },
]

const scriptConditionDefaults = (kind: string): Record<string, any> => {
  switch (kind) {
    case 'appInstalled': return { packageName: '' }
    case 'template': return { template: '', threshold: 0.85 }
    case 'shell': return { command: '', expected: '' }
    case 'time': return { targetTime: '09:00', timeout: 3600 }
    default: return {}
  }
}

// 切换判断类型时清掉上一种类型的参数，避免脏字段落盘
const onScriptConditionKindChange = (step: any, kind: string) => {
  for (const field of ['packageName', 'template', 'threshold', 'command', 'expected', 'targetTime', 'timeout']) {
    delete step[field]
  }
  Object.assign(step, scriptConditionDefaults(kind))
  if (kind === 'template') fetchScriptTemplates()
}

// 只允许跳到本步骤之后的步骤（target 是 1-based 步骤序号）
const scriptGotoTargets = (idx: number): number[] => {
  const targets: number[] = []
  for (let n = idx + 2; n <= scriptSteps.value.length; n++) targets.push(n)
  return targets
}

const onScriptBranchActionChange = (branch: any, idx: number) => {
  if (branch.action === 'goto') branch.target = idx + 2
  else delete branch.target
}

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

// 等待组件步骤展开时，预览当前已选中的模板图
const scriptTemplatePreview = ref('')
let scriptTemplatePreviewSeq = 0

const loadScriptTemplatePreview = async (name: string) => {
  const seq = ++scriptTemplatePreviewSeq
  scriptTemplatePreview.value = ''
  if (!name) return
  try {
    const res = await axios.post(`${API_BASE.value}/api/device/script/get-template`, { name })
    // 只让最后一次请求写入，避免模板快速切换时旧响应覆盖新图
    if (seq === scriptTemplatePreviewSeq) {
      scriptTemplatePreview.value = res.data.code === 200 ? (res.data.data || '') : ''
    }
  } catch {
    if (seq === scriptTemplatePreviewSeq) scriptTemplatePreview.value = ''
  }
}

const onScriptTemplatePreviewError = (e: Event) => {
  const img = e.target as HTMLImageElement
  if (img) img.style.visibility = 'hidden'
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
    case 'tts': return { text: '' }
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

// 等待组件步骤展开时加载模板预览（immediate watch 须在 scriptEditingStep 声明之后，否则 TDZ 报错）
watch(
  () => {
    const idx = scriptEditingStep.value
    if (idx === null || scriptSteps.value[idx]?.type !== 'waitFor') return ''
    return scriptSteps.value[idx]?.template || ''
  },
  loadScriptTemplatePreview,
  { immediate: true },
)

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
    if (step.type === 'input' || step.type === 'condition' || SCRIPT_ADB_STEPS.some(a => a.type === step.type)) return
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
    if (step.type === 'input' || step.type === 'condition' || SCRIPT_ADB_STEPS.some(a => a.type === step.type)) continue
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
// 判断步骤「组件出现」现截图时，保存后回填该步骤；空则沿用「等待组件(框选)」新增步骤的旧行为
const scriptCropTargetStep = ref<any>(null)

const toggleCropMode = () => {
  scriptCropMode.value = !scriptCropMode.value
  scriptCropRegion.value = null
  scriptCropTargetStep.value = null
  if (scriptCropMode.value) {
    showNotification('框选模式：在画面上拖出组件所在区域，松开保存为模板', 'info')
  }
  scriptRedrawMarkers()
}

// 判断步骤「组件出现」现截图：沿用框选交互，但保存后只回填该步骤的模板，不新增等待步骤
const startCropForCondition = (step: any) => {
  scriptCropTargetStep.value = step
  scriptCropRegion.value = null
  scriptCropMode.value = true
  showNotification('框选模式：在画面上拖出组件所在区域，松开保存为模板', 'info')
  scriptRedrawMarkers()
}

// 松开鼠标：把框选区域从缓存帧裁出，上传后端保存为模板
const finishCropRegion = async () => {
  const region = scriptCropRegion.value
  scriptCropRegion.value = null
  scriptCropMode.value = false
  const targetStep = scriptCropTargetStep.value
  scriptCropTargetStep.value = null
  if (!region || !scriptFrameCanvas) return

  const left = Math.min(region.x1, region.x2)
  const top = Math.min(region.y1, region.y2)
  const w = Math.abs(region.x2 - region.x1)
  const h = Math.abs(region.y2 - region.y1)
  // 太小基本是误触：留在框选模式里让用户重框，不退出
  if (w < 12 || h < 12) {
    scriptCropMode.value = true
    scriptCropTargetStep.value = targetStep
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
          if (targetStep) {
            // 判断步骤「现截图」：只回填该步骤的模板，不新增等待步骤；步骤已删除则只保存模板
            if (scriptSteps.value.includes(targetStep)) targetStep.template = d.name
          } else {
            // 模板就绪后立即加一个等待组件步骤引用它
            addScriptStep({
              type: 'waitFor',
              template: d.name,
              threshold: 0.85,
              timeout: 15,
              interval: 500,
              count: 1,
            })
          }
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

// ---------- 画面校验（录制中每次操作自动截图创建等待组件） ----------

// 按下时把点击点周围的画面裁下来暂存：等手势步骤生成后再上传，保证用的是「点击时的画面」
const captureVerifyFrame = (p: { x: number; y: number }) => {
  scriptVerifyPending = null
  const frame = scriptFrameCanvas
  if (!frame || !frame.width || !frame.height || !scriptRecording.value || !scriptVerify.value) return
  const w = Math.min(SCRIPT_VERIFY_CROP, frame.width)
  const h = Math.min(SCRIPT_VERIFY_CROP, frame.height)
  const left = Math.max(0, Math.min(frame.width - w, Math.round(p.x - w / 2)))
  const top = Math.max(0, Math.min(frame.height - h, Math.round(p.y - h / 2)))
  const crop = document.createElement('canvas')
  crop.width = w
  crop.height = h
  crop.getContext('2d')!.drawImage(frame, left, top, w, h, 0, 0, w, h)
  scriptVerifyPending = crop.toDataURL('image/png')
}

// 上传模板并把等待组件插到对应手势步骤前面（先校验画面再点击）；串行执行，保证快速连点时顺序不乱
const flushScriptVerify = (gesture: any) => {
  const image = scriptVerifyPending
  scriptVerifyPending = null
  if (!image) return
  const name = `verify_${Date.now()}_${++scriptVerifySeq}`
  scriptVerifyQueue = scriptVerifyQueue.then(async () => {
    try {
      const response = await axios.post(`${API_BASE.value}/api/device/script/save-template`, { name, image })
      if (response.data.code === 200) {
        const d = response.data.data
        // 手势步骤若已被删除就不补孤儿步骤；否则插在它前面，保证「等待组件 → 手势」紧邻
        const i = scriptSteps.value.indexOf(gesture)
        if (i < 0) return
        const step = {
          type: 'waitFor',
          template: d.name,
          threshold: SCRIPT_VERIFY_THRESHOLD,
          timeout: 15,
          interval: 500,
          count: 1,
        }
        scriptSteps.value.splice(i, 0, step)
        showNotification(`已记录画面校验：${scriptStepLabel(step)}`, 'info')
        fetchScriptTemplates()
      } else {
        showNotification('画面校验模板保存失败: ' + (response.data.message || '未知错误'), 'error')
      }
    } catch (error: any) {
      showNotification('画面校验模板保存失败: ' + error.message, 'error')
    }
  }).catch(() => {})
}

// 手势步骤照常记录，随后在它前面补一个画面校验的等待组件步骤
const addRecordedStep = (step: any) => {
  addScriptStep(step)
  flushScriptVerify(scriptSteps.value[scriptSteps.value.length - 1])
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
        setScriptMeta(frame.displayWidth, frame.displayHeight)
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
        setScriptMeta(msg.devices[0].width, msg.devices[0].height)
        scriptStreaming.value = true
        scriptWsStatus.value = '实时'
      } else if (msg.type === 'meta') {
        setScriptMeta(msg.width, msg.height)
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
  // 丢弃旧画面缓存，避免下个设备首帧未到时截到上一个设备的画面
  scriptFrameCanvas = null
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
  // 画面校验：先存下点击瞬间的画面，再注入触摸（避免新帧到达后截到操作后的画面）
  captureVerifyFrame(p)
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
      addRecordedStep({
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
    addRecordedStep({
      type: 'swipe',
      x1: scriptPressStart.x, y1: scriptPressStart.y,
      x2: p.x, y2: p.y,
      ...scriptOpConfig.value.swipe,
    })
  } else if (elapsed < SCRIPT_LONGPRESS_MS) {
    // 快速点按 → 点击步骤
    addRecordedStep({
      type: 'tap',
      x1: scriptPressStart.x, y1: scriptPressStart.y,
      ...scriptOpConfig.value.tap,
    })
  }
  // elapsed >= 600ms 且未移动的长按已在 timer 里记录
}

// 指针被中断（取消/离开窗口）：静默结束拖拽；框选中丢弃当前框但保留模式
const scriptOnPointerCancel = () => {
  // 手势被中断，丢弃这次按下的截图，避免下一步误用
  scriptVerifyPending = null
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

// 手动添加手势步骤（点击/长按/滑动/输入文本）：字段与录制生成的步骤一致
const addScriptGestureStep = (type: string) => {
  const cx = Math.round(scriptMeta.width / 2)
  const cy = Math.round(scriptMeta.height / 2)
  const base = { interval: 500, count: 1 }
  const step =
    type === 'tap' ? { type, x1: cx, y1: cy, ...base }
    : type === 'longpress' ? { type, x1: cx, y1: cy, duration: 1000, ...base }
    : type === 'swipe' ? { type, x1: cx, y1: cy, x2: cx, y2: Math.min(scriptMeta.height - 1, cy + 300), ...base }
    : { type, x1: 0, y1: 0, text: '', ...base }
  addScriptStep(step)
}

// 等待组件（选图）：直接新增等待步骤并展开参数，模板从已有模板图列表手动选择
const addScriptWaitStep = () => {
  fetchScriptTemplates()
  addScriptStep({
    type: 'waitFor',
    template: '',
    threshold: 0.85,
    timeout: 15,
    interval: 500,
    count: 1,
  })
  scriptEditingStep.value = scriptSteps.value.length - 1
}

// 步骤按类别分组显示；序号仍是全局执行顺序，分组只影响展示
const SCRIPT_STEP_GROUPS: Array<{ key: string; label: string; types: string[] }> = [
  { key: 'gesture', label: '手势操作', types: ['tap', 'longpress', 'swipe', 'input'] },
  { key: 'wait', label: '等待与判断', types: ['waitFor', 'condition'] },
  { key: 'device', label: '设备动作', types: SCRIPT_ADB_STEPS.map(a => a.type) },
]
const scriptStepGroups = computed(() => {
  const groups = SCRIPT_STEP_GROUPS.map(g => ({
    ...g,
    items: scriptSteps.value
      .map((step, idx) => ({ step, idx }))
      .filter(it => g.types.includes(it.step.type as string)),
  })).filter(g => g.items.length > 0)
  // 未知类型步骤（如导入脚本带新类型）不落任何组会从界面消失，兜底放进「其他」
  const known = new Set(SCRIPT_STEP_GROUPS.flatMap(g => g.types))
  const others = scriptSteps.value
    .map((step, idx) => ({ step, idx }))
    .filter(it => !known.has(it.step.type as string))
  if (others.length > 0) groups.push({ key: 'other', label: '其他', types: [], items: others })
  return groups
})

// 添加判断步骤（单次求值 + 真/假分支）
const addScriptConditionStep = () => {
  addScriptStep({
    type: 'condition',
    kind: 'appInstalled',
    ...scriptConditionDefaults('appInstalled'),
    negate: false,
    onTrue: { action: 'continue' },
    onFalse: { action: 'continue' },
    interval: 1000,
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
      scriptExporting.value = true
      try {
        const exported = await axios.post(`${API_BASE.value}/api/device/script/export-zip`, { name })
        if (exported.data.code === 200) {
          const d = exported.data.data
          const parts = [`脚本「${d.path}」导出成功`, `大小 ${formatBytes(d.size)}`]
          if (d.apkNames?.length) parts.push(`含 APK：${d.apkNames.join('、')}`)
          if (d.templateNames?.length) parts.push(`含模板图：${d.templateNames.join('、')}`)
          showNotification(parts.join('\n'), 'success')
        } else {
          showNotification('导出失败: ' + (exported.data.message || '未知错误'), 'error')
        }
      } catch (error: any) {
        showNotification('导出失败: ' + (error?.response?.data?.message || error.message), 'error')
      } finally {
        scriptExporting.value = false
      }
    }, '选择要导出的脚本', '还没有保存过脚本', '导出')
  } catch (error: any) {
    showNotification('获取脚本列表失败: ' + error.message, 'error')
  }
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

// SQLite 数据库文件判断（.db / .db3 / .sqlite / .sqlite3）
const isSqliteFile = (file: { name: string; isDirectory: boolean }) =>
  !file.isDirectory && /\.(db|db3|sqlite|sqlite3)$/i.test(file.name)

const openSqliteViewer = (file: { path: string; name: string }) => {
  if (!selectedDevice.value) {
    showMessage('请先选择设备')
    return
  }
  dbViewerFile.value = { path: file.path, name: file.name }
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
  scriptPreviewObserver?.disconnect()
  scriptPreviewObserver = null
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
const { autoInstallProbe } = useProbeSettings();

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
  // 设置关闭时不自动探测新设备，也不记为已处理；重新开启后刷新仍会安装
  if (!autoInstallProbe.value) return;
  for (const d of devices.value) {
    if (!d.deviceId || probeKnownDevices.has(d.deviceId)) continue;
    probeKnownDevices.add(d.deviceId);
    runProbe(d.deviceId, true);
  }
};

// 探针工具：心跳 0x00 / TTS 0x01 / Shell 0x08 / 截屏 0x09 / 拍照 0x0A
const probeBusy = ref('');
const probeResult = ref('');
const probeShellRoot = ref(false);
const probeDisabled = computed(() => probeBusy.value !== '' || !selectedDevice.value);

const probeReady = () => {
  if (probeBusy.value) return false;
  if (!selectedDevice.value) {
    showMessage('请先在左侧选择设备', '探针工具');
    return false;
  }
  return true;
};

const probePost = async (path: string, payload: any, timeout = 200000) => {
  const res = await axios.post(`${API_BASE.value}${path}`, payload, { timeout });
  const d = res.data;
  if (d?.code !== 200 || d?.data?.connected !== true) {
    throw new Error(d?.message || '通讯失败');
  }
  const r = d.data.response || {};
  if (r.code !== 200) {
    throw new Error(r.message || '设备返回错误');
  }
  return r.data || {};
};

const playProbeTts = () => {
  if (!probeReady()) return;
  showPrompt('输入要让设备朗读的文本', (text: string) => {
    const t = (text || '').trim();
    if (!t || new TextEncoder().encode(t).length > 60000) {
      nextTick(() => showMessage(!t ? '文本不能为空' : '文本过长（单帧上限 65535 字节）', '远程 TTS 语音播放'));
      return;
    }
    probeBusy.value = 'tts';
    probeResult.value = '正在请求设备朗读...';
    probePost('/api/probe/message', { deviceId: selectedDevice.value, cmd: 0x01, data: { text: t } })
      .then(() => {
        probeResult.value = '朗读完成';
        showMessage('朗读完成', '远程 TTS 语音播放');
      })
      .catch((e: any) => {
        const text = `播放失败：${e?.response?.data?.message || e?.message || '请求异常'}`;
        probeResult.value = text;
        showMessage(text, '远程 TTS 语音播放');
      })
      .finally(() => { probeBusy.value = ''; });
  }, '输入要朗读的文本', '远程 TTS 语音播放');
};

// 探针工具：心跳（0x00），结果用弹窗提示
const sendProbeHeartbeat = async () => {
  if (!probeReady()) return;
  probeBusy.value = 'heartbeat';
  probeResult.value = '正在检测设备探针...';
  try {
    const info = await probePost('/api/probe/message', { deviceId: selectedDevice.value, cmd: 0x00, data: {} }, 60000);
    probeResult.value = `设备在线，探针版本 ${info.version || '未知'}`;
    showMessage(probeResult.value, '探针心跳');
  } catch (e: any) {
    const text = `心跳失败：${e?.response?.data?.message || e?.message || '请求异常'}`;
    probeResult.value = text;
    showMessage(text, '探针心跳');
  } finally {
    probeBusy.value = '';
  }
};

// 探针工具：shell 命令执行（0x08），文本由弹窗输入
const runProbeShell = () => {
  if (!probeReady()) return;
  showPrompt('输入要在设备上执行的 shell 命令', (command: string) => {
    const cmd = (command || '').trim();
    if (!cmd) {
      nextTick(() => showMessage('命令不能为空', 'Shell 命令执行'));
      return;
    }
    probeBusy.value = 'shell';
    probeResult.value = `$ ${cmd}${probeShellRoot.value ? '  (root)' : ''}\n正在执行...`;
    probePost('/api/probe/message', {
      deviceId: selectedDevice.value,
      cmd: 0x08,
      data: { command: cmd, root: probeShellRoot.value, timeout: 60 }
    })
      .then((info: any) => {
        const parts: string[] = [`$ ${cmd}${probeShellRoot.value ? '  (root)' : ''}`];
        if (info.stdout) parts.push(info.stdout);
        if (info.stderr) parts.push(`[stderr]\n${info.stderr}`);
        parts.push(`[exit ${info.exit}]${info.truncated ? '（输出过长已截断）' : ''}`);
        probeResult.value = parts.join('\n');
      })
      .catch((e: any) => {
        const text = `执行失败：${e?.response?.data?.message || e?.message || '请求异常'}`;
        probeResult.value = text;
        showMessage(text, 'Shell 命令执行');
      })
      .finally(() => { probeBusy.value = ''; });
  }, '例：ls /sdcard', 'Shell 命令执行');
};

// 探针工具：截图（0x09）/ 拍照（0x0A），拍照先取摄像头列表选择
const probeFacing = (facing: string) => {
  if (facing === 'front') return '前置';
  if (facing === 'back') return '后置';
  if (facing === 'external') return '外接';
  return '未知';
};

const doProbeCapture = async (kind: 'screenshot' | 'photo', cameraId = '') => {
  if (!probeReady()) return;
  probeBusy.value = kind;
  const label = kind === 'screenshot' ? '截屏' : '拍照';
  probeResult.value = `正在${label}...`;
  try {
    const payload: any = { deviceId: selectedDevice.value, kind };
    if (cameraId) payload.cameraId = cameraId;
    // capture 响应 data.response.data：screenshot 带 path；photo 额外带 base64 与 deleted
    const res = await axios.post(`${API_BASE.value}/api/probe/capture`, payload, { timeout: 200000 });
    const d = res.data;
    if (d?.code !== 200 || d?.data?.connected !== true) {
      throw new Error(d?.message || '通讯失败');
    }
    const r = d.data.response || {};
    if (r.code !== 200) throw new Error(r.message || '设备返回错误');
    const info = r.data || {};
    const name = (info.path || '').split('/').pop() || (kind === 'screenshot' ? 'screenshot.png' : 'photo.jpg');
    let b64 = info.base64;
    if (!b64) {
      if (info.readError) throw new Error(info.readError);
      // photo 由服务端读成 base64 后立即删除设备端文件，无 base64 说明服务仍是旧代码，不能再按路径取图
      if (kind === 'photo') throw new Error('未取得照片数据，请确认 DFM-service 已重启后重试');
      const resp = await axios.post(`${API_BASE.value}/api/device/files/base64`, {
        deviceId: selectedDevice.value,
        remotePath: info.path
      });
      if (resp.data.code !== 200 || !resp.data.data?.base64) {
        throw new Error(resp.data.message || `已生成 ${name}，但读取预览失败`);
      }
      b64 = resp.data.data.base64;
    }
    const mime = name.toLowerCase().endsWith('.png') ? 'image/png' : 'image/jpeg';
    imageViewerList.value = [];
    imageViewerKind.value = 'image';
    imageViewerName.value = name;
    imageViewerSrc.value = '';
    imageViewerError.value = '';
    imageViewerSize.value = Number(info.size) || 0;
    imageViewerLoading.value = true;
    imageViewerVisible.value = true;
    imageViewerSrc.value = `data:${mime};base64,${b64}`;
    imageViewerLoading.value = false;
    probeResult.value = `${label}完成（${name}）${info.deleted ? '，设备端文件已删除' : ''}`;
  } catch (e: any) {
    const text = `${label}失败：${e?.response?.data?.message || e?.message || '请求异常'}`;
    probeResult.value = text;
    showMessage(text, kind === 'screenshot' ? '屏幕截图' : '摄像头拍照');
  } finally {
    probeBusy.value = '';
  }
};

const onProbePhoto = async () => {
  if (!probeReady()) return;
  probeBusy.value = 'photo';
  probeResult.value = '正在获取摄像头列表...';
  try {
    const res = await axios.post(`${API_BASE.value}/api/probe/cameras`, {
      deviceId: selectedDevice.value
    }, { timeout: 60000 });
    const cameras = res.data?.data?.cameras || [];
    if (res.data?.code !== 200 || cameras.length === 0) {
      throw new Error(res.data?.message || '未检测到摄像头');
    }
    if (cameras.length === 1) {
      // 单摄像头无需选择：先清 busy，否则 doProbeCapture 的 probeReady() 守卫会把本次调用直接吞掉，
      // probeBusy 永远停在 'photo'、界面卡在「正在获取摄像头列表...」（多摄像头分支在 :5239 已清）
      probeBusy.value = '';
      await doProbeCapture('photo', String(cameras[0].id));
      return;
    }
    probeBusy.value = '';
    const items = cameras.map((c: any) => ({
      value: String(c.id),
      label: `摄像头 ${c.id} · ${probeFacing(c.facing)}`,
      desc: c.resolution || ''
    }));
    showList(items, (id: string) => { doProbeCapture('photo', id); }, '选择摄像头', '未检测到摄像头', '拍照');
  } catch (e: any) {
    const text = `拍照失败：${e?.response?.data?.message || e?.message || '请求异常'}`;
    probeResult.value = text;
    showMessage(text, '摄像头拍照');
    probeBusy.value = '';
  }
};
provide('app-utils', { emit, API_BASE, TAB_ITEMS, visibleTabs, activeTab, loadConfig, fileSortKey, fileSortAsc, sortedFiles, toggleFileSort, selectedFiles, selectedFileCount, fileAllSelected, fileSomeSelected, toggleFileSelection, toggleSelectAllFiles, clearFileSelection, enterAction, devices, selectedDevice, deviceInfo, loading, apps, appLoading, files, fileLoading, currentPath, dbViewerFile, route, wirelessIp, wirelessStatus, batchDeviceInfos, batchSelectedDevices, batchLoading, batchModelFilter, batchFilteredDevices, batchSelectAllChecked, loadBatchDeviceInfos, toggleBatchDevice, toggleAllBatchDevices, selectedBatchCount, batchRemoteActive, batchRemoteConnecting, batchRemoteCards, batchRemoteSource, batchRemoteStatus, batchRemoteWsStatus, batchRemoteCanvasRefs, setBatchRemoteCanvasRef, setBatchRemoteSource, batchRemoteGridCols, batchRemoteSendMirror, BATCH_REMOTE_CLICK_THRESHOLD, batchRemoteOnPointer, batchRemoteHandleJson, batchRemoteCodecFromSps, batchRemoteIsKeyframe, batchRemoteDrawFrame, batchRemoteHandleBinary, concatU8, startBatchRemote, stopBatchRemote, scriptCanvasRef, scriptScreenBodyRef, scriptLayoutRef, scriptPreviewWidth, scriptSideTwoCol, scriptStreaming, scriptWsStatus, scriptRecording, scriptVerify, SCRIPT_VERIFY_THRESHOLD, SCRIPT_VERIFY_CROP, scriptVerifySeq, scriptSteps, scriptRunning, scriptOpConfig, scriptRepeat, scriptSaving, scriptMeta, syncScriptSideLayout, syncScriptPreviewWidth, setScriptMeta, scriptPointerActive, scriptRemotePressing, scriptPressStart, scriptMoved, SCRIPT_LONGPRESS_MS, scriptStepLabel, scriptKeyName, scriptKeyPresetOf, onScriptKeyPresetChange, SCRIPT_VARIABLE_HELP, scriptConditionDefaults, onScriptConditionKindChange, scriptGotoTargets, onScriptBranchActionChange, scriptApkOptions, scriptApkLoading, fetchScriptApks, scriptAppPackages, scriptAppNames, scriptAppLoading, fetchScriptApps, scriptTemplateOptions, scriptTemplateLoading, fetchScriptTemplates, scriptTemplatePreview, scriptTemplatePreviewSeq, loadScriptTemplatePreview, onScriptTemplatePreviewError, scriptAdbStepDefaults, scriptShowMarkers, scriptDragging, scriptEditingStep, scriptDrawMarkers, scriptDrawFrame, scriptCacheFrame, scriptRedrawMarkers, scriptMarkerHitRadius, scriptHitTest, scriptMoveMarker, scriptCropMode, scriptCropRegion, scriptCropTargetStep, toggleCropMode, startCropForCondition, finishCropRegion, captureVerifyFrame, flushScriptVerify, addRecordedStep, scriptHandleBinary, startScriptStream, shouldStreamBeActive, scriptReconnectAttempts, scriptManualStop, scheduleScriptReconnect, stopScriptStream, beginScriptStream, scriptLastDataAt, startStallWatch, stopStallWatch, canvasPoint, scriptSendTouch, scriptSendText, scriptKeyboardActive, scriptTypingIndex, scriptFlushTyping, scriptReleaseKeyboard, scriptOnKeyDown, scriptSendKey, scriptOnPointerDown, scriptOnPointerMove, scriptOnPointerUp, scriptOnPointerCancel, addScriptStep, addScriptAdbStep, addScriptGestureStep, addScriptWaitStep, scriptStepGroups, addScriptConditionStep, removeScriptStep, clearScriptSteps, setAllIntervals, toggleStepEdit, scriptStepDragFrom, scriptStepDragOver, onScriptStepDragStart, onScriptStepDragOver, onScriptStepDragLeave, onScriptStepDrop, onScriptStepDragEnd, toggleRecording, saveScript, applyImportedSteps, scriptExporting, exportScriptZip, importScriptFromZip, importScriptFromList, openImportMenu, runCurrentSteps, stopScriptRun, repeatRun, batchRebootLoading, batchInstallLoading, batchShellLoading, batchShellResults, showBatchShellResults, batchShellCommand, batchKeyLoading, batchKeyevent, batchReboot, batchInstallApp, modalVisible, modalType, modalTitle, modalMessage, modalPlaceholder, modalConfirmText, modalVariant, modalProgress, modalCallback, modalListItems, modalEmptyText, fetchDevices, fetchDeviceInfo, startScrcpy, fetchApps, fetchFiles, exportFile, isSqliteFile, openSqliteViewer, importFile, EDITABLE_EXTS, EDIT_MAX_BYTES, editorVisible, editorFileName, editorInitialContent, editorLoading, editorLoadError, editorSaving, isEditableTextFile, editDisabledReason, openFileEditor, readResp_dataCodeIs404, handleEditorSave, handleEditorClose, IMAGE_EXTS, VIDEO_EXTS, IMAGE_MAX_BYTES, previewKindOf, imageViewerVisible, imageViewerName, imageViewerSrc, imageViewerLoading, imageViewerError, imageViewerSize, imageViewerKind, imageViewerList, imageViewerIndex, imageViewerTotal, isViewableFile, isViewableImageFile, viewDisabledReason, openImageViewer, loadViewerFile, handleImageViewerNav, handleImageViewerClose, selectDevice, installApkFile, installApp, apkDragOver, onPageDragOver, onApkDragLeave, onApkDrop, uninstallApp, apkRepoVisible, apkRepoUploading, apkRepoOptions, apkRepoLoading, apkRepoInputEl, fetchApkRepo, openApkRepo, pickRepoApk, onRepoApkChosen, installFromRepo, removeRepoApk, formatRepoSize, startApp, stopApp, extractApk, navigateTo, goBack, createFolder, createFile, deleteFile, renameFile, batchDeleteSelected, batchMoveSelected, simulateKey, volumeUp, volumeDown, muteVolume, restartDevice, showDeviceLogs, openDeviceShell, capturingLogs, startLogCapture, stopLogCapture, cleaningMemory, cleanMemory, rebootToLoader, importingCert, importCaCert, connectWireless, goHome, showMessage, showConfirm, showPrompt, showList, showNotification, showProgressNotification, hideProgressNotification, handleModalConfirm, handleModalCancel, copyToClipboard, confirmDisconnect, copyDeviceInfo, disconnectAllLoading, confirmDisconnectAll, RATE_POLL_INTERVAL, SNAPSHOT_INTERVAL, RATE_HISTORY_MAX, monitorSnapshot, monitorRate, monitorLoading, monitorError, monitorActive, rateHistory, rateViewMode, topProcesses, topRequesting, fetchMonitorTop, rateRequesting, snapshotChartEl, rateChartEl, coreChartEl, isDarkMode, formatBytes, formatRate, formatFreq, formatPercent, usageColor, chartTextColor, chartAxisColor, chartSplitColor, buildGaugeOption, buildCoreOption, buildRateOption, renderSnapshotCharts, renderRateChart, resizeMonitorCharts, activeNic, fetchMonitorSnapshot, fetchMonitorRate, scheduleRatePoll, startMonitor, stopMonitor, refreshMonitor, disposeMonitorCharts, serialPorts, serialPortsLoading, serialToolDeployed, serialDevice, serialMode, serialPayload, serialEnd, serialBaud, serialLoop, serialSending, serialOutput, serialMessage, serialMessageType, serialStreamRunning, serialStreamPid, serialStreamLog, serialStreamBusy, serialReadBaud, serialReadParity, serialReadDataBits, serialReadStopBits, serialReadView, serialSendMode, serialSendPayload, serialSendTimes, serialSendInterval, serialSessionOpened, serialReading, serialReadLoopId, loadSerialPorts, deploySerialTool, sendSerialData, serialStream, buildSerialOpenPayload, postSerialCommand, appendSerialReadLine, failSerialReadLine, readSerialData, stopSerialRead, runSerialReadLoop, toggleSerialRead, sendSerialSessionData, stopSerialSessionSend, serialScheme, ptyRunning, ptyPid, ptyNodeType, ptyAppHolds, ptyLeftover, ptyLog, ptyBusy, keyList, keyInjectBusy, keyInjectOutput, buildKeyFrame, ptyDevice, loadKeyList, loadPtyStatus, deployPtyTools, controlPty, keySequence, appendKey, removeKeyAt, clearKeySequence, keyFunctionText, applyKeys, probeStates, probeLoadingMap, probeKnownDevices, probeStateOf, setProbeState, setProbeBusy, runProbe, retryProbe, autoInstallNewDevices, probeBusy, probeResult, probeShellRoot, probeDisabled, probeReady, probePost, playProbeTts, sendProbeHeartbeat, runProbeShell, probeFacing, doProbeCapture, onProbePhoto });
</script>
<style scoped>
:deep(.sidebar) {
  display: flex;
  flex-direction: column;
  width: 250px;
  height: 100%;
}

:deep(.device-list-container) {
  flex: 1;
  overflow-y: auto;
  margin-bottom: 8px;
}

:deep(.probe-dot) {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  flex-shrink: 0;
  background-color: #999;
  cursor: pointer;
  transition: background-color 0.2s;
}

:deep(.probe-dot.ok) {
  background-color: #4caf50;
}

:deep(.probe-dot.fail) {
  background-color: #f44336;
}

:deep(.probe-dot.busy) {
  background-color: #ff9800;
}

:deep(.probe-dot:hover) {
  box-shadow: 0 0 0 3px rgba(0, 0, 0, 0.1);
}

:deep(.file-list) {
  border: 1px solid #ccc;
  max-height: calc(100vh - 180px);
  overflow-y: auto;
}

:deep(.wireless-connect) {
  margin-top: auto;
  border-top: 1px solid #e0e0e0;
}

:deep(.wireless-input-group) {
  display: flex;
  gap: 8px;
  margin-top: 8px;
}

:deep(.wireless-input) {
  width: 150px;
  height: 32px;
  padding: 0 8px;
  border: 1px solid #ddd;
  border-radius: 4px;
  font-size: 14px;
}

:deep(.wireless-button) {
  height: 32px;
  padding: 0 16px;
  background-color: #4caf50;
  color: white;
  border: none;
  border-radius: 4px;
  cursor: pointer;
  font-size: 14px;
}

:deep(.wireless-button:hover) {
  background-color: #45a049;
}

:deep(.wireless-status) {
  margin-top: 8px;
  font-size: 12px;
  color: #666;
  min-height: 16px;
}

:deep(.back-button) {
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

:deep(.back-button:hover) {
  background: rgba(128, 128, 128, 0.8);
}

.app {
  position: relative;
  width: 100%;
  height: 100vh;
  min-height: 544px;
}

/* ===== 投屏页面卡片布局 ===== */
:deep(.keys-page) {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

:deep(.scrcpy-card) {
  background: #fff;
  border-radius: 10px;
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.08);
  overflow: hidden;
}

:deep(.card-header) {
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

:deep(.card-icon) {
  display: flex;
  align-items: center;
  color: #4caf50;
}

/* 卡片头部右侧操作按钮（如设备信息「复制」） */
:deep(.card-header-action) {
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

:deep(.card-header-action:hover:not(:disabled)) {
  background: #e8f5e9;
  border-color: #a5d6a7;
  color: #2e7d32;
}

:deep(.card-header-action:active:not(:disabled)) {
  background: #c8e6c9;
}

:deep(.card-header-action:disabled) {
  opacity: 0.5;
  cursor: not-allowed;
}

:deep(.card-body) {
  padding: 16px;
}

/* ===== 投屏启动按钮 ===== */
:deep(.scrcpy-btn) {
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

:deep(.scrcpy-btn:hover) {
  transform: translateY(-2px);
  box-shadow: 0 6px 20px rgba(76, 175, 80, 0.4);
  background: linear-gradient(135deg, #43a047, #388e3c);
}

:deep(.scrcpy-btn:active) {
  transform: translateY(0);
  box-shadow: 0 2px 8px rgba(76, 175, 80, 0.3);
}

/* ===== 按键网格 ===== */
:deep(.key-grid) {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 6px;
  margin-bottom: 10px;
}

:deep(.key-btn) {
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

:deep(.key-btn:hover) {
  background: #e8f5e9;
  border-color: #a5d6a7;
  color: #2e7d32;
  transform: translateY(-1px);
  box-shadow: 0 3px 10px rgba(76, 175, 80, 0.12);
}

:deep(.key-btn:active) {
  transform: translateY(0);
  background: #c8e6c9;
}

:deep(.key-btn svg) {
  color: #666;
}

:deep(.key-btn:hover svg) {
  color: #4caf50;
}

/* ===== 重启按钮 ===== */
:deep(.restart-btn) {
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

:deep(.restart-btn:hover) {
  background: #ffebee;
  border-color: #ef9a9a;
  transform: translateY(-1px);
  box-shadow: 0 3px 10px rgba(198, 40, 40, 0.12);
}

:deep(.restart-btn:active) {
  transform: translateY(0);
}

/* ===== 音量控制行 ===== */
:deep(.volume-row) {
  display: grid;
  grid-template-columns: 1fr 1fr 1fr;
  gap: 6px;
}

:deep(.vol-btn) {
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

:deep(.vol-btn:hover) {
  background: #e3f2fd;
  border-color: #90caf9;
  color: #1565c0;
  transform: translateY(-1px);
  box-shadow: 0 3px 10px rgba(33, 150, 243, 0.12);
}

:deep(.vol-btn:active) {
  transform: translateY(0);
  background: #bbdefb;
}

:deep(.vol-btn.mute-btn:hover) {
  background: #fff3e0;
  border-color: #ffcc80;
  color: #e65100;
  box-shadow: 0 3px 10px rgba(255, 152, 0, 0.12);
}

/* ===== 设备信息页 ===== */
:deep(.info-grid-2col) {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 2px 12px;
}

:deep(.info-item-2col) {
  padding: 6px 0;
  border-bottom: 1px solid #f5f5f5;
  min-width: 0;
}

:deep(.info-item-2col:nth-last-child(-n+2)) {
  border-bottom: none;
}

:deep(.info-label) {
  display: block;
  font-size: 11px;
  color: #999;
  margin-bottom: 2px;
}

:deep(.info-value) {
  display: block;
  font-size: 13px;
  color: #333;
  word-break: break-all;
}

:deep(.copyable-value) {
  cursor: pointer;
  padding: 2px 6px;
  margin: -2px -6px;
  border-radius: 4px;
  transition: all 0.2s ease;
  display: inline-block;
  position: relative;
}

:deep(.copyable-value:hover:not(.disabled)) {
  background-color: #e3f2fd;
  color: #1565c0;
}

:deep(.copyable-value:hover:not(.disabled)::after) {
  content: '📋';
  margin-left: 4px;
  font-size: 12px;
}

:deep(.copyable-value:active:not(.disabled)) {
  background-color: #bbdefb;
}

:deep(.copyable-value.disabled) {
  cursor: not-allowed;
  color: #999;
}

:deep(.status-badge) {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 10px;
  font-size: 12px;
  font-weight: 500;
}

:deep(.status-badge.connected) {
  background: #e8f5e9;
  color: #2e7d32;
}

:deep(.status-badge.locked) {
  background: #fff3e0;
  color: #e65100;
}

:deep(.loading-text) {
  color: #999;
  font-size: 13px;
}

/* ===== 功能区按钮 ===== */
:deep(.feature-grid) {
  display: grid;
  grid-template-columns: 1fr 1fr 1fr 1fr;
  gap: 8px;
}

:deep(.feature-btn) {
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

:deep(.feature-btn:hover) {
  background: #e8f5e9;
  border-color: #a5d6a7;
  color: #2e7d32;
  transform: translateY(-1px);
  box-shadow: 0 3px 10px rgba(76, 175, 80, 0.12);
}

:deep(.feature-btn:active) {
  transform: translateY(0);
  background: #c8e6c9;
}

:deep(.feature-btn.danger:hover) {
  background: #fff3e0;
  border-color: #ffcc80;
  color: #e65100;
  box-shadow: 0 3px 10px rgba(255, 152, 0, 0.12);
}

/* 抓取中状态：红色提示，悬停显示"结束抓取" */
:deep(.feature-btn.capturing) {
  background: #ffebee;
  border-color: #ef9a9a;
  color: #c62828;
  animation: capturePulse 1.6s ease-in-out infinite;
}

:deep(.feature-btn.capturing svg) {
  color: #c62828;
}

:deep(.capture-stop-label) {
  display: none;
}

:deep(.feature-btn.capturing:hover) {
  background: #ffcdd2;
  border-color: #e57373;
  color: #b71c1c;
  animation: none;
  transform: translateY(-1px);
}

:deep(.feature-btn.capturing:hover .capture-stop-label) {
  display: inline;
}

:deep(.feature-btn.capturing:hover .capture-status-label) {
  display: none;
}

@keyframes capturePulse {
  0%, 100% { box-shadow: 0 0 0 0 rgba(239, 83, 80, 0.25); }
  50% { box-shadow: 0 0 0 5px rgba(239, 83, 80, 0.08); }
}

:deep(.feature-btn svg) {
  color: #666;
}

:deep(.feature-btn:hover svg) {
  color: #4caf50;
}

:deep(.feature-btn.danger:hover svg) {
  color: #e65100;
}

/* ===== 应用管理页 ===== */
:deep(.card-action-btn) {
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

:deep(.card-action-btn:hover) {
  background: #43a047;
  transform: translateY(-1px);
}

:deep(.card-action-btn:active) {
  transform: translateY(0);
}

/* APK 全页拖拽安装覆盖层 */
:deep(.apk-drop-overlay) {
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

:deep(.apk-drop-overlay-box) {
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

:deep(.app-list) {
  max-height: calc(100vh - 140px);
  overflow-y: auto;
}

:deep(.app-item) {
  display: flex;
  align-items: center;
  padding: 10px 16px;
  border-top: 1px solid #f0f0f0;
  transition: background 0.2s;
}

:deep(.app-item:first-child) {
  border-top: none;
}

:deep(.app-item:hover) {
  background: #f8f9fb;
}

:deep(.app-item-info) {
  flex: 1;
  min-width: 0;
}

:deep(.app-item-name) {
  font-size: 13px;
  font-weight: 600;
  color: #333;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

:deep(.app-item-desc) {
  font-size: 11px;
  color: #999;
  margin-top: 2px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

:deep(.app-item-actions) {
  display: flex;
  gap: 4px;
  flex-shrink: 0;
  margin-left: 8px;
}

:deep(.app-act-btn) {
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

:deep(.app-act-btn:hover) {
  border-color: #a5d6a7;
  background: #e8f5e9;
}

:deep(.app-act-btn.start:hover) {
  border-color: #a5d6a7;
  background: #e8f5e9;
  color: #2e7d32;
}

:deep(.app-act-btn.stop:hover) {
  border-color: #90caf9;
  background: #e3f2fd;
  color: #1565c0;
}

:deep(.app-act-btn.extract:hover) {
  border-color: #ffcc80;
  background: #fff3e0;
  color: #e65100;
}

:deep(.app-act-btn.uninstall:hover) {
  border-color: #ef9a9a;
  background: #ffebee;
  color: #c62828;
}

/* ===== 文件管理页 ===== */
:deep(.file-path-text) {
  font-size: 13px;
  font-weight: 500;
  color: #555;
  flex: 1;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  margin-right: 8px;
}

:deep(.file-toolbar) {
  display: flex;
  gap: 4px;
  padding: 8px 12px;
  border-bottom: 1px solid #f0f0f0;
  background: #fafafa;
}

:deep(.toolbar-btn) {
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

:deep(.toolbar-btn:hover) {
  background: #e8f5e9;
  border-color: #a5d6a7;
  color: #2e7d32;
}

:deep(.toolbar-btn:active) {
  background: #c8e6c9;
}

:deep(.toolbar-btn:disabled) {
  opacity: 0.4;
  cursor: not-allowed;
}

:deep(.toolbar-btn:disabled:hover) {
  background: #fff;
  border-color: #e8eaed;
  color: #555;
  transform: none;
}

:deep(.file-item) {
  display: flex;
  align-items: center;
  padding: 8px 12px;
  border-top: 1px solid #f5f5f5;
  cursor: pointer;
  transition: background 0.2s;
}

:deep(.file-item:first-child) {
  border-top: none;
}

:deep(.file-item:hover) {
  background: #f0f7f0;
}

:deep(.file-item-icon) {
  flex-shrink: 0;
  margin-right: 8px;
  font-size: 16px;
}

/* 文件多选：勾选列与批量操作条 */
:deep(.file-item-check) {
  flex-shrink: 0;
  width: 24px;
  margin-right: 4px;
  display: flex;
  align-items: center;
  justify-content: center;
}

:deep(.file-item-check input[type="checkbox"]) {
  cursor: pointer;
  accent-color: #2e7d32;
}

:deep(.file-batch-info) {
  align-self: center;
  font-size: 12px;
  font-weight: 500;
  color: #2e7d32;
}

:deep(.toolbar-btn.danger:hover) {
  background: #ffebee;
  border-color: #ef9a9a;
  color: #c62828;
}

:deep(.file-item-name) {
  flex: 1;
  font-size: 13px;
  color: #333;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  min-width: 0;
}

:deep(.file-item-name.is-dir) {
  font-weight: 500;
  color: #1565c0;
}

:deep(.file-item-size) {
  flex-shrink: 0;
  font-size: 11px;
  color: #999;
  margin: 0 8px;
  font-family: 'Consolas', 'Monaco', monospace;
  min-width: 52px;
  text-align: right;
}

/* 文件列表排序表头与修改时间列 */
:deep(.file-list-header) {
  display: flex;
  align-items: center;
  padding: 6px 12px;
  border-bottom: 1px solid #f0f0f0;
  font-size: 11px;
  color: #999;
  user-select: none;
}

:deep(.file-sortable) {
  cursor: pointer;
}

:deep(.file-sortable:hover) {
  color: #1565c0;
}

:deep(.file-sortable.sort-active) {
  color: #1565c0;
  font-weight: 500;
}

:deep(.file-item-time) {
  flex-shrink: 0;
  font-size: 11px;
  color: #999;
  font-family: 'Consolas', 'Monaco', monospace;
  min-width: 110px;
  text-align: right;
}

:deep(.device-item) {
  display: flex !important;
  align-items: center;
  gap: 8px;
}

:deep(.device-item-info) {
  flex: 1;
  min-width: 0;
  cursor: pointer;
}

/* 断开全部：设备列表末尾的常驻按钮，宽度与设备项一致 */
:deep(.disconnect-all-btn) {
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

:deep(.disconnect-all-btn:hover:not(:disabled)) {
  background-color: #d32f2f;
}

:deep(.disconnect-all-btn:disabled) {
  filter: grayscale(1);
  cursor: not-allowed;
}

:deep(.disconnect-btn) {
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

:deep(.disconnect-btn:hover) {
  background: #ffebee;
  border-color: #ef9a9a;
}

:deep(.file-item-actions) {
  display: flex;
  gap: 4px;
  flex-shrink: 0;
  margin-left: 8px;
}

:deep(button.file-act-btn) {
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

:deep(button.file-act-btn.export:hover) {
  background: #e8f5e9;
  border-color: #a5d6a7;
}

:deep(button.file-act-btn.delete:hover) {
  background: #ffebee;
  border-color: #ef9a9a;
}

/* ===== 批量操作页面 ===== */
:deep(.tab-content) {
  height: auto;
  flex: 1;
  min-height: 0;
}

/* 模拟脚本 Tab 需要确定高度的父级链，让 script-page/layout 能撑满剩余空间 */
:deep(.tab-content:has(> .script-page)) {
  height: 100%;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

/* 批量操作 Tab 同理：需要确定高度，投屏区才能在剩余空间内滚而不撑高整页 */
:deep(.tab-content:has(> .batch-page)) {
  height: 100%;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

:deep(.batch-page) {
  display: flex;
  flex-direction: column;
  height: 100%;
  /* 底部留白与左右两侧的 10px 保持一致 */
  padding-bottom: 10px;
  gap: 12px;
  /* 整页不滚动：设备区与投屏区在剩余空间内自行分配，投影区内部滚 */
  min-height: 0;
}

:deep(.batch-card) {
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

:deep(.batch-header) {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

:deep(.batch-header-left) {
  display: flex;
  align-items: center;
  gap: 8px;
}

:deep(.batch-selected-info) {
  font-size: 13px;
  font-weight: 400;
  color: #4caf50;
}

:deep(.batch-content) {
  display: flex;
  flex-direction: column;
  flex: 1;
  overflow: hidden;
}

/* 上半部分：设备列表 - flex:1 占据剩余空间 */
:deep(.batch-device-list) {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  border-bottom: 1px solid #f0f0f0;
  min-height: 0;
}

:deep(.batch-list-header) {
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

:deep(.batch-list-header input[type="checkbox"]) {
  width: 16px;
  height: 16px;
  cursor: pointer;
}

:deep(.batch-list-body) {
  flex: 1;
  overflow-y: auto;
}

:deep(.batch-device-row) {
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

:deep(.batch-device-row:hover) {
  background: #f5f9ff;
}

:deep(.batch-device-row.is-selected) {
  background: #e8f5e9;
}

:deep(.batch-device-row input[type="checkbox"]) {
  width: 16px;
  height: 16px;
  cursor: pointer;
}

:deep(.col-device-id) {
  flex: 1.5;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
}

:deep(.col-model) {
  flex: 1.5;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
}

:deep(.col-sn) {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
  font-family: monospace;
  font-size: 12px;
}

:deep(.batch-checkbox-label) {
  display: flex;
  align-items: center;
  gap: 6px;
  cursor: pointer;
}

.batch-loading,
:deep(.batch-empty) {
  padding: 24px;
  text-align: center;
  color: #999;
  font-size: 13px;
}

:deep(.model-filter-input) {
  width: 90px;
  padding: 3px 8px;
  margin-top: 4px;
  border: 1px solid #d9d9d9;
  border-radius: 4px;
  font-size: 12px;
  outline: none;
  transition: border-color 0.2s;
}

:deep(.model-filter-input:focus) {
  border-color: #4caf50;
}

/* 下半部分：操作按钮区 + 结果区 - 高度由内容决定，不强制拉伸 */
:deep(.batch-action-area) {
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
:deep(.batch-action-grid) {
  flex: none;
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 12px;
  width: 100%;
  max-width: 600px;
}

:deep(button.batch-action-btn) {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  /* 5 列后按钮变窄，禁止文字换行以免撑高按钮区 */
  white-space: nowrap;
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

:deep(button.batch-action-btn:hover:not(:disabled)) {
  background: #e8f5e9;
  border-color: #4caf50;
  color: #2e7d32;
}

:deep(button.batch-action-btn:disabled) {
  background: #f5f5f5;
  color: #bbb;
  border-color: #eee;
  cursor: not-allowed;
}

:deep(.batch-action-placeholder) {
  color: #bbb;
  font-size: 14px;
}

/* 批量 Shell 执行结果 - 显示时给合适的高度，不参与外层比例 */
:deep(.batch-shell-results) {
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

:deep(.shell-results-header) {
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

:deep(.shell-close-btn) {
  background: none;
  border: none;
  font-size: 18px;
  color: #999;
  cursor: pointer;
  padding: 0 4px;
  line-height: 1;
}

:deep(.shell-close-btn:hover) {
  color: #666;
}

:deep(.shell-results-list) {
  display: flex;
  flex-direction: column;
  gap: 8px;
  flex: 1;
  overflow-y: auto;
  min-height: 0;
  margin-top: 8px;
}

:deep(.shell-result-item) {
  padding: 8px;
  background: #fafafa;
  border-radius: 4px;
  border-left: 3px solid #d9d9d9;
}

:deep(.shell-result-item:nth-child(odd)) {
  background: #f5f5f5;
}

:deep(.shell-success) {
  border-left-color: #4caf50;
}

:deep(.shell-failed) {
  border-left-color: #f44336;
}

:deep(.shell-result-device) {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 6px;
  font-size: 12px;
}

:deep(.shell-status-icon) {
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

:deep(.shell-success .shell-status-icon) {
  background: #4caf50;
}

:deep(.shell-failed .shell-status-icon) {
  background: #f44336;
}

:deep(.shell-device-id) {
  font-weight: 500;
  color: #333;
  word-break: break-all;
}

/* ===== 批量投屏画面区 ===== */
:deep(.batch-remote-area) {
  padding: 12px 16px;
  background: #fafafa;
  border-radius: 8px;
  /* 与设备区共享剩余空间；画面高度已按宽高比自适应，不会撑高整页 */
  flex: 1 1 auto;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

:deep(.batch-remote-header) {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
  font-size: 13px;
  font-weight: 600;
  color: #333;
  flex: 0 0 auto;
}

:deep(.batch-remote-status) {
  padding: 2px 10px;
  border-radius: 10px;
  background: #eceff1;
  color: #78909c;
  font-size: 11px;
  font-weight: 500;
}

:deep(.batch-remote-status.live) {
  background: #e8f5e9;
  color: #2e7d32;
}

:deep(.batch-remote-error) {
  margin-bottom: 10px;
  padding: 6px 10px;
  border-radius: 6px;
  background: #fff8e1;
  color: #b26a00;
  font-size: 12px;
}

:deep(.batch-remote-grid) {
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

:deep(.batch-remote-tile) {
  position: relative;
  background: #000;
  border-radius: 8px;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  /* 行高 1fr 下允许收缩，避免内容把行撑破 */
  min-height: 0;
}

:deep(.batch-remote-tile.source) {
  outline: 2px solid #4caf50;
}

:deep(.batch-remote-tile.dead) {
  opacity: 0.4;
}

:deep(.batch-remote-tile-header) {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 4px 8px;
  background: rgba(255, 255, 255, 0.92);
  font-size: 11px;
}

:deep(.batch-remote-tile-name) {
  font-weight: 600;
  color: #333;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

:deep(.batch-remote-tile-diag) {
  margin-left: 6px;
  font-weight: 400;
  color: #999;
  font-size: 10px;
}

:deep(button.batch-remote-tile-tag) {
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

:deep(button.batch-remote-tile-tag:hover:not(:disabled)) {
  background: #e8f5e9;
  border-color: #4caf50;
  color: #2e7d32;
}

button.batch-remote-tile-tag.active,
:deep(button.batch-remote-tile-tag:disabled.active) {
  background: #4caf50;
  border-color: #4caf50;
  color: #fff;
  cursor: default;
}

:deep(.batch-remote-tile-tag.active) {
  background: #4caf50;
  color: #fff;
}

/* 画面容器：撑满卡片剩余空间（卡片高度由网格行均分，见 .batch-remote-grid）。
   高度完全由布局分配，不再依赖 JS 测高，因此不存在自反馈。 */
:deep(.batch-remote-tile-body) {
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
:deep(.batch-remote-canvas) {
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
:deep(.batch-remote-tile.source .batch-remote-canvas) {
  cursor: crosshair;
}

:deep(.batch-remote-tile:not(.source) .batch-remote-canvas) {
  cursor: pointer;
}

:deep(.batch-remote-tile-loading) {
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

:deep(.shell-output-section) {
  margin-top: 4px;
}

:deep(.shell-output-label) {
  font-size: 11px;
  color: #888;
  margin-bottom: 2px;
  font-weight: 500;
}

:deep(.shell-output) {
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

:deep(.shell-error .shell-output) {
  background: #fff8f8;
  border-color: #ffd6d6;
}

:deep(.shell-error-text) {
  color: #c62828;
}

/* ===== 性能监控页面 ===== */
:deep(.monitor-error) {
  margin-bottom: 12px;
  padding: 8px 12px;
  background: #ffebee;
  border: 1px solid #ffcdd2;
  border-radius: 6px;
  color: #c62828;
  font-size: 13px;
}

:deep(.monitor-usage) {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

:deep(.gauge-chart-wide) {
  width: 100%;
  height: 190px;
}

/* 性能监控第二行：CPU 频率 | 内存/磁盘 两列布局 */
:deep(.monitor-row) {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
  align-items: stretch;
}

:deep(.gauge-chart) {
  width: 100%;
  height: 160px;
}

:deep(.gauge-detail-row) {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}

:deep(.gauge-detail) {
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

:deep(.detail-title) {
  font-size: 13px;
  font-weight: 600;
  color: #333;
  margin-bottom: 2px;
}

:deep(.monitor-empty) {
  color: #999;
  font-size: 12px;
}

:deep(.monitor-live-tag) {
  margin-left: auto;
  padding: 2px 8px;
  border-radius: 10px;
  background: #e8f5e9;
  color: #2e7d32;
  font-size: 11px;
  font-weight: 500;
}

/* ===== 视图切换开关（折线图 / TOP进程） ===== */
:deep(.rate-view-switch) {
  margin-left: auto;
  display: inline-flex;
  border: 1px solid #e0e0e0;
  border-radius: 8px;
  overflow: hidden;
}

:deep(.rate-view-switch button) {
  padding: 3px 10px;
  border: none;
  background: transparent;
  color: #888;
  font-size: 11px;
  cursor: pointer;
  transition: all 0.2s ease;
}

:deep(.rate-view-switch button + button) {
  border-left: 1px solid #e0e0e0;
}

:deep(.rate-view-switch button.active) {
  background: #4caf50;
  color: #fff;
}

/* ===== TOP 进程表格 ===== */
:deep(.top-table-wrap) {
  max-height: 260px;
  overflow-y: auto;
  border: 1px solid #eef1f4;
  border-radius: 8px;
}

:deep(.top-table) {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}

.top-table th,
:deep(.top-table td) {
  padding: 6px 10px;
  text-align: left;
  white-space: nowrap;
}

:deep(.top-table thead th) {
  position: sticky;
  top: 0;
  background: #f8f9fb;
  color: #666;
  font-weight: 600;
  border-bottom: 1px solid #eef1f4;
}

:deep(.top-table tbody tr) {
  border-bottom: 1px solid #f5f6f8;
}

:deep(.top-table tbody tr:last-child) {
  border-bottom: none;
}

:deep(.top-cpu-high) {
  color: #f44336;
  font-weight: 600;
}

:deep(.top-cmd) {
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 260px;
}

:deep(.monitor-freq-info) {
  margin-left: auto;
  font-size: 12px;
  font-weight: 400;
  color: #888;
}

:deep(.rate-summary) {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 8px;
  margin-bottom: 12px;
}

:deep(.rate-item) {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 8px 10px;
  background: #f8f9fb;
  border: 1px solid #eef1f4;
  border-radius: 8px;
}

:deep(.rate-label) {
  font-size: 11px;
  color: #999;
}

:deep(.rate-value) {
  font-size: 15px;
  font-weight: 600;
  color: #333;
}

:deep(.rate-value.cpu) {
  color: #4caf50;
}

:deep(.rate-value.down) {
  color: #2196f3;
}

:deep(.rate-value.up) {
  color: #ff9800;
}

:deep(.rate-chart) {
  width: 100%;
  height: 220px;
}

:deep(.core-chart) {
  width: 100%;
  height: 180px;
}

@media (max-width: 720px) {
  :deep(.monitor-row) {
    grid-template-columns: 1fr;
  }

  :deep(.gauge-detail-row) {
    grid-template-columns: 1fr;
  }

  :deep(.rate-summary) {
    grid-template-columns: repeat(2, 1fr);
  }
}

@media (prefers-color-scheme: dark) {
  .gauge-detail,
  :deep(.rate-item) {
    background: #33363e;
    border-color: #40434a;
    color: #b0b0b0;
  }

  .detail-title,
  :deep(.rate-value) {
    color: #e0e0e0;
  }

  :deep(.monitor-error) {
    background: #3a2426;
    border-color: #5c3234;
    color: #ef9a9a;
  }

  :deep(.monitor-live-tag) {
    background: #2e3d31;
    color: #81c784;
  }

  :deep(.monitor-freq-info) {
    color: #999;
  }
}

/* ==================== 模拟脚本页 ==================== */
/* 脚本页专属：撑满 tab-content 高度（普通 keys-page 是自然流式布局） */
:deep(.script-page) {
  height: 100%;
}

:deep(.script-layout) {
  display: flex;
  gap: 12px;
  /* 撑满 Tab 内容区剩余高度（不用 vh 魔法数字，避免底部空隙），
     min-height 只防极小窗口下被压扁 */
  flex: 1;
  min-height: 380px;
  padding-bottom: 10px;
  box-sizing: border-box;
}

:deep(.script-screen-panel) {
  /* 宽度由 JS 按画面比例给出，不再占满剩余空间（避免竖屏时左右大片黑框）；
     可用空间不足时允许收缩，保证右侧控制区不被挤掉 */
  flex: 0 1 auto;
  display: flex;
  flex-direction: column;
  background: #fff;
  border: 1px solid #e8eaed;
  border-radius: 12px;
  overflow: hidden;
  /* 面板高度稳定，内部 canvas 不随内容波动 */
  min-width: 0;
}

:deep(.script-screen-header) {
  display: flex;
  align-items: center;
  /* 面板随画面收窄后，头部控件换行而不被裁切 */
  flex-wrap: wrap;
  gap: 10px;
  padding: 10px 14px;
  border-bottom: 1px solid #e8eaed;
  font-size: 13px;
  font-weight: 600;
  color: #333;
}

:deep(.script-ws-status) {
  margin-left: auto;
  font-size: 12px;
  color: #999;
}

:deep(.script-ws-status.live) {
  color: #2e7d32;
}

:deep(.script-rec-tag) {
  color: #e53935;
  font-size: 12px;
  font-weight: 700;
  animation: capturePulse 1.2s ease-in-out infinite;
}

/* 键盘接管提示：点过画面后出现，提示可直接敲键盘打字 */
:deep(.script-kbd-tag) {
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

:deep(.script-kbd-exit) {
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
:deep(.script-switch) {
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

:deep(.script-switch input) {
  position: absolute;
  opacity: 0;
  width: 0;
  height: 0;
}

:deep(.script-switch-track) {
  position: relative;
  display: inline-block;
  width: 32px;
  height: 18px;
  border-radius: 9px;
  background: #d9d9d9;
  transition: background 0.25s ease;
}

:deep(.script-switch-track::after) {
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

:deep(.script-switch.on) {
  color: #2e7d32;
}

:deep(.script-switch.on .script-switch-track) {
  background: #4caf50;
}

:deep(.script-switch.on .script-switch-track::after) {
  transform: translateX(14px);
}

:deep(.script-switch input:focus-visible + .script-switch-track) {
  box-shadow: 0 0 0 3px rgba(76, 175, 80, 0.25);
}

:deep(.script-screen-body) {
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

:deep(.script-canvas) {
  /* 视口内完整可见：宽高都限制在容器内，保持比例 */
  max-width: 100%;
  max-height: 100%;
  width: auto;
  height: auto;
  object-fit: contain;
  cursor: pointer;
}

:deep(.script-canvas.recording) {
  cursor: crosshair;
  outline: 2px solid #e53935;
  outline-offset: -2px;
}

/* 显示步骤时可拖拽标记 */
:deep(.script-canvas.editing-markers) {
  cursor: grab;
}

:deep(.script-canvas.editing-markers.dragging) {
  cursor: grabbing;
}

:deep(.script-screen-placeholder) {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  color: rgba(255, 255, 255, 0.6);
  font-size: 14px;
}

:deep(.script-side-panel) {
  width: 300px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
  min-height: 0;
}

/* 竖屏等场景下预览面板收窄，右侧留白足够时：录制功能区与脚本步骤区左右两列并占满留白。
   触发由 JS 按「布局宽 - 预览宽 - gap」判断，横屏 / 窄窗口自动退回一列 */
:deep(.script-side-panel.two-col) {
  width: auto;
  flex: 1;
  flex-direction: row;
  align-items: stretch;
}

:deep(.script-side-panel.two-col .script-section) {
  flex: 1;
  min-width: 0;
}

:deep(.script-section) {
  background: #fff;
  border: 1px solid #e8eaed;
  border-radius: 12px;
  padding: 12px;
}

/* 步骤功能区标题栏：复用屏幕预览的标题样式，负边距抵消卡片内边距使其通栏 */
:deep(.script-section .script-screen-header) {
  margin: -12px -12px 12px;
}

:deep(.script-record-btn) {
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

:deep(.script-record-btn:hover) {
  background: #d32f2f;
}

:deep(.script-record-btn.active) {
  background: #c62828;
  animation: capturePulse 1.2s ease-in-out infinite;
}

/* 录制中：按钮收窄一半，右侧留出画面校验开关 */
:deep(.script-record-row) {
  display: flex;
  align-items: center;
  gap: 8px;
}

:deep(.script-record-row .script-record-btn) {
  flex: 1 1 0;
  width: auto;
  min-width: 0;
}

:deep(.script-verify-switch) {
  flex: 0 0 calc(50% - 4px);
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  min-width: 0;
  font-size: 12px;
  color: #555;
  white-space: nowrap;
}

:deep(.script-config-row) {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 6px;
  font-size: 12px;
  color: #555;
}

:deep(.script-config-row:last-of-type) {
  margin-bottom: 0;
}

/* 等待组件步骤：已选模板图预览 */
:deep(.script-template-preview-row) {
  align-items: flex-start;
}

:deep(.script-template-preview) {
  max-width: 68%;
  max-height: 120px;
  border: 1px solid #dfe3e8;
  border-radius: 6px;
  background: #fff;
  object-fit: contain;
}

:deep(.script-config-input) {
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
:deep(.script-config-select) {
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
:deep(.ep-config-input) {
  flex: 1;
  min-width: 0;
  max-width: 68%;
}

:deep(.ep-config-select) {
  width: 68%;
}

:deep(.ep-config-num) {
  width: 110px;
}

/* 添加设备动作步骤按钮组 */
:deep(.script-add-steps) {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  margin-top: 8px;
}

:deep(.script-add-label) {
  font-size: 12px;
  color: #888;
  margin-right: 2px;
}

:deep(.script-add-btn) {
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

:deep(.script-add-btn:hover:not(:disabled)) {
  border-color: #4caf50;
  color: #2e7d32;
  background: #f6fbf6;
}

:deep(.script-add-btn:disabled) {
  opacity: 0.4;
  cursor: default;
}

/* 框选模式进行中 */
:deep(.script-add-btn.cropping) {
  border-style: solid;
  border-color: #ff9800;
  color: #e65100;
  background: #fff3e0;
}

:deep(.script-config-hint) {
  margin-top: 6px;
  font-size: 11px;
  color: #999;
}

:deep(.script-list-section) {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
  /* 列表区不收缩到底部按钮被挤出可视区 */
  overflow: hidden;
}

/* 步骤列表弹性占满剩余空间，底栏（保存/执行/重复）始终贴底 */
:deep(.script-list-section .script-actions) {
  margin-top: auto;
  padding-top: 10px;
}

:deep(.script-list-header) {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 13px;
  font-weight: 600;
  color: #333;
  margin-bottom: 8px;
}

:deep(.script-help-icon) {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 14px;
  height: 14px;
  margin-right: 6px;
  border: 1px solid #bbb;
  border-radius: 50%;
  font-size: 11px;
  font-style: normal;
  color: #888;
  cursor: help;
  vertical-align: middle;
}

:deep(.script-help-content) {
  white-space: pre-line;
  line-height: 1.6;
  font-size: 12px;
  max-width: 340px;
}

:deep(.script-header-actions) {
  display: flex;
  gap: 6px;
}

:deep(.script-mini-btn) {
  padding: 3px 10px;
  border: 1px solid #e8eaed;
  border-radius: 6px;
  background: #fff;
  font-size: 12px;
  font-family: inherit;
  color: #666;
  cursor: pointer;
}

:deep(.script-mini-btn:hover:not(:disabled)) {
  border-color: #ef9a9a;
  color: #c62828;
}

:deep(.script-mini-btn:disabled) {
  opacity: 0.4;
  cursor: default;
}

:deep(.script-steps-list) {
  flex: 1;
  overflow-y: auto;
  min-height: 0;
  /* 步骤多时内部滚动，列表不把面板和底栏撑走 */
  scrollbar-width: thin;
}

:deep(.script-steps-list::-webkit-scrollbar) {
  width: 6px;
}

:deep(.script-steps-list::-webkit-scrollbar-thumb) {
  background: #d0d5db;
  border-radius: 3px;
}

:deep(.script-empty) {
  padding: 24px 8px;
  text-align: center;
  color: #999;
  font-size: 12px;
}

/* 步骤分组标题：只影响展示，序号仍是全局执行顺序 */
:deep(.script-step-group-header) {
  padding: 6px 4px 3px;
  font-size: 11px;
  font-weight: 600;
  color: #909399;
}

:deep(.script-step-item) {
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

:deep(.script-step-item:hover) {
  background: #f5f7fa;
}

:deep(.script-step-item.dragging) {
  opacity: 0.45;
}

:deep(.script-step-item.drag-over) {
  background: #e3f2fd;
  box-shadow: inset 0 2px 0 0 #1976d2;
}

:deep(.script-step-item.selected) {
  background: #e8f5e9;
  box-shadow: inset 0 0 0 1px #a5d6a7;
}

/* 展开的单步执行参数 */
:deep(.script-step-config) {
  flex-basis: 100%;
  margin-top: 6px;
  padding: 10px;
  background: #f8faf8;
  border: 1px solid #e0e8e0;
  border-radius: 8px;
}

:deep(.script-step-idx) {
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

:deep(.script-step-desc) {
  flex: 1;
  color: #333;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

:deep(.script-step-meta) {
  flex-shrink: 0;
  color: #999;
  font-size: 11px;
}

:deep(.script-step-del) {
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

:deep(.script-step-del:hover) {
  background: #ffebee;
  color: #c62828;
}

:deep(.script-actions) {
  display: flex;
  gap: 8px;
  margin-top: 10px;
}

:deep(.script-action-btn) {
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

:deep(.script-action-btn:hover:not(:disabled)) {
  background: #43a047;
}

:deep(.script-action-btn:disabled) {
  opacity: 0.5;
  cursor: default;
}

/* 执行中的停止按钮 */
:deep(.script-stop-btn) {
  background: #f44336;
}

:deep(.script-stop-btn:hover:not(:disabled)) {
  background: #d32f2f;
}

/* 模拟脚本页 暗色模式 */
@media (prefers-color-scheme: dark) {
  :deep(.script-step-group-header) {
    color: #6b7079;
  }

  :deep(.script-step-item:hover) {
    background: #33363e;
  }

  :deep(.script-step-item.selected) {
    background: #2e3d31;
    box-shadow: inset 0 0 0 1px #66bb6a;
  }

  :deep(.script-step-desc) {
    color: #e0e0e0;
  }

  :deep(.script-step-config) {
    background: #33363e;
    border-color: #40434a;
  }

  .script-config-text,
  :deep(.script-config-select) {
    background: #3a3d44;
    border-color: #4a4d55;
    color: #e0e0e0;
  }

  :deep(.script-add-btn) {
    background: #33363e;
    border-color: #4a4d55;
    color: #bbb;
  }

  :deep(.script-add-btn:hover:not(:disabled)) {
    border-color: #66bb6a;
    color: #81c784;
    background: #2e3d31;
  }
}

/* ==================== APK 仓库弹窗（应用管理页） ==================== */
:deep(.apps-header-btns) {
  margin-left: auto;
  display: flex;
  gap: 8px;
}

:deep(.apk-repo-mask) {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 200;
}

:deep(.apk-repo-dialog) {
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

:deep(.apk-repo-head) {
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

:deep(.apk-repo-close) {
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

:deep(.apk-repo-close:hover) {
  background: #ffebee;
  color: #c62828;
}

:deep(.apk-repo-body) {
  padding: 14px 16px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  overflow: hidden;
}

:deep(.apk-repo-actions) {
  display: flex;
  align-items: center;
  gap: 10px;
}

:deep(.apk-repo-hint) {
  font-size: 12px;
  color: #999;
}

:deep(.apk-repo-list) {
  flex: 1;
  min-height: 120px;
  max-height: 340px;
  overflow-y: auto;
  border: 1px solid #eef1f4;
  border-radius: 8px;
}

:deep(.apk-repo-empty) {
  padding: 32px;
  text-align: center;
  color: #999;
  font-size: 13px;
}

:deep(.apk-repo-item) {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 12px;
  border-bottom: 1px solid #f5f6f8;
  font-size: 13px;
}

:deep(.apk-repo-item:last-child) {
  border-bottom: none;
}

:deep(.apk-repo-name) {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: #333;
}

:deep(.apk-repo-size) {
  flex-shrink: 0;
  font-size: 12px;
  color: #888;
}

:deep(.apk-repo-install) {
  flex-shrink: 0;
  padding: 3px 12px;
  font-size: 12px;
  background: #fff;
  color: #2e7d32;
  border: 1px solid #a5d6a7;
  border-radius: 4px;
  cursor: pointer;
}

:deep(.apk-repo-install:hover) {
  background: #e8f5e9;
}

:deep(.apk-repo-del) {
  flex-shrink: 0;
  padding: 3px 10px;
  font-size: 12px;
  background: #fff;
  color: #c62828;
  border: 1px solid #ef9a9a;
  border-radius: 4px;
  cursor: pointer;
}

:deep(.apk-repo-del:hover) {
  background: #ffebee;
}

@media (prefers-color-scheme: dark) {
  :deep(.apk-repo-dialog) {
    background: #2b2e35;
  }

  :deep(.apk-repo-head) {
    background: #33363e;
    border-bottom-color: #40434a;
    color: #e0e0e0;
  }

  :deep(.apk-repo-list) {
    border-color: #40434a;
  }

  :deep(.apk-repo-item) {
    border-bottom-color: #383b42;
    color: #ddd;
  }

  :deep(.apk-repo-name) {
    color: #e0e0e0;
  }

  .apk-repo-install,
  :deep(.apk-repo-del) {
    background: #33363e;
  }

  :deep(.apk-repo-install) {
    color: #81c784;
    border-color: #4a6b4d;
  }

  :deep(.apk-repo-del) {
    color: #ef9a9a;
    border-color: #6b4a4a;
  }
}

/* ==================== 模拟串口页 ==================== */
:deep(.serial-row) {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 12px;
}

:deep(.serial-label) {
  font-size: 13px;
  color: #666;
  white-space: nowrap;
}

.serial-select,
:deep(.serial-input) {
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
:deep(.serial-input:focus) {
  outline: none;
  border-color: #4caf50;
  box-shadow: 0 0 0 3px rgba(76, 175, 80, 0.1);
}

.serial-select-sm,
:deep(.serial-input-sm) {
  width: 90px;
}

:deep(.serial-mode-group) {
  display: flex;
  gap: 4px;
}

:deep(.serial-mode-btn) {
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

:deep(.serial-mode-btn:hover) {
  background: #e8f5e9;
  border-color: #a5d6a7;
  color: #2e7d32;
}

:deep(.serial-mode-btn.active) {
  background: #4caf50;
  border-color: #4caf50;
  color: #fff;
}

:deep(.serial-check) {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: #666;
  cursor: pointer;
}

:deep(.serial-mini-btn) {
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

:deep(.serial-mini-btn:hover:not(:disabled)) {
  background: #e8f5e9;
  border-color: #a5d6a7;
  color: #2e7d32;
}

:deep(.serial-mini-btn:disabled) {
  opacity: 0.5;
  cursor: default;
}

:deep(.serial-textarea) {
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

:deep(.serial-textarea:focus) {
  outline: none;
  border-color: #4caf50;
  box-shadow: 0 0 0 3px rgba(76, 175, 80, 0.1);
}

:deep(.probe-actions) {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 10px;
}

:deep(.probe-shell-out) {
  margin-bottom: 10px;
  padding: 10px 12px;
  background: #f5f7fa;
  border: 1px solid #e0e0e0;
  border-radius: 8px;
  font-size: 12px;
  line-height: 1.5;
  font-family: 'Consolas', 'Monaco', monospace;
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 280px;
  overflow: auto;
}

:deep(.serial-actions) {
  display: flex;
  gap: 10px;
  margin-bottom: 10px;
}

:deep(.serial-stream-btn) {
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

:deep(.serial-stream-btn:hover:not(:disabled)) {
  background: #1976d2;
  transform: translateY(-2px);
}

:deep(.serial-stream-btn.active) {
  background: #f44336;
  box-shadow: 0 4px 14px -4px rgba(244, 67, 54, 0.3);
}

:deep(.serial-stream-btn:disabled) {
  background: #bdbdbd;
  box-shadow: none;
  cursor: not-allowed;
  transform: none;
}

:deep(.serial-msg) {
  margin-bottom: 10px;
  padding: 8px 12px;
  border-radius: 6px;
  font-size: 13px;
}

:deep(.serial-msg.ok) {
  background: #e8f5e9;
  color: #2e7d32;
}

:deep(.serial-msg.err) {
  background: #ffebee;
  color: #c62828;
}

:deep(.serial-stream-state) {
  margin-bottom: 10px;
  font-size: 12px;
  color: #1976d2;
}

:deep(.serial-tool-missing) {
  margin-left: auto;
  padding: 2px 8px;
  border-radius: 10px;
  background: #fff3e0;
  color: #ef6c00;
  font-size: 11px;
  font-weight: 500;
}

/* ===== 方案切换 ===== */
:deep(.serial-scheme-group) {
  display: flex;
  gap: 8px;
  margin-bottom: 14px;
  padding-bottom: 12px;
  border-bottom: 1px solid #f0f0f0;
}

:deep(.serial-scheme-btn) {
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

:deep(.serial-scheme-btn:hover) {
  background: #e8f5e9;
  border-color: #a5d6a7;
  color: #2e7d32;
}

:deep(.serial-scheme-btn.active) {
  background: #4caf50;
  border-color: #4caf50;
  color: #fff;
  font-weight: 600;
}

:deep(.serial-input-lg) {
  width: 200px;
}

:deep(.serial-hint) {
  font-size: 12px;
  color: #999;
  line-height: 1.6;
}

:deep(.serial-hint-block) {
  display: block;
  margin-bottom: 14px;
  padding: 10px 12px;
  background: #fff8e1;
  border: 1px solid #ffe082;
  border-radius: 8px;
  line-height: 1.6;
}

/* ===== pty 状态条 ===== */
:deep(.pty-state) {
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

:deep(.pty-state-item b.ok) {
  color: #2e7d32;
}

:deep(.pty-state-item b.warn) {
  color: #ef6c00;
}

:deep(.pty-state-item.err) {
  color: #c62828;
}

/* ===== 按键序列 ===== */
:deep(.serial-key-grid) {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 8px;
}

:deep(.serial-key-btn) {
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

:deep(.serial-key-btn:hover:not(:disabled)) {
  background: #e8f5e9;
  border-color: #a5d6a7;
  transform: translateY(-1px);
  box-shadow: 0 3px 10px rgba(76, 175, 80, 0.12);
}

:deep(.serial-key-btn:active:not(:disabled)) {
  transform: translateY(0);
  background: #c8e6c9;
}

:deep(.serial-key-btn:disabled) {
  opacity: 0.5;
  cursor: default;
}

:deep(.serial-key-btn-label) {
  font-size: 13px;
  font-weight: 600;
  color: #444;
}

:deep(.serial-key-btn-value) {
  font-size: 10px;
  color: #999;
}

/* 已输入的按键序列 */
:deep(.serial-seq-list) {
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

:deep(.serial-seq-empty) {
  font-size: 12px;
  color: #999;
}

:deep(.serial-seq-item) {
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

:deep(.serial-seq-item .serial-seq-idx) {
  font-size: 10px;
  color: #66bb6a;
  font-weight: 700;
}

:deep(.serial-seq-del) {
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

:deep(.serial-seq-del:hover) {
  background: #ffebee;
  color: #c62828;
}

:deep(.serial-output) {
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
  :deep(.serial-input) {
    background: #3a3d45;
    border-color: #555;
    color: #e0e0e0;
  }

  .serial-label,
  :deep(.serial-check) {
    color: #b0b0b0;
  }

  .serial-mode-btn,
  .serial-mini-btn,
  :deep(.serial-scheme-btn) {
    background: #3a3d45;
    border-color: #555;
    color: #ccc;
  }

  .serial-mode-btn.active,
  :deep(.serial-scheme-btn.active) {
    background: #66bb6a;
    border-color: #66bb6a;
    color: #1a1d23;
  }

  :deep(.serial-textarea) {
    background: #3a3d45;
    border-color: #555;
    color: #e0e0e0;
  }

  :deep(.serial-msg.ok) {
    background: #2e3d31;
    color: #81c784;
  }

  :deep(.serial-msg.err) {
    background: #3a2426;
    color: #ef9a9a;
  }

  :deep(.pty-state) {
    background: #33363e;
    border-color: #40434a;
  }

  :deep(.script-switch) {
    color: #999;
  }

  :deep(.script-switch.on) {
    color: #81c784;
  }

  :deep(.script-switch-track) {
    background: #4a4d55;
  }

  :deep(.script-switch.on .script-switch-track) {
    background: #66bb6a;
  }

  :deep(.serial-key-btn) {
    background: #3a3d45;
    border-color: #555;
  }

  :deep(.serial-key-btn:hover:not(:disabled)) {
    background: #2e3d31;
    border-color: #66bb6a;
  }

  :deep(.serial-key-btn-label) {
    color: #e0e0e0;
  }

  :deep(.serial-seq-list) {
    background: #33363e;
    border-color: #40434a;
  }

  :deep(.serial-seq-item) {
    background: #2e3d31;
    border-color: #66bb6a;
    color: #81c784;
  }

  :deep(.serial-seq-del:hover) {
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
              <TabInfo />
              
              <TabApps />
              
              <TabFiles />
              
              <TabKeys />

              <TabBatch />

              <TabScript />

              <TabSerial />

              <TabMonitor />

              <!-- 探针工具：心跳 / TTS / Shell / 截图 / 拍照 合并为一个功能区 -->
              <TabProbe />
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