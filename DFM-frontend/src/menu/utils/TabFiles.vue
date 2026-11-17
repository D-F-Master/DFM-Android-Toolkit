<script lang="ts" setup>
import { inject } from 'vue'
import SqliteDbViewer from '../../components/SqliteDbViewer.vue'

// 共享上下文由 App_Utils.vue 提供（含状态 ref 与操作函数）
const ctx = inject<any>('app-utils')!
const { activeTab, batchDeleteSelected, batchMoveSelected, clearFileSelection, createFile, createFolder, currentPath, dbViewerFile, deleteFile, editDisabledReason, exportFile, fetchFiles, fileAllSelected, fileLoading, fileSomeSelected, fileSortAsc, fileSortKey, files, formatBytes, goBack, importFile, isEditableTextFile, isSqliteFile, loading, navigateTo, openFileEditor, openImageViewer, openSqliteViewer, previewKindOf, renameFile, selectedDevice, selectedFileCount, selectedFiles, sortedFiles, toggleFileSelection, toggleFileSort, toggleSelectAllFiles, viewDisabledReason } = ctx
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
