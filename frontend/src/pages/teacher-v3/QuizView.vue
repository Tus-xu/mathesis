<template>
  <div class="tv3-page-shell" data-testid="tv3-quiz">
    <TeacherPageHeader title="题库与组卷" subtitle="选题组卷 · 发布到学生课堂任务 · 客观自动判 / 主观人工批" icon="📝">
      <div class="tv3-seg" style="margin-right: 4px" data-testid="tv3-quiz-bank-tabs">
        <router-link to="/teacher-v3/bank" class="tv3-seg__btn" data-testid="tv3-quiz-to-bank">题库</router-link>
        <router-link to="/teacher-v3/quiz" class="tv3-seg__btn is-active" data-testid="tv3-tab-quiz">组卷</router-link>
      </div>
      <span class="tv3-tag tv3-tag--gold">题库选题 · 图片题照常入卷</span>
      <router-link to="/teacher-v3/assign" class="tv3-btn tv3-btn--sm" data-testid="tv3-quiz-to-assign">作业与批改 →</router-link>
    </TeacherPageHeader>

    <div style="display: flex; gap: 14px; align-items: flex-start">
      <!-- 知识点分类树 -->
      <div class="tv3-card" style="width: 252px; flex-shrink: 0">
        <div class="tv3-card__head">
          <span class="tv3-card__title">分类树</span>
          <span class="tv3-card__sub">按知识点定位</span>
        </div>
        <div class="tv3-card__body tv3-treescroll" data-testid="tv3-kp-tree">
          <button class="tv3-tree-row" :class="{ 'is-sel': !selectedKp.id }" data-testid="tv3-kp-all" @click="selectKp({ id: '', name: '', codes: null, depth: 0, leaf: false, count: 0 })">
            全部知识点 <span class="tv3-tree-row__cnt">{{ questions.length }}</span>
          </button>
          <template v-for="n in treeItems" :key="n.id">
            <button class="tv3-tree-row" :class="['lv' + n.depth, { 'is-sel': selectedKp.id === n.id }]" :data-testid="`tv3-kp-${n.id}`" @click="selectKp(n)">
              <span class="tv3-tree-row__folder" v-if="n.codes === null">{{ n.leaf ? '▸' : '▾' }}</span>
              {{ n.name }} <span class="tv3-tree-row__cnt">{{ cntOf(n) }}</span>
            </button>
          </template>
        </div>
      </div>

      <!-- 题库 -->
      <div class="tv3-card" style="flex: 1; min-width: 0">
        <div class="tv3-card__head">
          <span class="tv3-card__title">题库</span>
          <span class="tv3-card__sub">{{ selectedKp.name || '全部知识点' }} · {{ filtered.length }} / {{ questions.length }} 题</span>
          <div class="tv3-card__spacer" />
          <select v-model="diffFilter" class="tv3-input" style="width: 110px">
            <option value="">全部难度</option>
            <option value="easy">容易</option>
            <option value="medium">中等</option>
            <option value="hard">较难</option>
          </select>
          <button class="tv3-btn tv3-btn--sm tv3-btn--gold" data-testid="tv3-scan-open" @click="scanOpen = true">＋ 扫描入库</button>
        </div>
        <div class="tv3-card__body tv3-qscroll" data-testid="tv3-quiz-list">
          <TeacherLoading v-if="loading" />
          <TeacherEmptyState
            v-else-if="!filtered.length"
            :title="questions.length ? '暂无匹配题目' : '题库为空'"
            :desc="questions.length ? '当前分类下暂无题目，试试切换知识点或调整难度筛选。' : '题库为空，可「扫描入库」添加题目，或先去题库录入。'"
            :cta="questions.length ? '' : '＋ 扫描入库'"
            @cta-click="scanOpen = true"
          />
          <div v-for="q in filtered" :key="q.id" class="tv3-qrow" :class="{ 'is-picked': paper.includes(q.id), 'tv3-qrow--image': q.q_type === 'image' }" :data-testid="`tv3-quiz-q-${q.id}`">
            <button class="tv3-qrow__pick" :data-testid="`tv3-pick-${q.id}`" @click="togglePick(q.id)">
              {{ paper.includes(q.id) ? '✓' : '＋' }}
            </button>
            <div style="flex: 1; min-width: 0">
              <div style="display: flex; gap: 6px; align-items: center; flex-wrap: wrap">
                <span class="tv3-tag" :class="q.difficulty === 'easy' ? 'tv3-tag--ok' : q.difficulty === 'hard' ? 'tv3-tag--danger' : 'tv3-tag--warn'">{{ diffLabel(q.difficulty) }}</span>
                <span class="tv3-tag tv3-tag--primary">{{ q.kp_name }}</span>
                <span v-if="q.q_type === 'image'" class="tv3-tag tv3-tag--gold">图片题</span>
                <span class="tv3-tag" :class="q.source === '拍照入库' ? 'tv3-tag--gold' : ''">{{ q.source }}</span>
              </div>
              <!-- 图片题型：题干为扫描原图 -->
              <div v-if="q.q_type === 'image' && q.stem_image" class="tv3-qrow__img">
                <img :src="q.stem_image" alt="题干图" data-testid="tv3-q-stem-image" />
                <span class="tv3-qrow__img-cap">题干为扫描原图</span>
              </div>
              <div class="tv3-qrow__stem" v-html="stemOf(q)" />
              <div v-if="q.options" class="tv3-qrow__opts">
                <span v-for="(o, i) in q.options" :key="i" class="tv3-qrow__opt" :class="{ 'is-answer': q.answer === String.fromCharCode(65 + i) }" v-html="renderStem(o)" />
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- 试卷预览 -->
      <div class="tv3-card" style="width: 400px; flex-shrink: 0">
        <div class="tv3-card__head">
          <span class="tv3-card__title">试卷预览</span>
          <span class="tv3-card__sub">A4 · {{ paperQuestions.length }} 题</span>
          <div class="tv3-card__spacer" />
          <button class="tv3-btn tv3-btn--sm" :disabled="!paperQuestions.length" data-testid="tv3-paper-clear" @click="paper = []">清空</button>
          <button class="tv3-btn tv3-btn--sm tv3-btn--primary" :disabled="!paperQuestions.length" data-testid="tv3-paper-export" @click="exportPaper">导出 PDF</button>
          <button class="tv3-btn tv3-btn--sm tv3-btn--gold" :disabled="!paperQuestions.length" data-testid="tv3-paper-publish" @click="openPublish">发布为作业</button>
        </div>
        <div class="tv3-card__body" style="padding: 0">
          <div class="tv3-paper" data-testid="tv3-paper-preview">
            <div class="tv3-paper__head">
              <div class="tv3-paper__school">＿＿＿＿中学 2026 学年第一学期</div>
              <div class="tv3-paper__title">数学单元练习卷</div>
              <div class="tv3-paper__meta">班级＿＿＿ 姓名＿＿＿ 学号＿＿＿ 成绩＿＿＿</div>
            </div>
            <div v-if="!paperQuestions.length" class="tv3-paper__empty">从左侧题库点「＋」选题，试卷实时排版</div>
            <div v-for="(q, i) in paperQuestions" :key="q.id" class="tv3-paper__q">
              <div class="tv3-paper__qhead" data-testid="tv3-paper-qhead">
                <b>{{ i + 1 }}.</b>
                <span class="tv3-paper__score">（{{ q.q_type === 'choice' ? 5 : q.q_type === 'fill' ? 5 : 12 }} 分）</span>
                <span v-if="q.q_type === 'image'" class="tv3-paper__score">图片题</span>
              </div>
              <div v-if="q.q_type === 'image' && q.stem_image" class="tv3-paper__img">
                <img :src="q.stem_image" alt="题干图" data-testid="tv3-paper-stem-image" />
              </div>
              <div class="tv3-paper__stem" v-html="stemOf(q)" />
              <div v-if="q.options" class="tv3-paper__opts">
                <span v-for="(o, j) in q.options" :key="j" class="tv3-paper__opt">
                  <b>{{ String.fromCharCode(65 + j) }}.</b> <span v-html="renderStem(o)" />
                </span>
              </div>
              <div v-if="q.q_type === 'solve'" class="tv3-paper__solve">（解答区 · 留白 8 行）</div>
              <button class="tv3-paper__del" :data-testid="`tv3-paper-del-${q.id}`" title="从试卷移除本题" @click="removePaper(q.id)">✕</button>
            </div>
            <div v-if="paperQuestions.length" class="tv3-paper__foot">第 1 页（共 1 页）· 命题：李文澜 · 审题：＿＿＿</div>
          </div>
        </div>
      </div>
    </div>

    <!-- 扫描入库弹层 -->
    <div v-if="scanOpen" class="tv3-modal" data-testid="tv3-scan-modal" @click.self="scanOpen = false">
      <div class="tv3-modal__box" style="width: 480px">
        <div class="tv3-modal__head">
          <span class="tv3-card__title">扫描 / 拍照入库</span>
          <span class="tv3-card__sub">手写或试卷原图 → 存入题库</span>
        </div>
        <div class="tv3-modal__body" style="display: flex; flex-direction: column; gap: 12px">
          <div class="tv3-form-label">1. 上传图片 <span style="color:var(--tv3-ink4);font-weight:400">题干与解答分开传，原样保留（解答可后补）</span></div>
          <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 10px">
            <label class="tv3-upload" data-testid="tv3-scan-file" @dragover.prevent @drop.prevent="onFileDrop($event)">
              <input type="file" accept="image/*" hidden @change="onFilePick($event)" data-testid="tv3-scan-input" />
              <span v-if="!scanSrc" style="font-weight:600;color:var(--tv3-ink2)">① 题干图（必传）</span>
              <span v-if="!scanSrc" class="tv3-upload__hint">{{ dragHint }}</span>
              <img v-else :src="scanSrc" alt="题干扫描原图" data-testid="tv3-scan-preview" style="max-height: 150px; border-radius: 8px" />
            </label>
            <label class="tv3-upload" data-testid="tv3-scan-sol-file">
              <input type="file" accept="image/*" hidden @change="onSolPick($event)" data-testid="tv3-scan-sol-input" />
              <span v-if="!scanSolSrc" style="font-weight:600;color:var(--tv3-ink2)">② 解答过程图（选传）</span>
              <span v-if="!scanSolSrc" class="tv3-upload__hint">详细解答 / 手写过程，可入库后补</span>
              <img v-else :src="scanSolSrc" alt="解答过程原图" data-testid="tv3-scan-sol-preview" style="max-height: 150px; border-radius: 8px" />
            </label>
          </div>
          <div style="display:flex;gap:8px">
            <button class="tv3-btn tv3-btn--sm tv3-btn--ghost" data-testid="tv3-scan-sample" @click="useSample">用示例题图</button>
            <button v-if="scanSolSrc" class="tv3-btn tv3-btn--sm" @click="scanSolSrc = ''">移除解答图</button>
          </div>

          <div class="tv3-form-label">2. 入库方式</div>
          <div class="tv3-radio-row">
            <label class="tv3-radio"><input type="radio" value="image" v-model="mode" data-testid="tv3-scan-mode-image" /> 图片题型原样入库<span class="tv3-radio__hint">保留扫描原图作题干，不强制识别</span></label>
            <label class="tv3-radio"><input type="radio" value="recognize" v-model="mode" data-testid="tv3-scan-mode-recognize" /> 识别成结构题<span class="tv3-radio__hint">走识别链路，进入可编辑的公式题干</span></label>
          </div>

          <div class="tv3-form-label">3. 归属知识点 <span style="color:var(--tv3-ink4);font-weight:400">可让 AI 先认，再手动纠正</span></div>
          <select v-model="scanKpCode" class="tv3-input tv3-input--block" data-testid="tv3-scan-kp">
            <option v-for="k in kpLeafOptions" :key="k.code" :value="k.code">{{ k.path }}</option>
          </select>
          <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
            <button class="tv3-btn tv3-btn--sm tv3-btn--ghost-ai" :disabled="suggestLoading || (!scanSrc && !scanSolSrc)" data-testid="tv3-scan-suggest" @click="runSuggest">
              {{ suggestLoading ? '识别中…' : '✦ AI 识别知识点' }}
            </button>
            <template v-if="suggest">
              <button class="tv3-btn tv3-btn--sm tv3-btn--gold" data-testid="tv3-suggest-apply" @click="scanKpCode = suggest.suggestion.code">
                ✓ {{ suggest.suggestion.path.join(' ▸ ') }}（{{ (suggest.suggestion.confidence * 100).toFixed(0) }}%）
              </button>
              <button v-for="a in suggest.alternates" :key="a.code" class="tv3-btn tv3-btn--sm" :data-testid="`tv3-suggest-alt-${a.code}`" @click="scanKpCode = a.code">
                {{ a.path.join(' ▸ ') }}
              </button>
            </template>
          </div>

          <div class="tv3-modal__foot">
            <button class="tv3-btn tv3-btn--sm" @click="scanOpen = false">取消</button>
            <button class="tv3-btn tv3-btn--sm tv3-btn--primary" :disabled="!scanSrc" data-testid="tv3-scan-import" @click="doScanImport">确认入库</button>
          </div>
        </div>
      </div>
    </div>
  </div>

  <!-- B4 · 发布为作业（真实 8000 班级作业：draft → published，学生课堂任务可见） -->
  <div v-if="publishOpen" class="tv3-push" @click.self="publishOpen = false">
    <div class="tv3-card tv3-push__panel" style="max-width: 620px" data-testid="tv3-publish-panel">
      <div class="tv3-card__head">
        <span class="tv3-card__title">发布到学生课堂任务 · {{ paperQuestions.length }} 题</span>
        <div class="tv3-card__spacer" />
        <button class="tv3-btn tv3-btn--sm" @click="publishOpen = false">×</button>
      </div>
      <div class="tv3-card__body" style="display: flex; flex-direction: column; gap: 12px">
        <!-- 发布前检查 -->
        <div style="padding: 8px 10px; border-radius: 10px; background: var(--tv3-bg2); display: flex; flex-direction: column; gap: 4px" data-testid="tv3-publish-checks">
          <div style="font-size: 11.5px; font-weight: 700">发布前检查</div>
          <div style="font-size: 11.5px; color: var(--tv3-ink2)">✓ 题目数：{{ paperQuestions.length }}</div>
          <div v-for="w in publishWarnings" :key="w.text" style="font-size: 11.5px" :style="{ color: w.block ? '#b1382c' : '#b45309' }">
            {{ w.block ? '✗' : '⚠' }} {{ w.text }}
          </div>
        </div>
        <div class="tv3-form-label">班级（仅展示我作为教师 / 助教的班级）</div>
        <select v-model="pubForm.class_id" class="tv3-input" data-testid="tv3-publish-class" :disabled="!pubClasses.length">
          <option v-if="!pubClasses.length" value="">暂无教师身份班级 —— 请先建班或让班主任添加你为教师</option>
          <option v-for="c in pubClasses" :key="c.class_id" :value="c.class_id">{{ c.name }}（{{ c.students }} 人）</option>
        </select>
        <div class="tv3-form-label">任务类型</div>
        <select v-model="pubForm.type" class="tv3-input" data-testid="tv3-publish-type">
          <option value="homework">课后习题</option>
          <option value="quiz">课堂练习</option>
          <option value="pre_class">学前作业</option>
        </select>
        <div class="tv3-form-label">截止时间</div>
        <input v-model="pubForm.deadline_local" type="datetime-local" class="tv3-input" data-testid="tv3-publish-deadline">
        <div class="tv3-seg">
          <button v-for="d in deadlinePresets" :key="d.label" class="tv3-seg__btn" :class="{ 'is-active': pubForm.deadline_local === d.value }" :data-testid="`tv3-publish-ddl-${d.label}`" @click="pubForm.deadline_local = d.value">{{ d.label }}</button>
        </div>
        <div style="font-size: 11.5px; color: var(--tv3-ink3); padding: 6px 10px; border-radius: 8px; background: var(--tv3-teal-soft, #e8f6f4)">
          发布后学生可在「课堂任务」看到并作答；评分方式：<b>客观题自动判分 + 主观题教师复核</b>。
        </div>
      </div>
      <div class="tv3-modal__foot" style="display: flex; gap: 8px; justify-content: flex-end; align-items: center">
        <span v-if="publishBlockers" style="font-size: 11.5px; color: #b1382c; flex: 1">存在阻断项：请先处理上方标红的题目</span>
        <span v-else-if="!pubClasses.length" style="font-size: 11.5px; color: #b1382c; flex: 1">没有可用于发布的教学班</span>
        <span v-else style="font-size: 11px; color: var(--tv3-ink4); flex: 1">将真实创建并发布到所选班级</span>
        <button class="tv3-btn tv3-btn--sm" @click="publishOpen = false">取消</button>
        <button class="tv3-btn tv3-btn--gold" :disabled="publishBlockers || !pubClasses.length || publishing" data-testid="tv3-publish-confirm" @click="doPublish">{{ publishing ? '发布中…' : `确认发布（${paperQuestions.length} 题）` }}</button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * QuizView —— 组卷中心
 * 分类树筛选（P4）：知识点分类树 → 按 kp_code 集合过滤题目
 * 扫描入库（P4）：手写/试卷原图 → 图片题或识别题，POST 进题库并即时刷新
 * 图片题型（P4）：q_type='image' 时题干为 stem_image 扫描原图，照常可选入试卷
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useToastStore } from '@/stores/toast'
import { TeacherPageHeader, TeacherEmptyState, TeacherLoading } from '@/components/teacherV3/common'
import { updateTv3Context } from '@/stores/teacherContext'
import { setReceipt, registerUndo } from '@/stores/companion'
import type { CompanionCandidate } from '@/pages/teacher-v3/companionData'

/* toast/router 惰性获取（测试环境无 Pinia/Router） */
const toast = { success: (t: string) => dynamicToast().success(t), error: (t: string) => dynamicToast().error(t), info: (t: string) => dynamicToast().info(t) }
function dynamicToast() { return useToastStore() }
const router = { push: (loc: string) => dynamicRouter().push(loc) }
function dynamicRouter() { return useRouter() }
import { v3Api, type V3QuizQuestion, type V3KpTreeNode } from '@/api/teacherV3'
import { renderLatex, renderStem } from '@/components/mathx/latex'
import { presignUpload } from '@/api/teacherV3Upload'

/** 扁平化的分类树节点（用于渲染与筛选）；count=服务器聚合题量（自底向上口径） */
interface TreeItem { id: string; name: string; depth: number; leaf: boolean; codes: string[] | null; count: number }

const loading = ref(true)
const questions = ref<V3QuizQuestion[]>([])
const tree = ref<V3KpTreeNode[]>([])
const diffFilter = ref('')
const paper = ref<string[]>([])
const selectedKp = ref<TreeItem>({ id: '', name: '', depth: 0, leaf: false, codes: null, count: 0 })
const scanOpen = ref(false)
const scanSrc = ref('')
/* M2-C：直传后的对象 key；无 key 时回落 dataURL（示例图/演示） */
const scanKey = ref('')
const scanSolKey = ref('')
const scanSolSrc = ref('')
const scanKpCode = ref('')
const mode = ref<'image' | 'recognize'>('image')
const dragHint = ref('点击或拖入试卷/手写照片')
/* V3.2：AI 识别知识点 */
const suggestLoading = ref(false)
const suggest = ref<{ suggestion: { code: string; path: string[]; name: string; confidence: number }; alternates: { code: string; path: string[]; name: string }[]; note: string } | null>(null)

const treeItems = computed<TreeItem[]>(() => {
  const out: TreeItem[] = []
  const walk = (nodes: V3KpTreeNode[], depth: number) => {
    for (const n of nodes) {
      if (n.children && n.children.length) {
        out.push({ id: n.id, name: n.name, depth, leaf: false, codes: collectCodes(n.children), count: Number(n.count ?? 0) })
        walk(n.children, depth + 1)
      } else {
        out.push({ id: n.id, name: n.name, depth, leaf: true, codes: collectCodes([n]), count: Number(n.count ?? 0) })
      }
    }
  }
  walk(tree.value, 1)
  return out
})

const kpLeafOptions = computed(() => {
  const opts: { code: string; path: string }[] = []
  const walk = (nodes: V3KpTreeNode[], prefix: string[]) => {
    for (const n of nodes) {
      const path = [...prefix, n.name]
      if (n.children && n.children.length) walk(n.children, path)
      else opts.push({ code: kpCodeOf(n), path: path.join(' ▸ ') })
    }
  }
  walk(tree.value, [])
  return opts
})

const filtered = computed(() =>
  questions.value.filter((q) => {
    if (selectedKp.value.codes && !selectedKp.value.codes.includes(q.kp_code)) return false
    if (diffFilter.value && q.difficulty !== diffFilter.value) return false
    return true
  }),
)
const paperQuestions = computed(() => paper.value.map((id) => questions.value.find((q) => q.id === id)!).filter(Boolean))

const diffLabel = (d: string) => ({ easy: '容易', medium: '中等', hard: '较难' } as Record<string, string>)[d] || (d ? d : '未定级')

/** 图片题的题干文字是说明性 caption，不送 KaTeX（避免中文进 math mode 的告警） */
function stemOf(q: V3QuizQuestion): string {
  if (q.q_type === 'image') return escapeHtmlRaw(q.stem_latex ?? '')
  return renderStem(q.stem_latex)
}
function escapeHtmlRaw(t: string): string {
  return t.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

/* B6 上下文写入 + 管家快捷动作 */
watch(paper, () => {
  updateTv3Context({ route: '/teacher-v3/quiz', topic: paper.value.length ? `试卷 ${paper.value.length} 题` : undefined })
})
function onButlerQuick(ev: Event) {
  const action = (ev as CustomEvent).detail?.action
  if (action === 'publish-check') {
    if (!paperQuestions.value.length) toast.info('试卷为空：先从左侧选题，再看发布前清单')
    else openPublish()
  } else if (action === 'scan-open') scanOpen.value = true
}

/* B4：题库/洞察带来的 kp 上下文预筛选（断点修复：之前 query 被忽略） */
onMounted(() => window.addEventListener('tv3-butler-quick', onButlerQuick as EventListener))

/* ---------- C2 伴随工具层：绘图台插入 → 真实「题图入库 + 入卷」（scan-import 同一通道） ---------- */
let companionSeqDone = ''
let lastInsertedId = ''
async function onCompanionInsert(ev: Event) {
  const d = (ev as CustomEvent).detail as { reqId: string; kind: string; draw?: { type: string; src?: string; expr?: string; latex?: string }; candidate?: CompanionCandidate } | undefined
  if (!d || d.reqId === companionSeqDone) return
  /* 图片类图形 → 题图题（原样入库，不转文字）并入卷 */
  if (d.kind === 'figure' && d.draw?.type === 'image' && d.draw.src) {
    try {
      const r = await v3Api.catalog.quizScanImport({ src: d.draw.src, as_image: true, kp_code: 'DRAW-01', kp_name: '绘图插入' })
      const q = r.data as V3QuizQuestion
      questions.value.unshift(q)
      if (!paper.value.includes(q.id)) paper.value.push(q.id)
      companionSeqDone = d.reqId
      lastInsertedId = q.id
      updateTv3Context({ route: '/teacher-v3/quiz', topic: `试卷 ${paper.value.length} 题`, extra: '刚插入：绘图题图' })
      setReceipt({ ok: true, message: `已把绘图落为题图并入卷（${q.kp_name} · 图片题，原样入库）`, locationLabel: '右侧试卷预览', undoLabel: '撤销' })
      registerUndo('撤销', async () => {
        paper.value = paper.value.filter((x) => x !== q.id)
        questions.value = questions.value.filter((x) => x.id !== q.id)
        try { await v3Api.catalog.quizRemove(q.id) } catch { /* mock */ }
      })
      window.dispatchEvent(new CustomEvent('tv3-companion-inserted', { detail: { reqId: d.reqId, handled: true } }))
    } catch {
      window.dispatchEvent(new CustomEvent('tv3-companion-inserted', { detail: { reqId: d.reqId, handled: false } }))
    }
    return
  }
  window.dispatchEvent(new CustomEvent('tv3-companion-inserted', { detail: { reqId: d.reqId, handled: false } }))
}
function onCompanionLocateQuiz() {
  if (!lastInsertedId) return
  nextTickLocate()
}
function nextTickLocate() {
  Promise.resolve().then(() => {
    document.querySelector('[data-testid="tv3-paper-preview"]')?.scrollIntoView({ block: 'center', behavior: 'smooth' })
  })
}
onMounted(() => {
  window.addEventListener('tv3-companion-insert', onCompanionInsert as EventListener)
  window.addEventListener('tv3-companion-locate', onCompanionLocateQuiz as EventListener)
})
onBeforeUnmount(() => {
  window.removeEventListener('tv3-butler-quick', onButlerQuick as EventListener)
  window.removeEventListener('tv3-companion-insert', onCompanionInsert as EventListener)
  window.removeEventListener('tv3-companion-locate', onCompanionLocateQuiz as EventListener)
})
onMounted(async () => {
  const kpQuery = new URLSearchParams(window.location.search).get('kp')

  try {
    try {
      // 2026-10-15 修复「组卷选题池只有 20 题」：此前不传分页参数，后端默认 limit=20
      // → 785 题的题库永远只加载前 20 条。与 BankView 同款分页循环拉全量。
      const PAGE = 100
      const acc: V3QuizQuestion[] = []
      let total = 0
      for (let offset = 0; offset < 5000; offset += PAGE) {
        const qr = await v3Api.catalog.quizQuestions({ limit: PAGE, offset } as Record<string, unknown>)
        total = Number(qr.data.total ?? qr.data.items.length)
        acc.push(...qr.data.items)
        if (acc.length >= total || qr.data.items.length < PAGE) break
      }
      const [tr] = await Promise.all([v3Api.catalog.quizKpTree()])
      questions.value = acc
      tree.value = tr.data.tree
      if (kpQuery) {
        // 按知识点名称匹配叶子 → 选中该知识点（与点击分类树同路径）
        const leaf = treeItems.value.find((t) => t.leaf && t.name === kpQuery)
        if (leaf) selectedKp.value = leaf
      }
      if (kpLeafOptions.value.length) scanKpCode.value = kpLeafOptions.value[0].code
    } catch { /* mock */ }
  } finally {
    loading.value = false
  }
})

function collectCodes(nodes: V3KpTreeNode[]): string[] {
  const codes: string[] = []
  for (const n of nodes) {
    if (n.children && n.children.length) codes.push(...collectCodes(n.children))
    else codes.push(kpCodeOf(n))
  }
  return [...new Set(codes)]
}
/** 叶节点 kp_code：优先取内嵌 kp_codes 首项，否则按 id 生成稳定值 */
function kpCodeOf(n: V3KpTreeNode): string {
  return (n as { kp_codes?: string[] }).kp_codes?.[0] ?? n.id.replace(/^kp-/, '')?.toUpperCase() ?? n.id
}
function cntOf(n: TreeItem): number {
  // 2026-10-15：优先用服务器聚合题量（自底向上、与题库同口径）；mock 树无 count 时回落前端计数
  if (Number.isFinite(n.count) && n.count > 0) return n.count
  if (!n.codes) return questions.value.length
  return questions.value.filter((q) => n.codes!.includes(q.kp_code)).length
}
function selectKp(n: TreeItem) { selectedKp.value = n }
/** V3.3：从已选试卷中移除单题（原只有「清空全部」） */
function removePaper(id: string) { paper.value = paper.value.filter((x) => x !== id) }

function togglePick(id: string) {
  const i = paper.value.indexOf(id)
  if (i >= 0) paper.value.splice(i, 1)
  else paper.value.push(id)
}
/* ---------- B4 发布到学生课堂任务（8100 → 8000，真实发布） ---------- */
const publishOpen = ref(false)
const publishing = ref(false)
const publishRequestId = ref('')
const pubClasses = ref<{ class_id: string; name: string; students: number }[]>([])

function localInputValue(d: Date): string {
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}
function at22(offsetDays: number): string {
  const d = new Date()
  d.setDate(d.getDate() + offsetDays)
  d.setHours(22, 0, 0, 0)
  return localInputValue(d)
}
/** 快捷截止：今晚 / 明晚 / 后天 22:00（真实本地时间，提交转 ISO） */
const deadlinePresets = computed(() => [
  { label: '今晚 22:00', value: at22(0) },
  { label: '明晚 22:00', value: at22(1) },
  { label: '后天 22:00', value: at22(2) },
])
const pubForm = ref({ class_id: '', deadline_local: at22(1), type: 'homework' as 'quiz' | 'pre_class' | 'homework' })
const publishBlockers = computed(() => publishWarnings.value.some((w) => w.block))
const publishWarnings = computed(() => {
  const out: { text: string; block: boolean }[] = []
  if (paperQuestions.value.length > 50) out.push({ text: `单次最多发布 50 题（当前 ${paperQuestions.value.length} 题），请拆分试卷`, block: true })
  for (const q of paperQuestions.value) {
    if (q.q_type === 'image') out.push({ text: `「${(q.stem_latex || '').slice(0, 16)}…」是图片题：学生作业暂不支持图片题干，请先从试卷移除`, block: true })
    else if (!q.answer || q.answer === '待批改') out.push({ text: `「${(q.stem_latex || '').slice(0, 16)}…」缺标准答案——发布前必须补齐（批改与公布的依据）`, block: true })
    else if (!q.analysis) out.push({ text: `「${(q.stem_latex || '').slice(0, 16)}…」缺解析——建议补充（不影响发布）`, block: false })
  }
  return out
})
function newPublishRequestId(): string {
  try {
    if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') return crypto.randomUUID()
  } catch { /* 非安全上下文回落 */ }
  return `paper-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`
}
async function openPublish() {
  if (!paperQuestions.value.length) return
  publishOpen.value = true
  publishRequestId.value = newPublishRequestId()
  if (!pubClasses.value.length) {
    try {
      const r = await v3Api.catalog.classes()
      pubClasses.value = r.data.items
      if (!pubForm.value.class_id || !pubClasses.value.some((c) => c.class_id === pubForm.value.class_id)) {
        pubForm.value.class_id = pubClasses.value[0]?.class_id || ''
      }
    } catch { pubClasses.value = [] }
  }
}
async function doPublish() {
  if (publishBlockers.value || publishing.value) return
  if (!pubForm.value.class_id) { toast.error('没有可发布的教学班：请先建班或让班主任把你加为教师'); return }
  const cls = pubClasses.value.find((c) => c.class_id === pubForm.value.class_id)
  const deadlineIso = pubForm.value.deadline_local ? new Date(pubForm.value.deadline_local).toISOString() : null
  if (!window.confirm(`确认发布到「${cls?.name || pubForm.value.class_id}」？\n· ${paperQuestions.value.length} 题 · 截止 ${pubForm.value.deadline_local.replace('T', ' ')}\n· 发布后学生课堂任务立即可见`)) return
  if (!publishRequestId.value) publishRequestId.value = newPublishRequestId()
  publishing.value = true
  try {
    const r = await v3Api.grading.publishPaper({
      title: `《${publishTitle.value}》${pubForm.value.type === 'pre_class' ? '学前作业' : pubForm.value.type === 'quiz' ? '课堂练习' : '课后作业'}`,
      class_id: pubForm.value.class_id,
      question_ids: paperQuestions.value.map((q) => q.id),
      deadline: deadlineIso,
      type: pubForm.value.type,
      client_request_id: publishRequestId.value,
    })
    publishOpen.value = false
    paper.value = []
    toast.success(`已发布到「${cls?.name || '班级'}」· ${r.data.question_count} 题；学生课堂任务已可见`)
    window.setTimeout(() => router.push('/teacher-v3/assign'), 900)
  } catch (e) {
    toast.error(e instanceof Error ? e.message : '发布失败，请稍后重试')
  } finally {
    publishing.value = false
  }
}
const publishTitle = computed(() => {
  const k = paperQuestions.value[0]?.kp_name || '数学'
  return k + '等 · 精选练习'
})

function exportPaper() {
  /* B0 诚实化：不再静默空函数。PDF/PPTX 导出依赖真实文件生成服务（后端 M3 接入），
     原型阶段明确告知边界，不伪装"导出成功"。toast 在调用时惰性获取（避免测试环境依赖 Pinia）。 */
  import('@/stores/toast').then(({ useToastStore }) => useToastStore().info('PDF 导出尚未接入真实文件服务；现在可直接用「发布为作业」把本卷下发到学生课堂任务。'))
}

/* ---- 扫描入库（V3.2：双图分开 + AI 识别归属） ---- */
function onFilePick(e: Event) {
  const f = (e.target as HTMLInputElement).files?.[0]
  if (f) readFile(f)
}
function onSolPick(e: Event) {
  const f = (e.target as HTMLInputElement).files?.[0]
  if (!f) return
  const r = new FileReader()
  r.onload = () => { scanSolSrc.value = String(r.result || '') }
  r.readAsDataURL(f)
  presignUpload(f).then((h) => { scanSolKey.value = h.key }).catch(() => {})
}
function onFileDrop(e: DragEvent) {
  const f = e.dataTransfer?.files?.[0]
  if (f) readFile(f)
}
function readFile(f: File) {
  const r = new FileReader()
  r.onload = () => { scanSrc.value = String(r.result || '') }
  r.readAsDataURL(f)
  // M2-C 预签名直传（IFC-004）：dataURL 仅本地预览，后端收对象 key（原图原样 PUT，红线 3）
  dragHint.value = '上传中 0%'
  presignUpload(f, { onProgress: (p) => { dragHint.value = `上传中 ${Math.round(p * 100)}%` } })
    .then((h) => { scanKey.value = h.key; dragHint.value = '原图已直传入库' })
    .catch(() => { dragHint.value = '上传失败，请重选图片' })
}
function useSample() {
  scanSrc.value = 'data:image/svg+xml;utf8,<svg xmlns="http://www.w3.org/2000/svg" width="180" height="80"><rect x="2" y="2" width="176" height="76" fill="#f8fafc" stroke="#4f46e5"/><text x="10" y="24" font-size="13" fill="#4f46e5">过椭圆 x²/4+y²/3=1 右焦点 F 作倾斜角 60° 的直线 l，</text><text x="10" y="46" font-size="13" fill="#4f46e5">交椭圆于 A、B，求 |AB|。</text><polygon points="120,60 150,60 160,72 130,72" fill="#e0e7ff" stroke="#c97"/></svg>'
  dragHint.value = '已载入示例原图'
}
async function doScanImport() {
  if (!scanSrc.value) return
  try {
    const r = await v3Api.catalog.quizScanImport({
      src: scanKey.value || scanSrc.value,
      kp_code: scanKpCode.value,
      kp_name: kpLeafOptions.value.find((o) => o.code === scanKpCode.value)?.path.split(' ▸ ').pop() || '拍照入库',
      kp_path: kpLeafOptions.value.find((o) => o.code === scanKpCode.value)?.path.split(' ▸ ') || [],
      as_image: mode.value === 'image',
      solution_src: scanSolKey.value || scanSolSrc.value || undefined,
    })
    questions.value.unshift(r.data)
    scanOpen.value = false
    scanSrc.value = ''
    scanSolSrc.value = ''
    suggest.value = null
  } catch { /* mock */ }
}
/* V3.2：AI 识别知识点 → 点选填入 */
async function runSuggest() {
  suggestLoading.value = true
  suggest.value = null
  try {
    const r = await v3Api.catalog.kpSuggest({ src: scanSrc.value || scanSolSrc.value || '' })
    suggest.value = r.data
  } catch { suggest.value = null } finally { suggestLoading.value = false }
}
</script>

<style scoped>
.tv3-page-shell {
  margin: -22px; padding: 22px; min-height: calc(100vh - var(--tv3-topbar-h));
  background: var(--teacher-bg-gradient);
  box-sizing: border-box;
}
.tv3-treescroll { max-height: 560px; overflow-y: auto; display: flex; flex-direction: column; gap: 2px; }
.tv3-qscroll { display: flex; flex-direction: column; gap: 8px; max-height: 560px; overflow-y: auto; }
.tv3-empty { text-align: center; color: var(--tv3-ink4); padding: 60px 0; font-size: 13px; }
.tv3-tree-row {
  display: flex; align-items: center; gap: 6px; width: 100%; text-align: left;
  padding: 6px 8px; border-radius: 8px; border: none; background: transparent; cursor: pointer;
  font-size: 12.5px; color: var(--tv3-ink2);
}
.tv3-tree-row.lv1 { font-weight: 700; color: var(--tv3-ink); }
.tv3-tree-row.lv2 { padding-left: 20px; }
.tv3-tree-row.lv3 { padding-left: 34px; }
.tv3-tree-row.is-sel { background: var(--tv3-gold-soft); color: var(--tv3-ink); box-shadow: inset 0 0 0 1px var(--tv3-gold); }
.tv3-tree-row:hover { background: var(--tv3-line-soft); }
.tv3-tree-row__folder { color: var(--tv3-gold); font-size: 11px; }
.tv3-tree-row__cnt { margin-left: auto; font-size: 11px; color: var(--tv3-ink4); background: var(--tv3-line-soft); border-radius: 9px; padding: 0 7px; }
.tv3-qrow { display: flex; gap: 10px; padding: 10px; border: 1px solid var(--tv3-line); border-radius: 12px; transition: all 0.12s ease; }
.tv3-qrow.is-picked { border-color: var(--tv3-gold); background: var(--tv3-gold-soft); }
.tv3-qrow--image { border-left: 3px solid var(--tv3-gold); }
.tv3-qrow__pick {
  width: 28px; height: 28px; border-radius: 50%; flex-shrink: 0; cursor: pointer;
  border: 1.5px solid var(--tv3-line); background: #fff; color: var(--tv3-ink3); font-size: 14px; font-weight: 700;
}
.tv3-qrow.is-picked .tv3-qrow__pick { background: var(--tv3-gold); border-color: var(--tv3-gold); color: #fff; }
.tv3-qrow__img { margin-top: 6px; border: 1px dashed var(--tv3-gold); border-radius: 8px; padding: 8px; background: #fff; }
.tv3-qrow__img img { max-width: 220px; border-radius: 6px; display: block; }
.tv3-qrow__img-cap { display: inline-block; margin-top: 4px; font-size: 11px; color: var(--tv3-ink4); }
.tv3-qrow__stem { font-size: 13.5px; margin-top: 6px; line-height: 1.6; }
.tv3-qrow__opts { display: flex; gap: 14px; flex-wrap: wrap; margin-top: 6px; font-size: 13px; }
.tv3-qrow__opt { padding: 2px 8px; border-radius: 6px; }
.tv3-qrow__opt.is-answer { background: var(--tv3-teal-soft); }
.tv3-paper { background: #fff; border: 1px solid var(--tv3-line); border-radius: 0 0 14px 14px; padding: 22px 26px; min-height: 480px; font-family: "Songti SC", "SimSun", serif; }
.tv3-paper__head { text-align: center; border-bottom: 2px solid var(--tv3-ink); padding-bottom: 10px; margin-bottom: 14px; }
.tv3-paper__school { font-size: 11.5px; color: var(--tv3-ink2); letter-spacing: 1px; }
.tv3-paper__title { font-size: 19px; font-weight: 800; margin: 4px 0; letter-spacing: 4px; }
.tv3-paper__meta { font-size: 11.5px; color: var(--tv3-ink2); }
.tv3-paper__empty { text-align: center; color: var(--tv3-ink4); padding: 80px 0; font-size: 13px; }
.tv3-paper__q { margin-bottom: 14px; position: relative; }
/* V3.3：试卷内单题移除（编辑态叠加控件，打印时 PDF 为 mock 不受影响） */
.tv3-paper__del {
  position: absolute; top: -6px; right: -6px; width: 20px; height: 20px; border-radius: 50%;
  border: none; cursor: pointer; background: var(--tv3-rose, #e5484d); color: #fff; font-size: 11px;
  line-height: 1; display: grid; place-items: center; opacity: 0;
  box-shadow: 0 1px 3px rgba(79, 70, 229, .18); transition: opacity .12s ease;
}
.tv3-paper__q:hover .tv3-paper__del { opacity: 1; }
.tv3-paper__qhead { display: flex; gap: 6px; font-size: 13.5px; }
.tv3-paper__score { font-size: 11.5px; color: var(--tv3-ink3); }
.tv3-paper__img { margin: 4px 0 2px 14px; }
.tv3-paper__img img { max-width: 260px; border: 1px solid var(--tv3-line); border-radius: 6px; }
.tv3-paper__stem { font-size: 13.5px; line-height: 1.7; margin: 3px 0 2px 14px; }
.tv3-paper__opts { display: grid; grid-template-columns: 1fr 1fr; gap: 3px 14px; margin-left: 14px; font-size: 13px; }
.tv3-paper__solve { margin: 6px 0 0 14px; height: 90px; border: 1px dashed var(--tv3-line); border-radius: 4px; display: grid; place-items: center; font-size: 11px; color: var(--tv3-ink4); }
.tv3-paper__foot { text-align: center; font-size: 10.5px; color: var(--tv3-ink3); border-top: 1px solid var(--tv3-line); padding-top: 8px; margin-top: 16px; }
.tv3-upload {
  flex: 1; min-height: 120px; border: 1.5px dashed var(--tv3-line); border-radius: 12px;
  display: grid; place-items: center; cursor: pointer; color: var(--tv3-ink4); font-size: 12.5px;
  background: var(--tv3-line-soft); padding: 8px; text-align: center;
}
.tv3-radio-row { display: flex; flex-direction: column; gap: 8px; }
.tv3-radio { display: flex; flex-direction: column; gap: 2px; font-size: 13px; color: var(--tv3-ink); cursor: pointer; }
.tv3-radio__hint { font-size: 11.5px; color: var(--tv3-ink4); padding-left: 20px; }
.tv3-input--block { width: 100%; }
.tv3-upload__hint { font-size: 11px; color: var(--tv3-ink4); }
.tv3-modal { position: fixed; inset: 0; background: rgba(15, 31, 54, 0.45); display: grid; place-items: center; z-index: 200; }
.tv3-modal__box { background: #fff; border-radius: 16px; padding: 18px 22px; }
.tv3-modal__head { display: flex; align-items: baseline; gap: 10px; border-bottom: 1px solid var(--tv3-line); padding-bottom: 10px; margin-bottom: 14px; }
.tv3-modal__foot { display: flex; justify-content: flex-end; gap: 10px; margin-top: 4px; }
</style>