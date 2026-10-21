<script lang="ts">
/**
 * 教材封面占位（共用）——课堂设计右栏「教材依据」与教材与资料右栏「教材来源」唯一实现。
 *
 * 无真实封面资产（教材切片零图片资产，禁止造假）→ 规范封面占位：
 * 固定 3:4 竖版 + 浅蓝底 + 学科短名居中（如「数学」）；教材全名不放封面
 * （62px 宽放不下，塞进去会被压成细碎多行、观感像渲染故障），
 * 由卡片右侧文字区承载全名。
 */

/** 教材全名归一：`·` → 空格、去多余空白、去掉年份（如 2019）。 */
export function textbookDisplayName(raw?: string | null): string {
  const t = (raw || '').replace(/·/g, ' ').replace(/\s+/g, ' ').trim()
  if (!t) return ''
  return t.replace(/(?:19|20)\d{2}/g, ' ').replace(/\s+/g, ' ').trim()
}

/** 封面主视觉短名：优先学科短名（高中数学 → 数学）；取不到时退回教材名前 4 字。 */
export function textbookCoverName(raw?: string | null): string {
  const t = textbookDisplayName(raw)
  if (!t) return ''
  const subject = /(思想政治|语文|数学|英语|物理|化学|生物|政治|历史|地理)/.exec(t)
  if (subject) return subject[1]
  return t.replace(/\s+/g, '').slice(0, 4)
}
</script>

<script setup lang="ts">
import { computed } from 'vue'

const props = withDefaults(defineProps<{ name?: string | null; size?: 'md' | 'sm' | 'lg' }>(), {
  name: null,
  size: 'md',
})

const shortName = computed(() => textbookCoverName(props.name))
</script>

<template>
  <div class="pc-cover" :class="`pc-cover--${size}`" aria-hidden="true">
    <span class="pc-cover__name">{{ shortName || '—' }}</span>
  </div>
</template>

<style scoped>
.pc-cover {
  flex: none;
  box-sizing: border-box;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 6px 5px;
  border-radius: 6px;
  background: var(--pc-primary-soft); /* #EFF4FF */
}
/* md：对话流/证据面板通用（62×80）；sm：教材与资料右栏（56×74）；
   lg：课堂设计展开卡「教材依据」（R6-S3fix：黄金图 G23 硬数字 ≈88×118）。
   lg 为课堂设计专用变体——md 为多处共用尺寸，全局改大会连带破坏别屏。 */
.pc-cover--md {
  width: 62px;
  height: 80px;
}
.pc-cover--sm {
  width: 56px;
  height: 74px;
}
.pc-cover--lg {
  width: 88px;
  height: 118px;
  border-radius: 8px;
}
.pc-cover__name {
  font-size: 13px;
  font-weight: 700;
  line-height: 1.35;
  color: var(--pc-text-2);
  text-align: center;
  overflow-wrap: anywhere;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.pc-cover--lg .pc-cover__name {
  font-size: 16px;
}
</style>
