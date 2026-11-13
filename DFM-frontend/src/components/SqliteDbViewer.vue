<template>
  <div class="dbv-page">
    <div class="dbv-card">
      <!-- 顶部：返回 / 文件名 / 回传设备 -->
      <div class="dbv-header">
        <button class="dbv-back-btn" @click="emit('close')" title="返回文件列表">
          <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2">
            <polyline points="15 18 9 12 15 6"/>
          </svg>
          返回文件列表
        </button>
        <span class="dbv-title" :title="remotePath">
          <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2">
            <ellipse cx="12" cy="5" rx="9" ry="3"/>
            <path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"/>
            <path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"/>
          </svg>
          {{ fileName }}
        </span>
        <span v-if="toast.text" class="dbv-toast" :class="{ err: toast.err }">{{ toast.text }}</span>
        <div class="dbv-header-actions">
          <button class="dbv-btn" @click="refreshTables" :disabled="loading">刷新表列表</button>
          <button class="dbv-btn primary" @click="pushBack" :disabled="loading">保存到设备</button>
        </div>
      </div>

      <!-- 加载中 / 出错 -->
      <div v-if="loading && !sessionId" class="dbv-state">正在拉取数据库文件到本地...</div>
      <div v-else-if="loadError" class="dbv-state dbv-err">{{ loadError }}</div>

      <!-- 左表列表 / 右数据表格 -->
      <div v-else-if="sessionId" class="dbv-layout">
        <aside class="dbv-sidebar">
          <div class="dbv-sidebar-title">表（{{ tables.length }}）</div>
          <div
            v-for="t in tables"
            :key="t.name"
            class="dbv-table-item"
            :class="{ active: t.name === currentTable }"
            @click="selectTable(t.name)"
            :title="t.name"
          >
            <span class="dbv-table-name">{{ t.name }}</span>
            <span class="dbv-table-count">{{ t.count === null ? '?' : t.count }}</span>
          </div>
          <div v-if="tables.length === 0" class="dbv-sidebar-empty">无用户表</div>
        </aside>

        <section class="dbv-main">
          <div class="dbv-table-toolbar">
            <input
              v-model="keyword"
              class="dbv-search"
              placeholder="全列模糊搜索，回车执行"
              @keyup.enter="doQuery(1)"
            />
            <button class="dbv-btn" @click="doQuery(1)" :disabled="!currentTable || queryLoading">查询</button>
            <button class="dbv-btn" @click="doQuery()" :disabled="!currentTable || queryLoading">刷新</button>
            <button
              class="dbv-btn primary"
              @click="openInsert"
              :disabled="!currentTable || queryLoading"
              title="向当前表插入一行"
            >新增行</button>
            <span class="dbv-toolbar-spacer"></span>
            <select v-model.number="pageSize" class="dbv-pagesize" @change="doQuery(1)">
              <option :value="20">20 行/页</option>
              <option :value="50">50 行/页</option>
              <option :value="100">100 行/页</option>
              <option :value="200">200 行/页</option>
            </select>
          </div>

          <div class="dbv-grid-wrap">
            <div v-if="queryLoading" class="dbv-state">查询中...</div>
            <div v-else-if="!currentTable" class="dbv-state">请从左侧选择一张表</div>
            <div v-else-if="rows.length === 0" class="dbv-state">无数据{{ keyword ? '（当前筛选条件下）' : '' }}</div>
            <table v-else class="dbv-grid">
              <thead>
                <tr>
                  <th v-if="hasRowid" class="dbv-rid">rowid</th>
                  <th v-for="c in columns" :key="c.name" :title="`${c.name} ${c.type}${c.pk ? ' · 主键' : ''}`">
                    {{ c.name }}<span v-if="c.type" class="dbv-col-type">{{ c.type }}</span>
                  </th>
                  <th class="dbv-actions-col">操作</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(row, idx) in rows" :key="hasRowid ? row.__rid__ : idx">
                  <td v-if="hasRowid" class="dbv-rid">{{ row.__rid__ }}</td>
                  <td v-for="c in columns" :key="c.name" :title="fmtCell(row[c.name])">
                    <span :class="{ 'dbv-null': row[c.name] === null }">{{ fmtCell(row[c.name]) }}</span>
                  </td>
                  <td class="dbv-actions-col">
                    <button class="dbv-row-btn" @click="openEdit(row)" title="编辑该行">
                      <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2">
                        <path d="M17 3a2.828 2.828 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5L17 3z"/>
                      </svg>
                    </button>
                    <button class="dbv-row-btn danger" @click="askDeleteRow(row)" title="删除该行">
                      <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2">
                        <polyline points="3 6 5 6 21 6"/>
                        <path d="M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2"/>
                      </svg>
                    </button>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>

          <div class="dbv-pager">
            <span>共 {{ total }} 行 · 第 {{ page }} / {{ pageCount }} 页</span>
            <button class="dbv-btn" @click="doQuery(page - 1)" :disabled="page <= 1 || queryLoading">上一页</button>
            <button class="dbv-btn" @click="doQuery(page + 1)" :disabled="page >= pageCount || queryLoading">下一页</button>
          </div>
        </section>
      </div>
    </div>

    <!-- 新增/编辑行弹窗 -->
    <div v-if="editVisible" class="dbv-mask" @click.self="editVisible = false">
      <div class="dbv-dialog">
        <div class="dbv-dialog-head">
          <span>{{ editMode === 'insert' ? `新增行 · ${currentTable}` : `编辑行 · ${currentTable} · rowid = ${editRowid}` }}</span>
          <button class="dbv-close" @click="editVisible = false">&times;</button>
        </div>
        <div class="dbv-dialog-body">
          <div v-if="!hasRowid && editMode === 'update'" class="dbv-dialog-hint warn">
            该表不支持 rowid（WITHOUT ROWID），无法编辑/删除
          </div>
          <div class="dbv-dialog-hint">值以文本提交，SQLite 按列类型自动转换；空值请勾选 NULL（INTEGER 主键留空自增也勾选 NULL）</div>
          <div v-for="c in columns" :key="c.name" class="dbv-field">
            <label class="dbv-field-label" :title="`${c.type}${c.pk ? ' · 主键' : ''}${c.notnull ? ' · NOT NULL' : ''}`">
              {{ c.name }}
              <span class="dbv-field-meta">{{ c.type }}{{ c.pk ? ' PK' : '' }}{{ c.notnull ? ' NN' : '' }}</span>
            </label>
            <input
              v-model="editValues[c.name]"
              class="dbv-field-input"
              :disabled="editNulls[c.name]"
              placeholder="NULL"
            />
            <label class="dbv-null-check">
              <input type="checkbox" v-model="editNulls[c.name]" /> NULL
            </label>
          </div>
        </div>
        <div class="dbv-dialog-foot">
          <button class="dbv-btn" @click="editVisible = false">取消</button>
          <button class="dbv-btn primary" @click="saveEdit" :disabled="editSaving">
            {{ editSaving ? '保存中...' : '保存' }}
          </button>
        </div>
      </div>
    </div>

    <!-- 删除行确认弹窗 -->
    <div v-if="confirmVisible" class="dbv-mask" @click.self="confirmVisible = false">
      <div class="dbv-dialog dbv-dialog-small">
        <div class="dbv-dialog-head">
          <span>确认删除</span>
          <button class="dbv-close" @click="confirmVisible = false">&times;</button>
        </div>
        <div class="dbv-dialog-body">
          <div class="dbv-dialog-hint">确定删除 {{ currentTable }} 表中 rowid = {{ confirmRowid }} 的这一行吗？</div>
        </div>
        <div class="dbv-dialog-foot">
          <button class="dbv-btn" @click="confirmVisible = false">取消</button>
          <button class="dbv-btn danger" @click="doDeleteRow" :disabled="editSaving">删除</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script lang="ts" setup>
import { ref, computed, onMounted } from 'vue'
import axios from 'axios'

const API_BASE = 'http://localhost:3000'

const props = defineProps({
  deviceId: { type: String, required: true },
  remotePath: { type: String, required: true },
  fileName: { type: String, default: '' }
})

const emit = defineEmits(['close'])

// ===== 会话状态 =====
const sessionId = ref('')
const loading = ref(false)
const loadError = ref('')
const tables = ref<Array<{ name: string; count: number | null }>>([])

// ===== 当前表查询状态 =====
const currentTable = ref('')
const columns = ref<Array<{ name: string; type: string; notnull: boolean; pk: boolean }>>([])
const rows = ref<Array<Record<string, any>>>([])
const hasRowid = ref(true)
const total = ref(0)
const page = ref(1)
const pageSize = ref(50)
const keyword = ref('')
const queryLoading = ref(false)

// ===== 编辑弹窗状态 =====
const editVisible = ref(false)
const editMode = ref<'insert' | 'update'>('insert')
const editRowid = ref(0)
const editValues = ref<Record<string, string>>({})
const editNulls = ref<Record<string, boolean>>({})
const editSaving = ref(false)

// ===== 删除确认状态 =====
const confirmVisible = ref(false)
const confirmRowid = ref(0)

// ===== 顶部提示条 =====
const toast = ref<{ text: string; err: boolean }>({ text: '', err: false })
let toastTimer: ReturnType<typeof setTimeout> | null = null
const notify = (text: string, err = false) => {
  toast.value = { text, err }
  if (toastTimer) clearTimeout(toastTimer)
  toastTimer = setTimeout(() => { toast.value = { text: '', err: false } }, 4000)
}

const pageCount = computed(() => Math.max(1, Math.ceil(total.value / pageSize.value)))

// null 显示为灰底 NULL，其余原样
const fmtCell = (v: any) => (v === null ? 'NULL' : String(v))

// ===== 打开数据库：拉取到本地并列出表 =====
const openDb = async () => {
  loading.value = true
  loadError.value = ''
  try {
    const resp = await axios.post(`${API_BASE}/api/device/sqlite/open`, {
      deviceId: props.deviceId,
      remotePath: props.remotePath
    })
    if (resp.data.code === 200 && resp.data.data) {
      sessionId.value = resp.data.data.sessionId
      tables.value = resp.data.data.tables || []
      if (resp.data.data.walPulled?.length) {
        notify(`已同步拉取 ${resp.data.data.walPulled.join(' / ')} 附属文件`)
      }
      if (tables.value.length > 0) {
        await selectTable(tables.value[0].name)
      }
    } else {
      loadError.value = resp.data.message || '打开数据库失败'
    }
  } catch (error: any) {
    loadError.value = '打开数据库失败: ' + (error.message || '网络异常')
  } finally {
    loading.value = false
  }
}

// ===== 切换表 =====
const selectTable = async (name: string) => {
  currentTable.value = name
  keyword.value = ''
  page.value = 1
  await doQuery()
}

// ===== 查询当前表数据 =====
const doQuery = async (targetPage?: number) => {
  if (!sessionId.value || !currentTable.value) return
  queryLoading.value = true
  try {
    const resp = await axios.post(`${API_BASE}/api/device/sqlite/query`, {
      sessionId: sessionId.value,
      table: currentTable.value,
      page: targetPage ?? page.value,
      pageSize: pageSize.value,
      keyword: keyword.value
    })
    if (resp.data.code === 200 && resp.data.data) {
      columns.value = resp.data.data.columns || []
      rows.value = resp.data.data.rows || []
      hasRowid.value = !!resp.data.data.hasRowid
      total.value = resp.data.data.total || 0
      page.value = resp.data.data.page || 1
    } else {
      notify(resp.data.message || '查询失败', true)
    }
  } catch (error: any) {
    notify('查询失败: ' + (error.message || '网络异常'), true)
  } finally {
    queryLoading.value = false
  }
}

// ===== 刷新表列表（不重新拉设备文件，保留本地修改） =====
const refreshTables = async () => {
  if (!sessionId.value) return
  try {
    const resp = await axios.post(`${API_BASE}/api/device/sqlite/tables`, {
      sessionId: sessionId.value
    })
    if (resp.data.code === 200 && resp.data.data) {
      tables.value = resp.data.data.tables || []
      notify('表列表已刷新')
    } else {
      notify(resp.data.message || '刷新表列表失败', true)
    }
  } catch (error: any) {
    notify('刷新表列表失败: ' + (error.message || '网络异常'), true)
  }
}

// ===== 新增行 =====
const openInsert = () => {
  editMode.value = 'insert'
  editRowid.value = 0
  editValues.value = {}
  editNulls.value = {}
  columns.value.forEach(c => {
    editValues.value[c.name] = ''
    editNulls.value[c.name] = false
  })
  editVisible.value = true
}

// ===== 编辑行 =====
const openEdit = (row: Record<string, any>) => {
  if (!hasRowid.value) {
    notify('该表不支持 rowid（WITHOUT ROWID），无法编辑', true)
    return
  }
  editMode.value = 'update'
  editRowid.value = row.__rid__
  editValues.value = {}
  editNulls.value = {}
  columns.value.forEach(c => {
    const v = row[c.name]
    editNulls.value[c.name] = v === null
    editValues.value[c.name] = v === null ? '' : String(v)
  })
  editVisible.value = true
}

// ===== 保存新增/编辑 =====
const saveEdit = async () => {
  editSaving.value = true
  try {
    const values: Record<string, any> = {}
    columns.value.forEach(c => {
      values[c.name] = editNulls.value[c.name] ? null : editValues.value[c.name]
    })
    const url = editMode.value === 'insert'
      ? `${API_BASE}/api/device/sqlite/insert`
      : `${API_BASE}/api/device/sqlite/update`
    const payload = editMode.value === 'insert'
      ? { sessionId: sessionId.value, table: currentTable.value, values }
      : { sessionId: sessionId.value, table: currentTable.value, rowid: editRowid.value, values }
    const resp = await axios.post(url, payload)
    if (resp.data.code === 200) {
      editVisible.value = false
      notify(editMode.value === 'insert' ? '插入成功' : '更新成功')
      await doQuery()
      await refreshTables()
    } else {
      notify(resp.data.message || '保存失败', true)
    }
  } catch (error: any) {
    notify('保存失败: ' + (error.message || '网络异常'), true)
  } finally {
    editSaving.value = false
  }
}

// ===== 删除行 =====
const askDeleteRow = (row: Record<string, any>) => {
  if (!hasRowid.value) {
    notify('该表不支持 rowid（WITHOUT ROWID），无法删除', true)
    return
  }
  confirmRowid.value = row.__rid__
  confirmVisible.value = true
}

const doDeleteRow = async () => {
  editSaving.value = true
  try {
    const resp = await axios.post(`${API_BASE}/api/device/sqlite/delete`, {
      sessionId: sessionId.value,
      table: currentTable.value,
      rowid: confirmRowid.value
    })
    if (resp.data.code === 200) {
      confirmVisible.value = false
      notify('删除成功')
      await doQuery()
      await refreshTables()
    } else {
      notify(resp.data.message || '删除失败', true)
    }
  } catch (error: any) {
    notify('删除失败: ' + (error.message || '网络异常'), true)
  } finally {
    editSaving.value = false
  }
}

// ===== 推回设备 =====
const pushBack = async () => {
  if (!sessionId.value) return
  if (!window.confirm(`确定把本地修改后的 ${props.fileName} 推回设备 ${props.remotePath} 吗？\n若目标在应用私有目录，需要 root 权限。`)) {
    return
  }
  loading.value = true
  try {
    const resp = await axios.post(`${API_BASE}/api/device/sqlite/push`, {
      sessionId: sessionId.value
    })
    if (resp.data.code === 200) {
      notify('已推回设备')
    } else {
      notify(resp.data.message || '推回设备失败', true)
    }
  } catch (error: any) {
    notify('推回设备失败: ' + (error.message || '网络异常'), true)
  } finally {
    loading.value = false
  }
}

onMounted(openDb)
</script>

<style scoped>
.dbv-page {
  display: flex;
  flex-direction: column;
}
.dbv-card {
  background: #fff;
  border: 1px solid #e5e5e5;
  border-radius: 8px;
  overflow: hidden;
}
.dbv-header {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 14px;
  border-bottom: 1px solid #f0f0f0;
}
.dbv-back-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 5px 10px;
  border: 1px solid #e0e0e0;
  border-radius: 6px;
  background: #fff;
  cursor: pointer;
  font-size: 12px;
  color: #555;
}
.dbv-back-btn:hover {
  background: #f5f5f5;
}
.dbv-title {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  font-weight: 600;
  color: #333;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.dbv-toast {
  font-size: 12px;
  color: #1a7f37;
  background: #eaf7ee;
  border: 1px solid #bfe3cb;
  border-radius: 4px;
  padding: 2px 8px;
  max-width: 420px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.dbv-toast.err {
  color: #b3261e;
  background: #fdecec;
  border-color: #f3c1bd;
}
.dbv-header-actions {
  margin-left: auto;
  display: flex;
  gap: 8px;
}
.dbv-btn {
  padding: 5px 12px;
  border: 1px solid #e0e0e0;
  border-radius: 6px;
  background: #fff;
  cursor: pointer;
  font-size: 12px;
  color: #444;
}
.dbv-btn:hover:not(:disabled) {
  background: #f5f5f5;
}
.dbv-btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}
.dbv-btn.primary {
  background: #1677ff;
  border-color: #1677ff;
  color: #fff;
}
.dbv-btn.primary:hover:not(:disabled) {
  background: #3c93ff;
}
.dbv-btn.danger {
  background: #e5484d;
  border-color: #e5484d;
  color: #fff;
}
.dbv-btn.danger:hover:not(:disabled) {
  background: #ec5f63;
}
.dbv-state {
  padding: 24px;
  text-align: center;
  font-size: 13px;
  color: #888;
}
.dbv-state.dbv-err {
  color: #b3261e;
  word-break: break-all;
}
.dbv-layout {
  display: flex;
  min-height: 420px;
}
.dbv-sidebar {
  width: 210px;
  flex-shrink: 0;
  border-right: 1px solid #f0f0f0;
  overflow-y: auto;
  max-height: 560px;
}
.dbv-sidebar-title {
  padding: 8px 12px;
  font-size: 12px;
  color: #999;
  border-bottom: 1px solid #f7f7f7;
}
.dbv-sidebar-empty {
  padding: 16px 12px;
  font-size: 12px;
  color: #bbb;
  text-align: center;
}
.dbv-table-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
  padding: 7px 12px;
  cursor: pointer;
  font-size: 13px;
  color: #444;
}
.dbv-table-item:hover {
  background: #f7f9fc;
}
.dbv-table-item.active {
  background: #eaf2ff;
  color: #1677ff;
  font-weight: 600;
}
.dbv-table-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.dbv-table-count {
  flex-shrink: 0;
  font-size: 11px;
  color: #999;
  background: #f2f2f2;
  border-radius: 8px;
  padding: 1px 7px;
}
.dbv-table-item.active .dbv-table-count {
  background: #d8e6ff;
  color: #1677ff;
}
.dbv-main {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
}
.dbv-table-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border-bottom: 1px solid #f0f0f0;
}
.dbv-search {
  width: 220px;
  padding: 5px 10px;
  border: 1px solid #e0e0e0;
  border-radius: 6px;
  font-size: 12px;
  outline: none;
}
.dbv-search:focus {
  border-color: #1677ff;
}
.dbv-toolbar-spacer {
  flex: 1;
}
.dbv-pagesize {
  padding: 4px 8px;
  border: 1px solid #e0e0e0;
  border-radius: 6px;
  font-size: 12px;
  background: #fff;
  cursor: pointer;
}
.dbv-grid-wrap {
  flex: 1;
  overflow: auto;
  max-height: 520px;
}
.dbv-grid {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}
.dbv-grid th {
  position: sticky;
  top: 0;
  background: #fafafa;
  border-bottom: 1px solid #e8e8e8;
  padding: 7px 10px;
  text-align: left;
  font-weight: 600;
  color: #555;
  white-space: nowrap;
  z-index: 1;
}
.dbv-col-type {
  margin-left: 5px;
  font-size: 10px;
  font-weight: 400;
  color: #aaa;
}
.dbv-grid td {
  border-bottom: 1px solid #f3f3f3;
  padding: 6px 10px;
  color: #333;
  white-space: nowrap;
  max-width: 280px;
  overflow: hidden;
  text-overflow: ellipsis;
}
.dbv-grid tbody tr:hover {
  background: #f7f9fc;
}
.dbv-rid {
  color: #999;
  background: #fafafa;
  font-family: monospace;
}
.dbv-null {
  color: #bbb;
  font-style: italic;
  background: #f7f7f7;
  border-radius: 3px;
  padding: 0 4px;
}
.dbv-actions-col {
  white-space: nowrap;
  text-align: center;
}
.dbv-row-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  margin-right: 4px;
  border: 1px solid #e0e0e0;
  border-radius: 5px;
  background: #fff;
  cursor: pointer;
  color: #555;
}
.dbv-row-btn:hover {
  background: #f5f5f5;
}
.dbv-row-btn.danger {
  color: #e5484d;
  border-color: #f3c1bd;
}
.dbv-row-btn.danger:hover {
  background: #fdecec;
}
.dbv-pager {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border-top: 1px solid #f0f0f0;
  font-size: 12px;
  color: #777;
}
/* 弹窗 */
.dbv-mask {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
}
.dbv-dialog {
  width: 480px;
  max-width: 92vw;
  max-height: 80vh;
  display: flex;
  flex-direction: column;
  background: #fff;
  border-radius: 10px;
  box-shadow: 0 8px 30px rgba(0, 0, 0, 0.25);
}
.dbv-dialog-small {
  width: 380px;
}
.dbv-dialog-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  border-bottom: 1px solid #f0f0f0;
  font-size: 14px;
  font-weight: 600;
  color: #333;
}
.dbv-close {
  border: none;
  background: none;
  font-size: 20px;
  line-height: 1;
  color: #999;
  cursor: pointer;
}
.dbv-close:hover {
  color: #555;
}
.dbv-dialog-body {
  flex: 1;
  overflow-y: auto;
  padding: 12px 16px;
}
.dbv-dialog-hint {
  font-size: 12px;
  color: #999;
  margin-bottom: 10px;
}
.dbv-dialog-hint.warn {
  color: #b3261e;
}
.dbv-field {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}
.dbv-field-label {
  width: 130px;
  flex-shrink: 0;
  font-size: 12px;
  color: #444;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  text-align: right;
}
.dbv-field-meta {
  color: #aaa;
  font-size: 10px;
  margin-left: 3px;
}
.dbv-field-input {
  flex: 1;
  min-width: 0;
  padding: 5px 9px;
  border: 1px solid #e0e0e0;
  border-radius: 6px;
  font-size: 12px;
  outline: none;
}
.dbv-field-input:focus {
  border-color: #1677ff;
}
.dbv-field-input:disabled {
  background: #f7f7f7;
  color: #bbb;
}
.dbv-null-check {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  font-size: 11px;
  color: #888;
  flex-shrink: 0;
  cursor: pointer;
}
.dbv-dialog-foot {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding: 12px 16px;
  border-top: 1px solid #f0f0f0;
}
</style>
