package com.dfm.probe;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.os.Build;
import android.util.Log;

/**
 * 接收 PC 的启动广播，拉起常驻前台服务（服务自己监听 21104 端口）。
 *
 * 触发方式（PC 端）：
 *   adb shell am broadcast --include-stopped-packages \
 *       -n com.dfm.probe/.StartReceiver -a com.dfm.probe.START
 *
 * 保留广播入口的原因：--include-stopped-packages 能拉起安装后处于 stopped 状态的应用，
 * 规避 Android 11 安装后无法直接启动前台服务的风险。adb 不再传递任何地址或业务数据。
 */
public class StartReceiver extends BroadcastReceiver {
    private static final String TAG = "DFMProbe";

    @Override
    public void onReceive(Context context, Intent intent) {
        Log.i(TAG, "START received");
        Intent service = new Intent(context, ProbeService.class);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            context.startForegroundService(service);
        } else {
            context.startService(service);
        }
    }
}
