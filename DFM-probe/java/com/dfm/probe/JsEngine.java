package com.dfm.probe;

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
                // 直接在全局作用域上逐轮执行：evaluateString 每次都会重新执行顶层 var 声明。
                // 不能对 scope 自身 setPrototype(scope)（自引用原型链会让 getBase 无限循环，
                // 卡死线程且 0x14 无法中断——此前 ART 上 live-lock 的根因）
                Object result = cx.evaluateString(scope, source, JS_ENTRY, 1, null);
                // 运行后校验（权威）：var 声明已落在作用域上
                Object v = scope.get("apiVersion", scope);
                if (!(v instanceof Number) || ((Number) v).intValue() > 1) {
                    throw new IllegalArgumentException("脚本 apiVersion 缺失或高于设备支持版本（设备支持 1）");
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
                    "sleep", "shell", "speak", "startApp", "stopApp", "installApk",
                    "uninstallApp", "deleteFolder", "reboot", "screenSize", "log", "randomDigits"};
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
                    throw new Exception("input 仅支持 ASCII（设备端暂不支持中文输入，P2 修复）");
                }
            }
            ShellOut r = shellExec("input text " + shellQuote(text), true, SHELL_TIMEOUT);
            checkExit(r, "input text");
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
