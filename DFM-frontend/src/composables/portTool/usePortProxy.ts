import { ref, watch } from 'vue';
import type { PortToolContext } from './usePortToolContext';

// 从 App_PortTool.vue 抽取；原逻辑未改动，仅把共享状态改为 ctx 参数。
export function usePortProxy(ctx: PortToolContext) {
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
      const response = await fetch(`${ctx.API_BASE.value}/api/portproxy/list`, {
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
      const response = await fetch(`${ctx.API_BASE.value}/api/portproxy/add`, {
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
    ctx.showConfirm(
      `确定要删除监听 ${rule.listenAddress}:${rule.listenPort} → ${rule.connectAddress}:${rule.connectPort} 的转发规则吗？`,
      async () => {
        proxyLoading.value = true;
        proxyError.value = '';
        try {
          const response = await fetch(`${ctx.API_BASE.value}/api/portproxy/delete`, {
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
  watch(ctx.activeTab, (newTab) => {
    if (newTab === 'portproxy') {
      loadProxyRules();
    }
  });

  return { proxyRules, proxyLoading, proxyError, proxyAddListenPort, proxyAddListenAddress, proxyAddConnectPort, proxyAddConnectAddress, proxyAddProtocol, proxyShowAddForm, proxyEditing, loadProxyRules, addProxyRule, deleteProxyRule, editProxyRule, toggleProxyForm };
}
