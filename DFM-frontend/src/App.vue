<script lang="ts" setup>
import { ref, onMounted } from 'vue';
import App_Utils from './menu/App_Utils.vue';
import App_PortTool from './menu/App_PortTool.vue';

const currentPage = ref('home');
const appVersion = ref('V2.0.0');

// 加载配置获取版本号
onMounted(async () => {
  try {
    const response = await fetch('/config.json');
    if (response.ok) {
      const config = await response.json();
      if (config.version) {
        appVersion.value = config.version;
      }
    }
  } catch (error) {
    console.error('加载配置失败:', error);
  }
});

const enterUtils = () => {
  currentPage.value = 'utils';
};

const enterPortTool = () => {
  currentPage.value = 'portTool';
};


const goBack = () => {
  currentPage.value = 'home';
};
</script>

<template>
  <div v-if="currentPage === 'home'" class="home">
    <!-- 背景装饰 -->
    <div class="bg-grid"></div>
    <div class="floating-orbs">
      <span class="orb orb-1"></span>
      <span class="orb orb-2"></span>
      <span class="orb orb-3"></span>
      <span class="orb orb-4"></span>
    </div>

    <div class="home-menu">
      <div class="card-accent-line"></div>

      <!-- Logo 区域 -->
      <div class="logo-area">
        <div class="logo-icon">
          <svg viewBox="0 0 48 48" fill="none" xmlns="http://www.w3.org/2000/svg">
            <rect x="4" y="8" width="40" height="32" rx="4" stroke="currentColor" stroke-width="2.5"/>
            <path d="M4 16h40" stroke="currentColor" stroke-width="2.5"/>
            <circle cx="10" cy="12" r="1.5" fill="currentColor"/>
            <circle cx="16" cy="12" r="1.5" fill="currentColor"/>
            <circle cx="22" cy="12" r="1.5" fill="currentColor"/>
            <path d="M14 24l4 4-4 4" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>
            <line x1="22" y1="32" x2="34" y2="32" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"/>
          </svg>
        </div>
      </div>

      <h1 class="title">
        <span class="title-prefix">DFM </span>
        <span class="title-suffix"> Android-Toolkit</span>
      </h1>

      <div class="divider">
        <span class="divider-dot"></span>
        <span class="divider-line"></span>
        <span class="divider-dot"></span>
      </div>

      <button class="menu-button" @click="enterUtils">
        <span class="btn-icon">
          <svg viewBox="0 0 20 20" fill="currentColor" width="20" height="20">
            <path fill-rule="evenodd" d="M3 4a1 1 0 011-1h12a1 1 0 011 1v6a1 1 0 01-1 1H4a1 1 0 01-1-1V4zm4 9a1 1 0 011-1h4a1 1 0 110 2H8a1 1 0 01-1-1zm-2 3a1 1 0 100 2h10a1 1 0 100-2H5z" clip-rule="evenodd"/>
          </svg>
        </span>
        <span class="btn-text">ADB工具</span>
        <span class="btn-arrow">
          <svg viewBox="0 0 20 20" fill="currentColor" width="18" height="18">
            <path fill-rule="evenodd" d="M10.293 3.293a1 1 0 011.414 0l6 6a1 1 0 010 1.414l-6 6a1 1 0 01-1.414-1.414L14.586 11H3a1 1 0 110-2h11.586l-4.293-4.293a1 1 0 010-1.414z" clip-rule="evenodd"/>
          </svg>
        </span>
      </button>

      <button class="menu-button" @click="enterPortTool">
        <span class="btn-icon">
          <svg viewBox="0 0 20 20" fill="currentColor" width="20" height="20">
            <path d="M5 4a2 2 0 012-2h6a2 2 0 012 2v14l-5-2.5L5 18V4z"/>
          </svg>
        </span>
        <span class="btn-text">端口工具</span>
        <span class="btn-arrow">
          <svg viewBox="0 0 20 20" fill="currentColor" width="18" height="18">
            <path fill-rule="evenodd" d="M10.293 3.293a1 1 0 011.414 0l6 6a1 1 0 010 1.414l-6 6a1 1 0 01-1.414-1.414L14.586 11H3a1 1 0 110-2h11.586l-4.293-4.293a1 1 0 010-1.414z" clip-rule="evenodd"/>
          </svg>
        </span>
      </button>

      <!--
        ⬆⬆⬆ 新按钮加在上方 ⬆⬆⬆
      -->

      <p class="version">{{ appVersion }}</p>
    </div>
  </div>
  <App_Utils v-if="currentPage === 'utils'" @back="goBack" />
  <App_PortTool v-else-if="currentPage === 'portTool'" @back="goBack" />
</template>

<style scoped>
/* ===== 亮色主题变量 ===== */
.home {
  --accent: #0d9668;
  --accent-hover: #0a7d56;
  --accent-light: rgba(13, 150, 104, 0.08);
  --accent-glow: rgba(13, 150, 104, 0.15);
  --bg-base: #f0f2f5;
  --bg-warm: #f8f9fb;
  --card-bg: rgba(255, 255, 255, 0.72);
  --card-border: rgba(13, 150, 104, 0.1);
  --card-shadow: rgba(13, 150, 104, 0.08);
  --text-primary: #1a1f2e;
  --text-muted: #8a94a6;

  display: flex;
  justify-content: center;
  align-items: center;
  min-height: 100vh;
  background: var(--bg-base);
  overflow: hidden;
  position: relative;
  font-family: 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif;
}

/* ===== 背景网格 ===== */
.bg-grid {
  position: absolute;
  inset: 0;
  background-image:
    linear-gradient(rgba(13, 150, 104, 0.04) 1px, transparent 1px),
    linear-gradient(90deg, rgba(13, 150, 104, 0.04) 1px, transparent 1px);
  background-size: 48px 48px;
  mask-image: radial-gradient(ellipse 70% 70% at 50% 50%, black 20%, transparent 100%);
  -webkit-mask-image: radial-gradient(ellipse 70% 70% at 50% 50%, black 20%, transparent 100%);
}

/* ===== 浮动光球 ===== */
.floating-orbs {
  position: absolute;
  inset: 0;
  pointer-events: none;
}

.orb {
  position: absolute;
  border-radius: 50%;
  filter: blur(90px);
  opacity: 0.5;
  animation: orbFloat 14s ease-in-out infinite;
}

.orb-1 {
  width: 320px; height: 320px;
  background: rgba(13, 150, 104, 0.12);
  top: -12%; left: -8%;
  animation-delay: 0s;
}

.orb-2 {
  width: 280px; height: 280px;
  background: rgba(59, 130, 246, 0.1);
  bottom: -10%; right: -6%;
  animation-delay: -5s;
  animation-duration: 17s;
}

.orb-3 {
  width: 200px; height: 200px;
  background: rgba(245, 158, 11, 0.08);
  top: 40%; left: 55%;
  animation-delay: -9s;
  animation-duration: 20s;
}

.orb-4 {
  width: 160px; height: 160px;
  background: rgba(13, 150, 104, 0.06);
  top: 15%; right: 18%;
  animation-delay: -3s;
  animation-duration: 12s;
}

@keyframes orbFloat {
  0%, 100% { transform: translate(0, 0) scale(1); }
  25% { transform: translate(25px, -18px) scale(1.04); }
  50% { transform: translate(-18px, 25px) scale(0.96); }
  75% { transform: translate(12px, 12px) scale(1.02); }
}

/* ===== 卡片 ===== */
.home-menu {
  position: relative;
  text-align: center;
  padding: 56px 64px 48px;
  background: var(--card-bg);
  border: 1px solid var(--card-border);
  border-radius: 20px;
  backdrop-filter: blur(20px) saturate(1.3);
  -webkit-backdrop-filter: blur(20px) saturate(1.3);
  box-shadow:
    0 0 0 1px rgba(255, 255, 255, 0.6),
    0 20px 60px -12px var(--card-shadow),
    0 4px 16px -4px rgba(0, 0, 0, 0.04);
  animation: cardEnter 0.7s cubic-bezier(0.16, 1, 0.3, 1) forwards;
  opacity: 0;
  transform: translateY(24px) scale(0.97);
  z-index: 1;
}

@keyframes cardEnter {
  to {
    opacity: 1;
    transform: translateY(0) scale(1);
  }
}

.card-accent-line {
  position: absolute;
  top: 0;
  left: 50%;
  transform: translateX(-50%);
  width: 50%;
  height: 3px;
  background: linear-gradient(90deg, transparent, var(--accent), transparent);
  border-radius: 0 0 4px 4px;
}

/* ===== Logo ===== */
.logo-area {
  margin-bottom: 20px;
  animation: logoPulse 4s ease-in-out infinite;
}

.logo-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 64px;
  height: 64px;
  color: var(--accent);
  background: var(--accent-light);
  border-radius: 16px;
  border: 1px solid rgba(13, 150, 104, 0.12);
}

.logo-icon svg {
  width: 36px;
  height: 36px;
}

@keyframes logoPulse {
  0%, 100% { transform: scale(1); }
  50% { transform: scale(1.04); }
}

/* ===== 标题 ===== */
.title {
  margin: 0 0 8px;
  font-size: 40px;
  font-weight: 800;
  letter-spacing: -0.5px;
  line-height: 1.1;
}

.title-prefix {
  color: var(--accent);
}

.title-suffix {
  color: var(--text-primary);
  font-weight: 300;
}

.subtitle {
  margin: 0 0 28px;
  color: var(--text-muted);
  font-size: 14px;
  font-weight: 400;
  letter-spacing: 1px;
}

/* ===== 分隔线 ===== */
.divider {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 12px;
  margin-bottom: 32px;
}

.divider-dot {
  width: 4px;
  height: 4px;
  border-radius: 50%;
  background: var(--accent);
  opacity: 0.35;
}

.divider-line {
  width: 48px;
  height: 1px;
  background: linear-gradient(90deg, transparent, rgba(13, 150, 104, 0.2), transparent);
}

/* ===== 按钮 ===== */
.menu-button {
  margin: 10px;
  display: inline-flex;
  align-items: center;
  gap: 10px;
  padding: 14px 36px;
  font-size: 16px;
  font-weight: 600;
  font-family: inherit;
  color: #fff;
  background: var(--accent);
  border: none;
  border-radius: 12px;
  cursor: pointer;
  position: relative;
  overflow: hidden;
  transition: all 0.35s cubic-bezier(0.16, 1, 0.3, 1);
  box-shadow: 0 4px 20px -4px var(--accent-glow);
  /* 按钮之间有间距 */
  margin-bottom: 12px;
}

.menu-button::before {
  content: '';
  position: absolute;
  inset: 0;
  background: linear-gradient(135deg, rgba(255,255,255,0.2), transparent 60%);
  opacity: 0;
  transition: opacity 0.35s;
}

.menu-button:hover {
  background: var(--accent-hover);
  transform: translateY(-3px) scale(1.02);
  box-shadow: 0 8px 32px -4px rgba(13, 150, 104, 0.3);
}

.menu-button:hover::before {
  opacity: 1;
}

.menu-button:active {
  transform: translateY(-1px) scale(0.99);
  box-shadow: 0 2px 10px -2px var(--accent-glow);
}

.btn-icon {
  display: flex;
  align-items: center;
  opacity: 0.85;
}

.btn-text {
  letter-spacing: 0.5px;
}

.btn-arrow {
  display: flex;
  align-items: center;
  opacity: 0.55;
  transition: all 0.35s cubic-bezier(0.16, 1, 0.3, 1);
}

.menu-button:hover .btn-arrow {
  opacity: 1;
  transform: translateX(4px);
}

/* ===== 版本号 ===== */
.version {
  margin: 20px 0 0;
  font-size: 16px;
  color: var(--text-muted);
  opacity: 0.45;
  letter-spacing: 2px;
  text-transform: uppercase;
}

/* ===== 减少动画偏好 ===== */
@media (prefers-reduced-motion: reduce) {
  .orb, .logo-area, .home-menu {
    animation: none !important;
  }
  .home-menu {
    opacity: 1;
    transform: none;
  }
}
</style>