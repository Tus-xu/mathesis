<template>
  <div class="hp-wrap">
    <div class="hp-grid">
      <div v-for="p in items" :key="p.localId" class="hp-item" :class="'st-' + p.status">
        <img v-if="p._url" :src="p._url" :alt="p.filename" @click="openLightbox(p._url)" />
        <div v-else class="hp-ic">📷</div>
        <div class="hp-mask" v-if="p.status === 'uploading' || p.status === 'parsing'">
          <div class="hp-spinner"></div>
          <div class="hp-ph">{{ p.status === 'uploading' ? `上传 ${p.progress}%` : '识别中…' }}</div>
        </div>
        <div v-else-if="p.status === 'failed'" class="hp-mask err" @click="retry(p)">
          <div>❌ 失败</div>
          <div class="hp-ph">点击重试</div>
        </div>
        <div class="hp-bar">
          <span class="hp-st" :class="'c-' + p.status">
            {{ p.status === 'ready' ? '✓ 已识别' : p.status === 'photo' ? '📷 照片' : '' }}
          </span>
          <button type="button" class="hp-x" title="移除" @click="removeItem(p)">×</button>
        </div>
        <div v-if="p.ocr_text" class="hp-ocr">
          <div class="hp-ocr-t">{{ p.ocr_text }}</div>
          <button type="button" class="hp-fill" @click="$emit('ocr', p.ocr_text)">填入答案</button>
        </div>
      </div>
    </div>

    <div v-if="!readonly" class="hp-actions">
      <button type="button" class="secondary hp-add" :disabled="busy || items.length >= max" @click="pick">
        {{ items.length >= max ? `最多 ${max} 张` : busy ? '处理中…' : '📷 添加解答照片（可多张）' }}
      </button>
      <span class="hp-hint">支持手写作答拍照，自动识别为公式/文字并可回填答案；OCR 失败仍可提交，由老师复核。</span>
      <input :ref="setInput" type="file" accept="image/*" multiple hidden @change="onPicked" />
    </div>
  </div>
</template>

<script setup>
import { nextTick, ref } from 'vue'
import { filesApi } from '@/api'
import { compressImage, putPresigned, sha256Hex } from '@/components/chat/useFileUpload'
import { openLightbox } from '@/utils/lightbox'

const props = defineProps({
  modelValue: { type: Array, default: () => [] },
  max: { type: Number, default: 3 },
  readonly: { type: Boolean, default: false },
})
const emit = defineEmits(['update:modelValue', 'ocr'])

const POLL_START_MS = 2000
const POLL_MAX_MS = 8000
const TIMEOUT_MS = 180000  // MiMo 对模糊/低光图可超 90s（实测），90s 会把 eventual-success 当超时
let seq = 0

const items = ref([])
const busy = ref(false)
const inputEl = ref(null)
function setInput(el) { inputEl.value = el }
function pick() { if (!busy.value) inputEl.value?.click() }

defineExpose({ pick })


function sync() {
  emit(
    'update:modelValue',
    items.value
      .filter((p) => p.fileId && p.status !== 'failed' && p.status !== 'removed')
      // status/ocr_text/error 随行下发：父组件渲染识别确认卡（S5），消费 file_id 的旧调用方不受影响
      .map((p) => ({ file_id: p.fileId, status: p.status, ocr_text: p.ocr_text || '', error: p.error || '' }))
  )
}

async function uploadOne(it) {
  try {
    const raw = it.file
    const file = await compressImage(raw)
    it.mime = file.type || 'image/jpeg'
    it.filename = file.name || 'homework.jpg'
    const sha256 = await sha256Hex(file)
    const init = await filesApi.uploadInit({
      filename: it.filename,
      mime: it.mime,
      size_bytes: file.size,
      sha256,
      multipart: false,
    })
    it.fileId = init.file_id
    if (!init.deduplicated) {
      it.status = 'uploading'
      it.progress = 0
      await putPresigned({ file, mime: it.mime, progress: 0 }, init.upload_url)
    }
    it.status = 'parsing'
    try {
      await filesApi.parse(it.fileId, { purpose: 'question_photo' })
    } catch (e) {
      if (e?.code !== 40901) throw e // 40901=解析中，属正常，直接转轮询
    }
    const detail = await pollDetail(it.fileId)
    if (detail.status === 'parsed') {
      it.status = 'ready'
      const text = (detail.assets || [])
        .filter((a) => a.asset_type === 'markdown' || a.asset_type === 'text')
        .sort((a, b) => (a.page_no || 0) - (b.page_no || 0))
        .map((a) => a.content || '')
        .join('\n')
        .trim()
      if (text) {
        it.ocr_text = text
        emit('ocr', text)
      }
    } else {
      it.status = 'photo'
    }
  } catch (e) {
    it.status = 'failed'
    it.error = e?.message || '上传失败'
  }
  sync()
  await nextTick()
  busy.value = false
}

async function pollDetail(fileId) {
  const started = Date.now()
  let delay = POLL_START_MS
  for (;;) {
    await new Promise((r) => setTimeout(r, delay))
    const d = await filesApi.detail(fileId)
    if (d.status === 'parsed' || d.status === 'failed') return d
    if (Date.now() - started > TIMEOUT_MS) return { status: 'failed', error: '识别超时' }
    delay = Math.min(POLL_MAX_MS, Math.round(delay * 1.5))
  }
}

async function onPicked(e) {
  const files = [...(e.target.files || [])]
  e.target.value = ''
  if (!files.length) return
  if (busy.value) return
  busy.value = true
  for (const raw of files) {
    if (items.value.length >= props.max) break
    const it = {
      localId: ++seq,
      file: raw,
      filename: raw.name,
      mime: raw.type || 'image/jpeg',
      status: 'uploading',
      progress: 0,
      fileId: null,
      ocr_text: '',
      _url: URL.createObjectURL(raw),
    }
    items.value.push(it)
    await uploadOne(it)
  }
  busy.value = false
}

function removeItem(p) {
  p.status = 'removed'
  if (p._url) URL.revokeObjectURL(p._url)
  items.value = items.value.filter((x) => x.status !== 'removed')
  sync()
}

function retry(p) {
  p.status = 'uploading'
  p.progress = 0
  uploadOne(p)
}
</script>

<style scoped>
.hp-wrap { margin-top: 10px; }
.hp-grid { display: flex; flex-wrap: wrap; gap: 10px; }
.hp-item {
  position: relative; width: 132px; border: 1px solid var(--line, #e5e7eb);
  border-radius: var(--radius-md, 10px); overflow: hidden; background: #fff;
}
.hp-item img { width: 132px; height: 88px; object-fit: cover; display: block; cursor: zoom-in; }
.hp-ic { width: 132px; height: 88px; display: flex; align-items: center; justify-content: center; font-size: 28px; color: var(--ink3, #9aa1ac); background: var(--brand-faint, #f6f9ff); }
.hp-mask {
  position: absolute; inset: 0; z-index: 2; display: flex; flex-direction: column;
  align-items: center; justify-content: center; gap: 6px; color: #fff;
  background: rgba(15, 15, 18, .62); font-size: 11.5px; font-weight: 600;
}
.hp-mask.err { cursor: pointer; background: rgba(170, 30, 30, .72); }
.hp-spinner {
  width: 18px; height: 18px; border-radius: 50%;
  border: 2px solid rgba(255, 255, 255, .35); border-top-color: #fff;
  animation: hp-spin .8s linear infinite;
}
@keyframes hp-spin { to { transform: rotate(360deg); } }
.hp-ph { font-size: 11px; opacity: .9; }
.hp-bar { display: flex; align-items: center; justify-content: space-between; padding: 4px 8px; }
.hp-st { font-size: 11px; font-weight: 700; }
.hp-st.c-ready { color: var(--ok-deep, #16a34a); }
.hp-st.c-photo { color: var(--ink2, #646a73); }
.hp-x { border: none; background: none; cursor: pointer; color: var(--ink3, #9aa1ac); font-size: 14px; padding: 0 2px; }
.hp-x:hover { color: var(--err, #dc2626); }
.hp-ocr { border-top: 1px dashed var(--line, #eef1f5); padding: 6px 8px; }
.hp-ocr-t {
  font-size: 11px; color: var(--ink2, #646a73); line-height: 1.5;
  max-height: 54px; overflow: hidden; display: -webkit-box;
  -webkit-line-clamp: 3; -webkit-box-orient: vertical;
}
.hp-fill { margin-top: 5px; font-size: 11px; font-weight: 700; color: var(--brand, #3b7bff); background: none; border: none; cursor: pointer; padding: 0; }
.hp-actions { display: flex; align-items: center; gap: 10px; margin-top: 10px; flex-wrap: wrap; }
.hp-add { font-size: 12px; }
.hp-hint { font-size: 11px; color: var(--ink3, #9aa1ac); }
</style>
