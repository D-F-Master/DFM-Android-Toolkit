<script lang="ts" setup>
import { ref, computed, onBeforeUnmount } from 'vue';
import axios from 'axios';

const API_BASE = ref('');

const emit = defineEmits(['back']);

// ===================== 状态 =====================
const devices = ref<any[]>([]);              // adb 设备列表
const selected = ref<Set<string>>(new Set()); // 勾选参与远控的设备
const sourceDevice = ref<string>('');         // 主控设备
const remoteActive = ref(false);              // 会话是否运行中
const connecting = ref(false);
const statusMsg = ref('');
const deviceCards = ref<any[]>([]);           // {deviceId, name, width, height, ready}
const wsStatus = ref('未连接');

let ws: WebSocket | null = null;
let configs: Record<number, Uint8Array> = {}; // index -> avcC 配置数据
let decoders: Record<number, VideoDecoder> = {};
let canvases: Record<number, HTMLCanvasElement> = {};
let sessionsStarted = false;

// ===================== 初始化 =====================
const loadConfig = async () => {
  try {
    const response = await fetch('/config.json');
    if (response.ok) {
      const config = await response.json();
      API_BASE.value = config.apiBase || 'http://localhost:3000';
    }
  } catch {
    API_BASE.value = 'http://localhost:3000';
  }
};

const fetchDevices = async () => {
  try {
    const response = await axios.post(`${API_BASE.value}/api/device/list`);
    if (response.data.code === 200) {
      devices.value = response.data.data || [];
      // 默认主控为第一个勾选设备
      if (!sourceDevice.value && selected.value.size > 0) {
        sourceDevice.value = [...selected.value][0];
      }
    }
  } catch (e) {
    statusMsg.value = '获取设备列表失败，请确认后端服务已启动';
  }
};

loadConfig().then(fetchDevices);

// ===================== 设备勾选 =====================
const toggleDevice = (id: string) => {
  if (remoteActive.value) return;
  const next = new Set(selected.value);
  if (next.has(id)) next.delete(id); else next.add(id);
  selected.value = next;
  if (!next.has(sourceDevice.value)) {
    sourceDevice.value = [...next][0] || '';
  }
};

const gridCols = computed(() => {
  const n = deviceCards.value.length;
  if (n <= 1) return 1;
  if (n <= 4) return 2;
  return 3;
});

// ===================== WebSocket 会话 =====================
const connectRemote = () => {
  if (selected.value.size === 0) {
    statusMsg.value = '请先勾选至少一台设备';
    return;
  }
  connecting.value = true;
  statusMsg.value = '正在建立会话...';
  deviceCards.value = [];
  configs = {};
  decoders = {};
  sessionsStarted = true;

  const wsUrl = API_BASE.value.replace(/^http/, 'ws') + '/ws/remote';
  ws = new WebSocket(wsUrl);
  ws.binaryType = 'arraybuffer';
  wsStatus.value = '连接中';

  ws.onopen = () => {
    ws!.send(JSON.stringify({
      type: 'start',
      devices: [...selected.value],
      source: sourceDevice.value,
    }));
  };

  ws.onmessage = (ev) => {
    if (typeof ev.data === 'string') {
      handleJson(JSON.parse(ev.data));
    } else {
      handleBinary(ev.data as ArrayBuffer);
    }
  };

  ws.onclose = () => {
    wsStatus.value = '已断开';
    if (sessionsStarted) {
      remoteActive.value = false;
      connecting.value = false;
    }
  };

  ws.onerror = () => {
    statusMsg.value = 'WebSocket 连接失败，请确认后端已启动';
    connecting.value = false;
  };
};

const handleJson = (msg: any) => {
  if (msg.type === 'meta') {
    deviceCards.value.push({
      deviceId: msg.deviceId, name: msg.name,
      width: msg.width, height: msg.height, ready: true,
    });
    statusMsg.value = '';
  } else if (msg.type === 'ready') {
    remoteActive.value = true;
    connecting.value = false;
    wsStatus.value = '运行中';
    // 清理未就绪的设备
    const okIds = new Set((msg.devices || []).map((d: any) => d.deviceId));
    deviceCards.value = deviceCards.value.filter(d => okIds.has(d.deviceId));
  } else if (msg.type === 'error') {
    statusMsg.value = `${msg.deviceId || ''}: ${msg.message}`;
    connecting.value = false;
  } else if (msg.type === 'closed') {
    const card = deviceCards.value.find(d => d.deviceId === msg.deviceId);
    if (card) card.ready = false;
  }
};

const handleBinary = (buf: ArrayBuffer) => {
  const view = new DataView(buf);
  const index = view.getUint8(0);
  const kind = view.getUint8(1);
  const payload = new Uint8Array(buf, 2);
  const card = deviceCards.value[index];
  if (!card) return;

  if (kind === 1) {
    // configuration（avcC）：缓存起来供 decoder 初始化
    configs[index] = payload;
    const desc = toAvcDescription(payload);
    if (decoders[index]) decoders[index].close();
    decoders[index] = new VideoDecoder({
      output: (frame) => drawFrame(index, frame),
      error: (e) => { statusMsg.value = `解码器错误 ${card.deviceId}: ${e.message}`; },
    });
    decoders[index].configure({ codec: 'avc1.640028', description: desc });
  } else if (kind === 2) {
    const dec = decoders[index];
    if (dec && dec.state === 'configured') {
      const chunk = new EncodedVideoChunk({
        type: isKeyframe(payload) ? 'key' : 'delta',
        timestamp: performance.now() * 1000,
        data: payload,
      });
      try { dec.decode(chunk); } catch { /* 密集帧时忽略解码队列满 */ }
    }
  }
};

const isKeyframe = (data: Uint8Array): boolean => {
  // AVCC 格式 NALU：4B 长度 + NAL 类型在首字节低 5 位
  if (data.length < 5) return false;
  return (data[4] & 0x1F) === 5;
};

const toAvcDescription = (config: Uint8Array): Uint8Array => {
  // scrcpy 的 configuration 包即 avcC 记录本身，直接使用
  return config;
};

const drawFrame = (index: number, frame: VideoFrame) => {
  const card = deviceCards.value[index];
  const cv = canvasRefs.value[index];
  if (!cv) { frame.close(); return; }
  if (cv.width !== frame.displayWidth) cv.width = frame.displayWidth;
  if (cv.height !== frame.displayHeight) cv.height = frame.displayHeight;
  const ctx = cv.getContext('2d')!;
  ctx.drawImage(frame, 0, 0);
  frame.close();
  if (card && !card.ready) card.ready = true;
};

// ===================== 主控输入捕获与同步 =====================
const sendMirror = (event: any) => {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: 'mirror', event }));
  }
};

// 主控画布上 pointer 事件：把画布坐标换算为设备真实坐标
const onPointer = (ev: PointerEvent, index: number) => {
  if (index !== sourceIndex.value) return;  // 仅主控触发同步
  const card = deviceCards.value[index];
  const cv = canvasRefs.value[index];
  if (!card || !cv) return;
  const rect = cv.getBoundingClientRect();
  const x = (ev.clientX - rect.left) * card.width / rect.width;
  const y = (ev.clientY - rect.top) * card.height / rect.height;
  const action = ev.type === 'pointerdown' ? 0 : ev.type === 'pointerup' ? 1 : 2;
  sendMirror({ kind: 'touch', action, x: Math.round(x), y: Math.round(y), w: card.width, h: card.height });
};

const sourceIndex = computed(() =>
  deviceCards.value.findIndex(d => d.deviceId === sourceDevice.value)
);

const setSource = (id: string) => {
  if (remoteActive.value) sourceDevice.value = id;
};

// ===================== 会话关闭 =====================
const disconnectRemote = () => {
  sessionsStarted = false;
  if (ws) {
    try { ws.send(JSON.stringify({ type: 'stop' })); } catch { /* ignore */ }
    ws.close();
    ws = null;
  }
  Object.values(decoders).forEach(d => { try { d.close(); } catch { /* ignore */ } });
  decoders = {};
  configs = {};
  deviceCards.value = [];
  remoteActive.value = false;
  connecting.value = false;
  wsStatus.value = '未连接';
};

onBeforeUnmount(disconnectRemote);

// ===================== 画布 refs =====================
const canvasRefs = ref<any[]>([]);
const setCanvasRef = (el: any, index: number) => {
  if (el) canvasRefs.value[index] = el;
};
</script>

<template>
  <div class="app">
    <button class="back-button" @click="emit('back')">&lt;</button>
    <main>
      <div class="container">
        <header class="remote-header">
          <h2>批量远控</h2>
          <span class="ws-status" :class="{ live: remoteActive }">{{ wsStatus }}</span>
        </header>

        <!-- 设备选择面板 -->
        <div class="device-select card" v-if="!remoteActive && !connecting">
          <div class="select-header">
            <span>选择要远控的设备（可多选）</span>
            <button class="refresh-btn" @click="fetchDevices">刷新</button>
          </div>
          <div class="select-list" v-if="devices.length > 0">
            <label v-for="d in devices" :key="d.id" class="device-item">
              <input
                type="checkbox"
                :checked="selected.has(d.id)"
                @change="toggleDevice(d.id)"
              />
              <span class="device-id">{{ d.id }}</span>
              <span class="device-state">{{ d.state }}</span>
              <button
                v-if="selected.has(d.id)"
                class="source-btn"
                :class="{ active: sourceDevice === d.id }"
                @click.prevent="setSource(d.id)"
              >
                {{ sourceDevice === d.id ? '主控' : '设为主控' }}
              </button>
            </label>
          </div>
          <div v-else class="empty-tip">暂无设备，请连接后刷新</div>

          <div class="select-actions">
            <button class="primary-btn" :disabled="selected.size === 0" @click="connectRemote">
              开始远控（{{ selected.size }} 台）
            </button>
          </div>
          <div v-if="statusMsg" class="status-msg">{{ statusMsg }}</div>
        </div>

        <!-- 远控画面网格 -->
        <div class="remote-grid" v-if="remoteActive || deviceCards.length > 0"
             :style="{ gridTemplateColumns: `repeat(${gridCols}, 1fr)` }">
          <div
            v-for="(card, index) in deviceCards"
            :key="card.deviceId"
            class="remote-tile"
            :class="{ source: index === sourceIndex, dead: !card.ready }"
          >
            <div class="tile-header">
              <span class="tile-name">{{ card.name }}</span>
              <button
                class="tile-source-btn"
                :class="{ active: index === sourceIndex }"
                @click="setSource(card.deviceId)"
              >
                {{ index === sourceIndex ? '主控中' : '设为主控' }}
              </button>
            </div>
            <canvas
              :ref="(el) => setCanvasRef(el, index)"
              class="tile-canvas"
              @pointerdown="onPointer($event, index)"
              @pointermove="onPointer($event, index)"
              @pointerup="onPointer($event, index)"
            ></canvas>
            <div v-if="!card.ready" class="tile-loading">等待画面...</div>
          </div>
        </div>

        <!-- 会话操作 -->
        <div class="session-actions" v-if="remoteActive">
          <button class="stop-btn" @click="disconnectRemote">结束远控</button>
        </div>
      </div>
    </main>
  </div>
</template>

<style scoped src="../styles/batch-remote.css"></style>
