<script setup lang="ts">
/**
 * 参数化数学图形（护城河层）：由 MathObject 参数实时生成 SVG，禁止贴图。
 *
 * - 数据→图形→公式同源：`a`/`b` 是唯一事实，`c=√(a²−b²)` 恒派生（与属性面板/公式同口径）；
 * - 开关真实作用于图形：焦点 F₁F₂ / 坐标轴 / 辅助线 PF₁PF₂ / 数值标注；
 * - 样式真实生效：颜色 / 线宽 / 线型；
 * - `compact`：缩略图微缩态（隐藏文字标注，保留几何）。
 */
import { computed } from 'vue'
import type { MathObject } from '@/features/teacher-prep-center/api/deckApi'

const props = withDefaults(
  defineProps<{
    mathObject: MathObject
    selected?: boolean
    /** 缩略图微缩态：隐藏文字，仅几何。 */
    compact?: boolean
  }>(),
  { selected: false, compact: false },
)

const emit = defineEmits<{ (e: 'select'): void }>()

const W = 680
const H = 410
const CX = W / 2
const CY = H / 2 + 6

const a = computed(() => {
  const v = Number(props.mathObject.params?.a)
  return Number.isFinite(v) && v > 0 ? v : 5
})
const b = computed(() => {
  const v = Number(props.mathObject.params?.b)
  return Number.isFinite(v) && v > 0 ? v : 3
})
/** 半焦距 c=√(a²−b²)：唯一派生口径（与后端 MathObjectSpec 同公式）。 */
const c = computed(() => Math.sqrt(Math.max(0, a.value * a.value - b.value * b.value)))

/** 世界坐标 → 像素比例（随参数自适应，椭圆填满图区且不溢出）。 */
const k = computed(() => {
  const kx = (W - 100) / 2 / Math.max(a.value, c.value, 1)
  const ky = (H - 62) / 2 / Math.max(b.value, 1)
  return Math.max(6, Math.min(kx, ky))
})
const px = (x: number): number => CX + x * k.value
const py = (y: number): number => CY - y * k.value

const color = computed(() => props.mathObject.style?.color || '#2563EB')
const strokeWidth = computed(() => {
  const v = Number(props.mathObject.style?.stroke_px)
  return Number.isFinite(v) && v > 0 ? v : 2
})
const dashArray = computed(() => {
  const dash = props.mathObject.style?.dash || 'solid'
  if (dash === 'dashed') return '10 6'
  if (dash === 'dotted') return '2 5'
  return undefined
})

const toggles = computed(() => ({
  foci: props.mathObject.toggles?.show_foci !== false,
  axes: props.mathObject.toggles?.show_axes !== false,
  aux: props.mathObject.toggles?.show_aux_lines !== false,
  labels: props.mathObject.toggles?.show_value_labels === true,
}))

/** 辅助线点 P（参数角 58°，避开顶点/焦点，与黄金图构图一致）。 */
const P = computed(() => {
  const theta = (58 * Math.PI) / 180
  return { x: a.value * Math.cos(theta), y: b.value * Math.sin(theta) }
})
const numeric = (v: number): string => (Number.isInteger(v) ? String(v) : v.toFixed(1))

const focusLabels = computed(() => {
  if (!toggles.value.foci) return []
  const cx = numeric(c.value)
  return [
    { x: px(-c.value), text: toggles.value.labels ? `F₁(−${cx}, 0)` : 'F₁(−c, 0)' },
    { x: px(c.value), text: toggles.value.labels ? `F₂(${cx}, 0)` : 'F₂(c, 0)' },
  ]
})

const pText = computed(() => {
  const p = P.value
  return toggles.value.labels ? `P(${numeric(p.x)}, ${numeric(p.y)})` : 'P(x, y)'
})

/** 坐标轴刻度（真实数值：±a、±c 与 ±b）。 */
const xTicks = computed(() => {
  if (!toggles.value.axes) return []
  const set = new Set([-a.value, -c.value, c.value, a.value].map((v) => Math.round(v * 100) / 100))
  return [...set].map((v) => ({ v, x: px(v) }))
})
const yTicks = computed(() => {
  if (!toggles.value.axes) return []
  return [-b.value, b.value].map((v) => ({ v, y: py(v) }))
})
</script>

<template>
  <div
    class="pc-mathfig"
    :class="{ 'is-selected': selected, 'is-compact': compact }"
    role="img"
    :aria-label="`参数化椭圆：a=${a}，b=${b}，c=${c.toFixed(1)}`"
    @click.stop="emit('select')"
  >
    <svg :viewBox="`0 0 ${W} ${H}`" class="pc-mathfig__svg" preserveAspectRatio="xMidYMid meet">
      <!-- 坐标轴（含箭头 / 原点 O / 刻度）——黄金图：坐标轴为深色墨线，非浅灰 -->
      <template v-if="toggles.axes">
        <line :x1="16" :y1="CY" :x2="W - 16" :y2="CY" stroke="#334155" stroke-width="1.6" />
        <polygon :points="`${W - 8},${CY} ${W - 20},${CY - 5.5} ${W - 20},${CY + 5.5}`" fill="#334155" />
        <line :x1="CX" :y1="H - 14" :x2="CX" :y2="14" stroke="#334155" stroke-width="1.6" />
        <polygon :points="`${CX},6 ${CX - 5.5},${19} ${CX + 5.5},${19}`" fill="#334155" />
        <line
          v-for="t in xTicks"
          :key="`xt-${t.v}`"
          :x1="t.x"
          :y1="CY - 4.5"
          :x2="t.x"
          :y2="CY + 4.5"
          stroke="#334155"
          stroke-width="1.4"
        />
        <line
          v-for="t in yTicks"
          :key="`yt-${t.v}`"
          :x1="CX - 4.5"
          :y1="t.y"
          :x2="CX + 4.5"
          :y2="t.y"
          stroke="#334155"
          stroke-width="1.4"
        />
        <template v-if="!compact">
          <text :x="W - 34" :y="CY + 24" font-size="17" font-style="italic" fill="#334155">x</text>
          <text :x="CX + 11" y="22" font-size="17" font-style="italic" fill="#334155">y</text>
          <text :x="CX - 21" :y="CY + 22" font-size="16" fill="#334155">O</text>
        </template>
      </template>

      <!-- 椭圆实线（参数实时生成） -->
      <ellipse
        :cx="CX"
        :cy="CY"
        :rx="a * k"
        :ry="b * k"
        fill="none"
        :stroke="color"
        :stroke-width="strokeWidth"
        :stroke-dasharray="dashArray"
      />

      <!-- 辅助线（虚线 PF₁ / PF₂）与点 P 标注 -->
      <template v-if="toggles.aux">
        <line
          :x1="px(-c)"
          :y1="py(0)"
          :x2="px(P.x)"
          :y2="py(P.y)"
          stroke="#8899b3"
          stroke-width="1.1"
          stroke-dasharray="5 4"
        />
        <line
          :x1="px(c)"
          :y1="py(0)"
          :x2="px(P.x)"
          :y2="py(P.y)"
          stroke="#8899b3"
          stroke-width="1.1"
          stroke-dasharray="5 4"
        />
        <circle :cx="px(P.x)" :cy="py(P.y)" r="3.6" fill="#1f2937" />
        <text
          v-if="!compact"
          :x="px(P.x) + 9"
          :y="py(P.y) - 8"
          font-size="15"
          fill="#0F172A"
        >{{ pText }}</text>
      </template>

      <!-- 焦点红点 F₁F₂ -->
      <template v-if="toggles.foci">
        <circle :cx="px(-c)" :cy="py(0)" r="4.6" fill="#DC2626" />
        <circle :cx="px(c)" :cy="py(0)" r="4.6" fill="#DC2626" />
        <text
          v-for="(f, i) in focusLabels"
          :key="`fl-${i}`"
          :x="f.x"
          :y="py(0) + 26"
          font-size="15"
          fill="#0f172a"
          text-anchor="middle"
          v-show="!compact"
        >{{ f.text }}</text>
      </template>
    </svg>
    <span v-if="selected && !compact" class="pc-mathfig__badge">已选中 · 图形属性</span>
  </div>
</template>

<style scoped>
.pc-mathfig {
  position: relative;
  width: 100%;
  height: 100%;
  cursor: pointer;
  border-radius: 8px;
}
.pc-mathfig.is-compact {
  cursor: default;
}
.pc-mathfig.is-selected {
  outline: 1.5px dashed var(--pc-primary);
  outline-offset: 4px;
}
.pc-mathfig__svg {
  width: 100%;
  height: 100%;
  display: block;
}
.pc-mathfig__badge {
  position: absolute;
  top: 6px;
  right: 8px;
  padding: 2px 8px;
  border-radius: var(--pc-radius-full);
  background: var(--pc-primary-soft);
  color: var(--pc-primary);
  font-size: 11px;
}
</style>
