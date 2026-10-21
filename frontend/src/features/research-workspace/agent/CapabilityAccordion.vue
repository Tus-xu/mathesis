<template>
  <div class="rw-cap" :class="{ 'rw-cap--open': open, 'rw-cap--running': running }">
    <button
      type="button"
      class="rw-cap__head"
      :aria-expanded="open"
      @click="emit('toggle', meta.preset)"
    >
      <!-- 展开且已产出「有状态」的结果：头部合并为状态行（20px 实心圆状态图标 + 能力名 + 状态药丸），
           与黄金图一致（不再重复渲染能力名）。 -->
      <template v-if="open && status">
        <span class="rw-cap__badge" :class="`rw-cap__badge--${status.tone}`" aria-hidden="true">
          <component :is="badgeIcon" />
        </span>
        <span class="rw-cap__title">{{ meta.label }}</span>
        <StatusPill class="rw-cap__pill" :tone="status.tone" :text="status.label" :icon="status.icon" />
      </template>

      <!-- 收起态 / 无状态结果（如 LaTeX 错误解释）：能力图标 + 标题 + chevron（展开朝上、收起朝下）。 -->
      <template v-else>
        <span class="rw-cap__icon" :class="`rw-cap__icon--${meta.tone}`" aria-hidden="true">
          <component :is="meta.icon" />
        </span>
        <span class="rw-cap__title">{{ meta.label }}</span>
        <span v-if="hint" class="rw-cap__hint">{{ hint }}</span>
        <span v-if="stateText" class="rw-cap__state">{{ stateText }}</span>
        <component
          :is="open ? ChevronUpOutline : ChevronDownOutline"
          class="rw-cap__chev"
          aria-hidden="true"
        />
      </template>
    </button>

    <div v-if="open" class="rw-cap__body">
      <!-- 执行中：只显示有意义的 activity 状态（宪法 §30/§55），禁止参数表单 -->
      <div v-if="running" class="rw-cap__activity" role="status" aria-live="polite">
        <span class="rw-cap__spinner" aria-hidden="true" />
        <span>{{ activity || meta.activity }}</span>
      </div>

      <!-- 失败：中文人话降级 + 重新尝试（宪法 §56） -->
      <div v-if="error" class="rw-cap__error" role="alert">
        <p class="rw-cap__error-text">{{ error.message }}</p>
        <button type="button" class="rw-cap__retry" @click="emit('retry', meta.preset)">重新尝试</button>
      </div>

      <!-- 结果卡（统一 Result DS）。始终渲染 slot：无结果时由 ResultCard 渲染诚实空态，
           保证「展开态结果卡」在视觉与结构上恒存在（黄金图 §4.4 / 宪法 §58/§59）。 -->
      <slot />
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * 能力 Accordion 卡（VISUAL-SPEC §4.4-3 / §4.4-4 / 宪法 §28/§29/§30/§54）。
 *
 * - 收起态 h46：左侧 28×28 圆角能力图标 + 标题 + 右侧 chevron（展开朝上、收起朝下）；
 * - 展开态：当前执行/刚完成的能力展开，**其他全部收起**（§29）；
 * - 点击能力**直接执行、绝不弹参数表单**（§30），立即进入 activity 状态；
 * - 任务状态可见（`queued/running/completed/failed/cancelled`），关闭 Accordion 后
 *   任务不消失（状态由面板/状态机持有，不由本组件持有，§54）。
 *
 * 头部按黄金图合并：当展开且结果带「状态」时，头部即**状态行**
 * （20px 实心圆状态图标 + 能力名 + 状态药丸）；`latex_error` 等无状态结果保留能力图标 + chevron。
 */
import { computed, type Component } from 'vue'
import {
  AlertCircle,
  CheckmarkCircle,
  ChevronDownOutline,
  ChevronUpOutline,
  CloseCircle,
  InformationCircleOutline,
} from '@vicons/ionicons5'
import type { RvError } from '../api/client'
import StatusPill, { type StatusTone } from '../ui/StatusPill.vue'
import type { PresetView } from './useAgentTask'

type TaskState = 'idle' | 'queued' | 'running' | 'completed' | 'failed' | 'cancelled'

/** 展开态头部要显示的状态（来自结果卡 `statusView`）。 */
export interface CapStatus {
  label: string
  tone: StatusTone
  icon: Component
}

const props = withDefaults(
  defineProps<{
    meta: PresetView
    open: boolean
    running?: boolean
    activity?: string
    error?: RvError | null
    state?: TaskState
    /** 克制的提醒（如「检测到 1 个编译错误」，宪法 §78/§79） */
    hint?: string
    /** 结果状态（展开且存在时，头部合并为状态行；`null` 表示无状态结果） */
    status?: CapStatus | null
  }>(),
  { running: false, activity: '', error: null, state: 'idle', hint: '', status: null },
)

const emit = defineEmits<{
  (e: 'toggle', preset: string): void
  (e: 'retry', preset: string): void
}>()

const stateText = computed(() => {
  switch (props.state) {
    case 'queued':
      return '排队中'
    case 'running':
      return '执行中'
    case 'failed':
      return '失败'
    case 'cancelled':
      return '已取消'
    default:
      return ''
  }
})

/**
 * 展开态状态行左侧 20px 实心圆里的图标（黄金图：`推导校验` 是**绿色圆 + 白色 ✓**）。
 * 注意与右侧状态药丸的图标**不同**：药丸用 `status.icon`（`基本成立` 是三角警示 ⚠），
 * 状态圆用色调对应的实心圆图标（success→✓ / warning→! / danger→×）。
 */
const badgeIcon = computed<Component>(() => {
  switch (props.status?.tone) {
    case 'success':
      return CheckmarkCircle
    case 'warning':
      return AlertCircle
    case 'danger':
      return CloseCircle
    default:
      return InformationCircleOutline
  }
})
</script>

<style scoped>
.rw-cap {
  border: var(--rw-border-w) solid var(--rw-border);
  border-radius: var(--rw-radius-lg);
  background: var(--rw-surface);
  overflow: hidden;
}
/* 展开卡边框：黄金图实测为「主蓝 30% + 白」的浅蓝（WRITE-GOLDEN 边框像素 ≈ #BCD5FE），
   不是主蓝实心 —— 用 color-mix 从既有 token 派生，不写字面色值（VISUAL-SPEC §7.9）。 */
.rw-cap--open {
  border-color: color-mix(in srgb, var(--rw-primary) 30%, var(--rw-surface));
}
.rw-cap__head {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  min-height: 46px;
  padding: 8px 12px;
  border: none;
  background: none;
  text-align: left;
  cursor: pointer;
}
/* 收起态能力图标：28×28 圆角容器 */
.rw-cap__icon {
  flex: 0 0 auto;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: var(--rw-radius-md);
  font-size: 17px;
}
/* 左侧能力图标本体 ≈17px（28×28 圆角容器内）；@vicons <svg> 无固有尺寸，必须显式约束 */
.rw-cap__icon :deep(svg) {
  width: 17px;
  height: 17px;
}
.rw-cap__icon--primary { background: var(--rw-primary-soft); color: var(--rw-primary); }
.rw-cap__icon--success { background: var(--rw-success-soft); color: var(--rw-success); }
.rw-cap__icon--warning { background: var(--rw-warning-soft); color: var(--rw-warning); }
.rw-cap__icon--danger { background: var(--rw-danger-soft); color: var(--rw-danger); }
/* purple（翻译 / 润色）：soft/ink 由既有 token 用 color-mix 派生，
   不在组件里写字面色值（VISUAL-SPEC §7.9）。 */
.rw-cap__icon--purple {
  background: color-mix(in srgb, var(--rw-code-math) 12%, var(--rw-surface));
  color: var(--rw-code-math);
}
/* 展开态状态图标：20px 实心圆（§4.4-4 状态行） */
.rw-cap__badge {
  flex: 0 0 auto;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  border-radius: 50%;
  color: var(--rw-surface);
}
.rw-cap__badge :deep(svg) {
  width: 14px;
  height: 14px;
}
.rw-cap__badge--success { background: var(--rw-success); }
.rw-cap__badge--warning { background: var(--rw-warning); }
.rw-cap__badge--danger { background: var(--rw-danger); }
.rw-cap__badge--info { background: var(--rw-primary); }
.rw-cap__pill { flex: 0 0 auto; margin-left: auto; }
.rw-cap__title {
  flex: 1 1 auto;
  min-width: 0;
  font-size: var(--rw-fs-h2);
  font-weight: var(--rw-fw-semibold);
  color: var(--rw-text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.rw-cap__state {
  flex: 0 0 auto;
  font-size: var(--rw-fs-xs);
  color: var(--rw-text-3);
}
.rw-cap__hint {
  flex: 0 0 auto;
  font-size: var(--rw-fs-xs);
  color: var(--rw-warning);
}
.rw-cap__chev {
  flex: 0 0 auto;
  width: 16px;   /* §4.4-3 右侧 chevron 16px（原仅 font-size，实测退化为 549×549） */
  height: 16px;
  font-size: 16px;
  color: var(--rw-text-3);
}
.rw-cap__body {
  padding: 0 12px 12px;
}
.rw-cap__activity {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 0;
  font-size: var(--rw-fs-body);
  color: var(--rw-text-2);
}
.rw-cap__spinner {
  width: 14px;
  height: 14px;
  flex: 0 0 auto;
  border: 2px solid var(--rw-primary-soft);
  border-top-color: var(--rw-primary);
  border-radius: 50%;
  animation: rw-cap-spin 0.8s linear infinite;
}
@keyframes rw-cap-spin {
  to { transform: rotate(360deg); }
}
@media (prefers-reduced-motion: reduce) {
  .rw-cap__spinner { animation: none; }
}
.rw-cap__error {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 6px 0;
}
.rw-cap__error-text {
  margin: 0;
  font-size: var(--rw-fs-body);
  color: var(--rw-text-2);
}
.rw-cap__retry {
  flex: 0 0 auto;
  height: 28px;
  padding: 0 12px;
  border: var(--rw-border-w) solid var(--rw-border-strong);
  border-radius: var(--rw-radius);
  background: var(--rw-surface);
  color: var(--rw-primary);
  font-size: var(--rw-fs-sm);
  font-weight: var(--rw-fw-medium);
  cursor: pointer;
}

/* ── WRITE 314px 紧凑态（VISUAL-SPEC §1.5：314px 下内容自适应、不隐藏）── */
@container rw-agent (max-width: 400px) {
  .rw-cap__head {
    gap: 8px;
    padding: 8px 10px;
  }
  .rw-cap__title {
    font-size: var(--rw-fs-h3);
  }
  .rw-cap__body {
    padding: 0 10px 10px;
  }
}
</style>
