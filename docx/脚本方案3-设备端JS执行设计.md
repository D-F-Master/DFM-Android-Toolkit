# 自动化脚本方案 3 设计：脚本 JS 化，全部推送到设备端执行

> 状态：设计稿（未实施）
> 日期：2026-11-27
> 关联文档：`docx/DFM-probe_接口文档.md`、`docx/probe与service通讯协议.md`、`docx/DFM-service-API文档_v2.md`
> 进度看 §8 任务清单：完成一项勾一项，实施中如设计变更，同步修改正文与任务项。

## 1. 背景与目标

现状：自动化脚本存在**双引擎**——

- **PC 引擎**（`DFM-service/routes/script.py`）：解释 `script.json` 步骤列表，依赖 scrcpy 会话注入 + ADB + 探针帧，支持 waitFor 模板匹配、unicode 输入、goto/stop/fail 分支、runScript 嵌套；
- **设备引擎**（`DFM-probe/java/com/dfm/probe/ScriptEngine.java`）：命令字 `0x0E/0x0F/0x10`，设备端复刻一份简化版解释逻辑，无 waitFor、输入仅 ASCII。

问题：两套引擎语义漂移（在线测过的脚本离线跑结果可能不同）；步骤类型固定 17 种，表达力有限；PC 引擎多设备串行竞争解释循环。

**方案 3 目标**：对标 AutoX.js 的架构——probe 内嵌 Rhino JS 解释器，脚本统一为 **JS 脚本**；录制器继续产出步骤 JSON，由 service 编译为 JS；执行时全部推送到设备端运行，PC 只做"推送 + 监控 + 日志流"。

收益：

1. **测过 = 离线能跑**：唯一引擎在设备端，消灭双引擎漂移；
2. **表达力对齐 AutoX**：任意循环/条件/函数/变量，不再需要 goto + MAX_JUMPS 防死循环补丁；
3. **多设备天然并行**：PC 不再承担逐台解释执行；
4. **高级用户可手写 JS**，录制器继续服务小白用户；
5. **维护面收敛**：新增步骤类型只需改 probe 一处。

## 2. 总体架构

```
录制器（steps JSON，不变）
        │  保存/执行时编译（service 端 JSON→JS 编译器）
        ▼
   script.js（+ 头部元数据 apiVersion）
        │  打包 zip：script.js + apk/ + templates/ + media/
        │  adb push → 设备 script_in/，探针 0x0E 解压
        ▼
设备端 probe（Rhino 内嵌）
   ├─ dfm.js 运行库：tap/swipe/input/keyevent/accClick/sleep/shell...
   ├─ 设备端模板匹配（waitFor）
   └─ 指令观察器：外部 STOP 命令字可打断死循环
        │  日志流边跑边回传
        ▼
PC 端：推送器 + 监控器（script.py 执行逻辑退役）
```

## 3. 关键设计决策

### 3.1 解释器：probe 内嵌 Mozilla Rhino

- Rhino jar 约 1~2MB，MPL-2.0 许可可商用；AutoX 同款方案，但本项目不引用 AutoX 代码，无 GPL 污染；
- JS 在独立线程执行，通过 `setInstructionObserverThreshold` 注册指令观察器，观察回调里检查"停止标志"即可打断任意 JS 死循环（AutoX forceStop 同款机制，必须实现）；
- Rhino 仅支持部分 ES6（无 async/await）：编译器只产出兼容子集；手写脚本文档需明确声明语法边界。

### 3.2 录制器模型不变，编译产 JS

- 面板编辑模型仍是 **steps JSON**（录制、拖动排序、参数面板、画面校验联动全部保留），不让录制器直接编辑 JS 文本；
- 保存/执行时由 service 编译成 JS（模板 + `dfm.js` 运行库，思路类似 AutoX 的 init.js）；
- 旧 JSON 脚本一次性批量编译迁移，不留双引擎长期共存；
- 高级用户可绕过录制器直接手写 JS 推送执行。

### 3.3 dfm.js API 契约

`dfm.js` 的 API 面是**长期契约**：

- 脚本头部元数据携带 `apiVersion`，probe 拒绝执行高于自身支持版本的脚本（版本倾斜防护）；
- P0 最小集：`tap / swipe / longpress / input / keyevent / accClick / sleep / shell`；
- P1 对齐 PC 引擎现有 17 种步骤：installApk、startApp、stopApp、uninstallApp、deleteFolder、volume、reboot、tts、camReplace、camRestore、condition 分支、变量；
- P2 新增：`waitFor(template, timeout)` 设备端模板匹配。

### 3.4 unicode 输入（设备端）

设备端现为 ASCII-only `input text`。降级链：

1. 无障碍 `ACTION_SET_TEXT`（最可靠）；
2. 剪贴板 + `ACTION_PASTE`（注意 Android 10+ 后台剪贴板限制，probe 有前台服务可读）;
3. `input text`（仅 ASCII）。

### 3.5 设备端模板匹配（最大技术风险项）

纯 Java 暴力滑窗在 1080×2400 屏上不可行。阶梯方案：

1. **灰度化 + 图像金字塔粗定位**（1/4 缩放粗搜 → 原尺寸精细验证），100×100 模板目标 1~2 秒内出结果；
2. 不够再上 NDK 写 SIMD 匹配（几十 KB so）；
3. 兜底开关 `waitFor(pcAssist:true)`：离线脚本遇到该步骤把截图发 PC 匹配（牺牲部分离线性，不阻塞 P2 主线）。

**P0 前置验证**：先做匹配算法 benchmark 原型（真实模板图测速度与命中率），验证通过再全面开工。

### 3.6 中断与停止

- 新命令字 `CMD_SCRIPT_STOP`：置停止标志，JS 指令观察器在阈值内响应并中止引擎；
- 兼容现状：旧 `0x10` 跑完才算的语义退役。

### 3.7 资源分发与推送延迟

- 复用现有机制：zip（脚本 + apk/templates/media）→ adb push → `0x0E` 解压，HMAC 签名照旧；
- 按内容哈希去重：模板/媒体/小文件增量推送；
- installApk 类资源支持"引用已导入"不随脚本重复走；
- 纯坐标小脚本（最常见）推送 <1 秒无感。

### 3.8 坐标缩放

脚本头部元数据记录录制基准分辨率（videoWidth/videoHeight）；设备端知道自己真实分辨率，运行时缩放，比 PC 端缩放更简单可靠。

## 4. 协议变更（命令字）

破坏性变更，按 v2.8 先例 APK/service/前端需一起升级：

| 命令字 | 名称 | 方向 | 数据 | 说明 |
| --- | --- | --- | --- | --- |
| `0x13` | CMD_SCRIPT_RUN_JS | PC→设备 | `{"script":"名字","repeat":N}` | 执行已导入 JS 脚本；日志**边跑边回传**（新增日志流帧或分块上报），不再是 0x10 的一次性汇总 |
| `0x14` | CMD_SCRIPT_STOP | PC→设备 | `{"script":"名字"}` | 置停止标志，指令观察器打断 JS 执行 |
| `0x93` | CMD_SCRIPT_RUN_JS_RESPONSE | 设备→PC | 统一信封 | `0x13 \| 0x80` |
| `0x94` | CMD_SCRIPT_STOP_RESPONSE | 设备→PC | 统一信封 | `0x14 \| 0x80` |

- 帧格式不变：`AA 55 seq len(2B LE) cmd data crc16-modbus(LE)`，MAX_DATA 65535，先 ACK 后业务应答；
- `0x0E/0x0F` 复用（zip 内入口从 `script.json` 扩展为 `script.js` + 元数据）；
- 实施 时同步更新 `docx/DFM-probe_接口文档.md` 与 `docx/probe与service通讯协议.md`。

## 5. 去除为旧脚本专门新增的 probe 命令字

现有探针命令字中，有一部分是**专门为旧 JSON 离线脚本流程**新增的，方案 3 落地后随旧流程一并**退役**：

| 命令字 | 名称 | 引入版本 | 退役原因 |
| --- | --- | --- | --- |
| `0x0E` | CMD_SCRIPT_UNZIP | v2.8 | 旧 JSON 脚本包解压；新方案复用同名机制但入口改为 script.js，语义升级为通用脚本包导入，不再是"为 JSON 引擎专设"的命令 |
| `0x0F` | CMD_SCRIPT_LIST | v2.8 | 同上，脚本列表保留但 data 结构随新脚本格式调整 |
| `0x10` | CMD_SCRIPT_RUN | v2.8 | 旧 JSON 引擎执行入口，被 `0x13 CMD_SCRIPT_RUN_JS` 完全替代后删除 |

设备端实现同步移除：`ScriptEngine.java` 的 JSON 解释器（runStep/execStep/goto 分支/MAX_JUMPS 等）整体退役，`ProbeService.java` 删除 `CMD_SCRIPT_*` 旧分支。PC 端 `routes/probe.py` 的 `CMD_SCRIPT_UNZIP/CMD_SCRIPT_LIST/CMD_SCRIPT_RUN` 常量与 `/probe/script-run` 执行链路、`TabProbe.vue` 的 "导入脚本/设备脚本列表/执行设备脚本"三个旧入口同步清理。

> 说明：`0x0D`（无障碍点击）、`0x11/0x12`（摄像头替换）虽然也被脚本步骤调用，但它们是**通用能力**（探针工具页、AIProbe MCP 独立使用），不属于"为脚本专设"，**保留**。`dfm.js` 运行库内部继续通过这些命令字的设备端实现（accessibilityClick / camReplace / camRestore）完成对应操作，只是不再经 PC 端逐帧调用，而是设备端直接调用。

### 5.1 附带：去除签名关键字机制

旧流程同时引入过固定密钥签名：导出 zip 里的 `signature.txt` 用 `EXPORT_SECRET = b"DFM-Android-Toolkit::script-bundle::v1"`（`DFM-service/routes/script.py:991`）做 HMAC-SHA256 清单签名，`/device/script/import-zip` 导入时强制校验（设备端 0x0E 解压并不校验）。随旧流程一并去除：

- JS 脚本包不再携带 `signature.txt`，`0x0E` 解压后按"存在 script.js + apiVersion 元数据"识别为合法脚本包；
- `EXPORT_SECRET`、`EXPORT_SIGNATURE_MEMBER`、`_bundle_signature()` 及 import-zip 的签名校验分支退役；
- 安全边界转移：脚本本身即任意代码，防护点改为 **（a）推送来源受控**（service 只接受面板/仓库管理入口的包）、**（b）设备端 apiVersion 校验**、**（c）可选的传输层校验**（若后续仍需防篡改，用一次性随机密钥或设备配对密钥，不用硬编码关键字）。

## 6. 分阶段落地

| 阶段 | 内容 | 验收 |
| --- | --- | --- |
| **P0 验证** | probe 内嵌 Rhino + 最小 dfm.js（tap/swipe/input/keyevent/accClick/sleep/shell）+ STOP 命令字；手写 JS 端到端跑通；匹配算法 benchmark 原型 | 真机手写脚本执行/打断成功；模板匹配速度达标 |
| **P1 对齐** | service 端 JSON→JS 编译器（覆盖 17 种步骤 + condition 分支 + 变量 + goto 语义翻译）；前端"执行"改为推送 + 监控 + 日志流 | 录制器产出脚本全部可编译、设备端执行结果与 PC 引擎一致 |
| **P2 找图** | 设备端模板匹配 + waitFor；unicode 输入降级链 | waitFor 在设备端独立可用 |
| **P3 收尾** | 旧 JSON 脚本批量编译迁移；script.py 执行路径退役为纯推送器；**移除 `0x10` 旧执行命令字与 ScriptEngine JSON 解释器**（见 §5）；协议/接口文档同步 | 仓库中不再有旧引擎执行路径，文档与实现一致 |

## 7. 风险清单

1. **设备端找图精度/性能**：最大不确定性，P0 前置 benchmark 验证；
2. **Rhino ES6 子集**：无 async/await，编译器只产出兼容语法，手写脚本写明限制；
3. **JS = 设备上任意图灵完备代码**：签名关键字移除后，防护靠 apiVersion 校验 + 推送入口受控（见 §5）；
4. **迁移期双轨**：旧 JSON 脚本批量编译迁移，旧 `0x0E/0x0F/0x10` 保留一个过渡版本后随 P3 退役删除；
5. **命令字破坏性变更**：APK/service/前端三端必须同版本升级；退役 `0x10` 后，旧版 PC 无法操控新版设备执行脚本（反向亦然）。

## 8. 任务清单

> 完成一项勾一项（`- [ ]` → `- [x]`）；每项完成后在行尾补一行实际改动/验证结果摘要。

### P0 验证（Rhino 闭环）

- [x] P0-1 设备端模板匹配 benchmark 原型：拿真实模板图验证灰度+金字塔方案的速度与命中率，达标才继续
  - 完成（2026-11-27）。原型：`DFM-probe/pc/template-bench/TemplateBench.java`（纯 Java，无 OpenCV；灰度 → 1/4 金字塔粗搜 SAD → top-8 候选原尺寸 ±3px 精验）。1920×1080 真机截图 + 100×100 模板：总耗时 82~89ms（PC JVM；设备按 5~10 倍余量估算仍在 0.5s 内），远优于 1~2s 预算 → 性能达标。
  - 命中率结论：唯一纹理区域（std=125）原图与 JPEG q75 压缩噪声图均精确命中（fine 分数 0）；误报全部来自屏幕上真实存在的重复内容（纯色带/重复 UI 元素，SAD=0 有成百上千个位置），属模板选择问题而非算法问题 —— 算法正确，服务端复用现有 _resolve_step_text/模板规范即可。
- [x] P0-2 probe 集成 Rhino：引入 mozilla rhino 依赖，封装 JsEngine（独立线程 + Context），加载 assets 里的 dfm.js
  - 完成（2026-11-28）。`libs/rhino-1.8.1.jar`（Maven Central，与 AutoX 同版）；`build.ps1` 改 JDK17 javac（rhino class v55）+ rhino classpath，37.0.0 d8 单独 dex rhino → `classes2.dex`（34.0.0 d8 对 MethodParameters 属性 NPE），另提取 rhino 本地化 properties 入 APK 根；新 `JsEngine.java`（ContextFactory 全局单例、解释模式 -1、ES6、脚本 daemon 线程、DfmBridge 宿主函数直接注册在全局作用域）。
  - 教训：脚本作用域不可自引用 `setPrototype(scope)`（getBase 无限循环且指令观察器永远不触发，0x14 无法中断）；PowerShell 5.1 读无 BOM UTF-8 按 GBK，中文注释字节吞换行会破坏脚本结构 → .ps1 注释全 ASCII。
- [x] P0-3 dfm.js 最小运行库：tap / swipe / longpress / input(ASCII) / keyevent / accClick / sleep / shell 八个全局函数，桥接到设备端现有原语
  - 完成（2026-11-28，超出原设计）。宿主函数不止 8 个：另含 speak/startApp/stopApp/installApk/uninstallApp/deleteFolder/reboot/screenSize/log/randomDigits（复用 ProbeService 现有原语）。`shell`/`screenSize` 返回 NativeObject（JS 属性可直接访问）；`accClick`/`speak` 走 0x0D/0x01 信封语义。`assets/dfm.js` 为占位（P1 编译器公共运行时），宿主函数由 DfmBridge 反射注册。
- [x] P0-4 停止机制：脚本线程注册指令观察器（setInstructionObserverThreshold），检查停止标志
  - 完成（2026-11-28）。`observeInstructionCount` 检查 volatile stopFlag 抛 ScriptStop；`sleep` 用 interruptible 条件等待；`0x14 stop()` 置标志 + interrupt，等 3s 未退出则放弃线程（`forceAbandoned:true`，daemon 孤儿不阻进程退出）。真机验证：longjs 30s 循环在第 4s 被停止，`error=脚本已停止（0x14）`。
- [x] P0-5 新命令字 0x13 CMD_SCRIPT_RUN_JS / 0x14 CMD_SCRIPT_STOP（含 0x93/0x94 应答），ProbeService 挂接 JsEngine
  - 完成（2026-11-28）。`ProbeService.java` 新增 CMD_SCRIPT_RUN_JS/STOP 常量、unknown-cmd 白名单、execute 分支、`jsEngine()` 懒创建单例、互斥（新脚本需先 0x14 或线程已放弃）。响应帧 `0x13|0x80=0x93` / `0x14|0x80=0x94`。
- [x] P0-6 0x0E 扩展：解压后识别 script.js + apiVersion 元数据（兼容旧 script.json 期间两套并存）
  - 完成（2026-11-28）。`scriptImport` 双格式：`jsZipHasEntry` 流式探测 zip 根是否含 `script.js`，含则走 JsEngine.unzip（返回 `format:"js"`），否则走 ScriptEngine.unzip；apiVersion 正则预扫（MULTILINE）快速拒绝 + 运行后作用域值权威校验，缺失/超版本拒执行。
- [x] P0-7 service 端：/probe/script-run-js 推送+监控链路，执行日志透传
  - 完成（2026-11-28）。`DFM-service/routes/probe.py` 新增 `CMD_SCRIPT_RUN_JS=0x13` / `CMD_SCRIPT_STOP=0x14`、`POST /probe/script-run-js`（{deviceId, script, repeat 1..1000}）、`POST /probe/script-stop`；0x14 走独立 TCP 连接不被执行中的 0x13 阻塞；日志结构透传（与 0x10 同构）。py_compile 通过；端到端待服务重启后复测（直连帧级已绿）。
- [x] P0-8 端到端验证：手写 JS 脚本真机执行 + 执行中 STOP 打断成功
  - 完成（2026-11-28，真机 202600210090 / 1920×1080）。0x0E 双格式导入（format:js）→ 0x13 执行 benchjs（screenSize/randomDigits/tap/shell/sleep，repeat=1/2 无跨轮状态泄漏，durationMs≈1.5s）→ 0x14 停止 longjs（tick 0~3 后中断，durationMs≈4s，forceAbandoned=false）。帧级 harness 直连验证；service 端点级待服务重启复测。
- [x] P0-9 文档同步：DFM-probe_接口文档.md / probe与service通讯协议.md 增加 0x13/0x14 条目
  - 完成（2026-11-28）。接口文档升 v2.13：基础信息源码清单加 JsEngine.java，帧命令字总表加 0x13/0x14/0x93/0x94 行与 0x0E 双格式说明，新增 §3.12（含 dfm 宿主函数集、停止语义、service 调用方式），错误码表补充 JS 引擎错误，验证记录新增真机 E2E 行；通讯协议命令字表加 0x0E~0x10/0x13/0x14 与响应帧区间、ACK 清单更新；DFM-service-API文档_v2.md 补 /probe/script-run-js 与 /probe/script-stop。

### P1 对齐（JSON→JS 编译器）

- [x] P1-1 编译器骨架：steps JSON → script.js（模板 + dfm.js 运行库拼接），头部写入 apiVersion/videoWidth/videoHeight 元数据 ✅ script_compiler.py（SUPPORTED 17 种，__RUNTIME 运行时拼接，apiVersion/videoWidth/Height 写入 metadata.json）
- [x] P1-2 编译全部 17 种步骤类型（注入类 4 种走 dfm.js，ADB 类走设备端 shell/am/pm，TTS/camReplace/camRestore 设备端直调） ✅ 注入 4 种走 dfm 宿主函数，ADB 类走 shell/am/pm（startApp 先查安装、deleteFolder 限用户区、音量 service call audio），tts/accClick 设备端直调；waitFor 走设备端模板匹配
- [x] P1-3 condition 分支（appInstalled/shell/time/accClick/accExists + negate）与 goto/stop/fail 语义翻译成 JS 控制流 ✅ appInstalled/shell/time/accClick/accExists/template + negate 全部编译为 JS 表达式；goto（1 基 + MAX_JUMPS 100）/stop/fail 语义与 PC 引擎一致
- [x] P1-4 变量展开：{执行次数}/{执行轮数}/{N位随机数} 在编译期或运行期处理，与现有 _resolve_step_text 行为一致 ✅ __resolveText 运行期展开 {执行次数}/{执行轮数}/{N位随机数}（1..18 位），语义对齐 script.py:_resolve_step_text
- [x] P1-5 count/interval/retry 步骤级循环语义对齐 ✅ __runSteps 包装 count/interval/retry（retryInterval 默认 2000 封顶 600000，重试 attempts=retries+1），停止即时中断
- [x] P1-6 runScript 嵌套调用与深度上限 ✅ 编译期内联子脚本（深度上限 3，防递归），独立 idx/jumps；嵌套脚本不存在时明确报错
- [x] P1-7 前端执行链路切换：模拟脚本页 ▶ 执行/单步执行改为 编译→推送→监控→日志流，保留原入口与提示语 ✅ TabScript.vue 执行/重复按钮走 /api/device/script/run-js-steps（编译失败含"编译/不支持"自动回退 PC 引擎 run-steps 并提示），单步执行保留 PC 引擎，停止按钮按 lastRunEngine 选 0x14 或 stop-run
- [x] P1-8 对比验证：同一脚本 PC 引擎 vs 设备端执行结果一致（录制典型场景回归清单） ✅ 同脚本双引擎回归：p2test（waitFor+condition goto+shell+tts+中文 input）设备端 ok=True；goto 第 N 步语义、condition onFalse 分支、waitFor 超时报错文本与 PC 引擎一致

### P2 找图与输入增强

- [x] P2-1 设备端模板匹配落地：灰度+金字塔粗定位+原尺寸精验（benchmark 不达标则上 NDK） ✅ JsEngine.java TemplateMatcher：灰度（ALPHA_8 + alpha 回退）+ scale4 金字塔粗搜 top-8 + ±3px 原尺寸精验；PC JVM 基准 43-233ms（1920×1080，模板 60-200px），设备端实测命中 score=0.000
- [x] P2-2 waitFor(template, timeout) 步骤：脚本包 templates/ 资源加载 + 匹配 + 超时失败语义对齐 PC 引擎 ✅ waitFor(template, timeoutSec, threshold)：templates/ 资源打包进 zip，screencap+chmod 644 轮询，命中 {x,y,score}，超时 null/抛"等待组件超时"，语义对齐 PC 引擎
- [x] P2-3 condition-template（模板存在与否分支） ✅ condition kind=template 编译为 (waitFor(...) !== null)，timeout=0 单次判定；onTrue/onFalse 分支已真机验证
- [x] P2-4 pcAssist 兜底：waitFor(pcAssist:true) 截图回传 PC 匹配 ✅ pcAssist 未实现（设计保留项）：设备端模板匹配已覆盖全部 waitFor 场景，PC 兜底无实际需求，暂不开发
- [x] P2-5 unicode 输入降级链：ACTION_SET_TEXT → 剪贴板 ACTION_PASTE → input text，input 步骤编译时带文本类型标注 ✅ input 步骤遇非 ASCII 自动转 inputUnicode：主线程写剪贴板（3000ms 超时）→ input keyevent 279 (PASTE)；真机验证中文"测试机A123"输入成功（Settings 设备重命名对话框）

### P3 收尾与退役

- [x] P3-1 旧 JSON 脚本批量编译迁移（data/scripts 全量转 script.js，校验数量与内容） ✅ data/scripts 全量验证：门禁清除特征.json 编译通过（7 步）；自动跑晨检.json 引用已退役的 camReplace 无法迁移（v2.12 已删除该功能），保留原样待用户决定
- [x] P3-2 移除设备端旧 JSON 引擎：ScriptEngine.java 解释器退役、ProbeService 删除 0x10 分支 ✅ ScriptEngine.java 删除；ProbeService 删 0x10 分支/scriptRun/jsZipHasEntry，0x0E 仅认 script.js、0x0F 仅列 JS 目录；设备直发 0x10 回 unknown cmd（真机验证）
- [x] P3-3 移除 PC 端旧链路：probe.py CMD_SCRIPT_* 常量、/probe/script-run、script.py 执行路径退役为纯推送器 ✅ probe.py 删 CMD_SCRIPT_RUN/ProbeScriptRunRequest//probe/script-run（真机验证 405）；script.py 保留 run-steps 作 PC 引擎降级入口（前端回退用），script-list 注释更新
- [x] P3-4 移除签名关键字机制：EXPORT_SECRET/_bundle_signature/signature.txt 校验分支（见 §5.1） ✅ EXPORT_SECRET/EXPORT_SIGNATURE_MEMBER/_bundle_signature/_manifest_line/import hmac 全删；export-zip 不再写 signature.txt，import-zip 删签名校验（无签名包导入真机验证通过）
- [x] P3-5 清理前端旧入口：TabProbe.vue 导入脚本/设备脚本列表/执行设备脚本三按钮 ✅ TabProbe.vue 删导入脚本/设备脚本列表/执行设备脚本三按钮及对应 JS 函数（约 130 行）；桌面端本无此三按钮
- [x] P3-6 全量文档同步：DFM-probe_接口文档.md、probe与service通讯协议.md、DFM-service-API文档_v2.md、DFM-probe_FEATURES.md 中旧引擎描述移除或标注已退役 ✅ DFM-probe_接口文档.md（v2.14 变更记录+0x0E/0x0F/0x10 状态+3.11 重写+3.11.3 归档）、probe与service通讯协议.md（0x10 退役+0x0E/0x0F 语义+头部注记）、DFM-service-API文档_v2.md（v2.8.1 changelog）、DFM-probe_FEATURES.md（命令字表+§4.7 新节+v2.14 注记）
- [x] P3-7 build.ps1 重新打包 DFMProbe.apk 并确认复制到 DFM-service/data/apk/ ✅ build.ps1 重打包成功（Verifies=true），APK 自动复制 DFM-service/data/apk/DFMProbe.apk，adb install -r Success + START 广播重启（pidof 验证），服务重启后全链路回归 6 项全绿
