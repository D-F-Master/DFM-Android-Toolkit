<script lang="ts" setup>
const emit = defineEmits(['back']);

const goHome = () => {
  emit('back');
};
</script>

<template>
  <div class="app">
    <button class="back-button" @click="goHome">&lt;</button>
    <main>
      <div class="container">
        <div class="prod-placeholder">
          <div class="prod-icon">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" width="36" height="36">
              <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/>
            </svg>
          </div>
          <h2 class="prod-title">生产工具</h2>
          <p class="prod-hint">功能开发中，敬请期待</p>
        </div>
      </div>
    </main>
  </div>
</template>

<style scoped>
/* ===== 返回按钮（与其他页面一致） ===== */
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
  justify-content: center;
  align-items: center;
}

/* ===== 占位内容 ===== */
.prod-placeholder {
  --accent: #0d9668;
  --accent-light: rgba(13, 150, 104, 0.08);
  text-align: center;
  font-family: 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif;
}

.prod-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 72px;
  height: 72px;
  color: var(--accent);
  background: var(--accent-light);
  border: 1px solid rgba(13, 150, 104, 0.12);
  border-radius: 18px;
  margin-bottom: 16px;
}

.prod-title {
  margin: 0 0 8px;
  font-size: 26px;
  font-weight: 700;
  color: #1a1f2e;
}

.prod-hint {
  margin: 0;
  font-size: 14px;
  color: #8a94a6;
  letter-spacing: 1px;
}
</style>
