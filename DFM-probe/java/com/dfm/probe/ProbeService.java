package com.dfm.probe;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.os.Build;
import android.os.IBinder;
import android.speech.tts.TextToSpeech;
import android.speech.tts.UtteranceProgressListener;
import android.util.Log;

import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.nio.charset.Charset;
import java.util.HashMap;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;

/**
 * 常驻前台服务：监听设备本机 0.0.0.0:21104，等待 PC 主动连入。
 *
 * 二进制帧协议详见 probe与service通讯协议.md：
 *   AA 55 | 帧序(1B) | 长度(2B，低字节在前) | 命令字(1B) | 数据区 | CRC16(低字节在前)
 *   数据区为 UTF-8 JSON，最长 65535 字节。
 *   业务帧（0x00、0x01）校验通过先回 ACK(0x06)，再发同帧序响应帧（命令字|0x80 或 0xFF）。
 *   CRC 错、数据区不是合法 JSON 对象的帧直接丢弃，不回 ACK，由发送端超时重传。
 */
public class ProbeService extends Service {
    private static final String TAG = "DFMProbe";
    private static final String CHANNEL_ID = "dfm_probe";
    private static final int NOTIFY_ID = 1;
    private static final int DEVICE_PORT = 21104;
    private static final int MAX_DATA = 65535;
    private static final Charset UTF8 = Charset.forName("UTF-8");

    private static final int CMD_HEARTBEAT = 0x00;
    private static final int CMD_TTS = 0x01;
    private static final int CMD_ACK = 0x06;
    private static final int CMD_ERROR = 0xFF;

    private volatile boolean running = false;
    private volatile ServerSocket serverSocket;
    private volatile TextToSpeech tts;

    @Override
    public void onCreate() {
        super.onCreate();
        createChannel();
        startForeground(NOTIFY_ID, buildNotification());
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        // 启动广播每次请求都会重发，只有尚未监听时才起线程，避免重复 bind 端口
        if (!running) {
            running = true;
            new Thread(new ListenTask(), "dfm-probe-listener").start();
        }
        return START_NOT_STICKY;
    }

    @Override
    public void onDestroy() {
        running = false;
        ServerSocket s = serverSocket;
        if (s != null) {
            try {
                s.close();
            } catch (Exception ignored) {
            }
        }
        shutdownTts();
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    private void createChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            NotificationChannel channel = new NotificationChannel(
                    CHANNEL_ID, "DFM Probe", NotificationManager.IMPORTANCE_LOW);
            NotificationManager manager =
                    (NotificationManager) getSystemService(Context.NOTIFICATION_SERVICE);
            if (manager != null) {
                manager.createNotificationChannel(channel);
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
                serverSocket = null;
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
            boolean unknown = cmd != CMD_HEARTBEAT && cmd != CMD_TTS;
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
    private JSONObject speak(String text) throws Exception {
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
