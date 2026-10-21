<template>
  <Teleport to="body">
    <transition name="psw-fade">
      <div v-if="visible" class="psw-mask" @click.self="handleClose">
        <transition name="psw-zoom">
          <div v-if="visible" class="psw-dialog" role="dialog" aria-modal="true" aria-label="拍照扫描入库">
            <header class="psw-header">
              <button class="psw-header__btn psw-header__btn--back" @click="handleBack" :disabled="step === 1">
                <span v-if="step > 1">←</span>
              </button>
              <div class="psw-steps">
                <div v-for="(s, i) in steps" :key="i" class="psw-steps__item">
                  <div class="psw-steps__dot" :class="stepClass(i + 1)">
                    <template v-if="step > i + 1">✓</template>
                    <template v-else>{{ i + 1 }}</template>
                  </div>
                  <span class="psw-steps__label" :class="{ 'is-active': step === i + 1, 'is-done': step > i + 1 }">
                    {{ s }}
                  </span>
                  <div v-if="i < steps.length - 1" class="psw-steps__bar" :class="{ 'is-done': step > i + 1 }" />
                </div>
              </div>
              <button class="psw-header__btn psw-header__btn--close" @click="handleClose" aria-label="关闭">×</button>
            </header>
            <div class="psw-body">
              <transition name="psw-slide-fade" mode="out-in">
                <div v-if="step === 1" key="step1" class="psw-step psw-step--choose">
                  <div class="psw-step__title">选择扫描方式</div>
                  <div class="psw-step__sub">将纸质文档快速数字化，支持智能增强</div>
                  <div class="psw-choose-grid">
                    <div class="psw-choose-card" :class="{ 'is-selected': scanMode === 'camera' }" @click="scanMode = 'camera'">
                      <div class="psw-choose-card__icon">📷</div>
                      <div class="psw-choose-card__title">拍照扫描</div>
                      <div class="psw-choose-card__desc">调用摄像头直接拍摄文档</div>
                      <div class="psw-choose-card__tags">
                        <span class="psw-tag psw-tag--gold">推荐</span>
                        <span class="psw-tag">实时边缘检测</span>
                      </div>
                    </div>
                    <div class="psw-choose-card" :class="{ 'is-selected': scanMode === 'album' }" @click="scanMode = 'album'">
                      <div class="psw-choose-card__icon">🖼️</div>
                      <div class="psw-choose-card__title">相册导入</div>
                      <div class="psw-choose-card__desc">从本地选择已有图片文件</div>
                      <div class="psw-choose-card__tags">
                        <span class="psw-tag">批量导入</span>
                        <span class="psw-tag">支持多选</span>
                      </div>
                    </div>
                  </div>
                  <div class="psw-tips">
                    <div class="psw-tips__item">
                      <span class="psw-tips__icon">📄</span>
                      支持 JPG / PNG 格式，最多 30 页
                    </div>
                    <div class="psw-tips__item psw-tips__item--edu">
                      <span class="psw-tips__icon">🎓</span>
                      教育特色：支持<span class="psw-tips__hl">试卷去手写</span>、<span class="psw-tips__hl">错题裁切</span>
                    </div>
                  </div>
                  <div class="psw-footer">
                    <button class="psw-btn psw-btn--gold psw-btn--lg" @click="goToStep(2)">
                      开始扫描
                    </button>
                  </div>
                </div>
                <div v-else-if="step === 2" key="step2" class="psw-step psw-step--capture">
                  <template v-if="scanMode === 'camera'">
                    <div class="psw-camera">
                      <div class="psw-camera__viewfinder">
                        <div class="psw-camera__scene">
                          <div class="psw-camera__doc-bg" />
                          <div class="psw-smart-border">
                            <div class="psw-smart-border__corner psw-smart-border__corner--tl" />
                            <div class="psw-smart-border__corner psw-smart-border__corner--tr" />
                            <div class="psw-smart-border__corner psw-smart-border__corner--bl" />
                            <div class="psw-smart-border__corner psw-smart-border__corner--br" />
                            <div class="psw-smart-border__line psw-smart-border__line--top" />
                            <div class="psw-smart-border__line psw-smart-border__line--right" />
                            <div class="psw-smart-border__line psw-smart-border__line--bottom" />
                            <div class="psw-smart-border__line psw-smart-border__line--left" />
                          </div>
                          <div class="psw-camera__hint">AI 已检测到文档边缘</div>
                        </div>
                        <div class="psw-camera__grid">
                          <div class="psw-camera__grid-line psw-camera__grid-line--v1" />
                          <div class="psw-camera__grid-line psw-camera__grid-line--v2" />
                          <div class="psw-camera__grid-line psw-camera__grid-line--h1" />
                          <div class="psw-camera__grid-line psw-camera__grid-line--h2" />
                        </div>
                      </div>
                      <div class="psw-camera__controls">
                        <div class="psw-camera__mode-switch">
                          <button class="psw-cam-btn" :class="{ 'is-active': captureMode === 'single' }" @click="captureMode = 'single'">单张</button>
                          <button class="psw-cam-btn" :class="{ 'is-active': captureMode === 'batch' }" @click="captureMode = 'batch'">批量</button>
                        </div>
                        <button class="psw-shutter-btn" @click="capturePhoto" :disabled="capturedPages.length >= 30">
                          <div class="psw-shutter-btn__inner" />
                        </button>
                        <button class="psw-camera__pages-btn" @click="showThumbnails = !showThumbnails">
                          <div class="psw-camera__pages-count">{{ capturedPages.length }}</div>
                          <div class="psw-camera__pages-label">页</div>
                        </button>
                      </div>
                      <transition name="psw-slide-up">
                        <div v-if="showThumbnails && capturedPages.length > 0" class="psw-thumb-bar">
                          <div class="psw-thumb-bar__title">已拍摄 <b>{{ capturedPages.length }}</b> 页</div>
                          <div class="psw-thumb-bar__list">
                            <div v-for="(p, i) in capturedPages" :key="i" class="psw-thumb-item">
                              <div class="psw-thumb-item__img" :style="{ background: p.color }" />
                              <div class="psw-thumb-item__num">{{ i + 1 }}</div>
                            </div>
                          </div>
                        </div>
                      </transition>
                    </div>
                  </template>
                  <template v-else>
                    <div class="psw-album">
                      <div class="psw-album__drop" @click="triggerFileInput">
                        <div class="psw-album__drop-icon">📁</div>
                        <div class="psw-album__drop-title">点击选择图片或拖拽到此处</div>
                        <div class="psw-album__drop-sub">支持 JPG / PNG，最多 30 张</div>
                        <input ref="fileInputRef" type="file" accept="image/*" multiple hidden @change="handleFileSelect">
                      </div>
                      <div v-if="importedPages.length > 0" class="psw-album__grid">
                        <div v-for="(p, i) in importedPages" :key="i" class="psw-album-item">
                          <div class="psw-album-item__img" :style="{ background: p.color }" />
                          <div class="psw-album-item__num">{{ i + 1 }}</div>
                          <button class="psw-album-item__remove" @click="removeImportedPage(i)">×</button>
                        </div>
                        <div class="psw-album-item psw-album-item--add" @click="triggerFileInput">
                          <span>+</span>
                          <em>添加</em>
                        </div>
                      </div>
                    </div>
                  </template>
                  <div class="psw-footer psw-footer--dark">
                    <button class="psw-btn psw-btn--ghost-light" @click="goToStep(1)">上一步</button>
                    <button class="psw-btn psw-btn--gold psw-btn--lg" :disabled="!canProceedFromStep2" @click="goToStep(3)">
                      下一步 · 增强
                    </button>
                  </div>
                </div>
                <div v-else-if="step === 3" key="step3" class="psw-step psw-step--enhance">
                  <div class="psw-enhance">
                    <div class="psw-enhance__preview">
                      <div class="psw-enhance__doc" :class="`is-${currentFilter}`">
                        <div class="psw-enhance__doc-inner" />
                        <div v-if="enhanceSettings.autoCrop" class="psw-enhance__badge psw-enhance__badge--tl">自动裁剪</div>
                        <div v-if="enhanceSettings.perspective" class="psw-enhance__badge psw-enhance__badge--tr">透视校正</div>
                      </div>
                    </div>
                    <div class="psw-enhance__section">
                      <div class="psw-enhance__section-title">智能增强</div>
                      <div class="psw-enhance__features">
                        <div class="psw-feature-item is-on">
                          <span class="psw-feature-item__icon">✅</span>
                          <span class="psw-feature-item__name">自动裁剪</span>
                          <span class="psw-feature-item__status">已开启</span>
                        </div>
                        <div class="psw-feature-item is-on">
                          <span class="psw-feature-item__icon">✅</span>
                          <span class="psw-feature-item__name">透视校正</span>
                          <span class="psw-feature-item__status">已开启</span>
                        </div>
                        <div class="psw-feature-item is-on">
                          <span class="psw-feature-item__icon">✅</span>
                          <span class="psw-feature-item__name">去阴影</span>
                          <span class="psw-feature-item__status">已开启</span>
                        </div>
                        <div class="psw-feature-item">
                          <span class="psw-feature-item__icon">🔄</span>
                          <span class="psw-feature-item__name">增亮模式</span>
                          <div class="psw-switch" :class="{ 'is-on': enhanceSettings.brighten }" @click="toggleEnhance('brighten')" />
                        </div>
                        <div class="psw-feature-item">
                          <span class="psw-feature-item__icon">🔄</span>
                          <span class="psw-feature-item__name">
                            去手写
                            <span class="psw-tag psw-tag--gold psw-tag--sm">推荐</span>
                          </span>
                          <div class="psw-switch" :class="{ 'is-on': enhanceSettings.removeHandwriting }" @click="toggleEnhance('removeHandwriting')" />
                        </div>
                        <div class="psw-feature-item">
                          <span class="psw-feature-item__icon">🔄</span>
                          <span class="psw-feature-item__name">
                            曲面矫正
                            <span class="psw-tag psw-tag--sm">书本扫描推荐</span>
                          </span>
                          <div class="psw-switch" :class="{ 'is-on': enhanceSettings.curveCorrection }" @click="toggleEnhance('curveCorrection')" />
                        </div>
                      </div>
                    </div>
                    <div class="psw-enhance__section">
                      <div class="psw-enhance__section-title">滤镜效果</div>
                      <div class="psw-filter-scroll">
                        <div v-for="f in filters" :key="f.key" class="psw-filter-item" :class="{ 'is-active': currentFilter === f.key }" @click="currentFilter = f.key">
                          <div class="psw-filter-item__thumb" :class="`is-${f.key}`" />
                          <span class="psw-filter-item__name">{{ f.label }}</span>
                        </div>
                      </div>
                    </div>
                    <div class="psw-enhance__section">
                      <div class="psw-enhance__section-title">手动调节</div>
                      <div class="psw-adjust-list">
                        <div class="psw-adjust-item">
                          <span class="psw-adjust-item__label">亮度</span>
                          <input type="range" min="0" max="100" v-model.number="adjustSettings.brightness" class="psw-range">
                          <span class="psw-adjust-item__value">{{ adjustSettings.brightness }}</span>
                        </div>
                        <div class="psw-adjust-item">
                          <span class="psw-adjust-item__label">对比度</span>
                          <input type="range" min="0" max="100" v-model.number="adjustSettings.contrast" class="psw-range">
                          <span class="psw-adjust-item__value">{{ adjustSettings.contrast }}</span>
                        </div>
                        <div class="psw-adjust-item">
                          <span class="psw-adjust-item__label">锐化</span>
                          <input type="range" min="0" max="100" v-model.number="adjustSettings.sharpness" class="psw-range">
                          <span class="psw-adjust-item__value">{{ adjustSettings.sharpness }}</span>
                        </div>
                      </div>
                    </div>
                  </div>
                  <div class="psw-footer">
                    <button class="psw-btn psw-btn--ghost" @click="goToStep(2)">上一步</button>
                    <button class="psw-btn psw-btn--gold psw-btn--lg" @click="goToStep(4)">
                      下一步 · 完成
                    </button>
                  </div>
                </div>
                <div v-else-if="step === 4" key="step4" class="psw-step psw-step--finish">
                  <div class="psw-finish">
                    <div class="psw-finish__head">
                      <div class="psw-finish__title">页面管理</div>
                      <div class="psw-finish__count">共 <b>{{ allPages.length }}</b> 页</div>
                    </div>
                    <div class="psw-page-grid">
                      <div v-for="(p, i) in allPages" :key="p.id" class="psw-page-card" :class="{ 'is-selected': selectedPageIndex === i }" @click="selectedPageIndex = i">
                        <div class="psw-page-card__img" :style="{ background: p.color }" />
                        <div class="psw-page-card__num">{{ i + 1 }}</div>
                        <div class="psw-page-card__actions">
                          <button class="psw-page-card__btn" title="旋转" @click.stop="rotatePage(i)">↻</button>
                          <button class="psw-page-card__btn" title="删除" @click.stop="deletePage(i)">🗑</button>
                        </div>
                        <div class="psw-page-card__order">{{ i + 1 }}</div>
                      </div>
                      <div class="psw-page-card psw-page-card--add" @click="addMorePages">
                        <span class="psw-page-card__add-icon">+</span>
                        <span class="psw-page-card__add-text">添加页面</span>
                      </div>
                    </div>
                    <div class="psw-finish__name">
                      <label class="psw-finish__name-label">文件名称</label>
                      <input v-model="docName" type="text" class="psw-input psw-input--lg" placeholder="请输入文件名称">
                    </div>
                  </div>
                  <div class="psw-footer">
                    <button class="psw-btn psw-btn--ghost" @click="goToStep(3)">上一步</button>
                    <button class="psw-btn psw-btn--gold psw-btn--lg" :disabled="allPages.length === 0" @click="handleComplete">
                      生成 PDF 并入库
                    </button>
                  </div>
                </div>
              </transition>
            </div>
          </div>
        </transition>
      </div>
    </transition>
  </Teleport>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'

defineProps<{
  visible: boolean
}>()

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'complete', data: { name: string; pages: number }): void
}>()

const step = ref(1)
const steps = ['选择', '拍摄', '增强', '完成']
const scanMode = ref<'camera' | 'album'>('camera')
const captureMode = ref<'single' | 'batch'>('batch')
const showThumbnails = ref(false)

const genMockPage = (idx: number) => ({
  id: `page-${Date.now()}-${idx}`,
  color: `linear-gradient(135deg, hsl(${200 + idx * 15}, 30%, 92%), hsl(${220 + idx * 10}, 25%, 85%))`,
  rotated: 0,
})

const capturedPages = ref<Array<{ id: string; color: string; rotated: number }>>([])
const importedPages = ref<Array<{ id: string; color: string; rotated: number }>>([])

const enhanceSettings = ref({
  autoCrop: true,
  perspective: true,
  deshadow: true,
  brighten: false,
  removeHandwriting: true,
  curveCorrection: false,
})

const filters = [
  { key: 'original', label: '原图' },
  { key: 'enhance', label: '增强' },
  { key: 'gray', label: '灰度' },
  { key: 'bw', label: '黑白' },
  { key: 'no-handwrite', label: '去手写' },
]
const currentFilter = ref('enhance')

const adjustSettings = ref({
  brightness: 50,
  contrast: 50,
  sharpness: 40,
})

const docName = ref('扫描文档_20260906')
const selectedPageIndex = ref(0)
const fileInputRef = ref<HTMLInputElement | null>(null)

const allPages = computed(() => {
  if (scanMode.value === 'camera') return capturedPages.value
  return importedPages.value
})

const canProceedFromStep2 = computed(() => {
  if (scanMode.value === 'camera') return capturedPages.value.length > 0
  return importedPages.value.length > 0
})

const stepClass = (s: number) => ({
  'is-active': step.value === s,
  'is-done': step.value > s,
})

const goToStep = (s: number) => {
  step.value = s
}

const handleBack = () => {
  if (step.value > 1) step.value--
}

const handleClose = () => {
  emit('close')
}

const capturePhoto = () => {
  if (capturedPages.value.length >= 30) return
  capturedPages.value.push(genMockPage(capturedPages.value.length))
  showThumbnails.value = true
}

const triggerFileInput = () => {
  fileInputRef.value?.click()
}

const handleFileSelect = (e: Event) => {
  const input = e.target as HTMLInputElement
  const files = input.files
  if (!files) return
  const count = Math.min(files.length, 30 - importedPages.value.length)
  for (let i = 0; i < count; i++) {
    importedPages.value.push(genMockPage(importedPages.value.length + i))
  }
  input.value = ''
}

const removeImportedPage = (idx: number) => {
  importedPages.value.splice(idx, 1)
}

const toggleEnhance = (key: keyof typeof enhanceSettings.value) => {
  enhanceSettings.value[key] = !enhanceSettings.value[key] as never
}

const rotatePage = (idx: number) => {
  const pages = scanMode.value === 'camera' ? capturedPages.value : importedPages.value
  pages[idx].rotated = (pages[idx].rotated + 90) % 360
}

const deletePage = (idx: number) => {
  const pages = scanMode.value === 'camera' ? capturedPages : importedPages
  pages.value.splice(idx, 1)
  if (selectedPageIndex.value >= pages.value.length) {
    selectedPageIndex.value = Math.max(0, pages.value.length - 1)
  }
}

const addMorePages = () => {
  step.value = 2
}

const handleComplete = () => {
  emit('complete', {
    name: docName.value || '扫描文档',
    pages: allPages.value.length,
  })
}
</script>

<style scoped>
.psw-mask {
  position: fixed;
  inset: 0;
  background: rgba(79, 70, 229, 0.45);
  backdrop-filter: blur(4px);
  z-index: 1000;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
  box-sizing: border-box;
}
.psw-dialog {
  width: 100%;
  max-width: 720px;
  min-width: 600px;
  max-height: 90vh;
  background: var(--tv3-card);
  border-radius: 16px;
  box-shadow: 0 24px 60px rgba(79, 70, 229, 0.25), 0 4px 12px rgba(79, 70, 229, 0.1);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.psw-header {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 14px 20px;
  border-bottom: 1px solid var(--tv3-line);
  background: var(--tv3-card);
  flex-shrink: 0;
}
.psw-header__btn {
  width: 32px;
  height: 32px;
  border-radius: 8px;
  border: none;
  background: transparent;
  color: var(--tv3-ink2);
  font-size: 16px;
  cursor: pointer;
  display: grid;
  place-items: center;
  flex-shrink: 0;
  transition: all 0.15s ease;
}
.psw-header__btn:hover:not(:disabled) {
  background: var(--tv3-bg2);
  color: var(--tv3-ink);
}
.psw-header__btn:disabled { opacity: 0.3; cursor: not-allowed; }
.psw-header__btn--close { font-size: 20px; line-height: 1; }

.psw-steps {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 0;
}
.psw-steps__item { display: flex; align-items: center; gap: 6px; }
.psw-steps__dot {
  width: 24px; height: 24px; border-radius: 50%;
  display: grid; place-items: center;
  font-size: 11.5px; font-weight: 700;
  background: var(--tv3-bg2); color: var(--tv3-ink3);
  border: 1.5px solid var(--tv3-line);
  font-family: var(--tv3-font-num);
  flex-shrink: 0;
  transition: all 0.25s ease;
}
.psw-steps__dot.is-active {
  background: linear-gradient(135deg, #06b6d4, #0891b2);
  border-color: var(--tv3-gold);
  color: #fff;
  box-shadow: 0 2px 8px rgba(6, 182, 212, 0.35);
}
.psw-steps__dot.is-done {
  background: var(--tv3-teal);
  border-color: var(--tv3-teal);
  color: #fff;
  font-size: 12px;
}
.psw-steps__label {
  font-size: 12px; color: var(--tv3-ink3);
  font-weight: 500; white-space: nowrap;
  transition: all 0.2s ease;
}
.psw-steps__label.is-active { color: var(--tv3-gold-deep); font-weight: 700; }
.psw-steps__label.is-done { color: var(--tv3-teal); }
.psw-steps__bar {
  width: 28px; height: 2px;
  background: var(--tv3-line);
  margin: 0 8px; flex-shrink: 0;
  transition: background 0.25s ease;
}
.psw-steps__bar.is-done { background: var(--tv3-teal); }

.psw-body {
  flex: 1;
  min-height: 0;
  overflow: hidden;
  position: relative;
}
.psw-step {
  height: 100%;
  display: flex;
  flex-direction: column;
  padding: 20px 24px;
  box-sizing: border-box;
  overflow-y: auto;
}
.psw-step__title {
  font-size: 18px; font-weight: 700;
  color: var(--tv3-ink);
  margin-bottom: 4px;
}
.psw-step__sub {
  font-size: 12.5px; color: var(--tv3-ink3);
  margin-bottom: 20px;
}

.psw-choose-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 14px;
  margin-bottom: 18px;
}
.psw-choose-card {
  border: 2px solid var(--tv3-line);
  border-radius: 12px;
  padding: 20px 16px;
  cursor: pointer;
  transition: all 0.2s ease;
  background: var(--tv3-card);
  position: relative;
}
.psw-choose-card:hover {
  border-color: var(--tv3-gold-border);
  background: linear-gradient(180deg, #fffdf6 0%, #fff 50%);
  transform: translateY(-1px);
  box-shadow: 0 4px 16px rgba(6, 182, 212, 0.12);
}
.psw-choose-card.is-selected {
  border-color: var(--tv3-gold);
  background: linear-gradient(180deg, #fff8e8 0%, #fff 55%);
  box-shadow: 0 4px 18px rgba(6, 182, 212, 0.18);
}
.psw-choose-card__icon { font-size: 32px; margin-bottom: 10px; }
.psw-choose-card__title {
  font-size: 15px; font-weight: 700;
  color: var(--tv3-ink); margin-bottom: 4px;
}
.psw-choose-card__desc {
  font-size: 12px; color: var(--tv3-ink3);
  margin-bottom: 10px;
}
.psw-choose-card__tags { display: flex; flex-wrap: wrap; gap: 5px; }

.psw-tag {
  display: inline-flex; align-items: center;
  padding: 2px 8px; border-radius: 999px;
  font-size: 10.5px; font-weight: 600;
  background: var(--tv3-slate-soft);
  color: var(--tv3-slate);
  border: 1px solid var(--tv3-line);
}
.psw-tag--gold {
  background: var(--tv3-gold-soft);
  color: var(--tv3-gold-deep);
  border-color: var(--tv3-gold-border);
}
.psw-tag--sm { padding: 1px 6px; font-size: 10px; }

.psw-tips {
  background: var(--tv3-bg2);
  border-radius: 10px;
  padding: 12px 14px;
  margin-bottom: 20px;
}
.psw-tips__item {
  display: flex; align-items: center; gap: 8px;
  font-size: 12.5px; color: var(--tv3-ink2);
  padding: 3px 0;
}
.psw-tips__item + .psw-tips__item { margin-top: 2px; }
.psw-tips__icon { font-size: 14px; flex-shrink: 0; }
.psw-tips__hl { color: var(--tv3-gold-deep); font-weight: 600; }

.psw-footer {
  margin-top: auto;
  padding-top: 16px;
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  flex-shrink: 0;
}
.psw-footer--dark {
  background: linear-gradient(180deg, transparent, rgba(0, 0, 0, 0.3) 30%);
  margin: 0 -24px -20px;
  padding: 24px 24px 20px;
}

.psw-btn {
  display: inline-flex; align-items: center; justify-content: center; gap: 6px;
  padding: 8px 18px; border-radius: 10px;
  border: 1px solid var(--tv3-line);
  background: var(--tv3-card);
  color: var(--tv3-ink2);
  font-size: 13px; font-weight: 500;
  cursor: pointer; transition: all 0.15s ease;
  white-space: nowrap;
}
.psw-btn:hover:not(:disabled) {
  border-color: var(--tv3-gold-border);
  color: var(--tv3-gold-deep);
}
.psw-btn:disabled { opacity: 0.45; cursor: not-allowed; }
.psw-btn--gold {
  background: linear-gradient(135deg, #06b6d4, #0891b2);
  border-color: var(--tv3-gold);
  color: #fff;
  box-shadow: 0 4px 14px rgba(6, 182, 212, 0.3);
  font-weight: 600;
}
.psw-btn--gold:hover:not(:disabled) {
  background: linear-gradient(135deg, #0891b2, #0e7490);
  color: #fff;
  box-shadow: 0 5px 18px rgba(6, 182, 212, 0.4);
  transform: translateY(-1px);
}
.psw-btn--lg { padding: 11px 24px; font-size: 14px; border-radius: 12px; }
.psw-btn--ghost { background: transparent; border-color: var(--tv3-line); }
.psw-btn--ghost-light {
  background: rgba(255, 255, 255, 0.1);
  border-color: rgba(255, 255, 255, 0.2);
  color: #e8ecf2;
}
.psw-btn--ghost-light:hover {
  background: rgba(255, 255, 255, 0.18);
  border-color: rgba(255, 255, 255, 0.35);
  color: #fff;
}

.psw-step--capture {
  background: #0d1117;
  padding: 0;
}
.psw-camera {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
}
.psw-camera__viewfinder {
  flex: 1;
  position: relative;
  background: #1a1f2e;
  margin: 16px;
  border-radius: 12px;
  overflow: hidden;
  min-height: 280px;
}
.psw-camera__scene {
  position: absolute;
  inset: 0;
  display: grid;
  place-items: center;
}
.psw-camera__doc-bg {
  width: 70%;
  height: 75%;
  background: linear-gradient(145deg, #f5f0e6 0%, #e8e0d0 50%, #d8cfbf 100%);
  border-radius: 4px;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.3);
  position: relative;
}
.psw-camera__doc-bg::before {
  content: '';
  position: absolute;
  inset: 12% 8%;
  background: repeating-linear-gradient(
    0deg,
    transparent 0px,
    transparent 14px,
    rgba(180, 160, 130, 0.15) 14px,
    rgba(180, 160, 130, 0.15) 15px
  );
}

.psw-smart-border {
  position: absolute;
  width: 72%;
  height: 77%;
  left: 14%;
  top: 11.5%;
  pointer-events: none;
  animation: psw-border-breath 2.4s ease-in-out infinite;
}
.psw-smart-border__corner {
  position: absolute;
  width: 20px;
  height: 20px;
  border-color: #10b981;
  border-style: solid;
  border-width: 0;
}
.psw-smart-border__corner--tl { top: 0; left: 0; border-top-width: 3px; border-left-width: 3px; border-top-left-radius: 4px; }
.psw-smart-border__corner--tr { top: 0; right: 0; border-top-width: 3px; border-right-width: 3px; border-top-right-radius: 4px; }
.psw-smart-border__corner--bl { bottom: 0; left: 0; border-bottom-width: 3px; border-left-width: 3px; border-bottom-left-radius: 4px; }
.psw-smart-border__corner--br { bottom: 0; right: 0; border-bottom-width: 3px; border-right-width: 3px; border-bottom-right-radius: 4px; }
.psw-smart-border__line {
  position: absolute;
  background: #10b981;
  opacity: 0.6;
}
.psw-smart-border__line--top { top: 0; left: 20px; right: 20px; height: 1.5px; }
.psw-smart-border__line--right { top: 20px; bottom: 20px; right: 0; width: 1.5px; }
.psw-smart-border__line--bottom { bottom: 0; left: 20px; right: 20px; height: 1.5px; }
.psw-smart-border__line--left { top: 20px; bottom: 20px; left: 0; width: 1.5px; }

@keyframes psw-border-breath {
  0%, 100% { opacity: 1; filter: drop-shadow(0 0 4px rgba(16, 185, 129, 0.4)); }
  50% { opacity: 0.7; filter: drop-shadow(0 0 12px rgba(16, 185, 129, 0.7)); }
}

.psw-camera__hint {
  position: absolute;
  bottom: 16px;
  left: 50%;
  transform: translateX(-50%);
  background: rgba(16, 185, 129, 0.9);
  color: #fff;
  font-size: 12px;
  font-weight: 600;
  padding: 5px 12px;
  border-radius: 999px;
  white-space: nowrap;
}

.psw-camera__grid {
  position: absolute;
  inset: 0;
  pointer-events: none;
}
.psw-camera__grid-line {
  position: absolute;
  background: rgba(255, 255, 255, 0.08);
}
.psw-camera__grid-line--v1 { left: 33.33%; top: 0; bottom: 0; width: 1px; }
.psw-camera__grid-line--v2 { left: 66.67%; top: 0; bottom: 0; width: 1px; }
.psw-camera__grid-line--h1 { top: 33.33%; left: 0; right: 0; height: 1px; }
.psw-camera__grid-line--h2 { top: 66.67%; left: 0; right: 0; height: 1px; }

.psw-camera__controls {
  display: flex;
  align-items: center;
  justify-content: space-around;
  padding: 14px 20px 18px;
  background: linear-gradient(180deg, transparent, rgba(0, 0, 0, 0.5));
}
.psw-camera__mode-switch {
  display: inline-flex;
  background: rgba(255, 255, 255, 0.1);
  border-radius: 999px;
  padding: 3px;
  gap: 2px;
}
.psw-cam-btn {
  padding: 6px 14px;
  border-radius: 999px;
  border: none;
  background: transparent;
  color: rgba(255, 255, 255, 0.6);
  font-size: 12.5px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.15s ease;
}
.psw-cam-btn.is-active {
  background: rgba(255, 255, 255, 0.95);
  color: #1a1f2e;
  font-weight: 600;
}

.psw-shutter-btn {
  width: 64px;
  height: 64px;
  border-radius: 50%;
  border: 3px solid #fff;
  background: transparent;
  cursor: pointer;
  display: grid;
  place-items: center;
  transition: all 0.15s ease;
  padding: 0;
}
.psw-shutter-btn:hover:not(:disabled) {
  transform: scale(1.05);
  border-color: var(--tv3-gold);
}
.psw-shutter-btn:active:not(:disabled) { transform: scale(0.95); }
.psw-shutter-btn:disabled { opacity: 0.4; cursor: not-allowed; }
.psw-shutter-btn__inner {
  width: 48px;
  height: 48px;
  border-radius: 50%;
  background: #fff;
  transition: all 0.15s ease;
}
.psw-shutter-btn:hover:not(:disabled) .psw-shutter-btn__inner {
  background: var(--tv3-gold);
}

.psw-camera__pages-btn {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  min-width: 56px;
  height: 48px;
  border-radius: 12px;
  border: 1px solid rgba(255, 255, 255, 0.2);
  background: rgba(255, 255, 255, 0.08);
  color: #fff;
  cursor: pointer;
  transition: all 0.15s ease;
}
.psw-camera__pages-btn:hover {
  background: rgba(255, 255, 255, 0.15);
}
.psw-camera__pages-count {
  font-size: 16px;
  font-weight: 700;
  font-family: var(--tv3-font-num);
  line-height: 1.1;
}
.psw-camera__pages-label {
  font-size: 10.5px;
  color: rgba(255, 255, 255, 0.6);
}

.psw-thumb-bar {
  background: rgba(0, 0, 0, 0.6);
  padding: 10px 16px;
  border-top: 1px solid rgba(255, 255, 255, 0.1);
}
.psw-thumb-bar__title {
  font-size: 12px;
  color: rgba(255, 255, 255, 0.7);
  margin-bottom: 8px;
}
.psw-thumb-bar__title b {
  color: var(--tv3-gold);
  font-weight: 700;
}
.psw-thumb-bar__list {
  display: flex;
  gap: 6px;
  overflow-x: auto;
  padding-bottom: 4px;
}
.psw-thumb-item {
  position: relative;
  flex-shrink: 0;
  width: 44px;
  height: 60px;
  border-radius: 4px;
  overflow: hidden;
  border: 1.5px solid rgba(255, 255, 255, 0.2);
}
.psw-thumb-item__img { width: 100%; height: 100%; }
.psw-thumb-item__num {
  position: absolute;
  bottom: 2px; right: 3px;
  font-size: 10px; font-weight: 700;
  color: #fff;
  background: rgba(0, 0, 0, 0.5);
  border-radius: 3px;
  padding: 0 4px;
  font-family: var(--tv3-font-num);
}

.psw-album {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 20px 24px;
  box-sizing: border-box;
  overflow-y: auto;
}
.psw-album__drop {
  border: 2px dashed var(--tv3-line);
  border-radius: 12px;
  padding: 32px 20px;
  text-align: center;
  cursor: pointer;
  transition: all 0.2s ease;
  background: var(--tv3-bg2);
}
.psw-album__drop:hover {
  border-color: var(--tv3-gold);
  background: var(--tv3-gold-soft);
}
.psw-album__drop-icon { font-size: 36px; margin-bottom: 10px; }
.psw-album__drop-title {
  font-size: 14px; font-weight: 600;
  color: var(--tv3-ink); margin-bottom: 4px;
}
.psw-album__drop-sub {
  font-size: 12px; color: var(--tv3-ink3);
}
.psw-album__grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(90px, 1fr));
  gap: 10px;
}
.psw-album-item {
  position: relative;
  aspect-ratio: 3 / 4;
  border-radius: 8px;
  overflow: hidden;
  border: 1.5px solid var(--tv3-line);
  background: var(--tv3-bg2);
}
.psw-album-item__img { width: 100%; height: 100%; }
.psw-album-item__num {
  position: absolute;
  bottom: 4px; right: 4px;
  font-size: 10.5px; font-weight: 700;
  color: #fff;
  background: rgba(79, 70, 229, 0.7);
  border-radius: 4px;
  padding: 1px 6px;
  font-family: var(--tv3-font-num);
}
.psw-album-item__remove {
  position: absolute;
  top: 4px; right: 4px;
  width: 20px; height: 20px;
  border-radius: 50%;
  border: none;
  background: rgba(220, 38, 70, 0.9);
  color: #fff;
  font-size: 14px; line-height: 1;
  cursor: pointer;
  display: grid; place-items: center;
  padding: 0;
}
.psw-album-item__remove:hover { background: var(--tv3-rose); }
.psw-album-item--add {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 4px;
  cursor: pointer;
  border-style: dashed;
  color: var(--tv3-ink3);
  transition: all 0.15s ease;
}
.psw-album-item--add:hover {
  border-color: var(--tv3-gold);
  color: var(--tv3-gold-deep);
  background: var(--tv3-gold-soft);
}
.psw-album-item--add span { font-size: 24px; line-height: 1; }
.psw-album-item--add em {
  font-size: 11px; font-style: normal; font-weight: 500;
}

.psw-enhance {
  flex: 1;
  overflow-y: auto;
  margin-bottom: 16px;
}
.psw-enhance__preview {
  background: var(--tv3-bg2);
  border-radius: 12px;
  padding: 16px;
  margin-bottom: 18px;
  display: flex;
  justify-content: center;
}
.psw-enhance__doc {
  width: 60%;
  max-width: 240px;
  aspect-ratio: 3 / 4;
  border-radius: 4px;
  overflow: hidden;
  position: relative;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.12);
  transition: filter 0.3s ease;
}
.psw-enhance__doc-inner {
  width: 100%;
  height: 100%;
  background: linear-gradient(145deg, #f8f5ed 0%, #ebe4d4 100%);
  position: relative;
}
.psw-enhance__doc-inner::before {
  content: '';
  position: absolute;
  inset: 12% 10%;
  background: repeating-linear-gradient(
    0deg,
    transparent 0px,
    transparent 16px,
    rgba(150, 130, 100, 0.12) 16px,
    rgba(150, 130, 100, 0.12) 17px
  );
}
.psw-enhance__doc.is-original .psw-enhance__doc-inner { filter: none; }
.psw-enhance__doc.is-enhance .psw-enhance__doc-inner { filter: contrast(1.1) saturate(1.05); }
.psw-enhance__doc.is-gray .psw-enhance__doc-inner { filter: grayscale(1); }
.psw-enhance__doc.is-bw .psw-enhance__doc-inner { filter: grayscale(1) contrast(1.4) brightness(1.05); }
.psw-enhance__doc.is-no-handwrite .psw-enhance__doc-inner {
  filter: contrast(1.15) saturate(0.8);
  background: linear-gradient(145deg, #faf8f2 0%, #f0ead9 100%);
}
.psw-enhance__badge {
  position: absolute;
  font-size: 10px;
  font-weight: 600;
  color: #fff;
  background: rgba(16, 185, 129, 0.9);
  padding: 2px 6px;
  border-radius: 4px;
  z-index: 2;
}
.psw-enhance__badge--tl { top: 6px; left: 6px; }
.psw-enhance__badge--tr { top: 6px; right: 6px; }

.psw-enhance__section { margin-bottom: 18px; }
.psw-enhance__section-title {
  font-size: 13px;
  font-weight: 700;
  color: var(--tv3-ink);
  margin-bottom: 10px;
  display: flex;
  align-items: center;
  gap: 6px;
}
.psw-enhance__section-title::before {
  content: '';
  width: 3px;
  height: 14px;
  background: linear-gradient(180deg, var(--tv3-gold), var(--tv3-gold-deep));
  border-radius: 2px;
}

.psw-enhance__features {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 6px;
}
.psw-feature-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 9px 10px;
  border-radius: 10px;
  background: var(--tv3-bg2);
  font-size: 12.5px;
}
.psw-feature-item.is-on {
  background: rgba(16, 185, 129, 0.08);
  border: 1px solid rgba(16, 185, 129, 0.2);
}
.psw-feature-item__icon { font-size: 14px; flex-shrink: 0; }
.psw-feature-item__name {
  flex: 1;
  color: var(--tv3-ink2);
  font-weight: 500;
  display: flex;
  align-items: center;
  gap: 5px;
}
.psw-feature-item__status {
  font-size: 10.5px;
  color: var(--tv3-teal);
  font-weight: 600;
  flex-shrink: 0;
}

.psw-switch {
  width: 34px;
  height: 20px;
  border-radius: 999px;
  background: var(--tv3-line);
  position: relative;
  cursor: pointer;
  transition: all 0.2s ease;
  flex-shrink: 0;
}
.psw-switch::after {
  content: '';
  position: absolute;
  top: 2px; left: 2px;
  width: 16px; height: 16px;
  border-radius: 50%;
  background: #fff;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.15);
  transition: all 0.2s ease;
}
.psw-switch.is-on { background: var(--tv3-gold); }
.psw-switch.is-on::after { left: 16px; }

.psw-filter-scroll {
  display: flex;
  gap: 10px;
  overflow-x: auto;
  padding-bottom: 4px;
  margin: 0 -4px;
  padding-left: 4px;
  padding-right: 4px;
}
.psw-filter-item {
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  cursor: pointer;
  padding: 4px;
  border-radius: 8px;
  transition: all 0.15s ease;
}
.psw-filter-item:hover { background: var(--tv3-bg2); }
.psw-filter-item__thumb {
  width: 54px;
  height: 68px;
  border-radius: 6px;
  border: 2px solid transparent;
  background: linear-gradient(145deg, #f8f5ed 0%, #ebe4d4 100%);
  transition: all 0.2s ease;
  position: relative;
  overflow: hidden;
}
.psw-filter-item__thumb::before {
  content: '';
  position: absolute;
  inset: 15% 12%;
  background: repeating-linear-gradient(
    0deg,
    transparent 0px,
    transparent 8px,
    rgba(150, 130, 100, 0.15) 8px,
    rgba(150, 130, 100, 0.15) 9px
  );
}
.psw-filter-item.is-active .psw-filter-item__thumb {
  border-color: var(--tv3-gold);
  box-shadow: 0 2px 8px rgba(6, 182, 212, 0.3);
}
.psw-filter-item__thumb.is-original { filter: none; }
.psw-filter-item__thumb.is-enhance { filter: contrast(1.1) saturate(1.05); }
.psw-filter-item__thumb.is-gray { filter: grayscale(1); }
.psw-filter-item__thumb.is-bw { filter: grayscale(1) contrast(1.4) brightness(1.05); }
.psw-filter-item__thumb.is-no-handwrite {
  filter: contrast(1.15) saturate(0.8);
  background: linear-gradient(145deg, #faf8f2 0%, #f0ead9 100%);
}
.psw-filter-item__name {
  font-size: 11.5px;
  color: var(--tv3-ink3);
  font-weight: 500;
}
.psw-filter-item.is-active .psw-filter-item__name {
  color: var(--tv3-gold-deep);
  font-weight: 600;
}

.psw-adjust-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.psw-adjust-item {
  display: flex;
  align-items: center;
  gap: 10px;
}
.psw-adjust-item__label {
  font-size: 12.5px;
  color: var(--tv3-ink2);
  width: 50px;
  flex-shrink: 0;
  font-weight: 500;
}
.psw-adjust-item__value {
  font-size: 12px;
  font-weight: 600;
  color: var(--tv3-gold-deep);
  width: 30px;
  text-align: right;
  font-family: var(--tv3-font-num);
  flex-shrink: 0;
}
.psw-range {
  flex: 1;
  -webkit-appearance: none;
  appearance: none;
  height: 4px;
  border-radius: 999px;
  background: var(--tv3-line);
  outline: none;
}
.psw-range::-webkit-slider-thumb {
  -webkit-appearance: none;
  appearance: none;
  width: 16px;
  height: 16px;
  border-radius: 50%;
  background: linear-gradient(135deg, #06b6d4, #0891b2);
  cursor: pointer;
  box-shadow: 0 2px 6px rgba(6, 182, 212, 0.4);
  border: 2px solid #fff;
}
.psw-range::-moz-range-thumb {
  width: 16px;
  height: 16px;
  border-radius: 50%;
  background: linear-gradient(135deg, #06b6d4, #0891b2);
  cursor: pointer;
  box-shadow: 0 2px 6px rgba(6, 182, 212, 0.4);
  border: 2px solid #fff;
}

.psw-finish {
  flex: 1;
  overflow-y: auto;
  margin-bottom: 16px;
}
.psw-finish__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}
.psw-finish__title {
  font-size: 15px;
  font-weight: 700;
  color: var(--tv3-ink);
}
.psw-finish__count {
  font-size: 12.5px;
  color: var(--tv3-ink3);
}
.psw-finish__count b {
  color: var(--tv3-gold-deep);
  font-weight: 700;
  font-family: var(--tv3-font-num);
}

.psw-page-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(100px, 1fr));
  gap: 10px;
  margin-bottom: 18px;
}
.psw-page-card {
  position: relative;
  aspect-ratio: 3 / 4;
  border-radius: 10px;
  overflow: hidden;
  border: 2px solid var(--tv3-line);
  background: var(--tv3-bg2);
  cursor: pointer;
  transition: all 0.15s ease;
}
.psw-page-card:hover {
  border-color: var(--tv3-gold-border);
  transform: translateY(-1px);
}
.psw-page-card.is-selected {
  border-color: var(--tv3-gold);
  box-shadow: 0 4px 14px rgba(6, 182, 212, 0.25);
}
.psw-page-card__img { width: 100%; height: 100%; }
.psw-page-card__num {
  position: absolute;
  bottom: 6px; left: 6px;
  font-size: 11px; font-weight: 700;
  color: #fff;
  background: rgba(79, 70, 229, 0.75);
  border-radius: 4px;
  padding: 1px 7px;
  font-family: var(--tv3-font-num);
  z-index: 2;
}
.psw-page-card__actions {
  position: absolute;
  top: 6px; right: 6px;
  display: flex;
  gap: 4px;
  opacity: 0;
  transition: opacity 0.15s ease;
  z-index: 2;
}
.psw-page-card:hover .psw-page-card__actions,
.psw-page-card.is-selected .psw-page-card__actions {
  opacity: 1;
}
.psw-page-card__btn {
  width: 24px;
  height: 24px;
  border-radius: 6px;
  border: none;
  background: rgba(255, 255, 255, 0.95);
  color: var(--tv3-ink2);
  font-size: 13px;
  cursor: pointer;
  display: grid;
  place-items: center;
  padding: 0;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.15);
}
.psw-page-card__btn:hover {
  background: #fff;
  color: var(--tv3-gold-deep);
}
.psw-page-card__order {
  position: absolute;
  top: 6px; left: 6px;
  width: 20px; height: 20px;
  border-radius: 50%;
  background: var(--tv3-gold);
  color: #fff;
  font-size: 11px;
  font-weight: 700;
  display: grid;
  place-items: center;
  font-family: var(--tv3-font-num);
  z-index: 2;
}

.psw-page-card--add {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  border-style: dashed;
  color: var(--tv3-ink3);
}
.psw-page-card--add:hover {
  border-color: var(--tv3-gold);
  color: var(--tv3-gold-deep);
  background: var(--tv3-gold-soft);
}
.psw-page-card__add-icon { font-size: 28px; line-height: 1; }
.psw-page-card__add-text { font-size: 12px; font-weight: 500; }

.psw-finish__name { margin-top: 8px; }
.psw-finish__name-label {
  display: block;
  font-size: 12px;
  font-weight: 600;
  color: var(--tv3-ink2);
  margin-bottom: 6px;
}
.psw-input {
  width: 100%;
  box-sizing: border-box;
  padding: 9px 12px;
  border-radius: 10px;
  border: 1px solid var(--tv3-line);
  background: var(--tv3-card);
  font-size: 13px;
  color: var(--tv3-ink);
  outline: none;
  transition: border 0.15s ease, box-shadow 0.15s ease;
  font-family: inherit;
}
.psw-input:focus {
  border-color: var(--tv3-gold);
  box-shadow: 0 0 0 3px rgba(6, 182, 212, 0.12);
}
.psw-input--lg {
  padding: 11px 14px;
  font-size: 14px;
  border-radius: 12px;
}

.psw-fade-enter-active,
.psw-fade-leave-active {
  transition: opacity 0.25s ease;
}
.psw-fade-enter-from,
.psw-fade-leave-to {
  opacity: 0;
}
.psw-zoom-enter-active,
.psw-zoom-leave-active {
  transition: opacity 0.25s ease, transform 0.25s cubic-bezier(0.34, 1.56, 0.64, 1);
}
.psw-zoom-enter-from,
.psw-zoom-leave-to {
  opacity: 0;
  transform: scale(0.94) translateY(10px);
}
.psw-slide-fade-enter-active,
.psw-slide-fade-leave-active {
  transition: opacity 0.2s ease, transform 0.25s ease;
}
.psw-slide-fade-enter-from {
  opacity: 0;
  transform: translateX(16px);
}
.psw-slide-fade-leave-to {
  opacity: 0;
  transform: translateX(-16px);
}
.psw-slide-up-enter-active,
.psw-slide-up-leave-active {
  transition: opacity 0.2s ease, transform 0.25s ease;
}
.psw-slide-up-enter-from,
.psw-slide-up-leave-to {
  opacity: 0;
  transform: translateY(10px);
}

.psw-step::-webkit-scrollbar,
.psw-enhance::-webkit-scrollbar,
.psw-finish::-webkit-scrollbar,
.psw-album::-webkit-scrollbar {
  width: 6px;
}
.psw-step::-webkit-scrollbar-thumb,
.psw-enhance::-webkit-scrollbar-thumb,
.psw-finish::-webkit-scrollbar-thumb,
.psw-album::-webkit-scrollbar-thumb {
  background: var(--tv3-line);
  border-radius: 999px;
}
.psw-step::-webkit-scrollbar-thumb:hover,
.psw-enhance::-webkit-scrollbar-thumb:hover,
.psw-finish::-webkit-scrollbar-thumb:hover,
.psw-album::-webkit-scrollbar-thumb:hover {
  background: var(--tv3-ink4);
}
.psw-thumb-bar__list::-webkit-scrollbar,
.psw-filter-scroll::-webkit-scrollbar {
  height: 4px;
}
.psw-thumb-bar__list::-webkit-scrollbar-thumb {
  background: rgba(255, 255, 255, 0.2);
  border-radius: 999px;
}
.psw-filter-scroll::-webkit-scrollbar-thumb {
  background: var(--tv3-line);
  border-radius: 999px;
}
</style>


