<template>
  <transition name="rup-fade">
    <div
      v-if="visible"
      class="rup-panel"
      :class="{ 'is-minimized': minimized }"
      role="dialog"
      aria-label="资源上传面板"
      data-testid="tv3-resource-upload-panel"
    >
      <!-- 头部 -->
      <header class="rup-head">
        <div class="rup-head__left">
          <div class="rup-head__icon">
            <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
              <polyline points="17 8 12 3 7 8"/>
              <line x1="12" y1="3" x2="12" y2="15"/>
            </svg>
          </div>
          <div class="rup-head__txt">
            <b>资源上传</b>
            <span v-if="hasTasks">上传中 {{ uploadingCount }}/{{ files.length }} 个文件</span>
            <span v-else>拖拽或点击选择文件</span>
          </div>
        </div>
        <div class="rup-head__ops">
          <button
            v-if="hasTasks && !minimized"
            class="rup-head__op"
            type="button"
            :title="minimized ? '展开' : '最小化'"
            data-testid="rup-minimize-btn"
            @click="minimized = !minimized"
          >
            <svg v-if="minimized" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <polyline points="18 15 12 9 6 15"/>
            </svg>
            <svg v-else viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <polyline points="6 9 12 15 18 9"/>
            </svg>
          </button>
          <button
            class="rup-head__close"
            type="button"
            aria-label="关闭"
            data-testid="rup-close-btn"
            @click="handleClose"
          >
            <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <line x1="18" y1="6" x2="6" y2="18"/>
              <line x1="6" y1="6" x2="18" y2="18"/>
            </svg>
          </button>
        </div>
      </header>

      <!-- 最小化状态：只显示总进度条 -->
      <div v-if="minimized && hasTasks" class="rup-minibar" @click="minimized = false">
        <div class="rup-minibar__progress">
          <div class="rup-progress__bar" :style="{ width: totalProgress + '%' }" />
        </div>
        <div class="rup-minibar__info">
          <span>{{ totalProgress }}%</span>
          <span class="rup-minibar__sep">·</span>
          <span>{{ uploadingCount }}/{{ files.length }} 上传中</span>
          <span class="rup-minibar__sep">·</span>
          <span>{{ remainingTime }}</span>
        </div>
      </div>

      <transition name="rup-expand">
        <div v-show="!minimized" class="rup-body">
          <!-- 拖拽上传区域 -->
          <div
            class="rup-dropzone"
            :class="{ 'is-dragover': isDragOver }"
            @dragover.prevent="onDragOver"
            @dragleave="onDragLeave"
            @drop.prevent="onDrop"
            @click="triggerFileInput"
            data-testid="rup-dropzone"
          >
            <div class="rup-dropzone__icon">
              <svg viewBox="0 0 48 48" width="42" height="42" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
                <path d="M38 32v6a4 4 0 0 1-4 4H14a4 4 0 0 1-4-4v-6"/>
                <polyline points="28 14 24 10 20 14"/>
                <line x1="24" y1="10" x2="24" y2="28"/>
                <path d="M16 22l8-8 8 8" opacity="0"/>
              </svg>
            </div>
            <div class="rup-dropzone__title">拖拽文件到此处，或点击选择文件</div>
            <div class="rup-dropzone__sub">支持文件夹上传 · 单文件最大 500MB</div>
            <button class="rup-dropzone__btn" type="button" @click.stop="triggerFileInput">
              选择文件
            </button>
          </div>
          <input
            ref="fileInputRef"
            type="file"
            multiple
            hidden
            :accept="acceptFormats"
            data-testid="rup-file-input"
            @change="onFileSelect"
          />

          <!-- 文件类型说明 -->
          <div class="rup-formats">
            <div class="rup-formats__label">支持格式</div>
            <div class="rup-formats__list">
              <span v-for="fmt in displayFormats" :key="fmt" class="rup-formats__tag">{{ fmt }}</span>
            </div>
            <a class="rup-formats__link" href="javascript:;" @click.prevent>查看完整格式说明 →</a>
          </div>

          <!-- 拍照扫描入口 -->
          <button
            class="rup-scan-btn"
            type="button"
            data-testid="rup-scan-btn"
            @click="$emit('open-scan')"
          >
            <span class="rup-scan-btn__icon">📷</span>
            <span class="rup-scan-btn__txt">
              <b>拍照 / 扫描入库</b>
              <span>快速将纸质资料转为数字资源</span>
            </span>
            <span class="rup-scan-btn__arrow">→</span>
          </button>

          <!-- 上传任务列表 -->
          <div v-if="hasTasks" class="rup-tasks" data-testid="rup-tasks">
            <!-- 总进度条 + 控制栏 -->
            <div class="rup-tasks__head">
              <div class="rup-tasks__head-left">
                <div class="rup-tasks__title">上传任务</div>
                <div class="rup-tasks__count">{{ files.length }} 个文件</div>
              </div>
              <div class="rup-tasks__head-right">
                <button
                  class="rup-tasks__ctrl"
                  type="button"
                  :disabled="!canPauseAll"
                  @click="pauseAll"
                >
                  <svg v-if="canPauseAll" viewBox="0 0 24 24" width="12" height="12" fill="currentColor">
                    <rect x="6" y="4" width="4" height="16" rx="1"/>
                    <rect x="14" y="4" width="4" height="16" rx="1"/>
                  </svg>
                  全部暂停
                </button>
                <button
                  class="rup-tasks__ctrl"
                  type="button"
                  :disabled="!canResumeAll"
                  @click="resumeAll"
                >
                  <svg v-if="canResumeAll" viewBox="0 0 24 24" width="12" height="12" fill="currentColor">
                    <polygon points="6,4 20,12 6,20"/>
                  </svg>
                  全部继续
                </button>
                <button class="rup-tasks__ctrl rup-tasks__ctrl--danger" type="button" @click="cancelAll">
                  <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
                    <line x1="18" y1="6" x2="6" y2="18"/>
                    <line x1="6" y1="6" x2="18" y2="18"/>
                  </svg>
                  全部取消
                </button>
              </div>
            </div>

            <!-- 总进度 -->
            <div class="rup-total">
              <div class="rup-total__progress">
                <div class="rup-progress__bar" :style="{ width: totalProgress + '%' }" />
              </div>
              <div class="rup-total__meta">
                <span class="rup-total__percent">{{ totalProgress }}%</span>
                <span class="rup-total__eta">预计剩余 {{ remainingTime }}</span>
                <span class="rup-total__speed">{{ totalSpeed }} MB/s</span>
              </div>
            </div>

            <!-- 完成提示 -->
            <div v-if="completedCount > 0 && allCompletedOrParsing" class="rup-complete-banner" data-testid="rup-complete-banner">
              <span class="rup-complete-banner__icon">✅</span>
              <span class="rup-complete-banner__text">
                <b>{{ completedCount }}</b> 个文件上传完成，AI 正在解析
              </span>
              <a class="rup-complete-banner__link" href="javascript:;" @click.prevent="$emit('view-processing')">
                查看解析进度 →
              </a>
            </div>

            <!-- 文件列表 -->
            <div class="rup-list">
              <div
                v-for="file in files"
                :key="file.id"
                class="rup-file"
                :class="[`is-${file.status}`]"
                :data-testid="`rup-file-${file.id}`"
              >
                <div class="rup-file__icon" :class="getFileIconClass(file.type)">
                  {{ getFileIcon(file.type) }}
                </div>
                <div class="rup-file__info">
                  <div class="rup-file__name" :title="file.name">{{ file.name }}</div>
                  <div class="rup-file__meta">
                    <span class="rup-file__size">{{ formatSize(file.size) }}</span>
                    <span class="rup-file__status-text">{{ statusText(file.status) }}</span>
                    <span v-if="file.status === 'uploading'" class="rup-file__speed">{{ file.speed }} MB/s</span>
                  </div>
                  <!-- 进度条 -->
                  <div v-if="showProgress(file.status)" class="rup-file__progress-wrap">
                    <div class="rup-file__progress">
                      <div
                        class="rup-progress__bar"
                        :class="{ 'is-gold': file.status === 'parsing' }"
                        :style="{ width: file.progress + '%' }"
                      />
                    </div>
                    <span class="rup-file__percent">{{ file.progress }}%</span>
                  </div>
                  <!-- 错误信息 -->
                  <div v-if="file.status === 'failed'" class="rup-file__error">
                    <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                      <circle cx="12" cy="12" r="10"/>
                      <line x1="12" y1="8" x2="12" y2="12"/>
                      <line x1="12" y1="16" x2="12.01" y2="16"/>
                    </svg>
                    {{ file.errorMsg || '上传失败，请检查网络后重试' }}
                  </div>
                </div>
                <div class="rup-file__ops">
                  <!-- 上传中 / 暂停中：暂停/继续 + 取消 -->
                  <template v-if="file.status === 'uploading'">
                    <button class="rup-file__op" type="button" title="暂停" @click="pauseFile(file.id)">
                      <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor">
                        <rect x="6" y="4" width="4" height="16" rx="1"/>
                        <rect x="14" y="4" width="4" height="16" rx="1"/>
                      </svg>
                    </button>
                  </template>
                  <template v-else-if="file.status === 'paused'">
                    <button class="rup-file__op rup-file__op--primary" type="button" title="继续" @click="resumeFile(file.id)">
                      <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor">
                        <polygon points="6,4 20,12 6,20"/>
                      </svg>
                    </button>
                  </template>
                  <!-- 失败：重试 -->
                  <template v-else-if="file.status === 'failed'">
                    <button class="rup-file__op rup-file__op--primary" type="button" title="重试" @click="retryFile(file.id)">
                      <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <polyline points="23 4 23 10 17 10"/>
                        <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/>
                      </svg>
                    </button>
                  </template>
                  <!-- 已完成 / 解析中 / 已完成AI：成功图标或AI图标 -->
                  <template v-else-if="file.status === 'done'">
                    <span class="rup-file__done" title="已完成">
                      <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                        <polyline points="20 6 9 17 4 12"/>
                      </svg>
                    </span>
                  </template>
                  <template v-else-if="file.status === 'parsing' || file.status === 'uploaded'">
                    <span class="rup-file__ai" title="AI 解析中">
                      <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <path d="M12 2v4"/>
                        <path d="M12 18v4"/>
                        <path d="M4.93 4.93l2.83 2.83"/>
                        <path d="M16.24 16.24l2.83 2.83"/>
                        <path d="M2 12h4"/>
                        <path d="M18 12h4"/>
                        <path d="M4.93 19.07l2.83-2.83"/>
                        <path d="M16.24 7.76l2.83-2.83"/>
                      </svg>
                    </span>
                  </template>
                  <template v-else-if="file.status === 'pending'">
                    <span class="rup-file__pending" title="排队中">
                      <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <circle cx="12" cy="12" r="10"/>
                        <polyline points="12 6 12 12 16 14"/>
                      </svg>
                    </span>
                  </template>

                  <!-- 取消 / 删除按钮（非已完成状态显示） -->
                  <button
                    v-if="file.status !== 'done'"
                    class="rup-file__op rup-file__op--cancel"
                    type="button"
                    title="取消"
                    @click="removeFile(file.id)"
                  >
                    <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
                      <line x1="18" y1="6" x2="6" y2="18"/>
                      <line x1="6" y1="6" x2="18" y2="18"/>
                    </svg>
                  </button>
                </div>
              </div>
            </div>
          </div>
        </div>
      </transition>

      <!-- 关闭确认弹窗 -->
      <transition name="rup-fade">
        <div v-if="showCloseConfirm" class="rup-confirm-mask" data-testid="rup-close-confirm">
          <div class="rup-confirm">
            <div class="rup-confirm__title">确认关闭上传面板？</div>
            <div class="rup-confirm__desc">
              当前有 <b>{{ uploadingCount }}</b> 个文件正在上传，关闭后上传将中断。
            </div>
            <div class="rup-confirm__ops">
              <button class="tv3-btn tv3-btn--sm" type="button" @click="showCloseConfirm = false">取消</button>
              <button class="tv3-btn tv3-btn--sm tv3-btn--primary" type="button" @click="confirmClose">确认关闭</button>
            </div>
          </div>
        </div>
      </transition>
    </div>
  </transition>
</template>

<script setup lang="ts">
/**
 * ResourceUploadPanel —— 教师端 V3 资源中心上传面板
 * 参考百度网盘 / 飞书文档上传面板设计，琥珀金 + 深海蓝主题。
 * 纯前端原型，用 mock 数据模拟上传进度，不调用真实后端 API。
 */
import { computed, onBeforeUnmount, ref, watch } from 'vue'

const props = defineProps<{
  visible: boolean
}>()
const emit = defineEmits<{
  (e: 'close'): void
  (e: 'open-scan'): void
  (e: 'view-processing'): void
}>()

/* ---------- 类型定义 ---------- */
type UploadStatus = 'pending' | 'uploading' | 'paused' | 'uploaded' | 'parsing' | 'done' | 'failed'
type FileCategory = 'ppt' | 'pdf' | 'doc' | 'xls' | 'image' | 'video' | 'audio' | 'zip' | 'other'

interface UploadFile {
  id: string
  name: string
  size: number // bytes
  type: FileCategory
  status: UploadStatus
  progress: number // 0-100
  speed: number // MB/s
  errorMsg?: string
}

/* ---------- 状态 ---------- */
const fileInputRef = ref<HTMLInputElement | null>(null)
const isDragOver = ref(false)
const minimized = ref(false)
const showCloseConfirm = ref(false)
const files = ref<UploadFile[]>([])

const acceptFormats = '.ppt,.pptx,.pdf,.doc,.docx,.xls,.xlsx,.jpg,.jpeg,.png,.mp4,.mp3,.zip,.rar'
const displayFormats = ['PPT', 'PPTX', 'PDF', 'DOC', 'DOCX', 'XLS', 'XLSX', 'JPG', 'PNG', 'MP4', 'MP3', 'ZIP']

/* ---------- 计算属性 ---------- */
const hasTasks = computed(() => files.value.length > 0)
const uploadingCount = computed(() => files.value.filter(f => f.status === 'uploading' || f.status === 'pending').length)
const completedCount = computed(() => files.value.filter(f => f.status === 'done' || f.status === 'parsing' || f.status === 'uploaded').length)
const allCompletedOrParsing = computed(() => files.value.length > 0 && files.value.every(f => f.status === 'done' || f.status === 'parsing' || f.status === 'uploaded' || f.status === 'failed'))

const totalProgress = computed(() => {
  if (!files.value.length) return 0
  const total = files.value.reduce((sum, f) => sum + f.progress, 0)
  return Math.round(total / files.value.length)
})

const totalSpeed = computed(() => {
  const uploading = files.value.filter(f => f.status === 'uploading')
  if (!uploading.length) return '0.0'
  const total = uploading.reduce((sum, f) => sum + f.speed, 0)
  return total.toFixed(1)
})

const remainingTime = computed(() => {
  const uploading = files.value.filter(f => f.status === 'uploading')
  if (!uploading.length) return '—'
  const totalRemainingBytes = uploading.reduce((sum, f) => {
    const remaining = f.size * (1 - f.progress / 100)
    return sum + remaining
  }, 0)
  const totalSpeedBytes = uploading.reduce((sum, f) => sum + f.speed * 1024 * 1024, 0)
  if (totalSpeedBytes === 0) return '计算中...'
  const seconds = Math.ceil(totalRemainingBytes / totalSpeedBytes)
  if (seconds < 60) return `${seconds} 秒`
  if (seconds < 3600) return `${Math.ceil(seconds / 60)} 分钟`
  return `${Math.ceil(seconds / 3600)} 小时`
})

const canPauseAll = computed(() => files.value.some(f => f.status === 'uploading'))
const canResumeAll = computed(() => files.value.some(f => f.status === 'paused'))

/* ---------- 文件类型工具 ---------- */
function getFileCategory(filename: string): FileCategory {
  const ext = filename.split('.').pop()?.toLowerCase() || ''
  if (['ppt', 'pptx'].includes(ext)) return 'ppt'
  if (ext === 'pdf') return 'pdf'
  if (['doc', 'docx'].includes(ext)) return 'doc'
  if (['xls', 'xlsx'].includes(ext)) return 'xls'
  if (['jpg', 'jpeg', 'png', 'gif', 'bmp', 'webp'].includes(ext)) return 'image'
  if (['mp4', 'avi', 'mov', 'mkv', 'flv'].includes(ext)) return 'video'
  if (['mp3', 'wav', 'flac', 'aac'].includes(ext)) return 'audio'
  if (['zip', 'rar', '7z', 'tar', 'gz'].includes(ext)) return 'zip'
  return 'other'
}

function getFileIcon(type: FileCategory): string {
  const map: Record<FileCategory, string> = {
    ppt: 'P',
    pdf: 'P',
    doc: 'W',
    xls: 'X',
    image: '🖼',
    video: '▶',
    audio: '♪',
    zip: 'Z',
    other: '?',
  }
  return map[type]
}

function getFileIconClass(type: FileCategory): string {
  return `rup-icon--${type}`
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
  if (bytes < 1024 * 1024 * 1024) return (bytes / (1024 * 1024)).toFixed(1) + ' MB'
  return (bytes / (1024 * 1024 * 1024)).toFixed(2) + ' GB'
}

function statusText(status: UploadStatus): string {
  const map: Record<UploadStatus, string> = {
    pending: '准备中',
    uploading: '上传中',
    paused: '已暂停',
    uploaded: '已上传',
    parsing: 'AI 解析中',
    done: '已完成',
    failed: '上传失败',
  }
  return map[status]
}

function showProgress(status: UploadStatus): boolean {
  return status === 'uploading' || status === 'paused' || status === 'parsing' || status === 'uploaded'
}

/* ---------- 拖拽处理 ---------- */
function onDragOver(e: DragEvent) {
  e.preventDefault()
  isDragOver.value = true
}
function onDragLeave() {
  isDragOver.value = false
}
function onDrop(e: DragEvent) {
  isDragOver.value = false
  const dt = e.dataTransfer
  if (!dt) return
  const droppedFiles = [...dt.files]
  addFiles(droppedFiles)
}

/* ---------- 文件选择 ---------- */
function triggerFileInput() {
  fileInputRef.value?.click()
}
function onFileSelect(e: Event) {
  const input = e.target as HTMLInputElement
  if (!input.files) return
  addFiles([...input.files])
  input.value = ''
}

/* ---------- 添加文件（mock） ---------- */
function addFiles(fileList: File[]) {
  const remaining = 20 - files.value.length
  if (remaining <= 0) return
  const toAdd = fileList.slice(0, remaining)

  const newFiles: UploadFile[] = toAdd.map((f, i) => ({
    id: `f${Date.now()}_${i}`,
    name: f.name,
    size: f.size || mockSize(f.name),
    type: getFileCategory(f.name),
    status: 'pending',
    progress: 0,
    speed: 0,
  }))

  files.value = [...files.value, ...newFiles]
  minimized.value = false

  // 依次启动上传（模拟并发控制）
  newFiles.forEach((f, idx) => {
    setTimeout(() => startUpload(f.id), idx * 300)
  })
}

function mockSize(name: string): number {
  const ext = name.split('.').pop()?.toLowerCase() || ''
  const baseSizes: Record<string, number> = {
    ppt: 5 * 1024 * 1024,
    pptx: 8 * 1024 * 1024,
    pdf: 3 * 1024 * 1024,
    doc: 2 * 1024 * 1024,
    docx: 1.5 * 1024 * 1024,
    xls: 1 * 1024 * 1024,
    xlsx: 1.2 * 1024 * 1024,
    jpg: 2 * 1024 * 1024,
    png: 3 * 1024 * 1024,
    mp4: 120 * 1024 * 1024,
    mp3: 5 * 1024 * 1024,
    zip: 20 * 1024 * 1024,
  }
  const base = baseSizes[ext] || 2 * 1024 * 1024
  return Math.round(base * (0.5 + Math.random()))
}

/* ---------- Mock 上传模拟 ---------- */
const uploadTimers = new Map<string, number>()

function startUpload(id: string) {
  const file = files.value.find(f => f.id === id)
  if (!file || file.status !== 'pending') return
  file.status = 'uploading'

  // 模拟随机失败概率（约 10%）
  const willFail = Math.random() < 0.1
  const failAt = willFail ? Math.floor(20 + Math.random() * 50) : 100

  const tick = () => {
    const f = files.value.find(x => x.id === id)
    if (!f) return
    if (f.status === 'paused') {
      uploadTimers.set(id, window.setTimeout(tick, 500))
      return
    }
    if (f.status !== 'uploading') return

    // 随机速度 0.5 - 5 MB/s
    f.speed = +(0.5 + Math.random() * 4.5).toFixed(1)
    const increment = (f.speed * 1024 * 1024 / f.size) * 100 * 0.3 // 每 tick 增长
    f.progress = Math.min(f.progress + increment, failAt)

    if (f.progress >= failAt) {
      if (willFail) {
        f.status = 'failed'
        f.errorMsg = '网络连接中断，请检查网络后重试'
        f.speed = 0
        return
      }
      f.progress = 100
      f.speed = 0
      f.status = 'uploaded'
      // 进入 AI 解析阶段
      setTimeout(() => startParsing(id), 400)
      return
    }

    uploadTimers.set(id, window.setTimeout(tick, 300))
  }

  uploadTimers.set(id, window.setTimeout(tick, 300))
}

function startParsing(id: string) {
  const file = files.value.find(f => f.id === id)
  if (!file) return
  file.status = 'parsing'
  file.progress = 0

  const parseDuration = 2000 + Math.random() * 3000
  const startTime = Date.now()

  const tick = () => {
    const f = files.value.find(x => x.id === id)
    if (!f || f.status !== 'parsing') return

    const elapsed = Date.now() - startTime
    f.progress = Math.min(Math.round((elapsed / parseDuration) * 100), 100)

    if (f.progress >= 100) {
      f.status = 'done'
      return
    }
    uploadTimers.set(id, window.setTimeout(tick, 200))
  }

  uploadTimers.set(id, window.setTimeout(tick, 200))
}

function pauseFile(id: string) {
  const file = files.value.find(f => f.id === id)
  if (!file || file.status !== 'uploading') return
  file.status = 'paused'
  file.speed = 0
}

function resumeFile(id: string) {
  const file = files.value.find(f => f.id === id)
  if (!file || file.status !== 'paused') return
  file.status = 'uploading'
  // 重启上传循环
  const tick = () => {
    const f = files.value.find(x => x.id === id)
    if (!f) return
    if (f.status === 'paused') {
      uploadTimers.set(id, window.setTimeout(tick, 500))
      return
    }
    if (f.status !== 'uploading') return

    f.speed = +(0.5 + Math.random() * 4.5).toFixed(1)
    const increment = (f.speed * 1024 * 1024 / f.size) * 100 * 0.3
    f.progress = Math.min(f.progress + increment, 100)

    if (f.progress >= 100) {
      f.progress = 100
      f.speed = 0
      f.status = 'uploaded'
      setTimeout(() => startParsing(id), 400)
      return
    }
    uploadTimers.set(id, window.setTimeout(tick, 300))
  }
  uploadTimers.set(id, window.setTimeout(tick, 300))
}

function retryFile(id: string) {
  const file = files.value.find(f => f.id === id)
  if (!file || file.status !== 'failed') return
  file.status = 'pending'
  file.progress = 0
  file.errorMsg = undefined
  startUpload(id)
}

function removeFile(id: string) {
  const timer = uploadTimers.get(id)
  if (timer) {
    clearTimeout(timer)
    uploadTimers.delete(id)
  }
  files.value = files.value.filter(f => f.id !== id)
  if (!files.value.length) {
    minimized.value = false
  }
}

function pauseAll() {
  files.value.forEach(f => {
    if (f.status === 'uploading') pauseFile(f.id)
  })
}

function resumeAll() {
  files.value.forEach(f => {
    if (f.status === 'paused') resumeFile(f.id)
  })
}

function cancelAll() {
  files.value.forEach(f => {
    const timer = uploadTimers.get(f.id)
    if (timer) {
      clearTimeout(timer)
      uploadTimers.delete(f.id)
    }
  })
  files.value = []
  minimized.value = false
}

/* ---------- 关闭处理 ---------- */
function handleClose() {
  const hasUploading = files.value.some(f => f.status === 'uploading' || f.status === 'pending')
  if (hasUploading) {
    showCloseConfirm.value = true
  } else {
    emit('close')
  }
}

function confirmClose() {
  showCloseConfirm.value = false
  // 清理所有定时器
  uploadTimers.forEach(timer => clearTimeout(timer))
  uploadTimers.clear()
  emit('close')
}

/* ---------- 生命周期 ---------- */
watch(() => props.visible, (v) => {
  if (!v) {
    showCloseConfirm.value = false
  }
})

onBeforeUnmount(() => {
  uploadTimers.forEach(timer => clearTimeout(timer))
  uploadTimers.clear()
})
</script>

<style scoped>
/* ========== 面板容器 ========== */
.rup-panel {
  position: fixed;
  right: 24px;
  bottom: 24px;
  width: 440px;
  min-width: 360px;
  max-width: 480px;
  max-height: calc(100vh - 48px);
  background: var(--tv3-card, #fff);
  border: 1px solid var(--tv3-line, #e3e8f0);
  border-radius: 12px;
  box-shadow: 0 12px 40px rgba(79, 70, 229, 0.18);
  display: flex;
  flex-direction: column;
  overflow: hidden;
  z-index: 900;
  font-size: 13px;
  color: var(--tv3-ink, #16233b);
}

.rup-panel.is-minimized {
  width: 360px;
  min-width: auto;
}

/* 过渡动画 */
.rup-fade-enter-active,
.rup-fade-leave-active {
  transition: opacity 0.2s ease, transform 0.2s ease;
}
.rup-fade-enter-from,
.rup-fade-leave-to {
  opacity: 0;
  transform: translateY(20px);
}

.rup-expand-enter-active,
.rup-expand-leave-active {
  transition: max-height 0.3s ease, opacity 0.25s ease;
  overflow: hidden;
}
.rup-expand-enter-from,
.rup-expand-leave-to {
  max-height: 0;
  opacity: 0;
}

/* ========== 头部 ========== */
.rup-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 14px;
  border-bottom: 1px solid var(--tv3-line, #e3e8f0);
  background: linear-gradient(135deg, var(--tv3-navy, #3730a3) 0%, var(--tv3-navy-mid, #4f46e5) 100%);
  color: #fff;
  flex-shrink: 0;
}

.rup-head__left {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}

.rup-head__icon {
  width: 32px;
  height: 32px;
  border-radius: 8px;
  background: rgba(6, 182, 212, 0.25);
  display: grid;
  place-items: center;
  color: var(--tv3-gold, #0891b2);
  flex-shrink: 0;
}

.rup-head__txt {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.rup-head__txt b {
  font-size: 13.5px;
  font-weight: 700;
  line-height: 1.3;
}

.rup-head__txt span {
  font-size: 11px;
  color: rgba(255, 255, 255, 0.65);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 240px;
}

.rup-head__ops {
  display: flex;
  align-items: center;
  gap: 2px;
  flex-shrink: 0;
}

.rup-head__op,
.rup-head__close {
  width: 28px;
  height: 28px;
  border: none;
  background: transparent;
  color: rgba(255, 255, 255, 0.7);
  cursor: pointer;
  border-radius: 6px;
  display: grid;
  place-items: center;
  transition: all 0.15s ease;
}

.rup-head__op:hover,
.rup-head__close:hover {
  background: rgba(255, 255, 255, 0.12);
  color: #fff;
}

/* ========== 最小化进度条 ========== */
.rup-minibar {
  padding: 10px 14px;
  cursor: pointer;
  transition: background 0.15s ease;
}

.rup-minibar:hover {
  background: var(--tv3-bg2, #eef1f7);
}

.rup-minibar__progress {
  height: 4px;
  border-radius: 999px;
  background: var(--tv3-bg2, #eef1f7);
  overflow: hidden;
  margin-bottom: 6px;
}

.rup-minibar__info {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 11.5px;
  color: var(--tv3-ink2, #4a5568);
  font-family: var(--tv3-font-num, system-ui);
}

.rup-minibar__sep {
  color: var(--tv3-ink4, #c3cad6);
}

/* ========== 主体 ========== */
.rup-body {
  flex: 1;
  overflow-y: auto;
  padding: 14px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

/* ========== 拖拽上传区 ========== */
.rup-dropzone {
  border: 1.5px dashed var(--tv3-line, #e3e8f0);
  border-radius: 10px;
  padding: 24px 16px;
  text-align: center;
  cursor: pointer;
  transition: all 0.2s ease;
  background: var(--tv3-bg, #f5f7fb);
  position: relative;
}

.rup-dropzone:hover {
  border-color: var(--tv3-primary, #4f46e5);
  background: var(--tv3-primary-soft, #eaf1fb);
}

.rup-dropzone.is-dragover {
  border-color: var(--tv3-gold, #0891b2);
  background: var(--tv3-gold-soft, #cffafe);
  border-style: solid;
}

.rup-dropzone.is-dragover .rup-dropzone__icon {
  transform: scale(1.15);
  color: var(--tv3-gold, #0891b2);
}

.rup-dropzone__icon {
  color: var(--tv3-primary, #4f46e5);
  margin-bottom: 10px;
  transition: transform 0.25s cubic-bezier(0.34, 1.56, 0.64, 1), color 0.2s ease;
}

.rup-dropzone__title {
  font-size: 13.5px;
  font-weight: 600;
  color: var(--tv3-ink, #16233b);
  margin-bottom: 4px;
}

.rup-dropzone__sub {
  font-size: 11.5px;
  color: var(--tv3-ink3, #8b95a7);
  margin-bottom: 12px;
}

.rup-dropzone__btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 7px 18px;
  border: none;
  border-radius: 8px;
  background: linear-gradient(135deg, var(--tv3-gold, #0891b2), var(--tv3-gold-deep, #0e7490));
  color: #fff;
  font-size: 12.5px;
  font-weight: 600;
  cursor: pointer;
  box-shadow: 0 4px 12px rgba(6, 182, 212, 0.3);
  transition: all 0.15s ease;
}

.rup-dropzone__btn:hover {
  transform: translateY(-1px);
  box-shadow: 0 6px 18px rgba(6, 182, 212, 0.4);
}

.rup-dropzone__btn:active {
  transform: translateY(0);
}

/* ========== 格式说明 ========== */
.rup-formats {
  background: var(--tv3-bg2, #eef1f7);
  border-radius: 10px;
  padding: 10px 12px;
}

.rup-formats__label {
  font-size: 11.5px;
  font-weight: 600;
  color: var(--tv3-ink2, #4a5568);
  margin-bottom: 6px;
}

.rup-formats__list {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-bottom: 6px;
}

.rup-formats__tag {
  display: inline-block;
  padding: 2px 7px;
  border-radius: 4px;
  background: var(--tv3-card, #fff);
  border: 1px solid var(--tv3-line, #e3e8f0);
  font-size: 10.5px;
  color: var(--tv3-ink3, #8b95a7);
  font-family: var(--tv3-font-num, system-ui);
  font-weight: 500;
}

.rup-formats__link {
  font-size: 11px;
  color: var(--tv3-primary, #4f46e5);
  text-decoration: none;
}

.rup-formats__link:hover {
  text-decoration: underline;
}

/* ========== 拍照扫描按钮 ========== */
.rup-scan-btn {
  display: flex;
  align-items: center;
  gap: 12px;
  width: 100%;
  padding: 12px 14px;
  border: 1px solid var(--tv3-ai-border, #ddd0f8);
  border-radius: 10px;
  background: var(--tv3-ai-soft, #f3eefd);
  cursor: pointer;
  text-align: left;
  transition: all 0.15s ease;
}

.rup-scan-btn:hover {
  border-color: var(--tv3-ai, #7c3aed);
  box-shadow: 0 4px 14px rgba(124, 58, 237, 0.15);
  transform: translateY(-1px);
}

.rup-scan-btn__icon {
  width: 36px;
  height: 36px;
  border-radius: 9px;
  background: linear-gradient(135deg, var(--tv3-ai, #7c3aed), #a06bf5);
  display: grid;
  place-items: center;
  font-size: 18px;
  flex-shrink: 0;
  color: #fff;
}

.rup-scan-btn__txt {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.rup-scan-btn__txt b {
  font-size: 13px;
  font-weight: 700;
  color: var(--tv3-ai-deep, #612fb8);
}

.rup-scan-btn__txt span {
  font-size: 11px;
  color: var(--tv3-ink3, #8b95a7);
}

.rup-scan-btn__arrow {
  color: var(--tv3-ai, #7c3aed);
  font-size: 14px;
  flex-shrink: 0;
  transition: transform 0.15s ease;
}

.rup-scan-btn:hover .rup-scan-btn__arrow {
  transform: translateX(3px);
}

/* ========== 任务列表 ========== */
.rup-tasks {
  border: 1px solid var(--tv3-line, #e3e8f0);
  border-radius: 10px;
  overflow: hidden;
}

.rup-tasks__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 12px;
  background: var(--tv3-bg2, #eef1f7);
  border-bottom: 1px solid var(--tv3-line, #e3e8f0);
  gap: 8px;
}

.rup-tasks__head-left {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.rup-tasks__title {
  font-size: 12.5px;
  font-weight: 700;
  color: var(--tv3-ink, #16233b);
}

.rup-tasks__count {
  font-size: 11px;
  color: var(--tv3-ink3, #8b95a7);
  font-family: var(--tv3-font-num, system-ui);
}

.rup-tasks__head-right {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
}

.rup-tasks__ctrl {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 4px 8px;
  border: 1px solid var(--tv3-line, #e3e8f0);
  border-radius: 6px;
  background: var(--tv3-card, #fff);
  color: var(--tv3-ink2, #4a5568);
  font-size: 11px;
  cursor: pointer;
  transition: all 0.12s ease;
}

.rup-tasks__ctrl:hover:not(:disabled) {
  border-color: var(--tv3-primary, #4f46e5);
  color: var(--tv3-primary, #4f46e5);
}

.rup-tasks__ctrl:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.rup-tasks__ctrl--danger:hover:not(:disabled) {
  border-color: var(--tv3-rose, #dc2646);
  color: var(--tv3-rose, #dc2646);
}

/* ========== 总进度 ========== */
.rup-total {
  padding: 10px 12px;
  border-bottom: 1px solid var(--tv3-line, #e3e8f0);
}

.rup-total__progress {
  height: 6px;
  border-radius: 999px;
  background: var(--tv3-bg2, #eef1f7);
  overflow: hidden;
  margin-bottom: 6px;
}

.rup-total__meta {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 11px;
  font-family: var(--tv3-font-num, system-ui);
}

.rup-total__percent {
  font-weight: 700;
  color: var(--tv3-primary, #4f46e5);
}

.rup-total__eta {
  color: var(--tv3-ink3, #8b95a7);
}

.rup-total__speed {
  margin-left: auto;
  color: var(--tv3-ink3, #8b95a7);
}

/* 通用进度条 */
.rup-progress__bar {
  height: 100%;
  border-radius: 999px;
  background: linear-gradient(90deg, var(--tv3-primary, #4f46e5), #3b74cf);
  transition: width 0.3s ease;
}

.rup-progress__bar.is-gold {
  background: linear-gradient(90deg, var(--tv3-gold, #0891b2), #e5c05a);
}

/* ========== 完成横幅 ========== */
.rup-complete-banner {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
  background: var(--tv3-gold-soft, #cffafe);
  border-bottom: 1px solid var(--tv3-gold-border, #ecd9a8);
  font-size: 12px;
}

.rup-complete-banner__icon {
  font-size: 14px;
  flex-shrink: 0;
}

.rup-complete-banner__text {
  flex: 1;
  color: var(--tv3-gold-deep, #0e7490);
}

.rup-complete-banner__text b {
  font-family: var(--tv3-font-num, system-ui);
  font-weight: 700;
}

.rup-complete-banner__link {
  color: var(--tv3-gold-deep, #0e7490);
  font-weight: 600;
  text-decoration: none;
  font-size: 11.5px;
  flex-shrink: 0;
}

.rup-complete-banner__link:hover {
  text-decoration: underline;
}

/* ========== 文件列表 ========== */
.rup-list {
  max-height: 260px;
  overflow-y: auto;
}

.rup-file {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 10px 12px;
  border-bottom: 1px solid var(--tv3-line2, #eef1f6);
  transition: background 0.12s ease;
}

.rup-file:last-child {
  border-bottom: none;
}

.rup-file:hover {
  background: var(--tv3-bg, #f5f7fb);
}

.rup-file.is-failed {
  background: var(--tv3-rose-soft, #fdecef);
}

.rup-file.is-done {
  background: rgba(14, 148, 136, 0.03);
}

/* 文件图标 */
.rup-file__icon {
  width: 36px;
  height: 36px;
  border-radius: 8px;
  display: grid;
  place-items: center;
  font-size: 13px;
  font-weight: 800;
  color: #fff;
  flex-shrink: 0;
  font-family: Georgia, serif;
}

.rup-icon--ppt { background: linear-gradient(135deg, #d24726, #b93617); }
.rup-icon--pdf { background: linear-gradient(135deg, #e53935, #c62828); }
.rup-icon--doc { background: linear-gradient(135deg, #2b579a, #1e3f70); }
.rup-icon--xls { background: linear-gradient(135deg, #217346, #185934); }
.rup-icon--image { background: linear-gradient(135deg, #8e24aa, #6a1b9a); font-size: 16px; }
.rup-icon--video { background: linear-gradient(135deg, #ef6c00, #e65100); font-size: 14px; }
.rup-icon--audio { background: linear-gradient(135deg, #00897b, #00695c); font-size: 14px; }
.rup-icon--zip { background: linear-gradient(135deg, #f9a825, #f57f17); }
.rup-icon--other { background: linear-gradient(135deg, #607d8b, #455a64); }

/* 文件信息 */
.rup-file__info {
  flex: 1;
  min-width: 0;
}

.rup-file__name {
  font-size: 12.5px;
  font-weight: 500;
  color: var(--tv3-ink, #16233b);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  margin-bottom: 3px;
}

.rup-file__meta {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 11px;
  color: var(--tv3-ink3, #8b95a7);
  font-family: var(--tv3-font-num, system-ui);
  margin-bottom: 5px;
}

.rup-file__size {
  color: var(--tv3-ink3, #8b95a7);
}

.rup-file__status-text {
  font-weight: 500;
}

.rup-file.is-uploading .rup-file__status-text { color: var(--tv3-primary, #4f46e5); }
.rup-file.is-paused .rup-file__status-text { color: var(--tv3-slate, #64748b); }
.rup-file.is-parsing .rup-file__status-text,
.rup-file.is-uploaded .rup-file__status-text { color: var(--tv3-gold-deep, #0e7490); }
.rup-file.is-done .rup-file__status-text { color: var(--tv3-teal, #0e9488); }
.rup-file.is-failed .rup-file__status-text { color: var(--tv3-rose, #dc2646); }
.rup-file.is-pending .rup-file__status-text { color: var(--tv3-ink3, #8b95a7); }

.rup-file__speed {
  color: var(--tv3-primary, #4f46e5);
  font-weight: 500;
}

/* 单文件进度条 */
.rup-file__progress-wrap {
  display: flex;
  align-items: center;
  gap: 8px;
}

.rup-file__progress {
  flex: 1;
  height: 4px;
  border-radius: 999px;
  background: var(--tv3-bg2, #eef1f7);
  overflow: hidden;
}

.rup-file__percent {
  font-size: 10.5px;
  color: var(--tv3-ink3, #8b95a7);
  font-family: var(--tv3-font-num, system-ui);
  font-weight: 600;
  min-width: 32px;
  text-align: right;
  flex-shrink: 0;
}

/* 错误信息 */
.rup-file__error {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 11px;
  color: var(--tv3-rose, #dc2646);
  margin-top: 4px;
}

/* 文件操作按钮 */
.rup-file__ops {
  display: flex;
  align-items: center;
  gap: 2px;
  flex-shrink: 0;
  padding-top: 2px;
}

.rup-file__op {
  width: 24px;
  height: 24px;
  border: 1px solid var(--tv3-line, #e3e8f0);
  border-radius: 6px;
  background: var(--tv3-card, #fff);
  color: var(--tv3-ink3, #8b95a7);
  cursor: pointer;
  display: grid;
  place-items: center;
  transition: all 0.12s ease;
}

.rup-file__op:hover {
  border-color: var(--tv3-primary, #4f46e5);
  color: var(--tv3-primary, #4f46e5);
}

.rup-file__op--primary {
  border-color: var(--tv3-primary, #4f46e5);
  background: var(--tv3-primary-soft, #eaf1fb);
  color: var(--tv3-primary, #4f46e5);
}

.rup-file__op--primary:hover {
  background: var(--tv3-primary, #4f46e5);
  color: #fff;
}

.rup-file__op--cancel:hover {
  border-color: var(--tv3-rose, #dc2646);
  color: var(--tv3-rose, #dc2646);
}

/* 完成状态图标 */
.rup-file__done {
  width: 24px;
  height: 24px;
  display: grid;
  place-items: center;
  color: var(--tv3-teal, #0e9488);
}

/* AI 解析图标（脉冲动画） */
.rup-file__ai {
  width: 24px;
  height: 24px;
  display: grid;
  place-items: center;
  color: var(--tv3-gold, #0891b2);
  animation: rup-spin 3s linear infinite;
}

@keyframes rup-spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

.rup-file__pending {
  width: 24px;
  height: 24px;
  display: grid;
  place-items: center;
  color: var(--tv3-ink3, #8b95a7);
}

/* ========== 关闭确认弹窗 ========== */
.rup-confirm-mask {
  position: absolute;
  inset: 0;
  background: rgba(79, 70, 229, 0.4);
  display: grid;
  place-items: center;
  z-index: 10;
  backdrop-filter: blur(2px);
}

.rup-confirm {
  width: 280px;
  background: var(--tv3-card, #fff);
  border-radius: 12px;
  padding: 18px;
  box-shadow: 0 16px 48px rgba(79, 70, 229, 0.25);
}

.rup-confirm__title {
  font-size: 14px;
  font-weight: 700;
  color: var(--tv3-ink, #16233b);
  margin-bottom: 8px;
}

.rup-confirm__desc {
  font-size: 12.5px;
  color: var(--tv3-ink2, #4a5568);
  line-height: 1.6;
  margin-bottom: 16px;
}

.rup-confirm__desc b {
  color: var(--tv3-rose, #dc2646);
  font-family: var(--tv3-font-num, system-ui);
}

.rup-confirm__ops {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}
</style>

