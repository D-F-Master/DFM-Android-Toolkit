<script lang="ts" setup>
import { inject } from 'vue'

// 共享上下文由 App_Utils.vue 提供（含状态 ref 与操作函数）
const ctx = inject<any>('app-utils')!
const { activeTab, batchDeviceInfos, batchFilteredDevices, batchInstallApp, batchInstallLoading, batchKeyLoading, batchKeyevent, batchLoading, batchModelFilter, batchReboot, batchRebootLoading, batchRemoteActive, batchRemoteCards, batchRemoteConnecting, batchRemoteGridCols, batchRemoteOnPointer, batchRemoteSource, batchRemoteStatus, batchRemoteWsStatus, batchSelectAllChecked, batchSelectedDevices, batchShellCommand, batchShellLoading, batchShellResults, loading, selectedBatchCount, setBatchRemoteCanvasRef, setBatchRemoteSource, showBatchShellResults, startBatchRemote, stopBatchRemote, toggleAllBatchDevices, toggleBatchDevice } = ctx
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
