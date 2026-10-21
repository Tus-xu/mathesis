<template>
  <div
    class="rw-resizer"
    role="separator"
    aria-orientation="vertical"
    :aria-label="ariaLabel"
    :aria-valuemin="min"
    :aria-valuemax="max"
    :aria-valuenow="Math.round(value)"
    tabindex="0"
    @pointerdown="onPointerDown"
    @keydown="onKeydown"
  >
    <span class="rw-resizer__grip" aria-hidden="true" />
  </div>
</template>

<script setup lang="ts">
/**
 * 面板拖动分隔条（FRONTEND-ARCH §2）。
 *
 * - pointer 事件驱动；拖动期对 `document.body` 设 `user-select:none` 防选中串扰；
 * - `min/max` 钳制；`side` 指明被调面板在分隔条哪一侧，决定位移正负；
 * - 支持键盘 ←/→（无障碍），每步 16px。
 * 只发出 `update:value`，不直接改 store（由 Shell 决定写到 panel.widths）。
 */
import { onBeforeUnmount } from 'vue'

const props = withDefaults(
  defineProps<{
    /** 当前面板宽度（px） */
    value: number
    /** 最小宽度（px） */
    min?: number
    /** 最大宽度（px） */
    max?: number
    /** 被调面板相对分隔条的位置 */
    side?: 'left' | 'right'
    /** 无障碍标签 */
    ariaLabel?: string
  }>(),
  {
    min: 220,
    max: 900,
    side: 'left',
    ariaLabel: '调整面板宽度',
  },
)

const emit = defineEmits<{ (e: 'update:value', v: number): void }>()

const clamp = (n: number) => Math.min(props.max, Math.max(props.min, Math.round(n)))

let startX = 0
let startValue = 0
let dragging = false

function onMove(e: PointerEvent) {
  if (!dragging) return
  const dx = e.clientX - startX
  // 面板在左：向右拖变大(+dx)；面板在右：向左拖变大(-dx)。
  const signed = props.side === 'left' ? dx : -dx
  emit('update:value', clamp(startValue + signed))
}

function onUp() {
  dragging = false
  window.removeEventListener('pointermove', onMove)
  window.removeEventListener('pointerup', onUp)
  document.body.style.userSelect = ''
  document.body.style.cursor = ''
}

function onPointerDown(e: PointerEvent) {
  if (e.button !== 0) return
  dragging = true
  startX = e.clientX
  startValue = props.value
  document.body.style.userSelect = 'none'
  document.body.style.cursor = 'col-resize'
  window.addEventListener('pointermove', onMove)
  window.addEventListener('pointerup', onUp)
  e.preventDefault()
}

function onKeydown(e: KeyboardEvent) {
  const step = 16
  if (e.key === 'ArrowLeft') {
    emit('update:value', clamp(props.value - step))
    e.preventDefault()
  } else if (e.key === 'ArrowRight') {
    emit('update:value', clamp(props.value + step))
    e.preventDefault()
  }
}

onBeforeUnmount(onUp)
</script>

<style scoped>
.rw-resizer {
  position: relative;
  width: 5px;
  flex: 0 0 5px;
  align-self: stretch;
  cursor: col-resize;
  background: transparent;
  touch-action: none;
}
.rw-resizer__grip {
  position: absolute;
  top: 0;
  left: 2px;
  width: 1px;
  height: 100%;
  background: var(--rw-border);
  transition: background-color .12s ease;
}
.rw-resizer:hover .rw-resizer__grip,
.rw-resizer:focus-visible .rw-resizer__grip {
  background: var(--rw-primary);
}
.rw-resizer:focus-visible {
  outline: none;
}
</style>
