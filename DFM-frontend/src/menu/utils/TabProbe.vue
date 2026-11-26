<script lang="ts" setup>
import { inject, ref, computed, watch, onMounted, onUnmounted, nextTick } from 'vue'
import axios from 'axios'
import * as echarts from 'echarts/core'
import { LineChart, BarChart, GaugeChart } from 'echarts/charts'
import { GridComponent, TooltipComponent, LegendComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import { useAppConfig } from '../../composables/useAppConfig'
import { useProbeSettings } from '../../composables/useProbeSettings'
import SqliteDbViewer from '../../components/SqliteDbViewer.vue'

echarts.use([LineChart, BarChart, GaugeChart, GridComponent, TooltipComponent, LegendComponent, CanvasRenderer])

const ctx = inject<any>('app-utils')!
const { API_BASE, activeTab, files, imageViewerError, imageViewerKind, imageViewerList, imageViewerLoading, imageViewerName, imageViewerSize, imageViewerSrc, imageViewerVisible, selectedDevice, showList, showMessage, showPrompt } = ctx
const probeBusy = ref('');
const probeResult = ref('');
const probeShellRoot = ref(false);
const probeDisabled = computed(() => probeBusy.value !== '' || !selectedDevice.value);

const probeReady = () => {
  if (probeBusy.value) return false;
  if (!selectedDevice.value) {
    showMessage('请先在左侧选择设备', '探针工具');
    return false;
  }
  return true;
};

const probePost = async (path: string, payload: any, timeout = 200000) => {
  const res = await axios.post(`${API_BASE.value}${path}`, payload, { timeout });
  const d = res.data;
  if (d?.code !== 200 || d?.data?.connected !== true) {
    throw new Error(d?.message || '通讯失败');
  }
  const r = d.data.response || {};
  if (r.code !== 200) {
    throw new Error(r.message || '设备返回错误');
  }
  return r.data || {};
};

const playProbeTts = () => {
  if (!probeReady()) return;
  showPrompt('输入要让设备朗读的文本', (text: string) => {
    const t = (text || '').trim();
    if (!t || new TextEncoder().encode(t).length > 60000) {
      nextTick(() => showMessage(!t ? '文本不能为空' : '文本过长（单帧上限 65535 字节）', '远程 TTS 语音播放'));
      return;
    }
    probeBusy.value = 'tts';
    probeResult.value = '正在请求设备朗读...';
    probePost('/api/probe/message', { deviceId: selectedDevice.value, cmd: 0x01, data: { text: t } })
      .then(() => {
        probeResult.value = '朗读完成';
        showMessage('朗读完成', '远程 TTS 语音播放');
      })
      .catch((e: any) => {
        const text = `播放失败：${e?.response?.data?.message || e?.message || '请求异常'}`;
        probeResult.value = text;
        showMessage(text, '远程 TTS 语音播放');
      })
      .finally(() => { probeBusy.value = ''; });
  }, '输入要朗读的文本', '远程 TTS 语音播放');
};

// 探针工具：心跳（0x00），结果用弹窗提示
const sendProbeHeartbeat = async () => {
  if (!probeReady()) return;
  probeBusy.value = 'heartbeat';
  probeResult.value = '正在检测设备探针...';
  try {
    const info = await probePost('/api/probe/message', { deviceId: selectedDevice.value, cmd: 0x00, data: {} }, 60000);
    probeResult.value = `设备在线，探针版本 ${info.version || '未知'}`;
    showMessage(probeResult.value, '探针心跳');
  } catch (e: any) {
    const text = `心跳失败：${e?.response?.data?.message || e?.message || '请求异常'}`;
    probeResult.value = text;
    showMessage(text, '探针心跳');
  } finally {
    probeBusy.value = '';
  }
};

// 探针工具：shell 命令执行（0x08），文本由弹窗输入
const runProbeShell = () => {
  if (!probeReady()) return;
  showPrompt('输入要在设备上执行的 shell 命令', (command: string) => {
    const cmd = (command || '').trim();
    if (!cmd) {
      nextTick(() => showMessage('命令不能为空', 'Shell 命令执行'));
      return;
    }
    probeBusy.value = 'shell';
    probeResult.value = `$ ${cmd}${probeShellRoot.value ? '  (root)' : ''}\n正在执行...`;
    probePost('/api/probe/message', {
      deviceId: selectedDevice.value,
      cmd: 0x08,
      data: { command: cmd, root: probeShellRoot.value, timeout: 60 }
    })
      .then((info: any) => {
        const parts: string[] = [`$ ${cmd}${probeShellRoot.value ? '  (root)' : ''}`];
        if (info.stdout) parts.push(info.stdout);
        if (info.stderr) parts.push(`[stderr]\n${info.stderr}`);
        parts.push(`[exit ${info.exit}]${info.truncated ? '（输出过长已截断）' : ''}`);
        probeResult.value = parts.join('\n');
      })
      .catch((e: any) => {
        const text = `执行失败：${e?.response?.data?.message || e?.message || '请求异常'}`;
        probeResult.value = text;
        showMessage(text, 'Shell 命令执行');
      })
      .finally(() => { probeBusy.value = ''; });
  }, '例：ls /sdcard', 'Shell 命令执行');
};

// 探针工具：截图（0x09）/ 拍照（0x0A），拍照先取摄像头列表选择
const probeFacing = (facing: string) => {
  if (facing === 'front') return '前置';
  if (facing === 'back') return '后置';
  if (facing === 'external') return '外接';
  return '未知';
};

const doProbeCapture = async (kind: 'screenshot' | 'photo', cameraId = '') => {
  if (!probeReady()) return;
  probeBusy.value = kind;
  const label = kind === 'screenshot' ? '截屏' : '拍照';
  probeResult.value = `正在${label}...`;
  try {
    const payload: any = { deviceId: selectedDevice.value, kind };
    if (cameraId) payload.cameraId = cameraId;
    // capture 响应 data.response.data：screenshot 带 path；photo 额外带 base64 与 deleted
    const res = await axios.post(`${API_BASE.value}/api/probe/capture`, payload, { timeout: 200000 });
    const d = res.data;
    if (d?.code !== 200 || d?.data?.connected !== true) {
      throw new Error(d?.message || '通讯失败');
    }
    const r = d.data.response || {};
    if (r.code !== 200) throw new Error(r.message || '设备返回错误');
    const info = r.data || {};
    const name = (info.path || '').split('/').pop() || (kind === 'screenshot' ? 'screenshot.png' : 'photo.jpg');
    let b64 = info.base64;
    if (!b64) {
      if (info.readError) throw new Error(info.readError);
      // photo 由服务端读成 base64 后立即删除设备端文件，无 base64 说明服务仍是旧代码，不能再按路径取图
      if (kind === 'photo') throw new Error('未取得照片数据，请确认 DFM-service 已重启后重试');
      const resp = await axios.post(`${API_BASE.value}/api/device/files/base64`, {
        deviceId: selectedDevice.value,
        remotePath: info.path
      });
      if (resp.data.code !== 200 || !resp.data.data?.base64) {
        throw new Error(resp.data.message || `已生成 ${name}，但读取预览失败`);
      }
      b64 = resp.data.data.base64;
    }
    const mime = name.toLowerCase().endsWith('.png') ? 'image/png' : 'image/jpeg';
    imageViewerList.value = [];
    imageViewerKind.value = 'image';
    imageViewerName.value = name;
    imageViewerSrc.value = '';
    imageViewerError.value = '';
    imageViewerSize.value = Number(info.size) || 0;
    imageViewerLoading.value = true;
    imageViewerVisible.value = true;
    imageViewerSrc.value = `data:${mime};base64,${b64}`;
    imageViewerLoading.value = false;
    probeResult.value = `${label}完成（${name}）${info.deleted ? '，设备端文件已删除' : ''}`;
  } catch (e: any) {
    const text = `${label}失败：${e?.response?.data?.message || e?.message || '请求异常'}`;
    probeResult.value = text;
    showMessage(text, kind === 'screenshot' ? '屏幕截图' : '摄像头拍照');
  } finally {
    probeBusy.value = '';
  }
};

const onProbePhoto = async () => {
  if (!probeReady()) return;
  probeBusy.value = 'photo';
  probeResult.value = '正在获取摄像头列表...';
  try {
    const res = await axios.post(`${API_BASE.value}/api/probe/cameras`, {
      deviceId: selectedDevice.value
    }, { timeout: 60000 });
    const cameras = res.data?.data?.cameras || [];
    if (res.data?.code !== 200 || cameras.length === 0) {
      throw new Error(res.data?.message || '未检测到摄像头');
    }
    if (cameras.length === 1) {
      // 单摄像头无需选择：先清 busy，否则 doProbeCapture 的 probeReady() 守卫会把本次调用直接吞掉，
      // probeBusy 永远停在 'photo'、界面卡在「正在获取摄像头列表...」（多摄像头分支在 :5239 已清）
      probeBusy.value = '';
      await doProbeCapture('photo', String(cameras[0].id));
      return;
    }
    probeBusy.value = '';
    const items = cameras.map((c: any) => ({
      value: String(c.id),
      label: `摄像头 ${c.id} · ${probeFacing(c.facing)}`,
      desc: c.resolution || ''
    }));
    showList(items, (id: string) => { doProbeCapture('photo', id); }, '选择摄像头', '未检测到摄像头', '拍照');
  } catch (e: any) {
    const text = `拍照失败：${e?.response?.data?.message || e?.message || '请求异常'}`;
    probeResult.value = text;
    showMessage(text, '摄像头拍照');
    probeBusy.value = '';
  }
};

// 探针工具：无障碍权限检测（0x0B），未启用才在设备上打开设置页，已启用直接提示
const openProbeAccessibility = async () => {
  if (!probeReady()) return;
  probeBusy.value = 'accessibility-settings';
  probeResult.value = '正在检测无障碍权限...';
  try {
    const info = await probePost('/api/probe/message', { deviceId: selectedDevice.value, cmd: 0x0B, data: {} }, 60000);
    if (info.enabled) {
      probeResult.value = '设备已启用「DFM Probe」无障碍服务，无需打开设置页';
      showMessage('无障碍权限已开启', '无障碍设置');
    } else {
      probeResult.value = '已在设备上打开无障碍设置页，请手动启用「DFM Probe」无障碍服务后再获取组件树';
      showMessage('已打开无障碍设置页', '无障碍设置');
    }
  } catch (e: any) {
    const text = `打开失败：${e?.response?.data?.message || e?.message || '请求异常'}`;
    probeResult.value = text;
    showMessage(text, '无障碍设置');
  } finally {
    probeBusy.value = '';
  }
};

// 把 0x0C 返回的 {package,nodeCount,truncated,root} 缩进为多行文本
const formatAccessibilityTree = (info: any) => {
  const lines: string[] = [`包名 ${info.package || '未知'} · 节点 ${info.nodeCount || 0}${info.truncated ? '（超出上限已截断）' : ''}`];
  const walk = (node: any, depth: number) => {
    if (!node) return;
    const attrs: string[] = [];
    if (node.clickable) attrs.push('可点击');
    if (node.scrollable) attrs.push('可滚动');
    if (!node.enabled) attrs.push('禁用');
    const label = [node.class, node.text, node.desc].filter(Boolean).join(' · ');
    lines.push(`${'  '.repeat(depth)}${label || '(无文本)'}${node.id ? ` [${node.id}]` : ''}${node.bounds ? ` @${node.bounds}` : ''}${attrs.length ? ` (${attrs.join('/')})` : ''}`);
    (node.children || []).forEach((c: any) => walk(c, depth + 1));
  };
  walk(info.root, 0);
  return lines.join('\n');
};

// 探针工具：获取无障碍组件树（0x0C），未启用无障碍权限时设备端会自动拉起设置页
const getProbeAccessibilityTree = async () => {
  if (!probeReady()) return;
  probeBusy.value = 'accessibility-tree';
  probeResult.value = '正在读取组件树...';
  try {
    const info = await probePost('/api/probe/message', { deviceId: selectedDevice.value, cmd: 0x0C, data: {} });
    probeResult.value = formatAccessibilityTree(info);
    showMessage(`组件树读取完成（${info.package || '未知包名'}，${info.nodeCount || 0} 节点）`, '无障碍组件树');
  } catch (e: any) {
    const raw = e?.response?.data?.message || e?.message || '';
    const text = raw.includes('已在设备上打开无障碍设置页')
      ? '设备未启用无障碍权限，已在设备上打开设置页，请启用「DFM Probe」后重新获取'
      : `获取失败：${raw || '请求异常'}`;
    probeResult.value = text;
    showMessage(text, '无障碍组件树');
  } finally {
    probeBusy.value = '';
  }
};

// ---------- 设备端离线脚本：0x0E 导入 / 0x0F 列表 / 0x10 执行 ----------

// 0x0F 返回的 {scripts:[{name,steps,size,importedAt,error?}]} 格式化为多行文本
const formatProbeScriptList = (info: any) => {
  const scripts = info.scripts || [];
  if (!scripts.length) return '设备端还没有脚本，请先用「导入脚本」把 zip 包推送到设备';
  const lines = [`设备端脚本 ${scripts.length} 个：`];
  scripts.forEach((s: any) => {
    lines.push(`  • ${s.name} — ${s.steps ?? 0} 步 · ${s.size ?? 0} B · ${s.importedAt || '未知时间'}${s.error ? `（${s.error}）` : ''}`);
  });
  return lines.join('\n');
};

// 0x10 返回的 {script,ok,error,summary,logs[],truncated} 格式化为多行文本
const formatProbeScriptRun = (info: any) => {
  const s = info.summary || {};
  const head = [
    `脚本 ${info.script || '?'} ${info.ok ? '执行成功' : '执行失败'}${info.error ? `：${info.error}` : ''}`,
    `轮数 ${s.rounds ?? '?'} · 步骤 ${s.steps ?? '?'} · 耗时 ${s.durationMs ?? '?'}ms${info.truncated ? '（日志已截断）' : ''}`,
  ];
  const logs = (info.logs || []).map((l: any) => `  ${l.no}. ${l.type} ${l.ok ? '✓' : '✗'} ${l.message || ''}`);
  return [...head, ...logs].join('\n');
};

// 0x0E：把脚本页导出的 zip 推到设备并解压（后端 script-import 先 adb push 再发 0x0E）
const importProbeScript = () => {
  if (!probeReady()) return;
  // 用系统文件对话框选 zip，取选中文件的本地绝对路径传给后端（后端 adb push 需要路径，不能传文件内容）
  // File.path 是 Electron/uTools 渲染进程提供的字段（同 TabApps 里选 APK 取路径的做法）
  const input = document.createElement('input');
  input.type = 'file';
  input.accept = '.zip,application/zip';
  input.onchange = () => {
    const file = input.files?.[0] as (File & { path?: string }) | undefined;
    if (!file) return;
    const zipPath = (file.path || '').trim();
    if (!zipPath) {
      showMessage('无法获取文件本地路径，请确认在 uTools/桌面端中使用（浏览器环境不支持）', '导入脚本到设备');
      return;
    }
    if (!zipPath.toLowerCase().endsWith('.zip')) {
      showMessage('请选择 .zip 文件', '导入脚本到设备');
      return;
    }
    probeBusy.value = 'script-import';
    probeResult.value = `正在推送并解压脚本包「${file.name}」到设备...`;
    probePost('/api/probe/script-import', { deviceId: selectedDevice.value, zipPath }, 600000)
      .then((info: any) => {
        probeResult.value = [
          `已导入脚本「${info.script || '?'}」`,
          `步骤 ${info.steps ?? 0} 个 · 文件 ${info.files ?? 0} 个`,
          info.apks?.length ? `APK：${info.apks.join('、')}` : '',
          info.templates?.length ? `模板图：${info.templates.join('、')}` : '',
          `设备端目录：${info.path || ''}`,
        ].filter(Boolean).join('\n');
        showMessage(`脚本「${info.script || '?'}」已导入设备`, '导入脚本到设备');
      })
      .catch((e: any) => {
        const text = `导入失败：${e?.response?.data?.message || e?.message || '请求异常'}`;
        probeResult.value = text;
        showMessage(text, '导入脚本到设备');
      })
      .finally(() => { probeBusy.value = ''; });
  };
  input.click();
};

// 0x0F：列出设备端已导入的脚本
const listProbeScripts = async () => {
  if (!probeReady()) return;
  probeBusy.value = 'script-list';
  probeResult.value = '正在读取设备端脚本列表...';
  try {
    const info = await probePost('/api/probe/script-list', { deviceId: selectedDevice.value }, 60000);
    probeResult.value = formatProbeScriptList(info);
    showMessage(`设备端脚本 ${(info.scripts || []).length} 个`, '设备脚本列表');
  } catch (e: any) {
    const text = `读取失败：${e?.response?.data?.message || e?.message || '请求异常'}`;
    probeResult.value = text;
    showMessage(text, '设备脚本列表');
  } finally {
    probeBusy.value = '';
  }
};

// 0x10：先列出设备端脚本供选择，再输入轮数执行
const runProbeScript = async () => {
  if (!probeReady()) return;
  probeBusy.value = 'script-run';
  probeResult.value = '正在读取设备端脚本列表...';
  try {
    const info = await probePost('/api/probe/script-list', { deviceId: selectedDevice.value }, 60000);
    const scripts = info.scripts || [];
    if (!scripts.length) throw new Error('设备端还没有脚本，请先用「导入脚本」推送');
    // 先清 busy，否则后续弹窗回调里的操作会被 probeReady 守卫吞掉（同 onProbePhoto）
    probeBusy.value = '';
    const items = scripts.map((s: any) => ({
      value: s.name,
      label: s.name,
      desc: `${s.steps ?? 0} 步 · ${s.importedAt || ''}`,
    }));
    showList(items, (name: string) => {
      // handleModalConfirm 在回调返回后会 modalVisible=false，同步弹 prompt 会被立刻关掉，
      // 故 nextTick 延迟到关闭之后（同 ModalDialog 里 watch 错开类型切换的时序处理）
      nextTick(() => showPrompt('输入执行轮数（0=按脚本自身配置，1-1000）', (repeatText: string) => {
        const repeat = repeatText.trim() === '' ? 1 : Number(repeatText.trim());
        if (!Number.isInteger(repeat) || repeat < 0 || repeat > 1000) {
          nextTick(() => showMessage('轮数需为 0-1000 的整数', '执行设备脚本'));
          return;
        }
        probeBusy.value = 'script-run';
        probeResult.value = `正在执行脚本「${name}」${repeat} 轮...`;
        probePost('/api/probe/script-run', { deviceId: selectedDevice.value, script: name, repeat }, 200000)
          .then((r: any) => {
            probeResult.value = formatProbeScriptRun(r);
            showMessage(`脚本「${name}」${r.ok ? '执行成功' : '执行失败'}`, '执行设备脚本');
          })
          .catch((e: any) => {
            const text = `执行失败：${e?.response?.data?.message || e?.message || '请求异常'}`;
            probeResult.value = text;
            showMessage(text, '执行设备脚本');
          })
          .finally(() => { probeBusy.value = ''; });
      }, '1', '执行设备脚本'));
    }, '选择要执行的设备端脚本', '设备端还没有脚本', '执行');
  } catch (e: any) {
    const text = `读取失败：${e?.response?.data?.message || e?.message || '请求异常'}`;
    probeResult.value = text;
    showMessage(text, '执行设备脚本');
    probeBusy.value = '';
  }
};

// ---------- 摄像头画面替换：0x11 替换 / 0x12 恢复真实画面，相机列表复用 0x02 ----------

// 镜像补偿：晨检 App 预览自带水平镜像，替换画面先反向镜像一次以抵消
const probeCamMirror = ref(false);

// 相机列表（0x02 已有关键字）：结果写进输出区
const listProbeCameras = async () => {
  if (!probeReady()) return;
  probeBusy.value = 'cam-list';
  probeResult.value = '正在获取摄像头列表...';
  try {
    const res = await axios.post(`${API_BASE.value}/api/probe/cameras`, {
      deviceId: selectedDevice.value
    }, { timeout: 60000 });
    const cameras = res.data?.data?.cameras || [];
    if (res.data?.code !== 200 || cameras.length === 0) {
      throw new Error(res.data?.message || '未检测到摄像头');
    }
    const lines = cameras.map((c: any) =>
      `摄像头 ${c.id} · ${probeFacing(c.facing)}${c.resolution ? ` · ${c.resolution}` : ''}`
    );
    probeResult.value = lines.join('\n');
    showMessage(`检测到 ${cameras.length} 个摄像头：\n${lines.join('\n')}`, '相机列表');
  } catch (e: any) {
    const text = `获取失败：${e?.response?.data?.message || e?.message || '请求异常'}`;
    probeResult.value = text;
    showMessage(text, '相机列表');
  } finally {
    probeBusy.value = '';
  }
};

// 0x11：选媒体文件 → 设备端 ls /dev/video* 找实际节点（单个直接用，多个弹窗选）→ 后端 adb push 后发帧
// （不用 0x02 相机列表：USB 相机可能不在 camera2 枚举里，cameraId 与 video 节点号也不一定对应）
const onProbeCamReplace = () => {
  if (!probeReady()) return;
  // 同导入脚本：系统文件对话框选文件，取本地绝对路径给后端 adb push（不能传文件内容）
  const input = document.createElement('input');
  input.type = 'file';
  input.accept = '.jpg,.jpeg,.png,.mp4,.mov,.avi,.webm,.3gp,.mkv,image/jpeg,image/png,video/*';
  input.onchange = async () => {
    const file = input.files?.[0] as (File & { path?: string }) | undefined;
    if (!file) return;
    const filePath = (file.path || '').trim();
    if (!filePath) {
      showMessage('无法获取文件本地路径，请确认在 uTools/桌面端中使用（浏览器环境不支持）', '替换摄像头画面');
      return;
    }
    // 设备端自己找摄像头节点（0x08 shell，root 与部署路径一致）；失败直接终止，不推送文件
    probeBusy.value = 'cam-replace';
    probeResult.value = '正在查找设备摄像头节点（ls /dev/video*）...';
    let nodes: string[] = [];
    try {
      const res = await probePost('/api/probe/message', {
        deviceId: selectedDevice.value,
        cmd: 0x08,
        data: { command: 'ls /dev/video*', root: true, timeout: 30 }
      }, 60000);
      nodes = String(res?.stdout || '').match(/\/dev\/video\d+/g) || [];
      if (nodes.length === 0) {
        throw new Error(`未找到 /dev/video* 节点（exit ${res?.exit ?? '?'}）：${res?.stderr || res?.stdout || '无输出'}`);
      }
    } catch (e: any) {
      const text = `查找摄像头节点失败：${e?.response?.data?.message || e?.message || '请求异常'}`;
      probeResult.value = text;
      showMessage(text, '替换摄像头画面');
      probeBusy.value = '';
      return;
    }
    // 先清 busy，否则弹窗回调里的操作会被 probeReady 守卫吞掉（同 onProbePhoto / runProbeScript）
    probeBusy.value = '';
    // 节点不存在（如节点刚被拔出）时，解析 available 节点让用户手动指定重试
    const doReplace = (params: { cameraId?: string; dev?: string }) => {
      probeBusy.value = 'cam-replace';
      probeResult.value = params.dev
        ? `正在替换摄像头画面（${params.dev}）...`
        : `正在替换摄像头画面（摄像头 ${params.cameraId}）...`;
      probePost('/api/probe/cam-replace', {
        deviceId: selectedDevice.value,
        filePath,
        cameraId: params.cameraId || '',
        dev: params.dev || '',
        mirror: probeCamMirror.value
      }, 400000)
        .then((info: any) => {
          probeResult.value = [
            `替换完成：${info.dev || '?'} · ${info.frames ?? '?'} 帧 · ${info.streamSize ?? 0} B${info.mirror ? ' · 已镜像补偿' : ''}`,
            info.rebootRequired
              ? '⚠ 首次安装：rc 已写入 LD_PRELOAD，需重启设备后再次替换才能生效'
              : 'camera provider 已重启，画面替换立即生效',
            info.status || '',
          ].filter(Boolean).join('\n');
          showMessage(info.rebootRequired ? '已部署，需重启设备生效' : '摄像头画面替换完成', '替换摄像头画面');
        })
        .catch((e: any) => {
          const msg = e?.response?.data?.message || e?.message || '请求异常';
          const m = /camera node missing: .+? available: (.+)$/.exec(msg);
          if (!m) {
            probeResult.value = `替换失败：${msg}`;
            showMessage(`替换失败：${msg}`, '替换摄像头画面');
            return;
          }
          // 先清 busy，否则弹窗回调里的操作会被 probeReady 守卫吞掉（同上）
          probeBusy.value = '';
          probeResult.value = `替换失败：${msg}`;
          nextTick(() => showPrompt(`设备上不存在该节点，输入实际节点重试（可用：${m[1]}）`, (dev: string) => {
            const d = (dev || '').trim();
            if (!/^\/dev\/video\d+$/.test(d)) {
              nextTick(() => showMessage('节点格式必须为 /dev/video<数字>，已取消替换', '替换摄像头画面'));
              return;
            }
            nextTick(() => doReplace({ dev: d }));
          }));
        })
        .finally(() => { probeBusy.value = ''; });
    };
    // 只有一个节点直接替换；多个（USB 相机常带 metadata 副节点）才弹窗选择
    if (nodes.length === 1) {
      nextTick(() => doReplace({ dev: nodes[0] }));
      return;
    }
    const items = nodes.map((n: string) => ({ value: n, label: n, desc: '' }));
    showList(items, (dev: string) => {
      // handleModalConfirm 回调返回后才会关闭弹窗，同步发请求会被守卫吞掉，故 nextTick 错开（同 runProbeScript）
      nextTick(() => doReplace({ dev }));
    }, '选择要替换的摄像头节点（若替换后画面未变，换一个节点重试）', '未检测到摄像头节点', '替换');
  };
  input.click();
};

// 0x12：恢复真实摄像头画面
const onProbeCamRestore = async () => {
  if (!probeReady()) return;
  probeBusy.value = 'cam-restore';
  probeResult.value = '正在恢复真实摄像头画面...';
  try {
    const info = await probePost('/api/probe/cam-restore', { deviceId: selectedDevice.value }, 120000);
    probeResult.value = `已恢复真实画面（${info.conf || 'enabled=0'}）\n${info.output || ''}`.trim();
    showMessage('已恢复真实摄像头画面', '恢复摄像头画面');
  } catch (e: any) {
    const text = `恢复失败：${e?.response?.data?.message || e?.message || '请求异常'}`;
    probeResult.value = text;
    showMessage(text, '恢复摄像头画面');
  } finally {
    probeBusy.value = '';
  }
};
</script>

<template>
              <div v-if="activeTab === 'probe'" class="keys-page">
                <div class="scrcpy-card">
                  <div class="card-header">
                    <span class="card-icon">
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="18" height="18">
                        <path d="M22 12h-4l-3 9L9 3l-3 9H2"/>
                      </svg>
                    </span>
                    <span>探针工具</span>
                  </div>
                  <div class="card-body">
                    <div class="probe-actions">
                      <button
                        class="check-button"
                        @click="sendProbeHeartbeat"
                        :disabled="probeDisabled"
                      >{{ probeBusy === 'heartbeat' ? '检测中...' : '探针心跳' }}</button>
                      <button
                        class="check-button"
                        @click="playProbeTts"
                        :disabled="probeDisabled"
                      >{{ probeBusy === 'tts' ? '朗读中...' : '远程 TTS' }}</button>
                      <button
                        class="check-button"
                        @click="runProbeShell"
                        :disabled="probeDisabled"
                      >{{ probeBusy === 'shell' ? '执行中...' : 'Shell 命令' }}</button>
                      <button
                        class="check-button"
                        @click="doProbeCapture('screenshot')"
                        :disabled="probeDisabled"
                      >{{ probeBusy === 'screenshot' ? '截屏中...' : '屏幕截图' }}</button>
                      <button
                        class="check-button"
                        @click="onProbePhoto"
                        :disabled="probeDisabled"
                      >{{ probeBusy === 'photo' ? '拍照中...' : '摄像头拍照' }}</button>
                      <button
                        class="check-button"
                        @click="openProbeAccessibility"
                        :disabled="probeDisabled"
                      >{{ probeBusy === 'accessibility-settings' ? '打开中...' : '无障碍设置页' }}</button>
                      <button
                        class="check-button"
                        @click="getProbeAccessibilityTree"
                        :disabled="probeDisabled"
                      >{{ probeBusy === 'accessibility-tree' ? '读取中...' : '无障碍组件树' }}</button>
                      <button
                        class="check-button"
                        @click="importProbeScript"
                        :disabled="probeDisabled"
                      >{{ probeBusy === 'script-import' ? '导入中...' : '导入脚本' }}</button>
                      <button
                        class="check-button"
                        @click="listProbeScripts"
                        :disabled="probeDisabled"
                      >{{ probeBusy === 'script-list' ? '读取中...' : '设备脚本列表' }}</button>
                      <button
                        class="check-button"
                        @click="runProbeScript"
                        :disabled="probeDisabled"
                      >{{ probeBusy === 'script-run' ? '执行中...' : '执行设备脚本' }}</button>
                      <button
                        class="check-button"
                        @click="listProbeCameras"
                        :disabled="probeDisabled"
                      >{{ probeBusy === 'cam-list' ? '读取中...' : '相机列表' }}</button>
                      <button
                        class="check-button"
                        @click="onProbeCamReplace"
                        :disabled="probeDisabled"
                      >{{ probeBusy === 'cam-replace' ? '替换中...' : '替换摄像头画面' }}</button>
                      <button
                        class="check-button"
                        @click="onProbeCamRestore"
                        :disabled="probeDisabled"
                      >{{ probeBusy === 'cam-restore' ? '恢复中...' : '恢复真实画面' }}</button>
                    </div>
                    <div class="serial-row">
                      <label class="serial-check">
                        <input type="checkbox" v-model="probeShellRoot" />
                        <span>Shell 以 root 执行（su 0，需设备已 root）</span>
                      </label>
                    </div>
                    <div class="serial-row">
                      <label class="serial-check">
                        <input type="checkbox" v-model="probeCamMirror" />
                        <span>替换摄像头时镜像补偿（晨检 App 预览自带水平镜像）</span>
                      </label>
                    </div>
                    <pre v-if="probeResult" class="probe-shell-out">{{ probeResult }}</pre>
                    <div class="serial-hint">
                      点击按钮即执行：心跳 0x00 / TTS 0x01 / Shell 0x08 / 截屏 0x09 / 拍照 0x0A / 无障碍设置页 0x0B / 无障碍组件树 0x0C / 导入脚本 0x0E / 设备脚本列表 0x0F / 执行设备脚本 0x10 / 相机列表 0x02 / 替换摄像头画面 0x11 / 恢复真实画面 0x12。TTS 与 Shell 会弹出输入框；导入脚本打开文件选择框，选脚本页「导出」生成的 zip（默认在 D:\\log\\script_exports\\），推送到设备后解压到应用私有目录；执行设备脚本先列出已导入脚本供选择，再输入轮数（0=按脚本自身配置）。替换摄像头画面打开文件选择框选照片/视频，设备端自动 ls /dev/video* 找实际摄像头节点（只有一个节点直接替换，多个弹窗选择；替换后画面未变可能是 metadata 副节点，换一个重试），设备端把画面缩放成 640x480 帧流（视频 15fps 最多 600 帧）经 root 部署后替换；首次安装需重启设备再替换一次才生效，恢复真实画面一键还原。组件树在未启用无障碍权限时会自动打开设备设置页，启用「DFM Probe」后重新获取即可。
                    </div>
                  </div>
                </div>
              </div>
</template>
