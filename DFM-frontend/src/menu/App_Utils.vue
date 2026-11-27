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
  if (step.type === 'reboot') return step.waitOnline === false ? '重启设备（不等上线）' : '重启设备并等待上线'
  if (step.type === 'tts') return `语音「${step.text ?? ''}」`
  if (step.type === 'sleep') return `固定延时 ${step.seconds ?? 1}s`
  if (step.type === 'shell') return `Shell「${step.command || '（未填写命令）'}」`
  if (step.type === 'screenshot') return `截图存档${step.label ? ` ${step.label}` : ''}`
  if (step.type === 'runScript') return `执行脚本 ${step.script || '（未选择）'}${step.repeat && step.repeat > 1 ? ` ×${step.repeat} 轮` : ''}`
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
    if (step.kind === 'accClick') {
      const parts = [
        step.id ? `id=${step.id}` : '',
        step.text ? `text=${step.text}` : '',
        step.desc ? `desc=${step.desc}` : '',
      ].filter(Boolean)
      return `${prefix} 无障碍点击 ${parts.join(' ') || '（未设置条件）'}`
    }
    if (step.kind === 'accExists') {
      const parts = [
        step.id ? `id=${step.id}` : '',
        step.text ? `text=${step.text}` : '',
        step.desc ? `desc=${step.desc}` : '',
      ].filter(Boolean)
      return `${prefix} 无障碍组件存在 ${parts.join(' ') || '（未设置条件）'}`
    }
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

  // 可见字符、退格/回车，以及屏幕预览里的左右方向键（映射 Android DPAD_LEFT/RIGHT）；其余交给默认行为
  const key = ev.key
  const isBackspace = key === 'Backspace'
  const isEnter = key === 'Enter'
  const isLeft = key === 'ArrowLeft'
  const isRight = key === 'ArrowRight'
  const isPrintable = key.length === 1 && !ev.ctrlKey && !ev.metaKey && !ev.altKey

  if (!isPrintable && !isBackspace && !isEnter && !isLeft && !isRight) return

  // 吞掉默认行为，避免页面滚动；方向键按住产生的重复事件不重复发送/记录
  ev.preventDefault()
  if ((isLeft || isRight) && ev.repeat) return

  if (isLeft || isRight) {
    scriptFlushTyping()
    const keycode = isLeft ? 21 : 22
    scriptSendKey(isLeft ? 'left' : 'right')
    if (scriptRecording.value) {
      const step = {
        type: 'keyevent' as const,
        keycode,
        interval: scriptOpConfig.value.tap.interval,
        count: 1,
      }
      scriptSteps.value.push(step)
      showNotification(`已记录第 ${scriptSteps.value.length} 步：${scriptStepLabel(step)}`, 'info')
    }
    return
  }

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
const scriptSendKey = (name: 'backspace' | 'enter' | 'left' | 'right', metaState = 0) => {
  if (!scriptWs.value || scriptWs.value.readyState !== WebSocket.OPEN) return
  // Android KeyEvent：BACKSPACE=67，ENTER=66，DPAD_LEFT=21，DPAD_RIGHT=22
  const code = name === 'backspace' ? 67 : name === 'left' ? 21 : name === 'right' ? 22 : 66
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

// 把 adb install 的原始报错映射成中文说明 + 处理建议；未知错误原样保留
const INSTALL_ERROR_TIPS: Array<[string, string]> = [
  ['INSTALL_FAILED_NO_MATCHING_ABIS', '应用与设备 CPU 架构不匹配（APK 缺少设备支持架构的 so 库）：请换用匹配架构的 APK，或更换设备/模拟器'],
  ['INSTALL_FAILED_INSUFFICIENT_STORAGE', '设备存储空间不足：清理空间后重试'],
  ['INSTALL_FAILED_VERSION_DOWNGRADE', '待装版本低于已装版本：请先卸载旧版，或安装更高版本'],
  ['INSTALL_FAILED_SIGNATURE_MISMATCH', '签名与已装版本冲突：请先卸载旧版再安装'],
  ['INSTALL_PARSE_FAILED_INCONSISTENT_CERTIFICATES', '签名与已装版本冲突：请先卸载旧版再安装'],
  ['INSTALL_FAILED_UPDATE_INCOMPATIBLE', '与已装版本不兼容：请先卸载旧版再安装'],
  ['INSTALL_FAILED_PERMISSION_MODEL_DOWNGRADE', '权限模型降级冲突：请先卸载旧版再安装'],
  ['INSTALL_FAILED_TEST_ONLY', 'APK 标记为仅测试（testOnly）：请用 adb install -t 安装，或换正式包'],
  ['INSTALL_FAILED_USER_RESTRICTED', '设备阻止了安装：请在设备弹窗中确认，或关闭“禁止安装未知来源应用”限制'],
  ['INSTALL_PARSE_FAILED_NOT_APK', '文件不是有效的 APK：请检查安装包是否完整'],
  ['INSTALL_FAILED_INVALID_APK', 'APK 文件损坏或格式错误：请重新下载安装包'],
]
const formatInstallError = (raw: string): string => {
  const msg = raw || '未知错误'
  for (const [code, tip] of INSTALL_ERROR_TIPS) {
    if (msg.includes(code)) return `${tip}（错误码：${code}）`
  }
  return msg
}

const installApkFile = async (file: File) => {
  if (!selectedDevice.value) return

  const formData = new FormData()
  formData.append('apk', file)
  formData.append('deviceId', selectedDevice.value)

  showProgressNotification(`正在安装 ${file.name}，请稍候...`)
  try {
    const response = await axios.post(
      `${API_BASE.value}/api/device/app/install`,
      formData,
      { headers: { 'Content-Type': 'multipart/form-data' } }
    )
    hideProgressNotification()
    if (response.data.code === 200) {
      showMessage('应用安装成功')
      fetchApps(selectedDevice.value)
    } else {
      showMessage('应用安装失败: ' + formatInstallError(response.data.message || '未知错误'))
    }
  } catch (error) {
    console.error('安装应用失败:', error)
    hideProgressNotification()
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
      showNotification('安装失败: ' + formatInstallError(response.data.message || '未知错误'), 'error')
    }
  } catch (error: any) {
    showNotification('安装失败: ' + formatInstallError(error?.response?.data?.message || error.message), 'error')
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
  // 重置确认按钮文案：showList/批量 Shell 会改它，不重置会残留「拍照」「执行」等文案
  modalConfirmText.value = '确定'
  modalVisible.value = true
  modalCallback.value = null
};

const showConfirm = (message: string, callback: Function, title: string = '确认') => {
  modalType.value = 'confirm'
  modalTitle.value = title
  modalMessage.value = message
  modalConfirmText.value = '确定'
  modalVisible.value = true
  modalCallback.value = callback
};

const showPrompt = (message: string, callback: Function, placeholder: string = '', title: string = '输入') => {
  modalType.value = 'prompt'
  modalTitle.value = title
  modalMessage.value = message
  modalPlaceholder.value = placeholder
  modalConfirmText.value = '确定'
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
  // 启动探针心跳：probe 被卸载/设备离线后绿点自动变红
  startProbeHeartbeat();

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

const runProbe = async (deviceId: string, install: boolean, silent = false) => {
  if (!deviceId || probeLoadingMap.value[deviceId]) return;
  setProbeBusy(deviceId, true);
  // silent（心跳复查）不打扰当前显示，拿到结果前绿点保持原样
  if (!silent) setProbeState(deviceId, { status: 'busy', message: install ? '安装通讯中...' : '检测中...' });
  try {
    const url = install ? '/api/probe/connect' : '/api/probe/check';
    // 后端安装超时 180s + 连接等待 15s，前端不能在后端还没返回前就判失败
    const res = await axios.post(`${API_BASE.value}${url}`, { deviceId }, { timeout: 200000 });
    const d = res.data;
    const ok = d?.code === 200 && d?.data?.connected === true;
    // 后端报「设备忙」（探针正被 tts/accClick 等长任务占用）时心跳不改判在线状态，绿点保持原样
    if (d?.data?.busy) return;
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

// 探针心跳：定时复查每台设备的 probe 存活状态（/probe/check 不触发安装），
// probe 被卸载/设备离线后绿点能自动变红，而不是永停绿点
let probeHeartbeatTimer: ReturnType<typeof setInterval> | null = null;
const startProbeHeartbeat = () => {
  if (probeHeartbeatTimer) return;
  // 15s 一轮，错开首次执行（设备刚连上时 autoInstallNewDevices 已在检测）
  probeHeartbeatTimer = setInterval(probeHeartbeatTick, 15000);
};
const probeHeartbeatTick = () => {
  for (const d of devices.value) {
    // busy（用户正在安装/检测）的设备不插手，避免和手动操作打架
    if (!d.deviceId || probeLoadingMap.value[d.deviceId]) continue;
    runProbe(d.deviceId, false, true);
  }
};
onUnmounted(() => {
  if (probeHeartbeatTimer) { clearInterval(probeHeartbeatTimer); probeHeartbeatTimer = null; }
});

// 探针工具：心跳 0x00 / TTS 0x01 / Shell 0x08 / 截屏 0x09 / 拍照 0x0A
provide('app-utils', { API_BASE, IMAGE_EXTS, SCRIPT_COMMON_KEYS, TAB_ITEMS, VIDEO_EXTS, activeTab, apkDragOver, apkRepoInputEl, apkRepoLoading, apkRepoOptions, apkRepoUploading, apkRepoVisible, appLoading, apps, autoInstallNewDevices, batchDeviceInfos, batchLoading, batchRemoteCodecFromSps, batchRemoteIsKeyframe, batchSelectedDevices, concatU8, confirmDisconnect, confirmDisconnectAll, connectWireless, currentPath, deviceInfo, devices, disconnectAllLoading, editorFileName, editorInitialContent, editorLoadError, editorLoading, editorSaving, editorTarget, editorVisible, emit, enterAction, fetchApkRepo, fetchApps, fetchDeviceInfo, fetchDevices, fetchFiles, fileLoading, files, formatBytes, formatRepoSize, goHome, handleEditorClose, handleEditorSave, handleImageViewerClose, handleImageViewerNav, handleModalCancel, handleModalConfirm, hideProgressNotification, imageViewerError, imageViewerIndex, imageViewerKind, imageViewerList, imageViewerLoading, imageViewerName, imageViewerSize, imageViewerSrc, imageViewerTotal, imageViewerVisible, installApkFile, installFromRepo, isViewableImageFile, loadBatchDeviceInfos, loadConfig, loadViewerFile, loading, modalCallback, modalConfirmText, modalEmptyText, modalListItems, modalMessage, modalPlaceholder, modalProgress, modalTitle, modalType, modalVariant, modalVisible, onApkDragLeave, onApkDrop, onPageDragOver, onRepoApkChosen, pickRepoApk, previewKindOf, probeKnownDevices, probeLoadingMap, probeStateOf, probeStates, removeRepoApk, retryProbe, route, runProbe, scriptFlushTyping, scriptKeyName, scriptKeyboardActive, scriptOnKeyDown, scriptOpConfig, scriptRecording, scriptSendKey, scriptSendText, scriptStepLabel, scriptSteps, scriptStreaming, scriptTypingIndex, scriptTypingStep, scriptTypingTimer, scriptWs, selectDevice, selectedDevice, selectedFiles, setProbeBusy, setProbeState, showConfirm, showList, showMessage, showNotification, showProgressNotification, showPrompt, startApp, stopApp, uninstallApp, visibleTabs, wirelessIp, wirelessStatus });
</script>
<style scoped src="../styles/utils/utils-shell.css"></style>
<style scoped src="../styles/utils/utils-keys.css"></style>
<style scoped src="../styles/utils/utils-info.css"></style>
<style scoped src="../styles/utils/utils-apps.css"></style>
<style scoped src="../styles/utils/utils-files.css"></style>
<style scoped src="../styles/utils/utils-batch.css"></style>
<style scoped src="../styles/utils/utils-monitor.css"></style>
<style scoped src="../styles/utils/utils-script.css"></style>
<style scoped src="../styles/utils/utils-apk-repo.css"></style>
<style scoped src="../styles/utils/utils-serial.css"></style>
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