# DFM-frontend 说明

`DFM-frontend` 是 uTools 插件的主前端（Vue 3 + Vite），通过 HTTP 调用本地 `DFM-service`（基址来自 `public/config.json` 的 `apiBase`，默认 `http://localhost:3000`）。本文件记录整理后的目录结构、分层约定与构建注意事项；组件交互与接口清单见 [项目架构总览.md](项目架构总览.md)、[DFM-service-API文档_v2.md](DFM-service-API文档_v2.md)。

## 1. 目录结构

```
DFM-frontend/
├── public/
│   ├── plugin.json              # uTools 插件入口（单入口 code: DFM-Android-Toolkit）
│   ├── config.json              # apiBase + features 开关（未配置的开关默认开启）
│   └── preload/services.js      # CommonJS preload，注入 window.services
├── src/
│   ├── App.vue                  # 菜单注册表 MENU_ITEMS + currentPage 切页（无 vue-router / pinia）
│   ├── main.js / main.css       # 入口与全局样式（reset、布局、通用按钮/Tab、暗色变量）
│   ├── components/              # 跨页通用组件：ModalDialog / FileEditorDialog / FileImageViewer / SqliteDbViewer
│   ├── composables/             # 全局单例：useAppConfig / useFeatureOverrides / useProbeSettings
│   │   ├── jardiff/            #   jar 对比、jar 改码两个 tab 的逻辑
│   │   └── portTool/           #   端口工具共享上下文 + 4 个 tab 的逻辑
│   ├── menu/                    # 功能页（文件名与 App.vue 的 MENU_ITEMS 对应，勿随意改名）
│   │   ├── App_Utils.vue        #   ADB 工具：仅保留 tab 注册、provide('app-utils') 与模板
│   │   ├── App_PortTool.vue     #   端口工具：仅保留 tab 注册、共享上下文装配与模板
│   │   ├── App_JarDiff.vue      #   jar 工具：仅保留 tab 注册、共享配置与模板
│   │   ├── App_Production.vue / App_Settings.vue / App_McpService.vue
│   │   └── utils/               #   ADB 工具的 9 个 tab 组件：TabInfo/TabApps/TabFiles/TabKeys/
│   │                             #   TabBatch/TabScript/TabSerial/TabMonitor/TabProbe
│   └── styles/                  # 提取出的样式
│       ├── page-shell.css       #   通用返回按钮 / .app / .container / .content
│       ├── card.css             #   完整卡片：.card/.card-header/.card-icon/.card-body
│       ├── card-simple.css      #   仅 .card/.card-icon（McpService 原样式只含这两项）
│       ├── tabs.css             #   .tab-container/.tab-buttons/.tab-button
│       └── utils/               #   ADB 工具 9 个 tab 的 :deep() 覆写，按 tab 分文件
├── scripts/patch-utools-dist.mjs # 把 dist 的 ESM script 改为经典 defer 脚本（uTools 禁止 ESM）
└── upxs/                        # 插件安装包
```

## 2. 分层约定

### 页面与逻辑

- `src/App.vue` 用 `MENU_ITEMS` + `currentPage` 切换功能页；**不使用 vue-router**，新增页面需同时改菜单注册表与功能开关配置。
- 功能页（如 `App_PortTool.vue`）保留：tab 注册表、`activeTab`/`visibleTabs`、共享上下文装配、页面模板（含 `<ModalDialog>`）。具体 tab 的状态与请求逻辑放 `src/composables/<页面名>/useXxx.ts`，通过显式参数（如 `ctx: PortToolContext`、`apiBase: Ref<string>`）注入共享引用，不再把全部逻辑堆在页面 script 里。
- `App_Utils.vue` 是特例：9 个 tab 早在上一轮已拆为 `src/menu/utils/Tab*.vue`，页面通过 `provide('app-utils', {...})` 暴露约 100 个状态/方法供子组件 `inject`。**该 provide 的键集合是契约，增删键必须同步检查所有 Tab 组件。**
- `App_JarDiff.vue` 的两个 tab 逻辑分别抽到 `src/composables/jardiff/useJarCompare.ts`、`useJarCodeEdit.ts`；页面层通过解构 ref 保持模板绑定名不变。

### 提示 / 弹窗怎么选

统一通过 `src/components/ModalDialog.vue` 的 `type` prop 和页面辅助函数使用；**用户说“来个提示”默认指右上角通知，不是带确定按钮的模态框**：

| 用户说法 | 辅助函数 | `ModalDialog.type` | 表现 |
| --- | --- | --- | --- |
| 来个提示 / 通知一下 / 报错了（非阻塞） | `showNotification(msg, variant)` | `notification` | 右上角滑入，4 秒自动消失；`variant` 为 `success` / `error` / `info` |
| 弹个消息让用户点确定 | `showMessage(msg, title?)` | `message` | 居中模态框，只有确定按钮 |
| 确认一下 / 删除前问问 | `showConfirm(msg, callback, title?)` | `confirm` | 居中模态框，取消 + 确定 |
| 让用户输入 | `showPrompt(msg, callback, placeholder?, title?)` | `prompt` | 居中模态框，输入框 + 取消 + 确定 |
| 让用户从列表里选 | 直接设置 `modalType='list'` + `listItems` | `list` | 列表选择（前端版 ModalDialog 特有） |

- `App_Utils.vue` 内已实现上述全部辅助函数；`src/composables/portTool/usePortToolContext.ts` 目前只暴露 `showMessage` / `showConfirm`，如端口工具需要非阻塞提示，应补 `showNotification`。
- 长耗时且拿不到百分比的操作可用 `notification` + `progress` 显示不定进度条。

### 样式

- 全局样式只放 `src/main.css`（reset、body/按钮/Tab 基础样式、暗色变量）。
- 多个页面重复且**逐字一致**的样式提取到 `src/styles/*.css`，用 `<style scoped src="..."></style>` 引入。Vue SFC 会对外部 CSS 应用同样的 scoped 转换，选择器与级联顺序保持不变。
- ADB 工具原有 3401 行 `:deep()` 覆写按 tab 拆到 `src/styles/utils/utils-*.css`，`App_Utils.vue` 只保留 10 个 `<style scoped src>` 标签。
- ⚠️ **动画与 keyframes 必须位于同一条 `<style>` 标签内**：scoped postcss 只在同一块里给 `animation-name` 与 `@keyframes` 加 hash。`utils-script.css` 因此重复声明了 `capturePulse`（与 `utils-info.css` 相同），否则动画名不会带 hash、动画失效。
- 新增样式优先复用 `page-shell.css` / `card.css` / `tabs.css`；确实只属于单个 tab 的规则再放进对应 `utils-*.css`。

### UI 组件库与 Element Plus

- **Element Plus**：项目用的是 Vue 3 版 Element Plus（不是 Vue 2 的 Element UI）。`package.json` 声明 `element-plus: ^2.14.7`、`@element-plus/icons-vue: ^2.3.2`；`src/main.js` 全局 `app.use(ElementPlus)`，并引入 `element-plus/dist/index.css` 与 `theme-chalk/dark/css-vars.css`，同步 `html.dark` 跟随系统主题。实际使用集中在 `src/menu/utils/TabScript.vue`（`el-select` / `el-option` / `el-input` / `el-input-number` / `el-checkbox` / `el-switch` / `el-tooltip`）。桌面端副本 `DFM-desktop/frontend` 也带 Element Plus；`DFM-ui` 演示站没有该依赖。
- **提示不走 `ElMessage`**：代码里未使用 `ElMessage`，通知/弹窗统一走自有 `ModalDialog`（见上节「提示 / 弹窗怎么选」）。
- **两套组件目录**：`DFM-frontend/src/components/` 是页面实际使用的组件（`ModalDialog.vue`、`FileEditorDialog.vue`、`FileImageViewer.vue`、`SqliteDbViewer.vue`）；`DFM-ui/src/components/` 是独立演示站，含 `Button.vue`、`ModalDialog.vue`、`FileEditorDialog.vue` 及各自 README，组件名即 PascalCase 文件名、默认导出。
- **版本差异**：`DFM-ui` 的 `ModalDialog.vue` 是旧版，缺少前端版已有的 `type='list'`（列表选择）与 `notification` + `progress`（不定进度条）；`Button.vue` 只存在于 `DFM-ui`，前端页面仍用原生 `<button>` + 局部样式。本次重构未引入或替换组件，以保持视觉/CSS 等价；是否接入由后续约定决定。

## 3. 开发与构建

```bash
cd DFM-frontend
npm install
npm run dev       # Vite dev server，端口 5175（由用户自行启动/停止）
npm run build     # vite build && node scripts/patch-utools-dist.mjs
```

- `npm run build` 必须打印 `[patch-utools-dist] 1 个脚本已转为经典脚本` 才算成功。
- ⚠️ `scripts/patch-utools-dist.mjs` 会检查打包产物，出现 `import(`、`import.meta`、`export {` 即报错。因此**禁止使用 `defineAsyncComponent(() => import(...))` 或动态路由加载**，新拆分的模块一律静态 `import`。
- 构建产物位于 `dist/`，由打包脚本写入 `upxs/`；服务端静态托管使用的是 `DFM-service/data/dist`，同步方式见 [项目架构总览.md](项目架构总览.md)。

## 4. 整理记录（行为等价验证）

| 项目 | 整理前 | 整理后 |
| --- | --- | --- |
| `src/menu/App_Utils.vue` | 4824 行（含 3401 行样式） | 1432 行（10 个外部 scoped 样式） |
| `src/menu/App_PortTool.vue` | 2281 行（含 814 行样式、975 行脚本） | 1262 行（脚本拆为 5 个 composable） |
| `src/menu/App_JarDiff.vue` | 1401 行（497 行脚本 + 664 行样式） | 912 行（脚本拆为 2 个 composable） |
| `src/menu/App_McpService.vue` | 949 行 | 890 行（复用 shell/card-simple） |

验证方式：每次改动后执行 `npm run build`，并对构建前后的 `dist/assets/index-*.css` 做“忽略 scoped hash 与 8 位 hex 动画后缀”的规则多重集比对，结果一致（仅组件 hash 因源码变化而改变，属预期）。**未启动任何服务**；UI 视觉验收需由用户在自己的 dev server / uTools 环境中进行。

## 5. 已知事项

- `src/menu/utils/TabScript.vue` 仍是最大的单文件（约 3250 行，脚本仓库树逻辑集中在 1735 行之后），后续可继续按 composable 拆分。
- `src/styles/batch-remote.css` 当前无引用、类名与现有批量投屏 DOM 不匹配，属历史遗留，暂未删除。
- `src/menu/App_PortTool.vue` 首行有历史遗留字符 `2`（`2<script lang="ts" setup>`），不影响 SFC 解析与构建，后续可在确认无并发改动时清理。
- 桌面端 `DFM-desktop/frontend/` 是独立副本且已分叉，本目录的改动**不会自动同步**到桌面端。
- 配置双源：`public/config.json`（features 开关）与 `App_Settings.vue`（localStorage）并存，新增开关时需明确以哪一侧为准。
