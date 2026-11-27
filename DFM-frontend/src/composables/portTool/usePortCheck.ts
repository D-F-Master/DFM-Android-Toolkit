import { ref } from 'vue';
import type { PortToolContext } from './usePortToolContext';

// 从 App_PortTool.vue 抽取；原逻辑未改动，仅把共享状态改为 ctx 参数。
export function usePortCheck(ctx: PortToolContext) {
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
  // ==================== 端口占用检查功能 ====================
  const checkPort = async () => {
    const port = portInput.value.trim();
    if (!port) {
      ctx.errorMessage.value = '请输入端口号';
      return;
    }
  
    if (isNaN(Number(port))) {
      ctx.errorMessage.value = '请输入有效的端口号';
      return;
    }
  
    loading.value = true;
    ctx.errorMessage.value = '';
    portInfo.value = [];
  
    try {
      const requestBody = { port: Number(port) };
      console.log('=== 端口检查请求 ===');
      console.log('ctx.API_BASE:', ctx.API_BASE.value);
      console.log('URL:', `${ctx.API_BASE.value}/api/port/check`);
      console.log('Request Body:', JSON.stringify(requestBody, null, 2));
    
      const response = await fetch(`${ctx.API_BASE.value}/api/port/check`, {
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
        ctx.errorMessage.value = data.message || '查询失败';
        console.error('查询失败 - Error Code:', data.code, 'Message:', data.message);
      }
    } catch (error: any) {
      console.error('=== 端口检查错误 ===');
      console.error('Error Type:', error.name);
      console.error('Error Message:', error.message);
      console.error('Full Error:', error);
    
      // 提供更具体的错误信息
      if (error.message.includes('Failed to fetch')) {
        ctx.errorMessage.value = `无法连接到服务器 ${ctx.API_BASE.value}，请确保后端服务已启动`;
      } else {
        ctx.errorMessage.value = `查询失败: ${error.message}`;
      }
    } finally {
      loading.value = false;
    }
  };

  const killProcess = async (pid: string) => {
    ctx.showConfirm(`确定要终止进程 ${pid} 吗？`, async () => {
      loading.value = true;
    
      try {
        const requestBody = { pid: Number(pid) };
        console.log('=== 终止进程请求 ===');
        console.log('URL:', `${ctx.API_BASE.value}/api/port/kill`);
        console.log('Request Body:', JSON.stringify(requestBody));
      
        const response = await fetch(`${ctx.API_BASE.value}/api/port/kill`, {
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
          ctx.errorMessage.value = data.message || '终止进程失败';
          console.error('终止进程失败 - Error Code:', data.code, 'Message:', data.message);
        }
      } catch (error: any) {
        console.error('=== 终止进程错误 ===');
        console.error('Error Message:', error.message);
        ctx.errorMessage.value = '终止进程失败';
      } finally {
        loading.value = false;
      }
    }, '确认终止');
  };

  const clearAllProcesses = async () => {
    if (!portInfo.value.length) return;
  
    ctx.showConfirm(`确定要终止所有占用端口 ${portInput.value} 的进程吗？`, async () => {
      loading.value = true;
      ctx.errorMessage.value = '';
    
      try {
        // 逐个终止进程
        for (const item of portInfo.value) {
          const requestBody = { pid: Number(item.pid) };
          console.log('=== 批量终止进程请求 ===');
          console.log('PID:', item.pid);
          console.log('Request Body:', JSON.stringify(requestBody));
        
          const response = await fetch(`${ctx.API_BASE.value}/api/port/kill`, {
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
        ctx.errorMessage.value = error.message || '清除全部占用失败';
      } finally {
        loading.value = false;
      }
    }, '确认清除');
  };


  return { portInput, loading, portInfo, checkPort, killProcess, clearAllProcesses };
}
