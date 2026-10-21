<template>
  <!--
    P01 · 平台工作区布局（WorkspaceLayout）。
    契约：P00 WorkspaceLayoutContract（见 ./layoutContract.ts）。

    设计要点：
    - **域无关**：本组件不知道 Teacher/Research 有哪些产物类型；
      域通过具名 slot 注入内容，而不是改布局；
    - **conversation-first**：navigation-or-conversation 是一个**共享槽**
      （导航与对话同处，不是一个独立的导航栏），这是范式契约的物理体现；
    - 三大必备 region 恒定存在：navigation-or-conversation / artifact-canvas / evidence-rail。
  -->
  <div
    class="ma-workspace"
    :class="rootClass"
    :data-layout="layout.layout_id"
    data-testid="ma-workspace"
  >
    <!-- 工具条：横向，承载切换与全局动作 -->
    <header class="ma-workspace__toolbar" role="toolbar" aria-label="工作区工具条">
      <slot name="toolbar" />
    </header>

    <div class="ma-workspace__body">
      <!-- 导航与对话共享区（conversation-first 的核心） -->
      <section
        v-show="!collapsed['navigation-or-conversation']"
        class="ma-workspace__left"
        :class="{ 'is-rail': behaviours.railed['navigation-or-conversation'] }"
        :data-region="'navigation-or-conversation'"
        data-testid="ma-region-left"
      >
        <slot name="left" />
      </section>

      <!-- 产物画布 -->
      <section
        class="ma-workspace__center"
        :data-region="'artifact-canvas'"
        data-testid="ma-region-center"
      >
        <slot name="center" />
      </section>

      <!-- 证据栏 -->
      <aside
        v-show="!collapsed['evidence-rail']"
        class="ma-workspace__right"
        :class="{ 'is-rail': behaviours.railed['evidence-rail'] }"
        :data-region="'evidence-rail'"
        data-testid="ma-region-right"
        aria-label="证据"
      >
        <slot name="right" />
      </aside>
    </div>

    <!-- 状态区：恒定可见（契约要求 status region 同时在场） -->
    <footer class="ma-workspace__status" role="status" aria-live="polite" data-testid="ma-region-status">
      <slot name="status" />
    </footer>
  </div>
</template>

<script setup lang="ts">
/**
 * WorkspaceLayout —— 平台共享的工作区布局。
 *
 * 组件边界（AGENTS.md / ADR-PLAT-10）：
 * - **不得** import TeachingSession / ResearchSession 或任何域 store；
 * - **不得**枚举域产物类型（那属于域契约）；
 * - 只消费 `WorkspaceLayoutContract` 描述与 slot 内容。
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import {
  buildWorkspaceLayout,
  validateLayoutContract,
  type WorkspaceLayoutShape,
  type PlatformRegionRole,
} from './layoutContract'

interface Props {
  /** 自定义布局描述；缺省使用平台标准布局。 */
  layout?: WorkspaceLayoutShape
}

const props = withDefaults(defineProps<Props>(), { layout: undefined })

const layout = computed<WorkspaceLayoutShape>(() => props.layout ?? buildWorkspaceLayout())

// 开发期自校验：布局漂移立即暴露（生产构建亦保留，成本极低）
if (import.meta.env.DEV) {
  const problems = validateLayoutContract(layout.value)
  if (problems.length > 0) {
    // eslint-disable-next-line no-console
    console.error('[WorkspaceLayout] 布局契约违规：', problems)
  }
}

/** 当前视口宽度（用于响应式折叠，按契约的 breakpoint 判定）。 */
const viewportWidth = ref(typeof window !== 'undefined' ? window.innerWidth : 1440)

function onResize() {
  viewportWidth.value = window.innerWidth
}

onMounted(() => {
  if (typeof window !== 'undefined') window.addEventListener('resize', onResize)
})
onBeforeUnmount(() => {
  if (typeof window !== 'undefined') window.removeEventListener('resize', onResize)
})

/**
 * 依据响应式规则计算各 role 的折叠形态。
 *
 * 关键语义区分（此前把两者混为一谈，导致窄屏左区被整块隐藏）：
 * - `HIDE`             → 真的离开视野（`v-show=false`）；
 * - `COLLAPSE_TO_RAIL` → **收窄为导轨，仍然可见**（conversation-first 下
 *                        会话入口不能消失，否则窄屏用户无路可走）。
 *
 * `OVERLAY_DRAWER` / `STACK` / `KEEP` 不在此层隐藏（由消费端决定呈现方式）。
 */
const behaviours = computed(() => {
  const railed: Record<string, boolean> = {}
  const hidden: Record<string, boolean> = {}
  const rules = [...layout.value.responsive_rules].sort(
    (a, b) => a.breakpoint_px - b.breakpoint_px,
  )
  for (const rule of rules) {
    // 视口 <= 断点 时该规则生效（更窄的规则后应用，覆盖更宽的）
    if (viewportWidth.value > rule.breakpoint_px) continue
    for (const [role, behaviour] of Object.entries(rule.collapse)) {
      if (behaviour === 'HIDE') {
        hidden[role] = true
        railed[role] = false
      } else if (behaviour === 'COLLAPSE_TO_RAIL') {
        railed[role] = true
        hidden[role] = false
      }
    }
  }
  return { railed, hidden }
})

const collapsed = computed<Record<string, boolean>>(() => behaviours.value.hidden)

const rootClass = computed(() => ({
  'ma-workspace--compact': viewportWidth.value <= 1280,
  'ma-workspace--narrow': viewportWidth.value <= 900,
}))

defineExpose({ layout, collapsed, behaviours })
</script>

<style scoped>
/* 全部颜色经 var(--ma-*) 取用；本文件不得出现字面色值（反模式扫描守门）。 */
.ma-workspace {
  display: flex;
  flex-direction: column;
  min-height: 100vh;
  background: var(--ma-surface-page);
  color: var(--ma-text-primary);
}

.ma-workspace__toolbar {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  gap: var(--ma-space-2);
  padding: var(--ma-space-2) var(--ma-space-4);
  background: var(--ma-surface-default);
  border-bottom: 1px solid var(--ma-surface-border);
}

.ma-workspace__body {
  flex: 1 1 auto;
  display: flex;
  min-height: 0;
}

.ma-workspace__left {
  flex: 0 0 320px;
  min-width: 0;
  border-right: 1px solid var(--ma-surface-border);
  background: var(--ma-surface-default);
  overflow: auto;
}

.ma-workspace__center {
  flex: 1 1 auto;
  min-width: 0;
  overflow: auto;
}

.ma-workspace__right {
  flex: 0 0 320px;
  min-width: 0;
  border-left: 1px solid var(--ma-surface-border);
  background: var(--ma-surface-default);
  overflow: auto;
}

.ma-workspace__status {
  flex: 0 0 auto;
  padding: var(--ma-space-1) var(--ma-space-4);
  border-top: 1px solid var(--ma-surface-border);
  background: var(--ma-surface-subtle);
  color: var(--ma-text-body);
  font-size: var(--ma-font-size-body);
}

/* 紧凑：证据栏收窄 */
.ma-workspace--compact .ma-workspace__right {
  flex-basis: 260px;
}

/* 窄屏：按契约折叠（navigation-or-conversation 转窄轨） */
.ma-workspace--narrow .ma-workspace__left {
  flex-basis: 72px;
}

/* COLLAPSE_TO_RAIL：收窄为导轨但**保持可见**（会话入口在窄屏不得消失）。
   与 `HIDE` 的区别：HIDE 由 `v-show` 真正移出视野，本类只改变宽度。 */
.ma-workspace__left.is-rail,
.ma-workspace__right.is-rail {
  flex-basis: 72px;
  overflow-x: hidden;
}

/* 键盘可达性：聚焦环统一走主色 */
.ma-workspace :where(section, aside, header, footer):focus-visible {
  outline: 2px solid var(--ma-action-primary);
  outline-offset: -2px;
}
</style>
