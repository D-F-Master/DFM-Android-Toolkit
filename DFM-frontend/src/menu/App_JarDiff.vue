<script lang="ts" setup>
import { computed, nextTick, onMounted, ref, onUnmounted, watch } from 'vue';
import { useAppConfig } from '../composables/useAppConfig';

const emit = defineEmits(['back']);

// ===== Tab 注册表（后续 jar 包相关工具加在这里） =====
const TAB_ITEMS = [
  { key: 'diff', label: 'jar包对比' },
  { key: 'codeEdit', label: 'jar代码修改' }
];

// 按配置过滤后的 Tab（未配置的默认显示）
const { isFeatureOn } = useAppConfig();
const visibleTabs = computed(() => TAB_ITEMS.filter(tab => isFeatureOn(`jarDiffTabs.${tab.key}`)));

const activeTab = ref('diff');

// 当前 Tab 被关掉时自动跳到第一个可见 Tab
watch(visibleTabs, (tabs) => {
  if (tabs.length && !tabs.some(t => t.key === activeTab.value)) {
    activeTab.value = tabs[0].key;
  }
}, { immediate: true });

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

const acceptTypes = ['.jar', '.war', '.zip'];

const validateDropped = (file: File | undefined | null): string | null => {
  if (!file) return null;
  const lower = file.name.toLowerCase();
  if (!acceptTypes.some((ext) => lower.endsWith(ext))) {
    return `仅支持 ${acceptTypes.join(' / ')} 文件，已忽略：${file.name}`;
  }
  return null;
};

const draggingA = ref(false);
const draggingB = ref(false);

const onDropA = (event: DragEvent) => {
  draggingA.value = false;
  const file = event.dataTransfer?.files && event.dataTransfer.files.length > 0 ? event.dataTransfer.files[0] : null;
  const err = validateDropped(file);
  if (err) { errorText.value = err; return; }
  if (file) fileA.value = file;
};

const onDropB = (event: DragEvent) => {
  draggingB.value = false;
  const file = event.dataTransfer?.files && event.dataTransfer.files.length > 0 ? event.dataTransfer.files[0] : null;
  const err = validateDropped(file);
  if (err) { errorText.value = err; return; }
  if (file) fileB.value = file;
};

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

// ==================== jar 代码修改 ====================
// 流程：上传 jar → 左侧树选 class → CFR 反编译源码 → 编辑 → javac 编译替换 → 下载新 jar
// 后端处理 BOOT-INF 前缀与匿名类 $N 的替换；这里只做展示与编辑

const jarId = ref('');
const jarName = ref('');
const jarFat = ref(false);
const jarEntries = ref<string[]>([]);
const editUploading = ref(false);
const editDragging = ref(false);
const editError = ref('');
const editMessage = ref('');
const editInput = ref<HTMLInputElement | null>(null);

// 树：目录展开状态
const expandedDirs = ref(new Set<string>());
const selectedClass = ref('');
const classSource = ref('');
const sourceLoading = ref(false);
const compiling = ref(false);
const newJarFile = ref('');

interface TreeRow {
  depth: number;
  name: string;
  path: string;
  isDir: boolean;
}

const visibleTreeRows = computed<TreeRow[]>(() => {
  const rows: TreeRow[] = [];
  // 由 entries 构建目录树：com/demo/Calc.class → com → demo → Calc.class
  const root = new Map<string, any>();
  for (const p of jarEntries.value) {
    const parts = p.split('/');
    let level = root;
    for (let i = 0; i < parts.length - 1; i++) {
      if (!level.has(parts[i])) level.set(parts[i], new Map<string, any>());
      level = level.get(parts[i]);
    }
    if (!level.has('__files__')) level.set('__files__', []);
    (level.get('__files__') as string[]).push(parts[parts.length - 1]);
  }
  const walk = (level: Map<string, any>, depth: number, prefix: string) => {
    const dirs = [...level.keys()].filter((k) => k !== '__files__').sort();
    for (const name of dirs) {
      const path = prefix ? `${prefix}/${name}` : name;
      rows.push({ depth, name, path, isDir: true });
      if (expandedDirs.value.has(path)) walk(level.get(name), depth + 1, path);
    }
    for (const f of (level.get('__files__') || []).sort()) {
      const path = prefix ? `${prefix}/${f}` : f;
      rows.push({ depth, name: f, path, isDir: false });
    }
  };
  walk(root, 0, '');
  return rows;
});

const toggleTreeDir = (row: TreeRow) => {
  const next = new Set(expandedDirs.value);
  if (next.has(row.path)) next.delete(row.path);
  else next.add(row.path);
  expandedDirs.value = next;
};

const validateEditDropped = (files: FileList): File | null => {
  const acceptTypes = ['.jar', '.war'];
  const hit = Array.from(files).find((f) => acceptTypes.some((ext) => f.name.toLowerCase().endsWith(ext)));
  if (!hit) {
    editError.value = `仅支持 ${acceptTypes.join(' / ')} 文件，已忽略：${Array.from(files).map((f) => f.name).join('、')}`;
    return null;
  }
  return hit;
};

const onEditDrop = (ev: DragEvent) => {
  editDragging.value = false;
  const file = validateEditDropped(ev.dataTransfer!.files);
  if (file) uploadEditJar(file);
};

const uploadEditJar = async (file: File) => {
  editUploading.value = true;
  editError.value = '';
  editMessage.value = '';
  newJarFile.value = '';
  selectedClass.value = '';
  classSource.value = '';
  try {
    const form = new FormData();
    form.append('file', file);
    const response = await fetch(`${API_BASE.value}/api/jaredit/upload`, { method: 'POST', body: form });
    const json = await response.json();
    if (json.code === 200) {
      jarId.value = json.data.id;
      jarName.value = json.data.name;
      jarFat.value = json.data.fat;
      jarEntries.value = json.data.entries || [];
      expandedDirs.value = new Set();
      // 默认展开第一层目录
      const first = new Set<string>();
      for (const p of jarEntries.value) first.add(p.split('/')[0]);
      expandedDirs.value = first;
    } else {
      editError.value = json.message || '上传失败';
    }
  } catch (error) {
    editError.value = `无法连接到服务器 ${API_BASE.value}`;
  } finally {
    editUploading.value = false;
  }
};

const onEditFileChosen = (ev: Event) => {
  const input = ev.target as HTMLInputElement;
  if (input.files && input.files.length > 0) uploadEditJar(input.files[0]);
  input.value = '';
};

const loadClassSource = async (path: string) => {
  selectedClass.value = path;
  sourceLoading.value = true;
  editError.value = '';
  editMessage.value = '';
  try {
    const response = await fetch(`${API_BASE.value}/api/jaredit/source/${jarId.value}?path=${encodeURIComponent(path)}`);
    const json = await response.json();
    if (json.code === 200) {
      classSource.value = json.data.source;
    } else {
      editError.value = json.message || '反编译失败';
      classSource.value = '';
    }
  } catch (error) {
    editError.value = `无法连接到服务器 ${API_BASE.value}`;
  } finally {
    sourceLoading.value = false;
  }
};

// 编译并替换：成功后合并 replaced 条目进树（新增的 $N 匿名类），并准备下载
const compileAndReplace = async () => {
  if (!selectedClass.value || compiling.value) return;
  compiling.value = true;
  editError.value = '';
  editMessage.value = '';
  try {
    const form = new FormData();
    form.append('sid', jarId.value);
    form.append('path', selectedClass.value);
    form.append('source', classSource.value);
    const response = await fetch(`${API_BASE.value}/api/jaredit/save`, { method: 'POST', body: form });
    const json = await response.json();
    if (json.code === 200) {
      newJarFile.value = json.data.file;
      editMessage.value = `编译成功，已替换 ${json.data.replaced.length} 个文件：${json.data.replaced.join('、')}`;
      // 新生成的 $N 匿名类不在原条目列表里，合并进树
      const known = new Set(jarEntries.value);
      for (const full of json.data.replaced as string[]) {
        const rel = jarFat.value ? full.replace(/^BOOT-INF\/classes\//, '') : full;
        if (rel.endsWith('.class') && !known.has(rel)) jarEntries.value.push(rel);
      }
      jarEntries.value.sort();
    } else {
      editError.value = json.message || '编译失败';
    }
  } catch (error) {
    editError.value = `无法连接到服务器 ${API_BASE.value}`;
  } finally {
    compiling.value = false;
  }
};

const downloadNewJar = () => {
  if (!newJarFile.value) return;
  const a = document.createElement('a');
  a.href = `${API_BASE.value}/api/jaredit/download/${jarId.value}`;
  a.download = newJarFile.value;
  a.click();
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
          <div class="tabs">
            <button
              v-for="tab in visibleTabs"
              :key="tab.key"
              class="tab-button"
              :class="{ active: activeTab === tab.key }"
              @click="activeTab = tab.key"
            >
              {{ tab.label }}
            </button>
          </div>
          <div v-if="activeTab === 'diff'">
          <!-- 文件选择与操作 -->
          <div class="card">
            <div class="card-header">
              <span class="card-icon">📦</span>
              jar 包更新对比
            </div>
            <div class="card-body">
              <div class="file-row">
                <div
                  class="file-pick"
                  :class="{ dragging: draggingA }"
                  @click="pickA"
                  @dragover.prevent
                  @dragenter.prevent="draggingA = true"
                  @dragleave="draggingA = false"
                  @drop.prevent="onDropA"
                >
                  <span class="pick-label old">旧版本</span>
                  <span class="pick-name" :class="{ placeholder: !fileA }">
                    {{ fileA ? fileA.name : '点击选择或拖入 jar / war / zip 文件' }}
                  </span>
                </div>
                <input ref="fileInputA" type="file" accept=".jar,.war,.zip" style="display: none" @change="handleFileA" />
                <div
                  class="file-pick"
                  :class="{ dragging: draggingB }"
                  @click="pickB"
                  @dragover.prevent
                  @dragenter.prevent="draggingB = true"
                  @dragleave="draggingB = false"
                  @drop.prevent="onDropB"
                >
                  <span class="pick-label new">新版本</span>
                  <span class="pick-name" :class="{ placeholder: !fileB }">
                    {{ fileB ? fileB.name : '点击选择或拖入 jar / war / zip 文件' }}
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

          <!-- jar 代码修改 -->
          <div v-if="activeTab === 'codeEdit'">
            <!-- 上传区（未上传或重新上传时显示） -->
            <div class="card">
              <div class="card-header">
                <span class="card-icon">✏️</span>
                jar 代码修改
              </div>
              <div class="card-body">
                <div class="file-row">
                  <div
                    class="file-pick"
                    :class="{ dragging: editDragging }"
                    @click="editInput?.click()"
                    @dragover.prevent
                    @dragenter.prevent="editDragging = true"
                    @dragleave="editDragging = false"
                    @drop.prevent="onEditDrop"
                  >
                    <span class="pick-label new">jar 包</span>
                    <span class="pick-name" :class="{ placeholder: !jarName }">
                      {{ jarName ? `${jarName}（${jarEntries.length} 个 class${jarFat ? '，Spring Boot 结构' : ''}）` : '点击选择或拖入 jar / war 文件' }}
                    </span>
                  </div>
                  <input ref="editInput" type="file" accept=".jar,.war" style="display: none" @change="onEditFileChosen" />
                </div>
                <div v-if="editUploading" class="progress-msg">正在上传解包…</div>
                <div v-if="editError" class="error-message edit-error">{{ editError }}</div>
                <div v-if="editMessage" class="edit-success">{{ editMessage }}</div>
                <div v-if="newJarFile" class="actions">
                  <button class="check-button" @click="downloadNewJar">⬇ 下载 {{ newJarFile }}</button>
                </div>
              </div>
            </div>

            <!-- 左树右编辑器 -->
            <div v-if="jarId" class="jaredit-layout">
              <aside class="card jaredit-tree">
                <div class="card-header">class 结构</div>
                <div class="tree-items">
                  <div
                    v-for="row in visibleTreeRows"
                    :key="row.path"
                    class="tree-row"
                    :class="{ selected: !row.isDir && selectedClass === row.path }"
                    :style="{ paddingLeft: 8 + row.depth * 14 + 'px' }"
                    @click="row.isDir ? toggleTreeDir(row) : loadClassSource(row.path)"
                  >
                    <span class="tree-icon">{{ row.isDir ? (expandedDirs.has(row.path) ? '📂' : '📁') : '📄' }}</span>
                    <span class="tree-name">{{ row.name }}</span>
                  </div>
                </div>
              </aside>
              <section class="card jaredit-editor">
                <div class="card-header editor-header">
                  <span class="editor-path">{{ selectedClass || '选择左侧 class 文件' }}</span>
                  <button
                    class="check-button editor-save"
                    :disabled="!selectedClass || sourceLoading || compiling"
                    @click="compileAndReplace"
                  >{{ compiling ? '编译中…' : '💾 编译并替换' }}</button>
                </div>
                <div class="editor-body">
                  <div v-if="sourceLoading" class="diff-hint">反编译中…</div>
                  <textarea
                    v-else
                    v-model="classSource"
                    class="code-editor"
                    spellcheck="false"
                    :disabled="!selectedClass"
                    placeholder="反编译出的 java 源码会显示在这里，可直接修改后点「编译并替换」"
                  ></textarea>
                </div>
              </section>
            </div>
          </div>
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

.file-pick.dragging {
  border-color: #4caf50;
  background: #e8f5e9;
  border-style: solid;
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

/* ===== jar 代码修改 ===== */
.jaredit-layout {
  display: flex;
  gap: 12px;
  align-items: stretch;
}

.jaredit-tree {
  width: 280px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
}

.jaredit-tree .card-header {
  flex-shrink: 0;
}

.tree-items {
  flex: 1;
  overflow: auto;
  max-height: 480px;
  padding: 6px 0;
}

.tree-row {
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 4px 8px;
  cursor: pointer;
  font-size: 13px;
  color: #333;
  white-space: nowrap;
  user-select: none;
}

.tree-row:hover {
  background: #f5f5f5;
}

.tree-row.selected {
  background: #e8f5e9;
  color: #2e7d32;
  font-weight: 600;
}

.tree-icon {
  flex-shrink: 0;
  font-size: 12px;
}

.tree-name {
  overflow: hidden;
  text-overflow: ellipsis;
}

.jaredit-editor {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}

.editor-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
}

.editor-path {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
}

.editor-save {
  flex-shrink: 0;
  height: 32px;
  padding: 0 16px;
  font-size: 13px;
}

.editor-body {
  flex: 1;
  padding: 12px 16px;
}

.code-editor {
  width: 100%;
  min-height: 440px;
  box-sizing: border-box;
  resize: vertical;
  font-family: Consolas, Monaco, 'Courier New', monospace;
  font-size: 12px;
  line-height: 1.5;
  color: #333;
  background: #fff;
  border: 1px solid #ddd;
  border-radius: 8px;
  padding: 10px 12px;
  outline: none;
}

.code-editor:focus {
  border-color: #4caf50;
  box-shadow: 0 0 0 3px rgba(76, 175, 80, 0.1);
}

.code-editor:disabled {
  background: #fafafa;
  color: #999;
}

.edit-success {
  margin-top: 12px;
  padding: 10px 14px;
  background: #e8f5e9;
  color: #2e7d32;
  border: 1px solid #c8e6c9;
  border-radius: 8px;
  font-size: 13px;
  word-break: break-all;
}

.edit-error {
  margin-top: 12px;
  white-space: pre-wrap;
  font-family: Consolas, Monaco, 'Courier New', monospace;
  font-size: 12px;
}

.progress-msg {
  margin-top: 12px;
  font-size: 13px;
  color: #666;
}
</style>
