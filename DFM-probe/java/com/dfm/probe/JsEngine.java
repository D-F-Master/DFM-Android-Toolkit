package com.dfm.probe;

import android.content.ClipData;
import android.content.ClipboardManager;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.util.Log;

import org.json.JSONArray;
import org.json.JSONObject;
import org.mozilla.javascript.Context;
import org.mozilla.javascript.ContextFactory;
import org.mozilla.javascript.EcmaError;
import org.mozilla.javascript.EvaluatorException;
import org.mozilla.javascript.FunctionObject;
import org.mozilla.javascript.RhinoException;
import org.mozilla.javascript.Scriptable;
import org.mozilla.javascript.ScriptableObject;
import org.mozilla.javascript.WrappedException;

import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.lang.reflect.Member;
import java.lang.reflect.Method;
import java.nio.charset.Charset;
import java.util.Random;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import java.util.zip.ZipEntry;
import java.util.zip.ZipInputStream;
import java.util.zip.ZipOutputStream;

/**
 * 设备端 JS 脚本引擎（方案3，0x13 执行 / 0x14 停止），见 docx/脚本方案3-设备端JS执行设计.md。
 *
 * 与 ScriptEngine（旧 JSON 引擎，0x10）并存：P0/P1 双轨迁移期两套都能跑，P3 再退役旧引擎。
 *
 * 设计要点：
 * - Rhino 1.8.1（与 AutoX 同版本），解释模式 optimizationLevel=-1：Android 上无法生成
 *   JVM 字节码，解释模式不需要自定义 ClassLoader / dex 缓存目录；
 * - 停止机制（P0-4）：Rhino 指令观察者每执行 N 条指令回调一次，回调里检查停止标记并抛
 *   ScriptStop 异常中断脚本；sleep 用可中断等待配合 thread.interrupt() 双保险；
 * - dfm.* API（P0-3）由 DfmBridge 宿主对象提供，桥接到 ProbeService 既有能力
 *   （0x08 shell / 0x01 TTS / 0x0D 无障碍点击 / dispatchGestureStroke 手势）；
 * - 脚本包为 zip（0x0E 解压，与旧格式共用入口），根目录含 script.js（可选 metadata.json），
 *   资源目录 templates/ apk/ media/ 与旧格式一致；
 * - 日志结构对齐 ScriptEngine：{no,type,ok,message,durationMs}，MAX_LOGS/MAX_LOG_MESSAGE 同旧引擎。
 */
public class JsEngine {
    private static final String TAG = "DFMProbe";
    private static final Charset UTF8 = Charset.forName("UTF-8");

    /** JS 脚本包根目录的入口文件名。 */
    static final String JS_ENTRY = "script.js";

    private static final int MAX_LOG_MESSAGE = 200;
    private static final int MAX_LOGS = 500;
    /** 指令观察者回调粒度：每 ~50 万条指令检查一次停止标记（间隔约几十毫秒）。 */
    private static final int INSTRUCTION_THRESHOLD = 500_000;
    /** 单条 shell 命令默认超时（秒），与 ScriptEngine 各步骤取值一致。 */
    private static final int SHELL_TIMEOUT = 15;

    /** 停止脚本时抛出的内部异常；穿过 Rhino 后被 run() 捕获转为正常返回。 */
    private static class ScriptStop extends RuntimeException {
        ScriptStop(String message) {
            super(message);
        }
    }

    private final ProbeService probe;

    /** 当前正在执行的线程（同一时刻只允许一个 JS 脚本，见 0x13 入口互斥），用于 interrupt 停止。 */
    private volatile Thread runThread;
    /** 停止标记：0x14 置位，脚本线程在指令观察者 / sleep / shell 间隙检查。 */
    private volatile boolean stopFlag;

    /** 当前执行轮号（1 起），由 runRhino 每轮更新；dfm.roundNo() 读给 {执行轮数} 变量用 */
    private volatile int currentRound;

    /** Rhino ContextFactory 全局单例：observeInstructionCount 挂停止检查。 */
    private static class DfmContextFactory extends ContextFactory {
        @Override
        protected void observeInstructionCount(Context cx, int instructionCount) {
            JsEngine engine = current.get();
            if (engine != null && engine.stopFlag) {
                throw new ScriptStop("脚本已停止（0x14）");
            }
            if (Thread.currentThread().isInterrupted()) {
                throw new ScriptStop("脚本已停止（线程中断）");
            }
        }

        @Override
        protected Context makeContext() {
            Context cx = super.makeContext();
            // Android 上必须解释模式（-1）：优化级别 >0 会尝试生成 JVM 字节码，
            // 而 Rhino 生成的字节码需要自定义 ClassLoader + dex 转换，无需引入
            cx.setOptimizationLevel(-1);
            cx.setLanguageVersion(Context.VERSION_ES6);
            cx.setInstructionObserverThreshold(INSTRUCTION_THRESHOLD);
            return cx;
        }
    }

    /** 与线程关联的引擎实例（ContextFactory 是全局的，回调里据此找到对应引擎）。 */
    private static final ThreadLocal<JsEngine> current = new ThreadLocal<>();

    JsEngine(ProbeService probe) {
        this.probe = probe;
        ContextFactory.initGlobal(new DfmContextFactory());
    }

    // ===== 命令字入口 =====

    /**
     * CMD_SCRIPT_RUN_JS (0x13)：执行 script_store/{script}/script.js。
     * 请求 {"script":"名字","repeat":N}；响应结构与 0x10 对齐（script/ok/error/summary/logs/truncated）。
     * 同一时刻只允许一个 JS 脚本执行（旧 0x10 JSON 引擎与之互不干扰）。
     */
    JSONObject run(String scriptName, int repeat) throws Exception {
        String name = safeName(scriptName, "script");
        if (repeat < 1) {
            repeat = 1;
        }
        if (repeat > 1000) {
            throw new IllegalArgumentException("repeat must be 1..1000");
        }
        File scriptDir = new File(storeDir(), name);
        File jsFile = new File(scriptDir, JS_ENTRY);
        if (!jsFile.isFile()) {
            throw new IllegalArgumentException(
                    "script.js not found: " + name + "（0x13 只执行 JS 脚本包；JSON 脚本请用 0x10）");
        }
        synchronized (this) {
            if (runThread != null && runThread.isAlive() && !stopFlag) {
                throw new IllegalArgumentException("另一个 JS 脚本正在执行（先发 0x14 停止）");
            }
            if (runThread != null && runThread.isAlive()) {
                // 已发 0x14 但线程仍存活（指令观察者覆盖不到的卡死，如原型链自引用）：
                // 无法安全强杀，放弃该线程（孤儿化为 daemon），放行新脚本
                Log.w(TAG, "js script thread wedged after stop, abandoning: "
                        + runThread.getName());
                runThread = null;
            }
            stopFlag = false;
        }

        // 每次执行新建线程：停止靠 stopFlag + interrupt，线程退出后引用释放
        final String fName = name;
        final File fDir = scriptDir;
        final int fRepeat = repeat;
        final JSONArray[] logsBox = new JSONArray[]{new JSONArray()};
        final String[] errorBox = {null};
        final long[] durationBox = {0};
        Thread t = new Thread(new Runnable() {
            @Override
            public void run() {
                current.set(JsEngine.this);
                scriptDirRef = fDir;
                long start = System.currentTimeMillis();
                try {
                    runRhino(fName, fDir, fRepeat, logsBox[0]);
                } catch (ScriptStop e) {
                    errorBox[0] = e.getMessage();
                } catch (RhinoException e) {
                    // 解释器模式下宿主异常会被包成 EvaluatorException（"Wrapped xxx: ..." 前缀，
                    // getWrappedException() 为 null），只能按消息识别；WrappedException 走根因
                    Throwable inner = e instanceof WrappedException
                            ? ((WrappedException) e).getWrappedException() : null;
                    if (inner instanceof ScriptStop) {
                        errorBox[0] = inner.getMessage();
                    } else if (e.getMessage() != null && e.getMessage().contains("JsEngine$ScriptStop")) {
                        String msg = e.getMessage();
                        int i = msg.indexOf(": ", msg.indexOf("Wrapped"));
                        errorBox[0] = i >= 0 ? msg.substring(i + 2) : "脚本已停止（0x14）";
                    } else {
                        errorBox[0] = describeThrowable(e);
                    }
                } catch (Throwable e) {
                    errorBox[0] = describeThrowable(e);
                } finally {
                    durationBox[0] = System.currentTimeMillis() - start;
                    scriptDirRef = null;
                    current.remove();
                    runThread = null;
                }
            }
        }, "dfm-probe-js-" + name);
        // daemon：卡死被放弃后不阻止进程退出
        t.setDaemon(true);
        synchronized (this) {
            runThread = t;
        }
        t.start();
        try {
            // 与帧处理同线程等待执行结束（PC 端 RESPONSE_TIMEOUT=120s；JS 引擎耗时可能更长，
            // 由 0x14 停止；此处同步返回保持协议简单，流式日志在 P1 再做）。
            // runThread != t：0x14 超时放弃线程后置 null，等待循环随即退出
            while (t.isAlive() && runThread == t) {
                t.join(1000);
            }
        } catch (InterruptedException e) {
            // 帧连接中断等意外情况：标记停止并等待线程退出，避免孤儿脚本线程继续操作设备
            stopFlag = true;
            t.interrupt();
            try {
                t.join(5000);
            } catch (InterruptedException ignored) {
            }
            throw new Exception("JS 脚本执行被中断");
        }
        if (stopFlag && errorBox[0] == null) {
            errorBox[0] = "脚本已停止";
        }

        int steps = 0;
        File meta = new File(scriptDir, "metadata.json");
        if (meta.isFile()) {
            try {
                steps = new JSONObject(readText(meta)).optInt("steps", 0);
            } catch (Exception ignored) {
            }
        }
        JSONObject summary = new JSONObject();
        summary.put("rounds", repeat);
        summary.put("steps", steps);
        summary.put("ok", errorBox[0] == null);
        summary.put("error", errorBox[0] == null ? JSONObject.NULL : errorBox[0]);
        summary.put("durationMs", durationBox[0]);
        JSONObject data = new JSONObject();
        data.put("script", name);
        data.put("ok", errorBox[0] == null);
        data.put("error", errorBox[0] == null ? JSONObject.NULL : errorBox[0]);
        data.put("summary", summary);
        data.put("logs", logsBox[0]);
        data.put("truncated", logsBox[0].length() >= MAX_LOGS);
        return data;
    }

    /**
     * CMD_SCRIPT_STOP (0x14)：停止正在执行的 JS 脚本。
     * 置停止标记（指令观察者检查）+ interrupt 脚本线程（sleep 可中断），幂等。
     */
    JSONObject stop() throws Exception {
        // 先捕获"到达时是否在跑"：脚本线程对标志响应极快，若在置位后再判断
        // isAlive 会恒得 false；wasRunning 必须表达"0x14 到达时有脚本在执行"
        Thread t0 = runThread;
        boolean was = (t0 != null && t0.isAlive() && !stopFlag) || stopFlag;
        stopFlag = true;
        Thread t = runThread;
        boolean abandoned = false;
        if (t != null && t.isAlive()) {
            t.interrupt();
            // 最多等 3s：正常脚本（解释模式 + 指令观察者 + 可中断 sleep）都会很快退出；
            // 超时仍存活视为卡死线程，放弃引用放行后续脚本（Java 线程无法安全强杀）
            long deadline = System.currentTimeMillis() + 3000;
            while (t.isAlive() && System.currentTimeMillis() < deadline) {
                t.join(200);
            }
            if (t.isAlive()) {
                abandoned = true;
                runThread = null;
            }
        }
        JSONObject data = new JSONObject();
        data.put("stopping", true);
        data.put("wasRunning", was || (t != null && t.isAlive()));
        data.put("forceAbandoned", abandoned);
        return data;
    }

    // ===== Rhino 执行 =====

    /** 实际进入 Rhino：建 Context、定义 dfm 宿主对象、按轮次 evaluateString。 */
    private void runRhino(String name, File scriptDir, int repeat, JSONArray logs) throws Exception {
        Scriptable scope;
        Context cx = DfmContextHolder.factory.enterContext();
        try {
            scope = cx.initStandardObjects();
            logsRef = logs;
            scopeRef = scope;
            DfmBridge.define(cx, scope, this, logs);
            String source = readText(new File(scriptDir, JS_ENTRY));
            // apiVersion 契约（设计文档 §3.3）：脚本必须声明 var apiVersion = 1;
            // var 声明只有 evaluateString 之后才落在作用域上，所以先正则预扫源码快速拒绝，
            // 运行后再以作用域值为准（P0 约定：missing/超版本都拒）
            java.util.regex.Matcher m = java.util.regex.Pattern
                    .compile("^\\s*var\\s+apiVersion\\s*=\\s*(\\d+)",
                            java.util.regex.Pattern.MULTILINE)
                    .matcher(source);
            if (!m.find() || Integer.parseInt(m.group(1)) > 1) {
                throw new IllegalArgumentException("脚本 apiVersion 缺失或高于设备支持版本（设备支持 1）");
            }
            for (int round = 1; round <= repeat; round++) {
                if (stopFlag) {
                    throw new ScriptStop("脚本已停止（0x14）");
                }
                // 编译器生成的脚本用 __stopRequested 标记 condition 的 stop 动作
                // （对齐 PC 引擎：stop = 结束整个脚本，后续轮次不再执行）
                scope.put("__stopRequested", scope, Boolean.FALSE);
                currentRound = round;
                // 直接在全局作用域上逐轮执行：evaluateString 每次都会重新执行顶层 var 声明。
                // 不能对 scope 自身 setPrototype(scope)（自引用原型链会让 getBase 无限循环，
                // 卡死线程且 0x14 无法中断——此前 ART 上 live-lock 的根因）
                Object result = cx.evaluateString(scope, source, JS_ENTRY, 1, null);
                // 运行后校验（权威）：var 声明已落在作用域上
                Object v = scope.get("apiVersion", scope);
                if (!(v instanceof Number) || ((Number) v).intValue() > 1) {
                    throw new IllegalArgumentException("脚本 apiVersion 缺失或高于设备支持版本（设备支持 1）");
                }
                // condition 的 stop 动作：本轮结束时终止整个脚本（不再进入下一轮）
                Object stopReq = scope.get("__stopRequested", scope);
                if (Boolean.TRUE.equals(stopReq)) {
                    engineLog(logs, 0, "round", true, "判断步骤触发 stop，脚本提前结束");
                    return;
                }
                engineLog(logs, 0, "round", true, "第 " + round + " 轮执行完成"
                        + (result != Context.getUndefinedValue() ? "，返回 " + abbreviate(Context.toString(result), 80) : ""));
            }
        } catch (RhinoException e) {
            // 解释器模式下宿主异常（如 ScriptStop）被包成 EvaluatorException：
            // "Wrapped com.dfm.probe.JsEngine$ScriptStop: 原消息"。剥壳后按原类型重抛，
            // 让外层 catch (ScriptStop) 记干净的 "脚本已停止（0x14）"。
            String msg = e.getMessage();
            if (msg != null && msg.contains("JsEngine$ScriptStop")) {
                int i = msg.indexOf(": ", msg.indexOf("Wrapped"));
                String text = i >= 0 ? msg.substring(i + 2) : "脚本已停止（0x14）";
                // RhinoException.getMessage() 尾部带 " (script.js#6)" 定位后缀，剥掉
                int j = text.lastIndexOf(" (");
                if (j > 0 && text.endsWith(")")) {
                    text = text.substring(0, j);
                }
                throw new ScriptStop(text);
            }
            throw new Exception(describeRhino(e));
        } finally {
            Context.exit();
        }
    }

    /** Rhino 报错转单行可读信息（脚本名:行号:消息）。 */
    private static String describeRhino(RhinoException e) {
        String msg = e.getMessage();
        int line = e.lineNumber();
        String src = e.sourceName();
        StringBuilder sb = new StringBuilder();
        if (src != null && !src.isEmpty()) {
            sb.append(src).append(':');
        }
        if (line > 0) {
            sb.append(line).append(": ");
        }
        sb.append(msg == null ? e.getClass().getSimpleName() : msg);
        return sb.toString();
    }

    /** 非 Rhino 异常转单行（含 WrappedException 的 Java 根因）。 */
    private static String describeThrowable(Throwable e) {
        // Rhino 会把宿主异常包进 WrappedException（消息带 "Wrapped xxx:" 前缀），剥掉包装层
        for (int i = 0; i < 5 && e instanceof WrappedException; i++) {
            Throwable inner = ((WrappedException) e).getWrappedException();
            if (inner == null) {
                break;
            }
            e = inner;
        }
        String msg = e.getMessage();
        String text = msg == null || msg.isEmpty() ? e.toString() : msg;
        return abbreviate(text, MAX_LOG_MESSAGE);
    }

    // ===== DfmBridge：dfm.* 宿主 API（P0-3 最小集） =====

    /**
     * dfm 宿主对象：注册为全局 dfm，同时把常用函数平铺到全局（tap()/sleep()/shell()…），
     * 手写脚本更顺手；方法直接桥接 ProbeService 既有实现，与 0x08/0x0D/0x10 行为一致。
     */
    static class DfmBridge {
        static void define(Context cx, Scriptable scope, final JsEngine engine, final JSONArray logs) {
            ScriptableObject global = (ScriptableObject) scope;
            // dfm 对象本身：脚本也可 dfm.tap(...) 调用
            ScriptableObject dfm = (ScriptableObject) cx.newObject(scope);
            dfm.setPrototype(scope);
            String[] names = {"tap", "longpress", "swipe", "input", "keyevent", "accClick",
                    "accExists", "sleep", "shell", "speak", "startApp", "stopApp", "installApk",
                    "uninstallApp", "deleteFolder", "reboot", "screenSize", "log", "randomDigits",
                    "volume", "screenshot", "installed", "stopped", "roundNo",
                    "waitFor", "waitForTap", "inputUnicode"};
            dfm.defineFunctionProperties(names, DfmBridge.class, ScriptableObject.DONTENUM);
            ScriptableObject.putProperty(global, "dfm", dfm);

            // 绑定引擎实例：静态 dfm 方法经 bridge() 取当前引擎（ThreadLocal），
            // 但执行入口固定为本次 define 的 engine，防跨脚本串扰
            bound = engine;
            boundLogs = logs;

            // 常用函数平铺到全局（同名不覆盖标准对象，如 print 保留 Rhino 内置）
            Method[] methods = DfmBridge.class.getDeclaredMethods();
            for (Method m : methods) {
                if (!java.lang.reflect.Modifier.isStatic(m.getModifiers())
                        || !java.lang.reflect.Modifier.isPublic(m.getModifiers())) {
                    continue;
                }
                String n = m.getName();
                if ("define".equals(n) || "bridge".equals(n) || n.startsWith("dfm")) {
                    continue;
                }
                if (ScriptableObject.hasProperty(global, n)) {
                    continue; // 不覆盖 Rhino 标准全局
                }
                Member member = m;
                FunctionObject fn = new FunctionObject(n, member, global);
                ScriptableObject.putProperty(global, n, fn);
            }
        }

        // ---- 以下 public static 方法即 dfm API；参数/返回值全部走基础类型，Rhino 自动包装 ----

        /** define() 时绑定的引擎实例（单脚本互斥下等于当前引擎）。 */
        static JsEngine bound;
        static JSONArray boundLogs;

        /** 当前引擎：优先 ThreadLocal，回退 define 绑定实例。 */
        static JsEngine bridge() throws Exception {
            JsEngine e = current.get();
            if (e == null) {
                e = bound;
            }
            if (e == null) {
                throw new Exception("dfm API 只能在脚本线程内调用");
            }
            return e;
        }

        public static void tap(double x, double y) throws Exception {
            gesture((int) x, (int) y, (int) x, (int) y, 120);
        }

        public static void longpress(double x, double y, double durationMs) throws Exception {
            gesture((int) x, (int) y, (int) x, (int) y, (int) Math.max(100, durationMs));
        }

        public static void swipe(double x1, double y1, double x2, double y2, double durationMs)
                throws Exception {
            gesture((int) x1, (int) y1, (int) x2, (int) y2,
                    (int) Math.max(50, durationMs <= 0 ? 300 : durationMs));
        }

        public static void input(String text) throws Exception {
            if (text == null || text.isEmpty()) {
                return;
            }
            for (int i = 0; i < text.length(); i++) {
                if (text.charAt(i) > 127) {
                    // 非 ASCII：input text 会 NPE（AOSP Input.java 只做 KeyEvent 映射），
                    // 走剪贴板 + PASTE 链（P2，见 inputUnicode）
                    inputUnicode(text);
                    return;
                }
            }
            ShellOut r = shellExec("input text " + shellQuote(text), true, SHELL_TIMEOUT);
            checkExit(r, "input text");
        }

        /**
         * Unicode 输入（方案3 P2）：剪贴板 + 系统粘贴键。
         *
         * 为什么不用 cmd clipboard / input text：前者在本设备不可用（"No shell
         * command implementation."），后者对非 ASCII 会 NPE。写入剪贴板不受
         * Android 10 "仅焦点应用/默认 IME 可读"限制（限制的是读取），PASTE 事件
         * 由系统分发给焦点窗口，粘贴进目标输入框。前置条件：焦点已在目标输入框
         * （脚本里通常先用 tap 点输入框），无焦点时 PASTE 无效果 —— 与真人手操
         * 一致，不做额外兜底。
         */
        public static void inputUnicode(String text) throws Exception {
            if (text == null || text.isEmpty()) {
                return;
            }
            final String t = text;
            // ClipboardManager 的 setPrimaryClip 部分实现校验主线程 Handler，
            // 引擎线程在后台：切主线程写入，用 CountDownLatch 同步等待结果
            final Object[] err = new Object[1];
            android.os.Handler h = new android.os.Handler(
                    bridge().probe.getMainLooper());
            final java.util.concurrent.CountDownLatch latch =
                    new java.util.concurrent.CountDownLatch(1);
            h.post(() -> {
                try {
                    ClipboardManager cm = (ClipboardManager)
                            bridge().probe.getSystemService(android.content.Context.CLIPBOARD_SERVICE);
                    if (cm == null) {
                        err[0] = "ClipboardManager unavailable";
                    } else {
                        cm.setPrimaryClip(ClipData.newPlainText("dfm", t));
                    }
                } catch (Exception e) {
                    err[0] = e.getMessage() == null ? e.toString() : e.getMessage();
                } finally {
                    latch.countDown();
                }
            });
            latch.await(3000, java.util.concurrent.TimeUnit.MILLISECONDS);
            if (err[0] != null) {
                throw new Exception("inputUnicode 写入剪贴板失败: " + err[0]);
            }
            // PASTE（KEYCODE_PASTE=279）：事件进焦点窗口；shell uid 注入，与调用方权限无关
            ShellOut r = shellExec("input keyevent 279", true, SHELL_TIMEOUT);
            checkExit(r, "input keyevent PASTE");
            // 给目标应用处理粘贴留一小段时间（多数 IME/输入框立即生效，纯节奏缓冲）
            sleep(100);
        }

        public static void keyevent(double keycode) throws Exception {
            ShellOut r = shellExec("input keyevent " + (int) keycode, true, SHELL_TIMEOUT);
            checkExit(r, "keyevent");
        }

        /** 无障碍点击：{"id","text","desc"} 至少一项，行为与 0x0D 一致。 */
        public static Object accClick(Object spec) throws Exception {
            if (!(spec instanceof Scriptable)) {
                throw new Exception("accClick 参数必须是 {id/text/desc} 对象");
            }
            Scriptable s = (Scriptable) spec;
            JSONObject req = new JSONObject();
            req.put("id", strProp(s, "id"));
            req.put("text", strProp(s, "text"));
            req.put("desc", strProp(s, "desc"));
            JSONObject res = bridge().probe.accessibilityClick(req);
            boolean clicked = res.optInt("code", 500) == 200;
            return clicked ? res.opt("data") : Context.getUndefinedValue();
        }

        /**
         * 无障碍组件存在判断（对齐 PC 端 accExists 条件，0x0C 树查找，只查不点）：
         * {"id","text","desc"} 至少一项；存在返回 true，不存在/未启用返回 false。
         */
        public static boolean accExists(Object spec) throws Exception {
            if (!(spec instanceof Scriptable)) {
                throw new Exception("accExists 参数必须是 {id/text/desc} 对象");
            }
            Scriptable s = (Scriptable) spec;
            String id = strProp(s, "id");
            String text = strProp(s, "text");
            String desc = strProp(s, "desc");
            if (id.isEmpty() && text.isEmpty() && desc.isEmpty()) {
                throw new Exception("accExists 需至少填写 id/text/desc 一项");
            }
            // 点击式查找会回溯可点击祖先，存在性判断需要纯匹配，这里直接查树。
            // 树接口返回整棵 JSON（≤150 节点），体积可接受（百级节点 ≈ 数十 KB）。
            JSONObject res = bridge().probe.accessibilityTree();
            if (res.optInt("code", 500) != 200) {
                return false;
            }
            JSONObject root = res.optJSONObject("data").optJSONObject("root");
            return root != null && treeFind(root, id, text, desc);
        }

        /** 与 PC 端 _acc_tree_find 同语义：id 精确、text/desc 包含，填了的同时满足。 */
        private static boolean treeFind(JSONObject node, String id, String text, String desc) {
            if (id.isEmpty() || id.equals(node.optString("id", ""))) {
                if (text.isEmpty() || ProbeService.globMatch(text, node.optString("text", ""))) {
                    if (desc.isEmpty() || ProbeService.globMatch(desc, node.optString("desc", ""))) {
                        return true;
                    }
                }
            }
            org.json.JSONArray children = node.optJSONArray("children");
            if (children != null) {
                for (int i = 0; i < children.length(); i++) {
                    JSONObject c = children.optJSONObject(i);
                    if (c != null && treeFind(c, id, text, desc)) {
                        return true;
                    }
                }
            }
            return false;
        }

        /**
         * 应用是否已安装（对齐 PC 端 appInstalled 条件与 startApp 前置检查）：
         * pm path 有输出即已安装。
         */
        public static boolean installed(String pkg) throws Exception {
            checkNotEmpty(pkg, "installed package");
            ShellOut r = shellExec("pm path " + shellQuote(pkg.trim()), false, SHELL_TIMEOUT);
            return r.exit == 0 && !r.stdout.trim().isEmpty();
        }

        /**
         * 音量：action up/down（KEYCODE_VOLUME_UP/DOWN）或 mute（service call audio 静音指定流）。
         * stream 仅 mute 生效：media/ring/alarm/notification，默认 media（与 PC 端一致）。
         */
        public static void volume(String action, Object stream) throws Exception {
            String a = action == null ? "" : action.trim();
            if ("up".equals(a) || "down".equals(a)) {
                String key = "up".equals(a) ? "KEYCODE_VOLUME_UP" : "KEYCODE_VOLUME_DOWN";
                ShellOut r = shellExec("input keyevent " + key, true, SHELL_TIMEOUT);
                checkExit(r, "volume " + a);
                return;
            }
            if ("mute".equals(a)) {
                String st = stream == null ? "media" : Context.toString(stream);
                int type;
                if ("ring".equals(st)) type = 2;
                else if ("alarm".equals(st)) type = 4;
                else if ("notification".equals(st)) type = 5;
                else type = 3; // media（STREAM_MUSIC），与 adb_utils.get_stream_type 一致
                ShellOut r = shellExec("service call audio " + (7 + type) + " i32 1", true, SHELL_TIMEOUT);
                checkExit(r, "volume mute");
                return;
            }
            throw new Exception("volume action 仅支持 up/down/mute: " + a);
        }

        /**
         * 截图存档到当前脚本目录 screenshots/（对齐 PC 端 screenshot 步骤，落盘名 label_HHMMSS.png）。
         * 返回保存的文件绝对路径。
         */
        public static String screenshot(Object label) throws Exception {
            String name = label == null ? "step" : Context.toString(label).trim();
            if (name.isEmpty()) {
                name = "step";
            }
            name = name.replaceAll("[^0-9A-Za-z_-]", "_");
            if (name.length() > 64) {
                name = name.substring(0, 64);
            }
            File dir = new File(bridge().currentScriptDir(), "screenshots");
            if (!dir.exists() && !dir.mkdirs()) {
                throw new Exception("cannot create dir: " + dir);
            }
            String ts = new java.text.SimpleDateFormat("HHmmss", java.util.Locale.US)
                    .format(new java.util.Date());
            File out = new File(dir, name + "_" + ts + ".png");
            ShellOut r = shellExec("screencap -p " + shellQuote(out.getAbsolutePath()), true, 30);
            checkExit(r, "screenshot");
            if (!out.isFile()) {
                throw new Exception("screenshot 保存失败: " + out.getName());
            }
            return out.getAbsolutePath();
        }

        /**
         * 等待模板图像出现在屏幕上（方案3 P2 waitFor/condition-template）。
         *
         * 算法与 PC 端 wait_for_template / TemplateBench 基准同构：灰度化（luma 299/587/114）
         * → 1/4 金字塔粗搜（SAD，取前 8 候选）→ 原尺寸 ±3px 精验取最小 SAD。基准
         * （TemplateBench，PC JVM）100×100 模板全屏 43-82ms，设备端预留轮询间隔
         * 300ms，不影响实时性。
         *
         * @param template 模板文件名（相对当前脚本 templates/ 目录）
         * @param timeoutSec 超时秒数（0 = 单次匹配，与 PC condition-template 一致）
         * @param threshold 匹配阈值 0-1（归一化 SAD score <= (1-threshold)*255 判命中）
         * @return 命中时返回 {x,y,score}（屏幕坐标，模板左上角），超时返回 null（Rhino undefined）
         */
        public static Object waitFor(Object template, Object timeoutSec, Object threshold) throws Exception {
            String name = template == null ? "" : Context.toString(template).trim();
            if (name.isEmpty() || name.contains("..") || name.contains("/") || name.contains("\\")) {
                throw new Exception("waitFor 模板名无效: " + name);
            }
            File tplFile = new File(new File(bridge().currentScriptDir(), "templates"), name);
            if (!tplFile.isFile()) {
                throw new Exception("waitFor 模板不存在: templates/" + name);
            }
            double timeout = timeoutSec == null ? 15 : Context.toNumber(timeoutSec);
            if (Double.isNaN(timeout) || timeout < 0) {
                timeout = 15;
            }
            double th = threshold == null ? 0.85 : Context.toNumber(threshold);
            if (Double.isNaN(th) || th <= 0 || th > 1) {
                th = 0.85;
            }
            int[] tplPix = TemplateMatcher.loadGray(tplFile);
            int[] tplDim = TemplateMatcher.lastSize;
            long deadline = timeout <= 0 ? 0 : System.currentTimeMillis() + (long) (timeout * 1000);
            File shot = new File(bridge().probe.getCacheDir(), "dfm_wait.png");
            while (true) {
                if (bridge().stopFlag) {
                    throw new ScriptStop("脚本已停止（0x14）");
                }
                ShellOut r = shellExec("screencap -p " + shellQuote(shot.getAbsolutePath())
                        + " && chmod 644 " + shellQuote(shot.getAbsolutePath()), true, 30);
                if (r.exit != 0) {
                    throw new Exception("waitFor 截屏失败: " + firstLine(
                            r.stderr == null || r.stderr.isEmpty() ? r.stdout : r.stderr));
                }
                int[] screen = TemplateMatcher.loadGray(shot);
                int[] screenDim = TemplateMatcher.lastSize;
                TemplateMatcher.Match m = TemplateMatcher.match(screen, screenDim[0], screenDim[1],
                        tplPix, tplDim[0], tplDim[1], th);
                if (m != null) {
                    Scriptable js = Context.getCurrentContext().newObject(currentScope(), "Object");
                    js.put("x", js, m.x);
                    js.put("y", js, m.y);
                    js.put("score", js, m.score);
                    return js;
                }
                if (deadline == 0 || System.currentTimeMillis() >= deadline) {
                    return null; // Rhino 侧为 undefined：condition-template 用作布尔
                }
                sleep(Math.min(300, Math.max(50, deadline - System.currentTimeMillis())));
            }
        }

        /**
         * 等待模板出现并点击中心（waitFor + tap 组合，等待期间停止即抛）。
         * @return 命中时返回点击坐标 {x,y,score}，超时抛异常（与 PC 画面校验-先等后点行为一致）
         */
        public static Object waitForTap(Object template, Object timeoutSec, Object threshold) throws Exception {
            int[] tplDim = TemplateMatcher.templateSize(new File(
                    new File(bridge().currentScriptDir(), "templates"),
                    template == null ? "" : Context.toString(template).trim()));
            Object m = waitFor(template, timeoutSec, threshold);
            if (m == null) {
                throw new Exception("waitForTap 超时未找到模板: " + template);
            }
            Scriptable s = (Scriptable) m;
            double cx = ((Number) ScriptableObject.getProperty(s, "x")).doubleValue()
                    + tplDim[0] / 2.0;
            double cy = ((Number) ScriptableObject.getProperty(s, "y")).doubleValue()
                    + tplDim[1] / 2.0;
            tap(cx, cy);
            return m;
        }

        public static void sleep(double ms) throws Exception {
            long left = (long) Math.max(0, ms);
            // 可中断分段睡眠：stopFlag 立即生效，中断异常恢复标记后继续检查
            while (left > 0 && !bridge().stopFlag) {
                long step = Math.min(left, 200);
                try {
                    Thread.sleep(step);
                } catch (InterruptedException e) {
                    Thread.currentThread().interrupt();
                    if (bridge().stopFlag) {
                        throw new ScriptStop("脚本已停止（0x14）");
                    }
                }
                left -= step;
            }
            if (bridge().stopFlag) {
                throw new ScriptStop("脚本已停止（0x14）");
            }
        }

        /** 执行 shell 命令并返回 {exit,stdout,stderr}；默认非 root，shell(cmd,true) 以 root 执行。 */
        public static Object shell(String command, Object root) throws Exception {
            ShellOut r = shellExec(command, root != null && Boolean.TRUE.equals(root), 60);
            Scriptable js = Context.getCurrentContext().newObject(currentScope(), "Object");
            js.put("exit", js, r.exit);
            js.put("stdout", js, r.stdout);
            js.put("stderr", js, r.stderr);
            return js;
        }

        public static void speak(String text) throws Exception {
            JSONObject res = bridge().probe.speak(text == null ? "" : text.trim());
            if (res.optInt("code", 500) != 200) {
                throw new Exception("speak 失败: " + res.optString("message", ""));
            }
        }

        public static void startApp(String pkg, Object activity) throws Exception {
            checkNotEmpty(pkg, "startApp package");
            ShellOut r;
            if (activity != null && !Context.toString(activity).isEmpty()
                    && !"undefined".equals(Context.toString(activity))) {
                r = shellExec("am start -n " + pkg + "/" + Context.toString(activity), false, SHELL_TIMEOUT);
            } else {
                r = shellExec("monkey -p " + pkg + " -c android.intent.category.LAUNCHER 1", false, SHELL_TIMEOUT);
            }
            checkExit(r, "startApp");
        }

        public static void stopApp(String pkg) throws Exception {
            checkNotEmpty(pkg, "stopApp package");
            ShellOut r = shellExec("am force-stop " + pkg, false, SHELL_TIMEOUT);
            checkExit(r, "stopApp");
        }

        public static void installApk(String apkName) throws Exception {
            checkNotEmpty(apkName, "installApk apkName");
            File apkDir = new File(bridge().currentScriptDir(), "apk");
            File apk = new File(apkDir, apkName);
            if (!apk.isFile()) {
                throw new Exception("apk 文件不存在: apk/" + apkName);
            }
            ShellOut r = shellExec("pm install -r " + shellQuote(apk.getAbsolutePath()), true, 120);
            checkExit(r, "installApk");
        }

        public static void uninstallApp(String pkg) throws Exception {
            checkNotEmpty(pkg, "uninstallApp package");
            ShellOut r = shellExec("pm uninstall " + pkg, true, 60);
            checkExit(r, "uninstallApp");
        }

        public static void deleteFolder(String path) throws Exception {
            checkNotEmpty(path, "deleteFolder path");
            if (!path.startsWith("/sdcard/") && !path.startsWith("/storage/")) {
                throw new Exception("deleteFolder 仅允许 /sdcard/ 或 /storage/ 路径");
            }
            ShellOut r = shellExec("rm -rf " + shellQuote(path), true, 60);
            checkExit(r, "deleteFolder");
        }

        public static void reboot() throws Exception {
            shellExec("reboot", true, 30);
        }

        /** 当前屏幕尺寸 {width,height}（wm size，与 0x10 引擎一致）。 */
        public static Object screenSize() throws Exception {
            int[] wh = bridge().currentScreenSize();
            JSONObject o = new JSONObject();
            o.put("width", wh[0]);
            o.put("height", wh[1]);
            // JSONObject 是 JavaBean 风格 getter，Rhino NativeJavaObject 上 s.width 解析不到
            // （getter 是 optWidth/width() 非 getWidth()）；包成 NativeObject 才有 .width 属性
            Scriptable js = Context.getCurrentContext().newObject(currentScope(), "Object");
            js.put("width", js, wh[0]);
            js.put("height", js, wh[1]);
            return js;
        }

        /** 脚本日志：dfm.log("...") 记一条 type=log 日志（与步骤日志同结构）。 */
        public static void log(String message) throws Exception {
            JSONArray logs = boundLogs != null ? boundLogs : bridge().logsRef;
            if (logs != null) {
                engineLog(logs, 0, "log", true, message == null ? "" : message);
            }
        }

        /** 当前轮号（1 起），编译器用它实现 {执行轮数}；手写脚本也可直接读。 */
        public static double roundNo() throws Exception {
            return bridge().currentRound;
        }

        /** N 位随机数字（首位不为 0），对齐 {N位随机数} 变量语义。 */
        public static String randomDigits(double digits) {
            int n = (int) Math.max(1, Math.min(18, digits));
            StringBuilder sb = new StringBuilder();
            Random random = new Random();
            for (int i = 0; i < n; i++) {
                sb.append(random.nextInt(10));
            }
            if (n > 1 && sb.charAt(0) == '0') {
                sb.setCharAt(0, '1');
            }
            return sb.toString();
        }

        /** 当前脚本是否被请求停止（0x14），供 JS 层长循环主动让出。 */
        public static boolean stopped() throws Exception {
            return bridge().stopFlag;
        }

        // ---- 内部实现 ----

        private static void gesture(int x1, int y1, int x2, int y2, int durationMs) throws Exception {
            // 统一走 root input（与 0x10 tap/swipe 完全一致，P2 再切换 dispatchGestureStroke）
            ShellOut r;
            if (x1 == x2 && y1 == y2 && durationMs <= 150) {
                r = shellExec("input tap " + x1 + " " + y1, true, SHELL_TIMEOUT);
            } else {
                r = shellExec("input swipe " + x1 + " " + y1 + " " + x2 + " " + y2 + " " + durationMs,
                        true, SHELL_TIMEOUT);
            }
            checkExit(r, "gesture");
        }

        private static void checkExit(ShellOut r, String what) throws Exception {
            if (r.exit != 0) {
                String err = r.stderr == null || r.stderr.isEmpty() ? r.stdout : r.stderr;
                throw new Exception(what + " 退出码 " + r.exit + ": " + firstLine(err));
            }
        }

        private static ShellOut shellExec(String command, boolean root, int timeoutSec) throws Exception {
            JsEngine e = bridge();
            JSONObject req = new JSONObject();
            req.put("command", command);
            req.put("root", root);
            req.put("timeout", Math.max(1, Math.min(120, timeoutSec)));
            // 注意：probe.shellExec 直接返回 data（{stdout,stderr,exit,truncated}），
            // 没有 {code,message,data} 信封——信封只在 0x08 帧响应层加上
            JSONObject data = e.probe.shellExec(req);
            if (data.has("error") && !data.optString("error", "").isEmpty()) {
                throw new Exception("shell 失败: " + data.optString("error", ""));
            }
            return new ShellOut(data.optInt("exit", -1),
                    data.optString("stdout", ""), data.optString("stderr", ""));
        }

        private static String strProp(Scriptable s, String key) throws Exception {
            Object v = ScriptableObject.getProperty(s, key);
            if (v == ScriptableObject.NOT_FOUND || v == Context.getUndefinedValue()) {
                return "";
            }
            String str = Context.toString(v);
            return str.length() > 200 ? str.substring(0, 200) : str;
        }

        private static void checkNotEmpty(String v, String what) {
            if (v == null || v.trim().isEmpty()) {
                throw new IllegalArgumentException(what + " is required");
            }
        }

        private static Scriptable currentScope() {
            JsEngine e = current.get();
            return e == null ? null : e.scopeRef;
        }

        private static File scriptStoreDir() throws Exception {
            return bridge().storeDir();
        }
    }

    private static class ShellOut {
        final int exit;
        final String stdout;
        final String stderr;

        ShellOut(int exit, String stdout, String stderr) {
            this.exit = exit;
            this.stdout = stdout;
            this.stderr = stderr;
        }
    }

    /**
     * 模板匹配（方案3 P2）：灰度金字塔 SAD，与 PC 端 TemplateBench 基准同构。
     *
     * 纯 Java 实现（无 OpenCV 依赖）：原图与模板先归一化为灰度 int[]（每像素 0-255），
     * 1/4 缩样粗搜取 SAD 最小的前 8 个候选，再在各候选 ±3px 窗口内做原尺寸精验，
     * 取归一化分最低的命中点。score = 平均绝对差（0-255），命中条件 score <= (1-threshold)*255。
     * PC 基准（100×100 模板 / 1920×1080 全屏）43-233ms；设备端同量级，轮询间隔 300ms 足够。
     */
    static final class TemplateMatcher {
        /** 最近一次 loadGray 的 {width,height}（调用方读取，避免多返回值） */
        static int[] lastSize = new int[]{0, 0};

        private TemplateMatcher() {
        }

        /** 读 PNG/BMP → 灰度 int[]（行优先）。失败拋 Exception，尺寸写入 lastSize。 */
        static int[] loadGray(File f) throws Exception {
            BitmapFactory.Options opt = new BitmapFactory.Options();
            opt.inPreferredConfig = Bitmap.Config.ALPHA_8;
            Bitmap bmp = BitmapFactory.decodeFile(f.getAbsolutePath(), opt);
            if (bmp == null) {
                throw new Exception("图像解码失败: " + f.getName());
            }
            int w = bmp.getWidth(), h = bmp.getHeight();
            if (w <= 0 || h <= 0) {
                bmp.recycle();
                throw new Exception("图像尺寸无效: " + f.getName());
            }
            int[] gray = new int[w * h];
            int[] row = new int[w];
            for (int y = 0; y < h; y++) {
                bmp.getPixels(row, 0, w, 0, y, w, 1);
                int base = y * w;
                for (int x = 0; x < w; x++) {
                    int p = row[x];
                    // ALPHA_8 下像素值存 alpha 通道；普通 ARGB 按上一致处理（截图不透明）
                    int r = (p >> 16) & 0xFF, g = (p >> 8) & 0xFF, b = p & 0xFF;
                    int luma = (r * 299 + g * 587 + b * 114) / 1000;
                    // ALPHA_8：getPixels 返回 p=0xAARRGGBB 形式但颜色通道为 0，
                    // 实际灰度在 alpha 字节 —— 两者都算，取合理值
                    int alpha = (p >>> 24) & 0xFF;
                    gray[base + x] = (r == 0 && g == 0 && b == 0) ? alpha : luma;
                }
            }
            bmp.recycle();
            lastSize = new int[]{w, h};
            return gray;
        }

        /** 仅取图像尺寸（不解码像素到 int[]，waitForTap 计算中心用） */
        static int[] templateSize(File f) throws Exception {
            BitmapFactory.Options opt = new BitmapFactory.Options();
            opt.inJustDecodeBounds = true;
            BitmapFactory.decodeFile(f.getAbsolutePath(), opt);
            if (opt.outWidth <= 0 || opt.outHeight <= 0) {
                throw new Exception("图像解码失败: " + f.getName());
            }
            return new int[]{opt.outWidth, opt.outHeight};
        }

        static final class Match {
            final int x;
            final int y;
            final double score;

            Match(int x, int y, double score) {
                this.x = x;
                this.y = y;
                this.score = score;
            }
        }

        /**
         * 匹配：金字塔粗搜（scale=4，top-8）+ 原尺寸精验（±3px）。
         * @return 最佳命中（score <= maxScore），无命中返回 null
         */
        static Match match(int[] big, int bw, int bh, int[] tpl, int tw, int th, double threshold) {
            if (tw <= 0 || th <= 0 || tw > bw || th > bh) {
                return null;
            }
            double maxScore = (1.0 - threshold) * 255.0;
            // 模板太小（<8px）金字塔失真，直接原尺寸全搜
            if (tw < 8 || th < 8) {
                return scanFull(big, bw, bh, tpl, tw, th, maxScore);
            }
            int scale = 4;
            int sw = bw / scale, sh = bh / scale;
            int stw = Math.max(1, tw / scale), sth = Math.max(1, th / scale);
            if (stw > sw || sth > sh) {
                return scanFull(big, bw, bh, tpl, tw, th, maxScore);
            }
            int[] sBig = downsample(big, bw, bh, scale);
            int[] sTpl = downsample(tpl, tw, th, scale);
            // 粗搜：记录前 8 低分候选（简单插入排序数组，N≈12 万帧开销可忽略）
            long[] candScore = new long[8];
            int[] candX = new int[8], candY = new int[8];
            java.util.Arrays.fill(candScore, Long.MAX_VALUE);
            int candN = 0;
            int maxX = sw - stw, maxY = sh - sth;
            for (int y = 0; y <= maxY; y++) {
                for (int x = 0; x <= maxX; x++) {
                    long s = sadAt(sBig, sw, sTpl, stw, sth, x, y, Long.MAX_VALUE);
                    if (candN < 8 || s < candScore[7]) {
                        int pos = candN < 8 ? candN++ : 7;
                        while (pos > 0 && candScore[pos - 1] > s) {
                            candScore[pos] = candScore[pos - 1];
                            candX[pos] = candX[pos - 1];
                            candY[pos] = candY[pos - 1];
                            pos--;
                        }
                        candScore[pos] = s;
                        candX[pos] = x;
                        candY[pos] = y;
                    }
                }
            }
            // 精验：每个候选中心 ×scale 对应原图位置 ±3px 窗口内原尺寸匹配
            Match best = null;
            double bestScore = Double.MAX_VALUE;
            for (int i = 0; i < candN; i++) {
                int cx = candX[i] * scale, cy = candY[i] * scale;
                for (int dy = -3; dy <= 3; dy++) {
                    int oy = cy + dy;
                    if (oy < 0 || oy + th > bh) continue;
                    for (int dx = -3; dx <= 3; dx++) {
                        int ox = cx + dx;
                        if (ox < 0 || ox + tw > bw) continue;
                        long s = sadAt(big, bw, tpl, tw, th, ox, oy, Long.MAX_VALUE);
                        double norm = s / (double) (tw * th);
                        if (norm < bestScore) {
                            bestScore = norm;
                            best = new Match(ox, oy, norm);
                        }
                    }
                }
            }
            return best != null && best.score <= maxScore ? best : null;
        }

        /** 模板过小时的原尺寸全图扫描（带早退）。 */
        private static Match scanFull(int[] big, int bw, int bh, int[] tpl, int tw, int th,
                                      double maxScore) {
            Match best = null;
            double bestScore = Double.MAX_VALUE;
            for (int y = 0; y + th <= bh; y++) {
                for (int x = 0; x + tw <= bw; x++) {
                    long s = sadAt(big, bw, tpl, tw, th, x, y, (long) (bestScore * tw * th));
                    double norm = s / (double) (tw * th);
                    if (norm < bestScore) {
                        bestScore = norm;
                        best = new Match(x, y, norm);
                    }
                }
            }
            return best != null && best.score <= maxScore ? best : null;
        }

        /** (ox,oy) 处 tw×th 窗口的绝对差之和；超过 earlyExit 提前返回。 */
        private static long sadAt(int[] big, int bw, int[] tpl, int tw, int th,
                                  int ox, int oy, long earlyExit) {
            long sum = 0;
            for (int y = 0; y < th; y++) {
                int bRow = (oy + y) * bw + ox;
                int tRow = y * tw;
                for (int x = 0; x < tw; x++) {
                    int d = big[bRow + x] - tpl[tRow + x];
                    sum += d < 0 ? -d : d;
                }
                if (sum > earlyExit) {
                    return sum;
                }
            }
            return sum;
        }

        /** 步长 scale 的降采样（取每块左上像素，与基准一致）。 */
        private static int[] downsample(int[] px, int w, int h, int scale) {
            int sw = w / scale, sh = h / scale;
            int[] out = new int[sw * sh];
            for (int y = 0; y < sh; y++) {
                int srcRow = y * scale * w;
                int dstRow = y * sw;
                for (int x = 0; x < sw; x++) {
                    out[dstRow + x] = px[srcRow + x * scale];
                }
            }
            return out;
        }
    }

    // ===== 共享小工具（与 ScriptEngine 同语义） =====

    private static class DfmContextHolder {
        static final ContextFactory factory = ContextFactory.getGlobal();
    }

    /** script_store 目录（app 外部私有目录下）。 */
    private File storeDir() throws Exception {
        File base = probe.getExternalFilesDir(null);
        if (base == null) {
            throw new Exception("external files dir unavailable");
        }
        File d = new File(base, "script_store");
        if (!d.exists() && !d.mkdirs()) {
            throw new Exception("cannot create dir: " + d);
        }
        return d;
    }

    /** 当前脚本目录（script_store/{name}），由 run() 设置、finally 清空。 */
    private File currentScriptDir() {
        return scriptDirRef;
    }

    /** wm size 解析屏幕尺寸（Override 优先，其次 Physical），失败回退 800x1280。 */
    private int[] currentScreenSize() throws Exception {
        try {
            ShellOut r = DfmBridge.shellExec("wm size", false, 10);
            Matcher override = Pattern.compile("Override size: (\\d+)x(\\d+)").matcher(r.stdout);
            Matcher physical = Pattern.compile("Physical size: (\\d+)x(\\d+)").matcher(r.stdout);
            Matcher m = override.find() ? override : (physical.find() ? physical : null);
            if (m != null) {
                return new int[]{Integer.parseInt(m.group(1)), Integer.parseInt(m.group(2))};
            }
        } catch (Exception e) {
            Log.w(TAG, "wm size failed: " + e);
        }
        Log.w(TAG, "screen size unknown, fallback 800x1280");
        return new int[]{800, 1280};
    }

    // ===== 0x0E 双格式支持（P0-6）：JS 脚本包解压 / 列表 / 删除 =====

    /**
     * 解压 JS 脚本包（0x0E 请求 {"zip":"xxx.zip"}）：script_store/{名}/ 含 script.js。
     * 与 JSON 引擎 unzip 同规则（同名清空、路径穿越校验），区别仅在校验 JS 入口文件。
     */
    JSONObject unzip(String zipName) throws Exception {
        String name = safeName(zipName, "zip");
        File inDir = probeDir("script_in");
        File zip = new File(inDir, name + ".zip");
        if (!zip.isFile()) {
            throw new IllegalArgumentException("zip not found in script_in: " + name + ".zip");
        }
        File target = new File(storeDir(), name);
        deleteRecursively(target);
        if (!target.mkdirs()) {
            throw new Exception("cannot create dir: " + target);
        }
        int files = 0;
        String canonicalTarget = target.getCanonicalPath() + File.separator;
        ZipInputStream zis = new ZipInputStream(new FileInputStream(zip));
        try {
            ZipEntry entry;
            while ((entry = zis.getNextEntry()) != null) {
                String entryName = entry.getName();
                if (entryName == null || entryName.isEmpty()) {
                    continue;
                }
                File out = new File(target, entryName);
                if (!out.getCanonicalPath().startsWith(canonicalTarget)) {
                    throw new Exception("zip entry escapes target dir: " + entryName);
                }
                if (entry.isDirectory()) {
                    out.mkdirs();
                    continue;
                }
                File parent = out.getParentFile();
                if (parent != null && !parent.exists() && !parent.mkdirs()) {
                    throw new Exception("cannot create dir: " + parent);
                }
                FileOutputStream fos = new FileOutputStream(out);
                try {
                    byte[] buf = new byte[8192];
                    int n;
                    while ((n = zis.read(buf)) >= 0) {
                        fos.write(buf, 0, n);
                    }
                } finally {
                    fos.close();
                }
                files++;
            }
        } finally {
            try {
                zis.close();
            } catch (Exception ignored) {
            }
        }
        File js = new File(target, JS_ENTRY);
        if (!js.isFile()) {
            throw new Exception("script.js not found in zip");
        }
        int steps = 0;
        File meta = new File(target, "metadata.json");
        if (meta.isFile()) {
            try {
                steps = new JSONObject(readText(meta)).optInt("steps", 0);
            } catch (Exception ignored) {
            }
        }
        JSONObject data = new JSONObject();
        data.put("script", name);
        data.put("format", "js");
        data.put("steps", steps);
        data.put("files", files);
        data.put("path", target.getAbsolutePath());
        return data;
    }

    /** 列出 JS 脚本（0x0F 请求 {"format":"js"} 或并入旧列表，P0 先独立）。 */
    JSONObject list() throws Exception {
        JSONArray arr = new JSONArray();
        File store = storeDir();
        File[] dirs = store.listFiles();
        if (dirs != null) {
            for (File d : dirs) {
                File js = new File(d, JS_ENTRY);
                if (!d.isDirectory() || !js.isFile()) {
                    continue;
                }
                JSONObject item = new JSONObject();
                item.put("name", d.getName());
                item.put("format", "js");
                int steps = 0;
                File meta = new File(d, "metadata.json");
                if (meta.isFile()) {
                    try {
                        steps = new JSONObject(readText(meta)).optInt("steps", 0);
                    } catch (Exception ignored) {
                    }
                }
                item.put("steps", steps);
                item.put("size", js.length());
                item.put("importedAt", new java.text.SimpleDateFormat("yyyy-MM-dd HH:mm:ss",
                        java.util.Locale.US).format(new java.util.Date(d.lastModified())));
                arr.put(item);
            }
        }
        JSONObject data = new JSONObject();
        data.put("scripts", arr);
        return data;
    }

    // ===== 内部状态与小工具 =====

    /** runRhino 期间的临时引用（仅脚本线程访问）。 */
    private JSONArray logsRef;
    private Scriptable scopeRef;
    private File scriptDirRef;

    /** 引擎侧日志写入（DfmBridge.log 走同名静态引擎日志 engineLog）。 */
    private static void engineLog(JSONArray logs, int no, String type, boolean ok, String message)
            throws Exception {
        if (logs.length() >= MAX_LOGS) {
            return;
        }
        JSONObject item = new JSONObject();
        item.put("no", no);
        item.put("type", type);
        item.put("ok", ok);
        String msg = message == null ? "" : message;
        if (msg.length() > MAX_LOG_MESSAGE) {
            msg = msg.substring(0, MAX_LOG_MESSAGE) + "…";
        }
        item.put("message", msg);
        logs.put(item);
    }

    private static String safeName(String name, String field) {
        if (name == null || name.trim().isEmpty()) {
            throw new IllegalArgumentException(field + " is required");
        }
        String clean = name.trim();
        if (clean.endsWith(".zip")) {
            clean = clean.substring(0, clean.length() - 4);
        }
        if (clean.contains("/") || clean.contains("\\") || clean.contains("..")) {
            throw new IllegalArgumentException(field + " 含非法字符: " + name);
        }
        return clean;
    }

    private File probeDir(String name) throws Exception {
        File base = probe.getExternalFilesDir(null);
        if (base == null) {
            throw new Exception("external files dir unavailable");
        }
        File d = new File(base, name);
        if (!d.exists() && !d.mkdirs()) {
            throw new Exception("cannot create dir: " + d);
        }
        return d;
    }

    private static void deleteRecursively(File f) {
        if (f == null || !f.exists()) {
            return;
        }
        File[] children = f.listFiles();
        if (children != null) {
            for (File c : children) {
                deleteRecursively(c);
            }
        }
        f.delete();
    }

    private static String readText(File f) throws Exception {
        FileInputStream in = new FileInputStream(f);
        try {
            java.io.ByteArrayOutputStream bos = new java.io.ByteArrayOutputStream();
            byte[] buf = new byte[8192];
            int n;
            while ((n = in.read(buf)) >= 0) {
                bos.write(buf, 0, n);
            }
            return new String(bos.toByteArray(), UTF8);
        } finally {
            in.close();
        }
    }

    private static String shellQuote(String s) {
        return "'" + s.replace("'", "'\\''") + "'";
    }

    private static String firstLine(String s) {
        if (s == null) {
            return "";
        }
        int idx = s.indexOf('\n');
        return (idx >= 0 ? s.substring(0, idx) : s).trim();
    }

    private static String abbreviate(String s, int max) {
        if (s == null) {
            return "";
        }
        return s.length() > max ? s.substring(0, max) + "…" : s;
    }
}
