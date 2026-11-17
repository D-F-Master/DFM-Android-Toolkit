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
const { API_BASE, activeTab, loading, selectedDevice } = ctx
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

onUnmounted(() => { stopSerialRead() })

</script>

<template>
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
</template>
