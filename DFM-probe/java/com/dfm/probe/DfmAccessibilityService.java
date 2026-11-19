package com.dfm.probe;

import android.accessibilityservice.AccessibilityService;
import android.util.Log;
import android.view.accessibility.AccessibilityEvent;

/**
 * 无障碍服务：在系统无障碍设置里手动启用后常驻，供 0x0C 读取当前活动窗口组件树。
 * 不消费任何事件，事件回调全部空实现；启用/关闭状态通过静态实例暴露给 ProbeService。
 */
public class DfmAccessibilityService extends AccessibilityService {
    private static final String TAG = "DFMProbe";

    private static volatile DfmAccessibilityService sInstance;

    /** 已启用返回实例，未启用返回 null（ProbeService 0x0C 据此报未开启）。 */
    static DfmAccessibilityService instance() {
        return sInstance;
    }

    @Override
    protected void onServiceConnected() {
        super.onServiceConnected();
        sInstance = this;
        Log.i(TAG, "accessibility service connected");
    }

    @Override
    public void onDestroy() {
        super.onDestroy();
        if (sInstance == this) {
            sInstance = null;
        }
    }

    @Override
    public void onAccessibilityEvent(AccessibilityEvent event) {
        // 不需要事件流，仅按需读取 rootInActiveWindow
    }

    @Override
    public void onInterrupt() {
        // 空实现
    }
}
