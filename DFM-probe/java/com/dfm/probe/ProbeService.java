package com.dfm.probe;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.os.Build;
import android.os.IBinder;
import android.util.Log;

import org.json.JSONObject;

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
}
