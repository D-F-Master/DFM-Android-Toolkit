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
| 组件声明 | `AndroidManifest.xml` | 权限、`START` / 开机 / 看门狗广播接收器、前台服务、TTS 包可见性声明 |
| 启动入口 | `java/com/dfm/probe/StartReceiver.java` | 接收 `com.dfm.probe.START` / `BOOT_COMPLETED` / 看门狗广播并拉起服务，注册 AlarmManager 定时自检 |
| 设备端主体 | `java/com/dfm/probe/ProbeService.java` | 前台常驻、TCP 监听、帧协议收发、3 个业务命令、系统 TTS、设备信息采集 |
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
- **开机自启**：清单声明 `RECEIVE_BOOT_COMPLETED`，`StartReceiver` 过滤 `android.intent.action.BOOT_COMPLETED`，
  设备重启后自动走同一路径拉起服务，并重新注册看门狗（重启会清空 AlarmManager 的 alarm）。
- **看门狗**：每次收到广播都用 `AlarmManager.setRepeating(ELAPSED_REALTIME_WAKEUP, 60s)` 注册/续期一条
  显式 `com.dfm.probe.WATCHDOG` 广播，定时自检重新拉起服务，兜住 `START_STICKY` 未生效的回收场景；
  `FLAG_UPDATE_CURRENT` 保证不会堆出多条 alarm。
- Android 8+ **优先普通 `startService` 静默拉起**（alarm/广播的临时白名单放行，全程零通知）；
  被系统后台限制拒绝（`IllegalStateException`）时才回退 `startForegroundService`，更低版本直接 `startService`；
  启动仍被拒绝时记日志，下一轮看门狗重试。
- **通知彻底不显示**：静默路径完全不挂前台服务；仅 FGS 回退路径会在启动瞬间 `startForeground` +
  立即 `stopForeground`（通知只闪现毫秒级，且渠道为 `IMPORTANCE_NONE` 的 `dfm_probe_none`，
  创建时删除旧渠道 `dfm_probe`、`dfm_probe_min`）。实测真机（Android 11 rockchip）通知栏全程无记录。
- 广播**可重复、幂等**：`running`（static）标志保证只有尚未监听时才起监听线程，不会重复 bind 端口。
- 服务返回 `START_STICKY`：进程被系统回收后自动重建（`onStartCommand` 收 `intent=null`，本服务不读 intent）。
- **监听线程跨服务生命周期存活**：系统约每 60 秒按 "app idle" 停掉后台服务（只回收 ServiceRecord，进程不死），
  `running` / `serverSocket` 为 static、`onDestroy` 不关端口，监听线程继续 accept，端口不中断；
  进程被真正杀死时看门狗广播会拉起新进程与监听线程（实测 `am kill` 后 ≤60s 自愈）。
- 清单：`minSdk 23` / `targetSdk 30`，权限 `INTERNET` + `FOREGROUND_SERVICE` + `RECEIVE_BOOT_COMPLETED`，
  `allowBackup=false`，服务 `exported=false`，接收器 `exported=true` 且过滤
  `com.dfm.probe.START` / `android.intent.action.BOOT_COMPLETED` / `com.dfm.probe.WATCHDOG`。

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
| `0x02` | CMD_DEVICE_INFO | `{}`（无必填字段） | 统一信封，`data` 为设备基础信息 + 串口 / 摄像头 / 外设列表 | 设备端采集详细信息，见 §4.3 |
| `0x03` | CMD_SERIAL_READ | `{}`（会话已打开时取增量字节） | 统一信封，`data` 为 `{"hex":"01 02 03","text":"...","bytes":N}` | 从串口会话读缓冲取增量字节，见 §4.4 |
| `0x04` | CMD_SERIAL_OPEN | `{"serial":"/dev/ttyS3","baud":9600,"parity":"none","dataBits":8,"stopBits":1}` | 统一信封，`data` 为打开参数与 `stty` 命令 | 打开串口并起后台读线程，见 §4.4 |
| `0x05` | CMD_SERIAL_SEND | `{"mode":"text","payload":"AT","times":1,"interval":500}` 或 `{"stop":true}` | 统一信封，`data` 为 `{mode,bytes,times,interval,loop}` 或 `{stopped}` | 单次/循环发送或停止循环，见 §4.4 |
| `0x07` | CMD_SERIAL_CLOSE | `{}` | 统一信封，`data` 为 `{"closed":bool}` | 停读线程与循环线程、关串口，见 §4.4 |
| `0x08` | CMD_SHELL | `{"command":"...","root":false,"timeout":30}` | 统一信封，`data` 为 `{stdout,stderr,exit,truncated}` | 设备端执行 shell 并回传输出（root 经 `su 0 sh -c`），见 §4.5 |
| `0x09` | CMD_SCREENSHOT | `{}`（无必填字段） | 统一信封，`data` 为 `{path,size,via}` | `screencap` 截屏存应用外部目录（直连被拒自动回退 `su`），见 §4.5 |
| `0x0A` | CMD_CAMERA | `{}` 或 `{"cameraId":"0"}` | 统一信封，`data` 为 `{path,size,cameraId,width,height}` | camera2 无预览拍照存应用外部目录（需 PC 端 pm grant + GhostActivity），见 §4.5 |
| `0x0B` | CMD_ACCESSIBILITY_SETTINGS | `{}`（无必填字段） | `{"code":200,"message":"success","data":{"enabled":bool,"started":bool}}` | 未启用无障碍服务时跳转系统设置页，已启用直接返回不跳页，见 §4.6 |
| `0x0C` | CMD_ACCESSIBILITY_TREE | `{}`（无必填字段） | 统一信封，`data` 为 `{package,nodeCount,truncated,root}` | 返回无障碍服务采集的当前窗口组件树（节点上限 150，超出置 truncated），见 §4.6 |
| `0x0D` | CMD_ACCESSIBILITY_CLICK | `{"id":"","text":"","desc":""}`（至少一项） | 统一信封，`data` 为 `{clicked,id,text,desc}` | 按条件匹配组件树节点并执行 ACTION_CLICK（真无障碍点击，不走坐标注入），见 §4.6 |
| 其它任意值 | 未知命令 | 任意 JSON 对象 | `{"code":400,"message":"unknown cmd: 0x..","data":null}`（命令字 `0xFF`） | 明确报错，连接不断开 |

> 旧版 `0x01`~`0x05`、`0x07`（PING / ECHO / TTS / INFO / QUIT / DEVICE_INFO）业务命令字已全部删除，
> 本版为破坏性不兼容变更，PC 侧脚本、service 路由与前端页面须一起升级。
> `0x02` / `0x82` 为新增的设备详细信息命令字/响应字。
> `0x03` / `0x83` 为串口读取（取会话增量）命令字/响应字；`0x04` / `0x84`、`0x05` / `0x85`、
> `0x07` / `0x87` 为新增的串口会话打开 / 发送 / 关闭命令字/响应字。
> `0x08` / `0x88`、`0x09` / `0x89`、`0x0A` / `0x8A` 为 v2.5 新增的 shell 执行 / 屏幕截图 / 摄像头拍照
> 命令字/响应字。`0x0B` / `0x8B`、`0x0C` / `0x8C` 为 v2.6 新增的无障碍设置页跳转 / 组件树命令字/响应字；
> `0x0D` / `0x8D` 为 v2.7 新增的无障碍点击命令字/响应字。

设备→PC 的通知类命令字：`0x06` ACK、`0x80` ~ `0x85` / `0x87` ~ `0x8D` 数据响应帧、`0xFF` 错误帧。
业务执行抛出未预期异常时回 `{"code":500,"message":"execute failed: ...","data":null}`，不会崩服务。

### 4.3 设备详细信息采集（`0x02`）

- 设备端 APK 直接采集，不依赖 PC 侧 `adb shell`：基础信息取自 `android.os.Build` 与
  `getprop ro.serialno` / `ro.build.date`、`/proc/version`；列表取自 `/dev` 目录、
  `CameraManager`、`InputManager` / `UsbManager`。
- 返回 `data` 含 12 项：`androidVersion` / `sdk` / `model` / `manufacturer` / `device` /
  `displayVersion` / `sn` / `buildDate` / `kernelVersion` / `serialPorts` / `cameras` /
  `peripherals`。字段类型与元素结构见 `接口文档.md`「3.3」。
- `serialPorts` 过滤 `ttyS*` / `ttyUSB*` / `ttyACM*` 并排序；`cameras` 给出 `id` / `facing` /
  `resolution`；`peripherals` 含 `input` 与 `usb` 两类（`type` / `id` / `name`，USB 另有
  `vendorId` / `productId`）。
- **单项采集失败降级**：`getprop` / `/proc/version` 不可读回 `Unknown`，列表不可读回空数组，
  摄像头特性读取失败回 `unknown`，互不影响，整帧仍为 `code:200`。
- 仅采集整体抛异常时回 `{"code":500,"message":"device info failed: ...","data":null}`。

### 4.4 串口会话（`0x04` / `0x03` / `0x05` / `0x07`）

按 `串口助手-反编译说明.md` 的透传逻辑：设备端不做任何帧解析，`read()` 返回多少原样显示多少，
发送侧把输入原样写出；Hex 与 Txt 两种展示由页面选择。设备端只保持**一个**串口会话。

- `0x04 CMD_SERIAL_OPEN`：参数 `serial`（必填非空、须为纯设备路径）、`baud`（13 个支持值内：
  1200/2400/4800/9600/19200/38400/57600/115200/230400/460800/921600/1500000/3000000）、
  `parity`（`none` / `odd` / `even`，默认 `none`）、`dataBits`（5/6/7/8，默认 8）、
  `stopBits`（1/2，默认 1）。先 `stty -F <serial> <baud> cs<N> [-]parenb [parodd] [-]cstopb raw -echo`
  配串口（需 root），再 `RandomAccessFile(serial,"rw")` 打开并起后台读线程持续读；重复 OPEN 先关旧会话。
  返回打开参数与 `stty` 命令。
- `0x03 CMD_SERIAL_READ`：不带参数，从会话读缓冲**取增量并清空**，返回 `{"hex","text","bytes"}`；
  `hex` 为空格分隔大写，`text` 为 UTF-8 解码文本。缓冲上限 65536 字节，溢出丢最旧字节。
  未 OPEN 回 `400 serial session not opened`。PC 轮询此命令字实现「打开串口持续读」。
- `0x05 CMD_SERIAL_SEND`：参数 `mode`（`text` / `hex`）、`payload`、`times`（默认 1，
  `0` = 持续到手动停止，上限 10000）、`interval`（毫秒，默认 500，上限 60000）。`times == 1`
  同步写出一帧；否则起设备端后台线程定时写出并立即回响应。`{"stop":true}` 停止循环（幂等）。
  hex 载荷奇数位 / 非法字符 / 空串均回 `400`。
- `0x07 CMD_SERIAL_CLOSE`：停读线程与循环发送线程、关串口、清缓冲，返回 `{"closed":bool}`；
  服务 `onDestroy` 也会关闭会话。
- 参数错误回 `{"code":400,...}`；`stty` 配置失败、`open` 被拒或线程异常回
  `{"code":500,"message":"serial cmd failed: ...","data":null}`。
- **权限依赖**：需设备已 root 且串口节点可读写；Android 设备的 `stty` 是否支持
  `csN` / `parenb` / `parodd` / `cstopb` 选项、以及 `RandomAccessFile.read` 阻塞时 CLOSE 的行为
  均未真机验证。参数规范见 `接口文档.md`「3.4」。

### 4.5 Shell 执行 / 屏幕截图 / 摄像头拍照（`0x08` / `0x09` / `0x0A`，v2.5）

- **`0x08 CMD_SHELL`**：参数 `command`（必填非空）、`root`（默认 `false`，`true` 经
  `su 0 sh -c <command>`，本设备 su 不支持 `-c`）、`timeout`（秒，默认 30，范围 1..120，
  超时强杀 `exit=-1`）。返回 `{stdout, stderr, exit, truncated}`，stdout/stderr 各按字节
  截断至 10000。空白命令回 `400 command is required`。
- **`0x09 CMD_SCREENSHOT`**：`screencap -p <应用外部目录>/shot_*.png`。先应用身份直连，
  失败自动回退 `su 0 screencap -p`（真机实测直连被静默拒绝、回退成功），返回
  `{path, size, via: direct|su}`；两条路径均失败回 `500 screencap failed (direct and su)`。
- **`0x0A CMD_CAMERA`**：camera2 + ImageReader 无预览拍照（JPEG 画质 95，取最大分辨率，
  open 5s / configure 5s / 成像 15s 超时），存 `photo_*.jpg`，返回
  `{path, size, cameraId, width, height}`。缺省取后置，无后置取第一个。
- **拍照前置条件（Android 11 while-in-use 限制）**：probe 的前台服务从后台 START 广播拉起，
  系统不授予 CAMERA capability，CameraService 直接拒 `CAMERA_DISABLED "disabled by policy"`
  （logcat: `Foreground service started from background can not have ... camera access`）。
  解决：PC 端（`DFM-service` 的 `/api/probe/capture`）先 `am start -n com.dfm.probe/.GhostActivity`
  拉起透明辅助 Activity（`GhostActivity.java`：不拦截触摸、不抢焦点、覆盖锁屏、停留 15s 自动
  finish），进程短暂回到 TOP 获得 CAMERA capability 后再发 0x0A；Activity 自动退出，用户无感知。
  CAMERA 运行时权限同样由 PC 端 `pm grant` 预授予（`AndroidManifest.xml` 已声明，probe 不弹授权框）。
- 文件统一存 `getExternalFilesDir(null)`（`/sdcard/Android/data/com.dfm.probe/files/`），
  app 无需存储权限；PC 经 adb（root）读取，前端经 `/api/device/files/base64` 拉回预览。
- **真机验证**（2026-11-15，rk3568_r Android 11）：心跳 / shell 应用与 root 身份 / 空白命令 400 /
  截图（via=su, PNG 800x1280）/ 拍照（GhostActivity 窗口内，JPEG 1920x1080 745KB）全部通过。

### 4.6 无障碍设置页 / 组件树 / 无障碍点击（`0x0B` / `0x0C` / `0x0D`，v2.6~v2.7）

- **`0x0B CMD_ACCESSIBILITY_SETTINGS`**：先检测 `DfmAccessibilityService` 是否已启用——未启用才
  `startActivity(Settings.ACTION_ACCESSIBILITY_SETTINGS)` + `FLAG_ACTIVITY_NEW_TASK` 把系统无障碍设置页
  拉到前台（`{enabled:false, started:true}`）；已启用不跳页（`{enabled:true, started:false}`）。
  probe 无界面，无障碍权限无法静默授予，只能由用户在设置页手动启用。
- **`0x0C CMD_ACCESSIBILITY_TREE`**：经新增的 `DfmAccessibilityService`（manifest 注册
  `BIND_ACCESSIBILITY_SERVICE` + `res/xml/accessibility_service_config.xml`，`canRetrieveWindowContent=true`）
  取 `rootInActiveWindow` 递归序列化，返回 `{package, nodeCount, truncated, root}`；
  节点字段 `{class, text, desc, id, bounds, clickable, scrollable, enabled, children}`。
  节点数上限 150（`MAX_TREE_NODES`），达到上限停止展开并置 `truncated:true`，防止响应超出单帧 65535。
- **`0x0D CMD_ACCESSIBILITY_CLICK`**：按 `id`（viewIdResourceName 精确相等）/ `text`（包含）/
  `desc`（包含）匹配当前窗口组件树节点（至少填一项，多项 AND；深度优先取首个命中，节点数上限
  150 同 `0x0C`），命中节点自身不可点击时向上回溯最近可点击祖先，对其执行
  `performAction(ACTION_CLICK)`——真无障碍点击，不走坐标注入，可命中不可见/被遮挡控件。
  成功回 `{clicked:true, id, text, desc}`（条件原样回显）；未命中回 `404 无障碍树未找到节点（…）`；
  节点无可点击祖先视为未命中；服务未启用保底拉起设置页（同 `0x0C`）。
- **前置条件**：`0x0C` / `0x0D` 需设备端已启用「DFM Probe」无障碍服务；未启用时保底自动拉起无障碍设置页
  （同 `0x0B`），并回 `500 accessibility service not enabled (已在设备上打开无障碍设置页…)`；
  无活动窗口回 `500 no active window for accessibility`。
- **调用方式**：前端「探针工具 → 无障碍设置页 / 无障碍组件树」按钮，走 `POST /api/probe/message`
  发 `cmd:11` / `cmd:12`；组件树在页面结果区按层级缩进显示；`0x0D` 由模拟脚本
  「无障碍点击（accClick）」步骤自动调用（`cmd:13`），也可在探针工具页手工发帧验证。

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

七步流程：检查设备在线（`adb get-state`）→ `adb install -r` → 发 START 广播 →
建立 TCP 连接（USB 用 `bind(127.0.0.1, 0)` 取随机本地端口做 forward，结束时 `--remove`；
网络直接连 `<IP>:21104`）→ 心跳 / 设备详细信息 / 串口会话四命令字参数校验 / TTS / 边界命令字测试 →
关闭连接。
START 广播是异步的，脚本用 10 次 × 0.3 秒的连接重试等待服务 bind 完成。

实现的协议能力：CRC16-Modbus 组帧/增量解帧（粘包、半包、CRC 错帧重新找帧头）、
**停等 ACK 重传**（T=200ms、最多共发 4 次、窗口 1）、帧序 0~255 循环、同帧序响应帧等待、
组帧前 65535 字节拒绝。

覆盖的测试项：组帧 65536 字节拒绝、CMD_HEARTBEAT / CMD_DEVICE_INFO / 串口会话四命令字
（`0x03` / `0x04` / `0x05` / `0x07`）参数校验 / CMD_TTS 成功应答、TTS 缺 text /
非字符串 text / 空文本 / 未知命令、CRC 错帧丢弃且无应答。
串口会话在脚本里只覆盖参数校验（未 OPEN 读取/发送、`0x04` 五参数、`0x05` 载荷与次数/间隔边界、
`0x07` / `stop` 幂等），真机打开串口、持续读与循环发送依赖硬件可达，不在确定性脚本里断言。
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

  `/api/probe/message` 的 `cmd` 接受 `0`~`255`（`0x00`~`0xFF`）任意整数，越界回 422；
  已登记的 `0` / `1` / `2` / `3` / `4` / `5` / `7` 返回业务应答（其中 `3` / `4` / `5` / `7` 为串口会话
  四命令字），其余命令字由设备端回 `0xFF CMD_ERROR`。
  `data` 必须为 JSON 对象、数据区超 65535 字节明确失败；每次请求都发幂等 START 广播；
  同一设备按 `deviceId` 加锁串行化；`ACK_TIMEOUT=0.2`、`MAX_RETRY=3`、`RESPONSE_TIMEOUT=120.0`。
- 前端 `DFM-frontend/src/menu/App_Probe.vue`「探针测试」页：左侧选设备，右侧测试区
  （安装并通讯 / 快捷命令 心跳·设备详细信息 / 串口会话：0x04 打开 / 0x03 取增量 /
  0x05 单次·循环发送 / 0x07 关闭 / TTS 朗读 / 自定义发送：自定义 0x00~0xFF 命令字与 JSON 数据区），
  下方展示请求、完整应答与帧 hex；
  底部「帧日志」累积记录时间 / 标题 / 设备 / 发送帧 / ACK / 响应帧三段真实字节 hex（最新在上、可滚动、可清空）。
- `DFM-frontend/src/menu/App_Utils.vue` 串口 tab 方案 C「串口会话」：0x04 打开设备端会话后
  页面轮询 0x03 取增量字节，按 Hex/Txt 原样追加执行日志，并支持 0x05 单次/循环发送、0x07 关闭。
- `App_Probe.vue` 与 `App_Utils.vue` 设备项的状态圆点：绿=通讯正常、红=失败、橙=处理中、灰=未检测，
  点击圆点=安装并重新通讯；设备列表刷新时只对**新出现**的设备自动调 `/api/probe/connect`，不重复安装。

---

## 8. 已知边界 / 暂不支持

明确列出，避免把验证工程当成完整控制能力：

- **只实现通讯 + 系统 TTS 朗读 + 设备信息采集 + 串口会话**：root shell、输入注入、截屏、文件传输、应用管理等控制能力**尚未实现**；
  `0x02` 的设备信息列表依赖真机系统节点/服务可读性，无真机时未做端到端验证。
  `0x03` / `0x04` / `0x05` / `0x07` 串口会话依赖设备已 root 且串口节点可读写；设备端 `stty` 的
  `csN` / `parenb` / `parodd` / `cstopb` 选项支持情况、读线程阻塞时 CLOSE 的行为均未真机验证。
  设备端会话为服务级单例，页面切换设备/方案只停 PC 轮询、不自动发 `0x07`，需手动关闭。
- **不做拆帧**：单帧数据区最多 65535 字节（长度字段 2 字节、低字节在前），超长判失败不截断，不支持自动分片。
- **不做多帧并发**：连接内串行（窗口 1）；TTS 朗读期间同步等待 `onDone`，最长占用该连接线程 60 秒，
  期间不处理该连接上的其它请求（其它连接与 `accept` 不受影响）。
- **force-stop 后无法自动恢复**：开机自启 + `START_STICKY` + 看门狗能覆盖设备重启与系统回收，
  但 force-stop 会同时清掉 AlarmManager 的 alarm 与所有广播，只能由 PC 重新发 START 广播恢复；
  这是 Android 系统限制。Doze / 电池优化白名单需部署时用
  `adb shell dumpsys deviceidle whitelist +com.dfm.probe` 设置，部分 ROM 的自启动管理也可能拦截开机广播。
  以上开机自启与看门狗效果尚未实机验证。
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
