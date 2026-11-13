# ModalDialog.vue — 通用对话框组件

## 概述

一个多功能模态对话框组件，支持消息提示、确认框、输入框和顶部通知四种模式。

## Props

| Prop | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `visible` | `boolean` | `false` | 控制对话框显示/隐藏 |
| `type` | `string` | `'message'` | 对话框类型：`message` / `confirm` / `prompt` / `notification` |
| `title` | `string` | `''` | 对话框标题（notification 类型不显示） |
| `message` | `string` | `''` | 正文内容（notification 类型也使用此字段） |
| `placeholder` | `string` | `''` | prompt 模式下输入框的占位提示文字 |
| `confirmText` | `string` | `'确定'` | 确认按钮的文字 |
| `variant` | `string` | `'success'` | notification 类型专用：`success` / `error` / `info`，控制左侧边框颜色 |

### 各类型说明

| type | 表现 |
|------|------|
| `message` | 显示标题 + 消息正文 + 一个确定按钮 |
| `confirm` | 显示标题 + 消息正文 + 取消 / 确定两个按钮 |
| `prompt` | 显示标题 + 文本输入框 + 取消 / 确定两个按钮 |
| `notification` | 右上角滑入通知，4秒自动关闭，无标题和按钮，可点击遮罩关闭。通过 `variant` 区分成功（绿）/ 错误（红）/ 提示（蓝） |

## Events

| 事件 | 参数 | 触发时机 |
|------|------|----------|
| `close` | — | 关闭对话框（点击遮罩层、取消、确定 均会触发） |
| `confirm` | `value: string` | 点击确定按钮；prompt 模式下 `value` 为输入框内容 |
| `cancel` | — | 点击取消按钮时触发 |

## 使用示例

```vue
<script setup>
import { ref } from 'vue'
import ModalDialog from './components/ModalDialog.vue'

const showMessage = ref(false)
const showConfirm = ref(false)
const showPrompt = ref(false)
const showNotification = ref(false)
</script>

<template>
  <!-- ========== 消息提示 ========== -->
  <button @click="showMessage = true">消息提示</button>
  <ModalDialog
    v-model:visible="showMessage"
    type="message"
    title="提示"
    message="操作已成功完成"
  />

  <!-- ========== 确认框 ========== -->
  <button @click="showConfirm = true">确认删除</button>
  <ModalDialog
    v-model:visible="showConfirm"
    type="confirm"
    title="确认删除"
    message="确定要删除这个文件吗？此操作不可撤销。"
    confirm-text="删除"
    @confirm="onDeleteConfirmed"
    @cancel="onDeleteCancelled"
  />

  <!-- ========== 输入框 ========== -->
  <button @click="showPrompt = true">新建文件夹</button>
  <ModalDialog
    v-model:visible="showPrompt"
    type="prompt"
    title="新建文件夹"
    message="请输入文件夹名称："
    placeholder="例如: MyFolder"
    confirm-text="创建"
    @confirm="(name) => createFolder(name)"
  />

  <!-- ========== 右上角通知 ========== -->
  <button @click="showNotification = true">成功通知</button>
  <ModalDialog
    v-model:visible="showNotification"
    type="notification"
    message="文件已成功导入"
  />

  <!-- ========== 带变体的通知 ========== -->
  <button @click="showErrorNotice = true">错误通知</button>
  <ModalDialog
    v-model:visible="showErrorNotice"
    type="notification"
    message="连接失败，请检查网络"
    variant="error"
  />
</template>
```

## 项目中实际调用方式

项目中通常不直接在模板里绑定 `v-model:visible`，而是通过 `showMessage` / `showConfirm` / `showPrompt` 等辅助函数动态控制：

```ts
// App_Utils.vue 中的使用模式

// 消息提示
const showMessage = (msg: string) => {
  modalType.value = 'message'
  modalTitle.value = '提示'
  modalMessage.value = msg
  modalVisible.value = true
}

// 确认框
const showConfirm = (msg: string, callback: Function) => {
  modalType.value = 'confirm'
  modalTitle.value = '确认'
  modalMessage.value = msg
  modalCallback.value = callback
  modalVisible.value = true
}

// 输入框
const showPrompt = (msg: string, callback: Function, title?: string) => {
  modalType.value = 'prompt'
  modalTitle.value = title || '输入'
  modalMessage.value = msg
  modalCallback.value = callback
  modalPlaceholder.value = ''
  modalVisible.value = true
}

// 右上角通知（success / error / info）
const showNotification = (message: string, variant: 'success' | 'error' | 'info' = 'success') => {
  modalType.value = 'notification'
  modalVariant.value = variant
  modalMessage.value = message
  modalVisible.value = true
}
```

```vue
<!-- v-model 绑定 + @confirm/@cancel 事件处理 -->
<ModalDialog
  v-model:visible="modalVisible"
  :type="modalType"
  :title="modalTitle"
  :message="modalMessage"
  :placeholder="modalPlaceholder"
  :confirm-text="modalConfirmText"
  :variant="modalVariant"
  @confirm="(val) => modalCallback?.(val)"
  @cancel="modalCallback = null"
/>
```

## 特性

- 点击遮罩层（半透明背景）自动关闭
- prompt 模式下输入框自动获得焦点，支持 Enter 键提交
- notification 模式 4 秒后自动关闭，带右侧滑入/滑出动画
- notification 模式支持 `variant` 区分成功（绿左框）/ 错误（红左框）/ 提示（蓝左框）
- 响应式布局，移动端适配
