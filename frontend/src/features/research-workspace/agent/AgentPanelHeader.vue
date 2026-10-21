<template>
  <header class="rw-agent-header">
    <span class="rw-agent-header__logo" aria-hidden="true">π</span>
    <div class="rw-agent-header__meta">
      <p class="rw-agent-header__title">
        <span class="rw-agent-header__name">Research Agent</span>
        <span class="rw-agent-header__cn">科研助手</span>
        <span class="rw-agent-header__online">
          <span class="rw-agent-header__dot" aria-hidden="true"></span>
          在线
        </span>
      </p>
      <p class="rw-agent-header__subtitle">{{ subtitle }}</p>
    </div>
    <div class="rw-agent-header__actions">
      <button
        type="button"
        class="rw-agent-header__new"
        title="开始一个新对话（保留当前项目与上下文）"
        @click="emit('new-chat')"
      >
        <AddOutline class="rw-agent-header__new-ico" aria-hidden="true" />
        <span>新对话</span>
      </button>
      <div class="rw-agent-header__menuwrap">
        <button
          type="button"
          class="rw-agent-header__menu"
          aria-label="更多操作"
          title="更多操作"
          :aria-expanded="menuOpen"
          aria-haspopup="menu"
          @click="menuOpen = !menuOpen"
        >
          <EllipsisHorizontalOutline />
        </button>
      <!-- 真实菜单（§70：⋯ 必须有真实动作） -->
      <div v-if="menuOpen" class="rw-agent-header__menupop" role="menu" aria-label="更多操作">
        <button type="button" role="menuitem" class="rw-agent-header__menuitem" @click="act('clear-chat')">
          清空对话记录
        </button>
        <button type="button" role="menuitem" class="rw-agent-header__menuitem" @click="act('clear-result')">
          清空当前结果卡
        </button>
      </div>
      </div>
    </div>
  </header>
</template>

<script setup lang="ts">
/**
 * Agent 面板头（VISUAL-SPEC §4.4-1 / 宪法 §25）。
 *
 * 圆形蓝 Logo + `Research Agent` + `科研助理智能体` + `● 在线` + 副标题（READ/WRITE 不同）
 * + 右侧 `⋯`。两页共用同一组件，仅副标题按 `mode` 分叉。
 * `⋯` 展开真实菜单：清空对话记录 / 清空当前结果卡（此前是 noop 摆设，§70）。
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { AddOutline, EllipsisHorizontalOutline } from '@vicons/ionicons5'
import type { WorkspaceMode } from '../api/types'

const props = defineProps<{ mode: WorkspaceMode }>()
const emit = defineEmits<{
  (e: 'menu'): void
  (e: 'menu-action', key: 'clear-chat' | 'clear-result'): void
  (e: 'new-chat'): void
}>()

const subtitle = computed(() =>
  props.mode === 'write'
    ? '基于草稿、编译日志与项目文献'
    : '基于文献、公式与项目笔记',
)

const menuOpen = ref(false)
const menuwrap = ref<HTMLElement | null>(null)

function act(key: 'clear-chat' | 'clear-result') {
  menuOpen.value = false
  emit('menu-action', key)
}

function onDocClick(e: MouseEvent) {
  if (!menuOpen.value) return
  const el = e.target as Node | null
  if (el && menuwrap.value?.contains(el)) return
  menuOpen.value = false
}
onMounted(() => document.addEventListener('mousedown', onDocClick))
onBeforeUnmount(() => document.removeEventListener('mousedown', onDocClick))
</script>

<style scoped>
.rw-agent-header {
  display: flex;
  align-items: center;
  gap: 10px;
  height: 64px;
  flex: 0 0 auto;
  padding: 0 14px 0 16px;
  background: var(--rw-surface);
  border-bottom: var(--rw-border-w) solid var(--rw-border);
}
.rw-agent-header__logo {
  width: 34px;
  height: 34px;
  flex: 0 0 auto;
  display: grid;
  place-items: center;
  border-radius: 999px;
  background: var(--gradient-brand);
  color: #fff;
  font: italic 900 17px Georgia, "Times New Roman", serif;
  box-shadow: var(--rw-shadow-card);
}
.rw-agent-header__meta {
  min-width: 0;
  flex: 1 1 auto;
}
.rw-agent-header__title {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 0;
  min-width: 0;
}
.rw-agent-header__name {
  font-size: 15px;
  font-weight: 800;
  color: var(--rw-text);
  white-space: nowrap;
  letter-spacing: -.01em;
}
.rw-agent-header__cn {
  font-size: 10.5px;
  font-weight: 700;
  color: var(--rw-primary);
  white-space: nowrap;
  padding: 1px 7px;
  border-radius: 999px;
  background: var(--rw-primary-soft);
}
.rw-agent-header__online {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 10px;
  font-weight: 700;
  color: var(--rw-success);
  white-space: nowrap;
}
.rw-agent-header__dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--rw-success);
  box-shadow: 0 0 0 3px var(--rw-success-soft);
}
.rw-agent-header__subtitle {
  margin: 3px 0 0;
  font-size: 10.5px;
  color: var(--rw-text-3);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.rw-agent-header__actions {
  display: flex;
  align-items: center;
  gap: 6px;
  flex: 0 0 auto;
}
.rw-agent-header__new {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 30px;
  padding: 0 10px;
  border: 1px solid var(--rw-border);
  border-radius: 999px;
  background: var(--rw-surface);
  color: var(--rw-text-2);
  font: inherit;
  font-size: 10.5px;
  font-weight: 700;
  cursor: pointer;
}
.rw-agent-header__new:hover {
  border-color: var(--rw-primary);
  color: var(--rw-primary);
  background: var(--rw-primary-soft);
}
.rw-agent-header__new-ico { width: 13px; height: 13px; }
.rw-agent-header__menu {
  flex: 0 0 auto;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border: var(--rw-border-w) solid var(--rw-border);
  border-radius: 999px;
  background: var(--rw-surface);
  color: var(--rw-text-2);
  font-size: 16px;
  cursor: pointer;
}
/* ⋯ 按钮外层容器 + 弹层菜单 */
.rw-agent-header__menuwrap {
  position: relative;
  flex: 0 0 auto;
  display: inline-flex;
}
.rw-agent-header__menupop {
  position: absolute;
  top: calc(100% + 6px);
  right: 0;
  z-index: 45;
  min-width: 160px;
  padding: 6px;
  border: var(--rw-border-w) solid var(--rw-border);
  border-radius: var(--rw-radius-md);
  background: var(--rw-surface);
  box-shadow: var(--rw-shadow-pop);
}
.rw-agent-header__menuitem {
  display: block;
  width: 100%;
  height: 32px;
  padding: 0 10px;
  border: none;
  border-radius: var(--rw-radius-sm);
  background: transparent;
  color: var(--rw-text);
  font-size: var(--rw-fs-sm);
  text-align: left;
  cursor: pointer;
  white-space: nowrap;
}
.rw-agent-header__menuitem:hover {
  background: var(--rw-surface-2);
  color: var(--rw-primary);
}
/* §4.4-1 面板头 `⋯` 图标 16px（按钮本体 36×36） */
.rw-agent-header__menu :deep(svg) {
  width: 16px;
  height: 16px;
}
.rw-agent-header__menu:hover {
  color: var(--rw-primary);
  border-color: var(--rw-primary);
}

/* ── WRITE 314px 紧凑态（VISUAL-SPEC §1.5 / 黄金图 WRITE 面板头）──
   黄金图 WRITE 头只显示 `Research Agent ● 在线` + 副标题（中文名在窄栏收起），
   且 `在线` 带**浅绿药丸底**（WRITE-GOLDEN 实测 bg ≈ #EAF9F1，READ 头无底）。
   此处按黄金图逐像素对齐；副标题、在线态、菜单均保留（不隐藏内容）。 */
@container rw-agent (max-width: 400px) {
  .rw-agent-header {
    gap: 8px;
    padding: 0 10px;
  }
  .rw-agent-header__cn {
    display: none;
  }
  .rw-agent-header__name {
    font-size: 13.5px;
  }
  .rw-agent-header__new {
    width: 30px;
    padding: 0;
    justify-content: center;
  }
  .rw-agent-header__new span {
    display: none;
  }
}
</style>
