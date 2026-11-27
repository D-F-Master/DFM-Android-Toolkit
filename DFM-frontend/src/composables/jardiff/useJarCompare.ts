import { computed, nextTick, onUnmounted, ref, type Ref } from 'vue';

// jar 包对比 tab 的状态与逻辑；从 App_JarDiff.vue 原样抽取，仅把 API_BASE 改为参数。
export function useJarCompare(apiBase: Ref<string>) {
  const fileA = ref<File | null>(null);
  const fileB = ref<File | null>(null);
  const fileInputA = ref<HTMLInputElement | null>(null);
  const fileInputB = ref<HTMLInputElement | null>(null);

  const comparing = ref(false);
  const percent = ref(0);
  const message = ref('');
  const errorText = ref('');

  const summary = ref<{ total: number; same: number; changed: number; added: number; removed: number } | null>(null);
  const entries = ref<Entry[]>([]);
  const jobId = ref('');

  const filterText = ref('');
  const showSame = ref(false);

  interface Entry {
    path: string;
    status: string;
    kind: string;
    left_size: string;
    right_size: string;
    note: string | null;
  }

  interface DiffRow {
    lno: number | null;
    ltxt: string | null;
    rno: number | null;
    rtxt: string | null;
    kind: string;
  }

  const detail = ref<{ path: string; status: string; kind: string; note: string | null; rows: DiffRow[] } | null>(null);
  const detailLoading = ref(false);
  const detailHint = ref('');
  const diffPos = ref(0);
  let pollTimer: number | null = null;

  const STATUS_LABELS: Record<string, string> = {
    same: '相同',
    changed: '修改',
    added: '新增',
    removed: '删除',
  };

  const canCompare = computed(() => !!(fileA.value && fileB.value) && !comparing.value);
  const resultReady = computed(() => !!summary.value && !comparing.value);

  const filteredEntries = computed(() =>
    entries.value.filter((e) =>
      (showSame.value || e.status !== 'same') &&
      (!filterText.value || e.path.toLowerCase().includes(filterText.value.toLowerCase()))
    )
  );

  const changedIndexes = computed(() =>
    detail.value ? detail.value.rows.map((r, i) => (r.kind !== 'same' ? i : -1)).filter((i) => i >= 0) : []
  );

  const loadConfig = async () => {
    try {
      const response = await fetch('/config.json');
      if (response.ok) {
        const config = await response.json();
        if (config.apiBase) {
          apiBase.value = config.apiBase;
        }
      }
    } catch (error) {
      console.error('加载配置失败:', error);
    }
  };

  const loadBackend = async () => {
    try {
      const response = await fetch(`${apiBase.value}/api/jardiff/backend`);
      const json = await response.json();
      if (json.code === 200) {
        backendText.value = json.data.text;
      }
    } catch (error) {
      console.error('查询反编译环境失败:', error);
    }
  };

  const goHome = () => {
    emit('back');
  };

  const clearDetail = () => {
    detail.value = null;
    detailHint.value = '';
    diffPos.value = 0;
  };

  const pickA = () => fileInputA.value?.click();
  const pickB = () => fileInputB.value?.click();

  const acceptTypes = ['.jar', '.war', '.zip'];

  const validateDropped = (file: File | undefined | null): string | null => {
    if (!file) return null;
    const lower = file.name.toLowerCase();
    if (!acceptTypes.some((ext) => lower.endsWith(ext))) {
      return `仅支持 ${acceptTypes.join(' / ')} 文件，已忽略：${file.name}`;
    }
    return null;
  };

  const draggingA = ref(false);
  const draggingB = ref(false);

  const onDropA = (event: DragEvent) => {
    draggingA.value = false;
    const file = event.dataTransfer?.files && event.dataTransfer.files.length > 0 ? event.dataTransfer.files[0] : null;
    const err = validateDropped(file);
    if (err) { errorText.value = err; return; }
    if (file) fileA.value = file;
  };

  const onDropB = (event: DragEvent) => {
    draggingB.value = false;
    const file = event.dataTransfer?.files && event.dataTransfer.files.length > 0 ? event.dataTransfer.files[0] : null;
    const err = validateDropped(file);
    if (err) { errorText.value = err; return; }
    if (file) fileB.value = file;
  };

  const handleFileA = (event: Event) => {
    const target = event.target as HTMLInputElement;
    fileA.value = target.files && target.files.length > 0 ? target.files[0] : null;
  };

  const handleFileB = (event: Event) => {
    const target = event.target as HTMLInputElement;
    fileB.value = target.files && target.files.length > 0 ? target.files[0] : null;
  };

  const poll = async (id: string) => {
    try {
      const response = await fetch(`${apiBase.value}/api/jardiff/status/${id}`);
      const json = await response.json();
      if (json.code === 200) {
        percent.value = json.data.percent;
        message.value = json.data.message;
        if (json.data.done) {
          comparing.value = false;
          if (json.data.error) {
            errorText.value = `对比失败：${json.data.error}`;
          } else {
            summary.value = json.data.summary;
            entries.value = json.data.entries;
          }
          return;
        }
      } else {
        errorText.value = json.message || '查询进度失败';
        comparing.value = false;
        return;
      }
    } catch (error) {
      errorText.value = `无法连接到服务器 ${apiBase.value}，请确保后端服务已启动`;
      comparing.value = false;
      return;
    }
    pollTimer = window.setTimeout(() => poll(id), 600);
  };

  const startCompare = async () => {
    if (!canCompare.value) return;
    errorText.value = '';
    summary.value = null;
    entries.value = [];
    jobId.value = '';
    clearDetail();
    comparing.value = true;
    percent.value = 0;
    message.value = '上传中…';
    try {
      const fd = new FormData();
      fd.append('old', fileA.value!);
      fd.append('new', fileB.value!);
      const response = await fetch(`${apiBase.value}/api/jardiff/compare`, {
        method: 'POST',
        body: fd,
      });
      const json = await response.json();
      if (json.code === 200 && json.data && json.data.id) {
        jobId.value = json.data.id;
        poll(jobId.value);
        return;
      }
      errorText.value = json.message || '提交对比失败';
      comparing.value = false;
    } catch (error) {
      errorText.value = `无法连接到服务器 ${apiBase.value}，请确保后端服务已启动`;
      comparing.value = false;
    }
  };

  const scrollToRow = (index: number) => {
    nextTick(() => {
      const el = document.getElementById(`diff-row-${index}`);
      if (el) {
        el.scrollIntoView({ block: 'center' });
      }
    });
  };

  const goToDiff = (pos: number) => {
    if (changedIndexes.value.length === 0) return;
    diffPos.value = (pos + changedIndexes.value.length) % changedIndexes.value.length;
    scrollToRow(changedIndexes.value[diffPos.value]);
  };

  const prevDiff = () => goToDiff(diffPos.value - 1);
  const nextDiff = () => goToDiff(diffPos.value + 1);

  const selectEntry = async (e: Entry) => {
    if (comparing.value) return;
    clearDetail();
    if (e.status === 'same') {
      detailHint.value = `「${e.path}」在两个版本中内容一致`;
      return;
    }
    detailLoading.value = true;
    try {
      const response = await fetch(`${apiBase.value}/api/jardiff/entry/${jobId.value}?path=${encodeURIComponent(e.path)}`);
      const json = await response.json();
      if (json.code === 200) {
        detail.value = {
          path: json.data.path,
          status: json.data.status,
          kind: json.data.kind,
          note: json.data.note,
          rows: json.data.rows.map((r: any[]) => ({
            lno: r[0], ltxt: r[1], rno: r[2], rtxt: r[3], kind: r[4],
          })),
        };
        diffPos.value = 0;
        if (changedIndexes.value.length > 0) {
          scrollToRow(changedIndexes.value[0]);
        }
      } else {
        detailHint.value = json.message || '读取差异失败';
      }
    } catch (error) {
      detailHint.value = `无法连接到服务器 ${apiBase.value}`;
    } finally {
      detailLoading.value = false;
    }
  };

  const exportMd = async () => {
    if (!resultReady.value) return;
    try {
      const response = await fetch(`${apiBase.value}/api/jardiff/export/${jobId.value}`);
      const json = await response.json();
      if (json.code === 200) {
        const blob = new Blob([json.data.content], { type: 'text/markdown;charset=utf-8' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = json.data.filename;
        a.click();
        URL.revokeObjectURL(url);
      } else {
        errorText.value = json.message || '导出失败';
      }
    } catch (error) {
      errorText.value = `无法连接到服务器 ${apiBase.value}`;
    }
  };

  onUnmounted(() => {
    if (pollTimer !== null) {
      window.clearTimeout(pollTimer);
    }
  });

  return {
    fileA, fileB, fileInputA, fileInputB, comparing, percent, message, errorText,
    summary, entries, jobId, filterText, showSame, detail, detailLoading, detailHint,
    diffPos, STATUS_LABELS, canCompare, resultReady, filteredEntries, changedIndexes,
    pickA, pickB, draggingA, draggingB, onDropA, onDropB, handleFileA, handleFileB,
    startCompare, prevDiff, nextDiff, selectEntry, exportMd,
  };
}
