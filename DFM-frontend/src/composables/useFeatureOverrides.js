import { ref } from 'vue';

// ===== 功能显示覆盖（localStorage 持久化） =====
// 只存被用户改过的 features 路径，未覆盖的路径仍走 config.json。
const STORAGE_KEY = 'dfm-feature-overrides';

const read = () => {
  try {
    const raw = JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}');
    return raw && typeof raw === 'object' && !Array.isArray(raw) ? raw : {};
  } catch {
    return {};
  }
};

const overrides = ref(read());

const setOverride = (path, enabled) => {
  const next = { ...overrides.value, [path]: enabled };
  overrides.value = next;
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
  } catch {
    // localStorage 不可用时仅保留本次会话内的值
  }
};

export function useFeatureOverrides() {
  return { overrides, setOverride };
}
