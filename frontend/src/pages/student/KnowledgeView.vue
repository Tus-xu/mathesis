<template>
  <div class="view">
    <div class="greeting">
      <div class="hello">📚 我的知识库</div>
      <div class="sub">上传教材、笔记、试卷——AI 解析切片后，对话能引用、能基于它出题和讲解。教材与题目分库存放，互不混淆。</div>
    </div>

    <!-- 上传区 -->
    <div class="kb-upload card" :class="{ drag }"
         @dragover.prevent="drag = true" @dragleave.prevent="drag = false" @drop.prevent="onDrop">
      <div class="kb-up-ic">📁</div>
      <div class="kb-up-txt">拖拽或选择文件上传（PDF / Word / PPT / Markdown / 图片，≤{{ MAX_MB }}MB）</div>
      <div class="kb-up-sub">上传后自动：解析 → 切片 → 向量化，进度实时可见</div>
      <!-- P3② 上传分类（存 meta.category，白名单与后端一致，超集回落「其他」） -->
      <div class="kb-up-cat">
        <span class="kb-up-cat-lb">上传分类</span>
        <select v-model="uploadCat" class="kb-cat-select">
          <option v-for="c in CATEGORIES" :key="c" :value="c">{{ c }}</option>
        </select>
        <span class="kb-up-cat-tip">图片题目选「题目」：按原图保存展示，文字识别仅辅助检索</span>
      </div>
      <label class="kb-up-btn">
        ＋ 选择文件
        <input type="file" hidden :accept="ACCEPT" @change="onPick" />
      </label>
    </div>

    <!-- P3② 分类筛选 chips（真实计数，来自已加载文档） -->
    <div class="kb-chips">
      <span
        v-for="c in catTabs" :key="c.key"
        class="kb-chip" :class="{ active: activeCat === c.key }"
        @click="activeCat = c.key"
      >{{ c.label }}<i v-if="c.count" class="kb-chip-n">{{ c.count }}</i></span>
    </div>

    <!-- 文档列表 -->
    <div class="kb-list">
      <div v-if="loading" style="padding:26px;text-align:center;color:var(--ink3);font-size:13px;">加载中…</div>
      <div v-else-if="loadError" style="padding:20px;text-align:center;color:var(--err);font-size:13px;">
        {{ loadError }}
        <div style="margin-top:8px;"><button class="kb-retry" @click="load">重试</button></div>
      </div>
      <div v-else-if="!docs.length" style="padding:26px;text-align:center;color:var(--ink3);font-size:13px;">
        还没有资料。先传一本教材或一份笔记，AI 就能基于你的资料出题和讲解。
      </div>
      <div v-else-if="!filteredDocs.length" style="padding:26px;text-align:center;color:var(--ink3);font-size:13px;">
        「{{ activeCat }}」分类下暂无资料，换一个分类或上传时选好分类。
      </div>
      <div v-else v-for="d in filteredDocs" :key="d.id" class="kb-doc">
        <!-- P3④ 图片类文档：原图缩略图为主展示（题目配图解析难保准确，原图更严谨），点击看原图 -->
        <template v-if="isImageDoc(d)">
          <div
            v-if="thumbUrls.get(d.id)"
            class="kd-thumb" title="点击查看原图"
            @click="openLightbox(thumbUrls.get(d.id))"
          >
            <img :src="thumbUrls.get(d.id)" alt="" loading="lazy" @error="thumbUrls.delete(d.id)">
          </div>
          <div v-else class="kd-ic">🖼️</div>
        </template>
        <div v-else class="kd-ic">{{ typeIcon(d.title) }}</div>
        <div class="kd-body">
          <div class="kd-title">{{ d.title }}</div>
          <div class="kd-meta">
            <span :class="['kd-st', statusClass(d.status)]">{{ statusZh(d) }}</span>
            <span v-if="d.page_count">📄 {{ d.page_count }} 页</span>
            <span v-if="d.chunk_count">🧩 {{ d.chunk_count }} 切片</span>
            <span>📅 {{ fmtDate(d.created_at) }}</span>
          </div>
          <!-- P3① 图片 OCR 稀疏：如实呈现 partial（非失败样式），文字仅辅助检索 -->
          <div v-if="d.index_state === 'partial' && d.status === 'ready'" class="kd-partial">
            🖼 已按原图保存（文字识别{{ d.ocr_len != null ? ` ${d.ocr_len} 字` : '较少' }}，仅辅助检索）
          </div>
          <div v-if="isActive(d.status)" class="kd-bar"><div class="kd-bar-fill" :style="{ width: (d.progress || 5) + '%' }"></div></div>
          <div v-if="d.status === 'failed'" class="kd-err">{{ errMsg(d.error) }}</div>
          <div class="kd-cat-row">
            <span class="kd-cat-lb">分类</span>
            <select
              class="kb-cat-select sm" :value="categoryOf(d)"
              :disabled="isActive(d.status) || catSaving === d.id" title="修改分类"
              @change="onCatChange(d, $event)"
            >
              <option v-for="c in CATEGORIES" :key="c" :value="c">{{ c }}</option>
            </select>
          </div>
        </div>
        <div class="kd-ops">
          <button class="kd-op" :disabled="isActive(d.status)" @click="reingest(d)" title="重新解析（切片/向量化重跑，幂等不重复）">↻ 重新解析</button>
          <button class="kd-op danger" :disabled="isActive(d.status)" @click="removeDoc(d)">删除</button>
        </div>
      </div>
    </div>

    <div class="kb-note">💡 在对话里直接说「基于我上传的资料出 5 道题」即可引用这里的内容；上传的原文不会被解析结果覆盖。</div>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { api } from '@/api/client'
import { filesApi, studentKbApi } from '@/api'
import { openLightbox } from '@/utils/lightbox'
import { useAuthStore } from '@/stores/auth'
import { useToastStore } from '@/stores/toast'

const auth = useAuthStore()
const toast = useToastStore()
const uid = computed(() => auth.user?.id || auth.user?.user_id || '')

const docs = ref([])
const loading = ref(false)
const loadError = ref('')
const drag = ref(false)

/* ===== P3② 分类管理（存 meta.category，无迁移；白名单与后端契约一致） ===== */
const CATEGORIES = ['教材', '教辅', '笔记', '试卷', '题目', '其他']
const uploadCat = ref('其他')
const activeCat = ref('全部')
const catSaving = ref('')

const isImageDoc = (d) => /\.(png|jpe?g)$/i.test(String(d?.title || ''))
// 后端缺省「其他」；老数据无该字段也归「其他」，不做前端编造
const categoryOf = (d) => {
  const c = String(d?.category || '').trim()
  return CATEGORIES.includes(c) ? c : '其他'
}
const catTabs = computed(() => [
  { key: '全部', label: '全部', count: docs.value.length },
  ...CATEGORIES.map((c) => ({
    key: c, label: c,
    count: docs.value.filter((d) => categoryOf(d) === c).length,
  })),
])
const filteredDocs = computed(() =>
  activeCat.value === '全部' ? docs.value : docs.value.filter((d) => categoryOf(d) === activeCat.value)
)

// P3④ 图片文档原图缩略图（file_id → 预签名内容 URL；旧后端/缺字段退回图标占位）
const thumbUrls = reactive(new Map())
async function loadThumbs() {
  for (const d of docs.value) {
    if (!isImageDoc(d) || thumbUrls.has(d.id)) continue
    const fid = d.file_id || d.file_uri
    if (!fid) continue
    try {
      const r = await filesApi.contentUrl(fid)
      if (r?.url) thumbUrls.set(d.id, r.url)
    } catch { /* 原图暂不可用：退回 🖼 图标，不阻塞列表 */ }
  }
}

async function onCatChange(d, ev) {
  const cat = String(ev?.target?.value || '')
  if (!cat || cat === categoryOf(d)) return
  catSaving.value = d.id
  try {
    await studentKbApi.patchDocument(d.id, cat)
    d.category = cat // 本地同步，刷新后由后端 meta.category 保持
    toast.success(`分类已改为「${cat}」`)
  } catch (e) {
    ev.target.value = categoryOf(d) // 回滚下拉显示
    const ZH = { 40400: '文档不存在', 40301: '只能修改自己上传的资料', 40001: '不是合法的分类' }
    toast.error(`分类修改失败：${ZH[e?.code] || e?.message || '请稍后重试'}`)
  } finally {
    catSaving.value = ''
  }
}

const ACCEPT = '.pdf,.docx,.pptx,.md,.txt,.png,.jpg,.jpeg'
const MAX_MB = 30
let pollTimer = null

const isActive = (s) => ['pending', 'parsing', 'chunking', 'embedding'].includes(s)

function typeIcon(name = '') {
  const n = name.toLowerCase()
  if (n.endsWith('.pdf')) return '📕'
  if (/\.(png|jpe?g)$/.test(n)) return '🖼️'
  if (/\.(docx?|pptx?)$/.test(n)) return '📘'
  return '📄'
}
function statusZh(d) {
  return ({ ready: '✓ 已解析', pending: '排队中', parsing: '解析中', chunking: '切片中', embedding: '向量化中', failed: '✗ 失败' })[d.status] || d.status
}
function statusClass(s) {
  return s === 'ready' ? 'ok' : s === 'failed' ? 'bad' : 'doing'
}
function errMsg(e) {
  if (!e) return '解析失败，可点击「重新解析」重试'
  return typeof e === 'string' ? e : (e.message || e.reason || '解析失败')
}
function fmtDate(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  return `${d.getMonth() + 1}/${d.getDate()}`
}

async function load() {
  loading.value = true
  loadError.value = ''
  try {
    const r = await api.raw('GET', '/v1/kb/documents', { query: { kb_name: 'default', user_id: uid.value } })
    docs.value = r.data?.items || []
    loadThumbs()
    schedulePoll()
  } catch (e) {
    // 首次使用/未建库：kb_not_found（404）是正常空态，不是错误
    const msg = e?.message || ''
    if (e?.code === 404 || e?.status === 404 || /不存在/.test(msg)) {
      docs.value = []
      loadError.value = ''
    } else {
      loadError.value = typeof msg === 'string' ? msg : '加载失败'
    }
  } finally {
    loading.value = false
  }
}

// 解析中的文档存在时 5s 轮询进度
function schedulePoll() {
  clearTimeout(pollTimer)
  if (docs.value.some((d) => isActive(d.status))) {
    pollTimer = setTimeout(load, 5000)
  }
}

async function onPick(e) {
  const files = [...(e.target.files || [])]
  e.target.value = ''
  for (const f of files) await uploadOne(f)
}
function onDrop(e) {
  drag.value = false
  const files = [...(e.dataTransfer?.files || [])]
  if (files.length) files.slice(0, 3).forEach((f) => uploadOne(f))
}

async function uploadOne(f) {
  if (f.size > MAX_MB * 1024 * 1024) {
    toast.error(`${f.name} 超过 ${MAX_MB}MB 上限`)
    return
  }
  const fd = new FormData()
  fd.append('file', f)
  fd.append('kb_name', 'default')
  fd.append('user_id', uid.value)
  fd.append('category', uploadCat.value) // P3② 可选分类，后端白名单外回落「其他」
  try {
    const r = await api.raw('POST', '/v1/kb/upload', { body: fd })
    toast.success(`${f.name}：${r.data?.duplicate ? '已存在，断点续传解析' : '上传成功，开始解析'}`)
    await load()
  } catch (e) {
    toast.error(`${f.name} 上传失败：${e?.message || '未知错误'}`)
  }
}

async function reingest(d) {
  try {
    // 后端重跑解析管线（有解析产物走快路径，只重做切片与向量化）；
    // 旧契约的「前端 fetch file_uri 重传」不可行——file_uri 是存储引用，浏览器不可达。
    await api.raw('POST', `/v1/kb/documents/${d.id}/reparse`)
    toast.info('已重新拉起解析管线')
    await load()
  } catch {
    toast.error('重新解析失败（原文文件缺失时可删除后重传）')
  }
}

async function removeDoc(d) {
  if (!window.confirm(`删除「${d.title}」？其切片与图谱关联将一并移除。`)) return
  try {
    await api.raw('DELETE', `/v1/kb/documents/${d.id}`, { query: { user_id: uid.value } })
    toast.success('已删除')
    await load()
  } catch (e) {
    toast.error(e?.message || '删除失败')
  }
}

onMounted(load)
onBeforeUnmount(() => clearTimeout(pollTimer))
</script>

<style scoped>
.kb-upload {
  border: 2px dashed var(--line); border-radius: var(--radius-xl, 22px); padding: 26px;
  text-align: center; margin-bottom: 16px; transition: all .2s ease;
}
.kb-upload.drag { border-color: var(--primary); background: var(--primary-subtle); }
.kb-up-ic { font-size: 30px; margin-bottom: 6px; }
.kb-up-txt { font-size: 14px; font-weight: 600; color: var(--ink); }
.kb-up-sub { font-size: 12px; color: var(--ink3); margin: 6px 0 12px; }
.kb-up-btn {
  display: inline-block; padding: 9px 22px; border-radius: 999px; cursor: pointer;
  background: var(--gradient-brand); color: #fff; font-size: 13px; font-weight: 700;
}
/* P3② 上传分类选择 */
.kb-up-cat {
  display: flex; align-items: center; justify-content: center; gap: 8px;
  margin: 0 0 12px; font-size: 12.5px; color: var(--ink2); flex-wrap: wrap;
}
.kb-up-cat-lb { font-weight: 600; color: var(--ink); }
.kb-up-cat-tip { font-size: 11.5px; color: var(--ink3); }
.kb-cat-select {
  padding: 5px 10px; border-radius: 8px; border: 1px solid var(--line);
  background: var(--card); font: inherit; font-size: 12.5px; color: var(--ink); cursor: pointer;
}
.kb-cat-select.sm { padding: 3px 8px; font-size: 11.5px; }
.kb-cat-select:disabled { opacity: .5; cursor: not-allowed; }
/* P3② 分类筛选 chips（与资源中心 rc-chip 同语言） */
.kb-chips { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 12px; }
.kb-chip {
  padding: 5px 13px; border-radius: 999px; font-size: 12.5px; cursor: pointer;
  background: var(--bg2, #f1f5f9); color: var(--ink2); border: 1px solid transparent;
}
.kb-chip.active {
  background: var(--primary-subtle, #eef2ff); color: var(--primary);
  border-color: var(--primary-border, #c7d2fe);
}
.kb-chip-n {
  font-style: normal; font-size: 10.5px; margin-left: 5px; opacity: .75;
}
/* P3④ 图片文档原图缩略图 */
.kd-thumb {
  width: 92px; aspect-ratio: 4 / 3; flex-shrink: 0; border-radius: 10px; overflow: hidden;
  border: 1px solid var(--line); background: var(--bg2, #f1f5f9); cursor: zoom-in;
}
.kd-thumb img { width: 100%; height: 100%; object-fit: cover; display: block; transition: transform .18s ease; }
.kd-thumb:hover img { transform: scale(1.04); }
/* P3① partial 如实呈现（信息态，非失败红） */
.kd-partial {
  font-size: 11.5px; color: var(--primary); background: var(--primary-subtle, #eef2ff);
  border-radius: 7px; padding: 4px 8px; margin-top: 6px; width: fit-content;
}
.kd-cat-row { display: flex; align-items: center; gap: 6px; margin-top: 6px; }
.kd-cat-lb { font-size: 11.5px; color: var(--ink3); }
.kb-doc {
  display: flex; align-items: flex-start; gap: 12px; padding: 14px 16px;
  background: var(--card); border: 1px solid var(--line); border-radius: 14px; margin-bottom: 10px;
}
.kd-ic { font-size: 24px; }
.kd-body { flex: 1; min-width: 0; }
.kd-title { font-size: 14px; font-weight: 700; color: var(--ink); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.kd-meta { display: flex; gap: 12px; font-size: 12px; color: var(--ink3); margin-top: 4px; flex-wrap: wrap; }
.kd-st.ok { color: var(--ok-deep); font-weight: 700; }
.kd-st.doing { color: var(--primary); font-weight: 700; }
.kd-st.bad { color: var(--err-deep); font-weight: 700; }
.kd-bar { height: 4px; background: var(--skeleton-bg); border-radius: 99px; margin-top: 8px; overflow: hidden; }
.kd-bar-fill { height: 100%; background: var(--gradient-brand); border-radius: 99px; transition: width .5s ease; }
.kd-err { font-size: 11.5px; color: var(--err-deep); margin-top: 6px; }
.kd-ops { display: flex; flex-direction: column; gap: 6px; }
.kd-op {
  padding: 6px 12px; border-radius: 8px; border: 1px solid var(--line); background: var(--card);
  font: inherit; font-size: 11.5px; color: var(--ink2); cursor: pointer; white-space: nowrap;
}
.kd-op.danger { color: var(--err); }
.kd-op:hover:not(:disabled) { border-color: var(--primary-border); color: var(--primary); }
.kd-op:disabled { opacity: .5; cursor: not-allowed; }
.kb-retry { padding: 6px 16px; border-radius: 8px; border: 1px solid var(--line); background: var(--card); font: inherit; font-size: 12px; cursor: pointer; }
.kb-note { margin-top: 14px; font-size: 12px; color: var(--ink3); line-height: 1.6; }
</style>
