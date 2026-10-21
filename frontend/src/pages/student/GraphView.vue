<template>
  <div class="view">
    <div class="greeting">
      <div class="hello">你的学习版图 · <span style="color:var(--ok-deep);">{{ learnedCount }} / {{ pie.total }}</span> 已学</div>
      <div class="sub" v-if="pieError" style="color:var(--err-deep);">{{ pieError }}</div>
      <div class="sub" v-else-if="pieLoading">正在加载学习版图…</div>
      <div class="sub" v-else>高中数学 {{ chapters.length }} 大章节，{{ pie.total }} 个考点。你已学 <b>{{ learnedCount }} 个</b>，其中 <b style="color:var(--err-deep);">{{ pie.critical.count }} 个</b> 临危（红色），<b>{{ pie.mastered.count }} 个</b> 稳了（绿色）。圆=基础，菱形=进阶，六边形=拔高。</div>
    </div>

    <div class="kg-controls">
      <div class="left">
        <div class="view-switch">
          <button :class="{ active: viewMode === 'tree' }" @click="switchView('tree')">📚 树形</button>
          <button :class="{ active: viewMode === 'galaxy' }" @click="switchView('galaxy')">🌌 星系</button>
        </div>
        <div class="shape-legend">
          <span><span class="shape-circle"></span>基础</span>
          <span><span class="shape-diamond"></span>进阶</span>
          <span><span class="shape-hex"></span>拔高</span>
        </div>
      </div>
      <div class="filters">
        <span class="filter err">🔴 {{ pie.critical.count }} 临危</span>
        <span class="filter warn">🟡 {{ pie.consolidating.count }} 巩固</span>
        <span class="filter ok">🟢 {{ pie.mastered.count }} 已稳</span>
        <span class="filter">未学 {{ pie.unlearned.count }}</span>
      </div>
    </div>

    <div class="kg-wrap">
      <div v-if="viewMode === 'tree'" class="kg-tree">
        <div v-if="treeLoading" style="padding:24px;color:var(--ink3);font-size:13px;">正在加载知识图谱…</div>
        <div v-else-if="treeError" style="padding:24px;color:var(--err-deep);font-size:13px;">{{ treeError }}</div>
        <div v-else-if="!chapters.length" style="padding:24px;color:var(--ink3);font-size:13px;">暂无知识点数据，完成首次练习后将生成你的学习版图。</div>
        <template v-else>
          <div v-for="ch in chapters" :key="ch.chap" class="ch-card" :class="{ folded: !isChapterOpen(ch) }">
            <div class="ch-head" @click="toggleChapter(ch)">
              <span class="ch-arrow">{{ isChapterOpen(ch) ? '▾' : '▸' }}</span>
              <span class="ch-name">{{ ch.title }}</span>
              <span v-if="chapterStarted(ch).length" class="ch-progress">
                <span class="ch-bar"><span class="ch-bar-fill" :style="{ width: chapterPct(ch) + '%' }"></span></span>
                <span class="ch-num">{{ chapterStarted(ch).length }}/{{ ch.nodes.length }}</span>
              </span>
              <span v-else class="ch-unlearned">未学 {{ ch.nodes.length }} 个考点</span>
            </div>
            <div v-if="isChapterOpen(ch)" class="ch-chips">
              <button
                v-for="n in chapterSorted(ch)" :key="n.kp_code"
                class="ch-chip" :class="[n.state, { selected: selected?.kp_code === n.kp_code }]"
                :title="n.kp_name || n.kp_code"
                @click="selectNode(n, ch)"
              >
                <span class="chip-dot"></span>{{ stripLatex(n.name) }}<span v-if="n.mastery != null" class="chip-pct">{{ Math.round(n.mastery * 100) }}%</span>
              </button>
            </div>
          </div>
        </template>
      </div>

      <div v-if="viewMode === 'galaxy'" class="kg-galaxy">
        <h4>🌌 知识点星系 · 一个环=一大章节，颜色=掌握度，点击节点联动右侧推荐</h4>
        <svg class="galaxy-svg" viewBox="0 0 640 480">
          <text x="320" y="246" text-anchor="middle" font-size="12" font-weight="800" fill="#9aa1ac">数学<br/><tspan x="320" dy="14">宇宙</tspan></text>
          <g v-for="(c, ci) in galaxyChapters" :key="c.chap">
            <circle cx="320" cy="240" :r="44 + ci * 78" fill="none" stroke="#e6eaf0" stroke-dasharray="3 4" />
            <text :x="320 + (44 + ci * 78) + 12" y="246" font-size="10" fill="#9aa1ac" font-weight="700">{{ (c.title || '').slice(0, 6) }}</text>
          </g>
          <g
            v-for="n in galaxyNodes" :key="n.kp_code"
            :transform="`translate(${n.x}, ${n.y})`"
            class="galaxy-node" :class="[n.state, { selected: selected?.kp_code === n.kp_code }]"
            :title="n.name"
            @click="galaxySelect(n)"
          >
            <circle :r="n.r" :fill="n.color" opacity="0.92" />
            <text y="3.5" text-anchor="middle" font-size="9" font-weight="700" fill="#fff">{{ n.short }}</text>
          </g>
        </svg>
        <div class="galaxy-legend">
          <span v-for="s in ['mastered','improving','weak','unlearned']" :key="s" class="legend-item">
            <span class="swatch" :style="{ background: STATE_COLOR[s] }"></span>{{ STATE_LABEL[s] }}
          </span>
        </div>
        <div style="font-size:12px;color:var(--ink2);margin-top:8px;">内环为基础知识，外环为进阶/拔高；点击任意节点查看追根溯源与下一步推荐。</div>
      </div>
      <div>
        <!-- ALEKS Pie -->
        <div class="kg-pie">
          <h4>🥧 我的学习版图 · ALEKS Pie</h4>
          <div v-if="pieLoading" style="padding:32px 0;text-align:center;color:var(--ink3);font-size:13px;">加载中…</div>
          <div v-else-if="pieError" style="padding:32px 0;text-align:center;color:var(--err-deep);font-size:13px;">{{ pieError }}</div>
          <div v-else-if="!pie.total" style="padding:32px 0;text-align:center;color:var(--ink3);font-size:13px;">暂无知识点，完成首次练习后生成版图。</div>
          <template v-else>
            <svg class="pie-svg" viewBox="0 0 200 200">
              <circle cx="100" cy="100" r="80" fill="#f1f5f9"/>
              <path v-if="masteredArc" :d="masteredArc" fill="#16a34a"/>
              <circle cx="100" cy="100" r="50" fill="#ffffff"/>
              <text x="100" y="95" font-size="36" font-weight="900" fill="#0f172a" text-anchor="middle" font-family="Inter">{{ masteredPct }}%</text>
              <text x="100" y="115" font-size="11" fill="#94a3b8" text-anchor="middle" font-weight="700">{{ pie.center_text }} · {{ pie.mastered.count }}/{{ pie.total }}</text>
              <text x="100" y="135" font-size="10" fill="#6366f1" text-anchor="middle" font-weight="700">{{ pie.mastered.count + pie.consolidating.count + pie.critical.count }} / {{ pie.total }} 已启动</text>
            </svg>
            <div style="font-size:12px;color:var(--ink2);line-height:1.6;padding:10px;background:var(--bg2);border-radius:8px;margin-top:8px;">
              <template v-if="etaReady">
                💡 <b>填饼到 50%</b>约需 {{ pie.eta.to_50pct_weeks }} 周，坚持练习即可达成。<br/>
                <b>填饼到 80%</b>约需 {{ pie.eta.to_80pct_weeks }} 周——按当前节奏稳步推进。
              </template>
              <template v-else>💡 完成首次练习后给出掌握时间预测。</template>
            </div>
          </template>
        </div>

        <!-- 节点详情（当前选中） -->
        <div class="node-detail" style="margin-top:14px;">
          <div v-if="!selected" style="padding:24px;color:var(--ink3);font-size:13px;">点击左侧任意知识点，查看追根溯源与学习推荐。</div>
          <template v-else>
            <div class="head">
              <div>
                <div class="title">{{ selected.name }}</div>
                <div class="ch">{{ selected.ch }}</div>
              </div>
              <div class="level" :style="{ color: selected.levelColor }">{{ selected.masteryPct }}<small style="font-size:13px;color:var(--ink3);font-weight:600;">%</small></div>
            </div>
            <div v-if="detailLoading" style="padding:16px;color:var(--ink3);font-size:13px;">正在分析依赖链…</div>
            <div v-else-if="detailError" style="padding:16px;color:var(--err-deep);font-size:13px;">{{ detailError }}</div>
            <template v-else>
              <div class="prereq">
                <h6>📍 追根溯源 · 你之所以卡这，是因这些还没稳</h6>
                <div class="chain" v-if="chainHtml" v-html="chainHtml"></div>
                <div class="chain" v-else style="color:var(--ink2);font-size:13px;">这是基础点，没有前置依赖，直接练即可。</div>
              </div>
              <div class="next-step">
                <h6>🎯 下一步推荐</h6>
                <p>{{ recommend.reason || '建议通过练题中心针对训练，提升掌握度。' }}</p>
              </div>
              <!-- S12 error_related：节点关联的学生真实错题（点击直达引导重解） -->
              <div v-if="nodeErrors.total" class="prereq" style="margin-top:10px;">
                <h6>📕 我在这个知识点的错题（{{ nodeErrors.total }}）</h6>
                <div
                  v-for="e in nodeErrors.items" :key="e.record_id"
                  class="chain"
                  style="cursor:pointer;padding:6px 8px;border-radius:8px;background:var(--brand-bg,#fff7e6);margin-top:6px;"
                  :title="e.question_text"
                  @click="redoFromGraph(e)"
                >🎯 {{ e.question_text.slice(0, 40) }}{{ e.question_text.length > 40 ? '…' : '' }}</div>
              </div>
              <div class="actions">
                <button class="primary" @click="goRecommend">→ {{ recommend.action_label || '开始学习' }}{{ recommend.minutes ? `（约${recommend.minutes}min）` : '' }}</button>
                <button @click="goPractice">直接练</button>
              </div>
            </template>
          </template>
        </div>

        <div class="kg-tips">
          <div class="lbl">💡 学法提示</div>
          <p>高中数学的知识是<b>"链式依赖"</b>的——<b>导数</b>依赖<b>函数</b>，<b>数列</b>依赖<b>函数</b>，<b>解析几何</b>依赖<b>函数+向量</b>。优先把红色临危节点的前置基础补稳，比直接攻难题效率高得多。</p>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { api, ApiError } from '@/api/client'
import { useToastStore } from '@/stores/toast'

const toast = useToastStore()
const router = useRouter()

/* ===== ALEKS 饼图：GET /api/student/knowledge-graph/pie ===== */
const emptyBucket = { count: 0, ratio: 0 }
const pie = ref({
  total: 0,
  mastered: { ...emptyBucket },
  consolidating: { ...emptyBucket },
  critical: { ...emptyBucket },
  unlearned: { ...emptyBucket },
  center_text: '',
  eta: { to_50pct_weeks: null, to_80pct_weeks: null },
})
const pieLoading = ref(true)
const pieError = ref('')

const learnedCount = computed(() => Math.max(0, pie.value.total - pie.value.unlearned.count))
const masteredPct = computed(() => (pie.value.total ? Math.round(pie.value.mastered.ratio * 100) : 0))
const gapTo80 = computed(() => Math.max(0, Math.ceil(pie.value.total * 0.8) - pie.value.mastered.count))
const etaReady = computed(() => pie.value.eta?.to_50pct_weeks != null && pie.value.eta?.to_80pct_weeks != null)

/** 顶部起笔的环形扇形 path；ratio<=0 不画，>=1 画整圆 */
const masteredArc = computed(() => {
  const r = pie.value.mastered.ratio
  if (!r || r <= 0) return ''
  if (r >= 1) {
    return 'M 100 20 A 80 80 0 1 1 99.99 20 Z'
  }
  const angle = r * 2 * Math.PI
  const x = 100 + 80 * Math.sin(angle)
  const y = 100 - 80 * Math.cos(angle)
  const large = r > 0.5 ? 1 : 0
  return `M 100 20 A 80 80 0 ${large} 1 ${x.toFixed(1)} ${y.toFixed(1)} L 100 100 Z`
})

/* ===== 章节树：GET /api/student/knowledge-graph/tree ===== */
const chapters = ref([])

// ===== S8（V2 文档）：章节卡片化——未学章节默认折叠，薄弱考点排前 =====
const openChapters = ref(new Set())
const initialised = ref(false)
// B2（V2 文档）：节点名 LaTeX 源码剥壳（$n$→n、arphi→φ），禁止 $ 残留进 chip
function stripLatex(s) {
  let t = String(s || '')
  const CMD = { varphi: 'φ', pi: 'π', alpha: 'α', beta: 'β', theta: 'θ', omega: 'ω', Delta: 'Δ', times: '×', ge: '≥', le: '≤' }
  t = t.replace(/\$\\([A-Za-z]+)\$/g, (_m, cmd) => CMD[cmd] || cmd)
  t = t.replace(/\$([^$]*)\$/g, '$1')
  return t
}
function chapterStarted(ch) {
  return (ch.nodes || []).filter((n) => n.state && n.state !== 'unlearned')
}
function chapterPct(ch) {
  const started = chapterStarted(ch)
  if (!ch.nodes?.length) return 0
  return Math.round((started.length / ch.nodes.length) * 100)
}
function chapterSorted(ch) {
  const rank = { weak: 0, improving: 1, mastered: 2, unlearned: 3 }
  return [...(ch.nodes || [])].sort((x, y) => (rank[x.state] ?? 9) - (rank[y.state] ?? 9))
}
function isChapterOpen(ch) {
  if (!initialised.value) return chapterStarted(ch).length > 0
  return openChapters.value.has(ch.chap)
}
function toggleChapter(ch) {
  const s = new Set(openChapters.value)
  if (s.has(ch.chap)) s.delete(ch.chap)
  else s.add(ch.chap)
  openChapters.value = s
}
const treeLoading = ref(true)
const treeError = ref('')

const STATE_COLOR = {
  mastered: 'var(--ok)',
  improving: 'var(--warn)',
  weak: 'var(--err)',
  unlearned: 'var(--ink3)',
}
const SHAPE_LABEL = { circle: '● 基础', diamond: '◆ 进阶', hex: '⬡ 拔高' }
const STATE_LABEL = { mastered: '绿', improving: '黄', weak: '红', unlearned: '未学' }

function masteryText(m) {
  return m == null ? '未学' : `${Math.round(m * 100)}%`
}

/* ===== 节点详情：deps + recommend ===== */
const selected = ref(null)
const detailLoading = ref(false)
const detailError = ref('')
const deps = ref(null)
const recommend = ref({})
const nodeErrors = ref({ total: 0, items: [] })

const chainHtml = computed(() => {
  const chain = deps.value?.chain || []
  // 契约：chain 仅含自身 = 基础点，走"直接练"空态文案
  if (chain.length <= 1) return ''
  return chain
    .map((c) => {
      const color = c.state === 'mastered' ? 'var(--ok-deep)' : c.state === 'improving' ? 'var(--warn-deep)' : 'var(--err-deep)'
      return `<b style="color:${color};">${c.kp_name || c.kp_code}</b>（${masteryText(c.mastery)} ${STATE_LABEL[c.state] || ''}）`
    })
    .join(' <span class="arrow">→</span> ')
})

async function loadPie() {
  pieLoading.value = true
  pieError.value = ''
  try {
    pie.value = await api.get('/student/knowledge-graph/pie')
  } catch (e) {
    pieError.value = e instanceof ApiError ? `学习版图加载失败：${e.message}` : '学习版图加载失败'
  } finally {
    pieLoading.value = false
  }
}

async function loadTree() {
  treeLoading.value = true
  treeError.value = ''
  try {
    const data = await api.get('/student/knowledge-graph/tree')
    chapters.value = data?.chapters || []
    // S8：默认展开"有启动考点"的章节，全未学的折叠
    openChapters.value = new Set(chapters.value.filter((c) => chapterStarted(c).length).map((c) => c.chap))
  initialised.value = true
    autoSelect()
  } catch (e) {
    treeError.value = e instanceof ApiError ? `知识图谱加载失败：${e.message}` : '知识图谱加载失败'
  } finally {
    treeLoading.value = false
  }
}

/** 默认选中链上最弱的非"已稳"节点（weak > improving > unlearned > 首个） */
function autoSelect() {
  for (const state of ['weak', 'improving', 'unlearned']) {
    for (const ch of chapters.value) {
      const n = ch.nodes.find((x) => x.state === state)
      if (n) return selectNode(n, ch)
    }
  }
  const ch = chapters.value[0]
  if (ch?.nodes?.length) selectNode(ch.nodes[0], ch)
}

async function selectNode(n, ch) {
  selected.value = {
    kp_code: n.kp_code,
    name: n.name,
    ch: `${ch.title} · ${SHAPE_LABEL[n.shape] || ''}`,
    masteryPct: n.mastery == null ? 0 : Math.round(n.mastery * 100),
    levelColor: STATE_COLOR[n.state] || 'var(--ink3)',
  }
  detailLoading.value = true
  detailError.value = ''
  deps.value = null
  recommend.value = {}
  nodeErrors.value = { total: 0, items: [] }
  try {
    const [d, r, errs] = await Promise.all([
      api.get(`/student/knowledge-graph/nodes/${encodeURIComponent(n.kp_code)}/deps`),
      api.get(`/student/knowledge-graph/nodes/${encodeURIComponent(n.kp_code)}/recommend`),
      api.get(`/student/knowledge-graph/nodes/${encodeURIComponent(n.kp_code)}/errors`).catch(() => null),
    ])
    // 防止快速连点时的乱序覆盖
    if (selected.value?.kp_code !== n.kp_code) return
    deps.value = d
    recommend.value = r || {}
    nodeErrors.value = errs || { total: 0, items: [] }  // 错题块 best-effort：旧后端无此端点时静默隐藏
  } catch (e) {
    if (selected.value?.kp_code !== n.kp_code) return
    detailError.value = e instanceof ApiError ? `节点详情加载失败：${e.message}` : '节点详情加载失败'
  } finally {
    if (selected.value?.kp_code === n.kp_code) detailLoading.value = false
  }
}

function goRecommend() {
  const route = recommend.value?.route
  router.push(route || '/practice')
}

// 迭代18 修复：从知识图谱"直接练"带选中知识点，练题中心据此定向出题
function goPractice() {
  const kp = selected.value?.kp_code
  router.push(kp ? '/practice?kp=' + encodeURIComponent(kp) : '/practice')
}

// S12 error_related：点击节点错题 → 引导重解深链（与错题本 redoWithSocratic 同款入口）
function redoFromGraph(e) {
  if (!e?.question_text) return
  router.push('/dialog?explain=' + encodeURIComponent(e.question_text))
}

const viewMode = ref('tree')
function switchView(mode) { viewMode.value = mode }

/* 星系视图：按章节分层环 + 放射分布，节点颜色/大小映射掌握度与层级 */
const galaxyChapters = computed(() => chapters.value.map((ch, ci) => ({ ...ch, ci })))
function galaxyRadius(ci) { return 44 + ci * 78 }
const galaxyNodes = computed(() => {
  const nodes = []
  chapters.value.forEach((ch, ci) => {
    const R = galaxyRadius(ci) + 22
    const n = ch.nodes || []
    n.forEach((node, i) => {
      const angle = (i / Math.max(1, n.length)) * Math.PI * 2 - Math.PI / 2
      nodes.push({
        ...node,
        x: 320 + R * Math.cos(angle),
        y: 240 + R * Math.sin(angle),
        r: node.shape === 'circle' ? 10 : node.shape === 'diamond' ? 12 : 14,
        color: STATE_COLOR[node.state] || 'var(--ink3)',
        short: (node.name || '').slice(0, 4),
      })
    })
  })
  return nodes
})
function galaxySelect(n) {
  const ch = chapters.value.find((c) => (c.nodes || []).some((x) => x.kp_code === n.kp_code))
  if (!ch) return
  selectNode(n, ch)
}

onMounted(() => {
  loadPie()
  loadTree()
})
</script>

<style scoped>
.tree-node.selected { outline: 2px solid var(--brand); outline-offset: 1px; }
.kg-galaxy { padding: 10px 0 4px; }
.galaxy-svg { width: 100%; max-width: 640px; background: linear-gradient(180deg, #fbfdff, #f6f9ff); border: 1px solid var(--line); border-radius: 12px; }
.galaxy-node { cursor: pointer; }
.galaxy-node.selected circle { stroke: var(--brand, #3b7bff); stroke-width: 2.5; }
.galaxy-legend { display: flex; gap: 14px; margin-top: 8px; font-size: 12px; font-weight: 600; color: var(--ink2); }
.galaxy-legend .swatch { display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 4px; }

/* ===== S8 章节卡片 ===== */
.ch-card { background: var(--card); border: 1px solid var(--line); border-radius: 14px; margin-bottom: 10px; overflow: hidden; }
.ch-card.folded { opacity: .82; }
.ch-head { display: flex; align-items: center; gap: 10px; padding: 12px 14px; cursor: pointer; user-select: none; }
.ch-head:hover { background: var(--bg2); }
.ch-arrow { color: var(--ink3); font-size: 12px; width: 14px; }
.ch-name { font-size: 13.5px; font-weight: 800; color: var(--ink); flex: 0 1 auto; }
.ch-progress { display: flex; align-items: center; gap: 8px; margin-left: auto; }
.ch-bar { width: 90px; height: 6px; border-radius: 99px; background: var(--skeleton-bg, #eef1f8); overflow: hidden; display: inline-block; }
.ch-bar-fill { display: block; height: 100%; background: var(--gradient-brand); border-radius: 99px; }
.ch-num { font-size: 11.5px; color: var(--ink3); font-weight: 700; }
.ch-unlearned { margin-left: auto; font-size: 11.5px; color: var(--ink3); }
.ch-chips { display: flex; flex-wrap: wrap; gap: 8px; padding: 2px 14px 14px; }
.ch-chip {
  display: inline-flex; align-items: center; gap: 6px; padding: 7px 12px;
  border-radius: 999px; border: 1px solid var(--line); background: var(--card);
  font: inherit; font-size: 12.5px; color: var(--ink); cursor: pointer;
}
.ch-chip:hover { border-color: var(--primary-border); box-shadow: var(--shadow-sm); }
.ch-chip.selected { border-color: var(--primary); background: var(--primary-subtle); }
.ch-chip .chip-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--ink4); }
.ch-chip.weak .chip-dot { background: var(--err); }
.ch-chip.improving .chip-dot { background: var(--warn); }
.ch-chip.mastered .chip-dot { background: var(--ok); }
.ch-chip.unlearned { opacity: .62; }
.ch-chip .chip-pct { font-size: 10.5px; color: var(--ink3); font-weight: 700; }
</style>
