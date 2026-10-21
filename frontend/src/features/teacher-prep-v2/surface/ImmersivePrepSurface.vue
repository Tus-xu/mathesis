<template>
  <!--
    T22.1 · 沉浸式备课 surface（页面级模式切换，UX-CONTRACT A–E）。

    职责**只有**两件事：
      1. 把 projection 的结果映射为 conversation / artifact_focus 两种形态；
      2. 托管按需互斥 drawer。
    没有任何业务 if/else 猜流程 —— 形态完全由 `projection/` 纯函数决定。

    默认态保证（P0）：
      - 固定三栏数 = 0；Artifact Canvas / Evidence Rail / Job Bar **在 DOM 中不存在**
      - 只有真实 artifact_ready 后才出现「查看并修改」
  -->
  <!--
    E · 聚焦式单状态（401/403/404/409/410/不可用）：**唯一主错误呈现**。
    错误态下**不渲染 FocusShell** —— 会话都取不到，就不该展示它的进展/产物；
    这也保证"403 页面不得出现任何就绪暗示"（里程碑 chip 里的"N 项已就绪"也不会出现）。
  -->
  <FocusedState
    v-if="focusedError"
    :kind="focusedError"
    :detail="error?.detail ?? null"
    :request-id="error?.requestId ?? null"
    @signin="onSignin"
    @back-to-list="onBack"
    @retry="onReload"
    @view-revision="onReload"
  />

  <FocusShell
    v-else
    :title="title"
    :meta="focusMeta"
    :milestone-label="milestones.label"
    :milestones="milestones.items"
    :milestones-open="milestonesOpen"
    :artifact-count="artifacts.length"
    :stream-phase="streamPhase"
    @back="onBack"
    @open-milestones="milestonesOpen = true"
    @close-milestones="milestonesOpen = false"
    @open-artifacts="onOpenFocus"
  >
  <div class="surface" data-testid="prep-immersive-surface" :data-surface-mode="surfaceMode">
    <!-- 自动推进失败必须可见：否则教师既看不到按钮也得不到解释，只能干等。 -->
    <p v-if="autoFlowError" class="surface__auto-error" role="alert" data-testid="prep-auto-flow-error">
      <span>{{ copyFor('prep.auto.deck_failed') }}{{ autoFlowError }}</span>
      <button type="button" class="surface__auto-retry" @click="resetAutoFlow">
        {{ copyFor('prep.auto.retry') }}
      </button>
    </p>
    <!-- D · 教师主动打开的双区编辑态 -->
    <ArtifactFocusCanvas
      v-if="surfaceMode === 'artifact_focus' && activeArtifact"
      :artifact="activeArtifact as ActiveArtifact"
      :body="artifactBody"
      :messages="timeline"
      :assistant-collapsed="ui.assistantCollapsed"
      :draft="ui.draft"
      :busy="busy"
      :diff="diff"
      :editing="editing"
      @close="onCloseFocus"
      @open-overlay="openOverlay"
      @toggle-assistant="onToggleAssistant"
      @ask-revision="onAskRevision"
      @accept-diff="onAcceptDiff"
      @reject-diff="onRejectDiff"
      @send="onSend"
      @stop="onStop"
      @attach="onAttach"
      @update:draft="onDraft"
      @run="onRun"
      @conflict="onConflict"
      @retry="onRetry"
      @propose-patch="(ops, summary) => handlers.propose(ops, summary)"
      @apply-patch="handlers.applyPending()"
      @reject-patch="handlers.rejectPending()"
      @revert-revision="(rev) => handlers.revertRevision(rev)"
      @regen-target="(p) => handlers.regen(p)"
      @retry-page-qa="(pageNo) => handlers.retryQaPage(pageNo)"
      @request-export="(format) => handlers.requestExport(format)"
      @cancel-export="(format) => handlers.cancelExport(format)"
      @refresh-download="(format) => handlers.refreshDownload(format)"
      @conflict-reload="handlers.conflictReload()"
      @conflict-reapply="handlers.conflictReapply()"
      @conflict-discard="handlers.conflictDiscard()"
    />

    <!-- A/B/C · 默认沉浸式对话 -->
    <ConversationStage
      v-else
      :title="title"
      :subtitle="subtitle"
      :timeline="timeline"
      :actions="availableActions"
      :context="context"
      :session="sessionView"
      :active-artifact="activeArtifact"
      :artifacts="artifacts"
      :show-dock="showDock"
      :session-phase="sessionPhase"
      :stream-phase="streamPhase"
      :gap-count="gapCount"
      :busy="busy"
      :draft="ui.draft"
      :suggestions="suggestions"
      @send="onSend"
      @stop="onStop"
      @attach="onAttach"
      @update:draft="onDraft"
      @run="onRun"
      @conflict="onConflict"
      @retry="onRetry"
      @apply-edits="onApplyEdits"
      @select="onSelect"
      @open-evidence="openOverlay('evidence')"
      @open-focus="onOpenFocus"
      @dismiss-dock="dismissDock"
      @back="onBack"
    />

  </div>
  </FocusShell>

  <OverlayHost
    :overlay="activeOverlay"
    :evidence="evidence"
    :revision="revision"
    @close="closeOverlay"
  />
</template>

<script setup lang="ts">
import { computed, getCurrentInstance, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'

import ArtifactFocusCanvas from './ArtifactFocusCanvas.vue'
import ConversationStage from './ConversationStage.vue'
import FocusedState from './FocusedState.vue'
import FocusShell from './FocusShell.vue'
import OverlayHost from './drawers/OverlayHost.vue'
import type { DiffView } from './RevisionDiffPanel.vue'
import { buildMilestones } from '@/features/teacher-prep-v2/projection/milestones'
import type { ActiveArtifact } from '@/features/teacher-prep-v2/projection/types'
import { copyFor, copyWith } from '@/features/teacher-prep-v2/i18n/copy'
import { useArtifactEditing } from './useArtifactEditing'
import { useAutoDeckFlow } from './useAutoDeckFlow'
import { usePrepSurfaceStore } from './usePrepSurfaceStore'

const props = withDefaults(defineProps<{ sessionId?: string | null }>(), { sessionId: null })

/**
 * 路由参数读取：**不用 `useRoute()`**，改读全局 `$route`。
 * 原因：本组件在隔离测试 / harness 中也会被挂载，此时没有 router 注入，
 * `useRoute()` 会打印 "injection not found" 警告。读 `$route` 在无 router
 * 时静默为 null，语义一致且零噪音。
 */
const route = (
  (getCurrentInstance()?.appContext.config.globalProperties.$route ?? null) as {
    params?: Record<string, unknown>
    query?: Record<string, unknown>
  } | null
)
/**
 * 同 `route` 的理由：不 `useRouter()`，读全局 `$router`；harness / 隔离测试下可能不存在，
 * 此时回退到整页跳转（见 onBack），保证"出口"在任何挂载方式下都成立。
 */
const globalRouter = (
  (getCurrentInstance()?.appContext.config.globalProperties.$router ?? null) as {
    push: (to: string | { name?: string; path?: string; query?: Record<string, string> }) => unknown
  } | null
)
const store = usePrepSurfaceStore()

const {
  ui, timeline, evidence, availableActions, error, revision, gapCount, activeOverlay, facts,
  surfaceMode, sessionPhase, streamPhase, activeArtifact, focusedError, showDock,
} = storeToRefs(store)

const snapshot = computed(() => store.snapshot)
const context = computed(
  () => (snapshot.value?.artifact?.context ?? null) as Record<string, unknown> | null,
)
const sessionView = computed(() => (snapshot.value?.session ?? null) as Record<string, unknown> | null)

const artifacts = computed<ActiveArtifact[]>(() => {
  const out: ActiveArtifact[] = []
  const source = snapshot.value?.artifact ?? null
  const addSpec = (
    raw: Record<string, unknown> | null | undefined,
    artifactType: ActiveArtifact['artifact_type'],
    fallbackTitle: string,
  ) => {
    if (!raw) return
    const id = typeof raw.spec_id === 'string' ? raw.spec_id : typeof raw.id === 'string' ? raw.id : ''
    if (!id) return
    const metadata = raw.metadata && typeof raw.metadata === 'object' ? raw.metadata as Record<string, unknown> : null
    out.push({
      artifact_id: id,
      artifact_type: artifactType,
      revision: typeof raw.revision === 'number' ? raw.revision : 0,
      title: typeof raw.title === 'string' && raw.title ? raw.title : typeof metadata?.title === 'string' ? metadata.title : fallbackTitle,
      status: 'available',
      updated_at: typeof raw.updated_at === 'string' ? raw.updated_at : null,
    })
  }
  // 2026-09-14 备课中心简化：产物入口只保留课件（PPT 编辑工作台）。
  // 教案 spec 仍由服务端投影并保留 lineage，但不再有可达的预览界面。
  addSpec((source?.teaching_deck ?? null) as Record<string, unknown> | null, 'teaching_deck_spec', copyFor('prep.artifact.type.teaching_deck_spec'))
  if (activeArtifact.value && !out.some((item) => item.artifact_id === activeArtifact.value?.artifact_id)) {
    out.unshift(activeArtifact.value)
  }
  return out
})

const artifactBody = computed(() => {
  const a = activeArtifact.value
  if (!a) return null
  const bp = snapshot.value?.artifact?.blueprint as Record<string, unknown> | null
  if (a.artifact_type === 'teaching_blueprint') return (bp?.body ?? bp ?? null) as Record<string, unknown> | null
  if (a.artifact_type === 'lesson_plan_spec') return (snapshot.value?.artifact?.lesson_plan ?? null) as Record<string, unknown> | null
  if (a.artifact_type === 'teaching_deck_spec') return (snapshot.value?.artifact?.teaching_deck ?? null) as Record<string, unknown> | null
  return (snapshot.value?.artifact?.diagnostic ?? null) as Record<string, unknown> | null
})

const busy = computed(() => sessionPhase.value === 'generating')

/**
 * T24 接线：仅在聚焦态为**可编辑 spec**（LessonPlanSpec / TeachingDeckSpec）时，
 * 才把 artifact 身份交给编辑 store（deck/lesson-plan 才是它的契约域）。
 * 身份来自服务端 body，最后才回落到 artifact_id（见 `resolveArtifactIdentity`）。
 * 未进入聚焦态时**不**发任何 spec 请求（避免对话态下的无谓取数）。
 */
const editingArtifact = computed(() =>
  surfaceMode.value === 'artifact_focus' &&
  activeArtifact.value &&
  (activeArtifact.value.artifact_type === 'lesson_plan_spec' || activeArtifact.value.artifact_type === 'teaching_deck_spec')
    ? (activeArtifact.value as ActiveArtifact)
    : null,
)
const editingRevisionHint = computed(() => (editingArtifact.value ? editingArtifact.value.revision : null))
const { editing, handlers } = useArtifactEditing(editingArtifact, artifactBody, editingRevisionHint)

const title = computed(() => {
  const t = sessionView.value?.title
  return typeof t === 'string' && t ? t : copyFor('prep.session.untitled')
})

const subtitle = computed(() => {
  const s = sessionView.value?.status
  if (typeof s !== 'string' || !s) return null
  const key = s === 'waiting_confirmation' ? 'prep.session.status.waiting_confirmation'
    : s === 'artifact_ready' ? 'prep.session.status.artifact_ready'
      : s === 'active' ? 'prep.session.status.active' : 'prep.session.status.unknown'
  return copyWith('prep.session.status_prefix', copyFor(key))
})

/** 动态里程碑（DESIGN-CORRECTION-R2 §1-2）：未启用项不进分母，不用固定 N/6。 */
const milestones = computed(() => buildMilestones(facts.value))
const milestonesOpen = ref(false)

/**
 * Focus 外壳顶部 meta：`班级 · 时长 · 教材`。
 * 班级只显示 `classes.name`（`session.class_name`）；**禁止** `class_id.slice(0,8)` 冒充。
 * 缺失的片段不显示（不填假班级/假教材）。
 */
const focusMeta = computed(() => {
  const parts: string[] = []
  const ctx = context.value ?? {}
  const source = (ctx.source ?? {}) as Record<string, unknown>
  const section = (ctx.section ?? {}) as Record<string, unknown>
  const className = sessionView.value?.class_name
  if (typeof className === 'string' && className.trim()) parts.push(className.trim())
  const dur = (ctx.selection as Record<string, unknown> | undefined)?.estimated_duration_minutes
  if (typeof dur === 'number') parts.push(`${dur} min`)
  const sec = typeof section.title === 'string' ? section.title : null
  const src = typeof source.title === 'string' ? source.title : null
  if (src) parts.push(src)
  if (sec) parts.push(sec)
  return parts.length ? parts.join(' · ') : subtitle.value
})

/**
 * 空态建议：**只**来自服务端 `available_actions` 的 copy_key，经 i18n 解析。
 * 前端**不拼装**模型推荐语句冒充 Agent 建议；无动作则诚实空态。
 */
const suggestions = computed<string[]>(() =>
  availableActions.value
    .filter((a) => !a.disabled && a.copy_key)
    .slice(0, 3)
    .map((a) => copyFor(a.copy_key)),
)

/** ProposalDiff：8100 当前未暴露局部修订端点 → 恒为 null（不造假 diff）。 */
const diff = ref<DiffView | null>(null)

// ── 事件处理（一律委托 store，保持单一取数通道）────────────────────────────
function onSend(text: string) {
  store.setPref('draft', '')
  void store.send(text)
}
function onDraft(v: string) {
  store.setPref('draft', v)
}
function onRun(action: string, target: string | null, payload?: Record<string, unknown>) {
  void store.runAction(action, target, payload)
}
function onConflict(messageId: string) {
  void store.runAction('reload_after_conflict', messageId)
}
function onRetry(messageId: string) {
  void store.runAction('retry_run', messageId)
}
function onApplyEdits(_messageId: string, text: string) {
  void store.runAction('apply_edits', null, { summary: text, fields: {} })
}
function onSelect(selection: { textbook_id?: string; section_id?: string; class_id?: string }) {
  // 选择卡只回传服务端下发的 UUID；正文仅作为审计可读的动作描述，
  // 实际选择通过 POST messages.selection 进入 8100 编排层，不绕过服务端事实源。
  void store.send('选择已确认', undefined, selection)
}
function onOpenFocus(selected?: ActiveArtifact) {
  const artifact = selected ?? activeArtifact.value
  const sessionId = resolvedSessionId.value
  // 2026-09-14 备课中心简化：AI 生成结束后唯一的工作面是 PPT 编辑工作台。
  // 「大纲」（teaching_blueprint）与「教案」（lesson_plan_spec）已不再可聚焦
  // （见 projection/types.ts FOCUSABLE_ARTIFACT_TYPES），因此这里不再有它们的入口分支。
  if (globalRouter && sessionId && artifact?.artifact_type === 'teaching_deck_spec') {
    void globalRouter.push({ name: 'tv3-slides', query: { session: sessionId, deck: artifact.artifact_id, mode: 'vnext' } })
    return
  }
  store.openFocus()
}
function onCloseFocus() {
  store.closeFocus()
}
function openOverlay(o: 'evidence' | 'version_history' | 'qa' | 'export') {
  store.openOverlay(o)
}
function closeOverlay() {
  store.closeOverlay()
}
function dismissDock() {
  store.dismissDock()
}
function onToggleAssistant() {
  store.setPref('assistantCollapsed', !ui.value.assistantCollapsed)
}
function onReload() {
  void store.reload()
}
function onBack() {
  // Focus Mode 下全局侧栏被刻意隐藏（T22.1 UX-CONTRACT），因此这里必须提供真实出口：
  //  - 处于 artifact_focus 时，先收起画布回到对话（保持原有语义）；
  //  - 已在对话态时，返回教师工作台，而不是什么都不做。
  // 之前的实现在对话态下是空操作 —— 一旦该 surface 成为教师落地页，
  // 教师将没有任何路径抵达题库/组卷/批改/学情/资源，形成死路。
  // 仍走 router（非整页跳转），保留会话内状态。
  if (store.activeOverlay !== 'none') {
    store.closeOverlay()
    return
  }
  if (store.canOpenFocus) {
    // 有产物可编辑时，back 优先收起聚焦态而非离开页面
    store.closeFocus()
    return
  }
  if (globalRouter) {
    void globalRouter.push('/teacher-v3/today')
    return
  }
  window.location.href = '/teacher-v3/today'
}
function onSignin() {
  // 交由既有登录流程，不实现第二套鉴权
  window.location.href = '/login'
}
function onStop() {
  // 停止生成：服务端下发 cancel_run 时**真正**中断本次 run。
  // 此前只 `reload()` —— 按钮点了没有任何可见效果，教师会误判成「消息发不出去」
  // （2026-09-14 真实反馈）。无该动作时保持诚实：只重拉快照，不伪造「已停止」。
  const cancel = availableActions.value.find((a) => !a.disabled && a.action === 'cancel_run')
  if (cancel) {
    void store.runAction('cancel_run', cancel.target)
    return
  }
  void store.reload()
}
function onAttach() {
  // 附件入口：8100 尚未提供上传端点 → 不改业务状态，仅保持入口存在
}
function onAskRevision(payload: { locator: string; instruction: string }) {
  // 局部修订需要服务端 `propose_blueprint_revision` 端点；当前 8100 未暴露 →
  // 若服务端下发了该动作就执行，否则**不伪造** diff（诚实保持 null）。
  const hasAction = availableActions.value.some((a) => a.action === 'propose_blueprint_revision')
  if (hasAction) void store.runAction('propose_blueprint_revision', payload.locator)
}
function onAcceptDiff() {
  diff.value = null
  void store.reload()
}
function onRejectDiff() {
  diff.value = null
}

// Escape 关闭 drawer（可达性）
function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape' && store.activeOverlay !== 'none') store.closeOverlay()
}

const resolvedSessionId = computed(
  () => props.sessionId ?? (route?.params?.sessionId as string | undefined) ?? null,
)

/**
 * WP-G：旧 `/teacher-v3/slides` 兼容跳转会带 `?focus=artifact`，
 * 表示"这次进来是要看那份产物的"。仅在确实可聚焦（有 artifact）时才开画布——
 * 依旧由 projection 决定形态，不做任何猜测；无该 query 时行为完全不变。
 */
const wantsArtifactFocus = route?.query?.focus === 'artifact'

/**
 * 2026-09-14 备课中心简化：AI 生成内容结束后，自动确认教学方案 → 自动投影双 Spec
 * → 直接把课件交给 PPT 编辑工作台，教师不再经过「大纲预览」「教案预览」两个环节。
 *
 * 这里只按顺序执行 8100 下发的真实动作，不发明流程；任何失败都会停止推进并把
 * 原因显示在页面上（见 `autoFlowError`），绝不静默失败或假装生成完成。
 * 必须定义在 `resolvedSessionId` 之后：该 watch 的 immediate 回调会立即读取它。
 */
const { error: autoFlowError, reset: resetAutoFlow } = useAutoDeckFlow({
  sessionId: computed(() => resolvedSessionId.value ?? null),
  snapshot,
  actions: availableActions,
  phase: sessionPhase,
  busy,
  run: (action, target, payload) => store.runAction(action, target, payload),
  openDeck: (sessionId, deckId) => {
    if (globalRouter) {
      void globalRouter.push({ name: 'tv3-slides', query: { session: sessionId, deck: deckId, mode: 'vnext' } })
      return
    }
    // 隔离挂载（组件单测 / harness）下没有 router：不伪造"已跳转"，
    // 也不让整页导航把挂载环境弄脏——真实产品总是带 router。
    try {
      const url = `/teacher-v3/slides?session=${encodeURIComponent(sessionId)}&deck=${encodeURIComponent(deckId)}&mode=vnext`
      if (typeof window !== 'undefined' && window.location) window.location.href = url
    } catch {
      /* 无法导航；保留当前会话，教师仍可通过产物入口手动打开 */
    }
  },
})

onMounted(() => {
  window.addEventListener('keydown', onKeydown)
  store.attach(resolvedSessionId.value ?? null)
  if (wantsArtifactFocus) {
    // 注意：immediate 回调可能同步触发，stop 必须允许"尚未赋值"（否则 TDZ 报错）
    let stop: (() => void) | null = null
    stop = watch(() => store.canOpenFocus, (ok) => {
      if (!ok) return
      store.openFocus()
      stop?.()
    }, { immediate: true })
  }
})
watch(resolvedSessionId, (id) => store.attach(id ?? null))
onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKeydown)
  store.detach()
})
</script>

<style scoped>
.surface {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  background: var(--ma-surface-page);
}

/* 自动推进失败条：只在真的失败时出现，且必须给出可执行动作（重试）。 */
.surface__auto-error {
  display: flex;
  align-items: center;
  gap: var(--ma-space-2);
  margin: 0;
  padding: var(--ma-space-2) var(--ma-space-4);
  border-bottom: 1px solid var(--ma-surface-border);
  background: var(--ma-surface-subtle);
  color: var(--ma-status-danger);
  font-size: var(--ma-font-size-body);
}

.surface__auto-retry {
  margin-left: auto;
  height: 28px;
  padding: 0 var(--ma-space-3);
  border: 1px solid currentColor;
  border-radius: var(--ma-radius-control);
  background: transparent;
  color: inherit;
  font-size: var(--ma-font-size-body);
  cursor: pointer;
}
</style>
