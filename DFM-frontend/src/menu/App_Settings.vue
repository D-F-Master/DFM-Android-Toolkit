<script lang="ts" setup>
import { useProbeSettings } from '../composables/useProbeSettings';

const emit = defineEmits(['back']);

const goHome = () => {
  emit('back');
};

const { autoInstallProbe, setAutoInstallProbe } = useProbeSettings();

const onSwitch = () => {
  setAutoInstallProbe(!autoInstallProbe.value);
};
</script>

<template>
  <div class="app">
    <button class="back-button" @click="goHome">&lt;</button>
    <main>
      <div class="container">
        <section class="settings-content">
          <h3 class="section-title">设置</h3>
          <div class="section-card">
            <div class="setting-row">
              <div class="setting-text">
                <div class="setting-label">自动安装 probe</div>
                <div class="setting-desc">刷新设备列表时自动为新设备安装探针 APK；关闭后需手动点击设备状态圆点安装</div>
              </div>
              <button
                class="toggle"
                :class="{ on: autoInstallProbe }"
                role="switch"
                :aria-checked="autoInstallProbe"
                @click="onSwitch"
              >
                <span class="toggle-thumb"></span>
              </button>
            </div>
          </div>
        </section>
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
  --accent: #0d9668;
  position: relative;
  width: 100%;
  height: 100vh;
  min-height: 544px;
  font-family: 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif;
}

.container {
  display: flex;
  width: 100%;
  height: 100%;
  overflow: hidden;
  background: #f0f2f5;
}

/* ===== 右侧详细设置 ===== */
.settings-content {
  flex: 1;
  min-width: 0;
  padding: 56px 28px 32px;
  overflow-y: auto;
}

.section-title {
  margin: 0 0 10px;
  font-size: 15px;
  font-weight: 600;
  color: #8a94a6;
}

.section-card {
  background: #fff;
  border: 1px solid #e8ebf0;
  border-radius: 12px;
  overflow: hidden;
}

.setting-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 14px 18px;
}

.setting-text {
  min-width: 0;
}

.setting-label {
  font-size: 14px;
  color: #1a1f2e;
}

.setting-desc {
  margin-top: 2px;
  font-size: 12px;
  color: #8a94a6;
}

/* ===== 开关 ===== */
.toggle {
  flex: 0 0 auto;
  position: relative;
  width: 40px;
  height: 22px;
  padding: 0;
  border: none;
  border-radius: 11px;
  background: #d4d9e0;
  cursor: pointer;
  transition: background 0.2s ease;
}

.toggle.on {
  background: var(--accent);
}

.toggle-thumb {
  position: absolute;
  top: 2px;
  left: 2px;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: #fff;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.2);
  transition: transform 0.2s ease;
}

.toggle.on .toggle-thumb {
  transform: translateX(18px);
}
</style>
