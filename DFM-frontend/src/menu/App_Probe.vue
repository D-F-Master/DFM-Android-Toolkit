<script lang="ts" setup>
import axios from 'axios'
import { ref, computed, watch, onMounted } from 'vue';

const emit = defineEmits(['back']);

const API_BASE = ref('http://localhost:3000');

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

interface ProbeState {
  status: 'idle' | 'busy' | 'ok' | 'fail';
  message: string;
}

interface FrameLog {
  id: number;
  time: string;
  title: string;
  device: string;
  tx: string;
  ack: string;
  rx: string;
  error: string;
}

const devices = ref<Device[]>([]);
const loading = ref(false);
const selectedDevice = ref('');
const probeStates = ref<Record<string, ProbeState>>({});
const probeLoading = ref<Record<string, boolean>>({});
// 只对刷新时新出现的设备自动安装，已处理过的不重复装
const knownDeviceIds = new Set<string>();

// ===== 请求 / 应答展示区 =====
const resultTitle = ref('尚未执行测试');
const requestText = ref('');
const responseText = ref('');
const ttsText = ref('设备语音测试');
const customCmd = ref('0');
const customData = ref('{}');
const frameText = ref('');
const frameLogs = ref<FrameLog[]>([]);
let frameLogId = 0;

const busy = computed(() => !!probeLoading.value[selectedDevice.value]);

const probeStateOf = (id: string): ProbeState =>
  probeStates.value[id] || { status: 'idle', message: '未检测' };

const setProbeState = (id: string, state: ProbeState) => {
  probeStates.value = { ...probeStates.value, [id]: state };
};

const setProbeBusy = (id: string, value: boolean) => {
  const next = { ...probeLoading.value };
  if (value) next[id] = true;
  else delete next[id];
  probeLoading.value = next;
};

// 圆点点击 / 新设备自动安装：只更新圆点状态，不占用展示区
const runProbe = async (deviceId: string, install: boolean) => {
  if (!deviceId || probeLoading.value[deviceId]) return;
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
  for (const id of Array.from(knownDeviceIds)) {
    if (!current.has(id)) knownDeviceIds.delete(id);
  }
  for (const d of devices.value) {
    if (!d.deviceId || knownDeviceIds.has(d.deviceId)) continue;
    knownDeviceIds.add(d.deviceId);
    runProbe(d.deviceId, true);
  }
};

const fetchDevices = async () => {
  loading.value = true;
  try {
    const response = await axios.post(`${API_BASE.value}/api/devices`);
    if (response.data.code === 200) {
      devices.value = response.data.data || [];
      if (devices.value.length > 0 && !devices.value.some(d => d.deviceId === selectedDevice.value)) {
        selectedDevice.value = devices.value[0].deviceId;
      }
      autoInstallNewDevices();
    }
  } catch (error) {
    console.error('获取设备列表失败:', error);
  } finally {
    loading.value = false;
  }
};

const selectDevice = (deviceId: string) => {
  selectedDevice.value = deviceId;
};

const disconnectDevice = async (deviceId: string) => {
  try {
    await axios.post(`${API_BASE.value}/api/device/disconnect`, { deviceId });
    if (selectedDevice.value === deviceId) selectedDevice.value = '';
    fetchDevices();
  } catch (error) {
    console.error('断开设备失败:', error);
  }
};

// 帧日志只记录右侧手动测试；圆点点击 / 自动安装不写日志
const addFrameLog = (title: string, device: string, d: any, error = '') => {
  const frames = d?.data?.frames || {};
  frameLogs.value.unshift({
    id: ++frameLogId,
    time: new Date().toLocaleTimeString('zh-CN', { hour12: false }),
    title,
    device: device || '—',
    tx: frames.tx || '—',
    ack: frames.ack || '—',
    rx: frames.rx || '—',
    error,
  });
};

const clearFrameLogs = () => {
  frameLogs.value = [];
};

// 功能区测试：把请求与完整应答写进展示区，并同步圆点状态
const callProbe = async (url: string, body: Record<string, unknown>, title: string) => {
  const deviceId = selectedDevice.value;
  if (!deviceId || probeLoading.value[deviceId]) return;
  setProbeBusy(deviceId, true);
  setProbeState(deviceId, { status: 'busy', message: '测试中...' });
  resultTitle.value = title;
  requestText.value = JSON.stringify(body, null, 2);
  responseText.value = '请求中...';
  frameText.value = '';
  try {
    // 后端安装超时 180s + TTS 朗读最长 90s，前端不能在后端返回前就判失败
    const res = await axios.post(`${API_BASE.value}${url}`, body, { timeout: 200000 });
    const d = res.data;
    const ok = d?.code === 200 && d?.data?.connected === true;
    responseText.value = JSON.stringify(d, null, 2);
    frameText.value = d?.data?.frame || '';
    addFrameLog(title, deviceId, d, ok ? '' : (d?.message || '通讯失败'));
    setProbeState(deviceId, { status: ok ? 'ok' : 'fail', message: d?.message || '通讯失败' });
  } catch (e: any) {
    const message = `请求失败: ${e?.message || e}`;
    responseText.value = message;
    frameText.value = '';
    addFrameLog(title, deviceId, null, message);
    setProbeState(deviceId, { status: 'fail', message: e?.message || '请求失败' });
  } finally {
    setProbeBusy(deviceId, false);
  }
};

const installAndConnect = () => {
  callProbe('/api/probe/connect', { deviceId: selectedDevice.value }, '安装并通讯');
};

const checkConnect = () => {
  callProbe('/api/probe/check', { deviceId: selectedDevice.value }, '通讯检测');
};

const sendMessage = (cmd: number, data: Record<string, unknown>, title: string) => {
  callProbe('/api/probe/message', { deviceId: selectedDevice.value, cmd, data }, title);
};

const sendHeartbeat = () => {
  sendMessage(0x00, {}, '心跳');
};

const sendBogus = () => {
  sendMessage(0x7E, {}, '未知命令字');
};

const sendTts = () => {
  sendMessage(0x01, { text: ttsText.value }, 'TTS 语音朗读');
};

const showLocalError = (text: string) => {
  resultTitle.value = '自定义命令帧';
  requestText.value = `cmd: ${customCmd.value}\ndata: ${customData.value}`;
  responseText.value = text;
  frameText.value = '';
};

const sendCustomMessage = () => {
  let parsed: any;
  try {
    parsed = JSON.parse(customData.value);
  } catch (e: any) {
    showLocalError(`JSON 解析失败: ${e?.message || e}`);
    return;
  }
  if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
    showLocalError('数据区必须是一个 JSON 对象（不能是数组 / null / 基本类型）');
    return;
  }
  const raw = customCmd.value.trim();
  const cmd = /^0x[0-9a-f]+$/i.test(raw)
    ? parseInt(raw, 16)
    : (/^\d+$/.test(raw) ? parseInt(raw, 10) : NaN);
  if (!Number.isInteger(cmd) || cmd < 0 || cmd > 255) {
    showLocalError('命令字必须是 0~255 的整数（可用 0x 前缀）');
    return;
  }
  sendMessage(cmd, parsed, '自定义命令帧');
};

watch(selectedDevice, (deviceId) => {
  resultTitle.value = deviceId ? '尚未执行测试' : '请先选择设备';
  requestText.value = '';
  responseText.value = '';
  frameText.value = '';
});

const goHome = () => {
  emit('back');
};

onMounted(async () => {
  await loadConfig();
  fetchDevices();
});
</script>

<template>
  <div class="app">
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
                @click="selectDevice(device.deviceId)"
              >
                <div class="device-item-info">
                  <div class="device-id">{{ device.deviceId }}</div>
                  <div class="device-info" v-if="device.model">{{ device.model }}</div>
                  <div class="device-status" v-if="device.status">{{ device.status }}</div>
                </div>
                <span
                  class="probe-dot"
                  :class="probeStateOf(device.deviceId).status"
                  :title="`探针: ${probeStateOf(device.deviceId).message}（点击安装通讯）`"
                  @click.stop="retryProbe(device.deviceId)"
                ></span>
                <button class="disconnect-btn" @click.stop="disconnectDevice(device.deviceId)" title="断开连接">
                  <svg viewBox="0 0 1024 1024" width="12" height="12" fill="currentColor">
                    <path d="M816.952281 614.064456l-45.118858-45.118857L895.99028 447.98866a223.99433 223.99433 0 0 0-316.791981-316.791981l-124.156857 120.956938-45.118858-45.438849 122.556898-122.236906a287.99271 287.99271 0 0 1 407.029697 407.029697zM288.00567 1023.974081a287.99271 287.99271 0 0 1-203.514849-491.507559l122.556898-122.556898 45.118858 45.118858-122.556898 122.556898A223.99433 223.99433 0 0 0 448.00162 895.977321l122.556898-122.556898 45.118858 45.118858-122.556898 122.556898A285.752767 285.752767 0 0 1 288.00567 1023.974081zM137.257486 182.555379L182.50434 137.276525l255.897523 255.897523-45.246855 45.246854zM585.214147 630.544039l45.246854-45.246854 255.897523 255.897522-45.246855 45.246855z"/>
                  </svg>
                </button>
              </div>
            </template>
          </div>
          <div class="probe-legend">
            <span><i class="legend-dot ok"></i>通讯正常</span>
            <span><i class="legend-dot fail"></i>通讯失败</span>
            <span><i class="legend-dot busy"></i>处理中</span>
            <span><i class="legend-dot idle"></i>未检测</span>
          </div>
        </div>

        <div class="content">
          <div class="probe-header">
            <h2>探针测试</h2>
            <span class="probe-target">当前设备：{{ selectedDevice || '未选择' }}</span>
          </div>
          <p class="probe-hint" v-if="busy">当前设备正在执行探针请求，请等待返回后再试。</p>
          <div v-if="!selectedDevice" class="hint">请先在左侧选择一个设备</div>
          <div v-else class="cards">
            <div class="card">
              <h3>安装与连接</h3>
              <p class="card-desc">安装 / 覆盖安装探针 APK，并验证帧协议通讯。</p>
              <div class="button-row">
                <button @click="installAndConnect" :disabled="busy">安装并通讯</button>
                <button class="secondary" @click="checkConnect" :disabled="busy">仅检测</button>
              </div>
            </div>

            <div class="card">
              <h3>快捷命令</h3>
              <p class="card-desc">发送内置命令字，查看设备应答。</p>
              <div class="button-row">
                <button @click="sendHeartbeat" :disabled="busy">心跳 (0x00)</button>
                <button class="secondary" @click="sendBogus" :disabled="busy">未知命令 (0x7E)</button>
              </div>
            </div>

            <div class="card">
              <h3>TTS 语音朗读</h3>
              <p class="card-desc">调用设备系统 TTS 引擎朗读文本，需引擎可用。</p>
              <div class="input-row">
                <input v-model="ttsText" class="text-input" placeholder="输入要朗读的文本">
                <button @click="sendTts" :disabled="busy">朗读</button>
              </div>
            </div>

            <div class="card">
              <h3>自定义命令帧</h3>
              <p class="card-desc">命令字 + UTF-8 JSON 数据区，单帧最多 65535 字节。</p>
              <div class="input-row">
                <input v-model="customCmd" class="cmd-input" placeholder="命令字 0-255，如 0 或 0x01">
              </div>
              <textarea v-model="customData" class="json-input" spellcheck="false"></textarea>
              <div class="button-row">
                <button @click="sendCustomMessage" :disabled="busy">发送</button>
              </div>
            </div>

            <div class="card result-card">
              <h3>{{ resultTitle }}</h3>
              <div class="frame-row" v-if="frameText">
                <span class="result-label">帧</span>
                <pre>{{ frameText }}</pre>
              </div>
              <div class="result-grid">
                <div class="result-col">
                  <div class="result-label">请求</div>
                  <pre>{{ requestText || '—' }}</pre>
                </div>
                <div class="result-col">
                  <div class="result-label">应答</div>
                  <pre>{{ responseText || '—' }}</pre>
                </div>
              </div>
            </div>

            <div class="card log-card">
              <div class="log-header">
                <h3>帧日志</h3>
                <button class="secondary" @click="clearFrameLogs" :disabled="!frameLogs.length">清空</button>
              </div>
              <p class="card-desc">发送帧 / ACK / 响应帧（真实字节），最新在上。</p>
              <div class="log-list" v-if="frameLogs.length">
                <div class="log-item" v-for="log in frameLogs" :key="log.id">
                  <div class="log-title">
                    <span class="log-time">{{ log.time }}</span>
                    <span class="log-name">{{ log.title }}</span>
                    <span class="log-device">{{ log.device }}</span>
                    <span class="log-error" v-if="log.error">{{ log.error }}</span>
                  </div>
                  <div class="log-frame"><span class="result-label">发送帧</span><pre>{{ log.tx }}</pre></div>
                  <div class="log-frame"><span class="result-label">ACK</span><pre>{{ log.ack }}</pre></div>
                  <div class="log-frame"><span class="result-label">响应帧</span><pre>{{ log.rx }}</pre></div>
                </div>
              </div>
              <div class="log-empty" v-else>暂无帧日志，点击上方测试按钮后显示。</div>
            </div>
          </div>
        </div>
      </div>
    </main>
  </div>
</template>

<style scoped>
.app {
  position: relative;
  width: 100%;
  height: 100vh;
  min-height: 544px;
}

.back-button {
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

.back-button:hover {
  background: rgba(128, 128, 128, 0.8);
}

main {
  height: 100%;
}

.container {
  display: flex;
  width: 100%;
  height: 100%;
  overflow: hidden;
}

.sidebar {
  display: flex;
  flex-direction: column;
  width: 250px;
  height: 100%;
  padding: 15px;
  background-color: #f0f0f0;
  border-right: 1px solid #ddd;
  box-sizing: border-box;
}

.device-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
}

.device-header h2 {
  margin: 0;
  font-size: 16px;
}

.refresh-button {
  padding: 4px 10px;
  font-size: 12px;
}

.device-list-container {
  flex: 1;
  overflow-y: auto;
}

.device-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px;
  margin-bottom: 6px;
  background-color: #fff;
  border: 1px solid #ddd;
  border-radius: 4px;
  cursor: pointer;
}

.device-item.active {
  background-color: #e3f2fd;
  border-color: #2196f3;
}

.device-item-info {
  flex: 1;
  min-width: 0;
}

.device-id {
  font-size: 13px;
  font-weight: bold;
  word-break: break-all;
}

.device-info,
.device-status {
  font-size: 11px;
  color: #666;
}

.probe-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  flex-shrink: 0;
  background-color: #999;
  cursor: pointer;
  transition: background-color 0.2s;
}

.probe-dot.ok {
  background-color: #4caf50;
}

.probe-dot.fail {
  background-color: #f44336;
}

.probe-dot.busy {
  background-color: #ff9800;
}

.probe-dot:hover {
  box-shadow: 0 0 0 3px rgba(0, 0, 0, 0.1);
}

.disconnect-btn {
  flex-shrink: 0;
  width: 24px;
  height: 24px;
  padding: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background-color: transparent;
  color: #e53935;
  border: 1px solid #f5c6c6;
  border-radius: 4px;
  cursor: pointer;
}

.disconnect-btn:hover {
  background-color: #ffebee;
}

.probe-legend {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 10px;
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px solid #ddd;
  font-size: 11px;
  color: #666;
}

.probe-legend span {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.legend-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background-color: #999;
}

.legend-dot.ok { background-color: #4caf50; }
.legend-dot.fail { background-color: #f44336; }
.legend-dot.busy { background-color: #ff9800; }

.content {
  flex: 1;
  overflow-y: auto;
  height: 100%;
  padding: 20px;
  box-sizing: border-box;
  background-color: #f5f5f5;
}

.probe-header {
  display: flex;
  align-items: baseline;
  gap: 16px;
  margin-bottom: 12px;
}

.probe-header h2 {
  margin: 0;
  font-size: 18px;
}

.probe-target {
  font-size: 12px;
  color: #666;
}

.probe-hint {
  margin: 0 0 12px;
  padding: 8px 12px;
  background-color: #fff3e0;
  border: 1px solid #ffcc80;
  border-radius: 4px;
  font-size: 12px;
  color: #e65100;
}

.hint {
  padding: 24px;
  color: #666;
}

.cards {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.card {
  background-color: #fff;
  border: 1px solid #e0e0e0;
  border-radius: 8px;
  padding: 14px 16px;
}

.card h3 {
  margin: 0 0 6px;
  font-size: 14px;
}

.card-desc {
  margin: 0 0 10px;
  font-size: 12px;
  color: #888;
}

.button-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.button-row button,
.input-row button,
.log-header button {
  padding: 6px 16px;
  font-size: 13px;
}

.button-row button.secondary,
.log-header button.secondary {
  background-color: #fff;
  color: #1976d2;
  border: 1px solid #1976d2;
}

.input-row {
  display: flex;
  gap: 8px;
}

.text-input {
  flex: 1;
  height: 32px;
  padding: 0 8px;
  border: 1px solid #ddd;
  border-radius: 4px;
  font-size: 13px;
}

.json-input {
  width: 100%;
  height: 90px;
  padding: 8px;
  margin-bottom: 8px;
  border: 1px solid #ddd;
  border-radius: 4px;
  font-family: Consolas, Monaco, monospace;
  font-size: 12px;
  resize: vertical;
  box-sizing: border-box;
}

.cmd-input {
  flex: 1;
  height: 32px;
  padding: 0 8px;
  margin-bottom: 8px;
  border: 1px solid #ddd;
  border-radius: 4px;
  font-family: Consolas, Monaco, monospace;
  font-size: 13px;
}

.frame-row {
  margin-bottom: 10px;
}

.frame-row pre {
  margin: 0;
  padding: 8px;
  max-height: 120px;
  overflow: auto;
  background-color: #f7f7f7;
  border: 1px solid #e0e0e0;
  border-radius: 4px;
  font-family: Consolas, Monaco, monospace;
  font-size: 12px;
  white-space: pre-wrap;
  word-break: break-all;
}

.result-grid {
  display: flex;
  gap: 12px;
}

.result-col {
  flex: 1;
  min-width: 0;
}

.result-label {
  margin-bottom: 4px;
  font-size: 12px;
  color: #666;
}

.result-col pre {
  margin: 0;
  padding: 8px;
  min-height: 80px;
  max-height: 260px;
  overflow: auto;
  background-color: #f7f7f7;
  border: 1px solid #e0e0e0;
  border-radius: 4px;
  font-family: Consolas, Monaco, monospace;
  font-size: 12px;
  white-space: pre-wrap;
  word-break: break-all;
}

.log-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.log-header h3 {
  margin-bottom: 6px;
}

.log-header button.secondary {
  padding: 4px 12px;
  font-size: 12px;
}

.log-list {
  max-height: 320px;
  overflow-y: auto;
}

.log-item {
  padding: 10px 0;
  border-top: 1px solid #eee;
}

.log-title {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  margin-bottom: 6px;
  font-size: 12px;
}

.log-time {
  color: #888;
  font-family: Consolas, Monaco, monospace;
}

.log-name {
  font-weight: 600;
}

.log-device {
  color: #666;
}

.log-error {
  color: #f44336;
}

.log-frame {
  margin-bottom: 6px;
}

.log-frame pre {
  margin: 0;
  padding: 6px 8px;
  background-color: #f7f7f7;
  border: 1px solid #e0e0e0;
  border-radius: 4px;
  font-family: Consolas, Monaco, monospace;
  font-size: 12px;
  white-space: pre-wrap;
  word-break: break-all;
}

.log-empty {
  padding: 12px 0;
  font-size: 12px;
  color: #888;
}
</style>
