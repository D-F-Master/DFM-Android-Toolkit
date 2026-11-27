import { ref, computed } from 'vue';
import type { PortToolContext } from './usePortToolContext';

// 从 App_PortTool.vue 抽取；原逻辑未改动，仅把共享状态改为 ctx 参数。
export function usePortScan(ctx: PortToolContext) {
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
      console.log('URL:', `${ctx.API_BASE.value}/api/port/telnet`);
      console.log('Request Body:', JSON.stringify(requestBody));
    
      const response = await fetch(`${ctx.API_BASE.value}/api/port/telnet`, {
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
        ctx.errorMessage.value = data.message || '打开Telnet失败';
        console.error('Telnet失败 - Error Code:', data.code, 'Message:', data.message);
      }
    } catch (error: any) {
      console.error('=== Telnet连接错误 ===');
      console.error('Error Message:', error.message);
      ctx.errorMessage.value = `打开Telnet失败: ${error.message}`;
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


  // 常用端口预设
  const commonPortsList = [80, 443, 22, 21, 3306, 6379, 27017, 5432, 8080, 8443, 3389, 5900];

  // 计算属性：只返回开放的端口
  const openPorts = computed(() => {
    return scanResults.value.filter(item => item.status === 'open');
  });
  // ==================== 端口扫描功能 ====================
  const validateScanInput = (): { valid: boolean; needsConfirm: boolean; portCount?: number } => {
    // 验证IP地址
    const ipRegex = /^(\d{1,3}\.){3}\d{1,3}$/;
    if (!ipRegex.test(targetIp.value)) {
      ctx.errorMessage.value = '请输入有效的IP地址';
      return { valid: false, needsConfirm: false };
    }
  
    // 验证IP地址范围
    const parts = targetIp.value.split('.').map(Number);
    if (parts.some(part => part < 0 || part > 255)) {
      ctx.errorMessage.value = 'IP地址各段必须在0-255之间';
      return { valid: false, needsConfirm: false };
    }
  
    // 验证端口范围
    const start = Number(startPort.value);
    const end = Number(endPort.value);
  
    if (isNaN(start) || isNaN(end)) {
      ctx.errorMessage.value = '请输入有效的端口号';
      return { valid: false, needsConfirm: false };
    }
  
    if (start < 1 || start > 65535 || end < 1 || end > 65535) {
      ctx.errorMessage.value = '端口号必须在1-65535之间';
      return { valid: false, needsConfirm: false };
    }
  
    if (start > end) {
      ctx.errorMessage.value = '起始端口不能大于结束端口';
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
    ctx.errorMessage.value = '';
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
    
      console.log('ctx.API_BASE:', ctx.API_BASE.value);
      console.log('URL:', `${ctx.API_BASE.value}/api/port/scan`);
      console.log('Request Body:', JSON.stringify(requestBody, null, 2));
    
      const response = await fetch(`${ctx.API_BASE.value}/api/port/scan`, {
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
        ctx.errorMessage.value = data.message || '扫描失败';
        console.error('扫描失败 - Error Code:', data.code, 'Message:', data.message);
      }
    } catch (error: any) {
      console.error('=== 端口扫描错误 ===');
      console.error('Error Type:', error.name);
      console.error('Error Message:', error.message);
      console.error('Full Error:', error);
    
      // 提供更具体的错误信息
      if (error.message.includes('Failed to fetch')) {
        ctx.errorMessage.value = `无法连接到服务器 ${ctx.API_BASE.value}，请确保后端服务已启动`;
      } else {
        ctx.errorMessage.value = `扫描失败: ${error.message}`;
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
      ctx.showConfirm(
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
      ctx.errorMessage.value = '没有可导出的扫描结果';
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
    ctx.errorMessage.value = '';
  };


  return { targetIp, startPort, endPort, scanResults, scanProgress, isScanning, commonPortsList, openPorts, telnetToPort, openWebPage, validateScanInput, executeScan, startScan, scanCommonPorts, exportResults, clearScanResults };
}
