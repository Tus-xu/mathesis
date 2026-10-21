<template>
  <!--
    T22 · Conversation-first Teacher Workspace。

    布局：**消费 P01 `platform/workspace-layout` 的通用 regions/slots**，
    不在 teacher-prep-v2 内重写第二个三栏布局系统（任务书第 1a 条）。
    Teacher 只提供 slot 内容与领域 artifact tab。
  -->
  <WorkspaceLayout :layout="layout">
    <!-- toolbar：标题 + 连接基调 + 证据栏收起（client state） -->
    <template #toolbar>
      <h1 class="prep-ws__title">{{ copyFor('prep.toolbar.title') }}</h1>
      <StatusPill :tone="tone" :label="statusLabel" test-id="prep-connection-tone" />
      <span class="prep-ws__spacer" />
      <ActionButton
        variant="ghost"
        :test-id="ui.evidenceRailCollapsed ? 'prep-evidence-expand' : 'prep-evidence-collapse'"
        :aria-expanded="!ui.evidenceRailCollapsed"
        @click="toggleRail"
      >
        {{ ui.evidenceRailCollapsed ? copyFor('prep.toolbar.rail.expand') : copyFor('prep.toolbar.rail.toggle') }}
      </ActionButton>
    </template>

    <!-- left（navigation-or-conversation）：conversation timeline + composer -->
    <template #left>
      <div class="prep-ws__conversation">
        <ConnectionBanner
          :phase="phase"
          :stream-state="streamState"
          :error="error"
          :gap-count="gapCount"
          @reload="reload"
        />
        <div class="prep-ws__timeline">
          <ConversationTimeline
            :messages="timeline"
            @retry="onRetry"
            @apply-edits="onApplyEdits"
          />
        </div>
        <Composer
          :model-value="ui.composerDraft"
          :disabled="phase === 'forbidden'"
          :busy="busy"
          @update:model-value="setDraft"
          @send="onSend"
        />
      </div>
    </template>

    <!-- center（artifact-canvas）：typed proposal card + living artifact tabs -->
    <template #center>
      <div class="prep-ws__center">
        <ErrorState :error="error" @retry="reload" @signin="onSignin" />

        <ProposalCard
          v-if="proposal"
          :proposal-kind="proposal.kind"
          :title="proposal.title"
          :status="proposal.status"
          :actions="proposalActions"
          :busy="busy"
          @run="runAction"
        >
          <StructuredCard :component-type="proposal.componentType" :props="proposal.value" />
        </ProposalCard>

        <ArtifactCanvas
          :artifact="artifact"
          :active="ui.activeArtifactTab"
          @update:active="setActiveTab"
        />
      </div>
    </template>

    <!-- right（evidence-rail）：可收起证据栏 -->
    <template #right>
      <EvidenceRail
        :evidence="evidence"
        :collapsed="ui.evidenceRailCollapsed"
        @toggle="toggleRail"
      />
    </template>

    <!-- status：长任务状态（恒定可见） -->
    <template #status>
      <JobStatus :jobs="jobs" :running="hasRunningJob" />
    </template>
  </WorkspaceLayout>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, watch } from 'vue'
import { useRoute } from 'vue-router'
import { storeToRefs } from 'pinia'

import { WorkspaceLayout, buildWorkspaceLayout } from '@/platform/workspace-layout'
import ArtifactCanvas from './ArtifactCanvas.vue'
import Composer from './Composer.vue'
import ConnectionBanner from './ConnectionBanner.vue'
import ConversationTimeline from './ConversationTimeline.vue'
import ErrorState from './ErrorState.vue'
import EvidenceRail from './EvidenceRail.vue'
import JobStatus from './JobStatus.vue'
import ActionButton from './primitives/ActionButton.vue'
import StatusPill from './primitives/StatusPill.vue'
import ProposalCard from './cards/ProposalCard.vue'
import StructuredCard from './cards/StructuredCard.vue'
import { copyFor } from '@/features/teacher-prep-v2/i18n/copy'
import { usePrepWorkspaceStore } from '@/features/teacher-prep-v2/stores/prepWorkspace'
import type { ActionView, ComponentType } from '@/features/teacher-prep-v2/types/workspace'

const props = withDefaults(defineProps<{ sessionId?: string | null }>(), { sessionId: null })

const layout = buildWorkspaceLayout()
const route = useRoute()
const store = usePrepWorkspaceStore()
// 状态经 `storeToRefs` 保持响应式（直接解构 store 会丢失 ref 语义）
const {
  ui, phase, error, streamState, gapCount,
  timeline, artifact, evidence, jobs, availableActions, hasRunningJob, connectionTone,
} = storeToRefs(store)

const busy = computed(() => hasRunningJob.value)

// UI 偏好写入（client state）：只经 store，不直接碰 localStorage
function toggleRail() {
  store.setUiPref('evidenceRailCollapsed', !ui.value.evidenceRailCollapsed)
}
function setActiveTab(key: string) {
  store.setUiPref('activeArtifactTab', key)
}
function setDraft(value: string) {
  store.setUiPref('composerDraft', value)
}

const tone = computed(() => {
  const t = connectionTone.value
  if (t === 'forbidden' || t === 'conflict' || t === 'error') return 'danger' as const
  if (t === 'reconnecting' || t === 'partial') return 'warning' as const
  if (t === 'running') return 'running' as const
  if (t === 'success') return 'success' as const
  return 'neutral' as const
})

const statusLabel = computed(() => {
  if (error.value) return copyFor(error.value.copyKey)
  if (streamState.value === 'reconnecting') return copyFor('prep.state.reconnecting')
  if (gapCount.value > 0) return copyFor('prep.state.stale')
  if (phase.value === 'loading') return copyFor('prep.state.loading')
  // 工具栏只反映"连接/就绪态"——空态文案由 timeline 自身承担，不重复
  if (phase.value === 'empty') return copyFor('prep.state.ready')
  return copyFor('prep.state.ready')
})

/** 当前待确认提案（server state 派生；不猜，只看服务端给了什么）。 */
const proposal = computed(() => {
  const a = artifact.value
  const bp = a?.blueprint as Record<string, unknown> | null
  const dg = a?.diagnostic as Record<string, unknown> | null
  if (bp && typeof bp === 'object') {
    return {
      kind: 'blueprint' as const,
      title: String(bp.title ?? copyFor('prep.region.artifact')),
      status: bp.status == null ? null : String(bp.status),
      componentType: 'blueprint_card' as ComponentType,
      value: bp,
    }
  }
  if (dg && typeof dg === 'object') {
    return {
      kind: 'diagnostic' as const,
      title: String(dg.title ?? copyFor('prep.diagnostic.propose')),
      status: dg.status == null ? null : String(dg.status),
      componentType: 'diagnostic_card' as ComponentType,
      value: dg,
    }
  }
  return null
})

/** 只展示与当前提案相关的动作；其余动作仍会在 timeline/composer 上下文出现。 */
const proposalActions = computed<ActionView[]>(() => availableActions.value)

function onSend(text: string) {
  void store.send(text)
}
function onRetry(messageId: string) {
  void store.runAction('retry_run', messageId)
}
function onApplyEdits(_messageId: string, text: string) {
  void store.runAction('apply_edits', null)
  // 编辑内容作为一条新消息提交（服务端权威，不留客户端合并逻辑）
  if (text.trim()) void store.send(text)
}
function runAction(action: string, target: string | null) {
  void store.runAction(action, target)
}
function reload() {
  void store.load()
}
function onSignin() {
  // 交由既有登录流程处理；此处不自行实现第二套鉴权
  window.location.href = '/login'
}

const resolvedSessionId = computed(
  () => props.sessionId ?? (route?.params?.sessionId as string | undefined) ?? null,
)

onMounted(() => {
  store.attach(resolvedSessionId.value ?? null)
})
watch(resolvedSessionId, (id) => store.attach(id ?? null))
onBeforeUnmount(() => store.detach())
</script>

<style scoped>
.prep-ws__title {
  margin: 0;
  font-size: var(--ma-font-size-title);
  color: var(--ma-text-primary);
}

.prep-ws__spacer {
  flex: 1 1 auto;
}

.prep-ws__conversation {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}

.prep-ws__timeline {
  flex: 1 1 auto;
  overflow: auto;
  padding: var(--ma-space-2);
}

.prep-ws__center {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}
</style>
