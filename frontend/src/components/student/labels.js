/** 学生端共享常量与格式化工具（与后端契约对齐） */

// 错因五枚举（ADR-M2B-002）
export const ERROR_TYPES = {
  concept: '概念不清',
  formula: '公式记错',
  calculation: '计算失误',
  logic: '思路卡壳',
  reading: '审题偏差',
}
export const ERROR_TYPE_TAG = { concept: 'red', formula: 'orange', calculation: 'cyan', logic: 'red', reading: 'orange' }
export function errorTypeName(t) { return ERROR_TYPES[t] || '待判定' }
export function errorTypeTag(t) { return ERROR_TYPE_TAG[t] || 'gray' }

// 题型三枚举（ADR-031）
export const Q_TYPES = { choice: '选择题', blank: '填空题', solution: '解答题', judge: '判断题' }
export function qTypeName(t) { return Q_TYPES[t] || t || '题目' }

export const DIFFICULTIES = { easy: '基础', medium: '进阶', hard: '挑战' }
export const DIFFICULTY_TAG = { easy: 'green', medium: 'orange', hard: 'red' }
export function difficultyName(d) { return DIFFICULTIES[d] || d || '' }

// 刷题三模式
export const MODE_NAMES = { special: '薄弱专练', retry: '错题重练', daily: '每日一题' }
export function modeName(m) { return MODE_NAMES[m] || '刷题' }

// 模拟试卷卷型
export const EXAM_TYPES = { full_mock: '全真模拟', topic: '专题训练' }
export function examTypeName(t) { return EXAM_TYPES[t] || t || '试卷' }

// 判分结果
export const VERDICTS = { correct: '回答正确', wrong: '回答错误', pending_review: '待教师确认' }
export const VERDICT_TAG = { correct: 'green', wrong: 'red', pending_review: 'orange' }
export function verdictName(v) { return VERDICTS[v] || v || '未知' }
export function verdictTag(v) { return VERDICT_TAG[v] || 'gray' }

// 知识图谱着色（后端 gray/red/yellow/green → 设计 tokens）
export const MASTERY_COLORS = { green: 'var(--accent-green)', yellow: 'var(--accent-orange)', red: 'var(--accent-red)', gray: '#CBD5E1' }
export function masteryColor(c) { return MASTERY_COLORS[c] || MASTERY_COLORS.gray }

export function pct(v, digits = 0) {
  if (v === null || v === undefined || isNaN(Number(v))) return '—'
  return `${(Number(v) * 100).toFixed(digits)}%`
}

const pad2 = (n) => String(n).padStart(2, '0')

export function fmtDate(iso) {
  if (!iso) return '—'
  const d = new Date(iso)
  if (isNaN(d)) return String(iso)
  return `${d.getMonth() + 1}月${d.getDate()}日`
}

export function fmtDateTime(iso) {
  if (!iso) return '—'
  const d = new Date(iso)
  if (isNaN(d)) return String(iso)
  return `${pad2(d.getMonth() + 1)}-${pad2(d.getDate())} ${pad2(d.getHours())}:${pad2(d.getMinutes())}`
}

export function todayStr() {
  const d = new Date()
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`
}

/** 间隔复习倒计时（按自然日差） */
export function reviewInfo(iso) {
  if (!iso) return { text: '未安排复习', cls: 'gray' }
  const target = new Date(iso)
  if (isNaN(target)) return { text: '—', cls: 'gray' }
  const sod = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime()
  const diff = Math.round((sod(target) - sod(new Date())) / 86400000)
  if (diff < 0) return { text: `已逾期 ${-diff} 天`, cls: 'red' }
  if (diff === 0) return { text: '今日到期', cls: 'orange' }
  return { text: `${diff} 天后复习`, cls: 'green' }
}

/** 手机号脱敏 138****1234 */
export function maskPhone(p) {
  if (!p) return ''
  return String(p).replace(/(\d{3})\d{4}(\d{3,4})/, '$1****$2')
}

/** 选择题 options 归一化为 [{key, text}]（兼容 dict / list / null） */
export function normalizeOptions(options) {
  if (!options) return []
  if (Array.isArray(options)) return options.map((text, i) => ({ key: String.fromCharCode(65 + i), text: String(text) }))
  if (typeof options === 'object') {
    return Object.entries(options).sort(([a], [b]) => a.localeCompare(b)).map(([key, text]) => ({ key, text: String(text) }))
  }
  return []
}

/** kp 英文代码 → 中文名（出题卡/知识点标签/推荐文案统一中文；geometry→立体几何等） */
export const KP_ZH = {
  geometry: '立体几何', solid_geometry: '立体几何', solid: '立体几何',
  function: '函数', trig: '三角函数', trigonometry: '三角函数',
  sequence: '数列', sequences: '数列', probability: '概率与统计', stats: '统计',
  inequality: '不等式', derivative: '导数', conic: '圆锥曲线', cone: '圆锥曲线',
  set_logic: '集合与逻辑', sets: '集合', vectors: '平面向量', complex: '复数',
  exponent_log: '指对数', polynomial: '多项式', plane: '平面几何', analytic: '解析几何',
  general: '综合', combination: '计数原理', binomial: '二项式', matrix: '矩阵',
  kp_general: '综合',
}
