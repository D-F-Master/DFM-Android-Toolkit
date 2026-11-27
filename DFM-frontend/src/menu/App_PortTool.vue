2<script lang="ts" setup>
import { ref, computed, onMounted, watch } from 'vue';
import ModalDialog from '../components/ModalDialog.vue';
import { useAppConfig } from '../composables/useAppConfig';
import { usePortToolContext } from '../composables/portTool/usePortToolContext';
import { usePortCheck } from '../composables/portTool/usePortCheck';
import { usePortScan } from '../composables/portTool/usePortScan';
import { useIpTools } from '../composables/portTool/useIpTools';
import { usePortProxy } from '../composables/portTool/usePortProxy';

const emit = defineEmits(['back']);

// Tab 切换状态
const activeTab = ref<'check' | 'scan' | 'ip' | 'portproxy'>('check');

// 按配置过滤后的 Tab（未配置的默认显示）
const { isFeatureOn } = useAppConfig();
const PORT_TAB_KEYS = ['check', 'scan', 'ip', 'portproxy'] as const;
const PORT_TAB_LABELS: Record<string, string> = {
  check: '端口占用检查', scan: '端口扫描', ip: 'IP工具', portproxy: '端口转发'
};
const visibleTabs = computed(() => PORT_TAB_KEYS.filter(key => isFeatureOn(`portToolTabs.${key}`)));

// 当前 Tab 被关掉时自动跳到第一个可见 Tab
watch(visibleTabs, (tabs) => {
  if (tabs.length && !tabs.includes(activeTab.value)) {
    activeTab.value = tabs[0];
  }
}, { immediate: true });

const ctx = usePortToolContext(activeTab);
const {
  API_BASE, errorMessage, modalVisible, modalType, modalTitle, modalMessage,
  modalPlaceholder, modalConfirmText, modalCallback, loadConfig,
  showMessage, showConfirm, handleModalConfirm, handleModalCancel,
} = ctx;

const {
  portInput, loading, portInfo, checkPort, killProcess, clearAllProcesses,
} = usePortCheck(ctx);

const {
  targetIp, startPort, endPort, scanResults, scanProgress, isScanning,
  commonPortsList, openPorts, telnetToPort, openWebPage, validateScanInput,
  executeScan, startScan, scanCommonPorts, exportResults, clearScanResults,
} = usePortScan(ctx);

const {
  scanIp, subnetMask, ipScanResults, isIpScanning, checkSingleIp, singleIpResult,
  isCheckingIp, batchConnectIps, batchConnectInput, batchConnectResults,
  isBatchConnecting, batchConnectStats, validateIpFormat, startIpScan, checkIpOnline,
  parseBatchConnectInput, batchAdbConnect, addIpToBatch, clearBatchConnect,
  batchConnectScannedIps, connectingIp, connectScannedIp,
} = useIpTools(ctx);

const {
  proxyRules, proxyLoading, proxyError, proxyAddListenPort, proxyAddListenAddress,
  proxyAddConnectPort, proxyAddConnectAddress, proxyAddProtocol, proxyShowAddForm,
  proxyEditing, loadProxyRules, addProxyRule, deleteProxyRule, editProxyRule,
  toggleProxyForm,
} = usePortProxy(ctx);

const goHome = () => {
  emit('back');
};

onMounted(async () => {
  await loadConfig();
});
</script>

<style scoped src="../styles/page-shell.css"></style>
<style scoped src="../styles/card.css"></style>
<style scoped src="../styles/tabs.css"></style>

<style scoped>
/* ===== 标题 ===== */
.section-title {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  color: #333;
}

.section-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}

/* ===== 错误消息 ===== */
.error-message {
  background: #fff3f3;
  color: #e74c3c;
  padding: 10px 14px;
  border-radius: 8px;
  border: 1px solid #f5c6cb;
  margin-bottom: 12px;
  font-size: 13px;
}

/* ===== 输入框组（端口检查） ===== */
.port-input-group {
  display: flex;
  gap: 10px;
}

.port-input {
  flex: 1;
  height: 40px;
  padding: 0 14px;
  border: 1px solid #ddd;
  border-radius: 8px;
  font-size: 14px;
  font-family: inherit;
  transition: border-color 0.25s ease, box-shadow 0.25s ease;
}

.port-input:focus {
  outline: none;
  border-color: #4caf50;
  box-shadow: 0 0 0 3px rgba(76, 175, 80, 0.1);
}

/* ===== 按钮基础 ===== */
.check-button,
.scan-button,
.export-button {
  height: 40px;
  padding: 0 22px;
  background: #4caf50;
  color: #fff;
  border: none;
  border-radius: 8px;
  cursor: pointer;
  font-size: 14px;
  font-weight: 600;
  font-family: inherit;
  transition: all 0.35s cubic-bezier(0.16, 1, 0.3, 1);
  display: inline-flex;
  align-items: center;
  gap: 6px;
  white-space: nowrap;
  box-shadow: 0 4px 14px -4px rgba(76, 175, 80, 0.3);
}

.check-button:hover,
.scan-button:hover,
.export-button:hover {
  background: #43a047;
  transform: translateY(-2px);
  box-shadow: 0 6px 20px -4px rgba(76, 175, 80, 0.4);
}

.check-button:active,
.scan-button:active,
.export-button:active {
  transform: translateY(0);
}

.check-button:disabled,
.scan-button:disabled,
.export-button:disabled {
  background: #bdbdbd;
  box-shadow: none;
  cursor: not-allowed;
  transform: none;
}

/* 辅助按钮（蓝色） */
.quick-scan-button,
.telnet-button {
  height: 40px;
  padding: 0 18px;
  background: #2196f3;
  color: #fff;
  border: none;
  border-radius: 8px;
  cursor: pointer;
  font-size: 13px;
  font-weight: 500;
  font-family: inherit;
  transition: all 0.35s cubic-bezier(0.16, 1, 0.3, 1);
  display: inline-flex;
  align-items: center;
  gap: 6px;
  white-space: nowrap;
  box-shadow: 0 4px 14px -4px rgba(33, 150, 243, 0.25);
}

.quick-scan-button:hover,
.telnet-button:hover {
  background: #1976d2;
  transform: translateY(-2px);
  box-shadow: 0 6px 20px -4px rgba(33, 150, 243, 0.35);
}

.quick-scan-button:disabled,
.telnet-button:disabled {
  background: #bdbdbd;
  box-shadow: none;
  cursor: not-allowed;
  transform: none;
}

/* 危险按钮（红色） */
.clear-all-button,
.kill-button,
.clear-button {
  height: 32px;
  padding: 0 14px;
  background: #f44336;
  color: #fff;
  border: none;
  border-radius: 6px;
  cursor: pointer;
  font-size: 12px;
  font-weight: 500;
  font-family: inherit;
  transition: all 0.25s ease;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  white-space: nowrap;
}

.clear-all-button:hover,
.kill-button:hover,
.clear-button:hover {
  background: #d32f2f;
  transform: translateY(-1px);
  box-shadow: 0 3px 10px rgba(244, 67, 54, 0.2);
}

.clear-all-button:active,
.kill-button:active,
.clear-button:active {
  transform: translateY(0);
}

/* ===== 端口占用信息头部 ===== */
.port-info-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 16px;
  border-bottom: 1px solid #f0f0f0;
  background: #fafafa;
}

.port-info-title {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
  color: #333;
  flex: 1;
}

.port-info-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}

.port-count {
  font-size: 13px;
  color: #888;
}

/* ===== 表格样式（端口占用） ===== */
.table-container {
  overflow-x: auto;
  overflow-y: auto;
  max-height: calc(100vh - 250px);
}

.port-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

.port-table thead {
  background: #fafafa;
}

.port-table th {
  padding: 10px 12px;
  text-align: left;
  font-weight: 600;
  color: #555;
  border-bottom: 1px solid #e8eaed;
  white-space: nowrap;
  font-size: 12px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

/* 固定列 - PID */
.col-pid {
  position: sticky;
  left: 0;
  width: 70px;
  min-width: 70px;
  font-weight: 600;
  color: #4caf50;
  background: #fff;
  z-index: 1;
}

.port-table thead .col-pid {
  background: #fafafa;
  z-index: 2;
}

/* 固定列 - 名称 */
.col-name {
  position: sticky;
  left: 70px;
  min-width: 120px;
  max-width: 200px;
  background: #fff;
  z-index: 1;
}

.port-table thead .col-name {
  background: #fafafa;
  z-index: 2;
}

/* 固定列 - 操作 */
.col-action {
  position: sticky;
  right: 0;
  width: 80px;
  min-width: 80px;
  text-align: center;
  background: #fff;
  z-index: 1;
}

.port-table thead .col-action {
  background: #fafafa;
  z-index: 2;
}

.port-table tbody tr {
  border-bottom: 1px solid #f0f0f0;
  transition: background 0.2s ease;
}

.port-table tbody tr:hover {
  background: #f5faf5;
}

.port-table tbody tr:hover .col-pid,
.port-table tbody tr:hover .col-name,
.port-table tbody tr:hover .col-action {
  background: #f5faf5;
}

.port-table td {
  padding: 10px 12px;
  color: #555;
  max-width: 200px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.col-state {
  width: 100px;
}

.state-badge {
  display: inline-block;
  padding: 3px 10px;
  border-radius: 10px;
  font-size: 11px;
  font-weight: 500;
}

.state-listening {
  background: #e8f5e9;
  color: #2e7d32;
}

.state-other {
  background: #fff3e0;
  color: #ef6c00;
}

.col-address {
  min-width: 140px;
  max-width: 180px;
}

.col-local {
  min-width: 140px;
  max-width: 180px;
}

.col-path {
  min-width: 200px;
  max-width: 350px;
  font-size: 12px;
  color: #888;
}

/* ===== 端口扫描输入区 ===== */
.scan-inputs-wrapper {
  display: flex;
  gap: 16px;
  flex-wrap: wrap;
}

.input-item {
  flex: 1;
  min-width: 200px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.input-label {
  font-size: 13px;
  font-weight: 600;
  color: #444;
  display: flex;
  align-items: center;
  gap: 6px;
}

.input-label::before {
  content: '';
  display: inline-block;
  width: 3px;
  height: 14px;
  background: #4caf50;
  border-radius: 2px;
}

.input-field {
  width: 100%;
  height: 40px;
  padding: 0 14px;
  border: 1px solid #ddd;
  border-radius: 8px;
  font-size: 14px;
  font-family: inherit;
  box-sizing: border-box;
  transition: border-color 0.25s ease, box-shadow 0.25s ease;
}

.input-field:focus {
  outline: none;
  border-color: #4caf50;
  box-shadow: 0 0 0 3px rgba(76, 175, 80, 0.1);
}

.input-hint {
  font-size: 12px;
  color: #999;
  line-height: 1.4;
  padding-left: 4px;
}

.button-group {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
  margin-top: 4px;
}

/* ===== 进度条 ===== */
.progress-section {
  margin-bottom: 12px;
  padding: 0 16px;
}

.progress-bar {
  width: 100%;
  height: 6px;
  background: #e8eaed;
  border-radius: 3px;
  overflow: hidden;
  margin-bottom: 6px;
}

.progress-fill {
  height: 100%;
  background: linear-gradient(90deg, #4caf50, #66bb6a);
  border-radius: 3px;
  transition: width 0.3s ease;
}

.progress-text {
  font-size: 12px;
  color: #888;
  text-align: center;
}

/* ===== 结果列表 ===== */
.results-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 16px;
  border-bottom: 1px solid #f0f0f0;
  background: #fafafa;
}

.results-actions {
  display: flex;
  gap: 8px;
}

.results-count {
  font-size: 13px;
  color: #666;
  font-weight: 500;
  flex: 1;
}

.results-list {
  list-style: none;
  padding: 8px 16px;
  margin: 0;
  max-height: calc(100vh - 380px);
  overflow-y: auto;
}

.result-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 14px;
  border: 1px solid #e8eaed;
  border-radius: 8px;
  margin-bottom: 6px;
  transition: all 0.25s ease;
}

.result-item:hover {
  border-color: #a5d6a7;
  box-shadow: 0 2px 8px rgba(76, 175, 80, 0.08);
  background: #fafff5;
}

.result-port {
  font-weight: 600;
  color: #333;
  font-size: 14px;
}

.result-status {
  display: inline-block;
  padding: 3px 12px;
  border-radius: 10px;
  font-size: 11px;
  font-weight: 500;
}

.status-open {
  background: #e8f5e9;
  color: #2e7d32;
}

.status-closed {
  background: #ffebee;
  color: #c62828;
}

.connect-ip-button {
  margin-left: auto;
  height: 28px;
  padding: 0 12px;
  background: #2196f3;
  color: #fff;
  border: none;
  border-radius: 6px;
  cursor: pointer;
  font-size: 12px;
  font-weight: 500;
  font-family: inherit;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  white-space: nowrap;
}

.connect-ip-button:hover:not(:disabled) {
  background: #1976d2;
}

.connect-ip-button:disabled {
  background: #bdbdbd;
  cursor: not-allowed;
  opacity: 0.75;
}

.result-service {
  font-size: 12px;
  color: #888;
  margin-top: 3px;
}

.result-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

/* ===== 无结果提示 ===== */
.no-results {
  text-align: center;
  padding: 32px 16px;
  color: #999;
  font-size: 14px;
}

/* ===== Loading 动画 ===== */
.loading {
  display: inline-block;
  width: 16px;
  height: 16px;
  border: 2px solid rgba(255, 255, 255, 0.3);
  border-top-color: #fff;
  border-radius: 50%;
  animation: spin 0.6s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

/* ===== 暗色模式 ===== */
@media (prefers-color-scheme: dark) {
  .container,
  .content {
    background: #1a1d23;
  }

  .card {
    background: #2d2f36;
    box-shadow: 0 2px 12px rgba(0, 0, 0, 0.25);
  }

  .card-header,
  .port-info-header,
  .results-header {
    background: #33363e;
    border-bottom-color: #40434a;
    color: #e0e0e0;
  }

  .card-icon {
    color: #66bb6a;
  }

  .tab-buttons {
    background: #2d2f36;
  }

  .tab-button {
    color: #999;
  }

  .tab-button:hover {
    background: #3a3d45;
    color: #ccc;
  }

  .tab-button.active {
    background: #4caf50;
    color: #fff;
  }

  .section-title,
  .port-info-title,
  .result-port,
  .input-label {
    color: #e0e0e0;
  }

  .port-input,
  .input-field {
    background: #3a3d45;
    border-color: #555;
    color: #e0e0e0;
  }

  .port-input:focus,
  .input-field:focus {
    border-color: #66bb6a;
  }

  .port-table thead {
    background: #33363e;
  }

  .port-table th {
    color: #999;
    border-bottom-color: #40434a;
  }

  .port-table td {
    color: #ccc;
  }

  .port-table tbody tr {
    border-bottom-color: #40434a;
  }

  .port-table tbody tr:hover {
    background: #35383f;
  }

  .port-table tbody tr:hover .col-pid,
  .port-table tbody tr:hover .col-name,
  .port-table tbody tr:hover .col-action {
    background: #35383f;
  }

  .col-pid,
  .col-name,
  .col-action {
    background: #2d2f36;
  }

  .port-table thead .col-pid,
  .port-table thead .col-name,
  .port-table thead .col-action {
    background: #33363e;
  }

  .col-pid {
    color: #66bb6a;
  }

  .error-message {
    background: #3d2526;
    border-color: #5f3838;
    color: #ef9a9a;
  }

  .result-item {
    border-color: #40434a;
    background: #2d2f36;
  }

  .result-item:hover {
    border-color: #388e3c;
    background: #2a3a2a;
  }

  .result-service,
  .input-hint,
  .port-count,
  .results-count {
    color: #999;
  }

  .no-results {
    color: #777;
  }

  .progress-bar {
    background: #40434a;
  }

  .progress-text {
    color: #999;
  }
}

/* ===== 端口转发表格容器 ===== */
.proxy-table-container {
  overflow-x: auto;
  overflow-y: auto;
  max-height: calc(100vh - 300px);
}

/* 表单 select 风格统一 */
select.input-field {
  appearance: auto;
  cursor: pointer;
  background: #fff;
}

/* ===== 暗色主题适配 ===== */
@media (prefers-color-scheme: dark) {
  select.input-field {
    background: #3a3d45;
    color: #e0e0e0;
  }
}
</style>

<template>
  <div class="app">
    <button class="back-button" @click="goHome">&lt;</button>
    <main>
      <div class="container">
        <div class="content">
          <!-- Tab 切换 -->
          <div class="tab-container">
            <div class="tab-buttons">
              <button
                v-for="tab in visibleTabs"
                :key="tab"
                class="tab-button"
                :class="{ active: activeTab === tab }"
                @click="activeTab = tab"
              >
                {{ PORT_TAB_LABELS[tab] }}
              </button>
            </div>
          </div>

          <!-- 端口占用检查功能 -->
          <div v-if="activeTab === 'check'">
            <div class="card">
              <div class="card-header">
                <span class="card-icon">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="18" height="18">
                    <circle cx="12" cy="12" r="10"/>
                    <polyline points="12 6 12 12 16 14"/>
                  </svg>
                </span>
                <span>端口占用检查</span>
              </div>
              <div class="card-body">
                <div class="port-input-group">
                  <input 
                    type="text" 
                    v-model="portInput" 
                    class="port-input" 
                    placeholder="请输入端口号 (例如: 8080)"
                    @keyup.enter="checkPort"
                  >
                  <button 
                    class="check-button" 
                    @click="checkPort"
                    :disabled="loading"
                  >
                    <span v-if="loading" class="loading"></span>
                    <span v-else>检查端口</span>
                  </button>
                </div>
              </div>
            </div>
            
            <div v-if="errorMessage" class="error-message">{{ errorMessage }}</div>
            
            <div class="card">
              <div class="port-info-header">
                <span class="card-icon">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="18" height="18">
                    <rect x="2" y="3" width="20" height="14" rx="2"/>
                    <line x1="8" y1="21" x2="16" y2="21"/>
                    <line x1="12" y1="17" x2="12" y2="21"/>
                  </svg>
                </span>
                <span class="port-info-title">占用信息</span>
                <div class="port-info-actions">
                  <span class="port-count" v-if="portInfo.length > 0">
                    共 {{ portInfo.length }} 个进程
                  </span>
                  <button v-if="portInfo.length > 0" class="clear-all-button" @click="clearAllProcesses">
                    清除全部占用
                  </button>
                </div>
              </div>
              
              <div class="table-container" v-if="portInfo.length > 0">
                <table class="port-table">
                  <thead>
                    <tr>
                      <th class="col-pid">PID</th>
                      <th class="col-name">名称</th>
                      <th class="col-state">状态</th>
                      <th class="col-address">地址</th>
                      <th class="col-local">本机位置</th>
                      <th class="col-path">进程路径</th>
                      <th class="col-action">操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="(item, index) in portInfo" :key="index" class="port-row">
                      <td class="col-pid">{{ item.pid }}</td>
                      <td class="col-name" :title="item.process">{{ item.process }}</td>
                      <td class="col-state">
                        <span class="state-badge" :class="item.state === 'LISTENING' ? 'state-listening' : 'state-other'">
                          {{ item.state }}
                        </span>
                      </td>
                      <td class="col-address" :title="item.foreignAddress">{{ item.foreignAddress }}</td>
                      <td class="col-local" :title="item.localAddress">{{ item.localAddress }}</td>
                      <td class="col-path" :title="item.processPath">{{ item.processPath || '-' }}</td>
                      <td class="col-action">
                        <button class="kill-button" @click="killProcess(item.pid)">
                          终止
                        </button>
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
              
              <div v-else-if="!loading" class="no-results">
                <p>暂无占用信息</p>
                <p style="font-size: 14px; margin-top: 8px;">输入端口号并点击检查按钮查看占用情况</p>
              </div>
              
              <div v-else class="no-results">
                <div class="loading" style="margin: 0 auto 16px;"></div>
                <p>正在查询端口占用情况...</p>
              </div>
            </div>
          </div>

          <div v-if="errorMessage && activeTab === 'scan'" class="error-message">{{ errorMessage }}</div>

          <!-- 端口扫描功能 -->
          <div v-if="activeTab === 'scan'">
            <div class="card">
              <div class="card-header">
                <span class="card-icon">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="18" height="18">
                    <circle cx="11" cy="11" r="8"/>
                    <line x1="21" y1="21" x2="16.65" y2="16.65"/>
                  </svg>
                </span>
                <span>端口扫描配置</span>
              </div>
              <div class="card-body">
              
              <div class="scan-inputs-wrapper">
                <div class="input-item">
                  <label class="input-label">目标IP地址</label>
                  <input 
                    type="text" 
                    v-model="targetIp" 
                    class="input-field" 
                    placeholder="例如: 127.0.0.1 或 192.168.1.1"
                  >
                  <span class="input-hint">要扫描的目标设备IP地址</span>
                </div>
                
                <div class="input-item">
                  <label class="input-label">起始端口</label>
                  <input 
                    type="number" 
                    v-model="startPort" 
                    class="input-field" 
                    placeholder="1"
                    min="1"
                    max="65535"
                  >
                  <span class="input-hint">扫描范围的起始端口号（1-65535）</span>
                </div>
                
                <div class="input-item">
                  <label class="input-label">结束端口</label>
                  <input 
                    type="number" 
                    v-model="endPort" 
                    class="input-field" 
                    placeholder="1024"
                    min="1"
                    max="65535"
                  >
                  <span class="input-hint">扫描范围的结束端口号（1-65535）</span>
                </div>
              </div>
              
              <div class="button-group">
                <button 
                  class="scan-button" 
                  @click="startScan"
                  :disabled="loading"
                >
                  <span v-if="loading" class="loading"></span>
                  <span v-else>开始扫描</span>
                </button>
                
                <button 
                  class="quick-scan-button" 
                  @click="scanCommonPorts"
                  :disabled="loading"
                >
                  扫描常用端口
                </button>
              </div>
              </div>
            </div>
            
            <div v-if="isScanning || scanProgress > 0" class="progress-section">
              <div class="progress-bar">
                <div class="progress-fill" :style="{ width: scanProgress + '%' }"></div>
              </div>
              <div class="progress-text">扫描进度: {{ scanProgress }}%</div>
            </div>
            
            <div class="card">
              <div class="results-header">
                <span class="card-icon">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="18" height="18">
                    <polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/>
                  </svg>
                </span>
                <span class="results-count">扫描结果（仅显示开放端口）</span>
                <div class="results-actions">
                  <button v-if="openPorts.length > 0" class="export-button" @click="exportResults">
                    导出结果
                  </button>
                  <button v-if="scanResults.length > 0" class="clear-button" @click="clearScanResults">
                    清除结果
                  </button>
                </div>
              </div>
              
              <ul class="results-list" v-if="openPorts.length > 0">
                <li v-for="(item, index) in openPorts" :key="index" class="result-item">
                  <div>
                    <div class="result-port">端口 {{ item.port }}</div>
                    <div v-if="item.service" class="result-service">{{ item.service }}</div>
                  </div>
                  <div class="result-actions">
                    <button 
                      class="telnet-button" 
                      @click="openWebPage(item.port)"
                      :title="'http://' + targetIp + ':' + item.port"
                    >
                      网页
                    </button>
                    <button 
                      class="telnet-button" 
                      @click="telnetToPort(item.port)"
                      :title="'telnet ' + targetIp + ' ' + item.port"
                    >
                      Telnet
                    </button>
                    <span class="result-status status-open">
                      开放
                    </span>
                  </div>
                </li>
              </ul>
              
              <div v-else-if="!loading && !isScanning && scanResults.length === 0" class="no-results">
                <p>暂无扫描结果</p>
                <p style="font-size: 14px; margin-top: 8px;">配置扫描参数并点击开始扫描</p>
              </div>
              
              <div v-else-if="!loading && !isScanning && scanResults.length > 0" class="no-results">
                <p>未发现开放端口</p>
                <p style="font-size: 14px; margin-top: 8px;">所有扫描的端口均处于关闭状态</p>
              </div>
              
              <div v-else-if="isScanning" class="no-results">
                <div class="loading" style="margin: 0 auto 16px;"></div>
                <p>正在扫描端口...</p>
              </div>
            </div>
          </div>

          <div v-if="errorMessage && activeTab === 'ip'" class="error-message">{{ errorMessage }}</div>

          <!-- IP工具功能 -->
          <div v-if="activeTab === 'ip'">
            <!-- IP网段扫描 -->
            <div class="card">
              <div class="card-header">
                <span class="card-icon">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="18" height="18">
                    <rect x="2" y="2" width="20" height="8" rx="2" ry="2"/>
                    <rect x="2" y="14" width="20" height="8" rx="2" ry="2"/>
                    <line x1="6" y1="6" x2="6.01" y2="6"/>
                    <line x1="6" y1="18" x2="6.01" y2="18"/>
                  </svg>
                </span>
                <span>IP网段扫描</span>
              </div>
              <div class="card-body">
                <div class="section-header" style="margin-bottom: 16px;">
                <button 
                  class="scan-button" 
                  @click="startIpScan"
                  :disabled="isIpScanning"
                  style="margin: 0;"
                >
                  <span v-if="isIpScanning" class="loading"></span>
                  <span v-else>开始扫描</span>
                </button>
              </div>
              
              <div class="scan-inputs-wrapper">
                <div class="input-item">
                  <label class="input-label">IP地址</label>
                  <input 
                    type="text" 
                    v-model="scanIp" 
                    class="input-field" 
                    placeholder="例如: 192.168.1.100"
                  >
                  <span class="input-hint">该网段内的任意IP地址</span>
                </div>
                
                <div class="input-item">
                  <label class="input-label">子网掩码</label>
                  <input 
                    type="text" 
                    v-model="subnetMask" 
                    class="input-field" 
                    placeholder="255.255.255.0 或 /24"
                  >
                  <span class="input-hint">支持标准格式或CIDR格式</span>
                </div>
              </div>
              </div>
            </div>
            
            <div class="card" v-if="ipScanResults.onlineIps.length > 0">
              <div class="results-header">
                <span class="card-icon">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="18" height="18">
                    <polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/>
                  </svg>
                </span>
                <span class="results-count">扫描结果 - 共{{ ipScanResults.total }}个IP，在线{{ ipScanResults.onlineCount }}个</span>
                <div class="results-actions">
                  <button 
                    class="export-button" 
                    @click="batchConnectScannedIps"
                    :disabled="isBatchConnecting"
                  >
                    <span v-if="isBatchConnecting" class="loading"></span>
                    <span v-else>ADB批量连接所有在线IP</span>
                  </button>
                </div>
              </div>
              
              <ul class="results-list">
                <li v-for="(ip, index) in ipScanResults.onlineIps" :key="index" class="result-item">
                  <div class="result-port">{{ ip }}</div>
                  <span class="result-status status-open">
                    在线
                  </span>
                  <button
                    class="connect-ip-button"
                    @click="connectScannedIp(ip)"
                    :disabled="connectingIp === ip"
                  >
                    <span v-if="connectingIp === ip" class="loading"></span>
                    <span v-else>ADB连接</span>
                  </button>
                </li>
              </ul>
            </div>
          </div>

          <div v-if="proxyError && activeTab === 'portproxy'" class="error-message">{{ proxyError }}</div>

          <!-- 端口转发管理 -->
          <div v-if="activeTab === 'portproxy'">
            <div class="card">
              <div class="card-header">
                <span class="card-icon">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="18" height="18">
                    <path d="M4 12h16M12 4v16M9 9l3-3 3 3M9 15l3 3 3-3" stroke-linecap="round" stroke-linejoin="round"/>
                  </svg>
                </span>
                <span>端口转发规则列表</span>
                <div class="results-actions">
                  <button class="scan-button" @click="loadProxyRules" :disabled="proxyLoading" style="margin:0;">
                    <span v-if="proxyLoading" class="loading"></span>
                    <span v-else>刷新</span>
                  </button>
                  <button class="quick-scan-button" @click="toggleProxyForm" style="margin:0;">
                    {{ proxyShowAddForm ? '取消' : '添加规则' }}
                  </button>
                </div>
              </div>
              <div class="card-body">
                <div v-if="proxyRules.length === 0 && !proxyLoading" class="no-results">
                  <p>暂无转发规则</p>
                  <p style="font-size: 14px; margin-top: 8px;">点击「添加规则」按钮创建端口转发</p>
                </div>
                <div v-else-if="proxyLoading" class="no-results">
                  <div class="loading" style="margin: 0 auto 16px;"></div>
                  <p>正在加载转发规则...</p>
                </div>
                <div v-else class="proxy-table-container">
                  <table class="port-table">
                    <thead>
                      <tr>
                        <th>监听地址</th>
                        <th>监听端口</th>
                        <th>目标地址</th>
                        <th>目标端口</th>
                        <th class="col-action">操作</th>
                      </tr>
                    </thead>
                    <tbody>
                      <tr v-for="(rule, index) in proxyRules" :key="index" class="port-row">
                        <td>{{ rule.listenAddress }}</td>
                        <td><span class="state-badge state-listening">{{ rule.listenPort }}</span></td>
                        <td>{{ rule.connectAddress }}</td>
                        <td>{{ rule.connectPort }}</td>
                        <td class="col-action">
                          <button class="telnet-button" @click="editProxyRule(rule)" :disabled="proxyLoading" style="margin-right:6px;">
                            编辑
                          </button>
                          <button class="kill-button" @click="deleteProxyRule(rule)" :disabled="proxyLoading">
                            删除
                          </button>
                        </td>
                      </tr>
                    </tbody>
                  </table>
                </div>
              </div>
            </div>

            <!-- 添加转发规则 -->
            <div v-if="proxyShowAddForm" class="card">
              <div class="card-header">
                <span class="card-icon">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="18" height="18">
                    <circle cx="12" cy="12" r="10"/>
                    <line x1="12" y1="8" x2="12" y2="16"/>
                    <line x1="8" y1="12" x2="16" y2="12"/>
                  </svg>
                </span>
                <span>{{ proxyEditing ? '编辑端口转发规则' : '添加端口转发规则' }}</span>
              </div>
              <div class="card-body">
                <div class="scan-inputs-wrapper">
                  <div class="input-item">
                    <label class="input-label">监听端口 <span style="color:#f44336;">*</span></label>
                    <input type="number" v-model="proxyAddListenPort" class="input-field" placeholder="例如: 8080" min="1" max="65535">
                    <span class="input-hint">本机监听的端口号</span>
                  </div>
                  <div class="input-item">
                    <label class="input-label">监听地址</label>
                    <input type="text" v-model="proxyAddListenAddress" class="input-field" placeholder="0.0.0.0">
                    <span class="input-hint">默认为 0.0.0.0（所有地址）</span>
                  </div>
                  <div class="input-item">
                    <label class="input-label">目标地址 <span style="color:#f44336;">*</span></label>
                    <input type="text" v-model="proxyAddConnectAddress" class="input-field" placeholder="例如: 192.168.1.100">
                    <span class="input-hint">转发目标 IP 地址</span>
                  </div>
                  <div class="input-item">
                    <label class="input-label">目标端口 <span style="color:#f44336;">*</span></label>
                    <input type="number" v-model="proxyAddConnectPort" class="input-field" placeholder="例如: 3389" min="1" max="65535">
                    <span class="input-hint">转发目标端口号</span>
                  </div>
                  <div class="input-item">
                    <label class="input-label">协议类型</label>
                    <select v-model="proxyAddProtocol" class="input-field" style="height:40px;">
                      <option value="v4tov4">v4tov4</option>
                      <option value="v4tov6">v4tov6</option>
                      <option value="v6tov4">v6tov4</option>
                      <option value="v6tov6">v6tov6</option>
                    </select>
                    <span class="input-hint">IPv4/IPv6 转发协议类型</span>
                  </div>
                </div>
                <div class="button-group" style="margin-top:16px;">
                  <button class="scan-button" @click="addProxyRule" :disabled="proxyLoading">
                    <span v-if="proxyLoading" class="loading"></span>
                    <span v-else>{{ proxyEditing ? '保存修改' : '确认添加' }}</span>
                  </button>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </main>
    
    <!-- 弹窗组件 -->
    <ModalDialog
      :visible="modalVisible"
      :type="modalType"
      :title="modalTitle"
      :message="modalMessage"
      :placeholder="modalPlaceholder"
      :confirm-text="modalConfirmText"
      @close="modalVisible = false"
      @confirm="handleModalConfirm"
      @cancel="handleModalCancel"
    />
  </div>
</template>