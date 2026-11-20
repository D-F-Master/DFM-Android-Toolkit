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
const { API_BASE, activeTab, currentPath, editorFileName, editorInitialContent, editorLoadError, editorLoading, editorTarget, editorVisible, fetchFiles, fileLoading, files, formatBytes, hideProgressNotification, imageViewerIndex, imageViewerList, loadViewerFile, loading, previewKindOf, selectedDevice, selectedFiles, showConfirm, showMessage, showNotification, showProgressNotification, showPrompt } = ctx
type FileSortKey = 'name' | 'size' | 'time'
const fileSortKey = ref<FileSortKey>('name')
const fileSortAsc = ref(true)
const sortedFiles = computed(() => {
  const list = [...files.value]
  const dirFirst = (a: FileInfo, b: FileInfo) => (a.isDirectory === b.isDirectory ? 0 : a.isDirectory ? -1 : 1)
  const cmp: Record<FileSortKey, (a: FileInfo, b: FileInfo) => number> = {
    name: (a, b) => a.name.localeCompare(b.name, 'zh-CN'),
    size: (a, b) => (Number(a.size) || 0) - (Number(b.size) || 0),
    time: (a, b) => (a.mtime || '').localeCompare(b.mtime || '')
  }
  list.sort((a, b) => dirFirst(a, b) || (fileSortAsc.value ? cmp[fileSortKey.value](a, b) : cmp[fileSortKey.value](b, a)))
  return list
})
const toggleFileSort = (key: FileSortKey) => {
  if (fileSortKey.value === key) {
    fileSortAsc.value = !fileSortAsc.value
  } else {
    fileSortKey.value = key
    fileSortAsc.value = true
  }
}

// 文件多选：勾选的条目路径集合（用于批量删除/移动）
const selectedFileCount = computed(() => selectedFiles.value.size)
const fileAllSelected = computed(() => files.value.length > 0 && files.value.every(f => selectedFiles.value.has(f.path)))
const fileSomeSelected = computed(() => selectedFiles.value.size > 0 && !fileAllSelected.value)

const toggleFileSelection = (path: string) => {
  const next = new Set(selectedFiles.value)
  if (next.has(path)) {
    next.delete(path)
  } else {
    next.add(path)
  }
  selectedFiles.value = next
}

const toggleSelectAllFiles = () => {
  if (fileAllSelected.value) {
    selectedFiles.value = new Set()
  } else {
    const next = new Set(selectedFiles.value)
    files.value.forEach(f => next.add(f.path))
    selectedFiles.value = next
  }
}

const clearFileSelection = () => {
  selectedFiles.value = new Set()
}

const dbViewerFile = ref<{ path: string; name: string } | null>(null)
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

// SQLite 数据库文件判断（.db / .db3 / .sqlite / .sqlite3）
const isSqliteFile = (file: { name: string; isDirectory: boolean }) =>
  !file.isDirectory && /\.(db|db3|sqlite|sqlite3)$/i.test(file.name)

const openSqliteViewer = (file: { path: string; name: string }) => {
  if (!selectedDevice.value) {
    showMessage('请先选择设备')
    return
  }
  dbViewerFile.value = { path: file.path, name: file.name }
}

const importFile = async () => {
  if (!selectedDevice.value) {
    showMessage('请先选择设备')
    return
  }

  const input = document.createElement('input')
  input.type = 'file'
  input.multiple = true

  input.onchange = async (e: Event) => {
    const target = e.target as HTMLInputElement
    const picked = Array.from(target.files || [])
    if (picked.length === 0) return

    // 逐个上传（adb push 本身串行，进度通知逐个显示），任一失败继续后面的文件
    let okCount = 0
    const failed: string[] = []
    const startedAt = Date.now()
    for (const file of picked) {
      const formData = new FormData()
      formData.append('file', file)
      formData.append('deviceId', selectedDevice.value)
      formData.append('remotePath', currentPath.value + '/')

      showProgressNotification(`正在导入 ${file.name}（${formatBytes(file.size)}）… ${okCount + 1}/${picked.length}`)
      try {
        const response = await axios.post(
          `${API_BASE.value}/api/device/files/upload`,
          formData,
          { headers: { 'Content-Type': 'multipart/form-data' } }
        )
        if (response.data.code === 200) {
          okCount++
        } else {
          failed.push(`${file.name}: ${response.data.message || '未知错误'}`)
        }
      } catch (error) {
        console.error('导入文件失败:', error)
        failed.push(`${file.name}: 网络异常`)
      }
    }
    hideProgressNotification()
    if (failed.length === 0) {
      showNotification(`已导入 ${okCount} 个文件，耗时 ${Math.round((Date.now() - startedAt) / 1000)}s`)
    } else {
      showNotification(`导入完成 ${okCount}/${picked.length}，失败：\n${failed.join('\n')}`, 'error')
    }
    fetchFiles(selectedDevice.value, currentPath.value)
  }

  input.click()
}

// ==================== 文本文件在线编辑 ====================
// 链路：download 拉到本地临时文件 → preload 的 services.readFile 读文本 →
//       编辑后构造 File 走 upload 覆盖回设备原路径（全程无需新后端接口）
const EDITABLE_EXTS = ['txt', 'log', 'json', 'xml', 'html', 'htm', 'css', 'js', 'mjs', 'cjs', 'ts', 'md', 'markdown', 'py', 'conf', 'cfg', 'ini', 'sh', 'prop', 'properties', 'csv', 'yml', 'yaml', 'sql', 'java', 'c', 'cpp', 'h', 'gradle', 'env']
const EDIT_MAX_BYTES = 10 * 1024 * 1024 // 10MB，超过不支持在线编辑

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
    editorTarget.value = { remotePath: file.path }
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

const IMAGE_MAX_BYTES = 20 * 1024 * 1024 // 与后端 IMAGE_PREVIEW_MAX_BYTES 保持一致

// 预览类型：图片（缩放/拖拽查看）或视频（浏览器播放器），都不支持返回 ''
const isViewableFile = (file: { name: string; isDirectory: boolean; size?: string }) => {
  if (!previewKindOf(file)) return false
  const sizeNum = Number(file.size)
  return isNaN(sizeNum) || sizeNum <= IMAGE_MAX_BYTES
}

// 图片可预览判定（保留给可能的外部引用；主流程走 previewKindOf）
const viewDisabledReason = (file: { name: string; isDirectory: boolean; size?: string }): string => {
  if (file.isDirectory) return '目录不支持预览'
  const kind = previewKindOf(file)
  if (!kind) return '不支持预览该类型的文件'
  const sizeNum = Number(file.size)
  if (!isNaN(sizeNum) && sizeNum > IMAGE_MAX_BYTES) {
    return `文件过大（${formatBytes(sizeNum)}），超过 ${formatBytes(IMAGE_MAX_BYTES, 0)} 不支持在线预览，可先导出`
  }
  return '不支持预览'
}

const openImageViewer = async (file: { path: string; name: string; size?: string }) => {
  if (!selectedDevice.value) return
  if (!isViewableFile(file)) {
    showMessage('该文件类型或大小不支持在线预览')
    return
  }

  // 收集当前目录的可预览文件（图片+视频），用于上一个/下一个导航
  imageViewerList.value = files.value.filter((f: any) => isViewableFile(f))
  imageViewerIndex.value = Math.max(0, imageViewerList.value.findIndex((f: any) => f.path === file.path))

  await loadViewerFile(file)
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
        showMessage('删除成功')
        fetchFiles(selectedDevice.value, currentPath.value)
      } else {
        showMessage('删除失败: ' + (response.data.message || '未知错误'))
      }
    } catch (error: any) {
      console.error('删除失败:', error)
      showMessage('删除失败: ' + (error.message || '网络异常'))
    }
  }, '确认删除')
}

// 重命名/移动文件或文件夹
const renameFile = (file: { path: string; name: string }) => {
  if (!selectedDevice.value) return

  showPrompt(
    `输入新名称或完整新路径（当前：${file.path}）`,
    async (newPath: string) => {
      const trimmed = (newPath || '').trim()
      if (!trimmed) {
        showMessage('新路径不能为空')
        return
      }
      try {
        const response = await axios.post(`${API_BASE.value}/api/device/files/rename`, {
          deviceId: selectedDevice.value,
          path: file.path,
          newPath: trimmed
        })
        if (response.data.code === 200) {
          showMessage('重命名成功')
          fetchFiles(selectedDevice.value, currentPath.value)
        } else {
          showMessage('重命名失败: ' + (response.data.message || '未知错误'))
        }
      } catch (error: any) {
        console.error('重命名失败:', error)
        showMessage('重命名失败: ' + (error.message || '网络异常'))
      }
    },
    file.name,
    '重命名/移动'
  )
}

// 批量删除选中的文件/文件夹
const batchDeleteSelected = () => {
  if (!selectedDevice.value || selectedFileCount.value === 0) return

  const paths = [...selectedFiles.value]
  showConfirm(`确定要删除选中的 ${paths.length} 项吗？（删除后不可恢复）`, async () => {
    let okCount = 0
    const failed: string[] = []
    for (const p of paths) {
      try {
        const response = await axios.post(`${API_BASE.value}/api/device/files/delete`, {
          deviceId: selectedDevice.value,
          path: p
        })
        if (response.data.code === 200) {
          okCount++
        } else {
          failed.push(`${p}: ${response.data.message || '未知错误'}`)
        }
      } catch (error: any) {
        failed.push(`${p}: ${error.message || '网络异常'}`)
      }
    }
    if (failed.length === 0) {
      showMessage(`已删除 ${okCount} 项`)
    } else {
      showMessage(`删除完成 ${okCount}/${paths.length}，失败：\n${failed.join('\n')}`, '删除结果')
    }
    fetchFiles(selectedDevice.value, currentPath.value)
  }, '确认删除')
}

// 批量移动选中的文件/文件夹到指定目录
const batchMoveSelected = () => {
  if (!selectedDevice.value || selectedFileCount.value === 0) return

  const paths = [...selectedFiles.value]
  showPrompt(
    `输入目标目录路径（如 /sdcard/DCIM），选中的 ${paths.length} 项将移动到该目录下`,
    async (destDir: string) => {
      const dir = (destDir || '').trim()
      if (!dir) {
        showMessage('目标目录不能为空')
        return
      }
      const base = dir.replace(/\/+$/, '')
      let okCount = 0
      const failed: string[] = []
      for (const p of paths) {
        const name = p.substring(p.lastIndexOf('/') + 1)
        try {
          const response = await axios.post(`${API_BASE.value}/api/device/files/rename`, {
            deviceId: selectedDevice.value,
            path: p,
            newPath: `${base}/${name}`
          })
          if (response.data.code === 200) {
            okCount++
          } else {
            failed.push(`${name}: ${response.data.message || '未知错误'}`)
          }
        } catch (error: any) {
          failed.push(`${name}: ${error.message || '网络异常'}`)
        }
      }
      if (failed.length === 0) {
        showMessage(`已移动 ${okCount} 项到 ${base}`)
      } else {
        showMessage(`移动完成 ${okCount}/${paths.length}，失败：\n${failed.join('\n')}`, '移动结果')
      }
      fetchFiles(selectedDevice.value, currentPath.value)
    },
    currentPath.value,
    '移动到目录'
  )
}

// 批量复制选中的文件/文件夹到指定目录（保留原件）
const batchCopySelected = () => {
  if (!selectedDevice.value || selectedFileCount.value === 0) return

  const paths = [...selectedFiles.value]
  showPrompt(
    `输入目标目录路径（如 /sdcard/DCIM），选中的 ${paths.length} 项将复制到该目录下（保留原件）`,
    async (destDir: string) => {
      const dir = (destDir || '').trim()
      if (!dir) {
        showMessage('目标目录不能为空')
        return
      }
      const base = dir.replace(/\/+$/, '')
      let okCount = 0
      const failed: string[] = []
      for (const p of paths) {
        const name = p.substring(p.lastIndexOf('/') + 1)
        try {
          const response = await axios.post(`${API_BASE.value}/api/device/files/copy`, {
            deviceId: selectedDevice.value,
            path: p,
            newPath: `${base}/${name}`
          })
          if (response.data.code === 200) {
            okCount++
          } else {
            failed.push(`${name}: ${response.data.message || '未知错误'}`)
          }
        } catch (error: any) {
          failed.push(`${name}: ${error.message || '网络异常'}`)
        }
      }
      if (failed.length === 0) {
        showMessage(`已复制 ${okCount} 项到 ${base}`)
      } else {
        showMessage(`复制完成 ${okCount}/${paths.length}，失败：\n${failed.join('\n')}`, '复制结果')
      }
      fetchFiles(selectedDevice.value, currentPath.value)
    },
    currentPath.value,
    '复制到目录'
  )
}

</script>

<template>
              <div v-if="activeTab === 'files'" class="keys-page">
                <!-- SQLite 数据库查看模式：整体替换文件列表 -->
                <SqliteDbViewer
                  v-if="dbViewerFile"
                  :device-id="selectedDevice"
                  :remote-path="dbViewerFile.path"
                  :file-name="dbViewerFile.name"
                  @close="dbViewerFile = null"
                />
                <div v-else class="scrcpy-card">
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
                      <span class="file-batch-info">{{ selectedFileCount > 0 ? '已选中 ' + selectedFileCount + ' 项' : '未选中' }}</span>
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
                      <button v-if="selectedFileCount > 0" class="toolbar-btn danger" @click="batchDeleteSelected" title="删除选中的文件/文件夹">
                        <svg viewBox="0 0 1024 1024" width="14" height="14" fill="currentColor">
                          <path d="M202.666667 256h-42.666667a32 32 0 0 1 0-64h704a32 32 0 0 1 0 64H266.666667v565.333333a53.333333 53.333333 0 0 0 53.333333 53.333334h384a53.333333 53.333333 0 0 0 53.333333-53.333334V352a32 32 0 0 1 64 0v469.333333c0 64.8-52.533333 117.333333-117.333333 117.333334H320c-64.8 0-117.333333-52.533333-117.333333-117.333334V256z m224-106.666667a32 32 0 0 1 0-64h170.666666a32 32 0 0 1 0 64H426.666667z m-32 288a32 32 0 0 1 64 0v256a32 32 0 0 1-64 0V437.333333z m170.666666 0a32 32 0 0 1 64 0v256a32 32 0 0 1-64 0V437.333333z"/>
                        </svg>
                        删除选中
                      </button>
                      <button v-if="selectedFileCount > 0" class="toolbar-btn" @click="batchMoveSelected" title="把选中的文件/文件夹移动到指定目录">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14">
                          <polyline points="15 10 20 15 15 20"/><path d="M4 4v7a4 4 0 0 0 4 4h12"/>
                        </svg>
                        移动到…
                      </button>
                      <button v-if="selectedFileCount > 0" class="toolbar-btn" @click="batchCopySelected" title="把选中的文件/文件夹复制到指定目录（保留原件）">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14">
                          <rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/>
                        </svg>
                        复制到…
                      </button>
                      <button v-if="selectedFileCount > 0" class="toolbar-btn" @click="clearFileSelection" title="取消选择">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14">
                          <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
                        </svg>
                        取消选择
                      </button>
                    </div>
                    <div v-if="fileLoading" class="loading-text" style="padding: 24px; text-align: center; border-top: 1px solid #f0f0f0;">加载文件列表中...</div>
                    <div v-else-if="files.length === 0" class="loading-text" style="padding: 24px; text-align: center; border-top: 1px solid #f0f0f0;">无文件</div>
                    <div v-else class="file-list">
                      <div class="file-list-header" @click.prevent>
                        <div class="file-item-check">
                          <input type="checkbox" :checked="fileAllSelected" :indeterminate="fileSomeSelected" @click.stop @change="toggleSelectAllFiles" title="全选/取消全选" />
                        </div>
                        <div class="file-item-icon"></div>
                        <div class="file-item-name file-sortable" :class="{ 'sort-active': fileSortKey === 'name' }" @click.stop="toggleFileSort('name')" title="按名称排序">名称 {{ fileSortKey === 'name' ? (fileSortAsc ? '↑' : '↓') : '' }}</div>
                        <div class="file-item-size file-sortable" :class="{ 'sort-active': fileSortKey === 'size' }" @click.stop="toggleFileSort('size')" title="按大小排序">大小 {{ fileSortKey === 'size' ? (fileSortAsc ? '↑' : '↓') : '' }}</div>
                        <div class="file-item-actions file-sortable" :class="{ 'sort-active': fileSortKey === 'time' }" @click.stop="toggleFileSort('time')" title="按修改时间排序">时间 {{ fileSortKey === 'time' ? (fileSortAsc ? '↑' : '↓') : '' }}</div>
                      </div>
                      <div v-for="file in sortedFiles" :key="file.path" class="file-item" @click="file.isDirectory && navigateTo(file.path)">
                        <div class="file-item-check" @click.stop>
                          <input type="checkbox" :checked="selectedFiles.has(file.path)" @change="toggleFileSelection(file.path)" title="选择该项" />
                        </div>
                        <div class="file-item-icon">{{ file.isDirectory ? '📁' : '📄' }}</div>
                        <div class="file-item-name" :class="{ 'is-dir': file.isDirectory }">{{ file.name }}</div>
                        <div class="file-item-size">
                          <template v-if="!file.isDirectory">{{ formatBytes(Number(file.size)) }}</template>
                          <template v-else-if="file.size">共 {{ file.size }} 项</template>
                        </div>
                        <div class="file-item-time">{{ file.mtime || '' }}</div>
                        <div class="file-item-actions">
                          <button v-if="previewKindOf(file) === 'image'" class="file-act-btn export" @click.stop="openImageViewer(file)" title="查看图片">
                            <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2">
                              <rect x="3" y="3" width="18" height="18" rx="2"/>
                              <circle cx="8.5" cy="8.5" r="1.5"/>
                              <polyline points="21 15 16 10 5 21"/>
                            </svg>
                          </button>
                          <button v-else-if="previewKindOf(file) === 'video'" class="file-act-btn export" @click.stop="openImageViewer(file)" title="播放视频">
                            <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2">
                              <rect x="2" y="4" width="20" height="16" rx="2"/>
                              <polygon points="10 9 15 12 10 15 10 9" fill="currentColor" stroke="none"/>
                            </svg>
                          </button>
                          <button v-else-if="!file.isDirectory" class="file-act-btn export" disabled :title="viewDisabledReason(file)" style="opacity:0.35;">
                            <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2">
                              <rect x="3" y="3" width="18" height="18" rx="2"/>
                              <circle cx="8.5" cy="8.5" r="1.5"/>
                              <polyline points="21 15 16 10 5 21"/>
                            </svg>
                          </button>
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
                          <button v-if="isSqliteFile(file)" class="file-act-btn export" @click.stop="openSqliteViewer(file)" title="查看数据库">
                            <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2">
                              <ellipse cx="12" cy="5" rx="9" ry="3"/>
                              <path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"/>
                              <path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"/>
                            </svg>
                          </button>
                          <button class="file-act-btn export" @click.stop="renameFile(file)" title="重命名/移动">
                            <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2">
                              <path d="M11 4H4a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-5"/>
                              <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/>
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
</template>
