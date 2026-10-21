<template>
  <div class="rw-ccb">
    <!-- 引擎：后端当前仅支持 XeLaTeX（compile.py 钉死）→ 下拉给真实说明，不给假选项 -->
    <div class="rw-ccb__dd">
      <button
        type="button"
        class="rw-ccb__select"
        :disabled="disabled"
        :aria-expanded="openMenu === 'engine'"
        @click="toggleMenu('engine')"
      >
        {{ engine }}
        <ChevronDownOutline class="rw-ccb__chev" aria-hidden="true" />
      </button>
      <div v-if="openMenu === 'engine'" class="rw-ccb__pop" role="note">
        <p class="rw-ccb__pop-row"><span class="rw-ccb__pop-k">编译引擎</span>XeLaTeX（当前唯一支持）</p>
        <p class="rw-ccb__pop-row"><span class="rw-ccb__pop-k">入口文件</span>main.tex</p>
      </div>
    </div>

    <!-- 自动编译：尚未实现 → 点击给诚实说明（§70 禁止摆设下拉） -->
    <div class="rw-ccb__dd">
      <button
        type="button"
        class="rw-ccb__select"
        :disabled="disabled"
        :aria-expanded="openMenu === 'auto'"
        @click="toggleMenu('auto')"
      >
        自动编译
        <ChevronDownOutline class="rw-ccb__chev" aria-hidden="true" />
      </button>
      <div v-if="openMenu === 'auto'" class="rw-ccb__pop" role="note">
        <p class="rw-ccb__pop-row">自动编译即将开放。当前请保存后在右侧点击「重新编译」。</p>
      </div>
    </div>

    <button
      type="button"
      class="rw-ccb__primary"
      :disabled="disabled"
      @click="emit('recompile')"
    >
      <RefreshOutline class="rw-ccb__ico" aria-hidden="true" />
      重新编译
    </button>
    <span class="rw-ccb__status" :class="statusClass">
      <component :is="statusIcon" class="rw-ccb__status-ico" aria-hidden="true" />
      {{ statusText }}
    </span>
    <span v-if="noticeText" class="rw-ccb__notice" role="status" aria-live="polite">
      {{ noticeText }}
    </span>

    <!-- 更多：编译配置速览（真实数据） -->
    <div class="rw-ccb__dd">
      <button
        type="button"
        class="rw-ccb__more"
        :disabled="disabled"
        aria-label="更多编译选项"
        :aria-expanded="openMenu === 'more'"
        @click="toggleMenu('more')"
      >
        <EllipsisHorizontalOutline aria-hidden="true" />
      </button>
      <div v-if="openMenu === 'more'" class="rw-ccb__pop" role="note">
        <p class="rw-ccb__pop-row"><span class="rw-ccb__pop-k">入口文件</span>main.tex</p>
        <p class="rw-ccb__pop-row"><span class="rw-ccb__pop-k">编译引擎</span>XeLaTeX（两遍 + BibTeX）</p>
        <p class="rw-ccb__pop-row"><span class="rw-ccb__pop-k">产物</span>main.pdf（预览栏实时渲染）</p>
        <p class="rw-ccb__pop-row">更多编译选项将在后续版本开放。</p>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * WRITE 顶栏中槽 · 编译控制条（VISUAL-SPEC §1.2）。
 * `XeLaTeX ⌄` / `自动编译 ⌄`（白底描边 h28 radius6）+ `⟳ 重新编译`（主蓝实心）
 * + 编译状态（成功绿 / 失败红 / 进行中蓝 / 未编译中性）+ `⋯` 描边方形 28×28。
 *
 * 状态文案**只反映真实编译状态**，无编译记录时显示「未编译」（不伪造成功）。
 *
 * 并发编译：同一入口已有编译在跑时，`files.compileNotice` 会给出一句人话提示
 * （「正在编译中，请稍候…」）——由 store 在收到后端 `COMPILE_IN_PROGRESS` 时置入，
 * 前端据此复用既有 run 继续轮询，**不白屏、不报裸错误、不静默丢弃**（宪法 §60/§61）。
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import type { Component } from 'vue'
import {
  CheckmarkCircleOutline,
  AlertCircleOutline,
  RefreshOutline,
  EllipsisHorizontalOutline,
  InformationCircleOutline,
  ChevronDownOutline,
} from '@vicons/ionicons5'
import type { CompileStatus } from '../api/types'
import { useRwFilesStore } from '../stores/files'

const props = withDefaults(
  defineProps<{
    engine?: string
    status?: CompileStatus | 'idle'
    /** 最近一次编译完成时刻（真实，未编译为 null） */
    lastCompiledAt?: string | null
    disabled?: boolean
  }>(),
  {
    engine: 'XeLaTeX',
    status: 'idle',
    lastCompiledAt: null,
    disabled: false,
  },
)

const emit = defineEmits<{ (e: 'recompile'): void }>()

/** 下拉弹层状态：`engine` / `auto` / `more` 三选一；点击其它区域关闭。 */
const openMenu = ref<'engine' | 'auto' | 'more' | null>(null)
function toggleMenu(key: 'engine' | 'auto' | 'more') {
  openMenu.value = openMenu.value === key ? null : key
}
function closeMenus(e: MouseEvent) {
  if (!openMenu.value) return
  const el = e.target as Node | null
  if (el && (el as HTMLElement).closest?.('.rw-ccb__dd')) return
  openMenu.value = null
}
onMounted(() => document.addEventListener('click', closeMenus))
onBeforeUnmount(() => document.removeEventListener('click', closeMenus))

/** 编译侧的临时人话提示（并发去重时由 store 置入；无提示为空串）。 */
const files = useRwFilesStore()
const noticeText = computed(() => files.compileNotice)

const statusText = computed(() => {
  switch (props.status) {
    case 'success':
    case 'completed':
      return props.lastCompiledAt ? `编译成功 ${props.lastCompiledAt}` : '编译成功'
    case 'failed':
      return '编译失败'
    case 'timeout':
      return '编译超时'
    case 'running':
    case 'queued':
      return '编译中…'
    case 'cancelled':
      return '已取消'
    default:
      return '未编译'
  }
})

const statusIcon = computed<Component>(() => {
  switch (props.status) {
    case 'success':
    case 'completed':
      return CheckmarkCircleOutline
    case 'failed':
    case 'timeout':
      return AlertCircleOutline
    case 'running':
    case 'queued':
      return RefreshOutline
    default:
      return InformationCircleOutline
  }
})

const statusClass = computed(() => {
  switch (props.status) {
    case 'success':
    case 'completed':
      return 'rw-ccb__status--completed'
    case 'failed':
    case 'timeout':
      return 'rw-ccb__status--failed'
    case 'running':
    case 'queued':
      return 'rw-ccb__status--running'
    default:
      return 'rw-ccb__status--idle'
  }
})
</script>

<style scoped>
.rw-ccb {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}
/* 下拉容器 + 弹层（engine / auto / more 共用） */
.rw-ccb__dd {
  position: relative;
  display: inline-flex;
}
.rw-ccb__pop {
  position: absolute;
  top: calc(100% + 6px);
  right: 0;
  z-index: 40;
  min-width: 220px;
  padding: 10px 12px;
  border: var(--rw-border-w) solid var(--rw-border);
  border-radius: var(--rw-radius-md);
  background: var(--rw-surface);
  box-shadow: var(--rw-shadow-pop);
  cursor: default;
}
.rw-ccb__pop-row {
  display: flex;
  gap: 8px;
  margin: 0 0 6px;
  font-size: var(--rw-fs-sm);
  color: var(--rw-text);
  white-space: nowrap;
}
.rw-ccb__pop-row:last-child {
  margin-bottom: 0;
  color: var(--rw-text-3);
}
.rw-ccb__pop-k {
  flex: 0 0 56px;
  color: var(--rw-text-3);
}
.rw-ccb__select {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 28px;
  padding: 0 9px;
  border: var(--rw-border-w) solid var(--rw-border);
  border-radius: var(--rw-radius);
  background: var(--rw-surface);
  color: var(--rw-text);
  font-size: var(--rw-fs-sm);
  font-weight: var(--rw-fw-medium);
  cursor: pointer;
}
.rw-ccb__chev {
  width: 13px;
  height: 13px;
  color: var(--rw-text-2);
}
.rw-ccb__primary {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 28px;
  padding: 0 12px;
  border: none;
  border-radius: var(--rw-radius);
  background: var(--rw-primary);
  color: var(--rw-surface);
  font-size: var(--rw-fs-sm);
  font-weight: var(--rw-fw-semibold);
  cursor: pointer;
}
.rw-ccb__primary:hover {
  background: var(--rw-primary-hover);
}
.rw-ccb__ico {
  width: 14px;
  height: 14px;
}
.rw-ccb__status {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: var(--rw-fs-sm);
  font-weight: var(--rw-fw-medium);
  white-space: nowrap;
}
.rw-ccb__status-ico {
  width: 14px;
  height: 14px;
}
.rw-ccb__status--completed { color: var(--rw-success); }
.rw-ccb__status--failed { color: var(--rw-danger); }
.rw-ccb__status--running,
.rw-ccb__status--queued { color: var(--rw-primary); }
.rw-ccb__status--idle,
.rw-ccb__status--cancelled { color: var(--rw-text-3); }
/* 并发编译提示：中性小字，不抢状态主色（不白屏、不报裸错误）。 */
.rw-ccb__notice {
  font-size: var(--rw-fs-sm);
  font-weight: var(--rw-fw-medium);
  color: var(--rw-text-2);
  white-space: nowrap;
}
.rw-ccb__more {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border: var(--rw-border-w) solid var(--rw-border);
  border-radius: var(--rw-radius);
  background: var(--rw-surface);
  color: var(--rw-text-2);
  cursor: pointer;
}
/* §1.2 顶栏 `⋯` 图标 16px（描边方形按钮 28×28） */
.rw-ccb__more :deep(svg) {
  width: 16px;
  height: 16px;
}
.rw-ccb__select:disabled,
.rw-ccb__primary:disabled,
.rw-ccb__more:disabled {
  opacity: .5;
  cursor: not-allowed;
}
</style>
