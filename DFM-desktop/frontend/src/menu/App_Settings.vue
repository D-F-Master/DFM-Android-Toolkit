<script lang="ts" setup>
import { reactive, ref, onMounted, onBeforeUnmount } from 'vue';

const emit = defineEmits(['back']);

const goHome = () => {
  emit('back');
};

// ===== 左侧分类 =====
const categories = [
  { key: 'general', label: '通用' },
  { key: 'device', label: '设备管理' },
  { key: 'mirror', label: '投屏' },
  { key: 'about', label: '关于' },
];

// ===== 设置数据（占位内容，localStorage 持久化） =====
const STORAGE_KEY = 'dfm-settings-v1';

const defaults: Record<string, boolean | string> = {
  'general.autoStart': false,
  'general.autoConnect': true,
  'general.language': 'zh-CN',
  'device.rememberLabels': true,
  'device.timeout': '10',
  'mirror.quality': 'high',
  'mirror.maxFps': '60',
};

const settings = reactive<Record<string, boolean | string>>({ ...defaults });

const sections = [
  {
    key: 'general',
    title: '通用',
    items: [
      { key: 'general.autoStart', type: 'switch', label: '开机自动启动服务', desc: '系统启动时自动运行本地服务端' },
      { key: 'general.autoConnect', type: 'switch', label: '自动连接最近设备', desc: '打开插件时自动连接上一次使用的设备' },
      { key: 'general.language', type: 'select', label: '界面语言', desc: '切换后立即生效', options: [{ value: 'zh-CN', label: '简体中文' }, { value: 'en-US', label: 'English' }] },
    ],
  },
  {
    key: 'device',
    title: '设备管理',
    items: [
      { key: 'device.rememberLabels', type: 'switch', label: '记住应用标签', desc: '应用列表的自定义命名保存到本地服务端' },
      { key: 'device.timeout', type: 'select', label: 'ADB 命令超时', desc: '超时后自动中断当前命令', options: [{ value: '5', label: '5 秒' }, { value: '10', label: '10 秒' }, { value: '30', label: '30 秒' }] },
    ],
  },
  {
    key: 'mirror',
    title: '投屏',
    items: [
      { key: 'mirror.quality', type: 'select', label: '投屏画质', desc: '高画质占用更多带宽', options: [{ value: 'low', label: '流畅' }, { value: 'high', label: '高清' }] },
      { key: 'mirror.maxFps', type: 'select', label: '最大帧率', desc: '降低帧率可减少延迟抖动', options: [{ value: '30', label: '30 FPS' }, { value: '60', label: '60 FPS' }] },
    ],
  },
  {
    key: 'about',
    title: '关于',
    items: [
      { type: 'static', label: 'DFM-Android-Toolkit', desc: '版本 2.0.3 · 仅供学习和内部使用' },
    ],
  },
] as const;

const loadSettings = () => {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) Object.assign(settings, JSON.parse(raw));
  } catch {
    // 数据损坏时回退默认值
  }
};

const saveSettings = () => {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(settings));
};

const onSwitch = (key: string) => {
  settings[key] = !settings[key];
  saveSettings();
};

const onSelect = (key: string) => {
  saveSettings();
};

// ===== 左侧点击 → 右侧滚动到对应分类 =====
const scrollPane = ref<HTMLElement | null>(null);
const sectionRefs = ref<Record<string, HTMLElement | null>>({});
const activeCategory = ref('general');

const setSectionRef = (key: string) => (el: any) => {
  sectionRefs.value[key] = el;
};

const scrollToCategory = (key: string) => {
  activeCategory.value = key;
  const pane = scrollPane.value;
  const el = sectionRefs.value[key];
  if (!pane || !el) return;
  pane.scrollTo({ top: el.offsetTop - 16, behavior: 'smooth' });
};

// ===== 右侧滚动 → 左侧高亮当前分类 =====
const onPaneScroll = () => {
  const pane = scrollPane.value;
  if (!pane) return;
  const pos = pane.scrollTop + 40;
  let current = categories[0].key;
  for (const c of categories) {
    const el = sectionRefs.value[c.key];
    if (el && el.offsetTop - 16 <= pos) current = c.key;
  }
  // 滚动到底时强制高亮最后一项
  if (pane.scrollTop + pane.clientHeight >= pane.scrollHeight - 4) {
    current = categories[categories.length - 1].key;
  }
  activeCategory.value = current;
};

onMounted(() => {
  loadSettings();
  scrollPane.value?.addEventListener('scroll', onPaneScroll, { passive: true });
});

onBeforeUnmount(() => {
  scrollPane.value?.removeEventListener('scroll', onPaneScroll);
});
</script>

<template>
  <div class="app">
    <button class="back-button" @click="goHome">&lt;</button>
    <main>
      <div class="container">
        <!-- 左侧分类导航 -->
        <aside class="settings-nav">
          <h2 class="nav-title">设置</h2>
          <nav class="nav-list">
            <button
              v-for="c in categories"
              :key="c.key"
              class="nav-item"
              :class="{ active: activeCategory === c.key }"
              @click="scrollToCategory(c.key)"
            >
              {{ c.label }}
            </button>
          </nav>
        </aside>

        <!-- 右侧详细设置 -->
        <section ref="scrollPane" class="settings-content" @scroll.passive="onPaneScroll">
          <div
            v-for="s in sections"
            :key="s.key"
            :ref="setSectionRef(s.key)"
            class="settings-section"
          >
            <h3 class="section-title">{{ s.title }}</h3>
            <div class="section-card">
              <div v-for="item in s.items" :key="item.key ?? item.label" class="setting-row">
                <div class="setting-text">
                  <div class="setting-label">{{ item.label }}</div>
                  <div class="setting-desc">{{ item.desc }}</div>
                </div>

                <!-- 开关 -->
                <button
                  v-if="item.type === 'switch'"
                  class="toggle"
                  :class="{ on: settings[item.key] }"
                  role="switch"
                  :aria-checked="!!settings[item.key]"
                  @click="onSwitch(item.key!)"
                >
                  <span class="toggle-thumb"></span>
                </button>

                <!-- 下拉 -->
                <select
                  v-else-if="item.type === 'select'"
                  class="setting-select"
                  :value="settings[item.key!] as string"
                  @change="onSelect(item.key!)"
                >
                  <option v-for="opt in item.options" :key="opt.value" :value="opt.value">{{ opt.label }}</option>
                </select>

                <!-- 静态信息 -->
                <span v-else class="setting-static">v2.0.3</span>
              </div>
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
  --accent-light: rgba(13, 150, 104, 0.08);
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

/* ===== 左侧分类导航 ===== */
.settings-nav {
  flex: 0 0 180px;
  padding: 64px 12px 16px;
  border-right: 1px solid #e2e6ec;
  background: #f7f8fa;
  overflow-y: auto;
}

.nav-title {
  margin: 0 8px 12px;
  font-size: 18px;
  font-weight: 700;
  color: #1a1f2e;
}

.nav-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.nav-item {
  display: block;
  width: 100%;
  padding: 9px 12px;
  border: none;
  border-radius: 8px;
  background: transparent;
  color: #4a5265;
  font-size: 14px;
  text-align: left;
  cursor: pointer;
  transition: background 0.15s ease, color 0.15s ease;
}

.nav-item:hover {
  background: rgba(13, 150, 104, 0.06);
  color: #1a1f2e;
}

.nav-item.active {
  background: var(--accent-light);
  color: var(--accent);
  font-weight: 600;
}

/* ===== 右侧详细设置 ===== */
.settings-content {
  position: relative; /* 让 section 的 offsetTop 相对本滚动区 */
  flex: 1;
  min-width: 0;
  padding: 56px 28px 32px;
  overflow-y: auto;
}

.settings-section + .settings-section {
  margin-top: 24px;
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

.setting-row + .setting-row {
  border-top: 1px solid #f0f2f5;
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

/* ===== 下拉 ===== */
.setting-select {
  flex: 0 0 auto;
  padding: 6px 10px;
  border: 1px solid #d9dee6;
  border-radius: 8px;
  background: #fff;
  color: #1a1f2e;
  font-size: 13px;
  cursor: pointer;
  outline: none;
}

.setting-select:focus {
  border-color: var(--accent);
}

.setting-static {
  font-size: 13px;
  color: #8a94a6;
}
</style>
