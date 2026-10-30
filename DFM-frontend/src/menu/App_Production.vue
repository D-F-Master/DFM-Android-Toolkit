<script lang="ts" setup>
import { ref, onMounted } from 'vue';
import axios from 'axios';

const emit = defineEmits(['back']);

const goHome = () => {
  emit('back');
};

// ===== 与 ADB工具 同源的骨架状态 =====
const API_BASE = ref('http://localhost:3000');
const devices = ref<Array<{ deviceId: string; model?: string; status?: string }>>([]);
const selectedDevice = ref('');
const loading = ref(false);
const activeTab = ref('placeholder');

// Tab 定义：功能还在规划中，先放占位页
const tabs = [
  { key: 'placeholder', label: '敬请期待' },
];

const loadConfig = async () => {
  try {
    const response = await fetch('/config.json');
    if (response.ok) {
      const config = await response.json();
      if (config.apiBase) {
        API_BASE.value = config.apiBase;
      }
    }
  } catch (error) {
    console.error('加载配置失败:', error);
  }
};

const fetchDevices = async () => {
  loading.value = true;
  try {
    const response = await axios.post(`${API_BASE.value}/api/devices`);
    if (response.data.code === 200) {
      devices.value = response.data.data || [];
      if (devices.value.length > 0 && !selectedDevice.value) {
        selectedDevice.value = devices.value[0].deviceId;
      }
    }
  } catch (error) {
    console.error('获取设备列表失败:', error);
  } finally {
    loading.value = false;
  }
};

const selectDevice = (deviceId: string) => {
  selectedDevice.value = deviceId;
};

// 无线连接功能暂未开放，输入区先不渲染，避免给用户"已支持"的误导
onMounted(async () => {
  await loadConfig();
  fetchDevices();
});
</script>

<template>
  <div class="app">
    <button class="back-button" @click="goHome">&lt;</button>
    <main>
      <div class="container">
        <!-- 左侧设备列表 -->
        <div class="sidebar">
          <div class="device-header">
            <h2>设备管理</h2>
            <button class="refresh-button" @click="fetchDevices">刷新</button>
          </div>
          <div class="device-list-container">
            <div v-if="loading">加载设备中...</div>
            <div v-else-if="devices.length === 0">无设备连接</div>
            <template v-else>
              <div
                v-for="device in devices"
                :key="device.deviceId"
                class="device-item"
                :class="{ active: selectedDevice === device.deviceId }"
                @click="selectDevice(device.deviceId)"
              >
                <div class="device-id">{{ device.deviceId }}</div>
                <div class="device-info" v-if="device.model">{{ device.model }}</div>
                <div class="device-status" :class="{ 'status-offline': device.status === 'offline' }">
                  {{ device.status === 'device' ? '已连接' : device.status === 'offline' ? '离线' : device.status }}
                </div>
              </div>
            </template>
          </div>
        </div>

        <!-- 右侧操作区：分页按钮 + 内容区 -->
        <div class="content">
          <div v-if="!selectedDevice" class="prod-empty">请选择一个设备</div>
          <template v-else>
            <div class="tabs">
              <button
                v-for="t in tabs"
                :key="t.key"
                class="tab-button"
                :class="{ active: activeTab === t.key }"
                @click="activeTab = t.key"
              >
                {{ t.label }}
              </button>
            </div>
            <div class="tab-content">
              <div class="prod-placeholder">
                <div class="prod-icon">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" width="36" height="36">
                    <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/>
                  </svg>
                </div>
                <h2 class="prod-title">生产工具</h2>
                <p class="prod-hint">功能规划中，敬请期待</p>
              </div>
            </div>
          </template>
        </div>
      </div>
    </main>
  </div>
</template>

<style scoped>
.app {
  position: relative;
  width: 100%;
  height: 100vh;
  min-height: 544px;
}

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

/* ===== 设备列表容器（ADB工具同款，滚动范围锁定在列表内） ===== */
.device-list-container {
  flex: 1;
  overflow-y: auto;
  min-height: 0;
}

.device-status {
  font-size: 12px;
  color: #4caf50;
  margin-top: 3px;
}

.device-status.status-offline {
  color: #f44336;
}

/* ===== 占位内容 ===== */
.prod-empty {
  padding: 40px;
  text-align: center;
  color: #999;
  font-size: 14px;
}

.prod-placeholder {
  --accent: #0d9668;
  --accent-light: rgba(13, 150, 104, 0.08);
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  min-height: 320px;
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
