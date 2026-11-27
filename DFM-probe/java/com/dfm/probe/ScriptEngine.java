package com.dfm.probe;

import android.util.Log;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.nio.charset.Charset;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;
import java.util.Random;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import java.util.zip.ZipEntry;
import java.util.zip.ZipInputStream;

/**
 * 设备端离线脚本引擎：支撑 0x0E 解压脚本包 / 0x0F 脚本列表 / 0x10 执行脚本三个命令字。
 *
 * 脚本包由 PC 端 adb push 到固定地址 script_in/（应用外部私有目录下），
 * 解压到同层 script_store/<脚本名>/，布局与服务端导出一致：
 *   script.json + apk/（脚本引用的安装包）+ templates/（模板图，设备端暂不使用）
 * 执行引擎在设备本地复刻服务端回放逻辑：goto/stop/fail 分支、runScript 嵌套、
 * count/interval 每步骤循环；注入类步骤用 shell input 命令实现（设备已 root）。
 * 模板匹配类步骤（waitFor / condition-template）设备端没有 OpenCV，明确报"不支持"，
 * 不静默跳过。每步骤追加一条 {no,type,ok,message,durationMs} 日志随响应回传。
 */
public class ScriptEngine {
    private static final String TAG = "DFMProbe";
    private static final Charset UTF8 = Charset.forName("UTF-8");

    /** zip 推送固定地址 / 解压目标目录（应用外部私有目录下的相对名）。 */
    static final String DIR_IN = "script_in";
    static final String DIR_STORE = "script_store";

    /** 每轮脚本跳转次数上限，防止分支构成死循环（对齐服务端 MAX_SCRIPT_JUMPS）。 */
    private static final int MAX_JUMPS = 100;
    /** runScript 嵌套深度上限（对齐服务端 MAX_NESTED_SCRIPT_DEPTH）。 */
    private static final int MAX_DEPTH = 3;
    /** 单条步骤日志 message 截断长度，防止响应超出单帧 65535。 */
    private static final int MAX_LOG_MESSAGE = 200;
    /** 日志条数上限，超出后停止记录并置 truncated。 */
    private static final int MAX_LOGS = 500;

    /** 步骤失败异常：携带已记录的日志向外层冒泡，外层汇总成响应。 */
    private static class StepFail extends Exception {
        StepFail(String message) {
            super(message);
        }
    }

    private final ProbeService probe;

    ScriptEngine(ProbeService probe) {
        this.probe = probe;
    }

    // ===== 命令字入口 =====

    /**
     * CMD_SCRIPT_UNZIP (0x0E)：解压 script_in/<zip> 到 script_store/<脚本名>/。
     * 数据区 {"zip":"xxx.zip"}；解压前先清空同名目录，条目名做路径穿越校验。
     */
    JSONObject unzip(String zipName) throws Exception {
        String name = safeName(zipName, "zip");
        File inDir = dir(DIR_IN);
        File zip = new File(inDir, name + ".zip");
        if (!zip.isFile()) {
            throw new IllegalArgumentException("zip not found in script_in: " + name + ".zip");
        }
        File target = new File(dir(DIR_STORE), name);
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
                // 防路径穿越：规范路径必须仍落在解压目录内
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
        File scriptJson = new File(target, "script.json");
        if (!scriptJson.isFile()) {
            throw new Exception("script.json not found in zip");
        }
        JSONObject script = new JSONObject(readText(scriptJson));
        JSONObject data = new JSONObject();
        data.put("script", name);
        data.put("steps", script.optJSONArray("steps") == null ? 0 : script.optJSONArray("steps").length());
        data.put("files", files);
        data.put("path", target.getAbsolutePath());
        data.put("apks", listChildNames(new File(target, "apk")));
        data.put("templates", listChildNames(new File(target, "templates")));
        return data;
    }

    /** CMD_SCRIPT_LIST (0x0F)：列出 script_store 下含 script.json 的脚本。 */
    JSONObject list() throws Exception {
        JSONArray arr = new JSONArray();
        File store = dir(DIR_STORE);
        File[] dirs = store.listFiles();
        if (dirs != null) {
            for (File d : dirs) {
                File json = new File(d, "script.json");
                if (!d.isDirectory() || !json.isFile()) {
                    continue;
                }
                JSONObject item = new JSONObject();
                item.put("name", d.getName());
                try {
                    JSONObject script = new JSONObject(readText(json));
                    item.put("steps", script.optJSONArray("steps") == null
                            ? 0 : script.optJSONArray("steps").length());
                } catch (Exception e) {
                    item.put("steps", 0);
                    item.put("error", "script.json 解析失败: " + e);
                }
                item.put("size", json.length());
                item.put("importedAt", new SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.US)
                        .format(new Date(d.lastModified())));
                arr.put(item);
            }
        }
        JSONObject data = new JSONObject();
        data.put("scripts", arr);
        return data;
    }

    /**
     * CMD_SCRIPT_RUN (0x10)：执行 script_store/<script>/script.json。
     * 数据区 {"script":"名字","repeat":N}；无论成败都回 code 200，
     * 由 data.ok / data.error / data.logs 表达每步骤结果，便于 PC 端拿全量日志。
     */
    JSONObject run(String scriptName, int repeat) throws Exception {
        String name = safeName(scriptName, "script");
        if (repeat < 1) {
            repeat = 1;
        }
        if (repeat > 1000) {
            throw new IllegalArgumentException("repeat must be 1..1000");
        }
        File scriptDir = new File(dir(DIR_STORE), name);
        File jsonFile = new File(scriptDir, "script.json");
        if (!jsonFile.isFile()) {
            throw new IllegalArgumentException("script not found: " + name + "（先发 0x0E 解压脚本包）");
        }
        JSONObject script = new JSONObject(readText(jsonFile));
        JSONArray steps = script.optJSONArray("steps");
        if (steps == null || steps.length() == 0) {
            throw new IllegalArgumentException("script steps is empty");
        }
        JSONArray logs = new JSONArray();
        long start = System.currentTimeMillis();
        String error = null;
        try {
            runSteps(scriptDir, script, steps, repeat, 1, logs);
        } catch (StepFail e) {
            error = e.getMessage();
        }
        JSONObject summary = new JSONObject();
        summary.put("rounds", repeat);
        summary.put("steps", steps.length());
        summary.put("ok", error == null);
        summary.put("error", error == null ? JSONObject.NULL : error);
        summary.put("durationMs", System.currentTimeMillis() - start);
        JSONObject data = new JSONObject();
        data.put("script", name);
        data.put("ok", error == null);
        data.put("error", error == null ? JSONObject.NULL : error);
        data.put("summary", summary);
        data.put("logs", logs);
        data.put("truncated", logs.length() >= MAX_LOGS);
        return data;
    }

    // ===== 执行引擎 =====

    /**
     * 执行一轮/多轮步骤序列；goto/stop/fail 分支与 runScript 嵌套语义对齐服务端。
     * stop = 正常结束（返回 true）；fail / 步骤失败 = 抛 StepFail。
     */
    private boolean runSteps(File scriptDir, JSONObject script, JSONArray steps,
                             int repeat, int depth, JSONArray logs) throws Exception {
        int baseW = script.optInt("videoWidth", 0);
        int baseH = script.optInt("videoHeight", 0);
        int[] screen = screenSize();
        float sx = baseW > 0 ? screen[0] / (float) baseW : 1f;
        float sy = baseH > 0 ? screen[1] / (float) baseH : 1f;
        for (int round = 1; round <= repeat; round++) {
            int idx = 0;
            int jumps = 0;
            while (idx < steps.length()) {
                JSONObject step = steps.optJSONObject(idx);
                if (step == null) {
                    throw new StepFail("第 " + (idx + 1) + " 步不是对象");
                }
                String type = step.optString("type", "");
                int count = Math.max(1, step.optInt("count", 1));
                int interval = Math.max(0, step.optInt("interval", 0));
                if ("condition".equals(type)) {
                    // 判断步骤：求一次条件，按真/假分支决定下一步
                    boolean ok = evalCondition(scriptDir, step, depth, logs);
                    JSONObject branch = step.optJSONObject(ok ? "onTrue" : "onFalse");
                    if (branch == null) {
                        branch = new JSONObject();
                    }
                    String action = branch.optString("action", "continue");
                    if ("goto".equals(action)) {
                        jumps++;
                        if (jumps > MAX_JUMPS) {
                            throw new StepFail("判断步骤跳转次数超过上限 " + MAX_JUMPS + "，可能存在死循环");
                        }
                        int target = branch.optInt("target", 0) - 1;
                        if (!(idx < target && target < steps.length())) {
                            throw new StepFail("判断步骤跳转目标无效: 第 " + branch.optInt("target", 0) + " 步");
                        }
                        idx = target;
                        continue;
                    }
                    if ("stop".equals(action)) {
                        return true;
                    }
                    if ("fail".equals(action)) {
                        throw new StepFail("判断步骤未通过（action=fail）");
                    }
                    if ("runScript".equals(action)) {
                        runNested(scriptDir, branch.optString("script", ""), 1, depth, logs);
                    } else if (isKnownType(action)) {
                        // 分支动作=直接执行某个步骤功能：把分支参数当作临时步骤
                        JSONObject tmp = new JSONObject(branch.toString());
                        tmp.put("type", action);
                        runStep(scriptDir, tmp, idx + 1, round, 1, sx, sy, depth, logs);
                    }
                } else if ("runScript".equals(type)) {
                    int nestedRepeat = Math.max(1, step.optInt("repeat", 1));
                    runNested(scriptDir, step.optString("script", ""), nestedRepeat, depth, logs);
                    log(logs, idx + 1, type, true, "嵌套脚本 " + step.optString("script", "") + " 执行完成");
                } else if ("waitFor".equals(type)) {
                    log(logs, idx + 1, type, false, "设备端不支持模板匹配步骤（waitFor），脚本中止");
                    throw new StepFail("第 " + (idx + 1) + " 步 waitFor 设备端不支持模板匹配");
                } else {
                    // 普通步骤：按 count 循环执行，失败即中止整脚本
                    for (int i = 1; i <= count; i++) {
                        runStep(scriptDir, step, idx + 1, round, i, sx, sy, depth, logs);
                        if (i < count && interval > 0) {
                            sleep(interval);
                        }
                    }
                }
                idx++;
                if (idx < steps.length() && interval > 0) {
                    sleep(interval);
                }
            }
        }
        return true;
    }

    /** 分支动作是否为可直接执行的步骤类型（condition 的 action=某步骤类型时复用）。 */
    private static boolean isKnownType(String type) {
        return "tap".equals(type) || "longpress".equals(type) || "swipe".equals(type)
                || "input".equals(type) || "installApk".equals(type)
                || "deleteFolder".equals(type) || "startApp".equals(type)
                || "stopApp".equals(type) || "uninstallApp".equals(type)
                || "keyevent".equals(type) || "volume".equals(type)
                || "reboot".equals(type) || "tts".equals(type)
                || "accClick".equals(type);
    }

    /** 执行单个步骤并记日志；ok=false 抛 StepFail 中止脚本。 */
    private void runStep(File scriptDir, JSONObject step, int no, int round, int countIdx,
                         float sx, float sy, int depth, JSONArray logs) throws Exception {
        String type = step.optString("type", "");
        long start = System.currentTimeMillis();
        boolean ok;
        String message;
        try {
            message = execStep(scriptDir, step, type, round, countIdx, sx, sy, depth, logs);
            ok = true;
        } catch (StepFail e) {
            throw e;
        } catch (Exception e) {
            ok = false;
            message = e.getMessage() == null ? e.toString() : e.getMessage();
        }
        log(logs, no, type, ok, message
                + (ok ? "" : "") + "（" + (System.currentTimeMillis() - start) + "ms）");
        if (!ok) {
            throw new StepFail("第 " + no + " 步(" + type + ")失败: " + message);
        }
    }

    /** 单步骤实现，返回成功说明文字；抛异常 = 失败。 */
    private String execStep(File scriptDir, JSONObject step, String type, int round, int countIdx,
                            float sx, float sy, int depth, JSONArray logs) throws Exception {
        if ("tap".equals(type)) {
            int x = Math.round(optFloat(step, "x1") * sx);
            int y = Math.round(optFloat(step, "y1") * sy);
            ShellResult r = shell("input tap " + x + " " + y, true, 15);
            checkExit(r, "input tap");
            return "点击 (" + x + "," + y + ")";
        }
        if ("longpress".equals(type)) {
            int x = Math.round(optFloat(step, "x1") * sx);
            int y = Math.round(optFloat(step, "y1") * sy);
            int duration = Math.max(100, step.optInt("duration", 800));
            ShellResult r = shell("input swipe " + x + " " + y + " " + x + " " + y + " " + duration,
                    true, 15);
            checkExit(r, "input longpress");
            return "长按 (" + x + "," + y + ") " + duration + "ms";
        }
        if ("swipe".equals(type)) {
            int x1 = Math.round(optFloat(step, "x1") * sx);
            int y1 = Math.round(optFloat(step, "y1") * sy);
            int x2 = Math.round(optFloat(step, "x2", optFloat(step, "x1")) * sx);
            int y2 = Math.round(optFloat(step, "y2", optFloat(step, "y1")) * sy);
            int duration = Math.max(100, step.optInt("duration", 300));
            ShellResult r = shell("input swipe " + x1 + " " + y1 + " " + x2 + " " + y2 + " " + duration,
                    true, 15);
            checkExit(r, "input swipe");
            return "滑动 (" + x1 + "," + y1 + ")->(" + x2 + "," + y2 + ")";
        }
        if ("input".equals(type)) {
            String text = resolveText(step.optString("text", ""), round, countIdx);
            if (text.isEmpty()) {
                return "空文本，跳过输入";
            }
            for (int i = 0; i < text.length(); i++) {
                if (text.charAt(i) > 127) {
                    throw new Exception("input text 仅支持 ASCII，文本含非 ASCII 字符（设备端不支持中文输入）");
                }
            }
            ShellResult r = shell("input text " + shellQuote(text), true, 15);
            checkExit(r, "input text");
            return "输入文本 " + text;
        }
        if ("startApp".equals(type)) {
            String pkg = step.optString("packageName", "").trim();
            if (pkg.isEmpty()) {
                throw new Exception("packageName 为空");
            }
            String activity = step.optString("activity", "").trim();
            if (!activity.isEmpty()) {
                ShellResult r = shell("am start -n " + pkg + "/" + activity, false, 15);
                checkExit(r, "am start");
                return "启动 " + pkg + "/" + activity;
            }
            checkInstalled(pkg);
            ShellResult r = shell("monkey -p " + pkg + " -c android.intent.category.LAUNCHER 1",
                    false, 15);
            checkExit(r, "monkey start");
            return "启动 " + pkg;
        }
        if ("stopApp".equals(type)) {
            String pkg = step.optString("packageName", "").trim();
            ShellResult r = shell("am force-stop " + pkg, false, 15);
            checkExit(r, "am force-stop");
            return "停止 " + pkg;
        }
        if ("uninstallApp".equals(type)) {
            String pkg = step.optString("packageName", "").trim();
            ShellResult r = shell("pm uninstall " + pkg, true, 60);
            checkExit(r, "pm uninstall");
            return "卸载 " + pkg;
        }
        if ("installApk".equals(type)) {
            String apkName = step.optString("apkName", "").trim();
            File apk = new File(new File(scriptDir, "apk"), apkName);
            if (!apk.isFile()) {
                throw new Exception("脚本包内找不到 APK: apk/" + apkName);
            }
            ShellResult r = shell("pm install -r " + shellQuote(apk.getAbsolutePath()), true, 120);
            checkExit(r, "pm install");
            return "安装 " + apkName;
        }
        if ("deleteFolder".equals(type)) {
            String path = step.optString("path", "").trim();
            if (!path.startsWith("/sdcard/") && !path.startsWith("/storage/")) {
                throw new Exception("deleteFolder 仅允许 /sdcard/ 或 /storage/ 下的路径: " + path);
            }
            ShellResult r = shell("rm -rf " + shellQuote(path), true, 60);
            checkExit(r, "rm -rf");
            return "删除 " + path;
        }
        if ("keyevent".equals(type)) {
            int keycode = step.optInt("keycode", 0);
            ShellResult r = shell("input keyevent " + keycode, true, 15);
            checkExit(r, "input keyevent");
            return "按键 " + keycode;
        }
        if ("volume".equals(type)) {
            String action = step.optString("action", "up").trim();
            int stream = streamType(step.optString("stream", "media"));
            if ("up".equals(action)) {
                ShellResult r = shell("input keyevent KEYCODE_VOLUME_UP", true, 15);
                checkExit(r, "volume up");
                return "音量加（stream " + stream + "）";
            }
            if ("down".equals(action)) {
                ShellResult r = shell("input keyevent KEYCODE_VOLUME_DOWN", true, 15);
                checkExit(r, "volume down");
                return "音量减（stream " + stream + "）";
            }
            if ("mute".equals(action)) {
                // 与服务端一致：service call audio 7+stream i32 1 静音对应流
                ShellResult r = shell("service call audio " + (7 + stream) + " i32 1", false, 15);
                checkExit(r, "volume mute");
                return "静音（stream " + stream + "）";
            }
            throw new Exception("volume action 无效: " + action);
        }
        if ("reboot".equals(type)) {
            ShellResult r = shell("reboot", true, 30);
            // reboot 会立刻断开 shell，退出码不一定为 0，发出即视为成功
            return "已下发重启命令（设备将断开连接）";
        }
        if ("tts".equals(type)) {
            String text = resolveText(step.optString("text", ""), round, countIdx);
            JSONObject res = probe.speak(text);
            if (res.optInt("code", 500) != 200) {
                throw new Exception("TTS 失败: " + res.optString("message", ""));
            }
            return "朗读 " + text;
        }
        if ("accClick".equals(type)) {
            JSONObject res = probe.accessibilityClick(step);
            if (res.optInt("code", 500) != 200) {
                throw new Exception("无障碍点击失败: " + res.optString("message", ""));
            }
            return "无障碍点击成功（id=" + step.optString("id", "-")
                    + " text=" + step.optString("text", "-")
                    + " desc=" + step.optString("desc", "-") + "）";
        }
        throw new Exception("未知步骤类型: " + type);
    }

    /** 求判断条件（appInstalled/shell/time/accClick），返回取反后的结果；template 明确报不支持。 */
    private boolean evalCondition(File scriptDir, JSONObject step, int depth, JSONArray logs)
            throws Exception {
        String kind = step.optString("kind", "");
        boolean ok;
        String reason;
        if ("appInstalled".equals(kind)) {
            String pkg = step.optString("packageName", "").trim();
            ok = packageInstalled(pkg);
            reason = "应用 " + pkg + (ok ? " 已安装" : " 未安装");
        } else if ("shell".equals(kind)) {
            String cmd = step.optString("command", "").trim();
            String expected = step.optString("expected", "");
            ShellResult r = shell(cmd, false, 60);
            if (r.exit != 0) {
                ok = false;
                reason = "Shell 命令执行失败: " + firstLine(r.stderr);
            } else {
                ok = r.stdout.contains(expected);
                reason = "Shell 输出「" + abbreviate(r.stdout, 120) + "」"
                        + (ok ? "包含" : "未包含") + "「" + expected + "」";
            }
        } else if ("time".equals(kind)) {
            reason = waitUntilTime(step);
            ok = reason == null;
            if (ok) {
                reason = "设备时间已到 " + step.optString("targetTime", "");
            }
        } else if ("accClick".equals(kind)) {
            JSONObject res = probe.accessibilityClick(step);
            ok = res.optInt("code", 500) == 200;
            reason = ok ? "无障碍点击成功" : "无障碍点击未成功: " + res.optString("message", "");
        } else if ("template".equals(kind)) {
            throw new StepFail("判断步骤 template 设备端不支持模板匹配");
        } else {
            throw new StepFail("未知判断类型: " + kind);
        }
        if (step.optBoolean("negate", false)) {
            ok = !ok;
            reason += "（已取反）";
        }
        Log.i(TAG, "condition " + kind + " -> " + ok + " (" + reason + ")");
        return ok;
    }

    /** time 条件：等设备本地时钟到 targetTime（当天已过立即成立），返回 null=到点，否则为失败原因。 */
    private String waitUntilTime(JSONObject step) throws Exception {
        String target = step.optString("targetTime", "").trim();
        Matcher m = Pattern.compile("^(\\d{1,2}):(\\d{2})(?::(\\d{2}))?$").matcher(target);
        if (!m.find()) {
            return "目标时刻无效: " + target;
        }
        int targetSec = Integer.parseInt(m.group(1)) * 3600
                + Integer.parseInt(m.group(2)) * 60
                + (m.group(3) == null ? 0 : Integer.parseInt(m.group(3)));
        long timeout = step.optLong("timeout", 3600);
        long poll = Math.max(100, step.optLong("interval", 1000));
        long deadline = System.currentTimeMillis() + timeout * 1000;
        while (System.currentTimeMillis() < deadline) {
            int now = localSeconds();
            if (now >= targetSec) {
                return null;
            }
            sleep(poll);
        }
        return "等待目标时刻 " + target + " 超时（timeout " + timeout + "s）";
    }

    /** 嵌套执行脚本（runScript / 分支 runScript）：从 script_store 加载，深度受限。 */
    private void runNested(File scriptDir, String name, int repeat, int depth, JSONArray logs)
            throws Exception {
        if (depth >= MAX_DEPTH) {
            throw new StepFail("嵌套执行脚本深度超过上限 " + MAX_DEPTH);
        }
        String clean = safeName(name, "script");
        File nestedDir = new File(dir(DIR_STORE), clean);
        File jsonFile = new File(nestedDir, "script.json");
        if (!jsonFile.isFile()) {
            throw new StepFail("嵌套脚本不存在: " + clean);
        }
        JSONObject nested = new JSONObject(readText(jsonFile));
        JSONArray steps = nested.optJSONArray("steps");
        if (steps == null || steps.length() == 0) {
            throw new StepFail("嵌套脚本 steps 为空: " + clean);
        }
        Log.i(TAG, "runScript nested " + clean + " depth=" + (depth + 1));
        runSteps(nestedDir, nested, steps, repeat, depth + 1, logs);
    }

    // ===== shell / 设备辅助 =====

    private static class ShellResult {
        final int exit;
        final String stdout;
        final String stderr;

        ShellResult(int exit, String stdout, String stderr) {
            this.exit = exit;
            this.stdout = stdout;
            this.stderr = stderr;
        }
    }

    /** 经 0x08 的 shellExec 执行命令：root=true 走 su 0 sh -c。 */
    private ShellResult shell(String command, boolean root, int timeoutSec) throws Exception {
        JSONObject req = new JSONObject();
        req.put("command", command);
        req.put("root", root);
        req.put("timeout", timeoutSec);
        JSONObject data = probe.shellExec(req);
        return new ShellResult(data.optInt("exit", -1),
                data.optString("stdout", ""), data.optString("stderr", ""));
    }

    private static void checkExit(ShellResult r, String what) throws Exception {
        if (r.exit != 0) {
            throw new Exception(what + " 退出码 " + r.exit
                    + (r.stderr.isEmpty() ? "" : ": " + firstLine(r.stderr)));
        }
    }

    /** wm size 解析实际屏幕尺寸（Override 优先，其次 Physical）；解析失败回退 800x1280 并告警。 */
    private int[] screenSize() {
        try {
            ShellResult r = shell("wm size", false, 10);
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

    private boolean packageInstalled(String pkg) throws Exception {
        return shell("pm path " + pkg, false, 15).exit == 0;
    }

    private void checkInstalled(String pkg) throws Exception {
        if (!packageInstalled(pkg)) {
            throw new Exception("应用未安装: " + pkg);
        }
    }

    /** 音量流类型映射（对齐服务端 adb_utils.get_stream_type）。 */
    private static int streamType(String stream) {
        if ("ring".equals(stream)) {
            return 2;
        }
        if ("alarm".equals(stream)) {
            return 4;
        }
        if ("notification".equals(stream)) {
            return 5;
        }
        return 3;
    }

    // ===== 文本变量 / 小工具 =====

    /**
     * 展开步骤文本变量（对齐服务端 _resolve_step_text）：
     * {执行次数} → 当前 count 序号，{执行轮数} → 当前轮号，{N位随机数} → N 位随机数字。
     */
    private static String resolveText(String text, int round, int countIdx) {
        if (text == null || text.isEmpty()) {
            return "";
        }
        String out = text.replace("{执行次数}", String.valueOf(countIdx))
                .replace("{执行轮数}", String.valueOf(round));
        Matcher m = Pattern.compile("\\{(\\d+)位随机数\\}").matcher(out);
        StringBuilder sb = new StringBuilder();
        Random random = new Random();
        int last = 0;
        while (m.find()) {
            int digits = Integer.parseInt(m.group(1));
            if (digits < 1 || digits > 18) {
                continue;
            }
            sb.append(out, last, m.start());
            StringBuilder num = new StringBuilder();
            for (int i = 0; i < digits; i++) {
                num.append(random.nextInt(10));
            }
            // 首位不为 0，避免长数字串被当成科学计数
            if (digits > 1 && num.charAt(0) == '0') {
                num.setCharAt(0, '1');
            }
            sb.append(num);
            last = m.end();
        }
        sb.append(out.substring(last));
        return sb.toString();
    }

    /** 脚本 / zip 名校验：非空、不含路径分隔符与 ..，防止路径穿越。 */
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

    private File dir(String name) throws Exception {
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

    private static JSONArray listChildNames(File dir) {
        JSONArray arr = new JSONArray();
        String[] names = dir.list();
        if (names != null) {
            for (String n : names) {
                arr.put(n);
            }
        }
        return arr;
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

    private static void log(JSONArray logs, int no, String type, boolean ok, String message)
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

    /** input text 参数加单引号防注入，内部单引号按 shell 规则转义。 */
    private static String shellQuote(String s) {
        return "'" + s.replace("'", "'\\''") + "'";
    }

    private static float optFloat(JSONObject o, String key) {
        return optFloat(o, key, 0f);
    }

    private static float optFloat(JSONObject o, String key, float def) {
        double v = o.optDouble(key, def);
        return (float) v;
    }

    private static int localSeconds() {
        java.util.Calendar c = java.util.Calendar.getInstance();
        return c.get(java.util.Calendar.HOUR_OF_DAY) * 3600
                + c.get(java.util.Calendar.MINUTE) * 60
                + c.get(java.util.Calendar.SECOND);
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

    private static void sleep(long ms) {
        try {
            Thread.sleep(ms);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
    }
}
