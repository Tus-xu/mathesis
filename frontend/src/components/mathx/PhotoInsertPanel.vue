<template>
  <div v-if="open" class="mxd-photo" data-testid="mxd-photo" @click.self="close">
    <div class="mxd-photo__panel">
      <div class="mxd-photo__head">
        <span style="font-weight: 700; font-size: 14.5px">📷 拍照插入</span>
        <span class="tv3-tag tv3-tag--gold" style="font-size: 11px">扫描增强 · 三选一处理</span>
        <div class="tv3-card__spacer" />
        <button class="tv3-btn tv3-btn--sm" @click="close">×</button>
      </div>

      <!-- 未上传：拖放/选择 -->
      <div
        v-if="!photo" class="mxd-photo__drop" data-testid="mxd-photo-drop"
        @dragover.prevent="dragOver = true" @dragleave="dragOver = false" @drop.prevent="onDrop"
        @click="fileEl?.click()"
      >
        <div style="font-size: 30px">📱</div>
        <div style="font-size: 13.5px; font-weight: 600; margin-top: 6px">点击或拖入一张手写/拍照图片</div>
        <div style="font-size: 12px; color: var(--tv3-ink3); margin-top: 3px">公式、图形、例题手稿均可 · 增强后三选一插入当前页</div>
      </div>
      <template v-else>
        <div class="mxd-photo__stage">
          <div class="mxd-photo__preview">
            <div class="mxd-photo__cvwrap">
              <img :src="previewSrc" alt="扫描增强预览" class="mxd-photo__cv" data-testid="mxd-photo-preview">
              <div v-if="enhancing" class="mxd-photo__spinner">增强中…</div>
            </div>
            <!-- 扫描增强参数（对标扫描王） -->
            <div class="mxd-photo__params">
              <label class="mx-slider"><span class="mx-slider__label">亮度</span>
                <input type="range" min="-40" max="40" step="1" v-model="params.brightness" data-testid="mxd-photo-bright">
                <span class="mx-slider__value">{{ params.brightness }}</span>
              </label>
              <label class="mx-slider"><span class="mx-slider__label">对比度</span>
                <input type="range" min="-40" max="100" step="1" v-model="params.contrast" data-testid="mxd-photo-contrast">
                <span class="mx-slider__value">{{ params.contrast }}</span>
              </label>
              <label class="mx-slider"><span class="mx-slider__label">去阴影</span>
                <input type="range" min="0" max="100" step="1" :value="Math.round((params.strength || 0) * 100)"
                  data-testid="mxd-photo-strength" @input="params.strength = Number(($event.target as HTMLInputElement).value) / 100">
                <span class="mx-slider__value">{{ Math.round((params.strength || 0) * 100) }}%</span>
              </label>
              <label style="display: inline-flex; gap: 6px; align-items: center; font-size: 12.5px; margin-top: 4px">
                <input type="checkbox" v-model="params.whiten" data-testid="mxd-photo-whiten" style="accent-color: var(--tv3-gold)"> 提白纸背（去灰底）
              </label>
              <div style="display:flex; gap:6px; margin-top: 6px">
                <button class="tv3-btn tv3-btn--sm" data-testid="mxd-photo-reset" @click="resetParams">重置</button>
                <button class="tv3-btn tv3-btn--sm" @click="changePhoto">换一张</button>
              </div>
            </div>
          </div>

          <!-- 右侧：识别结果 / 三选 -->
          <div class="mxd-photo__actions">
            <!-- 识别为公式 → 必须先审查（红线 R2） -->
            <template v-if="recogState === 'input'">
              <div class="tv3-form-label">处理方式三选一</div>
              <div class="mxd-photo__choice" data-testid="mxd-photo-choice" @click="choose('image')">
                <div class="mxd-photo__choice-icon">🖼️</div>
                <div style="flex:1"><b>图片素材</b><div style="font-size:11.5px;color:var(--tv3-ink3)">增强后的清晰图片插入，可缩放摆位，不转文字</div></div>
              </div>
              <div class="mxd-photo__choice" data-testid="mxd-photo-formula" @click="recognize">
                <div class="mxd-photo__choice-icon">🧮</div>
                <div style="flex:1"><b>识别为公式</b><div style="font-size:11.5px;color:var(--tv3-ink3)">AI 识别成可编辑公式，进编辑器审查后插入</div></div>
                <span v-if="recognizing" class="tv3-tag tv3-tag--ai">识别中…</span>
              </div>
              <div class="mxd-photo__choice" data-testid="mxd-photo-original" @click="choose('anchorPhoto')">
                <div class="mxd-photo__choice-icon">✍️</div>
                <div style="flex:1"><b>手写原样</b><div style="font-size:11.5px;color:var(--tv3-ink3)">原图直接插入并锚定，供对照</div></div>
              </div>
            </template>

            <template v-else-if="recogState === 'done'">
              <div class="tv3-form-label" style="display:flex;gap:6px;align-items:center">
                识别结果 · 审查后插入 <span class="tv3-tag tv3-tag--warn" title="原型为固定样例演示，未接入真实识别服务">识别演示（样例值）</span>
              </div>
              <MathField v-model="formula" ref="mf" :font-size="20" :testid="'mxd-photo-formula'" placeholder="在此修正识别结果…" />
              <div style="display:flex;gap:6px;justify-content:flex-end;margin-top:10px">
                <button class="tv3-btn tv3-btn--sm" data-testid="mxd-photo-reedit" @click="recogState = 'input'">← 换个处理</button>
                <button class="tv3-btn tv3-btn--sm tv3-btn--gold" data-testid="mxd-photo-insert-formula" @click="insertFormula">确认插入公式</button>
              </div>
            </template>
            <div v-else-if="recogState === 'err'" style="font-size:12.5px;color:var(--tv3-rose)">识别失败，请重试或改用图片素材</div>
          </div>
        </div>
      </template>

      <input ref="fileEl" type="file" accept="image/*" hidden @change="onFile">
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * PhotoInsertPanel —— 课件编辑器"拍照插入"（P2）
 * 扫描增强（scanEnhance，对标扫描王）→ 三选一：
 *  A 图片素材   → image 元素（增强后）
 *  B 识别为公式 → VLM mock → MathField 审查（红线 R2）→ formula 元素
 *  C 手写原样   → anchorPhoto 元素（原图锚定）
 */
import { computed, ref, watch } from 'vue'
import { v3Api } from '@/api/teacherV3'
import { enhanceSrcToDataUrl, SCAN_DEFAULTS, type ScanParams } from './scanEnhance'
import MathField from '@/components/mathx/MathField.vue'

type RecogState = 'input' | 'done' | 'err'

const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{
  (e: 'update:open', v: boolean): void
  (e: 'insert', payload: { kind: 'image' | 'formula' | 'anchorPhoto'; src?: string; latex?: string }): void
}>()

const dragOver = ref(false)
const fileEl = ref<HTMLInputElement | null>(null)
const photo = ref('')
const previewSrc = ref('')
const enhancing = ref(false)
const params = ref<ScanParams>({ ...SCAN_DEFAULTS })
const recogState = ref<RecogState>('input')
const recognizing = ref(false)
const formula = ref('')
const conf = ref(0)
const mf = ref<InstanceType<typeof MathField> | null>(null)

function close() { emit('update:open', false) }
function resetParams() { params.value = { ...SCAN_DEFAULTS } }
function changePhoto() { photo.value = ''; previewSrc.value = ''; recogState.value = 'input'; fileEl.value?.click() }

function onDrop(ev: DragEvent) {
  dragOver.value = false
  const f = ev.dataTransfer?.files?.[0]
  if (f?.type.startsWith('image/')) readFile(f)
}
function onFile(ev: Event) {
  const f = (ev.target as HTMLInputElement).files?.[0]
  if (f) readFile(f)
}
function readFile(f: File) {
  const rd = new FileReader()
  rd.onload = () => {
    if (typeof rd.result === 'string') {
      photo.value = rd.result
      previewSrc.value = rd.result
      recogState.value = 'input'
    }
  }
  rd.readAsDataURL(f)
}

// 参数变化 → 重新增强（防抖）
watch(
  [() => props.open, () => photo.value, () => params.value.brightness, () => params.value.contrast, () => params.value.strength, () => params.value.whiten],
  () => {
    if (!photo.value) return
    let t = 0
    if (typeof window !== 'undefined') {
      window.clearTimeout(t)
      t = window.setTimeout(apply, 160)
    } else apply()
  },
)
async function apply() {
  if (!photo.value) return
  try {
    enhancing.value = true
    previewSrc.value = await enhanceSrcToDataUrl(photo.value, params.value)
  } catch {
    /* canvas 不可用则保持原图 */
  } finally {
    enhancing.value = false
  }
}

function choose(kind: 'image' | 'anchorPhoto') {
  // 图片素材用增强后图；手写原样用原图
  const src = kind === 'image' && previewSrc.value ? previewSrc.value : photo.value
  emit('insert', { kind, src })
  close()
  resetParams()
}

async function recognize() {
  recognizing.value = true
  recogState.value = 'input'
  try {
    const r = await v3Api.recognition.photoToFormula({ src: photo.value })
    formula.value = r.data.latex
    conf.value = r.data.confidence
    recogState.value = 'done'
  } catch {
    recogState.value = 'err'
  } finally {
    recognizing.value = false
  }
}

function insertFormula() {
  emit('insert', { kind: 'formula', latex: formula.value || '\\,?' })
  close()
  resetParams()
}
</script>

<style scoped>
.mxd-photo { position: fixed; inset: 0; z-index: 400; background: rgba(10, 30, 58, 0.5); display: grid; place-items: center; }
.mxd-photo__panel { width: 860px; max-width: 94vw; max-height: 88vh; overflow-y: auto; background: #fff; border-radius: 16px; padding: 14px; }
.mxd-photo__head { display: flex; align-items: center; gap: 10px; margin-bottom: 12px; }
.mxd-photo__drop {
  border: 1.5px dashed var(--tv3-line); border-radius: 14px; padding: 46px 20px;
  display: grid; place-items: center; cursor: pointer; text-align: center; color: var(--tv3-ink3); transition: all .12s;
}
.mxd-photo__drop:hover { border-color: var(--tv3-gold); background: var(--tv3-gold-soft); }
.mxd-photo__stage { display: grid; grid-template-columns: 1.25fr 1fr; gap: 14px; }
.mxd-photo__preview { display: flex; flex-direction: column; gap: 10px; }
.mxd-photo__cvwrap { position: relative; border: 1px solid var(--tv3-line); border-radius: 12px; overflow: hidden; background: #f7f6f1; }
.mxd-photo__cv { width: 100%; height: 280px; object-fit: contain; display: block; }
.mxd-photo__spinner { position: absolute; inset: 0; display: grid; place-items: center; background: rgba(255,255,255,.55); font-size: 12.5px; color: var(--tv3-ink3); }
.mxd-photo__params { display: flex; flex-direction: column; background: var(--tv3-bg2); border: 1px solid var(--tv3-line); border-radius: 12px; padding: 10px 12px; }
.mxd-photo__actions { display: flex; flex-direction: column; gap: 8px; }
.mxd-photo__choice {
  display: flex; align-items: center; gap: 10px; padding: 10px 12px; border: 1px solid var(--tv3-line);
  border-radius: 12px; cursor: pointer; transition: all .12s;
}
.mxd-photo__choice:hover { border-color: var(--tv3-gold); background: var(--tv3-gold-soft); }
.mxd-photo__choice-icon { font-size: 20px; }
</style>