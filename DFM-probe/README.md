# DFM Probe

设备端 agent 的最小验证工程：ADB 装上一个 APK，PC 程序把「本机地址」发给它，
APK 据此**主动回连 PC** 建立 TCP 长连接，双向收发 JSON。用于验证「纯 ADB 不够用、
改用设备端常驻程序」这条路线是否走得通。

**`DFM-probe/` 是独立工程，不依赖 `DFM-service` 运行；页面探针由后端路由调用本目录的 APK。**

## 目录

```
DFM-probe/
├── AndroidManifest.xml          # 权限、receiver、前台服务声明
├── java/com/dfm/probe/
│   ├── ConnectReceiver.java     # 接收 host/port，拉起 ProbeService
│   └── ProbeService.java        # 前台服务：连回 PC + 按行收发 JSON
├── res/                         # app_name / 通知栏小图标
├── build.ps1                    # 不用 Gradle，直接调 SDK build-tools 打包签名
├── debug.keystore               # 固定调试签名（勿删，删了重装会签名冲突）
├── DFMProbe.apk                 # 构建产物（已签名，可直接 adb install）
├── pc/probe_test.py             # PC 端联调脚本
└── dist/                        # 构建中间产物（被 .gitignore 忽略）
```

## 构建

依赖：`ANDROID_HOME`（本机为 `E:\android-sdk-windows`）、build-tools 34.0.0、
platforms/android-34、JDK 8（`javac`）、JDK 17（`d8` / `apksigner` / `keytool`）。

```powershell
cd D:\uTools\DFM-probe
powershell -ExecutionPolicy Bypass -File build.ps1
```

流程：`aapt2 compile/link` → `javac` → `d8` → 合并 `classes.dex` → `zipalign`
→ `apksigner` 调试签名。产物 `DFMProbe.apk`。

## 通讯流程

```
PC                                      设备 (com.dfm.probe)
  │ adb install -r DFMProbe.apk          │
  │ ───────────────────────────────────> │
  │ 监听 0.0.0.0:<port>                  │
  │ am broadcast --include-stopped-packages
  │   -n com.dfm.probe/.ConnectReceiver
  │   -a com.dfm.probe.CONNECT
  │   --es host <PC_IP> --ei port <port> │
  │ ───────────────────────────────────> │ ConnectReceiver
  │                                      │ startForegroundService
  │ <────────── TCP 连接（APK 主动）──── │ ProbeService
  │ <── {"type":"hello", ...}            │
  │ ──> {"type":"ping","payload":"..."}  │
  │ <── {"type":"pong","payload":"..."}  │
  │ ──> {"type":"echo","payload":"..."}  │
  │ <── {"type":"echo","payload":"..."}  │
  │ ──> {"type":"tts","text":"..."}      │
  │ <── {"type":"tts","status":"done"}   │
  │ ──> {"type":"quit"}                  │
  │ <── {"type":"bye"}                   │
```

协议：UTF-8、换行分隔的 JSON。设备端连上后先发 `hello`；
支持 `ping`/`echo`/`tts`，其它 type 返回 `{"type":"error","message":...}`。
`tts` 用系统 `TextToSpeech` 朗读 `text`，读完回 `{"type":"tts","status":"done"}`，
失败回 `{"type":"tts","status":"error","message":...}`（连接不断开）。
空文本、引擎不可用、朗读超时都会明确回 error，不会崩服务。
断线后 APK 每 3 秒自动重连；PC 重新广播新地址会打断旧连接并改用新地址。

## 联调测试

```powershell
cd D:\uTools\DFM-probe
python pc/probe_test.py --device 192.168.0.59:5555
```

`--host` 可手动指定发给设备的地址（默认用 UDP connect 自动探测本机出口 IP）。
若 Windows 防火墙拦住入站，用 `--reverse` 走 `adb reverse`（设备连
`127.0.0.1:<port>`，由 adbd 转发回 PC）。
`--tts-text "你好"` 可自定义朗读文本（默认 `设备语音测试`），
`--skip-install` 跳过安装步骤。

## 实机验证结果（2026-11-05，设备 192.168.0.59:5555 / Android 11 / rk3568_r）

| 项 | 命令 | 结果 |
| --- | --- | --- |
| 构建签名 | `powershell -ExecutionPolicy Bypass -File build.ps1` | `DFMProbe.apk` 16858 bytes，apksigner v1/v2/v3 均 `true` |
| 安装 | `adb install -r DFMProbe.apk` | `Success`，`pm list packages` 可见 `com.dfm.probe`；keystore 固定在工程根目录，重复构建签名不变，可直接覆盖安装 |
| LAN 直连 | `python pc/probe_test.py --device 192.168.0.59:5555` | `PASS`，设备 `192.168.0.59` 主动回连到 `192.168.2.93` |
| 兜底通道 | `python pc/probe_test.py --reverse --skip-install` | `PASS`，走 `adb reverse` 从设备 `127.0.0.1` 回连 |
| hello | 连接建立后首包 | `{"type":"hello","device":"rk3568_r","sdk":30,"abi":"arm64-v8a","package":"com.dfm.probe"}` |
| 双向收发 | `ping`→`pong`、`echo`（含中文 `你好 DFM-Probe`）、`quit`→`bye` | 全部符合预期 |
| 系统 TTS | `{"type":"tts","text":"设备语音测试"}` → `{"type":"tts","status":"done"}` | `PASS`；讯飞引擎 logcat 有 `onSynthesizeText` / `onOutPutCallBack LEN=...` / `tts run end`，确认真实合成输出 |

边界场景（临时脚本，日志在 `.reasonix/logs/boundary_test.log`）：广播缺参数 /
空 host / 端口 70000 均被 `ConnectReceiver` 拒绝（3 条 `invalid host/port`，未拉起服务）；
未知 type 返回 `{"type":"error",...}`；坏 JSON 只断当前连接不崩溃；`pidof com.dfm.probe`
仍存活，logcat 无 `FATAL EXCEPTION`。共 10 项检查全通过。

TTS 边界（`.reasonix/logs/tts_boundary.log`，11/11 通过）：空文本 / 缺 `text` 字段 /
纯空格文本 → `{"type":"tts","status":"error","message":"empty text"}`；未知 type →
`{"type":"error","message":"unknown type: bogus"}`；中文+数字文本正常朗读 → `done`；
tts 后连接仍可 `ping`→`pong`；连续第二次朗读复用已初始化引擎 → `done`；
`quit`→`bye`；进程存活；logcat 无 `FATAL EXCEPTION`。

日志：`.reasonix/logs/build.log`、`probe_test.log`、`probe_test_reverse.log`、
`boundary_test.log`、`tts_build.log`、`tts_e2e.log`、`tts_boundary.log`
（该目录被 `.gitignore` 忽略，不入库）。

## 页面探针（DFM-frontend）

后端 `DFM-service/routes/probe.py` 三个端点，统一返回
`{"code":200,"message":...,"data":{"connected":true|false,...}}`：

| 端点 | 请求体 | 行为 |
| --- | --- | --- |
| `POST /api/probe/check` | `{"deviceId":"..."}` | 只探测，不装 APK |
| `POST /api/probe/connect` | `{"deviceId":"..."}` | 先 `install -r` 再探测 |
| `POST /api/probe/message` | `{"deviceId":"...","message":{...}}` | 把 `message` 原样发给设备，应答放在 `data.response` |

- 与 `pc/probe_test.py` 默认走 LAN 直连不同，服务路由统一走 `adb reverse`：
  PC 监听 `127.0.0.1:<port>`，设备连本机回环由 adbd 转发，USB 设备与网络设备都能连通，
  也不依赖 PC 的入站防火墙规则。每次探测结束自动 `adb reverse --remove` 并 `force-stop`
  常驻服务，避免 APK 对已关闭的端口空转重连。
- `message` 端点读超时单独设为 120 秒（`MESSAGE_TIMEOUT`），因为 TTS 朗读可能要等
  引擎初始化（30 秒）与 `onDone`（60 秒）；`check` / `connect` 仍是 15 秒。
- 首页菜单「探针测试」进入独立页 `DFM-frontend/src/menu/App_Probe.vue`：左侧选设备，
  右侧四个测试区（安装并通讯 / 快捷消息 ping·echo·未知类型 / TTS 朗读 / 自定义 JSON
  消息），下方展示请求与完整应答。自定义消息只在本地校验是否为 JSON 对象，解析失败
  不会发到后端。
- `App_Probe.vue` 与 `App_Utils.vue` 的设备项里，圆点都放在「断开连接」按钮左边：
  绿=通讯正常、红=失败、橙=处理中、灰=未检测；点击圆点=安装并重新通讯。两个页面都用
  `Set` 记住已处理过的 deviceId，只在设备列表刷新时对**新出现**的设备自动调
  `/api/probe/connect`，不重复安装。
- 服务路由从 `DFM-probe/DFMProbe.apk` 读取 APK。**源码运行（`.venv`）下路径可解析；
  PyInstaller 打包成 exe 后该路径失效**（`main.spec` / `main-onefile.spec` 的 `datas`
  只收集 `data`、`lib`），届时 `/api/probe/connect` 会返回「找不到 APK」；
  `/api/probe/check`、`/api/probe/message` 不装 APK，不受影响。此项尚未在打包产物上验证。
- 验证日志：`.reasonix/logs/probe_verify.log`、`probe_http_verify.log`、
  `probe_frontend_build.log`、`probe_message_verify.log`、`probe_page_flow.log`、
  `probe_frontend_build2.log`、`probe_bundle_check.log`。

## 已知边界

- 服务返回 `START_NOT_STICKY`：进程被系统杀掉后不会自行恢复，需要 PC 重新广播地址。
- 一次只服务一个连接；连接断开后进入重连循环。
- 当前只做「通讯 + 系统 TTS 朗读」验证，尚未实现 root shell、输入注入、截屏等控制能力。
- 通知常驻（前台服务），Android 8+ 需要通知渠道，本工程已处理。
- TTS 依赖设备装有可用引擎（本机为 `com.iflytek.speechcloud`）；Android 11 (targetSdk 30)
  必须在清单声明 `<queries>` 里含 `android.intent.action.TTS_SERVICE`，否则 bind 不上引擎、
  `onInit` 立即回 `ERROR`。引擎冷启动可达 10 秒以上，初始化等待窗口设为 30 秒。
  朗读期间会占用连接线程（同步等 `onDone`，最长 60 秒），期间不处理其它请求。
