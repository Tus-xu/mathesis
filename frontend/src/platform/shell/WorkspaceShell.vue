<template>
  <!--
    P01 · App Shell —— 统一账号区 + workspace 选择器 + capability 重校验。

    契约边界：
    - 只消费服务端 AccessContext，不自行签发/推导权限；
    - security_version 漂移即锁定（立即断开，不保留旧权限外壳）；
    - 选择器对"无 capability"的 workspace **显示但不可进入**（而非隐藏），
      让用户理解"我有这个身份但当前没有权限"，而不是困惑于"入口不见了"。
  -->
  <section class="ma-shell" data-testid="ma-shell">
    <header class="ma-shell__bar">
      <div class="ma-shell__account">
        <span class="ma-shell__avatar" aria-hidden="true">{{ initial }}</span>
        <div class="ma-shell__meta">
          <strong class="ma-shell__name" data-testid="ma-account-name">{{ displayName }}</strong>
          <span class="ma-shell__sub" data-testid="ma-account-role">{{ roleLabel }}</span>
        </div>
      </div>

      <nav class="ma-shell__chooser" aria-label="工作区选择" data-testid="ma-chooser">
        <button
          v-for="choice in choices"
          :key="choice.workspace"
          type="button"
          class="ma-shell__choice"
          :class="{
            'is-active': choice.workspace === activeWorkspace,
            'is-disabled': !choice.available,
          }"
          :disabled="!choice.available"
          :aria-current="choice.workspace === activeWorkspace ? 'page' : undefined"
          :data-workspace="choice.workspace"
          :data-available="choice.available"
          :data-testid="`ma-choice-${choice.workspace}`"
          @click="onChoose(choice)"
        >
          {{ workspaceLabel(choice.workspace) }}
        </button>
      </nav>

      <div class="ma-shell__actions">
        <slot name="actions" />
      </div>
    </header>

    <!-- 失效锁定提示：security_version 漂移 / 会话被替换时展示 -->
    <div
      v-if="locked"
      class="ma-shell__locked"
      role="alert"
      data-testid="ma-shell-locked"
    >
      <span>{{ lockedMessage }}</span>
      <button type="button" class="ma-shell__reenter" @click="emit('reenter')">
        重新进入
      </button>
    </div>

    <slot />
  </section>
</template>

<script setup lang="ts">
/**
 * WorkspaceShell —— App Shell 层。
 *
 * 与 WorkspaceLayout 的分工：
 * - WorkspaceShell：账号区 + workspace 选择 + 失效锁定（**平台外壳**）；
 * - WorkspaceLayout：三个核心 region 的 slot 容器（**内容布局**）。
 * 二者可组合：Shell 包 Layout，Layout 内放域内容。
 */
import { computed } from 'vue'
import type { AccessContext } from '@/features/platform-contracts/types/platform-contracts'
import { deriveWorkspaceChoices, type WorkspaceKey } from './workspaceManifest'
import { detectSecurityVersionDrift, type SecuritySnapshot } from './securityVersion'

interface Props {
  /** 服务端下发的全部 AccessContext。 */
  accessContexts?: readonly AccessContext[]
  /** 当前激活 workspace。 */
  activeWorkspace?: WorkspaceKey | null
  /** 账号显示名。 */
  displayName?: string
  /** 当前角色标签。 */
  roleLabel?: string
  /** 失效前持有的安全快照（用于漂移检测）。 */
  previousSnapshot?: SecuritySnapshot | null
}

const props = withDefaults(defineProps<Props>(), {
  accessContexts: () => [],
  activeWorkspace: null,
  displayName: '用户',
  roleLabel: '—',
  previousSnapshot: null,
})

const emit = defineEmits<{
  (e: 'choose', workspace: WorkspaceKey): void
  (e: 'reenter'): void
}>()

const choices = computed(() => deriveWorkspaceChoices(props.accessContexts))

/** 由当前 AccessContext 派生快照，与服务端权威比对。 */
const currentSnapshot = computed<SecuritySnapshot | null>(() => {
  const first = props.accessContexts?.[0]
  if (!first) return null
  return { actorId: first.actor_id, securityVersion: first.security_version }
})

/**
 * 是否已锁定。
 *
 * 触发条件：持有历史快照，且与服务端最新快照不一致（版本推进或换账号）。
 * 未持有历史快照时不锁定（首次进入，尚未建立可比基线）。
 */
const locked = computed(() => {
  if (!props.previousSnapshot || !currentSnapshot.value) return false
  return detectSecurityVersionDrift(props.previousSnapshot, currentSnapshot.value) !== 'OK'
})

const lockedMessage = computed(() =>
  '你的权限已变更，当前会话已锁定。请重新进入以获取最新权限。',
)

const initial = computed(() => (props.displayName || '用').slice(0, 1))

const WORKSPACE_LABELS: Record<string, string> = {
  student: '学生工作台',
  teacher: '教师工作台',
  research: '科研工作台',
  admin: '管理后台',
}

function workspaceLabel(w: WorkspaceKey): string {
  return WORKSPACE_LABELS[w] ?? w
}

function onChoose(choice: { workspace: WorkspaceKey; available: boolean }) {
  if (!choice.available) return
  emit('choose', choice.workspace)
}
</script>

<style scoped>
.ma-shell {
  display: flex;
  flex-direction: column;
  min-height: 100vh;
  background: var(--ma-surface-page);
}

.ma-shell__bar {
  display: flex;
  align-items: center;
  gap: var(--ma-space-4);
  padding: var(--ma-space-2) var(--ma-space-4);
  background: var(--ma-surface-default);
  border-bottom: 1px solid var(--ma-surface-border);
}

.ma-shell__account {
  display: flex;
  align-items: center;
  gap: var(--ma-space-2);
  flex: 0 0 auto;
}

.ma-shell__avatar {
  width: 32px;
  height: 32px;
  border-radius: var(--ma-radius-full);
  background: var(--ma-action-primary-soft);
  color: var(--ma-action-primary);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-weight: 600;
}

.ma-shell__meta {
  display: flex;
  flex-direction: column;
  line-height: 1.25;
}

.ma-shell__name {
  font-size: var(--ma-font-size-body);
  color: var(--ma-text-primary);
}

.ma-shell__sub {
  font-size: var(--ma-font-size-body);
  color: var(--ma-text-muted);
}

.ma-shell__chooser {
  display: flex;
  gap: var(--ma-space-1);
  flex: 1 1 auto;
  min-width: 0;
  overflow-x: auto;
}

.ma-shell__choice {
  flex: 0 0 auto;
  padding: var(--ma-space-1) var(--ma-space-3);
  border-radius: var(--ma-radius-md);
  border: 1px solid var(--ma-surface-border);
  background: var(--ma-surface-default);
  color: var(--ma-text-body);
  font-size: var(--ma-font-size-body);
  cursor: pointer;
}

.ma-shell__choice:hover:not(:disabled) {
  border-color: var(--ma-action-primary);
  color: var(--ma-action-primary);
}

.ma-shell__choice.is-active {
  background: var(--ma-action-primary);
  border-color: var(--ma-action-primary);
  color: var(--ma-surface-default);
}

.ma-shell__choice.is-disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.ma-shell__choice:focus-visible {
  outline: 2px solid var(--ma-action-primary);
  outline-offset: 2px;
}

.ma-shell__actions {
  flex: 0 0 auto;
}

.ma-shell__locked {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--ma-space-3);
  padding: var(--ma-space-2) var(--ma-space-4);
  background: var(--ma-action-primary-soft);
  color: var(--ma-text-primary);
  border-bottom: 1px solid var(--ma-surface-border);
}

.ma-shell__reenter {
  padding: var(--ma-space-1) var(--ma-space-3);
  border-radius: var(--ma-radius-md);
  border: 1px solid var(--ma-action-primary);
  background: var(--ma-action-primary);
  color: var(--ma-surface-default);
  cursor: pointer;
}
</style>
