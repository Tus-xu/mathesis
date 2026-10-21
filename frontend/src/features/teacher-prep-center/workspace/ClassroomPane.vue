<script setup lang="ts">
/**
 * 课堂设计页（R3 · 黄金图 1 真实实现）。
 *
 * - 对话面：无蓝图时留在主列（跟 AI 对话确定方向）；有蓝图时移出主列，
 *   首屏第一个可见元素 = 「这节课的教学流程（共 X 分钟）」标题行（对齐黄金图），
 *   完整对话由顶栏「对话」抽屉承载；
 * - 顶部标题行 + 「已考虑」胶囊（画像学情摘要 / 班级名，无则诚实隐藏）；
 * - 顶部动作条（仅在需要时出现）：提案确认 / 生成教案与课件 / 停止生成，
 *   按钮完全来自服务端 available_actions，点击即执行并把返回快照写回 store；
 * - 左轨时间轴 + 节点卡（序号徽标/环节名/时长/一句摘要/chevron；点击展开/收起）；
 *   折叠行的时长文字 = 轻量时长编辑入口（黄金图展开卡内无时长行）；
 * - 展开卡（NodeExpandedCard）：三组编辑 + 教材依据 + 教学提示；
 * - 底部悬浮 AI 条（AI 交互唯一入口）：「正在修改」上下文胶囊 = 当前展开节点（可清除）；
 *   麦克风 = Web Speech API 真实语音转文字（运行时不支持则不渲染入口，反空壳）；
 *   发送 = 受理制 + SSE 阶段文案；
 * - 左下角「教材与资料 N ›」悬浮胶囊 = 真实 evidence 数（点击打开抽屉）；
 * - 无蓝图：选择卡（真实选项直传）/ 诚实空态（真实 job 状态）。
 */
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { usePrepSessionStore } from '@/features/teacher-prep-center/stores/prepSession'
import {
  PrepCenterApiError,
  runPrepAction,
  type PrepBlueprintStage,
} from '@/features/teacher-prep-center/api/prepCenterApi'
import { newIdempotencyKey } from '@/features/teacher-prep-center/api/deckApi'
import { actionLabel } from '@/features/teacher-prep-center/copy/actionCopy'
import NodeExpandedCard from './classroom/NodeExpandedCard.vue'
import ConversationStream from './conversation/ConversationStream.vue'
import { clearEditTarget, setEditTarget, useConversation } from './conversation/useConversation'
import KatexText from '@/features/teacher-prep-center/math/KatexText.vue'

const store = usePrepSessionStore()
const { state: conversation } = useConversation()

const lessonFlow = computed<PrepBlueprintStage[]>(() => store.lessonNodes)
const hasBlueprint = computed(() => !!store.current?.artifact.blueprint)
/** 对话面留在主列的条件（对齐黄金图）：**还没有教学流程**（无蓝图，或蓝图尚未产出 lesson_flow）
 *  → 主列以对话为主内容（跟 AI 对话确定方向的真实场景，必须有）；
 *  已有教学流程（黄金图状态）→ 主列第一个可见元素必须是标题行，对话改由顶栏「对话」抽屉承载。 */
const showConversationInMain = computed(() => !hasBlueprint.value || lessonFlow.value.length === 0)
const expandedIndex = ref<number | null>(null)
/** 用户是否已手动操作展开态（点击节点 / 清除 AI 目标）：是则不再自动套用默认展开。 */
const expandTouched = ref(false)

/* 折叠行：时长缺失诚实显示「—」；总时长 = Σ节点时长（全有值）否则课时预算 */
function durationText(stage: PrepBlueprintStage): string {
  return typeof stage.duration_minutes === 'number' && stage.duration_minutes > 0 ? `${stage.duration_minutes} min` : '—'
}
const totalText = computed(() => {
  const t = store.totalNodeMinutes
  return t !== null ? `${t}` : `${store.budgetMinutes}`
})

/**
 * 折叠行一句摘要（R14 · C7）：优先描述性教学语言，疑问句绝不上屏。
 * 取值序：后端同轮 `summary`（可选字段，未落地时为空 → 回退）→ student_activities
 * → board_notes → items[0] → teacher_questions（最后选择）。
 * 以 `？`/`?` 结尾的候选视为疑问句（摘要质量差）一律跳过；全部不可用则诚实显示「—」。
 * 不在前端编造摘要。
 */
function summaryOf(stage: PrepBlueprintStage): string {
  const first = (arr?: { text: string }[] | null) => (arr && arr.length ? arr[0].text : null)
  const isQuestion = (t: string) => /[?？]\s*$/.test(t)
  // summary 为 additive 可选字段（后端可能尚未落地）：安全读取，空值回退下一优先级
  const summary = (stage as { summary?: string | null }).summary ?? null
  const candidates = [
    summary,
    first(stage.student_activities),
    first(stage.board_notes),
    stage.items?.length ? stage.items[0].text : null,
    first(stage.teacher_questions),
  ]
  for (const candidate of candidates) {
    const text = candidate?.replace(/\s+/g, ' ').trim() || null
    if (text && !isQuestion(text)) return text
  }
  return '—'
}

function toggleExpand(i: number) {
  expandTouched.value = true
  expandedIndex.value = expandedIndex.value === i ? null : i
}

/* ── 折叠行：环节时长轻量编辑（原展开卡内「调整时长」下沉至此）────────────
 * 黄金图展开卡内无「环节时长 / 调整时长 / 剩余可分配」行；但时长编辑是真实能力，
 * 不得删除 → 入口收敛到折叠行的时长文字处：点击原地出现数字输入，Enter/保存落库。
 * 仍走 store.editNode（真实 PATCH + 乐观回滚），超课时预算由后端 time_budget_exceeded 判定。 */
const durationEditIndex = ref<number | null>(null)
const durationDraft = ref<number | null>(null)
const durationSaving = ref(false)
const durationErrorIndex = ref<number | null>(null)
const durationError = ref('')

function startDurationEdit(i: number) {
  durationEditIndex.value = i
  durationErrorIndex.value = null
  durationError.value = ''
  const d = lessonFlow.value[i]?.duration_minutes
  durationDraft.value = typeof d === 'number' ? d : null
}

function cancelDurationEdit() {
  durationEditIndex.value = null
  durationDraft.value = null
}

async function saveDuration(i: number) {
  const sessionId = store.currentSessionId
  if (!sessionId || durationSaving.value) return
  durationSaving.value = true
  durationErrorIndex.value = null
  durationError.value = ''
  const ok = await store.editNode(sessionId, {
    idempotency_key: newIdempotencyKey(),
    expected_revision: store.current?.session.revision ?? 1,
    node_index: i,
    duration_minutes: durationDraft.value,
  })
  durationSaving.value = false
  durationEditIndex.value = null
  if (!ok) {
    const err = store.lastError
    if (err?.errorKey === 'time_budget_exceeded' || err?.data?.error_key === 'time_budget_exceeded') {
      const over = Number(err.data?.over_minutes ?? 0)
      durationError.value = `环节时长合计超出 ${store.budgetMinutes} 分钟课时预算，超出 ${over} 分钟`
    } else {
      durationError.value = store.currentError || '保存失败，请重试'
    }
    durationErrorIndex.value = i
    setTimeout(() => {
      if (durationErrorIndex.value === i) durationErrorIndex.value = null
    }, 8000)
  }
}

/* 展开节点即 AI 目标上下文（黄金图：「正在修改: 03 椭圆的定义」；底部 AI 条消费共享单例） */
watch(
  expandedIndex,
  (idx) => {
    if (idx === null) clearEditTarget()
    else setEditTarget(idx, String(lessonFlow.value[idx]?.title ?? `环节 ${idx + 1}`))
  },
  { immediate: true },
)

/* AI 完成后自动刷新目标节点标题（notice 展示 6s） */
watch(
  () => store.nodeNotice,
  (n) => {
    if (n) setTimeout(() => store.clearNodeNotice(), 6000)
  },
)

/* ── 章节选择卡 ────────────────────────────────────────────────────────
 * W1-C：选择卡已迁入对话面消息流（SelectionCard 组件就地渲染，按章节分组 + 已选回显），
 * 点选 → 结构化回传 POST /messages(selection)（useConversation.pickSection）。本 pane 不再
 * 维护独立的选择网格，避免双入口与双份状态。 */


/* ── 后台 job 状态（真实 job_summary；诚实空态）────────────────────────── */
const activeJobs = computed(() => {
  const jobs = store.current?.job_summary ?? []
  return jobs
    .map((j) => String((j as { status?: unknown }).status ?? '').toUpperCase())
    .filter((s) => ['PENDING', 'CLAIMED', 'STARTED', 'RETRYING'].includes(s))
})

const context = computed(() => store.current?.artifact.context ?? null)

/**
 * 默认展开当前节点（R14 · C1 / R2 修订）：进入页面即有且仅有一个节点处于展开态，
 * 点亮展开卡 / 序号徽标激活态 / AI 条上下文胶囊。
 * 取值：优先「当前/推荐节点」，否则回退**中间环节**（黄金图同为展开中段环节）——
 * 展开首环节会让时间轴整体下移，首屏只能看到 2 个环节行；展开中段环节时
 * 首屏可同时看到标题行 + ≥4 个环节行 + 展开卡上半部分（失重布局验收项）。
 * 用户手动操作后（expandTouched）不再自动干预。
 */
watch(
  lessonFlow,
  (flow) => {
    if (expandTouched.value) return
    if (!flow.length) {
      expandedIndex.value = null
      return
    }
    if (expandedIndex.value === null || expandedIndex.value >= flow.length) {
      expandedIndex.value = defaultNodeIndex(flow)
    }
  },
  { immediate: true },
)

/** 当前/推荐节点索引：后端暂未下发当前节点标记字段 → 约定回退中间环节
 * （扩展点：后端下发 is_current / recommended 标记后在此优先命中）。 */
function defaultNodeIndex(flow: PrepBlueprintStage[]): number {
  return Math.floor((flow.length - 1) / 2)
}

/* 展开卡滚动到可视区 */
watch(expandedIndex, async (v) => {
  if (v !== null) {
    await nextTick()
    document.querySelector(`[data-node-index="${v}"]`)?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
  }
})

/* ── 下一步动作条（唯一入口：提案确认 / 生成教案与课件 / 停止生成）─────────
 * 按钮集合**完全**来自服务端 `available_actions`（状态机权威推导）：
 * 未收录文案的动作（propose_* / 诊断类 / cancel_session）本 pane 不渲染，
 * 避免与顶栏/其它入口重复。动作条无内容时整体不渲染 → 版面与黄金图一致。 */

interface PrepActionEntry {
  action: string
  target: string | null
  label: string
  disabled: boolean
}

/** 查收录文案的动作；未收录（copy_key 不在本表）视同未实现，返回 null。 */
function actionOf(name: string): PrepActionEntry | null {
  const hit = (store.current?.available_actions ?? []).find((a) => a.action === name)
  if (!hit) return null
  const label = actionLabel(hit.copy_key)
  return label ? { action: hit.action, target: hit.target, label, disabled: hit.disabled === true } : null
}

const blueprint = computed(() => store.current?.artifact.blueprint ?? null)
const deckReady = computed(() => !!store.current?.artifact.teaching_deck)
const planReady = computed(() => !!store.current?.artifact.lesson_plan)

const confirmAction = computed(() => actionOf('confirm_proposal'))
const projectAction = computed(() => actionOf('project_specs'))
const cancelRunAction = computed(() => actionOf('cancel_run'))
const retryAction = computed(() => actionOf('retry_run'))
/** reject 只在提案条里作为次按钮出现（单独存在时不渲染假入口）。 */
const rejectAction = computed(() => actionOf('reject_proposal'))

/* ── §7 状态矩阵：加载骨架 / 生成中（阶段文案 + 已用时 + 可停止）/ 失败 / 空 ────
 * 生成中的「阶段文案」全部来自服务端（ai.stage / 本地受理态），计时为本页真实等待时长，
 * 不渲染任何假进度条。 */
const generating = computed(() => activeJobs.value.length > 0 || !!cancelRunAction.value)
/** 对话优先态下 AI 是否还在处理（本地受理态 / 服务端在途生成）：
 *  处理期间不渲染「这节课还没有教学流程」空态卡 —— 对话卡自带的进度行已说明一切，
 *  同时出现两张卡会让首屏看起来像「两个空盒子」（主编 1920×1080 实测）。 */
const conversationPending = computed(() => conversation.busy || generating.value)
const genStageText = computed(
  () => conversation.stage || store.aiStage?.label || store.aiStage?.stage || '正在生成教学流程…',
)
const genSince = ref<number | null>(null)
const genNow = ref(Date.now())
let genTick: ReturnType<typeof setInterval> | null = null

watch(
  generating,
  (on) => {
    if (on) {
      genSince.value = Date.now()
      genNow.value = Date.now()
      if (!genTick) genTick = setInterval(() => (genNow.value = Date.now()), 1000)
    } else {
      genSince.value = null
      if (genTick) {
        clearInterval(genTick)
        genTick = null
      }
    }
  },
  { immediate: true },
)
onBeforeUnmount(() => {
  if (genTick) clearInterval(genTick)
})

const genElapsedText = computed(() => {
  const since = genSince.value ?? genNow.value
  const sec = Math.max(0, Math.floor((genNow.value - since) / 1000))
  return `${String(Math.floor(sec / 60)).padStart(2, '0')}:${String(sec % 60).padStart(2, '0')}`
})

/** 失败重试：重新拉取权威快照（GET，幂等；不改动任何已落库内容）。 */
function reloadWorkspace(): void {
  const sid = store.currentSessionId
  if (sid) void store.loadWorkspace(sid)
}

/** 空态唯一主行动 = 聚焦底部 AI 条（工作台唯一输入入口，不新开输入框）。 */
function focusComposer(): void {
  window.dispatchEvent(new CustomEvent('pc:focus-composer'))
}

/**
 * 最近一次失败的诚实说明（durable basic_mode 卡）：
 * 失败原因随消息落库，教师刷新/重进后仍能看到「为什么失败 + 可以重试」，而不是空白或假成功。
 */
const lastFailure = computed<{ message: string; detail: string } | null>(() => {
  const timeline = store.current?.timeline ?? []
  for (let i = timeline.length - 1; i >= 0; i -= 1) {
    const msg = timeline[i]
    if (msg.role !== 'assistant' || msg.component?.type !== 'basic_mode_card') continue
    const props = (msg.component.props ?? {}) as { error_message?: unknown; error_detail?: unknown }
    return {
      message: typeof props.error_message === 'string' && props.error_message ? props.error_message : '本轮生成没有完成，未写入任何内容。',
      detail: typeof props.error_detail === 'string' ? props.error_detail : '',
    }
  }
  return null
})

/** 1) 蓝图处于 proposal：确认提案（采用 / 不要）。 */
const showProposalBar = computed(() => blueprint.value?.status === 'proposal' && !!confirmAction.value)
/** 2) 已确认但两份 spec 都还没出现：生成教案与课件。 */
const showProjectBar = computed(
  () =>
    blueprint.value?.status === 'confirmed' &&
    !deckReady.value &&
    !planReady.value &&
    !!projectAction.value,
)
/** 3) 有在途生成（服务端才下发 cancel_run）。 */
const showCancelBar = computed(() => !!cancelRunAction.value)
/** 4) 最近一次生成停在失败终态（服务端才下发 retry_run）：可恢复，不锁死会话。 */
const showRetryBar = computed(() => !!retryAction.value && !showCancelBar.value)
const showActionBar = computed(
  () => showProposalBar.value || showProjectBar.value || showCancelBar.value || showRetryBar.value,
)

const actionBarText = computed(() => {
  if (showProposalBar.value) return 'AI 已提出教学方案，确认后即可生成教案与课件'
  if (showProjectBar.value) return '教学方案已确认，现在可以生成教案与课件'
  if (showCancelBar.value) return '正在生成，可随时停止'
  return lastFailure.value?.message ?? '上一次生成没有完成（未写入任何内容），可以直接重试'
})

/* ── 执行 ──────────────────────────────────────────────────────────── */
const busyAction = ref<string | null>(null)
const actionError = ref('')

/** 后端真实错误文案（detail 非字符串时不得把对象拼上屏）。 */
function errText(e: unknown): string {
  if (e instanceof PrepCenterApiError) {
    const detail: unknown = e.detail
    if (typeof detail === 'string' && detail.trim()) return detail
    const msg = e.message
    if (msg && !msg.includes('[object Object]')) return msg
    return '操作失败，请稍后重试。'
  }
  if (e instanceof Error) return e.message
  return String(e)
}

function disabledTitle(entry: PrepActionEntry): string {
  return entry.disabled ? '当前不可执行，请稍后重试' : ''
}

async function runAction(entry: PrepActionEntry) {
  const sessionId = store.currentSessionId
  const base = store.current
  if (!sessionId || !base || busyAction.value) return
  busyAction.value = entry.action
  actionError.value = ''
  const exec = (revision: number) =>
    runPrepAction(sessionId, {
      action: entry.action,
      target: entry.target,
      expectedRevision: revision,
      // 双 Spec 投影：一次产出教案 + 课件（缺省只出教案）
      payload: entry.action === 'project_specs' ? { kinds: ['lesson_plan', 'deck'] } : undefined,
      idempotencyKey: newIdempotencyKey(),
    })
  try {
    let snapshot
    try {
      snapshot = await exec(base.session.revision)
    } catch (e) {
      // 409 = 快照已过期：拉权威快照后以最新 revision 重试一次（不死循环）
      if (e instanceof PrepCenterApiError && e.kind === 'conflict') {
        await store.refreshCurrent(sessionId)
        const fresh = store.current
        if (!fresh || fresh.session.revision === base.session.revision) throw e
        snapshot = await exec(fresh.session.revision)
      } else {
        throw e
      }
    }
    // 返回即完整工作区快照：直接替换 store（动作条随真实数据自动消失）
    if (store.currentSessionId === sessionId) store.current = snapshot
  } catch (e) {
    if ((e as Error)?.name === 'AbortError') return
    actionError.value = errText(e)
  } finally {
    busyAction.value = null
  }
}
</script>

<template>
  <section class="pc-classroom" :class="{ 'pc-classroom--conversation-first': showConversationInMain }">
    <!-- 对话面：仅在**无蓝图**时作为主列主内容（「跟 AI 对话确定方向」的真实场景，必须有）；
         有蓝图时对齐黄金图：首屏第一个可见元素即「这节课的教学流程」标题行，主列不再放对话卡；
         有蓝图后的完整对话从顶栏「对话」抽屉查看（同一 useConversation 单例，不是第二份数据）。 -->
    <ConversationStream v-if="showConversationInMain" class="pc-classroom__conv" />

    <!-- 下一步动作条（真实 available_actions 驱动；无动作时整体不渲染，版面与黄金图一致） -->
    <div v-if="showActionBar" class="pc-actions">
      <div class="pc-actions__main">
        <p class="pc-actions__title">{{ actionBarText }}</p>
        <!-- 失败详情（有界、非堆栈）：标注为技术细节，避免教师误读为产品文案 -->
        <p v-if="showRetryBar && lastFailure?.detail" class="pc-actions__detail">技术细节：{{ lastFailure.detail }}</p>
        <p v-if="actionError" class="pc-actions__error">{{ actionError }}</p>
      </div>
      <div class="pc-actions__btns">
        <!-- 1) 提案：采用 / 不要 -->
        <button
          v-if="showProposalBar && confirmAction"
          type="button"
          class="pc-actions__btn"
          :disabled="confirmAction.disabled || !!busyAction"
          :title="disabledTitle(confirmAction)"
          @click="runAction(confirmAction)"
        >
          {{ busyAction === 'confirm_proposal' ? '处理中…' : confirmAction.label }}
        </button>
        <button
          v-if="showProposalBar && rejectAction"
          type="button"
          class="pc-actions__btn pc-actions__btn--ghost"
          :disabled="rejectAction.disabled || !!busyAction"
          :title="disabledTitle(rejectAction)"
          @click="runAction(rejectAction)"
        >
          {{ busyAction === 'reject_proposal' ? '处理中…' : rejectAction.label }}
        </button>
        <!-- 2) 已确认未生成：生成教案与课件（一次产出两份 spec） -->
        <button
          v-if="showProjectBar && projectAction"
          type="button"
          class="pc-actions__btn"
          :disabled="projectAction.disabled || !!busyAction"
          :title="disabledTitle(projectAction)"
          @click="runAction(projectAction)"
        >
          {{ busyAction === 'project_specs' ? '处理中…' : projectAction.label }}
        </button>
        <!-- 3) 生成进行中：停止生成 -->
        <button
          v-if="cancelRunAction"
          type="button"
          class="pc-actions__btn pc-actions__btn--ghost"
          :disabled="cancelRunAction.disabled || !!busyAction"
          :title="disabledTitle(cancelRunAction)"
          @click="runAction(cancelRunAction)"
        >
          {{ busyAction === 'cancel_run' ? '处理中…' : cancelRunAction.label }}
        </button>
        <!-- 4) 最近一次生成失败：重试本次（仅重跑蓝图阶段；上下文/意图已落库） -->
        <button
          v-if="showRetryBar && retryAction"
          type="button"
          class="pc-actions__btn"
          :disabled="retryAction.disabled || !!busyAction"
          :title="retryAction.disabled ? disabledTitle(retryAction) : '重新生成这次的教学方案（不会重复写入其它内容）'"
          @click="runAction(retryAction)"
        >
          {{ busyAction === 'retry_run' ? '重试中…' : retryAction.label }}
        </button>
      </div>
    </div>

    <!-- 标题行 + 已考虑胶囊 -->
    <div v-if="hasBlueprint" class="pc-classroom__head">
      <h2 class="pc-classroom__title">
        这节课的教学流程
        <span class="pc-classroom__title-sub">（共 {{ totalText }} 分钟）</span>
      </h2>
      <span v-if="store.considerCapsule" class="pc-classroom__consider">
        <svg viewBox="0 0 14 14" aria-hidden="true">
          <path d="M2.4 12V7.6M7 12V2.4M11.6 12V5.2" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" />
        </svg>
        已考虑：{{ store.considerCapsule }}
      </span>
    </div>

    <!-- 真实教学路径时间轴 -->
    <ol v-if="lessonFlow.length" class="pc-timeline">
      <li
        v-for="(stage, i) in lessonFlow"
        :key="`${stage.stage}-${i}`"
        class="pc-timeline__item"
        :data-node-index="i"
      >
        <span class="pc-timeline__rail" aria-hidden="true">
          <span class="pc-timeline__dot" :class="{ 'is-active': expandedIndex === i }"></span>
        </span>
        <div class="pc-timeline__stack">
          <!-- 折叠行：整体可点展开；时长文字处为轻量编辑入口（点击不触发展开） -->
          <div
            class="pc-node"
            :class="{ 'is-active': expandedIndex === i }"
            role="button"
            tabindex="0"
            :aria-expanded="expandedIndex === i"
            @click="toggleExpand(i)"
            @keydown.enter.prevent="toggleExpand(i)"
            @keydown.space.prevent="toggleExpand(i)"
          >
            <span class="pc-node__no" :class="{ 'is-active': expandedIndex === i }">{{ String(i + 1).padStart(2, '0') }}</span>
            <span class="pc-node__title">{{ stage.title }}</span>

            <!-- 时长编辑入口（下沉自展开卡；黄金图展开卡内无时长行） -->
            <span class="pc-node__dur" @click.stop @keydown.stop>
              <template v-if="durationEditIndex !== i">
                <button type="button" class="pc-node__dur-btn" title="调整本环节时长" @click="startDurationEdit(i)">
                  {{ durationText(stage) }}
                  <svg viewBox="0 0 12 12" aria-hidden="true">
                    <path d="M8.6 1.6 10.4 3.4 4.4 9.4l-2.2.5.5-2.2z" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linejoin="round" />
                  </svg>
                </button>
              </template>
              <template v-else>
                <input
                  v-model.number="durationDraft"
                  type="number"
                  min="0"
                  max="120"
                  class="pc-node__dur-input"
                  :disabled="durationSaving"
                  @keydown.enter.prevent="saveDuration(i)"
                  @keydown.esc.prevent="cancelDurationEdit"
                />
                <button type="button" class="pc-node__dur-act" :disabled="durationSaving" @click="saveDuration(i)">
                  {{ durationSaving ? '保存中' : '保存' }}
                </button>
                <button type="button" class="pc-node__dur-act pc-node__dur-act--ghost" :disabled="durationSaving" @click="cancelDurationEdit">
                  取消
                </button>
              </template>
            </span>
            <span v-if="durationErrorIndex === i" class="pc-node__dur-error">{{ durationError }}</span>

            <span class="pc-node__summary"><KatexText :text="summaryOf(stage)" /></span>
            <svg class="pc-node__chevron" :class="{ 'is-open': expandedIndex === i }" viewBox="0 0 12 12" aria-hidden="true">
              <path d="M2.5 4.5 6 8l3.5-3.5" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" />
            </svg>
          </div>
          <NodeExpandedCard
            v-if="expandedIndex === i"
            :session-id="store.currentSessionId || ''"
            :node-index="i"
            :node="stage"
          />
        </div>
      </li>
    </ol>

    <!-- §7 状态矩阵：加载骨架 / 生成中 / 失败 / 空（选择卡已迁入对话面消息流） -->
    <div v-else class="pc-classroom__states">
      <!-- 首屏加载：与时间轴同构的骨架屏（禁止转圈遮罩） -->
      <div v-if="store.currentLoading" class="pc-skel" aria-hidden="true">
        <div v-for="i in 4" :key="i" class="pc-skel__row">
          <span class="pc-skel__no"></span>
          <span class="pc-skel__title"></span>
          <span class="pc-skel__line"></span>
        </div>
      </div>

      <!-- 生成中：服务端阶段文案 + 已用时 + 可停止（无假进度） -->
      <div v-else-if="generating" class="pc-state">
        <span class="pc-state__spinner" aria-hidden="true"></span>
        <p class="pc-state__title">正在生成教学流程</p>
        <p class="pc-state__desc">
          {{ genStageText }}
          <span class="pc-state__time">已用时 {{ genElapsedText }}</span>
        </p>
        <div v-if="cancelRunAction" class="pc-state__actions">
          <button
            type="button"
            class="pc-actions__btn pc-actions__btn--ghost"
            :disabled="cancelRunAction.disabled || !!busyAction"
            :title="disabledTitle(cancelRunAction)"
            @click="runAction(cancelRunAction)"
          >
            {{ busyAction === 'cancel_run' ? '处理中…' : cancelRunAction.label }}
          </button>
        </div>
      </div>

      <!-- 失败态：中文软提示 + 重试（幂等 GET，不改动已落库内容） -->
      <div v-else-if="store.currentError" class="pc-state pc-state--error">
        <p class="pc-state__title">课堂设计没能加载出来</p>
        <p class="pc-state__desc">{{ store.currentError }}</p>
        <div class="pc-state__actions">
          <button type="button" class="pc-actions__btn" @click="reloadWorkspace">重试</button>
        </div>
      </div>

      <!-- 空态：图标 + 一句说明 + 唯一主行动按钮（AI 处理中不渲染：对话卡进度行已表达） -->
      <div v-else-if="!conversationPending" class="pc-state">
        <span class="pc-state__icon" aria-hidden="true">
          <svg viewBox="0 0 24 24">
            <path d="M4.5 5.5h15v10.5h-8.2L6.6 19.4v-3.4H4.5z" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round" />
            <path d="M8.4 9.6h7.2M8.4 12.6h4.4" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" />
          </svg>
        </span>
        <p class="pc-state__title">这节课还没有教学流程</p>
        <p class="pc-state__desc">在下方告诉 AI 课题、课时与教材，它会先给出教学方案，确认后生成完整教学流程。</p>
        <div class="pc-state__actions">
          <button type="button" class="pc-actions__btn" @click="focusComposer">告诉 AI 我的备课想法</button>
        </div>
      </div>
    </div>

    <!-- 教材定位摘要（真实 context 投影；蓝图生成前后的能力提示，克制展示） -->
    <div v-if="context && !hasBlueprint" class="pc-classroom__context">
      <div class="pc-classroom__context-row">
        <span class="pc-classroom__context-label">教材</span>
        <span>{{ context.source?.title || '—' }}</span>
      </div>
      <div class="pc-classroom__context-row">
        <span class="pc-classroom__context-label">章节</span>
        <span>{{ context.section?.title || '—' }}<template v-if="context.section?.section_path">（{{ context.section.section_path }}）</template></span>
      </div>
    </div>

    <!-- 底部 AI 条已上移到 PrepWorkspace（全工作台共用，三条 Tab 行为一致） -->

    <!-- 左下角悬浮：教材与资料 N（真实 evidence 数；0 条仍可打开抽屉看诚实空态说明） -->
    <button
      type="button"
      class="pc-classroom__materials"
      :title="(store.current?.evidence?.length ?? 0) === 0 ? '本节课暂无教材依据，打开查看说明' : '查看教材与资料'"
      @click="store.toggleMaterials(true)"
    >
      <svg viewBox="0 0 16 16" aria-hidden="true">
        <path d="M10.6 4.4 6.4 8.6a1.9 1.9 0 0 0 2.7 2.7l4.5-4.5a3.4 3.4 0 0 0-4.8-4.8L4 6.8a4.9 4.9 0 0 0 7 7l3.4-3.4" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" />
      </svg>
      教材与资料
      <span class="pc-classroom__materials-count">{{ store.current?.evidence?.length ?? 0 }}</span>
      <span class="pc-classroom__materials-arrow" aria-hidden="true">›</span>
    </button>
  </section>
</template>

<style scoped>
.pc-classroom {
  position: relative;
  max-width: 1300px;
  margin: 0 auto;
  /* 水平内边距 10px：内容列 = 1300 - 20 = 1280px（黄金图基线 ≈1280，QA 实测原 1236 超差 -3.4%） */
  /* 底部安全区 184px：底部 AI 条占位 = bottom 26 + 胶囊 60 = 86px，余 ≥96px
     保证「全滚动到底」时展开卡最低点（含右栏教材依据/教学提示两卡）不被悬浮 AI 条压住。 */
  padding: 22px 10px 184px;
  font-family: var(--pc-font);
}

/* 下一步动作条（提案确认 / 生成教案与课件 / 停止生成） */
.pc-actions {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 16px;
  margin-bottom: 16px;
  padding: 14px 18px;
  background: var(--pc-primary-softer);
  border: 1px solid var(--pc-line);
  border-radius: var(--pc-radius-card);
}
.pc-actions__main {
  flex: 1;
  min-width: 0;
}
.pc-actions__title {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
  color: var(--pc-text-2);
}
.pc-actions__error {
  margin: 6px 0 0;
  font-size: 13px;
  color: var(--pc-danger);
}
/* 失败详情：次要信息（诚实说明，不抢主文案视线），最多两行 */
.pc-actions__detail {
  margin: 4px 0 0;
  font-size: 12px;
  line-height: 1.5;
  color: var(--pc-text-muted);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.pc-actions__btns {
  flex: none;
  display: flex;
  align-items: center;
  gap: 10px;
}
.pc-actions__btn {
  box-sizing: border-box;
  height: 34px;
  padding: 0 16px;
  border: none;
  border-radius: var(--pc-radius-btn);
  background: var(--pc-primary);
  color: #fff;
  font-size: 13px;
  font-family: inherit;
  white-space: nowrap;
  cursor: pointer;
}
.pc-actions__btn:hover:not(:disabled) {
  background: var(--pc-primary-hover);
}
.pc-actions__btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.pc-actions__btn--ghost {
  background: var(--pc-panel);
  border: 1px solid var(--pc-line);
  color: var(--pc-text-2);
}
.pc-actions__btn--ghost:hover:not(:disabled) {
  border-color: var(--pc-primary);
  color: var(--pc-primary);
  background: var(--pc-primary-soft);
}

/* 标题行 */
.pc-classroom__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}
.pc-classroom__title {
  margin: 0;
  font-size: 20px;
  font-weight: 700;
  color: var(--pc-text);
}
.pc-classroom__title-sub {
  font-size: 13px;
  font-weight: 400;
  color: var(--pc-text-muted);
}
/* 「已考虑」胶囊（黄金图：浅蓝底无边框，非页面灰底） */
.pc-classroom__consider {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 5px 12px;
  border-radius: var(--pc-radius-full);
  background: var(--pc-primary-soft);
  color: var(--pc-text-muted);
  font-size: 12px;
}
.pc-classroom__consider svg {
  width: 13px;
  height: 13px;
  color: var(--pc-primary);
}

/* 时间轴（R6-S3fix 密度对齐黄金图：单行卡高 49 / 卡间隙 5 / 节距 54） */
.pc-timeline {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 5px;
}
.pc-timeline__item {
  display: flex;
  /* 轨-卡距 10（黄金图）：rail 宽 10 + gap 10，dot 右缘 10.5 → 间距 ≈10 */
  gap: 10px;
}
.pc-timeline__rail {
  flex: none;
  width: 10px;
  position: relative;
  align-self: stretch;
}
.pc-timeline__rail::before {
  content: '';
  position: absolute;
  left: 4px;
  top: -10px;
  bottom: -10px;
  width: 1px;
  background: var(--pc-line);
}
.pc-timeline__item:first-child .pc-timeline__rail::before {
  top: 50%;
}
.pc-timeline__item:last-child .pc-timeline__rail::before {
  bottom: 50%;
}
.pc-timeline__dot {
  /* 左轨节点圆 12px（黄金图）；left -1.5 使圆心与竖线（left 4 + 0.5）对齐 */
  position: absolute;
  left: -1.5px;
  top: 50%;
  transform: translateY(-50%);
  width: 12px;
  height: 12px;
  border-radius: 50%;
  background: var(--pc-panel);
  border: 1.5px solid #c3cbd9;
}
.pc-timeline__dot.is-active {
  background: var(--pc-primary);
  border-color: var(--pc-primary);
}
.pc-timeline__stack {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  /* 展开卡与激活行贴合为同一容器（黄金图：描边环包住行头+展开体，中间无分隔） */
  gap: 0;
}

/* 节点折叠行（R6-S3fix：min-height 49 对齐黄金图单行卡高；6px 上下 padding
   保证 2px 激活描边下内容 32+12+4=48 仍 ≤49，行高恒为 49） */
.pc-node {
  box-sizing: border-box;
  display: flex;
  align-items: center;
  gap: 14px;
  width: 100%;
  min-height: 49px;
  padding: 6px 18px;
  background: var(--pc-panel);
  border: 1px solid var(--pc-line);
  border-radius: var(--pc-radius-card);
  box-shadow: var(--pc-shadow-card);
  font-family: inherit;
  cursor: pointer;
  text-align: left;
}
.pc-node:hover {
  border-color: #d4ddeb;
}
.pc-node:focus-visible {
  outline: 2px solid var(--pc-primary);
  outline-offset: 1px;
}
/* 激活行：2px 浅蓝描边（黄金图 #A2BEFD 系，非 1px 主蓝）；底部无边框与展开体连续 */
.pc-node.is-active {
  border: 2px solid var(--pc-primary-ring);
  border-bottom: none;
  border-radius: var(--pc-radius-card) var(--pc-radius-card) 0 0;
  background: var(--pc-primary-softer);
  box-shadow: none;
}
/* 编号圆（黄金图 G23）：32px 正圆（非圆角方块） */
.pc-node__no {
  flex: none;
  width: 32px;
  height: 32px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
  background: var(--pc-no-circle);
  color: var(--pc-text-muted);
  font-size: 13px;
  font-weight: 600;
}
.pc-node__no.is-active {
  background: var(--pc-primary);
  color: #fff;
}
.pc-node__title {
  /* 环节名完整显示：不省略，按需换行（长词/长串按 anywhere 断行，绝不超出列宽） */
  flex: 0 1 170px;
  min-width: 0;
  font-size: 15px;
  font-weight: 700;
  line-height: 1.45;
  color: var(--pc-text);
  overflow-wrap: anywhere;
}
.pc-node__summary {
  /* 摘要完整显示：不再单行省略；宽度由 flex 决定并在其中换行，行高随内容增长 */
  flex: 1 1 0;
  min-width: 0;
  font-size: 14px;
  color: var(--pc-text-muted);
  overflow-wrap: anywhere;
}
.pc-node__chevron {
  flex: none;
  width: 12px;
  height: 12px;
  color: var(--pc-text-faint);
  transition: transform 0.15s ease;
}
.pc-node__chevron.is-open {
  transform: rotate(180deg);
}

/* 折叠行时长轻量编辑（原展开卡「调整时长」下沉至此） */
.pc-node__dur {
  flex: none;
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.pc-node__dur-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  min-width: 52px;
  padding: 2px 4px;
  border: 1px solid transparent;
  border-radius: 6px;
  background: transparent;
  font-family: inherit;
  font-size: 13px;
  color: var(--pc-text-muted);
  text-align: left;
  cursor: pointer;
}
.pc-node__dur-btn:hover {
  border-color: var(--pc-line);
  background: var(--pc-panel);
  color: var(--pc-primary);
}
.pc-node__dur-btn svg {
  width: 11px;
  height: 11px;
  opacity: 0;
  transition: opacity 0.15s ease;
}
.pc-node:hover .pc-node__dur-btn svg,
.pc-node__dur-btn:focus-visible svg {
  opacity: 1;
}
.pc-node__dur-input {
  box-sizing: border-box;
  width: 56px;
  height: 26px;
  padding: 0 6px;
  border: 1px solid var(--pc-primary);
  border-radius: 6px;
  background: var(--pc-panel);
  font-family: inherit;
  font-size: 13px;
  color: var(--pc-text-2);
}
.pc-node__dur-input:focus {
  outline: none;
}
.pc-node__dur-act {
  height: 26px;
  padding: 0 8px;
  border: none;
  border-radius: 6px;
  background: var(--pc-primary);
  color: #fff;
  font-family: inherit;
  font-size: 12px;
  cursor: pointer;
}
.pc-node__dur-act:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
.pc-node__dur-act--ghost {
  background: var(--pc-panel);
  border: 1px solid var(--pc-line);
  color: var(--pc-text-2);
}
.pc-node__dur-error {
  flex: 0 1 auto;
  min-width: 0;
  font-size: 12px;
  color: var(--pc-danger);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* 对话面 / 教材定位 */
.pc-classroom__conv {
  margin-bottom: 18px;
}
/* ── 对话优先态（还没有教学流程）─────────────────────────────────────────────
 * 2026-09-23 修（主编实测）：此阶段对话就是主内容，但原来它只占 ~157px，
 * 下方近 900px 全是空白，整页看起来像没做完。改为：本态下工作区撑满可用高度、
 * 对话卡吸收剩余空间并在卡内滚动（内容可达、无整页空洞）。 */
.pc-classroom--conversation-first {
  display: flex;
  flex-direction: column;
  /* 可用高度 = 视口 - 顶栏(56) - 底部安全区(184)；下限 420 保证小屏不被压扁
   * --pc-viewport-h：嵌入外框后内容区可用高度（R1 G1；默认 100vh） */
  min-height: calc(var(--pc-viewport-h, 100vh) - 240px);
}
.pc-classroom--conversation-first .pc-classroom__conv {
  flex: 1 1 auto;
  min-height: 320px;
  display: flex;
  flex-direction: column;
}
/* 卡内滚动区吸收剩余高度（ConversationStream 为 scoped 样式，需 :deep 穿透） */
.pc-classroom--conversation-first :deep(.pc-conv__scroll),
.pc-classroom--conversation-first :deep(.pc-conv.is-collapsed .pc-conv__scroll) {
  flex: 1 1 auto;
  min-height: 0;
  max-height: none;
  overflow-y: auto;
}
/* 本态空态卡收敛为「次级说明条」：对话已是主内容，这里不再是一张占满屏的大白卡 */
.pc-classroom--conversation-first .pc-state {
  padding: 22px 24px;
  gap: 6px;
}
.pc-classroom--conversation-first .pc-state__icon {
  width: 36px;
  height: 36px;
}
.pc-classroom--conversation-first .pc-state__icon svg {
  width: 20px;
  height: 20px;
}
.pc-classroom__context {
  margin-top: 20px;
  padding: 20px;
  background: var(--pc-panel);
  border: 1px solid var(--pc-line);
  border-radius: var(--pc-radius-card);
}
.pc-classroom__context-row {
  display: flex;
  gap: 12px;
  padding: 4px 0;
  font-size: 14px;
  color: var(--pc-text-2);
}
.pc-classroom__context-label {
  flex: none;
  width: 48px;
  font-size: 13px;
  font-weight: 600;
  color: var(--pc-primary);
}
.pc-classroom__empty {
  padding: 64px 0;
  text-align: center;
  font-size: 14px;
  color: var(--pc-text-faint);
}

/* ── §7 状态矩阵（空 / 生成中 / 失败 / 加载骨架）────────────────────── */
.pc-classroom__states {
  padding: 6px 0 4px;
}
.pc-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 40px 24px;
  background: var(--pc-panel);
  border: 1px solid var(--pc-line);
  border-radius: var(--pc-radius-card);
  text-align: center;
}
.pc-state--error {
  background: #fffafa;
  border-color: #f4d4d4;
}
.pc-state__icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 46px;
  height: 46px;
  border-radius: 50%;
  background: var(--pc-primary-soft);
  color: var(--pc-primary);
}
.pc-state__icon svg {
  width: 24px;
  height: 24px;
}
.pc-state__title {
  margin: 0;
  font-size: 15px;
  font-weight: 700;
  color: var(--pc-text);
}
.pc-state__desc {
  margin: 0;
  max-width: 560px;
  font-size: 13px;
  line-height: 1.7;
  color: var(--pc-text-muted);
}
.pc-state--error .pc-state__desc {
  color: var(--pc-danger);
}
.pc-state__time {
  margin-left: 8px;
  font-variant-numeric: tabular-nums;
  color: var(--pc-text-faint);
}
.pc-state__actions {
  margin-top: 6px;
}
/* 生成中：脉冲点（真实「进行中」提示，非进度条） */
.pc-state__spinner {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: var(--pc-primary);
  animation: pc-state-pulse 1.1s ease-in-out infinite;
}
@keyframes pc-state-pulse {
  0%,
  100% {
    opacity: 1;
    transform: scale(1);
  }
  50% {
    opacity: 0.35;
    transform: scale(0.82);
  }
}
/* 加载骨架：与时间轴同构（序号徽标 + 环节名 + 摘要；密度同步 R6-S3fix） */
.pc-skel {
  display: flex;
  flex-direction: column;
  gap: 5px;
}
.pc-skel__row {
  display: flex;
  align-items: center;
  gap: 14px;
  /* 与时间轴行同构（R6-S3fix：min-height 49 / padding 6 与 .pc-node 一致，容纳 32px 编号圆） */
  min-height: 49px;
  box-sizing: border-box;
  padding: 6px 18px;
  background: var(--pc-panel);
  border: 1px solid var(--pc-line);
  border-radius: var(--pc-radius-card);
}
.pc-skel__no,
.pc-skel__title,
.pc-skel__line {
  display: block;
  height: 12px;
  border-radius: 6px;
  background: linear-gradient(90deg, #eef1f6 0%, #f6f8fb 50%, #eef1f6 100%);
  background-size: 200% 100%;
  animation: pc-skel-shimmer 1.4s ease-in-out infinite;
}
.pc-skel__no {
  flex: none;
  width: 32px;
  height: 32px;
  border-radius: 50%;
}
.pc-skel__title {
  flex: none;
  width: 130px;
}
.pc-skel__line {
  flex: 1;
}
@keyframes pc-skel-shimmer {
  0% {
    background-position: 200% 0;
  }
  100% {
    background-position: -200% 0;
  }
}

/* 底部 AI 条样式已迁至 conversation/ChatComposer.vue（全工作台共用） */

/* 左下角资料胶囊 */
.pc-classroom__materials {
  position: fixed;
  /* 宪法 IA：课堂设计视图无左导航（内容区全宽），胶囊贴内容区左缘 16px；
     原依赖 --pc-sidenav-w 的右移口径已随导航收敛移除 */
  left: 16px;
  bottom: 26px;
  z-index: 50;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 11px 16px;
  border: 1px solid var(--pc-line);
  border-radius: var(--pc-radius-full);
  background: var(--pc-panel);
  box-shadow: 0 8px 24px rgba(15, 23, 42, 0.14);
  font-size: 13px;
  font-family: inherit;
  color: var(--pc-text-2);
  cursor: pointer;
}
.pc-classroom__materials:hover:not(:disabled) {
  color: var(--pc-primary);
}
.pc-classroom__materials:disabled {
  cursor: not-allowed;
  color: var(--pc-text-faint);
}
.pc-classroom__materials > svg {
  width: 15px;
  height: 15px;
}
.pc-classroom__materials-count {
  min-width: 20px;
  height: 20px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0 6px;
  border-radius: var(--pc-radius-full);
  background: var(--pc-primary-soft);
  color: var(--pc-primary);
  font-size: 12px;
  font-weight: 600;
}
.pc-classroom__materials-arrow {
  color: var(--pc-text-faint);
  font-size: 14px;
}
</style>
