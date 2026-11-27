import { ref } from 'vue';
import type { PortToolContext } from './usePortToolContext';

// 从 App_PortTool.vue 抽取；原逻辑未改动，仅把共享状态改为 ctx 参数。
export function useIpTools(ctx: PortToolContext) {
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
  const connectingIp = ref<string | null>(null);
  const batchConnectStats = ref({
    total: 0,
    successCount: 0
  });

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
      ctx.errorMessage.value = '请输入有效的IP地址';
      return;
    }
  
    if (!subnetMask.value) {
      ctx.errorMessage.value = '请输入子网掩码';
      return;
    }
  
    isIpScanning.value = true;
    ctx.errorMessage.value = '';
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
      console.log('URL:', `${ctx.API_BASE.value}/api/ip/scan`);
      console.log('Request Body:', JSON.stringify(requestBody));
    
      const response = await fetch(`${ctx.API_BASE.value}/api/ip/scan`, {
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
        ctx.errorMessage.value = data.message || '扫描失败';
        console.error('扫描失败 - Error Code:', data.code, 'Message:', data.message);
      }
    } catch (error: any) {
      console.error('=== IP网段扫描错误 ===');
      console.error('Error Message:', error.message);
    
      if (error.message.includes('Failed to fetch')) {
        ctx.errorMessage.value = `无法连接到服务器 ${ctx.API_BASE.value}，请确保后端服务已启动`;
      } else {
        ctx.errorMessage.value = `扫描失败: ${error.message}`;
      }
    } finally {
      isIpScanning.value = false;
    }
  };

  const checkIpOnline = async () => {
    if (!validateIpFormat(checkSingleIp.value)) {
      ctx.errorMessage.value = '请输入有效的IP地址';
      return;
    }
  
    isCheckingIp.value = true;
    ctx.errorMessage.value = '';
    singleIpResult.value = null;
  
    try {
      const requestBody = {
        ip: checkSingleIp.value
      };
    
      console.log('=== 检查IP在线状态请求 ===');
      console.log('URL:', `${ctx.API_BASE.value}/api/ip/check`);
      console.log('Request Body:', JSON.stringify(requestBody));
    
      const response = await fetch(`${ctx.API_BASE.value}/api/ip/check`, {
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
        ctx.errorMessage.value = data.message || '检查失败';
        console.error('检查失败 - Error Code:', data.code, 'Message:', data.message);
      }
    } catch (error: any) {
      console.error('=== 检查IP在线状态错误 ===');
      console.error('Error Message:', error.message);
    
      if (error.message.includes('Failed to fetch')) {
        ctx.errorMessage.value = `无法连接到服务器 ${ctx.API_BASE.value}，请确保后端服务已启动`;
      } else {
        ctx.errorMessage.value = `检查失败: ${error.message}`;
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
      ctx.errorMessage.value = '请输入至少一个IP地址';
      return;
    }
  
    isBatchConnecting.value = true;
    ctx.errorMessage.value = '';
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
      console.log('URL:', `${ctx.API_BASE.value}/api/ip/batch-connect`);
      console.log('Request Body:', JSON.stringify(requestBody));
    
      const response = await fetch(`${ctx.API_BASE.value}/api/ip/batch-connect`, {
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
      
        ctx.modalType.value = 'notification';
        ctx.modalMessage.value = message;
        ctx.modalVisible.value = true;
      } else {
        ctx.errorMessage.value = data.message || '批量连接失败';
        console.error('批量连接失败 - Error Code:', data.code, 'Message:', data.message);
      }
    } catch (error: any) {
      console.error('=== 批量ADB连接错误 ===');
      console.error('Error Message:', error.message);
    
      if (error.message.includes('Failed to fetch')) {
        ctx.errorMessage.value = `无法连接到服务器 ${ctx.API_BASE.value}，请确保后端服务已启动`;
      } else {
        ctx.errorMessage.value = `批量连接失败: ${error.message}`;
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
    ctx.errorMessage.value = '';
  };

  const batchConnectScannedIps = async () => {
    if (ipScanResults.value.onlineIps.length === 0) {
      ctx.errorMessage.value = '没有可连接的在线IP';
      return;
    }
  
    // 将所有在线IP转换为带端口号的格式
    const ipsWithPort = ipScanResults.value.onlineIps.map(ip => ip + ':5555');
  
    isBatchConnecting.value = true;
    ctx.errorMessage.value = '';
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
      console.log('URL:', `${ctx.API_BASE.value}/api/ip/batch-connect`);
      console.log('Request Body:', JSON.stringify(requestBody));
    
      const response = await fetch(`${ctx.API_BASE.value}/api/ip/batch-connect`, {
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
      
        ctx.modalType.value = 'notification';
        ctx.modalMessage.value = message;
        ctx.modalVisible.value = true;
      } else {
        ctx.errorMessage.value = data.message || '批量连接失败';
        console.error('批量连接失败 - Error Code:', data.code, 'Message:', data.message);
      }
    } catch (error: any) {
      console.error('=== 批量ADB连接错误 ===');
      console.error('Error Message:', error.message);
    
      if (error.message.includes('Failed to fetch')) {
        ctx.errorMessage.value = `无法连接到服务器 ${ctx.API_BASE.value}，请确保后端服务已启动`;
      } else {
        ctx.errorMessage.value = `批量连接失败: ${error.message}`;
      }
    } finally {
      isBatchConnecting.value = false;
    }
  };

  const connectScannedIp = async (ip: string) => {
    connectingIp.value = ip;
    ctx.errorMessage.value = '';

    try {
      const response = await fetch(`${ctx.API_BASE.value}/api/ip/batch-connect`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({ ips: [`${ip}:5555`] })
      });

      const data = await response.json();
      if (data.code === 200) {
        const result = data.data?.results?.[0];
        const success = (data.data?.successCount || 0) > 0;
        const failMsg = result?.message || `${ip}:5555`;
        ctx.modalType.value = 'notification';
        ctx.modalMessage.value = success
          ? `ADB连接成功：${ip}:5555`
          : `ADB连接失败：${failMsg}`;
        ctx.modalVisible.value = true;
      } else {
        ctx.errorMessage.value = data.message || 'ADB连接失败';
      }
    } catch (error: any) {
      ctx.errorMessage.value = error.message.includes('Failed to fetch')
        ? `无法连接到服务器 ${ctx.API_BASE.value}，请确保后端服务已启动`
        : `ADB连接失败: ${error.message}`;
    } finally {
      connectingIp.value = null;
    }
  };

  return { scanIp, subnetMask, ipScanResults, isIpScanning, checkSingleIp, singleIpResult, isCheckingIp, batchConnectIps, batchConnectInput, batchConnectResults, isBatchConnecting, connectingIp, batchConnectStats, validateIpFormat, startIpScan, checkIpOnline, parseBatchConnectInput, batchAdbConnect, addIpToBatch, clearBatchConnect, batchConnectScannedIps, connectScannedIp };
}
