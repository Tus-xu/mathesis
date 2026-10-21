<script setup lang="ts">
/**
 * 教材切片插图（R4a）：真实图片渲染 + 失败降级占位。
 * - 图源走后端教材图资源（sliceMarkdown.sliceImageUrl）；
 * - 鉴权取图（Bearer，img 标签带不了 Authorization）→ fetch blob 渲染；
 * - 加载失败**不回退源码**（`![](images/…)` 永不上屏），只显示诚实占位与图题（G22）；
 * - **退化图元降级**（R4afix S7）：切片抽取会把行内小图标（如 37×39 实心圆点）当成插图，
 *   直渲会在正文里混入彩色圆盘。取图后按真实像素尺寸判退化（双维度 < 48px），
 *   一律降级为与加载失败一致的「插图暂不可用」占位，真实插图不受影响。
 */
import { computed, onUnmounted, ref, watch } from 'vue'
import { getAccessToken } from '@/api/authSession'
import { sliceImageUrl } from './sliceMarkdown'

const props = withDefaults(
  defineProps<{
    src: string | null | undefined
    caption?: string
    alt?: string
    compact?: boolean
  }>(),
  { caption: '', alt: '', compact: false },
)

const failed = ref(false)
const degenerate = ref(false)
const loadedSrc = ref<string | null>(null)
let objectUrl: string | null = null
const url = computed(() => sliceImageUrl(props.src))
const showImg = computed(() => !!loadedSrc.value && !failed.value && !degenerate.value)

watch(
  url,
  async (u) => {
    if (objectUrl) {
      URL.revokeObjectURL(objectUrl)
      objectUrl = null
    }
    loadedSrc.value = null
    failed.value = false
    degenerate.value = false
    if (!u) {
      failed.value = true
      return
    }
    try {
      const token = getAccessToken()
      const res = await fetch(u, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      })
      if (!res.ok) throw new Error(String(res.status))
      const blob = await res.blob()
      objectUrl = URL.createObjectURL(blob)
      loadedSrc.value = objectUrl
    } catch {
      failed.value = true
    }
  },
  { immediate: true },
)

/** 双维度 < 48px = 抽取退化图元（图标/圆点），不是教材插图。 */
function onImgLoad(e: Event) {
  const img = e.target as HTMLImageElement
  if (img.naturalWidth > 0 && img.naturalHeight > 0 && img.naturalWidth < 48 && img.naturalHeight < 48) {
    degenerate.value = true
  }
}

onUnmounted(() => {
  if (objectUrl) URL.revokeObjectURL(objectUrl)
})
</script>

<template>
  <figure class="pc-slice-fig" :class="{ 'pc-slice-fig--compact': compact }">
    <img
      v-if="showImg"
      class="pc-slice-fig__img"
      :src="loadedSrc!"
      :alt="alt || caption || '教材插图'"
      loading="lazy"
      decoding="async"
      @load="onImgLoad"
      @error="failed = true"
    />
    <div v-else class="pc-slice-fig__fallback" role="img" :aria-label="caption || '教材插图'">
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <rect x="3" y="4.5" width="18" height="15" rx="2.5" fill="none" stroke="currentColor" stroke-width="1.5" />
        <circle cx="8.6" cy="10" r="1.7" fill="none" stroke="currentColor" stroke-width="1.4" />
        <path d="M4 17.5 9.6 12l3.6 3.2 3-2.6 4 4.4" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" />
      </svg>
      <span>{{ failed || degenerate ? '插图暂不可用' : '插图' }}</span>
    </div>
    <figcaption v-if="caption" class="pc-slice-fig__caption">{{ caption }}</figcaption>
  </figure>
</template>

<style scoped>
.pc-slice-fig {
  margin: 12px 0 14px;
  text-align: center;
}
.pc-slice-fig__img {
  max-width: 100%;
  max-height: 360px;
  border: 1px solid var(--pc-line);
  border-radius: var(--pc-radius-btn);
  background: #fff;
}
.pc-slice-fig--compact .pc-slice-fig__img {
  max-height: 72px;
}
.pc-slice-fig__fallback {
  display: inline-flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 5px;
  min-width: 132px;
  padding: 13px 20px;
  /* 优雅占位（R4afix2）：浅灰斜体 + 细虚线描边框，不作实底生硬文本块 */
  border: 1px dashed var(--pc-line);
  border-radius: var(--pc-radius-btn);
  background: transparent;
  color: var(--pc-text-faint);
  font-size: 11.5px;
  font-style: italic;
  letter-spacing: 0.2px;
}
.pc-slice-fig--compact .pc-slice-fig__fallback {
  min-width: 76px;
  padding: 8px 11px;
}
.pc-slice-fig__fallback svg {
  width: 22px;
  height: 22px;
  opacity: 0.5;
}
.pc-slice-fig__caption {
  margin-top: 6px;
  font-size: 12px;
  color: var(--pc-text-muted);
}
</style>
