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
const { API_BASE, activeTab, selectedDevice, showConfirm, showMessage } = ctx
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

</script>

<template>
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
</template>
