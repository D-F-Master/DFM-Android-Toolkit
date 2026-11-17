<script lang="ts" setup>
import { ref, onMounted, onBeforeUnmount } from 'vue';
import { useProbeSettings } from '../composables/useProbeSettings';
import { useFeatureOverrides } from '../composables/useFeatureOverrides';
import { useAppConfig } from '../composables/useAppConfig';

const emit = defineEmits(['back']);

const goHome = () => {
  emit('back');
};

// ===== 左侧分类 =====
const categories = [
  { key: 'general', label: '通用' },
  { key: 'build', label: '构建' },
];
const activeCategory = ref('general');

// ===== 通用：自动安装 probe =====
const { autoInstallProbe, setAutoInstallProbe } = useProbeSettings();

const onSwitch = () => {
  setAutoInstallProbe(!autoInstallProbe.value);
};

// ===== 构建：页面功能显示（覆盖 config.json 的 features） =====
const { isFeatureOn } = useAppConfig();
const { setOverride } = useFeatureOverrides();

const menuFeatures = [
  { path: 'utils', label: 'ADB工具' },
  { path: 'portTool', label: '端口工具' },
  { path: 'production', label: '生产工具' },
  { path: 'jarDiff', label: 'jar包工具' },
  { path: 'mcpService', label: 'MCP管理工具' },
];

const utilsTabFeatures = [
  { path: 'utilsTabs.info', label: '设备信息' },
  { path: 'utilsTabs.apps', label: '应用管理' },
  { path: 'utilsTabs.files', label: '文件管理' },
  { path: 'utilsTabs.keys', label: '设备投屏' },
  { path: 'utilsTabs.batch', label: '批量操作' },
  { path: 'utilsTabs.script', label: '模拟脚本' },
  { path: 'utilsTabs.serial', label: '模拟串口' },
  { path: 'utilsTabs.monitor', label: '性能监控' },
  { path: 'utilsTabs.probe', label: '探针工具' },
];

const portToolTabFeatures = [
  { path: 'portToolTabs.check', label: '端口占用检查' },
  { path: 'portToolTabs.scan', label: '端口扫描' },
  { path: 'portToolTabs.ip', label: 'IP工具' },
  { path: 'portToolTabs.portproxy', label: '端口转发' },
];

const jarDiffTabFeatures = [
  { path: 'jarDiffTabs.diff', label: 'jar包对比' },
  { path: 'jarDiffTabs.codeEdit', label: 'jar代码修改' },
];

const toggleFeature = (path: string) => {
  setOverride(path, !isFeatureOn(path));
};

// ===== 左侧点击 → 右侧滚动到对应 section =====
const scrollPane = ref<HTMLElement | null>(null);
const sectionRefs = ref<Record<string, HTMLElement | null>>({});

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

// ===== 右侧滚动 → 左侧高亮当前 section =====
const onPaneScroll = () => {
  const pane = scrollPane.value;
  if (!pane) return;
  const pos = pane.scrollTop + 40;
  let current = categories[0].key;
  for (const c of categories) {
    const el = sectionRefs.value[c.key];
    if (el && el.offsetTop - 16 <= pos) current = c.key;
  }
  if (pane.scrollTop + pane.clientHeight >= pane.scrollHeight - 4) {
    current = categories[categories.length - 1].key;
  }
  activeCategory.value = current;
};

onMounted(() => {
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

        <!-- 右侧滚动区 -->
        <section ref="scrollPane" class="settings-content">
          <div :ref="setSectionRef('general')" class="settings-section">
            <h3 class="section-title">通用</h3>
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
          </div>

          <div :ref="setSectionRef('build')" class="settings-section">
            <h3 class="section-title">构建</h3>
            <div class="section-card">
              <div v-for="f in menuFeatures" :key="f.path" class="setting-row">
                <div class="setting-text">
                  <div class="setting-label">{{ f.label }}</div>
                  <div class="setting-desc">首页工具 · {{ f.path }}</div>
                </div>
                <button
                  class="toggle"
                  :class="{ on: isFeatureOn(f.path) }"
                  role="switch"
                  :aria-checked="isFeatureOn(f.path)"
                  @click="toggleFeature(f.path)"
                >
                  <span class="toggle-thumb"></span>
                </button>
              </div>
            </div>

            <h3 class="section-title group-title">ADB工具 Tab 页</h3>
            <div class="section-card">
              <div v-for="f in utilsTabFeatures" :key="f.path" class="setting-row">
                <div class="setting-text">
                  <div class="setting-label">{{ f.label }}</div>
                  <div class="setting-desc">ADB工具页 · {{ f.path }}</div>
                </div>
                <button
                  class="toggle"
                  :class="{ on: isFeatureOn(f.path) }"
                  role="switch"
                  :aria-checked="isFeatureOn(f.path)"
                  @click="toggleFeature(f.path)"
                >
                  <span class="toggle-thumb"></span>
                </button>
              </div>
            </div>

            <h3 class="section-title group-title">端口工具 Tab 页</h3>
            <div class="section-card">
              <div v-for="f in portToolTabFeatures" :key="f.path" class="setting-row">
                <div class="setting-text">
                  <div class="setting-label">{{ f.label }}</div>
                  <div class="setting-desc">端口工具页 · {{ f.path }}</div>
                </div>
                <button
                  class="toggle"
                  :class="{ on: isFeatureOn(f.path) }"
                  role="switch"
                  :aria-checked="isFeatureOn(f.path)"
                  @click="toggleFeature(f.path)"
                >
                  <span class="toggle-thumb"></span>
                </button>
              </div>
            </div>

            <h3 class="section-title group-title">jar包工具 Tab 页</h3>
            <div class="section-card">
              <div v-for="f in jarDiffTabFeatures" :key="f.path" class="setting-row">
                <div class="setting-text">
                  <div class="setting-label">{{ f.label }}</div>
                  <div class="setting-desc">jar包工具页 · {{ f.path }}</div>
                </div>
                <button
                  class="toggle"
                  :class="{ on: isFeatureOn(f.path) }"
                  role="switch"
                  :aria-checked="isFeatureOn(f.path)"
                  @click="toggleFeature(f.path)"
                >
                  <span class="toggle-thumb"></span>
                </button>
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

/* ===== 右侧滚动区 ===== */
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

.group-title {
  margin-top: 20px;
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
