import { ref, type Ref } from 'vue';

export interface PortToolContext {
  API_BASE: Ref<string>;
  errorMessage: Ref<string>;
  modalVisible: Ref<boolean>;
  modalType: Ref<string>;
  modalTitle: Ref<string>;
  modalMessage: Ref<string>;
  modalPlaceholder: Ref<string>;
  modalConfirmText: Ref<string>;
  modalCallback: Ref<Function | null>;
  activeTab: Ref<'check' | 'scan' | 'ip' | 'portproxy'>;
  showMessage: (message: string, title?: string) => void;
  showConfirm: (message: string, callback: Function, title?: string) => void;
  handleModalConfirm: (value?: string) => void;
  handleModalCancel: () => void;
  loadConfig: () => Promise<void>;
}

// 端口工具四个 tab 共享的后端地址、错误提示与弹窗状态。
// activeTab 由调用方传入，供“切到端口转发页时自动加载规则”使用。
export function usePortToolContext(activeTab: Ref<'check' | 'scan' | 'ip' | 'portproxy'>): PortToolContext {
  const API_BASE = ref('http://localhost:3000');
  const errorMessage = ref('');
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

  return {
    API_BASE, errorMessage, modalVisible, modalType, modalTitle, modalMessage,
    modalPlaceholder, modalConfirmText, modalCallback, activeTab,
    showMessage, showConfirm, handleModalConfirm, handleModalCancel, loadConfig,
  };
}
