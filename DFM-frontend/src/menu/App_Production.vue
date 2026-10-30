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
  type: string;
  apkName?: string;
  scriptName?: string;
  repeat?: number;
  saveDir?: string;
  path?: string;
  command?: string;
  packageName?: string;
  activity?: string;
  keycode?: number | string;
  action?: string;
  stream?: string;
  delayAfter?: number | string;
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
    packageName: s.packageName ?? '',
    activity: s.activity ?? '',
    keycode: s.keycode ?? '',
    action: s.action ?? 'up',
    stream: s.stream ?? 'media',
    delayAfter: s.delayAfter ?? '',
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
  startApp: { label: '启动应用', icon: '🚀' },
  stopApp: { label: '停止应用', icon: '⏹️' },
  uninstallApp: { label: '卸载应用', icon: '🧹' },
  keyevent: { label: '模拟按键', icon: '🔘' },
  volume: { label: '音量控制', icon: '🔊' },
  reboot: { label: '重启设备', icon: '🔄' },
  rebootLoader: { label: '重启进Loader', icon: '⚠️' },
  cleanMemory: { label: '清理内存', icon: '💾' },
};

// p3：ADB工具全部动作型功能，按分类组织（查看型功能如投屏/文件管理/日志窗口为交互界面，不纳入流水线）
const STEP_CATEGORIES: Array<{ name: string; types: string[] }> = [
  { name: '基础', types: ['installApk', 'simulate', 'screenshot', 'deleteFolder', 'shell'] },
  { name: '应用管理', types: ['startApp', 'stopApp', 'uninstallApp'] },
  { name: '设备控制', types: ['keyevent', 'volume', 'reboot', 'rebootLoader'] },
  { name: '系统维护', types: ['cleanMemory'] },
];

const stepDefaultParams = (type: string): Record<string, any> => {
  switch (type) {
    case 'installApk': return { apkName: apkOptions.value[0]?.name || '' };
    case 'simulate': return { scriptName: scriptOptions.value[0] || '', repeat: 1 };
    case 'screenshot': return { saveDir: '' };
    case 'shell': return { command: '' };
    case 'deleteFolder': return { path: '/sdcard/' };
    case 'startApp': return { packageName: '', activity: '' };
    case 'stopApp':
    case 'uninstallApp': return { packageName: '' };
    case 'keyevent': return { keycode: 4, repeat: 1 };
    case 'volume': return { action: 'up', stream: 'media' };
    case 'reboot':
    case 'rebootLoader':
    case 'cleanMemory': return {};
    default: return {};
  }
};

// 常用按键（下拉选择用），完整键值仍可用「自定义键值」输入
const COMMON_KEYS: Array<{ code: number; name: string }> = [
  { code: 4, name: '返回' },
  { code: 3, name: '主页' },
  { code: 187, name: '多任务' },
  { code: 26, name: '电源/锁屏' },
  { code: 24, name: '音量+' },
  { code: 25, name: '音量-' },
  { code: 164, name: '静音键' },
  { code: 82, name: '菜单' },
  { code: 23, name: '方向键·确认' },
  { code: 19, name: '方向键·上' },
  { code: 20, name: '方向键·下' },
  { code: 21, name: '方向键·左' },
  { code: 22, name: '方向键·右' },
  { code: 66, name: '回车' },
  { code: 67, name: '退格' },
  { code: 84, name: '搜索' },
];

const keyPresetOf = (s: PipelineStep): string => {
  const hit = COMMON_KEYS.find(k => k.code === Number(s.keycode));
  return hit ? String(hit.code) : 'custom';
};

const onKeyPresetChange = (s: PipelineStep, ev: Event) => {
  const v = (ev.target as HTMLSelectElement).value;
  s.keycode = v === 'custom' ? '' : Number(v);
};

const keyNameOf = (keycode: number | string | undefined): string => {
  const hit = COMMON_KEYS.find(k => k.code === Number(keycode));
  return hit ? `${hit.name}（${hit.code}）` : `键值 ${keycode ?? '?'}`;
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
// p3：最近使用的步骤类型（localStorage 持久化，最多 5 个，按最近点击顺序）
const RECENT_KEY = 'dfm_pipeline_recent_steps';
const recentSteps = ref<string[]>([]);
const RECENT_MAX = 5;
const RECENT_BASE = ['installApk', 'simulate', 'screenshot', 'deleteFolder', 'shell'];

const loadRecentSteps = () => {
  try {
    const saved = JSON.parse(localStorage.getItem(RECENT_KEY) || '[]');
    if (Array.isArray(saved)) {
      recentSteps.value = saved.filter((t: any) => typeof t === 'string' && !!STEP_META[t]).slice(0, RECENT_MAX);
    }
  } catch { /* 忽略损坏的存储 */ }
};

const recordStepUsage = (type: string) => {
  const next = [type, ...recentSteps.value.filter(t => t !== type)].slice(0, RECENT_MAX);
  recentSteps.value = next;
  try { localStorage.setItem(RECENT_KEY, JSON.stringify(next)); } catch { /* 忽略 */ }
};

// 添加行展示：有历史用历史（不足补基础类型），无历史用基础类型
const visibleAddSteps = computed(() => {
  const list = recentSteps.value.length > 0
    ? [...recentSteps.value]
    : [...RECENT_BASE];
  for (const t of RECENT_BASE) {
    if (list.length >= RECENT_MAX) break;
    if (!list.includes(t)) list.push(t);
  }
  return list.slice(0, RECENT_MAX);
});

const moreVisible = ref(false);            // 「更多」步骤弹窗

const addStep = (type: string) => {
  if (!STEP_META[type]) return;
  steps.value.push({ type, ...stepDefaultParams(type), _uid: ++_stepUid });
  recordStepUsage(type);
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
  if (s.type === 'deleteFolder') return s.path || '';
  if (s.type === 'startApp') return s.activity ? `${s.packageName}/${s.activity}` : (s.packageName || '（未填写包名）');
  if (s.type === 'stopApp' || s.type === 'uninstallApp') return s.packageName || '（未填写包名）';
  if (s.type === 'keyevent') return `${keyNameOf(s.keycode)} ×${s.repeat || 1}`;
  if (s.type === 'volume') return { up: '音量+', down: '音量-', mute: '静音' }[s.action || 'up'] || s.action;
  if (s.type === 'reboot') return 'adb reboot';
  if (s.type === 'rebootLoader') return 'adb reboot bootloader';
  if (s.type === 'cleanMemory') return '杀后台+释放页缓存';
  return '';
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
  loadRecentSteps();
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
                    <template v-else-if="s.type === 'startApp'">
                      <input v-model="s.packageName" class="pl-config-input" type="text" placeholder="应用包名，如 com.example.app">
                      <input v-model="s.activity" class="pl-config-input" type="text" placeholder="Activity（可选），如 .MainActivity">
                    </template>
                    <template v-else-if="s.type === 'stopApp' || s.type === 'uninstallApp'">
                      <input v-model="s.packageName" class="pl-config-input" type="text" placeholder="应用包名，如 com.example.app">
                    </template>
                    <template v-else-if="s.type === 'keyevent'">
                      <select
                        class="pl-config-select"
                        :value="keyPresetOf(s)"
                        @change="onKeyPresetChange(s, $event)"
                      >
                        <option v-for="k in COMMON_KEYS" :key="k.code" :value="String(k.code)">{{ k.name }}（{{ k.code }}）</option>
                        <option value="custom">自定义键值...</option>
                      </select>
                      <input
                        v-if="keyPresetOf(s) === 'custom'"
                        v-model="s.keycode"
                        class="pl-config-num"
                        type="number"
                        min="0"
                        max="65535"
                        placeholder="键值"
                      >
                      <label class="pl-config-label">次数
                        <input v-model.number="s.repeat" type="number" min="1" max="20" class="pl-config-num">
                      </label>
                    </template>
                    <template v-else-if="s.type === 'volume'">
                      <select v-model="s.action" class="pl-config-select">
                        <option value="up">音量+</option>
                        <option value="down">音量-</option>
                        <option value="mute">静音</option>
                      </select>
                      <select v-model="s.stream" class="pl-config-select" :disabled="s.action !== 'mute'" title="仅静音时生效">
                        <option value="media">媒体</option>
                        <option value="ring">铃声</option>
                        <option value="alarm">闹钟</option>
                        <option value="notification">通知</option>
                      </select>
                    </template>
                    <template v-else-if="s.type === 'deleteFolder'">
                      <input v-model="s.path" class="pl-config-input" type="text" placeholder="设备上的文件夹路径，如 /sdcard/xxx">
                    </template>
                    <template v-else>
                      <span class="pl-config-hint">{{ stepSummary(s) }}</span>
                    </template>
                    <label v-if="['screenshot','deleteFolder','keyevent','reboot','rebootLoader','cleanMemory'].includes(s.type)" class="pl-config-label">
                      完成后等待(s)
                      <input v-model.number="s.delayAfter" type="number" min="0" class="pl-config-num" placeholder="0">
                    </label>
                  </div>
                </div>
              </div>
            </div>

            <!-- 添加步骤 -->
            <div class="pl-add-row">
              <span class="pl-add-label">添加步骤：</span>
              <button v-for="t in visibleAddSteps" :key="t" class="pl-add-btn" @click="addStep(t)">
                {{ STEP_META[t].icon }} {{ STEP_META[t].label }}
              </button>
              <button class="pl-add-btn" @click="moreVisible = true">⋯ 更多</button>
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

    <!-- 更多步骤弹窗（p3：按分类展示全部步骤类型） -->
    <div v-if="moreVisible" class="apk-repo-mask" @click.self="moreVisible = false">
      <div class="apk-repo-dialog step-more-dialog">
        <div class="apk-repo-head">
          <span>添加步骤 · 全部功能</span>
          <button class="apk-repo-close" @click="moreVisible = false">✕</button>
        </div>
        <div class="apk-repo-body step-more-body">
          <div v-for="cat in STEP_CATEGORIES" :key="cat.name" class="step-more-cat">
            <div class="step-more-cat-name">{{ cat.name }}</div>
            <div class="step-more-grid">
              <button
                v-for="t in cat.types"
                :key="t"
                class="step-more-item"
                @click="addStep(t); moreVisible = false"
              >
                <span class="step-more-icon">{{ STEP_META[t].icon }}</span>
                <span>{{ STEP_META[t].label }}</span>
              </button>
            </div>
          </div>
          <div class="step-more-note">投屏、文件管理、日志窗口等查看型功能为交互界面，不作为流水线步骤</div>
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

.pl-config-hint {
  font-size: 12px;
  color: #999;
}

/* ===== 更多步骤弹窗 ===== */
.step-more-dialog {
  width: 620px;
}

.step-more-body {
  overflow-y: auto;
  gap: 14px;
}

.step-more-cat-name {
  font-size: 13px;
  font-weight: 600;
  color: #444;
  margin-bottom: 8px;
}

.step-more-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(130px, 1fr));
  gap: 8px;
}

.step-more-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
  font-size: 13px;
  background: #fafafa;
  border: 1px solid #e5e9ee;
  border-radius: 8px;
  cursor: pointer;
  color: #444;
  transition: all 0.15s ease;
}

.step-more-item:hover {
  border-color: #4caf50;
  color: #2e7d32;
  background: #f6fbf6;
}

.step-more-icon {
  font-size: 16px;
}

.step-more-note {
  font-size: 12px;
  color: #aaa;
  border-top: 1px dashed #e5e9ee;
  padding-top: 10px;
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
