package com.dfm.probe;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.os.Build;
import android.util.Log;

/**
 * 接收 PC 端发来的地址（host/port），拉起前台服务主动回连 PC。
 *
 * 触发方式（PC 端）：
 *   adb shell am broadcast --include-stopped-packages \
 *       -n com.dfm.probe/.ConnectReceiver -a com.dfm.probe.CONNECT \
 *       --es host 192.168.2.93 --ei port 19876
 */
public class ConnectReceiver extends BroadcastReceiver {
    private static final String TAG = "DFMProbe";

    @Override
    public void onReceive(Context context, Intent intent) {
        String host = intent.getStringExtra("host");
        int port = intent.getIntExtra("port", 0);
        if (host == null || host.length() == 0 || port <= 0 || port > 65535) {
            Log.e(TAG, "invalid host/port: " + host + ":" + port);
            return;
        }
        Log.i(TAG, "CONNECT received: " + host + ":" + port);

        Intent service = new Intent(context, ProbeService.class);
        service.putExtra("host", host);
        service.putExtra("port", port);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            context.startForegroundService(service);
        } else {
            context.startService(service);
        }
    }
}
