<template>
  <section class="rw-ovc" aria-label="AI 项目概览">
    <button
      type="button"
      class="rw-ovc__head"
      :aria-expanded="expanded"
      @click="expanded = !expanded"
    >
      <SparklesOutline class="rw-ovc__spark" aria-hidden="true" />
      <span class="rw-ovc__title">AI 项目概览</span>
      <ChevronForwardOutline class="rw-ovc__chev" :class="{ 'rw-ovc__chev--open': expanded }" aria-hidden="true" />
    </button>

    <p
      v-if="loading && !summary"
      class="rw-ovc__summary rw-ovc__summary--muted"
    >正在生成项目概览…</p>
    <!-- 点击头部展开/收起：展开时移除 2 行截断，显示完整 Project Memory Summary（真实行为） -->
    <p
      v-else-if="summary"
      class="rw-ovc__summary"
      :class="{ 'rw-ovc__summary--full': expanded }"
    >{{ summary }}</p>
    <p v-else class="rw-ovc__summary rw-ovc__summary--muted">{{ emptyText }}</p>

    <div class="rw-ovc__stats" role="list">
      <div v-for="(s, i) in stats" :key="s.key" class="rw-ovc__stat" role="listitem">
        <span v-if="i > 0" class="rw-ovc__divider" aria-hidden="true" />
        <span class="rw-ovc__num">{{ s.value }}</span>
        <span class="rw-ovc__label">{{ s.label }}</span>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
/**
 * AI 项目概览卡（VISUAL-SPEC §4.1-2）。
 *
 * - 头：✦ 图标 + `AI 项目概览` 14/600 主蓝 + 右侧 `›`；
 * - 正文：Project Memory Summary（**真实来自服务端**，非固定串 —— 宪法 §8/§70）；
 * - 统计条：4 列 `文献 / 草稿 / 笔记 / 数据`，数值为真实计数。
 *
 * 无真实数据时渲染诚实占位（「暂无项目数据」），**严禁**用固定文案冒充。
 */
import { computed, ref } from 'vue'
import { ChevronForwardOutline, SparklesOutline } from '@vicons/ionicons5'

const props = withDefaults(
  defineProps<{
    summary?: string
    stats?: { papers: number; drafts: number; notes: number; data: number } | null
    loading?: boolean
  }>(),
  { summary: '', stats: null, loading: false },
)

/** 头部点击 = 展开/收起完整概览（真实行为；此前是 noop 摆设，§70）。 */
const expanded = ref(false)

const emit = defineEmits<{ (e: 'open'): void }>()

const emptyText = computed(() =>
  props.loading ? '正在生成项目概览…' : '暂无项目数据。导入文献后将自动生成概览。',
)

const stats = computed(() => [
  { key: 'papers', label: '文献', value: props.stats?.papers ?? 0 },
  { key: 'drafts', label: '草稿', value: props.stats?.drafts ?? 0 },
  { key: 'notes', label: '笔记', value: props.stats?.notes ?? 0 },
  { key: 'data', label: '数据', value: props.stats?.data ?? 0 },
])
</script>

<style scoped>
.rw-ovc {
  padding: 12px 13px;
  border-radius: 16px;
  background:
    linear-gradient(135deg, rgba(238, 242, 255, .55), rgba(255, 255, 255, .96) 60%),
    var(--rw-surface);
  border: 1px solid var(--rw-border);
}
.rw-ovc__head {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding: 0;
  margin-bottom: 8px;
  border: none;
  background: transparent;
  cursor: pointer;
  text-align: left;
}
.rw-ovc__spark {
  width: 16px;
  height: 16px;
  color: var(--rw-primary);
  flex: 0 0 auto;
}
.rw-ovc__title {
  flex: 1 1 auto;
  font-size: var(--rw-fs-h3);
  font-weight: var(--rw-fw-semibold);
  color: var(--rw-primary);
}
.rw-ovc__chev {
  width: 14px;
  height: 14px;
  color: var(--rw-text-3);
  flex: 0 0 auto;
}
.rw-ovc__summary {
  margin: 0 0 10px;
  font-size: var(--rw-fs-sm);
  line-height: var(--rw-lh-body);
  color: var(--rw-text-2);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.rw-ovc__summary--muted {
  color: var(--rw-text-3);
}
/* 展开态：解除 2 行截断，显示完整概览 */
.rw-ovc__summary--full {
  display: block;
  -webkit-line-clamp: unset;
  line-clamp: unset;
}
.rw-ovc__chev--open {
  transform: rotate(90deg);
}
.rw-ovc__stats {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  align-items: center;
}
.rw-ovc__stat {
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 1px;
}
.rw-ovc__divider {
  position: absolute;
  left: 0;
  top: 2px;
  bottom: 2px;
  width: 1px;
  background: var(--rw-border);
}
.rw-ovc__num {
  font-size: var(--rw-fs-h2);
  font-weight: var(--rw-fw-bold);
  color: var(--rw-text);
  line-height: 1.2;
}
.rw-ovc__label {
  font-size: var(--rw-fs-xs);
  color: var(--rw-text-3);
}
</style>
