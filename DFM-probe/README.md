# DFM Probe

设备端 agent 的最小验证工程：ADB 只负责把 APK 推到设备上安装并启动，
APK 作为 TCP 服务端监听设备本机 `0.0.0.0:21104`，PC 作为客户端主动连入，
按二进制帧协议收发数据。用于验证「纯 ADB 不够用、改用设备端常驻程序」这条路线是否走得通。

**`DFM-probe/` 是独立工程，不依赖 `DFM-service` 运行；页面探针由后端路由调用本目录的 APK。**
帧格式、命令字、CRC 与 ACK 规则见仓库根目录 `probe与service通讯协议.md`。

## 目录

```
DFM-probe/
├── AndroidManifest.xml          # 权限、receiver、前台服务声明
├── java/com/dfm/probe/
│   ├── StartReceiver.java       # 接收 START 广播，拉起 ProbeService
│   └── ProbeService.java        # 前台服务：监听 21104 + 帧协议收发
├── res/                         # app_name / 通知栏小图标
├── build.ps1                    # 不用 Gradle，直接调 SDK build-tools 打包签名
├── debug.keystore               # 固定调试签名（勿删，删了重装会签名冲突）
├── DFMProbe.apk                 # 构建产物（已签名，可直接 adb install）
├── pc/probe_test.py             # PC 端帧协议联调脚本
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
  │ am broadcast --include-stopped-packages
  │   -n com.dfm.probe/.StartReceiver
  │   -a com.dfm.probe.START            │
  │ ───────────────────────────────────> │ StartReceiver
  │                                      │ startForegroundService
  │                                      │ ProbeService 监听 0.0.0.0:21104
  │ USB: adb forward tcp:<local> tcp:21104
  │ 网络: 直连 <设备IP>:21104            │
  │ ───────── TCP 连接（PC 主动）───────> │
  │ ──> AA 55 <seq> <len_lo> <len_hi> <cmd> <data> <crc_lo> <crc_hi>
  │ <── AA 55 <seq> 00 00 06 <crc_lo> <crc_hi>                          (ACK)
  │ <── AA 55 <seq> <len_lo> <len_hi> <cmd|0x80> <data> <crc_lo> <crc_hi> (响应帧)
```

adb 只做三件事：安装、发 START 广播启动服务、USB 设备做 `adb forward` 端口映射；
**adb 命令不承载任何业务数据**，业务数据全部走 21104 上的帧协议。
网络设备不需要 forward，APK 启动后直接连 `<设备IP>:21104`。

## 协议

帧格式：`AA 55 | 帧序(1B) | 长度(2B，低字节在前) | 命令字(1B) | 数据区 | CRC16(低字节在前)`。
长度 = 数据区字节数（低字节在前），**单帧数据区最多 65535 字节**；
CRC16-Modbus 覆盖 `帧序 + 长度(2B) + 命令字 + 数据区`。完整定义见 `probe与service通讯协议.md`。

数据区为 UTF-8 JSON 对象（裸换行须转义）。设备校验通过后立即回 ACK
（`AA 55 <同帧序> 00 00 06 <crc_lo> <crc_hi>`），执行完再发**同帧序响应帧**，
命令字 = `请求命令字 | 0x80`，数据区为业务结果（统一含 `ok` 布尔字段；
`CMD_DEVICE_INFO (0x07)` 例外，沿用 `code/message/data` 信封），响应帧不要求 ACK。
CRC 错或长度非法的帧直接丢弃且不回 ACK；同一帧序重复帧只回 ACK、不重复执行业务。
PC 停等 ACK：`T=200ms`、`N=3`（最多共发 4 次）、窗口 1、帧序 0~255 循环。
设备校验通过就回 ACK（不等业务执行完），否则 200ms 停等无法容忍 TTS 朗读的数十秒耗时。

| 命令字 | 名称 | 方向 | 数据区 | 应答 |
| --- | --- | --- | --- | --- |
| `0x01` | CMD_PING | PC→设备 | `{"payload":"..."}` | `{"ok":true,"payload":"..."}` |
| `0x02` | CMD_ECHO | PC→设备 | `{"payload":"..."}` | `{"ok":true,"payload":"..."}` |
| `0x03` | CMD_TTS | PC→设备 | `{"text":"..."}` | `{"ok":true,"status":"done"}` / `{"ok":false,"message":"..."}` |
| `0x04` | CMD_INFO | PC→设备 | `{}` | `{"ok":true,"device":...,"sdk":...,"abi":...,"package":...}` |
| `0x05` | CMD_QUIT | PC→设备 | `{}` | `{"ok":true,"message":"bye"}`，只关当前连接，服务继续监听 |
| `0x06` | CMD_ACK | 设备→PC | 无 | 无（通知类） |
| `0x07` | CMD_DEVICE_INFO | PC→设备 | `{}` | `{"code":200,"message":"success","data":{...14 项设备信息...}}`，见 `接口文档.md`「三」 |
| `0x81`–`0x85`、`0x87` | CMD_RESPONSE | 设备→PC | UTF-8 JSON | 无（通知类，不要求 ACK） |
| `0xFF` | CMD_ERROR | 设备→PC | `{"ok":false,"message":"unknown cmd: 0x.."}` | 无（通知类） |

`CMD_TTS` 用系统 `TextToSpeech` 朗读 `text`，读完回 `{"ok":true,"status":"done"}`，
失败回 `{"ok":false,"message":...}`（连接不断开）。空文本、引擎不可用、朗读超时都会明确回
`ok:false`，不会崩服务。

## 联调测试

```powershell
cd D:\uTools\DFM-probe
python pc/probe_test.py --device 202600298120
python pc/probe_test.py --device 192.168.0.59:5555 --tts-text 你好世界
python pc/probe_test.py --device 202600298120 --skip-install
```

`--device` 含 `:` 视为网络设备（直连该 IP 的 21104），否则视为 USB 设备（自动
`adb forward` 随机本地端口并在结束时 `--remove`）。
`--tts-text` 自定义朗读文本，`--skip-install` 跳过安装。
脚本覆盖：组帧 65536 字节拒绝、CMD_INFO / CMD_DEVICE_INFO / PING / ECHO / TTS /
未知命令 / 空文本 / CRC 错帧丢弃、CMD_QUIT 后重新连入仍可 ping（证明服务继续监听）。
测试结束**不 force-stop**，服务常驻监听 21104。

## 实机验证结果（2026-11-05，设备 202600298120 / USB / Android 11 / rk3568_r）

> 本表及下方 46/46、前端构建记录均为**旧版 1 字节长度帧协议**构建的实机/回归结果；
> 2 字节长度帧协议与 `CMD_DEVICE_INFO (0x07)` 目前只有编译、静态与帧仿真验证，尚未实机复测。

| 项 | 命令 | 结果 |
| --- | --- | --- |
| 构建签名 | `powershell -ExecutionPolicy Bypass -File build.ps1` | `DFMProbe.apk` 16858 bytes，apksigner v1/v2/v3 均 `true` |
| 清单 | `aapt2 dump xmltree DFMProbe.apk --file AndroidManifest.xml` | `.StartReceiver` + action `com.dfm.probe.START`、`.ProbeService`、TTS `<queries>` |
| 安装 | `adb install -r DFMProbe.apk` | `Success`，`pm list packages` 可见 `com.dfm.probe`；keystore 固定在工程根目录，重复构建签名不变，可直接覆盖安装 |
| 监听 | `adb shell ss -ltn \| grep 21104` | `LISTEN 0 50 *:21104 *:*` |
| 帧协议全链路 | `python pc/probe_test.py --device 202600298120` | `[6/6] PASS`、`TEST_EXIT_CODE=0`（日志 `.reasonix/logs/frame_probe_test.log`） |
| 系统 TTS | CMD_TTS → `{"ok":true,"status":"done"}` | logcat 有 `onOutPutCallBack-----LEN=...` / `tts run end` / `onSynthesizeText---4--`，确认真实合成输出 |
| 边界 | 未知命令 / 空文本 / CRC 错帧 / quit 后重连 | logcat `bad crc, drop frame seq=6`、`client disconnected` 后 `client connected` 再 ping 正常 |
| 帧日志三段 | `POST /api/probe/message` 实机 | `data.frames` 含 `tx` / `ack` / `rx`：`tx` 与 `data.frame` 一致、ACK 为 `AA 55 <seq> 00 06 ...`、响应帧命令字为 `cmd\|0x80` |

后端纯函数单测 + HTTP 接口回归 46/46 PASS（`.reasonix/logs/frame_backend_verify.log`）：
CRC 与文档 C 实现对拍、组帧/解帧正常+粘包+半包+CRC 错+长度非法+255/256 边界、
ACK 停等 1~4 次丢包、帧序 0~255 回绕、三端点注册、非法入参 400/422、
全命令字实机应答、quit 后服务仍监听、离线设备明确失败；
另含 `data.frames` 三段真实帧校验（`tx` 与 `data.frame` 一致、ACK 为 `00 06`、
响应帧命令字为 `cmd|0x80`）。

前端 `npm run build` `BUILD_EXIT=0`（`.reasonix/logs/frame_log_frontend_build.log`），
产物字符串检查 18/18 PASS（`.reasonix/logs/frame_log_bundle_check.log`）。

日志：`.reasonix/logs/build.log`、`probe_test.log`、`boundary_test.log`、
`tts_build.log`、`tts_e2e.log`、`tts_boundary.log`、`probe_verify.log`、
`probe_http_verify.log`、`probe_message_verify.log`、`probe_page_flow.log`、
`probe_frontend_build2.log`、`probe_bundle_check.log`、`frame_protocol_round.log`、
`frame_build.log`、`frame_probe_test.log`、`frame_backend_verify.log`、
`frame_frontend_build.log`、`frame_bundle_check.log`、
`frame_log_frontend_build.log`、`frame_log_bundle_check.log`
（该目录被 `.gitignore` 忽略，不入库）。

## 页面探针（DFM-frontend）

后端 `DFM-service/routes/probe.py` 三个端点，统一返回
`{"code":200,"message":...,"data":{"connected":true|false,...}}`：

| 端点 | 请求体 | 行为 |
| --- | --- | --- |
| `POST /api/probe/check` | `{"deviceId":"..."}` | 只发一帧 CMD_PING 探测，不装 APK |
| `POST /api/probe/connect` | `{"deviceId":"..."}` | 先 `install -r` 再发 CMD_PING |
| `POST /api/probe/message` | `{"deviceId":"...","cmd":1,"data":{...}}` | 按命令字发一帧，`data` 作数据区；应答在 `data.response`，发送帧 hex 在 `data.frame`，三段真实帧 hex 在 `data.frames`（`tx` / `ack` / `rx`） |

- `cmd` 限 0~255；`data` 必须是 JSON 对象；数据区超过 65535 字节明确返回失败。
- 服务路由每次都发幂等的 START 广播启动 APK；USB 设备用系统分配的随机本地端口做
  `adb forward`（结束时 `--remove`，多设备不抢端口），网络设备直连 `<IP>:21104`。
  **不再用 `adb reverse`，也不再 `force-stop`**：服务常驻监听 21104。
- `RESPONSE_TIMEOUT = 120.0` 覆盖 TTS 引擎初始化 30s + 朗读 60s；`ACK_TIMEOUT = 0.2`、
  `MAX_RETRY = 3`。同一设备的请求按 `deviceId` 加锁串行化，避免帧序与 forward 端口互相干扰。
- 首页菜单「探针测试」进入独立页 `DFM-frontend/src/menu/App_Probe.vue`：左侧选设备，
  右侧四个测试区（安装并通讯 / 快捷命令 ping·echo·未知命令 / TTS 朗读 / 自定义命令帧），
  下方展示请求、完整应答与帧 hex。自定义命令帧在本地校验命令字 0~255 与数据区 JSON 对象，
  解析失败不会发到后端。
- 独立页底部「帧日志」按测试累积记录（最新在上、可滚动、可清空）：时间 / 标题 / 设备 /
  发送帧 / ACK / 响应帧三段真实字节 hex。失败请求也记录并带错误说明，缺失的帧显示 `—`。
  只记录右侧手动测试（四个测试区 + 安装并通讯 / 仅检测）；圆点点击与进入页面自动安装不写日志。
- `App_Probe.vue` 与 `App_Utils.vue` 的设备项里，圆点都放在「断开连接」按钮左边：
  绿=通讯正常、红=失败、橙=处理中、灰=未检测；点击圆点=安装并重新通讯。两个页面都用
  `Set` 记住已处理过的 deviceId，只在设备列表刷新时对**新出现**的设备自动调
  `/api/probe/connect`，不重复安装。
- 服务路由从 `DFM-probe/DFMProbe.apk` 读取 APK。**源码运行（`.venv`）下路径可解析；
  PyInstaller 打包成 exe 后该路径失效**（`main.spec` / `main-onefile.spec` 的 `datas`
  只收集 `data`、`lib`），届时 `/api/probe/connect` 会返回「找不到 APK」；
  `/api/probe/check`、`/api/probe/message` 不装 APK，不受影响。此项尚未在打包产物上验证。

## 已知边界

- 服务返回 `START_NOT_STICKY`：进程被系统杀掉后不会自行恢复，需要重新发 START 广播。
- 每连接一个工作线程、连接内串行（窗口 1）；TTS 朗读期间会占用该连接线程（同步等
  `onDone`，最长 60 秒），期间不处理该连接上的其它请求，但 `accept` 循环不受影响。
- 单帧数据区最多 65535 字节（长度字段 2 字节、低字节在前），超长判失败不截断；当前工程不做拆帧。
- 21104 端口若被设备上其它程序占用会 bind 失败，服务会记日志 `listen failed on port 21104`
  并把 `running` 置 false；当前未做端口自适应。
- 当前只做「通讯 + 系统 TTS 朗读」验证，尚未实现 root shell、输入注入、截屏等控制能力。
- 通知常驻（前台服务），Android 8+ 需要通知渠道，本工程已处理。
- TTS 依赖设备装有可用引擎（本机为 `com.iflytek.speechcloud`）；Android 11 (targetSdk 30)
  必须在清单声明 `<queries>` 里含 `android.intent.action.TTS_SERVICE`，否则 bind 不上引擎、
  `onInit` 立即回 `ERROR`。引擎冷启动可达 10 秒以上，初始化等待窗口设为 30 秒。
- 网络设备直连路径当前只有 USB 设备 `202600298120` 实机验证；`192.168.0.59:5555` 曾反复
  `adb connect` 报 10060，网络直连尚未实机验证。
- 页面未在 uTools GUI 中实际渲染验证；`DFM-frontend/package.json` 无 `vue-tsc` / typecheck
  脚本，vite 只做转译不做类型检查。
- 未组帧的失败（设备不在线、安装失败、数据区超 65535 字节）没有 `frames`，页面帧日志三段
  显示 `—`；本地 JSON / 命令字校验失败不写帧日志，只显示在结果区。
