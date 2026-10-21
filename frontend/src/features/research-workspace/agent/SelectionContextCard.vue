<template>
  <section v-if="selection" class="rw-selctx" aria-label="当前选中文本">
    <header class="rw-selctx__head">
      <span class="rw-selctx__quote" aria-hidden="true">“</span>
      <span class="rw-selctx__title">选中文本</span>
      <button
        v-if="sourceLabel"
        type="button"
        class="rw-selctx__source"
        title="回到原文位置"
        @click="emit('locate')"
      >
        {{ sourceLabel }}
      </button>
      <button type="button" class="rw-selctx__close" aria-label="清除选中上下文" @click="emit('clear')">
        <CloseOutline />
      </button>
    </header>

    <blockquote class="rw-selctx__body" :class="{ 'is-expanded': expanded }" @click="emit('locate')">
      <MarkdownView :text="selection" :zoomable="false" />
    </blockquote>
    <button v-if="isLong" type="button" class="rw-selctx__more" @click="expanded = !expanded">
      {{ expanded ? '收起' : '展开全文' }}
      <ChevronDownOutline :class="{ 'is-up': expanded }" />
    </button>

    <div class="rw-selctx__actions">
      <button type="button" class="rw-selctx__act rw-selctx__act--primary" @click="emit('judge')">
        <CheckmarkCircle />
        评判
      </button>
      <button type="button" class="rw-selctx__act" @click="emit('explain')">
        <ChatbubbleEllipsesOutline />
        解释
      </button>
      <button type="button" class="rw-selctx__act" @click="emit('translate')">
        <LanguageOutline />
        翻译
      </button>
      <button type="button" class="rw-selctx__act" @click="emit('note')">
        <CreateOutline />
        记笔记
      </button>
      <button type="button" class="rw-selctx__locate" title="回到原文" @click="emit('locate')">
        <LocateOutline />
      </button>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import {
  ChatbubbleEllipsesOutline,
  CheckmarkCircle,
  ChevronDownOutline,
  CloseOutline,
  CreateOutline,
  LanguageOutline,
  LocateOutline,
} from '@vicons/ionicons5'
import MarkdownView from '@/components/MarkdownView.vue'
import type { WorkspaceMode } from '../api/types'

const props = withDefaults(
  defineProps<{
    mode: WorkspaceMode
    selection: string
    paperTitle?: string
    page?: number | null
    section?: string | null
    filePath?: string | null
    line?: number | null
  }>(),
  { paperTitle: '', page: null, section: null, filePath: '', line: null },
)

const emit = defineEmits<{
  (e: 'judge'): void
  (e: 'explain'): void
  (e: 'translate'): void
  (e: 'note'): void
  (e: 'locate'): void
  (e: 'clear'): void
}>()

const expanded = ref(false)
watch(() => props.selection, () => { expanded.value = false })

const isLong = computed(() => (props.selection || '').length > 140)
const sourceLabel = computed(() => {
  if (props.mode === 'write') {
    if (!props.filePath) return ''
    return `${props.filePath.split('/').pop() || props.filePath}${props.line ? ` L${props.line}` : ''}`
  }
  const parts = []
  if (props.paperTitle) parts.push(props.paperTitle.length > 22 ? `${props.paperTitle.slice(0, 22)}…` : props.paperTitle)
  if (props.page) parts.push(`p.${props.page}`)
  if (props.section) parts.push(`§${props.section}`)
  return parts.join(' · ')
})
</script>

<style scoped>
.rw-selctx {
  margin: 10px 12px 0;
  padding: 10px 12px 11px;
  border: 1px solid var(--rw-primary-border, #c7d2fe);
  border-radius: 14px;
  background:
    linear-gradient(135deg, rgba(238, 242, 255, .85), rgba(255, 255, 255, .98) 55%),
    var(--rw-surface);
}
.rw-selctx__head { display: flex; align-items: center; gap: 6px; min-width: 0; }
.rw-selctx__quote { font: 900 20px Georgia, serif; color: var(--rw-primary); line-height: 1; }
.rw-selctx__title { font-size: 11px; font-weight: 800; color: var(--rw-primary); white-space: nowrap; }
.rw-selctx__source {
  min-width: 0; max-width: 58%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  border: 0; padding: 1px 7px; border-radius: 999px; background: rgba(255,255,255,.7);
  color: var(--rw-text-3); font: inherit; font-size: 9.5px; cursor: pointer;
}
.rw-selctx__source:hover { color: var(--rw-primary); }
.rw-selctx__close {
  margin-left: auto; width: 22px; height: 22px; flex: none; display: grid; place-items: center;
  border: 0; border-radius: 6px; background: transparent; color: var(--rw-text-3); cursor: pointer;
}
.rw-selctx__close:hover { background: var(--rw-surface-2); color: var(--rw-text); }
.rw-selctx__close :deep(svg) { width: 13px; height: 13px; }
.rw-selctx__body {
  margin: 8px 0 0; padding: 8px 10px; border-left: 3px solid var(--rw-primary);
  border-radius: 0 8px 8px 0; background: rgba(255,255,255,.8);
  font-size: 11.5px; line-height: 1.65; color: var(--rw-text-2); cursor: pointer;
  max-height: 76px; overflow: hidden; position: relative;
}
.rw-selctx__body.is-expanded { max-height: 260px; overflow: auto; }
.rw-selctx__body :deep(.md-body) { font-size: 11.5px; line-height: 1.65; }
.rw-selctx__body :deep(.md-body p) { margin: 0 0 4px; }
.rw-selctx__body :deep(.md-body p:last-child) { margin-bottom: 0; }
.rw-selctx__more {
  display: inline-flex; align-items: center; gap: 2px; margin-top: 3px; padding: 0;
  border: 0; background: none; color: var(--rw-primary); font: inherit; font-size: 9.5px; font-weight: 700; cursor: pointer;
}
.rw-selctx__more :deep(svg) { width: 11px; height: 11px; transition: transform .16s; }
.rw-selctx__more :deep(svg.is-up) { transform: rotate(180deg); }
.rw-selctx__actions { display: flex; align-items: center; gap: 5px; flex-wrap: wrap; margin-top: 9px; }
.rw-selctx__act {
  display: inline-flex; align-items: center; gap: 4px; height: 27px; padding: 0 9px;
  border: 1px solid var(--rw-border); border-radius: 999px; background: var(--rw-surface);
  color: var(--rw-text-2); font: inherit; font-size: 10.5px; font-weight: 700; cursor: pointer;
}
.rw-selctx__act:hover { border-color: var(--rw-primary); color: var(--rw-primary); }
.rw-selctx__act :deep(svg) { width: 13px; height: 13px; }
.rw-selctx__act--primary {
  border-color: transparent; background: var(--gradient-brand); color: #fff;
  box-shadow: 0 4px 12px -8px rgba(79, 70, 229, .6);
}
.rw-selctx__act--primary:hover { color: #fff; filter: brightness(1.04); }
.rw-selctx__locate {
  margin-left: auto; width: 27px; height: 27px; display: grid; place-items: center; flex: none;
  border: 1px solid var(--rw-border); border-radius: 8px; background: var(--rw-surface);
  color: var(--rw-text-3); cursor: pointer;
}
.rw-selctx__locate:hover { color: var(--rw-primary); border-color: var(--rw-primary); }
.rw-selctx__locate :deep(svg) { width: 13px; height: 13px; }
@container rw-agent (max-width: 400px) {
  .rw-selctx { margin: 8px 8px 0; padding: 9px 10px 10px; }
  .rw-selctx__source { max-width: 46%; }
  .rw-selctx__act { padding: 0 7px; }
}
</style>
