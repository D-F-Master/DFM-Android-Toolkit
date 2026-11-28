# -*- coding: utf-8 -*-
"""
模拟脚本 JSON → 设备端 JS 编译器（方案3 P1）

把 data/scripts/{name}.json 的步骤数组编译成 script.js（Rhino ES6 兼容子集），
推送设备后由 JsEngine（0x13）执行。设计文档：docx/脚本方案3-设备端JS执行设计.md §3。

与 PC 引擎（script.py::_run_script_on_device）逐条对齐的语义：
- 轮次循环在设备引擎 Java 层（每轮 evaluateString 一次 script.js），
  {执行轮数} 通过 dfm.roundNo() 读取；
- {执行次数} = 步骤在本轮内的 1 基序号（与 PC 传 i+1 一致），编译期为常量；
- count/interval/retry 步骤级循环；condition 分支（goto/stop/fail/runScript/
  直接执行步骤功能，含 volumeAction 映射）；goto 上限 100 每块独立计数；
- runScript 嵌套：编译期内联子脚本步骤数组（深度上限 3），独立 idx/jumps；
- shell 步骤非 root（PC 经 adb shell 同权限）；installApk/uninstallApp/
  deleteFolder/reboot 用 root（设备端宿主实现）；
- waitFor / condition-template 需要 P2 设备端模板匹配，编译期明确报错。
"""

import json
from pathlib import Path
from typing import List

MAX_SCRIPT_JUMPS = 100
MAX_NESTED_SCRIPT_DEPTH = 3
MAX_STEP_RETRIES = 5
STEP_RETRY_INTERVAL_CAP = 600000

SUPPORTED_STEP_TYPES = (
    "tap", "swipe", "longpress", "input",
    "installApk", "deleteFolder", "startApp", "stopApp", "uninstallApp",
    "keyevent", "volume", "reboot", "tts", "accClick", "runScript",
    "sleep", "shell", "screenshot", "waitFor", "condition",
)
# 分支动作允许直接执行的步骤功能（不含 condition 防套娃；runScript 单独处理）
BRANCH_STEP_ACTIONS = tuple(t for t in SUPPORTED_STEP_TYPES if t not in ("runScript", "condition"))


class CompileError(Exception):
    """编译失败（脚本本身的问题，消息直接面向用户）"""


def _js(v) -> str:
    """Python 值 → JS 字面量"""
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    return json.dumps(str(v), ensure_ascii=False)


def _js_obj(d: dict) -> str:
    parts = [f"{k}: {_js(v)}" for k, v in (d or {}).items()]
    return "{" + ", ".join(parts) + "}"


class Compiler:
    def __init__(self, scripts_dir: Path, apks_dir: Path, depth: int = 0,
                 templates_dir: Path = None):
        self.scripts_dir = Path(scripts_dir)
        self.apks_dir = Path(apks_dir)
        self.templates_dir = Path(templates_dir) if templates_dir else None
        self.depth = depth
        # 需要打进 zip 的 APK 文件名（installApk 步骤引用），端点据此打包
        self.required_apks: List[str] = []
        # 需要打进 zip 的模板文件名（waitFor / condition-template 引用）
        self.required_templates: List[str] = []
        # 内联子脚本声明（最深层先注册：合并子编译器的 _sub_vars 后再 append 自己）
        self._sub_vars: List[tuple] = []
        self._sub_counter = 0

    # ---- 单步骤函数体（执行一次；count/interval/retry 由 __runSteps 包装） ----

    def _emit_body(self, step: dict, seq: int) -> str:
        stype = step.get("type")

        if stype == "tap":
            return f"tap(__sx({float(step['x1'])}, 'w'), __sx({float(step['y1'])}, 'h'));"

        if stype == "longpress":
            dur = max(100, int(step.get("duration", 800) or 800))
            return f"longpress(__sx({float(step['x1'])}, 'w'), __sx({float(step['y1'])}, 'h'), {dur});"

        if stype == "swipe":
            x2 = float(step.get("x2", step["x1"]))
            y2 = float(step.get("y2", step["y1"]))
            # PC 引擎 swipe 用 10 段 move（瞬时），设备端 input swipe 用时长近似（300ms）
            return (f"swipe(__sx({float(step['x1'])}, 'w'), __sx({float(step['y1'])}, 'h'), "
                    f"__sx({x2}, 'w'), __sx({y2}, 'h'), 300);")

        if stype == "input":
            text = str(step.get("text") or "")
            # 非 ASCII 由宿主 input 明确报错（P2 提供 unicode 输入链）
            return f"input(__resolveText({_js(text)}, {seq}, dfm.roundNo()));"

        if stype == "installApk":
            apk = str(step.get("apkName", "")).strip()
            if not apk:
                raise CompileError("installApk 步骤未选择 APK 文件")
            if not (self.apks_dir / apk).is_file():
                raise CompileError(f"APK {apk} 不存在，请先上传到 APK 仓库")
            if apk not in self.required_apks:
                self.required_apks.append(apk)
            return f"installApk({_js(apk)});"

        if stype == "deleteFolder":
            return f"deleteFolder({_js(str(step.get('path', '')).strip())});"

        if stype == "startApp":
            pkg = str(step.get("packageName", "")).strip()
            act = str(step.get("activity", "") or "").strip()
            # 与 PC 引擎一致：先查安装；am/monkey 输出含 Error 判失败（宿主 exit 常为 0）
            cmd = f"am start -n {pkg}/{act}" if act else f"monkey -p {pkg} -c android.intent.category.LAUNCHER 1"
            return (
                f"if (!installed({_js(pkg)})) throw new Error('设备未安装应用 {pkg}，无法启动');\n"
                f"  __expectNoError({_js(cmd)});"
            )

        if stype == "stopApp":
            pkg = str(step.get("packageName", "")).strip()
            return (
                f"if (!installed({_js(pkg)})) throw new Error('设备未安装应用 {pkg}，无法停止');\n"
                f"  stopApp({_js(pkg)});"
            )

        if stype == "uninstallApp":
            pkg = str(step.get("packageName", "")).strip()
            return (
                f"if (!installed({_js(pkg)})) throw new Error('设备未安装应用 {pkg}，无法卸载');\n"
                f"  uninstallApp({_js(pkg)});"
            )

        if stype == "keyevent":
            return f"keyevent({int(step.get('keycode'))});"

        if stype == "volume":
            action = str(step.get("action", "")).strip()
            stream = str(step.get("stream", "") or "").strip() or "media"
            return f"volume({_js(action)}, {_js(stream)});"

        if stype == "reboot":
            body = "reboot();"
            if step.get("waitOnline", True):
                timeout = min(600, max(30, int(step.get("onlineTimeout", 180) or 180)))
                body += f"\n  __waitBoot({timeout});"
            return body

        if stype == "tts":
            text = str(step.get("text", ""))
            return f"speak(__resolveText({_js(text)}, {seq}, dfm.roundNo()).trim());"

        if stype == "accClick":
            spec = {k: str(step.get(k, "") or "").strip() for k in ("id", "text", "desc")}
            attempts = []
            if spec["id"] and spec["text"] and spec["desc"]:
                attempts.append(spec)
            for k in ("desc", "text", "id"):
                if spec[k]:
                    attempts.append({k: spec[k]})
            has_xy = isinstance(step.get("x"), (int, float)) and isinstance(step.get("y"), (int, float))
            lines = ["var __ok = false;",
                     f"var __as = [{', '.join(_js_obj(a) for a in attempts)}];"]
            lines.append("for (var i = 0; i < __as.length; i++) { var r = accClick(__as[i]); "
                         "if (r !== undefined) { __ok = true; break; } }")
            if has_xy:
                # 录制坐标兜底（动态文本回放匹配不到时）：坐标按比例换算后 tap
                lines.append("if (!__ok) { tap(__sx(%f, 'w'), __sx(%f, 'h')); __ok = true; }"
                             % (float(step["x"]), float(step["y"])))
            else:
                lines.append("if (!__ok) throw new Error('无障碍点击失败: 无匹配节点且无兜底坐标');")
            return "\n  ".join(lines)

        if stype == "sleep":
            seconds = float(step.get("seconds", 1) or 1)
            return f"sleep({int(seconds * 1000)});"

        if stype == "shell":
            cmd = str(step.get("command", "")).strip()
            expect = str(step.get("expectOutput", "") or "")
            fail_on_err = bool(step.get("failOnError"))
            # PC 端 shell 步骤经 adb shell（非 root），设备端对齐用 root=false
            lines = [f"var sh = shell({_js(cmd)}, false);"]
            if expect:
                lines.append(f"if (String(sh.stdout || '').indexOf({_js(expect)}) < 0) "
                             f"throw new Error('Shell 步骤输出未包含 {_js(expect)}：' "
                             f"+ String(sh.stdout || sh.stderr || '').slice(0, 200));")
            if fail_on_err:
                lines.append("if (sh.exit !== 0) throw new Error('Shell 命令返回码 ' + sh.exit + ': ' "
                             "+ String(sh.stderr || sh.stdout || '').slice(0, 200));")
            return "\n  ".join(lines)

        if stype == "screenshot":
            label = str(step.get("label", "") or "").strip()
            return f"__shot({_js(label)});"

        if stype == "waitFor":
            tpl = str(step.get("template", "") or "").strip()
            if not tpl:
                raise CompileError("waitFor 步骤未选择模板")
            self._require_template(tpl)
            timeout = min(86400, max(0, float(step.get("timeout", 15) or 15)))
            threshold = float(step.get("threshold", 0.85) or 0.85)
            return f"__waitTpl({_js(tpl)}, {timeout}, {threshold});"

        raise CompileError(f"不支持的步骤类型: {stype}")

    # ---- condition 步骤 → 控制流 ----

    def _emit_condition(self, step: dict, total: int) -> str:
        kind = step.get("kind")
        negate = bool(step.get("negate", False))

        if kind == "appInstalled":
            pkg = str(step.get("packageName", "")).strip()
            if not pkg:
                raise CompileError("判断步骤未填写应用包名")
            expr = f"installed({_js(pkg)})"
        elif kind == "shell":
            cmd = str(step.get("command", "")).strip()
            expected = str(step.get("expected", ""))
            expr = f"(String(shell({_js(cmd)}, false).stdout || '').indexOf({_js(expected)}) >= 0)"
        elif kind == "time":
            target = str(step.get("targetTime", "")).strip()
            timeout = min(86400, max(1, float(step.get("timeout", 3600) or 3600)))
            poll = max(100, int(step.get("interval", 1000) or 1000))
            expr = f"__waitClock({_js(target)}, {int(timeout * 1000)}, {poll})"
        elif kind == "accClick":
            spec = {k: str(step.get(k, "") or "").strip() for k in ("id", "text", "desc")}
            if not any(spec.values()):
                raise CompileError("无障碍点击条件需至少填写 id/text/desc 一项")
            expr = f"(accClick({_js_obj(spec)}) !== undefined)"
        elif kind == "accExists":
            spec = {k: str(step.get(k, "") or "").strip() for k in ("id", "text", "desc")}
            if not any(spec.values()):
                raise CompileError("无障碍组件判断需至少填写 id/text/desc 一项")
            expr = f"accExists({_js_obj(spec)})"
        elif kind == "template":
            tpl = str(step.get("template", "") or "").strip()
            if not tpl:
                raise CompileError("画面判断步骤未选择模板")
            self._require_template(tpl)
            # timeout=0 单次匹配（对齐 PC 端 condition-template 语义）
            timeout = min(86400, max(0, float(step.get("timeout", 0) or 0)))
            threshold = float(step.get("threshold", 0.85) or 0.85)
            expr = f"(waitFor({_js(tpl)}, {timeout}, {threshold}) !== null)"
        else:
            raise CompileError(f"未知判断类型: {kind}")

        branches = {}
        for label in ("onTrue", "onFalse"):
            branch = step.get(label) or {}
            action = branch.get("action", "continue")
            if action == "goto":
                target = int(branch.get("target", 0))
                if not (1 <= target <= total):
                    raise CompileError(f"判断步骤跳转目标无效: 第 {target} 步（共 {total} 步）")
                branches[label] = ("goto", target)
            elif action == "runScript":
                name = str(branch.get("script", "")).strip()
                if not name:
                    raise CompileError(f"{label} 动作是执行脚本，但未选择脚本")
                var, repeat = self._inline_script(name)
                branches[label] = ("fn", f"__runSub({var}, {repeat});")
            elif action == "volume":
                # 分支 action 键被占用：音量子动作存 volumeAction（PC _run_branch_step 同规则）
                fake = {k: v for k, v in branch.items() if k not in ("action", "target")}
                fake["type"] = "volume"
                fake["action"] = str(branch.get("volumeAction", "up"))
                branches[label] = ("fn", self._emit_body(fake, 0))
            elif action in BRANCH_STEP_ACTIONS:
                fake = {k: v for k, v in branch.items() if k not in ("action", "target")}
                fake["type"] = action
                branches[label] = ("fn", self._emit_body(fake, 0))
            elif action in ("continue", "stop", "fail"):
                branches[label] = (action, None)
            else:
                raise CompileError(f"{label} 动作无效: {action}")

        def b_js(b):
            k, v = b
            if k == "goto":
                return f"{{action:'goto',target:{v}}}"
            if k == "fn":
                return f"{{action:'fn',fn:function(){{ {v} }}}}"
            return f"{{action:'{k}'}}"

        cond = f"(!({expr}))" if negate else f"({expr})"
        return f"__branch({cond}, {b_js(branches['onTrue'])}, {b_js(branches['onFalse'])});"

    def _require_template(self, name: str):
        """模板存在性校验 + 登记（端点据此打包 templates/）。"""
        if self.templates_dir is not None:
            if not (self.templates_dir / name).is_file():
                raise CompileError(f"模板 {name} 不存在于模板仓库，请先上传")
        if name not in self.required_templates:
            self.required_templates.append(name)

    # ---- 嵌套脚本内联 ----

    def _inline_script(self, name: str):
        if self.depth >= MAX_NESTED_SCRIPT_DEPTH:
            raise CompileError(f"嵌套执行脚本深度超过上限 {MAX_NESTED_SCRIPT_DEPTH}，请检查是否存在递归嵌套")
        safe = name.strip()
        if "\\" in safe or ".." in safe:
            raise CompileError(f"嵌套脚本名不能包含反斜杠或 ..: {name}")
        path = self.scripts_dir / f"{safe}.json"
        if not path.is_file():
            raise CompileError(f"嵌套脚本 {safe} 不存在，请先保存")
        try:
            sub = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            raise CompileError(f"嵌套脚本 {safe} 读取失败: {e}")
        sub_repeat = max(1, min(99, int(sub.get("repeat", 1) or 1)))
        self._sub_counter += 1
        var = f"__sub{self._sub_counter}"
        sub_c = Compiler(self.scripts_dir, self.apks_dir, depth=self.depth + 1,
                         templates_dir=self.templates_dir)
        sub_c._sub_counter = self._sub_counter
        arr = sub_c.compile_array(sub.get("steps", []))
        self.required_apks.extend(a for a in sub_c.required_apks if a not in self.required_apks)
        self.required_templates.extend(t for t in sub_c.required_templates
                                       if t not in self.required_templates)
        # 先合并子编译器（可能又内联了孙脚本）的声明，再登记自己 → 深层在前
        self._sub_vars.extend(sub_c._sub_vars)
        self._sub_counter = sub_c._sub_counter
        self._sub_vars.append((var, arr))
        return var, sub_repeat

    # ---- 步骤数组编译 ----

    def _compile_one(self, step: dict, idx: int, total: int) -> str:
        if not isinstance(step, dict):
            raise CompileError(f"第 {idx + 1} 步不是对象")
        stype = step.get("type")
        if stype not in SUPPORTED_STEP_TYPES:
            raise CompileError(f"第 {idx + 1} 步的 type 无效或暂不支持: {stype}")
        # {执行次数} 与 PC 一致 = 步骤 1 基序号（编译期常量）；分支子步骤无独立序号，传 0
        seq = idx + 1
        body = (self._emit_condition(step, total) if stype == "condition"
                else self._emit_body(step, seq))
        count = 1 if stype == "condition" else max(1, int(step.get("count", 1) or 1))
        interval = max(0, int(step.get("interval", 0) or 0))
        retry = 0 if stype == "condition" else max(0, min(MAX_STEP_RETRIES, int(step.get("retry", 0) or 0)))
        retry_interval = max(0, min(STEP_RETRY_INTERVAL_CAP, int(step.get("retryInterval", 2000) or 2000)))
        meta = {"type": stype, "count": count, "interval": interval,
                "retry": retry, "retryInterval": retry_interval,
                "label": f"步骤 {idx + 1}"}
        return f"{{meta:{_js_obj(meta)},run:function(){{\n  {body}\n}}}}"

    def compile_array(self, steps: List[dict]) -> str:
        """步骤数组 → JS 数组字面量（不含子脚本声明，由 compile_script 统一发射）"""
        if not isinstance(steps, list) or not steps:
            raise CompileError("脚本 steps 必须是非空数组")
        total = len(steps)
        items = [self._compile_one(s, i, total) for i, s in enumerate(steps)]
        return "[\n" + ",\n".join(items) + "\n]"


def compile_script(script: dict, scripts_dir: Path, apks_dir: Path, name: str = "",
                   templates_dir: Path = None) -> dict:
    """
    脚本 JSON → script.js 源码 + 元信息

    返回 {js, apks, repeat, steps}；抛 CompileError（消息面向用户）。
    """
    steps = script.get("steps", [])
    if not isinstance(steps, list) or not steps:
        raise CompileError("脚本 steps 必须是非空数组")
    repeat = max(1, int(script.get("repeat", 1) or 1))
    video_w = int(script.get("videoWidth") or 0)
    video_h = int(script.get("videoHeight") or 0)
    display_name = name or str(script.get("name") or "(未命名)")

    templates_dir = scripts_dir.parent / "templates" if templates_dir is None else templates_dir
    c = Compiler(scripts_dir, apks_dir, depth=0, templates_dir=templates_dir)
    arr_js = c.compile_array(steps)
    # 内联子脚本声明（深层先注册，Rhino var 提升使求值顺序无碍，仅求可读性）
    sub_decls = "\n".join(f"var {v} = [\n{a}\n];" for v, a in c._sub_vars)

    header = (
        f"// 由 DFM-service 编译生成（{display_name}），请勿手改\n"
        f"var apiVersion = 1;\n"
        f"var __meta = {{name:{_js(display_name)},repeat:{repeat},"
        f"videoWidth:{video_w},videoHeight:{video_h},steps:{len(steps)}}};\n"
    )
    js = header + _RUNTIME + "\n" + sub_decls + f"\nvar __steps = \n{arr_js}\n;\n" + _MAIN
    return {"js": js, "apks": c.required_apks, "templates": c.required_templates,
            "repeat": repeat, "steps": len(steps),
            "videoWidth": video_w, "videoHeight": video_h, "name": display_name}


# ==== 运行时 prelude（Rhino ES6；宿主函数已平铺到全局，轮次由引擎 Java 层驱动） ====
_RUNTIME = r"""
function __sx(v, axis) { // 录制基准坐标 → 实际屏幕坐标（基准缺失或相等时原样取整）
  var base = axis === 'w' ? __meta.videoWidth : __meta.videoHeight;
  var cur = axis === 'w' ? __SCR.w : __SCR.h;
  return (base > 0 && cur > 0 && base !== cur) ? Math.round(v * cur / base) : Math.round(v);
}
function __resolveText(s, seq, round) { // 与 PC 端 _resolve_step_text 同语义
  s = String(s == null ? '' : s)
    .replace(/\{执行次数\}/g, String(seq))
    .replace(/\{执行轮数\}/g, String(round));
  return s.replace(/\{(\d+)位随机数\}/g, function(m, n) {
    n = parseInt(n, 10);
    if (!(n >= 1 && n <= 18)) return m;
    var out = '';
    for (var i = 0; i < n; i++) out += Math.floor(Math.random() * 10);
    if (n > 1 && out.charAt(0) === '0') out = '1' + out.substring(1);
    return out;
  });
}
function __expectNoError(cmd) { // am start 失败经常 exit 0，错误只在输出里
  var sh = shell(cmd, false);
  var out = String(sh.stdout || '') + String(sh.stderr || '');
  if (out.indexOf('Error') >= 0) throw new Error(out.split('\n')[0].slice(0, 120));
}
function __waitBoot(timeoutSec) { // reboot 后轮询 sys.boot_completed（PC 端等消失+重现的近似）
  sleep(3000);
  var deadline = Date.now() + timeoutSec * 1000;
  while (Date.now() < deadline) {
    try {
      var s = shell('getprop sys.boot_completed', false);
      if (String(s.stdout || '').trim() === '1') { dfm.log('设备已重新上线'); return; }
    } catch (e) { /* 重启中 shell 不可用：继续等 */ }
    sleep(2000);
  }
  throw new Error('等待设备重新上线超时（' + timeoutSec + 's 内未恢复）');
}
function __waitTpl(tpl, timeoutSec, th) { // waitFor 步骤：命中记日志，超时报错（PC 端同语义）
  var m = waitFor(tpl, timeoutSec, th);
  if (m === null) throw new Error('等待组件超时（' + timeoutSec + 's 内未出现: ' + tpl + '）');
  dfm.log('组件已出现: ' + tpl + ' @ ' + Math.round(m.x) + ',' + Math.round(m.y)
    + ' score=' + m.score.toFixed(3));
}
function __shot(label) {
  var p = screenshot(label || 'step');
  dfm.log('截图已保存: ' + String(p).split('/').pop());
}
function __waitClock(target, timeoutMs, pollMs) { // HH:mm[:ss] 当天到点（对齐 PC time 条件）
  var m = /^(\d{1,2}):(\d{2})(?::(\d{2}))?$/.exec(String(target).trim());
  if (!m) throw new Error('目标时刻无效: ' + target);
  var tsec = parseInt(m[1], 10) * 3600 + parseInt(m[2], 10) * 60 + parseInt(m[3] || '0', 10);
  var deadline = Date.now() + timeoutMs;
  while (true) {
    var out = String(shell('date "+%H:%M:%S"', false).stdout || '');
    var mm = /(\d{1,2}):(\d{2}):(\d{2})/.exec(out);
    if (!mm) throw new Error('读取设备时间失败: ' + out.slice(0, 120));
    var now = parseInt(mm[1], 10) * 3600 + parseInt(mm[2], 10) * 60 + parseInt(mm[3], 10);
    if (now >= tsec) return true;
    if (Date.now() >= deadline) return false;
    sleep(Math.min(pollMs, Math.max(1, deadline - Date.now())));
  }
}
function __branch(ok, onTrue, onFalse) { // condition 分支（goto/stop/fail 用控制异常传给 __runSteps）
  var b = ok ? onTrue : onFalse;
  if (!b || b.action === 'continue') return;
  if (b.action === 'stop') throw {__stop: true};
  if (b.action === 'fail') throw new Error('判断步骤未通过');
  if (b.action === 'goto') throw {__goto: b.target};
  if (b.action === 'fn') { b.fn(); return; }
}
function __runSub(steps, repeat) { // 内联子脚本：共享停止、独立 idx/jumps（与 PC 嵌套语义一致）
  for (var r = 0; r < repeat; r++) __runSteps(steps);
}
"""

# ==== 解释器主循环（每轮 evaluateString 执行一次；轮号读 dfm.roundNo()） ====
_MAIN = r"""
function __runSteps(steps) {
  var idx = 0, jumps = 0;
  while (idx < steps.length) {
    var st = steps[idx], m1 = st.meta, gotoTarget = 0;
    if (m1.type !== 'condition') dfm.log(m1.label + ': ' + m1.type);
    for (var c = 0; c < m1.count && !gotoTarget; c++) {
      var attempt = 0, done = false, lastErr = null;
      while (attempt <= m1.retry && !gotoTarget) {
        try {
          st.run();
          done = true;
          break;
        } catch (e) {
          if (e && e.__goto) { // condition goto：限 100 次防死循环（PC 同语义）
            jumps++;
            if (jumps > 100) throw new Error('判断步骤跳转次数超过上限 100，可能存在死循环');
            gotoTarget = e.__goto;
            break;
          }
          if (e && e.__stop) throw e; // 控制异常不重试
          if (dfm.stopped()) throw e; // 0x14 停止不重试
          lastErr = e;
          attempt++;
          if (attempt > m1.retry) break;
          dfm.log(m1.label + ' 第 ' + attempt + ' 次失败重试: ' + (e && e.message ? e.message : e));
          sleep(m1.retryInterval);
        }
      }
      if (gotoTarget) break;
      if (!done) throw lastErr;
      if (c < m1.count - 1 && m1.interval > 0) sleep(m1.interval);
    }
    if (gotoTarget) { // 块内跳转（目标已在编译期校验 1..len，这里再防御）
      if (!(gotoTarget >= 1 && gotoTarget <= steps.length))
        throw new Error('判断步骤跳转目标无效: 第 ' + gotoTarget + ' 步');
      idx = gotoTarget - 1;
      continue;
    }
    idx++;
    if (idx < steps.length && m1.interval > 0) sleep(m1.interval);
  }
}
var __SCR = {w: screenSize().width, h: screenSize().height};
dfm.log('脚本开始（第 ' + dfm.roundNo() + ' 轮，共 ' + __meta.repeat + ' 轮）');
try {
  __runSteps(__steps);
} catch (e) {
  if (e && e.__stop) { __stopRequested = true; } // 对齐 PC：stop 动作结束整个脚本
  else { throw e; }
}
'完成';
"""
