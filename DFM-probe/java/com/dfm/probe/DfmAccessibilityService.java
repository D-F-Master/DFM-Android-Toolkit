package com.dfm.probe;

import android.accessibilityservice.AccessibilityService;
import android.accessibilityservice.GestureDescription;
import android.graphics.Path;
import android.os.Build;
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

    /**
     * 在屏幕坐标 (x, y) 派发一次点击手势（dispatchGesture，API 24+）。
     * 用于 ACTION_CLICK 不生效时（节点不可点击/纯展示控件）的坐标点击兜底，无需 root。
     *
     * @return 派发是否被系统受理（受理≠保证点击生效，但为无障碍服务能做的最大努力）
     */
    boolean dispatchTap(float x, float y) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.N) {
            Log.w(TAG, "dispatchTap requires API 24+, current " + Build.VERSION.SDK_INT);
            return false;
        }
        Path p = new Path();
        p.moveTo(x, y);
        GestureDescription.Builder builder = new GestureDescription.Builder();
        builder.addStroke(new GestureDescription.StrokeDescription(p, 0, 1));
        return dispatchGesture(builder.build(), null, null);
    }

    /**
     * 派发一条自定义笔画手势（供脚本引擎的 tap/longpress/swipe 使用，API 24+）。
     *
     * @param path         手势轨迹
     * @param startTimeMs  距手势开始的延迟（0 立即）
     * @param durationMs   笔画持续时间（长按 = 按住时长，滑动 = 滑动时长）
     * @return 派发是否被系统受理
     */
    boolean dispatchGestureStroke(Path path, long startTimeMs, long durationMs) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.N) {
            Log.w(TAG, "dispatchGestureStroke requires API 24+, current " + Build.VERSION.SDK_INT);
            return false;
        }
        GestureDescription.Builder builder = new GestureDescription.Builder();
        builder.addStroke(new GestureDescription.StrokeDescription(path, startTimeMs, durationMs));
        return dispatchGesture(builder.build(), null, null);
    }
}
