2<script lang="ts" setup>
import { ref, computed, onMounted, watch } from 'vue';
import ModalDialog from '../components/ModalDialog.vue';

const emit = defineEmits(['back']);

// Tab 切换状态
const activeTab = ref<'check' | 'scan' | 'ip' | 'portproxy'>('check');

// ==================== 端口占用检查相关 ====================
const API_BASE = ref('http://localhost:3000');
const portInput = ref('');
const loading = ref(false);
const portInfo = ref<Array<{ 
  pid: string; 
  process: string; 
  processPath?: string;
  protocol: string; 
  localAddress: string; 
  foreignAddress: string; 
  state: string 
}>>([]);
const errorMessage = ref('');

// ==================== 端口扫描相关 ====================
const targetIp = ref('127.0.0.1');
const startPort = ref('1');
const endPort = ref('1024');
const scanResults = ref<Array<{ port: number; status: string; service?: string }>>([]);
const scanProgress = ref(0);
const isScanning = ref(false);

// Telnet连接功能
const telnetToPort = async (port: number) => {
  try {
    const requestBody = {
      target: targetIp.value,
      port: port
    };
    
    console.log('=== Telnet连接请求 ===');
    console.log('URL:', `${API_BASE.value}/api/port/telnet`);
    console.log('Request Body:', JSON.stringify(requestBody));
    
    const response = await fetch(`${API_BASE.value}/api/port/telnet`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify(requestBody)
    });
    
    console.log('Response Status:', response.status);
    const data = await response.json();
    console.log('Response Data:', data);
    
    if (data.code === 200) {
      // 成功，不需要额外提示，因为已经打开了新窗口
      console.log('Telnet窗口已打开');
    } else {
      errorMessage.value = data.message || '打开Telnet失败';
      console.error('Telnet失败 - Error Code:', data.code, 'Message:', data.message);
    }
  } catch (error: any) {
    console.error('=== Telnet连接错误 ===');
    console.error('Error Message:', error.message);
    errorMessage.value = `打开Telnet失败: ${error.message}`;
  }
};

// 使用默认浏览器打开目标 IP 的指定端口网页
const openWebPage = (port: number) => {
  const url = `http://${targetIp.value}:${port}`;
  console.log('=== 打开网页 ===');
  console.log('URL:', url);
  
  if (window.utools && window.utools.shellOpenExternal) {
    // uTools 环境：调用系统默认浏览器打开
    window.utools.shellOpenExternal(url);
  } else {
    // 浏览器开发环境 fallback：新标签页打开
    window.open(url, '_blank');
  }
};

// ==================== IP扫描相关 ====================
const scanIp = ref('192.168.0.1');
const subnetMask = ref('255.255.255.0');
const ipScanResults = ref<{
  total: number;
  onlineCount: number;
  onlineIps: string[];
}>({
  total: 0,
  onlineCount: 0,
  onlineIps: []
});
const isIpScanning = ref(false);

const checkSingleIp = ref('');
const singleIpResult = ref<{
  ip: string;
  online: boolean;
} | null>(null);
const isCheckingIp = ref(false);

const batchConnectIps = ref<string[]>([]);
const batchConnectInput = ref('');
const batchConnectResults = ref<Array<{
  ip: string;
  success: boolean;
  message: string;
}>>([]);
const isBatchConnecting = ref(false);
const batchConnectStats = ref({
  total: 0,
  successCount: 0
});

// ==================== 端口转发管理相关 ====================
interface PortProxyRule {
  listenPort: number;
  listenAddress: string;
  connectAddress: string;
  connectPort: number;
  connectBindAddress?: string;
}

const proxyRules = ref<PortProxyRule[]>([]);
const proxyLoading = ref(false);
const proxyError = ref('');
const proxyAddListenPort = ref('');
const proxyAddListenAddress = ref('0.0.0.0');
const proxyAddConnectPort = ref('');
const proxyAddConnectAddress = ref('');
const proxyAddProtocol = ref('v4tov4');
const proxyShowAddForm = ref(false);
const proxyEditing = ref(false);

const loadProxyRules = async () => {
  proxyLoading.value = true;
  proxyError.value = '';
  try {
    const response = await fetch(`${API_BASE.value}/api/portproxy/list`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({})
    });
    const data = await response.json();
    if (data.code === 200) {
      proxyRules.value = data.data || [];
    } else {
      proxyError.value = data.message || '获取转发规则列表失败';
    }
  } catch (error: any) {
    proxyError.value = `获取转发规则失败: ${error.message}`;
  } finally {
    proxyLoading.value = false;
  }
};

const addProxyRule = async () => {
  const listenPort = Number(proxyAddListenPort.value);
  const connectPort = Number(proxyAddConnectPort.value);
  if (!proxyAddListenPort.value || !proxyAddConnectPort.value || !proxyAddConnectAddress.value) {
    proxyError.value = '请填写监听端口、目标地址和目标端口';
    return;
  }
  if (listenPort < 1 || listenPort > 65535 || connectPort < 1 || connectPort > 65535) {
    proxyError.value = '端口号必须在 1-65535 之间';
    return;
  }
  proxyLoading.value = true;
  proxyError.value = '';
  try {
    const response = await fetch(`${API_BASE.value}/api/portproxy/add`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        listenPort,
        listenAddress: proxyAddListenAddress.value || '0.0.0.0',
        connectPort,
        connectAddress: proxyAddConnectAddress.value,
        protocol: proxyAddProtocol.value
      })
    });
    const data = await response.json();
    if (data.code === 200) {
      proxyAddListenPort.value = '';
      proxyAddListenAddress.value = '0.0.0.0';
      proxyAddConnectPort.value = '';
      proxyAddConnectAddress.value = '';
      proxyAddProtocol.value = 'v4tov4';
      proxyShowAddForm.value = false;
      proxyEditing.value = false;
      await loadProxyRules();
    } else {
      proxyError.value = data.message || '添加转发规则失败';
    }
  } catch (error: any) {
    proxyError.value = `添加转发规则失败: ${error.message}`;
  } finally {
    proxyLoading.value = false;
  }
};

const deleteProxyRule = async (rule: PortProxyRule) => {
  showConfirm(
    `确定要删除监听 ${rule.listenAddress}:${rule.listenPort} → ${rule.connectAddress}:${rule.connectPort} 的转发规则吗？`,
    async () => {
      proxyLoading.value = true;
      proxyError.value = '';
      try {
        const response = await fetch(`${API_BASE.value}/api/portproxy/delete`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            listenPort: rule.listenPort,
            listenAddress: rule.listenAddress,
            protocol: proxyAddProtocol.value
          })
        });
        const data = await response.json();
        if (data.code === 200) {
          await loadProxyRules();
        } else {
          proxyError.value = data.message || '删除转发规则失败';
        }
      } catch (error: any) {
        proxyError.value = `删除转发规则失败: ${error.message}`;
      } finally {
        proxyLoading.value = false;
      }
    },
    '确认删除'
  );
};

// 编辑转发规则：将规则数据填充到表单
const editProxyRule = (rule: PortProxyRule) => {
  proxyEditing.value = true;
  proxyAddListenPort.value = String(rule.listenPort);
  proxyAddListenAddress.value = rule.listenAddress;
  proxyAddConnectPort.value = String(rule.connectPort);
  proxyAddConnectAddress.value = rule.connectAddress;
  proxyShowAddForm.value = true;
};

// 切换添加/编辑表单显示
const toggleProxyForm = () => {
  proxyShowAddForm.value = !proxyShowAddForm.value;
  if (!proxyShowAddForm.value) {
    proxyEditing.value = false;
  }
};

// 切换到端口转发页面时自动加载规则列表
watch(activeTab, (newTab) => {
  if (newTab === 'portproxy') {
    loadProxyRules();
  }
});

// 常用端口预设
const commonPortsList = [80, 443, 22, 21, 3306, 6379, 27017, 5432, 8080, 8443, 3389, 5900];

// 计算属性：只返回开放的端口
const openPorts = computed(() => {
  return scanResults.value.filter(item => item.status === 'open');
});

// 弹窗状态
const modalVisible = ref(false);
const modalType = ref('confirm');
const modalTitle = ref('');
const modalMessage = ref('');
const modalPlaceholder = ref('');
const modalConfirmText = ref('确定');
const modalCallback = ref<Function | null>(null);

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

const goHome = () => {
  emit('back');
};

// 弹窗方法
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

const handleModalConfirm = (value?: string) => {
  if (modalCallback.value) {
    modalCallback.value(value);
  }
  modalVisible.value = false;
};

const handleModalCancel = () => {
  modalVisible.value = false;
};

// ==================== 端口占用检查功能 ====================
const checkPort = async () => {
  const port = portInput.value.trim();
  if (!port) {
    errorMessage.value = '请输入端口号';
    return;
  }
  
  if (isNaN(Number(port))) {
    errorMessage.value = '请输入有效的端口号';
    return;
  }
  
  loading.value = true;
  errorMessage.value = '';
  portInfo.value = [];
  
  try {
    const requestBody = { port: Number(port) };
    console.log('=== 端口检查请求 ===');
    console.log('API_BASE:', API_BASE.value);
    console.log('URL:', `${API_BASE.value}/api/port/check`);
    console.log('Request Body:', JSON.stringify(requestBody, null, 2));
    
    const response = await fetch(`${API_BASE.value}/api/port/check`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify(requestBody)
    });
    
    console.log('Response Status:', response.status);
    console.log('Response OK:', response.ok);
    
    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`);
    }
    
    const data = await response.json();
    console.log('Response Data:', JSON.stringify(data, null, 2));
    
    if (data.code === 200) {
      portInfo.value = data.data || [];
      console.log('查询成功，收到', portInfo.value.length, '个进程信息');
    } else {
      errorMessage.value = data.message || '查询失败';
      console.error('查询失败 - Error Code:', data.code, 'Message:', data.message);
    }
  } catch (error: any) {
    console.error('=== 端口检查错误 ===');
    console.error('Error Type:', error.name);
    console.error('Error Message:', error.message);
    console.error('Full Error:', error);
    
    // 提供更具体的错误信息
    if (error.message.includes('Failed to fetch')) {
      errorMessage.value = `无法连接到服务器 ${API_BASE.value}，请确保后端服务已启动`;
    } else {
      errorMessage.value = `查询失败: ${error.message}`;
    }
  } finally {
    loading.value = false;
  }
};

const killProcess = async (pid: string) => {
  showConfirm(`确定要终止进程 ${pid} 吗？`, async () => {
    loading.value = true;
    
    try {
      const requestBody = { pid: Number(pid) };
      console.log('=== 终止进程请求 ===');
      console.log('URL:', `${API_BASE.value}/api/port/kill`);
      console.log('Request Body:', JSON.stringify(requestBody));
      
      const response = await fetch(`${API_BASE.value}/api/port/kill`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify(requestBody)
      });
      
      console.log('Response Status:', response.status);
      const data = await response.json();
      console.log('Response Data:', data);
      
      if (data.code === 200) {
        // 重新查询端口状态
        await checkPort();
      } else {
        errorMessage.value = data.message || '终止进程失败';
        console.error('终止进程失败 - Error Code:', data.code, 'Message:', data.message);
      }
    } catch (error: any) {
      console.error('=== 终止进程错误 ===');
      console.error('Error Message:', error.message);
      errorMessage.value = '终止进程失败';
    } finally {
      loading.value = false;
    }
  }, '确认终止');
};

const clearAllProcesses = async () => {
  if (!portInfo.value.length) return;
  
  showConfirm(`确定要终止所有占用端口 ${portInput.value} 的进程吗？`, async () => {
    loading.value = true;
    errorMessage.value = '';
    
    try {
      // 逐个终止进程
      for (const item of portInfo.value) {
        const requestBody = { pid: Number(item.pid) };
        console.log('=== 批量终止进程请求 ===');
        console.log('PID:', item.pid);
        console.log('Request Body:', JSON.stringify(requestBody));
        
        const response = await fetch(`${API_BASE.value}/api/port/kill`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json'
          },
          body: JSON.stringify(requestBody)
        });
        
        console.log('Response Status:', response.status);
        const data = await response.json();
        console.log('Response Data:', data);
        
        if (data.code !== 200) {
          throw new Error(`终止进程 ${item.pid} 失败: ${data.message || '未知错误'}`);
        }
      }
      
      // 重新查询端口状态
      await checkPort();
    } catch (error: any) {
      errorMessage.value = error.message || '清除全部占用失败';
    } finally {
      loading.value = false;
    }
  }, '确认清除');
};

// ==================== 端口扫描功能 ====================
const validateScanInput = (): { valid: boolean; needsConfirm: boolean; portCount?: number } => {
  // 验证IP地址
  const ipRegex = /^(\d{1,3}\.){3}\d{1,3}$/;
  if (!ipRegex.test(targetIp.value)) {
    errorMessage.value = '请输入有效的IP地址';
    return { valid: false, needsConfirm: false };
  }
  
  // 验证IP地址范围
  const parts = targetIp.value.split('.').map(Number);
  if (parts.some(part => part < 0 || part > 255)) {
    errorMessage.value = 'IP地址各段必须在0-255之间';
    return { valid: false, needsConfirm: false };
  }
  
  // 验证端口范围
  const start = Number(startPort.value);
  const end = Number(endPort.value);
  
  if (isNaN(start) || isNaN(end)) {
    errorMessage.value = '请输入有效的端口号';
    return { valid: false, needsConfirm: false };
  }
  
  if (start < 1 || start > 65535 || end < 1 || end > 65535) {
    errorMessage.value = '端口号必须在1-65535之间';
    return { valid: false, needsConfirm: false };
  }
  
  if (start > end) {
    errorMessage.value = '起始端口不能大于结束端口';
    return { valid: false, needsConfirm: false };
  }
  
  const portCount = end - start + 1;
  
  // 检查是否需要确认
  if (portCount > 1000) {
    return { valid: true, needsConfirm: true, portCount };
  }
  
  return { valid: true, needsConfirm: false };
};

const executeScan = async (ports?: number[]) => {
  loading.value = true;
  isScanning.value = true;
  errorMessage.value = '';
  scanResults.value = [];
  scanProgress.value = 0;
  
  try {
    let requestBody: any;
    
    // 如果提供了 ports 数组，使用方式二；否则使用方式一
    if (ports && ports.length > 0) {
      requestBody = {
        target: targetIp.value,
        ports: ports
      };
      console.log('=== 端口扫描请求（指定端口列表） ===');
    } else {
      requestBody = {
        target: targetIp.value,
        startPort: Number(startPort.value),
        endPort: Number(endPort.value)
      };
      console.log('=== 端口扫描请求（端口范围） ===');
    }
    
    console.log('API_BASE:', API_BASE.value);
    console.log('URL:', `${API_BASE.value}/api/port/scan`);
    console.log('Request Body:', JSON.stringify(requestBody, null, 2));
    
    const response = await fetch(`${API_BASE.value}/api/port/scan`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify(requestBody)
    });
    
    console.log('Response Status:', response.status);
    console.log('Response OK:', response.ok);
    console.log('Response Headers:', response.headers);
    
    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`);
    }
    
    const data = await response.json();
    console.log('Response Data:', JSON.stringify(data, null, 2));
    
    if (data.code === 200) {
      scanResults.value = data.data || [];
      scanProgress.value = 100;
      console.log('扫描成功，收到', scanResults.value.length, '个结果');
    } else {
      errorMessage.value = data.message || '扫描失败';
      console.error('扫描失败 - Error Code:', data.code, 'Message:', data.message);
    }
  } catch (error: any) {
    console.error('=== 端口扫描错误 ===');
    console.error('Error Type:', error.name);
    console.error('Error Message:', error.message);
    console.error('Full Error:', error);
    
    // 提供更具体的错误信息
    if (error.message.includes('Failed to fetch')) {
      errorMessage.value = `无法连接到服务器 ${API_BASE.value}，请确保后端服务已启动`;
    } else {
      errorMessage.value = `扫描失败: ${error.message}`;
    }
  } finally {
    loading.value = false;
    isScanning.value = false;
  }
};

const startScan = async () => {
  const validation = validateScanInput();
  
  if (!validation.valid) {
    return;
  }
  
  if (validation.needsConfirm && validation.portCount) {
    showConfirm(
      `将要扫描 ${validation.portCount} 个端口，这可能需要较长时间。确定继续吗？`,
      () => executeScan(),
      '确认扫描'
    );
  } else {
    await executeScan();
  }
};

const scanCommonPorts = async () => {
  targetIp.value = targetIp.value || '127.0.0.1';
  
  // 使用 ports 数组方式扫描常用端口
  await executeScan(commonPortsList);
};

const exportResults = () => {
  if (openPorts.value.length === 0) {
    errorMessage.value = '没有可导出的扫描结果';
    return;
  }
  
  const csvContent = [
    ['端口', '状态', '服务'].join(','),
    ...openPorts.value.map(item => [item.port, item.status, item.service || ''].join(','))
  ].join('\n');
  
  const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
  const link = document.createElement('a');
  const url = URL.createObjectURL(blob);
  link.setAttribute('href', url);
  link.setAttribute('download', `port_scan_${targetIp.value}_${new Date().getTime()}.csv`);
  link.style.visibility = 'hidden';
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
};

const clearScanResults = () => {
  scanResults.value = [];
  scanProgress.value = 0;
  errorMessage.value = '';
};

// ==================== IP扫描功能 ====================
const validateIpFormat = (ip: string): boolean => {
  const ipRegex = /^(\d{1,3}\.){3}\d{1,3}$/;
  if (!ipRegex.test(ip)) {
    return false;
  }
  
  const parts = ip.split('.').map(Number);
  return parts.every(part => part >= 0 && part <= 255);
};

const startIpScan = async () => {
  if (!validateIpFormat(scanIp.value)) {
    errorMessage.value = '请输入有效的IP地址';
    return;
  }
  
  if (!subnetMask.value) {
    errorMessage.value = '请输入子网掩码';
    return;
  }
  
  isIpScanning.value = true;
  errorMessage.value = '';
  ipScanResults.value = {
    total: 0,
    onlineCount: 0,
    onlineIps: []
  };
  
  try {
    const requestBody = {
      ip: scanIp.value,
      subnetMask: subnetMask.value
    };
    
    console.log('=== IP网段扫描请求 ===');
    console.log('URL:', `${API_BASE.value}/api/ip/scan`);
    console.log('Request Body:', JSON.stringify(requestBody));
    
    const response = await fetch(`${API_BASE.value}/api/ip/scan`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify(requestBody)
    });
    
    console.log('Response Status:', response.status);
    const data = await response.json();
    console.log('Response Data:', data);
    
    if (data.code === 200) {
      ipScanResults.value = data.data || {
        total: 0,
        onlineCount: 0,
        onlineIps: []
      };
      console.log('扫描成功，发现', ipScanResults.value.onlineCount, '个在线IP');
    } else {
      errorMessage.value = data.message || '扫描失败';
      console.error('扫描失败 - Error Code:', data.code, 'Message:', data.message);
    }
  } catch (error: any) {
    console.error('=== IP网段扫描错误 ===');
    console.error('Error Message:', error.message);
    
    if (error.message.includes('Failed to fetch')) {
      errorMessage.value = `无法连接到服务器 ${API_BASE.value}，请确保后端服务已启动`;
    } else {
      errorMessage.value = `扫描失败: ${error.message}`;
    }
  } finally {
    isIpScanning.value = false;
  }
};

const checkIpOnline = async () => {
  if (!validateIpFormat(checkSingleIp.value)) {
    errorMessage.value = '请输入有效的IP地址';
    return;
  }
  
  isCheckingIp.value = true;
  errorMessage.value = '';
  singleIpResult.value = null;
  
  try {
    const requestBody = {
      ip: checkSingleIp.value
    };
    
    console.log('=== 检查IP在线状态请求 ===');
    console.log('URL:', `${API_BASE.value}/api/ip/check`);
    console.log('Request Body:', JSON.stringify(requestBody));
    
    const response = await fetch(`${API_BASE.value}/api/ip/check`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify(requestBody)
    });
    
    console.log('Response Status:', response.status);
    const data = await response.json();
    console.log('Response Data:', data);
    
    if (data.code === 200) {
      singleIpResult.value = data.data || {
        ip: checkSingleIp.value,
        online: false
      };
      console.log('检查结果:', singleIpResult.value.online ? '在线' : '离线');
    } else {
      errorMessage.value = data.message || '检查失败';
      console.error('检查失败 - Error Code:', data.code, 'Message:', data.message);
    }
  } catch (error: any) {
    console.error('=== 检查IP在线状态错误 ===');
    console.error('Error Message:', error.message);
    
    if (error.message.includes('Failed to fetch')) {
      errorMessage.value = `无法连接到服务器 ${API_BASE.value}，请确保后端服务已启动`;
    } else {
      errorMessage.value = `检查失败: ${error.message}`;
    }
  } finally {
    isCheckingIp.value = false;
  }
};

const parseBatchConnectInput = () => {
  if (!batchConnectInput.value.trim()) {
    batchConnectIps.value = [];
    return;
  }
  
  // 支持逗号、分号、换行符分隔；未带端口的 IP 默认使用 5555
  const ips = batchConnectInput.value
    .split(/[,;\n]+/)
    .map(ip => ip.trim())
    .filter(ip => ip.length > 0)
    .map(ip => ip.includes(':') ? ip : `${ip}:5555`);
  
  batchConnectIps.value = ips;
};

const batchAdbConnect = async () => {
  parseBatchConnectInput();
  
  if (batchConnectIps.value.length === 0) {
    errorMessage.value = '请输入至少一个IP地址';
    return;
  }
  
  isBatchConnecting.value = true;
  errorMessage.value = '';
  batchConnectResults.value = [];
  batchConnectStats.value = {
    total: 0,
    successCount: 0
  };
  
  try {
    const requestBody = {
      ips: batchConnectIps.value
    };
    
    console.log('=== 批量ADB连接请求 ===');
    console.log('URL:', `${API_BASE.value}/api/ip/batch-connect`);
    console.log('Request Body:', JSON.stringify(requestBody));
    
    const response = await fetch(`${API_BASE.value}/api/ip/batch-connect`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify(requestBody)
    });
    
    console.log('Response Status:', response.status);
    const data = await response.json();
    console.log('Response Data:', data);
    
    if (data.code === 200) {
      batchConnectResults.value = data.data.results || [];
      batchConnectStats.value = {
        total: data.data.total || 0,
        successCount: data.data.successCount || 0
      };
      console.log('批量连接完成，成功', batchConnectStats.value.successCount, '个');
      
      // 显示 Toast 提示
      const failedCount = data.data.failedCount || (data.data.total - data.data.successCount);
      let message = `批量连接完成！\n总计: ${data.data.total} 个 | 成功: ${data.data.successCount} 个 | 失败: ${failedCount} 个`;
      
      modalType.value = 'notification';
      modalMessage.value = message;
      modalVisible.value = true;
    } else {
      errorMessage.value = data.message || '批量连接失败';
      console.error('批量连接失败 - Error Code:', data.code, 'Message:', data.message);
    }
  } catch (error: any) {
    console.error('=== 批量ADB连接错误 ===');
    console.error('Error Message:', error.message);
    
    if (error.message.includes('Failed to fetch')) {
      errorMessage.value = `无法连接到服务器 ${API_BASE.value}，请确保后端服务已启动`;
    } else {
      errorMessage.value = `批量连接失败: ${error.message}`;
    }
  } finally {
    isBatchConnecting.value = false;
  }
};

const addIpToBatch = (ip: string) => {
  if (!batchConnectInput.value) {
    batchConnectInput.value = ip + ':5555';
  } else {
    batchConnectInput.value += '\n' + ip + ':5555';
  }
  parseBatchConnectInput();
};

const clearBatchConnect = () => {
  batchConnectInput.value = '';
  batchConnectIps.value = [];
  batchConnectResults.value = [];
  batchConnectStats.value = {
    total: 0,
    successCount: 0
  };
  errorMessage.value = '';
};

const batchConnectScannedIps = async () => {
  if (ipScanResults.value.onlineIps.length === 0) {
    errorMessage.value = '没有可连接的在线IP';
    return;
  }
  
  // 将所有在线IP转换为带端口号的格式
  const ipsWithPort = ipScanResults.value.onlineIps.map(ip => ip + ':5555');
  
  isBatchConnecting.value = true;
  errorMessage.value = '';
  batchConnectResults.value = [];
  batchConnectStats.value = {
    total: 0,
    successCount: 0
  };
  
  try {
    const requestBody = {
      ips: ipsWithPort
    };
    
    console.log('=== 批量ADB连接请求（扫描结果） ===');
    console.log('URL:', `${API_BASE.value}/api/ip/batch-connect`);
    console.log('Request Body:', JSON.stringify(requestBody));
    
    const response = await fetch(`${API_BASE.value}/api/ip/batch-connect`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify(requestBody)
    });
    
    console.log('Response Status:', response.status);
    const data = await response.json();
    console.log('Response Data:', data);
    
    if (data.code === 200) {
      batchConnectResults.value = data.data.results || [];
      batchConnectStats.value = {
        total: data.data.total || 0,
        successCount: data.data.successCount || 0
      };
      console.log('批量连接完成，成功', batchConnectStats.value.successCount, '个');
      
      // 显示 Toast 提示
      const failedCount = data.data.failedCount || (data.data.total - data.data.successCount);
      let message = `批量连接完成！\n总计: ${data.data.total} 个 | 成功: ${data.data.successCount} 个 | 失败: ${failedCount} 个`;
      
      modalType.value = 'notification';
      modalMessage.value = message;
      modalVisible.value = true;
    } else {
      errorMessage.value = data.message || '批量连接失败';
      console.error('批量连接失败 - Error Code:', data.code, 'Message:', data.message);
    }
  } catch (error: any) {
    console.error('=== 批量ADB连接错误 ===');
    console.error('Error Message:', error.message);
    
    if (error.message.includes('Failed to fetch')) {
      errorMessage.value = `无法连接到服务器 ${API_BASE.value}，请确保后端服务已启动`;
    } else {
      errorMessage.value = `批量连接失败: ${error.message}`;
    }
  } finally {
    isBatchConnecting.value = false;
  }
};

onMounted(async () => {
  await loadConfig();
});
</script>

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

/* ===== 卡片通用样式（匹配 ADB 工具） ===== */
.card {
  background: #fff;
  border-radius: 10px;
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.08);
  overflow: hidden;
  margin-bottom: 12px;
}

.card-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 16px;
  font-size: 14px;
  font-weight: 600;
  color: #333;
  border-bottom: 1px solid #f0f0f0;
  background: #fafafa;
}

.card-icon {
  display: flex;
  align-items: center;
  color: #4caf50;
}

.card-body {
  padding: 16px;
}

/* ===== Tab 切换栏 ===== */
.tab-container {
  margin-bottom: 12px;
}

.tab-buttons {
  display: flex;
  gap: 4px;
  background: #fff;
  border-radius: 10px;
  padding: 4px;
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.08);
}

.tab-button {
  flex: 1;
  padding: 10px 16px;
  border: none;
  background: transparent;
  cursor: pointer;
  font-size: 14px;
  font-weight: 500;
  color: #666;
  border-radius: 8px;
  transition: all 0.35s cubic-bezier(0.16, 1, 0.3, 1);
  font-family: inherit;
}

.tab-button:hover {
  background: #f5f5f5;
  color: #333;
}

.tab-button.active {
  background: #4caf50;
  color: #fff;
  box-shadow: 0 2px 8px rgba(76, 175, 80, 0.25);
}

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
                class="tab-button" 
                :class="{ active: activeTab === 'check' }"
                @click="activeTab = 'check'"
              >
                端口占用检查
              </button>
              <button 
                class="tab-button" 
                :class="{ active: activeTab === 'scan' }"
                @click="activeTab = 'scan'"
              >
                端口扫描
              </button>
              <button 
                class="tab-button" 
                :class="{ active: activeTab === 'ip' }"
                @click="activeTab = 'ip'"
              >
                IP工具
              </button>
              <button 
                class="tab-button" 
                :class="{ active: activeTab === 'portproxy' }"
                @click="activeTab = 'portproxy'"
              >
                端口转发
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