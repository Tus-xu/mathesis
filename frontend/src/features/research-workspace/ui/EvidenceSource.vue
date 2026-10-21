<template>
  <div class="rw-evidence" :class="{ 'rw-evidence--labeled': showLabel }">
    <span v-if="showLabel" class="rw-evidence__label">证据来源</span>
    <span class="rw-evidence__badge" aria-hidden="true">PDF</span>
    <button type="button" class="rw-evidence__link" :title="fullTitle" @click="emit('select', source)">
      {{ displayTitle }}
    </button>
    <span v-if="pageText" class="rw-evidence__page">{{ pageText }}</span>
  </div>
</template>

<script setup lang="ts">
/**
 * 证据来源行（VISUAL-SPEC §4.4「证据来源行」/ 宪法 §33 → Evidence-first）。
 *
 * 一行表达一条可回溯证据：`证据来源` 标签 + PDF 徽标 + 文献标题链接 + 右侧页码。
 * 点击链接 → 向上抛出 `select`，由面板/外壳调用阅读器 `定位原文`（宪法 §14）。
 * 证据完全来自后端返回的 `evidence[]`，前端**不臆造**文献信息。
 */
import { computed } from 'vue'
import type { EvidenceSource as EvidenceSourceType } from '../api/types'

const props = withDefaults(
  defineProps<{
    source: EvidenceSourceType
    /** 是否显示左侧「证据来源」标签（同组仅首行显示） */
    showLabel?: boolean
  }>(),
  { showLabel: false },
)

const emit = defineEmits<{ (e: 'select', source: EvidenceSourceType): void }>()

const authorLabel = computed(() => {
  const raw = String(props.source?.authors ?? '').trim()
  if (!raw) return ''
  const first = raw.split(/[,;]/)[0]?.trim() ?? ''
  return first
})

const yearLabel = computed(() => (props.source?.year ? String(props.source.year) : ''))

const displayTitle = computed(() => {
  const title = String(props.source?.title ?? '').trim() || '未命名来源'
  const prefix = [authorLabel.value, yearLabel.value ? `(${yearLabel.value})` : '']
    .filter(Boolean)
    .join(' ')
  return prefix ? `${prefix}. ${title}` : title
})

const fullTitle = computed(() => {
  const parts = [displayTitle.value]
  if (props.source?.section) parts.push(`§${props.source.section}`)
  if (props.source?.page) parts.push(`p.${props.source.page}`)
  return parts.join(' · ')
})

const pageText = computed(() => (props.source?.page ? `p. ${props.source.page}` : ''))
</script>

<style scoped>
.rw-evidence {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  font-size: var(--rw-fs-sm);
}
.rw-evidence__label {
  flex: 0 0 auto;
  /* 黄金图实测「证据来源」为深色（近 --rw-text），明显深于 VISUAL-SPEC §4.4 记的 --rw-text-3；
     按「黄金图 > 文档」取深色。 */
  color: var(--rw-text);
  font-size: var(--rw-fs-sm);
}
.rw-evidence__badge {
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
  letter-spacing: 0.02em;
}
.rw-evidence__link {
  flex: 1 1 auto;
  min-width: 0;
  padding: 0;
  border: none;
  background: none;
  text-align: left;
  font-size: var(--rw-fs-body);
  font-weight: var(--rw-fw-medium);
  color: var(--rw-primary);
  cursor: pointer;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.rw-evidence__link:hover {
  text-decoration: underline;
}
.rw-evidence__page {
  flex: 0 0 auto;
  color: var(--rw-text-2);
  font-size: var(--rw-fs-sm);
  font-variant-numeric: tabular-nums;
}

/* ── WRITE 314px 紧凑态：证据行允许折行、收紧间距（VISUAL-SPEC §1.5）── */
@container rw-agent (max-width: 400px) {
  .rw-evidence {
    flex-wrap: wrap;
    gap: 6px;
  }
  .rw-evidence__link {
    flex: 1 1 60%;
  }
}
</style>
