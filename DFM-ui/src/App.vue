<template>
  <div class="page">
    <header class="page-header">
      <h1>DFM 组件库</h1>
      <p class="page-sub">从 DFM-frontend 提取的通用组件 · Vue 3 + TypeScript + Vite</p>
    </header>

    <main class="page-body">
      <!-- ==================== ModalDialog ==================== -->
      <section class="card">
        <div class="card-head">
          <h2>ModalDialog</h2>
          <span class="card-tag">通用对话框</span>
        </div>
        <p class="card-desc">
          消息提示、确认框、输入框、右上角通知四种模式；每次显示都会触发对应回调。
        </p>

        <div class="demo-row">
          <button class="demo-btn" @click="openMessage">消息提示</button>
          <button class="demo-btn" @click="openConfirm">确认框</button>
          <button class="demo-btn" @click="openPrompt">输入框</button>
          <button class="demo-btn" @click="openNotification('success')">成功通知</button>
          <button class="demo-btn" @click="openNotification('error')">错误通知</button>
          <button class="demo-btn" @click="openNotification('info')">提示通知</button>
        </div>

        <div class="result-line">
          <span class="result-label">最近回调</span>
          <code class="result-value">{{ lastEvent }}</code>
        </div>

        <details class="usage">
          <summary>用法</summary>
          <pre>{{ modalUsage }}</pre>
        </details>
      </section>

      <!-- ==================== FileEditorDialog ==================== -->
      <section class="card">
        <div class="card-head">
          <h2>FileEditorDialog</h2>
          <span class="card-tag">文件编辑器</span>
        </div>
        <p class="card-desc">
          CodeMirror 6 全屏编辑器：按扩展名自动切换语法高亮，跟随系统亮暗主题，Ctrl/Cmd + S 保存，Esc 关闭。
        </p>

        <div class="demo-row">
          <button class="demo-btn primary" @click="openEditor">打开编辑器（示例 config.json）</button>
        </div>

        <div class="result-line">
          <span class="result-label">保存结果</span>
          <span v-if="savedAt" class="result-value">{{ savedAt }} 保存，内容共 {{ savedContent.length }} 字符</span>
          <code v-else class="result-value muted">尚未保存过</code>
        </div>
        <pre v-if="savedAt" class="saved-content">{{ savedContent }}</pre>

        <details class="usage">
          <summary>用法</summary>
          <pre>{{ editorUsage }}</pre>
        </details>
      </section>
    </main>

    <!-- ==================== 组件实例 ==================== -->
    <ModalDialog
      :visible="modal.visible"
      :type="modal.type"
      :title="modal.title"
      :message="modal.message"
      :placeholder="modal.placeholder"
      :confirm-text="modal.confirmText"
      :variant="modal.variant"
      @confirm="onModalConfirm"
      @cancel="lastEvent = 'cancel'"
      @close="modal.visible = false"
    />

    <FileEditorDialog
      :visible="editor.visible"
      :file-name="editor.fileName"
      :initial-content="editor.initialContent"
      :loading="editor.loading"
      :saving="editor.saving"
      @save="onEditorSave"
      @close="editor.visible = false"
    />
  </div>
</template>

<script lang="ts" setup>
import { reactive, ref } from 'vue'
import ModalDialog from './components/ModalDialog.vue'
import FileEditorDialog from './components/FileEditorDialog.vue'

type ModalType = 'message' | 'confirm' | 'prompt' | 'notification'
type ModalVariant = 'success' | 'error' | 'info'

// ==================== ModalDialog ====================
const lastEvent = ref('—')

const modal = reactive({
  visible: false,
  type: 'message' as ModalType,
  title: '',
  message: '',
  placeholder: '',
  confirmText: '确定',
  variant: 'success' as ModalVariant
})

// 每次打开先把上一次的字段复位，避免残留
const openModal = (patch: Partial<Omit<typeof modal, 'visible'>>) => {
  Object.assign(
    modal,
    { title: '', message: '', placeholder: '', confirmText: '确定', variant: 'success' },
    patch
  )
  modal.visible = true
}

const openMessage = () =>
  openModal({ type: 'message', title: '提示', message: '操作已成功完成', confirmText: '知道了' })

const openConfirm = () =>
  openModal({
    type: 'confirm',
    title: '确认删除',
    message: '确定要删除这个文件吗？此操作不可撤销。',
    confirmText: '删除'
  })

const openPrompt = () =>
  openModal({
    type: 'prompt',
    title: '新建文件夹',
    message: '请输入文件夹名称：',
    placeholder: '例如: MyFolder',
    confirmText: '创建'
  })

const openNotification = (variant: ModalVariant) => {
  const text = {
    success: '文件已成功导入',
    error: '连接失败，请检查网络',
    info: '正在同步数据，请稍候'
  }[variant]
  openModal({ type: 'notification', message: text, variant })
}

// confirm / prompt 都会带一个 value，notification 与 message 为空串
const onModalConfirm = (value: string) => {
  lastEvent.value = modal.type === 'prompt' ? `confirm: ${value || '(空)'}` : 'confirm'
}

// ==================== FileEditorDialog ====================
const SAMPLE = `{
  "name": "dfm-ui",
  "version": "0.0.0",
  "components": ["ModalDialog", "FileEditorDialog"],
  "enabled": true
}
`

const editor = reactive({
  visible: false,
  fileName: 'config.json',
  initialContent: '',
  loading: false,
  saving: false
})

const savedContent = ref('')
const savedAt = ref('')

// 模拟父组件的异步读取：先打开空编辑器，内容到达后再同步进去
const openEditor = () => {
  editor.visible = true
  editor.loading = true
  editor.initialContent = ''
  window.setTimeout(() => {
    if (!editor.visible) return
    editor.initialContent = SAMPLE
    editor.loading = false
  }, 600)
}

// 模拟写回：保存成功后关闭编辑器并弹一条通知
const onEditorSave = (text: string) => {
  editor.saving = true
  window.setTimeout(() => {
    editor.saving = false
    savedContent.value = text
    savedAt.value = new Date().toLocaleTimeString('zh-CN')
    editor.visible = false
    openModal({ type: 'notification', message: 'config.json 已保存（模拟写回）', variant: 'success' })
  }, 500)
}

// ==================== 用法片段 ====================
const modalUsage = `<ModalDialog
  :visible="visible"
  type="confirm"
  title="确认删除"
  message="确定要删除这个文件吗？"
  confirm-text="删除"
  @confirm="onConfirm"
  @cancel="onCancel"
  @close="visible = false"
/>`

const editorUsage = `<FileEditorDialog
  :visible="editorVisible"
  file-name="config.json"
  :initial-content="content"
  :loading="loading"
  :saving="saving"
  @save="(text) => content = text"
  @close="editorVisible = false"
/>`
</script>

<style scoped>
.page {
  max-width: 920px;
  margin: 0 auto;
  padding: 40px 24px 64px;
}

.page-header {
  margin-bottom: 28px;
}

.page-header h1 {
  margin: 0 0 8px;
  font-size: 26px;
  font-weight: 700;
  color: #1a1a1a;
}

.page-sub {
  margin: 0;
  font-size: 13px;
  color: #888;
}

.page-body {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.card {
  background: #fff;
  border-radius: 10px;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.08);
  padding: 22px 24px;
}

.card-head {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 8px;
}

.card-head h2 {
  margin: 0;
  font-size: 17px;
  font-weight: 600;
  color: #1a1a1a;
}

.card-tag {
  padding: 2px 8px;
  border-radius: 10px;
  background: #e6f4ff;
  color: #1890ff;
  font-size: 11px;
  font-weight: 500;
}

.card-desc {
  margin: 0 0 18px;
  font-size: 13px;
  line-height: 1.6;
  color: #666;
}

.demo-row {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-bottom: 16px;
}

.demo-btn {
  padding: 7px 16px;
  border: 1px solid #d9d9d9;
  border-radius: 6px;
  background: #fff;
  color: #333;
  font-size: 13px;
  font-family: inherit;
  cursor: pointer;
  transition: all 0.2s ease;
}

.demo-btn:hover {
  border-color: #1890ff;
  color: #1890ff;
}

.demo-btn.primary {
  background: #1890ff;
  border-color: #1890ff;
  color: #fff;
}

.demo-btn.primary:hover {
  background: #40a9ff;
  border-color: #40a9ff;
  color: #fff;
}

.result-line {
  display: flex;
  align-items: baseline;
  gap: 10px;
  font-size: 13px;
}

.result-label {
  flex-shrink: 0;
  color: #999;
  font-size: 12px;
}

.result-value {
  font-family: Consolas, Monaco, monospace;
  font-size: 12px;
  color: #4caf50;
}

.result-value.muted {
  color: #bbb;
}

.saved-content {
  margin: 12px 0 0;
  padding: 12px 14px;
  background: #fafafa;
  border: 1px solid #eee;
  border-radius: 6px;
  font-family: Consolas, Monaco, monospace;
  font-size: 12px;
  line-height: 1.6;
  color: #333;
  overflow-x: auto;
  white-space: pre;
}

.usage {
  margin-top: 16px;
  border-top: 1px solid #f0f0f0;
  padding-top: 12px;
}

.usage summary {
  font-size: 12px;
  color: #888;
  cursor: pointer;
  user-select: none;
}

.usage summary:hover {
  color: #1890ff;
}

.usage pre {
  margin: 10px 0 0;
  padding: 12px 14px;
  background: #fafafa;
  border: 1px solid #eee;
  border-radius: 6px;
  font-family: Consolas, Monaco, monospace;
  font-size: 12px;
  line-height: 1.6;
  color: #333;
  overflow-x: auto;
}
</style>
