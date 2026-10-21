<script setup lang="ts">
/**
 * 板书设计·数学图形（参数化 SVG，读 `board_design.math_object`）。
 *
 * 反红线：图形由独立参数 (a,b) 实时生成，半焦距 c=√(a²−b²) 恒派生（不落库、不贴死图）；
 * 焦点（`show_foci`）/ 坐标轴（`show_axes`）/ 辅助线（`show_aux_lines`）按 toggles 显示。
 * 数学一致性：a>b>0 恒成立（构造侧强校验），图形/焦点/数值标注同源。
 */
import { computed } from 'vue'
import type { PlanMathObject } from '@/features/teacher-prep-center/api/lessonPlanApi'

const props = defineProps<{ object: PlanMathObject | null; showLabels?: boolean }>()

const VB_W = 340
const VB_H = 190
const CX = 176
const CY = 98

/** 参数（仅取真实 params；缺省用课堂基线 a=5,b=3，与服务端投影默认一致）。 */
const a = computed(() => {
  const v = Number(props.object?.params?.a)
  return Number.isFinite(v) && v > 0 ? v : 5
})
const b = computed(() => {
  const v = Number(props.object?.params?.b)
  return Number.isFinite(v) && v > 0 ? v : 3
})
/** 半焦距：恒派生（a>b>0）。 */
const c = computed(() => Math.sqrt(Math.max(a.value * a.value - b.value * b.value, 0)))

const scale = computed(() => 118 / (a.value * 1.28))
const rx = computed(() => a.value * scale.value)
const ry = computed(() => b.value * scale.value)
const fx = computed(() => c.value * scale.value)

const toggles = computed(() => ({
  foci: props.object?.toggles?.show_foci !== false,
  axes: props.object?.toggles?.show_axes !== false,
  aux: props.object?.toggles?.show_aux_lines !== false,
  // 板书场景强制显示 F₁/F₂/P 数值标注（黄金图板书图形含标注）；其余场景尊重 toggles。
  values: props.showLabels === true || props.object?.toggles?.show_value_labels !== false,
}))

const color = computed(() => props.object?.style?.color || '#2563eb')

/** 有效果：kind 非 ellipse（或参数非法）时不出图，由父组件给诚实空态。 */
const isEllipse = computed(() => props.object?.kind === 'ellipse')

/** 点 P（椭圆上，t=52°）。 */
const pointP = computed(() => {
  const t = (52 * Math.PI) / 180
  return { x: CX + a.value * Math.cos(t) * scale.value, y: CY - b.value * Math.sin(t) * scale.value }
})

function fmt(n: number): string {
  return Number.isInteger(n) ? String(n) : n.toFixed(1)
}
</script>

<template>
  <svg v-if="isEllipse" class="pc-boardfig" :viewBox="`0 0 ${VB_W} ${VB_H}`" role="img" aria-label="椭圆参数化图形">
    <!-- 坐标轴 -->
    <g v-if="toggles.axes" stroke="#94a3b8" stroke-width="1">
      <line :x1="CX - rx - 34" :y1="CY" :x2="CX + rx + 40" :y2="CY" />
      <line :x1="CX" :y1="CY + ry + 30" :x2="CX" :y2="CY - ry - 30" />
      <path :d="`M${CX + rx + 40} ${CY} l-6 -3.4 v6.8 z`" fill="#94a3b8" stroke="none" />
      <path :d="`M${CX} ${CY - ry - 30} l-3.4 6 h6.8 z`" fill="#94a3b8" stroke="none" />
    </g>
    <text :x="CX + rx + 44" :y="CY + 4" font-size="10" fill="#64748b">x</text>
    <text :x="CX - 3" :y="CY - ry - 34" font-size="10" fill="#64748b">y</text>
    <text :x="CX - 12" :y="CY + 13" font-size="10" fill="#64748b">O</text>

    <!-- 椭圆 -->
    <ellipse :cx="CX" :cy="CY" :rx="rx" :ry="ry" fill="none" :stroke="color" stroke-width="1.6" />

    <!-- 辅助线 P-F₁ / P-F₂ -->
    <g v-if="toggles.aux" stroke="#a7d9f5" stroke-width="1.2" stroke-dasharray="5 4">
      <line :x1="pointP.x" :y1="pointP.y" :x2="CX - fx" :y2="CY" />
      <line :x1="pointP.x" :y1="pointP.y" :x2="CX + fx" :y2="CY" />
    </g>

    <!-- 焦点（红） -->
    <g v-if="toggles.foci" fill="#dc2626">
      <circle :cx="CX - fx" :cy="CY" r="3" />
      <circle :cx="CX + fx" :cy="CY" r="3" />
    </g>

    <!-- 点 P -->
    <circle :cx="pointP.x" :cy="pointP.y" r="3" :fill="color" />

    <!-- 数值标注 -->
    <g v-if="toggles.values" font-size="9.5" fill="#475569">
      <text :x="CX - fx - 30" :y="CY + 16">F₁(-{{ fmt(c) }},0)</text>
      <text :x="CX + fx + 6" :y="CY + 16">F₂({{ fmt(c) }},0)</text>
      <text :x="pointP.x + 6" :y="pointP.y - 6">P(x,y)</text>
    </g>
  </svg>
</template>

<style scoped>
.pc-boardfig {
  display: block;
  width: 100%;
  height: auto;
  max-height: 150px;
}
</style>
