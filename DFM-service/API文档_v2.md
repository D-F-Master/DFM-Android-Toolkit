# 工具通用服务器 API 文档 v2.0

## 基础信息

- **服务地址**: `http://localhost:3000`
- **版本**: 2.0.0
- **框架**: FastAPI
- **启动命令**: `python main.py`
- **自动文档**: 启动后可访问 `http://localhost:3000/docs` 查看 Swagger UI

## 统一规范

### 请求方式
所有接口统一使用 **POST** 方法，参数放入请求体（JSON格式）。

### 返回格式
所有接口统一返回以下格式：

**成功响应**:
```json
{
  "code": 200,
  "message": "success",
  "data": {...}
}
```

**失败响应**:
```json
{
  "code": 400/500,
  "message": "错误信息",
  "data": null
}
```

**状态码说明**:
- `200`: 成功
- `400`: 请求参数错误
- `500`: 服务器内部错误

---

## 一、ADB工具接口

### 1. 获取设备列表
**接口**: `POST /api/devices`  
**描述**: 获取所有已连接的ADB设备列表  
**请求体**: 无  
**响应**:
```json
{
  "code": 200,
  "message": "success",
  "data": [
    {"deviceId": "xxx", "model": "xxx"}
  ]
}
```

### 2. 获取设备信息
**接口**: `POST /api/device/info`  
**描述**: 获取指定设备的详细信息  
**请求体**:
```json
{"deviceId": "device_serial_number"}
```
**响应**:
```json
{
  "code": 200,
  "message": "success",
  "data": {
    "status": "在线",
    "model": "设备型号",
    "version": "系统版本",
    "manufacturer": "制造商",
    "deviceName": "设备名称",
    "displayVersion": "版本信息/构建ID",
    "androidVersion": "安卓版本",
    "kernelVersion": "4.19.191 #4 SMP PREEMPT Fri Mar 6 15:26:02 CST 2026",
    "buildDate": "构建日期",
    "codename": "设备代号",
    "unlockStatus": "解锁状态（已解锁/已锁定/未锁定）"
  }
}
```
**status说明**:
- `在线`: 设备在线，已授权
- `离线`: 设备离线
- `未授权`: 设备未授权（需要在设备上确认调试权限）

### 3. 执行自定义ADB命令
**接口**: `POST /api/command`  
**描述**: 执行任意ADB命令（慎用）  
**请求体**:
```json
{"command": "adb devices"}
```
**响应**:
```json
{
  "code": 200,
  "message": "success",
  "data": {"stdout": "输出", "stderr": ""}
}
```

### 4. 获取应用列表
**接口**: `POST /api/device/apps`  
**描述**: 获取设备上安装的第三方应用列表  
**请求体**:
```json
{"deviceId": "device_serial_number"}
```
**响应**:
```json
{
  "code": 200,
  "message": "success",
  "data": ["com.example.app1", "com.example.app2"]
}
```

### 5. 获取应用详细信息
**接口**: `POST /api/device/app/info`  
**描述**: 获取指定应用的版本信息和名称  
**请求体**:
```json
{
  "deviceId": "device_serial_number",
  "packageName": "com.example.app"
}
```
**响应**:
```json
{
  "code": 200,
  "message": "success",
  "data": {
    "packageName": "com.example.app",
    "versionName": "1.0.0",
    "versionCode": "1",
    "appName": "应用名称"
  }
}
```

### 6. 安装应用
**接口**: `POST /api/device/app/install`  
**描述**: 安装APK文件到设备  
**Content-Type**: `multipart/form-data`  
**表单参数**:
- `deviceId`: 设备ID
- `apk`: APK文件

**响应**:
```json
{"code": 200, "message": "success", "data": null}
```

### 7. 卸载应用
**接口**: `POST /api/device/app/uninstall`  
**请求体**:
```json
{
  "deviceId": "device_serial_number",
  "packageName": "com.example.app"
}
```

### 8. 启动应用
**接口**: `POST /api/device/app/start`  
**请求体**:
```json
{
  "deviceId": "device_serial_number",
  "packageName": "com.example.app",
  "activity": "com.example.MainActivity"
}
```
**说明**: activity可选

### 9. 停止应用
**接口**: `POST /api/device/app/stop`  
**请求体**:
```json
{
  "deviceId": "device_serial_number",
  "packageName": "com.example.app"
}
```

### 10. 提取APK
**接口**: `POST /api/device/app/extract`  
**请求体**:
```json
{
  "deviceId": "device_serial_number",
  "packageName": "com.example.app",
  "localPath": "D:/output/app.apk"
}
```

### 11. 获取应用详情
**接口**: `POST /api/device/app/details`  
**请求体**:
```json
{
  "deviceId": "device_serial_number",
  "packageName": "com.example.app"
}
```

### 12. 浏览文件系统
**接口**: `POST /api/device/files/browse`  
**请求体**:
```json
{
  "deviceId": "device_serial_number",
  "path": "/sdcard"
}
```
**响应**:
```json
{
  "code": 200,
  "message": "success",
  "data": {
    "files": [
      {
        "name": "file.txt",
        "path": "/sdcard/file.txt",
        "isDirectory": false,
        "size": "1024",
        "permissions": "-rw-rw----"
      }
    ],
    "currentPath": "/sdcard"
  }
}
```

### 13. 创建文件夹
**接口**: `POST /api/device/files/mkdir`  
**请求体**:
```json
{
  "deviceId": "device_serial_number",
  "path": "/sdcard/new_folder"
}
```

### 14. 创建文件
**接口**: `POST /api/device/files/create`  
**描述**: 在设备上创建空文件  
**请求体**:
```json
{
  "deviceId": "device_serial_number",
  "path": "/sdcard/new_file.txt"
}
```
**响应**:
```json
{"code": 200, "message": "success", "data": null}
```
**说明**:
- 文件已存在时不报错，`touch` 会更新其时间戳（与 `mkdir -p` 的幂等行为一致）
- 父目录不存在时返回 code 500，message 为 adb 原始错误（如 `No such file or directory`）
- `path` 为空返回 code 400
- 文件名可包含空格：路径以单引号包裹后传给设备 shell

### 15. 删除文件或文件夹
**接口**: `POST /api/device/files/delete`  
**请求体**:
```json
{
  "deviceId": "device_serial_number",
  "path": "/sdcard/file.txt"
}
```

### 16. 上传文件
**接口**: `POST /api/device/files/upload`  
**描述**: 上传文件到设备  
**Content-Type**: `multipart/form-data`  
**表单参数**:
- `deviceId`: 设备ID（必填）
- `file`: 文件（必填）
- `remotePath`: 远程路径（可选，默认"/sdcard/"）

### 17. 下载文件
**接口**: `POST /api/device/files/download`  
**描述**: 从设备下载文件到本地  
**请求体**:
```json
{
  "deviceId": "device_serial_number",
  "remotePath": "/sdcard/file.txt",
  "localPath": "D:/download/file.txt"
}
```
**说明**:
- 如果 `localPath` 指向的目录不存在，会自动创建
- 如果路径以 `/` 或 `\` 结尾，或没有文件扩展名，则视为目录

### 18. 读取文本文件
**接口**: `POST /api/device/files/read`  
**描述**: 读取设备上的文本文件全文  
**请求体**:
```json
{
  "deviceId": "device_serial_number",
  "remotePath": "/sdcard/log/ztl_dmesg.log"
}
```
**响应**:
```json
{
  "code": 200,
  "message": "success",
  "data": {
    "path": "/sdcard/log/ztl_dmesg.log",
    "content": "文件全文文本"
  }
}
```
**说明**:
- `remotePath` 为空返回 code 400；文件不存在、路径是目录或设备离线返回 code 500，message 为 adb 原始错误
- 1MB 文本实测约 0.2 秒返回，更大的文件建议改用下载接口
- 本接口已实现，正常情况下不会返回 code 404；前端可约定：请求返回 404（旧版后端无此路由）时回落到其他读取方案
- 仅适合文本文件：读取二进制文件不报错，但内容经 UTF-8 解码后为乱码（含 U+FFFD 替换符），不会截断报错
- adb pty 若把换行转成 `\r\n`，服务端会统一还原为 `\n`（换行规范化的已知取舍）

### 19. 按键模拟
**接口**: `POST /api/device/keyevent`  
**描述**: 发送Android keycode事件  
**请求体**:
```json
{
  "deviceId": "device_serial_number",
  "keycode": 26
}
```

### 20. 基本按键
**接口**: `POST /api/device/key`  
**描述**: 发送常用按键事件  
**请求体**:
```json
{
  "deviceId": "device_serial_number",
  "key": "home"
}
```
**支持的key值及对应功能**:
- `power`: 电源键 (KEYCODE_POWER)
- `volume_up`: 音量加 (KEYCODE_VOLUME_UP)
- `volume_down`: 音量减 (KEYCODE_VOLUME_DOWN)
- `back`: 返回键 (KEYCODE_BACK)
- `home`: Home键 (KEYCODE_HOME)
- `menu`: 菜单键 (KEYCODE_MENU)
- `recent`: 最近任务 (KEYCODE_APP_SWITCH)
- `camera`: 相机键 (KEYCODE_CAMERA)
- `search`: 搜索键 (KEYCODE_SEARCH)

### 21. 启动投屏
**接口**: `POST /api/device/scrcpy`  
**描述**: 启动scrcpy投屏工具  
**请求体**:
```json
{"deviceId": "device_serial_number"}
```
**注意**: 需要确保 scrcpy 已安装并添加到系统环境变量

### 22. 打开日志窗口
**接口**: `POST /api/device/logs`  
**描述**: 在新窗口中打开logcat日志，自动过滤当前活动应用的日志  
**请求体**:
```json
{"deviceId": "device_serial_number"}
```

### 23. 打开Shell窗口
**接口**: `POST /api/device/shell`  
**描述**: 在新窗口中打开ADB Shell交互界面  
**请求体**:
```json
{"deviceId": "device_serial_number"}
```

### 24. 控制音量
**接口**: `POST /api/device/volume`  
**描述**: 控制设备音量（增加/减小/静音/取消静音）  
**请求体**:
```json
{
  "deviceId": "device_serial_number",
  "action": "up",
  "stream": "media"
}
```
**参数说明**:
- `deviceId`: 设备ID（必填）
- `action`: 操作类型（必填），支持：
  - `up`: 音量+（模拟音量加按键）
  - `down`: 音量-（模拟音量减按键）
  - `mute`: 静音（尝试多种方式：service call > settings > 设置音量为0）
  - `unmute`: 取消静音（尝试多种方式：service call > settings > 恢复音量）
- `stream`: 音频流类型（可选，默认"media"），支持：
  - `media`: 媒体音量
  - `ring`: 铃声音量
  - `alarm`: 闹钟音量
  - `notification`: 通知音量

**注意**:
- 静音功能会尝试多种方式实现，优先级：
  1. 使用 Android service call 直接调用音频服务（需要系统权限）
  2. 使用 settings 设置静音标志位
  3. 将音量值设置为0
- 部分设备可能需要 root 权限或系统签名才能完全静音
- 取消静音时会尝试恢复之前的音量状态

**响应**:
```json
{
  "code": 200,
  "message": "操作成功",
  "data": {
    "currentVolume": 5,
    "maxVolume": 15,
    "isMuted": false,
    "stream": "media"
  }
}
```

### 25. 获取音量状态
**接口**: `POST /api/device/volume/status`  
**描述**: 获取设备当前音量状态  
**请求体**:
```json
{
  "deviceId": "device_serial_number",
  "stream": "media"
}
```
**参数说明**:
- `deviceId`: 设备ID（必填）
- `stream`: 音频流类型（可选，默认"media"），支持：media, ring, alarm, notification

**响应**:
```json
{
  "code": 200,
  "message": "获取成功",
  "data": {
    "currentVolume": 5,
    "maxVolume": 15,
    "isMuted": false,
    "stream": "media"
  }
}
```

---

### 26. 断开全部ADB连接
**接口**: `POST /api/device/disconnect/all`  
**描述**: 断开所有通过网络连接的ADB设备（deviceId 形如 ip:端口），逐台并发执行 adb disconnect  
**请求体**: 无（POST 空请求体 `{}` 即可）

**响应**:
```json
{
  "code": 200,
  "message": "success",
  "data": {
    "total": 2,
    "successCount": 2,
    "failedCount": 0,
    "results": [
      {
        "deviceId": "192.168.1.2:5555",
        "success": true,
        "message": "already disconnected"
      },
      {
        "deviceId": "192.168.1.10:5555",
        "success": true,
        "message": "disconnected 192.168.1.10:5555"
      }
    ]
  }
}
```
**说明**:
- 仅断开网络设备（deviceId 含端口号），USB 设备为物理连接无法远程断开，自动跳过
- 设备离线且已被移出设备列表时，视为断开成功（幂等）
- 结果按 IP 数值排序
- 无网络设备时返回 `total: 0`、`results: []`，code 仍为 200

---

### 27. 获取设备性能快照
**接口**: `POST /api/device/monitor`  
**描述**: 获取设备内存占用、CPU 频率、磁盘使用情况  
**请求体**:
```json
{"deviceId": "device_serial_number"}
```

**响应**:
```json
{
  "code": 200,
  "message": "success",
  "data": {
    "cpu": {
      "coreCount": 4,
      "maxFreqKhz": 1992000,
      "cores": [
        {"core": 0, "currentKhz": 1992000},
        {"core": 1, "currentKhz": 1992000},
        {"core": 2, "currentKhz": 1992000},
        {"core": 3, "currentKhz": 1992000}
      ]
    },
    "memory": {
      "totalBytes": 2051616768,
      "usedBytes": 1560350720,
      "availableBytes": 491266048,
      "freeBytes": 53063680,
      "cachedBytes": 665911296,
      "usagePercent": 76.05
    },
    "disk": {
      "mountPoint": "/data",
      "totalBytes": 10547134464,
      "usedBytes": 978874368,
      "availableBytes": 9417265152,
      "usagePercent": 9.28
    }
  }
}
```

**字段说明**:
- `cpu.coreCount`: CPU 核心数
- `cpu.maxFreqKhz`: CPU 最高频率（kHz）
- `cpu.cores[].currentKhz`: 各核心当前频率（kHz）
- `memory.usagePercent`: 内存使用率（%），按 `(MemTotal - MemAvailable) / MemTotal` 计算
- `memory.cachedBytes`: 页缓存大小
- `disk`: 仅返回 `/data` 分区，无该分区时为 `null`

**说明**:
- 单次 adb 往返取回全部指标，实测约 0.2 秒
- 频率取自 `scaling_cur_freq`（调频策略当前值）。硬件真实频率 `cpuinfo_cur_freq` 需 root 权限，无权限时不可读，因此不使用
- 磁盘只统计 `/data`。`/`、`/vendor`、`/system_ext` 等只读分区常显示 100% 占用，属于正常现象，不作为磁盘使用指标上报
- CPU 占用率与网络速率需要采样，见下一接口

---

### 28. 获取设备实时速率
**接口**: `POST /api/device/monitor/rate`  
**描述**: 获取设备 CPU 占用率、网络上传/下载速率  
**请求体**:
```json
{
  "deviceId": "device_serial_number",
  "duration": 1
}
```
**参数说明**:
- `deviceId`: 设备ID（必填）
- `duration`: 采样时长，单位秒（可选，默认 1，范围 1-5）

**响应**:
```json
{
  "code": 200,
  "message": "success",
  "data": {
    "duration": 1,
    "cpu": {
      "usagePercent": 31.14
    },
    "network": [
      {
        "name": "wlan0",
        "rxBytes": 597981082,
        "txBytes": 73906267,
        "rxBytesPerSec": 21100.0,
        "txBytesPerSec": 348.0
      }
    ]
  }
}
```

**字段说明**:
- `duration`: 实际采样时长（秒）
- `cpu.usagePercent`: 采样区间内的 CPU 平均占用率（%）
- `network[].rxBytes` / `txBytes`: 网卡累计接收/发送字节数（自开机起）
- `network[].rxBytesPerSec` / `txBytesPerSec`: 采样区间内的平均速率（字节/秒）

**说明**:
- `/proc/stat` 与 `/proc/net/dev` 均为自开机起的累计值，单次读取只能得到总量，无法得到占用率与速率，因此采用两次采样求差值
- 采样在设备端 `sleep`，一次 adb 往返完成，避免两次往返之间被其他请求插队导致采样区间失真
- CPU 占用率算法：`(busy2 - busy1) / (total2 - total1) × 100`，其中 busy 为 total 减去 idle 与 iowait
- 仅上报有流量的网卡，开机至今收发均为 0 的虚拟网卡（`ip6_vti0`、`sit0`、未启用的 `eth0` 等）与回环 `lo`、`dummy0` 不上报
- 网卡计数器在网卡重启后会归零，速率按 `max(差值, 0)` 兜底，不会出现负数
- `duration` 超出 1-5 范围返回 code 400；采样会占用一个 adb 连接，限制上限避免并发拖垮 adb server
- 设备无 `/proc/stat` 或数据不可解析时返回 code 500

---

#### 性能监控展示建议（前端参考）

以下为接口字段与展示形式的对应建议，供前端实现参考，非接口约定。

**一、字段与展示形式对应**

`POST /api/device/monitor`（静态指标，变化慢）：

| 字段 | 建议展示 |
|------|----------|
| `memory.usagePercent` | 环形进度 / 仪表盘 + 百分比数字 |
| `memory.usedBytes` / `availableBytes` | 堆叠条（已用 + 可用 = 总量） |
| `memory.cachedBytes` | 辅助数值文本（页缓存，可折叠） |
| `disk.usagePercent` | 环形进度 / 仪表盘 + 百分比数字 |
| `disk.usedBytes` / `availableBytes` | 堆叠条 |
| `cpu.coreCount` / `cpu.maxFreqKhz` | 静态文本 |
| `cpu.cores[].currentKhz` | 每核柱状图（4 核即 4 根），或横向条形图 |

`POST /api/device/monitor/rate`（速率指标，需时间序列）：

| 字段 | 建议展示 |
|------|----------|
| `cpu.usagePercent` | 折线图（随时间滚动） |
| `network[].rxBytesPerSec` | 折线图（下载速率） |
| `network[].txBytesPerSec` | 折线图（上传速率，与下载同图双线或分图） |
| `network[].rxBytes` / `txBytes` | 累计总量，静态文本（自开机起） |

**二、单位换算**

- 字节：按 1024 进制换算为 KB / MB / GB（如 `2051616768` → `1.91 GB`）
- 频率：接口返回 kHz，展示时 ÷1000 转为 MHz（如 `1992000` → `1992 MHz`）
- 速率：接口返回字节/秒，展示时换算为 KB/s 或 MB/s

**三、轮询建议**

- `monitor/rate` 单次请求耗时约等于 `duration` + 0.2 秒（`duration=1` 时约 1.2 秒），轮询间隔建议 **不小于 2 秒**
- 必须防止请求堆积：上一次请求未返回时不要发起下一次（用标志位或 `AbortController`），否则 adb server 会因连接占满而变慢
- `monitor` 为静态指标，变化慢，建议 **5-10 秒** 或按需刷新，无需与 `rate` 同频
- 多个图表共用同一份 `rate` 响应即可，不要为每个图表单独请求

**四、空值处理（必须处理）**

以下情况接口会返回合法但为空的数据，前端需有对应兜底，不可直接取下标或做除法：

| 情况 | 接口表现 | 建议展示 |
|------|----------|----------|
| 设备无 `/data` 分区 | `disk` 为 `null` | 磁盘卡片显示"—"或隐藏 |
| 所有网卡累计流量为 0 | `network` 为空数组 `[]` | 显示"暂无网络流量" |
| 设备无 cpufreq 节点 | `cpu.cores` 为空数组、`cpu.maxFreqKhz` 为 `null` | 隐藏频率图表 |

**五、采样窗口说明**

`rate` 接口返回的是 `duration` 区间内的**平均值**，不是瞬时值。折线图上相邻两点之间若间隔大于 `duration`，中间区间未被覆盖。若需连续无缺口的时间序列，轮询间隔应等于 `duration`（但需满足上面"不小于 2 秒"的约束，即 `duration` 取 1-2 秒较合适）。

---

## 二、端口管理接口

### 1. 检查端口占用
**接口**: `POST /api/port/check`  
**描述**: 检查本地端口占用情况，返回使用该端口的进程信息  
**请求体**:
```json
{"port": "8080"}
```
**响应**:
```json
{
  "code": 200,
  "message": "success",
  "data": [
    {
      "protocol": "TCP",
      "localAddress": "0.0.0.0:8080",
      "foreignAddress": "0.0.0.0:0",
      "state": "LISTENING",
      "pid": "1234",
      "process": "java.exe",
      "processPath": "C:\\Program Files\\Java\\jdk-11\\bin\\java.exe"
    }
  ]
}
```

### 2. 终止进程
**接口**: `POST /api/port/kill`  
**描述**: 强制终止指定PID的进程  
**请求体**:
```json
{"pid": "1234"}
```

### 3. 扫描端口
**接口**: `POST /api/port/scan`  
**描述**: 扫描目标主机的端口开放状态  

**方式一：扫描端口范围**
**请求体**:
```json
{
  "target": "192.168.1.1",
  "startPort": 1,
  "endPort": 1000
}
```

**方式二：扫描指定端口列表**
**请求体**:
```json
{
  "target": "192.168.1.1",
  "ports": [22, 80, 443, 3306, 8080]
}
```

**响应**:
```json
{
  "code": 200,
  "message": "扫描成功",
  "data": [
    {
      "port": 80,
      "status": "open",
      "service": "HTTP"
    },
    {
      "port": 443,
      "status": "open",
      "service": "HTTPS"
    },
    {
      "port": 22,
      "status": "closed"
    }
  ]
}
```
**性能优化说明**:
- 自动根据扫描端口数量调整并发数和超时时间
- ≤100个端口：50并发，0.3秒超时
- ≤1000个端口：100并发，0.2秒超时
- >1000个端口：200并发，0.15秒超时
- 使用线程池并发扫描，大幅提升扫描速度

### 4. Telnet访问端口
**接口**: `POST /api/port/telnet`  
**描述**: 打开命令行窗口并执行telnet命令访问指定IP和端口  

**请求体**:
```json
{
  "target": "192.168.1.1",
  "port": 80
}
```

**参数说明**:
- `target`: 目标IP地址（必填）
- `port`: 目标端口号（必填）

**响应**:
```json
{
  "code": 200,
  "message": "success",
  "data": {
    "command": "telnet 192.168.1.1 80"
  }
}
```

**失败响应**:
```json
{
  "code": 500,
  "message": "执行失败: telnet命令不存在或未安装",
  "data": null
}
```

**功能说明**:
- 在Windows系统中打开新的CMD窗口
- 自动执行telnet命令连接到指定的IP和端口
- 如果telnet命令不可用，返回错误信息
- 命令格式：`telnet <target> <port>`

**注意事项**:
- Windows系统默认可能未启用telnet客户端，需要在"控制面板 > 程序和功能 > 启用或关闭Windows功能"中启用"Telnet客户端"
- 该接口会打开一个新的命令行窗口，不会在当前进程中执行
- 适用于快速测试端口连通性

---

## 三、IP工具接口

### 1. IP网段扫描
**接口**: `POST /api/ip/scan`  
**描述**: 扫描指定网段内在线的IP地址  
**请求体**:
```json
{
  "ip": "192.168.1.100",
  "subnetMask": "255.255.255.0"
}
```
**参数说明**:
- `ip`: 任意该网段内的IP地址
- `subnetMask`: 子网掩码（支持标准格式，如 255.255.255.0 或 CIDR 格式如 /24）

**响应**:
```json
{
  "code": 200,
  "message": "扫描完成",
  "data": {
    "total": 254,
    "onlineCount": 5,
    "onlineIps": [
      "192.168.1.1",
      "192.168.1.10",
      "192.168.1.100",
      "192.168.1.200",
      "192.168.1.254"
    ]
  }
}
```
**性能优化说明**:
- 使用线程池并发扫描，最多100个线程同时检测
- 单个IP检测超时时间0.5秒
- 自动按IP地址排序返回结果

### 2. 检查单个IP在线状态
**接口**: `POST /api/ip/check`  
**描述**: 检查指定IP是否在线  
**请求体**:
```json
{
  "ip": "192.168.1.100"
}
```
**响应**:
```json
{
  "code": 200,
  "message": "success",
  "data": {
    "ip": "192.168.1.100",
    "online": true
  }
}
```

### 3. 批量ADB连接
**接口**: `POST /api/ip/batch-connect`  
**描述**: 批量执行adb connect命令连接多个设备  
**请求体**:
```json
{
  "ips": [
    "192.168.1.100:5555",
    "192.168.1.101:5555",
    "192.168.1.102:5555"
  ]
}
```
**参数说明**:
- `ips`: IP地址数组，可以包含端口号（默认5555）

**响应**:
```json
{
  "code": 200,
  "message": "批量连接完成",
  "data": {
    "total": 3,
    "successCount": 2,
    "results": [
      {
        "ip": "192.168.1.100:5555",
        "success": true,
        "message": "connected to 192.168.1.100:5555"
      },
      {
        "ip": "192.168.1.101:5555",
        "success": true,
        "message": "connected to 192.168.1.101:5555"
      },
      {
        "ip": "192.168.1.102:5555",
        "success": false,
        "message": "unable to connect to 192.168.1.102:5555"
      }
    ]
  }
}
```
**性能优化说明**:
- 使用线程池并发执行adb connect，最多20个线程
- 每个连接有10秒超时保护
- 自动统计成功和失败数量
- 结果按IP地址排序

---

## 注意事项

1. **所有接口均使用POST方法**
2. **除文件上传外，所有参数均通过JSON请求体传递**
3. **统一返回格式**: `{"code": xxx, "message": "xxx", "data": ...}`
4. **ADB功能需要确保ADB已安装并添加到系统环境变量**
5. **端口管理功能仅在Windows系统下有效**
6. **文件上传接口需要使用 multipart/form-data 格式**
7. **scrcpy投屏需要预先安装scrcpy并配置环境变量**
8. **日志窗口和Shell窗口会在新的CMD窗口中打开**
9. **生产环境建议修改CORS配置，指定允许的域名**
10. **音量控制功能需要设备支持 settings 命令，部分设备可能需要 root 权限**
11. **Telnet访问需要在Windows系统中启用“Telnet客户端”功能**
12. **IP扫描功能使用TCP连接检测，超时时间0.5秒**
13. **批量ADB连接最多20个并发，避免ADB服务器压力过大**

---

## 更新日志

### v2.3.0
- 新增创建文件接口 `POST /api/device/files/create`
- 新增读取文件接口 `POST /api/device/files/read`

### v2.2.0
- 新增设备性能监控接口：
  - `POST /api/device/monitor` — 内存占用、CPU 频率、磁盘使用（单次往返，约 0.2 秒）
  - `POST /api/device/monitor/rate` — CPU 占用率、网络上传/下载速率（设备端采样，一次往返）
- 磁盘指标只统计 `/data`，排除显示 100% 占用的只读分区
- 网络速率只上报有流量的网卡，排除回环与虚拟网卡

### v2.1.0
- 新增断开全部ADB连接接口 `POST /api/device/disconnect/all`
- 断开接口支持幂等：设备已被移出设备列表时视为断开成功
- `POST /api/device/disconnect` 兼容离线设备断开

### v2.0.0
- 统一所有接口使用POST方法
- 统一返回格式
- 新增端口管理功能
- 新增音量控制功能（支持媒体、铃声、闹钟、通知音量）
- 优化ADB设备信息获取
- 支持自动过滤应用日志
- 端口扫描性能优化
- 新增Telnet访问端口功能
- 新增IP工具接口（网段扫描、单IP检测、批量ADB连接）
- IP工具采用多线程并发优化，提升扫描和连接速度
