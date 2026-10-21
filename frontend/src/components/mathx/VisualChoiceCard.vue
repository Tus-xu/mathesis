<template>
  <div
    class="tv3-choice" :class="{ 'is-selected': selected }"
    :data-testid="testid"
    role="button" tabindex="0"
    @click="$emit('select')"
    @keydown.enter.prevent="$emit('select')"
    @keydown.space.prevent="$emit('select')"
  >
    <div class="tv3-choice__head">
      <span class="tv3-choice__badge" :style="badgeStyle">{{ badge }}</span>
      <span class="tv3-choice__name">{{ name }}</span>
      <span v-if="aiGenerated" class="tv3-ai-badge">AI 概念</span>
      <span v-if="selected" class="tv3-choice__check">✓</span>
    </div>

    <div class="tv3-choice__preview">
      <!-- 自渲染小样：封面概念图仅风格参考，内容页一律组件绘制（红线 R10） -->
      <slot name="preview">
        <div v-if="kind === 'deck'" class="mx-preview mx-preview--deck" :style="deckStyle">
          <div class="mx-preview__page mx-preview__page--cover" :style="coverPageStyle">
            <div class="mx-preview__deco" :style="{ borderColor: swatch.accent }" />
            <div class="mx-preview__cover-title" :style="{ color: swatch.primary }">{{ sampleText }}</div>
            <div class="mx-preview__cover-sub" />
          </div>
          <div class="mx-preview__page" :style="contentPageStyle">
            <div class="mx-preview__line" v-for="i in 3" :key="i" :style="{ width: i === 2 ? '58%' : '82%' }" />
            <div class="mx-preview__formula" :style="{ color: swatch.primary }">y²=2px</div>
            <svg class="mx-preview__geo" viewBox="0 0 60 34">
              <path d="M8 30 Q30 2 52 30" fill="none" :stroke="swatch.accent" stroke-width="2" />
              <line x1="4" y1="30" x2="56" y2="30" :stroke="swatch.primary" stroke-width="1" opacity="0.5" />
            </svg>
          </div>
        </div>

        <div v-else-if="kind === 'lesson'" class="mx-preview mx-preview--lesson">
          <div v-for="(s, i) in sections.slice(0, 4)" :key="i" class="mx-preview__sec">
            <span class="mx-preview__sec-no" :style="{ background: swatch.primary }">{{ i + 1 }}</span>
            <div class="mx-preview__sec-body">
              <div class="mx-preview__sec-name">{{ s }}</div>
              <div class="mx-preview__sec-bar" :style="{ background: swatch.accent, width: secWidth(i) }" />
            </div>
          </div>
        </div>

        <div v-else-if="kind === 'photo'" class="mx-preview mx-preview--photo">
          <div class="mx-preview__photo-doc">
            <div class="mx-preview__photo-line" v-for="i in 4" :key="i" :style="{ width: 40 + i * 12 + '%' }" />
            <div class="mx-preview__photo-mark" :style="{ borderColor: swatch.accent }" />
          </div>
          <svg class="mx-preview__arrow" viewBox="0 0 24 24"><path d="M4 12h14m-5-6 6 6-6 6" fill="none" stroke="currentColor" stroke-width="2" /></svg>
          <div class="mx-preview__slide-mini" :style="{ borderColor: swatch.primary }">
            <span class="mx-preview__slide-fx" :style="{ color: swatch.primary }">∑</span>
          </div>
        </div>

        <div v-else-if="kind === 'font'" class="mx-preview mx-preview--font">
          <div class="mx-preview__font-sample" :style="{ fontSize: fontPx, color: swatch.primary }">解：S=½ab·sinC</div>
          <div class="mx-preview__font-note">{{ note || `${fontPx}px` }}</div>
        </div>
      </slot>
    </div>

    <div v-if="pages?.length" class="tv3-choice__pages">
      <span v-for="(p, i) in pages" :key="i" class="tv3-choice__page" :class="{ 'is-first': i === 0 }" :style="pageStyle(p)" />
    </div>
    <div v-if="fit" class="tv3-choice__fit">适用：<b>{{ fit }}</b></div>
    <div v-if="note" class="tv3-choice__note">{{ note }}</div>
  </div>
</template>

<script setup lang="ts">
/**
 * VisualChoiceCard —— 可视化选择卡（SPEC §5.2/§5.9 模板选择；§5.10 拍照配置）
 * 预览一律自渲染（SVG/CSS 绘制样张），AI 生成图仅用于封面概念（红线 R10）。
 * 通用载体：课件模板 / 教案模板 / 拍照模式 / 字号档 / 图形重建候选。
 */
import { computed } from 'vue'

const props = withDefaults(defineProps<{
  selected?: boolean
  badge?: string
  name: string
  kind?: 'deck' | 'lesson' | 'photo' | 'font'
  swatch?: { bg: string; primary: string; accent: string; light?: boolean }
  sampleText?: string
  sections?: string[]
  pages?: number[]
  fit?: string
  note?: string
  fontPx?: number
  aiGenerated?: boolean
  testid?: string
}>(), {
  selected: false,
  badge: '',
  kind: 'deck',
  swatch: () => ({ bg: '#ffffff', primary: '#0f4787', accent: '#c99735', light: true }),
  sampleText: '椭圆及其标准方程',
  sections: () => [],
  pages: () => [],
  fontPx: 20,
  aiGenerated: false,
})

defineEmits<{ (e: 'select'): void }>()

const badgeStyle = computed(() => ({ background: props.swatch.primary }))
const deckStyle = computed(() => ({
  background: props.swatch.light ? '#f6f8fc' : '#0d1b2e',
}))
const coverPageStyle = computed(() => ({
  background: props.swatch.bg,
}))
const contentPageStyle = computed(() => ({
  background: props.swatch.light ? '#ffffff' : '#f4f6fa',
  borderColor: props.swatch.primary + '33',
}))
const secWidth = (i: number) => `${64 + ((i * 17) % 30)}%`
const pageStyle = (hue: number) => (props.pages && props.pages.length ? { background: `hsl(${(hue * 47 + 200) % 360} 38% 88%)` } : {})
</script>

<style scoped>
.mx-preview { width: 100%; height: 100%; display: flex; align-items: center; justify-content: center; gap: 6px; padding: 8px; box-sizing: border-box; }

/* deck：封面 + 内容页双页样张 */
.mx-preview--deck { gap: 7px; }
.mx-preview__page {
  height: 88%; aspect-ratio: 4 / 3; border-radius: 4px; position: relative;
  border: 1px solid rgba(10, 53, 104, 0.14); box-shadow: 0 2px 6px rgba(10, 53, 104, 0.1);
  display: flex; flex-direction: column; justify-content: center; align-items: center; gap: 4px;
  padding: 6px; overflow: hidden;
}
.mx-preview__page--cover { flex-shrink: 0; width: 44%; }
.mx-preview__deco { width: 34%; height: 3px; border-radius: 2px; background: transparent; border-top: 2.5px solid; }
.mx-preview__cover-title { font-size: 8.5px; font-weight: 700; text-align: center; line-height: 1.3; }
.mx-preview__cover-sub { width: 40%; height: 2px; border-radius: 1px; background: rgba(74, 85, 104, 0.25); }
.mx-preview__line { height: 2px; border-radius: 1px; background: rgba(74, 85, 104, 0.28); }
.mx-preview__formula { font-family: "Cambria Math", Georgia, serif; font-size: 9px; font-weight: 600; margin-top: 1px; }
.mx-preview__geo { width: 52%; height: auto; }

/* lesson：环节 schema 纵列 */
.mx-preview--lesson { flex-direction: column; justify-content: center; gap: 5px; }
.mx-preview__sec { display: flex; align-items: center; gap: 6px; width: 88%; }
.mx-preview__sec-no {
  width: 13px; height: 13px; border-radius: 4px; color: #fff;
  font-size: 8px; font-weight: 700; display: grid; place-items: center; flex-shrink: 0;
}
.mx-preview__sec-body { flex: 1; min-width: 0; }
.mx-preview__sec-name { font-size: 8.5px; color: #4a5568; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.mx-preview__sec-bar { height: 2px; border-radius: 1px; margin-top: 2px; opacity: 0.7; }

/* photo：原题照片 → 结构化页 */
.mx-preview--photo { gap: 5px; }
.mx-preview__photo-doc {
  width: 34%; height: 82%; border-radius: 4px; background: #fdf9ef;
  border: 1px solid #e5d9bd; position: relative; padding: 7px 5px;
  display: flex; flex-direction: column; gap: 3px; transform: rotate(-3deg);
}
.mx-preview__photo-line { height: 1.6px; border-radius: 1px; background: #c9b98d; }
.mx-preview__photo-mark {
  position: absolute; inset: 4px; border: 1.5px dashed; border-radius: 3px; opacity: 0.6;
}
.mx-preview__arrow { width: 16px; height: 16px; color: var(--tv3-ink3); flex-shrink: 0; }
.mx-preview__slide-mini {
  width: 40%; height: 82%; border-radius: 4px; background: #fff;
  border: 1.5px solid; display: grid; place-items: center;
}
.mx-preview__slide-fx { font-family: Georgia, serif; font-size: 13px; font-weight: 700; }

/* font：字号档样张 */
.mx-preview--font { flex-direction: column; gap: 4px; }
.mx-preview__font-sample { font-family: "Cambria Math", Georgia, serif; font-weight: 600; white-space: nowrap; }
.mx-preview__font-note { font-size: 9.5px; color: var(--tv3-ink3); font-family: var(--tv3-font-num); }

.tv3-choice__check {
  margin-left: auto; width: 18px; height: 18px; border-radius: 50%;
  background: var(--tv3-gold); color: #fff; font-size: 11px; font-weight: 700;
  display: grid; place-items: center;
}
</style>
