package com.dfm.probe;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.hardware.camera2.CameraCaptureSession;
import android.hardware.camera2.CameraCharacteristics;
import android.hardware.camera2.CameraDevice;
import android.hardware.camera2.CameraManager;
import android.hardware.camera2.CameraMetadata;
import android.hardware.camera2.CaptureFailure;
import android.hardware.camera2.CaptureRequest;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.ImageFormat;
import android.graphics.Matrix;
import android.graphics.Path;
import android.graphics.Rect;
import android.hardware.input.InputManager;
import android.hardware.usb.UsbDevice;
import android.hardware.usb.UsbManager;
import android.media.Image;
import android.media.ImageReader;
import android.media.MediaMetadataRetriever;
import android.os.Build;
import android.os.Handler;
import android.os.HandlerThread;
import android.os.IBinder;
import android.os.SystemClock;
import android.provider.Settings;
import android.speech.tts.TextToSpeech;
import android.speech.tts.UtteranceProgressListener;
import android.util.Log;
import android.util.Size;
import android.view.InputDevice;
import android.view.accessibility.AccessibilityNodeInfo;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.io.RandomAccessFile;
import java.net.InetSocketAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.charset.Charset;
import java.text.SimpleDateFormat;
import java.util.Arrays;
import java.util.Collections;
import java.util.Date;
import java.util.HashMap;
import java.util.Locale;
import java.util.Map;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;
import java.util.zip.ZipEntry;
import java.util.zip.ZipFile;

/**
 * 常驻服务：监听设备本机 0.0.0.0:21104，等待 PC 主动连入。
 *
 * 二进制帧协议详见 probe与service通讯协议.md：
 *   AA 55 | 帧序(1B) | 长度(2B，低字节在前) | 命令字(1B) | 数据区 | CRC16(低字节在前)
 *   数据区为 UTF-8 JSON，最长 65535 字节。
 *   业务帧（0x00、0x01、0x02）校验通过先回 ACK(0x06)，再发同帧序响应帧（命令字|0x80 或 0xFF）。
 *   CRC 错、数据区不是合法 JSON 对象的帧直接丢弃，不回 ACK，由发送端超时重传。
 */
public class ProbeService extends Service {
    private static final String TAG = "DFMProbe";
    private static final String CHANNEL_ID = "dfm_probe_none"; // IMPORTANCE_NONE：通知不渲染进通知栏（前台服务要求的通知仍存在，服务不崩）；换 ID 才能降级
    private static final String[] OLD_CHANNEL_IDS = {"dfm_probe", "dfm_probe_min"};
    private static final int NOTIFY_ID = 1;
    static final int DEVICE_PORT = 21104;
    private static final int MAX_DATA = 65535;
    private static final Charset UTF8 = Charset.forName("UTF-8");

    private static final int CMD_HEARTBEAT = 0x00;
    private static final int CMD_TTS = 0x01;
    private static final int CMD_DEVICE_INFO = 0x02;
    private static final int CMD_SERIAL_READ = 0x03;
    private static final int CMD_SERIAL_OPEN = 0x04;
    private static final int CMD_SERIAL_SEND = 0x05;
    private static final int CMD_SERIAL_CLOSE = 0x07;
    private static final int CMD_SHELL = 0x08;
    private static final int CMD_SCREENSHOT = 0x09;
    private static final int CMD_CAMERA = 0x0A;
    private static final int CMD_ACCESSIBILITY_SETTINGS = 0x0B;
    private static final int CMD_ACCESSIBILITY_TREE = 0x0C;
    private static final int CMD_ACCESSIBILITY_CLICK = 0x0D;
    private static final int CMD_SCRIPT_IMPORT = 0x0E;
    private static final int CMD_SCRIPT_LIST = 0x0F;
    private static final int CMD_SCRIPT_RUN = 0x10;
    private static final int CMD_CAM_REPLACE = 0x11;
    private static final int CMD_CAM_RESTORE = 0x12;
    private static final int CMD_ACK = 0x06;
    private static final int CMD_ERROR = 0xFF;

    /** 串口会话读缓冲上限，超出时丢弃最旧的字节，避免长时间无人读取撑爆内存。 */
    private static final int MAX_SERIAL_BUFFER = 65536;

    /** shell 输出（stdout / stderr 各自）按字节截断的上限，防止响应 JSON 超出单帧 65535。 */
    private static final int MAX_SHELL_OUTPUT = 10000;

    /** 0x0C 组件树序列化的节点数上限，超出后停止展开并置 truncated，防止响应超出单帧 65535。 */
    private static final int MAX_TREE_NODES = 150;

    /** 设备端离线脚本引擎（0x0E 解压 / 0x0F 列表 / 0x10 执行），懒创建。 */
    private ScriptEngine scriptEngine;

    /** 监听状态必须 static：系统每 60 秒把后台服务按 app idle 停掉（ onDestroy ），但进程仍存活；
     *  监听线程挂在 static 字段上即可跨服务生命周期继续 accept，端口不中断。 */
    private static volatile boolean running = false;
    private static volatile ServerSocket serverSocket;
    private volatile TextToSpeech tts;
    /** 设备端只保持一个串口会话，OPEN 会先关闭旧会话。 */
    private volatile SerialSession serialSession;
    /** 仅当本次经 startForegroundService 拉起（系统要求 5 秒内 startForeground）时置 true。 */
    static volatile boolean sWantForeground = false;

    @Override
    public void onCreate() {
        super.onCreate();
        createChannel();
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        // START / BOOT_COMPLETED / 看门狗广播都会重发，只有尚未监听时才起线程，避免重复 bind 端口
        if (!running) {
            running = true;
            new Thread(new ListenTask(), "dfm-probe-listener").start();
        }
        // startForegroundService 要求 5 秒内调用 startForeground 否则崩溃；但系统会把前台服务
        // 通知的渠道强制提升到 LOW（实测 IMPORTANCE_NONE 渠道 mOriginalImp=0 仍被提升为 2），
        // appops/权限撤销在 Android 11 也不被通知系统采纳，渠道层无法隐藏。
        // 因此只有经 startForegroundService 拉起时才 startForeground 并立即 stopForeground
        // （通知只闪现毫秒级）；看门狗/广播的静默路径走普通 startService，全程零通知。
        if (sWantForeground) {
            startForeground(NOTIFY_ID, buildNotification());
            stopForeground(true);
            sWantForeground = false;
        }
        // 系统回收进程后自动重建服务；重建时 intent 可能为 null，本服务不读 intent
        return START_STICKY;
    }

    @Override
    public void onDestroy() {
        // 系统按 app idle 停掉服务时（约每 60 秒一次）只回收 ServiceRecord，进程仍存活；
        // 不关闭监听线程/端口，服务被停后 21104 继续监听。进程被真正杀死时一切自然回收，
        // 看门狗广播会重新拉起服务与监听线程。
        Log.i(TAG, "service destroyed, listener keeps running on " + DEVICE_PORT);
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    /** 探测本机 21104 是否已在监听；StartReceiver 用来跳过重复拉起，避免通知闪现。 */
    static boolean isListening() {
        Socket probe = new Socket();
        try {
            probe.connect(new InetSocketAddress("127.0.0.1", DEVICE_PORT), 200);
            return true;
        } catch (Exception e) {
            return false;
        } finally {
            try {
                probe.close();
            } catch (Exception ignored) {
            }
        }
    }

    private void createChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            NotificationChannel channel = new NotificationChannel(
                    CHANNEL_ID, "DFM Probe", NotificationManager.IMPORTANCE_NONE);
            NotificationManager manager =
                    (NotificationManager) getSystemService(Context.NOTIFICATION_SERVICE);
            if (manager != null) {
                manager.createNotificationChannel(channel);
                for (String oldId : OLD_CHANNEL_IDS) {
                    manager.deleteNotificationChannel(oldId);
                }
            }
        }
    }

    private Notification buildNotification() {
        Notification.Builder builder = Build.VERSION.SDK_INT >= Build.VERSION_CODES.O
                ? new Notification.Builder(this, CHANNEL_ID)
                : new Notification.Builder(this);
        return builder
                .setSmallIcon(R.drawable.ic_probe)
                .setContentTitle("DFM Probe")
                .setContentText("监听 21104 端口")
                .setOngoing(true)
                .build();
    }

    private final class ListenTask implements Runnable {
        @Override
        public void run() {
            ServerSocket server = null;
            try {
                server = new ServerSocket();
                server.setReuseAddress(true);
                server.bind(new InetSocketAddress("0.0.0.0", DEVICE_PORT));
                serverSocket = server;
                Log.i(TAG, "listening on 0.0.0.0:" + DEVICE_PORT);
                while (running) {
                    Socket client = server.accept();
                    Log.i(TAG, "client connected: " + client.getRemoteSocketAddress());
                    // 每个连接一个线程：TTS 朗读最长约 90s，不能卡住 accept 循环
                    new Thread(new ConnectionTask(client), "dfm-probe-conn").start();
                }
            } catch (Exception e) {
                Log.e(TAG, "listen failed on port " + DEVICE_PORT + ": " + e);
                running = false;
            } finally {
                // 只清理自己 bind 的 socket：bind 失败时不能抹掉旧监听线程仍持有的 serverSocket
                if (serverSocket == server) {
                    serverSocket = null;
                }
                if (server != null) {
                    try {
                        server.close();
                    } catch (Exception ignored) {
                    }
                }
                Log.i(TAG, "listener stopped");
            }
        }
    }

    private final class ConnectionTask implements Runnable {
        private final Socket socket;
        /** 本连接最近一次已成功处理业务的帧序，用于重传幂等；-1 表示还没有处理过。 */
        private int lastSeq = -1;

        ConnectionTask(Socket socket) {
            this.socket = socket;
        }

        @Override
        public void run() {
            try {
                serve(socket);
            } catch (Exception e) {
                Log.w(TAG, "connection failed: " + e);
            } finally {
                try {
                    socket.close();
                } catch (Exception ignored) {
                }
                Log.i(TAG, "client disconnected");
            }
        }

        private void serve(Socket socket) throws Exception {
            InputStream in = socket.getInputStream();
            OutputStream out = socket.getOutputStream();
            ByteArrayOutputStream acc = new ByteArrayOutputStream();
            byte[] chunk = new byte[256];
            while (running) {
                int n = in.read(chunk);
                if (n < 0) {
                    break;
                }
                acc.write(chunk, 0, n);
                byte[] bytes = acc.toByteArray();
                int consumed = 0;
                boolean quit = false;
                while (true) {
                    int idx = findHeader(bytes, consumed);
                    if (idx < 0) {
                        // 只保留末尾可能是半个帧头的 0xAA，其余全部丢弃
                        int keep = (bytes.length > consumed
                                && (bytes[bytes.length - 1] & 0xFF) == 0xAA) ? 1 : 0;
                        consumed = bytes.length - keep;
                        break;
                    }
                    if (idx + 6 > bytes.length) {
                        // 帧头、帧序、长度(2B)、命令字还没收齐
                        consumed = idx;
                        break;
                    }
                    int seq = bytes[idx + 2] & 0xFF;
                    int len = (bytes[idx + 3] & 0xFF) | ((bytes[idx + 4] & 0xFF) << 8);
                    int cmd = bytes[idx + 5] & 0xFF;
                    int frameLen = 8 + len;
                    if (idx + frameLen > bytes.length) {
                        // 半包：从帧头开始保留，等后续字节
                        consumed = idx;
                        break;
                    }
                    int crcLo = bytes[idx + 6 + len] & 0xFF;
                    int crcHi = bytes[idx + 7 + len] & 0xFF;
                    int expect = crc16(bytes, idx + 2, 4 + len);
                    if ((expect & 0xFF) != crcLo || ((expect >> 8) & 0xFF) != crcHi) {
                        // CRC 错：丢弃并从下一字节重新找帧头，不回 ACK
                        Log.w(TAG, "bad crc, drop frame seq=" + seq);
                        consumed = idx + 1;
                        continue;
                    }
                    byte[] data = new byte[len];
                    System.arraycopy(bytes, idx + 6, data, 0, len);
                    quit = handleFrame(out, seq, cmd, data);
                    consumed = idx + frameLen;
                    if (quit) {
                        break;
                    }
                }
                byte[] rest = new byte[bytes.length - consumed];
                System.arraycopy(bytes, consumed, rest, 0, rest.length);
                acc.reset();
                acc.write(rest, 0, rest.length);
                if (quit) {
                    break;
                }
            }
        }

        /**
         * 处理一条完整业务帧。当前没有命令字要求关闭连接，返回值恒为 false。
         * CRC 正确且结构完整即回 ACK；数据区不是合法 JSON 对象则丢弃、不回 ACK。
         */
        private boolean handleFrame(OutputStream out, int seq, int cmd, byte[] data) throws Exception {
            JSONObject req;
            try {
                req = new JSONObject(new String(data, UTF8));
            } catch (Exception e) {
                Log.w(TAG, "invalid payload json, drop frame without ACK");
                return false;
            }
            if (seq == lastSeq) {
                // 重传帧：只回 ACK，不重复执行业务（尤其 TTS 不能念两遍）
                Log.i(TAG, "duplicate seq=" + seq + ", ACK only");
                sendFrame(out, seq, CMD_ACK, new byte[0]);
                return false;
            }
            lastSeq = seq;
            // 先回 ACK 确认帧已完整收到，避免发送端 200ms 超时重传（业务可能耗时数十秒）
            sendFrame(out, seq, CMD_ACK, new byte[0]);
            boolean unknown = cmd != CMD_HEARTBEAT && cmd != CMD_TTS
                    && cmd != CMD_DEVICE_INFO && cmd != CMD_SERIAL_READ
                    && cmd != CMD_SERIAL_OPEN && cmd != CMD_SERIAL_SEND
                    && cmd != CMD_SERIAL_CLOSE
                    && cmd != CMD_SHELL && cmd != CMD_SCREENSHOT && cmd != CMD_CAMERA
                    && cmd != CMD_ACCESSIBILITY_SETTINGS && cmd != CMD_ACCESSIBILITY_TREE
                    && cmd != CMD_ACCESSIBILITY_CLICK
                    && cmd != CMD_SCRIPT_IMPORT && cmd != CMD_SCRIPT_LIST
                    && cmd != CMD_SCRIPT_RUN
                    && cmd != CMD_CAM_REPLACE && cmd != CMD_CAM_RESTORE;
            JSONObject res = execute(cmd, req, unknown);
            int respCmd = unknown ? CMD_ERROR : (cmd | 0x80);
            byte[] respData = res.toString().getBytes(UTF8);
            if (respData.length > MAX_DATA) {
                res = fail(500, "response too large: " + respData.length);
                respData = res.toString().getBytes(UTF8);
            }
            sendFrame(out, seq, respCmd, respData);
            Log.i(TAG, "cmd=0x" + Integer.toHexString(cmd) + " seq=" + seq + " -> " + res);
            return false;
        }
    }

    private JSONObject execute(int cmd, JSONObject req, boolean unknown) throws Exception {
        if (unknown) {
            return fail(400, "unknown cmd: 0x" + Integer.toHexString(cmd));
        }
        if (cmd == CMD_HEARTBEAT) {
            try {
                String version = getPackageManager().getPackageInfo(getPackageName(), 0).versionName;
                if (version == null || version.isEmpty()) {
                    return fail(500, "version unavailable");
                }
                JSONObject data = new JSONObject();
                data.put("version", version);
                return ok(data);
            } catch (Exception e) {
                Log.w(TAG, "version unavailable: " + e);
                return fail(500, "version unavailable: " + e);
            }
        }
        if (cmd == CMD_TTS) {
            Object text = req.opt("text");
            if (!(text instanceof String)) {
                return fail(400, "text must be a string");
            }
            if (((String) text).trim().isEmpty()) {
                return fail(400, "empty text");
            }
            try {
                return speak((String) text);
            } catch (Exception e) {
                Log.w(TAG, "execute failed: " + e);
                return fail(500, "execute failed: " + e);
            }
        }
        if (cmd == CMD_DEVICE_INFO) {
            try {
                return ok(deviceInfo());
            } catch (Exception e) {
                Log.w(TAG, "device info failed: " + e);
                return fail(500, "device info failed: " + e);
            }
        }
        if (cmd == CMD_SERIAL_READ || cmd == CMD_SERIAL_OPEN
                || cmd == CMD_SERIAL_SEND || cmd == CMD_SERIAL_CLOSE) {
            try {
                JSONObject data;
                if (cmd == CMD_SERIAL_OPEN) {
                    data = serialOpen(req);
                } else if (cmd == CMD_SERIAL_SEND) {
                    data = serialSend(req);
                } else if (cmd == CMD_SERIAL_CLOSE) {
                    data = serialClose();
                } else {
                    data = serialRead();
                }
                return ok(data);
            } catch (IllegalArgumentException e) {
                Log.w(TAG, "serial cmd param error: " + e);
                return fail(400, e.getMessage());
            } catch (Exception e) {
                Log.w(TAG, "serial cmd failed: " + e);
                return fail(500, "serial cmd failed: " + e);
            }
        }
        if (cmd == CMD_SHELL || cmd == CMD_SCREENSHOT || cmd == CMD_CAMERA) {
            try {
                JSONObject data;
                if (cmd == CMD_SHELL) {
                    data = shellExec(req);
                } else if (cmd == CMD_SCREENSHOT) {
                    data = takeScreenshot();
                } else {
                    data = takePhoto(req);
                }
                return ok(data);
            } catch (IllegalArgumentException e) {
                Log.w(TAG, "cmd param error: " + e);
                return fail(400, e.getMessage());
            } catch (Exception e) {
                Log.w(TAG, "cmd 0x" + Integer.toHexString(cmd) + " failed: " + e);
                return fail(500, "cmd failed: " + e);
            }
        }
        if (cmd == CMD_ACCESSIBILITY_SETTINGS || cmd == CMD_ACCESSIBILITY_TREE
                || cmd == CMD_ACCESSIBILITY_CLICK) {
            try {
                if (cmd == CMD_ACCESSIBILITY_SETTINGS) {
                    return ok(openAccessibilitySettings());
                }
                if (cmd == CMD_ACCESSIBILITY_CLICK) {
                    return accessibilityClick(req);
                }
                return accessibilityTree();
            } catch (Exception e) {
                Log.w(TAG, "cmd 0x" + Integer.toHexString(cmd) + " failed: " + e);
                return fail(500, "cmd failed: " + e);
            }
        }
        if (cmd == CMD_SCRIPT_IMPORT || cmd == CMD_SCRIPT_LIST || cmd == CMD_SCRIPT_RUN) {
            try {
                JSONObject data;
                if (cmd == CMD_SCRIPT_IMPORT) {
                    data = scriptImport(req);
                } else if (cmd == CMD_SCRIPT_LIST) {
                    data = scriptList();
                } else {
                    data = scriptRun(req);
                }
                return ok(data);
            } catch (IllegalArgumentException e) {
                Log.w(TAG, "script cmd param error: " + e);
                return fail(400, e.getMessage());
            } catch (Exception e) {
                Log.w(TAG, "script cmd 0x" + Integer.toHexString(cmd) + " failed: " + e);
                return fail(500, "script cmd failed: " + e);
            }
        }
        if (cmd == CMD_CAM_REPLACE || cmd == CMD_CAM_RESTORE) {
            try {
                return ok(cmd == CMD_CAM_REPLACE ? camReplace(req) : camRestore());
            } catch (IllegalArgumentException e) {
                Log.w(TAG, "cam cmd param error: " + e);
                return fail(400, e.getMessage());
            } catch (Exception e) {
                Log.w(TAG, "cam cmd 0x" + Integer.toHexString(cmd) + " failed: " + e);
                return fail(500, "cam cmd failed: " + e);
            }
        }
        return fail(400, "unknown cmd: 0x" + Integer.toHexString(cmd));
    }

    /** 成功信封，对齐 service 统一返回格式。 */
    private static JSONObject ok(JSONObject data) throws Exception {
        JSONObject res = new JSONObject();
        res.put("code", 200);
        res.put("message", "success");
        res.put("data", data);
        return res;
    }

    /** 失败信封，data 固定为 null。 */
    private static JSONObject fail(int code, String message) throws Exception {
        JSONObject res = new JSONObject();
        res.put("code", code);
        res.put("message", message);
        res.put("data", JSONObject.NULL);
        return res;
    }

    // ===== 脚本离线执行（0x0E 解压 / 0x0F 列表 / 0x10 执行），引擎见 ScriptEngine =====

    /** 脚本引擎懒创建：依赖本服务上下文（外部私有目录 / 0x08 shell / 0x01 TTS / 0x0D 无障碍）。 */
    private synchronized ScriptEngine scriptEngine() throws Exception {
        if (scriptEngine == null) {
            scriptEngine = new ScriptEngine(this);
        }
        return scriptEngine;
    }

    /** CMD_SCRIPT_IMPORT (0x0E)：解压 script_in/ 下 zip 到 script_store/{脚本名}/。请求 {"zip":"xxx.zip"}。 */
    private JSONObject scriptImport(JSONObject req) throws Exception {
        String zip = req.optString("zip", "").trim();
        return scriptEngine().unzip(zip);
    }

    /** CMD_SCRIPT_LIST (0x0F)：列出 script_store/ 下已导入的脚本。 */
    private JSONObject scriptList() throws Exception {
        return scriptEngine().list();
    }

    /** CMD_SCRIPT_RUN (0x10)：执行 script_store/{script}/script.json。请求 {"script":"名字","repeat":N}。 */
    private JSONObject scriptRun(JSONObject req) throws Exception {
        String name = req.optString("script", req.optString("name", "")).trim();
        int repeat = req.optInt("repeat", 1);
        return scriptEngine().run(name, repeat);
    }

    // ===== 摄像头画面替换（0x11 替换 / 0x12 恢复），部署命令对齐 camreplace APK 已验证流程 =====

    /** 替换源统一缩放到 640x480（camhook 与相机 HAL 协商的实际分辨率）。 */
    private static final int CAM_W = 640;
    private static final int CAM_H = 480;
    /** 视频采样帧率与帧数上限（同 camreplace APK）。 */
    private static final int CAM_SAMPLE_FPS = 15;
    private static final int CAM_MAX_FRAMES = 600;
    private static final int CAM_JPEG_Q = 85;

    private static final String CAM_RC_PATH =
            "/vendor/etc/init/android.hardware.camera.provider@2.4-external-service.rc";
    private static final String CAM_SO_DST = "/vendor/lib/camhook.so";
    private static final String CAM_STREAM_DST = "/dev/camhook.stream";
    private static final String CAM_CONF_PATH = "/dev/camhook.conf";
    private static final String CAM_STATUS_PATH = "/dev/camhook.status";
    private static final String CAM_ASSET_SO = "camhook.so";

    /** CMD_CAM_REPLACE (0x11)：PC 端先 adb push 媒体到 files/cam_in/，本端转成 640x480 JPEG 帧流
     *  （图片 1 帧 / 视频 15fps 最多 600 帧，可选镜像补偿），经 su 部署 camhook 并重启 camera provider。
     *  请求 {"file":"xxx.jpg|mp4","cameraId":"2","mirror":false}；cameraId 为 0x02 相机列表返回的 id，
     *  映射 /dev/video{cameraId}（外部相机 id 即 video 节点号），节点不存在回 400 并列出可用节点。
     *  可选 srcPath（绝对路径）：脚本步骤（ScriptEngine）媒体在脚本包 media/ 目录内，直接指定源文件，
     *  绕过 cam_in 查找；public 供 ScriptEngine 复用。 */
    public JSONObject camReplace(JSONObject req) throws Exception {
        String file = req.optString("file", "").trim();
        if (file.isEmpty() || file.contains("/") || file.contains("\\") || file.contains("..")) {
            throw new IllegalArgumentException("file is required (file name only)");
        }
        File src;
        String srcPath = req.optString("srcPath", "").trim();
        if (!srcPath.isEmpty()) {
            src = new File(srcPath);
            if (!src.isFile()) {
                throw new IllegalArgumentException("srcPath not found: " + src.getAbsolutePath());
            }
        } else {
            File camDir = new File(ensureFilesDir(), "cam_in");
            src = new File(camDir, file);
            if (!src.isFile()) {
                throw new IllegalArgumentException("file not pushed to device: " + src.getAbsolutePath());
            }
        }
        boolean mirror = req.optBoolean("mirror", false);

        // 目标节点：显式 dev 优先，否则按 0x02 相机列表的 cameraId 映射 /dev/video{id}
        String dev = req.optString("dev", "").trim();
        if (dev.isEmpty()) {
            String cameraId = req.optString("cameraId", "").trim();
            if (!cameraId.matches("\\d+")) {
                throw new IllegalArgumentException("cameraId (number) or dev is required");
            }
            dev = "/dev/video" + cameraId;
        }
        if (!dev.matches("/dev/video\\d+")) {
            throw new IllegalArgumentException("dev must match /dev/video<id>");
        }

        String ext = "";
        int dot = file.lastIndexOf('.');
        if (dot >= 0) ext = file.substring(dot + 1).toLowerCase(Locale.US);
        boolean isImage = false;
        for (String e : new String[]{"jpg", "jpeg", "png"}) {
            if (e.equals(ext)) {
                isImage = true;
                break;
            }
        }
        boolean isVideo = false;
        for (String e : new String[]{"mp4", "mov", "avi", "webm", "3gp", "mkv"}) {
            if (e.equals(ext)) {
                isVideo = true;
                break;
            }
        }
        if (!isImage && !isVideo) {
            throw new IllegalArgumentException("unsupported file type: ." + ext);
        }

        // 目标 video 节点必须存在，不存在时列出全部节点便于排查 cameraId 与节点号的映射差异
        String check = runSuScript(new String[]{
                "[ -e " + dev + " ] && echo NODE_OK || echo NODE_MISSING",
                "ls /dev/video* 2>/dev/null || echo none"
        }, 15000);
        if (check.contains("NODE_MISSING")) {
            throw new IllegalArgumentException("camera node missing: " + dev
                    + ", available: " + camVideoNodes(check));
        }

        byte[] stream;
        int frames;
        if (isImage) {
            stream = camImageToStream(src, mirror);
            frames = 1;
        } else {
            stream = camVideoToStream(src, mirror);
            frames = camCountFrames(stream);
        }
        if (stream == null || stream.length <= 4 || frames <= 0) {
            throw new Exception("media converted to 0 frames");
        }

        // camhook.so 从 APK assets 解出（与 camreplace APK assets 同一 so）
        File soFile = new File(getFilesDir(), "camhook.so");
        copyAssetFile(CAM_ASSET_SO, soFile);
        File streamFile = new File(getFilesDir(), "camhook.stream");
        FileOutputStream fo = new FileOutputStream(streamFile);
        try {
            fo.write(stream);
        } finally {
            fo.close();
        }
        String files = getFilesDir().getAbsolutePath();

        String out = runSuScript(new String[]{
                "mount -o remount,rw /vendor 2>/dev/null",
                "[ -f " + CAM_SO_DST + " ] || cp " + files + "/camhook.so " + CAM_SO_DST,
                "chmod 644 " + CAM_SO_DST,
                "grep -q camhook.so " + CAM_RC_PATH + " 2>/dev/null || sed -i \"s/user cameraserver/user cameraserver\\n    setenv LD_PRELOAD " + CAM_SO_DST + "/\" " + CAM_RC_PATH,
                "printf 'enabled=1\\ndev=" + dev + "\\nmode=1\\nsource=" + CAM_STREAM_DST + "\\nquality=" + CAM_JPEG_Q + "\\n' > " + CAM_CONF_PATH,
                "chmod 644 " + CAM_CONF_PATH,
                "cp " + files + "/camhook.stream " + CAM_STREAM_DST,
                "chmod 644 " + CAM_STREAM_DST,
                "touch " + CAM_STATUS_PATH + " && chmod 666 " + CAM_STATUS_PATH,
                "echo RCCHK:$(grep -c LD_PRELOAD " + CAM_RC_PATH + ")"
        }, 60000);
        boolean rebootRequired = out.contains("RCCHK:0");
        String restart = "";
        if (!rebootRequired) {
            restart = runSuScript(new String[]{
                    "kill $(pgrep -f external-service) 2>/dev/null",
                    "echo restart_done"
            }, 30000);
        }
        String status = runSuScript(new String[]{
                "cat " + CAM_STATUS_PATH + " 2>/dev/null | head -20 || echo nostatus"
        }, 15000);

        JSONObject data = new JSONObject();
        data.put("dev", dev);
        data.put("frames", frames);
        data.put("streamSize", stream.length);
        data.put("mirror", mirror);
        data.put("rebootRequired", rebootRequired);
        data.put("deployOutput", camTail(out));
        if (!restart.isEmpty()) {
            data.put("restartOutput", camTail(restart));
        }
        data.put("status", camTail(status));
        return data;
    }

    /** CMD_CAM_RESTORE (0x12)：写 enabled=0 配置并重启 camera provider，恢复真实摄像头画面（su，需 root）。
     *  public 供脚本步骤（ScriptEngine camRestore）复用。 */
    public JSONObject camRestore() throws Exception {
        String out = runSuScript(new String[]{
                "printf 'enabled=0\\ndev=/dev/video2\\nmode=0\\nsource=" + CAM_STREAM_DST + "\\nquality=" + CAM_JPEG_Q + "\\n' > " + CAM_CONF_PATH,
                "chmod 644 " + CAM_CONF_PATH,
                "kill $(pgrep -f external-service) 2>/dev/null",
                "echo restore_done"
        }, 30000);
        JSONObject data = new JSONObject();
        data.put("conf", "enabled=0");
        data.put("output", camTail(out));
        return data;
    }

    /** 以 root 顺序执行多条命令：本设备 su 不支持 -c，固定 "su 0 sh -c <script>"；
     *  每条命令后回显 EXIT:$? 定位失败行；合并 stdout/stderr，超时强杀。 */
    private String runSuScript(String[] cmds, long timeoutMs) throws Exception {
        StringBuilder script = new StringBuilder();
        for (String c : cmds) {
            script.append(c).append("; echo EXIT:$?; ");
        }
        script.append("exit 0");
        ProcessBuilder pb = new ProcessBuilder("su", "0", "sh", "-c", script.toString());
        pb.redirectErrorStream(true);
        final Process proc = pb.start();
        final boolean[] trunc = new boolean[1];
        final AtomicReference<byte[]> outRef = new AtomicReference<>(new byte[0]);
        Thread reader = new Thread(new Runnable() {
            @Override
            public void run() {
                try {
                    outRef.set(drainStream(proc.getInputStream(), trunc));
                } catch (Exception ignored) {
                }
            }
        }, "dfm-probe-su-out");
        reader.start();
        if (!proc.waitFor(timeoutMs, TimeUnit.MILLISECONDS)) {
            proc.destroyForcibly();
            reader.join(2000);
            throw new Exception("su script timeout");
        }
        reader.join(2000);
        return new String(outRef.get(), UTF8);
    }

    /** 把 APK assets 里的文件复制到目标位置（覆盖）。 */
    private void copyAssetFile(String name, File dst) throws Exception {
        InputStream is = getAssets().open(name);
        try {
            OutputStream os = new FileOutputStream(dst);
            try {
                byte[] buf = new byte[8192];
                int n;
                while ((n = is.read(buf)) > 0) {
                    os.write(buf, 0, n);
                }
            } finally {
                os.close();
            }
        } finally {
            is.close();
        }
    }

    /** 图片解码缩放后压成单帧 .stream（[u32 LE len][JPEG q85]）。 */
    private byte[] camImageToStream(File f, boolean mirror) throws Exception {
        BitmapFactory.Options bounds = new BitmapFactory.Options();
        bounds.inJustDecodeBounds = true;
        InputStream is = new FileInputStream(f);
        BitmapFactory.decodeStream(is, null, bounds);
        is.close();
        if (bounds.outWidth <= 0 || bounds.outHeight <= 0) {
            throw new Exception("image decode failed: " + f.getName());
        }
        int sample = 1;
        while (bounds.outWidth / sample > CAM_W * 2 || bounds.outHeight / sample > CAM_H * 2) {
            sample *= 2;
        }
        BitmapFactory.Options opts = new BitmapFactory.Options();
        opts.inSampleSize = sample;
        is = new FileInputStream(f);
        Bitmap b = BitmapFactory.decodeStream(is, null, opts);
        is.close();
        if (b == null) {
            throw new Exception("image decode failed: " + f.getName());
        }
        ByteArrayOutputStream st = new ByteArrayOutputStream(256 * 1024);
        camAppendFrame(st, b, mirror);
        b.recycle();
        return st.toByteArray();
    }

    /** 视频按 15fps 抽帧（最多 600 帧），逐帧缩放压成 .stream。 */
    private byte[] camVideoToStream(File f, boolean mirror) throws Exception {
        MediaMetadataRetriever m = new MediaMetadataRetriever();
        try {
            m.setDataSource(f.getAbsolutePath());
            String durStr = m.extractMetadata(MediaMetadataRetriever.METADATA_KEY_DURATION);
            long durMs = durStr == null ? 0 : Long.parseLong(durStr);
            long stepUs = 1000000L / CAM_SAMPLE_FPS;
            int maxF = (int) Math.min(CAM_MAX_FRAMES, durMs * 1000L / stepUs);
            if (maxF <= 0) {
                maxF = 1;
            }
            ByteArrayOutputStream st = new ByteArrayOutputStream(64 * 1024 * 1024);
            int count = 0;
            for (int i = 0; i < maxF; i++) {
                Bitmap frame = m.getFrameAtTime(i * stepUs, MediaMetadataRetriever.OPTION_CLOSEST);
                if (frame == null) {
                    break;
                }
                camAppendFrame(st, frame, mirror);
                count++;
            }
            if (count == 0) {
                throw new Exception("video has 0 decodable frames: " + f.getName());
            }
            return st.toByteArray();
        } finally {
            try {
                m.release();
            } catch (Exception ignored) {
            }
        }
    }

    /** 缩放到 640x480（可选镜像补偿）、JPEG q85 压缩后按 [u32 LE len][JPEG] 追加到流。 */
    private static void camAppendFrame(ByteArrayOutputStream st, Bitmap frame, boolean mirror) {
        Bitmap b = (frame.getWidth() == CAM_W && frame.getHeight() == CAM_H)
                ? frame : Bitmap.createScaledBitmap(frame, CAM_W, CAM_H, true);
        if (mirror) {
            Matrix m2 = new Matrix();
            m2.preScale(-1f, 1f);
            Bitmap mirrored = Bitmap.createBitmap(b, 0, 0, b.getWidth(), b.getHeight(), m2, false);
            if (mirrored != b) {
                b.recycle();
            }
            b = mirrored;
        }
        ByteArrayOutputStream jb = new ByteArrayOutputStream(64 * 1024);
        b.compress(Bitmap.CompressFormat.JPEG, CAM_JPEG_Q, jb);
        b.recycle();
        byte[] jd = jb.toByteArray();
        ByteBuffer lb = ByteBuffer.allocate(4).order(ByteOrder.LITTLE_ENDIAN);
        lb.putInt(jd.length);
        st.write(lb.array(), 0, 4);
        st.write(jd, 0, jd.length);
    }

    /** 校验 .stream 完整性：按 [u32 LE len][jpeg] 链遍历，返回帧数（尾部截断则小于实际帧数）。 */
    private static int camCountFrames(byte[] stream) {
        int frames = 0;
        int i = 0;
        while (i + 4 <= stream.length) {
            int len = (stream[i] & 0xFF) | ((stream[i + 1] & 0xFF) << 8)
                    | ((stream[i + 2] & 0xFF) << 16) | ((stream[i + 3] & 0xFF) << 24);
            if (len <= 0 || i + 4 + len > stream.length) {
                break;
            }
            frames++;
            i += 4 + len;
        }
        return frames;
    }

    /** 从 "ls /dev/video*" 输出里取含 /dev/video 的那一行作为可用节点列表。 */
    private static String camVideoNodes(String out) {
        for (String line : out.split("\n")) {
            if (line.contains("/dev/video")) {
                return line.trim();
            }
        }
        return "(no /dev/video* nodes)";
    }

    /** 清掉逐条命令的 EXIT:0 回显行，输出过长时只留尾部。 */
    private static String camTail(String s) {
        s = s == null ? "" : s.replaceAll("(?m)^EXIT:\\d+\\s*$", "").trim();
        return s.length() <= 3000 ? s : s.substring(s.length() - 3000);
    }

    /** CMD_DEVICE_INFO (0x02)：基础信息 + 串口 / 摄像头 / 外设列表，采集失败降级为空或 Unknown。 */
    private JSONObject deviceInfo() throws Exception {
        JSONObject data = new JSONObject();
        data.put("androidVersion", Build.VERSION.RELEASE);
        data.put("sdk", Build.VERSION.SDK_INT);
        data.put("model", Build.MODEL);
        data.put("manufacturer", Build.MANUFACTURER);
        data.put("device", Build.DEVICE);
        data.put("displayVersion", Build.DISPLAY);
        data.put("sn", getprop("ro.serialno"));
        data.put("buildDate", getprop("ro.build.date"));
        data.put("kernelVersion", kernelVersion());
        data.put("serialPorts", serialPorts());
        data.put("cameras", cameras());
        data.put("peripherals", peripherals());
        return data;
    }

    /** CMD_SERIAL_OPEN (0x04)：按完整参数打开串口，起后台读线程持续把字节写入会话缓冲。 */
    private synchronized JSONObject serialOpen(JSONObject req) throws Exception {
        String serial = req.optString("serial", null);
        if (serial == null || serial.trim().isEmpty()) {
            throw new IllegalArgumentException("serial is required");
        }
        serial = serial.trim();
        int baud = req.optInt("baud", 0);
        if (!isValidBaud(baud)) {
            throw new IllegalArgumentException("baud must be one of: 1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600, 1500000, 3000000");
        }
        String parity = req.optString("parity", "none");
        int dataBits = req.optInt("dataBits", 8);
        int stopBits = req.optInt("stopBits", 1);
        String sttyCmd = buildSttyCommand(serial, baud, parity, dataBits, stopBits);
        File port = new File(serial);
        if (!port.exists()) {
            throw new IllegalArgumentException("serial port not found: " + serial);
        }
        shutdownSerialSession();
        Process sttyProc = Runtime.getRuntime().exec(sttyCmd);
        int sttyExit = sttyProc.waitFor();
        if (sttyExit != 0) {
            BufferedReader errReader = new BufferedReader(
                    new InputStreamReader(sttyProc.getErrorStream(), UTF8));
            String errLine = errReader.readLine();
            sttyProc.destroy();
            throw new Exception("stty failed (exit " + sttyExit + "): " +
                    (errLine == null ? "unknown error" : errLine));
        }
        sttyProc.destroy();
        RandomAccessFile raf = new RandomAccessFile(serial, "rw");
        SerialSession session = new SerialSession(serial, baud, parity, dataBits, stopBits, raf);
        session.readerThread = new Thread(new SerialReadTask(session), "dfm-probe-serial-read");
        session.readerThread.start();
        serialSession = session;
        JSONObject data = new JSONObject();
        data.put("serial", serial);
        data.put("baud", baud);
        data.put("parity", parity);
        data.put("dataBits", dataBits);
        data.put("stopBits", stopBits);
        data.put("stty", sttyCmd);
        return data;
    }

    /** CMD_SERIAL_READ (0x03)：取走会话缓冲里的增量字节，Hex/Txt 原样返回，不做帧解析。 */
    private synchronized JSONObject serialRead() throws Exception {
        SerialSession s = serialSession;
        if (s == null) {
            throw new IllegalArgumentException("serial session not opened");
        }
        if (!s.reading) {
            throw new IllegalArgumentException("serial read thread stopped");
        }
        byte[] bytes;
        synchronized (s.lock) {
            bytes = s.buffer.toByteArray();
            s.buffer.reset();
        }
        JSONObject data = new JSONObject();
        data.put("hex", toHex(bytes));
        data.put("text", new String(bytes, UTF8));
        data.put("bytes", bytes.length);
        return data;
    }

    /** CMD_SERIAL_SEND (0x05)：单次写出，或起后台线程按 times / interval 循环写出。 */
    private synchronized JSONObject serialSend(JSONObject req) throws Exception {
        if (req.optBoolean("stop", false)) {
            SerialSession s = serialSession;
            if (s != null) {
                stopSerialLoop(s);
            }
            JSONObject stopped = new JSONObject();
            stopped.put("stopped", s != null);
            return stopped;
        }
        String mode = req.optString("mode", "text");
        if (!"text".equals(mode) && !"hex".equals(mode)) {
            throw new IllegalArgumentException("mode must be text / hex");
        }
        String payload = req.optString("payload", null);
        if (payload == null || payload.isEmpty()) {
            throw new IllegalArgumentException("payload is required");
        }
        byte[] bytes = "hex".equals(mode) ? hexToBytes(payload) : payload.getBytes(UTF8);
        if (bytes.length == 0) {
            throw new IllegalArgumentException("payload is empty");
        }
        int times = req.optInt("times", 1);
        int interval = req.optInt("interval", 500);
        if (times < 0 || times > 10000) {
            throw new IllegalArgumentException("times must be 0..10000 (0 = until stopped)");
        }
        if (interval < 0 || interval > 60000) {
            throw new IllegalArgumentException("interval must be 0..60000ms");
        }
        SerialSession s = serialSession;
        if (s == null) {
            throw new IllegalArgumentException("serial session not opened");
        }
        JSONObject data = new JSONObject();
        data.put("mode", mode);
        data.put("bytes", bytes.length);
        data.put("times", times);
        data.put("interval", interval);
        if (times == 1) {
            s.raf.write(bytes);
            data.put("loop", false);
            return data;
        }
        stopSerialLoop(s);
        s.looping = true;
        s.sendThread = new Thread(new SerialSendTask(s, bytes, times, interval),
                "dfm-probe-serial-send");
        s.sendThread.start();
        data.put("loop", true);
        return data;
    }

    /** CMD_SERIAL_CLOSE (0x07)：停读线程与循环发送线程、关串口、清缓冲。 */
    private synchronized JSONObject serialClose() throws Exception {
        boolean had = serialSession != null;
        shutdownSerialSession();
        JSONObject data = new JSONObject();
        data.put("closed", had);
        return data;
    }

    /** 服务销毁或重新 OPEN 时清理会话；可重复调用。 */
    private synchronized void shutdownSerialSession() {
        SerialSession s = serialSession;
        serialSession = null;
        if (s == null) {
            return;
        }
        s.reading = false;
        stopSerialLoop(s);
        Thread reader = s.readerThread;
        if (reader != null) {
            reader.interrupt();
        }
        try {
            s.raf.close();
        } catch (Exception ignored) {
        }
        synchronized (s.lock) {
            s.buffer.reset();
        }
    }

    private static void stopSerialLoop(SerialSession s) {
        s.looping = false;
        Thread send = s.sendThread;
        if (send != null) {
            send.interrupt();
        }
    }

    /** 读线程写入缓冲；超过上限时丢弃最旧的字节。 */
    private static void appendSerialBytes(SerialSession s, byte[] chunk, int n) {
        synchronized (s.lock) {
            if (s.buffer.size() + n > MAX_SERIAL_BUFFER) {
                byte[] old = s.buffer.toByteArray();
                s.buffer.reset();
                int keep = Math.max(0, MAX_SERIAL_BUFFER - n);
                int drop = old.length - keep;
                if (drop < old.length) {
                    s.buffer.write(old, drop, old.length - drop);
                }
            }
            s.buffer.write(chunk, 0, n);
        }
    }

    /** 拼 stty 命令：端口 / 波特率 / 校验位 / 数据位 / 停止位，静态纯逻辑便于校验。 */
    private static String buildSttyCommand(String serial, int baud, String parity,
                                           int dataBits, int stopBits) {
        if (serial == null || serial.isEmpty() || !serial.matches("[A-Za-z0-9_/.-]+")) {
            throw new IllegalArgumentException("serial must be a plain device path");
        }
        if (!"none".equals(parity) && !"odd".equals(parity) && !"even".equals(parity)) {
            throw new IllegalArgumentException("parity must be none / odd / even");
        }
        if (dataBits < 5 || dataBits > 8) {
            throw new IllegalArgumentException("dataBits must be 5 / 6 / 7 / 8");
        }
        if (stopBits != 1 && stopBits != 2) {
            throw new IllegalArgumentException("stopBits must be 1 / 2");
        }
        StringBuilder cmd = new StringBuilder();
        cmd.append("stty -F ").append(serial).append(' ').append(baud)
                .append(" cs").append(dataBits);
        if ("none".equals(parity)) {
            cmd.append(" -parenb");
        } else if ("odd".equals(parity)) {
            cmd.append(" parenb parodd");
        } else {
            cmd.append(" parenb -parodd");
        }
        cmd.append(stopBits == 2 ? " cstopb" : " -cstopb");
        cmd.append(" raw -echo");
        return cmd.toString();
    }

    /** Hex 字符串转字节；允许空白分隔，奇数位或非法字符明确报错。 */
    private static byte[] hexToBytes(String hex) {
        String cleaned = hex.replaceAll("\\s+", "");
        if (cleaned.isEmpty()) {
            throw new IllegalArgumentException("hex payload is empty");
        }
        if ((cleaned.length() & 1) != 0) {
            throw new IllegalArgumentException("hex payload must have even length");
        }
        byte[] out = new byte[cleaned.length() / 2];
        for (int i = 0; i < out.length; i++) {
            int hi = Character.digit(cleaned.charAt(i * 2), 16);
            int lo = Character.digit(cleaned.charAt(i * 2 + 1), 16);
            if (hi < 0 || lo < 0) {
                throw new IllegalArgumentException("hex payload has invalid digit: "
                        + cleaned.substring(i * 2, i * 2 + 2));
            }
            out[i] = (byte) ((hi << 4) | lo);
        }
        return out;
    }

    private static String toHex(byte[] bytes) {
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < bytes.length; i++) {
            if (i > 0) {
                sb.append(' ');
            }
            sb.append(String.format("%02X", bytes[i]));
        }
        return sb.toString();
    }

    // ===== shell 执行 / 截屏 / 拍照（0x08 / 0x09 / 0x0A）=====

    /** CMD_SHELL (0x08)：PC 下发 shell 命令，设备端执行并回传输出。root=true 时经 su 以 root 运行。 */
    JSONObject shellExec(JSONObject req) throws Exception {
        String command = req.optString("command", null);
        if (command == null || command.trim().isEmpty()) {
            throw new IllegalArgumentException("command is required");
        }
        int timeoutSec = req.optInt("timeout", 30);
        if (timeoutSec < 1 || timeoutSec > 120) {
            throw new IllegalArgumentException("timeout must be 1..120 seconds");
        }
        boolean root = req.optBoolean("root", false);
        // 本设备的 su 不支持 -c，root 模式固定用 "su 0 sh -c <command>" 形式
        ProcessBuilder pb = root
                ? new ProcessBuilder("su", "0", "sh", "-c", command)
                : new ProcessBuilder("sh", "-c", command);
        pb.redirectErrorStream(false);
        Process proc = pb.start();
        boolean[] outTrunc = new boolean[1];
        boolean[] errTrunc = new boolean[1];
        AtomicReference<byte[]> outRef = new AtomicReference<>(new byte[0]);
        AtomicReference<byte[]> errRef = new AtomicReference<>(new byte[0]);
        Thread outThread = new Thread(new Runnable() {
            @Override
            public void run() {
                try {
                    outRef.set(drainStream(proc.getInputStream(), outTrunc));
                } catch (Exception ignored) {
                }
            }
        }, "dfm-probe-shell-out");
        Thread errThread = new Thread(new Runnable() {
            @Override
            public void run() {
                try {
                    errRef.set(drainStream(proc.getErrorStream(), errTrunc));
                } catch (Exception ignored) {
                }
            }
        }, "dfm-probe-shell-err");
        outThread.start();
        errThread.start();
        int exit = -1;
        if (proc.waitFor(timeoutSec, TimeUnit.SECONDS)) {
            outThread.join();
            errThread.join();
            exit = proc.exitValue();
        } else {
            // 超时强杀，exit 保留 -1 表示未正常退出
            proc.destroyForcibly();
            outThread.join(2000);
            errThread.join(2000);
        }
        JSONObject data = new JSONObject();
        data.put("stdout", new String(outRef.get(), UTF8));
        data.put("stderr", new String(errRef.get(), UTF8));
        data.put("exit", exit);
        data.put("truncated", outTrunc[0] || errTrunc[0]);
        return data;
    }

    /** 读完整个流并按字节截断；无效 UTF-8 由 String 解码兜底替换。 */
    private static byte[] drainStream(InputStream is, boolean[] truncated) throws IOException {
        ByteArrayOutputStream bos = new ByteArrayOutputStream();
        byte[] buf = new byte[4096];
        int n;
        while ((n = is.read(buf)) >= 0) {
            if (bos.size() < MAX_SHELL_OUTPUT) {
                bos.write(buf, 0, Math.min(n, MAX_SHELL_OUTPUT - bos.size()));
            } else {
                truncated[0] = true;
            }
        }
        return bos.toByteArray();
    }

    /** CMD_SCREENSHOT (0x09)：screencap 截屏存应用外部目录。app 直连被拒时回退 su（设备已 root）。 */
    private JSONObject takeScreenshot() throws Exception {
        File dir = ensureFilesDir();
        File out = new File(dir, "shot_"
                + new SimpleDateFormat("yyyyMMdd_HHmmss", Locale.US).format(new Date()) + ".png");
        String path = out.getAbsolutePath();
        String via;
        if (runCapture(new String[]{"screencap", "-p", path}, 20000) && out.length() > 0) {
            via = "direct";
        } else if (runCapture(new String[]{"su", "0", "screencap", "-p", path}, 20000) && out.length() > 0) {
            via = "su";
        } else {
            out.delete();
            throw new Exception("screencap failed (direct and su)");
        }
        JSONObject data = new JSONObject();
        data.put("path", path);
        data.put("size", out.length());
        data.put("via", via);
        return data;
    }

    /** 启动截屏进程，静默排干输出防管道堵塞，超时强杀；退出码非 0 视为失败。 */
    private static boolean runCapture(String[] cmd, long timeoutMs) {
        try {
            Process proc = new ProcessBuilder(cmd).start();
            Thread out = new Thread(new Runnable() {
                @Override
                public void run() {
                    try {
                        drainToNothing(proc.getInputStream());
                    } catch (Exception ignored) {
                    }
                }
            });
            Thread err = new Thread(new Runnable() {
                @Override
                public void run() {
                    try {
                        drainToNothing(proc.getErrorStream());
                    } catch (Exception ignored) {
                    }
                }
            });
            out.start();
            err.start();
            if (!proc.waitFor(timeoutMs, TimeUnit.MILLISECONDS)) {
                proc.destroyForcibly();
                return false;
            }
            out.join(1000);
            err.join(1000);
            return proc.exitValue() == 0;
        } catch (Exception e) {
            Log.w(TAG, "capture failed: " + cmd[0] + ": " + e);
            return false;
        }
    }

    private static void drainToNothing(InputStream is) throws IOException {
        byte[] buf = new byte[4096];
        while (is.read(buf) >= 0) {
        }
    }

    /** CMD_CAMERA (0x0A)：camera2 无预览拍照（JPEG）。CAMERA 权限由 PC 端 pm grant 预先授予。 */
    private JSONObject takePhoto(JSONObject req) throws Exception {
        CameraManager cm = (CameraManager) getSystemService(Context.CAMERA_SERVICE);
        if (checkSelfPermission(android.Manifest.permission.CAMERA)
                != PackageManager.PERMISSION_GRANTED) {
            return fail(500, "camera permission not granted (PC: pm grant com.dfm.probe android.permission.CAMERA)");
        }
        String cameraId = pickCamera(cm, req.optString("cameraId", null));
        Size[] sizes = cm.getCameraCharacteristics(cameraId)
                .get(CameraCharacteristics.SCALER_STREAM_CONFIGURATION_MAP)
                .getOutputSizes(ImageFormat.JPEG);
        Size best = sizes[0];
        for (Size s : sizes) {
            if ((long) s.getWidth() * s.getHeight() > (long) best.getWidth() * best.getHeight()) {
                best = s;
            }
        }
        HandlerThread thread = new HandlerThread("dfm-probe-camera");
        thread.start();
        Handler handler = new Handler(thread.getLooper());
        ImageReader reader = ImageReader.newInstance(
                best.getWidth(), best.getHeight(), ImageFormat.JPEG, 1);
        CameraDevice device = null;
        CameraCaptureSession session = null;
        try {
            AtomicReference<CameraDevice> opened = new AtomicReference<>();
            AtomicReference<String> openErr = new AtomicReference<>();
            CountDownLatch openLatch = new CountDownLatch(1);
            cm.openCamera(cameraId, new CameraDevice.StateCallback() {
                @Override
                public void onOpened(CameraDevice camera) {
                    opened.set(camera);
                    openLatch.countDown();
                }

                @Override
                public void onDisconnected(CameraDevice camera) {
                    openErr.compareAndSet(null, "camera disconnected");
                    openLatch.countDown();
                    camera.close();
                }

                @Override
                public void onError(CameraDevice camera, int error) {
                    openErr.compareAndSet(null, "camera open error " + error);
                    openLatch.countDown();
                    camera.close();
                }
            }, handler);
            if (!openLatch.await(5, TimeUnit.SECONDS) || opened.get() == null) {
                throw new Exception(openErr.get() != null ? openErr.get() : "camera open timeout");
            }
            device = opened.get();

            AtomicReference<CameraCaptureSession> conf = new AtomicReference<>();
            AtomicReference<String> confErr = new AtomicReference<>();
            CountDownLatch confLatch = new CountDownLatch(1);
            device.createCaptureSession(Collections.singletonList(reader.getSurface()),
                    new CameraCaptureSession.StateCallback() {
                        @Override
                        public void onConfigured(CameraCaptureSession s) {
                            conf.set(s);
                            confLatch.countDown();
                        }

                        @Override
                        public void onConfigureFailed(CameraCaptureSession s) {
                            confErr.compareAndSet(null, "capture session configure failed");
                            confLatch.countDown();
                        }
                    }, handler);
            if (!confLatch.await(5, TimeUnit.SECONDS) || conf.get() == null) {
                throw new Exception(confErr.get() != null ? confErr.get() : "capture session timeout");
            }
            session = conf.get();

            AtomicReference<Image> imageRef = new AtomicReference<>();
            AtomicReference<String> imgErr = new AtomicReference<>();
            CountDownLatch imgLatch = new CountDownLatch(1);
            reader.setOnImageAvailableListener(new ImageReader.OnImageAvailableListener() {
                @Override
                public void onImageAvailable(ImageReader r) {
                    try {
                        imageRef.compareAndSet(null, r.acquireLatestImage());
                    } catch (Exception e) {
                        imgErr.compareAndSet(null, "acquire image failed: " + e);
                    }
                    imgLatch.countDown();
                }
            }, handler);
            CaptureRequest.Builder rb = device.createCaptureRequest(CameraDevice.TEMPLATE_STILL_CAPTURE);
            rb.addTarget(reader.getSurface());
            rb.set(CaptureRequest.CONTROL_MODE, CameraMetadata.CONTROL_MODE_AUTO);
            rb.set(CaptureRequest.JPEG_QUALITY, (byte) 95);
            session.capture(rb.build(), new CameraCaptureSession.CaptureCallback() {
                @Override
                public void onCaptureFailed(CameraCaptureSession s, CaptureRequest r, CaptureFailure f) {
                    imgErr.compareAndSet(null, "capture failed, reason " + f.getReason());
                    imgLatch.countDown();
                }
            }, handler);
            if (!imgLatch.await(15, TimeUnit.SECONDS) || imageRef.get() == null) {
                throw new Exception(imgErr.get() != null ? imgErr.get() : "capture timeout");
            }

            Image image = imageRef.get();
            int width = image.getWidth();
            int height = image.getHeight();
            ByteBuffer buf = image.getPlanes()[0].getBuffer();
            byte[] jpeg = new byte[buf.remaining()];
            buf.get(jpeg);
            image.close();
            imageRef.set(null);

            File dir = ensureFilesDir();
            File out = new File(dir, "photo_"
                    + new SimpleDateFormat("yyyyMMdd_HHmmss", Locale.US).format(new Date()) + ".jpg");
            FileOutputStream fos = new FileOutputStream(out);
            try {
                fos.write(jpeg);
            } finally {
                fos.close();
            }
            JSONObject data = new JSONObject();
            data.put("path", out.getAbsolutePath());
            data.put("size", jpeg.length);
            data.put("cameraId", cameraId);
            data.put("width", width);
            data.put("height", height);
            return data;
        } finally {
            try {
                if (session != null) {
                    session.close();
                }
            } catch (Exception ignored) {
            }
            try {
                if (device != null) {
                    device.close();
                }
            } catch (Exception ignored) {
            }
            try {
                reader.close();
            } catch (Exception ignored) {
            }
            thread.quitSafely();
        }
    }

    /** 选摄像头：指定 cameraId 必须存在；未指定时默认后置，否则第一个。 */
    private String pickCamera(CameraManager cm, String cameraId) throws Exception {
        String[] ids = cm.getCameraIdList();
        if (ids.length == 0) {
            throw new IllegalStateException("no camera on device");
        }
        if (cameraId != null && !cameraId.trim().isEmpty()) {
            cameraId = cameraId.trim();
            for (String id : ids) {
                if (id.equals(cameraId)) {
                    return id;
                }
            }
            throw new IllegalArgumentException("camera not found: " + cameraId);
        }
        for (String id : ids) {
            Integer facing = cm.getCameraCharacteristics(id).get(CameraCharacteristics.LENS_FACING);
            if (facing != null && facing == CameraCharacteristics.LENS_FACING_BACK) {
                return id;
            }
        }
        return ids[0];
    }

    /** 截图 / 照片统一存应用外部私有目录（/sdcard/Android/data/com.dfm.probe/files），PC 端 adb 可读。 */
    private File ensureFilesDir() throws Exception {
        File dir = getExternalFilesDir(null);
        if (dir == null) {
            throw new Exception("external files dir unavailable");
        }
        if (!dir.exists() && !dir.mkdirs()) {
            throw new Exception("cannot create files dir: " + dir);
        }
        return dir;
    }

    /** CMD_ACCESSIBILITY_SETTINGS (0x0B)：无障碍服务未启用时跳转系统设置页，已启用则直接返回不跳页。 */
    private JSONObject openAccessibilitySettings() throws Exception {
        JSONObject data = new JSONObject();
        boolean enabled = DfmAccessibilityService.instance() != null;
        data.put("enabled", enabled);
        if (enabled) {
            Log.i(TAG, "accessibility service already enabled, skip settings page");
            data.put("started", false);
            return data;
        }
        Intent intent = new Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS);
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        startActivity(intent);
        Log.i(TAG, "accessibility settings page requested");
        data.put("started", true);
        return data;
    }

    /** CMD_ACCESSIBILITY_TREE (0x0C)：返回当前活动窗口组件树（需无障碍服务已启用）；未启用时保底拉起设置页并回明确应答。 */
    private JSONObject accessibilityTree() throws Exception {
        DfmAccessibilityService svc = DfmAccessibilityService.instance();
        if (svc == null) {
            // 保底：无障碍权限缺失时不只报错，直接把设置页拉到前台，让用户就地启用
            openAccessibilitySettings();
            return fail(500, "accessibility service not enabled (已在设备上打开无障碍设置页，请启用 DFM Probe 后重试)");
        }
        AccessibilityNodeInfo root = svc.getRootInActiveWindow();
        if (root == null) {
            return fail(500, "no active window for accessibility");
        }
        AtomicInteger count = new AtomicInteger(0);
        JSONObject rootNode = serializeNode(root, count);
        JSONObject data = new JSONObject();
        CharSequence pkg = root.getPackageName();
        data.put("package", pkg == null ? "" : pkg.toString());
        data.put("nodeCount", Math.min(count.get(), MAX_TREE_NODES));
        data.put("truncated", count.get() > MAX_TREE_NODES);
        data.put("root", rootNode);
        return ok(data);
    }

    /** CMD_ACCESSIBILITY_CLICK (0x0D)：按 id/text/desc 匹配节点并对其执行 ACTION_CLICK（无障碍点击，不依赖坐标）。 */
    JSONObject accessibilityClick(JSONObject req) throws Exception {
        String id = req.optString("id", "").trim();
        String text = req.optString("text", "").trim();
        String desc = req.optString("desc", "").trim();
        if (id.isEmpty() && text.isEmpty() && desc.isEmpty()) {
            return fail(400, "id/text/desc 至少填一项");
        }
        if (id.length() > 200 || text.length() > 200 || desc.length() > 200) {
            return fail(400, "id/text/desc 每项最长 200 字符");
        }
        DfmAccessibilityService svc = DfmAccessibilityService.instance();
        if (svc == null) {
            // 保底：无障碍权限缺失时直接把设置页拉到前台
            openAccessibilitySettings();
            return fail(500, "accessibility service not enabled (已在设备上打开无障碍设置页，请启用 DFM Probe 后重试)");
        }
        AccessibilityNodeInfo root = svc.getRootInActiveWindow();
        if (root == null) {
            return fail(500, "no active window for accessibility");
        }
        AccessibilityNodeInfo target = findClickableNode(root, id, text, desc, new AtomicInteger(0));
        if (target == null) {
            StringBuilder cond = new StringBuilder();
            if (!id.isEmpty()) cond.append("id=").append(id).append(' ');
            if (!text.isEmpty()) cond.append("text=").append(text).append(' ');
            if (!desc.isEmpty()) cond.append("desc=").append(desc);
            return fail(404, "无障碍树未找到节点（" + cond.toString().trim() + "）；请确认目标界面在前台");
        }
        boolean clicked = target.performAction(AccessibilityNodeInfo.ACTION_CLICK);
        if (!clicked) {
            // 节点存在但不可点击（如纯展示 TextView）：无障碍手势在屏幕坐标中心派发点击兜底（API 24+，无需 root）
            Rect r = new Rect();
            target.getBoundsInScreen(r);
            float cx = (r.left + r.right) / 2f;
            float cy = (r.top + r.bottom) / 2f;
            clicked = svc.dispatchTap(cx, cy);
            if (!clicked) {
                return fail(500, "节点不支持 ACTION_CLICK 且手势派发失败（clickable=false, dispatchGesture=false）");
            }
        }
        JSONObject data = new JSONObject();
        data.put("clicked", clicked);
        data.put("id", id);
        data.put("text", text);
        data.put("desc", desc);
        return ok(data);
    }

    /** 深度优先找首个匹配节点：id 精确相等、text/desc 包含，填了的条件同时满足；命中后若自身不可点击则向上回溯最近可点击祖先。 */
    private AccessibilityNodeInfo findClickableNode(AccessibilityNodeInfo node,
                                                    String id, String text, String desc,
                                                    AtomicInteger guard) {
        if (guard.incrementAndGet() > MAX_TREE_NODES) {
            return null;
        }
        if (nodeMatches(node, id, text, desc)) {
            if (node.isClickable()) {
                return node;
            }
            AccessibilityNodeInfo p = node.getParent();
            while (p != null) {
                if (p.isClickable()) {
                    return p;
                }
                AccessibilityNodeInfo next = p.getParent();
                p.recycle();
                p = next;
            }
            // 自身与祖先链都不可点击：返回匹配节点本身，由外层按其坐标 tap 兜底
            return node;
        }
        // 同节点匹配失败但填了 id：尝试祖先组合匹配（id 由带相同 text/desc 的祖先提供）
        if (!id.isEmpty()) {
            AccessibilityNodeInfo ancestor = matchWithAncestorId(node, id, text, desc);
            if (ancestor != null) {
                if (ancestor.isClickable()) {
                    return ancestor;
                }
                AccessibilityNodeInfo p = ancestor.getParent();
                while (p != null) {
                    if (p.isClickable()) {
                        return p;
                    }
                    AccessibilityNodeInfo next = p.getParent();
                    p.recycle();
                    p = next;
                }
                // 祖先链不可点击：返回祖先本身（id+text/desc 命中处），由外层按坐标 tap 兜底
                return ancestor;
            }
        }
        for (int i = 0; i < node.getChildCount(); i++) {
            AccessibilityNodeInfo child = node.getChild(i);
            if (child == null) {
                continue;
            }
            AccessibilityNodeInfo hit = findClickableNode(child, id, text, desc, guard);
            if (hit != null) {
                if (hit != child) {
                    child.recycle();
                }
                return hit;
            }
            child.recycle();
        }
        return null;
    }

    private boolean nodeMatches(AccessibilityNodeInfo node, String id, String text, String desc) {
        if (!id.isEmpty() && !id.equals(node.getViewIdResourceName())) {
            return false;
        }
        CharSequence t = node.getText();
        if (!text.isEmpty() && (t == null || !t.toString().contains(text))) {
            return false;
        }
        CharSequence d = node.getContentDescription();
        if (!desc.isEmpty() && (d == null || !d.toString().contains(desc))) {
            return false;
        }
        return true;
    }

    /**
     * 祖先增强匹配：命中节点无 resource-id 但步骤同时填了 id 时，
     * 允许 id 由「带相同 text/desc 的祖先」提供（跨节点组合，应对
     * 可点击容器有 id、文本在子节点的布局）。祖先 text/desc 也必须满足条件，
     * 避免 id 与文本跨层拼凑导致误点。
     */
    private AccessibilityNodeInfo matchWithAncestorId(AccessibilityNodeInfo node, String id, String text, String desc) {
        AccessibilityNodeInfo cur = node;
        int depth = 0;
        while (cur != null && depth < 8) {
            AccessibilityNodeInfo parent = cur.getParent();
            depth++;
            if (parent == null) {
                break;
            }
            if (nodeMatches(parent, id, text, desc)) {
                return parent;
            }
            cur = parent;
        }
        return null;
    }

    /** 递归序列化无障碍节点：超过 MAX_TREE_NODES 停止展开（该子树返回 null，不计入 children）。 */
    private JSONObject serializeNode(AccessibilityNodeInfo node, AtomicInteger count) throws Exception {
        if (count.incrementAndGet() > MAX_TREE_NODES) {
            return null;
        }
        JSONObject o = new JSONObject();
        CharSequence cls = node.getClassName();
        o.put("class", cls == null ? "" : cls.toString());
        CharSequence text = node.getText();
        o.put("text", text == null ? "" : text.toString());
        CharSequence desc = node.getContentDescription();
        o.put("desc", desc == null ? "" : desc.toString());
        CharSequence id = node.getViewIdResourceName();
        o.put("id", id == null ? "" : id.toString());
        Rect bounds = new Rect();
        node.getBoundsInScreen(bounds);
        o.put("bounds", bounds.left + "," + bounds.top + "," + bounds.right + "," + bounds.bottom);
        o.put("clickable", node.isClickable());
        o.put("scrollable", node.isScrollable());
        o.put("enabled", node.isEnabled());
        JSONArray children = new JSONArray();
        for (int i = 0; i < node.getChildCount(); i++) {
            AccessibilityNodeInfo child = node.getChild(i);
            if (child == null) {
                continue;
            }
            JSONObject c = serializeNode(child, count);
            if (c != null) {
                children.put(c);
            }
        }
        o.put("children", children);
        return o;
    }

    /** 设备端只保持一个串口会话：OPEN 后常驻，直到 CLOSE 或服务销毁。 */
    private static final class SerialSession {
        final String serial;
        final int baud;
        final String parity;
        final int dataBits;
        final int stopBits;
        final RandomAccessFile raf;
        final ByteArrayOutputStream buffer = new ByteArrayOutputStream();
        final Object lock = new Object();
        volatile boolean reading = true;
        volatile boolean looping = false;
        volatile Thread readerThread;
        volatile Thread sendThread;

        SerialSession(String serial, int baud, String parity, int dataBits,
                      int stopBits, RandomAccessFile raf) {
            this.serial = serial;
            this.baud = baud;
            this.parity = parity;
            this.dataBits = dataBits;
            this.stopBits = stopBits;
            this.raf = raf;
        }
    }

    /** 打开串口后持续读：read() 返回多少就追加多少，不做帧解析。 */
    private final class SerialReadTask implements Runnable {
        private final SerialSession session;

        SerialReadTask(SerialSession session) {
            this.session = session;
        }

        @Override
        public void run() {
            byte[] chunk = new byte[512];
            while (session.reading && running) {
                try {
                    int n = session.raf.read(chunk, 0, chunk.length);
                    if (n > 0) {
                        appendSerialBytes(session, chunk, n);
                    } else {
                        Thread.sleep(10);
                    }
                } catch (Exception e) {
                    Log.w(TAG, "serial read thread stopped: " + e);
                    break;
                }
            }
            session.reading = false;
        }
    }

    /** 循环发送：times=0 表示一直发到 stop / CLOSE。 */
    private final class SerialSendTask implements Runnable {
        private final SerialSession session;
        private final byte[] bytes;
        private final int times;
        private final int interval;

        SerialSendTask(SerialSession session, byte[] bytes, int times, int interval) {
            this.session = session;
            this.bytes = bytes;
            this.times = times;
            this.interval = interval;
        }

        @Override
        public void run() {
            int sent = 0;
            while (session.looping && running && (times == 0 || sent < times)) {
                try {
                    session.raf.write(bytes);
                    sent++;
                    if (interval > 0) {
                        Thread.sleep(interval);
                    }
                } catch (InterruptedException e) {
                    Thread.currentThread().interrupt();
                    break;
                } catch (Exception e) {
                    Log.w(TAG, "serial send thread stopped: " + e);
                    break;
                }
            }
            session.looping = false;
        }
    }

    /** 检查波特率是否在支持的列表中（对齐 serial-writer 的 baud_speed）。 */
    private static boolean isValidBaud(int baud) {
        return baud == 1200 || baud == 2400 || baud == 4800 || baud == 9600 ||
                baud == 19200 || baud == 38400 || baud == 57600 || baud == 115200 ||
                baud == 230400 || baud == 460800 || baud == 921600 ||
                baud == 1500000 || baud == 3000000;
    }

    /** 读取单个 getprop；不可读时返回 Unknown，不抛异常。 */
    private static String getprop(String key) {
        Process proc = null;
        BufferedReader reader = null;
        try {
            proc = Runtime.getRuntime().exec("getprop " + key);
            reader = new BufferedReader(new InputStreamReader(proc.getInputStream(), UTF8));
            String line = reader.readLine();
            return line == null || line.trim().isEmpty() ? "Unknown" : line.trim();
        } catch (Exception e) {
            Log.w(TAG, "getprop " + key + " failed: " + e);
            return "Unknown";
        } finally {
            if (reader != null) {
                try {
                    reader.close();
                } catch (Exception ignored) {
                }
            }
            if (proc != null) {
                proc.destroy();
            }
        }
    }

    /** /proc/version 首行；不可读时返回 Unknown。 */
    private static String kernelVersion() {
        FileInputStream in = null;
        BufferedReader reader = null;
        try {
            in = new FileInputStream("/proc/version");
            reader = new BufferedReader(new InputStreamReader(in, UTF8));
            String line = reader.readLine();
            return line == null || line.trim().isEmpty() ? "Unknown" : line.trim();
        } catch (Exception e) {
            Log.w(TAG, "kernel version failed: " + e);
            return "Unknown";
        } finally {
            if (reader != null) {
                try {
                    reader.close();
                } catch (Exception ignored) {
                }
            }
            if (in != null) {
                try {
                    in.close();
                } catch (Exception ignored) {
                }
            }
        }
    }

    /** 枚举 /dev 下的串口节点（ttyS* / ttyUSB* / ttyACM*），不可读时返回空数组。 */
    private static JSONArray serialPorts() throws Exception {
        JSONArray arr = new JSONArray();
        File dev = new File("/dev");
        String[] names = dev.list();
        if (names == null) {
            return arr;
        }
        Arrays.sort(names);
        for (String name : names) {
            if (name.startsWith("ttyS") || name.startsWith("ttyUSB") || name.startsWith("ttyACM")) {
                arr.put("/dev/" + name);
            }
        }
        return arr;
    }

    /** 用 CameraManager 枚举摄像头 id、朝向与分辨率；不可用时返回空数组。 */
    private JSONArray cameras() {
        JSONArray arr = new JSONArray();
        try {
            CameraManager manager = (CameraManager) getSystemService(Context.CAMERA_SERVICE);
            if (manager == null) {
                return arr;
            }
            for (String id : manager.getCameraIdList()) {
                JSONObject item = new JSONObject();
                item.put("id", id);
                try {
                    CameraCharacteristics c = manager.getCameraCharacteristics(id);
                    Integer facing = c.get(CameraCharacteristics.LENS_FACING);
                    int f = facing == null ? -1 : facing;
                    item.put("facing", f == CameraCharacteristics.LENS_FACING_FRONT ? "front"
                            : f == CameraCharacteristics.LENS_FACING_BACK ? "back"
                            : f == CameraCharacteristics.LENS_FACING_EXTERNAL ? "external" : "unknown");
                    Size size = c.get(CameraCharacteristics.SENSOR_INFO_PIXEL_ARRAY_SIZE);
                    item.put("resolution", size == null ? "unknown" : size.getWidth() + "x" + size.getHeight());
                } catch (Exception e) {
                    Log.w(TAG, "camera " + id + " characteristics failed: " + e);
                    item.put("facing", "unknown");
                    item.put("resolution", "unknown");
                }
                arr.put(item);
            }
        } catch (Exception e) {
            Log.w(TAG, "camera list failed: " + e);
        }
        return arr;
    }

    /** 枚举输入设备与 USB 设备；不可用时返回空数组。 */
    private JSONArray peripherals() {
        JSONArray arr = new JSONArray();
        try {
            InputManager im = (InputManager) getSystemService(Context.INPUT_SERVICE);
            if (im != null) {
                for (int id : im.getInputDeviceIds()) {
                    InputDevice d = InputDevice.getDevice(id);
                    String name = d == null ? null : d.getName();
                    JSONObject item = new JSONObject();
                    item.put("type", "input");
                    item.put("id", id);
                    item.put("name", name == null ? "unknown" : name);
                    arr.put(item);
                }
            }
        } catch (Exception e) {
            Log.w(TAG, "input devices failed: " + e);
        }
        try {
            UsbManager um = (UsbManager) getSystemService(Context.USB_SERVICE);
            if (um != null) {
                for (UsbDevice u : um.getDeviceList().values()) {
                    String name = u.getProductName();
                    JSONObject item = new JSONObject();
                    item.put("type", "usb");
                    item.put("id", u.getDeviceId());
                    item.put("name", name == null ? u.getDeviceName() : name);
                    item.put("vendorId", u.getVendorId());
                    item.put("productId", u.getProductId());
                    arr.put(item);
                }
            }
        } catch (Exception e) {
            Log.w(TAG, "usb devices failed: " + e);
        }
        return arr;
    }

    private static void sendFrame(OutputStream out, int seq, int cmd, byte[] data) throws Exception {
        if (data.length > MAX_DATA) {
            throw new IllegalArgumentException("frame data too large: " + data.length);
        }
        out.write(buildFrame(seq, cmd, data));
        out.flush();
    }

    private static byte[] buildFrame(int seq, int cmd, byte[] data) {
        byte[] frame = new byte[8 + data.length];
        frame[0] = (byte) 0xAA;
        frame[1] = (byte) 0x55;
        frame[2] = (byte) seq;
        frame[3] = (byte) (data.length & 0xFF);
        frame[4] = (byte) ((data.length >> 8) & 0xFF);
        frame[5] = (byte) cmd;
        System.arraycopy(data, 0, frame, 6, data.length);
        int crc = crc16(frame, 2, 4 + data.length);
        frame[6 + data.length] = (byte) (crc & 0xFF);
        frame[7 + data.length] = (byte) ((crc >> 8) & 0xFF);
        return frame;
    }

    /** CRC16-Modbus，覆盖 seq + len(2B) + cmd + data；结果以低字节在前写入帧尾。 */
    private static int crc16(byte[] b, int off, int len) {
        int crc = 0xFFFF;
        for (int i = off; i < off + len; i++) {
            crc ^= (b[i] & 0xFF);
            for (int j = 0; j < 8; j++) {
                if ((crc & 1) != 0) {
                    crc = (crc >> 1) ^ 0xA001;
                } else {
                    crc >>= 1;
                }
            }
        }
        return crc & 0xFFFF;
    }

    private static int findHeader(byte[] b, int from) {
        for (int i = from; i + 1 < b.length; i++) {
            if ((b[i] & 0xFF) == 0xAA && (b[i + 1] & 0xFF) == 0x55) {
                return i;
            }
        }
        return -1;
    }

    /**
     * 用系统 TTS 朗读 text，读完/失败后返回统一信封（由调用方回给 PC）。
     *
     * TTS 引擎懒初始化：首次调用才创建并同步等待引擎就绪（最多 30 秒，讯飞冷启动实测 ~13 秒），
     * 失败回 code 500，连接保持不断。
     */
    JSONObject speak(String text) throws Exception {
        String t = text.trim();
        final TextToSpeech engine = ensureTts();
        if (engine == null) {
            return fail(500, "tts engine unavailable");
        }
        final CountDownLatch done = new CountDownLatch(1);
        final int[] outcome = {TextToSpeech.ERROR};
        engine.setOnUtteranceProgressListener(new UtteranceProgressListener() {
            @Override
            public void onStart(String utteranceId) {
            }

            @Override
            public void onDone(String utteranceId) {
                outcome[0] = TextToSpeech.SUCCESS;
                done.countDown();
            }

            @Override
            public void onError(String utteranceId) {
                outcome[0] = TextToSpeech.ERROR;
                done.countDown();
            }
        });
        HashMap<String, String> params = new HashMap<>();
        params.put(TextToSpeech.Engine.KEY_PARAM_UTTERANCE_ID, "dfm-probe");
        int queued = engine.speak(t, TextToSpeech.QUEUE_FLUSH, params);
        if (queued != TextToSpeech.SUCCESS) {
            return fail(500, "tts speak rejected: " + queued);
        }
        // 朗读最长等 60 秒，超时按失败处理但不中断连接
        if (!done.await(60, TimeUnit.SECONDS)) {
            return fail(500, "tts timeout");
        }
        if (outcome[0] != TextToSpeech.SUCCESS) {
            return fail(500, "tts engine reported error");
        }
        JSONObject data = new JSONObject();
        data.put("status", "done");
        return ok(data);
    }

    /** 懒初始化 TTS 并等待就绪；失败返回 null（下次请求会重试）。
     *  讯飞引擎冷启动可能要 10 秒以上（实测 ~13s），等待窗口必须放宽。 */
    private synchronized TextToSpeech ensureTts() {
        if (tts != null) {
            return tts;
        }
        final CountDownLatch ready = new CountDownLatch(1);
        final boolean[] ok = {false};
        final TextToSpeech engine;
        try {
            engine = new TextToSpeech(getApplicationContext(), new TextToSpeech.OnInitListener() {
                @Override
                public void onInit(int status) {
                    Log.i(TAG, "tts init status: " + status);
                    ok[0] = status == TextToSpeech.SUCCESS;
                    ready.countDown();
                }
            });
        } catch (Exception e) {
            Log.w(TAG, "tts init failed: " + e);
            return null;
        }
        // 回调可能先于构造返回执行，成功与否只认 ok[]，不碰字段，
        // 避免初始化失败被构造赋值覆盖
        if (!await(ready) || !ok[0]) {
            try {
                engine.shutdown();
            } catch (Exception ignored) {
            }
            return null;
        }
        tts = engine;
        return engine;
    }

    private static boolean await(CountDownLatch latch) {
        try {
            return latch.await(30, TimeUnit.SECONDS);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            return false;
        }
    }

    private void shutdownTts() {
        TextToSpeech engine = tts;
        tts = null;
        if (engine != null) {
            try {
                engine.stop();
                engine.shutdown();
            } catch (Exception ignored) {
            }
        }
    }
}
