<script lang="ts" setup>
import { computed, nextTick, onMounted, ref, onUnmounted } from 'vue';

const emit = defineEmits(['back']);

const API_BASE = ref('http://localhost:3000');
const backendText = ref('');

const fileA = ref<File | null>(null);
const fileB = ref<File | null>(null);
const fileInputA = ref<HTMLInputElement | null>(null);
const fileInputB = ref<HTMLInputElement | null>(null);

const comparing = ref(false);
const percent = ref(0);
const message = ref('');
const errorText = ref('');

const summary = ref<{ total: number; same: number; changed: number; added: number; removed: number } | null>(null);
const entries = ref<Entry[]>([]);
const jobId = ref('');

const filterText = ref('');
const showSame = ref(false);

interface Entry {
  path: string;
  status: string;
  kind: string;
  left_size: string;
  right_size: string;
  note: string | null;
}

interface DiffRow {
  lno: number | null;
  ltxt: string | null;
  rno: number | null;
  rtxt: string | null;
  kind: string;
}

const detail = ref<{ path: string; status: string; kind: string; note: string | null; rows: DiffRow[] } | null>(null);
const detailLoading = ref(false);
const detailHint = ref('');
const diffPos = ref(0);
let pollTimer: number | null = null;

const STATUS_LABELS: Record<string, string> = {
  same: '相同',
  changed: '修改',
  added: '新增',
  removed: '删除',
};

const canCompare = computed(() => !!(fileA.value && fileB.value) && !comparing.value);
const resultReady = computed(() => !!summary.value && !comparing.value);

const filteredEntries = computed(() =>
  entries.value.filter((e) =>
    (showSame.value || e.status !== 'same') &&
    (!filterText.value || e.path.toLowerCase().includes(filterText.value.toLowerCase()))
  )
);

const changedIndexes = computed(() =>
  detail.value ? detail.value.rows.map((r, i) => (r.kind !== 'same' ? i : -1)).filter((i) => i >= 0) : []
);

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

const loadBackend = async () => {
  try {
    const response = await fetch(`${API_BASE.value}/api/jardiff/backend`);
    const json = await response.json();
    if (json.code === 200) {
      backendText.value = json.data.text;
    }
  } catch (error) {
    console.error('查询反编译环境失败:', error);
  }
};

const goHome = () => {
  emit('back');
};

const clearDetail = () => {
  detail.value = null;
  detailHint.value = '';
  diffPos.value = 0;
};

const pickA = () => fileInputA.value?.click();
const pickB = () => fileInputB.value?.click();

const handleFileA = (event: Event) => {
  const target = event.target as HTMLInputElement;
  fileA.value = target.files && target.files.length > 0 ? target.files[0] : null;
};

const handleFileB = (event: Event) => {
  const target = event.target as HTMLInputElement;
  fileB.value = target.files && target.files.length > 0 ? target.files[0] : null;
};

const poll = async (id: string) => {
  try {
    const response = await fetch(`${API_BASE.value}/api/jardiff/status/${id}`);
    const json = await response.json();
    if (json.code === 200) {
      percent.value = json.data.percent;
      message.value = json.data.message;
      if (json.data.done) {
        comparing.value = false;
        if (json.data.error) {
          errorText.value = `对比失败：${json.data.error}`;
        } else {
          summary.value = json.data.summary;
          entries.value = json.data.entries;
        }
        return;
      }
    } else {
      errorText.value = json.message || '查询进度失败';
      comparing.value = false;
      return;
    }
  } catch (error) {
    errorText.value = `无法连接到服务器 ${API_BASE.value}，请确保后端服务已启动`;
    comparing.value = false;
    return;
  }
  pollTimer = window.setTimeout(() => poll(id), 600);
};

const startCompare = async () => {
  if (!canCompare.value) return;
  errorText.value = '';
  summary.value = null;
  entries.value = [];
  jobId.value = '';
  clearDetail();
  comparing.value = true;
  percent.value = 0;
  message.value = '上传中…';
  try {
    const fd = new FormData();
    fd.append('old', fileA.value!);
    fd.append('new', fileB.value!);
    const response = await fetch(`${API_BASE.value}/api/jardiff/compare`, {
      method: 'POST',
      body: fd,
    });
    const json = await response.json();
    if (json.code === 200 && json.data && json.data.id) {
      jobId.value = json.data.id;
      poll(jobId.value);
      return;
    }
    errorText.value = json.message || '提交对比失败';
    comparing.value = false;
  } catch (error) {
    errorText.value = `无法连接到服务器 ${API_BASE.value}，请确保后端服务已启动`;
    comparing.value = false;
  }
};

const scrollToRow = (index: number) => {
  nextTick(() => {
    const el = document.getElementById(`diff-row-${index}`);
    if (el) {
      el.scrollIntoView({ block: 'center' });
    }
  });
};

const goToDiff = (pos: number) => {
  if (changedIndexes.value.length === 0) return;
  diffPos.value = (pos + changedIndexes.value.length) % changedIndexes.value.length;
  scrollToRow(changedIndexes.value[diffPos.value]);
};

const prevDiff = () => goToDiff(diffPos.value - 1);
const nextDiff = () => goToDiff(diffPos.value + 1);

const selectEntry = async (e: Entry) => {
  if (comparing.value) return;
  clearDetail();
  if (e.status === 'same') {
    detailHint.value = `「${e.path}」在两个版本中内容一致`;
    return;
  }
  detailLoading.value = true;
  try {
    const response = await fetch(`${API_BASE.value}/api/jardiff/entry/${jobId.value}?path=${encodeURIComponent(e.path)}`);
    const json = await response.json();
    if (json.code === 200) {
      detail.value = {
        path: json.data.path,
        status: json.data.status,
        kind: json.data.kind,
        note: json.data.note,
        rows: json.data.rows.map((r: any[]) => ({
          lno: r[0], ltxt: r[1], rno: r[2], rtxt: r[3], kind: r[4],
        })),
      };
      diffPos.value = 0;
      if (changedIndexes.value.length > 0) {
        scrollToRow(changedIndexes.value[0]);
      }
    } else {
      detailHint.value = json.message || '读取差异失败';
    }
  } catch (error) {
    detailHint.value = `无法连接到服务器 ${API_BASE.value}`;
  } finally {
    detailLoading.value = false;
  }
};

const exportMd = async () => {
  if (!resultReady.value) return;
  try {
    const response = await fetch(`${API_BASE.value}/api/jardiff/export/${jobId.value}`);
    const json = await response.json();
    if (json.code === 200) {
      const blob = new Blob([json.data.content], { type: 'text/markdown;charset=utf-8' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = json.data.filename;
      a.click();
      URL.revokeObjectURL(url);
    } else {
      errorText.value = json.message || '导出失败';
    }
  } catch (error) {
    errorText.value = `无法连接到服务器 ${API_BASE.value}`;
  }
};

onMounted(async () => {
  await loadConfig();
  loadBackend();
});

onUnmounted(() => {
  if (pollTimer !== null) {
    window.clearTimeout(pollTimer);
  }
});
</script>

<template>
  <div class="app">
    <button class="back-button" @click="goHome">&lt;</button>
    <main>
      <div class="container">
        <div class="content">
          <!-- 文件选择与操作 -->
          <div class="card">
            <div class="card-header">
              <span class="card-icon">📦</span>
              jar 包更新对比
            </div>
            <div class="card-body">
              <div class="file-row">
                <div class="file-pick" @click="pickA">
                  <span class="pick-label old">旧版本</span>
                  <span class="pick-name" :class="{ placeholder: !fileA }">
                    {{ fileA ? fileA.name : '点击选择 jar / war / zip 文件' }}
                  </span>
                </div>
                <input ref="fileInputA" type="file" accept=".jar,.war,.zip" style="display: none" @change="handleFileA" />
                <div class="file-pick" @click="pickB">
                  <span class="pick-label new">新版本</span>
                  <span class="pick-name" :class="{ placeholder: !fileB }">
                    {{ fileB ? fileB.name : '点击选择 jar / war / zip 文件' }}
                  </span>
                </div>
                <input ref="fileInputB" type="file" accept=".jar,.war,.zip" style="display: none" @change="handleFileB" />
              </div>
              <div class="actions">
                <button class="check-button" :disabled="!canCompare" @click="startCompare">
                  {{ comparing ? '对比中…' : '开始对比' }}
                </button>
                <button class="check-button" :disabled="!resultReady" @click="exportMd">导出报告</button>
                <span class="backend-text">{{ backendText }}</span>
              </div>
              <div v-if="comparing" class="progress-wrap">
                <div class="progress-bar">
                  <div class="progress-fill" :style="{ width: percent + '%' }"></div>
                </div>
                <span class="progress-msg">{{ message }}</span>
              </div>
              <div v-if="errorText" class="error-message">{{ errorText }}</div>
            </div>
          </div>

          <!-- 对比结果 -->
          <template v-if="summary">
            <div class="summary-chips">
              <span class="chip">共 {{ summary.total }}</span>
              <span class="chip st-same">相同 {{ summary.same }}</span>
              <span class="chip st-changed">修改 {{ summary.changed }}</span>
              <span class="chip st-added">新增 {{ summary.added }}</span>
              <span class="chip st-removed">删除 {{ summary.removed }}</span>
            </div>
            <div class="jardiff-body">
              <aside class="card file-list">
                <div class="list-toolbar">
                  <input
                    v-model="filterText"
                    class="filter-input"
                    type="text"
                    placeholder="过滤文件名…"
                  />
                  <label class="show-same">
                    <input v-model="showSame" type="checkbox" />
                    显示相同
                  </label>
                </div>
                <div class="list-items">
                  <div
                    v-for="e in filteredEntries"
                    :key="e.path"
                    class="list-item"
                    :class="{ selected: detail && detail.path === e.path }"
                    @click="selectEntry(e)"
                  >
                    <span class="item-path" :title="e.path">{{ e.path }}</span>
                    <span class="item-size">{{ e.left_size }} → {{ e.right_size }}</span>
                    <span class="status-badge" :class="'st-' + e.status">{{ STATUS_LABELS[e.status] || e.status }}</span>
                  </div>
                  <div v-if="filteredEntries.length === 0" class="list-empty">没有符合条件的文件</div>
                </div>
              </aside>
              <section class="card diff-panel">
                <template v-if="detailHint">
                  <div class="diff-hint">{{ detailHint }}</div>
                </template>
                <template v-else-if="detailLoading">
                  <div class="diff-hint">加载中…</div>
                </template>
                <template v-else-if="detail">
                  <div class="diff-header">
                    <span class="diff-path" :title="detail.path">{{ detail.path }}</span>
                    <div class="diff-nav">
                      <button class="nav-button" :disabled="changedIndexes.length === 0" @click="prevDiff">↑ 上一处</button>
                      <span class="nav-pos">{{ changedIndexes.length ? diffPos + 1 : 0 }}/{{ changedIndexes.length }}</span>
                      <button class="nav-button" :disabled="changedIndexes.length === 0" @click="nextDiff">下一处 ↓</button>
                    </div>
                  </div>
                  <div v-if="detail.note" class="diff-note">{{ detail.note }}</div>
                  <div class="diff-table">
                    <div
                      v-for="(row, i) in detail.rows"
                      :id="'diff-row-' + i"
                      :key="i"
                      class="diff-row"
                      :class="'k-' + row.kind"
                    >
                      <div class="diff-cell left">
                        <span class="line-no">{{ row.lno ?? '' }}</span>
                        <span class="line-text">{{ row.ltxt ?? '' }}</span>
                      </div>
                      <div class="diff-cell right">
                        <span class="line-no">{{ row.rno ?? '' }}</span>
                        <span class="line-text">{{ row.rtxt ?? '' }}</span>
                      </div>
                    </div>
                  </div>
                </template>
                <template v-else>
                  <div class="diff-hint">← 从左侧选择文件查看差异</div>
                </template>
              </section>
            </div>
          </template>
        </div>
      </div>
    </main>
  </div>
</template>

<style scoped>
/* ===== 返回按钮 ===== */
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

.app {
  position: relative;
  width: 100%;
  height: 100vh;
  min-height: 544px;
}

.container {
  display: flex;
  width: 100%;
  height: 100%;
  overflow: hidden;
  background: #f0f2f5;
}

.content {
  flex: 1;
  padding: 16px;
  overflow-y: auto;
  height: 100%;
  background: #f0f2f5;
}

/* ===== 卡片通用样式（匹配端口工具） ===== */
.card {
  background: #fff;
  border-radius: 10px;
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.08);
  overflow: hidden;
  margin-bottom: 12px;
}

.card-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 16px;
  font-size: 14px;
  font-weight: 600;
  color: #333;
  border-bottom: 1px solid #f0f0f0;
  background: #fafafa;
}

.card-icon {
  display: flex;
  align-items: center;
  color: #4caf50;
}

.card-body {
  padding: 16px;
}

/* ===== 文件选择 ===== */
.file-row {
  display: flex;
  gap: 10px;
  margin-bottom: 12px;
}

.file-pick {
  flex: 1;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 14px;
  border: 1px dashed #bbb;
  border-radius: 8px;
  cursor: pointer;
  transition: border-color 0.25s ease, background 0.25s ease;
  min-width: 0;
}

.file-pick:hover {
  border-color: #4caf50;
  background: #f6fbf6;
}

.pick-label {
  flex-shrink: 0;
  padding: 3px 10px;
  border-radius: 6px;
  font-size: 12px;
  font-weight: 600;
  color: #fff;
}

.pick-label.old {
  background: #ff9800;
}

.pick-label.new {
  background: #4caf50;
}

.pick-name {
  font-size: 13px;
  color: #333;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.pick-name.placeholder {
  color: #999;
}

/* ===== 操作区 ===== */
.actions {
  display: flex;
  align-items: center;
  gap: 10px;
}

.check-button {
  height: 40px;
  padding: 0 22px;
  background: #4caf50;
  color: #fff;
  border: none;
  border-radius: 8px;
  cursor: pointer;
  font-size: 14px;
  font-weight: 600;
  font-family: inherit;
  transition: all 0.35s cubic-bezier(0.16, 1, 0.3, 1);
  display: inline-flex;
  align-items: center;
  gap: 6px;
  white-space: nowrap;
  box-shadow: 0 4px 14px -4px rgba(76, 175, 80, 0.3);
}

.check-button:hover:not(:disabled) {
  filter: brightness(1.05);
  transform: translateY(-1px);
}

.check-button:disabled {
  opacity: 0.45;
  cursor: not-allowed;
  box-shadow: none;
}

.backend-text {
  font-size: 12px;
  color: #888;
}

/* ===== 进度条 ===== */
.progress-wrap {
  margin-top: 12px;
}

.progress-bar {
  height: 8px;
  border-radius: 4px;
  background: #eee;
  overflow: hidden;
}

.progress-fill {
  height: 100%;
  background: linear-gradient(90deg, #66bb6a, #4caf50);
  border-radius: 4px;
  transition: width 0.4s ease;
}

.progress-msg {
  display: block;
  margin-top: 6px;
  font-size: 12px;
  color: #666;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* ===== 错误消息 ===== */
.error-message {
  background: #fff3f3;
  color: #e74c3c;
  padding: 10px 14px;
  border-radius: 8px;
  border: 1px solid #f5c6cb;
  margin-top: 12px;
  font-size: 13px;
}

/* ===== 汇总 chips ===== */
.summary-chips {
  display: flex;
  gap: 8px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}

.chip {
  padding: 6px 14px;
  border-radius: 16px;
  background: #fff;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.06);
  font-size: 13px;
  color: #555;
  font-weight: 500;
}

.chip.st-same {
  color: #607d8b;
}

.chip.st-changed {
  color: #e67e22;
}

.chip.st-added {
  color: #4caf50;
}

.chip.st-removed {
  color: #e74c3c;
}

/* ===== 列表 + diff 双栏 ===== */
.jardiff-body {
  display: flex;
  gap: 12px;
  align-items: stretch;
}

.file-list {
  width: 300px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  margin-bottom: 0;
}

.list-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
  border-bottom: 1px solid #f0f0f0;
  background: #fafafa;
}

.filter-input {
  flex: 1;
  height: 32px;
  padding: 0 10px;
  border: 1px solid #ddd;
  border-radius: 6px;
  font-size: 13px;
  font-family: inherit;
  min-width: 0;
}

.filter-input:focus {
  outline: none;
  border-color: #4caf50;
}

.show-same {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  color: #666;
  cursor: pointer;
  white-space: nowrap;
}

.list-items {
  flex: 1;
  overflow-y: auto;
  max-height: 480px;
}

.list-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border-bottom: 1px solid #f5f5f5;
  cursor: pointer;
  transition: background 0.2s ease;
}

.list-item:hover {
  background: #f6fbf6;
}

.list-item.selected {
  background: #e8f5e9;
}

.item-path {
  flex: 1;
  font-size: 12px;
  color: #333;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
}

.item-size {
  flex-shrink: 0;
  font-size: 11px;
  color: #999;
  white-space: nowrap;
}

.status-badge {
  flex-shrink: 0;
  padding: 2px 8px;
  border-radius: 10px;
  font-size: 11px;
  font-weight: 600;
  color: #fff;
}

.status-badge.st-same {
  background: #b0bec5;
}

.status-badge.st-changed {
  background: #e67e22;
}

.status-badge.st-added {
  background: #4caf50;
}

.status-badge.st-removed {
  background: #e74c3c;
}

.list-empty {
  padding: 20px;
  text-align: center;
  font-size: 13px;
  color: #999;
}

/* ===== diff 面板 ===== */
.diff-panel {
  flex: 1;
  min-width: 0;
  margin-bottom: 0;
  display: flex;
  flex-direction: column;
}

.diff-hint {
  padding: 40px 20px;
  text-align: center;
  font-size: 14px;
  color: #999;
}

.diff-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 10px 14px;
  border-bottom: 1px solid #f0f0f0;
  background: #fafafa;
}

.diff-path {
  font-size: 13px;
  font-weight: 600;
  color: #333;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
}

.diff-nav {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 8px;
}

.nav-button {
  height: 30px;
  padding: 0 12px;
  border: 1px solid #ddd;
  background: #fff;
  border-radius: 6px;
  cursor: pointer;
  font-size: 12px;
  color: #555;
  font-family: inherit;
  transition: all 0.2s ease;
}

.nav-button:hover:not(:disabled) {
  border-color: #4caf50;
  color: #4caf50;
}

.nav-button:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.nav-pos {
  font-size: 12px;
  color: #888;
  min-width: 40px;
  text-align: center;
}

.diff-note {
  padding: 8px 14px;
  font-size: 12px;
  color: #8a6d3b;
  background: #fcf8e3;
  border-bottom: 1px solid #f5e79e;
}

.diff-table {
  flex: 1;
  overflow: auto;
  max-height: 480px;
  font-family: Consolas, Monaco, 'Courier New', monospace;
  font-size: 12px;
  line-height: 1.5;
}

.diff-row {
  display: flex;
  border-bottom: 1px solid #f7f7f7;
}

.diff-cell {
  flex: 1;
  display: flex;
  min-width: 0;
  padding: 0;
}

.diff-cell.left {
  border-right: 1px solid #eee;
}

.line-no {
  flex-shrink: 0;
  width: 42px;
  padding: 2px 6px;
  text-align: right;
  color: #aaa;
  background: #fafafa;
  user-select: none;
}

.line-text {
  flex: 1;
  padding: 2px 8px;
  white-space: pre-wrap;
  word-break: break-all;
  color: #333;
  min-width: 0;
}

/* 差异行着色（左=旧 右=新） */
.diff-row.k-changed .diff-cell.left .line-text {
  background: #fdecea;
  color: #c0392b;
}

.diff-row.k-changed .diff-cell.right .line-text {
  background: #e8f5e9;
  color: #2e7d32;
}

.diff-row.k-removed .diff-cell.left .line-text {
  background: #fdecea;
  color: #c0392b;
}

.diff-row.k-added .diff-cell.right .line-text {
  background: #e8f5e9;
  color: #2e7d32;
}
</style>
