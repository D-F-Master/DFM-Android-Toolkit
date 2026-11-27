<script lang="ts" setup>
import { computed, onMounted, ref, watch } from 'vue';
import { useJarCompare } from '../composables/jardiff/useJarCompare';
import { useJarCodeEdit } from '../composables/jardiff/useJarCodeEdit';
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

const jarCompare = useJarCompare(API_BASE);
const {
  fileA, fileB, fileInputA, fileInputB, comparing, percent, message, errorText,
  summary, entries, jobId, filterText, showSame, detail, detailLoading, detailHint,
  diffPos, STATUS_LABELS, canCompare, resultReady, filteredEntries, changedIndexes,
  pickA, pickB, draggingA, draggingB, onDropA, onDropB, handleFileA, handleFileB,
  startCompare, prevDiff, nextDiff, selectEntry, exportMd,
} = jarCompare;

const jarCodeEdit = useJarCodeEdit(API_BASE);
const {
  jarId, jarName, jarFat, jarEntries, editUploading, editDragging, editError,
  editMessage, editInput, expandedDirs, selectedClass, classSource, sourceLoading,
  compiling, newJarFile, visibleTreeRows, toggleTreeDir, onEditDrop,
  onEditFileChosen, compileAndReplace, downloadNewJar,
} = jarCodeEdit;

onMounted(async () => {
  await loadConfig();
  loadBackend();
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

<style scoped src="../styles/page-shell.css"></style>
<style scoped src="../styles/card.css"></style>

<style scoped>
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
