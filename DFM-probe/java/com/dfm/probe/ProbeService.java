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

import java.util.HashMap;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;

import java.io.BufferedReader;
import java.io.BufferedWriter;
import java.io.InputStreamReader;
import java.io.OutputStreamWriter;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.nio.charset.Charset;

/**
 * 常驻前台服务：按 host/port 主动连回 PC，按行收发 JSON。
 *
 * 协议（UTF-8，换行分隔）：
 *   连上后设备先发 {"type":"hello","device":...,"sdk":...,"abi":...,"package":...}
 *   PC -> {"type":"ping","payload":"..."}  设备 -> {"type":"pong","payload":"..."}
 *   PC -> {"type":"echo","payload":"..."}  设备 -> {"type":"echo","payload":"..."}
 *   PC -> {"type":"tts","text":"..."}      设备 -> {"type":"tts","status":"done"|"error","message":...}
 *   PC -> {"type":"quit"}                  设备 -> {"type":"bye"} 后断开
 *   其它 type -> {"type":"error","message":...}
 */
public class ProbeService extends Service {
    private static final String TAG = "DFMProbe";
    private static final String CHANNEL_ID = "dfm_probe";
    private static final int NOTIFY_ID = 1;
    private static final int CONNECT_TIMEOUT_MS = 5000;
    private static final long RETRY_INTERVAL_MS = 3000;
    private static final Charset UTF8 = Charset.forName("UTF-8");

    private volatile String host;
    private volatile int port;
    private volatile boolean running = false;
    private volatile Socket currentSocket;
    private volatile TextToSpeech tts;

    @Override
    public void onCreate() {
        super.onCreate();
        createChannel();
        startForeground(NOTIFY_ID, buildNotification());
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent != null) {
            String h = intent.getStringExtra("host");
            int p = intent.getIntExtra("port", 0);
            if (h != null && p > 0) {
                boolean changed = !h.equals(host) || p != port;
                host = h;
                port = p;
                // 地址变了要打断旧连接，让连接线程用新地址重连
                if (changed && currentSocket != null) {
                    try {
                        currentSocket.close();
                    } catch (Exception ignored) {
                    }
                }
            }
        }
        if (!running && host != null) {
            running = true;
            new Thread(new ConnectTask(), "dfm-probe-connector").start();
        }
        return START_NOT_STICKY;
    }

    @Override
    public void onDestroy() {
        running = false;
        Socket s = currentSocket;
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
                .setContentText("等待连接")
                .setOngoing(true)
                .build();
    }

    private final class ConnectTask implements Runnable {
        @Override
        public void run() {
            while (running) {
                Socket socket = null;
                try {
                    String h = host;
                    int p = port;
                    Log.i(TAG, "connecting to " + h + ":" + p);
                    socket = new Socket();
                    socket.connect(new InetSocketAddress(h, p), CONNECT_TIMEOUT_MS);
                    currentSocket = socket;
                    Log.i(TAG, "connected");
                    serve(socket);
                } catch (Exception e) {
                    Log.w(TAG, "connect/serve failed: " + e);
                } finally {
                    currentSocket = null;
                    if (socket != null) {
                        try {
                            socket.close();
                        } catch (Exception ignored) {
                        }
                    }
                }
                if (!running) {
                    break;
                }
                try {
                    Thread.sleep(RETRY_INTERVAL_MS);
                } catch (InterruptedException e) {
                    break;
                }
            }
            Log.i(TAG, "connector stopped");
        }
    }

    private void serve(Socket socket) throws Exception {
        BufferedReader reader = new BufferedReader(
                new InputStreamReader(socket.getInputStream(), UTF8));
        BufferedWriter writer = new BufferedWriter(
                new OutputStreamWriter(socket.getOutputStream(), UTF8));

        JSONObject hello = new JSONObject();
        hello.put("type", "hello");
        hello.put("device", Build.MODEL);
        hello.put("sdk", Build.VERSION.SDK_INT);
        hello.put("abi", Build.SUPPORTED_ABIS[0]);
        hello.put("package", getPackageName());
        send(writer, hello);

        String line;
        while (running && (line = reader.readLine()) != null) {
            Log.i(TAG, "recv: " + line);
            JSONObject req = new JSONObject(line);
            String type = req.optString("type");
            JSONObject res = new JSONObject();
            if ("ping".equals(type)) {
                res.put("type", "pong");
                res.put("payload", req.optString("payload"));
            } else if ("echo".equals(type)) {
                res.put("type", "echo");
                res.put("payload", req.optString("payload"));
            } else if ("tts".equals(type)) {
                speak(req.optString("text"), res);
            } else if ("quit".equals(type)) {
                res.put("type", "bye");
                send(writer, res);
                break;
            } else {
                res.put("type", "error");
                res.put("message", "unknown type: " + type);
            }
            send(writer, res);
        }
    }

    private static void send(BufferedWriter writer, JSONObject obj) throws Exception {
        writer.write(obj.toString());
        writer.write("\n");
        writer.flush();
    }

    /**
     * 用系统 TTS 朗读 text，读完/失败后把结果写进 res（由调用方回给 PC）。
     *
     * TTS 引擎懒初始化：首次调用才创建并同步等待引擎就绪（最多 30 秒，讯飞冷启动实测 ~13 秒），
     * 失败回 {"type":"tts","status":"error"}，连接保持不断。
     */
    private void speak(String text, JSONObject res) throws Exception {
        res.put("type", "tts");
        String t = text == null ? "" : text.trim();
        if (t.length() == 0) {
            res.put("status", "error");
            res.put("message", "empty text");
            return;
        }
        final TextToSpeech engine = ensureTts();
        if (engine == null) {
            res.put("status", "error");
            res.put("message", "tts engine unavailable");
            return;
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
            res.put("status", "error");
            res.put("message", "tts speak rejected: " + queued);
            return;
        }
        // 朗读最长等 60 秒，超时按失败处理但不中断连接
        if (!done.await(60, TimeUnit.SECONDS)) {
            res.put("status", "error");
            res.put("message", "tts timeout");
            return;
        }
        if (outcome[0] == TextToSpeech.SUCCESS) {
            res.put("status", "done");
        } else {
            res.put("status", "error");
            res.put("message", "tts engine reported error");
        }
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
