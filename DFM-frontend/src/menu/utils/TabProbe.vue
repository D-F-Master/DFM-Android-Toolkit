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

// 探针工具：获取无障碍组件树（0x0C），需设备已启用「DFM Probe」无障碍服务
const getProbeAccessibilityTree = async () => {
  if (!probeReady()) return;
  probeBusy.value = 'accessibility-tree';
  probeResult.value = '正在读取组件树...';
  try {
    const info = await probePost('/api/probe/message', { deviceId: selectedDevice.value, cmd: 0x0C, data: {} });
    probeResult.value = formatAccessibilityTree(info);
    showMessage(`组件树读取完成（${info.package || '未知包名'}，${info.nodeCount || 0} 节点）`, '无障碍组件树');
  } catch (e: any) {
    const text = `获取失败：${e?.response?.data?.message || e?.message || '请求异常'}`;
    probeResult.value = text;
    showMessage(text, '无障碍组件树');
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
                    </div>
                    <div class="serial-row">
                      <label class="serial-check">
                        <input type="checkbox" v-model="probeShellRoot" />
                        <span>Shell 以 root 执行（su 0，需设备已 root）</span>
                      </label>
                    </div>
                    <pre v-if="probeResult" class="probe-shell-out">{{ probeResult }}</pre>
                    <div class="serial-hint">
                      点击按钮即执行：心跳 0x00 / TTS 0x01 / Shell 0x08 / 截屏 0x09 / 拍照 0x0A / 无障碍设置页 0x0B / 无障碍组件树 0x0C。TTS 与 Shell 会弹出输入框；拍照先读取摄像头列表供选择，照片回传后设备端立即删除；组件树需先在设备上启用「DFM Probe」无障碍服务（0x0B 跳设置页）。
                    </div>
                  </div>
                </div>
              </div>
</template>
