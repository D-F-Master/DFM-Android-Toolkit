# DFM Probe

设备端 agent 的最小验证工程：ADB 只负责把 APK 推到设备上安装并启动，
APK 作为 TCP 服务端监听设备本机 `0.0.0.0:21104`，PC 作为客户端主动连入，
按二进制帧协议收发数据。用于验证「纯 ADB 不够用、改用设备端常驻程序」这条路线是否走得通。

**`DFM-probe/` 是独立工程，不依赖 `DFM-service` 运行；页面探针由后端路由调用本目录的 APK。**
程序支持的功能清单见 [FEATURES.md](FEATURES.md)；
帧格式、命令字、CRC 与 ACK 规则见仓库根目录 `probe与service通讯协议.md`。

## 目录

```
DFM-probe/
├── FEATURES.md                  # 程序支持的功能清单
├── AndroidManifest.xml          # 权限、receiver、前台服务声明
├── java/com/dfm/probe/
│   ├── StartReceiver.java       # 接收 START/开机/看门狗广播，拉起 ProbeService
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

## 自启动与后台保活

- 清单声明 `RECEIVE_BOOT_COMPLETED`，`StartReceiver` 同时过滤 `com.dfm.probe.START`、
  `android.intent.action.BOOT_COMPLETED` 和 `com.dfm.probe.WATCHDOG`：设备重启后自动拉起服务，
  不需要 PC 再发广播。
- 服务返回 `START_STICKY`：进程被系统回收后自动重建并重新监听 21104。
- 每次广播都用 `AlarmManager.setRepeating(ELAPSED_REALTIME_WAKEUP, 60s)` 注册/续期看门狗广播，
  兜住 `START_STICKY` 未生效的回收场景；启动前台服务被系统拒绝时记日志，下一轮重试。
- `force-stop` 会同时清掉 alarm 与所有广播，只能由 PC 重新发 START 广播恢复；
  Doze 白名单需部署时执行 `adb shell dumpsys deviceidle whitelist +com.dfm.probe`。
  以上开机自启与看门狗效果尚未实机验证。

## 协议

帧格式：`AA 55 | 帧序(1B) | 长度(2B，低字节在前) | 命令字(1B) | 数据区 | CRC16(低字节在前)`。
长度 = 数据区字节数（低字节在前），**单帧数据区最多 65535 字节**；
CRC16-Modbus 覆盖 `帧序 + 长度(2B) + 命令字 + 数据区`。完整定义见 `probe与service通讯协议.md`。

数据区为 UTF-8 JSON 对象（裸换行须转义）。设备校验通过后立即回 ACK
（`AA 55 <同帧序> 00 00 06 <crc_lo> <crc_hi>`），执行完再发**同帧序响应帧**，
命令字 = `请求命令字 | 0x80`，数据区为统一 `{code,message,data}` 信封
（成功 `code:200` / `data` 为业务对象，失败 `code:400`/`500` / `data` 固定 `null`），
响应帧不要求 ACK。完整参数规范见 `接口文档.md`「三」。
CRC 错或长度非法的帧直接丢弃且不回 ACK；同一帧序重复帧只回 ACK、不重复执行业务。
PC 停等 ACK：`T=200ms`、`N=3`（最多共发 4 次）、窗口 1、帧序 0~255 循环。
设备校验通过就回 ACK（不等业务执行完），否则 200ms 停等无法容忍 TTS 朗读的数十秒耗时。

| 命令字 | 名称 | 方向 | 数据区 | 应答 |
| --- | --- | --- | --- | --- |
| `0x00` | CMD_HEARTBEAT | PC→设备 | `{}` | `{"code":200,"message":"success","data":{"version":"1.0"}}` |
| `0x01` | CMD_TTS | PC→设备 | `{"text":"..."}` | `{"code":200,"message":"success","data":{"status":"done"}}` / `{"code":400/500,"message":"...","data":null}` |
| `0x02` | CMD_DEVICE_INFO | PC→设备 | `{}` | 统一信封，`data` 为设备基础信息 + 串口 / 摄像头 / 外设列表（见 `接口文档.md`「三」） |
| `0x03` | CMD_SERIAL_READ | PC→设备 | `{}` | 统一信封，`data` 为 `{"hex":"01 02 03","text":"..","bytes":N}`（从会话缓冲取增量，见 `接口文档.md`「3.4」） |
| `0x04` | CMD_SERIAL_OPEN | PC→设备 | `{"serial":"/dev/ttyS3","baud":9600,"parity":"none","dataBits":8,"stopBits":1}` | 统一信封，`data` 为 `{serial,baud,parity,dataBits,stopBits,stty}` |
| `0x05` | CMD_SERIAL_SEND | PC→设备 | `{"mode":"text","payload":"AT","times":1,"interval":500}` 或 `{"stop":true}` | 统一信封，`data` 为 `{mode,bytes,times,interval,loop}` 或 `{stopped}` |
| `0x06` | CMD_ACK | 设备→PC | 无 | 无（通知类） |
| `0x07` | CMD_SERIAL_CLOSE | PC→设备 | `{}` | 统一信封，`data` 为 `{"closed":bool}` |
| `0x80`、`0x81`、`0x82`、`0x83`、`0x84`、`0x85`、`0x87` | CMD_RESPONSE | 设备→PC | UTF-8 JSON | 无（通知类，不要求 ACK） |
| `0xFF` | CMD_ERROR | 设备→PC | `{"code":400,"message":"unknown cmd: 0x..","data":null}` | 无（通知类） |

> 旧版 `0x01`~`0x05`、`0x07` 业务命令字已全部删除，本版为破坏性不兼容变更；
> 没有关闭 TCP 连接的命令字，连接由 PC 侧主动关闭。
> `0x02` / `0x82` 为新增的设备详细信息命令字/响应字。
> `0x03` / `0x83`、`0x04` / `0x84`、`0x05` / `0x85`、`0x07` / `0x87` 为串口会话命令字/响应字：
> 设备端打开串口后后台持续读并缓冲，`0x03` 取增量，不做帧解析，Hex/Txt 原样显示。

`CMD_TTS` 用系统 `TextToSpeech` 朗读 `text`，读完回
`{"code":200,"message":"success","data":{"status":"done"}}`，
失败回 `{"code":400/500,"message":"...","data":null}`（连接不断开）。空文本、引擎不可用、
朗读超时都会明确回错误码，不会崩服务。

`CMD_DEVICE_INFO` 由设备端 APK 采集并返回：安卓版本 / SDK / 机型 / 厂商 / 设备代号 /
显示版本 / SN / 构建日期 / 内核版本，以及 `/dev` 下的串口节点列表、`CameraManager` 的
摄像头列表、`InputManager` + `UsbManager` 的外设/输入设备列表。单项采集不可读时降级为
空数组或 `Unknown`，不会让整帧失败；参数规范见 `接口文档.md`「3.3」。

串口会话四命令字由设备端 APK 直接打开串口节点（默认 `/dev/ttyS3`），**不经 adb shell**，
按 `串口助手-反编译说明.md` 的透传逻辑：接收侧 `read()` 返回多少原样显示多少、不做帧解析，
发送侧把输入原样写出；设备端只保持一个会话，所有串口方法串行化。

- `CMD_SERIAL_OPEN (0x04)`：`serial`（必填）、`baud`（13 档白名单）、`parity`（默认 `none`）、
  `dataBits`（默认 `8`）、`stopBits`（默认 `1`）；`stty -F <serial> <baud> cs<N> ... raw -echo`
  配置后以 `RandomAccessFile(serial,"rw")` 打开并起后台读线程持续读、写入 65536 上限缓冲
  （溢出丢最旧）。重复 OPEN 先关旧会话。
- `CMD_SERIAL_READ (0x03)`：不带参数，取缓冲增量并清空，返回 `{hex,text,bytes}`；未 OPEN 回
  `400 serial session not opened`。页面轮询此命令字即「打开串口持续读」。
- `CMD_SERIAL_SEND (0x05)`：`mode` 为 `text`/`hex`、`payload` 非空、`times`（默认 1，`0`=持续）、
  `interval`（默认 500ms）；`times==1` 同步写出，否则起设备端后台线程定时写出并立即回响应，
  `{stop:true}` 停止循环。参数校验在会话状态检查之前。
- `CMD_SERIAL_CLOSE (0x07)`：停读线程与循环线程、关串口、清缓冲，返回 `{closed}`；`onDestroy` 也关闭。

需设备已 root 且串口节点可读写；`stty` 配置失败或 `open` 被拒回 `500 serial cmd failed`，
参数越界回 `400`。Android 设备 `stty` 是否支持 `csN` / `parenb` / `parodd` / `cstopb` 选项、
`RandomAccessFile.read` 阻塞时 `0x07` 的关闭行为均未真机验证；参数规范见 `接口文档.md`「3.4」。

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
脚本覆盖：组帧 65536 字节拒绝、CMD_HEARTBEAT / CMD_DEVICE_INFO 参数校验 /
串口会话四命令字（`0x04` 五个参数、`0x03` 未 OPEN、`0x05` 载荷/次数/间隔、`0x07` 与 stop 幂等）
共 16 个参数校验用例、CMD_TTS 成功应答、TTS 缺 text / 非字符串 text / 空文本 /
未知命令 / CRC 错帧丢弃。
测试结束**不 force-stop**，服务常驻监听 21104。

## 实机验证结果（2026-11-05，设备 202600298120 / USB / Android 11 / rk3568_r）

> 本表及下方 46/46、前端构建记录均为**旧版 1 字节长度帧协议 + 旧命令字**构建的实机/回归结果；
> 当前 2 字节长度帧协议、统一信封与 `CMD_HEARTBEAT (0x00)` / `CMD_TTS (0x01)` /
> `CMD_DEVICE_INFO (0x02)` 及串口会话四命令字 `CMD_SERIAL_OPEN (0x04)` /
> `CMD_SERIAL_READ (0x03)` / `CMD_SERIAL_SEND (0x05)` / `CMD_SERIAL_CLOSE (0x07)`
> 目前只有编译、静态与帧仿真验证，尚未实机复测；
> 表内 `ok` 类应答与 `CMD_QUIT` 仅属历史记录。旧版 `0x03` 一次性读取（`duration` / `maxBytes`）
> 的真机刷卡数据在 `接口文档.md`「3.4」末尾保留为历史记录。

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
| `POST /api/probe/check` | `{"deviceId":"..."}` | 只发一帧 `CMD_HEARTBEAT (0x00)` 探测并取回程序版本，不装 APK |
| `POST /api/probe/connect` | `{"deviceId":"..."}` | 先 `install -r` 再发 `CMD_HEARTBEAT (0x00)` |
| `POST /api/probe/message` | `{"deviceId":"...","cmd":0,"data":{}}` | 按命令字发一帧，`data` 作数据区；应答在 `data.response`，发送帧 hex 在 `data.frame`，三段真实帧 hex 在 `data.frames`（`tx` / `ack` / `rx`） |

- `/api/probe/message` 的 `cmd` 接受 `0`~`255`（`0x00`~`0xFF`）任意整数，越界回 422；
  已登记的 `0` / `1` / `2` / `3` / `4` / `5` / `7` 返回业务应答
  （`3` / `4` / `5` / `7` 为串口会话四命令字），其余命令字由设备端回 `0xFF CMD_ERROR`，
  供页面「自定义发送」探测。`data` 必须是 JSON 对象；数据区超过 65535 字节明确返回失败。
- 服务路由每次都发幂等的 START 广播启动 APK；USB 设备用系统分配的随机本地端口做
  `adb forward`（结束时 `--remove`，多设备不抢端口），网络设备直连 `<IP>:21104`。
  **不再用 `adb reverse`，也不再 `force-stop`**：服务常驻监听 21104。
- `RESPONSE_TIMEOUT = 120.0` 覆盖 TTS 引擎初始化 30s + 朗读 60s；`ACK_TIMEOUT = 0.2`、
  `MAX_RETRY = 3`。同一设备的请求按 `deviceId` 加锁串行化，避免帧序与 forward 端口互相干扰。
- 首页菜单「探针测试」进入独立页 `DFM-frontend/src/menu/App_Probe.vue`：左侧选设备，
  右侧测试区（安装并通讯 / 快捷命令 心跳·设备详细信息 / 串口会话：0x04 打开 /
  0x03 取增量 / 0x05 单次·循环发送 / 0x07 关闭 / TTS 朗读 /
  自定义发送：自定义 0x00~0xFF 命令字与 JSON 数据区），
  下方展示请求、完整应答与帧 hex。
- `DFM-frontend/src/menu/App_Utils.vue` 方案 C「串口会话」同样走探针四命令字：
  打开串口后页面轮询 `0x03` 取增量，Hex/Txt 原样显示（不做帧解析），单次/循环发送由
  设备端后台线程执行，关闭串口发 `0x07`。切换设备/方案只停 PC 轮询，不自动发 `0x07 CLOSE`。
- 独立页底部「帧日志」按测试累积记录（最新在上、可滚动、可清空）：时间 / 标题 / 设备 /
  发送帧 / ACK / 响应帧三段真实字节 hex。失败请求也记录并带错误说明，缺失的帧显示 `—`。
  只记录右侧手动测试（快捷命令、TTS 朗读、自定义发送 + 安装并通讯 / 仅检测）；圆点点击与进入页面自动安装不写日志。
  自定义发送的命令字 / 数据区格式校验不通过时不发请求，只在应答区提示，不写帧日志。
- `App_Probe.vue` 与 `App_Utils.vue` 的设备项里，圆点都放在「断开连接」按钮左边：
  绿=通讯正常、红=失败、橙=处理中、灰=未检测；点击圆点=安装并重新通讯。两个页面都用
  `Set` 记住已处理过的 deviceId，只在设备列表刷新时对**新出现**的设备自动调
  `/api/probe/connect`，不重复安装。
- 服务路由从 `DFM-probe/DFMProbe.apk` 读取 APK。**源码运行（`.venv`）下路径可解析；
  PyInstaller 打包成 exe 后该路径失效**（`main.spec` / `main-onefile.spec` 的 `datas`
  只收集 `data`、`lib`），届时 `/api/probe/connect` 会返回「找不到 APK」；
  `/api/probe/check`、`/api/probe/message` 不装 APK，不受影响。此项尚未在打包产物上验证。

## 已知边界

- 服务返回 `START_STICKY` + 开机自启 + 60 秒看门狗：设备重启与系统回收后能自行恢复；
  但 force-stop 会清掉 alarm 与所有广播，只能由 PC 重新发 START 广播恢复，Doze 白名单也需自行设置。
- 每连接一个工作线程、连接内串行（窗口 1）；TTS 朗读期间会占用该连接线程（同步等
  `onDone`，最长 60 秒），期间不处理该连接上的其它请求，但 `accept` 循环不受影响。
- 单帧数据区最多 65535 字节（长度字段 2 字节、低字节在前），超长判失败不截断；当前工程不做拆帧。
- 21104 端口若被设备上其它程序占用会 bind 失败，服务会记日志 `listen failed on port 21104`
  并把 `running` 置 false；当前未做端口自适应。
- 当前只做「通讯 + 系统 TTS 朗读 + 设备信息采集 + 串口会话」验证，尚未实现 root shell、
  输入注入、截屏等控制能力。串口会话（`0x04` / `0x03` / `0x05` / `0x07`）依赖设备已 root
  且串口节点可读写；打开串口、后台持续读、循环发送、CLOSE 停线程均**未在真机端到端验证**，
  Android `stty` 的 `csN` / `parenb` / `parodd` / `cstopb` 选项与读阻塞时 CLOSE 行为亦未验证。
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
