<script lang="ts" setup>
import { ref, computed, onMounted, onUnmounted, nextTick } from 'vue';
import axios from 'axios';

const emit = defineEmits(['back']);

const goHome = () => {
  emit('back');
};

// ===== 基础状态 =====
const API_BASE = ref('http://localhost:3000');
const devices = ref<Array<{ deviceId: string; model?: string; status?: string }>>([]);
const loading = ref(false);
const deviceFilter = ref('');               // 设备名筛选
const selectedDevices = ref<string[]>([]);  // 多选

const loadConfig = async () => {
  try {
    const response = await fetch('/config.json');
    if (response.ok) {
      const config = await response.json();
      if (config.apiBase) API_BASE.value = config.apiBase;
    }
  } catch (error) {
    console.error('加载配置失败:', error);
  }
};

// ===== 设备 =====
const filteredDevices = computed(() => {
  const kw = deviceFilter.value.trim().toLowerCase();
  if (!kw) return devices.value;
  return devices.value.filter(d =>
    (d.deviceId || '').toLowerCase().includes(kw) ||
    (d.model || '').toLowerCase().includes(kw)
  );
});

const fetchDevices = async () => {
  loading.value = true;
  try {
    const response = await axios.post(`${API_BASE.value}/api/devices`);
    if (response.data.code === 200) {
      devices.value = response.data.data || [];
      // 已勾选的设备掉线了也要保留勾选（画面区还能看到最后状态）
    }
  } catch (error) {
    console.error('获取设备列表失败:', error);
  } finally {
    loading.value = false;
  }
};

const toggleDevice = (deviceId: string) => {
  const idx = selectedDevices.value.indexOf(deviceId);
  if (idx >= 0) selectedDevices.value.splice(idx, 1);
  else selectedDevices.value.push(deviceId);
};

const selectAllFiltered = () => {
  for (const d of filteredDevices.value) {
    if (!selectedDevices.value.includes(d.deviceId)) selectedDevices.value.push(d.deviceId);
  }
  pruneScreens();
};

const clearSelection = () => {
  selectedDevices.value = [];
  screenTiles.value.clear();
};

// ===== 脚本列表（快捷执行下拉）=====
const scriptOptions = ref<string[]>([]);

const fetchScripts = async () => {
  try {
    const response = await axios.post(`${API_BASE.value}/api/device/script/list`);
    if (response.data.code === 200) {
      scriptOptions.value = (response.data.data || []).map((s: any) => s.name);
    }
  } catch (error) {
    console.error('获取脚本列表失败:', error);
  }
};

// ===== 执行与日志 =====
interface LogEntry { time: string; deviceId: string; step: string; level: string; message: string; }
const runId = ref('');
const running = ref(false);
const runLogs = ref<LogEntry[]>([]);
const logCursor = ref(0);
const logFilePath = ref('');               // 本次运行落盘的 run.log 路径
const logBoxEl = ref<HTMLDivElement | null>(null);
let logTimer: ReturnType<typeof setInterval> | null = null;
let logPolling = false;

const appendLogs = (entries: LogEntry[]) => {
  runLogs.value.push(...entries);
  nextTick(() => {
    if (logBoxEl.value) logBoxEl.value.scrollTop = logBoxEl.value.scrollHeight;
  });
};

const startLogPolling = () => {
  if (logTimer) clearInterval(logTimer);
  logTimer = setInterval(pollLogs, 1000);
};

const pollLogs = async () => {
  if (!runId.value || logPolling) return;
  logPolling = true;
  try {
    const response = await axios.post(`${API_BASE.value}/api/device/pipeline/logs`, {
      runId: runId.value,
      cursor: logCursor.value,
    });
    if (response.data.code === 200) {
      const data = response.data.data;
      if (data.logs?.length) {
        appendLogs(data.logs);
        logCursor.value = data.total;
      }
      if (!data.running) {
        running.value = false;
        if (logTimer) { clearInterval(logTimer); logTimer = null; }
      }
    }
  } catch (error) {
    console.error('拉取日志失败:', error);
  } finally {
    logPolling = false;
  }
};

const stopRun = async () => {
  if (!runId.value) return;
  try {
    await axios.post(`${API_BASE.value}/api/device/pipeline/stop`, { name: runId.value });
  } catch (error) {
    console.error('停止失败:', error);
  }
};

const levelClass = (level: string) =>
  level === 'err' ? 'log-err' : level === 'ok' ? 'log-ok' : 'log-info';

// ===== 设备画面快照 =====
interface ScreenTile {
  deviceId: string;
  imageDataUrl: string;   // data:image/jpeg;base64,...
  loading: boolean;
  error: string;
  capturedAt: string;
}
const screenTiles = ref<Map<string, ScreenTile>>(new Map());
const screensLoading = ref(false);
let screenPollTimer: ReturnType<typeof setInterval> | null = null;
const screenPolling = ref(false);

// 获取所有勾选设备的画面快照（一轮，并发限 4 路）
const captureScreens = async () => {
  const targets = selectedDevices.value.filter(d => {
    const t = screenTiles.value.get(d);
    return !t || !t.loading;   // 跳过正在截取中的
  });
  if (targets.length === 0) return;
  targets.forEach(d => {
    const t = screenTiles.value.get(d);
    if (t) t.loading = true;
    else screenTiles.value.set(d, { deviceId: d, imageDataUrl: '', loading: true, error: '', capturedAt: '' });
  });
  const workers = Math.min(4, targets.length);
  let cursor = 0;
  const runOne = async () => {
    while (cursor < targets.length) {
      const deviceId = targets[cursor++];
      try {
        const response = await axios.post(`${API_BASE.value}/api/device/pipeline/screen`, { deviceId }, { timeout: 30000 });
        if (response.data.code === 200) {
          const t = screenTiles.value.get(deviceId);
          if (t) {
            t.imageDataUrl = 'data:image/jpeg;base64,' + response.data.data.image;
            t.capturedAt = new Date().toTimeString().slice(0, 8);
            t.error = '';
          }
        } else {
          const t = screenTiles.value.get(deviceId);
          if (t) t.error = response.data.message || '截取失败';
        }
      } catch (error: any) {
        const t = screenTiles.value.get(deviceId);
        if (t) t.error = error?.response?.data?.message || error.message;
      } finally {
        const t = screenTiles.value.get(deviceId);
        if (t) t.loading = false;
      }
    }
  };
  await Promise.all(Array.from({ length: workers }, runOne));
};

// 手动获取画面
const fetchScreens = async () => {
  if (selectedDevices.value.length === 0) { alert('请先勾选设备'); return; }
  screensLoading.value = true;
  try { await captureScreens(); } finally { screensLoading.value = false; }
};

// 画面自动刷新开关（3 秒一轮）
const toggleScreenPolling = () => {
  screenPolling.value = !screenPolling.value;
  if (screenPollTimer) { clearInterval(screenPollTimer); screenPollTimer = null; }
  if (screenPolling.value) {
    screenPollTimer = setInterval(captureScreens, 3000);
    captureScreens();
  }
};

// 移除已取消勾选设备的画面
const pruneScreens = () => {
  for (const d of Array.from(screenTiles.value.keys())) {
    if (!selectedDevices.value.includes(d)) screenTiles.value.delete(d);
  }
};

// ===== 快捷执行脚本（选脚本 -> 对勾选设备执行）=====
const quickScriptName = ref('');
const quickRunning = ref(false);

const quickRunScript = async () => {
  if (selectedDevices.value.length === 0) { alert('请先勾选设备'); return; }
  if (!quickScriptName.value) { alert('请先选择脚本'); return; }
  quickRunning.value = true;
  try {
    const response = await axios.post(`${API_BASE.value}/api/device/pipeline/run-script`, {
      deviceIds: selectedDevices.value,
      scriptName: quickScriptName.value,
    });
    if (response.data.code === 200) {
      runId.value = response.data.data.runId;
      running.value = true;
      runLogs.value = [];
      logCursor.value = 0;
      logFilePath.value = (response.data.data.logDir ? response.data.data.logDir + '\\run.log' : '');
      const offline = response.data.data.offline || [];
      if (offline.length > 0) {
        appendLogs(offline.map((d: string) => ({
          time: new Date().toTimeString().slice(0, 8),
          deviceId: d, step: '准备', level: 'err', message: '设备不在线，已跳过',
        })));
      }
      startLogPolling();
    } else {
      alert(response.data.message || '启动失败');
    }
  } catch (error: any) {
    alert(`启动失败: ${error.message}`);
  } finally {
    quickRunning.value = false;
  }
};

onMounted(async () => {
  await loadConfig();
  fetchDevices();
  fetchScripts();
});

onUnmounted(() => {
  if (logTimer) { clearInterval(logTimer); logTimer = null; }
  if (screenPollTimer) { clearInterval(screenPollTimer); screenPollTimer = null; }
});
</script>

<template>
  <div class="app">
    <button class="back-button" @click="goHome">&lt;</button>
    <main>
      <div class="container">
        <!-- 左侧设备列表：筛选 + 多选 -->
        <div class="sidebar">
          <div class="device-header">
            <h2>设备选择</h2>
            <button class="refresh-button" @click="fetchDevices">刷新</button>
          </div>
          <input
            v-model="deviceFilter"
            class="device-filter"
            type="text"
            placeholder="按名称筛选设备..."
          >
          <div class="select-tools">
            <button class="mini-btn" @click="selectAllFiltered">全选</button>
            <button class="mini-btn" @click="clearSelection">清空</button>
            <span class="select-count">已选 {{ selectedDevices.length }} 台</span>
          </div>
          <div class="device-list-container">
            <div v-if="loading">加载设备中...</div>
            <div v-else-if="filteredDevices.length === 0">无匹配设备</div>
            <template v-else>
              <label
                v-for="device in filteredDevices"
                :key="device.deviceId"
                class="device-item device-check"
                :class="{ active: selectedDevices.includes(device.deviceId), 'status-offline-item': device.status !== 'device' }"
              >
                <input
                  type="checkbox"
                  :checked="selectedDevices.includes(device.deviceId)"
                  @change="toggleDevice(device.deviceId)"
                >
                <span class="device-check-info">
                  <span class="device-id">{{ device.deviceId }}</span>
                  <span class="device-info" v-if="device.model">{{ device.model }}</span>
                  <span class="device-status" :class="{ 'status-offline': device.status !== 'device' }">
                    {{ device.status === 'device' ? '已连接' : device.status === 'offline' ? '离线' : device.status }}
                  </span>
                </span>
              </label>
            </template>
          </div>
        </div>

        <!-- 右侧：设备画面 + 快捷执行 + 日志 -->
        <div class="content">
          <div class="prod-page">
            <!-- 设备画面网格：3 列，可滚动，左上角设备 IP -->
            <div class="scr-section">
              <div class="scr-toolbar">
                <span class="scr-title">设备画面（{{ selectedDevices.length }} 台）</span>
                <div class="scr-toolbar-btns">
                  <button class="prod-btn" :disabled="screensLoading || selectedDevices.length === 0" @click="fetchScreens">
                    {{ screensLoading ? '获取中...' : '📷 获取画面' }}
                  </button>
                  <button class="prod-btn" :class="{ 'scr-polling-on': screenPolling }" @click="toggleScreenPolling">
                    {{ screenPolling ? '⏸ 停止刷新' : '🔄 自动刷新(3s)' }}
                  </button>
                </div>
              </div>
              <div class="scr-grid" v-if="selectedDevices.length > 0">
                <div v-for="d in selectedDevices" :key="d" class="scr-tile">
                  <span class="scr-tile-ip">{{ d }}</span>
                  <span v-if="screenTiles.get(d)?.capturedAt" class="scr-tile-time">{{ screenTiles.get(d)?.capturedAt }}</span>
                  <div class="scr-tile-body">
                    <img v-if="screenTiles.get(d)?.imageDataUrl" :src="screenTiles.get(d)?.imageDataUrl" class="scr-tile-img">
                    <div v-else-if="screenTiles.get(d)?.loading" class="scr-tile-loading">截取中...</div>
                    <div v-else-if="screenTiles.get(d)?.error" class="scr-tile-error">{{ screenTiles.get(d)?.error }}</div>
                    <div v-else class="scr-tile-loading">点击「获取画面」</div>
                    <div v-if="screenTiles.get(d)?.loading && screenTiles.get(d)?.imageDataUrl" class="scr-tile-refresh">刷新中...</div>
                  </div>
                </div>
              </div>
              <div v-else class="scr-empty">先在左侧勾选设备</div>
            </div>

            <!-- 快捷执行：选脚本直接跑 -->
            <div class="quick-bar">
              <span class="quick-label">快捷执行：</span>
              <select v-model="quickScriptName" class="quick-select">
                <option value="" disabled>选择模拟脚本...</option>
                <option v-for="n in scriptOptions" :key="n" :value="n">{{ n }}</option>
              </select>
              <button class="prod-run-btn" :disabled="quickRunning || running" @click="quickRunScript">
                {{ quickRunning ? '启动中...' : '▶ 对已选设备执行脚本' }}
              </button>
              <button v-if="running" class="prod-stop-btn" @click="stopRun">■ 停止</button>
            </div>

            <!-- 日志 -->
            <div class="prod-log-card">
              <div class="prod-log-head">
                <span>执行日志</span>
                <span v-if="logFilePath" class="prod-log-file" :title="logFilePath">落盘: {{ logFilePath }}</span>
              </div>
              <div ref="logBoxEl" class="prod-log-box">
                <div v-if="runLogs.length === 0" class="prod-log-empty">暂无日志</div>
                <div v-for="(log, i) in runLogs" :key="i" class="prod-log-line" :class="levelClass(log.level)">
                  [{{ log.time }}] [{{ log.deviceId }}] {{ log.step }} - {{ log.message }}
                </div>
              </div>
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

/* ===== 左栏：筛选 + 多选 ===== */
.device-filter {
  margin: 8px 0;
  width: 100%;
  box-sizing: border-box;
  height: 30px;
  padding: 0 8px;
  border: 1px solid #ddd;
  border-radius: 4px;
  font-size: 13px;
}

.select-tools {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 8px;
}

.mini-btn {
  padding: 3px 10px;
  font-size: 12px;
  background: #eceff1;
  color: #455a64;
  border: 1px solid #cfd8dc;
  border-radius: 4px;
  cursor: pointer;
}

.mini-btn:hover {
  background: #e8f5e9;
  border-color: #a5d6a7;
  color: #2e7d32;
}

.select-count {
  margin-left: auto;
  font-size: 12px;
  color: #666;
}

.device-list-container {
  flex: 1;
  overflow-y: auto;
  min-height: 0;
}

.device-check {
  display: flex;
  align-items: flex-start;
  gap: 8px;
}

.device-check input[type='checkbox'] {
  margin-top: 3px;
  flex-shrink: 0;
  cursor: pointer;
}

.device-check-info {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.device-status {
  font-size: 12px;
  color: #4caf50;
  margin-top: 3px;
}

.device-status.status-offline {
  color: #f44336;
}

.status-offline-item {
  opacity: 0.55;
}

/* ===== 右栏：画面 + 快捷执行 + 日志 ===== */
.prod-page {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 10px;
  height: 100%;
  box-sizing: border-box;
  overflow-y: auto;
}

/* ===== 按钮 ===== */
.prod-btn {
  padding: 6px 14px;
  font-size: 13px;
  background: #fff;
  color: #2e7d32;
  border: 1px solid #a5d6a7;
  border-radius: 6px;
  cursor: pointer;
  transition: all 0.2s ease;
}

.prod-btn:hover:not(:disabled) {
  background: #e8f5e9;
}

.prod-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.prod-run-btn {
  padding: 7px 18px;
  font-size: 13px;
  font-weight: 600;
  color: #fff;
  background: linear-gradient(135deg, #4caf50, #43a047);
  border: none;
  border-radius: 8px;
  cursor: pointer;
  box-shadow: 0 3px 12px rgba(76, 175, 80, 0.3);
  transition: all 0.25s ease;
}

.prod-run-btn:hover:not(:disabled) {
  transform: translateY(-1px);
  box-shadow: 0 5px 16px rgba(76, 175, 80, 0.4);
}

.prod-run-btn:disabled {
  filter: grayscale(0.5);
  cursor: not-allowed;
}

.prod-stop-btn {
  padding: 7px 16px;
  font-size: 13px;
  font-weight: 600;
  color: #fff;
  background: #f44336;
  border: none;
  border-radius: 8px;
  cursor: pointer;
}

.prod-stop-btn:hover {
  background: #d32f2f;
}

/* ===== 设备画面网格 ===== */
.scr-section {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.scr-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.scr-title {
  font-size: 13px;
  font-weight: 600;
  color: #444;
}

.scr-toolbar-btns {
  display: flex;
  gap: 8px;
}

.scr-polling-on {
  background: #e8f5e9;
  border-color: #66bb6a;
  color: #2e7d32;
}

.scr-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 10px;
}

.scr-tile {
  position: relative;
  background: #1e2430;
  border-radius: 8px;
  overflow: hidden;
}

.scr-tile-ip {
  position: absolute;
  top: 6px;
  left: 8px;
  z-index: 2;
  padding: 2px 8px;
  border-radius: 4px;
  background: rgba(0, 0, 0, 0.55);
  color: #fff;
  font-size: 11px;
  font-family: Consolas, monospace;
  pointer-events: none;
}

.scr-tile-time {
  position: absolute;
  top: 6px;
  right: 8px;
  z-index: 2;
  padding: 2px 6px;
  border-radius: 4px;
  background: rgba(0, 0, 0, 0.55);
  color: #9fd6a8;
  font-size: 10px;
  font-family: Consolas, monospace;
  pointer-events: none;
}

.scr-tile-body {
  position: relative;
  min-height: 120px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.scr-tile-img {
  width: 100%;
  display: block;
  aspect-ratio: 16 / 9;
  object-fit: contain;
  background: #000;
}

.scr-tile-loading,
.scr-tile-error {
  padding: 40px 10px;
  font-size: 12px;
  color: #8a97a8;
  text-align: center;
}

.scr-tile-error {
  color: #ff8a80;
  word-break: break-all;
}

.scr-tile-refresh {
  position: absolute;
  bottom: 6px;
  right: 8px;
  padding: 2px 6px;
  border-radius: 4px;
  background: rgba(0, 0, 0, 0.55);
  color: #ffd54f;
  font-size: 10px;
}

.scr-empty {
  padding: 32px;
  text-align: center;
  color: #999;
  font-size: 13px;
  border: 1px dashed #ddd;
  border-radius: 8px;
}

/* ===== 快捷执行条 ===== */
.quick-bar {
  display: flex;
  align-items: center;
  gap: 10px;
}

.quick-label {
  font-size: 13px;
  color: #666;
}

.quick-select {
  min-width: 200px;
  height: 32px;
  padding: 0 8px;
  border: 1px solid #ddd;
  border-radius: 6px;
  font-size: 13px;
  background: #fff;
  color: #333;
}

/* ===== 日志 ===== */
.prod-log-card {
  background: #1e2430;
  border-radius: 10px;
  overflow: hidden;
  flex-shrink: 0;
}

.prod-log-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 8px 14px;
  font-size: 13px;
  font-weight: 600;
  color: #9fb3c8;
  background: #182030;
}

.prod-log-file {
  font-size: 11px;
  font-weight: 400;
  color: #5f7189;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.prod-log-box {
  height: 180px;
  overflow-y: auto;
  padding: 8px 14px;
  font-family: Consolas, 'Courier New', monospace;
  font-size: 12px;
  line-height: 1.7;
}

.prod-log-empty {
  color: #5a6b80;
}

.prod-log-line {
  white-space: pre-wrap;
  word-break: break-all;
}

.log-info { color: #b7c5d6; }
.log-ok { color: #7cd992; }
.log-err { color: #ff8a80; }
</style>
