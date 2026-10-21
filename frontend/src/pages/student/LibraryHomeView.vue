<template>
  <div class="view library-page">
    <header class="lib-head">
      <div class="lib-head-copy">
        <div class="hello">📚 知识库</div>
        <div class="sub">把资料变成可引用的证据，把题目变成可执行的练习。</div>
      </div>
      <div class="lib-stats" aria-label="知识库统计">
        <div><b>{{ docs.length }}</b><span>份资料</span></div>
        <div><b>{{ readyCount }}</b><span>可引用</span></div>
        <div><b>{{ totalChunks }}</b><span>切片</span></div>
        <div><b>{{ qTotal ? qTotal : '—' }}</b><span>题库</span></div>
      </div>
    </header>

    <div class="lib-tabs" role="tablist" aria-label="知识库分类">
      <button role="tab" :aria-selected="tab === 'materials'" :class="{ active: tab === 'materials' }" @click="setTab('materials')">
        📄 我的资料 <span class="tab-count">{{ docs.length }}</span>
      </button>
      <button role="tab" :aria-selected="tab === 'questions'" :class="{ active: tab === 'questions' }" @click="setTab('questions')">
        📝 我的题库 <span class="tab-count">{{ qTotal || '—' }}</span>
      </button>
      <span class="lib-tabs-hint">
        {{ tab === 'materials' ? '上传后自动解析 · 对话可引用 · 点开可看切片' : '平台题库 · 支持搜索 / 筛选 / 直接开练' }}
      </span>
    </div>

    <!-- ===================== 我的资料 ===================== -->
    <template v-if="tab === 'materials'">
      <section
        class="lib-upload" :class="{ 'is-drag': dragOver }"
        @dragover.prevent="dragOver = true"
        @dragleave.prevent="dragOver = false"
        @drop.prevent="onDrop"
      >
        <div class="lu-main">
          <span class="lu-ic" aria-hidden="true">📥</span>
          <div class="lu-copy">
            <b>把资料变成可引用的知识</b>
            <p>PDF / Word / PPT / Markdown / 图片，≤30MB。上传后自动解析、切片、向量化；图片会保留原图。</p>
          </div>
        </div>
        <div class="lu-actions">
          <label class="lu-cat">
            分类
            <select v-model="uploadCat">
              <option v-for="c in CATEGORIES" :key="c" :value="c">{{ c }}</option>
            </select>
          </label>
          <button type="button" class="lu-btn" @click="pickFiles">＋ 上传资料</button>
          <input ref="fileInput" type="file" hidden multiple :accept="ACCEPT" @change="onPick" />
        </div>
        <div v-if="uploading.length" class="lu-queue">
          <span v-for="u in uploading" :key="u.key" class="lu-queue-item">
            <i :class="u.status"></i>{{ u.name }} · {{ u.status === 'done' ? '已上传' : u.status === 'failed' ? '失败' : '上传中…' }}
          </span>
        </div>
      </section>

      <div class="lib-toolbar">
        <div class="lib-search">
          <span aria-hidden="true">⌕</span>
          <input v-model.trim="docSearch" type="search" placeholder="搜索资料标题…" aria-label="搜索资料标题" />
          <button v-if="docSearch" type="button" title="清除搜索" @click="docSearch = ''">✕</button>
        </div>
        <select v-model="docSort" class="lib-select" aria-label="资料排序">
          <option value="new">最近上传</option>
          <option value="name">按名称</option>
          <option value="chunks">按切片数</option>
        </select>
        <button type="button" class="lib-refresh" :disabled="docsLoading" @click="loadDocs">↻ 刷新</button>
      </div>

      <div class="lib-cats" role="tablist" aria-label="资料分类">
        <button
          v-for="c in catTabs" :key="c.key" type="button" role="tab"
          class="lib-cat" :class="{ active: activeCat === c.key }"
          :aria-selected="activeCat === c.key" @click="activeCat = c.key"
        >
          {{ c.label }}<i v-if="c.count">{{ c.count }}</i>
        </button>
      </div>

      <div v-if="docsLoading && !docs.length" class="lib-loading">
        <span class="spinner"></span> 正在加载资料…
      </div>
      <div v-else-if="docsError" class="lib-error">
        {{ docsError }}
        <button type="button" @click="loadDocs">重试</button>
      </div>
      <div v-else-if="!docs.length" class="lib-empty">
        <div class="lib-empty-ic">🗂️</div>
        <div class="lib-empty-title">还没有资料</div>
        <p>先传一本教材、一份笔记或一张题图。AI 会把它切成可引用的证据，之后在对话里就能基于它提问和出题。</p>
        <button type="button" class="lu-btn" @click="pickFiles">上传第一份资料</button>
      </div>
      <div v-else-if="!filteredDocs.length" class="lib-empty lib-empty--sm">
        <div class="lib-empty-title">没有匹配的资料</div>
        <p>换一个分类，或清空搜索关键词。</p>
      </div>
      <template v-else>
        <section v-for="group in docGroups" :key="group.key" class="doc-group">
          <div class="doc-group-head">
            <h3>{{ group.label }}</h3>
            <span>{{ group.items.length }} 份</span>
          </div>
          <div
            v-for="d in group.items" :key="d.id"
            class="doc-row" role="button" tabindex="0"
            :aria-label="`打开资料 ${d.title}`"
            @click="openDoc(d.id)" @keydown.enter.prevent="openDoc(d.id)" @keydown.space.prevent="openDoc(d.id)"
          >
            <span class="doc-ic" :class="{ image: isImageDoc(d) }">{{ typeIcon(d.title) }}</span>
            <span class="doc-main">
              <span class="doc-title-line">
                <span class="doc-title">{{ d.title }}</span>
                <span class="doc-cat-badge">{{ categoryOf(d) }}</span>
              </span>
              <span class="doc-meta">
                <span :class="['doc-status', statusClass(d.status)]">{{ statusZh(d) }}</span>
                <span v-if="d.page_count">📄 {{ d.page_count }} 页</span>
                <span v-if="d.chunk_count">🧩 {{ d.chunk_count }} 切片</span>
                <span v-if="d.index_state === 'partial'" class="doc-partial">按原图保存</span>
                <span>📅 {{ fmtDate(d.created_at) }}</span>
              </span>
              <span v-if="d.status === 'failed'" class="doc-error">{{ d.error || '解析失败，可重新解析或删除重传' }}</span>
            </span>
            <span class="doc-ops">
              <button type="button" class="doc-op" :disabled="isActive(d.status)" @click.stop="reparseDoc(d)">↻</button>
              <button type="button" class="doc-op danger" :disabled="isActive(d.status)" @click.stop="removeDoc(d)">删除</button>
              <span class="doc-chevron" aria-hidden="true">›</span>
            </span>
          </div>
        </section>
      </template>
    </template>

    <!-- ===================== 我的题库 ===================== -->
    <template v-else>
      <div class="lib-toolbar lib-toolbar--q">
        <div class="lib-search">
          <span aria-hidden="true">⌕</span>
          <input v-model.trim="qSearch" type="search" placeholder="搜索题干关键词，如「函数」「导数」…" aria-label="搜索题干" />
          <button v-if="qSearch" type="button" title="清除搜索" @click="qSearch = ''">✕</button>
        </div>
        <select v-model="qType" class="lib-select" aria-label="题型">
          <option value="">全部题型</option>
          <option value="choice">选择题</option>
          <option value="blank">填空题</option>
          <option value="solution">解答题</option>
        </select>
        <select v-model="qDiff" class="lib-select" aria-label="难度">
          <option value="">全部难度</option>
          <option value="easy">基础</option>
          <option value="medium">进阶</option>
          <option value="hard">压轴</option>
        </select>
        <span class="lib-count">共 <b>{{ qTotal }}</b> 题</span>
      </div>

      <div v-if="qLoading && !questions.length" class="lib-loading"><span class="spinner"></span> 题库加载中…</div>
      <div v-else-if="qError" class="lib-error">
        {{ qError }}
        <button type="button" @click="loadQuestions(true)">重试</button>
      </div>
      <div v-else-if="!questions.length" class="lib-empty">
        <div class="lib-empty-ic">📝</div>
        <div class="lib-empty-title">没有找到题目</div>
        <p>{{ qSearch || qType || qDiff ? '试试清空筛选条件，或换一个关键词。' : '题库还没有题目——上传试卷或在对话里出题会自动收录。' }}</p>
        <button v-if="qSearch || qType || qDiff" type="button" class="lu-btn" @click="resetQuestionFilters">清空筛选</button>
      </div>
      <template v-else>
        <div class="q-list">
          <div
            v-for="(q, i) in questions" :key="q.id"
            class="q-card" role="button" tabindex="0"
            :aria-label="`打开题目 ${qStart + i + 1}`"
            @click="openQuestion(q)"
            @keydown.enter.prevent="openQuestion(q)"
            @keydown.space.prevent="openQuestion(q)"
          >
            <div class="q-index">{{ String(qStart + i + 1).padStart(2, '0') }}</div>
            <div class="q-card-main">
              <div class="q-stem-preview" :class="{ 'is-clamp': !hasMarkdownTable(q.stem) }">
                <LatexText :text="compactQuestionPreview(q.stem, 180)" />
              </div>
              <div class="q-tags">
                <span class="q-tag q-tag--type">{{ qTypeZh(q.q_type) }}</span>
                <span class="q-tag">{{ diffZh(q.difficulty) }}</span>
                <span v-if="q.is_real_exam" class="q-tag q-tag--exam">真题{{ q.year ? ` · ${q.year}` : '' }}</span>
                <span v-for="name in (q.kp_names || []).slice(0, 3)" :key="name" class="q-tag q-tag--kp">{{ name }}</span>
                <span v-if="hasMarkdownTable(q.stem)" class="q-tag q-tag--table">含数据表</span>
                <span v-if="q.source" class="q-source">{{ q.source }}</span>
              </div>
            </div>
            <span class="q-chevron" aria-hidden="true">›</span>
          </div>
        </div>
        <div class="q-load-more">
          <span>已显示 {{ questions.length }} / {{ qTotal }}</span>
          <button v-if="questions.length < qTotal" type="button" :disabled="qLoading" @click="loadQuestions(false)">
            {{ qLoading ? '加载中…' : '加载更多' }}
          </button>
        </div>
      </template>
    </template>

    <LibraryDocDrawer
      :open="!!selectedDocId" :doc="selectedDoc" :busy="docBusy"
      @close="closeDoc" @reparse="reparseSelected" @delete="deleteSelected"
      @category-change="changeSelectedCategory" @ask="openAsk"
    />
    <LibraryQuestionDrawer
      :open="!!selectedQuestion" :question="selectedQuestion"
      @close="selectedQuestion = null" @ask="openAsk" @practice="practiceQuestion"
    />
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '@/api/client'
import { studentKbApi, questionBankApi } from '@/api'
import { useAuthStore } from '@/stores/auth'
import { useToastStore } from '@/stores/toast'
import { useConfirm } from '@/composables/useConfirm'
import LatexText from '@/components/LatexText.vue'
import LibraryDocDrawer from '@/components/student/LibraryDocDrawer.vue'
import LibraryQuestionDrawer from '@/components/student/LibraryQuestionDrawer.vue'
import { compactQuestionPreview, hasMarkdownTable } from '@/utils/questionPreview'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const toast = useToastStore()
const { confirm } = useConfirm()

const uid = computed(() => auth.user?.id || auth.user?.user_id || '')
const CATEGORIES = ['教材', '教辅', '笔记', '试卷', '题目', '其他']
const ACCEPT = '.pdf,.docx,.pptx,.md,.txt,.png,.jpg,.jpeg'
const MAX_MB = 30
const validTabs = ['materials', 'questions']
const tab = ref(validTabs.includes(route.query.tab) ? route.query.tab : 'materials')

/* ===================== 资料 ===================== */
const docs = ref([])
const docsLoading = ref(false)
const docsError = ref('')
const docSearch = ref('')
const docSort = ref('new')
const activeCat = ref('全部')
const uploadCat = ref('其他')
const dragOver = ref(false)
const fileInput = ref(null)
const uploading = ref([])
const selectedDocId = ref('')
const docBusy = ref(false)
let pollTimer = null

const isActive = (s) => ['pending', 'parsing', 'chunking', 'embedding'].includes(s)
const categoryOf = (d) => {
  const c = String(d?.category || '').trim()
  return CATEGORIES.includes(c) ? c : '其他'
}
const isImageDoc = (d) => /\.(png|jpe?g)$/i.test(String(d?.title || '')) || d?.file_type === 'image'
const readyCount = computed(() => docs.value.filter((d) => d.status === 'ready').length)
const totalChunks = computed(() => docs.value.reduce((sum, d) => sum + (Number(d.chunk_count) || 0), 0))
const catTabs = computed(() => [
  { key: '全部', label: '全部', count: docs.value.length },
  ...CATEGORIES.map((c) => ({ key: c, label: c, count: docs.value.filter((d) => categoryOf(d) === c).length })),
])
const filteredDocs = computed(() => {
  const term = docSearch.value.trim().toLowerCase()
  const list = docs.value.filter((d) => {
    if (activeCat.value !== '全部' && categoryOf(d) !== activeCat.value) return false
    if (term && !String(d.title || '').toLowerCase().includes(term)) return false
    return true
  })
  const sorted = [...list]
  if (docSort.value === 'name') sorted.sort((a, b) => String(a.title || '').localeCompare(String(b.title || ''), 'zh-CN'))
  else if (docSort.value === 'chunks') sorted.sort((a, b) => (Number(b.chunk_count) || 0) - (Number(a.chunk_count) || 0))
  else sorted.sort((a, b) => new Date(b.created_at || 0) - new Date(a.created_at || 0))
  return sorted
})
const docGroups = computed(() => {
  if (docSearch.value.trim() || activeCat.value !== '全部') {
    return [{ key: activeCat.value === '全部' ? '搜索结果' : activeCat.value, label: activeCat.value === '全部' ? '搜索结果' : activeCat.value, items: filteredDocs.value }]
  }
  return CATEGORIES
    .map((c) => ({ key: c, label: c, items: filteredDocs.value.filter((d) => categoryOf(d) === c) }))
    .filter((g) => g.items.length)
})
const selectedDoc = computed(() => docs.value.find((d) => String(d.id) === String(selectedDocId.value)) || null)

function typeIcon(name = '') {
  const n = String(name).toLowerCase()
  if (n.endsWith('.pdf')) return '📕'
  if (/\.(png|jpe?g)$/.test(n)) return '🖼️'
  if (/\.(docx?|pptx?)$/.test(n)) return '📘'
  return '📄'
}
function statusZh(d) {
  return ({ ready: '✓ 可引用', pending: '排队中', parsing: '解析中', chunking: '切片中', embedding: '向量化中', failed: '✗ 失败' })[d?.status] || d?.status || ''
}
function statusClass(s) {
  return s === 'ready' ? 'ok' : s === 'failed' ? 'bad' : 'doing'
}
function fmtDate(iso) {
  if (!iso) return '—'
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? '—' : `${d.getMonth() + 1}/${d.getDate()}`
}

async function loadDocs() {
  docsLoading.value = true
  docsError.value = ''
  try {
    const d = await studentKbApi.documents({ limit: 200 })
    docs.value = d?.items || []
    if (selectedDocId.value && !docs.value.some((x) => String(x.id) === String(selectedDocId.value))) closeDoc()
    schedulePoll()
  } catch (e) {
    docsError.value = e?.message || '资料加载失败'
  } finally {
    docsLoading.value = false
  }
}
function schedulePoll() {
  clearTimeout(pollTimer)
  if (docs.value.some((d) => isActive(d.status))) pollTimer = setTimeout(loadDocs, 5000)
}

function pickFiles() { fileInput.value?.click() }
function onPick(e) {
  const files = [...(e.target.files || [])]
  e.target.value = ''
  uploadFiles(files)
}
function onDrop(e) {
  dragOver.value = false
  uploadFiles([...(e.dataTransfer?.files || [])])
}
async function uploadFiles(files) {
  const accepted = files.slice(0, 6)
  for (const f of accepted) {
    if (f.size > MAX_MB * 1024 * 1024) {
      toast.error(`${f.name} 超过 ${MAX_MB}MB 上限`)
      continue
    }
    const ext = `.${String(f.name).split('.').pop().toLowerCase()}`
    if (!ACCEPT.split(',').includes(ext)) {
      toast.error(`${f.name} 类型不支持（支持 PDF / Word / PPT / Markdown / 图片）`)
      continue
    }
    await uploadOne(f)
  }
}
async function uploadOne(file) {
  const key = `${file.name}-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`
  uploading.value.push({ key, name: file.name, status: 'uploading' })
  const item = uploading.value.find((u) => u.key === key)
  const fd = new FormData()
  fd.append('file', file)
  fd.append('kb_name', 'default')
  fd.append('user_id', uid.value)
  fd.append('category', uploadCat.value)
  try {
    const r = await api.raw('POST', '/v1/kb/upload', { body: fd })
    item.status = 'done'
    toast.success(`${file.name}：${r.data?.duplicate ? '已存在，继续解析' : '上传成功，开始解析'}`)
    await loadDocs()
  } catch (e) {
    item.status = 'failed'
    toast.error(`${file.name} 上传失败：${e?.message || '未知错误'}`)
  } finally {
    setTimeout(() => { uploading.value = uploading.value.filter((u) => u.key !== key) }, 4000)
  }
}

function openDoc(id) { selectedDocId.value = String(id) }
function closeDoc() { selectedDocId.value = '' }

async function reparseDoc(d) {
  try {
    await studentKbApi.reparse(d.id)
    toast.info('已重新拉起解析管线')
    await loadDocs()
  } catch (e) {
    toast.error(e?.message || '重新解析失败')
  }
}
async function reparseSelected() {
  if (!selectedDoc.value) return
  docBusy.value = true
  try { await reparseDoc(selectedDoc.value) } finally { docBusy.value = false }
}
async function removeDoc(d) {
  const ok = await confirm({
    title: '删除资料',
    message: `删除「${d.title}」？其切片与检索索引将一并移除，删除后不可恢复。`,
    confirmText: '删除',
    danger: true,
  })
  if (!ok) return
  try {
    await studentKbApi.removeDocument(d.id)
    toast.success('已删除')
    if (String(selectedDocId.value) === String(d.id)) closeDoc()
    await loadDocs()
  } catch (e) {
    toast.error(e?.message || '删除失败')
  }
}
async function deleteSelected() {
  if (!selectedDoc.value) return
  docBusy.value = true
  try { await removeDoc(selectedDoc.value) } finally { docBusy.value = false }
}
async function changeSelectedCategory(cat) {
  if (!selectedDoc.value || !CATEGORIES.includes(cat)) return
  docBusy.value = true
  try {
    await studentKbApi.patchDocument(selectedDoc.value.id, cat)
    selectedDoc.value.category = cat
    toast.success(`分类已改为「${cat}」`)
  } catch (e) {
    toast.error(e?.message || '分类修改失败')
    await loadDocs()
  } finally {
    docBusy.value = false
  }
}
function openAsk(payload) {
  router.push({
    path: '/dialog',
    query: {
      ask: payload.text,
      display: payload.display || '📚 基于我的资料提问',
      ...(payload.skill ? { skill: payload.skill } : {}),
    },
  })
}

/* ===================== 题库 ===================== */
const questions = ref([])
const qTotal = ref(0)
const qLoading = ref(false)
const qError = ref('')
const qSearch = ref('')
const qType = ref('')
const qDiff = ref('')
const qOffset = ref(0)
const qStart = ref(0)
const Q_LIMIT = 30
const selectedQuestion = ref(null)
let qSearchTimer = null
let qReqSeq = 0

function qTypeZh(t) { return ({ choice: '选择题', blank: '填空题', solution: '解答题' })[t] || t || '未分型' }
function diffZh(d) { return ({ easy: '基础', medium: '进阶', hard: '压轴' })[d] || d || '未标难度' }

async function loadQuestions(reset = true) {
  if (reset) {
    qOffset.value = 0
    qError.value = ''
  }
  qLoading.value = true
  const seq = ++qReqSeq
  const start = reset ? 0 : qOffset.value
  try {
    const d = await questionBankApi.list({
      limit: Q_LIMIT,
      offset: start,
      ...(qType.value ? { q_type: qType.value } : {}),
      ...(qDiff.value ? { difficulty: qDiff.value } : {}),
      ...(qSearch.value ? { q: qSearch.value } : {}),
    })
    if (seq !== qReqSeq) return // 快速改筛选：丢弃过期响应，避免旧结果覆盖新条件
    const items = d?.items || []
    questions.value = reset ? items : [...questions.value, ...items]
    qTotal.value = Number(d?.total) || questions.value.length
    qStart.value = start
    qOffset.value = start + items.length
  } catch (e) {
    if (seq !== qReqSeq) return
    qError.value = e?.message || '题库加载失败'
    if (reset) questions.value = []
  } finally {
    if (seq === qReqSeq) qLoading.value = false
  }
}
async function loadQuestionTotal() {
  try {
    const d = await questionBankApi.list({ limit: 1 })
    qTotal.value = Number(d?.total) || 0
  } catch { /* 统计失败不影响页面 */ }
}
function resetQuestionFilters() {
  qSearch.value = ''
  qType.value = ''
  qDiff.value = ''
}
function openQuestion(q) { selectedQuestion.value = q }
function practiceQuestion(detail) {
  const stem = String(detail?.stem || '').slice(0, 1200)
  selectedQuestion.value = null
  openAsk({
    text: `请用苏格拉底方式带我练这道题，先让我自己尝试第一步：\n${stem}`,
    display: '✏️ 开始练这道题',
    skill: 'socratic',
  })
}

watch(qSearch, () => {
  clearTimeout(qSearchTimer)
  qSearchTimer = setTimeout(() => loadQuestions(true), 280)
})
watch([qType, qDiff], () => loadQuestions(true))
watch(tab, (t) => {
  router.replace({ query: { ...route.query, tab: t } })
  if (t === 'questions' && !questions.value.length && !qLoading.value) loadQuestions(true)
})
watch(() => route.query.tab, (t) => { if (validTabs.includes(t)) tab.value = t })
function setTab(t) { tab.value = t }

onMounted(() => {
  loadDocs()
  loadQuestionTotal()
  if (tab.value === 'questions') loadQuestions(true)
})
onBeforeUnmount(() => {
  clearTimeout(pollTimer)
  clearTimeout(qSearchTimer)
})
</script>

<style scoped>
.library-page { max-width: 1180px; margin: 0 auto; }
.lib-head { display: flex; align-items: flex-end; justify-content: space-between; gap: 20px; margin-bottom: 14px; flex-wrap: wrap; }
.lib-head .hello { font-size: 22px; font-weight: 800; color: var(--ink); }
.lib-head .sub { margin-top: 4px; font-size: 12.5px; color: var(--ink3); }
.lib-stats { display: flex; gap: 8px; }
.lib-stats > div { min-width: 66px; padding: 8px 12px; border: 1px solid var(--line); border-radius: 11px; background: var(--card); text-align: center; }
.lib-stats b { display: block; font-size: 16px; font-weight: 900; color: var(--ink); font-family: var(--font-num); line-height: 1.2; }
.lib-stats span { font-size: 9.5px; color: var(--ink3); }

.lib-tabs { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 14px; padding-bottom: 12px; border-bottom: 1px solid var(--line); }
.lib-tabs > button {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 8px 16px; border-radius: 999px; border: 1px solid var(--line); background: var(--card);
  font: inherit; font-size: 12.5px; font-weight: 700; color: var(--ink2); cursor: pointer;
}
.lib-tabs > button.active { background: var(--brand-soft); border-color: var(--primary-border); color: var(--brand-deep); }
.tab-count { font-size: 10px; font-weight: 800; padding: 0 6px; border-radius: 999px; background: var(--bg2); color: var(--ink3); }
.lib-tabs > button.active .tab-count { background: #fff; color: var(--brand-deep); }
.lib-tabs-hint { margin-left: auto; font-size: 10.5px; color: var(--ink3); }

.lib-upload {
  display: flex; align-items: center; justify-content: space-between; gap: 14px; flex-wrap: wrap;
  padding: 14px 16px; border: 1.5px dashed var(--primary-border); border-radius: var(--radius-lg);
  background: linear-gradient(135deg, rgba(238, 242, 255, .75), rgba(255, 255, 255, .92));
  margin-bottom: 12px; transition: all .18s ease;
}
.lib-upload.is-drag { border-color: var(--brand); background: var(--brand-soft); box-shadow: var(--shadow-glow-brand); }
.lu-main { display: flex; align-items: center; gap: 12px; min-width: 0; }
.lu-ic { width: 40px; height: 40px; flex: none; display: grid; place-items: center; border-radius: 12px; background: #fff; border: 1px solid var(--primary-border); font-size: 19px; }
.lu-copy b { display: block; font-size: 13px; font-weight: 800; color: var(--ink); }
.lu-copy p { margin: 3px 0 0; font-size: 11px; line-height: 1.5; color: var(--ink3); }
.lu-actions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.lu-cat { display: inline-flex; align-items: center; gap: 6px; font-size: 11px; color: var(--ink2); }
.lu-cat select { height: 30px; padding: 0 8px; border: 1px solid var(--line); border-radius: 8px; background: var(--card); font: inherit; font-size: 11.5px; color: var(--ink); }
.lu-btn { height: 32px; padding: 0 15px; border: 0; border-radius: 9px; background: var(--gradient-brand); color: #fff; font: inherit; font-size: 12px; font-weight: 800; cursor: pointer; box-shadow: 0 6px 16px -9px rgba(79,70,229,.6); }
.lu-btn:hover { filter: brightness(1.04); }
.lu-queue { flex-basis: 100%; display: flex; flex-wrap: wrap; gap: 8px; padding-top: 4px; }
.lu-queue-item { display: inline-flex; align-items: center; gap: 5px; font-size: 10.5px; color: var(--ink3); }
.lu-queue-item i { width: 6px; height: 6px; border-radius: 50%; background: var(--brand); }
.lu-queue-item i.done { background: var(--ok); }
.lu-queue-item i.failed { background: var(--err); }

.lib-toolbar { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin: 10px 0; }
.lib-toolbar--q { margin-top: 4px; }
.lib-search { flex: 1; min-width: 200px; display: flex; align-items: center; gap: 6px; height: 34px; padding: 0 10px; border: 1px solid var(--line); border-radius: 9px; background: var(--card); }
.lib-search > span { color: var(--ink3); font-size: 16px; line-height: 1; }
.lib-search input { flex: 1; min-width: 0; border: 0; outline: 0; background: transparent; font: inherit; font-size: 12.5px; color: var(--ink); }
.lib-search input::placeholder { color: var(--ink3); }
.lib-search button { border: 0; background: none; color: var(--ink3); cursor: pointer; font-size: 11px; }
.lib-select { height: 34px; padding: 0 9px; border: 1px solid var(--line); border-radius: 9px; background: var(--card); font: inherit; font-size: 11.5px; color: var(--ink2); }
.lib-refresh { height: 34px; padding: 0 11px; border: 1px solid var(--line); border-radius: 9px; background: var(--card); font: inherit; font-size: 11.5px; color: var(--ink2); cursor: pointer; }
.lib-refresh:hover { border-color: var(--primary-border); color: var(--brand-deep); }
.lib-count { font-size: 11.5px; color: var(--ink3); white-space: nowrap; }
.lib-count b { color: var(--ink); font-family: var(--font-num); }

.lib-cats { display: flex; gap: 6px; flex-wrap: wrap; margin: 4px 0 12px; }
.lib-cat { padding: 5px 11px; border: 1px solid transparent; border-radius: 999px; background: var(--bg2); color: var(--ink2); font: inherit; font-size: 11px; cursor: pointer; }
.lib-cat i { font-style: normal; margin-left: 4px; font-size: 9.5px; opacity: .7; }
.lib-cat.active { background: var(--brand-soft); border-color: var(--primary-border); color: var(--brand-deep); font-weight: 800; }

.lib-loading { display: flex; justify-content: center; align-items: center; gap: 8px; padding: 32px; color: var(--ink3); font-size: 12.5px; }
.lib-error { padding: 16px; border-radius: 10px; background: var(--err-bg); color: var(--err-deep); font-size: 12.5px; display: flex; align-items: center; gap: 10px; }
.lib-error button { border: 0; background: none; color: inherit; font: inherit; font-weight: 800; cursor: pointer; }
.lib-empty { padding: 34px 20px; text-align: center; border: 1px dashed var(--line); border-radius: var(--radius-lg); background: rgba(255,255,255,.55); }
.lib-empty--sm { padding: 22px; }
.lib-empty-ic { font-size: 30px; opacity: .55; margin-bottom: 8px; }
.lib-empty-title { font-size: 14px; font-weight: 800; color: var(--ink); }
.lib-empty p { max-width: 440px; margin: 6px auto 14px; font-size: 11.5px; line-height: 1.7; color: var(--ink3); }

.doc-group { margin-bottom: 14px; }
.doc-group-head { display: flex; align-items: baseline; gap: 8px; margin: 0 2px 6px; }
.doc-group-head h3 { margin: 0; font-size: 11.5px; font-weight: 800; color: var(--ink2); letter-spacing: .02em; }
.doc-group-head span { font-size: 10px; color: var(--ink3); }
.doc-row {
  display: flex; align-items: center; gap: 11px; padding: 11px 13px; margin-bottom: 7px;
  border: 1px solid var(--line); border-radius: 12px; background: var(--card);
  cursor: pointer; transition: border-color .16s ease, box-shadow .16s ease, transform .16s ease;
}
.doc-row:hover { border-color: var(--primary-border); box-shadow: var(--shadow-sm); }
.doc-row:focus-visible { outline: 2px solid var(--primary-border); outline-offset: 1px; }
.doc-ic { width: 38px; height: 38px; flex: none; display: grid; place-items: center; border-radius: 10px; background: var(--bg2); font-size: 18px; }
.doc-ic.image { background: var(--cyan-soft); }
.doc-main { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 3px; }
.doc-title-line { display: flex; align-items: center; gap: 7px; min-width: 0; }
.doc-title { font-size: 12.5px; font-weight: 700; color: var(--ink); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.doc-cat-badge { flex: none; font-size: 9px; font-weight: 800; padding: 1px 6px; border-radius: 999px; background: var(--brand-soft); color: var(--brand-deep); }
.doc-meta { display: flex; align-items: center; gap: 9px; flex-wrap: wrap; font-size: 10px; color: var(--ink3); }
.doc-status { font-weight: 800; }
.doc-status.ok { color: var(--ok-deep); }
.doc-status.doing { color: var(--brand-deep); }
.doc-status.bad { color: var(--err-deep); }
.doc-partial { padding: 0 6px; border-radius: 999px; background: var(--brand-soft); color: var(--brand-deep); font-weight: 700; }
.doc-error { font-size: 10px; color: var(--err-deep); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.doc-ops { display: flex; align-items: center; gap: 5px; flex: none; }
.doc-op { height: 26px; padding: 0 8px; border: 1px solid var(--line); border-radius: 7px; background: var(--card); color: var(--ink2); font: inherit; font-size: 10.5px; cursor: pointer; }
.doc-op:hover:not(:disabled) { border-color: var(--primary-border); color: var(--brand-deep); }
.doc-op.danger { color: var(--err); }
.doc-op:disabled { opacity: .45; cursor: not-allowed; }
.doc-chevron { color: var(--ink3); font-size: 18px; margin-left: 2px; }

.q-list { display: flex; flex-direction: column; gap: 8px; }
.q-card {
  display: flex; align-items: flex-start; gap: 11px; padding: 12px 14px;
  border: 1px solid var(--line); border-radius: 12px; background: var(--card);
  cursor: pointer; transition: border-color .16s ease, box-shadow .16s ease;
}
.q-card:hover { border-color: var(--primary-border); box-shadow: var(--shadow-sm); }
.q-card:focus-visible { outline: 2px solid var(--primary-border); outline-offset: 1px; }
.q-index { flex: none; width: 28px; height: 28px; display: grid; place-items: center; border-radius: 8px; background: var(--bg2); color: var(--ink3); font-size: 10px; font-weight: 900; font-family: var(--font-num); }
.q-card-main { flex: 1; min-width: 0; }
.q-stem-preview { font-size: 12.5px; line-height: 1.65; color: var(--ink); word-break: break-word; }
.q-stem-preview.is-clamp { display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.q-tags { display: flex; align-items: center; gap: 5px; flex-wrap: wrap; margin-top: 7px; }
.q-tag { padding: 1px 7px; border-radius: 999px; background: var(--bg2); color: var(--ink2); font-size: 9.5px; font-weight: 700; }
.q-tag--type { background: var(--brand-soft); color: var(--brand-deep); }
.q-tag--exam { background: var(--warn-bg); color: var(--warn-deep); }
.q-tag--kp { background: var(--cyan-soft); color: var(--cyan-deep); }
.q-tag--table { background: var(--purple-soft); color: var(--purple); }
.q-source { margin-left: auto; font-size: 9.5px; color: var(--ink3); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 44%; }
.q-chevron { flex: none; color: var(--ink3); font-size: 18px; margin-top: 2px; }
.q-load-more { display: flex; align-items: center; justify-content: center; gap: 10px; padding: 14px 0 4px; font-size: 10.5px; color: var(--ink3); }
.q-load-more button { height: 30px; padding: 0 14px; border: 1px solid var(--line); border-radius: 8px; background: var(--card); font: inherit; font-size: 11px; font-weight: 700; color: var(--ink2); cursor: pointer; }
.q-load-more button:hover:not(:disabled) { border-color: var(--primary-border); color: var(--brand-deep); }

@media (max-width: 760px) {
  .lib-head { align-items: flex-start; }
  .lib-stats { width: 100%; }
  .lib-stats > div { flex: 1; min-width: 0; }
  .lib-tabs-hint { display: none; }
  .lib-upload { align-items: flex-start; }
  .lu-actions { width: 100%; justify-content: space-between; }
  .doc-row { align-items: flex-start; }
  .doc-ops .doc-op { display: none; }
  .doc-chevron { display: block; }
  .q-source { max-width: 100%; margin-left: 0; flex-basis: 100%; }
}
</style>
