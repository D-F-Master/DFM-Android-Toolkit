package com.dfm.probe;

import android.app.AlarmManager;
import android.app.PendingIntent;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.os.Build;
import android.os.SystemClock;
import android.util.Log;

/**
 * 接收 PC 的启动广播、设备开机广播与看门狗定时广播，拉起常驻前台服务（服务自己监听 21104 端口）。
 *
 * 触发方式（PC 端）：
 *   adb shell am broadcast --include-stopped-packages \
 *       -n com.dfm.probe/.StartReceiver -a com.dfm.probe.START
 *
 * 保留广播入口的原因：--include-stopped-packages 能拉起安装后处于 stopped 状态的应用，
 * 规避 Android 11 安装后无法直接启动前台服务的风险。adb 不再传递任何地址或业务数据。
 *
 * BOOT_COMPLETED 让设备重启后自动拉起服务；AlarmManager 看门狗每 60 秒自检一次，
 * 进程被系统回收（START_STICKY 未生效）时重新拉起。force-stop 会同时取消 alarm 与所有广播，
 * 只能由 PC 重新发 START 广播恢复，这是 Android 系统限制。
 */
public class StartReceiver extends BroadcastReceiver {
    private static final String TAG = "DFMProbe";
    private static final String ACTION_WATCHDOG = "com.dfm.probe.WATCHDOG";
    private static final int WATCHDOG_REQUEST_CODE = 1;
    private static final long WATCHDOG_INTERVAL_MS = 60000L;

    @Override
    public void onReceive(Context context, Intent intent) {
        Log.i(TAG, "broadcast received: " + (intent == null ? null : intent.getAction()));
        scheduleWatchdog(context);
        try {
            Intent service = new Intent(context, ProbeService.class);
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                context.startForegroundService(service);
            } else {
                context.startService(service);
            }
        } catch (Exception e) {
            // 后台启动前台服务可能被系统拒绝；alarm 仍在，下一轮看门狗会重试
            Log.w(TAG, "start service failed: " + e);
        }
    }

    /** 注册/续期看门狗：显式 Intent 指向本接收器，不受隐式广播限制；FLAG_UPDATE_CURRENT 避免重复 alarm。 */
    private static void scheduleWatchdog(Context context) {
        AlarmManager am = (AlarmManager) context.getSystemService(Context.ALARM_SERVICE);
        if (am == null) {
            Log.w(TAG, "alarm service unavailable, watchdog not scheduled");
            return;
        }
        Intent watchdog = new Intent(context, StartReceiver.class).setAction(ACTION_WATCHDOG);
        PendingIntent pi = PendingIntent.getBroadcast(context, WATCHDOG_REQUEST_CODE, watchdog,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        am.setRepeating(AlarmManager.ELAPSED_REALTIME_WAKEUP,
                SystemClock.elapsedRealtime() + WATCHDOG_INTERVAL_MS,
                WATCHDOG_INTERVAL_MS, pi);
    }
}
