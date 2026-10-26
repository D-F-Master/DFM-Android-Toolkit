# Button.vue — 按钮组件

## 概述

按 DFM 首页设计语言实现的通用按钮：薄荷绿主色令牌、玻璃质感的柔和投影、弹性缓动。

依赖设计令牌：使用前在入口引入 `src/styles/tokens.css`（提供 `--accent` 等变量）。

## Props

| Prop | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `variant` | `string` | `'primary'` | 变体：`primary` / `secondary` / `success` / `warning` / `danger` |
| `size` | `string` | `'md'` | 尺寸：`sm` / `md` / `lg` |
| `loading` | `boolean` | `false` | 加载中：显示转圈、自动禁用、阻止点击 |
| `disabled` | `boolean` | `false` | 禁用：去色（`grayscale`）+ `not-allowed`，沿用 `main.css` 约定 |

## 插槽

| 插槽 | 说明 |
|------|------|
| 默认插槽 | 按钮文字 |
| `icon` | 左侧图标（建议 `<svg fill="currentColor">`，尺寸 16–18px）；`loading` 时自动隐藏 |

## Events

`click` 等原生事件直接透传到内部 `<button>`，无需额外声明；按钮固定 `type="button"`，不会误触发表单提交。

## 使用示例

```vue
<script setup>
import Button from './components/Button.vue'
</script>

<template>
  <Button @click="onSave">主要按钮</Button>
  <Button variant="secondary" size="sm">次要小号</Button>
  <Button variant="danger" :loading="submitting">删除</Button>
  <Button disabled>禁用</Button>
  <Button>
    <template #icon><svg viewBox="0 0 20 20" width="16" height="16" fill="currentColor">…</svg></template>
    带图标
  </Button>
</template>
```

## 风格约定

| 项 | 取值 | 来源 |
|----|------|------|
| 主色 / 悬停 | `--accent` `#0d9668` / `--accent-hover` `#0a7d56` | 首页 `App.vue` |
| 圆角 | `md`/`lg` 12px，`sm` 8px | 首页 `.menu-button` 12px |
| 缓动 | `0.35s cubic-bezier(0.16, 1, 0.3, 1)` | 首页 `.menu-button` |
| 投影 | `0 4px 20px -4px var(--accent-glow)`，悬停加深 | 首页 `.menu-button` |
| 成功 / 警告 / 危险 | `#4caf50`→`#43a047`、`#ff9800`→`#f57c00`、`#e74c3c`→`#d32f2f` | 原项目既有按钮色值 |
| 禁用 | `filter: grayscale(1)` + `not-allowed` | `main.css` 全局 `button:disabled` |

## 已知限制

- 暗色模式：设计令牌目前只有亮色一套（首页未定义暗色变量），本组件未自行发明暗色配色；待补 `@media (prefers-color-scheme: dark)` 令牌后自动生效。
- 二次点击防抖、按钮组（ButtonGroup）等未实现，暂无使用场景。
