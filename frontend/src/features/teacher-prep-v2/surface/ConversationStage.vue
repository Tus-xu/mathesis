<template>
  <!--
    T22.1 · CONVERSATION_FOCUS（默认模式，UX-CONTRACT A）。
    铁律：
    - **居中单列**，阅读宽度 760–920px（不横向铺满）；
    - 默认不存在第二/第三栏、不存在空画布、不存在底部 Job Bar；
    - 只有用户接近底部时才自动跟随滚动；流式追加不抢输入焦点。
  -->
  <div class="stage" data-testid="prep-conversation-stage">
    <!-- 顶部信息栏由 `FocusShell` 统一承载（全局侧栏已隐藏，不在此重复头部） -->

    <div ref="scrollEl" class="stage__scroll" data-testid="prep-timeline-scroll" @scroll="onScroll">
      <div class="stage__column">
        <ContextChips :context="context" :session="session" @change="onChipChange" />

        <!-- 空态：一句真实上下文开场 + 服务端下发的真实建议动作（无数据则诚实空态） -->
        <section v-if="!timeline.length" class="stage__empty" data-testid="prep-empty-state">
          <h2 class="stage__empty-title">{{ emptyTitle }}</h2>
          <p class="stage__empty-desc">{{ emptyDesc }}</p>
          <div v-if="suggestions.length" class="stage__suggestions">
            <button
              v-for="s in suggestions"
              :key="s"
              type="button"
              class="stage__suggestion"
              data-testid="prep-suggestion"
              @click="emit('send', s)"
            >
              {{ s }}
            </button>
          </div>
          <p v-else class="stage__empty-note" data-testid="prep-empty-honest">
            {{ copyFor('prep.empty.no_actions') }}
          </p>
        </section>

        <!-- 消息时间线 -->
        <!--
          WP-K 可达性修复：整条对话此前**没有任何 aria-live 区域**，屏幕阅读器对
          「AI 正在流式回话」完全无感。会话记录用 role="log"（隐式 aria-live="polite"、
          aria-relevant="additions text"）语义最贴切：新增/追加的消息会被播报，
          且不会像对每条消息加 aria-live 那样在每次 token 时重读整条。
        -->
        <div
          v-else
          class="stage__timeline"
          role="log"
          aria-live="polite"
          aria-relevant="additions text"
          :aria-busy="busy"
          aria-label="备课对话记录"
        >
          <MessageItem
            v-for="m in timeline"
            :key="m.message_id"
            :message="m"
            :actions="actions"
            :busy="busy"
            :active-artifact="activeArtifact"
            @run="(a, t, p) => emit('run', a, t, p)"
            @retry="(id) => emit('retry', id)"
            @conflict="(id) => emit('conflict', id)"
            @open-evidence="emit('open-evidence')"
            @propose-edit="onProposeEdit"
            @apply-edits="(id, t) => emit('apply-edits', id, t)"
            @open-focus="emit('open-focus')"
            @select="(payload) => emit('select', payload)"
          />
        </div>

        <!-- 让最后一条消息不被输入框遮挡 -->
        <div v-if="quickActions.length" class="stage__actions" data-testid="prep-action-rail">
          <button
            v-for="action in quickActions"
            :key="action.action + (action.target ?? '')"
            type="button"
            class="stage__action"
            :data-testid="'prep-quick-action'"
            :disabled="busy"
            @click="emit('run', action.action, action.target, actionPayload(action))"
          >
            {{ action.copy_key ? copyFor(action.copy_key) : action.action }}
          </button>
        </div>
        <div class="stage__tail" aria-hidden="true" />
      </div>
    </div>

    <ArtifactDock
      v-if="showDock && activeArtifact"
      :artifact="activeArtifact"
      :artifacts="artifacts"
      @open="(artifact) => emit('open-focus', artifact)"
      @dismiss="emit('dismiss-dock')"
    />

    <div class="stage__composer-dock" data-testid="prep-composer-dock">
      <ComposerStage
        :model-value="draft"
        :disabled="false"
        :busy="busy"
        @update:model-value="(v) => emit('update:draft', v)"
        @send="(v) => emit('send', v)"
        @stop="emit('stop')"
        @attach="emit('attach')"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import ArtifactDock from './ArtifactDock.vue'
import ComposerStage from './ComposerStage.vue'
import ContextChips from './ContextChips.vue'
import MessageItem from './MessageItem.vue'
import { copyFor } from '@/features/teacher-prep-v2/i18n/copy'
import type { ActiveArtifact, SessionPhase, StreamPhase } from '@/features/teacher-prep-v2/projection/types'
import type { ActionView, MessageView } from '@/features/teacher-prep-v2/types/workspace'

const props = defineProps<{
  title: string
  subtitle?: string | null
  timeline: MessageView[]
  actions: ActionView[]
  context: Record<string, unknown> | null
  session: Record<string, unknown> | null
  activeArtifact: ActiveArtifact | null
  artifacts?: ActiveArtifact[]
  showDock: boolean
  sessionPhase: SessionPhase
  streamPhase: StreamPhase
  gapCount: number
  busy: boolean
  draft: string
  suggestions: string[]
}>()

const emit = defineEmits<{
  (e: 'send', text: string): void
  (e: 'stop'): void
  (e: 'attach'): void
  (e: 'update:draft', v: string): void
  (e: 'run', action: string, target: string | null, payload?: Record<string, unknown>): void
  (e: 'retry', messageId: string): void
  (e: 'conflict', messageId: string): void
  (e: 'apply-edits', messageId: string, text: string): void
  (e: 'open-evidence'): void
  (e: 'open-focus', artifact?: ActiveArtifact): void
  (e: 'dismiss-dock'): void
  (e: 'back'): void
  (e: 'select', payload: { textbook_id?: string; section_id?: string; class_id?: string }): void
}>()

const scrollEl = ref<HTMLElement | null>(null)
/** 只有用户接近底部时才自动跟随（UX-CONTRACT §6）。 */
let pinnedToBottom = true

function onScroll() {
  const el = scrollEl.value
  if (!el) return
  pinnedToBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 120
}

async function follow() {
  await nextTick()
  const el = scrollEl.value
  if (el && pinnedToBottom) el.scrollTop = el.scrollHeight
}

watch(() => props.timeline.length, follow)
onMounted(follow)

const emptyTitle = computed(() =>
  props.activeArtifact
    ? copyFor('prep.empty.title.continue')
    : copyFor('prep.empty.title.start'),
)

const emptyDesc = computed(() =>
  props.context
    ? copyFor('prep.empty.desc.with_context')
    : copyFor('prep.empty.desc.without_context'),
)

/**
 * 除提案确认/拒绝外的服务端动作（例如诊断与双 Spec 投影）必须在对话内可达。
 * 动作与文案仍完全来自 8100 available_actions；前端不发明流程或 target。
 *
 * 2026-09-14 备课中心简化：`confirm_proposal` / `project_specs` 已由
 * `useAutoDeckFlow` 自动执行（AI 生成结束 → 直达 PPT 编辑工作台），
 * 因此不再出现在动作栏里——否则教师会看到一个"还需要自己点一下"的中间环节，
 * 而且它在自动推进的瞬间就会消失，反而像故障。
 */
const AUTO_DRIVEN_ACTIONS = new Set(['confirm_proposal', 'reject_proposal', 'project_specs'])

const quickActions = computed(() =>
  props.actions.filter((action) => {
    if (action.disabled) return false
    if (AUTO_DRIVEN_ACTIONS.has(action.action)) return false
    return !['confirm', 'accept', 'reject', 'skip'].some((token) => action.action.includes(token))
  }),
)

function actionPayload(action: ActionView): Record<string, unknown> | undefined {
  // T24 is a paired projection: one explicit teacher action must request both
  // server-owned specs so the lesson-plan and deck shells share lineage.
  return action.action === 'project_specs' ? { kinds: ['lesson_plan', 'deck'] } : undefined
}

/** chip 点击 → 回填输入框（不跳表单页）。 */
function onChipChange(chip: { prompt: string }) {
  emit('update:draft', chip.prompt)
}

function onProposeEdit(_messageId: string) {
  emit('update:draft', copyFor('prep.draft.propose_edit'))
}
</script>

<style scoped>
.stage {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  /* B3：全幅柔和底色，氛围层由 FocusShell 的 token-backed layers 承载。 */
  background: var(--ma-surface-page);
}

.stage__scroll {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  scroll-behavior: auto;
}

.stage__column {
  /* 880px 舒适阅读宽度；居中，不横向铺满 */
  width: 100%;
  max-width: 880px;
  margin: 0 auto;
  padding: var(--ma-space-4) var(--ma-space-4) var(--ma-space-3);
  box-sizing: border-box;
}

.stage__timeline {
  display: flex;
  flex-direction: column;
}

.stage__tail {
  height: var(--ma-space-2);
}

/* B3：Composer 常驻底部，与主列同宽 */
.stage__composer-dock {
  flex: 0 0 auto;
  padding: 0 var(--ma-space-4) var(--ma-space-3);
  background: var(--ma-surface-page);
}
.stage__composer-dock :deep(.composer) {
  max-width: 880px;
  margin: 0 auto;
}

.stage__empty {
  padding: var(--ma-space-6) 0;
}

.stage__empty-title {
  margin: 0 0 var(--ma-space-2);
  font-size: var(--ma-font-size-display);
  line-height: var(--ma-typography-title);
  color: var(--ma-text-primary);
  letter-spacing: -.02em;
}

.stage__empty-desc {
  max-width: 560px;
  margin: 0 0 var(--ma-space-4);
  font-size: var(--ma-font-size-body);
  line-height: var(--ma-typography-body);
  color: var(--ma-text-body);
}


.stage__suggestions {
  display: flex;
  flex-wrap: wrap;
  gap: var(--ma-space-2);
}

.stage__suggestion {
  padding: var(--ma-space-2) var(--ma-space-3);
  border: 1px solid var(--ma-surface-border);
  border-radius: var(--ma-radius-card);
  background: var(--ma-surface-default);
  color: var(--ma-text-body);
  font-size: var(--ma-font-size-body);
  cursor: pointer;
  text-align: left;
}

.stage__suggestion:hover {
  border-color: var(--ma-action-primary);
  color: var(--ma-text-primary);
}

.stage__suggestion:focus-visible {
  outline: none;
  outline: 2px solid var(--ma-focus-ring);
  outline-offset: 1px;
}

.stage__empty-note {
  margin: 0;
  font-size: var(--ma-font-size-body);
  color: var(--ma-text-muted);
}

</style>
