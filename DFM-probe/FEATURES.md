# DFM Probe 功能说明

本文只回答一个问题：**这套程序支持哪些功能**。

构建步骤与实机验证记录见 [README.md](README.md)，帧格式、命令字、CRC 与 ACK 的字节级定义见仓库根目录
[`probe与service通讯协议.md`](../probe与service通讯协议.md)。

**定位**：设备端 agent 的最小验证工程。`adb` 只负责安装 APK、发广播启动服务、（USB 时）做端口映射，
**不承载任何业务数据**；PC 作为 TCP 客户端主动连入设备 `21104` 端口，用二进制帧协议收发业务数据。
`DFM-probe/` 是独立工程，不依赖 `DFM-service` 运行；页面探针由后端路由调用本目录的 APK。
当前只验证「通讯链路 + 系统 TTS 朗读」这条路线是否走得通。

---

## 1. 程序组成

| 组件 | 文件 | 提供的能力 |
| --- | --- | --- |
| 组件声明 | `AndroidManifest.xml` | 权限、`START` 广播接收器、前台服务、TTS 包可见性声明 |
| 启动入口 | `java/com/dfm/probe/StartReceiver.java` | 接收 `com.dfm.probe.START` 广播并拉起服务 |
| 设备端主体 | `java/com/dfm/probe/ProbeService.java` | 前台常驻、TCP 监听、帧协议收发、2 个业务命令、系统 TTS |
| 资源 | `res/` | 应用名 `DFM Probe`、通知栏小图标 |
| 构建脚本 | `build.ps1` | 不用 Gradle，直接调 SDK build-tools 打包并签名 |
| 签名 | `debug.keystore` | 固定调试签名，保证重复构建可覆盖安装 |
| 产物 | `DFMProbe.apk` | 已签名，可直接 `adb install -r` |
| PC 联调端 | `pc/probe_test.py` | 自动装/启/连接，按帧协议跑完整功能测试 |
| 构建中间产物 | `dist/` | aapt2 / javac / d8 等临时文件（被忽略，不入库） |

---

## 2. 设备端 APK：启动与常驻

- **无桌面图标、无 Activity**，靠显式广播 `com.dfm.probe.START`（`-n com.dfm.probe/.StartReceiver` + `--include-stopped-packages`）启动；
  这样能拉起刚安装、仍处于 stopped 状态的应用，规避 Android 11 安装后无法直接起前台服务的限制。
- Android 8+ 走 `startForegroundService`，更低版本走 `startService`。
- **前台服务 + 常驻通知**：标题 `DFM Probe`、内容「监听 21104 端口」、`setOngoing(true)`；
  Android 8+ 自动创建低重要性通知渠道 `dfm_probe`。
- START 广播**可重复、幂等**：`running` 标志保证只有尚未监听时才起监听线程，不会重复 bind 端口。
- 服务返回 `START_NOT_STICKY`；`onDestroy` 会关闭 `ServerSocket` 并释放 TTS 引擎。
- 清单：`minSdk 23` / `targetSdk 30`，权限 `INTERNET` + `FOREGROUND_SERVICE`，`allowBackup=false`，
  服务 `exported=false`，接收器 `exported=true` 且只过滤 `com.dfm.probe.START`。

## 3. 设备端 APK：网络服务

- 监听 **`0.0.0.0:21104`**（`SO_REUSEADDR`），同时支持两条接入路径：

  | 路径 | PC 侧做法 | 说明 |
  | --- | --- | --- |
  | USB 设备 | `adb forward tcp:<随机本地端口> tcp:21104` | 端口由系统分配，避免多设备互抢；用完 `--remove` |
  | 网络设备 | 直连 `<设备IP>:21104` | 不需要 forward |
- **每连接一个独立工作线程**，`accept` 循环不受长耗时业务（TTS 最长约 90 秒）阻塞；
  连接内部串行处理（窗口 1）。
- 设备端**不再有关闭连接的命令字**，连接由 PC 侧主动关闭，`ServerSocket` 继续 accept。
- 端口被占用导致 bind 失败时记日志 `listen failed on port 21104` 并把 `running` 置 false；当前不做端口自适应。

## 4. 设备端 APK：帧协议能力

帧格式：`AA 55 | 帧序(1B) | 长度(2B，低字节在前) | 命令字(1B) | 数据区 | CRC16(低字节在前)`，
CRC16-Modbus 覆盖 `帧序 + 长度(2B) + 命令字 + 数据区`。

- **组帧与解帧**：CRC 计算/校验、65535 字节长度上限校验（长度 2 字节、低字节在前）。
- **增量解帧**：支持粘包、半包、帧头重同步；无完整帧头时只保留末尾可能是半个帧头的 `0xAA`。
- **错误帧处理**：CRC 错的帧直接丢弃、不回 ACK（由发送端超时重传）；数据区必须是 UTF-8 **JSON 对象**，
  解析失败同样丢弃且不回 ACK。
- **单帧数据区 ≤ 65535 字节**；设备回的响应若超过 65535 字节，会改写为
  `{"code":500,"message":"response too large: N","data":null}`，不会发出非法长度帧。
- **可靠投递**：帧序 0~255 循环；每个连接记住**最近一次已受理业务的帧序**，同帧序重传帧只回 ACK、
  不重复执行业务（尤其避免 TTS 朗读两遍）。
- **先 ACK 后业务**：CRC/结构/JSON 校验通过立即回 ACK（`0x06`，同帧序、空数据区），再执行业务，
  最后发**同帧序响应帧**（命令字 = `请求命令字 | 0x80`）；这样 200ms 的 ACK 停等不会被 TTS 的数十秒耗时拖垮。
  ACK 与响应帧都是通知类，**不要求再回 ACK**，业务结果 JSON 统一采用对齐 service 的
  `{code,message,data}` 信封：成功 `code:200` / `data` 为业务对象，失败 `code:400`/`500` / `data` 固定 `null`。
  请求与返回参数规范见 `接口文档.md`「三」。

### 4.1 业务命令字清单

| 命令字 | 名称 | PC→设备数据区 | 设备应答 | 功能 |
| --- | --- | --- | --- | --- |
| `0x00` | CMD_HEARTBEAT | `{}`（无必填字段） | `{"code":200,"message":"success","data":{"version":"1.0"}}` | 心跳，返回 `AndroidManifest.xml` 的 `versionName` |
| `0x01` | CMD_TTS | `{"text":"..."}` | `{"code":200,"message":"success","data":{"status":"done"}}` 或 `{"code":400/500,"message":"...","data":null}` | 调用系统 TTS 朗读文本，见 §4.2 |
| 其它任意值 | 未知命令 | 任意 JSON 对象 | `{"code":400,"message":"unknown cmd: 0x..","data":null}`（命令字 `0xFF`） | 明确报错，连接不断开 |

> 旧版 `0x01`~`0x05`、`0x07`（PING / ECHO / TTS / INFO / QUIT / DEVICE_INFO）业务命令字已全部删除，
> 本版为破坏性不兼容变更，PC 侧脚本、service 路由与前端页面须一起升级。

设备→PC 的通知类命令字：`0x06` ACK、`0x80` / `0x81` 数据响应帧、`0xFF` 错误帧。
业务执行抛出未预期异常时回 `{"code":500,"message":"execute failed: ...","data":null}`，不会崩服务。

### 4.2 系统 TTS 朗读

- 使用 Android `TextToSpeech` 系统引擎，`QUEUE_FLUSH` 模式、`utteranceId = dfm-probe`，
  通过 `UtteranceProgressListener` 的 `onDone` / `onError` 判定朗读结束。
- **懒初始化**：首次 TTS 请求才创建引擎并同步等待 `onInit`，最长 30 秒（讯飞引擎冷启动实测约 13 秒）；
  初始化失败会 `shutdown`，下次请求重试。
- **朗读等待最长 60 秒**，读完才回 `{"code":200,"message":"success","data":{"status":"done"}}`。
- 文本先 `trim`，支持 UTF-8 中文；失败一律回 `code:400`（参数错误）或 `code:500`（引擎错误）
  且 `data` 固定 `null`、**保持连接不断开**：

  | code | message | 触发条件 |
  | --- | --- | --- |
  | `400` | `text must be a string` | `text` 缺失或非字符串 |
  | `400` | `empty text` | 空文本 / 全空白 |
  | `500` | `tts engine unavailable` | 引擎创建或 30 秒初始化失败 |
  | `500` | `tts speak rejected: N` | `speak()` 返回值非 SUCCESS |
  | `500` | `tts timeout` | 60 秒内未收到 onDone/onError |
  | `500` | `tts engine reported error` | 引擎回调 onError |
- Android 11（targetSdk 30）在清单 `<queries>` 中声明 `android.intent.action.TTS_SERVICE`，
  否则 bind 不上引擎、`onInit` 立即回 ERROR；前提是设备装有可用的 TTS 引擎（本机为 `com.iflytek.speechcloud`）。

---

## 5. PC 端联调脚本 `pc/probe_test.py`

命令行参数：

| 参数 | 默认值 | 作用 |
| --- | --- | --- |
| `--device` | `192.168.0.59:5555` | 含 `:` 视为网络设备（直连该 IP 的 21104），否则视为 USB 设备（自动 forward） |
| `--apk` | 工程根目录 `DFMProbe.apk` | 指定要安装的 APK |
| `--timeout` | `130.0` | 响应帧等待秒数，需覆盖 TTS 初始化 30s + 朗读 60s |
| `--skip-install` | 关 | 跳过 `adb install -r` |
| `--tts-text` | `设备语音测试` | 自定义朗读文本 |

六步流程：检查设备在线（`adb get-state`）→ `adb install -r` → 发 START 广播 →
建立 TCP 连接（USB 用 `bind(127.0.0.1, 0)` 取随机本地端口做 forward，结束时 `--remove`；
网络直接连 `<IP>:21104`）→ 心跳 / TTS / 边界命令字测试 → 关闭连接。
START 广播是异步的，脚本用 10 次 × 0.3 秒的连接重试等待服务 bind 完成。

实现的协议能力：CRC16-Modbus 组帧/增量解帧（粘包、半包、CRC 错帧重新找帧头）、
**停等 ACK 重传**（T=200ms、最多共发 4 次、窗口 1）、帧序 0~255 循环、同帧序响应帧等待、
组帧前 65535 字节拒绝。

覆盖的测试项：组帧 65536 字节拒绝、CMD_HEARTBEAT / CMD_TTS 成功应答、
TTS 缺 text / 非字符串 text / 空文本 / 未知命令、CRC 错帧丢弃且无应答。
成功返回退出码 `0`，失败返回 `1` 并打印 `FAIL: ...`；测试结束**不 force-stop**，服务继续监听。

---

## 6. 构建脚本 `build.ps1`

- **不依赖 Gradle**，直接调用 Android SDK build-tools；`powershell -ExecutionPolicy Bypass -File build.ps1`。
- 依赖：`ANDROID_HOME`（本机 `E:\android-sdk-windows`）、build-tools 34.0.0、platforms/android-34、
  JDK 8 的 `javac`、JDK 17 的 `d8` / `apksigner` / `keytool`；启动前逐项检查路径，缺失立即报错。
- 参数 `-Sdk`（覆盖 SDK 路径）、`-OutApk`（覆盖产物路径）。
- 流水线：`aapt2 compile/link`（minSdk 23 / targetSdk 30）→ `javac`（source/target 1.8）→
  `d8`（min-api 23）→ 合并 `classes.dex` → `zipalign` → `apksigner sign` → `apksigner verify --verbose`。
- `debug.keystore` 固定在工程根目录且**存在即复用**，保证重复构建签名不变、可覆盖安装（勿删）。
- 产物 `DFMProbe.apk` 已签名，可直接 `adb install -r`。

---

## 7. 上层页面探针（关联工程，代码不在本仓库）

本工程只产出 APK 与协议；`DFM-service` / `DFM-frontend` 基于它提供页面化探针能力：

- 后端 `DFM-service/routes/probe.py` 三个端点，统一返回 `{"code":200,"message":...,"data":{"connected":true|false,...}}`：

  | 端点 | 请求体 | 行为 |
  | --- | --- | --- |
  | `POST /api/probe/check` | `{"deviceId":"..."}` | 只发一帧 `CMD_HEARTBEAT (0x00)` 探测并取回程序版本，不装 APK |
  | `POST /api/probe/connect` | `{"deviceId":"..."}` | 先 `install -r` 再发 `CMD_HEARTBEAT (0x00)` |
  | `POST /api/probe/message` | `{"deviceId":"...","cmd":0,"data":{}}` | 按命令字发一帧，`data` 作数据区；应答在 `data.response`，发送帧 hex 在 `data.frame`，三段真实帧 hex 在 `data.frames`（`tx` / `ack` / `rx`） |

  `cmd` 限 0~255、`data` 必须为 JSON 对象、数据区超 65535 字节明确失败；每次请求都发幂等 START 广播；
  同一设备按 `deviceId` 加锁串行化；`ACK_TIMEOUT=0.2`、`MAX_RETRY=3`、`RESPONSE_TIMEOUT=120.0`。
- 前端 `DFM-frontend/src/menu/App_Probe.vue`「探针测试」页：左侧选设备，右侧四个测试区
  （安装并通讯 / 快捷命令 心跳·未知命令 / TTS 朗读 / 自定义命令帧），下方展示请求、完整应答与帧 hex；
  底部「帧日志」累积记录时间 / 标题 / 设备 / 发送帧 / ACK / 响应帧三段真实字节 hex（最新在上、可滚动、可清空）。
  自定义命令帧在本地校验命令字与 JSON 对象，解析失败不发到后端。
- `App_Probe.vue` 与 `App_Utils.vue` 设备项的状态圆点：绿=通讯正常、红=失败、橙=处理中、灰=未检测，
  点击圆点=安装并重新通讯；设备列表刷新时只对**新出现**的设备自动调 `/api/probe/connect`，不重复安装。

---

## 8. 已知边界 / 暂不支持

明确列出，避免把验证工程当成完整控制能力：

- **只实现通讯 + 系统 TTS 朗读**：root shell、输入注入、截屏、文件传输、应用管理等控制能力**尚未实现**。
- **不做拆帧**：单帧数据区最多 65535 字节（长度字段 2 字节、低字节在前），超长判失败不截断，不支持自动分片。
- **不做多帧并发**：连接内串行（窗口 1）；TTS 朗读期间同步等待 `onDone`，最长占用该连接线程 60 秒，
  期间不处理该连接上的其它请求（其它连接与 `accept` 不受影响）。
- **不自动恢复**：`START_NOT_STICKY`，进程被系统杀掉后不会自启，需重新发 START 广播；也没有开机自启。
- **不做端口自适应**：21104 被占用即 bind 失败；不做鉴权、加密、多客户端排队等。
- **帧序去重只记最近一帧**：`lastSeq` 为每连接最近一次受理的帧序，不是全历史去重表。
- **网络直连尚未实机验证**：只有 USB 设备 `202600298120` 完成实机验证；`192.168.0.59:5555` 曾反复
  `adb connect` 报 10060。
- **打包 exe 后 APK 路径失效**：后端从 `DFM-probe/DFMProbe.apk` 读 APK，PyInstaller 的 `main.spec` /
  `main-onefile.spec` 的 `datas` 只收集 `data`、`lib`，届时 `/api/probe/connect` 会返回「找不到 APK」；
  `/api/probe/check`、`/api/probe/message` 不装 APK，不受影响。此项未在打包产物上验证。
- 页面未在 uTools GUI 中实际渲染验证；`DFM-frontend/package.json` 无类型检查脚本，vite 只转译不校验类型。
- 未组帧即失败（设备不在线、安装失败、数据区超 65535 字节）时没有 `frames`，帧日志三段显示 `—`。

---

## 9. 相关文档

- [README.md](README.md)：目录、构建步骤、通讯流程、协议摘要、实机验证结果与日志清单。
- [接口文档.md](接口文档.md)：命令字请求/返回参数规范、HTTP 端点与错误码。
- [`../probe与service通讯协议.md`](../probe与service通讯协议.md)：帧结构、CRC、ACK、重试、响应超时的完整协议定义。
