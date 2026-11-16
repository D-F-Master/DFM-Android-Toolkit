import { ref } from 'vue';

// ===== 自动安装 probe 设置（localStorage 持久化） =====
// 未设置或读取失败时默认开启，保持原有的自动安装行为。
const STORAGE_KEY = 'dfm-probe-auto-install';

const read = () => {
  try {
    return localStorage.getItem(STORAGE_KEY) !== '0';
  } catch {
    return true;
  }
};

const autoInstallProbe = ref(read());

const setAutoInstallProbe = (value) => {
  autoInstallProbe.value = value;
  try {
    localStorage.setItem(STORAGE_KEY, value ? '1' : '0');
  } catch {
    // localStorage 不可用时仅保留本次会话内的值
  }
};

export function useProbeSettings() {
  return { autoInstallProbe, setAutoInstallProbe };
}
