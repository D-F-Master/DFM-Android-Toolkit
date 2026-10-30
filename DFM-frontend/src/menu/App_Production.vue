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

// ===== 流水线编辑 =====
interface PipelineStep {
  type: 'installApk' | 'simulate' | 'screenshot' | 'deleteFolder' | 'shell';
  apkName?: string;
  scriptName?: string;
  repeat?: number;
  saveDir?: string;
  path?: string;
  command?: string;
  _uid?: number;
}
let _stepUid = 0;
const normalizeSteps = (raw: any[]): PipelineStep[] =>
  (raw || []).map(s => ({
    ...s,
    command: s.command ?? '',
    apkName: s.apkName ?? '',
    scriptName: s.scriptName ?? '',
    repeat: s.repeat ?? 1,
    saveDir: s.saveDir ?? '',
    path: s.path ?? '',
    _uid: ++_stepUid,
  }));
const pipelineName = ref(`流水线_${new Date().toISOString().slice(0, 10).replace(/-/g, '')}`);
const steps = ref<PipelineStep[]>([]);
const savedNames = ref<string[]>([]);
const scriptOptions = ref<string[]>([]);
const apkOptions = ref<Array<{ name: string; size: number }>>([]);
const runMode = ref<'parallel' | 'serial'>('parallel');

const STEP_META: Record<string, { label: string; icon: string }> = {
  installApk: { label: '安装APK', icon: '📦' },
  simulate: { label: '模拟操作', icon: '👆' },
  screenshot: { label: '获取画面', icon: '📷' },
  deleteFolder: { label: '删除文件夹', icon: '🗑️' },
  shell: { label: '执行Shell命令', icon: '⌨️' },
};

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
};

const clearSelection = () => {
  selectedDevices.value = [];
};

// ===== 流水线编辑 =====
const addStep = (type: PipelineStep['type']) => {
  if (type === 'installApk') steps.value.push({ type, apkName: apkOptions.value[0]?.name || '', _uid: ++_stepUid });
  else if (type === 'simulate') steps.value.push({ type, scriptName: scriptOptions.value[0] || '', repeat: 1, _uid: ++_stepUid });
  else if (type === 'screenshot') steps.value.push({ type, saveDir: '', _uid: ++_stepUid });
  else if (type === 'shell') steps.value.push({ type, command: '', _uid: ++_stepUid });
  else steps.value.push({ type, path: '/sdcard/', _uid: ++_stepUid });
};

const removeStep = (idx: number) => {
  steps.value.splice(idx, 1);
};

const moveStep = (idx: number, dir: -1 | 1) => {
  const target = idx + dir;
  if (target < 0 || target >= steps.value.length) return;
  const arr = steps.value;
  [arr[idx], arr[target]] = [arr[target], arr[idx]];
};

const stepSummary = (s: PipelineStep): string => {
  if (s.type === 'installApk') return s.apkName || '（未选择APK）';
  if (s.type === 'simulate') return `${s.scriptName || '（未选择脚本）'} ×${s.repeat || 1}`;
  if (s.type === 'screenshot') return s.saveDir || '默认 D:\\log\\{设备SN}\\';
  if (s.type === 'shell') return s.command || '（未填写命令）';
  return s.path || '';
};

const fetchMaterials = async () => {
  try {
    const [scriptRes, apkRes] = await Promise.all([
      axios.post(`${API_BASE.value}/api/device/script/list`),
      axios.post(`${API_BASE.value}/api/device/pipeline/list-apks`),
    ]);
    if (scriptRes.data.code === 200) {
      scriptOptions.value = (scriptRes.data.data || []).map((s: any) => s.name);
    }
    if (apkRes.data.code === 200) {
      apkOptions.value = apkRes.data.data || [];
    }
  } catch (error) {
    console.error('获取素材列表失败:', error);
  }
};

const apkUploading = ref(false);
const apkInputEl = ref<HTMLInputElement | null>(null);
const apkRepoVisible = ref(false);          // APK 仓库弹窗

const openApkRepo = () => {
  apkRepoVisible.value = true;
  fetchMaterials();
};

const pickApk = () => apkInputEl.value?.click();

const onApkChosen = async (event: Event) => {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  input.value = '';
  if (!file) return;
  apkUploading.value = true;
  try {
    const form = new FormData();
    form.append('apk', file);
    const response = await axios.post(`${API_BASE.value}/api/device/pipeline/upload-apk`, form);
    if (response.data.code === 200) {
      await fetchMaterials();
    } else {
      alert(response.data.message || '上传失败');
    }
  } catch (error: any) {
    alert(`上传失败: ${error.message}`);
  } finally {
    apkUploading.value = false;
  }
};

const removeApk = async (name: string) => {
  if (!confirm(`确定删除 APK 素材 ${name} 吗？`)) return;
  try {
    const response = await axios.post(`${API_BASE.value}/api/device/pipeline/delete-apk`, { name });
    if (response.data.code === 200) {
      await fetchMaterials();
    } else {
      alert(response.data.message || '删除失败');
    }
  } catch (error: any) {
    alert(`删除失败: ${error.message}`);
  }
};

const formatSize = (size: number): string => {
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(0)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
};

// ===== 保存 / 加载 / 导出 / 导入 =====
const savePipeline = async () => {
  if (steps.value.length === 0) { alert('请先添加至少一个步骤'); return; }
  try {
    const response = await axios.post(`${API_BASE.value}/api/device/pipeline/save`, {
      name: pipelineName.value,
      steps: steps.value,
    });
    if (response.data.code === 200) {
      pipelineName.value = response.data.data.name;
      await fetchSavedPipelines();
    } else {
      alert(response.data.message || '保存失败');
    }
  } catch (error: any) {
    alert(`保存失败: ${error.message}`);
  }
};

const fetchSavedPipelines = async () => {
  try {
    const response = await axios.post(`${API_BASE.value}/api/device/pipeline/list`);
    if (response.data.code === 200) {
      savedNames.value = (response.data.data || []).map((p: any) => p.name);
    }
  } catch (error) {
    console.error('获取流水线列表失败:', error);
  }
};

const loadPipelineByName = async (name: string) => {
  try {
    const response = await axios.post(`${API_BASE.value}/api/device/pipeline/load`, { name });
    if (response.data.code === 200) {
      pipelineName.value = response.data.data.name;
      steps.value = normalizeSteps(response.data.data.steps);
    } else {
      alert(response.data.message || '加载失败');
    }
  } catch (error: any) {
    alert(`加载失败: ${error.message}`);
  }
};

const exportPipeline = () => {
  if (steps.value.length === 0) { alert('当前流水线为空'); return; }
  const data = JSON.stringify({ name: pipelineName.value, steps: steps.value }, null, 2);
  const blob = new Blob([data], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `${pipelineName.value}.json`;
  a.click();
  URL.revokeObjectURL(url);
};

const importInputEl = ref<HTMLInputElement | null>(null);
const pickImport = () => importInputEl.value?.click();

const onImportChosen = async (event: Event) => {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  input.value = '';
  if (!file) return;
  try {
    const content = await file.text();
    const response = await axios.post(`${API_BASE.value}/api/device/pipeline/import`, { content });
    if (response.data.code === 200) {
      await loadPipelineByName(response.data.data.name);
      await fetchSavedPipelines();
    } else {
      alert(response.data.message || '导入失败');
    }
  } catch (error: any) {
    alert(`导入失败: ${error.message}`);
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

const startRun = async () => {
  if (selectedDevices.value.length === 0) { alert('请先勾选设备'); return; }
  if (steps.value.length === 0) { alert('请先添加步骤并保存'); return; }
  try {
    const response = await axios.post(`${API_BASE.value}/api/device/pipeline/run`, {
      name: pipelineName.value,
      deviceIds: selectedDevices.value,
      mode: runMode.value,
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
      if (logTimer) clearInterval(logTimer);
      logTimer = setInterval(pollLogs, 1000);
    } else {
      alert(response.data.message || '启动失败');
    }
  } catch (error: any) {
    alert(`启动失败: ${error.message}`);
  }
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

onMounted(async () => {
  await loadConfig();
  fetchDevices();
  fetchMaterials();
  fetchSavedPipelines();
});

onUnmounted(() => {
  if (logTimer) { clearInterval(logTimer); logTimer = null; }
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

        <!-- 右侧：流水线编辑 + 执行 -->
        <div class="content">
          <div class="pipeline-page">
            <!-- 工具栏 -->
            <div class="pl-toolbar">
              <input v-model="pipelineName" class="pl-name-input" type="text" placeholder="流水线名称">
              <div class="pl-toolbar-btns">
                <button class="pl-btn" @click="savePipeline">保存</button>
                <button class="pl-btn" @click="exportPipeline">导出</button>
                <button class="pl-btn" @click="pickImport">导入</button>
                <input ref="importInputEl" type="file" accept=".json" style="display:none" @change="onImportChosen">
                <select class="pl-saved-select" @change="loadPipelineByName(($event.target as HTMLSelectElement).value); ($event.target as HTMLSelectElement).value = ''">
                  <option value="">加载已保存...</option>
                  <option v-for="n in savedNames" :key="n" :value="n">{{ n }}</option>
                </select>
              </div>
            </div>

            <!-- 步骤流水线 -->
            <div class="pl-steps">
              <div v-if="steps.length === 0" class="pl-steps-empty">
                还没有步骤，从下方「添加步骤」开始组装流水线
              </div>
              <div v-for="s in steps" :key="s._uid" class="pl-step">
                <div class="pl-step-index">{{ steps.indexOf(s) + 1 }}</div>
                <div class="pl-step-body">
                  <div class="pl-step-head">
                    <span class="pl-step-type">{{ STEP_META[s.type].icon }} {{ STEP_META[s.type].label }}</span>
                    <span class="pl-step-summary">{{ stepSummary(s) }}</span>
                    <span class="pl-step-actions">
                      <button class="pl-step-btn" title="上移" :disabled="steps.indexOf(s) === 0" @click="moveStep(steps.indexOf(s), -1)">↑</button>
                      <button class="pl-step-btn" title="下移" :disabled="steps.indexOf(s) === steps.length - 1" @click="moveStep(steps.indexOf(s), 1)">↓</button>
                      <button class="pl-step-btn pl-step-del" title="删除" @click="removeStep(steps.indexOf(s))">✕</button>
                    </span>
                  </div>
                  <!-- 步骤参数配置 -->
                  <div class="pl-step-config">
                    <template v-if="s.type === 'installApk'">
                      <select v-model="s.apkName" class="pl-config-select">
                        <option value="" disabled>选择 APK</option>
                        <option v-for="a in apkOptions" :key="a.name" :value="a.name">{{ a.name }} ({{ (a.size / 1024 / 1024).toFixed(1) }}MB)</option>
                      </select>
                    </template>
                    <template v-else-if="s.type === 'simulate'">
                      <select v-model="s.scriptName" class="pl-config-select">
                        <option value="" disabled>选择模拟脚本</option>
                        <option v-for="n in scriptOptions" :key="n" :value="n">{{ n }}</option>
                      </select>
                      <label class="pl-config-label">重复
                        <input v-model.number="s.repeat" type="number" min="1" class="pl-config-num">
                      </label>
                    </template>
                    <template v-else-if="s.type === 'screenshot'">
                      <input v-model="s.saveDir" class="pl-config-input" type="text" placeholder="保存目录，留空 = D:\log\{设备SN}\">
                    </template>
                    <template v-else-if="s.type === 'shell'">
                      <input v-model="s.command" class="pl-config-input" type="text" placeholder="设备 shell 命令，如 rm -rf /sdcard/xxx 或 am start -n pkg/.MainActivity">
                    </template>
                    <template v-else>
                      <input v-model="s.path" class="pl-config-input" type="text" placeholder="设备上的文件夹路径，如 /sdcard/xxx">
                    </template>
                  </div>
                </div>
              </div>
            </div>

            <!-- 添加步骤 -->
            <div class="pl-add-row">
              <span class="pl-add-label">添加步骤：</span>
              <button class="pl-add-btn" @click="addStep('installApk')">📦 安装APK</button>
              <button class="pl-add-btn" @click="addStep('simulate')">👆 模拟操作</button>
              <button class="pl-add-btn" @click="addStep('screenshot')">📷 获取画面</button>
              <button class="pl-add-btn" @click="addStep('deleteFolder')">🗑️ 删除文件夹</button>
              <button class="pl-add-btn" @click="addStep('shell')">⌨️ 执行Shell命令</button>
              <button class="pl-add-btn pl-add-apk" @click="openApkRepo">🗄️ APK仓库</button>
              <input ref="apkInputEl" type="file" accept=".apk" style="display:none" @change="onApkChosen">
            </div>

            <!-- 执行控制 -->
            <div class="pl-run-bar">
              <label class="pl-mode-label">执行方式
                <select v-model="runMode" class="pl-config-select" :disabled="running">
                  <option value="parallel">并发（每步所有设备同步）</option>
                  <option value="serial">单线程（每台依次跑完）</option>
                </select>
              </label>
              <button class="pl-run-btn" :disabled="running" @click="startRun">
                {{ running ? '执行中...' : '▶ 执行流水线' }}
              </button>
              <button v-if="running" class="pl-stop-btn" @click="stopRun">■ 停止</button>
            </div>

            <!-- 日志 -->
            <div class="pl-log-card">
              <div class="pl-log-head">
                <span>执行日志</span>
                <span v-if="logFilePath" class="pl-log-file" :title="logFilePath">落盘: {{ logFilePath }}</span>
              </div>
              <div ref="logBoxEl" class="pl-log-box">
                <div v-if="runLogs.length === 0" class="pl-log-empty">暂无日志</div>
                <div v-for="(log, i) in runLogs" :key="i" class="pl-log-line" :class="levelClass(log.level)">
                  [{{ log.time }}] [{{ log.deviceId }}] {{ log.step }} - {{ log.message }}
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </main>

    <!-- APK 仓库弹窗 -->
    <div v-if="apkRepoVisible" class="apk-repo-mask" @click.self="apkRepoVisible = false">
      <div class="apk-repo-dialog">
        <div class="apk-repo-head">
          <span>APK 仓库</span>
          <button class="apk-repo-close" @click="apkRepoVisible = false">✕</button>
        </div>
        <div class="apk-repo-body">
          <div class="apk-repo-actions">
            <button class="pl-btn" :disabled="apkUploading" @click="pickApk">
              {{ apkUploading ? '上传中...' : '⬆ 上传 APK' }}
            </button>
            <span class="apk-repo-hint">文件存放于服务端 data\apk\ 目录</span>
          </div>
          <div class="apk-repo-list">
            <div v-if="apkOptions.length === 0" class="apk-repo-empty">仓库为空，先上传 APK</div>
            <div v-for="a in apkOptions" :key="a.name" class="apk-repo-item">
              <span class="apk-repo-name" :title="a.name">📦 {{ a.name }}</span>
              <span class="apk-repo-size">{{ formatSize(a.size) }}</span>
              <button class="apk-repo-del" @click="removeApk(a.name)">删除</button>
            </div>
          </div>
        </div>
      </div>
    </div>
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

/* ===== 右栏：流水线 ===== */
.pipeline-page {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 10px;
  height: 100%;
  box-sizing: border-box;
}

.pl-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
}

.pl-name-input {
  flex: 1;
  height: 32px;
  padding: 0 10px;
  border: 1px solid #ddd;
  border-radius: 6px;
  font-size: 14px;
  font-weight: 600;
}

.pl-toolbar-btns {
  display: flex;
  gap: 6px;
  align-items: center;
}

.pl-btn {
  padding: 6px 14px;
  font-size: 13px;
  background: #fff;
  color: #2e7d32;
  border: 1px solid #a5d6a7;
  border-radius: 6px;
  cursor: pointer;
  transition: all 0.2s ease;
}

.pl-btn:hover {
  background: #e8f5e9;
}

.pl-saved-select {
  height: 30px;
  max-width: 160px;
  border: 1px solid #ddd;
  border-radius: 6px;
  font-size: 12px;
  color: #555;
}

/* ===== 步骤列表 ===== */
.pl-steps {
  flex: 1;
  overflow-y: auto;
  min-height: 120px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding-right: 2px;
}

.pl-steps-empty {
  padding: 24px;
  text-align: center;
  color: #999;
  font-size: 13px;
  border: 1px dashed #ddd;
  border-radius: 8px;
}

.pl-step {
  display: flex;
  gap: 10px;
  background: #fff;
  border: 1px solid #e8ecf0;
  border-radius: 10px;
  padding: 10px 12px;
  box-shadow: 0 1px 6px rgba(0, 0, 0, 0.05);
}

.pl-step-index {
  width: 26px;
  height: 26px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #0d9668;
  color: #fff;
  border-radius: 50%;
  font-size: 13px;
  font-weight: 700;
}

.pl-step-body {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.pl-step-head {
  display: flex;
  align-items: center;
  gap: 8px;
}

.pl-step-type {
  font-size: 13px;
  font-weight: 600;
  color: #333;
}

.pl-step-summary {
  flex: 1;
  min-width: 0;
  font-size: 12px;
  color: #888;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.pl-step-actions {
  display: flex;
  gap: 4px;
  flex-shrink: 0;
}

.pl-step-btn {
  width: 24px;
  height: 24px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: 1px solid #e0e0e0;
  border-radius: 4px;
  background: #fff;
  color: #666;
  cursor: pointer;
  font-size: 12px;
}

.pl-step-btn:hover:not(:disabled) {
  background: #e8f5e9;
  border-color: #a5d6a7;
  color: #2e7d32;
}

.pl-step-btn:disabled {
  opacity: 0.35;
  cursor: not-allowed;
}

.pl-step-del:hover {
  background: #ffebee !important;
  border-color: #ef9a9a !important;
  color: #c62828 !important;
}

.pl-step-config {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.pl-config-select,
.pl-config-input {
  height: 28px;
  padding: 0 8px;
  border: 1px solid #ddd;
  border-radius: 4px;
  font-size: 12px;
  background: #fafafa;
}

.pl-config-input {
  flex: 1;
  min-width: 200px;
}

.pl-config-num {
  width: 56px;
  height: 28px;
  padding: 0 6px;
  border: 1px solid #ddd;
  border-radius: 4px;
  font-size: 12px;
}

.pl-config-label {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: #666;
}

/* ===== 添加步骤 ===== */
.pl-add-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.pl-add-label {
  font-size: 13px;
  color: #666;
}

.pl-add-btn {
  padding: 6px 12px;
  font-size: 13px;
  background: #fff;
  border: 1px dashed #bdbdbd;
  border-radius: 6px;
  cursor: pointer;
  color: #444;
  transition: all 0.2s ease;
}

.pl-add-btn:hover {
  border-color: #4caf50;
  color: #2e7d32;
  background: #f6fbf6;
}

.pl-add-apk {
  margin-left: auto;
  border-style: solid;
  border-color: #a5d6a7;
  color: #2e7d32;
}

/* ===== 执行控制 ===== */
.pl-run-bar {
  display: flex;
  align-items: center;
  gap: 10px;
}

.pl-mode-label {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: #666;
}

.pl-run-btn {
  padding: 8px 22px;
  font-size: 14px;
  font-weight: 600;
  color: #fff;
  background: linear-gradient(135deg, #4caf50, #43a047);
  border: none;
  border-radius: 8px;
  cursor: pointer;
  box-shadow: 0 3px 12px rgba(76, 175, 80, 0.3);
  transition: all 0.25s ease;
}

.pl-run-btn:hover:not(:disabled) {
  transform: translateY(-1px);
  box-shadow: 0 5px 16px rgba(76, 175, 80, 0.4);
}

.pl-run-btn:disabled {
  filter: grayscale(0.5);
  cursor: not-allowed;
}

.pl-stop-btn {
  padding: 8px 18px;
  font-size: 14px;
  font-weight: 600;
  color: #fff;
  background: #f44336;
  border: none;
  border-radius: 8px;
  cursor: pointer;
}

.pl-stop-btn:hover {
  background: #d32f2f;
}

/* ===== 日志 ===== */
.pl-log-card {
  background: #1e2430;
  border-radius: 10px;
  overflow: hidden;
  flex-shrink: 0;
}

.pl-log-head {
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

.pl-log-file {
  font-size: 11px;
  font-weight: 400;
  color: #5f7189;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.pl-log-box {
  height: 150px;
  overflow-y: auto;
  padding: 8px 14px;
  font-family: Consolas, 'Courier New', monospace;
  font-size: 12px;
  line-height: 1.7;
}

.pl-log-empty {
  color: #5a6b80;
}

.pl-log-line {
  white-space: pre-wrap;
  word-break: break-all;
}

.log-info { color: #b7c5d6; }
.log-ok { color: #7cd992; }
.log-err { color: #ff8a80; }

/* ===== APK 仓库弹窗 ===== */
.apk-repo-mask {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 200;
}

.apk-repo-dialog {
  width: 520px;
  max-width: 92vw;
  max-height: 70vh;
  display: flex;
  flex-direction: column;
  background: #fff;
  border-radius: 12px;
  box-shadow: 0 12px 48px rgba(0, 0, 0, 0.25);
  overflow: hidden;
}

.apk-repo-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  font-size: 15px;
  font-weight: 600;
  color: #333;
  background: #fafafa;
  border-bottom: 1px solid #f0f0f0;
}

.apk-repo-close {
  width: 26px;
  height: 26px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: #888;
  cursor: pointer;
  font-size: 13px;
}

.apk-repo-close:hover {
  background: #ffebee;
  color: #c62828;
}

.apk-repo-body {
  padding: 14px 16px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  overflow: hidden;
}

.apk-repo-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}

.apk-repo-hint {
  font-size: 12px;
  color: #999;
}

.apk-repo-list {
  flex: 1;
  min-height: 120px;
  max-height: 320px;
  overflow-y: auto;
  border: 1px solid #eef1f4;
  border-radius: 8px;
}

.apk-repo-empty {
  padding: 32px;
  text-align: center;
  color: #999;
  font-size: 13px;
}

.apk-repo-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 12px;
  border-bottom: 1px solid #f5f6f8;
  font-size: 13px;
}

.apk-repo-item:last-child {
  border-bottom: none;
}

.apk-repo-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: #333;
}

.apk-repo-size {
  flex-shrink: 0;
  font-size: 12px;
  color: #888;
}

.apk-repo-del {
  flex-shrink: 0;
  padding: 3px 10px;
  font-size: 12px;
  background: #fff;
  color: #c62828;
  border: 1px solid #ef9a9a;
  border-radius: 4px;
  cursor: pointer;
}

.apk-repo-del:hover {
  background: #ffebee;
}
</style>
