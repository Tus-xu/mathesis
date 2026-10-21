/**
 * 双师课堂纯函数工具（可单测，不依赖 DOM/API）。
 *
 * - groupSlideSections：把一页 slide 的 blocks 按「效果图课堂版式」归类：
 *   学习目标 / 定理框 / 主讲讲解 / 例题 / 表格 / 图形 / 公式板书 / 结论小结。
 * - splitSolutionSteps：把例题解析按 ①②③ / 换行 / 分号切成分步板书。
 * - practiceAccuracy：分层练习正确率（含环形图角度）。
 * - verificationMeta / sourceTypeMeta / deriveSourceTitle / 时间工具。
 * - 老师课堂（teacher_deck）：课件列表映射、空态文案、课件舞台等比缩放。
 */

/** 验证状态元信息 */
export function verificationMeta(status) {
  const map = {
    verified: { label: '数学已验证', tone: 'ok', icon: '✓' },
    needs_review: { label: '需要复核', tone: 'warn', icon: '⚠' },
    failed: { label: '验证未通过', tone: 'err', icon: '✗' },
    generating: { label: '生成中', tone: 'muted', icon: '…' },
  }
  return map[status] || { label: '未验证', tone: 'muted', icon: '·' }
}

/** 课堂来源元信息 */
export function sourceTypeMeta(type) {
  const map = {
    topic: { label: '主题生成', icon: '✍️' },
    photo: { label: '拍题', icon: '📷' },
    file: { label: '教案上传', icon: '📄' },
    teacher_deck: { label: '老师课堂', icon: '🎓' },
  }
  return map[type] || { label: '课堂', icon: '📘' }
}

/** 从前 N 行文本推导课堂标题（首行去行号/编号，截断 20 字） */
export function deriveSourceTitle(text, fallback = '数学课堂') {
  if (!text || !text.trim()) return fallback
  const firstLine = text
    .split('\n')
    .map((s) => s.trim())
    .find((s) => s.length > 0) || ''
  const cleaned = firstLine.replace(/^[（(]?\d+[）).:：、]?/, '').replace(/\s+/g, ' ').trim()
  const title = cleaned || fallback
  return title.length > 22 ? title.slice(0, 21) + '…' : title
}

/** 旧版生成管线遗留的工程警告文本（学生端一律不展示，OpenMAIC 哲学：缺口静默降级） */
const LEGACY_WARNING_RE =
  /未渲染图形|未自动补造|缺少可验证构造数据|请先补充题目条件|需要教师复核|未检索到可引用教材|不渲染默认/

function isLegacyWarning(block) {
  if (!block || typeof block !== 'object') return false
  const text = String(block.text || block.caption || '')
  return LEGACY_WARNING_RE.test(text)
}

/** 把一页 slide 归类为课堂版式结构（每段均为数组，缺失时为空数组由模板兜底） */
export function groupSlideSections(slide) {
  /** @type {{ goals: any[], theorems: any[], lecture: any[], examples: any[], tables: any[], figures: any[], formulas: any[], summary: any[], textbook: any, practice: any[] }} */
  const empty = {
    goals: [], theorems: [], lecture: [], examples: [], tables: [],
    figures: [], formulas: [], summary: [], textbook: null, practice: [],
  }
  if (!slide || !Array.isArray(slide.blocks)) {
    return { ...empty }
  }
  const sections = { ...empty }
  const examples = []
  for (const b of slide.blocks) {
    if (!b || typeof b !== 'object') continue
    if (isLegacyWarning(b)) continue
    const kind = b.kind || 'text'
    if (kind === 'latex') sections.formulas.push(b)
    else if (kind === 'plot2d' || kind === 'geometry' || kind === 'figure' || kind === 'ggb') sections.figures.push(b)
    else if (kind === 'example' || kind === 'question') examples.push(b)
    else if (kind === 'note') sections.summary.push(b)
    else if (kind === 'theorem') sections.theorems.push(b)
    else if (kind === 'table') sections.tables.push(b)
    else if (kind === 'textbook_association') sections.textbook = b
    else sections.lecture.push(b)
  }
  sections.examples = examples
  sections.practice = examples
  if (slide.key_points && slide.key_points.length) sections.goals = slide.key_points
  return sections
}

/** 例题解析 → 分步板书（按 ①②③/换行/分号切分，保留编号）。
 *  切分必须数学感知：$...$ 内部的 ①/分号/换行不是步骤边界，否则会把
 *  一个公式腰斩成两步，各步拿到半个公式渲染出"乱码"。 */
function splitTopLayer(text, isSep) {
  const parts = []
  let buf = ''
  let inMath = false
  for (let i = 0; i < text.length; i++) {
    const ch = text[i]
    if (ch === '$' && text[i - 1] !== '\\') inMath = !inMath
    if (!inMath && isSep(ch)) {
      parts.push(buf)
      buf = ''
      continue
    }
    buf += ch
  }
  parts.push(buf)
  return parts
}

export function splitSolutionSteps(analysis) {
  const text = String(analysis || '').trim()
  if (!text) return []
  const isMarker = (ch) => /[①②③④⑤⑥⑦⑧⑨⑩]/.test(ch)
  let inMath = false
  const cutIdx = []
  for (let i = 0; i < text.length; i++) {
    const ch = text[i]
    if (ch === '$' && text[i - 1] !== '\\') inMath = !inMath
    if (!inMath && isMarker(ch)) cutIdx.push(i)
  }
  let parts
  if (cutIdx.length >= 2) {
    parts = cutIdx.map((idx, i) =>
      text.slice(idx, i + 1 < cutIdx.length ? cutIdx[i + 1] : undefined),
    )
  } else {
    parts = splitTopLayer(text, (ch) => ch === '\n' || ch === '；' || ch === ';')
  }
  const cleaned = parts.map((s) => s.trim()).filter(Boolean)
  return cleaned.length ? cleaned : [text]
}

/** 分层练习正确率（跨档汇总）：返回 0-100 整数 */
export function practiceAccuracy(stats) {
  const s = stats && typeof stats === 'object' ? stats : {}
  let total = 0
  let correct = 0
  for (const tier of ['basic', 'advanced', 'challenge']) {
    const t = s[tier] || {}
    total += Number(t.total) || 0
    correct += Number(t.correct) || 0
  }
  if (!total) return 0
  return Math.round((correct / total) * 100)
}

/** 练习异步生成状态：只有明确 pending 才显示加载，旧数据不能无限转圈。 */
export function practiceGenerationMeta(verification) {
  const info = verification?.practice_generation || {}
  const state = ['pending', 'ready', 'fallback', 'failed'].includes(info.status) ? info.status : 'unknown'
  return { state, error: info.error || '' }
}

/** 页面结构序数（渲染六段标题用） */
export const SECTION_STEPS = [
  { key: 'goals', label: '学习目标', icon: '🎯' },
  { key: 'lecture', label: '主讲讲解', icon: '👨‍🏫' },
  { key: 'formulas', label: '公式 / 板书', icon: '📐' },
  { key: 'figures', label: '图形示意', icon: '🧊' },
  { key: 'practice', label: '学生跟做', icon: '✍️' },
  { key: 'summary', label: '小结 · 易错', icon: '📌' },
]

export function percent(a, b) {
  return b ? Math.min(100, Math.round((a / b) * 100)) : 0
}

export function fmtDuration(seconds) {
  const s = Math.max(0, Math.floor(seconds || 0))
  const mm = String(Math.floor(s / 60)).padStart(2, '0')
  const ss = String(s % 60).padStart(2, '0')
  return `${mm}:${ss}`
}

/** 课堂计时（HH:MM:SS，效果图顶栏样式） */
export function fmtClock(seconds) {
  const s = Math.max(0, Math.floor(seconds || 0))
  const hh = String(Math.floor(s / 3600)).padStart(2, '0')
  const mm = String(Math.floor((s % 3600) / 60)).padStart(2, '0')
  const ss = String(s % 60).padStart(2, '0')
  return `${hh}:${mm}:${ss}`
}

export function fmtTime(iso) {
  if (!iso) return ''
  try {
    const d = new Date(iso)
    if (Number.isNaN(d.getTime())) return ''
    return `${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
  } catch { return '' }
}

/* ==================== 老师课堂（teacher_deck） ==================== */

/** 老师课件舞台设计尺寸（与教师端 SlideCanvas 一致：980×551 / 16:9）。 */
export const DECK_STAGE_WIDTH = 980
export const DECK_STAGE_HEIGHT = 551

/**
 * 课件舞台等比缩放系数（纯函数，便于单测）。
 *
 * 约束：
 * - 只缩不放：最大 1（980×551 是设计尺寸，放大不会带来更多信息）；
 * - 高度已知时取宽/高较小者，保证整页可见、不溢出、不裁切；
 * - 容器尚未测量（0/NaN）时回退 1，由 ResizeObserver 首帧后纠正；
 * - 下限 0.1，避免极端窄容器把舞台压成 0 高度。
 *
 * @param {number} containerWidth  容器可用宽度（px）
 * @param {number} [containerHeight] 容器可用高度（px；未知传 0/省略）
 * @returns {number} 0.1–1 之间、保留 4 位小数的缩放系数
 */
export function deckStageScale(containerWidth, containerHeight = 0) {
  const w = Number(containerWidth) || 0
  const h = Number(containerHeight) || 0
  if (w <= 0) return 1
  const byWidth = w / DECK_STAGE_WIDTH
  const byHeight = h > 0 ? h / DECK_STAGE_HEIGHT : Infinity
  const scale = Math.min(byWidth, byHeight, 1)
  return Math.max(0.1, Math.round(scale * 10000) / 10000)
}

function percentInt(v) {
  const n = Number(v)
  if (!Number.isFinite(n)) return 0
  return Math.max(0, Math.min(100, Math.round(n)))
}

/**
 * 老师课堂列表（`GET /api/student/classroom-coursewares` 的 items）→ 视图模型。
 *
 * 只做字段归一化与状态文案，不伪造数据：缺失的老师名/班级保留空串，由视图诚实省略；
 * 无 `courseware_id` 的脏条目直接丢弃（点击无法 open 的卡片不允许渲染）。
 */
export function mapTeacherCoursewares(items) {
  if (!Array.isArray(items)) return []
  return items
    .map((it) => {
      if (!it || typeof it !== 'object') return null
      const id = String(it.courseware_id || '').trim()
      if (!id) return null
      const opened = !!it.opened
      const progressPct = percentInt(it.progress_pct)
      const lastSessionId = it.last_session_id ? String(it.last_session_id) : ''
      let statusText = '未开始'
      let tone = 'idle'
      if (progressPct >= 100) {
        statusText = '已学完'
        tone = 'done'
      } else if (opened || progressPct > 0 || lastSessionId) {
        statusText = '学习中'
        tone = 'doing'
      }
      return {
        id,
        title: String(it.title || '').trim() || '未命名课堂',
        deckId: String(it.deck_id || '').trim(),
        deckRevision: Number(it.deck_revision) || 0,
        teacherName: String(it.teacher_name || '').trim(),
        className: String(it.class_name || '').trim(),
        classId: String(it.class_id || '').trim(),
        pageCount: Math.max(0, Number(it.page_count) || 0),
        publishedAt: it.published_at || '',
        publishedText: fmtTime(it.published_at),
        opened,
        progressPct,
        // 后端无作答时返回 null（不是 0）：保留 null，由视图显示「—」，不把“未作答”说成 0%。
        practiceAccuracy:
          it.practice_accuracy == null ? null : percentInt(it.practice_accuracy),
        lastSessionId,
        statusText,
        tone,
        actionText: opened ? '继续学习' : '开始学习',
      }
    })
    .filter(Boolean)
}

/**
 * 老师课堂区空态文案（诚实：未加载 / 失败 / 真无内容三种状态不混淆）。
 * 有内容时返回空串（不渲染空态）。
 */
export function teacherCoursewareEmptyText({ loading = false, failed = false, count = 0 } = {}) {
  if (count > 0) return ''
  if (loading) return '正在加载老师发布的课堂…'
  if (failed) return '老师课堂加载失败，请稍后重试'
  return '老师还没有发布课堂课件——去课堂任务看看，或自己开一节 AI 课堂'
}
