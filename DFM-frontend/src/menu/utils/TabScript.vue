<script lang="ts" setup>
import { inject } from 'vue'

// 共享上下文由 App_Utils.vue 提供（含状态 ref 与操作函数）
const ctx = inject<any>('app-utils')!
const { SCRIPT_VARIABLE_HELP, activeTab, addScriptAdbStep, addScriptConditionStep, addScriptGestureStep, addScriptWaitStep, clearScriptSteps, exportScriptZip, loading, onScriptBranchActionChange, onScriptConditionKindChange, onScriptKeyPresetChange, onScriptStepDragEnd, onScriptStepDragLeave, onScriptStepDragOver, onScriptStepDragStart, onScriptStepDrop, onScriptTemplatePreviewError, openImportMenu, removeScriptStep, repeatRun, runCurrentSteps, saveScript, scriptApkLoading, scriptApkOptions, scriptAppLoading, scriptAppNames, scriptAppPackages, scriptCanvasRef, scriptCropMode, scriptDragging, scriptEditingStep, scriptExporting, scriptGotoTargets, scriptKeyPresetOf, scriptKeyboardActive, scriptLayoutRef, scriptOnPointerCancel, scriptOnPointerDown, scriptOnPointerMove, scriptOnPointerUp, scriptPreviewWidth, scriptRecording, scriptReleaseKeyboard, scriptRunning, scriptSaving, scriptScreenBodyRef, scriptShowMarkers, scriptSideTwoCol, scriptStepDragFrom, scriptStepDragOver, scriptStepGroups, scriptStepLabel, scriptSteps, scriptStreaming, scriptTemplateLoading, scriptTemplateOptions, scriptTemplatePreview, scriptVerify, scriptWsStatus, selectedDevice, setAllIntervals, startApp, startCropForCondition, stopApp, stopScriptRun, toggleCropMode, toggleRecording, toggleStepEdit, uninstallApp } = ctx
</script>

<template>
              <div v-if="activeTab === 'script'" class="keys-page script-page">
                <div class="script-layout" ref="scriptLayoutRef">
                  <!-- 左：屏幕流 -->
                  <div class="script-screen-panel" :style="{ width: scriptPreviewWidth ? scriptPreviewWidth + 'px' : '100%' }">
                    <div class="script-screen-header">
                      <span>屏幕预览</span>
                      <span class="script-ws-status" :class="{ live: scriptStreaming }">{{ scriptWsStatus }}</span>
                      <span v-if="scriptRecording" class="script-rec-tag">● 录制中</span>
                      <span v-if="scriptKeyboardActive" class="script-kbd-tag" title="点击画面空白处可退出键盘接管">
                        ⌨ 键盘已接管
                        <button class="script-kbd-exit" @click.stop="scriptReleaseKeyboard">退出</button>
                      </span>
                      <label class="script-switch" :class="{ on: scriptShowMarkers }">
                        <input type="checkbox" v-model="scriptShowMarkers" />
                        <span class="script-switch-track"></span>
                        <span class="script-switch-text">显示步骤</span>
                      </label>
                    </div>
                    <div class="script-screen-body" ref="scriptScreenBodyRef">
                      <canvas
                        ref="scriptCanvasRef"
                        class="script-canvas"
                        :class="{
                          recording: scriptRecording,
                          'editing-markers': scriptShowMarkers && !scriptRecording,
                          dragging: !!scriptDragging
                        }"
                        @pointerdown="scriptOnPointerDown"
                        @pointermove="scriptOnPointerMove"
                        @pointerup="scriptOnPointerUp"
                        @pointercancel="scriptOnPointerCancel"
                      ></canvas>
                      <div v-if="!scriptStreaming" class="script-screen-placeholder">
                        {{ selectedDevice ? '正在连接屏幕流...' : '请先选择设备' }}
                      </div>
                    </div>
                  </div>

                  <!-- 右：控制区 -->
                  <div class="script-side-panel" :class="{ 'two-col': scriptSideTwoCol }">
                    <!-- 上：录制控制 + 添加设备动作步骤 -->
                    <div class="script-section">
                      <div class="script-screen-header"><span>步骤功能</span></div>
                      <div class="script-record-row">
                        <button class="script-record-btn" :class="{ active: scriptRecording }" @click="toggleRecording">
                          {{ scriptRecording ? '■ 结束录制' : '● 开始录制' }}
                        </button>
                        <div v-if="scriptRecording" class="script-verify-switch">
                          <span>画面校验</span>
                          <el-switch v-model="scriptVerify" />
                        </div>
                      </div>
                      <div class="script-add-steps">
                        <span class="script-add-label">添加步骤</span>
                        <button
                          v-for="a in SCRIPT_GESTURE_STEPS"
                          :key="a.type"
                          class="script-add-btn"
                          :disabled="scriptRunning"
                          @click="addScriptGestureStep(a.type)"
                        >{{ a.icon }} {{ a.label }}</button>
                        <button
                          v-for="a in SCRIPT_ADB_STEPS"
                          :key="a.type"
                          class="script-add-btn"
                          :disabled="scriptRunning"
                          @click="addScriptAdbStep(a.type)"
                        >{{ a.icon }} {{ a.label }}</button>
                        <button
                          class="script-add-btn"
                          :disabled="scriptRunning"
                          title="新增等待组件步骤，在步骤参数里从已有模板图中手动选择"
                          @click="addScriptWaitStep"
                        >🖼 等待组件(选图)</button>
                        <button
                          class="script-add-btn"
                          :class="{ cropping: scriptCropMode }"
                          :disabled="!scriptStreaming || scriptRunning"
                          title="开启后在画面上框住组件，松开保存为模板并添加等待步骤"
                          @click="toggleCropMode"
                        >{{ scriptCropMode ? '✕ 取消框选' : '👁 等待组件(框选)' }}</button>
                        <button
                          class="script-add-btn"
                          :disabled="scriptRunning"
                          title="按应用/组件/Shell 输出/设备时刻判断，并按成立与否跳转"
                          @click="addScriptConditionStep"
                        >🧭 判断步骤</button>
                      </div>
                    </div>

                    <!-- 下：脚本列表 + 底部按钮 -->
                    <div class="script-section script-list-section">
                      <div class="script-list-header">
                        <span>
                          <el-tooltip placement="bottom-start">
                            <template #content>
                              <div class="script-help-content">{{ SCRIPT_VARIABLE_HELP }}</div>
                            </template>
                            <span class="script-help-icon">i</span>
                          </el-tooltip>脚本步骤（{{ scriptSteps.length }}）</span>
                        <div class="script-header-actions">
                          <button
                            class="script-mini-btn"
                            @click="setAllIntervals"
                            :disabled="scriptSteps.length === 0 || scriptRunning"
                            title="把所有步骤的执行间隔统一设置为同一个值"
                          >统一间隔</button>
                          <button
                            class="script-mini-btn"
                            @click="openImportMenu"
                            :disabled="scriptSaving || scriptRunning"
                          >导入</button>
                          <button class="script-mini-btn" @click="clearScriptSteps" :disabled="scriptSteps.length === 0">清空</button>
                        </div>
                      </div>
                      <div class="script-steps-list">
                        <div v-if="scriptSteps.length === 0" class="script-empty">
                          录制模式下在左侧屏幕上操作即可记录步骤
                        </div>
                        <template v-for="group in scriptStepGroups" :key="group.key">
                          <div class="script-step-group-header">{{ group.label }}（{{ group.items.length }}）</div>
                          <div
                            v-for="{ step, idx } in group.items"
                            :key="idx"
                            class="script-step-item"
                          :class="{
                            selected: scriptEditingStep === idx,
                            'drag-over': scriptStepDragOver === idx && scriptStepDragFrom !== idx,
                            dragging: scriptStepDragFrom === idx,
                          }"
                          draggable="true"
                          @click="toggleStepEdit(idx)"
                          @dragstart="onScriptStepDragStart(idx, $event)"
                          @dragover="onScriptStepDragOver(idx, $event)"
                          @dragleave="onScriptStepDragLeave(idx)"
                          @drop="onScriptStepDrop(idx, $event)"
                          @dragend="onScriptStepDragEnd"
                        >
                          <span class="script-step-idx">{{ idx + 1 }}</span>
                          <span class="script-step-desc">{{ scriptStepLabel(step) }}</span>
                          <span class="script-step-meta">{{ step.interval }}ms × {{ step.count }}次</span>
                          <button class="script-step-del" @click.stop="removeScriptStep(idx)">×</button>

                          <!-- 选中后展开该步的执行参数 -->
                          <div v-if="scriptEditingStep === idx" class="script-step-config" @click.stop>
                            <div v-if="step.type === 'input'" class="script-config-row">
                              <span>输入内容</span>
                              <el-input v-model="step.text" class="ep-config-input" />
                            </div>
                            <!-- 等待组件步骤的专属参数 -->
                            <div v-if="step.type === 'waitFor'" class="script-config-row">
                              <span>模板</span>
                              <el-select v-model="step.template" class="ep-config-select" placeholder="选择模板" :loading="scriptTemplateLoading">
                                <el-option v-for="t in scriptTemplateOptions" :key="t.name" :value="t.name" :label="`${t.name}（${t.width}x${t.height}）`" />
                              </el-select>
                            </div>
                            <div v-if="step.type === 'waitFor' && step.template && scriptTemplatePreview" class="script-config-row script-template-preview-row">
                              <img
                                :key="scriptTemplatePreview"
                                :src="scriptTemplatePreview"
                                class="script-template-preview"
                                alt="模板预览"
                                @error="onScriptTemplatePreviewError"
                              />
                            </div>
                            <div v-if="step.type === 'waitFor'" class="script-config-row">
                              <span>匹配阈值</span>
                              <el-input-number v-model="step.threshold" class="ep-config-num" :min="0.5" :max="1" :step="0.05" :precision="2" controls-position="right" />
                            </div>
                            <div v-if="step.type === 'waitFor'" class="script-config-row">
                              <span>超时(秒)</span>
                              <el-input-number v-model="step.timeout" class="ep-config-num" :min="1" :max="600" controls-position="right" />
                            </div>
                            <!-- 判断步骤的专属参数 -->
                            <template v-if="step.type === 'condition'">
                              <div class="script-config-row">
                                <span>判断类型</span>
                                <el-select v-model="step.kind" class="ep-config-select" @change="onScriptConditionKindChange(step, $event)">
                                  <el-option v-for="k in SCRIPT_CONDITION_KINDS" :key="k.kind" :value="k.kind" :label="k.label" />
                                </el-select>
                              </div>
                              <div class="script-config-row">
                                <span>条件取反</span>
                                <el-switch v-model="step.negate" />
                              </div>
                              <div v-if="step.kind === 'appInstalled'" class="script-config-row">
                                <span>应用包名</span>
                                <el-input v-model="step.packageName" class="ep-config-input" placeholder="com.example.app" />
                              </div>
                              <div v-if="step.kind === 'template'" class="script-config-row">
                                <span>模板</span>
                                <el-select v-model="step.template" class="ep-config-select" placeholder="选择模板" :loading="scriptTemplateLoading">
                                  <el-option v-for="t in scriptTemplateOptions" :key="t.name" :value="t.name" :label="`${t.name}（${t.width}x${t.height}）`" />
                                </el-select>
                                <button
                                  class="script-add-btn"
                                  :disabled="!scriptStreaming || scriptRunning"
                                  title="在画面上框住组件，保存为模板并设为当前判断步骤的模板"
                                  @click="startCropForCondition(step)"
                                >📷 现截图</button>
                              </div>
                              <div v-if="step.kind === 'template'" class="script-config-row">
                                <span>匹配阈值</span>
                                <el-input-number v-model="step.threshold" class="ep-config-num" :min="0.5" :max="1" :step="0.05" :precision="2" controls-position="right" />
                              </div>
                              <div v-if="step.kind === 'shell'" class="script-config-row">
                                <span>Shell 命令</span>
                                <el-input v-model="step.command" class="ep-config-input" placeholder="getprop ro.build.version.release" />
                              </div>
                              <div v-if="step.kind === 'shell'" class="script-config-row">
                                <span>输出包含</span>
                                <el-input v-model="step.expected" class="ep-config-input" placeholder="13" />
                              </div>
                              <div v-if="step.kind === 'time'" class="script-config-row">
                                <span>目标时刻</span>
                                <el-input v-model="step.targetTime" class="ep-config-input" placeholder="09:30 或 09:30:00" />
                              </div>
                              <div v-if="step.kind === 'time'" class="script-config-row">
                                <span>等待上限(秒)</span>
                                <el-input-number v-model="step.timeout" class="ep-config-num" :min="1" :max="86400" controls-position="right" />
                              </div>
                              <div class="script-config-row">
                                <span>成立时</span>
                                <el-select v-model="step.onTrue.action" class="ep-config-select" @change="onScriptBranchActionChange(step.onTrue, idx)">
                                  <el-option
                                    v-for="a in SCRIPT_BRANCH_ACTIONS"
                                    :key="a.action"
                                    :value="a.action"
                                    :label="a.label"
                                    :disabled="a.action === 'goto' && scriptGotoTargets(idx).length === 0"
                                  />
                                </el-select>
                              </div>
                              <div v-if="step.onTrue.action === 'goto'" class="script-config-row">
                                <span>跳到</span>
                                <el-select v-model="step.onTrue.target" class="ep-config-select" placeholder="选择目标步骤">
                                  <el-option v-for="n in scriptGotoTargets(idx)" :key="n" :value="n" :label="`第 ${n} 步：${scriptStepLabel(scriptSteps[n - 1])}`" />
                                </el-select>
                              </div>
                              <div class="script-config-row">
                                <span>不成立时</span>
                                <el-select v-model="step.onFalse.action" class="ep-config-select" @change="onScriptBranchActionChange(step.onFalse, idx)">
                                  <el-option
                                    v-for="a in SCRIPT_BRANCH_ACTIONS"
                                    :key="a.action"
                                    :value="a.action"
                                    :label="a.label"
                                    :disabled="a.action === 'goto' && scriptGotoTargets(idx).length === 0"
                                  />
                                </el-select>
                              </div>
                              <div v-if="step.onFalse.action === 'goto'" class="script-config-row">
                                <span>跳到</span>
                                <el-select v-model="step.onFalse.target" class="ep-config-select" placeholder="选择目标步骤">
                                  <el-option v-for="n in scriptGotoTargets(idx)" :key="n" :value="n" :label="`第 ${n} 步：${scriptStepLabel(scriptSteps[n - 1])}`" />
                                </el-select>
                              </div>
                            </template>
                            <!-- 设备动作类步骤的专属参数 -->
                            <div v-if="step.type === 'installApk'" class="script-config-row">
                              <span>APK 文件</span>
                              <el-select v-model="step.apkName" class="ep-config-select" placeholder="选择 APK" :loading="scriptApkLoading">
                                <el-option v-for="a in scriptApkOptions" :key="a.name" :value="a.name" :label="a.name" />
                              </el-select>
                            </div>
                            <div v-if="step.type === 'deleteFolder'" class="script-config-row">
                              <span>文件夹路径</span>
                              <el-input v-model="step.path" class="ep-config-input" placeholder="/sdcard/xxx" />
                            </div>
                            <div v-if="step.type === 'startApp' || step.type === 'stopApp' || step.type === 'uninstallApp'" class="script-config-row">
                              <span>应用</span>
                              <el-select
                                v-model="step.packageName"
                                class="ep-config-select"
                                placeholder="选择应用"
                                filterable
                                :loading="scriptAppLoading"
                              >
                                <el-option v-for="p in scriptAppPackages" :key="p" :value="p" :label="scriptAppNames[p] || p" />
                              </el-select>
                            </div>
                            <div v-if="step.type === 'keyevent'" class="script-config-row">
                              <span>按键</span>
                              <el-select
                                :model-value="scriptKeyPresetOf(step)"
                                class="ep-config-select"
                                @change="onScriptKeyPresetChange(step, $event)"
                              >
                                <el-option v-for="k in SCRIPT_COMMON_KEYS" :key="k.code" :value="String(k.code)" :label="`${k.name}（${k.code}）`" />
                                <el-option value="custom" label="自定义键值..." />
                              </el-select>
                            </div>
                            <div v-if="step.type === 'keyevent' && scriptKeyPresetOf(step) === 'custom'" class="script-config-row">
                              <span>键值</span>
                              <el-input-number v-model.number="step.keycode" class="ep-config-num" :min="0" :max="65535" controls-position="right" />
                            </div>
                            <div v-if="step.type === 'volume'" class="script-config-row">
                              <span>音量动作</span>
                              <el-select v-model="step.action" class="ep-config-select">
                                <el-option value="up" label="音量+" />
                                <el-option value="down" label="音量-" />
                                <el-option value="mute" label="静音" />
                              </el-select>
                            </div>
                            <div v-if="step.type === 'volume' && step.action === 'mute'" class="script-config-row">
                              <span>音源</span>
                              <el-select v-model="step.stream" class="ep-config-select">
                                <el-option value="media" label="媒体" />
                                <el-option value="ring" label="铃声" />
                                <el-option value="alarm" label="闹钟" />
                                <el-option value="notification" label="通知" />
                              </el-select>
                            </div>
                            <div v-if="step.type === 'tts'" class="script-config-row">
                              <span>朗读文本</span>
                              <el-input v-model="step.text" class="ep-config-input" placeholder="输入设备要朗读的文本" />
                            </div>
                            <div class="script-config-row">
                              <span>{{ step.type === 'condition' ? '轮询/步间(ms)' : '执行间隔(ms)' }}</span>
                              <el-input-number v-model="step.interval" class="ep-config-num" :min="0" :max="600000" controls-position="right" />
                            </div>
                            <div v-if="step.type !== 'condition'" class="script-config-row">
                              <span>执行次数</span>
                              <el-input-number v-model="step.count" class="ep-config-num" :min="1" :max="99" controls-position="right" />
                            </div>
                            <div v-if="step.type === 'input'" class="script-config-hint">
                              输入到设备当前聚焦的输入框，通常放在一次点击之后
                            </div>
                            <div v-else-if="step.type === 'waitFor'" class="script-config-hint">
                              反复截屏搜索模板，组件出现才继续下一步；超时未出现判失败
                            </div>
                            <div v-else-if="step.type === 'condition'" class="script-config-hint">
                              单次判断后按「成立/不成立」分支；时间条件按上面的间隔轮询设备时钟（当天目标时刻已过则立即成立），跳转目标用绝对步骤序号，排序或增删步骤后请重新核对
                            </div>
                            <div v-else-if="step.type === 'tts'" class="script-config-hint">
                              通过探针 0x01 命令让设备朗读上方文本；需先在设备列表完成探针安装通讯，朗读最长约 90 秒
                            </div>
                            <div v-else-if="SCRIPT_ADB_STEPS.some(a => a.type === step.type)" class="script-config-hint">
                              设备动作步骤直接走 adb 命令，无需屏幕录制；可放在任意位置
                            </div>
                            <div v-else class="script-config-hint">
                              开启「显示步骤」后可在画面上拖动该步骤的位置
                            </div>
                          </div>
                          </div>
                        </template>
                      </div>
                      <div class="script-actions">
                        <button class="script-action-btn" @click="saveScript" :disabled="scriptSaving">保存</button>
                        <button class="script-action-btn" @click="runCurrentSteps" :disabled="scriptRunning || scriptSaving">执行</button>
                        <button v-if="scriptRunning" class="script-action-btn script-stop-btn" @click="stopScriptRun">停止</button>
                        <button v-if="!scriptRunning" class="script-action-btn" @click="repeatRun" :disabled="scriptSaving">重复</button>
                        <button class="script-action-btn" @click="exportScriptZip" :disabled="scriptExporting || scriptSaving" title="导出为 zip 包（脚本 + 引用的 APK/模板图），拷到其他电脑导入即可用">导出</button>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
</template>
