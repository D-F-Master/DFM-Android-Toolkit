<template>
  <div v-if="visible" :class="['modal-overlay', { 'notification-overlay': type === 'notification', 'notification-leaving': type === 'notification' && isLeaving }]" @click.self="close">
    <div :class="['modal-content', { 'notification-content': type === 'notification' }, type === 'notification' ? `notification-${variant}` : '']">
      <div v-if="type !== 'notification'" class="modal-header">
        <h3>{{ title }}</h3>
        <button class="close-button" @click="close">&times;</button>
      </div>
      <div class="modal-body">
        <p v-if="type === 'message' || type === 'confirm' || type === 'notification'">{{ message }}</p>
        <input 
          v-if="type === 'prompt'" 
          v-model="inputValue" 
          type="text" 
          class="input-field" 
          :placeholder="placeholder"
          @keyup.enter="handleEnter"
          ref="inputRef"
        />
      </div>
      <div v-if="type !== 'notification'" class="modal-footer">
        <button 
          v-if="type === 'confirm' || type === 'prompt'" 
          class="btn btn-cancel" 
          @click="handleCancel"
        >
          取消
        </button>
        <button 
          class="btn btn-primary" 
          @click="handleConfirm"
        >
          {{ confirmText }}
        </button>
      </div>
    </div>
  </div>
</template>

<script lang="ts" setup>
import { ref, onMounted, watch } from 'vue';

const props = defineProps({
  visible: {
    type: Boolean,
    default: false
  },
  type: {
    type: String,
    default: 'message',
    validator: (value: string) => ['message', 'confirm', 'prompt', 'notification'].includes(value)
  },
  title: {
    type: String,
    default: ''
  },
  message: {
    type: String,
    default: ''
  },
  placeholder: {
    type: String,
    default: ''
  },
  confirmText: {
    type: String,
    default: '确定'
  },
  variant: {
    type: String,
    default: 'success',
    validator: (value: string) => ['success', 'error', 'info'].includes(value)
  }
});

const emit = defineEmits(['close', 'confirm', 'cancel']);

const inputValue = ref('');
const inputRef = ref<HTMLInputElement | null>(null);
const isLeaving = ref(false);

const close = () => {
  if (props.type === 'notification') {
    // notification: 播完消退动画再关闭
    isLeaving.value = true;
    setTimeout(() => {
      isLeaving.value = false;
      emit('close');
    }, 300);
  } else {
    emit('close');
  }
};

const handleCancel = () => {
  emit('cancel');
  close();
};

const handleConfirm = () => {
  emit('confirm', inputValue.value);
  close();
};

const handleEnter = () => {
  if (props.type === 'prompt') {
    handleConfirm();
  }
};

watch(() => props.visible, (newVal) => {
  if (newVal && props.type === 'prompt') {
    inputValue.value = '';
    setTimeout(() => {
      inputRef.value?.focus();
    }, 100);
  } else if (newVal && props.type === 'notification') {
    // 自动关闭通知
    setTimeout(() => {
      close();
    }, 4000);
  }
});
</script>

<style scoped>
.modal-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background-color: rgba(0, 0, 0, 0.5);
  display: flex;
  justify-content: center;
  align-items: center;
  z-index: 1000;
}

.modal-content {
  background-color: white;
  border-radius: 8px;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
  width: 400px;
  max-width: 90%;
  animation: modalFadeIn 0.3s ease;
}

@keyframes modalFadeIn {
  from {
    opacity: 0;
    transform: translateY(-20px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

.modal-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 16px 20px;
  border-bottom: 1px solid #e8e8e8;
}

.modal-header h3 {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  color: #333;
}

.close-button {
  background: none;
  border: none;
  font-size: 20px;
  cursor: pointer;
  color: #999;
  padding: 0;
  width: 24px;
  height: 24px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 4px;
  transition: all 0.3s;
}

.close-button:hover {
  background-color: #f0f0f0;
  color: #666;
}

.modal-body {
  padding: 20px;
}

.modal-body p {
  margin: 0;
  color: #333;
  line-height: 1.5;
}

.input-field {
  width: 100%;
  padding: 8px 12px;
  border: 1px solid #d9d9d9;
  border-radius: 4px;
  font-size: 14px;
  transition: all 0.3s;
  box-sizing: border-box;
}

.input-field:focus {
  outline: none;
  border-color: #1890ff;
  box-shadow: 0 0 0 2px rgba(24, 144, 255, 0.2);
}

.modal-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding: 16px 20px;
  border-top: 1px solid #e8e8e8;
  background-color: #fafafa;
  border-radius: 0 0 8px 8px;
}

.btn {
  padding: 6px 16px;
  border: 1px solid #d9d9d9;
  border-radius: 4px;
  font-size: 14px;
  cursor: pointer;
  transition: all 0.3s;
}

.btn-cancel {
  background-color: white;
  color: #333;
}

.btn-cancel:hover {
  border-color: #1890ff;
  color: #1890ff;
}

.btn-primary {
  background-color: #1890ff;
  border-color: #1890ff;
  color: white;
}

.btn-primary:hover {
  background-color: #40a9ff;
  border-color: #40a9ff;
}

@media (max-width: 480px) {
  .modal-content {
    width: 95%;
  }
}

/* 通知样式 */
.notification-overlay {
  background-color: transparent;
  pointer-events: none;
}

.notification-content {
  width: 300px;
  max-width: 90%;
  position: fixed;
  top: 20px;
  right: 20px;
  background-color: rgba(255, 255, 255, 0.95);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
  animation: notificationSlideIn 0.3s ease;
  pointer-events: auto;
}

@keyframes notificationSlideIn {
  from {
    opacity: 0;
    transform: translateX(100%);
  }
  to {
    opacity: 1;
    transform: translateX(0);
  }
}

@keyframes notificationSlideOut {
  from {
    opacity: 1;
    transform: translateX(0);
  }
  to {
    opacity: 0;
    transform: translateX(100%);
  }
}

.notification-leaving .notification-content {
  animation: notificationSlideOut 0.3s ease forwards;
}

.notification-content .modal-body {
  padding: 16px;
  text-align: center;
}

.notification-success {
  border-left: 4px solid #4caf50;
}

.notification-error {
  border-left: 4px solid #e53935;
}

.notification-info {
  border-left: 4px solid #2196f3;
}
</style>