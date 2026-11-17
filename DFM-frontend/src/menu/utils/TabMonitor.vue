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
const { API_BASE, activeTab, formatBytes, loading, scriptOnKeyDown, selectedDevice } = ctx
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


onMounted(() => { window.addEventListener('resize', resizeMonitorCharts) })
onUnmounted(() => { window.removeEventListener('resize', resizeMonitorCharts); stopMonitor(); disposeMonitorCharts() })

</script>

<template>
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
</template>
