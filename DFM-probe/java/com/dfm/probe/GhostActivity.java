package com.dfm.probe;

import android.app.Activity;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.WindowManager;

/**
 * 拍照辅助 Activity（PC 端 am start 拉起）。
 *
 * 背景：Android 11 起，从后台启动的前台服务（probe 的 START 广播路径）不具备
 * while-in-use 的 CAMERA capability（logcat: "Foreground service started from
 * background can not have location/camera/microphone access"），CameraService
 * UidPolicy 会把 openCamera 拒为 CAMERA_DISABLED "disabled by policy"。
 * 本 Activity 完全透明、不拦截触摸（FLAG_NOT_TOUCHABLE / FLAG_NOT_FOCUSABLE），
 * 仅在停留期间让应用进程短暂回到前台（uid 获得 CAMERA capability），拍照完成
 * 后自动 finish，用户无感知。锁屏时 setShowWhenLocked + setTurnScreenOn 保证
 * 仍能进入 resumed 状态。
 *
 * 方案3 P2 的 unicode 输入不走本 Activity：写剪贴板（setPrimaryClip）不受
 * Android 10 的"仅焦点应用/默认 IME 可读"限制影响（限制的是读取），PASTE
 * 事件由系统分发给焦点窗口（目标输入框），任何进程写入的剪贴板内容都能被
 * 粘贴。流程见 JsEngine.inputUnicode：setPrimaryClip → input keyevent 279。
 */
public class GhostActivity extends Activity {

    /** 停留时长：覆盖 openCamera(5s) + 成像(15s) 的正常耗时上限（ms） */
    static final long LINGER_MS = 15000;

    private final Handler mHandler = new Handler(Looper.getMainLooper());
    private final Runnable mFinish = new Runnable() {
        @Override
        public void run() {
            finish();
        }
    };

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        // 覆盖锁屏：锁屏状态下 activity 才能进入 resumed，uid 才算前台
        setShowWhenLocked(true);
        setTurnScreenOn(true);
        // 完全不交互：触摸穿透、不抢焦点，用户无感知
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_SHOW_WALLPAPER);
    }

    @Override
    protected void onResume() {
        super.onResume();
        mHandler.removeCallbacks(mFinish);
        mHandler.postDelayed(mFinish, LINGER_MS);
    }

    @Override
    protected void onPause() {
        mHandler.removeCallbacks(mFinish);
        super.onPause();
    }

    @Override
    protected void onDestroy() {
        mHandler.removeCallbacks(mFinish);
        super.onDestroy();
    }
}
