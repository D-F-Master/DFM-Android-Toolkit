<script lang="ts" setup>
import { inject } from 'vue'

// 共享上下文由 App_Utils.vue 提供（含状态 ref 与操作函数）
const ctx = inject<any>('app-utils')!
const { activeTab, doProbeCapture, onProbePhoto, playProbeTts, probeBusy, probeDisabled, probeResult, probeShellRoot, runProbeShell, sendProbeHeartbeat } = ctx
</script>

<template>
              <div v-if="activeTab === 'probe'" class="keys-page">
                <div class="scrcpy-card">
                  <div class="card-header">
                    <span class="card-icon">
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="18" height="18">
                        <path d="M22 12h-4l-3 9L9 3l-3 9H2"/>
                      </svg>
                    </span>
                    <span>探针工具</span>
                  </div>
                  <div class="card-body">
                    <div class="probe-actions">
                      <button
                        class="check-button"
                        @click="sendProbeHeartbeat"
                        :disabled="probeDisabled"
                      >{{ probeBusy === 'heartbeat' ? '检测中...' : '探针心跳' }}</button>
                      <button
                        class="check-button"
                        @click="playProbeTts"
                        :disabled="probeDisabled"
                      >{{ probeBusy === 'tts' ? '朗读中...' : '远程 TTS' }}</button>
                      <button
                        class="check-button"
                        @click="runProbeShell"
                        :disabled="probeDisabled"
                      >{{ probeBusy === 'shell' ? '执行中...' : 'Shell 命令' }}</button>
                      <button
                        class="check-button"
                        @click="doProbeCapture('screenshot')"
                        :disabled="probeDisabled"
                      >{{ probeBusy === 'screenshot' ? '截屏中...' : '屏幕截图' }}</button>
                      <button
                        class="check-button"
                        @click="onProbePhoto"
                        :disabled="probeDisabled"
                      >{{ probeBusy === 'photo' ? '拍照中...' : '摄像头拍照' }}</button>
                    </div>
                    <div class="serial-row">
                      <label class="serial-check">
                        <input type="checkbox" v-model="probeShellRoot" />
                        <span>Shell 以 root 执行（su 0，需设备已 root）</span>
                      </label>
                    </div>
                    <pre v-if="probeResult" class="probe-shell-out">{{ probeResult }}</pre>
                    <div class="serial-hint">
                      点击按钮即执行：心跳 0x00 / TTS 0x01 / Shell 0x08 / 截屏 0x09 / 拍照 0x0A。TTS 与 Shell 会弹出输入框；拍照先读取摄像头列表供选择，照片回传后设备端立即删除。
                    </div>
                  </div>
                </div>
              </div>
</template>
