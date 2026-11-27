# permissive su（测试设备专用替换 su）

## 背景

部分出厂测试机（如 YQSH rk3568，Android 13 userdebug）的 `/system/xbin/su` 是 **toybox su**，
内置只允许 `uid 0(root)` / `uid 2000(shell)` 提权 —— `adb shell` 可用，但普通应用
（如 DFM-probe）exec 后得到 `su: not allowed`，导致探针 camReplace/camRestore 报
`Cannot run program "su": error=13, Permission denied`（配合 `/system/xbin` 目录 0750 时）。

本目录的 `su`（源码 `su.c`）替换原 toybox su：setuid root、允许任意调用者提权，
兼容 toybox 用法 `su [WHO] [COMMAND...]`（探针固定用 `su 0 sh -c <script>`）。

仅限**出厂测试设备**使用（任何应用可静默提权，绝不能装在个人手机上）。

## 部署步骤（adb 已 root 时）

```bash
# 编译（NDK 29）
E:/android-sdk-windows/ndk/29.0.14206865/toolchains/llvm/prebuilt/windows-x86_64/bin/aarch64-linux-android24-clang.cmd \
  -O2 -s -o su su.c

adb push su /data/local/tmp/su.dfm
adb shell
  mount -o remount,rw /            # 需要时可写；若报 ENOSPC 见下
  dd if=/data/local/tmp/su.dfm of=/system/xbin/su
  chmod u+s,g+s /system/xbin/su    # ⚠ toybox chmod 数字 6755 不生效，必须用 u+s,g+s
  chown 0:0 /system/xbin/su
  ls -l /system/xbin/su            # 应为 -rwsr-sr-x
  rm /data/local/tmp/su.dfm
```

验证：设备上跑探针 0x08 `ls /dev/video*` root:true 应返回 200。

## 坑：系统分区写满（ENOSPC）

dm-0(/) 若 100% 满，即使 root 写入也报 `No space left on device`
（ext4 保留块占用全部剩余空间，`df` 显示的 avail 是误导）。
解决办法（本机 192.168.0.22 已执行，tune2fs 写了 superblock，重启后仍生效）：

```bash
tune2fs -m 0 /dev/block/dm-0
echo 0 > /sys/fs/ext4/dm-0/reserved_clusters   # 立即生效（重启后由 superblock 保证）
```

## 已知改动记录（192.168.0.22:5555，2026-11-26）

- `/system/xbin/su`：toybox su → 本 permissive su（原 toybox su 在写满故障中被截断为 0 字节，无备份；如需恢复可从同型号设备/ROM 提取）
- `/system/xbin` 目录 0750 → 0755（应用可进入）
- dm-0 保留块 5% → 0%（tune2fs -m 0）
- **删除了两个字体文件腾空间**：`/system/fonts/NotoSerifCJK-Regular.ttc`（26MB）、
  `/system/fonts/NotoColorEmojiLegacy.ttf`（9MB）——事后分析其实腾空间并非必需，
  保留块清零后原剩余空间即够用。影响：衬线中文/旧版 emoji 显示回退；
  如需恢复请从同型号设备拉取这两个文件或重新刷机。
