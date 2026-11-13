import { ref, computed } from 'vue';

// ===== 全局配置单例 =====
// config.json 只加载一次，多个组件共享同一份响应式数据。
// 用法：
//   const { config, isFeatureOn } = useAppConfig();
//   isFeatureOn('utils')            // 顶层功能开关
//   isFeatureOn('utilsTabs.info')   // 嵌套开关（点号分层）
//
// 规则：开关"默认开启"，只有配置里显式写 false 才隐藏。
//       这样 config.json 加载失败或漏配时，界面不至于空白。

const config = ref({});
let loadPromise = null;

function load() {
  if (!loadPromise) {
    loadPromise = fetch('/config.json')
      .then(res => (res.ok ? res.json() : {}))
      .then(data => { config.value = data || {}; })
      .catch(err => {
        console.error('加载配置失败:', err);
        config.value = {};
      });
  }
  return loadPromise;
}

/**
 * 读取 features 开关，支持 'utilsTabs.info' 这种嵌套路径。
 * 返回 true 表示启用（含未配置的默认情况），false 表示配置里显式关闭。
 */
function isFeatureOn(path) {
  let node = config.value.features;
  for (const key of path.split('.')) {
    if (node == null || typeof node !== 'object') return true; // 中间层缺失 → 默认开启
    if (!(key in node)) return true;                           // 键未配置   → 默认开启
    node = node[key];
  }
  return node !== false;
}

export function useAppConfig() {
  load();

  const version = computed(() => config.value.version || '');
  const edition = computed(() => config.value.edition || '');

  return { config, version, edition, isFeatureOn };
}
