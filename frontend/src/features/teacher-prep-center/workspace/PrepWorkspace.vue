<script setup lang="ts">
/**
 * 工作台路由组件（/teacher-v3/prep/w/:sessionId/:tab?）。
 *
 * - 挂载即拉权威快照（刷新/重进还原）；
 * - tab ∈ classroom|ppt|plan|materials|overview|chat（缺省 classroom），切换由 Shell 写 URL，
 *   本组件渲染对应 pane；chat = S2 对话三栏主面（R4b）；
 * - 顶栏 = PrepCenterShell（黄金图 §1）；
 * - 实时事件 = SSE（durable+live 双通道；选择卡/蓝图/AI 阶段实时驱动）；
 * - 后台 job（蓝图/工件生成）进行中 → 每 5s 静默轮询权威快照兜底（SSE 断线兜底），
 *   终态即停（进度语义完全来自服务端 job_summary，无前端假进度）；
 * - 等待 AI 回复（最后一条 timeline 是 user / session.status=processing）→ 每 3s 兜底轮询，
 *   AI 回复出现或 90s 上限即停（受理型异步链路无 job 行，job 轮询覆盖不到）。
 */
import { computed, onBeforeUnmount, watch } from 'vue'
import { useRoute } from 'vue-router'
// --pc-* 视觉 token 必须随路由根组件注入（index.ts 的出口不参与路由加载）
import '@/features/teacher-prep-center/tokens/prep.css'
import PrepCenterShell from '@/features/teacher-prep-center/shell/PrepCenterShell.vue'
import ClassroomPane from '@/features/teacher-prep-center/workspace/ClassroomPane.vue'
import DeckPane from '@/features/teacher-prep-center/workspace/DeckPane.vue'
import LessonPlanPane from '@/features/teacher-prep-center/workspace/LessonPlanPane.vue'
import MaterialsPane from '@/features/teacher-prep-center/workspace/MaterialsPane.vue'
import OverviewPane from '@/features/teacher-prep-center/workspace/OverviewPane.vue'
import { usePrepSessionStore } from '@/features/teacher-prep-center/stores/prepSession'
import { openPrepEventStream, type PrepEventStream } from '@/features/teacher-prep-center/api/eventsClient'
import ChatComposer from '@/features/teacher-prep-center/workspace/conversation/ChatComposer.vue'
import ConversationStream from '@/features/teacher-prep-center/workspace/conversation/ConversationStream.vue'
import TextbookEvidencePanel from '@/features/teacher-prep-center/workspace/conversation/TextbookEvidencePanel.vue'
import {
  bootstrapConversation,
  disposeConversation,
  onConversationEvent,
  reloadMessages,
  setStreamStatus,
} from '@/features/teacher-prep-center/workspace/conversation/useConversation'

const route = useRoute()
const store = usePrepSessionStore()

const sessionId = computed(() => String(route.params.sessionId || ''))

/* 权威快照 timeline 变化（SSE 快照 / 等待 AI 回复轮询 / 发送响应）→ 重载消息流。
 * 与 useConversation 内部兜底轮询并列，作为 SSE 在开发代理下被缓冲时的第二道自愈通道。 */
watch(
  () => store.current?.timeline?.length ?? 0,
  () => {
    if (store.currentSessionId) void reloadMessages()
  },
)

watch(
  sessionId,
  (id) => {
    if (id && id !== store.currentSessionId) {
      void store.loadWorkspace(id)
    }
    // 对话面：同步 bootstrap（会话切换即重置消息流并拉权威历史）
    if (id) bootstrapConversation(id)
  },
  { immediate: true },
)

/* ── SSE 实时事件（选择卡/蓝图提案/ai.stage/错误 → 权威快照刷新）────────── */
let stream: PrepEventStream | null = null

/** 会话 id 合法性守卫：非 UUID（空串 / undefined / 路由残缺）绝不开流，
 *  避免把坏 id 带进客户端内部重连循环（曾实测 GET /conversations/undefined/events）。 */
const SESSION_ID_RE = /^[0-9a-f-]{36}$/i

function isStreamableSessionId(id: string): boolean {
  return SESSION_ID_RE.test(id)
}

function closeStream() {
  stream?.close()
  stream = null
}

watch(
  sessionId,
  (id) => {
    closeStream()
    if (!id || !isStreamableSessionId(id)) return
    stream = openPrepEventStream(id, {
      onSnapshot: (ws) => {
        if (store.currentSessionId === id && !store.aiBusy) store.current = ws
      },
      onEvent: (frame) => {
        // 会话守卫：旧会话的迟到帧不得驱动新会话的对话面（防串台）
        if (store.currentSessionId !== id) return
        // 对话面：SSE 增量转发（ai.stage / 终态 → 消息流重载）
        onConversationEvent(frame)
        const type = frame.event_type
        if (
          type === 'conversation.updated' ||
          type === 'conversation.selection.requested' ||
          type === 'blueprint.proposed' ||
          type === 'blueprint.confirmed' ||
          type === 'blueprint.node.updated' ||
          type === 'assistant.message.completed' ||
          type === 'conversation.run.completed' ||
          type === 'conversation.basic_mode.entered'
        ) {
          void store.refreshCurrent(id)
        }
        // ai.stage / error 由 store 的 AI 流程自身消费（sendAiMessage 打开的独立流）；
        // 本工作台级流同样驱动阶段展示（去重由 UI 层幂等消化）
        const p = (frame.payload ?? {}) as Record<string, unknown>
        if (type === 'ai.stage' && store.aiBusy) {
          store.aiStage = { stage: String(p.stage ?? ''), label: String(p.stage_label ?? '') }
        }
      },
      onStatus: (status) => {
        setStreamStatus(status)
      },
      onError: () => {
        /* 断线由事件流内部退避重连；durable replay 权威补齐 */
      },
    })
  },
  { immediate: true },
)

const activeTab = computed(() => {
  const t = route.params.tab
  if (t === 'ppt' || t === 'plan' || t === 'materials' || t === 'overview' || t === 'chat') return t
  return 'classroom'
})

/** 底部 AI 条渲染白名单：对话 / 课堂设计 / PPT / 教材与资料。
 *  教案页输入在右面板 PlanAiPanel（§3-S5.4）；本次备课/导出页无底部 AI 条（golden3）。 */
const COMPOSER_TABS = new Set(['chat', 'classroom', 'ppt', 'materials'])
const showComposer = computed(() => !!store.current && COMPOSER_TABS.has(activeTab.value))

/* ── 后台 job 轮询（真实 job_summary 驱动；终态停） ─────────────────── */
const ACTIVE_JOB_STATES = new Set(['PENDING', 'CLAIMED', 'STARTED', 'RETRYING'])
let pollTimer: ReturnType<typeof setInterval> | null = null

function hasActiveJobs(): boolean {
  const jobs = store.current?.job_summary ?? []
  return jobs.some((j) => ACTIVE_JOB_STATES.has(String((j as { status?: unknown }).status)))
}

function stopPoll() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

watch(
  () => store.current,
  () => {
    if (hasActiveJobs()) {
      if (!pollTimer) {
        pollTimer = setInterval(() => {
          if (!hasActiveJobs()) {
            stopPoll()
            return
          }
          const id = store.currentSessionId
          if (id) void store.refreshCurrent(id)
        }, 5000)
      }
    } else {
      stopPoll()
    }
  },
)

/* ── 「等待 AI 回复」兜底轮询（与 job 轮询并列，条件各自独立）──────────
 * 「受理 → AI 回复」异步链路不产生 job 行，job 轮询覆盖不到：一旦 SSE 帧没被消费，
 * UI 会永久停在空态。此处按真实快照判定「老师刚发完话、AI 还没回」并 3s 兜底拉取权威快照，
 * 出现 assistant 消息或超过 90s 上限即停（不无限轮询、不伪造进度）。 ────────── */
const WAIT_POLL_MS = 3000
const WAIT_POLL_MAX_MS = 90_000
let waitTimer: ReturnType<typeof setInterval> | null = null
let waitStartedAt = 0
/** 本轮轮询自身写入的错误文案（恢复成功时按文案精确清除，不误伤其它错误态）。 */
let waitPollError: string | null = null

/** 权威快照最后一条 timeline 消息的角色（空 timeline → ''）。 */
function lastTimelineRole(): string {
  const tl = store.current?.timeline ?? []
  if (!tl.length) return ''
  return String(tl[tl.length - 1]?.role ?? '')
}

/** 是否仍在「等待 AI 回复」：最后一条是 user，或服务端受理后语义为 processing。 */
function awaitingAiReply(): boolean {
  if (!store.current) return false
  return lastTimelineRole() === 'user' || store.current.session.status === 'processing'
}

function stopWaitPoll() {
  if (waitTimer) {
    clearInterval(waitTimer)
    waitTimer = null
  }
  waitStartedAt = 0
  waitPollError = null
}

async function tickWaitPoll() {
  const id = store.currentSessionId
  // 已切走 / AI 已回复 / 超时上限 → 停止
  if (!id || !awaitingAiReply() || Date.now() - waitStartedAt > WAIT_POLL_MAX_MS) {
    stopWaitPoll()
    return
  }
  const ok = await store.refreshCurrent(id, (e) => {
    // 失败不静默：写入现有错误通道；但不放弃轮询（下一次成功即恢复）
    const msg = e instanceof Error ? e.message : String(e)
    waitPollError = msg
    store.currentError = msg
  })
  if (ok && waitPollError && store.currentError === waitPollError) {
    store.currentError = null
    waitPollError = null
  }
}

watch(
  () => store.current,
  () => {
    if (awaitingAiReply()) {
      if (!waitTimer) {
        waitStartedAt = Date.now()
        waitTimer = setInterval(() => void tickWaitPoll(), WAIT_POLL_MS)
      }
    } else {
      stopWaitPoll()
    }
  },
  { immediate: true },
)

// 会话切换：立即停掉上一会话的等待轮询（新会话由 store.current 变化重新判定）
watch(sessionId, () => {
  stopWaitPoll()
})

onBeforeUnmount(() => {
  stopPoll()
  stopWaitPoll()
  closeStream()
  disposeConversation()
})
</script>

<template>
  <div class="pc-workspace">
    <PrepCenterShell />
    <main class="pc-workspace__body">
      <div v-if="store.currentLoading && !store.current" class="pc-workspace__state">
        正在加载备课会话…
      </div>
      <div v-else-if="store.currentError && !store.current" class="pc-workspace__state pc-workspace__state--error">
        {{ store.currentError }}
      </div>
      <template v-else>
        <!-- S2 对话三栏主面（R4b，golden1）：左 备课导航+最近备课（PrepSideNav chat 形态，
             固定于内容区左缘）｜ 中 消息流（composer+阶段标签+确认卡行为原样保留）｜
             右 教材依据面板 ≈300px。抽屉形态已移除（单实现冻结 G2）。 -->
        <div v-if="activeTab === 'chat'" class="pc-chat3">
          <ConversationStream class="pc-chat3__stream" expanded />
          <TextbookEvidencePanel class="pc-chat3__evidence" :session-id="sessionId" />
        </div>
        <ClassroomPane v-else-if="activeTab === 'classroom'" />
        <DeckPane v-else-if="activeTab === 'ppt'" />
        <LessonPlanPane v-else-if="activeTab === 'plan'" />
        <MaterialsPane v-else-if="activeTab === 'materials'" />
        <OverviewPane v-else />
      </template>
    </main>
    <!-- 底部 AI 条（唯一 composer，VISUAL-SPEC-V3 §5.6 单一 composer 铁律）：
         仅 课堂设计 / PPT / 教材与资料 三页渲染（白名单）；
         教案页输入在右面板 PlanAiPanel（§3-S5.4），本次备课页无底部 AI 条（golden3）。 -->
    <ChatComposer v-if="showComposer" />
  </div>
</template>

<style scoped>
.pc-workspace {
  /* --pc-viewport-h：嵌入外框后内容区可用高度（R1 G1；默认 100vh） */
  min-height: var(--pc-viewport-h, 100vh);
  display: flex;
  flex-direction: column;
  background: var(--pc-bg);
}
.pc-workspace__body {
  flex: 1;
}
.pc-workspace__state {
  padding: 80px 0;
  text-align: center;
  font-size: 14px;
  color: var(--pc-text-muted);
  font-family: var(--pc-font);
}
.pc-workspace__state--error {
  color: var(--pc-danger);
}

/* S2 对话三栏主面：中栏消息流 + 右栏教材依据面板（左栏 = 固定 PrepSideNav）。
 * 1440×900 无横向滚动：右栏 300px 固定，中栏自适应；整面在内容区内不越界。 */
.pc-chat3 {
  display: flex;
  gap: 12px;
  box-sizing: border-box;
  height: calc(var(--pc-viewport-h, 100vh) - var(--pc-header-h));
  padding: 12px 16px;
}
.pc-chat3__stream {
  flex: 1;
  min-width: 0;
  min-height: 0;
}
/* 底部 AI 条为固定悬浮层（单一 composer）：消息区留出底部安全距，最后一条不被压住 */
.pc-chat3__stream :deep(.pc-conv__scroll) {
  padding-bottom: 96px;
}
.pc-chat3__evidence {
  min-height: 0;
}
</style>
