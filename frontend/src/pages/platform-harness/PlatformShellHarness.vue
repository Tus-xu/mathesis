<template>
  <!--
    P01 · App Shell 集成沙盒（仅 E2E harness 使用）。

    ⚠️ 非生产页面 / NON-PRODUCTION HARNESS
    本页 **不是** 任何 workspace 的真实首页，仅供 P01 验收用 Playwright 驱动真实的
    WorkspaceShell + WorkspaceLayout 组合，验证：
      - capability 显示与可用性
      - 旧直达 URL 兼容
      - security_version 漂移锁定
      - 跨 workspace 守卫
      - 刷新恢复

    它 **不** 引入任何 Teacher/Research 域状态、不伪造学生学习数据、不伪造证据/进度。
    全部 props 来自 URL query（测试显式注入），页面自身不含任何业务默认值。
  -->
  <WorkspaceShell
    :access-contexts="contexts"
    :active-workspace="activeWorkspace"
    :display-name="displayName"
    :role-label="roleLabel"
    :previous-snapshot="previousSnapshot"
    @choose="onChoose"
    @reenter="onReenter"
  >
    <template #actions>
      <button type="button" data-testid="ma-refresh" class="ma-harness__btn" @click="refresh">
        刷新权限
      </button>
    </template>

    <WorkspaceLayout :layout="layout">
      <template #left>
        <div data-testid="ma-harness-left">会话区（harness 占位，无业务数据）</div>
      </template>
      <template #center>
        <div data-testid="ma-harness-center">产物区（harness 占位，无业务数据）</div>
      </template>
      <template #right>
        <div data-testid="ma-harness-right">证据区（harness 占位，无业务数据）</div>
      </template>
      <template #status>
        <span data-testid="ma-harness-status">就绪</span>
      </template>
    </WorkspaceLayout>

    <!-- 守卫结论可视化：让 E2E 能断言"守卫拒绝了什么"，而不必真跳转 -->
    <p data-testid="ma-harness-guard" hidden>{{ guardReason }}</p>
  </WorkspaceShell>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import type { AccessContext } from '@/features/platform-contracts/types/platform-contracts'
import WorkspaceShell from '@/platform/shell/WorkspaceShell.vue'
import WorkspaceLayout from '@/platform/workspace-layout/WorkspaceLayout.vue'
import { buildWorkspaceLayout } from '@/platform/workspace-layout/layoutContract'
import {
  resolvePlatformNavigation,
  resolveTargetWorkspace,
} from '@/platform/shell/navigation'
import type { WorkspaceKey } from '@/platform/shell/workspaceManifest'
import type { SecuritySnapshot } from '@/platform/shell/securityVersion'

const route = useRoute()
const router = useRouter()

/**
 * 由 URL query 解析 AccessContext 注入内容。
 *
 * 设计原则：**默认什么都不给**（无 capability、无 context），
 * 全部场景必须由测试显式声明，避免"harness 自带了数据把 bug 掩盖掉"。
 *
 *   ?caps=teacher:teacher.prep.v2,research:research.vnext
 *   ?active=teacher
 *   &prev=（形如 1:7 表示 actorId=1, securityVersion=7）
 *   &name=李老师 &role=教师
 */
function parseCaps(raw: unknown): AccessContext[] {
  if (typeof raw !== 'string' || raw.length === 0) return []
  return raw
    .split(',')
    .map((chunk) => chunk.trim())
    .filter(Boolean)
    .map((chunk, i) => {
      const [workspace, capsRaw] = chunk.split(':')
      const capabilities = (capsRaw || '').split('|').map((c) => c.trim()).filter(Boolean)
      return {
        workspace,
        actor_id: `harness-actor-${i}`,
        domain: workspace,
        capabilities,
        security_version: Number(route.query.sv ?? 1),
        trace_id: `harness-trace-${i}`,
      } as unknown as AccessContext
    })
}

function parseSnapshot(raw: unknown): SecuritySnapshot | null {
  if (typeof raw !== 'string' || !raw.includes(':')) return null
  const [actorId, sv] = raw.split(':')
  return { actorId, securityVersion: Number(sv) }
}

const contexts = computed(() => parseCaps(route.query.caps))
const activeWorkspace = computed<WorkspaceKey | null>(() => {
  const a = route.query.active
  return typeof a === 'string' && a ? (a as WorkspaceKey) : null
})
const displayName = computed(() => (typeof route.query.name === 'string' ? route.query.name : '测试用户'))
const roleLabel = computed(() => (typeof route.query.role === 'string' ? route.query.role : '—'))
const previousSnapshot = computed(() => parseSnapshot(route.query.prev))
const layout = buildWorkspaceLayout()

/** 实时守卫结论：用真实守卫函数算，保证 E2E 断的是真逻辑而非 harness 自造。 */
const guardReason = ref('')
const target = computed<WorkspaceKey | null>(() => resolveTargetWorkspace(route.meta as Record<string, unknown>))
const guard = computed(() =>
  resolvePlatformNavigation({
    to: route.path,
    toMeta: route.meta as Record<string, unknown>,
    status: contexts.value.length > 0 ? 'authenticated' : 'anonymous',
    activeWorkspace: activeWorkspace.value,
    targetWorkspace: target.value,
    accessContexts: contexts.value,
    isPublic: false,
  }),
)
guardReason.value = guard.value.reason ?? 'ALLOW'

function onChoose(workspace: WorkspaceKey) {
  // harness 内切换 active，模拟 chooser 行为（真实实现由 App Shell 决定落地路由）
  router.replace({ query: { ...route.query, active: workspace } })
}

function onReenter() {
  router.replace({ query: { ...route.query, prev: '' } })
}

function refresh() {
  router.replace({ query: { ...route.query, _t: String(Date.now()) } })
}
</script>

<style scoped>
.ma-harness__btn {
  padding: var(--ma-space-1) var(--ma-space-3);
  border-radius: var(--ma-radius-md);
  border: 1px solid var(--ma-surface-border);
  background: var(--ma-surface-default);
  color: var(--ma-text-body);
  cursor: pointer;
}
</style>
