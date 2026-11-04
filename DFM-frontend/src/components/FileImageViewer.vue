<template>
  <div v-if="visible" class="imgv-overlay" @click.self="handleClose">
    <div class="imgv-window">
      <!-- 顶部工具条 -->
      <div class="imgv-header">
        <span class="imgv-title" :title="fileName">{{ fileName }}</span>
        <span v-if="sizeText" class="imgv-size">{{ sizeText }}</span>
        <div class="imgv-actions">
          <button class="imgv-btn zoom-out" @click="zoomOut" :disabled="loading || !!loadError" title="缩小">−</button>
          <button class="imgv-btn zoom-reset" @click="resetZoom" :disabled="loading || !!loadError">重置</button>
          <button class="imgv-btn zoom-in" @click="zoomIn" :disabled="loading || !!loadError" title="放大">＋</button>
          <button class="imgv-close" @click="handleClose">&times;</button>
        </div>
      </div>

      <!-- 图片区 -->
      <div class="imgv-body">
        <div v-if="loadError" class="imgv-error">{{ loadError }}</div>
        <div v-else-if="loading" class="imgv-loading">正在读取图片，请稍候...</div>
        <div
          v-else
          class="imgv-viewport"
          @wheel.prevent="onWheel"
          @mousedown="onMouseDown"
          @dblclick="resetZoom"
        >
          <img
            :src="src"
            class="imgv-img"
            :style="{ transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})` }"
            draggable="false"
            alt=""
          />
        </div>
      </div>
    </div>
  </div>
</template>

<script lang="ts" setup>
import { ref, watch, onUnmounted, computed } from 'vue'

const props = defineProps({
  visible: { type: Boolean, default: false },
  fileName: { type: String, default: '' },
  // data: URL，由父组件加载后传入
  src: { type: String, default: '' },
  loading: { type: Boolean, default: false },
  loadError: { type: String, default: '' },
  // 字节数，用于展示；0/undefined 不显示
  fileSize: { type: Number, default: 0 }
})

const emit = defineEmits(['close'])

const zoom = ref(1)
const pan = ref({ x: 0, y: 0 })

const MIN_ZOOM = 0.1
const MAX_ZOOM = 10

// 字节数格式化为人类可读
const sizeText = computed(() => {
  if (!props.fileSize) return ''
  if (props.fileSize < 1024) return `${props.fileSize} B`
  if (props.fileSize < 1024 * 1024) return `${(props.fileSize / 1024).toFixed(1)} KB`
  return `${(props.fileSize / 1024 / 1024).toFixed(2)} MB`
})

// 换一张图时重置缩放和平移
watch(
  () => [props.visible, props.src] as const,
  ([v]) => {
    if (v) {
      zoom.value = 1
      pan.value = { x: 0, y: 0 }
    }
  },
  { immediate: true }
)

const setZoom = (next: number, cx = 0, cy = 0) => {
  const clamped = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, next))
  // 以鼠标位置为中心缩放：保持光标下的内容不动
  const ratio = clamped / zoom.value
  pan.value = {
    x: cx - (cx - pan.value.x) * ratio,
    y: cy - (cy - pan.value.y) * ratio
  }
  zoom.value = clamped
}

const zoomIn = () => setZoom(zoom.value * 1.25)
const zoomOut = () => setZoom(zoom.value / 1.25)
const resetZoom = () => {
  zoom.value = 1
  pan.value = { x: 0, y: 0 }
}

const onWheel = (e: WheelEvent) => {
  const target = e.currentTarget as HTMLElement
  const rect = target.getBoundingClientRect()
  // 光标相对视口中心的偏移
  const cx = e.clientX - rect.left - rect.width / 2
  const cy = e.clientY - rect.top - rect.height / 2
  const factor = e.deltaY < 0 ? 1.15 : 1 / 1.15
  setZoom(zoom.value * factor, cx, cy)
}

let dragging = false
let lastX = 0
let lastY = 0

const onMouseDown = (e: MouseEvent) => {
  dragging = true
  lastX = e.clientX
  lastY = e.clientY
  const onMove = (ev: MouseEvent) => {
    if (!dragging) return
    pan.value = {
      x: pan.value.x + (ev.clientX - lastX),
      y: pan.value.y + (ev.clientY - lastY)
    }
    lastX = ev.clientX
    lastY = ev.clientY
  }
  const onUp = () => {
    dragging = false
    window.removeEventListener('mousemove', onMove)
    window.removeEventListener('mouseup', onUp)
  }
  window.addEventListener('mousemove', onMove)
  window.addEventListener('mouseup', onUp)
}

const handleClose = () => {
  emit('close')
}

// Esc 关闭
const onKeydown = (e: KeyboardEvent) => {
  if (!props.visible) return
  if (e.key === 'Escape') handleClose()
}
window.addEventListener('keydown', onKeydown)
onUnmounted(() => window.removeEventListener('keydown', onKeydown))
</script>

<style scoped>
.imgv-overlay {
  position: fixed;
  inset: 0;
  z-index: 2000;
  background: rgba(0, 0, 0, 0.55);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
}

.imgv-window {
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

.imgv-header {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 16px;
  border-bottom: 1px solid #f0f0f0;
  background: #fafafa;
}

.imgv-title {
  flex: 1;
  min-width: 0;
  font-size: 13px;
  font-weight: 600;
  color: #333;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.imgv-size {
  flex-shrink: 0;
  padding: 2px 8px;
  border-radius: 10px;
  background: #e8f5e9;
  color: #2e7d32;
  font-size: 11px;
  font-weight: 500;
}

.imgv-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.imgv-btn {
  padding: 5px 14px;
  border: 1px solid #e0e0e0;
  border-radius: 6px;
  font-size: 12px;
  font-weight: 500;
  font-family: inherit;
  background: #f5f5f5;
  color: #666;
  cursor: pointer;
  transition: all 0.2s ease;
}

.imgv-btn:hover:not(:disabled) {
  background: #eee;
}

.imgv-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.imgv-close {
  background: none;
  border: none;
  font-size: 20px;
  line-height: 1;
  color: #999;
  cursor: pointer;
  padding: 0 4px;
}

.imgv-close:hover {
  color: #666;
}

.imgv-body {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

.imgv-viewport {
  flex: 1;
  min-height: 0;
  overflow: hidden;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: grab;
  background:
    /* 棋盘格底纹：透明图片时能看清边界 */
    repeating-conic-gradient(#f0f0f0 0% 25%, #fff 0% 50%) 0 0 / 20px 20px;
}

.imgv-viewport:active {
  cursor: grabbing;
}

.imgv-img {
  max-width: 90%;
  max-height: 90%;
  user-select: none;
  will-change: transform;
}

.imgv-error {
  margin: 16px;
  padding: 12px;
  background: #ffebee;
  border: 1px solid #ffcdd2;
  border-radius: 6px;
  color: #c62828;
  font-size: 13px;
}

.imgv-loading {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #999;
  font-size: 14px;
}

@media (prefers-color-scheme: dark) {
  .imgv-window {
    background: #2d2f36;
  }

  .imgv-header {
    background: #33363e;
    border-bottom-color: #40434a;
  }

  .imgv-title {
    color: #e0e0e0;
  }

  .imgv-size {
    background: #24402a;
    color: #81c784;
  }

  .imgv-btn {
    background: #3a3d45;
    color: #b0b0b0;
    border-color: #555;
  }

  .imgv-btn:hover:not(:disabled) {
    background: #44474f;
  }

  .imgv-close {
    color: #888;
  }

  .imgv-viewport {
    background:
      repeating-conic-gradient(#26282e 0% 25%, #2d2f36 0% 50%) 0 0 / 20px 20px;
  }

  .imgv-error {
    background: #3a2426;
    border-color: #5c3234;
    color: #ef9a9a;
  }
}
</style>
