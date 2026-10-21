<template>
  <div
    class="rw-seg"
    :class="[
      { 'rw-seg--sm': modelValue === 'write' },
      { 'rw-seg--header': variant === 'header' },
    ]"
    role="tablist"
    aria-label="工作区切换"
  >
    <button
      v-for="opt in options"
      :key="opt.value"
      type="button"
      role="tab"
      class="rw-seg__item"
      :class="{ 'rw-seg__item--active': opt.value === modelValue }"
      :aria-selected="opt.value === modelValue"
      @click="emit('update:modelValue', opt.value)"
    >
      {{ opt.label }}
    </button>
  </div>
</template>

<script setup lang="ts">
/**
 * `阅读 | 写作` 分段控件（两页共用同一组件）。
 *
 * **尺寸由主理人脚本复测黄金图得出**（VISUAL-SPEC §1.3 记的「容器 h30 / 选中项 h26 / padding 18px」
 * 是目测错值，实测两页尺寸不同）：
 *
 * | 页 | 选中项 | 文字宽 | 字号 |
 * |---|---|---|---|
 * | READ  | **110×35**（x 344–453, y 79–113） | 30px | 15px |
 * | WRITE | **98×32**（x 413–510, y 70–101） | 26px | 13px |
 *
 * r6 修正：READ 选中项由 106×34 → **110×35**（黄金图复测 110×35；细节见 `.rw-seg__item`）：
 * 宽度差全在 padding（38→40），高度 34→35。WRITE 侧 98×32 **已命中，未改动**。
 * 复算：`tmp/r6_measure.py` §D。
 * 切换只改工作区，不清空 Project 与 Agent 上下文（FRONTEND-ARCH §5）。
 *
 * ── 2026-09-18 用户指令驱动的**有意偏离黄金图**（勿当回归）────────────────────
 * 用户原话：「把阅读和写作的切换按钮需要移动到最上面那一栏（跟大标题平齐那样，
 * 不然会极大的压缩研究者的工作空间）」。黄金图把本控件放在**栏内**（READ 中栏首行、
 * WRITE 工作区条），改后放进 **62px 顶栏**、与大标题同一基线，故必须按顶栏收紧高度：
 * `variant="header"` 把容器压到 34px、选中项 28px（≤34px 顶栏约束），
 * 但**保持宽度口径不变**（READ 110 / WRITE 98），以免破坏上述黄金图宽度复测结论。
 *
 * 两种挂载形态：
 *  - `variant="rail"`（默认）：原栏内形态，尺寸见上表（保留以备回退）；
 *  - `variant="header"`：顶栏形态，由 `shell/GlobalHeader.vue` 使用。
 */
import type { WorkspaceMode } from '../api/types'

defineProps<{ modelValue: WorkspaceMode; variant?: 'rail' | 'header' }>()
const emit = defineEmits<{ (e: 'update:modelValue', v: WorkspaceMode): void }>()

const options: { value: WorkspaceMode; label: string }[] = [
  { value: 'read', label: '阅读' },
  { value: 'write', label: '写作' },
]
</script>

<style scoped>
/* 2026-10-21 三端视觉统一：分段控件改为学生端同款“浅底胶囊 + 白色选中态”，
   不再使用整块实心主蓝。尺寸保持可点面积（顶栏 34px）。 */
.rw-seg {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  height: 34px;
  padding: 3px;
  border: 1px solid var(--rw-border);
  border-radius: 999px;
  background: var(--rw-surface-2);
}
.rw-seg__item {
  height: 28px;
  padding: 0 18px;
  border: none;
  border-radius: 999px;
  background: transparent;
  color: var(--rw-text-2);
  font-family: var(--rw-font-sans);
  font-size: var(--rw-fs-body);
  font-weight: 600;
  line-height: 1;
  cursor: pointer;
  transition: background-color .14s ease, color .14s ease, box-shadow .14s ease;
}
.rw-seg__item:hover {
  color: var(--rw-primary);
}
.rw-seg__item--active,
.rw-seg__item--active:hover {
  background: var(--rw-surface);
  color: var(--rw-primary);
  font-weight: 800;
  box-shadow: 0 1px 3px rgba(15, 23, 42, .08);
}

.rw-seg--sm {
  height: 32px;
  padding: 3px;
}
.rw-seg--sm .rw-seg__item {
  height: 26px;
  padding: 0 15px;
  font-size: var(--rw-fs-sm);
}

/* 顶栏形态：与普通胶囊一致；保留类名以兼容既有调用。 */
.rw-seg--header {
  height: 34px;
  padding: 3px;
}
.rw-seg--header .rw-seg__item {
  height: 28px;
}
.rw-seg--header.rw-seg--sm {
  height: 32px;
  padding: 3px;
}
</style>
