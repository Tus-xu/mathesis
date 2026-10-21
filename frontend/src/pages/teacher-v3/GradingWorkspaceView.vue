<template>
  <div class="tv3-page-shell" data-testid="tv3-grading-workspace">
    <TeacherPageHeader title="批改工作台" subtitle="题聚焦 · 匿名队列 · 原图对照 · 教师终审唯一写分" icon="✍️">
      <select v-if="classes.length" v-model="pickClass" class="tv3-input" style="width: 150px; background: #fff; border-color: var(--tv3-line); color: var(--tv3-ink)" data-testid="tv3-gw-class">
        <option v-for="c in classes" :key="c.id" :value="c.id">{{ c.name }}</option>
      </select>
      <button class="tv3-btn tv3-btn--sm" data-testid="tv3-gw-review-insights" @click="loadReviewInsights">讲评要点</button>
    </TeacherPageHeader>

    <!-- 无班级：引导态 -->
    <TeacherEmptyState
      v-if="!classesLoading && !classes.length"
      title="还没有班级"
      desc="批改发生在班级内。先到班级管理建班，学生加入并提交作业后，这里会出现待批队列。"
      cta="去班级管理"
      @cta-click="$router.push('/teacher-v3/classes')"
    />

    <template v-else>
      <!-- 顶部：作业切换 + 题目切换 + 状态过滤 + 进度 -->
      <div class="tv3-card" style="margin-bottom: 14px" data-testid="tv3-gw-context">
        <div class="tv3-card__body" style="display: flex; gap: 10px; align-items: center; flex-wrap: wrap">
          <span v-if="ws?.context.class?.label" class="tv3-tag tv3-tag--primary">{{ ws.context.class.label }}</span>
          <select v-if="(ws?.available_context.assignments.length || 0) > 0" v-model="pickAssignment" class="tv3-input" style="max-width: 300px; background: #fff; border-color: var(--tv3-line); color: var(--tv3-ink)" data-testid="tv3-gw-assignment">
            <option v-for="a in ws?.available_context.assignments" :key="a.assignment_id" :value="a.assignment_id">{{ a.title }}</option>
          </select>
          <div class="tv3-seg" data-testid="tv3-gw-question-tabs">
            <button
              v-for="q in ws?.available_context.questions" :key="q.item_no"
              class="tv3-seg__btn" :class="{ 'is-active': ws?.context.question?.item_no === q.item_no }"
              :title="q.question_text || ''"
              @click="pickQuestion(q.item_no)"
            >{{ q.label }}</button>
          </div>
          <div class="tv3-seg" data-testid="tv3-gw-status-filter">
            <button v-for="s in STATUS_OPTIONS" :key="s.value" class="tv3-seg__btn" :class="{ 'is-active': statusFilter === s.value }" @click="statusFilter = s.value">{{ s.label }}</button>
          </div>
          <div class="tv3-card__spacer" />
          <span style="font-size: 12px; color: var(--tv3-ink3)" data-testid="tv3-gw-progress">
            已批 {{ ws?.context.progress.confirmed ?? 0 }} / {{ ws?.context.progress.total ?? 0 }} · 待批 {{ ws?.context.progress.remaining ?? 0 }}
          </span>
        </div>
      </div>

      <TeacherLoading v-if="wsLoading" />

      <!-- 空队列（诚实空态：区分无作业 / 无作答 / 全批完） -->
      <TeacherEmptyState
        v-else-if="!ws || !ws.queue.length"
        :title="emptyTitle"
        :desc="emptyDesc"
      />

      <div v-else style="display: grid; grid-template-columns: 320px 1fr; gap: 14px; align-items: start">
        <!-- 左：匿名队列 -->
        <div class="tv3-card" data-testid="tv3-gw-queue">
          <div class="tv3-card__head">
            <span class="tv3-card__title">作答队列</span>
            <span class="tv3-card__sub">默认匿名 · 批完自动跳下一份</span>
          </div>
          <div class="tv3-card__body" style="display: flex; flex-direction: column; gap: 6px">
            <div
              v-for="entry in ws.queue" :key="entry.submission_item_id"
              class="tv3-row" :class="{ 'is-selected': entry.submission_item_id === currentId }" style="cursor: pointer"
              :data-testid="`tv3-gw-queue-${entry.submission_item_id}`"
              @click="selectItem(entry.submission_item_id)"
            >
              <input
                v-if="entry.state !== 'confirmed' && entry.state !== 'auto_graded'" type="checkbox" style="accent-color: var(--tv3-primary)"
                :checked="batchSel.has(entry.submission_item_id)"
                @click.stop="toggleBatch(entry.submission_item_id)"
              >
              <div style="flex: 1; min-width: 0">
                <div style="font-size: 13px; font-weight: 600">{{ entry.anonymous_label }}</div>
                <div v-if="suggestionScoreOf(entry)" style="font-size: 11px; color: var(--tv3-ink3)">建议 {{ suggestionScoreOf(entry) }} 分{{ finalScoreOf(entry) != null ? ` · 终分 ${finalScoreOf(entry)}` : '' }}</div>
              </div>
              <span class="tv3-tag" :class="stateClass(entry.state)" style="font-size: 10px" :data-testid="`tv3-gw-state-${entry.state}`">{{ stateLabel(entry.state) }}</span>
            </div>
          </div>
        </div>

        <!-- 右：题聚焦批改 -->
        <div style="display: flex; flex-direction: column; gap: 14px; min-width: 0">
          <div v-if="sel" class="tv3-card" :key="sel.submission_item_id" data-testid="tv3-gw-detail">
            <div class="tv3-card__head">
              <span class="tv3-card__title">{{ queueLabel(sel.submission_item_id) }}</span>
              <span v-if="qTypeLabel" class="tv3-tag">{{ qTypeLabel }}</span>
              <div class="tv3-card__spacer" />
              <button class="tv3-btn tv3-btn--sm" :disabled="!ws?.navigation.previous_id" data-testid="tv3-gw-prev" @click="navTo(ws?.navigation.previous_id)">← 上一份</button>
              <button class="tv3-btn tv3-btn--sm tv3-btn--primary" :disabled="!ws?.navigation.next_ungraded_id" data-testid="tv3-gw-next" @click="navTo(ws?.navigation.next_ungraded_id)">下一份待批 →</button>
            </div>

            <div class="tv3-card__body" style="display: flex; flex-direction: column; gap: 12px">
              <!-- 题干与参考答案 -->
              <div v-if="ws?.context.question?.question_text" style="font-size: 13.5px; line-height: 1.7" v-html="renderStem(ws.context.question.question_text)" />
              <div class="tv3-prep__diffitem" data-testid="tv3-gw-standard" style="border-color: var(--tv3-teal, #0e9488); background: #f4fbfa">
                <div style="font-size: 12.5px; line-height: 1.7">
                  <b>参考答案：</b><span v-if="sel.scoring.standard_answer" v-html="renderStem(sel.scoring.standard_answer)" /><span v-else style="color: var(--tv3-ink3)">（题库未设参考答案）</span>
                </div>
                <div v-if="sel.scoring.answer_analysis" style="font-size: 12px; color: var(--tv3-ink2); line-height: 1.7; margin-top: 4px">
                  <b>解析：</b><span v-html="renderStem(sel.scoring.answer_analysis)" />
                </div>
                <div style="margin-top: 6px">
                  <template v-if="sel.scoring.rubric_status === 'ready' && sel.scoring.rubric_items.length">
                    <span v-for="(r, i) in sel.scoring.rubric_items" :key="r.id" class="tv3-tag" style="font-size: 10.5px; background: #fff; margin-right: 5px">
                      评分点 {{ i + 1 }}：{{ r.criterion }}（{{ r.points }} 分）
                    </span>
                  </template>
                  <span v-else class="tv3-tag tv3-tag--warn" style="font-size: 10.5px" data-testid="tv3-gw-rubric-missing">未设量规（按参考答案整体给分）</span>
                </div>
              </div>

              <div style="display: grid; grid-template-columns: minmax(240px, 340px) 1fr; gap: 14px; align-items: start">
                <!-- 学生手写原图（鉴权 blob，不出域） -->
                <div>
                  <div class="tv3-form-label">学生作答原图</div>
                  <!-- tel E2E 修复：拍照作答文件在 attachments（file_id 列恒空）——有任一即展示原图块 -->
                  <div v-if="sel.work.file_id || (sel.work.attachments?.length ?? 0) > 0" class="gw-photo" data-testid="tv3-gw-photo">
                    <TeacherLoading v-if="photoLoading" />
                    <img v-else-if="photoUrl" :src="photoUrl" alt="学生手写作答原图" data-testid="tv3-gw-photo-img">
                    <div v-else class="gw-photo__empty" data-testid="tv3-gw-photo-missing">原图读取失败（文件已清理？）请按作答文本批改</div>
                  </div>
                  <div v-else class="gw-photo gw-photo--none" data-testid="tv3-gw-no-photo">
                    <div style="font-size: 12px; color: var(--tv3-ink3); line-height: 1.8">
                      <template v-if="sel.work.original_answer">文本作答（原文）：</template>
                      <template v-else>该份作答未上传照片、未填写文本</template>
                    </div>
                  </div>
                  <div v-if="sel.work.original_answer" class="tv3-prep__diffitem" style="margin-top: 8px; font-size: 12.5px; line-height: 1.7; white-space: pre-wrap" data-testid="tv3-gw-answer-text">{{ sel.work.original_answer }}</div>
                </div>

                <!-- 打分区 -->
                <div style="display: flex; flex-direction: column; gap: 10px">
                  <!-- AI / 本地建议（仅参考） -->
                  <div v-if="sel.suggestion.suggestion_id" class="tv3-prep__diffitem" style="background: var(--tv3-gold-soft, #fdf6e3)" data-testid="tv3-gw-suggestion">
                    <div style="display: flex; gap: 8px; align-items: center; flex-wrap: wrap">
                      <span class="tv3-tag tv3-tag--ai" style="font-size: 10px">{{ suggestionTag }}</span>
                      <b style="font-family: var(--tv3-font-num)">建议 {{ sel.suggestion.proposed_score ?? '—' }} 分</b>
                      <span v-if="sel.scoring.max_score != null" style="font-size: 11.5px; color: var(--tv3-ink3)">/ 满分 {{ sel.scoring.max_score }}</span>
                    </div>
                    <div v-for="(ev, i) in sel.suggestion.evidence" :key="i" style="font-size: 11.5px; color: var(--tv3-ink2); line-height: 1.6; margin-top: 4px">{{ ev.text }}</div>
                    <div style="margin-top: 6px">
                      <button class="tv3-btn tv3-btn--sm" :disabled="suggesting" data-testid="tv3-gw-ai-suggest" @click="genAiSuggestion">
                        {{ suggesting ? '生成中…' : '✦ 生成 AI 建议（解答题触发 AI 初批，仅参考）' }}
                      </button>
                    </div>
                  </div>
                  <div v-else class="tv3-prep__diffitem" style="background: var(--tv3-bg2)">
                    <div style="display: flex; gap: 8px; align-items: center; flex-wrap: wrap">
                      <span style="font-size: 12px; color: var(--tv3-ink3)">暂无评分建议</span>
                      <button class="tv3-btn tv3-btn--sm" :disabled="suggesting" @click="genAiSuggestion">{{ suggesting ? '生成中…' : '生成本地/AI 建议' }}</button>
                    </div>
                  </div>

                  <!-- 教师给分 + 评语 → 确认（唯一正式分入口） -->
                  <template v-if="!sel.confirmed_decision">
                    <div class="tv3-form-label">教师终审给分（必填）</div>
                    <div style="display: flex; gap: 8px; align-items: center">
                      <input
                        v-model.number="draftScore" type="number" :min="0" :max="sel.scoring.max_score ?? undefined" step="0.5"
                        class="tv3-input" style="width: 110px; background: #fff; border-color: var(--tv3-line); color: var(--tv3-ink)"
                        data-testid="tv3-gw-score"
                      >
                      <span style="font-size: 12px; color: var(--tv3-ink3)">{{ sel.scoring.max_score != null ? `/ ${sel.scoring.max_score} 分` : '/ 未设满分' }}</span>
                      <span v-if="scoreError" class="tv3-tag tv3-tag--danger" style="font-size: 10.5px" data-testid="tv3-gw-score-error">{{ scoreError }}</span>
                    </div>
                    <div class="tv3-form-label">评语（将展示给学生）</div>
                    <textarea v-model="draftFeedback" class="tv3-textarea" rows="3" style="font-size: 12.5px" placeholder="如：第二问设参思路对，计算丢负号；建议…（可留空）" data-testid="tv3-gw-feedback" />
                    <div style="display: flex; justify-content: flex-end; gap: 8px">
                      <button class="tv3-btn tv3-btn--primary" :disabled="confirming || !draftValid" data-testid="tv3-gw-confirm" @click="confirmOne">
                        {{ confirming ? '提交中…' : '✓ 确认批改（写入终分）' }}
                      </button>
                    </div>
                  </template>
                  <div v-else class="tv3-prep__diffitem" style="background: var(--tv3-teal-soft, #eefaf7)" data-testid="tv3-gw-confirmed">
                    <span class="tv3-tag tv3-tag--ok">✓ 已终审</span>
                    <b style="font-family: var(--tv3-font-num); margin-left: 6px">终分 {{ sel.confirmed_decision.final_score }}</b>
                    <span v-if="sel.confirmed_decision.feedback" style="font-size: 12px; color: var(--tv3-ink2); margin-left: 8px">{{ sel.confirmed_decision.feedback }}</span>
                  </div>
                </div>
              </div>
            </div>
          </div>

          <!-- 讲评要点 -->
          <div v-if="reviewInsights" class="tv3-card" data-testid="tv3-gw-review-insights">
            <div class="tv3-card__head">
              <span class="tv3-card__title">讲评要点 · {{ reviewInsights.title || '最近已批作业' }}</span>
              <span class="tv3-card__sub">确定性聚合（判错口径 verdict=wrong）· 无编造</span>
              <div class="tv3-card__spacer" />
              <button class="tv3-btn tv3-btn--sm" @click="reviewInsights = null">×</button>
            </div>
            <div class="tv3-card__body" style="display: flex; flex-direction: column; gap: 8px">
              <div v-if="!reviewInsights.top_questions.length" style="font-size: 12.5px; color: var(--tv3-ink3)">最近一份作业还没有可讲评的错误数据。</div>
              <div v-for="(tq, i) in reviewInsights.top_questions" :key="i" class="tv3-row" style="align-items: flex-start">
                <span class="tv3-tag tv3-tag--danger" style="font-size: 10.5px; flex-shrink: 0">{{ tq.wrong_count }}/{{ tq.graded_count }} 判错</span>
                <div style="flex: 1; min-width: 0; font-size: 12.5px; line-height: 1.6" v-html="renderStem(tq.question_text)" />
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- 底部批量确认条 -->
      <div v-if="batchSel.size" class="gw-batch" data-testid="tv3-gw-batch-bar">
        <span style="font-size: 12.5px">已选 <b>{{ batchSel.size }}</b> 份 · 按各自建议分/自动分确认</span>
        <span style="flex: 1" />
        <button class="tv3-btn tv3-btn--sm" @click="batchSel = new Set()">取消选择</button>
        <button class="tv3-btn tv3-btn--sm tv3-btn--primary" :disabled="batchConfirming" data-testid="tv3-gw-batch-confirm" @click="confirmBatch">
          {{ batchConfirming ? '批量确认中…' : `✓ 批量确认 ${batchSel.size} 份` }}
        </button>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
/**
 * GradingWorkspaceView —— 批改工作台（tel PLAN §1.2③）
 * 数据面 = 8000 /api/teacher/grading/*（题聚焦投影 grading.py:626-889）：
 * - 队列四态 ungraded/review/auto_graded/confirmed（tel F3：选择/判断题系统自动批阅
 *   直接可见、无需逐题人审），匿名「第 N 份作答」；
 * - 选择题自动判分直接可见（建议分=自动分），批量确认兜底（batch-confirm 逐项独立校验）；
 * - 主观题：原图（鉴权 blob，不出域）+ 量规（缺失如实「未设量规」）+ AI 建议仅参考
 *   （教师显式 POST suggest 触发，D9）+ 教师手动给分 → confirm 唯一正式分入口；
 * - 无编造：无班级/无作业/无作答/全批完各自诚实空态。
 */
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { api8000, telKey, type ApiClassItem, type ApiGradingQueueItem, type ApiGradingReviewInsights, type ApiGradingWorkspace } from '@/api/api8000'
import { TeacherPageHeader, TeacherEmptyState, TeacherLoading } from '@/components/teacherV3/common'
import { renderStem } from '@/components/mathx/latex'
import { useToastStore } from '@/stores/toast'
import { teachingClasses } from '@/utils/teachingClasses'

const STATUS_OPTIONS = [
  { value: 'all', label: '全部' },
  { value: 'ungraded', label: '待批' },
  { value: 'review', label: '需复核' },
  { value: 'auto_graded', label: '自动批' },
  { value: 'confirmed', label: '已批' },
] as const

const toast = useToastStore()
const classes = ref<ApiClassItem[]>([])
const classesLoading = ref(true)
const pickClass = ref('')
const pickAssignment = ref('')
const statusFilter = ref<string>('all')
const ws = ref<ApiGradingWorkspace | null>(null)
const wsLoading = ref(false)
const wsLoadError = ref('')

const currentId = computed(() => ws.value?.selected?.submission_item_id ?? '')
const sel = computed(() => ws.value?.selected ?? null)

/** 队列分数快照（grading/queue 端点）：让选择题自动判分在队列中直接可见（§1.2③⑥） */
const queueScores = ref<Map<string, { suggestion: number | null; final: number | null }>>(new Map())

/* 教师输入草稿（按 item 缓存，切队列不丢输入；确认才落库） */
const drafts = ref<Record<string, { score: number | null; feedback: string }>>({})
const draftScore = computed<number | null>({
  get: () => drafts.value[currentId.value]?.score ?? sel.value?.suggestion.proposed_score ?? null,
  set: (v) => {
    const d = drafts.value[currentId.value] ?? { score: null, feedback: '' }
    d.score = Number.isFinite(v) ? v : null
    drafts.value[currentId.value] = d
  },
})
const draftFeedback = computed<string>({
  get: () => drafts.value[currentId.value]?.feedback ?? '',
  set: (v) => {
    const d = drafts.value[currentId.value] ?? { score: null, feedback: '' }
    d.feedback = v
    drafts.value[currentId.value] = d
  },
})

const scoreError = computed(() => {
  if (!sel.value) return ''
  const max = sel.value.scoring.max_score
  const s = draftScore.value
  if (s == null) return '请输入分数'
  if (s < 0) return '分数不能为负'
  if (max != null && s > max) return `不能超过满分 ${max}`
  return ''
})
const draftValid = computed(() => !!sel.value?.suggestion.suggestion_id && !scoreError.value)
const confirming = ref(false)
const suggesting = ref(false)

/* 原图 blob（鉴权读取，出组件即回收） */
const photoUrl = ref('')
const photoLoading = ref(false)
let lastPhotoItem = ''

const reviewInsights = ref<ApiGradingReviewInsights | null>(null)

const batchSel = ref<Set<string>>(new Set())
const batchConfirming = ref(false)

const qTypeLabel = computed(() => {
  const t = ws.value?.context.question?.q_type
  return ({ choice: '选择题', judge: '判断题', blank: '填空题', solution: '解答题' } as Record<string, string>)[t || ''] || ''
})
const suggestionTag = computed(() => (sel.value?.suggestion.review_needed ? 'AI/本地建议 · 仅参考' : '规则自动判分'))

const emptyTitle = computed(() => {
  if (!ws.value) return '暂无可批改内容'
  if (!ws.value.available_context.assignments.length) return '没有已发布的作业'
  if (!ws.value.context.progress.total) return '这份作业还没有学生作答'
  if (statusFilter.value !== 'all' && !ws.value.queue.length) return '该过滤条件下没有作答'
  return '该题暂无作答'
})
const emptyDesc = computed(() => {
  if (!ws.value) return wsLoadError.value || '先在「作业发布」发布一份作业，学生提交后这里会出现批改队列。'
  if (!ws.value.available_context.assignments.length) return '去「作业发布」用四步编排发布学前作业或课后习题。'
  if (!ws.value.context.progress.total) return '发布后学生可在课堂任务中心作答；提交到达时自动进入队列。'
  return '试试切换题目、状态过滤，或选择其他作业。'
})

function stateLabel(s: string): string {
  return ({ ungraded: '待批', review: '需复核', auto_graded: '自动批', confirmed: '已批' } as Record<string, string>)[s] || s
}
function stateClass(s: string): string {
  return ({ ungraded: 'tv3-tag--warn', review: 'tv3-tag--danger', auto_graded: 'tv3-tag--ok', confirmed: 'tv3-tag--ok' } as Record<string, string>)[s] || ''
}
function queueLabel(id: string): string {
  return ws.value?.queue.find((q) => q.submission_item_id === id)?.anonymous_label || '作答'
}
function suggestionScoreOf(entry: ApiGradingQueueItem): number | null {
  if (entry.submission_item_id === currentId.value) return sel.value?.suggestion.proposed_score ?? null
  return queueScores.value.get(entry.submission_item_id)?.suggestion ?? null
}
function finalScoreOf(entry: ApiGradingQueueItem): number | null {
  if (entry.submission_item_id === currentId.value) return sel.value?.confirmed_decision?.final_score ?? null
  return queueScores.value.get(entry.submission_item_id)?.final ?? null
}

/** 切换聚焦题（同作业内）：以题号重取投影，无锚点（后端取该题首个未批） */
function pickQuestion(itemNo: number) {
  void api8000.grading.workspace({
    class_id: pickClass.value,
    assignment_id: pickAssignment.value || undefined,
    status: statusFilter.value,
    item_no: itemNo,
  }).then((r) => {
    ws.value = r
    void loadPhoto()
  }).catch((e: unknown) => toast.error(e instanceof Error ? e.message : '切换题目失败，请重试'))
}

async function loadClasses() {
  classesLoading.value = true
  try {
    const r = await api8000.classes.list()
    classes.value = teachingClasses(r.items)
    if (!pickClass.value && classes.value[0]) pickClass.value = classes.value[0].id
  } finally {
    classesLoading.value = false
  }
}

async function loadWorkspace(anchor?: string) {
  if (!pickClass.value) { ws.value = null; return }
  wsLoading.value = true
  wsLoadError.value = ''
  try {
    const r = await api8000.grading.workspace({
      class_id: pickClass.value,
      assignment_id: pickAssignment.value || undefined,
      status: statusFilter.value,
      // 锚点必须存在于当前过滤队列，否则后端 404——换过滤/换班时传 '' 重新取首个未批
      submission_item_id: (anchor !== undefined ? anchor : currentId.value) || undefined,
    })
    ws.value = r
    if (r.context.assignment && !pickAssignment.value) pickAssignment.value = r.context.assignment.assignment_id
  } catch (e) {
    ws.value = null
    wsLoadError.value = e instanceof Error ? e.message : '批改工作台加载失败'
    toast.error(wsLoadError.value)
  } finally {
    wsLoading.value = false
  }
  void refreshQueueScores()
  void loadPhoto()
}

async function refreshQueueScores() {
  if (!pickClass.value) return
  try {
    const r = await api8000.grading.queue(pickClass.value)
    const map = new Map<string, { suggestion: number | null; final: number | null }>()
    for (const q of r.queue) map.set(q.submission_item_id, { suggestion: q.suggestion_score, final: q.teacher_final_score })
    queueScores.value = map
  } catch { /* 队列分数仅增强显示；失败不阻断工作台 */ }
}

function selectItem(id: string) {
  if (!id || id === currentId.value) return
  // 换目标 item：以 submission_item_id 精确锚定（跨题时由后端换题聚焦）
  void api8000.grading.workspace({
    class_id: pickClass.value,
    assignment_id: pickAssignment.value || undefined,
    status: statusFilter.value,
    submission_item_id: id,
  }).then((r) => {
    ws.value = r
    if (r.context.question && r.context.assignment) {
      pickAssignment.value = r.context.assignment.assignment_id
    }
    void loadPhoto()
  }).catch((e: unknown) => toast.error(e instanceof Error ? e.message : '切换失败，请重试'))
}

function navTo(id: string | null | undefined) {
  if (id) selectItem(id)
}

async function loadPhoto() {
  // tel E2E 修复：file_id 列恒空时回退 attachments 第一张作答图（与后端 grading_file 兜底一致）
  const fileId = sel.value?.work.file_id || sel.value?.work.attachments?.[0]?.file_id
  const itemId = currentId.value
  if (!fileId || !itemId) {
    if (photoUrl.value) { URL.revokeObjectURL(photoUrl.value); photoUrl.value = '' }
    lastPhotoItem = itemId
    return
  }
  if (itemId === lastPhotoItem && photoUrl.value) return
  if (photoUrl.value) { URL.revokeObjectURL(photoUrl.value); photoUrl.value = '' }
  photoLoading.value = true
  lastPhotoItem = itemId
  try {
    const r = await api8000.grading.fetchFileBlob(itemId)
    photoUrl.value = URL.createObjectURL(r.blob)
  } catch {
    photoUrl.value = '' // 诚实呈现：原图读取失败不造假图
  } finally {
    photoLoading.value = false
  }
}

async function genAiSuggestion() {
  const item = sel.value
  const classId = ws.value?.context.class?.class_id
  if (!item || !classId) return
  suggesting.value = true
  try {
    await api8000.grading.suggest(item.submission_item_id, classId, telKey('gw-suggest'))
    toast.success('已生成新建议（仅供参考）；请核对后给分确认')
    await loadWorkspace()
  } catch (e) {
    toast.error(e instanceof Error ? `建议生成失败：${e.message}` : '建议生成失败')
  } finally {
    suggesting.value = false
  }
}

async function confirmOne() {
  const item = sel.value
  if (!item || !item.suggestion.suggestion_id || scoreError.value) return
  const score = draftScore.value as number
  // tel F7：accept 仅客观题可用（grading.py confirm：choice/judge + 证据一致才 accept，
  // 主观题 422 trusted_context_required）——主观题给分（含 0 分=建议分）恒走 override，
  // 否则「给 0 分」被前端锁死成 accept 而报错。
  const objective = ['choice', 'judge'].includes(ws.value?.context.question?.q_type || '')
  confirming.value = true
  try {
    await api8000.grading.confirm(item.submission_item_id, {
      suggestion_id: item.suggestion.suggestion_id,
      decision: objective && score === item.suggestion.proposed_score ? 'accept' : 'override',
      final_score: score,
      teacher_feedback: draftFeedback.value || undefined,
      version: item.suggestion.version || 1,
    })
    toast.success('已确认：终分与学生可见反馈生效')
    // 已批项在「待批/需复核」过滤下会退出队列——此时不带锚点重载（后端取首个未批），避免 404
    await loadWorkspace(statusFilter.value === 'all' ? currentId.value : '')
  } catch (e) {
    toast.error(e instanceof Error ? `确认失败：${e.message}` : '确认失败，请重试')
  } finally {
    confirming.value = false
  }
}

function toggleBatch(id: string) {
  const next = new Set(batchSel.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  batchSel.value = next
}

async function confirmBatch() {
  if (!batchSel.value.size || !ws.value) return
  // 批量确认：仅对「已批」态外、当前队列可见的项，以各自建议分（客观题=自动分）accept。
  // 教师改过分的项走单份 confirm（override），批量条不做静默改分。
  const byId = new Map(ws.value.queue.map((q) => [q.submission_item_id, q]))
  const targets = [...batchSel.value].filter((id) => byId.has(id))
  if (!targets.length) return
  if (!window.confirm(`批量确认 ${targets.length} 份：将按各自建议分/自动分写入终分（已批项自动跳过）。\n需要改分的请逐份终审。`)) return
  batchConfirming.value = true
  try {
    // 逐份取详情拿 suggestion_id（批量契约要求每项带 suggestion_id）；
    // tel F7：accept 仅客观题（choice/judge）——主观题/填空按建议分走 override
    // （批量条文案已明示「按各自建议分/自动分写入终分」，教师显式同意，不静默改分）。
    const items: { submission_item_id: string; suggestion_id: string; decision: 'accept' | 'override'; final_score?: number; version?: number }[] = []
    for (const id of targets) {
      try {
        const d = await api8000.grading.detail(id)
        if (d.suggestion?.suggestion_id && d.suggestion.suggestion_score != null) {
          const objective = ['choice', 'judge'].includes(d.question_type || '')
          items.push({
            submission_item_id: id,
            suggestion_id: d.suggestion.suggestion_id,
            decision: objective ? 'accept' : 'override',
            final_score: d.suggestion.suggestion_score,
            version: d.suggestion.version || 1,
          })
        }
      } catch { /* 单项取详情失败 → 跳过，结果计数如实呈现 */ }
    }
    if (!items.length) {
      toast.error('所选作答没有可用的建议分，无法批量确认；请逐份终审')
      return
    }
    const r = await api8000.grading.batchConfirm(items)
    const okCount = r.results.filter((x) => x.ok).length
    const fail = r.results.filter((x) => !x.ok)
    if (fail.length) toast.error(`批量确认：${okCount} 份成功，${fail.length} 份失败（${fail[0].message}）`)
    else toast.success(`批量确认完成：${okCount} 份终分已写入`)
    batchSel.value = new Set()
    await loadWorkspace('')
  } catch (e) {
    toast.error(e instanceof Error ? `批量确认失败：${e.message}` : '批量确认失败，请重试')
  } finally {
    batchConfirming.value = false
  }
}

async function loadReviewInsights() {
  const classId = pickClass.value || ws.value?.context.class?.class_id
  if (!classId) { toast.error('先选择班级'); return }
  try {
    reviewInsights.value = await api8000.grading.insights(classId)
  } catch (e) {
    toast.error(e instanceof Error ? `讲评要点加载失败：${e.message}` : '讲评要点加载失败')
  }
}

/* 过滤/班级/作业变化 → 重载（保留导航锚点语义：无锚则后端取首个未批） */
watch([statusFilter], () => {
  ws.value = null
  void loadWorkspace()
})
watch([pickAssignment], (nv, ov) => {
  if (nv === ov) return
  ws.value = null
  void loadWorkspace()
})
watch([pickClass], (nv, ov) => {
  if (nv === ov) return
  pickAssignment.value = ''
  ws.value = null
  void loadWorkspace()
})

void loadClasses().then(() => loadWorkspace())

onBeforeUnmount(() => {
  if (photoUrl.value) URL.revokeObjectURL(photoUrl.value)
})
</script>

<style scoped>
.tv3-page-shell {
  margin: -22px; padding: 22px; min-height: calc(100vh - var(--tv3-topbar-h));
  background: var(--teacher-bg-gradient);
  box-sizing: border-box;
}
.gw-photo {
  border: 1px solid var(--tv3-line); border-radius: 10px; overflow: hidden; background: #fffdf6;
  min-height: 180px; display: grid; place-items: center;
}
.gw-photo img { width: 100%; display: block; }
.gw-photo__empty { padding: 24px; font-size: 12px; color: var(--tv3-rose); text-align: center; }
.gw-photo--none { background: var(--tv3-bg2); }
.gw-batch {
  position: fixed; bottom: 18px; left: 50%; transform: translateX(-50%);
  display: flex; gap: 12px; align-items: center; padding: 10px 18px;
  background: #fff; border: 1px solid var(--tv3-line); border-radius: 14px;
  box-shadow: var(--tv3-shadow-lg, 0 18px 50px rgba(9, 20, 40, 0.25)); z-index: 900;
}
</style>
