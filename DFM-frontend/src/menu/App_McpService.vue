<script lang="ts" setup>
import { ref, onMounted, onUnmounted } from 'vue';
import ModalDialog from '../components/ModalDialog.vue';

const emit = defineEmits(['back']);

// ==================== 基础状态 ====================
const API_BASE = ref('http://localhost:3000');
const errorMessage = ref('');
const loading = ref(false);

interface McpServer {
  name: string;
  version: string;
  description: string;
  mcpVersion: string | null;
  installStatus: 'installing' | 'ready' | 'install_failed' | null;
  installError: string | null;
  running: boolean;
  port: number | null;
  pid: number | null;
  lastHeartbeat: string | null;
  endpoint: string | null;
}

const servers = ref<McpServer[]>([]);

// 加载后端配置的 API 地址
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

// ==================== 弹窗状态 ====================
const modalVisible = ref(false);
const modalType = ref('confirm');
const modalTitle = ref('');
const modalMessage = ref('');
const modalPlaceholder = ref('');
const modalConfirmText = ref('确定');
const modalCallback = ref<Function | null>(null);

const showMessage = (message: string, title: string = '提示') => {
  modalType.value = 'message';
  modalTitle.value = title;
  modalMessage.value = message;
  modalCallback.value = null;
  modalVisible.value = true;
};

const showConfirm = (message: string, callback: Function, title: string = '确认') => {
  modalType.value = 'confirm';
  modalTitle.value = title;
  modalMessage.value = message;
  modalVisible.value = true;
  modalCallback.value = callback;
};

const showPrompt = (message: string, placeholder: string, callback: Function, title: string = '输入') => {
  modalType.value = 'prompt';
  modalTitle.value = title;
  modalMessage.value = message;
  modalPlaceholder.value = placeholder;
  modalVisible.value = true;
  modalCallback.value = callback;
};

const handleModalConfirm = (value?: string) => {
  if (modalCallback.value) {
    modalCallback.value(value);
  }
  modalVisible.value = false;
};

const handleModalCancel = () => {
  modalVisible.value = false;
};

// ==================== 服务列表 ====================
const loadServers = async () => {
  try {
    const response = await fetch(`${API_BASE.value}/api/mcp/list`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({})
    });
    const data = await response.json();
    if (data.code === 200) {
      servers.value = data.data || [];
      errorMessage.value = '';
    } else {
      errorMessage.value = data.message || '获取服务列表失败';
    }
  } catch (error: any) {
    errorMessage.value = `获取服务列表失败: ${error.message}`;
  }
};

// 启动服务
const startServer = async (item: McpServer) => {
  try {
    const response = await fetch(`${API_BASE.value}/api/mcp/start`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: item.name })
    });
    const data = await response.json();
    if (data.code === 200) {
      showMessage(`服务 ${item.name} 已启动，端口 ${data.data.port}`, '启动成功');
    } else {
      showMessage(data.message || '启动失败', '启动失败');
    }
    await loadServers();
  } catch (error: any) {
    showMessage(`启动失败: ${error.message}`, '启动失败');
  }
};

// 停止服务
const stopServer = async (item: McpServer) => {
  try {
    const response = await fetch(`${API_BASE.value}/api/mcp/stop`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: item.name })
    });
    const data = await response.json();
    if (data.code === 200) {
      showMessage(`服务 ${item.name} 已停止`, '停止成功');
    } else {
      showMessage(data.message || '停止失败', '停止失败');
    }
    await loadServers();
  } catch (error: any) {
    showMessage(`停止失败: ${error.message}`, '停止失败');
  }
};

// 设置固定端口（写入 manifest.json，重复/占用由后端拒绝）
const setPort = (item: McpServer) => {
  showPrompt(
    `请输入固定端口（当前: ${item.port || '未设置'}，范围 3010~3050，运行中需先停止）`,
    '例如 3010',
    (portText: string) => {
      const port = parseInt(portText, 10);
      if (!portText || isNaN(port) || port < 3010 || port > 3050) {
        deferModal(() => showMessage('端口无效，需为 3010~3050 的整数', '提示'));
        return;
      }
      applyPort(item.name, port);
    }
  );
};

const applyPort = async (name: string, port: number) => {
  try {
    const response = await fetch(`${API_BASE.value}/api/mcp/set-port`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, port })
    });
    const data = await response.json();
    if (data.code === 200) {
      showMessage(`服务 ${name} 固定端口已设为 ${port}`, '设置成功');
    } else {
      showMessage(data.message || '设置失败', '设置失败');
    }
    await loadServers();
  } catch (error: any) {
    showMessage(`设置失败: ${error.message}`, '设置失败');
  }
};

// 卸载服务
const uninstallServer = (item: McpServer) => {
  showConfirm(`确定卸载 MCP 服务「${item.name}」吗？该服务的所有文件和依赖将被删除。`, async () => {
    try {
      const response = await fetch(`${API_BASE.value}/api/mcp/uninstall`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: item.name })
      });
      const data = await response.json();
      if (data.code === 200) {
        showMessage(`服务 ${item.name} 已卸载`, '卸载成功');
      } else {
        showMessage(data.message || '卸载失败', '卸载失败');
      }
      await loadServers();
    } catch (error: any) {
      showMessage(`卸载失败: ${error.message}`, '卸载失败');
    }
  }, '卸载确认');
};

// ==================== 日志查看 ====================
const logsVisible = ref(false);
const logsName = ref('');
const logsType = ref<'server' | 'install'>('server');
const logsContent = ref('');

const viewLogs = async (item: McpServer, type: 'server' | 'install') => {
  logsName.value = item.name;
  logsType.value = type;
  logsContent.value = '加载中...';
  logsVisible.value = true;
  try {
    const response = await fetch(`${API_BASE.value}/api/mcp/logs`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: item.name, type, lines: 200 })
    });
    const data = await response.json();
    if (data.code === 200) {
      logsContent.value = data.data.logs || '（日志为空）';
    } else {
      logsContent.value = `读取失败: ${data.message}`;
    }
  } catch (error: any) {
    logsContent.value = `读取失败: ${error.message}`;
  }
};

const closeLogs = () => {
  logsVisible.value = false;
};

// ==================== 创建模板项目 ====================
const creating = ref(false);

const createTemplate = async (name: string, path: string) => {
  creating.value = true;
  try {
    const response = await fetch(`${API_BASE.value}/api/mcp/create-template`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, path })
    });
    const data = await response.json();
    if (data.code === 200) {
      showMessage(`模板项目已生成: ${data.data.path}`, '创建成功');
    } else {
      showMessage(data.message || '创建失败', '创建失败');
    }
  } catch (error: any) {
    showMessage(`创建失败: ${error.message}`, '创建失败');
  } finally {
    creating.value = false;
  }
};

// 两步弹窗输入：项目名称 → 目标目录
// ModalDialog 确认后会自己触发 close 并把 visible 置 false，同步在回调里再开
// 新弹窗会被这个 close 覆盖掉，因此链式弹窗/提示必须推迟到确认流程结束后
const deferModal = (fn: Function) => setTimeout(fn, 0);

const showCreateTemplateDialog = () => {
  showPrompt('请输入项目名称（字母、数字、下划线、中划线）', '例如 demo-server', (name: string) => {
    if (!name || !name.trim()) {
      deferModal(() => showMessage('项目名称不能为空', '提示'));
      return;
    }
    deferModal(() => chooseTargetDir(name.trim()));
  });
};

// 第二步：选择目标目录。uTools 环境用系统目录选择框；浏览器开发环境退回手动输入
const chooseTargetDir = (name: string) => {
  if (window.utools && window.utools.showOpenDialog) {
    // 返回所选目录路径数组，取消返回 undefined
    const selected = window.utools.showOpenDialog({ title: '选择目标目录', properties: ['openDirectory'] });
    if (selected && selected.length > 0) {
      createTemplate(name, selected[0]);
    }
    return;
  }
  showPrompt('请输入目标目录（绝对路径，将在其下创建同名项目文件夹）', '例如 D:\\projects', (path: string) => {
    if (!path || !path.trim()) {
      deferModal(() => showMessage('目标目录不能为空', '提示'));
      return;
    }
    createTemplate(name, path.trim());
  });
};

// ==================== 上传安装 ====================
const fileInput = ref<HTMLInputElement | null>(null);
const uploading = ref(false);

const triggerFileSelect = () => {
  if (fileInput.value) {
    fileInput.value.click();
  }
};

const onFileChange = (event: Event) => {
  const target = event.target as HTMLInputElement;
  const file = target.files && target.files.length > 0 ? target.files[0] : null;
  if (file) {
    uploadPackage(file);
  }
};

const uploadPackage = async (file: File) => {
  uploading.value = true;
  try {
    const formData = new FormData();
    formData.append('package', file);
    const response = await fetch(`${API_BASE.value}/api/mcp/upload`, {
      method: 'POST',
      body: formData
    });
    const data = await response.json();
    if (data.code === 200) {
      showMessage(`服务 ${data.data.name} 已开始安装，依赖安装完成后即可启动`, '上传成功');
      if (fileInput.value) {
        fileInput.value.value = '';
      }
    } else {
      showMessage(data.message || '上传失败', '上传失败');
    }
    await loadServers();
  } catch (error: any) {
    showMessage(`上传失败: ${error.message}`, '上传失败');
  } finally {
    uploading.value = false;
  }
};

// ==================== 生命周期 ====================
let refreshTimer: number | null = null;

onMounted(async () => {
  await loadConfig();
  await loadServers();
  // 每 5 秒刷新一次列表，覆盖安装进度与心跳状态变化
  refreshTimer = window.setInterval(loadServers, 5000);
});

onUnmounted(() => {
  if (refreshTimer !== null) {
    clearInterval(refreshTimer);
    refreshTimer = null;
  }
});

// 安装状态文案与样式
const installStatusText = (item: McpServer) => {
  if (item.installStatus === 'installing') return '安装中';
  if (item.installStatus === 'install_failed') return '安装失败';
  if (item.installStatus === 'ready') return '就绪';
  return '未知';
};

const formatHeartbeat = (item: McpServer) => {
  if (!item.lastHeartbeat) return '-';
  return item.lastHeartbeat.replace('T', ' ').substring(0, 19);
};

// 打开服务的 MCP 端点（供 AIAgent 接入时复制/访问）
const openEndpoint = (endpoint: string) => {
  if (window.utools && window.utools.shellOpenExternal) {
    // uTools 环境：调用系统默认浏览器打开
    window.utools.shellOpenExternal(endpoint);
  } else {
    // 浏览器开发环境 fallback：新标签页打开
    window.open(endpoint, '_blank');
  }
};
</script>

<template>
  <div class="app">
    <button class="back-button" @click="emit('back')">&lt;</button>
    <div class="container">
      <div class="content">
        <div v-if="errorMessage" class="error-message">{{ errorMessage }}</div>

        <!-- MCP 服务列表 -->
        <div class="card">
          <div class="port-info-header">
            <span class="card-icon">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="18" height="18">
                <rect x="2" y="3" width="20" height="14" rx="2"/>
                <line x1="8" y1="21" x2="16" y2="21"/>
                <line x1="12" y1="17" x2="12" y2="21"/>
              </svg>
            </span>
            <span class="port-info-title">MCP 服务列表</span>
            <div class="port-info-actions">
              <span class="port-count" v-if="servers.length > 0">
                共 {{ servers.length }} 个服务
              </span>
              <button class="refresh-button" @click="loadServers">刷新</button>
              <button class="create-button" @click="showCreateTemplateDialog" :disabled="creating">创建模板项目</button>
              <button class="upload-button" @click="triggerFileSelect" :disabled="uploading">
                <span v-if="uploading" class="loading"></span>
                <span v-else>上传安装包</span>
              </button>
              <input
                ref="fileInput"
                type="file"
                accept=".zip"
                style="display: none"
                @change="onFileChange"
              >
            </div>
          </div>

          <div class="table-container" v-if="servers.length > 0">
            <table class="mcp-table">
              <thead>
                <tr>
                  <th class="col-name">名称</th>
                  <th class="col-version">端口</th>
                  <th class="col-version">版本</th>
                  <th class="col-version">MCP SDK</th>
                  <th class="col-status">安装状态</th>
                  <th class="col-status">运行状态</th>
                  <th class="col-endpoint">端点</th>
                  <th class="col-heartbeat">最近心跳</th>
                  <th class="col-action">操作</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="item in servers" :key="item.name" class="mcp-row">
                  <td class="col-name" :title="item.description">{{ item.name }}</td>
                  <td class="col-version">{{ item.port || '-' }}</td>
                  <td class="col-version">{{ item.version || '-' }}</td>
                  <td class="col-version">{{ item.mcpVersion || '-' }}</td>
                  <td class="col-status">
                    <span
                      class="state-badge"
                      :class="{
                        'state-ok': item.installStatus === 'ready',
                        'state-warn': item.installStatus === 'installing',
                        'state-fail': item.installStatus === 'install_failed'
                      }"
                      :title="item.installError || ''"
                    >
                      {{ installStatusText(item) }}
                    </span>
                  </td>
                  <td class="col-status">
                    <span class="state-badge" :class="item.running ? 'state-ok' : 'state-idle'">
                      {{ item.running ? '运行中' : '已停止' }}
                    </span>
                  </td>
                  <td class="col-endpoint" :title="item.endpoint || ''">
                    <span v-if="item.endpoint" class="endpoint-link" @click="openEndpoint(item.endpoint)">{{ item.endpoint }}</span>
                    <span v-else>-</span>
                  </td>
                  <td class="col-heartbeat">{{ formatHeartbeat(item) }}</td>
                  <td class="col-action">
                    <button
                      v-if="!item.running"
                      class="start-button"
                      @click="startServer(item)"
                      :disabled="item.installStatus !== 'ready'"
                    >
                      启动
                    </button>
                    <button
                      v-else
                      class="stop-button"
                      @click="stopServer(item)"
                    >
                      停止
                    </button>
                    <button class="log-button" @click="setPort(item)">端口</button>
                    <button class="log-button" @click="viewLogs(item, 'server')">运行日志</button>
                    <button class="log-button" @click="viewLogs(item, 'install')">安装日志</button>
                    <button
                      class="uninstall-button"
                      @click="uninstallServer(item)"
                      :disabled="item.installStatus === 'installing'"
                    >
                      卸载
                    </button>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <div v-else class="empty-state">
            暂无 MCP 服务，请通过上方「上传安装包」安装，或先「创建模板项目」开发自己的服务
          </div>
        </div>
      </div>
    </div>

    <!-- 日志弹层 -->
    <div v-if="logsVisible" class="logs-overlay" @click.self="closeLogs">
      <div class="logs-panel">
        <div class="logs-header">
          <span>{{ logsName }} - {{ logsType === 'server' ? '运行日志' : '安装日志' }}</span>
          <button class="logs-close" @click="closeLogs">&times;</button>
        </div>
        <pre class="logs-content">{{ logsContent }}</pre>
      </div>
    </div>

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

<style scoped>
/* ===== 返回按钮 ===== */
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
}

.content {
  flex: 1;
  padding: 16px;
  overflow-y: auto;
  height: 100%;
  background: #f0f2f5;
}

/* ===== 卡片通用样式（匹配端口工具） ===== */
.card {
  background: #fff;
  border-radius: 10px;
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.08);
  overflow: hidden;
  margin-bottom: 12px;
}

.card-icon {
  display: flex;
  align-items: center;
  color: #4caf50;
}

/* ===== 主操作按钮（绿色） ===== */
.refresh-button,
.upload-button {
  height: 32px;
  padding: 0 14px;
  background: #4caf50;
  color: #fff;
  border: none;
  border-radius: 8px;
  cursor: pointer;
  font-size: 12px;
  font-weight: 500;
  font-family: inherit;
  transition: all 0.25s ease;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  white-space: nowrap;
}

/* 辅助按钮（蓝色） */
.create-button {
  height: 32px;
  padding: 0 14px;
  background: #2196f3;
  color: #fff;
  border: none;
  border-radius: 8px;
  cursor: pointer;
  font-size: 12px;
  font-weight: 500;
  font-family: inherit;
  transition: all 0.25s ease;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  white-space: nowrap;
}

.create-button:hover {
  background: #1976d2;
  transform: translateY(-1px);
}

.create-button:disabled {
  background: #bdbdbd;
  cursor: not-allowed;
  transform: none;
}

.refresh-button:hover,
.upload-button:hover {
  background: #43a047;
  transform: translateY(-1px);
}

.refresh-button:active,
.upload-button:active {
  transform: translateY(0);
}

.upload-button:disabled {
  background: #bdbdbd;
  cursor: not-allowed;
  transform: none;
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

/* ===== 列表头部 ===== */
.port-info-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 16px;
  border-bottom: 1px solid #f0f0f0;
  background: #fafafa;
}

.port-info-title {
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
  font-size: 12px;
  color: #999;
}

/* ===== 表格 ===== */
.table-container {
  overflow-x: auto;
}

.mcp-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

.mcp-table thead {
  background: #fafafa;
}

.mcp-table th {
  padding: 10px 12px;
  text-align: left;
  font-weight: 600;
  color: #666;
  border-bottom: 1px solid #f0f0f0;
  white-space: nowrap;
}

.mcp-table td {
  padding: 10px 12px;
  border-bottom: 1px solid #f5f5f5;
  color: #333;
}

.mcp-table tbody tr:hover {
  background: #f9fdf9;
}

.col-name {
  font-weight: 600;
}

.col-endpoint {
  max-width: 220px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.endpoint-link {
  color: #2196f3;
  cursor: pointer;
}

.endpoint-link:hover {
  text-decoration: underline;
}

/* ===== 状态徽章 ===== */
.state-badge {
  display: inline-block;
  padding: 2px 10px;
  border-radius: 10px;
  font-size: 12px;
  font-weight: 500;
  white-space: nowrap;
}

.state-ok {
  background: #e8f5e9;
  color: #2e7d32;
}

.state-warn {
  background: #fff3e0;
  color: #ef6c00;
}

.state-fail {
  background: #ffebee;
  color: #c62828;
}

.state-idle {
  background: #f0f0f0;
  color: #757575;
}

/* ===== 操作按钮 ===== */
.start-button {
  height: 28px;
  padding: 0 12px;
  background: #4caf50;
  color: #fff;
  border: none;
  border-radius: 6px;
  cursor: pointer;
  font-size: 12px;
  font-weight: 500;
  font-family: inherit;
  transition: all 0.25s ease;
  margin-right: 6px;
}

.start-button:hover {
  background: #43a047;
}

.start-button:disabled {
  background: #bdbdbd;
  cursor: not-allowed;
}

.stop-button {
  height: 28px;
  padding: 0 12px;
  background: #ff9800;
  color: #fff;
  border: none;
  border-radius: 6px;
  cursor: pointer;
  font-size: 12px;
  font-weight: 500;
  font-family: inherit;
  transition: all 0.25s ease;
  margin-right: 6px;
}

.stop-button:hover {
  background: #f57c00;
}

.log-button {
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
  transition: all 0.25s ease;
  margin-right: 6px;
}

.log-button:hover {
  background: #1976d2;
}

.uninstall-button {
  height: 28px;
  padding: 0 12px;
  background: #f44336;
  color: #fff;
  border: none;
  border-radius: 6px;
  cursor: pointer;
  font-size: 12px;
  font-weight: 500;
  font-family: inherit;
  transition: all 0.25s ease;
}

.uninstall-button:hover {
  background: #d32f2f;
}

.uninstall-button:disabled {
  background: #bdbdbd;
  cursor: not-allowed;
}

/* ===== 空状态 ===== */
.empty-state {
  padding: 32px 16px;
  text-align: center;
  font-size: 13px;
  color: #999;
}

/* ===== 日志弹层 ===== */
.logs-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 200;
}

.logs-panel {
  width: 80%;
  max-width: 860px;
  max-height: 80vh;
  background: #1e1e1e;
  border-radius: 10px;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.35);
}

.logs-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  background: #2d2d2d;
  color: #eee;
  font-size: 14px;
  font-weight: 600;
}

.logs-close {
  background: none;
  border: none;
  color: #aaa;
  font-size: 22px;
  cursor: pointer;
  line-height: 1;
}

.logs-close:hover {
  color: #fff;
}

.logs-content {
  margin: 0;
  padding: 16px;
  overflow: auto;
  color: #d4d4d4;
  font-size: 12px;
  line-height: 1.6;
  font-family: Consolas, Monaco, monospace;
  white-space: pre-wrap;
  word-break: break-all;
}

/* ===== 加载动画 ===== */
.loading {
  width: 14px;
  height: 14px;
  border: 2px solid rgba(255, 255, 255, 0.4);
  border-top-color: #fff;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
  display: inline-block;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}
</style>
