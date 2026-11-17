<script lang="ts" setup>
import { inject } from 'vue'

// 共享上下文由 App_Utils.vue 提供（含状态 ref 与操作函数）
const ctx = inject<any>('app-utils')!
const { activeTab, appendKey, applyKeys, buildKeyFrame, clearKeySequence, controlPty, deployPtyTools, deploySerialTool, keyFunctionText, keyInjectBusy, keyInjectOutput, keyList, keySequence, loadPtyStatus, loadSerialPorts, loading, ptyAppHolds, ptyBusy, ptyDevice, ptyLeftover, ptyLog, ptyNodeType, ptyPid, ptyRunning, removeKeyAt, selectedDevice, sendSerialData, sendSerialSessionData, serialBaud, serialDevice, serialEnd, serialLoop, serialMessage, serialMessageType, serialMode, serialOutput, serialPayload, serialPorts, serialPortsLoading, serialReadBaud, serialReadDataBits, serialReadParity, serialReadStopBits, serialReadView, serialReading, serialScheme, serialSendInterval, serialSendMode, serialSendPayload, serialSendTimes, serialSending, serialSessionOpened, serialStream, serialStreamBusy, serialStreamLog, serialStreamPid, serialStreamRunning, serialToolDeployed, stopSerialSessionSend, toggleSerialRead } = ctx
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
