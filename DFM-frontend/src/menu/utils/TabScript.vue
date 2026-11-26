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
const { API_BASE, SCRIPT_COMMON_KEYS, activeTab, batchRemoteCodecFromSps, batchRemoteIsKeyframe, concatU8, devices, files, formatBytes, loading, scriptFlushTyping, scriptKeyboardActive, scriptOnKeyDown, scriptOpConfig, scriptRecording, scriptStepLabel, scriptSteps, scriptStreaming, scriptWs, selectedDevice, showList, showMessage, showNotification, showPrompt, startApp, stopApp, uninstallApp } = ctx
const scriptCanvasRef = ref<HTMLCanvasElement | null>(null)
const scriptScreenBodyRef = ref<HTMLElement | null>(null)
const scriptLayoutRef = ref<HTMLElement | null>(null)
const scriptPreviewWidth = ref(0)
const scriptSideTwoCol = ref(false)
// 用户手动调整的预览宽度比例（100 = 按高度自适应的默认宽度），localStorage 持久化
const storedPreviewScale = Number(localStorage.getItem('dfm_script_preview_scale'))
const scriptPreviewScale = ref(Number.isFinite(storedPreviewScale) && storedPreviewScale >= 40 && storedPreviewScale <= 160 ? storedPreviewScale : 100)
// 生效宽度：自适应宽度 × 用户比例；未挂载（自适应宽为 0）时仍为 0，模板回退 100%
const scriptPreviewEffWidth = computed(() => {
  if (scriptPreviewWidth.value <= 0) return 0
  return Math.max(120, Math.round(scriptPreviewWidth.value * scriptPreviewScale.value / 100))
})
// 面板右缘拖拽调宽：按下时记录起点，拖动按像素差换算回比例（热区 6px，1:1 像素跟随）
let scriptWidthDrag: { startX: number; startScale: number; autoW: number } | null = null
const scriptWidthDragging = ref(false)
const applyPreviewScale = (v: number) => {
  const clamped = Math.min(160, Math.max(40, v))
  if (scriptPreviewScale.value === clamped) return
  scriptPreviewScale.value = clamped
  localStorage.setItem('dfm_script_preview_scale', String(clamped))
  syncScriptSideLayout()
}
const onScriptWidthHandleDown = (ev: PointerEvent) => {
  if (ev.button !== 0 || scriptPreviewWidth.value <= 0) return
  ev.preventDefault()
  scriptWidthDrag = { startX: ev.clientX, startScale: scriptPreviewScale.value, autoW: scriptPreviewWidth.value }
  scriptWidthDragging.value = true
  ;(ev.target as HTMLElement).setPointerCapture(ev.pointerId)
}
const onScriptWidthHandleMove = (ev: PointerEvent) => {
  if (!scriptWidthDrag) return
  const dx = ev.clientX - scriptWidthDrag.startX
  applyPreviewScale(Math.round(scriptWidthDrag.startScale + dx / scriptWidthDrag.autoW * 100))
}
const onScriptWidthHandleUp = (ev: PointerEvent) => {
  if (!scriptWidthDrag) return
  scriptWidthDrag = null
  scriptWidthDragging.value = false
  try { (ev.target as HTMLElement).releasePointerCapture(ev.pointerId) } catch { /* ignore */ }
}
let scriptPreviewObserver: ResizeObserver | null = null
const scriptWsStatus = ref('未连接')
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
const scriptRunning = ref(false)
// 每类操作的默认配置（录制时快照进步骤）
const scriptRepeat = ref(1)
const scriptSaving = ref(false)
// 执行结束邮件通知：独立于步骤序列，由右侧列表的「通知」分类控制（添加后才启用）
const scriptNotifyAdded = ref(false)
const scriptNotifyEditing = ref(false)
const scriptNotifyEmail = ref('dfm_mail_service@163.com')

let scriptDecoder: VideoDecoder | null = null
let scriptCodecConfig: Uint8Array | null = null
let scriptMeta = { width: 1080, height: 2340 }

// 右侧留白够放两列时，控制区改成左右两列并占满留白；不足则保持一列 300px。
// 620px 是两列下限：每列不窄于现在一列时的 300px（含 10px 间距），沿用原 1600px 媒体查询的取值。
const syncScriptSideLayout = () => {
  const layout = scriptLayoutRef.value
  if (!layout || layout.clientWidth <= 0 || scriptPreviewEffWidth.value <= 0) return
  // -12 对应 .script-layout 的 gap；预览面板宽度含左右各 1px 边框
  const free = layout.clientWidth - scriptPreviewEffWidth.value - 12
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
  { type: 'accClick', label: '无障碍点击', icon: '🎯' },
  { type: 'runScript', label: '执行脚本', icon: '🧩' },
  { type: 'sleep', label: '固定延时', icon: '⏳' },
  { type: 'shell', label: '执行Shell', icon: '💻' },
  { type: 'screenshot', label: '截图存档', icon: '📸' },
  { type: 'camReplace', label: '替换摄像头画面', icon: '🎥' },
  { type: 'camRestore', label: '恢复真实画面', icon: '📷' },
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

// 判断步骤：六种条件与分支动作
const SCRIPT_CONDITION_KINDS: Array<{ kind: string; label: string }> = [
  { kind: 'appInstalled', label: '应用已安装' },
  { kind: 'template', label: '组件出现' },
  { kind: 'shell', label: 'Shell 输出包含' },
  { kind: 'time', label: '设备时刻已到' },
  { kind: 'accClick', label: '无障碍点击' },
  { kind: 'accExists', label: '无障碍组件存在' },
]
const SCRIPT_BRANCH_ACTIONS: Array<{ action: string; label: string }> = [
  { action: 'continue', label: '继续下一步' },
  { action: 'goto', label: '跳到第 N 步' },
  { action: 'stop', label: '终止脚本' },
  { action: 'fail', label: '判定失败' },
  { action: 'runScript', label: '执行脚本' },
  // 执行左边功能区的某个步骤功能（把该功能当作分支命中后的单步执行）
  ...SCRIPT_GESTURE_STEPS.map((g: any) => ({ action: g.type, label: `执行·${g.label}` })),
  ...SCRIPT_ADB_STEPS.map((a: any) => ({ action: a.type, label: `执行·${a.label}` })),
  { action: 'waitFor', label: '执行·等待组件' },
]

// 分支动作是否需要展示嵌套脚本下拉（仅 runScript）
const branchActionNeedsScript = (branch: any): boolean => branch?.action === 'runScript'

// 分支动作=执行类功能时的默认参数种子（与普通步骤默认值一致；分支手势无画布标记，坐标填数字）
const scriptBranchStepDefaults = (action: string): Record<string, any> => {
  const cx = Math.round(scriptMeta.width / 2)
  const cy = Math.round(scriptMeta.height / 2)
  switch (action) {
    case 'tap': return { x1: cx, y1: cy }
    case 'longpress': return { x1: cx, y1: cy, duration: 1000 }
    case 'swipe': return { x1: cx, y1: cy, x2: cx, y2: Math.min(scriptMeta.height - 1, cy + 300) }
    case 'input': return { text: '' }
    case 'waitFor': return { template: '', threshold: 0.85 }
    // 分支对象的 action 键被分支动作占用，音量子动作存 volumeAction
    case 'volume': return { volumeAction: 'up', stream: 'media' }
    default: return scriptAdbStepDefaults(action)
  }
}

const scriptConditionDefaults = (kind: string): Record<string, any> => {
  switch (kind) {
    case 'appInstalled': return { packageName: '' }
    case 'template': return { template: '', threshold: 0.85 }
    case 'shell': return { command: '', expected: '' }
    case 'time': return { targetTime: '09:00', timeout: 3600 }
    case 'accClick': return { id: '', text: '', desc: '' }
    // accExists 与 accClick 共用 id/text/desc，返回空对象保留已填值
    case 'accExists': return {}
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

// 允许跳到任意步骤（含往前，target 是 1-based 步骤序号）；跳到第 1 步即「从头开始」
const scriptGotoTargets = (idx: number): number[] => {
  const targets: number[] = []
  for (let n = 1; n <= scriptSteps.value.length; n++) targets.push(n)
  return targets
}

const onScriptBranchActionChange = (branch: any, idx: number) => {
  if (branch.action === 'goto') branch.target = 1
  else delete branch.target
  if (branch.action === 'runScript') {
    branch.script = branch.script || ''
    if (scriptSavedOptions.value.length === 0) fetchSavedScriptOptions()
  }
  // TTS 分支朗读文本：进入时确保字段存在。text 为 input/tts/accClick 共用字段，离开时不删，避免误删或切回丢失。
  if (branch.action === 'tts' && branch.text === undefined) branch.text = ''
  // 执行类分支动作：补齐默认参数字段（已有值保留），并按需加载对应下拉数据源
  const defaults = scriptBranchStepDefaults(branch.action)
  for (const key of Object.keys(defaults)) {
    if (branch[key] === undefined) branch[key] = defaults[key]
  }
  if (branch.action === 'installApk' && scriptApkOptions.value.length === 0) fetchScriptApks()
  if (branch.action === 'camReplace' && scriptCamMediaOptions.value.length === 0) fetchScriptCamMedia()
  // video 节点依赖当前设备，每次进入 camReplace 配置都刷新一次
  if (branch.action === 'camReplace') fetchScriptCamDevNodes()
  if (['startApp', 'stopApp', 'uninstallApp'].includes(branch.action) && scriptAppPackages.value.length === 0) fetchScriptApps()
  if (branch.action === 'waitFor' && scriptTemplateOptions.value.length === 0) fetchScriptTemplates()
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

// 摄像头替换素材列表（替换摄像头画面步骤的下拉数据源；图片/视频，存于服务端 media 素材库）
const scriptCamMediaOptions = ref<Array<{ name: string; size: number }>>([])
const scriptCamMediaLoading = ref(false)

const fetchScriptCamMedia = async () => {
  scriptCamMediaLoading.value = true
  try {
    const res = await axios.post(`${API_BASE.value}/api/device/script/list-media`)
    if (res.data.code === 200) scriptCamMediaOptions.value = res.data.data || []
  } catch {
    /* 素材列表拉取失败不阻断脚本编辑 */
  } finally {
    scriptCamMediaLoading.value = false
  }
}

// 上传摄像头替换素材（图片/视频）到服务端素材库，供 camReplace 步骤选择
const uploadScriptCamMedia = () => {
  const input = document.createElement('input')
  input.type = 'file'
  input.accept = 'image/jpeg,image/png,video/*,.mkv'
  input.onchange = async () => {
    const file = input.files?.[0]
    if (!file) return
    scriptCamMediaLoading.value = true
    try {
      const form = new FormData()
      form.append('media', file)
      const res = await axios.post(`${API_BASE.value}/api/device/script/upload-media`, form)
      if (res.data.code !== 200) {
        showNotification('上传素材失败: ' + (res.data.message || '未知错误'), 'error')
        return
      }
      await fetchScriptCamMedia()
      showNotification(`已上传摄像头素材「${res.data.data.name}」`, 'success')
    } catch (error: any) {
      showNotification('上传素材失败: ' + (error?.response?.data?.message || error.message), 'error')
    } finally {
      scriptCamMediaLoading.value = false
    }
  }
  input.click()
}

// video 节点列表（替换摄像头画面步骤的下拉数据源；探针 0x08 关键字 ls /dev/video* 从设备实时获取，
// 不用 0x02 相机列表：USB 相机可能不在 camera2 枚举里，cameraId 与 video 节点号也不一定对应）
const scriptCamDevOptions = ref<string[]>([])
const scriptCamDevLoading = ref(false)

const fetchScriptCamDevNodes = async () => {
  if (!selectedDevice.value) {
    showNotification('请先选择设备后再获取摄像头节点', 'error')
    return
  }
  scriptCamDevLoading.value = true
  try {
    const res = await axios.post(`${API_BASE.value}/api/probe/message`, {
      deviceId: selectedDevice.value,
      cmd: 0x08,
      data: { command: 'ls /dev/video*', root: true, timeout: 30 }
    }, { timeout: 60000 })
    const d = res.data
    if (d?.code !== 200 || d?.data?.connected !== true) throw new Error(d?.message || '通讯失败')
    const r = d.data.response || {}
    if (r.code !== 200) throw new Error(r.message || '设备返回错误')
    const nodes = String(r.data?.stdout || '').match(/\/dev\/video\d+/g) || []
    if (nodes.length === 0) {
      showNotification('设备上未找到 /dev/video* 节点' + (r.data?.stderr ? '：' + r.data.stderr : ''), 'error')
    }
    scriptCamDevOptions.value = nodes
  } catch (error: any) {
    showNotification('获取摄像头节点失败: ' + (error?.response?.data?.message || error.message), 'error')
  } finally {
    scriptCamDevLoading.value = false
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

// 已保存脚本列表（执行脚本步骤与分支动作 runScript 的下拉数据源）
const scriptSavedOptions = ref<Array<{ value: string; label: string; desc: string }>>([])
const scriptSavedLoading = ref(false)

const fetchSavedScriptOptions = async () => {
  scriptSavedLoading.value = true
  try {
    const res = await axios.post(`${API_BASE.value}/api/device/script/list`)
    if (res.data.code === 200) {
      const scripts = res.data.data || []
      // 过滤掉自动生成的临时脚本（current_xxx），它们是执行过程产生的中间产物
      scriptSavedOptions.value = scripts
        .filter((s: any) => !String(s.name).startsWith('current_') && !String(s.name).startsWith('_current_'))
        .map((s: any) => {
          // 子文件夹脚本引用为「path/名字」（相对 scripts 根），下拉展示目录前缀
          const value = s.path ? `${s.path}/${s.name}` : s.name
          return {
            value,
            label: s.path ? `${s.path} / ${s.name}` : s.name,
            desc: `${s.stepCount} 个步骤 · 重复 ${s.repeat} 轮`,
          }
        })
    }
  } catch {
    /* 脚本列表拉取失败不阻断脚本编辑 */
  } finally {
    scriptSavedLoading.value = false
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
    case 'accClick': return { id: '', text: '', desc: '' }
    case 'runScript': return { script: '', repeat: 1 }
    case 'sleep': return { seconds: 3 }
    case 'shell': return { command: '', timeout: 60 }
    case 'screenshot': return { label: '' }
    case 'camReplace': return { file: scriptCamMediaOptions.value[0]?.name || '', dev: '', mirror: false }
    case 'camRestore': return {}
    default: return {}
  }
}

// ---------- 画面上的步骤标记 ----------

// 是否在投屏画面上标出脚本步骤位置
const scriptShowMarkers = ref(false)

// 实时控制：开启后点击画面才会注入操作设备（关闭时点击仅用于查看/编辑，不触达设备）
const scriptLiveControl = ref(false)

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

  // 点开点击/长按/滑动详情页时，画面上单独只显示该步骤的标记（其他步骤不画）
  const editingStep = scriptEditingStep.value !== null ? scriptSteps.value[scriptEditingStep.value] : null
  const focusOnly = !!editingStep && ['tap', 'longpress', 'swipe'].includes(editingStep.type)

  scriptSteps.value.forEach((step, idx) => {
    if (focusOnly && idx !== scriptEditingStep.value) return
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
  scriptDrawTree(ctx)
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
  scriptDrawTree(ctx)
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

// 移动标记到新坐标（夹在画面范围内，拖出屏幕外无效）
const scriptMoveMarker = (target: { stepIndex: number; part: 'start' | 'end' }, x: number, y: number) => {
  const step = scriptSteps.value[target.stepIndex]
  if (!step) return
  const cx = Math.min(Math.max(x, 0), scriptMeta.width)
  const cy = Math.min(Math.max(y, 0), scriptMeta.height)
  if (target.part === 'end' && step.type === 'swipe') {
    step.x2 = cx
    step.y2 = cy
  } else {
    step.x1 = cx
    step.y1 = cy
  }
}

// ---------- 框选模板（等待组件 / 判断步骤「组件出现」的配置行） ----------

// 框选模式：开启后在画面上拖框，松开即把框内画面存为模板
const scriptCropMode = ref(false)
// 正在框选的区域（设备坐标）
const scriptCropRegion = ref<{ x1: number; y1: number; x2: number; y2: number } | null>(null)
// 配置行发起框选时记录目标步骤，保存后回填该步骤的模板，不新增步骤
const scriptCropTargetStep = ref<any>(null)

// 等待组件/判断步骤「组件出现」现截图：沿用框选交互，保存后只回填该步骤的模板
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
  // 不在开头按 canvas 存在与否提前 return：切 Tab 期间 canvas 被 v-if 销毁，
  // 但仍需继续解码以维持 H.264 参考帧链，否则切回后全是无法解码的 P 帧。
  // output 回调动态取当前 canvas（每次可能已被重建）。

  // kind=3：后端应用层心跳（画面静态无新帧时每秒一拍），仅喂狗不解码
  if (kind === 3) return

  if (kind === 1) {
    scriptCodecConfig = payload
    if (scriptDecoder) {
      try { scriptDecoder.close() } catch { /* ignore */ }
    }
    scriptDecoder = new VideoDecoder({
      output: (frame) => {
        const curCv = scriptCanvasRef.value
        // 画布不存在（切到别的 Tab）时只丢弃绘制，帧已解码、参考链不断
        if (!curCv) { frame.close(); return }
        if (curCv.width !== frame.displayWidth) curCv.width = frame.displayWidth
        if (curCv.height !== frame.displayHeight) curCv.height = frame.displayHeight
        const ctx = curCv.getContext('2d')!
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
  if (scriptWs.value && scriptWs.value.readyState !== WebSocket.OPEN && scriptWs.value.readyState !== WebSocket.CONNECTING) {
    try { scriptWs.value.close() } catch { /* ignore */ }
    scriptWs.value = null
  }
  if (scriptWs.value) return

  scriptWsStatus.value = '连接中'
  const wsUrl = API_BASE.value.replace(/^http/, 'ws') + '/ws/remote'
  const thisWs = new WebSocket(wsUrl)
  scriptWs.value = thisWs
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
    if (scriptWs.value === thisWs) {
      scriptWs.value = null
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

// 离开脚本页后保留屏幕预览长连接，5 分钟内切回直接复用；超时才断开
const SCRIPT_STREAM_IDLE_MS = 5 * 60 * 1000
let scriptIdleTimer: ReturnType<typeof setTimeout> | null = null
const cancelScriptIdleTimer = () => {
  if (scriptIdleTimer) { clearTimeout(scriptIdleTimer); scriptIdleTimer = null }
}

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
  // 主动停流：抑制自动重连（同时取消离开 Tab 的空闲断连计时）
  cancelScriptIdleTimer()
  scriptManualStop = true
  if (scriptReconnectTimer) { clearTimeout(scriptReconnectTimer); scriptReconnectTimer = null }
  if (scriptWs.value) {
    try { scriptWs.value.send(JSON.stringify({ type: 'stop' })) } catch { /* ignore */ }
    try { scriptWs.value.close() } catch { /* ignore */ }
    scriptWs.value = null
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
  // 流断开后退出无障碍获取模式，避免按钮状态与实际不符
  finishScriptTreePick()
  // 流断开后不再接管键盘，避免打字继续发往已关闭的连接
  scriptReleaseKeyboard()
}

// 主动启动入口：清除手动停止标记、取消空闲断连计时
const beginScriptStream = () => {
  cancelScriptIdleTimer()
  scriptManualStop = false
  startScriptStream()
}

// 重试：彻底断开当前连接后重新建立（先 stop 清理旧连接/decoder，再 begin 重连）
const retryScriptStream = () => {
  if (!selectedDevice.value) return
  stopScriptStream()
  beginScriptStream()
}

// 离开脚本页：不立即断开，改为 5 分钟后空闲断连；期间切回由 activeTab watch 取消计时并复用连接
const scheduleScriptIdleClose = () => {
  cancelScriptIdleTimer()
  scriptIdleTimer = setTimeout(() => {
    scriptIdleTimer = null
    stopScriptStream()
  }, SCRIPT_STREAM_IDLE_MS)
}

// ---------- 半开 TCP 检测：流"活着"但 10 秒没有任何数据 → 强制断开触发重连 ----------
let scriptLastDataAt = 0
let scriptStallTimer: ReturnType<typeof setInterval> | null = null

const startStallWatch = () => {
  if (scriptStallTimer) return
  scriptLastDataAt = Date.now()
  scriptStallTimer = setInterval(() => {
    // 窗口失焦/最小化时浏览器暂停数据流，期间的"无数据"不是断流，跳过判定
    if (document.hidden) return
    if (!scriptWs.value || !scriptStreaming.value) {
      scriptLastDataAt = Date.now()
      return
    }
    if (Date.now() - scriptLastDataAt > 10000) {
      // 数据流卡死：显示提示并强断，onclose 会触发自动重连
      scriptWsStatus.value = '画面超过 10 秒无数据，重连中...'
      try { scriptWs.value.close() } catch { /* ignore */ }
    }
  }, 2000)
}

const stopStallWatch = () => {
  if (scriptStallTimer) { clearInterval(scriptStallTimer); scriptStallTimer = null }
}

// 窗口从失焦/最小化恢复时喂狗，防止误判断流
const scriptOnVisibilityChange = () => {
  if (!document.hidden && scriptWs.value && scriptStreaming.value) {
    scriptLastDataAt = Date.now()
  }
}

// 进入/离开 Tab 时开关屏幕流（离开不立即断连，保留 5 分钟供切回复用）
watch(activeTab, async (tab) => {
  if (tab === 'script') {
    cancelScriptIdleTimer()
    await nextTick()
    startStallWatch()
    // canvas 被 v-if 重建（尺寸回到默认）：先恢复尺寸再画回缓存帧，
    // 避免静止画面（scrcpy 只发心跳不推帧）时一直黑屏或画面被裁切
    const cv = scriptCanvasRef.value
    if (cv && scriptFrameCanvas) {
      if (cv.width !== scriptFrameCanvas.width) cv.width = scriptFrameCanvas.width
      if (cv.height !== scriptFrameCanvas.height) cv.height = scriptFrameCanvas.height
    }
    scriptRedrawMarkers()
    // 连接仍开着就直接复用，避免每次切回都重连；已断开（含半开被清理后）才重新建连
    if (selectedDevice.value && (!scriptWs.value || scriptWs.value.readyState !== WebSocket.OPEN)) beginScriptStream()
    // 进入脚本页时准备安装APK / 等待组件 / 应用下拉的数据
    if (scriptApkOptions.value.length === 0) fetchScriptApks()
    fetchScriptTemplates()
    fetchScriptApps()
  } else {
    // 保留 stall watch 守护这条长连接：后端 session 死掉时仍能强断，
    // 避免切回时复用到半开连接而卡死
    scheduleScriptIdleClose()
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

// 右键 = 返回键：注入 Android BACK（keycode=4）按下+抬起（对齐 scrcpy 桌面版行为）
const scriptSendBack = () => {
  if (!scriptWs.value || scriptWs.value.readyState !== WebSocket.OPEN) return
  scriptWs.value.send(JSON.stringify({ type: 'mirror', event: { kind: 'key', keycode: 4, action: 0 } }))
  scriptWs.value.send(JSON.stringify({ type: 'mirror', event: { kind: 'key', keycode: 4, action: 1 } }))
}

// 非录制模式下把触控事件直接同步到设备（走 mirror 通道，同批量投屏）
const scriptSendTouch = (action: number, p: { x: number; y: number }) => {
  if (scriptWs.value && scriptWs.value.readyState === WebSocket.OPEN) {
    scriptWs.value.send(JSON.stringify({
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
const scriptReleaseKeyboard = () => {
  scriptKeyboardActive.value = false
  scriptFlushTyping()
}

// 键盘接管时点击屏幕预览以外的区域 → 退出接管并停止键盘监听（capture 抢在画布事件前）
const scriptOnDocPointerDown = (ev: PointerEvent) => {
  if (!scriptKeyboardActive.value) return
  const target = ev.target as HTMLElement | null
  if (target?.closest?.('.script-screen-panel')) return
  scriptReleaseKeyboard()
}

const scriptOnPointerDown = (ev: PointerEvent) => {
  if (!scriptStreaming.value) return
  ev.preventDefault()

  // 右键 = 返回键：注入 BACK，不当作触摸（对齐 scrcpy 桌面版）
  // 实时控制门控：未开启实时控制且非录制时，右键不触达设备
  if (ev.button === 2) {
    if (scriptLiveControl.value || scriptRecording.value) scriptSendBack()
    return
  }

  const p = canvasPoint(ev)

  // 框选模板模式：拖框裁剪画面当作「等待组件出现」的模板，不注入设备
  if (scriptCropMode.value) {
    try { scriptCanvasRef.value?.setPointerCapture(ev.pointerId) } catch { /* ignore */ }
    scriptCropRegion.value = { x1: p.x, y1: p.y, x2: p.x, y2: p.y }
    return
  }

  // 点画面即接管键盘：之后敲电脑键盘的文字会实时发到设备并在录制时记为输入步骤
  // 实时控制门控：未开启实时控制且非录制时不接管键盘（点画面不操作设备）
  if (scriptLiveControl.value || scriptRecording.value) {
    scriptKeyboardActive.value = true
    // 实时控制开关是 INPUT，点它开启后焦点停在上面；点画面 preventDefault 不转移焦点，
    // 导致 keydown 被 scriptOnKeyDown 的 INPUT 排除逻辑吞掉——主动 blur 让输入发往设备
    const focused = document.activeElement as HTMLElement | null
    focused?.blur?.()
    // 点别处意味着上一段输入结束
    scriptFlushTyping()
  }

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
    // 实时控制门控：未开启实时控制时点击不注入设备（仅查看/编辑模式）
    if (!scriptLiveControl.value) return
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

  // 组件树悬停提示：记录命中节点并重绘（不影响下方手势逻辑）
  if (scriptShowTree.value && !scriptPointerActive && !scriptDragging.value) {
    const node = scriptTreeNodeAt(p.x, p.y)
    const prev = scriptTreeHover.value
    if (node !== prev?.node || p.x !== prev?.x || p.y !== prev?.y) {
      scriptTreeHover.value = node ? { x: p.x, y: p.y, node } : null
      scriptRedrawMarkers()
    }
  } else if (scriptTreeHover.value) {
    scriptTreeHover.value = null
    scriptRedrawMarkers()
  }

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

  // 无障碍属性回填：点「获取」进入的获取模式，或原有的展开 accClick 详情自动回填
  // （纯编辑行为：不录新步骤；节点三项完全同步为该组件当前值）
  const pickIdx = scriptTreePickStep.value
  const backfillIdx = pickIdx !== null ? pickIdx : scriptEditingStep.value
  const backfillStep = backfillIdx !== null ? scriptSteps.value[backfillIdx] : null
  const isAccStep = !!backfillStep && (
    backfillStep.type === 'accClick' ||
    (backfillStep.type === 'condition' && (backfillStep.kind === 'accClick' || backfillStep.kind === 'accExists'))
  )
  if (
    !scriptRecording.value &&
    !scriptLiveControl.value &&
    scriptShowTree.value &&
    isAccStep &&
    backfillIdx !== null
  ) {
    const node = scriptTreeNodeAt(p.x, p.y)
    if (node) {
      backfillStep!.id = node.id || ''
      backfillStep!.text = node.text || ''
      backfillStep!.desc = node.desc || ''
      showNotification(`已把组件属性填入第 ${backfillIdx + 1} 步`, 'success')
      // 获取模式：回填完成即退出并恢复组件树原开关状态
      if (pickIdx !== null) finishScriptTreePick()
    } else {
      showNotification('该位置没有命中带 id/text/desc 的组件', 'info')
    }
    return
  }

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
    // 快速点按：组件树开启且命中带 id/text/desc 的节点 → 记无障碍点击（执行时走探针 0x0D）
    // 存储策略：id/text/desc 有值全存；执行端按 全组合→desc→text→id→坐标 依次兜底，
    // 动态文本（如时间组件）回放时会变，靠后续条件与坐标兜底命中
    const accNode = scriptShowTree.value ? scriptTreeNodeAt(scriptPressStart.x, scriptPressStart.y) : null
    let accStep: Record<string, any> | null = null
    if (accNode) {
      const accId = accNode.id || ''
      const accText = accNode.text || ''
      const accDesc = accNode.desc || ''
      if (accId || accText || accDesc) {
        accStep = {
          type: 'accClick',
          id: accId,
          text: accText,
          desc: accDesc,
          x: Math.round(scriptPressStart.x),
          y: Math.round(scriptPressStart.y),
          interval: 500,
          count: 1,
        }
      }
    }
    if (accStep) {
      addRecordedStep(accStep)
    } else {
      // 普通点击步骤
      addRecordedStep({
        type: 'tap',
        x1: scriptPressStart.x, y1: scriptPressStart.y,
        ...scriptOpConfig.value.tap,
      })
    }
  }
  // elapsed >= 600ms 且未移动的长按已在 timer 里记录
}

// 鼠标移出画面：清掉悬停提示
const onScriptPointerLeave = () => {
  if (!scriptTreeHover.value) return
  scriptTreeHover.value = null
  scriptRedrawMarkers()
}

// 指针被中断（取消/离开窗口）：静默结束拖拽；框选中丢弃当前框但保留模式
const scriptOnPointerCancel = () => {  // 手势被中断，丢弃这次按下的截图，避免下一步误用
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
  // 步骤级失败重试是所有类型通用的开关，统一在这里补默认值
  if (step.retry === undefined) step.retry = 0
  if (step.retryInterval === undefined) step.retryInterval = 2000
  scriptSteps.value.push(step)
  showNotification(`已记录第 ${scriptSteps.value.length} 步：${scriptStepLabel(step)}`, 'info')
}

// 添加设备动作类步骤（安装APK/删除文件夹/应用管理/按键/音量/重启/执行脚本）
const addScriptAdbStep = (type: string) => {
  if (type === 'installApk' && scriptApkOptions.value.length === 0) {
    fetchScriptApks()
  }
  // 替换摄像头画面步骤用素材列表做下拉
  if (type === 'camReplace' && scriptCamMediaOptions.value.length === 0) {
    fetchScriptCamMedia()
  }
  // video 节点依赖当前设备，每次添加 camReplace 步骤都刷新一次
  if (type === 'camReplace') {
    fetchScriptCamDevNodes()
  }
  // 应用管理类步骤用应用列表做下拉：进脚本页时可能列表还是旧的（刚卸载过应用），刷新一次
  if (type === 'startApp' || type === 'stopApp' || type === 'uninstallApp') {
    fetchScriptApps()
  }
  // 执行脚本步骤用已保存脚本列表做下拉
  if (type === 'runScript') {
    fetchSavedScriptOptions()
  }
  addScriptStep({
    type,
    ...scriptAdbStepDefaults(type),
    interval: 500,
    count: 1,
  })
  if (type === 'runScript' || type === 'shell' || type === 'screenshot') {
    scriptEditingStep.value = scriptSteps.value.length - 1
  }
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

// 等待组件：直接新增等待步骤并展开参数，模板从已有模板图列表手动选择，或在模板行点「框选」现截
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
  // 获取模式下删除目标步骤（或其之前的步骤会使下标错位）→ 退出并恢复组件树
  if (scriptTreePickStep.value !== null && scriptTreePickStep.value >= idx) finishScriptTreePick()
  scriptSteps.value.splice(idx, 1)
}

const clearScriptSteps = () => {
  finishScriptTreePick()
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
  // 获取模式的目标下标同样跟随被移动的步骤，避免回填到错误步骤
  if (scriptTreePickStep.value !== null) {
    if (scriptTreePickStep.value === from) {
      scriptTreePickStep.value = idx
    } else if (from < scriptTreePickStep.value && idx >= scriptTreePickStep.value) {
      scriptTreePickStep.value -= 1
    } else if (from > scriptTreePickStep.value && idx <= scriptTreePickStep.value) {
      scriptTreePickStep.value += 1
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
  // 后端 load 返回的 steps 已经过 _normalize_steps 标准化，这里整对象透传即可。
  // 之前用字段白名单逐字段拷贝，漏掉了 accClick 的 id/desc/x/y、condition 的
  // kind/onTrue/onFalse、runScript 的 script/repeat 等，导致导入后这些属性消失。
  scriptSteps.value = steps.map((s: any) => ({ ...s }))
  scriptRepeat.value = repeat
  showNotification(`已导入脚本「${name}」，共 ${steps.length} 个步骤`, 'success')
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
      if (d.mediaNames?.length) extras.push(`摄像头素材 ${d.mediaNames.length} 个`)
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
      .map((s: any) => {
        // 子文件夹脚本引用为「path/名字」，载入时拆回 name + path 传给 load 接口
        const value = s.path ? `${s.path}/${s.name}` : s.name
        return {
          value,
          label: s.path ? `${s.path} / ${s.name}` : s.name,
          desc: `${s.stepCount} 个步骤 · 重复 ${s.repeat} 轮`,
        }
      })

    showList(items, async (ref?: string) => {
      if (!ref) return
      try {
        scriptSaving.value = true
        const sep = ref.lastIndexOf('/')
        const payload = sep >= 0 ? { name: ref.slice(sep + 1), path: ref.slice(0, sep) } : { name: ref }
        const loaded = await axios.post(`${API_BASE.value}/api/device/script/load`, payload)
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

// ===== 脚本仓库：VSCode 风格文件夹树（懒加载 + 右键菜单） =====
interface RepoDirNode {
  name: string
  path: string        // 相对 scripts 目录的路径，"" 表示根目录
  isRoot: boolean
  expanded: boolean
  loaded: boolean
  loading: boolean
  dirs: RepoDirNode[]
  scripts: any[]
}
const makeRepoDir = (name: string, path: string, isRoot = false): RepoDirNode => ({
  name, path, isRoot, expanded: isRoot, loaded: false, loading: false, dirs: [], scripts: []
})
const repoRoot = ref<RepoDirNode>(makeRepoDir('脚本仓库', '', true))
const scriptRepoOpen = ref(false)
const scriptRepoLoading = ref(false)
const scriptRepoAction = ref<'create' | 'rename' | 'move' | 'deleteDir' | 'deleteScript' | ''>('')
const scriptRepoInput = ref('')
const scriptRepoTarget = ref<any>(null)      // 文件夹节点或脚本对象
const scriptRepoParent = ref<RepoDirNode | null>(null)  // 操作所在的目录节点
const repoMenu = ref<{ x: number; y: number; row: any } | null>(null)

const repoJoin = (base: string, name: string) => base ? `${base}/${name}` : name

// 懒加载某个文件夹的直接子项（首次展开或强制刷新时请求）
const repoLoadDir = async (node: RepoDirNode, force = false) => {
  if (node.loaded && !force) return
  node.loading = true
  try {
    const d = (await axios.post(`${API_BASE.value}/api/device/script/folder/list`, { path: node.path })).data
    if (d.code !== 200) { showNotification('读取文件夹失败: ' + (d.message || '未知错误'), 'error'); return }
    // 按 path 复用已有子节点，刷新后保留其展开/加载状态
    const oldByPath = new Map(node.dirs.map(c => [c.path, c]))
    node.dirs = (d.data.dirs || []).map((n: string) => {
      const p = repoJoin(node.path, n)
      return oldByPath.get(p) || makeRepoDir(n, p)
    })
    node.scripts = d.data.scripts || []
    node.loaded = true
  } catch (error: any) {
    showNotification('读取文件夹失败: ' + error.message, 'error')
  } finally {
    node.loading = false
  }
}
// 刷新所有已加载节点（操作后保持展开状态地更新）
const repoRefreshLoaded = () => {
  const walk = (node: RepoDirNode) => {
    if (node.loaded) repoLoadDir(node, true)
    node.dirs.forEach(walk)
  }
  walk(repoRoot.value)
}
const openScriptRepo = () => {
  scriptRepoOpen.value = true
  if (!repoRoot.value.loaded) {
    scriptRepoLoading.value = true
    repoLoadDir(repoRoot.value).finally(() => { scriptRepoLoading.value = false })
  } else {
    repoRefreshLoaded()
  }
}
const closeScriptRepo = () => { scriptRepoOpen.value = false; repoMenu.value = null }
const repoToggleDir = (node: RepoDirNode) => {
  node.expanded = !node.expanded
  if (node.expanded) repoLoadDir(node)
}
// 把树拍平成按显示顺序排列的行，供模板单层 v-for 渲染（无需递归组件）
const repoFlatRows = computed(() => {
  const rows: any[] = []
  const walk = (node: RepoDirNode, depth: number, parent: RepoDirNode | null) => {
    rows.push({ kind: 'dir', node, parent, depth })
    if (!node.expanded) return
    for (const child of node.dirs) walk(child, depth + 1, node)
    for (const s of node.scripts) rows.push({ kind: 'script', script: s, parent: node, depth: depth + 1 })
  }
  walk(repoRoot.value, 0, null)
  return rows
})
const openRepoMenu = (ev: MouseEvent, row: any) => {
  repoMenu.value = {
    x: Math.min(ev.clientX, window.innerWidth - 180),
    y: Math.min(ev.clientY, window.innerHeight - 210),
    row
  }
}
const closeRepoMenu = () => { repoMenu.value = null }
const repoStartAction = (action: typeof scriptRepoAction.value, target: any, parent: RepoDirNode | null, input = '') => {
  scriptRepoAction.value = action
  scriptRepoTarget.value = target
  scriptRepoParent.value = parent
  scriptRepoInput.value = input
  repoMenu.value = null
}
const repoCancelAction = () => {
  scriptRepoAction.value = ''
  scriptRepoInput.value = ''
  scriptRepoTarget.value = null
  scriptRepoParent.value = null
}
const repoParentPath = () => scriptRepoParent.value?.path ?? ''

const repoCreateFolder = async () => {
  const name = scriptRepoInput.value.trim()
  if (!name) { showNotification('请输入文件夹名', 'error'); return }
  const parent = scriptRepoParent.value || repoRoot.value
  try {
    const d = (await axios.post(`${API_BASE.value}/api/device/script/folder/create`, { path: parent.path, name })).data
    if (d.code !== 200) { showNotification('创建失败: ' + (d.message || '未知错误'), 'error'); return }
    showNotification('文件夹已创建', 'success')
    parent.expanded = true
    repoCancelAction()
    repoLoadDir(parent, true)
  } catch (error: any) { showNotification('创建失败: ' + error.message, 'error') }
}
const repoDeleteDir = async () => {
  const node = scriptRepoTarget.value as RepoDirNode
  const parent = scriptRepoParent.value || repoRoot.value
  try {
    const d = (await axios.post(`${API_BASE.value}/api/device/script/folder/delete`, { path: parent.path, name: node.name })).data
    if (d.code !== 200) { showNotification('删除失败: ' + (d.message || '未知错误'), 'error'); return }
    showNotification('文件夹已删除', 'success')
    repoCancelAction()
    repoLoadDir(parent, true)
  } catch (error: any) { showNotification('删除失败: ' + error.message, 'error') }
}
const repoLoadScript = async (s: any, parent: RepoDirNode) => {
  try {
    scriptSaving.value = true
    const d = (await axios.post(`${API_BASE.value}/api/device/script/load`, { name: s.fileName, path: parent.path })).data
    if (d.code !== 200) { showNotification('读取脚本失败: ' + (d.message || '未知错误'), 'error'); return }
    applyImportedSteps(d.data.steps, d.data.repeat, d.data.name)
    showNotification(`已载入脚本「${d.data.name}」`, 'success')
    closeScriptRepo()
  } catch (error: any) { showNotification('读取脚本失败: ' + error.message, 'error') }
  finally { scriptSaving.value = false }
}
const repoDeleteScript = async () => {
  const s = scriptRepoTarget.value
  try {
    const d = (await axios.post(`${API_BASE.value}/api/device/script/delete`, { name: s.fileName, path: repoParentPath() })).data
    if (d.code !== 200) { showNotification('删除失败: ' + (d.message || '未知错误'), 'error'); return }
    showNotification('脚本已删除', 'success')
    repoCancelAction()
    repoRefreshLoaded()
  } catch (error: any) { showNotification('删除失败: ' + error.message, 'error') }
}
const repoRenameScript = async () => {
  const newName = scriptRepoInput.value.trim()
  if (!newName) { showNotification('请输入新名称', 'error'); return }
  const s = scriptRepoTarget.value
  try {
    const d = (await axios.post(`${API_BASE.value}/api/device/script/rename`, { path: repoParentPath(), name: s.fileName, newName })).data
    if (d.code !== 200) { showNotification('重命名失败: ' + (d.message || '未知错误'), 'error'); return }
    showNotification('已重命名', 'success')
    repoCancelAction()
    repoRefreshLoaded()
  } catch (error: any) { showNotification('重命名失败: ' + error.message, 'error') }
}
const repoMoveScript = async () => {
  const toPath = scriptRepoInput.value.trim()
  const s = scriptRepoTarget.value
  try {
    const d = (await axios.post(`${API_BASE.value}/api/device/script/move`, { name: s.fileName, fromPath: repoParentPath(), toPath })).data
    if (d.code !== 200) { showNotification('移动失败: ' + (d.message || '未知错误'), 'error'); return }
    showNotification('脚本已移动', 'success')
    repoCancelAction()
    repoRefreshLoaded()
  } catch (error: any) { showNotification('移动失败: ' + error.message, 'error') }
}

// 仓库拖拽移动：脚本行拖到文件夹行上即移动（复用 /script/move）
const repoDragging = ref<{ script: any; parent: RepoDirNode } | null>(null)
const repoDragOverPath = ref<string | null>(null)
const onRepoScriptDragStart = (script: any, parent: RepoDirNode, ev: DragEvent) => {
  repoDragging.value = { script, parent }
  if (ev.dataTransfer) {
    ev.dataTransfer.effectAllowed = 'move'
    // Firefox 需要 setData 才会进入拖拽状态
    ev.dataTransfer.setData('text/plain', script.fileName)
  }
  closeRepoMenu()
}
const onRepoScriptDragEnd = () => {
  repoDragging.value = null
  repoDragOverPath.value = null
}
const onRepoDirDragOver = (node: RepoDirNode, ev: DragEvent) => {
  if (!repoDragging.value) return
  ev.preventDefault()
  if (ev.dataTransfer) ev.dataTransfer.dropEffect = 'move'
  // 拖到源文件夹本身不高亮（移到原地无意义）
  repoDragOverPath.value = repoDragging.value.parent.path === node.path ? null : node.path
}
const onRepoDirDragLeave = (node: RepoDirNode) => {
  if (repoDragOverPath.value === node.path) repoDragOverPath.value = null
}
const onRepoDirDrop = async (node: RepoDirNode, ev: DragEvent) => {
  ev.preventDefault()
  const from = repoDragging.value
  repoDragOverPath.value = null
  repoDragging.value = null
  if (!from || from.parent.path === node.path) return
  try {
    const d = (await axios.post(`${API_BASE.value}/api/device/script/move`, {
      name: from.script.fileName, fromPath: from.parent.path, toPath: node.path,
    })).data
    if (d.code !== 200) { showNotification('移动失败: ' + (d.message || '未知错误'), 'error'); return }
    showNotification(`已将「${from.script.name}」移动到「${node.name}」`, 'success')
    node.expanded = true
    // 目标文件夹可能还没加载过，先加载它（否则展开后看到空目录）
    await repoLoadDir(node, true)
    repoRefreshLoaded()
  } catch (error: any) { showNotification('移动失败: ' + error.message, 'error') }
}

// 仓库右键导出：先选保存目录再导出指定脚本（含子文件夹），不弹二次脚本列表
const repoExportScript = (s: any, parent: RepoDirNode) => {
  closeRepoMenu()
  // uTools 环境用系统目录选择框选保存位置（不默认写死目录）；浏览器开发态回退手输
  const doExport = (targetDir: string) => {
    if (!targetDir || !targetDir.trim()) { showNotification('导出目录不能为空', 'error'); return }
    axios.post(`${API_BASE.value}/api/device/script/export-zip`, {
      name: s.fileName, path: parent.path, targetDir: targetDir.trim(),
    }).then((res) => {
      const d = res.data
      if (d.code !== 200) { showNotification('导出失败: ' + (d.message || '未知错误'), 'error'); return }
      const info = d.data
      const parts = [`脚本「${s.name}」导出成功: ${info.path}`, `大小 ${formatBytes(info.size)}`]
      if (info.apkNames?.length) parts.push(`含 APK：${info.apkNames.join('、')}`)
      if (info.templateNames?.length) parts.push(`含模板图：${info.templateNames.join('、')}`)
      if (info.mediaNames?.length) parts.push(`含摄像头素材：${info.mediaNames.join('、')}`)
      showNotification(parts.join('\n'), 'success')
    }).catch((error: any) => {
      showNotification('导出失败: ' + (error?.response?.data?.message || error.message), 'error')
    })
  }
  if ((window as any).utools && (window as any).utools.showOpenDialog) {
    const selected = (window as any).utools.showOpenDialog({ title: '选择导出目录', properties: ['openDirectory'] })
    if (selected && selected.length > 0) doExport(selected[0])
    return
  }
  showPrompt('请输入导出目录（绝对路径）', (dir: string) => doExport(dir), '例如 D:\\log\\script_exports', '导出脚本')
}

// 添加一个独立的邮箱通知分类（不在执行脚本步骤序列里）
const addScriptNotify = () => {
  if (scriptNotifyAdded.value) return
  scriptNotifyAdded.value = true
  showNotification('已在列表新增「通知」分类', 'success')
}
const removeScriptNotify = () => {
  scriptNotifyAdded.value = false
  scriptNotifyEditing.value = false
}
const toggleNotifyEdit = () => {
  scriptNotifyEditing.value = !scriptNotifyEditing.value
}

// 执行结果邮件通知：通知分类独立于步骤序列，执行结束后自动发信告知成败
const sendRunNotify = async (subject: string, body: string) => {
  if (!scriptNotifyAdded.value || !scriptNotifyEmail.value.trim()) return
  try {
    await axios.post(`${API_BASE.value}/api/notify/email`, {
      to: [scriptNotifyEmail.value.trim()],
      subject,
      body,
    }, { timeout: 30000 })
  } catch (e: any) {
    showNotification('通知邮件发送失败: ' + (e?.response?.data?.message || e.message), 'error')
  }
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
      const reason = response.data.message || '执行失败'
      showNotification(reason, 'error')
      await sendRunNotify('模拟脚本执行失败', `设备：${selectedDevice.value}\n结果：失败\n原因：${reason}`)
      return
    }
    const d = response.data.data || {}
    const summary = `${d.stepCount ?? scriptSteps.value.length} 步 × ${d.repeat ?? scriptRepeat.value} 轮`
    showNotification(`执行完成：${summary}`, 'success')
    await sendRunNotify('模拟脚本执行成功', `设备：${selectedDevice.value}\n结果：成功\n详情：${summary}`)
  } catch (error: any) {
    const reason = '执行失败: ' + (error?.response?.data?.message || error.message)
    showNotification(reason, 'error')
    await sendRunNotify('模拟脚本执行失败', `设备：${selectedDevice.value}\n结果：失败\n原因：${reason}`)
  } finally {
    scriptRunning.value = false
  }
}

// 单步执行：只跑这一步（repeat 固定 1），用于验证某一步是否满足需求
const runScriptStep = async (idx: number) => {
  if (!selectedDevice.value) {
    showMessage('请先选择设备', '提示')
    return
  }
  if (scriptRunning.value) {
    showNotification('已有脚本在执行中', 'error')
    return
  }
  const step = scriptSteps.value[idx]
  if (!step) return
  scriptRunning.value = true
  try {
    const response = await axios.post(`${API_BASE.value}/api/device/script/run-steps`, {
      deviceId: selectedDevice.value,
      steps: [step],
      repeat: 1,
      videoWidth: scriptMeta.width,
      videoHeight: scriptMeta.height,
    }, { timeout: 600000 })
    if (response.data.code !== 200) {
      showNotification(`步骤 ${idx + 1} 执行失败：${response.data.message || '执行失败'}`, 'error')
      return
    }
    showNotification(`步骤 ${idx + 1} 执行通过`, 'success')
  } catch (error: any) {
    showNotification(`步骤 ${idx + 1} 执行失败: ` + (error?.response?.data?.message || error.message), 'error')
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

// 显示组件树：开启后每 1 秒经探针 0x0C 刷新当前窗口组件树（悬浮面板展示）
const scriptShowTree = ref(false)
const scriptTreeInfo = ref<any>(null)
const scriptTreeError = ref('')
const scriptTreeLoading = ref(false)
let scriptTreeTimer: number | null = null

// 无障碍输入框「获取」：临时开启组件树，点选组件回填 id/text/desc 后恢复原开关状态
const scriptTreePickStep = ref<number | null>(null) // 正在获取的目标步骤下标，null 表示未在获取
let scriptTreePickPrevShow = false // 进入获取前组件树是否开启，退出时据此恢复
const startScriptTreePick = (idx: number) => {
  if (scriptTreePickStep.value === idx) { cancelScriptTreePick(); return }
  // 若已在其它步骤的获取模式，先按原基准恢复，再以当前状态作为新基准
  if (scriptTreePickStep.value !== null) finishScriptTreePick()
  scriptTreePickStep.value = idx
  scriptTreePickPrevShow = scriptShowTree.value
  if (!scriptShowTree.value) scriptShowTree.value = true
  showNotification('组件树已开启，请在左侧屏幕预览中点击要获取的无障碍组件', 'info')
}
const finishScriptTreePick = () => {
  if (scriptTreePickStep.value === null) return
  scriptTreePickStep.value = null
  if (!scriptTreePickPrevShow) scriptShowTree.value = false
}
const cancelScriptTreePick = () => {
  const idx = scriptTreePickStep.value
  finishScriptTreePick()
  if (idx !== null) showNotification('已取消获取', 'info')
}
const fetchScriptTree = async () => {
  if (!selectedDevice.value || scriptTreeLoading.value) return
  scriptTreeLoading.value = true
  try {
    const res = await axios.post(`${API_BASE.value}/api/probe/message`, {
      deviceId: selectedDevice.value,
      cmd: 0x0C,
      data: {},
    }, { timeout: 15000 })
    const d = res.data
    if (d?.code !== 200 || d?.data?.connected !== true) throw new Error(d?.message || '通讯失败')
    const r = d.data.response || {}
    if (r.code !== 200) throw new Error(r.message || '设备返回错误')
    scriptTreeInfo.value = r.data || {}
    scriptTreeError.value = ''
    // 树刷新后按最后悬停位置重新命中，避免提示每秒闪烁消失
    if (scriptTreeHover.value) {
      const hx = scriptTreeHover.value.x
      const hy = scriptTreeHover.value.y
      scriptTreeHover.value = null
      const node = scriptTreeNodeAt(hx, hy)
      scriptTreeHover.value = node ? { x: hx, y: hy, node } : null
    }
    // 画面静止时没有新帧，拿到新树后手动重绘一次叠加框
    scriptRedrawMarkers()
  } catch (e: any) {
    const raw = e?.response?.data?.message || e?.message || '请求异常'
    scriptTreeError.value = raw
    scriptTreeInfo.value = null
    // 保底跳页：未启用无障碍权限时设备端已自动拉起设置页，停掉轮询避免反复拉页
    if (raw.includes('已在设备上打开无障碍设置页')) {
      stopScriptTreePolling()
      scriptShowTree.value = false
      scriptTreePickStep.value = null // 权限未开启强制关树，退出获取模式（不恢复树）
    }
  } finally {
    scriptTreeLoading.value = false
  }
}
const stopScriptTreePolling = () => {
  if (scriptTreeTimer !== null) { clearInterval(scriptTreeTimer); scriptTreeTimer = null }
  scriptTreeInfo.value = null
  scriptTreeError.value = ''
  scriptTreeHover.value = null
  // 清掉画面上已画的组件树框
  scriptRedrawMarkers()
}
const startScriptTreePolling = () => {
  stopScriptTreePolling()
  if (!scriptShowTree.value) return
  fetchScriptTree()
  scriptTreeTimer = window.setInterval(fetchScriptTree, 1000)
}
watch([scriptShowTree, selectedDevice], () => {
  if (scriptShowTree.value && selectedDevice.value) startScriptTreePolling()
  else stopScriptTreePolling()
})
// 解析 "l,t,r,b" 原始物理像素矩形，非法/零面积返回 null
const parseRawBounds = (b: string) => {
  const m = /^(-?\d+),(-?\d+),(-?\d+),(-?\d+)$/.exec(b || '')
  if (!m) return null
  const left = +m[1], top = +m[2], right = +m[3], bottom = +m[4]
  if (right <= left || bottom <= top) return null
  return { left, top, width: right - left, height: bottom - top }
}
// 无障碍树 bounds 是设备物理像素，画布是 scrcpy 按 max_size 等比缩小后的尺寸。
// 用根节点 bounds（全屏物理宽）推算缩放比，避免高分辨率设备（如 1080x2400 → 576x1280）
// 的框整体放大、点击命不中；低分辨率设备 scale≈1 不受影响。
const scriptTreeScale = () => {
  const rb = scriptTreeInfo.value?.root ? parseRawBounds(scriptTreeInfo.value.root.bounds) : null
  if (!rb || !scriptMeta.width || rb.width <= 0) return 1
  const scale = scriptMeta.width / rb.width
  return scale > 0 && scale < 1 ? scale : 1
}
// 解析 "l,t,r,b" 并换算到画布坐标系（物理像素 × scale）
const parseTreeBounds = (b: string) => {
  const raw = parseRawBounds(b)
  if (!raw) return null
  const s = scriptTreeScale()
  return { left: raw.left * s, top: raw.top * s, width: raw.width * s, height: raw.height * s }
}
// 全树拍平出可绘制的 bounds 矩形列表
const scriptTreeRects = computed(() => {
  const info = scriptTreeInfo.value
  if (!info?.root) return []
  const rects: { left: number; top: number; width: number; height: number }[] = []
  const walk = (node: any) => {
    if (!node) return
    const rect = parseTreeBounds(node.bounds)
    if (rect) rects.push(rect)
    ;(node.children || []).forEach(walk)
  }
  walk(info.root)
  return rects
})
// 找出包含点 (x, y) 且带 id/text/desc 的最内层节点（悬停提示与录制点击用）
const scriptTreeNodeAt = (x: number, y: number) => {
  const info = scriptTreeInfo.value
  if (!info?.root) return null
  let found: any = null
  const walk = (node: any) => {
    if (!node) return
    const rect = parseTreeBounds(node.bounds)
    if (rect && x >= rect.left && x <= rect.left + rect.width && y >= rect.top && y <= rect.top + rect.height) {
      if (node.id || node.text || node.desc) found = node
      ;(node.children || []).forEach(walk)
    }
  }
  walk(info.root)
  return found
}
// 悬停提示：鼠标所在设备坐标 + 命中的组件节点
const scriptTreeHover = ref<{ x: number; y: number; node: any } | null>(null)
// 把组件树框叠加画在画面上（bounds 已在 parseTreeBounds 换算到画布坐标系）
const scriptDrawTree = (ctx: CanvasRenderingContext2D) => {
  if (!scriptShowTree.value) return
  const rects = scriptTreeRects.value
  if (!rects.length) return
  ctx.save()
  ctx.strokeStyle = '#00e5ff'
  ctx.lineWidth = Math.max(1, Math.round(scriptMeta.width / 400))
  ctx.globalAlpha = 0.9
  for (const rect of rects) ctx.strokeRect(rect.left, rect.top, rect.width, rect.height)
  ctx.restore()

  // 悬停高亮 + 组件信息提示（id/text/desc），画在画布内随画面缩放
  const hover = scriptTreeHover.value
  if (!hover?.node) return
  const rect = parseTreeBounds(hover.node.bounds)
  if (!rect) return
  ctx.save()
  ctx.strokeStyle = '#ff9800'
  ctx.lineWidth = Math.max(2, Math.round(scriptMeta.width / 250))
  ctx.globalAlpha = 0.95
  ctx.strokeRect(rect.left, rect.top, rect.width, rect.height)
  ctx.globalAlpha = 1
  const fontSize = Math.max(16, Math.round(scriptMeta.width / 40))
  ctx.font = `${fontSize}px sans-serif`
  const lines = [
    hover.node.id ? `id: ${hover.node.id}` : '',
    hover.node.text ? `text: ${hover.node.text}` : '',
    hover.node.desc ? `desc: ${hover.node.desc}` : '',
  ].filter(Boolean)
  const pad = Math.round(fontSize * 0.5)
  const lineH = Math.round(fontSize * 1.4)
  let maxW = 0
  for (const line of lines) maxW = Math.max(maxW, ctx.measureText(line).width)
  const boxW = maxW + pad * 2
  const boxH = lines.length * lineH + pad * 2
  // 默认画在悬停点右下，靠边时向左/上翻折，始终留在画面内
  let boxX = hover.x + pad * 2
  let boxY = hover.y + pad * 2
  if (boxX + boxW > scriptMeta.width) boxX = scriptMeta.width - boxW - pad
  if (boxY + boxH > scriptMeta.height) boxY = scriptMeta.height - boxH - pad
  boxX = Math.max(pad, boxX)
  boxY = Math.max(pad, boxY)
  ctx.fillStyle = 'rgba(20, 24, 34, 0.88)'
  ctx.fillRect(boxX, boxY, boxW, boxH)
  ctx.strokeStyle = '#ff9800'
  ctx.lineWidth = 1
  ctx.strokeRect(boxX, boxY, boxW, boxH)
  ctx.fillStyle = '#ffd54f'
  ctx.textAlign = 'left'
  ctx.textBaseline = 'top'
  lines.forEach((line, i) => {
    ctx.fillText(line, boxX + pad, boxY + pad + i * lineH)
  })
  ctx.restore()
}


onMounted(() => {
  window.addEventListener('keydown', scriptOnKeyDown)
  document.addEventListener('pointerdown', scriptOnDocPointerDown, true)
  // 窗口重新可见时立即喂狗：失焦期间 stall 检测被跳过，恢复瞬间先把
  // lastDataAt 拉到当前，避免用停更的旧时间误判 10 秒无数据强断
  document.addEventListener('visibilitychange', scriptOnVisibilityChange)
})
onUnmounted(() => {
  window.removeEventListener('keydown', scriptOnKeyDown)
  document.removeEventListener('pointerdown', scriptOnDocPointerDown, true)
  document.removeEventListener('visibilitychange', scriptOnVisibilityChange)
  scriptPreviewObserver?.disconnect(); scriptPreviewObserver = null; cancelScriptIdleTimer(); stopScriptStream(); stopStallWatch(); stopScriptTreePolling()
})

</script>

<template>
              <div v-if="activeTab === 'script'" class="keys-page script-page">
                <div class="script-layout" ref="scriptLayoutRef">
                  <!-- 左：屏幕流 -->
                  <div class="script-screen-panel" :style="{ width: scriptPreviewEffWidth ? scriptPreviewEffWidth + 'px' : '100%' }">
                    <div class="script-screen-header">
                      <span>屏幕预览</span>
                      <span class="script-ws-status" :class="{ live: scriptStreaming }">{{ scriptWsStatus }}</span>
                      <button
                        class="script-mini-btn"
                        @click="retryScriptStream"
                        :disabled="!selectedDevice"
                        title="断开当前连接并重新连接屏幕预览"
                      >重试</button>
                      <button
                        class="script-mini-btn"
                        @click="scriptWs || scriptStreaming ? stopScriptStream() : beginScriptStream()"
                        :disabled="!selectedDevice"
                        :title="scriptWs || scriptStreaming ? '断开屏幕预览连接' : '连接屏幕预览'"
                      >{{ scriptWs || scriptStreaming ? '断开连接' : '端口连接' }}</button>
                      <span v-if="scriptRecording" class="script-rec-tag">● 录制中</span>
                      <label class="script-switch" :class="{ on: scriptShowTree }">
                        <input type="checkbox" v-model="scriptShowTree" />
                        <span class="script-switch-track"></span>
                        <span class="script-switch-text">组件树</span>
                      </label>
                      <label class="script-switch" :class="{ on: scriptShowMarkers }">
                        <input type="checkbox" v-model="scriptShowMarkers" />
                        <span class="script-switch-track"></span>
                        <span class="script-switch-text">步骤</span>
                      </label>
                      <label class="script-switch" :class="{ on: scriptLiveControl }">
                        <input type="checkbox" v-model="scriptLiveControl" />
                        <span class="script-switch-track"></span>
                        <span class="script-switch-text">实时控制</span>
                      </label>
                    </div>
                    <div class="script-screen-body" ref="scriptScreenBodyRef">
                      <canvas
                        ref="scriptCanvasRef"
                        class="script-canvas"
                        :class="{
                          recording: scriptRecording,
                          'keyboard-active': scriptKeyboardActive,
                          'live-control': scriptLiveControl,
                          'editing-markers': scriptShowMarkers && !scriptRecording,
                          dragging: !!scriptDragging
                        }"
                        @pointerdown="scriptOnPointerDown"
                        @pointermove="scriptOnPointerMove"
                        @pointerup="scriptOnPointerUp"
                        @pointercancel="scriptOnPointerCancel"
                        @pointerleave="onScriptPointerLeave"
                        @contextmenu.prevent
                      ></canvas>
                      <div v-if="!scriptStreaming" class="script-screen-placeholder">
                        {{ selectedDevice ? '正在连接屏幕流...' : '请先选择设备' }}
                      </div>
                      <span v-if="scriptShowTree && scriptTreeError" class="script-tree-error" :title="scriptTreeError">{{ scriptTreeError.includes('已在设备上打开无障碍设置页') ? '无障碍权限未开启，已在设备上打开设置页' : '组件树获取失败' }}</span>
                    </div>
                    <!-- 右缘拖拽热区：按住左右拖动调整面板宽度 -->
                    <div
                      class="script-width-handle"
                      :class="{ dragging: scriptWidthDragging }"
                      title="拖动调整预览宽度"
                      @pointerdown="onScriptWidthHandleDown"
                      @pointermove="onScriptWidthHandleMove"
                      @pointerup="onScriptWidthHandleUp"
                      @pointercancel="onScriptWidthHandleUp"
                    ></div>
                  </div>

                  <!-- 右：控制区 -->
                  <div class="script-side-panel" :class="{ 'two-col': scriptSideTwoCol }">
                    <!-- 上：录制控制 + 添加设备动作步骤 -->
                    <div class="script-section">
                      <div class="script-screen-header"><span>步骤功能</span></div>
                      <div class="script-record-row">
                        <button class="script-record-btn" :class="{ active: scriptRecording }" @click="toggleRecording">
                          {{ scriptRecording ? '■ 结束录制' : '● 开始录制' }}
                        </button>
                        <div v-if="scriptRecording" class="script-verify-switch">
                          <span>画面校验</span>
                          <el-switch v-model="scriptVerify" />
                        </div>
                      </div>
                      <div class="script-add-steps">
                        <div class="script-add-row">
                          <span class="script-add-label">手势操作</span>
                          <button
                            v-for="a in SCRIPT_GESTURE_STEPS"
                            :key="a.type"
                            class="script-add-btn"
                            :disabled="scriptRunning"
                            @click="addScriptGestureStep(a.type)"
                          >{{ a.icon }} {{ a.label }}</button>
                        </div>
                        <div class="script-add-row">
                          <span class="script-add-label">设备动作</span>
                          <button
                            v-for="a in SCRIPT_ADB_STEPS"
                            :key="a.type"
                            class="script-add-btn"
                            :disabled="scriptRunning"
                            @click="addScriptAdbStep(a.type)"
                          >{{ a.icon }} {{ a.label }}</button>
                        </div>
                        <div class="script-add-row">
                          <span class="script-add-label">等待与判断</span>
                          <button
                            class="script-add-btn"
                            :disabled="scriptRunning"
                            title="新增等待组件步骤，在步骤参数里从已有模板中选择，或点「框选」在画面上现截"
                            @click="addScriptWaitStep"
                          >🖼 等待组件</button>
                          <button
                            class="script-add-btn"
                            :disabled="scriptRunning"
                            title="按应用/组件/Shell 输出/设备时刻判断，并按成立与否跳转"
                            @click="addScriptConditionStep"
                          >🧭 判断步骤</button>
                          <button
                            class="script-add-btn"
                            :disabled="scriptRunning"
                            title="新增执行脚本步骤：运行中嵌套执行另一个已保存的脚本"
                            @click="addScriptAdbStep('runScript')"
                          >🧩 执行脚本</button>
                        </div>
                        <div class="script-add-row">
                          <span class="script-add-label">通知</span>
                          <button
                            class="script-add-btn"
                            :disabled="scriptRunning"
                            title="在右侧列表新增独立的通知分类，执行结束后发送结果邮件"
                            @click="addScriptNotify"
                          >✉ 邮箱通知</button>
                        </div>
                      </div>
                    </div>

                    <!-- 下：脚本列表 + 底部按钮 -->
                    <div class="script-section script-list-section">
                      <div class="script-list-header">
                        <span>
                          <el-tooltip placement="bottom-start">
                            <template #content>
                              <div class="script-help-content">{{ SCRIPT_VARIABLE_HELP }}</div>
                            </template>
                            <span class="script-help-icon">i</span>
                          </el-tooltip>脚本步骤（{{ scriptSteps.length }}）</span>
                        <div class="script-header-actions">
                          <button
                            class="script-mini-btn"
                            @click="setAllIntervals"
                            :disabled="scriptSteps.length === 0 || scriptRunning"
                            title="把所有步骤的执行间隔统一设置为同一个值"
                          >统一间隔</button>
                          <button
                            class="script-mini-btn"
                            @click="importScriptFromZip"
                            :disabled="scriptSaving || scriptRunning"
                            title="从导出的 zip 包导入（脚本 + 引用的 APK/模板图/摄像头素材）"
                          >导入</button>
                          <button
                            class="script-mini-btn"
                            @click="openScriptRepo"
                            :disabled="scriptRunning"
                            title="脚本仓库：按文件夹管理已保存的脚本"
                          >仓库</button>
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
                          :draggable="scriptEditingStep !== idx"
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
                          <button class="script-step-run" :disabled="scriptRunning" title="只执行这一步，验证是否满足需求" @click.stop="runScriptStep(idx)">▶</button>
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
                              <button
                                class="script-add-btn"
                                :disabled="!scriptStreaming || scriptRunning"
                                title="在画面上框住组件，保存为模板并设为当前等待步骤的模板"
                                @click="startCropForCondition(step)"
                              >📷 框选</button>
                            </div>
                            <div v-if="step.type === 'waitFor' && step.template && scriptTemplatePreview" class="script-config-row script-template-preview-row">
                              <img
                                :key="scriptTemplatePreview"
                                :src="scriptTemplatePreview"
                                class="script-template-preview"
                                alt="模板预览"
                                @error="onScriptTemplatePreviewError"
                              />
                            </div>
                            <div v-if="step.type === 'waitFor'" class="script-config-row">
                              <span>匹配阈值</span>
                              <el-input-number v-model="step.threshold" class="ep-config-num" :min="0.5" :max="1" :step="0.05" :precision="2" controls-position="right" />
                            </div>
                            <div v-if="step.type === 'waitFor'" class="script-config-row">
                              <span>超时(秒)</span>
                              <el-input-number v-model="step.timeout" class="ep-config-num" :min="1" :max="600" controls-position="right" />
                            </div>
                            <!-- 判断步骤的专属参数 -->
                            <template v-if="step.type === 'condition'">
                              <div class="script-config-row">
                                <span>判断类型</span>
                                <el-select v-model="step.kind" class="ep-config-select" @change="onScriptConditionKindChange(step, $event)">
                                  <el-option v-for="k in SCRIPT_CONDITION_KINDS" :key="k.kind" :value="k.kind" :label="k.label" />
                                </el-select>
                              </div>
                              <div class="script-config-row">
                                <span>条件取反</span>
                                <el-switch v-model="step.negate" />
                              </div>
                              <div v-if="step.kind === 'appInstalled'" class="script-config-row">
                                <span>应用包名</span>
                                <el-input v-model="step.packageName" class="ep-config-input" placeholder="com.example.app" />
                              </div>
                              <div v-if="step.kind === 'template'" class="script-config-row">
                                <span>模板</span>
                                <el-select v-model="step.template" class="ep-config-select" placeholder="选择模板" :loading="scriptTemplateLoading">
                                  <el-option v-for="t in scriptTemplateOptions" :key="t.name" :value="t.name" :label="`${t.name}（${t.width}x${t.height}）`" />
                                </el-select>
                                <button
                                  class="script-add-btn"
                                  :disabled="!scriptStreaming || scriptRunning"
                                  title="在画面上框住组件，保存为模板并设为当前判断步骤的模板"
                                  @click="startCropForCondition(step)"
                                >📷 现截图</button>
                              </div>
                              <div v-if="step.kind === 'template'" class="script-config-row">
                                <span>匹配阈值</span>
                                <el-input-number v-model="step.threshold" class="ep-config-num" :min="0.5" :max="1" :step="0.05" :precision="2" controls-position="right" />
                              </div>
                              <div v-if="step.kind === 'shell'" class="script-config-row">
                                <span>Shell 命令</span>
                                <el-input v-model="step.command" class="ep-config-input" placeholder="getprop ro.build.version.release" />
                              </div>
                              <div v-if="step.kind === 'shell'" class="script-config-row">
                                <span>输出包含</span>
                                <el-input v-model="step.expected" class="ep-config-input" placeholder="13" />
                              </div>
                              <div v-if="step.kind === 'time'" class="script-config-row">
                                <span>目标时刻</span>
                                <el-input v-model="step.targetTime" class="ep-config-input" placeholder="09:30 或 09:30:00" />
                              </div>
                              <div v-if="step.kind === 'time'" class="script-config-row">
                                <span>等待上限(秒)</span>
                                <el-input-number v-model="step.timeout" class="ep-config-num" :min="1" :max="86400" controls-position="right" />
                              </div>
                              <div v-if="step.kind === 'accClick' || step.kind === 'accExists'" class="script-config-row">
                                <span>resource-id</span>
                                <el-input v-model="step.id" class="ep-config-input" placeholder="com.example:id/btn（精确匹配）" />
                                <button class="script-add-btn" :disabled="!scriptStreaming || scriptRunning" :title="scriptTreePickStep === idx ? '取消获取并恢复组件树原状态' : '开启组件树，点选组件自动填入 id/text/desc'" @click="startScriptTreePick(idx)">{{ scriptTreePickStep === idx ? '取消获取' : '获取组件' }}</button>
                              </div>
                              <div v-if="step.kind === 'accClick' || step.kind === 'accExists'" class="script-config-row">
                                <span>文本</span>
                                <el-input v-model="step.text" class="ep-config-input" placeholder="包含匹配，如 确定" />
                              </div>
                              <div v-if="step.kind === 'accClick' || step.kind === 'accExists'" class="script-config-row">
                                <span>内容描述</span>
                                <el-input v-model="step.desc" class="ep-config-input" placeholder="包含匹配，如 提交按钮" />
                              </div>
                              <div class="script-config-row">
                                <span>成立时</span>
                                <el-select v-model="step.onTrue.action" class="ep-config-select" @change="onScriptBranchActionChange(step.onTrue, idx)">
                                  <el-option
                                    v-for="a in SCRIPT_BRANCH_ACTIONS"
                                    :key="a.action"
                                    :value="a.action"
                                    :label="a.label"
                                    :disabled="a.action === 'goto' && scriptGotoTargets(idx).length === 0"
                                  />
                                </el-select>
                              </div>
                              <div v-if="step.onTrue.action === 'goto'" class="script-config-row">
                                <span>跳到</span>
                                <el-select v-model="step.onTrue.target" class="ep-config-select" placeholder="选择目标步骤">
                                  <el-option v-for="n in scriptGotoTargets(idx)" :key="n" :value="n" :label="`第 ${n} 步：${scriptStepLabel(scriptSteps[n - 1])}`" />
                                </el-select>
                              </div>
                              <div v-if="branchActionNeedsScript(step.onTrue)" class="script-config-row">
                                <span>目标脚本</span>
                                <el-select v-model="step.onTrue.script" class="ep-config-select" placeholder="选择已保存的脚本" :loading="scriptSavedLoading" filterable>
                                  <el-option v-for="s in scriptSavedOptions" :key="s.value" :value="s.value" :label="s.label" />
                                </el-select>
                              </div>
                              <div v-if="step.onTrue.action === 'tts'" class="script-config-row">
                                <span>朗读文本</span>
                                <el-input v-model="step.onTrue.text" class="ep-config-input" placeholder="输入设备要朗读的文本" />
                              </div>
                              <!-- 执行类分支动作的专属参数（与普通步骤参数行一致；分支手势无画布标记，坐标用数字填写） -->
                              <div v-if="step.onTrue.action === 'tap' || step.onTrue.action === 'longpress'" class="script-config-row">
                                <span>X / Y 坐标</span>
                                <el-input-number v-model="step.onTrue.x1" class="ep-config-num" :min="0" controls-position="right" />
                                <el-input-number v-model="step.onTrue.y1" class="ep-config-num" :min="0" controls-position="right" />
                              </div>
                              <div v-if="step.onTrue.action === 'longpress'" class="script-config-row">
                                <span>长按时长(ms)</span>
                                <el-input-number v-model="step.onTrue.duration" class="ep-config-num" :min="100" :max="60000" controls-position="right" />
                              </div>
                              <div v-if="step.onTrue.action === 'swipe'" class="script-config-row">
                                <span>起点 X / Y</span>
                                <el-input-number v-model="step.onTrue.x1" class="ep-config-num" :min="0" controls-position="right" />
                                <el-input-number v-model="step.onTrue.y1" class="ep-config-num" :min="0" controls-position="right" />
                              </div>
                              <div v-if="step.onTrue.action === 'swipe'" class="script-config-row">
                                <span>终点 X / Y</span>
                                <el-input-number v-model="step.onTrue.x2" class="ep-config-num" :min="0" controls-position="right" />
                                <el-input-number v-model="step.onTrue.y2" class="ep-config-num" :min="0" controls-position="right" />
                              </div>
                              <div v-if="step.onTrue.action === 'input'" class="script-config-row">
                                <span>输入内容</span>
                                <el-input v-model="step.onTrue.text" class="ep-config-input" placeholder="输入到设备当前聚焦的输入框" />
                              </div>
                              <div v-if="step.onTrue.action === 'waitFor'" class="script-config-row">
                                <span>模板</span>
                                <el-select v-model="step.onTrue.template" class="ep-config-select" placeholder="选择模板" :loading="scriptTemplateLoading">
                                  <el-option v-for="t in scriptTemplateOptions" :key="t.name" :value="t.name" :label="`${t.name}（${t.width}x${t.height}）`" />
                                </el-select>
                              </div>
                              <div v-if="step.onTrue.action === 'waitFor'" class="script-config-row">
                                <span>匹配阈值</span>
                                <el-input-number v-model="step.onTrue.threshold" class="ep-config-num" :min="0.5" :max="1" :step="0.05" :precision="2" controls-position="right" />
                              </div>
                              <div v-if="step.onTrue.action === 'installApk'" class="script-config-row">
                                <span>APK 文件</span>
                                <el-select v-model="step.onTrue.apkName" class="ep-config-select" placeholder="选择 APK" :loading="scriptApkLoading">
                                  <el-option v-for="a in scriptApkOptions" :key="a.name" :value="a.name" :label="a.name" />
                                </el-select>
                              </div>
                              <div v-if="step.onTrue.action === 'camReplace'" class="script-config-row">
                                <span>素材文件</span>
                                <el-select v-model="step.onTrue.file" class="ep-config-select" placeholder="选择图片/视频素材" :loading="scriptCamMediaLoading">
                                  <el-option v-for="m in scriptCamMediaOptions" :key="m.name" :value="m.name" :label="m.name" />
                                </el-select>
                                <button class="script-mini-btn" @click="uploadScriptCamMedia" :disabled="scriptCamMediaLoading" title="上传图片/视频到素材库">上传</button>
                              </div>
                              <div v-if="step.onTrue.action === 'camReplace'" class="script-config-row">
                                <span>video 节点</span>
                                <el-select v-model="step.onTrue.dev" class="ep-config-select" placeholder="自动探测" :loading="scriptCamDevLoading" clearable>
                                  <el-option value="" label="自动探测 /dev/videoN" />
                                  <el-option v-for="n in scriptCamDevOptions" :key="n" :value="n" :label="n" />
                                </el-select>
                                <button class="script-mini-btn" @click="fetchScriptCamDevNodes" :disabled="!selectedDevice || scriptCamDevLoading" title="从设备实时获取 /dev/video* 节点">刷新</button>
                              </div>
                              <div v-if="step.onTrue.action === 'camReplace'" class="script-config-row">
                                <span>镜像补偿</span>
                                <el-checkbox v-model="step.onTrue.mirror">画面左右镜像（预览自带镜像时勾选）</el-checkbox>
                              </div>
                              <div v-if="step.onTrue.action === 'deleteFolder'" class="script-config-row">
                                <span>文件夹路径</span>
                                <el-input v-model="step.onTrue.path" class="ep-config-input" placeholder="/sdcard/xxx" />
                              </div>
                              <div v-if="step.onTrue.action === 'startApp' || step.onTrue.action === 'stopApp' || step.onTrue.action === 'uninstallApp'" class="script-config-row">
                                <span>应用</span>
                                <el-select v-model="step.onTrue.packageName" class="ep-config-select" placeholder="选择应用" filterable :loading="scriptAppLoading">
                                  <el-option v-for="p in scriptAppPackages" :key="p" :value="p" :label="scriptAppNames[p] || p" />
                                </el-select>
                              </div>
                              <div v-if="step.onTrue.action === 'keyevent'" class="script-config-row">
                                <span>按键</span>
                                <el-select :model-value="scriptKeyPresetOf(step.onTrue)" class="ep-config-select" @change="onScriptKeyPresetChange(step.onTrue, $event)">
                                  <el-option v-for="k in SCRIPT_COMMON_KEYS" :key="k.code" :value="String(k.code)" :label="`${k.name}（${k.code}）`" />
                                  <el-option value="custom" label="自定义键值..." />
                                </el-select>
                              </div>
                              <div v-if="step.onTrue.action === 'keyevent' && scriptKeyPresetOf(step.onTrue) === 'custom'" class="script-config-row">
                                <span>键值</span>
                                <el-input-number v-model.number="step.onTrue.keycode" class="ep-config-num" :min="0" :max="65535" controls-position="right" />
                              </div>
                              <div v-if="step.onTrue.action === 'volume'" class="script-config-row">
                                <span>音量动作</span>
                                <el-select v-model="step.onTrue.volumeAction" class="ep-config-select">
                                  <el-option value="up" label="音量+" />
                                  <el-option value="down" label="音量-" />
                                  <el-option value="mute" label="静音" />
                                </el-select>
                              </div>
                              <div v-if="step.onTrue.action === 'volume' && step.onTrue.volumeAction === 'mute'" class="script-config-row">
                                <span>音源</span>
                                <el-select v-model="step.onTrue.stream" class="ep-config-select">
                                  <el-option value="media" label="媒体" />
                                  <el-option value="ring" label="铃声" />
                                  <el-option value="alarm" label="闹钟" />
                                  <el-option value="notification" label="通知" />
                                </el-select>
                              </div>
                              <div v-if="step.onTrue.action === 'accClick'" class="script-config-row">
                                <span>resource-id</span>
                                <el-input v-model="step.onTrue.id" class="ep-config-input" placeholder="com.x:id/btn（精确匹配）" />
                              </div>
                              <div v-if="step.onTrue.action === 'accClick'" class="script-config-row">
                                <span>文本 text</span>
                                <el-input v-model="step.onTrue.text" class="ep-config-input" placeholder="包含匹配，如「登录」" />
                              </div>
                              <div v-if="step.onTrue.action === 'accClick'" class="script-config-row">
                                <span>描述 desc</span>
                                <el-input v-model="step.onTrue.desc" class="ep-config-input" placeholder="content-desc 包含匹配" />
                              </div>
                              <div v-if="step.onTrue.action === 'sleep'" class="script-config-row">
                                <span>延时时长(秒)</span>
                                <el-input-number v-model="step.onTrue.seconds" class="ep-config-num" :min="0.1" :max="3600" :step="0.5" :precision="1" controls-position="right" />
                              </div>
                              <div v-if="step.onTrue.action === 'shell'" class="script-config-row">
                                <span>Shell 命令</span>
                                <el-input v-model="step.onTrue.command" class="ep-config-input" placeholder="在设备 shell 中执行，如 getprop ro.serialno" />
                              </div>
                              <div v-if="step.onTrue.action === 'shell'" class="script-config-row">
                                <span>期望输出</span>
                                <el-input v-model="step.onTrue.expectOutput" class="ep-config-input" placeholder="选填：输出须包含该内容，否则本步失败" />
                              </div>
                              <div v-if="step.onTrue.action === 'shell'" class="script-config-row">
                                <span>命令超时(秒)</span>
                                <el-input-number v-model="step.onTrue.timeout" class="ep-config-num" :min="1" :max="600" controls-position="right" />
                              </div>
                              <div v-if="step.onTrue.action === 'shell'" class="script-config-row">
                                <span>返回码非 0</span>
                                <el-checkbox v-model="step.onTrue.failOnError">判定为失败</el-checkbox>
                              </div>
                              <div v-if="step.onTrue.action === 'screenshot'" class="script-config-row">
                                <span>截图名称</span>
                                <el-input v-model="step.onTrue.label" class="ep-config-input" maxlength="64" placeholder="选填：文件名前缀，如 启动后" />
                              </div>
                              <div v-if="step.onTrue.action === 'reboot'" class="script-config-row">
                                <span>等待上线</span>
                                <el-checkbox v-model="step.onTrue.waitOnline">重启后等设备重新上线</el-checkbox>
                              </div>
                              <div v-if="step.onTrue.action === 'reboot' && step.onTrue.waitOnline !== false" class="script-config-row">
                                <span>上线超时(秒)</span>
                                <el-input-number v-model="step.onTrue.onlineTimeout" class="ep-config-num" :min="10" :max="1800" controls-position="right" />
                              </div>
                              <div class="script-config-row">
                                <span>不成立时</span>
                                <el-select v-model="step.onFalse.action" class="ep-config-select" @change="onScriptBranchActionChange(step.onFalse, idx)">
                                  <el-option
                                    v-for="a in SCRIPT_BRANCH_ACTIONS"
                                    :key="a.action"
                                    :value="a.action"
                                    :label="a.label"
                                    :disabled="a.action === 'goto' && scriptGotoTargets(idx).length === 0"
                                  />
                                </el-select>
                              </div>
                              <div v-if="step.onFalse.action === 'goto'" class="script-config-row">
                                <span>跳到</span>
                                <el-select v-model="step.onFalse.target" class="ep-config-select" placeholder="选择目标步骤">
                                  <el-option v-for="n in scriptGotoTargets(idx)" :key="n" :value="n" :label="`第 ${n} 步：${scriptStepLabel(scriptSteps[n - 1])}`" />
                                </el-select>
                              </div>
                              <div v-if="branchActionNeedsScript(step.onFalse)" class="script-config-row">
                                <span>目标脚本</span>
                                <el-select v-model="step.onFalse.script" class="ep-config-select" placeholder="选择已保存的脚本" :loading="scriptSavedLoading" filterable>
                                  <el-option v-for="s in scriptSavedOptions" :key="s.value" :value="s.value" :label="s.label" />
                                </el-select>
                              </div>
                              <div v-if="step.onFalse.action === 'tts'" class="script-config-row">
                                <span>朗读文本</span>
                                <el-input v-model="step.onFalse.text" class="ep-config-input" placeholder="输入设备要朗读的文本" />
                              </div>
                              <!-- 执行类分支动作的专属参数（与成立时分支一致，绑定到 onFalse） -->
                              <div v-if="step.onFalse.action === 'tap' || step.onFalse.action === 'longpress'" class="script-config-row">
                                <span>X / Y 坐标</span>
                                <el-input-number v-model="step.onFalse.x1" class="ep-config-num" :min="0" controls-position="right" />
                                <el-input-number v-model="step.onFalse.y1" class="ep-config-num" :min="0" controls-position="right" />
                              </div>
                              <div v-if="step.onFalse.action === 'longpress'" class="script-config-row">
                                <span>长按时长(ms)</span>
                                <el-input-number v-model="step.onFalse.duration" class="ep-config-num" :min="100" :max="60000" controls-position="right" />
                              </div>
                              <div v-if="step.onFalse.action === 'swipe'" class="script-config-row">
                                <span>起点 X / Y</span>
                                <el-input-number v-model="step.onFalse.x1" class="ep-config-num" :min="0" controls-position="right" />
                                <el-input-number v-model="step.onFalse.y1" class="ep-config-num" :min="0" controls-position="right" />
                              </div>
                              <div v-if="step.onFalse.action === 'swipe'" class="script-config-row">
                                <span>终点 X / Y</span>
                                <el-input-number v-model="step.onFalse.x2" class="ep-config-num" :min="0" controls-position="right" />
                                <el-input-number v-model="step.onFalse.y2" class="ep-config-num" :min="0" controls-position="right" />
                              </div>
                              <div v-if="step.onFalse.action === 'input'" class="script-config-row">
                                <span>输入内容</span>
                                <el-input v-model="step.onFalse.text" class="ep-config-input" placeholder="输入到设备当前聚焦的输入框" />
                              </div>
                              <div v-if="step.onFalse.action === 'waitFor'" class="script-config-row">
                                <span>模板</span>
                                <el-select v-model="step.onFalse.template" class="ep-config-select" placeholder="选择模板" :loading="scriptTemplateLoading">
                                  <el-option v-for="t in scriptTemplateOptions" :key="t.name" :value="t.name" :label="`${t.name}（${t.width}x${t.height}）`" />
                                </el-select>
                              </div>
                              <div v-if="step.onFalse.action === 'waitFor'" class="script-config-row">
                                <span>匹配阈值</span>
                                <el-input-number v-model="step.onFalse.threshold" class="ep-config-num" :min="0.5" :max="1" :step="0.05" :precision="2" controls-position="right" />
                              </div>
                              <div v-if="step.onFalse.action === 'installApk'" class="script-config-row">
                                <span>APK 文件</span>
                                <el-select v-model="step.onFalse.apkName" class="ep-config-select" placeholder="选择 APK" :loading="scriptApkLoading">
                                  <el-option v-for="a in scriptApkOptions" :key="a.name" :value="a.name" :label="a.name" />
                                </el-select>
                              </div>
                              <div v-if="step.onFalse.action === 'camReplace'" class="script-config-row">
                                <span>素材文件</span>
                                <el-select v-model="step.onFalse.file" class="ep-config-select" placeholder="选择图片/视频素材" :loading="scriptCamMediaLoading">
                                  <el-option v-for="m in scriptCamMediaOptions" :key="m.name" :value="m.name" :label="m.name" />
                                </el-select>
                                <button class="script-mini-btn" @click="uploadScriptCamMedia" :disabled="scriptCamMediaLoading" title="上传图片/视频到素材库">上传</button>
                              </div>
                              <div v-if="step.onFalse.action === 'camReplace'" class="script-config-row">
                                <span>video 节点</span>
                                <el-select v-model="step.onFalse.dev" class="ep-config-select" placeholder="自动探测" :loading="scriptCamDevLoading" clearable>
                                  <el-option value="" label="自动探测 /dev/videoN" />
                                  <el-option v-for="n in scriptCamDevOptions" :key="n" :value="n" :label="n" />
                                </el-select>
                                <button class="script-mini-btn" @click="fetchScriptCamDevNodes" :disabled="!selectedDevice || scriptCamDevLoading" title="从设备实时获取 /dev/video* 节点">刷新</button>
                              </div>
                              <div v-if="step.onFalse.action === 'camReplace'" class="script-config-row">
                                <span>镜像补偿</span>
                                <el-checkbox v-model="step.onFalse.mirror">画面左右镜像（预览自带镜像时勾选）</el-checkbox>
                              </div>
                              <div v-if="step.onFalse.action === 'deleteFolder'" class="script-config-row">
                                <span>文件夹路径</span>
                                <el-input v-model="step.onFalse.path" class="ep-config-input" placeholder="/sdcard/xxx" />
                              </div>
                              <div v-if="step.onFalse.action === 'startApp' || step.onFalse.action === 'stopApp' || step.onFalse.action === 'uninstallApp'" class="script-config-row">
                                <span>应用</span>
                                <el-select v-model="step.onFalse.packageName" class="ep-config-select" placeholder="选择应用" filterable :loading="scriptAppLoading">
                                  <el-option v-for="p in scriptAppPackages" :key="p" :value="p" :label="scriptAppNames[p] || p" />
                                </el-select>
                              </div>
                              <div v-if="step.onFalse.action === 'keyevent'" class="script-config-row">
                                <span>按键</span>
                                <el-select :model-value="scriptKeyPresetOf(step.onFalse)" class="ep-config-select" @change="onScriptKeyPresetChange(step.onFalse, $event)">
                                  <el-option v-for="k in SCRIPT_COMMON_KEYS" :key="k.code" :value="String(k.code)" :label="`${k.name}（${k.code}）`" />
                                  <el-option value="custom" label="自定义键值..." />
                                </el-select>
                              </div>
                              <div v-if="step.onFalse.action === 'keyevent' && scriptKeyPresetOf(step.onFalse) === 'custom'" class="script-config-row">
                                <span>键值</span>
                                <el-input-number v-model.number="step.onFalse.keycode" class="ep-config-num" :min="0" :max="65535" controls-position="right" />
                              </div>
                              <div v-if="step.onFalse.action === 'volume'" class="script-config-row">
                                <span>音量动作</span>
                                <el-select v-model="step.onFalse.volumeAction" class="ep-config-select">
                                  <el-option value="up" label="音量+" />
                                  <el-option value="down" label="音量-" />
                                  <el-option value="mute" label="静音" />
                                </el-select>
                              </div>
                              <div v-if="step.onFalse.action === 'volume' && step.onFalse.volumeAction === 'mute'" class="script-config-row">
                                <span>音源</span>
                                <el-select v-model="step.onFalse.stream" class="ep-config-select">
                                  <el-option value="media" label="媒体" />
                                  <el-option value="ring" label="铃声" />
                                  <el-option value="alarm" label="闹钟" />
                                  <el-option value="notification" label="通知" />
                                </el-select>
                              </div>
                              <div v-if="step.onFalse.action === 'accClick'" class="script-config-row">
                                <span>resource-id</span>
                                <el-input v-model="step.onFalse.id" class="ep-config-input" placeholder="com.x:id/btn（精确匹配）" />
                              </div>
                              <div v-if="step.onFalse.action === 'accClick'" class="script-config-row">
                                <span>文本 text</span>
                                <el-input v-model="step.onFalse.text" class="ep-config-input" placeholder="包含匹配，如「登录」" />
                              </div>
                              <div v-if="step.onFalse.action === 'accClick'" class="script-config-row">
                                <span>描述 desc</span>
                                <el-input v-model="step.onFalse.desc" class="ep-config-input" placeholder="content-desc 包含匹配" />
                              </div>
                              <div v-if="step.onFalse.action === 'sleep'" class="script-config-row">
                                <span>延时时长(秒)</span>
                                <el-input-number v-model="step.onFalse.seconds" class="ep-config-num" :min="0.1" :max="3600" :step="0.5" :precision="1" controls-position="right" />
                              </div>
                              <div v-if="step.onFalse.action === 'shell'" class="script-config-row">
                                <span>Shell 命令</span>
                                <el-input v-model="step.onFalse.command" class="ep-config-input" placeholder="在设备 shell 中执行，如 getprop ro.serialno" />
                              </div>
                              <div v-if="step.onFalse.action === 'shell'" class="script-config-row">
                                <span>期望输出</span>
                                <el-input v-model="step.onFalse.expectOutput" class="ep-config-input" placeholder="选填：输出须包含该内容，否则本步失败" />
                              </div>
                              <div v-if="step.onFalse.action === 'shell'" class="script-config-row">
                                <span>命令超时(秒)</span>
                                <el-input-number v-model="step.onFalse.timeout" class="ep-config-num" :min="1" :max="600" controls-position="right" />
                              </div>
                              <div v-if="step.onFalse.action === 'shell'" class="script-config-row">
                                <span>返回码非 0</span>
                                <el-checkbox v-model="step.onFalse.failOnError">判定为失败</el-checkbox>
                              </div>
                              <div v-if="step.onFalse.action === 'screenshot'" class="script-config-row">
                                <span>截图名称</span>
                                <el-input v-model="step.onFalse.label" class="ep-config-input" maxlength="64" placeholder="选填：文件名前缀，如 启动后" />
                              </div>
                              <div v-if="step.onFalse.action === 'reboot'" class="script-config-row">
                                <span>等待上线</span>
                                <el-checkbox v-model="step.onFalse.waitOnline">重启后等设备重新上线</el-checkbox>
                              </div>
                              <div v-if="step.onFalse.action === 'reboot' && step.onFalse.waitOnline !== false" class="script-config-row">
                                <span>上线超时(秒)</span>
                                <el-input-number v-model="step.onFalse.onlineTimeout" class="ep-config-num" :min="10" :max="1800" controls-position="right" />
                              </div>
                            </template>
                            <!-- 设备动作类步骤的专属参数 -->
                            <div v-if="step.type === 'installApk'" class="script-config-row">
                              <span>APK 文件</span>
                              <el-select v-model="step.apkName" class="ep-config-select" placeholder="选择 APK" :loading="scriptApkLoading">
                                <el-option v-for="a in scriptApkOptions" :key="a.name" :value="a.name" :label="a.name" />
                              </el-select>
                            </div>
                            <div v-if="step.type === 'camReplace'" class="script-config-row">
                              <span>素材文件</span>
                              <el-select v-model="step.file" class="ep-config-select" placeholder="选择图片/视频素材" :loading="scriptCamMediaLoading">
                                <el-option v-for="m in scriptCamMediaOptions" :key="m.name" :value="m.name" :label="m.name" />
                              </el-select>
                              <button class="script-mini-btn" @click="uploadScriptCamMedia" :disabled="scriptCamMediaLoading" title="上传图片/视频到素材库">上传</button>
                            </div>
                            <div v-if="step.type === 'camReplace'" class="script-config-row">
                              <span>video 节点</span>
                              <el-select v-model="step.dev" class="ep-config-select" placeholder="自动探测" :loading="scriptCamDevLoading" clearable>
                                <el-option value="" label="自动探测 /dev/videoN" />
                                <el-option v-for="n in scriptCamDevOptions" :key="n" :value="n" :label="n" />
                              </el-select>
                              <button class="script-mini-btn" @click="fetchScriptCamDevNodes" :disabled="!selectedDevice || scriptCamDevLoading" title="从设备实时获取 /dev/video* 节点">刷新</button>
                            </div>
                            <div v-if="step.type === 'camReplace'" class="script-config-row">
                              <span>镜像补偿</span>
                              <el-checkbox v-model="step.mirror">画面左右镜像（预览自带镜像时勾选）</el-checkbox>
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
                            <div v-if="step.type === 'tts'" class="script-config-row">
                              <span>朗读文本</span>
                              <el-input v-model="step.text" class="ep-config-input" placeholder="输入设备要朗读的文本" />
                            </div>
                            <div v-if="step.type === 'accClick'" class="script-config-row">
                              <span>resource-id</span>
                              <el-input v-model="step.id" class="ep-config-input" placeholder="com.x:id/btn（精确匹配）" />
                              <button class="script-add-btn" :disabled="!scriptStreaming || scriptRunning" :title="scriptTreePickStep === idx ? '取消获取并恢复组件树原状态' : '开启组件树，点选组件自动填入 id/text/desc'" @click="startScriptTreePick(idx)">{{ scriptTreePickStep === idx ? '取消获取' : '获取组件' }}</button>
                            </div>
                            <div v-if="step.type === 'accClick'" class="script-config-row">
                              <span>文本 text</span>
                              <el-input v-model="step.text" class="ep-config-input" placeholder="包含匹配，如「登录」" />
                            </div>
                            <div v-if="step.type === 'accClick'" class="script-config-row">
                              <span>描述 desc</span>
                              <el-input v-model="step.desc" class="ep-config-input" placeholder="content-desc 包含匹配" />
                            </div>
                            <div v-if="step.type === 'runScript'" class="script-config-row">
                              <span>目标脚本</span>
                              <el-select v-model="step.script" class="ep-config-select" placeholder="选择已保存的脚本" :loading="scriptSavedLoading" filterable>
                                <el-option v-for="s in scriptSavedOptions" :key="s.value" :value="s.value" :label="s.label" />
                              </el-select>
                            </div>
                            <div v-if="step.type === 'runScript'" class="script-config-row">
                              <span>执行轮数</span>
                              <el-input-number v-model="step.repeat" class="ep-config-num" :min="1" :max="99" controls-position="right" />
                            </div>
                            <div v-if="step.type === 'sleep'" class="script-config-row">
                              <span>延时时长(秒)</span>
                              <el-input-number v-model="step.seconds" class="ep-config-num" :min="0.1" :max="3600" :step="0.5" :precision="1" controls-position="right" />
                            </div>
                            <div v-if="step.type === 'shell'" class="script-config-row">
                              <span>Shell 命令</span>
                              <el-input v-model="step.command" class="ep-config-input" placeholder="在设备 shell 中执行，如 getprop ro.serialno" />
                            </div>
                            <div v-if="step.type === 'shell'" class="script-config-row">
                              <span>期望输出</span>
                              <el-input v-model="step.expectOutput" class="ep-config-input" placeholder="选填：输出须包含该内容，否则本步失败" />
                            </div>
                            <div v-if="step.type === 'shell'" class="script-config-row">
                              <span>命令超时(秒)</span>
                              <el-input-number v-model="step.timeout" class="ep-config-num" :min="1" :max="600" controls-position="right" />
                            </div>
                            <div v-if="step.type === 'shell'" class="script-config-row">
                              <span>返回码非 0</span>
                              <el-checkbox v-model="step.failOnError">判定为失败</el-checkbox>
                            </div>
                            <div v-if="step.type === 'screenshot'" class="script-config-row">
                              <span>截图名称</span>
                              <el-input v-model="step.label" class="ep-config-input" maxlength="64" placeholder="选填：文件名前缀，如 启动后" />
                            </div>
                            <div v-if="step.type === 'reboot'" class="script-config-row">
                              <span>等待上线</span>
                              <el-checkbox v-model="step.waitOnline">重启后等设备重新上线</el-checkbox>
                            </div>
                            <div v-if="step.type === 'reboot' && step.waitOnline !== false" class="script-config-row">
                              <span>上线超时(秒)</span>
                              <el-input-number v-model="step.onlineTimeout" class="ep-config-num" :min="10" :max="1800" controls-position="right" />
                            </div>
                            <div class="script-config-row">
                              <span>失败重试(次)</span>
                              <el-input-number v-model="step.retry" class="ep-config-num" :min="0" :max="5" controls-position="right" />
                            </div>
                            <div v-if="step.retry > 0" class="script-config-row">
                              <span>重试间隔(ms)</span>
                              <el-input-number v-model="step.retryInterval" class="ep-config-num" :min="0" :max="600000" controls-position="right" />
                            </div>
                            <div class="script-config-row">
                              <span>{{ step.type === 'condition' ? '轮询/步间(ms)' : '执行间隔(ms)' }}</span>
                              <el-input-number v-model="step.interval" class="ep-config-num" :min="0" :max="600000" controls-position="right" />
                            </div>
                            <div v-if="step.type !== 'condition'" class="script-config-row">
                              <span>执行次数</span>
                              <el-input-number v-model="step.count" class="ep-config-num" :min="1" :max="99" controls-position="right" />
                            </div>
                          </div>
                          </div>
                      </div>

                      <!-- 通知分类：与脚本步骤分开的独立列表，点击功能区「邮箱通知」后新增 -->
                      <div v-if="scriptNotifyAdded" class="script-notify-section">
                        <div class="script-list-header">
                          <span>通知</span>
                        </div>
                        <div class="script-notify-list">
                          <div
                            class="script-step-item"
                            :class="{ selected: scriptNotifyEditing }"
                            @click="toggleNotifyEdit"
                          >
                            <span class="script-step-idx">✉</span>
                            <span class="script-step-desc">邮箱通知</span>
                            <button class="script-step-del" title="移除邮箱通知" @click.stop="removeScriptNotify">×</button>
                            <!-- 点开后展开该通知的配置 -->
                            <div v-if="scriptNotifyEditing" class="script-step-config" @click.stop>
                              <div class="script-config-row">
                                <span>收件邮箱</span>
                                <el-input
                                  v-model="scriptNotifyEmail"
                                  class="ep-config-input"
                                  placeholder="收件邮箱"
                                  :disabled="scriptRunning"
                                />
                              </div>
                            </div>
                          </div>
                        </div>
                      </div>

                      <div class="script-actions">
                        <button class="script-action-btn" @click="saveScript" :disabled="scriptSaving">保存</button>
                        <button class="script-action-btn" @click="runCurrentSteps" :disabled="scriptRunning || scriptSaving">执行</button>
                        <button v-if="scriptRunning" class="script-action-btn script-stop-btn" @click="stopScriptRun">停止</button>
                        <button v-if="!scriptRunning" class="script-action-btn" @click="repeatRun" :disabled="scriptSaving">重复</button>
                      </div>
                    </div>
                  </div>
                </div>

                <!-- 脚本仓库：VSCode 风格文件夹树 -->
                <div v-if="scriptRepoOpen" class="script-repo-overlay" @mousedown.self="closeScriptRepo" @contextmenu.prevent>
                  <div class="script-repo-dialog" @click="closeRepoMenu">
                    <div class="script-repo-head">
                      <span class="script-repo-title">脚本仓库</span>
                      <span class="script-repo-tip">右键文件夹或脚本进行操作</span>
                      <button class="script-mini-btn" @click.stop="closeScriptRepo">关闭</button>
                    </div>
                    <div class="script-repo-tree" @scroll="closeRepoMenu">
                      <div v-if="scriptRepoLoading" class="script-repo-empty">加载中...</div>
                      <template v-else>
                        <div
                          v-for="row in repoFlatRows"
                          :key="row.kind === 'dir' ? 'd:' + row.node.path : 's:' + row.parent.path + '/' + row.script.fileName"
                          class="script-repo-node"
                          :class="{
                            'script-repo-node-selected': repoMenu && repoMenu.row === row,
                            'script-repo-drop-target': row.kind === 'dir' && repoDragOverPath === row.node.path,
                            'script-repo-dragging': row.kind === 'script' && repoDragging?.script === row.script,
                          }"
                          :style="{ paddingLeft: (8 + row.depth * 18) + 'px' }"
                          :draggable="row.kind === 'script' && scriptRepoAction === ''"
                          @dragstart="row.kind === 'script' && onRepoScriptDragStart(row.script, row.parent, $event)"
                          @dragend="onRepoScriptDragEnd"
                          @dragover="row.kind === 'dir' && onRepoDirDragOver(row.node, $event)"
                          @dragleave="row.kind === 'dir' && onRepoDirDragLeave(row.node)"
                          @drop="row.kind === 'dir' && onRepoDirDrop(row.node, $event)"
                          @contextmenu.prevent.stop="openRepoMenu($event, row)"
                        >
                          <!-- 文件夹节点 -->
                          <template v-if="row.kind === 'dir'">
                            <span class="script-repo-arrow" @click.stop="repoToggleDir(row.node)">{{ row.node.expanded ? '▾' : '▸' }}</span>
                            <template v-if="scriptRepoAction === 'create' && scriptRepoTarget === row.node">
                              <input v-model="scriptRepoInput" class="script-repo-input" placeholder="新文件夹名" @click.stop @keyup.enter.stop="repoCreateFolder" />
                              <button class="script-mini-btn" @click.stop="repoCreateFolder">确定</button>
                              <button class="script-mini-btn" @click.stop="repoCancelAction">取消</button>
                            </template>
                            <template v-else-if="scriptRepoAction === 'deleteDir' && scriptRepoTarget === row.node">
                              <span class="script-repo-name">📁 {{ row.node.name }}</span>
                              <span class="script-repo-confirm">删除文件夹及其中全部脚本？</span>
                              <button class="script-mini-btn script-repo-danger" @click.stop="repoDeleteDir">确认删除</button>
                              <button class="script-mini-btn" @click.stop="repoCancelAction">取消</button>
                            </template>
                            <template v-else>
                              <span class="script-repo-name script-repo-folder" @click.stop="repoToggleDir(row.node)">{{ row.node.expanded ? '📂' : '📁' }} {{ row.node.name }}</span>
                              <span v-if="row.node.loading" class="script-repo-meta">加载中...</span>
                            </template>
                          </template>
                          <!-- 脚本节点 -->
                          <template v-else>
                            <span class="script-repo-arrow script-repo-file">📄</span>
                            <template v-if="scriptRepoAction === 'rename' && scriptRepoTarget === row.script">
                              <input v-model="scriptRepoInput" class="script-repo-input" @click.stop @keyup.enter.stop="repoRenameScript" />
                              <button class="script-mini-btn" @click.stop="repoRenameScript">确定</button>
                              <button class="script-mini-btn" @click.stop="repoCancelAction">取消</button>
                            </template>
                            <template v-else-if="scriptRepoAction === 'move' && scriptRepoTarget === row.script">
                              <input v-model="scriptRepoInput" class="script-repo-input" placeholder="目标文件夹路径，留空=根目录" @click.stop @keyup.enter.stop="repoMoveScript" />
                              <button class="script-mini-btn" @click.stop="repoMoveScript">确定</button>
                              <button class="script-mini-btn" @click.stop="repoCancelAction">取消</button>
                            </template>
                            <template v-else-if="scriptRepoAction === 'deleteScript' && scriptRepoTarget === row.script">
                              <span class="script-repo-name">{{ row.script.name }}</span>
                              <span class="script-repo-confirm">确认删除？</span>
                              <button class="script-mini-btn script-repo-danger" @click.stop="repoDeleteScript">确认删除</button>
                              <button class="script-mini-btn" @click.stop="repoCancelAction">取消</button>
                            </template>
                            <template v-else>
                              <span class="script-repo-name">{{ row.script.name }}</span>
                              <span class="script-repo-meta">{{ row.script.stepCount }} 步 · ×{{ row.script.repeat }}</span>
                            </template>
                          </template>
                        </div>
                        <div v-if="repoFlatRows.length === 1" class="script-repo-empty">（空仓库，保存脚本后会出现在根目录）</div>
                      </template>
                    </div>
                  </div>
                  <!-- 右键菜单 -->
                  <div v-if="repoMenu" class="script-repo-menu" :style="{ left: repoMenu.x + 'px', top: repoMenu.y + 'px' }" @click.stop @contextmenu.prevent.stop>
                    <template v-if="repoMenu.row.kind === 'dir'">
                      <div class="script-repo-menu-item" @click="repoStartAction('create', repoMenu.row.node, repoMenu.row.node)">＋ 新建子文件夹</div>
                      <div v-if="!repoMenu.row.node.isRoot" class="script-repo-menu-item danger" @click="repoStartAction('deleteDir', repoMenu.row.node, repoMenu.row.parent)">🗑 删除文件夹（含脚本）</div>
                    </template>
                    <template v-else>
                      <div class="script-repo-menu-item" @click="repoLoadScript(repoMenu.row.script, repoMenu.row.parent)">载入脚本</div>
                      <div class="script-repo-menu-item" @click="repoExportScript(repoMenu.row.script, repoMenu.row.parent)">导出脚本(zip)</div>
                      <div class="script-repo-menu-item" @click="repoStartAction('rename', repoMenu.row.script, repoMenu.row.parent, repoMenu.row.script.name)">重命名</div>
                      <div class="script-repo-menu-item" @click="repoStartAction('move', repoMenu.row.script, repoMenu.row.parent, '')">移动到文件夹...</div>
                      <div class="script-repo-menu-item danger" @click="repoStartAction('deleteScript', repoMenu.row.script, repoMenu.row.parent)">🗑 删除脚本</div>
                    </template>
                  </div>
                </div>
              </div>
</template>
