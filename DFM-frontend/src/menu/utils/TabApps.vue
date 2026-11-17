<script lang="ts" setup>
import { inject } from 'vue'

// 共享上下文由 App_Utils.vue 提供（含状态 ref 与操作函数）
const ctx = inject<any>('app-utils')!
const { activeTab, appLoading, apps, extractApk, installApp, loading, openApkRepo, startApp, stopApp, uninstallApp } = ctx
</script>

<template>
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
                    <div class="apps-header-btns">
                      <button class="card-action-btn" @click="openApkRepo">🗄️ APK仓库</button>
                      <button class="card-action-btn" @click="installApp">+ 安装</button>
                    </div>
                  </div>
                  <div
                    class="card-body"
                    style="padding: 0;"
                  >
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
</template>
