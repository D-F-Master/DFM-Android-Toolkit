/**
 * dfm.js — DFM Probe JS 脚本运行时占位（方案3 P0）。
 *
 * P0 阶段 dfm.* 宿主 API 由 JsEngine.DfmBridge 直接注册到全局（tap/swipe/longpress/
 * input/keyevent/accClick/sleep/shell/speak/startApp/stopApp/installApk/uninstallApp/
 * deleteFolder/reboot/screenSize/log/randomDigits），本文件暂不注入执行环境。
 *
 * P1 阶段此文件将承载 JSON→JS 编译产物的公共运行时（步骤调度、条件判断、
 * 变量替换 {执行次数}/{执行轮数}/{N位随机数}、goto/stop/fail 控制流），
 * 由 JsEngine 在 evaluateString 前预编译进作用域。
 *
 * 设计文档：docx/脚本方案3-设备端JS执行设计.md
 */
var DFM_RUNTIME_VERSION = '0.0.1-p0-placeholder';
