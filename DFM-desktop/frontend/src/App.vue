<script lang="ts" setup>
import { ref, onMounted } from 'vue';
import App_Utils from './menu/App_Utils.vue';

const currentPage = ref('utils');
const appVersion = ref('V2.0.0');

// 加载配置获取版本号
onMounted(async () => {
  try {
    const response = await fetch('/config.json');
    if (response.ok) {
      const config = await response.json();
      if (config.version) {
        appVersion.value = config.version;
      }
    }
  } catch (error) {
    console.error('加载配置失败:', error);
  }
});
</script>

<template>
  <App_Utils />
</template>
