<template>
  <Teleport to="body">
    <transition name="aip-fade">
      <div v-if="visible" class="aip-scrim" @click.self="emit('close')">
        <transition name="aip-zoom">
          <div v-if="visible" class="aip-dialog" role="dialog" aria-label="AI 解析状态" data-testid="tv3-ai-processing-panel">
            <!-- 标题栏 -->
            <header class="aip-head">
              <div class="aip-head__brand">
                <div class="aip-head__icon">⚡</div>
                <div class="aip-head__text">
                  <div class="aip-head__title">AI 资源处理中心</div>
                  <div class="aip-head__sub">智能解析 · 自动打标 · 知识点提取</div>
                </div>
              </div>
              <button class="aip-head__close" data-testid="tv3-aip-close" @click="emit('close')" aria-label="关闭">×</button>
            </header>

            <!-- 统计概览 -->
            <section class="aip-stats">
              <div class="aip-stat-card">
                <div class="aip-stat-card__num">{{ stats.total }}</div>
                <div class="aip-stat-card__label">总计</div>
              </div>
              <div class="aip-stat-card aip-stat-card--success">
                <div class="aip-stat-card__num">{{ stats.success }}</div>
                <div class="aip-stat-card__label">成功</div>
              </div>
              <div class="aip-stat-card aip-stat-card--processing">
                <div class="aip-stat-card__num">
                  {{ stats.processing }}
                  <span v-if="stats.processing > 0" class="aip-stat-card__pulse" />
                </div>
                <div class="aip-stat-card__label">解析中</div>
              </div>
              <div class="aip-stat-card aip-stat-card--failed">
                <div class="aip-stat-card__num">{{ stats.failed }}</div>
                <div class="aip-stat-card__label">失败</div>
              </div>
            </section>

            <!-- Tabs -->
            <div class="aip-tabs" role="tablist">
              <button
                v-for="t in tabs" :key="t.key" type="button"
                class="aip-tab" :class="{ 'is-on': activeTab === t.key }"
                role="tab" :aria-selected="activeTab === t.key"
                @click="activeTab = t.key"
              >
                <span class="aip-tab__icon">{{ t.icon }}</span>
                <span class="aip-tab__label">{{ t.label }}</span>
                <span class="aip-tab__count">{{ tabCount(t.key) }}</span>
              </button>
            </div>

            <!-- 内容区 -->
            <div class="aip-content">
              <!-- 处理中 -->
              <template v-if="activeTab === 'processing'">
                <div v-if="!processingList.length" class="aip-empty">
                  <div class="aip-empty__icon">⏳</div>
                  <div class="aip-empty__title">暂无正在解析的资源</div>
                  <div class="aip-empty__desc">上传文件后 AI 会自动解析</div>
                </div>
                <transition-group name="aip-card" tag="div" class="aip-list">
                  <div v-for="task in processingList" :key="task.id" class="aip-card aip-card--processing" :data-testid="`tv3-aip-processing-${task.id}`">
                    <div class="aip-card__head">
                      <div class="aip-file">
                        <span class="aip-file__icon">{{ fileIcon(task.type) }}</span>
                        <div class="aip-file__info">
                          <div class="aip-file__name">{{ task.name }}</div>
                          <div class="aip-file__meta">{{ formatSize(task.size) }} · {{ task.startedAt }}</div>
                        </div>
                      </div>
                    </div>

                    <!-- 阶段时间线 -->
                    <div class="aip-timeline">
                      <div
                        v-for="(stage, idx) in stages" :key="stage.key"
                        class="aip-timeline__node"
                        :class="{
                          'is-done': idx < task.currentStage,
                          'is-current': idx === task.currentStage,
                          'is-pending': idx > task.currentStage,
                        }"
                      >
                        <div class="aip-timeline__dot">
                          <template v-if="idx < task.currentStage">✓</template>
                          <template v-else-if="idx === task.currentStage"><span class="aip-timeline__pulse" /></template>
                          <template v-else>○</template>
                        </div>
                        <div class="aip-timeline__label">{{ stage.label }}</div>
                        <div v-if="idx < stages.length - 1" class="aip-timeline__line" />
                      </div>
                    </div>

                    <!-- 进度条 -->
                    <div class="aip-progress">
                      <div class="aip-progress__bar" :style="{ width: task.progress + '%' }" />
                      <div class="aip-progress__info">
                        <span class="aip-progress__percent">{{ Math.round(task.progress) }}%</span>
                        <span class="aip-progress__eta">预计剩余 {{ task.eta }}</span>
                      </div>
                    </div>

                    <!-- 操作 -->
                    <div class="aip-card__actions">
                      <button class="aip-btn aip-btn--ghost aip-btn--danger" @click="cancelTask(task.id)">取消解析</button>
                    </div>
                  </div>
                </transition-group>
              </template>

              <!-- 已完成 -->
              <template v-else-if="activeTab === 'completed'">
                <div v-if="!completedList.length" class="aip-empty">
                  <div class="aip-empty__icon">✅</div>
                  <div class="aip-empty__title">还没有完成解析的资源</div>
                  <div class="aip-empty__desc">解析完成的资源会出现在这里</div>
                </div>
                <transition-group name="aip-card" tag="div" class="aip-list">
                  <div v-for="task in completedList" :key="task.id" class="aip-card aip-card--completed" :data-testid="`tv3-aip-completed-${task.id}`">
                    <div class="aip-card__head">
                      <div class="aip-file">
                        <span class="aip-file__icon">{{ fileIcon(task.type) }}</span>
                        <div class="aip-file__info">
                          <div class="aip-file__name">{{ task.name }}</div>
                          <div class="aip-file__meta">
                            <span class="aip-file__status">✓ 解析完成</span>
                            <span class="aip-file__dot">·</span>
                            <span>{{ task.completedAt }}</span>
                          </div>
                        </div>
                      </div>
                    </div>

                    <!-- 自动生成标签 -->
                    <div class="aip-tags">
                      <span v-for="tag in task.tags" :key="tag.text" class="aip-tag" :data-type="tag.type">{{ tag.text }}</span>
                    </div>

                    <!-- 资源摘要 -->
                    <div class="aip-summary">
                      <span class="aip-summary__label">AI 摘要</span>
                      {{ task.summary }}
                    </div>

                    <!-- 操作 -->
                    <div class="aip-card__actions">
                      <button class="aip-btn aip-btn--ghost" @click="reparseTask(task.id)">重新解析</button>
                      <button class="aip-btn aip-btn--primary" @click="viewResource(task.id)">查看资源</button>
                    </div>
                  </div>
                </transition-group>
              </template>

              <!-- 失败 -->
              <template v-else-if="activeTab === 'failed'">
                <div v-if="!failedList.length" class="aip-empty">
                  <div class="aip-empty__icon">🎉</div>
                  <div class="aip-empty__title">暂无失败的任务</div>
                  <div class="aip-empty__desc">所有资源都解析成功啦</div>
                </div>
                <transition-group name="aip-card" tag="div" class="aip-list">
                  <div v-for="task in failedList" :key="task.id" class="aip-card aip-card--failed" :data-testid="`tv3-aip-failed-${task.id}`">
                    <div class="aip-card__head">
                      <div class="aip-file">
                        <span class="aip-file__icon">{{ fileIcon(task.type) }}</span>
                        <div class="aip-file__info">
                          <div class="aip-file__name">{{ task.name }}</div>
                          <div class="aip-file__meta">
                            <span class="aip-file__status aip-file__status--failed">✕ 解析失败</span>
                            <span class="aip-file__dot">·</span>
                            <span>{{ task.failedAt }}</span>
                          </div>
                        </div>
                      </div>
                    </div>

                    <!-- 失败原因 -->
                    <div class="aip-error">
                      <span class="aip-error__icon">⚠</span>
                      <span class="aip-error__text">{{ task.errorReason }}</span>
                    </div>

                    <!-- 操作 -->
                    <div class="aip-card__actions">
                      <button class="aip-btn aip-btn--ghost aip-btn--danger" @click="deleteTask(task.id)">删除</button>
                      <button class="aip-btn aip-btn--primary" @click="retryTask(task.id)">重试</button>
                    </div>
                  </div>
                </transition-group>
              </template>
            </div>
          </div>
        </transition>
      </div>
    </transition>
  </Teleport>
</template>

<script setup lang="ts">
/**
 * AiProcessingPanel —— AI 资源处理状态面板
 * 教师端 V3 资源中心：展示资源上传后的 AI 处理进度
 * - OCR 识别、知识点提取、自动打标签等多阶段时间线
 * - 琥珀金主题，720px 居中弹窗
 * - 纯前端原型，进度用定时器模拟增长
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

const props = defineProps<{ visible: boolean }>()
const emit = defineEmits<{
  (e: 'close'): void
  (e: 'view-resource', id: string): void
}>()

/* ---------- 类型定义 ---------- */
type TaskType = 'ppt' | 'pdf' | 'image' | 'doc' | 'excel'
type TagType = 'subject' | 'grade' | 'category' | 'knowledge'

interface ProcessingTask {
  id: string
  name: string
  type: TaskType
  size: number // bytes
  startedAt: string
  currentStage: number
  progress: number
  eta: string
}

interface CompletedTask {
  id: string
  name: string
  type: TaskType
  completedAt: string
  tags: { text: string; type: TagType }[]
  summary: string
}

interface FailedTask {
  id: string
  name: string
  type: TaskType
  failedAt: string
  errorReason: string
}

/* ---------- 阶段定义 ---------- */
const stages = [
  { key: 'upload', label: '上传完成' },
  { key: 'convert', label: '格式转换' },
  { key: 'ocr', label: 'OCR 识别' },
  { key: 'understand', label: '内容理解' },
  { key: 'knowledge', label: '知识点提取' },
  { key: 'tagging', label: '自动打标签' },
  { key: 'done', label: '完成' },
]

/* ---------- Tabs ---------- */
const tabs = [
  { key: 'processing' as const, label: '处理中', icon: '🔄' },
  { key: 'completed' as const, label: '已完成', icon: '✅' },
  { key: 'failed' as const, label: '失败', icon: '❌' },
]
const activeTab = ref<'processing' | 'completed' | 'failed'>('processing')

/* ---------- Mock 数据 ---------- */
const processingList = ref<ProcessingTask[]>([
  {
    id: 'p1',
    name: '高二数学·导数的概念与几何意义.pptx',
    type: 'ppt',
    size: 8 * 1024 * 1024,
    startedAt: '3 分钟前开始',
    currentStage: 2,
    progress: 35,
    eta: '约 2 分钟',
  },
  {
    id: 'p2',
    name: '高三一轮复习·函数与方程.pdf',
    type: 'pdf',
    size: 12 * 1024 * 1024,
    startedAt: '1 分钟前开始',
    currentStage: 1,
    progress: 18,
    eta: '约 5 分钟',
  },
  {
    id: 'p3',
    name: '期中试卷扫描件.jpg',
    type: 'image',
    size: 2.4 * 1024 * 1024,
    startedAt: '刚刚开始',
    currentStage: 0,
    progress: 8,
    eta: '约 3 分钟',
  },
])

const completedList = ref<CompletedTask[]>([
  {
    id: 'c1',
    name: '高一数学·集合的基本运算教案.docx',
    type: 'doc',
    completedAt: '10 分钟前完成',
    tags: [
      { text: '数学', type: 'subject' },
      { text: '高一', type: 'grade' },
      { text: '教案', type: 'category' },
      { text: '集合运算', type: 'knowledge' },
      { text: '交集并集', type: 'knowledge' },
    ],
    summary: '包含集合的交集、并集、补集三种基本运算的概念讲解与典型例题，配有 Venn 图辅助理解，适合高一新授课使用。',
  },
  {
    id: 'c2',
    name: '三角函数图像与性质课件.pptx',
    type: 'ppt',
    completedAt: '25 分钟前完成',
    tags: [
      { text: '数学', type: 'subject' },
      { text: '高一', type: 'grade' },
      { text: '课件', type: 'category' },
      { text: '三角函数', type: 'knowledge' },
      { text: '图像变换', type: 'knowledge' },
    ],
    summary: '系统讲解正弦、余弦、正切函数的图像与性质，包含五点作图法、图像平移伸缩变换及周期性、奇偶性、单调性分析。',
  },
  {
    id: 'c3',
    name: '立体几何单元测试卷.pdf',
    type: 'pdf',
    completedAt: '1 小时前完成',
    tags: [
      { text: '数学', type: 'subject' },
      { text: '高二', type: 'grade' },
      { text: '试卷', type: 'category' },
      { text: '立体几何', type: 'knowledge' },
      { text: '空间向量', type: 'knowledge' },
    ],
    summary: '涵盖空间几何体结构、点线面位置关系、空间向量及其应用等知识点，题型包括选择、填空和解答，难度适中。',
  },
  {
    id: 'c4',
    name: '数列求和方法总结.pdf',
    type: 'pdf',
    completedAt: '2 小时前完成',
    tags: [
      { text: '数学', type: 'subject' },
      { text: '高二', type: 'grade' },
      { text: '讲义', type: 'category' },
      { text: '数列', type: 'knowledge' },
      { text: '错位相减', type: 'knowledge' },
      { text: '裂项相消', type: 'knowledge' },
    ],
    summary: '归纳整理了等差数列、等比数列及特殊数列的求和方法，包括公式法、错位相减法、裂项相消法、分组求和法等。',
  },
  {
    id: 'c5',
    name: '圆锥曲线专题训练.docx',
    type: 'doc',
    completedAt: '昨天 16:30 完成',
    tags: [
      { text: '数学', type: 'subject' },
      { text: '高二', type: 'grade' },
      { text: '习题', type: 'category' },
      { text: '圆锥曲线', type: 'knowledge' },
      { text: '椭圆', type: 'knowledge' },
      { text: '双曲线', type: 'knowledge' },
    ],
    summary: '精选椭圆、双曲线、抛物线的典型例题与变式训练，注重定义运用、几何性质转化及直线与圆锥曲线位置关系的综合应用。',
  },
])

const failedList = ref<FailedTask[]>([
  {
    id: 'f1',
    name: '老旧扫描版教材.zip',
    type: 'image',
    failedAt: '30 分钟前失败',
    errorReason: '文件格式不支持：暂不支持 ZIP 压缩包格式，请解压后单独上传图片文件',
  },
  {
    id: 'f2',
    name: '手写笔记扫描件.png',
    type: 'image',
    failedAt: '1 小时前失败',
    errorReason: 'OCR 识别超时：图片模糊或手写内容过多，建议上传清晰的印刷版文档',
  },
])

/* ---------- 统计 ---------- */
const stats = computed(() => ({
  total: processingList.value.length + completedList.value.length + failedList.value.length,
  success: completedList.value.length,
  processing: processingList.value.length,
  failed: failedList.value.length,
}))

function tabCount(key: 'processing' | 'completed' | 'failed'): number {
  if (key === 'processing') return processingList.value.length
  if (key === 'completed') return completedList.value.length
  return failedList.value.length
}

/* ---------- 工具函数 ---------- */
function fileIcon(type: TaskType): string {
  const map: Record<TaskType, string> = {
    ppt: '📊',
    pdf: '📄',
    image: '🖼️',
    doc: '📝',
    excel: '📈',
  }
  return map[type] || '📁'
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB'
}

/* ---------- 进度模拟 ---------- */
let progressTimer: number | null = null

function startProgressSimulation() {
  if (progressTimer) return
  progressTimer = window.setInterval(() => {
    processingList.value = processingList.value.map((task) => {
      // 随机增加进度
      const increment = Math.random() * 3 + 0.5
      let newProgress = Math.min(task.progress + increment, 99)

      // 根据进度更新当前阶段
      const stageProgress = newProgress / 100
      const totalStages = stages.length - 1 // 最后是"完成"
      let newStage = Math.min(Math.floor(stageProgress * totalStages), totalStages - 1)

      // 计算预计剩余时间
      const remainingPercent = 100 - newProgress
      const speed = increment / 1.5 // 每 1.5 秒增加 increment
      const remainingSeconds = Math.ceil(remainingPercent / speed)
      let eta = ''
      if (remainingSeconds < 60) eta = `约 ${remainingSeconds} 秒`
      else eta = `约 ${Math.ceil(remainingSeconds / 60)} 分钟`

      return {
        ...task,
        progress: newProgress,
        currentStage: newStage,
        eta,
      }
    })
  }, 1500)
}

function stopProgressSimulation() {
  if (progressTimer) {
    window.clearInterval(progressTimer)
    progressTimer = null
  }
}

/* ---------- 操作 ---------- */
function cancelTask(id: string) {
  processingList.value = processingList.value.filter((t) => t.id !== id)
}

function viewResource(id: string) {
  emit('view-resource', id)
}

function reparseTask(id: string) {
  const task = completedList.value.find((t) => t.id === id)
  if (!task) return
  // 移到处理中
  completedList.value = completedList.value.filter((t) => t.id !== id)
  processingList.value.unshift({
    id: `${id}-reparse-${Date.now()}`,
    name: task.name,
    type: task.type,
    size: 5 * 1024 * 1024,
    startedAt: '刚刚开始',
    currentStage: 0,
    progress: 0,
    eta: '约 3 分钟',
  })
}

function retryTask(id: string) {
  const task = failedList.value.find((t) => t.id === id)
  if (!task) return
  failedList.value = failedList.value.filter((t) => t.id !== id)
  processingList.value.unshift({
    id: `${id}-retry-${Date.now()}`,
    name: task.name,
    type: task.type,
    size: 3 * 1024 * 1024,
    startedAt: '刚刚开始',
    currentStage: 0,
    progress: 0,
    eta: '约 3 分钟',
  })
}

function deleteTask(id: string) {
  failedList.value = failedList.value.filter((t) => t.id !== id)
}

/* ---------- 生命周期 ---------- */
onMounted(() => {
  if (props.visible) startProgressSimulation()
})

onBeforeUnmount(() => {
  stopProgressSimulation()
})

// 监听 visible 变化控制定时器
import { watch } from 'vue'
watch(() => props.visible, (v) => {
  if (v) startProgressSimulation()
  else stopProgressSimulation()
})
</script>

<style scoped>
/* ==========================================================================
   遮罩与对话框
   ========================================================================== */
.aip-scrim {
  position: fixed;
  inset: 0;
  z-index: 1000;
  background: rgba(7, 26, 50, 0.35);
  backdrop-filter: blur(3px);
  display: grid;
  place-items: center;
}

.aip-fade-enter-active,
.aip-fade-leave-active {
  transition: opacity 0.25s ease;
}
.aip-fade-enter-from,
.aip-fade-leave-to {
  opacity: 0;
}

.aip-dialog {
  width: 720px;
  max-width: 94vw;
  max-height: 86vh;
  background: #fff;
  border-radius: 16px;
  box-shadow: 0 20px 60px rgba(10, 30, 58, 0.25);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.aip-zoom-enter-active,
.aip-zoom-leave-active {
  transition: all 0.3s cubic-bezier(0.34, 1.56, 0.64, 1);
}
.aip-zoom-enter-from,
.aip-zoom-leave-to {
  opacity: 0;
  transform: scale(0.92) translateY(10px);
}

/* ==========================================================================
   标题栏
   ========================================================================== */
.aip-head {
  display: flex;
  align-items: center;
  padding: 18px 24px 16px;
  border-bottom: 1px solid var(--tv3-line2);
  background: linear-gradient(135deg, #fdf8ec 0%, #fff 60%);
}

.aip-head__brand {
  display: flex;
  align-items: center;
  gap: 12px;
  flex: 1;
}

.aip-head__icon {
  width: 44px;
  height: 44px;
  border-radius: 12px;
  background: linear-gradient(135deg, var(--tv3-gold), var(--tv3-gold-deep));
  display: grid;
  place-items: center;
  font-size: 22px;
  flex-shrink: 0;
  box-shadow: var(--tv3-shadow-gold);
}

.aip-head__text {
  min-width: 0;
}

.aip-head__title {
  font-size: 17px;
  font-weight: 800;
  color: var(--tv3-ink);
  line-height: 1.2;
}

.aip-head__sub {
  font-size: 12px;
  color: var(--tv3-ink3);
  margin-top: 4px;
}

.aip-head__close {
  width: 36px;
  height: 36px;
  border: none;
  background: transparent;
  border-radius: 10px;
  cursor: pointer;
  font-size: 22px;
  font-weight: 500;
  color: var(--tv3-ink3);
  display: grid;
  place-items: center;
  transition: all 0.15s ease;
}

.aip-head__close:hover {
  background: var(--tv3-rose-soft);
  color: var(--tv3-rose);
}

/* ==========================================================================
   统计概览
   ========================================================================== */
.aip-stats {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 12px;
  padding: 16px 24px;
  border-bottom: 1px solid var(--tv3-line2);
  background: #fbfcfe;
}

.aip-stat-card {
  background: #fff;
  border: 1px solid var(--tv3-line);
  border-radius: 12px;
  padding: 14px 12px;
  text-align: center;
  transition: all 0.2s ease;
}

.aip-stat-card:hover {
  transform: translateY(-2px);
  box-shadow: var(--tv3-shadow-sm);
}

.aip-stat-card__num {
  font-size: 26px;
  font-weight: 800;
  font-family: var(--tv3-font-num);
  color: var(--tv3-ink);
  line-height: 1.1;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
}

.aip-stat-card__label {
  font-size: 12px;
  color: var(--tv3-ink3);
  margin-top: 6px;
  font-weight: 500;
}

.aip-stat-card--success .aip-stat-card__num {
  color: var(--tv3-teal);
}

.aip-stat-card--processing .aip-stat-card__num {
  color: var(--tv3-primary);
}

.aip-stat-card__pulse {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--tv3-primary);
  display: inline-block;
  animation: aip-pulse 1.5s ease-in-out infinite;
}

@keyframes aip-pulse {
  0%, 100% {
    opacity: 1;
    box-shadow: 0 0 0 0 rgba(79, 70, 229, 0.5);
  }
  50% {
    opacity: 0.7;
    box-shadow: 0 0 0 6px rgba(79, 70, 229, 0);
  }
}

.aip-stat-card--failed .aip-stat-card__num {
  color: var(--tv3-rose);
}

/* ==========================================================================
   Tabs
   ========================================================================== */
.aip-tabs {
  display: flex;
  gap: 4px;
  padding: 12px 24px 0;
  border-bottom: 1px solid var(--tv3-line);
  background: #fff;
}

.aip-tab {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 10px 18px;
  border: none;
  background: transparent;
  border-bottom: 2px solid transparent;
  cursor: pointer;
  font-size: 13px;
  color: var(--tv3-ink3);
  font-weight: 500;
  transition: all 0.18s ease;
  margin-bottom: -1px;
}

.aip-tab:hover {
  color: var(--tv3-ink2);
}

.aip-tab.is-on {
  color: var(--tv3-gold-deep);
  font-weight: 700;
  border-bottom-color: var(--tv3-gold);
}

.aip-tab__icon {
  font-size: 14px;
}

.aip-tab__count {
  font-size: 11px;
  font-family: var(--tv3-font-num);
  background: var(--tv3-bg2);
  color: var(--tv3-ink3);
  padding: 2px 8px;
  border-radius: 999px;
  font-weight: 600;
}

.aip-tab.is-on .aip-tab__count {
  background: var(--tv3-gold-soft);
  color: var(--tv3-gold-deep);
}

/* ==========================================================================
   内容区
   ========================================================================== */
.aip-content {
  flex: 1;
  overflow-y: auto;
  padding: 16px 24px 24px;
  background: #fbfcfe;
}

.aip-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

/* 空状态 */
.aip-empty {
  text-align: center;
  padding: 48px 20px;
  color: var(--tv3-ink3);
}

.aip-empty__icon {
  font-size: 44px;
  margin-bottom: 14px;
  opacity: 0.8;
}

.aip-empty__title {
  font-size: 15px;
  font-weight: 600;
  color: var(--tv3-ink2);
  margin-bottom: 6px;
}

.aip-empty__desc {
  font-size: 12.5px;
  line-height: 1.7;
}

/* ==========================================================================
   卡片
   ========================================================================== */
.aip-card {
  background: #fff;
  border: 1px solid var(--tv3-line);
  border-radius: 12px;
  padding: 16px 18px;
  transition: all 0.2s ease;
}

.aip-card:hover {
  box-shadow: var(--tv3-shadow-sm);
}

.aip-card--processing {
  border-left: 3px solid var(--tv3-primary);
}

.aip-card--completed {
  border-left: 3px solid var(--tv3-teal);
}

.aip-card--failed {
  border-left: 3px solid var(--tv3-rose);
}

.aip-card__head {
  margin-bottom: 14px;
}

.aip-card__actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 14px;
  padding-top: 14px;
  border-top: 1px solid var(--tv3-line2);
}

/* 文件信息 */
.aip-file {
  display: flex;
  align-items: center;
  gap: 12px;
}

.aip-file__icon {
  width: 40px;
  height: 40px;
  border-radius: 10px;
  background: var(--tv3-gold-soft);
  display: grid;
  place-items: center;
  font-size: 20px;
  flex-shrink: 0;
}

.aip-file__info {
  flex: 1;
  min-width: 0;
}

.aip-file__name {
  font-size: 14px;
  font-weight: 600;
  color: var(--tv3-ink);
  line-height: 1.3;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.aip-file__meta {
  font-size: 12px;
  color: var(--tv3-ink3);
  margin-top: 4px;
  display: flex;
  align-items: center;
  gap: 6px;
}

.aip-file__status {
  color: var(--tv3-teal);
  font-weight: 600;
}

.aip-file__status--failed {
  color: var(--tv3-rose);
}

.aip-file__dot {
  color: var(--tv3-ink4);
}

/* ==========================================================================
   时间线
   ========================================================================== */
.aip-timeline {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  padding: 10px 0 4px;
  position: relative;
}

.aip-timeline__node {
  display: flex;
  flex-direction: column;
  align-items: center;
  flex: 1;
  position: relative;
}

.aip-timeline__dot {
  width: 20px;
  height: 20px;
  border-radius: 50%;
  display: grid;
  place-items: center;
  font-size: 10px;
  font-weight: 700;
  background: #fff;
  border: 2px solid var(--tv3-ink4);
  color: var(--tv3-ink4);
  z-index: 2;
  transition: all 0.3s ease;
}

.aip-timeline__node.is-done .aip-timeline__dot {
  background: var(--tv3-teal);
  border-color: var(--tv3-teal);
  color: #fff;
}

.aip-timeline__node.is-current .aip-timeline__dot {
  background: var(--tv3-primary);
  border-color: var(--tv3-primary);
  color: #fff;
}

.aip-timeline__pulse {
  display: block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #fff;
  animation: aip-dot-pulse 1.5s ease-in-out infinite;
}

@keyframes aip-dot-pulse {
  0%, 100% {
    box-shadow: 0 0 0 0 rgba(255, 255, 255, 0.5);
    transform: scale(1);
  }
  50% {
    box-shadow: 0 0 0 4px rgba(255, 255, 255, 0);
    transform: scale(1.15);
  }
}

.aip-timeline__label {
  font-size: 10.5px;
  color: var(--tv3-ink3);
  margin-top: 6px;
  text-align: center;
  white-space: nowrap;
  max-width: 80px;
  overflow: hidden;
  text-overflow: ellipsis;
  line-height: 1.3;
}

.aip-timeline__node.is-done .aip-timeline__label,
.aip-timeline__node.is-current .aip-timeline__label {
  color: var(--tv3-ink2);
  font-weight: 500;
}

.aip-timeline__line {
  position: absolute;
  top: 9px;
  left: 50%;
  width: 100%;
  height: 2px;
  background: var(--tv3-ink4);
  z-index: 1;
}

.aip-timeline__node.is-done .aip-timeline__line {
  background: var(--tv3-teal);
}

/* ==========================================================================
   进度条
   ========================================================================== */
.aip-progress {
  margin-top: 14px;
}

.aip-progress__bar {
  height: 6px;
  border-radius: 999px;
  background: linear-gradient(90deg, var(--tv3-gold), var(--tv3-gold-deep));
  transition: width 0.6s cubic-bezier(0.4, 0, 0.2, 1);
  box-shadow: 0 1px 4px rgba(6, 182, 212, 0.3);
  position: relative;
  overflow: hidden;
}

.aip-progress__bar::after {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: linear-gradient(
    90deg,
    transparent 0%,
    rgba(255, 255, 255, 0.4) 50%,
    transparent 100%
  );
  animation: aip-shine 2s ease-in-out infinite;
}

@keyframes aip-shine {
  0% { transform: translateX(-100%); }
  100% { transform: translateX(100%); }
}

.aip-progress__info {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: 8px;
  font-size: 11.5px;
}

.aip-progress__percent {
  font-family: var(--tv3-font-num);
  font-weight: 700;
  color: var(--tv3-gold-deep);
}

.aip-progress__eta {
  color: var(--tv3-ink3);
}

/* 进度条背景槽 */
.aip-progress {
  position: relative;
}

.aip-progress::before {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 6px;
  border-radius: 999px;
  background: var(--tv3-bg2);
}

.aip-progress__bar {
  position: relative;
  z-index: 1;
}

/* ==========================================================================
   标签（已完成卡片）
   ========================================================================== */
.aip-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 12px;
}

.aip-tag {
  font-size: 11px;
  font-weight: 600;
  padding: 3px 10px;
  border-radius: 999px;
  border: 1px solid var(--tv3-gold-border);
  background: var(--tv3-gold-soft);
  color: var(--tv3-gold-deep);
  transition: all 0.15s ease;
}

.aip-tag:hover {
  transform: translateY(-1px);
}

.aip-tag[data-type='subject'] {
  background: #eef4fb;
  border-color: #c3d6f2;
  color: var(--tv3-primary);
}

.aip-tag[data-type='grade'] {
  background: var(--tv3-teal-soft);
  border-color: var(--tv3-teal-border);
  color: var(--tv3-teal);
}

.aip-tag[data-type='category'] {
  background: #f3eefd;
  border-color: #ddd0f8;
  color: var(--tv3-ai-deep);
}

.aip-tag[data-type='knowledge'] {
  background: var(--tv3-gold-soft);
  border-color: var(--tv3-gold-border);
  color: var(--tv3-gold-deep);
}

/* ==========================================================================
   摘要
   ========================================================================== */
.aip-summary {
  font-size: 12.5px;
  color: var(--tv3-ink2);
  line-height: 1.7;
  padding: 10px 12px;
  background: var(--tv3-bg2);
  border-radius: 8px;
}

.aip-summary__label {
  font-weight: 700;
  color: var(--tv3-gold-deep);
  margin-right: 6px;
}

/* ==========================================================================
   失败原因
   ========================================================================== */
.aip-error {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 10px 12px;
  background: var(--tv3-rose-soft);
  border-radius: 8px;
  font-size: 12.5px;
  color: var(--tv3-rose);
  line-height: 1.6;
}

.aip-error__icon {
  flex-shrink: 0;
  font-size: 14px;
  margin-top: 1px;
}

.aip-error__text {
  flex: 1;
}

/* ==========================================================================
   按钮
   ========================================================================== */
.aip-btn {
  border: 1px solid var(--tv3-line);
  background: #fff;
  border-radius: 8px;
  padding: 6px 14px;
  font-size: 12px;
  cursor: pointer;
  color: var(--tv3-ink2);
  transition: all 0.15s ease;
  font-weight: 500;
}

.aip-btn:hover {
  transform: translateY(-1px);
}

.aip-btn--ghost {
  background: transparent;
  border-color: var(--tv3-line);
}

.aip-btn--ghost:hover {
  border-color: var(--tv3-navy);
  color: var(--tv3-navy);
  background: var(--tv3-primary-soft);
}

.aip-btn--danger {
  color: var(--tv3-rose);
}

.aip-btn--danger:hover {
  border-color: var(--tv3-rose);
  background: var(--tv3-rose-soft);
  color: var(--tv3-rose);
}

.aip-btn--primary {
  background: linear-gradient(135deg, var(--tv3-gold), var(--tv3-gold-deep));
  border-color: var(--tv3-gold);
  color: #fff;
  font-weight: 600;
  box-shadow: 0 2px 8px rgba(6, 182, 212, 0.25);
}

.aip-btn--primary:hover {
  box-shadow: 0 4px 14px rgba(6, 182, 212, 0.35);
  color: #fff;
}

.aip-btn--primary:active {
  transform: translateY(0);
}

/* ==========================================================================
   卡片列表动画
   ========================================================================== */
.aip-card-enter-active,
.aip-card-leave-active {
  transition: all 0.25s ease;
}

.aip-card-enter-from {
  opacity: 0;
  transform: translateY(8px);
}

.aip-card-leave-to {
  opacity: 0;
  transform: translateX(20px);
}

.aip-card-move {
  transition: transform 0.3s ease;
}

/* ==========================================================================
   滚动条美化
   ========================================================================== */
.aip-content::-webkit-scrollbar {
  width: 6px;
}

.aip-content::-webkit-scrollbar-track {
  background: transparent;
}

.aip-content::-webkit-scrollbar-thumb {
  background: var(--tv3-ink4);
  border-radius: 999px;
}

.aip-content::-webkit-scrollbar-thumb:hover {
  background: var(--tv3-ink3);
}
</style>
