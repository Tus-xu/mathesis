<template>
  <div
    class="ck-root"
    data-testid="tv3-companion-dock"
    :class="{ 'ck-root--dragging': dragging }"
    :style="dockStyle"
    ref="dockRef"
  >
    <!-- 收起态（窄屏）：单主球，点击展开三个入口 -->
    <template v-if="collapsed && !expanded">
      <button
        class="ck-item ck-item--ai"
        type="button"
        aria-label="打开伴随工具"
        title="伴随工具（AI / 资源 / 绘图）"
        :class="{ 'ck-item--pulse': unread > 0 && !butlerOpen }"
        @click="expanded = true"
      >
        <span class="ck-item__halo" />
        <span class="ck-item__badge">∫</span>
        <span v-if="unread > 0 && !butlerOpen" class="ck-item__dot">{{ unread > 9 ? '9+' : unread }}</span>
      </button>
    </template>

    <!-- 展开态：绘图 / 资源 次入口 + AI 主入口（纵向，视觉权重递增） -->
    <transition-group name="ck-expand" tag="div" class="ck-stack">
      <template v-if="!collapsed || expanded">
        <transition name="ck-fade">
          <button
            v-if="showDraw"
            key="draw"
            class="ck-item ck-item--sm ck-item--draw"
            type="button"
            aria-label="数学绘图"
            data-testid="tv3-dock-draw"
            @click="onToolClick('draw')"
          >
            <span class="ck-item__ic">📐</span>
            <span class="ck-item__name">数学绘图</span>
            <span class="ck-tip ck-tip--left">
              <span class="ck-tip__title">数学绘图</span>
              <span class="ck-tip__desc">构造公式图形 · 可插入课件</span>
            </span>
          </button>
        </transition>

        <transition name="ck-fade">
          <button
            v-if="showResource"
            key="resource"
            class="ck-item ck-item--sm ck-item--resource"
            type="button"
            aria-label="伴随资源"
            data-testid="tv3-dock-resource"
            @click="onToolClick('resource')"
          >
            <span class="ck-item__ic">📚</span>
            <span class="ck-item__name">伴随资源</span>
            <span class="ck-tip ck-tip--left">
              <span class="ck-tip__title">伴随资源</span>
              <span class="ck-tip__desc">按教学意图推荐 · 随取随用</span>
            </span>
          </button>
        </transition>

        <button
          key="ai"
          class="ck-item ck-item--ai"
          type="button"
          aria-label="AI 助教"
          data-testid="tv3-butler-fab"
          :aria-expanded="butlerOpen"
          :class="{ 'ck-item--active': butlerOpen, 'ck-item--pulse': unread > 0 && !butlerOpen }"
          @click="onAiClick"
        >
          <span class="ck-item__halo" />
          <span class="ck-item__badge">∫</span>
          <span class="ck-item__label">AI</span>
          <span v-if="unread > 0 && !butlerOpen" class="ck-item__dot">{{ unread > 9 ? '9+' : unread }}</span>
          <span class="ck-tip ck-tip--left">
            <span class="ck-tip__title">AI 助教</span>
            <span class="ck-tip__desc">对话 · 搜索 · 语音公式 · 应用内操作</span>
          </span>
        </button>

        <transition name="ck-fade">
          <button
            v-if="collapsed && expanded"
            key="close"
            class="ck-item ck-item--sm ck-item--ghost"
            type="button"
            aria-label="收起伴随工具"
            @click="expanded = false"
          >
            <span class="ck-item__ic">×</span>
          </button>
        </transition>
      </template>
    </transition-group>

    <!-- 拖拽把手（仅在 hover 主球时显示，实现 Microsoft Copilot 式可拖动） -->
    <div
      v-if="!collapsed && !expanded"
      class="ck-drag-handle"
      :class="{ 'is-dragging': dragging }"
      @mousedown="startDrag"
    >
      <span class="ck-drag-handle__bar" />
      <span class="ck-drag-handle__bar" />
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * TeacherCompanionDock —— 右下角统一伴随工具入口（C2，IFC-C2-a）
 * 【2026 升级】参考 Microsoft Copilot DAB + 飞书 AI 侧边栏 + Material FAB 设计模式
 * - 视觉：主球带呼吸光晕（暗示 AI 活性），次入口 hover 展开文字标签
 * - 交互：可拖拽贴边（参考 Copilot 可停靠设计），展开有级联动画
 * - 状态：未读脉冲提醒、激活态高亮、忙碌态波纹
 * - 肌肉记忆：找 AI / 找资源 / 画数学图 → 都在右下角
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

const props = defineProps<{ butlerOpen: boolean; unread: number }>()
const emit = defineEmits<{ (e: 'ai'): void; (e: 'tool', tool: 'resource' | 'draw'): void }>()

const route = useRoute()
const dockRef = ref<HTMLElement | null>(null)
const expanded = ref(false)
const narrow = ref(false)
const dragging = ref(false)

/* ---------- 拖拽位置（可停靠到左右边缘） ---------- */
const dockSide = ref<'right' | 'left'>('right')
const dockBottom = ref(26)
let dragStartX = 0
let dragStartY = 0
let dragOrigBottom = 26
let dragOrigSide: 'right' | 'left' = 'right'

const dockStyle = computed(() => {
  const style: Record<string, string> = {
    bottom: dockBottom.value + 'px',
  }
  if (dockSide.value === 'right') {
    style.right = '22px'
    style.left = 'auto'
  } else {
    style.left = '22px'
    style.right = 'auto'
  }
  return style
})

function startDrag(ev: MouseEvent) {
  if (narrow.value) return
  dragging.value = true
  dragStartX = ev.clientX
  dragStartY = ev.clientY
  dragOrigBottom = dockBottom.value
  dragOrigSide = dockSide.value
  document.addEventListener('mousemove', onDrag)
  document.addEventListener('mouseup', endDrag)
  ev.preventDefault()
}
function onDrag(ev: MouseEvent) {
  if (!dragging.value) return
  const dx = ev.clientX - dragStartX
  const dy = ev.clientY - dragStartY
  // 垂直方向：调整 bottom 位置
  const newBottom = Math.max(20, Math.min(window.innerHeight - 200, dragOrigBottom - dy))
  dockBottom.value = newBottom
  // 水平方向：超过屏幕中线则换边
  const midX = window.innerWidth / 2
  const currentX = dragOrigSide === 'right'
    ? window.innerWidth - 22 - 28 + dx // 28 是球心偏移近似
    : 22 + 28 + dx
  dockSide.value = currentX > midX ? 'right' : 'left'
}
function endDrag() {
  dragging.value = false
  document.removeEventListener('mousemove', onDrag)
  document.removeEventListener('mouseup', endDrag)
}

/* ---------- 响应式 ---------- */
watch(narrow, (v) => { if (!v) expanded.value = false })
function onResize() { narrow.value = window.innerWidth <= 1100 }

/* ---------- 页面矩阵：按路由决定次入口显隐 ---------- */
const MATRIX: Record<string, { resource: boolean; draw: boolean }> = {
  today: { resource: true, draw: false },
  prep: { resource: true, draw: true },
  slides: { resource: true, draw: true },
  classroom: { resource: true, draw: true },
  bank: { resource: true, draw: true },
  quiz: { resource: true, draw: true },
  assign: { resource: true, draw: false },
  insights: { resource: true, draw: false },
  resources: { resource: false, draw: true },
}
const pageKey = computed(() => route.path.replace('/teacher-v3/', '') || 'today')
const showResource = computed(() => MATRIX[pageKey.value]?.resource ?? true)
const showDraw = computed(() => MATRIX[pageKey.value]?.draw ?? false)
const collapsed = computed(() => narrow.value)

/* ---------- 点击处理 ---------- */
function onAiClick() {
  expanded.value = false
  emit('ai')
}
function onToolClick(tool: 'resource' | 'draw') {
  expanded.value = false
  emit('tool', tool)
}

onMounted(() => {
  onResize()
  window.addEventListener('resize', onResize)
})
onBeforeUnmount(() => {
  window.removeEventListener('resize', onResize)
  document.removeEventListener('mousemove', onDrag)
  document.removeEventListener('mouseup', endDrag)
})
</script>

<style scoped>
/* ==========================================================================
   容器与基础
   ========================================================================== */
.ck-root {
  position: fixed;
  right: 22px;
  bottom: 26px;
  z-index: 920;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
  transition: left .35s cubic-bezier(0.34, 1.56, 0.64, 1),
              right .35s cubic-bezier(0.34, 1.56, 0.64, 1),
              bottom .25s ease;
}
.ck-root--dragging {
  transition: none;
  cursor: grabbing;
}
.ck-stack {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
}

/* ==========================================================================
   悬浮球通用样式
   ========================================================================== */
.ck-item {
  position: relative;
  border: none;
  cursor: pointer;
  border-radius: 50%;
  display: grid;
  place-items: center;
  padding: 0;
  transition: transform .22s cubic-bezier(0.34, 1.56, 0.64, 1),
              box-shadow .22s ease,
              width .25s cubic-bezier(0.34, 1.56, 0.64, 1),
              height .25s cubic-bezier(0.34, 1.56, 0.64, 1),
              background .2s ease;
}
.ck-item:focus-visible {
  outline: 2px solid var(--tv3-gold, #0891b2);
  outline-offset: 3px;
}

/* ==========================================================================
   AI 主球（核心视觉锚点）
   参考 Microsoft Copilot DAB + 呼吸光晕
   ========================================================================== */
.ck-item--ai {
  width: 60px;
  height: 60px;
  background: linear-gradient(135deg, #4f46e5 0%, #1a5aa8 50%, #4f46e5 100%);
  color: #fff;
  box-shadow:
    0 8px 28px rgba(79, 70, 229, 0.42),
    0 2px 8px rgba(79, 70, 229, 0.25),
    inset 0 1px 0 rgba(255, 255, 255, 0.18);
  overflow: visible;
}
.ck-item--ai:hover {
  transform: translateY(-3px) scale(1.06);
  box-shadow:
    0 14px 38px rgba(79, 70, 229, 0.52),
    0 4px 14px rgba(79, 70, 229, 0.3),
    inset 0 1px 0 rgba(255, 255, 255, 0.22);
}
.ck-item--ai:active {
  transform: translateY(-1px) scale(1.02);
}
.ck-item--ai.ck-item--active {
  transform: scale(0.94);
  box-shadow:
    0 4px 14px rgba(79, 70, 229, 0.35),
    inset 0 2px 6px rgba(0, 0, 0, 0.2);
}

/* 呼吸光晕 —— 暗示 AI 是"活的" */
.ck-item__halo {
  position: absolute;
  inset: -8px;
  border-radius: 50%;
  background: radial-gradient(circle, rgba(79, 70, 229, 0.35) 0%, transparent 70%);
  opacity: 0;
  pointer-events: none;
  animation: ck-halo-breathe 4s ease-in-out infinite;
}
.ck-item--ai:hover .ck-item__halo {
  opacity: 1;
  animation-duration: 2.5s;
}
@keyframes ck-halo-breathe {
  0%, 100% { transform: scale(0.9); opacity: 0.4; }
  50% { transform: scale(1.15); opacity: 0.7; }
}

/* 未读脉冲提醒 */
.ck-item--pulse::after {
  content: '';
  position: absolute;
  inset: -4px;
  border-radius: 50%;
  border: 2px solid var(--tv3-rose, #dc2646);
  opacity: 0;
  animation: ck-pulse-ring 2s ease-out infinite;
  pointer-events: none;
}
@keyframes ck-pulse-ring {
  0% { transform: scale(0.85); opacity: 0.8; }
  100% { transform: scale(1.4); opacity: 0; }
}

.ck-item__badge {
  font-family: Cambria Math, Georgia, serif;
  font-size: 28px;
  line-height: 1;
  font-weight: 600;
  text-shadow: 0 1px 2px rgba(0, 0, 0, 0.2);
  position: relative;
  z-index: 1;
}
.ck-item__label {
  position: absolute;
  bottom: 10px;
  right: 10px;
  font-size: 9.5px;
  font-weight: 800;
  background: linear-gradient(135deg, #e0b458, #0891b2);
  color: #1c2b1a;
  border-radius: 999px;
  padding: 1.5px 6px;
  line-height: 1.3;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.2);
  z-index: 1;
  letter-spacing: 0.3px;
}
.ck-item__dot {
  position: absolute;
  top: -3px;
  right: -3px;
  min-width: 20px;
  height: 20px;
  border-radius: 999px;
  background: linear-gradient(135deg, #ef4444, #dc2646);
  color: #fff;
  font-size: 11px;
  font-weight: 700;
  display: grid;
  place-items: center;
  padding: 0 5px;
  border: 2px solid #fff;
  box-shadow: 0 2px 6px rgba(220, 38, 70, 0.4);
  z-index: 2;
  font-family: var(--tv3-font-num);
}

/* ==========================================================================
   次入口球（资源 / 绘图）
   hover 时展开为胶囊形态，显示文字标签
   ========================================================================== */
.ck-item--sm {
  width: 48px;
  height: 48px;
  background: #fff;
  border: 1.5px solid var(--tv3-line2, #e2e7ef);
  box-shadow:
    0 4px 16px rgba(10, 30, 58, 0.12),
    0 1px 3px rgba(10, 30, 58, 0.06);
  font-size: 20px;
  overflow: hidden;
}
.ck-item--sm:hover {
  transform: translateY(-2px);
  width: 140px;
  border-radius: 999px;
  border-color: var(--tv3-gold, #0891b2);
  box-shadow:
    0 8px 24px rgba(6, 182, 212, 0.2),
    0 2px 8px rgba(6, 182, 212, 0.12);
}
.ck-item--sm:active {
  transform: translateY(0);
}
.ck-item__ic {
  line-height: 1;
  transition: transform .2s ease;
}
.ck-item--sm:hover .ck-item__ic {
  transform: scale(1.1);
}
.ck-item__name {
  position: absolute;
  left: 48px;
  right: 12px;
  font-size: 12.5px;
  font-weight: 600;
  color: var(--tv3-ink, #16233b);
  white-space: nowrap;
  opacity: 0;
  transform: translateX(-8px);
  transition: opacity .18s ease .05s, transform .2s ease .05s;
  text-align: left;
}
.ck-item--sm:hover .ck-item__name {
  opacity: 1;
  transform: translateX(0);
}

/* 资源球专属配色 */
.ck-item--resource:hover {
  border-color: var(--tv3-navy, #4f46e5);
  background: linear-gradient(135deg, #fff, #f0f6fd);
  box-shadow:
    0 8px 24px rgba(79, 70, 229, 0.18),
    0 2px 8px rgba(79, 70, 229, 0.1);
}
.ck-item--resource:hover .ck-item__name {
  color: var(--tv3-navy, #4f46e5);
}

/* 绘图球专属配色 */
.ck-item--draw:hover {
  border-color: var(--tv3-ai, #7c3aed);
  background: linear-gradient(135deg, #fff, #f8f3fd);
  box-shadow:
    0 8px 24px rgba(124, 58, 237, 0.18),
    0 2px 8px rgba(124, 58, 237, 0.1);
}
.ck-item--draw:hover .ck-item__name {
  color: var(--tv3-ai, #7c3aed);
}

/* 收起按钮 */
.ck-item--ghost {
  background: var(--tv3-ink, #16233b);
  color: #fff;
  border: none;
  font-size: 16px;
  font-weight: 600;
}
.ck-item--ghost:hover {
  background: var(--tv3-navy, #4f46e5);
  transform: scale(1.08);
}

/* ==========================================================================
   增强版 Tooltip（标题 + 描述）
   ========================================================================== */
.ck-tip {
  position: absolute;
  right: calc(100% + 14px);
  top: 50%;
  transform: translateY(-50%);
  background: var(--tv3-ink, #16233b);
  color: #fff;
  padding: 8px 12px;
  border-radius: 10px;
  white-space: nowrap;
  opacity: 0;
  pointer-events: none;
  transition: opacity .18s ease, transform .18s ease;
  display: flex;
  flex-direction: column;
  gap: 2px;
  box-shadow: 0 6px 20px rgba(0, 0, 0, 0.25);
  z-index: 10;
}
.ck-tip::after {
  content: '';
  position: absolute;
  right: -5px;
  top: 50%;
  transform: translateY(-50%) rotate(45deg);
  width: 10px;
  height: 10px;
  background: var(--tv3-ink, #16233b);
}
.ck-tip--left::after {
  right: -5px;
  left: auto;
}
.ck-tip__title {
  font-size: 12.5px;
  font-weight: 700;
  line-height: 1.3;
}
.ck-tip__desc {
  font-size: 11px;
  color: rgba(255, 255, 255, 0.65);
  line-height: 1.3;
}
.ck-item:hover .ck-tip,
.ck-item:focus-visible .ck-tip {
  opacity: 1;
  transform: translateY(-50%) translateX(0);
}

/* 次入口 hover 时隐藏 tooltip（因为已经展开了名字） */
.ck-item--sm:hover .ck-tip {
  opacity: 0;
}

/* ==========================================================================
   拖拽把手
   ========================================================================== */
.ck-drag-handle {
  position: absolute;
  top: -24px;
  left: 50%;
  transform: translateX(-50%);
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding: 6px 8px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.9);
  box-shadow: 0 2px 8px rgba(10, 30, 58, 0.12);
  cursor: grab;
  opacity: 0;
  transition: opacity .2s ease, transform .2s ease;
}
.ck-root:hover .ck-drag-handle {
  opacity: 1;
}
.ck-drag-handle.is-dragging {
  opacity: 1;
  cursor: grabbing;
}
.ck-drag-handle__bar {
  width: 20px;
  height: 3px;
  border-radius: 999px;
  background: var(--tv3-ink3, #8b95a7);
}

/* ==========================================================================
   展开 / 收起动画（级联效果）
   ========================================================================== */
.ck-expand-enter-active,
.ck-expand-leave-active {
  transition: all .3s cubic-bezier(0.34, 1.56, 0.64, 1);
}
.ck-expand-enter-from,
.ck-expand-leave-to {
  opacity: 0;
  transform: translateY(10px) scale(0.8);
}

.ck-fade-enter-active,
.ck-fade-leave-active {
  transition: all .25s cubic-bezier(0.34, 1.56, 0.64, 1);
}
.ck-fade-enter-from,
.ck-fade-leave-to {
  opacity: 0;
  transform: translateY(8px) scale(0.85);
}

/* 级联延迟：从下往上依次出现 */
.ck-stack > *:nth-child(1) { transition-delay: 0s !important; }
.ck-stack > *:nth-child(2) { transition-delay: .05s !important; }
.ck-stack > *:nth-child(3) { transition-delay: .1s !important; }
.ck-stack > *:nth-child(4) { transition-delay: .15s !important; }
</style>
