<template>
  <Teleport to="body">
    <div v-if="open" class="doc-drawer" @click.self="$emit('close')">
      <aside class="doc-panel" role="dialog" aria-modal="true" :aria-label="`资料详情：${doc?.title || ''}`">
        <header class="dp-head">
          <div class="dp-title-wrap">
            <span class="dp-ic">{{ typeIcon(doc?.title) }}</span>
            <div class="dp-title-body">
              <h2 class="dp-title">{{ doc?.title || '资料详情' }}</h2>
              <div class="dp-sub">
                <span :class="['dp-status', statusClass(doc?.status)]">{{ statusZh(doc) }}</span>
                <span v-if="doc?.category" class="dp-cat">{{ doc.category }}</span>
                <span v-if="doc?.parse_engine" class="dp-engine">{{ engineZh(doc.parse_engine) }}</span>
              </div>
            </div>
          </div>
          <button type="button" class="dp-close" aria-label="关闭" @click="$emit('close')">✕</button>
        </header>

        <div class="dp-scroll">
          <div class="dp-meta-grid">
            <div><span>页数</span><b>{{ doc?.page_count ?? '—' }}</b></div>
            <div><span>可引用切片</span><b>{{ doc?.chunk_count ?? 0 }}</b></div>
            <div><span>上传时间</span><b>{{ fmtDate(doc?.created_at) }}</b></div>
            <div><span>分类</span>
              <select
                class="dp-cat-select" :value="doc?.category || '其他'"
                :disabled="busy || isActive(doc?.status)"
                @change="$emit('category-change', $event.target.value)"
              >
                <option v-for="c in CATEGORIES" :key="c" :value="c">{{ c }}</option>
              </select>
            </div>
          </div>

          <div v-if="doc?.status === 'failed'" class="dp-error">
            <b>解析失败</b>
            <p>{{ doc?.error || '未返回具体原因，可重新解析或删除后重传。' }}</p>
          </div>
          <div v-else-if="isActive(doc?.status)" class="dp-progress">
            <div class="dp-progress-row"><span>AI 正在解析并建立可引用索引…</span><b>{{ doc?.progress || 10 }}%</b></div>
            <div class="dp-bar"><i :style="{ width: (doc?.progress || 10) + '%' }"></i></div>
            <p>解析完成后即可在对话中引用这份资料，也可以直接生成练习。</p>
          </div>
          <div v-else-if="doc?.index_state === 'partial'" class="dp-partial">
            <b>已按原图保存</b>
            <p>这道题的 OCR 文字较少（{{ doc?.ocr_len ?? 0 }} 字），文字仅作检索辅助；请以原图为准。</p>
          </div>

          <section v-if="isImage" class="dp-image-wrap">
            <div v-if="imageUrl" class="dp-image" @click="openLightbox(imageUrl)">
              <img :src="imageUrl" alt="资料原图" loading="lazy" />
            </div>
            <div v-else class="dp-image-loading">原图加载中…</div>
          </section>

          <section class="dp-section">
            <div class="dp-section-head">
              <span>切片预览</span>
              <span v-if="chunkTotal" class="dp-section-note">共 {{ chunkTotal }} 片 · 仅展示前 {{ chunks.length }} 片</span>
            </div>
            <div v-if="chunksLoading" class="dp-chunk-loading">切片加载中…</div>
            <div v-else-if="chunkError" class="dp-chunk-error">
              {{ chunkError }}
              <button type="button" @click="loadChunks">重试</button>
            </div>
            <div v-else-if="chunks.length" class="dp-chunks">
              <article v-for="c in chunks" :key="c.id" class="dp-chunk">
                <div class="dp-chunk-index">#{{ Number(c.index) + 1 }} · {{ c.char_count }} 字</div>
                <MarkdownView :text="c.content" :zoomable="true" />
              </article>
            </div>
            <p v-else class="dp-empty-chunks">
              {{ doc?.status === 'ready' ? '这篇资料没有可引用切片（图片/扫描件以原图为准）。' : '解析完成后会在这里显示切片。' }}
            </p>
          </section>
        </div>

        <footer class="dp-actions">
          <button type="button" class="dp-btn dp-btn--primary" :disabled="!canCite" @click="ask('kb_qa')">
            📚 基于此资料提问
          </button>
          <button type="button" class="dp-btn" :disabled="!canCite" @click="ask('quiz_gen')">
            📝 生成练习
          </button>
          <button type="button" class="dp-btn" :disabled="busy || isActive(doc?.status)" @click="$emit('reparse')">
            ↻ 重新解析
          </button>
          <button type="button" class="dp-btn dp-btn--danger" :disabled="busy || isActive(doc?.status)" @click="$emit('delete')">
            删除
          </button>
        </footer>
      </aside>
    </div>
  </Teleport>
</template>

<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { filesApi, studentKbApi } from '@/api'
import { openLightbox } from '@/utils/lightbox'
import MarkdownView from '@/components/MarkdownView.vue'

const props = defineProps({
  open: { type: Boolean, default: false },
  doc: { type: Object, default: null },
  busy: { type: Boolean, default: false },
})
const emit = defineEmits(['close', 'reparse', 'delete', 'category-change', 'ask'])

const CATEGORIES = ['教材', '教辅', '笔记', '试卷', '题目', '其他']
const chunks = ref([])
const chunkTotal = ref(0)
const chunksLoading = ref(false)
const chunkError = ref('')
const imageUrl = ref('')

const isImage = computed(() => /\.(png|jpe?g)$/i.test(String(props.doc?.title || '')) || props.doc?.file_type === 'image')
const canCite = computed(() => props.doc?.status === 'ready' && Number(props.doc?.chunk_count || 0) > 0)
const isActive = (s) => ['pending', 'parsing', 'chunking', 'embedding'].includes(s)

function typeIcon(name = '') {
  const n = String(name).toLowerCase()
  if (n.endsWith('.pdf')) return '📕'
  if (/\.(png|jpe?g)$/.test(n)) return '🖼️'
  if (/\.(docx?|pptx?)$/.test(n)) return '📘'
  return '📄'
}
function statusZh(d) {
  return ({ ready: '✓ 可引用', pending: '排队中', parsing: '解析中', chunking: '切片中', embedding: '向量化中', failed: '✗ 解析失败' })[d?.status] || d?.status || '未知状态'
}
function statusClass(s) {
  return s === 'ready' ? 'ok' : s === 'failed' ? 'bad' : 'doing'
}
function engineZh(e) {
  return ({ 'asset-reuse': '复用解析产物', rapidocr: 'OCR', spark_vl: '视觉模型', pymupdf: 'PDF 解析', pandoc: '文档转换', direct: '文本直读' })[e] || e
}
function fmtDate(iso) {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  return `${d.getFullYear()}/${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

async function loadChunks() {
  if (!props.doc?.id) return
  chunks.value = []
  chunkTotal.value = 0
  chunkError.value = ''
  if (!['ready', 'failed'].includes(props.doc.status)) return
  chunksLoading.value = true
  try {
    const d = await studentKbApi.chunks(props.doc.id, { limit: 20 })
    chunks.value = d?.items || []
    chunkTotal.value = Number(d?.chunk_count) || chunks.value.length
  } catch (e) {
    chunkError.value = e?.message || '切片加载失败'
  } finally {
    chunksLoading.value = false
  }
}

async function loadImage() {
  if (!isImage.value || !props.doc?.file_id) return
  if (imageUrl.value) return
  try {
    const d = await filesApi.contentUrl(String(props.doc.file_id))
    if (d?.url) imageUrl.value = d.url
  } catch { /* 原图不可用：保留文字信息 */ }
}

function ask(skill) {
  const title = props.doc?.title || '这份资料'
  const text = skill === 'quiz_gen'
    ? `请基于我上传的资料《${title}》出 5 道由易到难的练习题，先出第一题，并标注每题考查的知识点。`
    : `请基于我上传的资料《${title}》帮我梳理核心知识点，再用苏格拉底式提问引导我理解。`
  emit('ask', { text, display: skill === 'quiz_gen' ? '📝 基于资料生成练习' : '📚 基于资料提问', skill })
}

function onKeydown(e) {
  if (e.key === 'Escape') emit('close')
}
watch(
  () => [props.open, props.doc?.id, props.doc?.status],
  ([open]) => {
    if (!open) return
    imageUrl.value = ''
    loadChunks()
    loadImage()
  },
  { immediate: true },
)
watch(
  () => props.open,
  (open) => {
    if (open) document.addEventListener('keydown', onKeydown)
    else document.removeEventListener('keydown', onKeydown)
  },
)
onBeforeUnmount(() => document.removeEventListener('keydown', onKeydown))
</script>

<style scoped>
.doc-drawer {
  position: fixed; inset: 0; z-index: 1400; display: flex; justify-content: flex-end;
  background: rgba(15, 23, 42, .28); backdrop-filter: blur(3px);
}
.doc-panel {
  width: min(620px, 94vw); height: 100%; display: flex; flex-direction: column;
  background: var(--card); border-left: 1px solid var(--line);
  box-shadow: -20px 0 60px rgba(15, 23, 42, .16);
  animation: dp-in .24s cubic-bezier(.16, 1, .3, 1);
}
@keyframes dp-in { from { transform: translateX(24px); opacity: .5; } }
.dp-head { display: flex; align-items: flex-start; gap: 12px; padding: 20px 22px 16px; border-bottom: 1px solid var(--line); }
.dp-title-wrap { flex: 1; min-width: 0; display: flex; gap: 12px; }
.dp-ic { width: 42px; height: 42px; flex: none; display: grid; place-items: center; border-radius: 12px; background: var(--brand-soft); font-size: 20px; }
.dp-title-body { min-width: 0; }
.dp-title { margin: 0; font-size: 17px; font-weight: 800; color: var(--ink); line-height: 1.4; word-break: break-word; }
.dp-sub { display: flex; flex-wrap: wrap; gap: 7px; margin-top: 6px; font-size: 10.5px; }
.dp-status { font-weight: 800; }
.dp-status.ok { color: var(--ok-deep); }
.dp-status.doing { color: var(--brand-deep); }
.dp-status.bad { color: var(--err-deep); }
.dp-cat, .dp-engine { padding: 1px 8px; border-radius: 999px; background: var(--bg2); color: var(--ink2); }
.dp-close { width: 30px; height: 30px; border: 1px solid var(--line); border-radius: 9px; background: var(--card); color: var(--ink3); cursor: pointer; }
.dp-close:hover { color: var(--ink); border-color: var(--border-strong); }
.dp-scroll { flex: 1; min-height: 0; overflow: auto; padding: 16px 22px 22px; }
.dp-meta-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; }
.dp-meta-grid > div { padding: 9px 10px; border-radius: 10px; background: var(--bg2); display: flex; flex-direction: column; gap: 4px; }
.dp-meta-grid span { font-size: 10px; color: var(--ink3); }
.dp-meta-grid b { font-size: 13px; color: var(--ink); font-weight: 800; font-family: var(--font-num); }
.dp-cat-select { border: 0; background: transparent; padding: 0; font: inherit; font-size: 12.5px; font-weight: 700; color: var(--ink); cursor: pointer; }
.dp-error, .dp-partial { margin-top: 12px; padding: 11px 12px; border-radius: 10px; font-size: 12px; line-height: 1.6; }
.dp-error { background: var(--err-bg); border: 1px solid var(--err-border); color: var(--err-deep); }
.dp-partial { background: var(--brand-soft); border: 1px solid var(--primary-border); color: var(--brand-deep); }
.dp-error p, .dp-partial p { margin: 3px 0 0; }
.dp-progress { margin-top: 12px; padding: 11px 12px; border-radius: 10px; background: var(--brand-soft); border: 1px solid var(--primary-border); }
.dp-progress-row { display: flex; justify-content: space-between; font-size: 12px; color: var(--brand-deep); font-weight: 700; }
.dp-bar { height: 5px; margin-top: 8px; border-radius: 999px; background: var(--primary-border); overflow: hidden; }
.dp-bar i { display: block; height: 100%; border-radius: inherit; background: var(--gradient-brand); transition: width .4s ease; }
.dp-progress p { margin: 6px 0 0; font-size: 10.5px; color: var(--ink3); }
.dp-image-wrap { margin-top: 14px; }
.dp-image { border: 1px solid var(--line); border-radius: 12px; overflow: hidden; background: var(--bg2); cursor: zoom-in; max-height: 360px; }
.dp-image img { display: block; width: 100%; height: auto; max-height: 360px; object-fit: contain; }
.dp-image-loading { padding: 24px; text-align: center; color: var(--ink3); font-size: 12px; background: var(--bg2); border-radius: 12px; }
.dp-section { margin-top: 16px; }
.dp-section-head { display: flex; justify-content: space-between; gap: 8px; align-items: baseline; margin-bottom: 8px; font-size: 12px; font-weight: 800; color: var(--ink2); }
.dp-section-note { font-size: 10px; color: var(--ink3); font-weight: 500; }
.dp-chunks { display: flex; flex-direction: column; gap: 8px; }
.dp-chunk { padding: 10px 12px; border: 1px solid var(--line); border-radius: 10px; background: var(--bg-white); }
.dp-chunk-index { margin-bottom: 5px; font-size: 9.5px; color: var(--ink3); font-weight: 700; letter-spacing: .03em; }
.dp-chunk :deep(.md-body) { font-size: 12px; line-height: 1.65; color: var(--ink2); }
.dp-chunk :deep(.md-body p) { margin: 0 0 4px; }
.dp-chunk :deep(.md-body table) { font-size: 11px; }
.dp-chunk-loading, .dp-empty-chunks { padding: 14px; border-radius: 10px; background: var(--bg2); color: var(--ink3); font-size: 12px; text-align: center; }
.dp-chunk-error { padding: 12px; border-radius: 10px; background: var(--err-bg); color: var(--err-deep); font-size: 12px; display: flex; justify-content: space-between; gap: 8px; }
.dp-chunk-error button { border: 0; background: none; color: inherit; font: inherit; font-weight: 800; cursor: pointer; }
.dp-actions { display: flex; flex-wrap: wrap; gap: 8px; padding: 14px 20px; border-top: 1px solid var(--line); background: var(--card); }
.dp-btn { height: 34px; padding: 0 13px; border: 1px solid var(--line); border-radius: 9px; background: var(--card); color: var(--ink2); font: inherit; font-size: 11.5px; font-weight: 700; cursor: pointer; }
.dp-btn:hover:not(:disabled) { border-color: var(--primary-border); color: var(--brand-deep); }
.dp-btn:disabled { opacity: .45; cursor: not-allowed; }
.dp-btn--primary { border-color: transparent; background: var(--gradient-brand); color: #fff; }
.dp-btn--primary:hover:not(:disabled) { color: #fff; filter: brightness(1.04); }
.dp-btn--danger { color: var(--err); }
@media (max-width: 640px) {
  .doc-panel { width: 100%; }
  .dp-meta-grid { grid-template-columns: repeat(2, 1fr); }
  .dp-actions { position: sticky; bottom: 0; }
}
</style>
