import { computed, ref, type Ref } from 'vue';

// jar 代码修改 tab 的状态与逻辑；从 App_JarDiff.vue 原样抽取，仅把 API_BASE 改为参数。
export function useJarCodeEdit(apiBase: Ref<string>) {
  // ==================== jar 代码修改 ====================
  // 流程：上传 jar → 左侧树选 class → CFR 反编译源码 → 编辑 → javac 编译替换 → 下载新 jar
  // 后端处理 BOOT-INF 前缀与匿名类 $N 的替换；这里只做展示与编辑

  const jarId = ref('');
  const jarName = ref('');
  const jarFat = ref(false);
  const jarEntries = ref<string[]>([]);
  const editUploading = ref(false);
  const editDragging = ref(false);
  const editError = ref('');
  const editMessage = ref('');
  const editInput = ref<HTMLInputElement | null>(null);

  // 树：目录展开状态
  const expandedDirs = ref(new Set<string>());
  const selectedClass = ref('');
  const classSource = ref('');
  const sourceLoading = ref(false);
  const compiling = ref(false);
  const newJarFile = ref('');

  interface TreeRow {
    depth: number;
    name: string;
    path: string;
    isDir: boolean;
  }

  const visibleTreeRows = computed<TreeRow[]>(() => {
    const rows: TreeRow[] = [];
    // 由 entries 构建目录树：com/demo/Calc.class → com → demo → Calc.class
    const root = new Map<string, any>();
    for (const p of jarEntries.value) {
      const parts = p.split('/');
      let level = root;
      for (let i = 0; i < parts.length - 1; i++) {
        if (!level.has(parts[i])) level.set(parts[i], new Map<string, any>());
        level = level.get(parts[i]);
      }
      if (!level.has('__files__')) level.set('__files__', []);
      (level.get('__files__') as string[]).push(parts[parts.length - 1]);
    }
    const walk = (level: Map<string, any>, depth: number, prefix: string) => {
      const dirs = [...level.keys()].filter((k) => k !== '__files__').sort();
      for (const name of dirs) {
        const path = prefix ? `${prefix}/${name}` : name;
        rows.push({ depth, name, path, isDir: true });
        if (expandedDirs.value.has(path)) walk(level.get(name), depth + 1, path);
      }
      for (const f of (level.get('__files__') || []).sort()) {
        const path = prefix ? `${prefix}/${f}` : f;
        rows.push({ depth, name: f, path, isDir: false });
      }
    };
    walk(root, 0, '');
    return rows;
  });

  const toggleTreeDir = (row: TreeRow) => {
    const next = new Set(expandedDirs.value);
    if (next.has(row.path)) next.delete(row.path);
    else next.add(row.path);
    expandedDirs.value = next;
  };

  const validateEditDropped = (files: FileList): File | null => {
    const acceptTypes = ['.jar', '.war'];
    const hit = Array.from(files).find((f) => acceptTypes.some((ext) => f.name.toLowerCase().endsWith(ext)));
    if (!hit) {
      editError.value = `仅支持 ${acceptTypes.join(' / ')} 文件，已忽略：${Array.from(files).map((f) => f.name).join('、')}`;
      return null;
    }
    return hit;
  };

  const onEditDrop = (ev: DragEvent) => {
    editDragging.value = false;
    const file = validateEditDropped(ev.dataTransfer!.files);
    if (file) uploadEditJar(file);
  };

  const uploadEditJar = async (file: File) => {
    editUploading.value = true;
    editError.value = '';
    editMessage.value = '';
    newJarFile.value = '';
    selectedClass.value = '';
    classSource.value = '';
    try {
      const form = new FormData();
      form.append('file', file);
      const response = await fetch(`${apiBase.value}/api/jaredit/upload`, { method: 'POST', body: form });
      const json = await response.json();
      if (json.code === 200) {
        jarId.value = json.data.id;
        jarName.value = json.data.name;
        jarFat.value = json.data.fat;
        jarEntries.value = json.data.entries || [];
        expandedDirs.value = new Set();
        // 默认展开第一层目录
        const first = new Set<string>();
        for (const p of jarEntries.value) first.add(p.split('/')[0]);
        expandedDirs.value = first;
      } else {
        editError.value = json.message || '上传失败';
      }
    } catch (error) {
      editError.value = `无法连接到服务器 ${apiBase.value}`;
    } finally {
      editUploading.value = false;
    }
  };

  const onEditFileChosen = (ev: Event) => {
    const input = ev.target as HTMLInputElement;
    if (input.files && input.files.length > 0) uploadEditJar(input.files[0]);
    input.value = '';
  };

  const loadClassSource = async (path: string) => {
    selectedClass.value = path;
    sourceLoading.value = true;
    editError.value = '';
    editMessage.value = '';
    try {
      const response = await fetch(`${apiBase.value}/api/jaredit/source/${jarId.value}?path=${encodeURIComponent(path)}`);
      const json = await response.json();
      if (json.code === 200) {
        classSource.value = json.data.source;
      } else {
        editError.value = json.message || '反编译失败';
        classSource.value = '';
      }
    } catch (error) {
      editError.value = `无法连接到服务器 ${apiBase.value}`;
    } finally {
      sourceLoading.value = false;
    }
  };

  // 编译并替换：成功后合并 replaced 条目进树（新增的 $N 匿名类），并准备下载
  const compileAndReplace = async () => {
    if (!selectedClass.value || compiling.value) return;
    compiling.value = true;
    editError.value = '';
    editMessage.value = '';
    try {
      const form = new FormData();
      form.append('sid', jarId.value);
      form.append('path', selectedClass.value);
      form.append('source', classSource.value);
      const response = await fetch(`${apiBase.value}/api/jaredit/save`, { method: 'POST', body: form });
      const json = await response.json();
      if (json.code === 200) {
        newJarFile.value = json.data.file;
        editMessage.value = `编译成功，已替换 ${json.data.replaced.length} 个文件：${json.data.replaced.join('、')}`;
        // 新生成的 $N 匿名类不在原条目列表里，合并进树
        const known = new Set(jarEntries.value);
        for (const full of json.data.replaced as string[]) {
          const rel = jarFat.value ? full.replace(/^BOOT-INF\/classes\//, '') : full;
          if (rel.endsWith('.class') && !known.has(rel)) jarEntries.value.push(rel);
        }
        jarEntries.value.sort();
      } else {
        editError.value = json.message || '编译失败';
      }
    } catch (error) {
      editError.value = `无法连接到服务器 ${apiBase.value}`;
    } finally {
      compiling.value = false;
    }
  };

  const downloadNewJar = () => {
    if (!newJarFile.value) return;
    const a = document.createElement('a');
    a.href = `${apiBase.value}/api/jaredit/download/${jarId.value}`;
    a.download = newJarFile.value;
    a.click();
  };

  return {
    jarId, jarName, jarFat, jarEntries, editUploading, editDragging, editError,
    editMessage, editInput, expandedDirs, selectedClass, classSource, sourceLoading,
    compiling, newJarFile, visibleTreeRows, toggleTreeDir, onEditDrop,
    onEditFileChosen, compileAndReplace, downloadNewJar,
  };
}
