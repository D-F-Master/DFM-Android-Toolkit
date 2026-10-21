<script lang="ts" setup>
import axios from 'axios'
import { onMounted, onUnmounted, ref, computed, nextTick, watch } from 'vue';
import ModalDialog from '../components/ModalDialog.vue';
import FileEditorDialog from '../components/FileEditorDialog.vue';
import * as echarts from 'echarts/core';
import { LineChart, BarChart, GaugeChart } from 'echarts/charts';
import { GridComponent, TooltipComponent, LegendComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';

// 按需注册 ECharts 模块，避免全量引入导致打包体积膨胀
echarts.use([LineChart, BarChart, GaugeChart, GridComponent, TooltipComponent, LegendComponent, CanvasRenderer]);

const emit = defineEmits(['back']);

const API_BASE = ref('http://localhost:3000');

const loadConfig = async () => {
  try {
    const response = await fetch('/config.json');
    if (response.ok) {
      const config = await response.json();
      if (config.apiBase) {
        API_BASE.value = config.apiBase;
      }
    }
  } catch (error) {
    console.error('加载配置失败:', error);
  }
};

interface Device {
  deviceId: string;
  model?: string;
  status?: string;
}

interface AppInfo {
  packageName: string;
  appName?: string;
  versionName?: string;
  versionCode?: string;
}

interface FileInfo {
  name: string;
  path: string;
  isDirectory: boolean;
  size?: string;
  permissions?: string;
}

interface DeviceInfo {
  deviceId: string;
  status: string;
  model?: string;
  version?: string;
  manufacturer?: string;
  deviceName?: string;
  displayVersion?: string;
  androidVersion?: string;
  kernelVersion?: string;
  buildDate?: string;
  codename?: string;
  unlockStatus?: string;
  sn?: string;
  wifiMac?: string;
  ethMac?: string;
}

const enterAction = ref({})
const devices = ref<Device[]>([])
const selectedDevice = ref('')
const deviceInfo = ref<DeviceInfo | null>(null)
const loading = ref(false)
const apps = ref<AppInfo[]>([])
const appLoading = ref(false)
const files = ref<FileInfo[]>([])
const fileLoading = ref(false)
const currentPath = ref('/sdcard')
const activeTab = ref('info')
const route = ref('')
const wirelessIp = ref('192.168.0.')
const wirelessStatus = ref('')

// 批量操作相关
const batchDeviceInfos = ref<Array<{
  deviceId: string;
  model?: string;
  sn?: string;
  status?: string;
}>>([])
const batchSelectedDevices = ref<Set<string>>(new Set())
const batchLoading = ref(false)
const batchModelFilter = ref('')

const batchFilteredDevices = computed(() => {
  if (!batchModelFilter.value.trim()) {
    return batchDeviceInfos.value
  }
  const keyword = batchModelFilter.value.trim().toLowerCase()
  return batchDeviceInfos.value.filter(d => d.model?.toLowerCase().includes(keyword))
})

const batchSelectAllChecked = computed(() => {
  const filtered = batchFilteredDevices.value
  if (filtered.length === 0) return false
  return filtered.every(d => batchSelectedDevices.value.has(d.deviceId))
})

const loadBatchDeviceInfos = async () => {
  if (devices.value.length === 0) {
    batchDeviceInfos.value = []
    return
  }
  batchLoading.value = true
  try {
    const infos = await Promise.all(
      devices.value.map(async (d) => {
        try {
          const res = await axios.post(`${API_BASE.value}/api/device/sn`, { deviceId: d.deviceId })
          if (res.data.code === 200 && res.data.data) {
            return {
              deviceId: d.deviceId,
              model: d.model,
              sn: res.data.data.sn || '未知',
              status: d.status
            }
          }
        } catch (e) {
          console.error(`获取设备 ${d.deviceId} 信息失败:`, e)
        }
        return { deviceId: d.deviceId, model: d.model, sn: '未知', status: d.status }
      })
    )
    batchDeviceInfos.value = infos
    // 清理已断开设备的选中状态
    const deviceIds = new Set(infos.map(i => i.deviceId))
    const newSelected = new Set<string>()
    batchSelectedDevices.value.forEach(id => {
      if (deviceIds.has(id)) newSelected.add(id)
    })
    batchSelectedDevices.value = newSelected
  } finally {
    batchLoading.value = false
  }
}

const toggleBatchDevice = (deviceId: string) => {
  const next = new Set(batchSelectedDevices.value)
  if (next.has(deviceId)) {
    next.delete(deviceId)
  } else {
    next.add(deviceId)
  }
  batchSelectedDevices.value = next
}

const toggleAllBatchDevices = () => {
  const visibleIds = batchFilteredDevices.value.map(d => d.deviceId)
  if (visibleIds.length > 0 && visibleIds.every(id => batchSelectedDevices.value.has(id))) {
    batchSelectedDevices.value = new Set()
  } else {
    const next = new Set(batchSelectedDevices.value)
    visibleIds.forEach(id => next.add(id))
    batchSelectedDevices.value = next
  }
}

const selectedBatchCount = computed(() => batchSelectedDevices.value.size)

const batchRebootLoading = ref(false)
const batchInstallLoading = ref(false)

// 批量 Shell 相关
const batchShellLoading = ref(false)
const batchShellResults = ref<Array<{
  deviceId: string;
  success: boolean;
  stdout: string;
  stderr: string;
}>>([])
const showBatchShellResults = ref(false)

const batchShellCommand = async () => {
  const deviceIds = Array.from(batchSelectedDevices.value)
  if (deviceIds.length === 0) {
    showMessage('请先选择设备')
    return
  }

  // 使用 prompt 弹窗输入命令
  modalType.value = 'prompt'
  modalTitle.value = '批量 Shell 命令'
  modalMessage.value = '请输入要执行的 shell 命令'
  modalPlaceholder.value = '例如: ls -la /sdcard'
  modalConfirmText.value = '执行'
  modalVariant.value = 'info'
  modalCallback.value = async (command?: string) => {
    if (!command || !command.trim()) {
      showMessage('请输入命令', '错误')
      return
    }

    batchShellLoading.value = true
    showBatchShellResults.value = false
    try {
      const response = await axios.post(`${API_BASE.value}/api/device/shell/batch`, {
        deviceIds,
        command: command.trim()
      })

      if (response.data.code === 200) {
        const data = response.data.data
        batchShellResults.value = data.results || []
        showBatchShellResults.value = true
        showMessage(`批量执行完成：成功 ${data.successCount} 个，失败 ${data.failedCount} 个`, '批量 Shell')
      } else {
        showMessage(response.data.message || '批量执行失败', '错误')
      }
    } catch (error: any) {
      console.error('批量 Shell 执行失败:', error)
      showMessage(`批量执行失败: ${error.message}`, '错误')
    } finally {
      batchShellLoading.value = false
      modalVisible.value = false
    }
  }
  modalVisible.value = true
}

const batchReboot = async () => {
  const deviceIds = Array.from(batchSelectedDevices.value)
  if (deviceIds.length === 0) {
    showMessage('请先选择设备')
    return
  }
  
  batchRebootLoading.value = true
  try {
    const response = await axios.post(`${API_BASE.value}/api/device/reboot/batch`, { deviceIds })
    if (response.data.code === 200) {
      showMessage(response.data.message || '重启命令已发送', '批量重启')
      
      // 延迟刷新设备列表
      setTimeout(() => {
        fetchDevices()
      }, 2000)
    } else {
      showMessage(response.data.message || '批量重启失败', '错误')
    }
  } catch (error: any) {
    console.error('批量重启失败:', error)
    showMessage(`批量重启失败: ${error.message}`, '错误')
  } finally {
    batchRebootLoading.value = false
  }
}

const batchInstallApp = async () => {
  const deviceIds = Array.from(batchSelectedDevices.value)
  if (deviceIds.length === 0) {
    showMessage('请先选择设备')
    return
  }

  const input = document.createElement('input')
  input.type = 'file'
  input.accept = '.apk'

  input.onchange = async (e: Event) => {
    const target = e.target as HTMLInputElement
    const file = target.files?.[0]
    if (!file) return

    batchInstallLoading.value = true
    try {
      const formData = new FormData()
      formData.append('apk', file)
      formData.append('deviceIds', deviceIds.join(','))

      const response = await axios.post(
        `${API_BASE.value}/api/device/app/install/batch`,
        formData,
        { headers: { 'Content-Type': 'multipart/form-data' } }
      )

      if (response.data.code === 200) {
        showMessage(response.data.message || '批量安装命令已发送', '批量安装')
      } else {
        showMessage(response.data.message || '批量安装失败', '错误')
      }
    } catch (error: any) {
      console.error('批量安装失败:', error)
      showMessage(`批量安装失败: ${error.message}`, '错误')
    } finally {
      batchInstallLoading.value = false
    }
  }

  input.click()
}

// 弹窗状态
const modalVisible = ref(false)
const modalType = ref('message') // message, confirm, prompt
const modalTitle = ref('')
const modalMessage = ref('')
const modalPlaceholder = ref('')
const modalConfirmText = ref('确定')
const modalVariant = ref<'success' | 'error' | 'info'>('success')
const modalCallback = ref<Function | null>(null)

const fetchDevices = async () => {
  loading.value = true
  try {
    const response = await axios.post(`${API_BASE.value}/api/devices`)
    if (response.data.code === 200) {
      devices.value = response.data.data || []
      if (devices.value.length > 0 && !selectedDevice.value) {
        selectDevice(devices.value[0].deviceId)
      }
      loadBatchDeviceInfos()
    }
  } catch (error) {
    console.error('获取设备列表失败:', error)
  } finally {
    loading.value = false
  }
}

const fetchDeviceInfo = async (deviceId: string) => {
  try {
    const response = await axios.post(`${API_BASE.value}/api/device/info`, {
      deviceId: deviceId
    })
    if (response.data.code === 200 && response.data.data) {
      // 查找设备的实际状态
      const device = devices.value.find(d => d.deviceId === deviceId)
      const status = device?.status === 'device' ? '已连接' : device?.status === 'offline' ? '离线' : device?.status || '未知'
      
      deviceInfo.value = {
        deviceId: deviceId,
        status: status,
        ...response.data.data
      }
    }
  } catch (error) {
    console.error('获取设备信息失败:', error)
  }
}

const startScrcpy = async () => {
  if (!selectedDevice.value) return
  
  try {
    const response = await axios.post(`${API_BASE.value}/api/device/scrcpy`, {
      deviceId: selectedDevice.value
    })
    if (response.data.code !== 200) {
      showMessage(response.data.message || '启动投屏失败')
    }
  } catch (error) {
    console.error('启动投屏失败:', error)
    showMessage('启动投屏失败，请确保 scrcpy 已安装并添加到系统环境变量')
  }
}

const fetchApps = async (deviceId: string) => {
  appLoading.value = true
  try {
    const response = await axios.post(`${API_BASE.value}/api/device/apps`, {
      deviceId: deviceId
    })
    if (response.data.code === 200) {
      const packageNames = response.data.data || []
      
      // 为每个应用获取详细信息
      const appDetailsPromises = packageNames.map(async (pkg: string) => {
        try {
          const infoResponse = await axios.post(`${API_BASE.value}/api/device/app/info`, {
            deviceId: deviceId,
            packageName: pkg
          })
          if (infoResponse.data.code === 200 && infoResponse.data.data) {
            return infoResponse.data.data
          }
        } catch (error) {
          console.error(`获取应用 ${pkg} 详细信息失败:`, error)
        }
        // 如果获取失败，返回默认信息
        return {
          packageName: pkg,
          appName: pkg,
          versionName: '未知',
          versionCode: ''
        }
      })
      
      apps.value = await Promise.all(appDetailsPromises)
    }
  } catch (error) {
    console.error('获取应用列表失败:', error)
  } finally {
    appLoading.value = false
  }
}

const fetchFiles = async (deviceId: string, path: string = '/sdcard') => {
  fileLoading.value = true
  try {
    const response = await axios.post(`${API_BASE.value}/api/device/files/browse`, {
      deviceId: deviceId,
      path: path
    })
    if (response.data.code === 200) {
      files.value = response.data.data?.files || []
      currentPath.value = response.data.data?.currentPath || path
    }
  } catch (error) {
    console.error('获取文件列表失败:', error)
  } finally {
    fileLoading.value = false
  }
}

const exportFile = async (file: { path: string; name: string; isDirectory: boolean }) => {
  if (!selectedDevice.value) {
    showMessage('请先选择设备')
    return
  }

  // 固定保存到 D:/log 目录
  const localPath = `D:/log/${file.name}`

  console.log('导出文件:', {
    deviceId: selectedDevice.value,
    remotePath: file.path,
    localPath: localPath
  })

  try {
    const response = await axios.post(`${API_BASE.value}/api/device/files/download`, {
      deviceId: selectedDevice.value,
      remotePath: file.path,
      localPath: localPath
    })

    console.log('导出响应:', response.data)

    // 后端返回格式: {code: 200, message: 'success', data: null}
    if (response.data.code === 200) {
      showMessage(`${file.isDirectory ? '文件夹' : '文件'}导出成功\n保存位置: ${localPath}`)
    } else {
      showMessage(`${file.isDirectory ? '文件夹' : '文件'}导出失败: ` + (response.data.message || '未知错误'))
      console.error('导出失败:', response.data)
    }
  } catch (error: any) {
    console.error('导出异常:', error)
    showMessage('导出失败: ' + error.message)
  }
}

const importFile = async () => {
  if (!selectedDevice.value) {
    showMessage('请先选择设备')
    return
  }
  
  const input = document.createElement('input')
  input.type = 'file'
  
  input.onchange = async (e: Event) => {
    const target = e.target as HTMLInputElement
    const file = target.files?.[0]
    if (!file) return
    
    const formData = new FormData()
    formData.append('file', file)
    formData.append('deviceId', selectedDevice.value)
    formData.append('remotePath', currentPath.value + '/')
    
    try {
      const response = await axios.post(
        `${API_BASE.value}/api/device/files/upload`,
        formData,
        { headers: { 'Content-Type': 'multipart/form-data' } }
      )
      if (response.data.code === 200) {
        showMessage('文件导入成功')
        fetchFiles(selectedDevice.value, currentPath.value)
      } else {
        showMessage('文件导入失败: ' + (response.data.message || '未知错误'))
      }
    } catch (error) {
      console.error('导入文件失败:', error)
      showMessage('导入文件失败')
    }
  }
  
  input.click()
}

// ==================== 文本文件在线编辑 ====================
// 链路：download 拉到本地临时文件 → preload 的 services.readFile 读文本 →
//       编辑后构造 File 走 upload 覆盖回设备原路径（全程无需新后端接口）
const EDITABLE_EXTS = ['txt', 'log', 'json', 'xml', 'html', 'htm', 'css', 'js', 'mjs', 'cjs', 'ts', 'md', 'markdown', 'py', 'conf', 'cfg', 'ini', 'sh', 'prop', 'properties', 'csv', 'yml', 'yaml', 'sql', 'java', 'c', 'cpp', 'h', 'gradle', 'env']
const EDIT_MAX_BYTES = 10 * 1024 * 1024 // 10MB，超过不支持在线编辑

const editorVisible = ref(false)
const editorFileName = ref('')
const editorInitialContent = ref('')
const editorLoading = ref(false)
const editorLoadError = ref('')
const editorSaving = ref(false)
// 当前编辑的目标信息（远端路径、本地临时路径）
let editorTarget: { remotePath: string; localPath: string } | null = null

const isEditableTextFile = (file: { name: string; isDirectory: boolean; size?: string }) => {
  if (file.isDirectory) return false
  const ext = (file.name.split('.').pop() || '').toLowerCase()
  if (!EDITABLE_EXTS.includes(ext)) return false
  const sizeNum = Number(file.size)
  if (!isNaN(sizeNum) && sizeNum > EDIT_MAX_BYTES) return false
  return true
}

// 文件不可编辑的具体原因，用于禁用按钮的悬浮提示
const editDisabledReason = (file: { name: string; isDirectory: boolean; size?: string }): string => {
  if (file.isDirectory) return '目录不支持编辑'
  const ext = (file.name.split('.').pop() || '').toLowerCase()
  if (!EDITABLE_EXTS.includes(ext)) return `不支持编辑 .${ext} 类型的文件`
  const sizeNum = Number(file.size)
  if (!isNaN(sizeNum) && sizeNum > EDIT_MAX_BYTES) {
    return `文件过大（${formatBytes(sizeNum)}），超过 ${formatBytes(EDIT_MAX_BYTES, 0)} 不支持在线编辑，可先导出`
  }
  return '不支持编辑'
}

const openFileEditor = async (file: { path: string; name: string; size?: string }) => {
  if (!selectedDevice.value) return
  if (!isEditableTextFile(file)) {
    showMessage('该文件类型或大小不支持在线编辑')
    return
  }

  editorFileName.value = file.name
  editorInitialContent.value = ''
  editorLoadError.value = ''
  editorLoading.value = true
  editorVisible.value = true

  try {
    // 读取链路：优先走后端 /api/device/files/read（不依赖 uTools，浏览器也能用）；
    // 后端未部署该接口时，回落到 download + preload readFile 的旧链路（需 uTools）
    let content: string | null = null
    try {
      const readResp = await axios.post(`${API_BASE.value}/api/device/files/read`, {
        deviceId: selectedDevice.value,
        remotePath: file.path
      })
      if (readResp.data.code === 200 && readResp.data.data?.content !== undefined) {
        content = readResp.data.data.content
      } else if (readResp.data.code !== 404) {
        // 接口存在但读取失败（权限/二进制等），直接报错不再回落
        throw new Error(readResp.data.message || '读取文件失败')
      }
    } catch (readError: any) {
      // 404 = 后端还没有该接口，走回落；其他错误视为真实读取失败
      const isNotFound = readError?.response?.status === 404
        || readResp_dataCodeIs404(readError)
      if (!isNotFound) throw readError
    }

    if (content === null) {
      // ---- 回落链路：download 到本地 + preload 读文件（需 uTools）----
      if (!(window as any).services?.readFile) {
        throw new Error('后端不支持在线读取，且未检测到 uTools 环境（preload 未加载），无法编辑')
      }
      const ext = file.name.includes('.') ? file.name.split('.').pop() : 'txt'
      const localPath = `D:/log/.tmp_edit_${Date.now()}.${ext}`
      const dlResp = await axios.post(`${API_BASE.value}/api/device/files/download`, {
        deviceId: selectedDevice.value,
        remotePath: file.path,
        localPath
      })
      if (dlResp.data.code !== 200) {
        throw new Error(dlResp.data.message || '下载文件失败')
      }
      const text = (window as any).services.readFile(localPath)
      content = typeof text === 'string' ? text : String(text)
    }

    editorInitialContent.value = content
    editorTarget = { remotePath: file.path }
  } catch (error: any) {
    console.error('打开文件编辑失败:', error)
    editorLoadError.value = `无法读取文件: ${error.message}（可能是权限不足或二进制文件）`
  } finally {
    editorLoading.value = false
  }
}

// 判断后端返回体里的 code 是否为 404（表示接口未实现）
const readResp_dataCodeIs404 = (error: any): boolean => {
  return error?.response?.data?.code === 404
}

const handleEditorSave = async (newContent: string) => {
  if (!selectedDevice.value || !editorTarget) return
  editorSaving.value = true
  try {
    // 3. 用字符串构造 File 走现有上传接口，覆盖回原路径
    const blob = new Blob([newContent], { type: 'text/plain' })
    const formData = new FormData()
    formData.append('file', blob, editorFileName.value)
    formData.append('deviceId', selectedDevice.value)
    const dir = editorTarget.remotePath.substring(0, editorTarget.remotePath.lastIndexOf('/') + 1) || '/'
    formData.append('remotePath', dir)

    const response = await axios.post(
      `${API_BASE.value}/api/device/files/upload`,
      formData,
      { headers: { 'Content-Type': 'multipart/form-data' } }
    )
    if (response.data.code === 200) {
      showMessage('保存成功，已覆盖设备上的文件')
      editorVisible.value = false
      editorTarget = null
      fetchFiles(selectedDevice.value, currentPath.value)
    } else {
      showMessage('保存失败: ' + (response.data.message || '未知错误'))
    }
  } catch (error: any) {
    console.error('保存文件失败:', error)
    showMessage('保存失败: ' + error.message)
  } finally {
    editorSaving.value = false
  }
}

const handleEditorClose = () => {
  editorVisible.value = false
  editorTarget = null
}

const selectDevice = (deviceId: string) => {
  selectedDevice.value = deviceId
  fetchDeviceInfo(deviceId)
  fetchApps(deviceId)
  fetchFiles(deviceId, '/sdcard')
}

const installApp = async (event: Event) => {
  if (!selectedDevice.value) return
  
  const input = document.createElement('input')
  input.type = 'file'
  input.accept = '.apk'
  
  input.onchange = async (e: Event) => {
    const target = e.target as HTMLInputElement
    const file = target.files?.[0]
    if (!file) return
    
    const formData = new FormData()
    formData.append('apk', file)
    formData.append('deviceId', selectedDevice.value)
    
    try {
      const response = await axios.post(
        `${API_BASE.value}/api/device/app/install`,
        formData,
        { headers: { 'Content-Type': 'multipart/form-data' } }
      )
      if (response.data.code === 200) {
        showMessage('应用安装成功')
        fetchApps(selectedDevice.value)
      } else {
        showMessage('应用安装失败: ' + (response.data.message || '未知错误'))
      }
    } catch (error) {
      console.error('安装应用失败:', error)
      showMessage('安装应用失败')
    }
  }
  
  input.click()
}

const uninstallApp = async (packageName: string) => {
  if (!selectedDevice.value) return
  
  try {
    const response = await axios.post(`${API_BASE.value}/api/device/app/uninstall`, {
      deviceId: selectedDevice.value,
      packageName: packageName
    })
    if (response.data.code === 200) {
      fetchApps(selectedDevice.value)
    }
  } catch (error) {
    console.error('卸载应用失败:', error)
  }
}

const startApp = async (packageName: string) => {
  if (!selectedDevice.value) return
  
  try {
    await axios.post(`${API_BASE.value}/api/device/app/start`, {
      deviceId: selectedDevice.value,
      packageName: packageName
    })
  } catch (error) {
    console.error('启动应用失败:', error)
  }
}

const stopApp = async (packageName: string) => {
  if (!selectedDevice.value) return
  
  try {
    await axios.post(`${API_BASE.value}/api/device/app/stop`, {
      deviceId: selectedDevice.value,
      packageName: packageName
    })
  } catch (error) {
    console.error('停止应用失败:', error)
  }
}

const extractApk = async (packageName: string, appName?: string) => {
  if (!selectedDevice.value) return
  
  const input = document.createElement('input') as HTMLInputElement & { webkitdirectory: boolean; directory: boolean }
  input.type = 'file'
  input.webkitdirectory = true
  input.directory = true
  
  input.onchange = async (e: Event) => {
    const target = e.target as HTMLInputElement
    
    if (!target.files || target.files.length === 0) {
        showMessage('请选择一个目录')
        return
      }
      
      const selectedFile = target.files![0] as File & { path: string }
      const filePath = selectedFile.path
      const localDir = filePath.substring(0, filePath.lastIndexOf('\\') + 1)
      
      if (!localDir) {
        showMessage('无法获取目录路径')
        return
      }
      
      const safeAppName = appName?.replace(/[^a-zA-Z0-9]/g, '_') || packageName.replace(/[^a-zA-Z0-9]/g, '_')
      const localPath = `${localDir}${safeAppName}.apk`
      
      try {
        const normalizedLocalPath = localPath.replace(/\\/g, '/')
        
        const response = await axios.post(`${API_BASE.value}/api/device/app/extract`, {
          deviceId: selectedDevice.value,
          packageName: packageName,
          localPath: normalizedLocalPath
        })
        
        if (response.data.code === 200) {
          showMessage('APK提取成功')
        } else {
          showMessage('APK提取失败: ' + (response.data.message || '未知错误'))
        }
      } catch (error: any) {
        console.error('提取APK失败:', error)
        showMessage('提取APK失败: ' + error.message)
      }
  }
  
  input.click()
}

const navigateTo = (path: string) => {
  if (!selectedDevice.value) return
  fetchFiles(selectedDevice.value, path)
}

const goBack = () => {
  if (!selectedDevice.value) return
  
  if (currentPath.value === '/' || currentPath.value === '/sdcard') return
  
  const lastSlashIndex = currentPath.value.lastIndexOf('/')
  const parentPath = lastSlashIndex <= 0 ? '/' : currentPath.value.substring(0, lastSlashIndex)
  fetchFiles(selectedDevice.value, parentPath)
}

const createFolder = async () => {
  if (!selectedDevice.value) return
  
  showPrompt('请输入文件夹名称:', async (folderName: string) => {
    if (!folderName) return
    
    const folderPath = `${currentPath.value}/${folderName}`
    
    try {
      const response = await axios.post(`${API_BASE.value}/api/device/files/mkdir`, {
        deviceId: selectedDevice.value,
        path: folderPath
      })
      if (response.data.code === 200) {
        showMessage('文件夹创建成功')
        fetchFiles(selectedDevice.value, currentPath.value)
      } else {
        showMessage('文件夹创建失败: ' + (response.data.message || '未知错误'))
      }
    } catch (error: any) {
      console.error('创建文件夹失败:', error)
      showMessage('文件夹创建失败: ' + error.message)
    }
  }, '', '创建文件夹')
}

const createFile = async () => {
  if (!selectedDevice.value) return
  
  showPrompt('请输入文件名称:', async (fileName: string) => {
    if (!fileName) return
    
    const filePath = `${currentPath.value}/${fileName}`
    
    try {
      const response = await axios.post(`${API_BASE.value}/api/device/files/create`, {
        deviceId: selectedDevice.value,
        path: filePath
      })
      if (response.data.code === 200) {
        showMessage('文件创建成功')
        fetchFiles(selectedDevice.value, currentPath.value)
      } else {
        showMessage('文件创建失败: ' + (response.data.message || '未知错误'))
      }
    } catch (error: any) {
      console.error('创建文件失败:', error)
      showMessage('创建文件失败: ' + error.message)
    }
  }, '', '创建文件')
}

const deleteFile = async (path: string, isDirectory: boolean) => {
  if (!selectedDevice.value) return
  
  showConfirm(`确定要删除${isDirectory ? '文件夹' : '文件'}吗？`, async () => {
    try {
      const response = await axios.post(`${API_BASE.value}/api/device/files/delete`, {
        deviceId: selectedDevice.value,
        path: path
      })
      if (response.data.code === 200) {
        fetchFiles(selectedDevice.value, currentPath.value)
      }
    } catch (error) {
      console.error('删除失败:', error)
    }
  }, '确认删除')
}

const simulateKey = async (key: string) => {
  if (!selectedDevice.value) return
  
  try {
    await axios.post(`${API_BASE.value}/api/device/key`, {
      deviceId: selectedDevice.value,
      key: key
    })
  } catch (error) {
    console.error('按键模拟失败:', error)
  }
}

const volumeUp = async () => {
  if (!selectedDevice.value) return
  
  try {
    await axios.post(`${API_BASE.value}/api/device/volume`, {
      deviceId: selectedDevice.value,
      action: 'up',
      stream: 'media'
    })
  } catch (error) {
    console.error('音量增加失败:', error)
  }
}

const volumeDown = async () => {
  if (!selectedDevice.value) return
  
  try {
    await axios.post(`${API_BASE.value}/api/device/volume`, {
      deviceId: selectedDevice.value,
      action: 'down',
      stream: 'media'
    })
  } catch (error) {
    console.error('音量减小失败:', error)
  }
}

const muteVolume = async () => {
  if (!selectedDevice.value) return
  
  try {
    await axios.post(`${API_BASE.value}/api/device/volume`, {
      deviceId: selectedDevice.value,
      action: 'mute',
      stream: 'media'
    })
  } catch (error) {
    console.error('静音失败:', error)
  }
}

const restartDevice = async () => {
  if (!selectedDevice.value) return
  
  showConfirm('确定要重启设备吗？', async () => {
    try {
      await axios.post(`${API_BASE.value}/api/command`, {
        command: `adb -s ${selectedDevice.value} reboot`
      })
      showMessage('设备正在重启...')
    } catch (error) {
      console.error('重启设备失败:', error)
      showMessage('重启设备失败')
    }
  }, '确认重启')
}

const showDeviceLogs = async () => {
  if (!selectedDevice.value) return
  
  try {
    const response = await axios.post(`${API_BASE.value}/api/device/logs`, {
      deviceId: selectedDevice.value
    })
    if (response.data.code !== 200) {
      showMessage('启动日志窗口失败: ' + (response.data.message || '未知错误'))
    }
  } catch (error: any) {
    console.error('启动日志窗口失败:', error)
    showMessage('启动日志窗口失败: ' + error.message)
  }
}

const openDeviceShell = async () => {
  if (!selectedDevice.value) return
  
  try {
    const response = await axios.post(`${API_BASE.value}/api/device/shell`, {
      deviceId: selectedDevice.value
    })
    if (response.data.code !== 200) {
      showMessage('启动 shell 窗口失败: ' + (response.data.message || '未知错误'))
    }
  } catch (error: any) {
    console.error('启动 shell 窗口失败:', error)
    showMessage('启动 shell 窗口失败: ' + error.message)
  }
}

const rebootToLoader = async () => {
  if (!selectedDevice.value) return
  
  showConfirm('确定要重启到 fastboot 模式吗？', async () => {
    try {
      await axios.post(`${API_BASE.value}/api/command`, {
        command: `adb -s ${selectedDevice.value} reboot loader`
      })
      showMessage('设备正在重启到 fastboot 模式...')
    } catch (error) {
      console.error('重启到 fastboot 模式失败:', error)
      showMessage('重启到 fastboot 模式失败')
    }
  }, '确认重启')
}

const connectWireless = async () => {
  if (!wirelessIp.value) {
    wirelessStatus.value = '请输入设备IP地址'
    return
  }
  
  try {
    wirelessStatus.value = '正在连接...'
    await axios.post(`${API_BASE.value}/api/command`, {
      command: 'adb start-server'
    })
    
    const target = wirelessIp.value.includes(':') ? wirelessIp.value.trim() : `${wirelessIp.value.trim()}:5555`
    const response = await axios.post(`${API_BASE.value}/api/command`, {
      command: `adb connect ${target}`,
    })
    
    if (response.data.code === 200) {
      wirelessStatus.value = '连接成功'
      fetchDevices()
    } else {
      wirelessStatus.value = '连接失败: ' + (response.data.message || '未知错误')
    }
  } catch (error) {
    console.error('无线连接失败:', error)
    wirelessStatus.value = '连接失败: ' + (error instanceof Error ? error.message : String(error))
  }
}

const goHome = () => {
  emit('back');
};

// 弹窗方法
const showMessage = (message: string, title: string = '提示') => {
  modalType.value = 'message'
  modalTitle.value = title
  modalMessage.value = message
  modalVisible.value = true
  modalCallback.value = null
};

const showConfirm = (message: string, callback: Function, title: string = '确认') => {
  modalType.value = 'confirm'
  modalTitle.value = title
  modalMessage.value = message
  modalVisible.value = true
  modalCallback.value = callback
};

const showPrompt = (message: string, callback: Function, placeholder: string = '', title: string = '输入') => {
  modalType.value = 'prompt'
  modalTitle.value = title
  modalMessage.value = message
  modalPlaceholder.value = placeholder
  modalVisible.value = true
  modalCallback.value = callback
};

const showNotification = (message: string, variant: 'success' | 'error' | 'info' = 'success') => {
  modalType.value = 'notification'
  modalVariant.value = variant
  modalMessage.value = message
  modalVisible.value = true
};

const handleModalConfirm = (value?: string) => {
  if (modalCallback.value) {
    modalCallback.value(value)
  }
  modalVisible.value = false
};

const handleModalCancel = () => {
  modalVisible.value = false
};

// 复制文本到剪贴板
const copyToClipboard = async (text: string, label: string) => {
  if (!text || text === '未知') {
    showNotification(`${label}为空，无法复制`, 'error')
    return
  }
  try {
    // 优先使用 navigator.clipboard API
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text)
    } else {
      // 降级方案：使用 textarea + execCommand
      const textarea = document.createElement('textarea')
      textarea.value = text
      textarea.style.position = 'fixed'
      textarea.style.top = '-1000px'
      textarea.style.left = '-1000px'
      textarea.style.opacity = '0'
      document.body.appendChild(textarea)
      textarea.select()
      document.execCommand('copy')
      document.body.removeChild(textarea)
    }
    showNotification(`${label}已复制：${text}`, 'success')
  } catch (error) {
    console.error('复制失败:', error)
    showNotification(`复制${label}失败`, 'error')
  }
};

const confirmDisconnect = (deviceId: string) => {
  showConfirm('确定要断开设备连接吗？', async () => {
    try {
      const response = await axios.post(`${API_BASE.value}/api/device/disconnect`, {
        deviceId: deviceId
      })
      if (response.data.code === 200) {
        showNotification('设备已断开')
        fetchDevices()
      } else {
        showMessage('断开失败: ' + (response.data.message || '未知错误'))
      }
    } catch (error) {
      console.error('断开设备失败:', error)
      showMessage('断开设备失败')
    }
  })
};

// 断开全部网络设备连接（仅断开 IP:端口 形式的网络设备，USB 设备不受影响）
const disconnectAllLoading = ref(false)
const confirmDisconnectAll = () => {
  showConfirm('确定要断开全部网络设备的 ADB 连接吗？', async () => {
    disconnectAllLoading.value = true
    try {
      const response = await axios.post(`${API_BASE.value}/api/device/disconnect/all`, {})
      if (response.data.code === 200) {
        const data = response.data.data
        if (!data || data.total === 0) {
          showNotification('没有网络连接的设备', 'info')
        } else if (data.failedCount > 0) {
          showNotification(`断开完成：成功 ${data.successCount} 个，失败 ${data.failedCount} 个`, 'error')
        } else {
          showNotification(`已断开全部网络设备（共 ${data.successCount} 个）`)
        }
        fetchDevices()
      } else {
        showMessage('断开失败: ' + (response.data.message || '未知错误'))
      }
    } catch (error) {
      console.error('断开全部设备失败:', error)
      showMessage('断开全部设备失败')
    } finally {
      disconnectAllLoading.value = false
    }
  }, '确认断开')
}

// ==================== 性能监控 ====================
// 接口: POST /api/device/monitor（静态指标）、POST /api/device/monitor/rate（采样速率）
// 原则：未进入该 Tab 不发任何请求；进入后静态指标低频刷新，速率指标按 2s 轮询且防止请求堆积

interface CpuCoreFreq {
  core: number;
  currentKhz: number | null;
}

interface MonitorSnapshot {
  cpu: {
    coreCount: number;
    maxFreqKhz: number | null;
    cores: CpuCoreFreq[];
  } | null;
  memory: {
    totalBytes: number;
    usedBytes: number;
    availableBytes: number;
    freeBytes?: number;
    cachedBytes?: number;
    usagePercent: number;
  } | null;
  disk: {
    mountPoint: string;
    totalBytes: number;
    usedBytes: number;
    availableBytes: number;
    usagePercent: number;
  } | null;
}

interface MonitorRate {
  duration: number;
  cpu: { usagePercent: number } | null;
  network: Array<{
    name: string;
    rxBytes: number;
    txBytes: number;
    rxBytesPerSec: number;
    txBytesPerSec: number;
  }>;
}

const RATE_POLL_INTERVAL = 2000;   // 速率轮询间隔，接口文档建议不小于 2s
const SNAPSHOT_INTERVAL = 8000;    // 静态指标刷新间隔，变化慢
const RATE_HISTORY_MAX = 30;       // 折线图保留的采样点数

const monitorSnapshot = ref<MonitorSnapshot | null>(null)
const monitorRate = ref<MonitorRate | null>(null)
const monitorLoading = ref(false)      // 首次加载
const monitorError = ref('')
const monitorActive = ref(false)       // 是否停留在性能监控 Tab（懒加载开关）
const rateHistory = ref<Array<{ time: string; cpu: number; rx: number; tx: number }>>([])

let snapshotTimer: ReturnType<typeof setInterval> | null = null
let rateTimer: ReturnType<typeof setTimeout> | null = null
let rateRequesting = false             // 防止上一次请求未返回就发起下一次

const snapshotChartEl = ref<HTMLDivElement | null>(null)
const rateChartEl = ref<HTMLDivElement | null>(null)
const coreChartEl = ref<HTMLDivElement | null>(null)
let snapshotChart: echarts.ECharts | null = null
let rateChart: echarts.ECharts | null = null
let coreChart: echarts.ECharts | null = null

const isDarkMode = () => window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches

// 字节按 1024 进制换算
const formatBytes = (bytes?: number | null, digits = 2): string => {
  if (bytes === null || bytes === undefined || isNaN(bytes)) return '—'
  if (bytes < 1024) return `${bytes} B`
  const units = ['KB', 'MB', 'GB', 'TB']
  let value = bytes / 1024
  let i = 0
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024
    i++
  }
  return `${value.toFixed(digits)} ${units[i]}`
}

// 速率按 1024 进制换算，带 /s
const formatRate = (bytesPerSec?: number | null): string => {
  if (bytesPerSec === null || bytesPerSec === undefined || isNaN(bytesPerSec)) return '—'
  return `${formatBytes(bytesPerSec, 1)}/s`
}

// kHz 转 MHz
const formatFreq = (khz?: number | null): string => {
  if (khz === null || khz === undefined || isNaN(khz)) return '—'
  return `${(khz / 1000).toFixed(0)} MHz`
}

const formatPercent = (value?: number | null): string => {
  if (value === null || value === undefined || isNaN(value)) return '—'
  return `${value.toFixed(1)}%`
}

// 内存/磁盘使用率颜色：低绿、中橙、高红
const usageColor = (percent?: number | null): string => {
  if (percent === null || percent === undefined || isNaN(percent)) return '#bdbdbd'
  if (percent >= 90) return '#f44336'
  if (percent >= 70) return '#ff9800'
  return '#4caf50'
}

const chartTextColor = () => (isDarkMode() ? '#e0e0e0' : '#333333')
const chartAxisColor = () => (isDarkMode() ? '#555555' : '#e0e0e0')
const chartSplitColor = () => (isDarkMode() ? '#40434a' : '#f0f0f0')

// 环形仪表盘：内存 / 磁盘使用率
const buildGaugeOption = (percent: number, color: string, label: string) => ({
  series: [{
    type: 'gauge',
    startAngle: 90,
    endAngle: -270,
    radius: '92%',
    center: ['50%', '50%'],
    pointer: { show: false },
    progress: {
      show: true,
      overlap: false,
      roundCap: true,
      clip: false,
      itemStyle: { color }
    },
    axisLine: {
      lineStyle: { width: 10, color: [[1, isDarkMode() ? '#3a3d45' : '#eef1f4']] }
    },
    splitLine: { show: false },
    axisTick: { show: false },
    axisLabel: { show: false },
    data: [{ value: Number(percent.toFixed(1)), name: label }],
    detail: {
      valueAnimation: true,
      offsetCenter: [0, 0],
      fontSize: 18,
      fontWeight: 'bold',
      color: chartTextColor(),
      formatter: (v: number) => `${v.toFixed(1)}%`
    },
    title: {
      offsetCenter: [0, '32%'],
      fontSize: 11,
      color: isDarkMode() ? '#999999' : '#8a94a6'
    }
  }]
})

// 每核当前频率柱状图
const buildCoreOption = (cores: CpuCoreFreq[], maxFreqKhz: number | null) => ({
  grid: { left: 44, right: 12, top: 16, bottom: 24 },
  tooltip: {
    trigger: 'axis',
    axisPointer: { type: 'shadow' },
    formatter: (params: any) => {
      const p = Array.isArray(params) ? params[0] : params
      return `核心 ${p.name}<br/>当前频率: ${formatFreq(p.value)}`
    }
  },
  xAxis: {
    type: 'category',
    data: cores.map(c => `核${c.core}`),
    axisLine: { lineStyle: { color: chartAxisColor() } },
    axisTick: { show: false },
    axisLabel: { color: isDarkMode() ? '#999999' : '#888888', fontSize: 11 }
  },
  yAxis: {
    type: 'value',
    name: 'MHz',
    nameTextStyle: { color: isDarkMode() ? '#999999' : '#888888', fontSize: 10 },
    max: maxFreqKhz ? Math.ceil(maxFreqKhz / 1000) : undefined,
    axisLine: { show: false },
    axisTick: { show: false },
    splitLine: { lineStyle: { color: chartSplitColor() } },
    axisLabel: { color: isDarkMode() ? '#999999' : '#888888', fontSize: 10 }
  },
  series: [{
    type: 'bar',
    data: cores.map(c => (c.currentKhz === null || c.currentKhz === undefined ? 0 : Number((c.currentKhz / 1000).toFixed(0)))),
    barMaxWidth: 36,
    itemStyle: { color: '#4caf50', borderRadius: [4, 4, 0, 0] },
    label: {
      show: true,
      position: 'top',
      fontSize: 10,
      color: isDarkMode() ? '#999999' : '#888888',
      formatter: (p: any) => (p.value ? `${p.value}` : '—')
    }
  }]
})

// CPU 占用率 + 上下行速率折线图
const buildRateOption = (history: Array<{ time: string; cpu: number; rx: number; tx: number }>) => {
  const textColor = isDarkMode() ? '#999999' : '#888888'
  const toKb = (v: number) => Number((v / 1024).toFixed(1))
  return {
    grid: { left: 52, right: 52, top: 34, bottom: 24 },
    tooltip: {
      trigger: 'axis',
      formatter: (params: any) => {
        const list = Array.isArray(params) ? params : [params]
        const head = list[0]?.axisValue ?? ''
        const lines = list.map((p: any) => {
          if (p.seriesName === 'CPU') return `CPU: ${p.value}%`
          return `${p.seriesName}: ${p.value} KB/s`
        })
        return [head, ...lines].join('<br/>')
      }
    },
    legend: {
      data: ['CPU', '下载', '上传'],
      top: 0,
      itemWidth: 12,
      itemHeight: 8,
      textStyle: { color: textColor, fontSize: 11 }
    },
    xAxis: {
      type: 'category',
      boundaryGap: false,
      data: history.map(h => h.time),
      axisLine: { lineStyle: { color: chartAxisColor() } },
      axisTick: { show: false },
      axisLabel: { color: textColor, fontSize: 10 }
    },
    yAxis: [
      {
        type: 'value',
        name: 'CPU %',
        min: 0,
        max: 100,
        nameTextStyle: { color: textColor, fontSize: 10 },
        axisLine: { show: false },
        axisTick: { show: false },
        splitLine: { lineStyle: { color: chartSplitColor() } },
        axisLabel: { color: textColor, fontSize: 10 }
      },
      {
        type: 'value',
        name: 'KB/s',
        min: 0,
        nameTextStyle: { color: textColor, fontSize: 10 },
        axisLine: { show: false },
        axisTick: { show: false },
        splitLine: { show: false },
        axisLabel: { color: textColor, fontSize: 10 }
      }
    ],
    series: [
      {
        name: 'CPU',
        type: 'line',
        smooth: true,
        showSymbol: false,
        yAxisIndex: 0,
        data: history.map(h => h.cpu),
        lineStyle: { width: 2, color: '#4caf50' },
        itemStyle: { color: '#4caf50' }
      },
      {
        name: '下载',
        type: 'line',
        smooth: true,
        showSymbol: false,
        yAxisIndex: 1,
        data: history.map(h => toKb(h.rx)),
        lineStyle: { width: 2, color: '#2196f3' },
        itemStyle: { color: '#2196f3' }
      },
      {
        name: '上传',
        type: 'line',
        smooth: true,
        showSymbol: false,
        yAxisIndex: 1,
        data: history.map(h => toKb(h.tx)),
        lineStyle: { width: 2, color: '#ff9800' },
        itemStyle: { color: '#ff9800' }
      }
    ]
  }
}

const renderSnapshotCharts = () => {
  const snap = monitorSnapshot.value
  if (!snap) return
  if (snapshotChartEl.value && !snapshotChart) {
    snapshotChart = echarts.init(snapshotChartEl.value)
  }
  if (snapshotChart) {
    const memPercent = snap.memory?.usagePercent ?? 0
    const diskPercent = snap.disk?.usagePercent ?? 0
    snapshotChart.setOption({
      series: [
        { ...buildGaugeOption(memPercent, usageColor(memPercent), '内存').series[0], center: ['25%', '50%'] },
        { ...buildGaugeOption(diskPercent, usageColor(diskPercent), '磁盘').series[0], center: ['75%', '50%'] }
      ]
    }, true)
  }
  if (coreChartEl.value && !coreChart) {
    coreChart = echarts.init(coreChartEl.value)
  }
  if (coreChart) {
    coreChart.setOption(buildCoreOption(snap.cpu?.cores ?? [], snap.cpu?.maxFreqKhz ?? null), true)
  }
}

const renderRateChart = () => {
  if (rateChartEl.value && !rateChart) {
    rateChart = echarts.init(rateChartEl.value)
  }
  if (rateChart) {
    rateChart.setOption(buildRateOption(rateHistory.value), true)
  }
}

const resizeMonitorCharts = () => {
  snapshotChart?.resize()
  rateChart?.resize()
  coreChart?.resize()
}

// 多网卡时取流量最大的那个作为展示口径，摘要与折线图保持一致
const activeNic = computed(() => {
  const list = monitorRate.value?.network || []
  return list.reduce<MonitorRate['network'][number] | null>((acc, cur) => {
    if (!acc) return cur
    return (cur.rxBytesPerSec + cur.txBytesPerSec) > (acc.rxBytesPerSec + acc.txBytesPerSec) ? cur : acc
  }, null)
})

// 静态指标：内存 / CPU 频率 / 磁盘
const fetchMonitorSnapshot = async () => {
  if (!selectedDevice.value) return
  try {
    const response = await axios.post(`${API_BASE.value}/api/device/monitor`, {
      deviceId: selectedDevice.value
    })
    if (response.data.code === 200 && response.data.data) {
      monitorSnapshot.value = response.data.data
      monitorError.value = ''
      await nextTick()
      renderSnapshotCharts()
    } else {
      monitorError.value = response.data.message || '获取性能快照失败'
    }
  } catch (error: any) {
    console.error('获取性能快照失败:', error)
    monitorError.value = `获取性能快照失败: ${error.message}`
  }
}

// 速率指标：CPU 占用率 + 网络上下行。用标志位防止请求堆积
const fetchMonitorRate = async () => {
  if (!selectedDevice.value || rateRequesting) return
  rateRequesting = true
  try {
    const response = await axios.post(`${API_BASE.value}/api/device/monitor/rate`, {
      deviceId: selectedDevice.value,
      duration: 1
    })
    if (response.data.code === 200 && response.data.data) {
      const data: MonitorRate = response.data.data
      monitorRate.value = data
      monitorError.value = ''
      const net = activeNic.value
      const now = new Date()
      const time = `${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}:${String(now.getSeconds()).padStart(2, '0')}`
      const next = rateHistory.value.concat([{
        time,
        cpu: data.cpu?.usagePercent ?? 0,
        rx: net?.rxBytesPerSec ?? 0,
        tx: net?.txBytesPerSec ?? 0
      }])
      rateHistory.value = next.length > RATE_HISTORY_MAX ? next.slice(next.length - RATE_HISTORY_MAX) : next
      await nextTick()
      renderRateChart()
    } else {
      monitorError.value = response.data.message || '获取实时速率失败'
    }
  } catch (error: any) {
    console.error('获取实时速率失败:', error)
    monitorError.value = `获取实时速率失败: ${error.message}`
  } finally {
    rateRequesting = false
  }
}

// 速率轮询：上一次请求返回后再排下一次，天然防止堆积
const scheduleRatePoll = () => {
  if (rateTimer) {
    clearTimeout(rateTimer)
    rateTimer = null
  }
  if (!monitorActive.value || !selectedDevice.value) return
  rateTimer = setTimeout(async () => {
    rateTimer = null
    if (!monitorActive.value || !selectedDevice.value) return
    await fetchMonitorRate()
    scheduleRatePoll()
  }, RATE_POLL_INTERVAL)
}

const startMonitor = async () => {
  if (monitorActive.value) return
  monitorActive.value = true
  if (!selectedDevice.value) return
  monitorLoading.value = true
  monitorError.value = ''
  rateHistory.value = []
  try {
    await fetchMonitorSnapshot()
    await fetchMonitorRate()
    await nextTick()
    renderSnapshotCharts()
    renderRateChart()
  } finally {
    monitorLoading.value = false
    scheduleRatePoll()
    if (!snapshotTimer) {
      snapshotTimer = setInterval(() => {
        if (monitorActive.value) fetchMonitorSnapshot()
      }, SNAPSHOT_INTERVAL)
    }
  }
}

const stopMonitor = () => {
  monitorActive.value = false
  rateRequesting = false
  if (rateTimer) {
    clearTimeout(rateTimer)
    rateTimer = null
  }
  if (snapshotTimer) {
    clearInterval(snapshotTimer)
    snapshotTimer = null
  }
}

const refreshMonitor = async () => {
  if (!monitorActive.value) return
  monitorLoading.value = true
  monitorError.value = ''
  try {
    await fetchMonitorSnapshot()
    await fetchMonitorRate()
    await nextTick()
    renderSnapshotCharts()
    renderRateChart()
  } finally {
    monitorLoading.value = false
  }
}

const disposeMonitorCharts = () => {
  snapshotChart?.dispose()
  rateChart?.dispose()
  coreChart?.dispose()
  snapshotChart = null
  rateChart = null
  coreChart = null
}

onMounted(async () => {
  await loadConfig();
  
  // 组件挂载时自动刷新设备信息
  fetchDevices();
  
  if (window.utools) {
    window.utools.onPluginEnter((action) => {
      route.value = action.code
      enterAction.value = action
      fetchDevices()
    })
    window.utools.onPluginOut((isKill) => {
      route.value = ''
    })
  }
})

// 懒加载：只有真正切到性能监控 Tab 才发起请求，离开立即停止轮询并销毁图表
// 注意：面板是 v-if，离开 Tab 后 DOM 会被销毁，图表实例必须一并释放，否则再次进入会操作已脱离文档的节点
watch(activeTab, async (tab) => {
  if (tab === 'monitor') {
    await nextTick()
    startMonitor()
    resizeMonitorCharts()
  } else {
    stopMonitor()
    disposeMonitorCharts()
  }
})

// 监控中切换设备时，重置历史并立即拉取新设备数据
watch(selectedDevice, async (deviceId) => {
  if (!monitorActive.value) return
  rateHistory.value = []
  monitorSnapshot.value = null
  monitorRate.value = null
  disposeMonitorCharts()
  if (!deviceId) return
  await nextTick()
  await refreshMonitor()
  scheduleRatePoll()
})

onMounted(() => {
  window.addEventListener('resize', resizeMonitorCharts)
})

onUnmounted(() => {
  // 组件销毁时务必停掉轮询并释放图表实例
  window.removeEventListener('resize', resizeMonitorCharts)
  stopMonitor()
  disposeMonitorCharts()
})
</script>
<style scoped>
.sidebar {
  display: flex;
  flex-direction: column;
  width: 250px;
  height: 100%;
}

.device-list-container {
  flex: 1;
  overflow-y: auto;
  margin-bottom: 8px;
}

.file-list {
  border: 1px solid #ccc;
  max-height: calc(100vh - 180px);
  overflow-y: auto;
}

.wireless-connect {
  margin-top: auto;
  border-top: 1px solid #e0e0e0;
}

.wireless-input-group {
  display: flex;
  gap: 8px;
  margin-top: 8px;
}

.wireless-input {
  width: 150px;
  height: 32px;
  padding: 0 8px;
  border: 1px solid #ddd;
  border-radius: 4px;
  font-size: 14px;
}

.wireless-button {
  height: 32px;
  padding: 0 16px;
  background-color: #4caf50;
  color: white;
  border: none;
  border-radius: 4px;
  cursor: pointer;
  font-size: 14px;
}

.wireless-button:hover {
  background-color: #45a049;
}

.wireless-status {
  margin-top: 8px;
  font-size: 12px;
  color: #666;
  min-height: 16px;
}

.back-button {
  position: absolute;
  top: 10px;
  left: 10px;
  width: 36px;
  height: 36px;
  padding: 0;
  background: rgba(128, 128, 128, 0.3);
  color: white;
  border: none;
  border-radius: 50%;
  cursor: pointer;
  z-index: 100;
  font-size: 18px;
  line-height: 36px;
  text-align: center;
  transition: background 0.3s ease;
}

.back-button:hover {
  background: rgba(128, 128, 128, 0.8);
}

.app {
  position: relative;
  width: 100%;
  height: 100vh;
  min-height: 544px;
}

/* ===== 投屏页面卡片布局 ===== */
.keys-page {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.scrcpy-card {
  background: #fff;
  border-radius: 10px;
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.08);
  overflow: hidden;
}

.card-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 16px;
  font-size: 14px;
  font-weight: 600;
  color: #333;
  border-bottom: 1px solid #f0f0f0;
  background: #fafafa;
}

.card-icon {
  display: flex;
  align-items: center;
  color: #4caf50;
}

.card-body {
  padding: 16px;
}

/* ===== 投屏启动按钮 ===== */
.scrcpy-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  width: 100%;
  padding: 10px;
  font-size: 14px;
  font-weight: 600;
  font-family: inherit;
  color: #fff;
  background: linear-gradient(135deg, #4caf50, #43a047);
  border: none;
  border-radius: 8px;
  cursor: pointer;
  transition: all 0.3s ease;
  box-shadow: 0 4px 16px rgba(76, 175, 80, 0.3);
}

.scrcpy-btn:hover {
  transform: translateY(-2px);
  box-shadow: 0 6px 20px rgba(76, 175, 80, 0.4);
  background: linear-gradient(135deg, #43a047, #388e3c);
}

.scrcpy-btn:active {
  transform: translateY(0);
  box-shadow: 0 2px 8px rgba(76, 175, 80, 0.3);
}

/* ===== 按键网格 ===== */
.key-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 6px;
  margin-bottom: 10px;
}

.key-btn {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 10px 12px;
  background: #f5f7fa;
  border: 1px solid #e8eaed;
  border-radius: 8px;
  cursor: pointer;
  font-size: 12px;
  font-weight: 500;
  color: #444;
  font-family: inherit;
  transition: all 0.25s ease;
}

.key-btn:hover {
  background: #e8f5e9;
  border-color: #a5d6a7;
  color: #2e7d32;
  transform: translateY(-1px);
  box-shadow: 0 3px 10px rgba(76, 175, 80, 0.12);
}

.key-btn:active {
  transform: translateY(0);
  background: #c8e6c9;
}

.key-btn svg {
  color: #666;
}

.key-btn:hover svg {
  color: #4caf50;
}

/* ===== 重启按钮 ===== */
.restart-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 5px;
  width: 100%;
  padding: 6px;
  background: #fff3f3;
  border: 1px solid #ffcdd2;
  border-radius: 8px;
  cursor: pointer;
  font-size: 13px;
  font-weight: 500;
  color: #c62828;
  font-family: inherit;
  transition: all 0.25s ease;
}

.restart-btn:hover {
  background: #ffebee;
  border-color: #ef9a9a;
  transform: translateY(-1px);
  box-shadow: 0 3px 10px rgba(198, 40, 40, 0.12);
}

.restart-btn:active {
  transform: translateY(0);
}

/* ===== 音量控制行 ===== */
.volume-row {
  display: grid;
  grid-template-columns: 1fr 1fr 1fr;
  gap: 6px;
}

.vol-btn {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 3px;
  padding: 8px 6px;
  background: #f5f7fa;
  border: 1px solid #e8eaed;
  border-radius: 8px;
  cursor: pointer;
  font-size: 12px;
  font-weight: 500;
  color: #444;
  font-family: inherit;
  transition: all 0.25s ease;
}

.vol-btn:hover {
  background: #e3f2fd;
  border-color: #90caf9;
  color: #1565c0;
  transform: translateY(-1px);
  box-shadow: 0 3px 10px rgba(33, 150, 243, 0.12);
}

.vol-btn:active {
  transform: translateY(0);
  background: #bbdefb;
}

.vol-btn.mute-btn:hover {
  background: #fff3e0;
  border-color: #ffcc80;
  color: #e65100;
  box-shadow: 0 3px 10px rgba(255, 152, 0, 0.12);
}

/* ===== 设备信息页 ===== */
.info-grid-2col {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 2px 12px;
}

.info-item-2col {
  padding: 6px 0;
  border-bottom: 1px solid #f5f5f5;
  min-width: 0;
}

.info-item-2col:nth-last-child(-n+2) {
  border-bottom: none;
}

.info-label {
  display: block;
  font-size: 11px;
  color: #999;
  margin-bottom: 2px;
}

.info-value {
  display: block;
  font-size: 13px;
  color: #333;
  word-break: break-all;
}

.copyable-value {
  cursor: pointer;
  padding: 2px 6px;
  margin: -2px -6px;
  border-radius: 4px;
  transition: all 0.2s ease;
  display: inline-block;
  position: relative;
}

.copyable-value:hover:not(.disabled) {
  background-color: #e3f2fd;
  color: #1565c0;
}

.copyable-value:hover:not(.disabled)::after {
  content: '📋';
  margin-left: 4px;
  font-size: 12px;
}

.copyable-value:active:not(.disabled) {
  background-color: #bbdefb;
}

.copyable-value.disabled {
  cursor: not-allowed;
  color: #999;
}

.status-badge {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 10px;
  font-size: 12px;
  font-weight: 500;
}

.status-badge.connected {
  background: #e8f5e9;
  color: #2e7d32;
}

.status-badge.locked {
  background: #fff3e0;
  color: #e65100;
}

.loading-text {
  color: #999;
  font-size: 13px;
}

/* ===== 功能区按钮 ===== */
.feature-grid {
  display: grid;
  grid-template-columns: 1fr 1fr 1fr;
  gap: 8px;
}

.feature-btn {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 14px 8px;
  background: #f5f7fa;
  border: 1px solid #e8eaed;
  border-radius: 8px;
  cursor: pointer;
  font-size: 12px;
  font-weight: 500;
  color: #444;
  font-family: inherit;
  transition: all 0.25s ease;
}

.feature-btn:hover {
  background: #e8f5e9;
  border-color: #a5d6a7;
  color: #2e7d32;
  transform: translateY(-1px);
  box-shadow: 0 3px 10px rgba(76, 175, 80, 0.12);
}

.feature-btn:active {
  transform: translateY(0);
  background: #c8e6c9;
}

.feature-btn.danger:hover {
  background: #fff3e0;
  border-color: #ffcc80;
  color: #e65100;
  box-shadow: 0 3px 10px rgba(255, 152, 0, 0.12);
}

.feature-btn svg {
  color: #666;
}

.feature-btn:hover svg {
  color: #4caf50;
}

.feature-btn.danger:hover svg {
  color: #e65100;
}

/* ===== 应用管理页 ===== */
.card-action-btn {
  margin-left: auto;
  padding: 4px 12px;
  background: #4caf50;
  color: #fff;
  border: none;
  border-radius: 6px;
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  font-family: inherit;
  transition: all 0.2s ease;
}

.card-action-btn:hover {
  background: #43a047;
  transform: translateY(-1px);
}

.card-action-btn:active {
  transform: translateY(0);
}

.app-list {
  max-height: calc(100vh - 140px);
  overflow-y: auto;
}

.app-item {
  display: flex;
  align-items: center;
  padding: 10px 16px;
  border-top: 1px solid #f0f0f0;
  transition: background 0.2s;
}

.app-item:first-child {
  border-top: none;
}

.app-item:hover {
  background: #f8f9fb;
}

.app-item-info {
  flex: 1;
  min-width: 0;
}

.app-item-name {
  font-size: 13px;
  font-weight: 600;
  color: #333;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.app-item-desc {
  font-size: 11px;
  color: #999;
  margin-top: 2px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.app-item-actions {
  display: flex;
  gap: 4px;
  flex-shrink: 0;
  margin-left: 8px;
}

.app-act-btn {
  width: 28px;
  height: 28px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: 1px solid #e8eaed;
  border-radius: 6px;
  background: #fff;
  cursor: pointer;
  color: #888;
  transition: all 0.2s ease;
}

.app-act-btn:hover {
  border-color: #a5d6a7;
  background: #e8f5e9;
}

.app-act-btn.start:hover {
  border-color: #a5d6a7;
  background: #e8f5e9;
  color: #2e7d32;
}

.app-act-btn.stop:hover {
  border-color: #90caf9;
  background: #e3f2fd;
  color: #1565c0;
}

.app-act-btn.extract:hover {
  border-color: #ffcc80;
  background: #fff3e0;
  color: #e65100;
}

.app-act-btn.uninstall:hover {
  border-color: #ef9a9a;
  background: #ffebee;
  color: #c62828;
}

/* ===== 文件管理页 ===== */
.file-path-text {
  font-size: 13px;
  font-weight: 500;
  color: #555;
  flex: 1;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  margin-right: 8px;
}

.file-toolbar {
  display: flex;
  gap: 4px;
  padding: 8px 12px;
  border-bottom: 1px solid #f0f0f0;
  background: #fafafa;
}

.toolbar-btn {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 5px 10px;
  background: #fff;
  border: 1px solid #e8eaed;
  border-radius: 6px;
  cursor: pointer;
  font-size: 12px;
  color: #555;
  font-family: inherit;
  transition: all 0.2s ease;
}

.toolbar-btn:hover {
  background: #e8f5e9;
  border-color: #a5d6a7;
  color: #2e7d32;
}

.toolbar-btn:active {
  background: #c8e6c9;
}

.toolbar-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.toolbar-btn:disabled:hover {
  background: #fff;
  border-color: #e8eaed;
  color: #555;
  transform: none;
}

.file-item {
  display: flex;
  align-items: center;
  padding: 8px 12px;
  border-top: 1px solid #f5f5f5;
  cursor: pointer;
  transition: background 0.2s;
}

.file-item:first-child {
  border-top: none;
}

.file-item:hover {
  background: #f0f7f0;
}

.file-item-icon {
  flex-shrink: 0;
  margin-right: 8px;
  font-size: 16px;
}

.file-item-name {
  flex: 1;
  font-size: 13px;
  color: #333;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  min-width: 0;
}

.file-item-name.is-dir {
  font-weight: 500;
  color: #1565c0;
}

.file-item-size {
  flex-shrink: 0;
  font-size: 11px;
  color: #999;
  margin: 0 8px;
  font-family: 'Consolas', 'Monaco', monospace;
  min-width: 52px;
  text-align: right;
}

.device-item {
  display: flex !important;
  align-items: center;
  gap: 8px;
}

.device-item-info {
  flex: 1;
  min-width: 0;
  cursor: pointer;
}

/* 断开全部：设备列表末尾的常驻按钮，宽度与设备项一致 */
.disconnect-all-btn {
  display: block;
  width: 100%;
  box-sizing: border-box;
  margin-top: 8px;
  padding: 8px 10px;
  font-size: 13px;
  font-weight: 500;
  font-family: inherit;
  background-color: #f44336;
  color: white;
  border: none;
  border-radius: 4px;
  cursor: pointer;
  transition: background-color 0.2s;
  flex-shrink: 0; /* 列表滚动时按钮不被压缩，始终完整显示在末尾 */
}

.disconnect-all-btn:hover:not(:disabled) {
  background-color: #d32f2f;
}

.disconnect-all-btn:disabled {
  filter: grayscale(1);
  cursor: not-allowed;
}

.disconnect-btn {
  flex-shrink: 0;
  width: 24px;
  height: 24px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: 1px solid transparent;
  border-radius: 4px;
  background: transparent;
  color: #e53935;
  cursor: pointer;
  transition: all 0.2s ease;
}

.disconnect-btn:hover {
  background: #ffebee;
  border-color: #ef9a9a;
}

.file-item-actions {
  display: flex;
  gap: 4px;
  flex-shrink: 0;
  margin-left: 8px;
}

button.file-act-btn {
  width: 26px;
  height: 26px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: 1px solid transparent;
  border-radius: 4px;
  background: transparent;
  cursor: pointer;
  font-size: 13px;
  color: #333 !important;
  transition: all 0.2s ease;
}

button.file-act-btn.export:hover {
  background: #e8f5e9;
  border-color: #a5d6a7;
}

button.file-act-btn.delete:hover {
  background: #ffebee;
  border-color: #ef9a9a;
}

/* ===== 批量操作页面 ===== */
.tab-content {
  height: auto;
  flex: 1;
  min-height: 0;
}

.batch-page {
  display: flex;
  flex-direction: column;
  height: 100%;
  /* 底部留白与左右两侧的 10px 保持一致 */
  padding-bottom: 10px;
}

.batch-card {
  background: #fff;
  border-radius: 10px;
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.08);
  overflow: hidden;
  display: flex;
  flex-direction: column;
  height: 100%;
}

.batch-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.batch-header-left {
  display: flex;
  align-items: center;
  gap: 8px;
}

.batch-selected-info {
  font-size: 13px;
  font-weight: 400;
  color: #4caf50;
}

.batch-content {
  display: flex;
  flex-direction: column;
  flex: 1;
  overflow: hidden;
}

/* 上半部分：设备列表 - flex:1 占据剩余空间 */
.batch-device-list {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  border-bottom: 1px solid #f0f0f0;
  min-height: 0;
}

.batch-list-header {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 16px;
  font-size: 13px;
  font-weight: 600;
  color: #666;
  background: #fafafa;
  border-bottom: 1px solid #f0f0f0;
}

.batch-list-header input[type="checkbox"] {
  width: 16px;
  height: 16px;
  cursor: pointer;
}

.batch-list-body {
  flex: 1;
  overflow-y: auto;
}

.batch-device-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 16px;
  font-size: 13px;
  color: #333;
  border-bottom: 1px solid #f5f5f5;
  cursor: pointer;
  transition: background 0.15s;
}

.batch-device-row:hover {
  background: #f5f9ff;
}

.batch-device-row.is-selected {
  background: #e8f5e9;
}

.batch-device-row input[type="checkbox"] {
  width: 16px;
  height: 16px;
  cursor: pointer;
}

.col-device-id {
  flex: 1.5;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
}

.col-model {
  flex: 1.5;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
}

.col-sn {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
  font-family: monospace;
  font-size: 12px;
}

.batch-checkbox-label {
  display: flex;
  align-items: center;
  gap: 6px;
  cursor: pointer;
}

.batch-loading,
.batch-empty {
  padding: 24px;
  text-align: center;
  color: #999;
  font-size: 13px;
}

.model-filter-input {
  width: 90px;
  padding: 3px 8px;
  margin-top: 4px;
  border: 1px solid #d9d9d9;
  border-radius: 4px;
  font-size: 12px;
  outline: none;
  transition: border-color 0.2s;
}

.model-filter-input:focus {
  border-color: #4caf50;
}

/* 下半部分：操作按钮区 + 结果区 - 高度由内容决定，不强制拉伸 */
.batch-action-area {
  flex: 0 0 auto;
  padding: 16px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: flex-start;
  gap: 12px;
  background: #fafafa;
  overflow: hidden;
}

/* 按钮区 - 高度由按钮内容决定，不拉伸 */
.batch-action-grid {
  flex: none;
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 12px;
  width: 100%;
  max-width: 600px;
}

button.batch-action-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 10px 12px;
  font-size: 13px;
  font-weight: 500;
  color: #333;
  background: #fff;
  border: 1px solid #d9d9d9;
  border-radius: 6px;
  cursor: pointer;
  transition: all 0.2s ease;
}

button.batch-action-btn:hover:not(:disabled) {
  background: #e8f5e9;
  border-color: #4caf50;
  color: #2e7d32;
}

button.batch-action-btn:disabled {
  background: #f5f5f5;
  color: #bbb;
  border-color: #eee;
  cursor: not-allowed;
}

.batch-action-placeholder {
  color: #bbb;
  font-size: 14px;
}

/* 批量 Shell 执行结果 - 显示时给合适的高度，不参与外层比例 */
.batch-shell-results {
  flex: none;
  width: 100%;
  max-width: 800px;
  max-height: 260px;
  padding: 12px;
  background: #fff;
  border: 1px solid #e8e8e8;
  border-radius: 6px;
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
}

.shell-results-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-bottom: 8px;
  margin-bottom: 8px;
  border-bottom: 1px solid #f0f0f0;
  font-size: 13px;
  font-weight: 600;
  color: #333;
}

.shell-close-btn {
  background: none;
  border: none;
  font-size: 18px;
  color: #999;
  cursor: pointer;
  padding: 0 4px;
  line-height: 1;
}

.shell-close-btn:hover {
  color: #666;
}

.shell-results-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  flex: 1;
  overflow-y: auto;
  min-height: 0;
  margin-top: 8px;
}

.shell-result-item {
  padding: 8px;
  background: #fafafa;
  border-radius: 4px;
  border-left: 3px solid #d9d9d9;
}

.shell-result-item:nth-child(odd) {
  background: #f5f5f5;
}

.shell-success {
  border-left-color: #4caf50;
}

.shell-failed {
  border-left-color: #f44336;
}

.shell-result-device {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 6px;
  font-size: 12px;
}

.shell-status-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
  border-radius: 50%;
  font-size: 10px;
  font-weight: bold;
  color: #fff;
}

.shell-success .shell-status-icon {
  background: #4caf50;
}

.shell-failed .shell-status-icon {
  background: #f44336;
}

.shell-device-id {
  font-weight: 500;
  color: #333;
  word-break: break-all;
}

.shell-output-section {
  margin-top: 4px;
}

.shell-output-label {
  font-size: 11px;
  color: #888;
  margin-bottom: 2px;
  font-weight: 500;
}

.shell-output {
  margin: 0;
  padding: 6px 8px;
  background: #fff;
  border: 1px solid #e8e8e8;
  border-radius: 3px;
  font-size: 11px;
  font-family: 'Consolas', 'Monaco', 'Courier New', monospace;
  color: #333;
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 100px;
  overflow-y: auto;
  line-height: 1.4;
}

.shell-error .shell-output {
  background: #fff8f8;
  border-color: #ffd6d6;
}

.shell-error-text {
  color: #c62828;
}

/* ===== 性能监控页面 ===== */
.monitor-error {
  margin-bottom: 12px;
  padding: 8px 12px;
  background: #ffebee;
  border: 1px solid #ffcdd2;
  border-radius: 6px;
  color: #c62828;
  font-size: 13px;
}

.monitor-usage {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.gauge-chart-wide {
  width: 100%;
  height: 190px;
}

/* 性能监控第二行：CPU 频率 | 内存/磁盘 两列布局 */
.monitor-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
  align-items: stretch;
}

.gauge-chart {
  width: 100%;
  height: 160px;
}

.gauge-detail-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}

.gauge-detail {
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding: 10px 12px;
  background: #f8f9fb;
  border: 1px solid #eef1f4;
  border-radius: 8px;
  font-size: 12px;
  color: #666;
}

.detail-title {
  font-size: 13px;
  font-weight: 600;
  color: #333;
  margin-bottom: 2px;
}

.monitor-empty {
  color: #999;
  font-size: 12px;
}

.monitor-live-tag {
  margin-left: auto;
  padding: 2px 8px;
  border-radius: 10px;
  background: #e8f5e9;
  color: #2e7d32;
  font-size: 11px;
  font-weight: 500;
}

.monitor-freq-info {
  margin-left: auto;
  font-size: 12px;
  font-weight: 400;
  color: #888;
}

.rate-summary {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 8px;
  margin-bottom: 12px;
}

.rate-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 8px 10px;
  background: #f8f9fb;
  border: 1px solid #eef1f4;
  border-radius: 8px;
}

.rate-label {
  font-size: 11px;
  color: #999;
}

.rate-value {
  font-size: 15px;
  font-weight: 600;
  color: #333;
}

.rate-value.cpu {
  color: #4caf50;
}

.rate-value.down {
  color: #2196f3;
}

.rate-value.up {
  color: #ff9800;
}

.rate-chart {
  width: 100%;
  height: 220px;
}

.core-chart {
  width: 100%;
  height: 180px;
}

@media (max-width: 720px) {
  .monitor-row {
    grid-template-columns: 1fr;
  }

  .gauge-detail-row {
    grid-template-columns: 1fr;
  }

  .rate-summary {
    grid-template-columns: repeat(2, 1fr);
  }
}

@media (prefers-color-scheme: dark) {
  .gauge-detail,
  .rate-item {
    background: #33363e;
    border-color: #40434a;
    color: #b0b0b0;
  }

  .detail-title,
  .rate-value {
    color: #e0e0e0;
  }

  .monitor-error {
    background: #3a2426;
    border-color: #5c3234;
    color: #ef9a9a;
  }

  .monitor-live-tag {
    background: #2e3d31;
    color: #81c784;
  }

  .monitor-freq-info {
    color: #999;
  }
}

</style>
<template>
  <div class="app">
    <button class="back-button" @click="goHome">&lt;</button>
    <main>
      <div class="container">
        <div class="sidebar">
          <div class="device-header">
            <h2>设备管理</h2>
            <button class="refresh-button" @click="fetchDevices">刷新</button>
          </div>
          <div class="device-list-container">
            <div v-if="loading">加载设备中...</div>
            <div v-else-if="devices.length === 0">无设备连接</div>
            <template v-else>
              <div
                v-for="device in devices"
                :key="device.deviceId"
                class="device-item"
                :class="{ active: selectedDevice === device.deviceId }"
              >
                <div class="device-item-info" @click="selectDevice(device.deviceId)">
                  <div class="device-id">{{ device.deviceId }}</div>
                  <div class="device-info" v-if="device.model">{{ device.model }}</div>
                  <div class="device-status" :class="{ 'status-offline': device.status === 'offline' }">
                    {{ device.status === 'device' ? '已连接' : device.status === 'offline' ? '离线' : device.status }}
                  </div>
                </div>
                <button class="disconnect-btn" @click.stop="confirmDisconnect(device.deviceId)" title="断开连接">
                  <svg viewBox="0 0 1024 1024" width="12" height="12" fill="currentColor">
                    <path d="M816.952281 614.064456l-45.118858-45.118857L895.99028 447.98866a223.99433 223.99433 0 0 0-316.791981-316.791981l-124.156857 120.956938-45.118858-45.438849 122.556898-122.236906a287.99271 287.99271 0 0 1 407.029697 407.029697zM288.00567 1023.974081a287.99271 287.99271 0 0 1-203.514849-491.507559l122.556898-122.556898 45.118858 45.118858-122.556898 122.556898A223.99433 223.99433 0 0 0 448.00162 895.977321l122.556898-122.556898 45.118858 45.118858-122.556898 122.556898A285.752767 285.752767 0 0 1 288.00567 1023.974081zM137.257486 182.555379L182.50434 137.276525l255.897523 255.897523-45.246855 45.246854zM585.214147 630.544039l45.246854-45.246854 255.897523 255.897522-45.246855 45.246855z"/>
                  </svg>
                </button>
              </div>
            </template>
            <!-- 断开全部：始终固定在设备列表末尾，宽度与设备项一致 -->
            <button class="disconnect-all-btn" @click="confirmDisconnectAll" :disabled="disconnectAllLoading">
              {{ disconnectAllLoading ? '断开中...' : '断开全部' }}
            </button>
          </div>

          <div class="wireless-connect">
            <h3>无线连接</h3>
            <div class="wireless-input-group">
              <input 
                type="text" 
                v-model="wirelessIp" 
                placeholder="输入设备IP地址（端口可选，默认5555）"
                class="wireless-input"
              >
              <button class="wireless-button" @click="connectWireless">连接</button>
            </div>
            <div v-if="wirelessStatus" class="wireless-status">
              {{ wirelessStatus }}
            </div>
          </div>
        </div>
        <div class="content">
          <div v-if="!selectedDevice">请选择一个设备</div>
          <div v-else style="height: 100%; display: flex; flex-direction: column; overflow: hidden;">
            <div class="tabs">
              <button 
                class="tab-button" 
                :class="{ active: activeTab === 'info' }"
                @click="activeTab = 'info'"
              >
                设备信息
              </button>
              <button 
                class="tab-button" 
                :class="{ active: activeTab === 'apps' }"
                @click="activeTab = 'apps'"
              >
                应用管理
              </button>
              <button 
                class="tab-button" 
                :class="{ active: activeTab === 'files' }"
                @click="activeTab = 'files'"
              >
                文件管理
              </button>
              <button 
                class="tab-button" 
                :class="{ active: activeTab === 'keys' }"
                @click="activeTab = 'keys'"
              >
                设备投屏
              </button>
              <button 
                class="tab-button" 
                :class="{ active: activeTab === 'batch' }"
                @click="activeTab = 'batch'"
              >
                批量操作
              </button>
              <button 
                class="tab-button" 
                :class="{ active: activeTab === 'monitor' }"
                @click="activeTab = 'monitor'"
              >
                性能监控
              </button>
              
            </div>
            
            <div class="tab-content">
              <div v-if="activeTab === 'info'" class="keys-page">
                <!-- 卡片1: 设备信息 -->
                <div class="scrcpy-card">
                  <div class="card-header">
                    <span class="card-icon">
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="18" height="18">
                        <rect x="4" y="4" width="16" height="16" rx="2"/>
                        <line x1="4" y1="10" x2="20" y2="10"/>
                        <line x1="10" y1="4" x2="10" y2="20"/>
                      </svg>
                    </span>
                    <span>设备信息</span>
                  </div>
                  <div class="card-body">
                    <div v-if="deviceInfo" class="info-grid-2col">
                      <div class="info-item-2col">
                        <span class="info-label">设备状态</span>
                        <span class="info-value"><span class="status-badge connected">{{ deviceInfo.status }}</span></span>
                      </div>
                      <div class="info-item-2col">
                        <span class="info-label">设备序列号</span>
                        <span class="info-value">{{ deviceInfo.deviceId }}</span>
                      </div>
                      <div class="info-item-2col">
                        <span class="info-label">设备名称</span>
                        <span class="info-value">{{ deviceInfo.deviceName || deviceInfo.model || '未知' }}</span>
                      </div>
                      <div class="info-item-2col">
                        <span class="info-label">安卓版本</span>
                        <span class="info-value">{{ deviceInfo.androidVersion || deviceInfo.version || '未知' }}</span>
                      </div>
                      <div class="info-item-2col">
                        <span class="info-label">版本信息</span>
                        <span class="info-value">{{ deviceInfo.displayVersion || '未知' }}</span>
                      </div>
                      <div class="info-item-2col">
                        <span class="info-label">构建日期</span>
                        <span class="info-value">{{ deviceInfo.buildDate || '未知' }}</span>
                      </div>
                      <div class="info-item-2col">
                        <span class="info-label">SN号</span>
                        <span 
                          class="info-value copyable-value" 
                          :class="{ disabled: !deviceInfo.sn }"
                          @click="copyToClipboard(deviceInfo.sn, 'SN号')"
                          title="点击复制"
                        >
                          {{ deviceInfo.sn || '未知' }}
                        </span>
                      </div>
                      <div class="info-item-2col">
                        <span class="info-label">WiFi MAC</span>
                        <span 
                          class="info-value copyable-value" 
                          :class="{ disabled: !deviceInfo.wifiMac }"
                          @click="copyToClipboard(deviceInfo.wifiMac, 'WiFi MAC')"
                          title="点击复制"
                        >
                          {{ deviceInfo.wifiMac || '未知' }}
                        </span>
                      </div>
                      <div class="info-item-2col">
                        <span class="info-label">以太网 MAC</span>
                        <span 
                          class="info-value copyable-value" 
                          :class="{ disabled: !deviceInfo.ethMac }"
                          @click="copyToClipboard(deviceInfo.ethMac, '以太网 MAC')"
                          title="点击复制"
                        >
                          {{ deviceInfo.ethMac || '未知' }}
                        </span>
                      </div>
                    </div>
                    <div v-else class="loading-text">加载设备信息中...</div>
                  </div>
                </div>

                <!-- 卡片2: 功能区 -->
                <div class="scrcpy-card">
                  <div class="card-header">
                    <span class="card-icon">
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="18" height="18">
                        <circle cx="12" cy="12" r="3"/>
                        <path d="M19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 010 2.83 2 2 0 01-2.83 0l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 01-2 2 2 2 0 01-2-2v-.09A1.65 1.65 0 009 19.4a1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 01-2.83 0 2 2 0 010-2.83l.06-.06A1.65 1.65 0 004.68 15a1.65 1.65 0 00-1.51-1H3a2 2 0 01-2-2 2 2 0 012-2h.09A1.65 1.65 0 004.6 9a1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 010-2.83 2 2 0 012.83 0l.06.06A1.65 1.65 0 009 4.68a1.65 1.65 0 001-1.51V3a2 2 0 012-2 2 2 0 012 2v.09a1.65 1.65 0 001 1.51 1.65 1.65 0 001.82-.33l.06-.06a2 2 0 012.83 0 2 2 0 010 2.83l-.06.06a1.65 1.65 0 00-.33 1.82V9a1.65 1.65 0 001.51 1H21a2 2 0 012 2 2 2 0 01-2 2h-.09a1.65 1.65 0 00-1.51 1z"/>
                      </svg>
                    </span>
                    <span>功能区</span>
                  </div>
                  <div class="card-body">
                    <div class="feature-grid">
                      <button class="feature-btn" @click="showDeviceLogs">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="18" height="18">
                          <polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/>
                        </svg>
                        <span>实时日志</span>
                      </button>
                      <button class="feature-btn" @click="openDeviceShell">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="18" height="18">
                          <polyline points="16 18 22 12 16 6"/>
                          <polyline points="8 6 2 12 8 18"/>
                        </svg>
                        <span>Shell窗口</span>
                      </button>
                      <button class="feature-btn danger" @click="rebootToLoader">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="18" height="18">
                          <polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 11-2.12-9.36L23 10"/>
                        </svg>
                        <span>re boot loader</span>
                      </button>
                    </div>
                  </div>
                </div>
              </div>
              
              <div v-if="activeTab === 'apps'" class="keys-page">
                <div class="scrcpy-card">
                  <div class="card-header">
                    <span class="card-icon">
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="18" height="18">
                        <rect x="4" y="2" width="16" height="20" rx="2"/>
                        <line x1="8" y1="6" x2="16" y2="6"/>
                        <line x1="8" y1="10" x2="16" y2="10"/>
                        <line x1="8" y1="14" x2="12" y2="14"/>
                      </svg>
                    </span>
                    <span>应用管理</span>
                    <button class="card-action-btn" @click="installApp">+ 安装</button>
                  </div>
                  <div class="card-body" style="padding: 0;">
                    <div v-if="appLoading" class="loading-text" style="padding: 24px; text-align: center;">加载应用列表中...</div>
                    <div v-else-if="apps.length === 0" class="loading-text" style="padding: 24px; text-align: center;">无应用</div>
                    <div v-else class="app-list">
                      <div v-for="app in apps" :key="app.packageName" class="app-item">
                        <div class="app-item-info">
                          <div class="app-item-name">{{ app.appName }}</div>
                          <div class="app-item-desc">{{ app.packageName }} · v{{ app.versionName || '?' }}</div>
                        </div>
                        <div class="app-item-actions">
                          <button class="app-act-btn start" @click="startApp(app.packageName)" title="启动">
                            <svg viewBox="0 0 24 24" fill="currentColor" width="12" height="12"><polygon points="5 3 19 12 5 21 5 3"/></svg>
                          </button>
                          <button class="app-act-btn stop" @click="stopApp(app.packageName)" title="停止">
                            <svg viewBox="0 0 24 24" fill="currentColor" width="12" height="12"><rect x="6" y="6" width="12" height="12"/></svg>
                          </button>
                          <button class="app-act-btn extract" @click="extractApk(app.packageName, app.appName)" title="提取APK">
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="12" height="12"><path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
                          </button>
                          <button class="app-act-btn uninstall" @click="uninstallApp(app.packageName)" title="卸载">
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="12" height="12"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2"/></svg>
                          </button>
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
              
              <div v-if="activeTab === 'files'" class="keys-page">
                <div class="scrcpy-card">
                  <div class="card-header">
                    <span class="card-icon">
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="18" height="18">
                        <path d="M22 19a2 2 0 01-2 2H4a2 2 0 01-2-2V5a2 2 0 012-2h5l2 3h9a2 2 0 012 2z"/>
                      </svg>
                    </span>
                    <span class="file-path-text">{{ currentPath }}</span>
                    <button class="card-action-btn" @click="navigateTo('/sdcard')">根目录</button>
                  </div>
                  <div class="card-body" style="padding: 0;">
                    <div class="file-toolbar">
                      <button class="toolbar-btn" @click="fetchFiles(selectedDevice, currentPath)" title="刷新">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14">
                          <polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 11-2.12-9.36L23 10"/>
                        </svg>
                        刷新
                      </button>
                      <button class="toolbar-btn" @click="goBack" title="返回上一级" :disabled="currentPath === '/' || currentPath === '/sdcard'">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14">
                          <polyline points="15 18 9 12 15 6"/>
                        </svg>
                        返回
                      </button>
                      <button class="toolbar-btn" @click="createFolder" title="创建文件夹">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14">
                          <line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>
                        </svg>
                        新建文件夹
                      </button>
                      <button class="toolbar-btn" @click="createFile" title="创建文件">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14">
                          <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="12" y1="12" x2="12" y2="18"/><line x1="9" y1="15" x2="15" y2="15"/>
                        </svg>
                        新建文件
                      </button>
                      <button class="toolbar-btn" @click="importFile" title="导入文件">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14">
                          <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>
                        </svg>
                        导入
                      </button>
                    </div>
                    <div v-if="fileLoading" class="loading-text" style="padding: 24px; text-align: center; border-top: 1px solid #f0f0f0;">加载文件列表中...</div>
                    <div v-else-if="files.length === 0" class="loading-text" style="padding: 24px; text-align: center; border-top: 1px solid #f0f0f0;">无文件</div>
                    <div v-else class="file-list">
                      <div v-for="file in files" :key="file.path" class="file-item" @click="file.isDirectory && navigateTo(file.path)">
                        <div class="file-item-icon">{{ file.isDirectory ? '📁' : '📄' }}</div>
                        <div class="file-item-name" :class="{ 'is-dir': file.isDirectory }">{{ file.name }}</div>
                        <div class="file-item-size">
                          <template v-if="!file.isDirectory">{{ formatBytes(Number(file.size)) }}</template>
                          <template v-else-if="file.size">共 {{ file.size }} 项</template>
                        </div>
                        <div class="file-item-actions">
                          <button v-if="isEditableTextFile(file)" class="file-act-btn export" @click.stop="openFileEditor(file)" title="编辑">
                            <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2">
                              <path d="M17 3a2.828 2.828 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5L17 3z"/>
                            </svg>
                          </button>
                          <button v-else-if="!file.isDirectory" class="file-act-btn export" disabled :title="editDisabledReason(file)" style="opacity:0.35;">
                            <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2">
                              <path d="M17 3a2.828 2.828 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5L17 3z"/>
                            </svg>
                          </button>
                          <button class="file-act-btn export" @click.stop="exportFile(file)" title="导出">
                            <svg viewBox="0 0 1024 1024" width="14" height="14" fill="currentColor">
                              <path d="M853.333333 853.333333a42.666667 42.666667 0 0 1 0 85.333334H170.666667a42.666667 42.666667 0 0 1 0-85.333334h682.666666zM512 85.504a42.666667 42.666667 0 0 1 42.666667 42.666667v515.370666l204.373333-204.373333a42.666667 42.666667 0 0 1 63.914667 56.277333l-3.584 4.010667-277.376 277.546667a42.666667 42.666667 0 0 1-56.32 3.584l-4.010667-3.541334-277.12-276.650666a42.666667 42.666667 0 0 1 56.234667-63.957334l4.010666 3.541334L469.333333 644.096V128.170667a42.666667 42.666667 0 0 1 42.666667-42.666667z"/>
                            </svg>
                          </button>
                          <button class="file-act-btn delete" @click.stop="deleteFile(file.path, file.isDirectory)" title="删除">
                            <svg viewBox="0 0 1024 1024" width="14" height="14" fill="currentColor">
                              <path d="M202.666667 256h-42.666667a32 32 0 0 1 0-64h704a32 32 0 0 1 0 64H266.666667v565.333333a53.333333 53.333333 0 0 0 53.333333 53.333334h384a53.333333 53.333333 0 0 0 53.333333-53.333334V352a32 32 0 0 1 64 0v469.333333c0 64.8-52.533333 117.333333-117.333333 117.333334H320c-64.8 0-117.333333-52.533333-117.333333-117.333334V256z m224-106.666667a32 32 0 0 1 0-64h170.666666a32 32 0 0 1 0 64H426.666667z m-32 288a32 32 0 0 1 64 0v256a32 32 0 0 1-64 0V437.333333z m170.666666 0a32 32 0 0 1 64 0v256a32 32 0 0 1-64 0V437.333333z"/>
                            </svg>
                          </button>
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
              
              <div v-if="activeTab === 'keys'" class="keys-page">
                <!-- 卡片1: 投屏控制 -->
                <div class="scrcpy-card">
                  <div class="card-header">
                    <span class="card-icon">
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="18" height="18">
                        <rect x="2" y="3" width="20" height="14" rx="2"/>
                        <line x1="8" y1="21" x2="16" y2="21"/>
                        <line x1="12" y1="17" x2="12" y2="21"/>
                      </svg>
                    </span>
                    <span>投屏控制</span>
                  </div>
                  <div class="card-body">
                    <button class="scrcpy-btn" @click="startScrcpy">
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="22" height="22">
                        <polygon points="5 3 19 12 5 21 5 3" fill="currentColor" stroke="none"/>
                      </svg>
                      启动投屏
                    </button>
                  </div>
                </div>

                <!-- 卡片2: 按键模拟 -->
                <div class="scrcpy-card">
                  <div class="card-header">
                    <span class="card-icon">
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="18" height="18">
                        <rect x="4" y="2" width="16" height="20" rx="2"/>
                        <line x1="8" y1="6" x2="16" y2="6"/>
                        <line x1="12" y1="6" x2="12" y2="2"/>
                      </svg>
                    </span>
                    <span>按键模拟</span>
                  </div>
                  <div class="card-body">
                    <div class="key-grid">
                      <button class="key-btn" @click="simulateKey('back')">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="18" height="18">
                          <polyline points="15 18 9 12 15 6"/>
                        </svg>
                        <span>返回</span>
                      </button>
                      <button class="key-btn" @click="simulateKey('home')">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="18" height="18">
                          <path d="M3 12l9-9 9 9"/><path d="M5 10v8a1 1 0 001 1h12a1 1 0 001-1v-8"/>
                        </svg>
                        <span>主页</span>
                      </button>
                      <button class="key-btn" @click="simulateKey('recent')">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="18" height="18">
                          <rect x="3" y="4" width="18" height="6" rx="1"/>
                          <rect x="3" y="14" width="18" height="6" rx="1"/>
                        </svg>
                        <span>多任务</span>
                      </button>
                      <button class="key-btn" @click="simulateKey('power')">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="18" height="18">
                          <path d="M12 2v10"/><path d="M18.36 6.64a9 9 0 11-12.72 0"/>
                        </svg>
                        <span>锁屏</span>
                      </button>
                    </div>
                    <button class="restart-btn" @click="restartDevice">
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="16" height="16">
                        <polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 11-2.12-9.36L23 10"/>
                      </svg>
                      重启设备
                    </button>
                  </div>
                </div>

                <!-- 卡片3: 音量控制 -->
                <div class="scrcpy-card">
                  <div class="card-header">
                    <span class="card-icon">
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="18" height="18">
                        <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/>
                        <path d="M19.07 4.93a10 10 0 010 14.14M15.54 8.46a5 5 0 010 7.07"/>
                      </svg>
                    </span>
                    <span>音量控制</span>
                  </div>
                  <div class="card-body">
                    <div class="volume-row">
                      <button class="vol-btn" @click="volumeUp">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="20" height="20">
                          <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/>
                          <line x1="23" y1="9" x2="17" y2="15"/><line x1="17" y1="9" x2="23" y2="15"/>
                        </svg>
                        <span>音量+</span>
                      </button>
                      <button class="vol-btn mute-btn" @click="muteVolume">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="20" height="20">
                          <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/>
                          <line x1="23" y1="9" x2="17" y2="15"/><line x1="17" y1="9" x2="23" y2="15"/>
                        </svg>
                        <span>静音</span>
                      </button>
                      <button class="vol-btn" @click="volumeDown">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="20" height="20">
                          <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/>
                          <line x1="22" y1="12" x2="16" y2="12"/>
                        </svg>
                        <span>音量-</span>
                      </button>
                    </div>
                  </div>
                </div>
              </div>

              <div v-if="activeTab === 'batch'" class="batch-page">
                <div class="batch-card">
                  <div class="card-header batch-header">
                    <div class="batch-header-left">
                      <span class="card-icon">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="18" height="18">
                          <rect x="3" y="3" width="7" height="7" rx="1"/>
                          <rect x="14" y="3" width="7" height="7" rx="1"/>
                          <rect x="3" y="14" width="7" height="7" rx="1"/>
                          <rect x="14" y="14" width="7" height="7" rx="1"/>
                        </svg>
                      </span>
                      <span>批量操作</span>
                    </div>
                    <span class="batch-selected-info">已选择 {{ selectedBatchCount }} 个设备</span>
                  </div>
                  <div class="batch-content">
                    <!-- 上半部分：设备列表 (60%) -->
                    <div class="batch-device-list">
                      <div class="batch-list-header">
                        <label class="batch-checkbox-label">
                          <input type="checkbox" :checked="batchSelectAllChecked" @change="toggleAllBatchDevices" />
                        </label>
                        <span class="col-device-id">设备ID</span>
                        <span class="col-model">
                          型号
                          <input type="text" v-model="batchModelFilter" class="model-filter-input" placeholder="筛选型号..." />
                        </span>
                        <span class="col-sn">SN号</span>
                      </div>
                      <div v-if="batchLoading" class="batch-loading">加载设备信息中...</div>
                      <div v-else-if="batchFilteredDevices.length === 0" class="batch-empty">{{ batchDeviceInfos.length === 0 ? '暂无可用设备' : '无匹配设备' }}</div>
                      <div v-else class="batch-list-body">
                        <label v-for="device in batchFilteredDevices" :key="device.deviceId" class="batch-device-row" :class="{ 'is-selected': batchSelectedDevices.has(device.deviceId) }">
                          <input type="checkbox" :checked="batchSelectedDevices.has(device.deviceId)" @change="toggleBatchDevice(device.deviceId)" />
                          <span class="col-device-id" :title="device.deviceId">{{ device.deviceId }}</span>
                          <span class="col-model">{{ device.model || '未知' }}</span>
                          <span class="col-sn">{{ device.sn || '未知' }}</span>
                        </label>
                      </div>
                    </div>
                    <!-- 下半部分：操作按钮区 (40%) -->
                    <div class="batch-action-area">
                      <div class="batch-action-grid">
                        <button class="batch-action-btn" @click="batchReboot" :disabled="batchRebootLoading">
                          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="16" height="16">
                            <polyline points="23 4 23 10 17 10"/>
                            <path d="M20.49 15a9 9 0 11-2.12-9.36L23 10"/>
                          </svg>
                          <span>{{ batchRebootLoading ? '重启中...' : '批量重启' }}</span>
                        </button>
                        <button class="batch-action-btn" @click="batchInstallApp" :disabled="batchInstallLoading">
                          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="16" height="16">
                            <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/>
                            <polyline points="7 10 12 15 17 10"/>
                            <line x1="12" y1="15" x2="12" y2="3"/>
                          </svg>
                          <span>{{ batchInstallLoading ? '安装中...' : '批量安装' }}</span>
                        </button>
                        <button class="batch-action-btn" @click="batchShellCommand" :disabled="batchShellLoading">
                          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="16" height="16">
                            <polyline points="4 17 10 11 4 5"/>
                            <line x1="12" y1="19" x2="20" y2="19"/>
                          </svg>
                          <span>{{ batchShellLoading ? '执行中...' : '批量Shell' }}</span>
                        </button>
                        <button class="batch-action-btn" disabled>
                          <span>预留2</span>
                        </button>
                      </div>

                      <!-- Shell 执行结果 -->
                      <div v-if="showBatchShellResults && batchShellResults.length > 0" class="batch-shell-results">
                        <div class="shell-results-header">
                          <span>批量 Shell 执行结果</span>
                          <button class="shell-close-btn" @click="showBatchShellResults = false">×</button>
                        </div>
                        <div class="shell-results-list">
                          <div v-for="result in batchShellResults" :key="result.deviceId" class="shell-result-item">
                            <div class="shell-result-device">
                              <span class="shell-status-icon" :class="{ 'shell-success': result.success, 'shell-failed': !result.success }">
                                {{ result.success ? '✓' : '✗' }}
                              </span>
                              <span class="shell-device-id">{{ result.deviceId }}</span>
                            </div>
                            <div v-if="result.stdout" class="shell-output-section">
                              <div class="shell-output-label">标准输出:</div>
                              <pre class="shell-output">{{ result.stdout }}</pre>
                            </div>
                            <div v-if="result.stderr" class="shell-output-section shell-error">
                              <div class="shell-output-label">错误输出:</div>
                              <pre class="shell-output shell-error-text">{{ result.stderr }}</pre>
                            </div>
                            <div v-if="!result.success && !result.stderr" class="shell-output-section shell-error">
                              <div class="shell-output-label">执行失败</div>
                            </div>
                          </div>
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
              </div>

              <div v-if="activeTab === 'monitor'" class="keys-page">
                <!-- 第一行: CPU 实时占用率与网络速率 -->
                <div class="scrcpy-card">
                  <div class="card-header">
                    <span class="card-icon">
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="18" height="18">
                        <polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/>
                      </svg>
                    </span>
                    <span>CPU 占用率 / 网络速率</span>
                    <span class="monitor-live-tag" v-if="monitorActive">实时刷新中</span>
                  </div>
                  <div class="card-body">
                    <div class="rate-summary">
                      <div class="rate-item">
                        <span class="rate-label">CPU 占用率</span>
                        <span class="rate-value cpu">{{ formatPercent(monitorRate?.cpu?.usagePercent) }}</span>
                      </div>
                      <div class="rate-item">
                        <span class="rate-label">下载速率</span>
                        <span class="rate-value down">{{ formatRate(activeNic?.rxBytesPerSec) }}</span>
                      </div>
                      <div class="rate-item">
                        <span class="rate-label">上传速率</span>
                        <span class="rate-value up">{{ formatRate(activeNic?.txBytesPerSec) }}</span>
                      </div>
                      <div class="rate-item">
                        <span class="rate-label">累计收发</span>
                        <span class="rate-value">
                          ↓{{ formatBytes(activeNic?.rxBytes) }} / ↑{{ formatBytes(activeNic?.txBytes) }}
                        </span>
                      </div>
                    </div>
                    <div ref="rateChartEl" class="rate-chart"></div>
                    <div v-if="monitorRate && (!monitorRate.network || monitorRate.network.length === 0)" class="monitor-empty">
                      暂无网络流量
                    </div>
                  </div>
                </div>

                <!-- 第二行: 左右两列（CPU 频率 | 内存 / 磁盘） -->
                <div class="monitor-row">
                  <!-- CPU 频率 -->
                  <div class="scrcpy-card" v-if="monitorSnapshot && monitorSnapshot.cpu && monitorSnapshot.cpu.cores.length > 0">
                    <div class="card-header">
                      <span class="card-icon">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="18" height="18">
                          <rect x="4" y="4" width="16" height="16" rx="2"/>
                          <line x1="4" y1="10" x2="20" y2="10"/>
                          <line x1="10" y1="4" x2="10" y2="20"/>
                        </svg>
                      </span>
                      <span>CPU 频率</span>
                      <span class="monitor-freq-info">
                        {{ monitorSnapshot.cpu.coreCount }} 核 · 最高 {{ formatFreq(monitorSnapshot.cpu.maxFreqKhz) }}
                      </span>
                    </div>
                    <div class="card-body">
                      <div ref="coreChartEl" class="core-chart"></div>
                    </div>
                  </div>

                  <!-- 内存 / 磁盘 -->
                  <div class="scrcpy-card">
                    <div class="card-header">
                      <span class="card-icon">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="18" height="18">
                          <rect x="4" y="4" width="16" height="16" rx="2"/>
                          <rect x="9" y="9" width="6" height="6"/>
                          <line x1="9" y1="1" x2="9" y2="4"/><line x1="15" y1="1" x2="15" y2="4"/>
                          <line x1="9" y1="20" x2="9" y2="23"/><line x1="15" y1="20" x2="15" y2="23"/>
                          <line x1="20" y1="9" x2="23" y2="9"/><line x1="20" y1="14" x2="23" y2="14"/>
                          <line x1="1" y1="9" x2="4" y2="9"/><line x1="1" y1="14" x2="4" y2="14"/>
                        </svg>
                      </span>
                      <span>内存 / 磁盘</span>
                      <button class="card-action-btn" @click="refreshMonitor" :disabled="monitorLoading">
                        {{ monitorLoading ? '刷新中...' : '刷新' }}
                      </button>
                    </div>
                    <div class="card-body">
                      <div v-if="monitorError" class="monitor-error">{{ monitorError }}</div>
                      <div v-if="monitorSnapshot" class="monitor-usage">
                        <div ref="snapshotChartEl" class="gauge-chart"></div>
                        <div class="gauge-detail-row">
                          <div class="gauge-detail">
                            <div class="detail-title">内存</div>
                            <template v-if="monitorSnapshot.memory">
                              <span>已用 {{ formatBytes(monitorSnapshot.memory.usedBytes) }}</span>
                              <span>可用 {{ formatBytes(monitorSnapshot.memory.availableBytes) }}</span>
                              <span>总计 {{ formatBytes(monitorSnapshot.memory.totalBytes) }}</span>
                              <span v-if="monitorSnapshot.memory.cachedBytes !== undefined">
                                页缓存 {{ formatBytes(monitorSnapshot.memory.cachedBytes) }}
                              </span>
                            </template>
                            <span v-else class="monitor-empty">内存数据不可用</span>
                          </div>
                          <div class="gauge-detail">
                            <div class="detail-title">磁盘</div>
                            <template v-if="monitorSnapshot.disk">
                              <span>挂载点 {{ monitorSnapshot.disk.mountPoint }}</span>
                              <span>已用 {{ formatBytes(monitorSnapshot.disk.usedBytes) }}</span>
                              <span>可用 {{ formatBytes(monitorSnapshot.disk.availableBytes) }}</span>
                              <span>总计 {{ formatBytes(monitorSnapshot.disk.totalBytes) }}</span>
                            </template>
                            <span v-else class="monitor-empty">该设备无 /data 分区，磁盘数据不可用</span>
                          </div>
                        </div>
                      </div>
                      <div v-else-if="monitorLoading" class="loading-text">加载性能数据中...</div>
                      <div v-else class="loading-text">暂无性能数据</div>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </main>
    
    <!-- 弹窗组件 -->
    <ModalDialog
      :visible="modalVisible"
      :type="modalType"
      :title="modalTitle"
      :message="modalMessage"
      :placeholder="modalPlaceholder"
      :confirm-text="modalConfirmText"
      :variant="modalVariant"
      @close="modalVisible = false"
      @confirm="handleModalConfirm"
      @cancel="handleModalCancel"
    />

    <!-- 文本文件编辑器 -->
    <FileEditorDialog
      :visible="editorVisible"
      :file-name="editorFileName"
      :initial-content="editorInitialContent"
      :loading="editorLoading"
      :load-error="editorLoadError"
      :saving="editorSaving"
      @save="handleEditorSave"
      @close="handleEditorClose"
    />
  </div>
</template>