# DFM Probe

设备端 agent 的最小验证工程：ADB 装上一个 APK，PC 程序把「本机地址」发给它，
APK 据此**主动回连 PC** 建立 TCP 长连接，双向收发 JSON。用于验证「纯 ADB 不够用、
改用设备端常驻程序」这条路线是否走得通。

**本目录完全独立于 `DFM-service`，不修改后端任何代码。**

## 目录

```
DFM-probe/
├── AndroidManifest.xml          # 权限、receiver、前台服务声明
├── java/com/dfm/probe/
│   ├── ConnectReceiver.java     # 接收 host/port，拉起 ProbeService
│   └── ProbeService.java        # 前台服务：连回 PC + 按行收发 JSON
├── res/                         # app_name / 通知栏小图标
├── build.ps1                    # 不用 Gradle，直接调 SDK build-tools 打包签名
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
  │ ──> {"type":"quit"}                  │
  │ <── {"type":"bye"}                   │
```

协议：UTF-8、换行分隔的 JSON。设备端连上后先发 `hello`；
支持 `ping`/`echo`，其它 type 返回 `{"type":"error","message":...}`。
断线后 APK 每 3 秒自动重连；PC 重新广播新地址会打断旧连接并改用新地址。

## 联调测试

```powershell
cd D:\uTools\DFM-probe
python pc/probe_test.py --device 192.168.0.59:5555
```

`--host` 可手动指定发给设备的地址（默认用 UDP connect 自动探测本机出口 IP）。
若 Windows 防火墙拦住入站，用 `--reverse` 走 `adb reverse`（设备连
`127.0.0.1:<port>`，由 adbd 转发回 PC）。

## 已知边界

- 服务返回 `START_NOT_STICKY`：进程被系统杀掉后不会自行恢复，需要 PC 重新广播地址。
- 一次只服务一个连接；连接断开后进入重连循环。
- 当前只做「通讯」验证，尚未实现 root shell、输入注入、截屏等控制能力。
- 通知常驻（前台服务），Android 8+ 需要通知渠道，本工程已处理。
