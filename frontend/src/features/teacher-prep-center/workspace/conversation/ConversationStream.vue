<script setup lang="ts">
/**
 * 对话面「会话条」（VISUAL-SPEC-V2 §3.2 裁决：对话框以会话条形式内嵌在教学流程上方）。
 *
 * - 无蓝图：作为课堂设计主列主内容（跟 AI 对话确定方向的真实场景）；
 *   有蓝图：主列让位给教学流程（对齐黄金图），完整对话改由顶栏「对话」抽屉承载
 *   —— 抽屉传 `:expanded="true"`（不折叠形态），同一组件、同一条真实数据流；
 * - 白卡，落在 1300 内容列内；**折叠态锚定最后一个连续 assistant 消息组**（蓝图卡+证据卡
 *   连发时整组可见；busy 期间 = 最近一问一答 + 新消息）且整条会话条高度硬上限 170px
 *   （CSS 折叠态 max-height，含卡片，可滚动），点「查看全部对话」展开滚动区（max-height 360）
 *   ——避免整条对话把课堂设计流程挤到屏幕外（失重布局）；
 * - 消息唯一来源：后端 `GET /messages`（bootstrap/reload/SSE 增量统一由 useConversation 拉权威）；
 * - 教师右 / AI 左、AI 头像与名称、消息时间、选择卡与已选回显、发送中态；
 * - 卡片型消息占满会话条宽度（MessageBubble 负责）；
 * - 全状态覆盖：加载骨架 / 空对话引导态 / 生成中（服务端阶段文案 + 已用时，可停止）/ 断线重连；
 * - 输入统一走底部 AI 条（本会话条内不放输入框）。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { usePrepSessionStore } from '@/features/teacher-prep-center/stores/prepSession'
import {
  PrepCenterApiError,
  runPrepAction,
} from '@/features/teacher-prep-center/api/prepCenterApi'
import { newIdempotencyKey } from '@/features/teacher-prep-center/api/deckApi'
import MessageBubble from './MessageBubble.vue'
import SessionHeadCard from './SessionHeadCard.vue'
import { retryMessage, pickSection, reloadMessages, sendConversationMessage, useConversation } from './useConversation'
import type { PrepChatMessage, SelectionCardInfo } from './useConversation'

const store = usePrepSessionStore()
const route = useRoute()
const { state } = useConversation()

/**
 * 展开/不折叠形态（抽屉专用）：展示全部消息、滚动区撑满容器。
 * prop 为 true 时不渲染折叠开关与内部标题（抽屉面板自带标题与关闭），
 * 主列场景（默认 false）行为与之前完全一致。
 */
const props = withDefaults(defineProps<{ expanded?: boolean }>(), { expanded: false })
const unfolded = computed(() => props.expanded)

const scroller = ref<HTMLElement | null>(null)

/** 折叠态兜底条数（无任何 assistant 消息时至少展示最后 1 条；有 assistant 组时锚定整组）。 */
const COLLAPSED_COUNT = 1
/** 主列内「查看全部对话」开关（与抽屉形态 unfolded 相互独立：同一组件两种入口）。 */
const showAll = ref(false)

/** 防御式消息视图：过滤 null / 非对象项（服务端脏数据绝不导致 v-for 崩溃）。 */
const safeMessages = computed<PrepChatMessage[]>(() =>
  (state.messages as (PrepChatMessage | null | undefined)[]).filter(
    (m): m is PrepChatMessage => !!m && typeof m === 'object',
  ),
)
const hasMore = computed(() => safeMessages.value.length > COLLAPSED_COUNT)
/**
 * 折叠态展示范围：**最后一个连续 assistant 消息组**（2026-10-04 修，golden 对齐）。
 *
 * 旧实现锚定「最后一条非 pending assistant」（一条）：蓝图卡+证据卡等连发时，
 * 组内靠前的可操作卡被折叠吞掉。现改为整组可见；busy 期间（新消息在途）窗口
 * 自然放宽为「最近一问一答 + 新消息」——组前的教师提问与组后的在途消息均在
 * slice(start) 范围内。hiddenCount 行始终可点开（查看全部对话）。
 * （声明顺序在 hiddenCount 之前：本项目曾因 TDZ 引用顺序导致白屏，此处显式规避。）
 */
const visibleMessages = computed(() => {
  const msgs = safeMessages.value
  if (unfolded.value || showAll.value || !hasMore.value) return msgs
  let start = msgs.length - COLLAPSED_COUNT
  // 从尾向前找最后一条服务端 assistant 消息
  let end = msgs.length - 1
  while (end >= 0 && !(msgs[end]?.role === 'assistant' && !msgs[end]?.pending)) end -= 1
  if (end >= 0) {
    start = end
    // 连续 assistant 消息组整组可见（蓝图卡+证据卡连发不拆散）
    while (start > 0 && msgs[start - 1]?.role === 'assistant' && !msgs[start - 1]?.pending) start -= 1
    // 连同该组对应的教师提问一起展示（一问一答成对，符合对话直觉）
    if (start > 0 && msgs[start - 1]?.role === 'user') start -= 1
  }
  return msgs.slice(start)
})
const hiddenCount = computed(() => Math.max(0, safeMessages.value.length - visibleMessages.value.length))

/** 所属教材展示名（真实投影；缺失则省略）。 */
const sourceTitle = computed(() => store.current?.artifact.context?.source?.title ?? null)

/** 最后一条选择卡下标 —— 仅当它仍是最后一条消息且未在处理中时为「活跃可点选」。 */
const lastSelectionIndex = computed(() => {
  const msgs = safeMessages.value
  for (let i = msgs.length - 1; i >= 0; i -= 1) {
    if (msgs[i]?.component?.type === 'selection_card') return i
  }
  return -1
})
/** 会话级抑制（G6）：章节一旦确定（真实投影有 section）→ 选择卡永不激活/不再渲染胶囊云。 */
const sectionLocked = computed(
  () => !!(store.current?.artifact?.context?.section?.id || store.current?.session?.intent?.section_id),
)
const selectionActive = computed(
  () =>
    lastSelectionIndex.value >= 0 &&
    lastSelectionIndex.value === safeMessages.value.length - 1 &&
    !state.busy &&
    !sectionLocked.value,
)

const showSkeleton = computed(() => state.loading && safeMessages.value.length === 0)
const showEmpty = computed(() => !state.loading && state.loaded && safeMessages.value.length === 0)
const offline = computed(() => state.streamStatus === 'reconnecting')

/* ── 生成中：服务端阶段文案（ai.stage）+ 已用时（真实计时，禁止假进度）────── */
const now = ref(Date.now())
let tick: ReturnType<typeof setInterval> | null = null

function startTick(): void {
  if (tick) return
  now.value = Date.now()
  tick = setInterval(() => (now.value = Date.now()), 1000)
}
function stopTick(): void {
  if (tick) {
    clearInterval(tick)
    tick = null
  }
}

watch(
  () => state.busy,
  (busy) => (busy ? startTick() : stopTick()),
  { immediate: true },
)
onBeforeUnmount(stopTick)

const elapsedText = computed(() => {
  const since = state.busySince ?? now.value
  const sec = Math.max(0, Math.floor((now.value - since) / 1000))
  return `${String(Math.floor(sec / 60)).padStart(2, '0')}:${String(sec % 60).padStart(2, '0')}`
})

async function scrollToBottom(): Promise<void> {
  await nextTick()
  const el = scroller.value
  if (el) el.scrollTop = el.scrollHeight
}

watch(
  () => [safeMessages.value.length, state.busy, state.stage] as const,
  () => void scrollToBottom(),
)
/** 展开瞬间（主列开关或抽屉形态）滚到最新（展开后滚动区才存在）。 */
watch([showAll, unfolded], () => void scrollToBottom())

onMounted(() => void scrollToBottom())

/** 选择卡点选：option + 卡上下文（kind / source_id 由 MessageBubble 从后端 card props 透传）。 */
function onPick(option: { id: string; label: string }, card: SelectionCardInfo): void {
  void pickSection(option, card)
}
function onRetry(id: string): void {
  void retryMessage(id)
}

/* ── 蓝图确认卡三按钮（G10）+ 决策卡动作 ─────────────────────────────────── */

const confirmBusy = ref(false)
const confirmError = ref('')

/** 最后一条已落库消息 id（决策卡只在它是最新一条时可操作，不产生幽灵按钮）。 */
const lastServerId = computed(() => {
  const msgs = safeMessages.value.filter((m) => !m.pending)
  return msgs.length ? msgs[msgs.length - 1].id : ''
})

/** 「确认，继续生成」：真实 confirm_proposal action（服务端状态机落定，G9 备课状态真实改变）。 */
async function onConfirmBlueprint(payload: { blueprintId: string }): Promise<void> {
  const sessionId = store.currentSessionId
  const base = store.current
  if (!sessionId || !base || confirmBusy.value) return
  const hit = (base.available_actions ?? []).find((a) => a.action === 'confirm_proposal')
  const target = hit?.target ?? payload.blueprintId ?? null
  if (!target) return
  confirmBusy.value = true
  confirmError.value = ''
  const exec = (revision: number) =>
    runPrepAction(sessionId, {
      action: 'confirm_proposal',
      target,
      expectedRevision: revision,
      idempotencyKey: newIdempotencyKey(),
    })
  try {
    let snapshot
    try {
      snapshot = await exec(base.session.revision)
    } catch (e) {
      // 409：快照过期 → 拉权威 revision 后重试一次（与 ClassroomPane 同一恢复路径）
      if (e instanceof PrepCenterApiError && e.kind === 'conflict') {
        await store.refreshCurrent(sessionId)
        const fresh = store.current
        if (!fresh || fresh.session.revision === base.session.revision) throw e
        snapshot = await exec(fresh.session.revision)
      } else {
        throw e
      }
    }
    if (store.currentSessionId === sessionId) store.current = snapshot
    await reloadMessages()
  } catch (e) {
    confirmError.value =
      e instanceof PrepCenterApiError ? e.detail || '确认失败，请重试。' : '确认失败，请重试。'
  } finally {
    confirmBusy.value = false
  }
}

/** 「修改需求」：聚焦底部 AI 条（工作台唯一输入入口，不新开输入框）。 */
function onModifyRequest(): void {
  window.dispatchEvent(new CustomEvent('pc:focus-composer'))
}

/** 「查看教材依据」（R4b）：S2 三栏主面 → 聚焦/高亮右栏教材依据面板（抽屉形态已移除）；
 *  非对话视图（课堂设计内嵌会话条）→ 打开既有「教材与资料」抽屉（真实依据数据）。 */
function onViewEvidence(): void {
  if (route.params.tab === 'chat') {
    window.dispatchEvent(new CustomEvent('pc:show-evidence-panel'))
    return
  }
  store.toggleMaterials(true)
}

/** 决策卡按钮：结构化回传（select_section 直传 id / send_text 发真实消息），不经 LLM。 */
function onDecide(option: {
  id: string
  label: string
  action: string
  section_id?: string | null
  textbook_id?: string | null
  text?: string | null
}): void {
  if (option.action === 'select_section' && option.section_id) {
    void sendConversationMessage(option.label, {
      selection: { textbook_id: option.textbook_id ?? undefined, section_id: option.section_id },
    })
    return
  }
  const text = (option.text ?? option.label).trim()
  if (text) void sendConversationMessage(text)
}

/** 「查看教学流程」：把课堂设计时间轴滚入视野（真实定位；时间轴在同一页下方）。 */
function locateFlow(): void {
  document.querySelector('.pc-timeline')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

/** 卡片内「展开全文」：会话条放开折叠高度上限（改为滚动区），避免内容被裁掉。
 *  滚动定位由 MessageBubble 自己 scrollIntoView（nearest 到本卡）——
 *  这里不再无条件 scrollToBottom：展开靠前的卡会被视野甩到最后一条（N5「无可见反馈」根因）。 */
const cardOverflow = ref(false)
function onOverflow(on: boolean): void {
  cardOverflow.value = on
}
</script>

<template>
  <div class="pc-conv-wrap">
    <!-- S2 会话头卡（golden1 中栏顶部）：返回链+状态 pill 行 + 头卡（图标/标题/副行/四步进度）。
         仅三栏主面（expanded，R4b：抽屉形态已移除后唯一 S2 形态）渲染；课堂设计内嵌会话条不加。 -->
    <SessionHeadCard v-if="unfolded" class="pc-conv__headcard" />

    <section
      class="pc-conv"
      :class="{ 'is-collapsed': !unfolded && !showAll && !cardOverflow, 'pc-conv--docked': unfolded }"
      aria-label="备课对话"
    >
    <!-- 抽屉形态由面板提供标题与关闭 → 此处仅在有断线提示时才保留条头（避免重复标题） -->
    <header v-if="!unfolded || offline" class="pc-conv__bar">
      <span v-if="!unfolded" class="pc-conv__bar-title">备课对话</span>
      <span v-if="offline" class="pc-conv__offline">
        <span class="pc-conv__offline-dot" aria-hidden="true"></span>连接已断开，正在重连…
      </span>
      <span class="pc-conv__bar-spacer"></span>
      <span v-if="hasMore && !showAll && !unfolded" class="pc-conv__bar-count">还有 {{ hiddenCount }} 条更早的消息</span>
      <button v-if="hasMore && !unfolded" type="button" class="pc-conv__toggle" @click="showAll = !showAll">
        {{ showAll ? '收起对话' : '查看全部对话' }}
        <svg viewBox="0 0 12 12" :class="{ 'is-open': showAll }" aria-hidden="true">
          <path d="M2.5 4.5 6 8l3.5-3.5" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" />
        </svg>
      </button>
    </header>

    <div ref="scroller" class="pc-conv__scroll" :class="{ 'is-expanded': unfolded || showAll || cardOverflow }">
      <!-- 加载骨架 -->
      <div v-if="showSkeleton" class="pc-conv__skeleton">
        <div class="pc-conv__sk-avatar"></div>
        <div class="pc-conv__sk-lines">
          <span class="pc-conv__sk-line" style="width: 46%"></span>
          <span class="pc-conv__sk-line" style="width: 72%"></span>
        </div>
      </div>

      <!-- 空对话引导态（结构化空态：渐变图标 + 标题 + 建议话题胶囊） -->
      <div v-else-if="showEmpty" class="pc-conv__empty">
        <span class="pc-conv__empty-icon" aria-hidden="true">
          <span class="pc-conv__empty-icon-ring"></span>
          <svg viewBox="0 0 24 24"><path d="M12 2.5c.5 0 1 .3 1.3.8l2.8 6.4 7 .7c.6.1 1.1.6 1.1 1.2s-.3 1.1-.9 1.3l-6 2.6 1.8 6.8c.2.6-.1 1.2-.7 1.4-.2.1-.4.1-.5.1-.4 0-.8-.2-1-.6L12 16.5l-4 6.7c-.3.4-.8.6-1.2.6-.2 0-.4 0-.6-.1-.5-.2-.8-.8-.7-1.4l1.8-6.8L1.4 13c-.6-.2-1-1.1-.9-1.3.1-.6.5-1.1 1.1-1.2l7-.7 2.8-6.4c.3-.5.8-.8 1.3-.8z" fill="currentColor" /></svg>
        </span>
        <p class="pc-conv__empty-title">今天想备什么课？</p>
        <p class="pc-conv__empty-hint">告诉我课题、课时和教材就行，比如：</p>
        <div class="pc-conv__empty-suggests">
          <button type="button" class="pc-conv__suggest">椭圆第一课时 · 45 分钟</button>
          <button type="button" class="pc-conv__suggest">立体几何二面角</button>
          <button type="button" class="pc-conv__suggest">函数单调性与最值</button>
        </div>
      </div>

      <!-- 消息流 -->
      <template v-else>
        <MessageBubble
          v-for="msg in visibleMessages"
          :key="msg.id"
          :message="msg"
          :selection-active="selectionActive && msg.id === safeMessages[lastSelectionIndex]?.id"
          :decision-active="!msg.pending && msg.id === lastServerId && !state.busy"
          :action-busy="confirmBusy"
          :selected-id="state.lastSelectionId"
          :source-title="sourceTitle"
          @pick="onPick"
          @retry="onRetry"
          @locate="locateFlow"
          @overflow="onOverflow"
          @confirm-blueprint="onConfirmBlueprint"
          @modify-request="onModifyRequest"
          @view-evidence="onViewEvidence"
          @decide="onDecide"
        />

        <!-- 失败态：中文软提示 + 行内重试（幂等，见 useConversation.retryMessage） -->
        <div v-if="!state.busy && (state.error || confirmError)" class="pc-conv__failed">
          <span>{{ confirmError || state.error }}</span>
          <button
            v-if="!confirmError && safeMessages.some((m) => m.pending === 'failed')"
            type="button"
            class="pc-conv__failed-retry"
            @click="onRetry(safeMessages.filter((m) => m.pending === 'failed').slice(-1)[0].id)"
          >
            重试
          </button>
        </div>

        <!-- 生成中：服务端阶段文案 + 已用时（无假进度条） -->
        <div v-if="state.busy" class="pc-conv__typing">
          <span class="pc-conv__typing-dot" aria-hidden="true"></span>
          <span class="pc-conv__typing-text">{{ state.stage || 'AI 正在处理…' }}</span>
          <span class="pc-conv__typing-time">已用时 {{ elapsedText }}</span>
        </div>
      </template>
    </div>
    </section>
  </div>
</template>

<style scoped>
/* 根容器（R2fix-S2）：S2 主面 = 会话头卡 + 会话条两段；课堂设计内嵌形态只有会话条。
 * 会话条沿用原 `.pc-conv` 全部行为/样式，仅外层多一段纵向 flex 包裹。 */
.pc-conv-wrap {
  display: flex;
  flex-direction: column;
  min-height: 0;
}
.pc-conv-wrap > .pc-conv {
  flex: 1 1 auto;
  min-height: 0;
}
.pc-conv__headcard {
  margin-bottom: 12px;
}
.pc-conv {
  display: flex;
  flex-direction: column;
  background: var(--pc-panel, #fff);
  border: 1px solid var(--pc-line, #e5e9f2);
  border-radius: var(--pc-radius-panel, 14px);
  box-shadow: var(--pc-shadow-card, 0 1px 2px rgba(15, 23, 42, 0.04));
  overflow: hidden;
  font-family: var(--pc-font);
}
.pc-conv__bar {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 9px 18px;
  border-bottom: 1px solid var(--pc-line, #e5e9f2);
  background: var(--pc-primary-softer, #f5f8ff);
}
.pc-conv__bar-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--pc-text, #111827);
}
.pc-conv__bar-spacer {
  flex: 1;
}
.pc-conv__bar-count {
  font-size: 12px;
  color: var(--pc-text-faint, #9ca3af);
}
.pc-conv__toggle {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  border: none;
  background: transparent;
  color: var(--pc-primary, #2563eb);
  font-size: 13px;
  font-family: inherit;
  cursor: pointer;
  padding: 2px 4px;
  border-radius: 4px;
}
.pc-conv__toggle:hover {
  background: var(--pc-primary-soft, #eff4ff);
}
.pc-conv__toggle svg {
  width: 11px;
  height: 11px;
  transition: transform 0.15s ease;
}
.pc-conv__toggle svg.is-open {
  transform: rotate(180deg);
}
.pc-conv__offline {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 3px 10px;
  border-radius: 999px;
  background: #fff7ed;
  color: var(--pc-warning, #d97706);
  font-size: 12px;
}
.pc-conv__offline-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--pc-warning, #d97706);
  animation: pc-conv-pulse 1.2s ease-in-out infinite;
}
@keyframes pc-conv-pulse {
  0%,
  100% {
    opacity: 1;
  }
  50% {
    opacity: 0.3;
  }
}
.pc-conv__scroll {
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 12px 18px;
  min-height: 96px;
}
/* 折叠态：只展示最后 1 条，且**高度贴合内容**（min-height:0）。
 * 2026-09-23 主编视觉打回：原折叠态沿用 min-height 96px，单条短消息时卡内出现
 * ~90px 死空白（截图实测），观感像渲染故障。折叠态不再保留最小高度。 */
.pc-conv.is-collapsed .pc-conv__scroll {
  min-height: 0;
  max-height: 170px;
  /* 折叠态也允许滚动：可操作卡（选择卡）必须完整可达（曾用 hidden，13 个选项会被截死） */
  overflow-y: auto;
}
.pc-conv__scroll.is-expanded {
  max-height: 360px;
  overflow-y: auto;
  overflow-x: hidden;
}
/* 抽屉形态：整条撑满容器高度，消息区自身滚动（主列折叠态样式不受影响）。
   必须写在 is-expanded 之后：同特异性下后者生效。 */
.pc-conv--docked {
  flex: 1;
  min-height: 0;
}
.pc-conv--docked .pc-conv__scroll {
  flex: 1;
  min-height: 0;
  max-height: none;
  overflow-y: auto;
  overflow-x: hidden;
}
.pc-conv__skeleton {
  display: flex;
  gap: 10px;
}
.pc-conv__sk-avatar {
  flex: none;
  width: 30px;
  height: 30px;
  border-radius: 50%;
  background: var(--pc-bg, #f6f8fb);
}
.pc-conv__sk-lines {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding-top: 4px;
}
.pc-conv__sk-line {
  display: block;
  height: 12px;
  border-radius: 6px;
  background: linear-gradient(90deg, #eef1f6 0%, #f6f8fb 50%, #eef1f6 100%);
  background-size: 200% 100%;
  animation: pc-conv-shimmer 1.4s ease-in-out infinite;
}
@keyframes pc-conv-shimmer {
  0% {
    background-position: 200% 0;
  }
  100% {
    background-position: -200% 0;
  }
}
.pc-conv__empty {
  margin: auto;
  padding: 20px 20px;
  text-align: center;
  max-width: 460px;
}
.pc-conv__empty-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 56px;
  height: 56px;
  border-radius: 50%;
  background: linear-gradient(135deg, #eef2ff 0%, #e0f2fe 100%);
  color: #6366f1;
  margin-bottom: 14px;
  position: relative;
}
.pc-conv__empty-icon-ring {
  position: absolute;
  inset: -4px;
  border-radius: 50%;
  background: linear-gradient(135deg, rgba(99,102,241,.25), rgba(6,182,212,.2));
  z-index: -1;
  filter: blur(8px);
}
.pc-conv__empty-icon svg {
  width: 28px;
  height: 28px;
  filter: drop-shadow(0 2px 4px rgba(99,102,241,.2));
}
.pc-conv__empty-title {
  margin: 0 0 6px;
  font-size: 19px;
  font-weight: 700;
  background: linear-gradient(135deg, #4338ca 0%, #0891b2 100%);
  -webkit-background-clip: text;
  background-clip: text;
  -webkit-text-fill-color: transparent;
}
.pc-conv__empty-hint {
  margin: 0 0 14px;
  font-size: 13px;
  line-height: 1.6;
  color: var(--pc-text-muted, #6b7280);
}
.pc-conv__empty-suggests {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  justify-content: center;
}
.pc-conv__suggest {
  border: 1px solid #e0e7ff;
  background: linear-gradient(135deg, #fff 0%, #f5f7ff 100%);
  border-radius: 999px;
  padding: 7px 16px;
  font-size: 13px;
  color: #4f46e5;
  font-family: inherit;
  cursor: pointer;
  transition: all .2s ease;
  box-shadow: 0 1px 2px rgba(99,102,241,.08);
}
.pc-conv__suggest:hover {
  background: linear-gradient(135deg, #eef2ff 0%, #e0f2fe 100%);
  border-color: #a5b4fc;
  transform: translateY(-1px);
  box-shadow: 0 4px 12px rgba(99,102,241,.18);
}
.pc-conv__typing {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  align-self: flex-start;
  margin-left: 40px;
  padding: 8px 14px;
  border-radius: 999px;
  background: var(--pc-bg, #f6f8fb);
  border: 1px solid var(--pc-line, #e5e9f2);
  font-size: 12px;
  color: var(--pc-text-muted, #6b7280);
}
.pc-conv__typing-text {
  color: var(--pc-text-2, #1f2937);
}
.pc-conv__typing-time {
  color: var(--pc-text-faint, #9ca3af);
  font-variant-numeric: tabular-nums;
}
.pc-conv__typing-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--pc-primary, #2563eb);
  animation: pc-conv-pulse 1.1s ease-in-out infinite;
}
.pc-conv__failed {
  display: flex;
  align-items: center;
  gap: 10px;
  align-self: flex-start;
  margin-left: 40px;
  padding: 8px 12px;
  border-radius: var(--pc-radius-btn, 8px);
  background: #fef2f2;
  border: 1px solid #f4d4d4;
  color: var(--pc-danger, #dc2626);
  font-size: 12px;
}
.pc-conv__failed-retry {
  border: 1px solid var(--pc-danger, #dc2626);
  background: transparent;
  color: var(--pc-danger, #dc2626);
  border-radius: 999px;
  padding: 2px 12px;
  font-size: 12px;
  font-family: inherit;
  cursor: pointer;
}
.pc-conv__failed-retry:hover {
  background: var(--pc-danger, #dc2626);
  color: #fff;
}
</style>