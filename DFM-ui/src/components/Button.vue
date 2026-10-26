<template>
  <button
    class="dfm-btn"
    :class="[`dfm-btn--${variant}`, `dfm-btn--${size}`, { 'dfm-btn--loading': loading }]"
    type="button"
    :disabled="disabled || loading"
  >
    <span v-if="loading" class="dfm-btn__spinner" aria-hidden="true"></span>
    <span v-else-if="$slots.icon" class="dfm-btn__icon"><slot name="icon" /></span>
    <span class="dfm-btn__text"><slot /></span>
  </button>
</template>

<script lang="ts" setup>
defineProps({
  variant: {
    type: String,
    default: 'primary',
    validator: (value: string) => ['primary', 'secondary', 'success', 'warning', 'danger'].includes(value)
  },
  size: {
    type: String,
    default: 'md',
    validator: (value: string) => ['sm', 'md', 'lg'].includes(value)
  },
  loading: {
    type: Boolean,
    default: false
  },
  disabled: {
    type: Boolean,
    default: false
  }
});
</script>

<style scoped>
.dfm-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  border: 1px solid transparent;
  border-radius: 12px;
  font-family: inherit;
  font-weight: 600;
  line-height: 1.4;
  cursor: pointer;
  position: relative;
  overflow: hidden;
  transition: all var(--dur-base) var(--ease-out-expo);
  user-select: none;
}

.dfm-btn__text {
  letter-spacing: 0.5px;
}

.dfm-btn__icon {
  display: inline-flex;
  align-items: center;
}

/* ===== 尺寸 ===== */
.dfm-btn--sm {
  padding: 4px 12px;
  gap: 6px;
  font-size: 12px;
  border-radius: 8px;
}

.dfm-btn--md {
  padding: 8px 20px;
  font-size: 14px;
}

.dfm-btn--lg {
  padding: 14px 32px;
  gap: 10px;
  font-size: 16px;
}

/* ===== 主要按钮：实心主色 + 高光扫过 + 上浮 ===== */
.dfm-btn--primary {
  color: #fff;
  background: var(--accent);
  box-shadow: 0 4px 20px -4px var(--accent-glow);
}

.dfm-btn--primary::before {
  content: '';
  position: absolute;
  inset: 0;
  background: linear-gradient(135deg, rgba(255, 255, 255, 0.2), transparent 60%);
  opacity: 0;
  transition: opacity var(--dur-base);
}

.dfm-btn--primary:hover:not(:disabled) {
  background: var(--accent-hover);
  transform: translateY(-2px);
  box-shadow: 0 8px 32px -4px rgba(13, 150, 104, 0.3);
}

.dfm-btn--primary:hover:not(:disabled)::before {
  opacity: 1;
}

.dfm-btn--primary:active:not(:disabled) {
  transform: translateY(0) scale(0.99);
  box-shadow: 0 2px 10px -2px var(--accent-glow);
}

/* ===== 次要按钮：白底描边，悬停转主色 ===== */
.dfm-btn--secondary {
  color: var(--text-primary);
  background: #fff;
  border-color: rgba(13, 150, 104, 0.2);
}

.dfm-btn--secondary:hover:not(:disabled) {
  color: var(--accent);
  border-color: var(--accent);
  background: var(--accent-light);
}

/* ===== 状态色按钮（取原项目中已使用的色值） ===== */
.dfm-btn--success {
  color: #fff;
  background: #4caf50;
}

.dfm-btn--success:hover:not(:disabled) {
  background: #43a047;
}

.dfm-btn--warning {
  color: #fff;
  background: #ff9800;
}

.dfm-btn--warning:hover:not(:disabled) {
  background: #f57c00;
}

.dfm-btn--danger {
  color: #fff;
  background: #e74c3c;
}

.dfm-btn--danger:hover:not(:disabled) {
  background: #d32f2f;
}

/* ===== 禁用：沿用 main.css 的去色约定 ===== */
.dfm-btn:disabled {
  filter: grayscale(1);
  opacity: 0.55;
  cursor: not-allowed;
  transform: none;
  box-shadow: none;
}

/* ===== 键盘焦点 ===== */
.dfm-btn:focus-visible {
  outline: none;
  box-shadow: 0 0 0 3px var(--accent-glow);
}

/* ===== 加载中 ===== */
.dfm-btn--loading {
  cursor: progress;
}

.dfm-btn__spinner {
  width: 1em;
  height: 1em;
  border: 2px solid currentColor;
  border-right-color: transparent;
  border-radius: 50%;
  animation: dfm-btn-spin 0.6s linear infinite;
}

@keyframes dfm-btn-spin {
  to {
    transform: rotate(360deg);
  }
}

/* ===== 减少动画偏好 ===== */
@media (prefers-reduced-motion: reduce) {
  .dfm-btn {
    transition: none;
  }

  .dfm-btn--primary:hover:not(:disabled) {
    transform: none;
  }
}
</style>
