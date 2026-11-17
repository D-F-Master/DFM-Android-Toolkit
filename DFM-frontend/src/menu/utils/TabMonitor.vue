<script lang="ts" setup>
import { inject } from 'vue'

// 共享上下文由 App_Utils.vue 提供（含状态 ref 与操作函数）
const ctx = inject<any>('app-utils')!
const { activeNic, activeTab, coreChartEl, formatBytes, formatFreq, formatPercent, formatRate, loading, monitorError, monitorLoading, monitorRate, monitorSnapshot, rateChartEl, rateViewMode, refreshMonitor, snapshotChartEl, topProcesses } = ctx
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
