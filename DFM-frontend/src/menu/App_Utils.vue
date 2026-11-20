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
const selectedFiles = ref<Set<string>>(new Set())
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

const concatU8 = (a: Uint8Array, b: Uint8Array): Uint8Array => {
  const out = new Uint8Array(a.length + b.length)
  out.set(a, 0)
  out.set(b, a.length)
  return out
}

// 画面适配交给 CSS（见 .batch-remote-tile-body / .batch-remote-canvas）。
// 不做 JS 测高：投屏区是 flex:1 且卡片高度又影响它，测高会形成自反馈，
// 高度一路上涨或读到 0，表现为画面被裁 / 高度不适配。

const scriptStreaming = ref(false)
const scriptRecording = ref(false)
// 画面校验：录制中开启后，每次在画面上的操作都追加一个等待组件步骤
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
const scriptOpConfig = ref<Record<'tap' | 'swipe' | 'longpress', { interval: number; count: number }>>({
  tap: { interval: 500, count: 1 },
  swipe: { interval: 500, count: 1 },
  longpress: { interval: 500, count: 1 },
})
const scriptWs = ref<WebSocket | null>(null)
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
  if (step.type === 'accClick') {
    const parts = [
      step.id ? `id=${step.id}` : '',
      step.text ? `text=${step.text}` : '',
      step.desc ? `desc=${step.desc}` : '',
    ].filter(Boolean)
    return `无障碍点击 ${parts.join(' ') || '（未设置条件）'}`
  }
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

const scriptSendText = (text: string) => {
  if (!text) return
  if (scriptWs.value && scriptWs.value.readyState === WebSocket.OPEN) {
    scriptWs.value.send(JSON.stringify({ type: 'mirror', event: { kind: 'text', text } }))
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
const scriptOnKeyDown = async (ev: KeyboardEvent) => {
  if (!scriptKeyboardActive.value || !scriptStreaming.value) return
  // 正在页面上的输入框里打字时不拦截（右侧输入框、保存弹窗等），否则会吞掉正常输入
  const el = ev.target as HTMLElement | null
  if (el) {
    const tag = el.tagName
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || el.isContentEditable) return
  }
  // Ctrl 组合键：Ctrl+A/C 全选/复制走设备侧注入；Ctrl+V 优先把电脑剪贴板文字打到设备，
  // 读不到剪贴板（非安全上下文/被拒）时退回设备侧粘贴
  if (ev.ctrlKey && !ev.metaKey && !ev.altKey) {
    const k = ev.key.toLowerCase()
    if (k === 'a') { ev.preventDefault(); scriptSendCtrlKey(29); return }
    if (k === 'c') { ev.preventDefault(); scriptSendCtrlKey(31); return }
    if (k === 'v') {
      ev.preventDefault()
      try {
        const text = await navigator.clipboard.readText()
        if (text) { scriptSendText(text); scriptFlushTyping(); return }
      } catch { /* 读剪贴板失败，走设备侧粘贴 */ }
      scriptSendCtrlKey(50)
      return
    }
    // 其余 Ctrl 组合不处理，交给默认行为
    return
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

// 特殊键：走 key 通道（后端 inject_keycode）；metaState 为 Android META_* 修饰键掩码（如 Ctrl=4096）
const scriptSendKey = (name: 'backspace' | 'enter', metaState = 0) => {
  if (!scriptWs.value || scriptWs.value.readyState !== WebSocket.OPEN) return
  // Android KeyEvent：BACKSPACE=67，ENTER=66
  const code = name === 'backspace' ? 67 : 66
  scriptWs.value.send(JSON.stringify({ type: 'mirror', event: { kind: 'key', keycode: code, action: 0, metaState } }))
  scriptWs.value.send(JSON.stringify({ type: 'mirror', event: { kind: 'key', keycode: code, action: 1, metaState } }))
}

// Ctrl 组合键：注入带 META_CTRL_ON 修饰的按键事件（Android 文本框原生支持 Ctrl+A/C/V）
// Android KeyEvent：A=29，C=31，V=50，META_CTRL_ON=4096
const scriptSendCtrlKey = (keycode: number) => {
  if (!scriptWs.value || scriptWs.value.readyState !== WebSocket.OPEN) return
  scriptWs.value.send(JSON.stringify({ type: 'mirror', event: { kind: 'key', keycode, action: 0, metaState: 4096 } }))
  scriptWs.value.send(JSON.stringify({ type: 'mirror', event: { kind: 'key', keycode, action: 1, metaState: 4096 } }))
}


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

const editorVisible = ref(false)
const editorFileName = ref('')
const editorInitialContent = ref('')
const editorLoading = ref(false)
const editorLoadError = ref('')
const editorSaving = ref(false)
// 当前编辑的目标信息（远端路径、本地临时路径）
const editorTarget = ref<{ remotePath: string; localPath: string } | null>(null)

const handleEditorSave = async (newContent: string) => {
  if (!selectedDevice.value || !editorTarget.value) return
  editorSaving.value = true
  try {
    // 3. 用字符串构造 File 走现有上传接口，覆盖回原路径
    const blob = new Blob([newContent], { type: 'text/plain' })
    const formData = new FormData()
    formData.append('file', blob, editorFileName.value)
    formData.append('deviceId', selectedDevice.value)
    const dir = editorTarget.value.remotePath.substring(0, editorTarget.value.remotePath.lastIndexOf('/') + 1) || '/'
    formData.append('remotePath', dir)

    const response = await axios.post(
      `${API_BASE.value}/api/device/files/upload`,
      formData,
      { headers: { 'Content-Type': 'multipart/form-data' } }
    )
    if (response.data.code === 200) {
      showMessage('保存成功，已覆盖设备上的文件')
      editorVisible.value = false
      editorTarget.value = null
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
  editorTarget.value = null
}

// ==================== 图片在线查看 ====================
// 链路：后端 /api/device/files/base64 读出 base64 → 前端拼 data: URL 交给弹窗预览
const IMAGE_EXTS = ['png', 'jpg', 'jpeg', 'gif', 'bmp', 'webp']
const VIDEO_EXTS = ['mp4', 'webm', 'mkv', '3gp', 'mov']
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
const isViewableImageFile = (file: { name: string; isDirectory: boolean; size?: string }) => {
  return previewKindOf(file) === 'image' && isViewableFile(file)
}

// 图片/视频不可预览的具体原因，用于禁用按钮的悬浮提示
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
provide('app-utils', { API_BASE, IMAGE_EXTS, SCRIPT_COMMON_KEYS, TAB_ITEMS, VIDEO_EXTS, activeTab, apkDragOver, apkRepoInputEl, apkRepoLoading, apkRepoOptions, apkRepoUploading, apkRepoVisible, appLoading, apps, autoInstallNewDevices, batchDeviceInfos, batchLoading, batchRemoteCodecFromSps, batchRemoteIsKeyframe, batchSelectedDevices, concatU8, confirmDisconnect, confirmDisconnectAll, connectWireless, currentPath, deviceInfo, devices, disconnectAllLoading, editorFileName, editorInitialContent, editorLoadError, editorLoading, editorSaving, editorTarget, editorVisible, emit, enterAction, fetchApkRepo, fetchApps, fetchDeviceInfo, fetchDevices, fetchFiles, fileLoading, files, formatBytes, formatRepoSize, goHome, handleEditorClose, handleEditorSave, handleImageViewerClose, handleImageViewerNav, handleModalCancel, handleModalConfirm, hideProgressNotification, imageViewerError, imageViewerIndex, imageViewerKind, imageViewerList, imageViewerLoading, imageViewerName, imageViewerSize, imageViewerSrc, imageViewerTotal, imageViewerVisible, installApkFile, installFromRepo, isViewableImageFile, loadBatchDeviceInfos, loadConfig, loadViewerFile, loading, modalCallback, modalConfirmText, modalEmptyText, modalListItems, modalMessage, modalPlaceholder, modalProgress, modalTitle, modalType, modalVariant, modalVisible, onApkDragLeave, onApkDrop, onPageDragOver, onRepoApkChosen, pickRepoApk, previewKindOf, probeKnownDevices, probeLoadingMap, probeStateOf, probeStates, removeRepoApk, retryProbe, route, runProbe, scriptFlushTyping, scriptKeyName, scriptKeyboardActive, scriptOnKeyDown, scriptOpConfig, scriptRecording, scriptSendKey, scriptSendText, scriptStepLabel, scriptSteps, scriptStreaming, scriptTypingIndex, scriptTypingStep, scriptTypingTimer, scriptWs, selectDevice, selectedDevice, selectedFiles, setProbeBusy, setProbeState, showConfirm, showList, showMessage, showNotification, showProgressNotification, showPrompt, startApp, stopApp, uninstallApp, visibleTabs, wirelessIp, wirelessStatus });
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
  /* 定位右缘拖拽热区（.script-width-handle） */
  position: relative;
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

/* ===== 预览宽度拖拽热区 ===== */
:deep(.script-width-handle) {
  position: absolute;
  top: 0;
  right: 0;
  bottom: 0;
  width: 6px;
  cursor: col-resize;
  touch-action: none;
  user-select: none;
  z-index: 2;
}

:deep(.script-width-handle:hover),
:deep(.script-width-handle.dragging) {
  background: rgba(76, 175, 80, 0.35);
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

/* 键盘接管中：画面预览边框变绿（录制中的红色边框优先级更高，放它前面） */
:deep(.script-canvas.keyboard-active) {
  outline: 2px solid #4caf50;
  outline-offset: -2px;
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

/* 组件树获取失败：预览头部小标签（框画在画面 canvas 上，失败不打扰画面） */
:deep(.script-tree-error) {
  padding: 2px 8px;
  border-radius: 10px;
  background: #ffebee;
  color: #c62828;
  font-size: 11px;
  font-weight: 500;
  cursor: default;
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
  flex-direction: column;
  gap: 6px;
  margin-top: 8px;
}

/* 步骤功能区单行：类别标签 + 该类按钮 */
:deep(.script-add-row) {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
}

:deep(.script-add-label) {
  flex-shrink: 0;
  width: 60px;
  font-size: 12px;
  color: #888;
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

/* 单步执行按钮：只执行这一步，验证该步是否满足需求 */
:deep(.script-step-run) {
  flex-shrink: 0;
  width: 20px;
  height: 20px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: none;
  border-radius: 50%;
  background: transparent;
  color: #4caf50;
  font-size: 12px;
  cursor: pointer;
}

:deep(.script-step-run:hover:not(:disabled)) {
  background: #e8f5e9;
  color: #2e7d32;
}

:deep(.script-step-run:disabled) {
  opacity: 0.4;
  cursor: default;
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

  :deep(.script-tree-error) {
    background: #3a2426;
    color: #ef9a9a;
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