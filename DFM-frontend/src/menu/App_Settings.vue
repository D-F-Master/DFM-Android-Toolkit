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
        <div class="settings-placeholder">
          <div class="settings-icon">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" width="36" height="36">
              <circle cx="12" cy="12" r="3"/>
              <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"/>
            </svg>
          </div>
          <h2 class="settings-title">设置</h2>
          <p class="settings-hint">设置项开发中，敬请期待</p>
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
.settings-placeholder {
  --accent: #0d9668;
  --accent-light: rgba(13, 150, 104, 0.08);
  text-align: center;
  font-family: 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif;
}

.settings-icon {
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

.settings-title {
  margin: 0 0 8px;
  font-size: 26px;
  font-weight: 700;
  color: #1a1f2e;
}

.settings-hint {
  margin: 0;
  font-size: 14px;
  color: #8a94a6;
  letter-spacing: 1px;
}
</style>
