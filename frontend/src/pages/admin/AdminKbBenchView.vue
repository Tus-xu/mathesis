<template>
  <section class="adm-ws" aria-label="检索试验台">
    <header class="adm-ws-head">
      <h1>检索试验台</h1>
      <p class="adm-ws-desc">本地知识库试点检索（/kb/retrieve）+ 召回评测（/kb/eval/recall）+ 文档列表（/kb/docs）。</p>
    </header>

    <div class="adm-card">
      <h3 class="adm-card-h">试点检索</h3>
      <div class="adm-form">
        <label class="adm-field-row">
          <span class="adm-lbl">查询</span>
          <input v-model="q.query" class="adm-input" placeholder="如：函数单调性的定义" @keyup.enter="runRetrieve" />
        </label>
        <div class="adm-field-row">
          <span class="adm-lbl">参数</span>
          <div class="adm-bench-opts">
            <label class="adm-opt">
              Top K
              <input v-model.number="q.top_k" class="adm-input adm-num" type="number" min="1" max="10" />
            </label>
            <label class="adm-opt">
              内容类型
              <select v-model="q.content_type" class="adm-input">
                <option value="">全部</option>
                <option v-for="ct in contentTypes" :key="ct" :value="ct">{{ ct }}</option>
              </select>
            </label>
            <label class="adm-opt">
              范围
              <select v-model="q.scope" class="adm-input">
                <option value="">按角色默认</option>
                <option value="student">student</option>
                <option value="student,teacher">student,teacher</option>
                <option value="student,teacher,research">全量</option>
              </select>
            </label>
          </div>
        </div>
      </div>
      <div class="adm-actions">
        <button class="adm-btn primary" type="button" :disabled="retrieving" @click="runRetrieve">
          {{ retrieving ? '检索中…' : '检索' }}
        </button>
      </div>

      <div v-if="retrieveResult" class="adm-bench-result">
        <div class="adm-bench-meta">
          <span class="adm-tag" :class="retrieveResult.answerable ? 'ok' : ''">{{ retrieveResult.answerable ? '可作答' : '不可作答' }}</span>
          <span class="adm-tag">scope: {{ retrieveResult.scope }}</span>
          <span class="adm-tag">top1: {{ retrieveResult.gate?.top1_score ?? 0 }} / 阈值 {{ retrieveResult.gate?.threshold ?? 0.35 }}</span>
        </div>
        <div v-if="!retrieveResult.chunks?.length" class="adm-empty">无检索结果</div>
        <div v-for="(c, i) in retrieveResult.chunks" :key="i" class="adm-chunk">
          <div class="adm-chunk-head">
            <span class="adm-chunk-idx">#{{ i + 1 }}</span>
            <span class="adm-tag" :class="c.score >= (retrieveResult.gate?.threshold ?? 0.35) ? 'ok' : ''">score {{ c.score }}</span>
            <span class="adm-tag">raw {{ c.raw_score }}</span>
            <span class="adm-chunk-doc">{{ c.doc_title || '未知文档' }}</span>
          </div>
          <div class="adm-chunk-content">{{ c.content }}</div>
          <div v-if="c.kp_codes?.length" class="adm-chunk-kp">kp: {{ c.kp_codes.join(', ') }}</div>
        </div>
      </div>
    </div>

    <div class="adm-card">
      <div class="adm-card-head">
        <h3 class="adm-card-h">召回评测</h3>
        <button class="adm-btn slim" type="button" :disabled="loadingEval" @click="loadEval">{{ loadingEval ? '加载中…' : '刷新' }}</button>
      </div>
      <div v-if="evalData" class="adm-eval">
        <div class="adm-eval-row"><span>评测集</span><b>{{ evalData.eval_set || '—' }}</b></div>
        <div class="adm-eval-row"><span>Recall@5</span><b>{{ evalData.recall_at_5 }}</b></div>
        <div class="adm-eval-row"><span>MRR</span><b>{{ evalData.mrr }}</b></div>
        <div class="adm-eval-row"><span>运行时间</span><b>{{ evalData.run_at || '—' }}</b></div>
      </div>
      <div v-else-if="!loadingEval" class="adm-empty">暂无评测数据</div>
    </div>

    <div class="adm-card">
      <div class="adm-card-head">
        <h3 class="adm-card-h">文档列表</h3>
        <button class="adm-btn slim" type="button" :disabled="loadingDocs" @click="loadDocs">{{ loadingDocs ? '加载中…' : '刷新' }}</button>
      </div>
      <div v-if="!docs.length && !loadingDocs" class="adm-empty">暂无文档</div>
      <div v-for="d in docs" :key="d.doc_id" class="adm-doc">
        <div class="adm-doc-main">
          <span class="adm-doc-title">{{ d.title }}</span>
          <span class="adm-doc-meta">{{ d.content_type }} · {{ d.batch_id || '无批次' }} · {{ d.status }}</span>
        </div>
        <span class="adm-tag">{{ d.created_at?.slice(0, 10) || '—' }}</span>
      </div>
    </div>
  </section>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { kbApi } from '@/api'
import { useToastStore } from '@/stores/toast'

const toast = useToastStore()
const contentTypes = ['textbook', 'question', 'standard', 'lesson_plan', 'method_card', 'term', 'paper']
const q = reactive({ query: '', top_k: 4, content_type: '', scope: '' })
const retrieving = ref(false)
const retrieveResult = ref(null)
const loadingEval = ref(false)
const evalData = ref(null)
const loadingDocs = ref(false)
const docs = ref([])

async function runRetrieve() {
  if (!q.query.trim()) {
    toast.error('请输入查询内容')
    return
  }
  retrieving.value = true
  retrieveResult.value = null
  try {
    retrieveResult.value = await kbApi.retrieve({
      query: q.query.trim(),
      top_k: q.top_k,
      ...(q.content_type ? { content_type: q.content_type } : {}),
      ...(q.scope ? { scope: q.scope } : {}),
    })
  } catch (e) {
    toast.error(e?.message || '检索失败')
  } finally {
    retrieving.value = false
  }
}

async function loadEval() {
  loadingEval.value = true
  try {
    evalData.value = await kbApi.evalRecall()
  } catch (e) {
    toast.error(e?.message || '读取评测失败')
  } finally {
    loadingEval.value = false
  }
}

async function loadDocs() {
  loadingDocs.value = true
  try {
    const d = await kbApi.docs({ page: 1, size: 20 })
    docs.value = d?.items || []
  } catch (e) {
    toast.error(e?.message || '读取文档失败')
  } finally {
    loadingDocs.value = false
  }
}

onMounted(() => {
  loadEval()
  loadDocs()
})
</script>

<style scoped>
.adm-ws-head h1 { font-size: var(--text-2xl); margin: 0 0 4px; }
.adm-ws-desc { color: var(--text-secondary); margin: 0 0 20px; font-size: var(--text-sm); }
.adm-card { background: var(--card); border: 1px solid var(--border); border-radius: var(--radius-lg); padding: 18px; box-shadow: var(--shadow-sm); margin-bottom: 16px; }
.adm-card-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; }
.adm-card-h { margin: 0; font-size: var(--text-base); }
.adm-form { display: flex; flex-direction: column; gap: 10px; }
.adm-field-row { display: flex; align-items: center; gap: 12px; }
.adm-lbl { width: 110px; flex: none; color: var(--text-secondary); font-size: var(--text-sm); }
.adm-input { flex: 1; padding: 8px 12px; border: 1px solid var(--border); border-radius: var(--radius-sm); font-size: var(--text-sm); background: var(--card); }
.adm-num { max-width: 90px; }
.adm-bench-opts { flex: 1; display: flex; gap: 14px; flex-wrap: wrap; }
.adm-opt { display: flex; align-items: center; gap: 6px; font-size: var(--text-xs); color: var(--text-secondary); }
.adm-actions { display: flex; gap: 10px; margin-top: 14px; }
.adm-btn { padding: 8px 18px; border-radius: var(--radius-sm); border: 1px solid var(--border); background: var(--card); cursor: pointer; font-size: var(--text-sm); }
.adm-btn.slim { padding: 4px 10px; font-size: var(--text-xs); }
.adm-btn.primary { background: var(--primary); border-color: var(--primary); color: #fff; }
.adm-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.adm-tag { padding: 2px 10px; border-radius: var(--radius-full); background: var(--bg-subtle); font-size: var(--text-xs); }
.adm-tag.ok { background: var(--ok-bg); color: var(--ok-deep); }
.adm-empty { color: var(--text-muted); font-size: var(--text-sm); padding: 12px 0; }
.adm-bench-result { margin-top: 14px; border-top: 1px solid var(--border); padding-top: 12px; display: flex; flex-direction: column; gap: 10px; }
.adm-bench-meta { display: flex; gap: 8px; flex-wrap: wrap; }
.adm-chunk { border: 1px solid var(--border); border-radius: var(--radius-sm); padding: 10px 12px; }
.adm-chunk-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.adm-chunk-idx { font-weight: var(--font-semibold); font-size: var(--text-sm); }
.adm-chunk-doc { margin-left: auto; color: var(--text-secondary); font-size: var(--text-xs); }
.adm-chunk-content { margin-top: 6px; font-size: var(--text-sm); color: var(--text-primary); line-height: 1.6; }
.adm-chunk-kp { margin-top: 6px; font-size: var(--text-xs); color: var(--text-muted); }
.adm-eval { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; }
.adm-eval-row { display: flex; flex-direction: column; gap: 4px; }
.adm-eval-row span { font-size: var(--text-xs); color: var(--text-secondary); }
.adm-eval-row b { font-size: var(--text-base); }
.adm-doc { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 10px 0; border-bottom: 1px solid var(--border); }
.adm-doc:last-child { border-bottom: none; }
.adm-doc-main { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.adm-doc-title { font-size: var(--text-sm); font-weight: var(--font-medium); }
.adm-doc-meta { font-size: var(--text-xs); color: var(--text-secondary); }
</style>
