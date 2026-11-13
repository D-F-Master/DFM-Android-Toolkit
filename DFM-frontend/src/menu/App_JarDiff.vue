<script lang="ts" setup>
const emit = defineEmits(['back']);

const goHome = () => {
  emit('back');
};
</script>

<template>
  <div class="app">
    <button class="back-button" @click="goHome">&lt;</button>
    <main>
      <div class="container">
        <div class="content">
          <!-- jar包对比工具（待开发，页面先留空） -->
        </div>
      </div>
    </main>
  </div>
</template>

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
</style>
