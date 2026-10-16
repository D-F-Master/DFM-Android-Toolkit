<template>
  <div v-if="visible" class="editor-overlay">
    <div class="editor-window">
      <!-- 顶部工具条 -->
      <div class="editor-header">
        <span class="editor-title" :title="fileName">{{ fileName }}</span>
        <span v-if="isModified" class="editor-modified-tag">未保存</span>
        <div class="editor-actions">
          <button class="editor-btn cancel" @click="handleCancel" :disabled="saving">取消</button>
          <button class="editor-btn save" @click="handleSave" :disabled="saving || !isModified">
            {{ saving ? '保存中...' : '保存' }}
          </button>
          <button class="editor-close" @click="handleCancel" :disabled="saving">&times;</button>
        </div>
      </div>

      <!-- 编辑区 -->
      <div class="editor-body">
        <div v-if="loadError" class="editor-error">{{ loadError }}</div>
        <template v-else>
          <div class="editor-cm-host" v-show="!loading">
            <VueCodemirror
              v-model="content"
              :extensions="extensions"
              :style="{ height: '100%' }"
              placeholder="文件内容为空"
            />
          </div>
          <div v-if="loading" class="editor-loading">正在读取文件，请稍候...</div>
        </template>
      </div>
    </div>
  </div>
</template>

<script lang="ts" setup>
import { ref, computed, watch, onUnmounted, nextTick } from 'vue'
import { Codemirror as VueCodemirror } from 'vue-codemirror'
import { EditorView } from '@codemirror/view'
import { oneDark } from '@codemirror/theme-one-dark'
import { javascript } from '@codemirror/lang-javascript'
import { json } from '@codemirror/lang-json'
import { xml } from '@codemirror/lang-xml'
import { css } from '@codemirror/lang-css'
import { html } from '@codemirror/lang-html'
import { markdown } from '@codemirror/lang-markdown'
import { python } from '@codemirror/lang-python'

const props = defineProps({
  visible: { type: Boolean, default: false },
  fileName: { type: String, default: '' },
  // 打开时由父组件填入的原始内容
  initialContent: { type: String, default: '' },
  loading: { type: Boolean, default: false },
  loadError: { type: String, default: '' },
  saving: { type: Boolean, default: false }
})

const emit = defineEmits(['save', 'close'])

const content = ref('')
const darkMode = ref(false)

const isModified = computed(() => content.value !== props.initialContent)

// 扩展名 → 语言映射
const langFor = (name: string) => {
  const ext = (name.split('.').pop() || '').toLowerCase()
  switch (ext) {
    case 'js': case 'mjs': case 'cjs': case 'ts':
      return javascript({ typescript: ext === 'ts' })
    case 'json':
      return json()
    case 'xml': case 'plist':
      return xml()
    case 'css':
      return css()
    case 'html': case 'htm':
      return html()
    case 'md': case 'markdown':
      return markdown()
    case 'py':
      return python()
    default:
      return []
  }
}

const extensions = computed(() => {
  const exts: any[] = [
    EditorView.lineWrapping
  ]
  const lang = langFor(props.fileName)
  if (Array.isArray(lang)) {
    exts.push(...lang)
  } else {
    exts.push(lang)
  }
  if (darkMode.value) exts.push(oneDark)
  return exts
})

// 检测系统亮暗主题（与项目其他页面的 prefers-color-scheme 约定一致）
const media = window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null
const syncTheme = () => { darkMode.value = !!media?.matches }
if (media) {
  if (media.addEventListener) media.addEventListener('change', syncTheme)
  syncTheme()
}
onUnmounted(() => {
  if (media && media.removeEventListener) media.removeEventListener('change', syncTheme)
})

// 打开时用父组件传入的原始内容重置；
// initialContent 是异步加载的（下载完成后才有值），所以内容到达时也要同步进来
watch(
  () => [props.visible, props.initialContent] as const,
  ([v, initial]) => {
    if (v) {
      content.value = initial
    }
  },
  { immediate: true }
)

const handleSave = () => {
  emit('save', content.value)
}

const handleCancel = () => {
  if (isModified.value && !props.saving) {
    if (!window.confirm('有未保存的修改，确定放弃吗？')) return
  }
  emit('close')
}

// Esc 关闭
const onKeydown = (e: KeyboardEvent) => {
  if (!props.visible) return
  if (e.key === 'Escape') handleCancel()
  if ((e.ctrlKey || e.metaKey) && e.key === 's') {
    e.preventDefault()
    if (isModified.value && !props.saving) handleSave()
  }
}
window.addEventListener('keydown', onKeydown)
onUnmounted(() => window.removeEventListener('keydown', onKeydown))
</script>

<style scoped>
.editor-overlay {
  position: fixed;
  inset: 0;
  z-index: 2000;
  background: rgba(0, 0, 0, 0.55);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
}

.editor-window {
  width: 100%;
  height: 100%;
  max-width: 1000px;
  background: #fff;
  border-radius: 10px;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.editor-header {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 16px;
  border-bottom: 1px solid #f0f0f0;
  background: #fafafa;
}

.editor-title {
  flex: 1;
  min-width: 0;
  font-size: 13px;
  font-weight: 600;
  color: #333;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.editor-modified-tag {
  flex-shrink: 0;
  padding: 2px 8px;
  border-radius: 10px;
  background: #fff3e0;
  color: #e65100;
  font-size: 11px;
  font-weight: 500;
}

.editor-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.editor-btn {
  padding: 5px 14px;
  border: none;
  border-radius: 6px;
  font-size: 12px;
  font-weight: 500;
  font-family: inherit;
  cursor: pointer;
  transition: all 0.2s ease;
}

.editor-btn.save {
  background: #4caf50;
  color: #fff;
}

.editor-btn.save:hover:not(:disabled) {
  background: #43a047;
}

.editor-btn.save:disabled {
  background: #bdbdbd;
  cursor: not-allowed;
}

.editor-btn.cancel {
  background: #f5f5f5;
  color: #666;
  border: 1px solid #e0e0e0;
}

.editor-btn.cancel:hover:not(:disabled) {
  background: #eee;
}

.editor-close {
  background: none;
  border: none;
  font-size: 20px;
  line-height: 1;
  color: #999;
  cursor: pointer;
  padding: 0 4px;
}

.editor-close:hover:not(:disabled) {
  color: #666;
}

.editor-body {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

.editor-cm-host {
  flex: 1;
  min-height: 0;
  overflow: hidden;
}

.editor-cm-host :deep(.cm-editor) {
  height: 100%;
  font-size: 13px;
}

.editor-cm-host :deep(.cm-editor.cm-focused) {
  outline: none;
}

.editor-error {
  margin: 16px;
  padding: 12px;
  background: #ffebee;
  border: 1px solid #ffcdd2;
  border-radius: 6px;
  color: #c62828;
  font-size: 13px;
}

.editor-loading {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #999;
  font-size: 14px;
}

@media (prefers-color-scheme: dark) {
  .editor-window {
    background: #2d2f36;
  }

  .editor-header {
    background: #33363e;
    border-bottom-color: #40434a;
  }

  .editor-title {
    color: #e0e0e0;
  }

  .editor-modified-tag {
    background: #4a3720;
    color: #ffb74d;
  }

  .editor-btn.cancel {
    background: #3a3d45;
    color: #b0b0b0;
    border-color: #555;
  }

  .editor-btn.cancel:hover:not(:disabled) {
    background: #44474f;
  }

  .editor-close {
    color: #888;
  }

  .editor-error {
    background: #3a2426;
    border-color: #5c3234;
    color: #ef9a9a;
  }
}
</style>
