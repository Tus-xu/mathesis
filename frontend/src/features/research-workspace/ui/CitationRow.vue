<template>
  <div class="rw-cite" :class="{ 'rw-cite--unverified': unverified }">
    <span class="rw-cite__badge" aria-hidden="true">{{ unverified ? '?' : 'PDF' }}</span>
    <div class="rw-cite__main">
      <div class="rw-cite__head">
        <span class="rw-cite__title">{{ title }}</span>
        <StatusPill
          v-if="unverified"
          class="rw-cite__pill"
          tone="warning"
          :text="'未验证引用'"
          :icon="AlertCircle"
        />
        <span v-else-if="support" class="rw-cite__support">{{ support }}</span>
      </div>
      <p v-if="metaLine" class="rw-cite__meta">{{ metaLine }}</p>
      <p v-if="snippet" class="rw-cite__snippet">{{ snippet }}</p>
    </div>
    <span v-if="pageText" class="rw-cite__page">{{ pageText }}</span>
  </div>
</template>

<script setup lang="ts">
/**
 * 引用候选行（VISUAL-SPEC §4.4 / 宪法 §36「引用补全」+ §37「引用不许伪造」）。
 *
 * 科研人员判断「为什么这篇论文支持我的论述」所需的最小信息：
 * 文献、作者·年份、对应片段（snippet）、支持程度、来源页码。
 * **核验不到时显式标 `未验证引用`**，绝不伪装成真实来源（宪法 §37）。
 */
import { computed } from 'vue'
import { AlertCircle } from '@vicons/ionicons5'
import StatusPill from './StatusPill.vue'

export interface CitationItem {
  title?: string
  authors?: string
  year?: number
  page?: number
  section?: string
  snippet?: string
  support?: string
  unverified?: boolean
}

const props = withDefaults(defineProps<{ item: CitationItem }>(), { item: () => ({}) })

const title = computed(() => String(props.item.title ?? '').trim() || '未命名来源')
const unverified = computed(() => Boolean(props.item.unverified))

const metaLine = computed(() => {
  const authors = String(props.item.authors ?? '').trim()
  const year = props.item.year ? String(props.item.year) : ''
  const parts = [authors, year].filter(Boolean)
  if (props.item.section) parts.push(`§${props.item.section}`)
  return parts.join(' · ')
})

const snippet = computed(() => String(props.item.snippet ?? '').trim())
const support = computed(() => String(props.item.support ?? '').trim())
const pageText = computed(() => (props.item.page ? `p. ${props.item.page}` : ''))
</script>

<style scoped>
.rw-cite {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 8px;
  border: var(--rw-border-w) solid var(--rw-border);
  border-radius: var(--rw-radius-md);
  background: var(--rw-surface);
}
.rw-cite--unverified {
  border-color: var(--rw-warning-soft);
  background: var(--rw-surface-2);
}
.rw-cite__badge {
  flex: 0 0 auto;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 20px;
  border-radius: var(--rw-radius-sm);
  background: var(--rw-pdf-badge);
  color: var(--rw-surface);
  font-size: 9px;
  font-weight: var(--rw-fw-bold);
}
.rw-cite--unverified .rw-cite__badge {
  background: var(--rw-warning);
}
.rw-cite__main {
  flex: 1 1 auto;
  min-width: 0;
}
.rw-cite__head {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.rw-cite__title {
  min-width: 0;
  font-size: var(--rw-fs-body);
  font-weight: var(--rw-fw-medium);
  color: var(--rw-text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.rw-cite__pill {
  flex: 0 0 auto;
}
.rw-cite__support {
  flex: 0 0 auto;
  font-size: var(--rw-fs-sm);
  color: var(--rw-primary);
}
.rw-cite__meta {
  margin: 2px 0 0;
  font-size: var(--rw-fs-sm);
  color: var(--rw-text-2);
}
.rw-cite__snippet {
  margin: 4px 0 0;
  font-size: var(--rw-fs-sm);
  line-height: var(--rw-lh-body);
  color: var(--rw-text-2);
  overflow-wrap: anywhere;
}
.rw-cite__page {
  flex: 0 0 auto;
  font-size: var(--rw-fs-sm);
  color: var(--rw-text-3);
  font-variant-numeric: tabular-nums;
}
</style>
