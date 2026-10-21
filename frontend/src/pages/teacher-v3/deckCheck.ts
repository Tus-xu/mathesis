/**
 * B3 · 课件「可讲性体检」（创新 2）
 * 全部为确定性检查（可解释、可定位、可预览修复），不做总分、不冒充 AI 评分。
 * 维度依据：投影可读性（字号/溢出）、例题完整性、推导分步、版式节奏、理解检查点密度。
 * 讲解角色标注与 AI 语义检查属后端能力，前端不假装（诚实缺省）。
 */
import type { V3Deck, V3Slide, V3Element } from '@/types/teacherV3'

export interface CheckIssue {
  id: string
  severity: 'error' | 'warn' | 'info'
  slideIndex: number
  slideTitle: string
  elementId?: string
  rule: string
  why: string
  /** 可预览修复：bump-font=放大字号；split-slide=把推导后半拆到新页；none=仅提示 */
  fix?: { kind: 'bump-font'; font: number } | { kind: 'split-slide' } | { kind: 'none' }
}

const slideTitle = (s: V3Slide): string => {
  const t = s.elements.find((e) => e.type === 'text') as Extract<V3Element, { type: 'text' }> | undefined
  return (t?.html || '').replace(/<[^>]+>/g, '').slice(0, 14) || `第 ${s.id.slice(-3)} 页`
}

const minFontOf = (e: V3Element): number | null => {
  if (e.type === 'text') return e.font_size
  if (e.type === 'formula') return e.font_size
  return null
}

/** 与后端 rich lesson 去重保持同一类归一化：去定界符/空白/无语义括号差异。 */
const normalizeFormula = (latex: string): string => latex
  .replace(/^\s*(?:\$+|\\\(|\\\[)/, '')
  .replace(/(?:\$+|\\\)|\\\])\s*$/, '')
  .replace(/\\left|\\right/g, '')
  .replace(/\\[,;!: ]/g, '')
  .replace(/\s+/g, '')
  .replace(/\^\{([^{}]+)\}/g, '^$1')

const hasExecutableTask = (s: V3Slide): boolean => {
  const task = s.task
  if (!task) return false
  return [task.goal, task.context, task.student_action, task.expected_output]
    .every((value) => typeof value === 'string' && value.trim().length > 0)
}

export function checkDeck(deck: V3Deck): CheckIssue[] {
  const issues: CheckIssue[] = []
  let seq = 0
  const push = (i: CheckIssue['severity'], si: number, s: V3Slide, rule: string, why: string, elementId?: string, fix?: CheckIssue['fix']) => {
    issues.push({ id: `ck-${++seq}`, severity: i, slideIndex: si, slideTitle: slideTitle(s), elementId, rule, why, fix })
  }

  const firstFormulaByKey = new Map<string, { slideIndex: number; elementId: string; title: string }>()

  deck.slides.forEach((s, si) => {
    const title = slideTitle(s)
    /* 1. 投影可读性：字号下限（确定性） */
    for (const e of s.elements) {
      const f = minFontOf(e)
      if (f !== null && f < 18) {
        push('error', si, s, '字号过小', `「${title}」有 ${f}pt 内容——教室投影下后排学生不可读，建议正文 ≥18pt、标题 ≥24pt。`, e.id, { kind: 'bump-font', font: e.type === 'formula' ? 22 : 20 })
      }
    }
    /* 2. 溢出：1280×720 画布外（确定性） */
    for (const e of s.elements) {
      if (e.left + e.width > 1284 || e.top + e.height > 724 || e.left < -4 || e.top < -4) {
        push('error', si, s, '内容溢出画布', `「${title}」有元素超出 1280×720 画布——放映时会被裁掉，请拖回画布内。`, e.id, { kind: 'none' })
      }
    }
    /* 2a. 公式语义去重：跨页完全相同的标准式不能机械填页。 */
    for (const e of s.elements) {
      if (e.type !== 'formula') continue
      const key = normalizeFormula(e.latex)
      if (!key) continue
      const first = firstFormulaByKey.get(key)
      if (first && first.slideIndex !== si) {
        push('error', si, s, '公式机械重复', `「${title}」复用了第 ${first.slideIndex + 1} 页的同一归一化公式——应补充推导、变式或应用语境，不能用标准式填充空页。`, e.id, { kind: 'none' })
      } else if (!first) {
        firstFormulaByKey.set(key, { slideIndex: si, elementId: e.id, title })
      }
    }
    /* 3. 例题完整性：缺解答/答案（确定性） */
    if (s.layout === 'example') {
      const text = s.elements.map((e) => (e.type === 'text' ? (e as Extract<V3Element, { type: 'text' }>).html : '')).join('')
      if (!/解|答|∴|步骤/.test(text)) {
        push('warn', si, s, '例题缺少解答', `「${title}」只有题干，没有解答/答案——课堂讲到此处会"卡壳"。可在本页补解答，或明确此页为学生先练（页上加"先练"提示）。`, undefined, { kind: 'none' })
      }
    }
    /* 4. 推导分步：一次暴露过多步骤（确定性信号） */
    if (s.layout === 'derivation') {
      const formulas = s.elements.filter((e) => e.type === 'formula')
      if (formulas.length >= 4) {
        push('warn', si, s, '推导步骤过密', `「${title}」一次性暴露 ${formulas.length} 个推导公式——学生跟不上板书节奏。建议拆成两页（保留推导链前半，后半移入续页逐步讲）。`, formulas[formulas.length - 1].id, { kind: 'split-slide' })
      }
    }
  })

  /* 5. 版式节奏：连续 3+ 页同 layout（确定性） */
  let runStart = 0
  for (let i = 1; i <= deck.slides.length; i++) {
    const same = i < deck.slides.length && deck.slides[i].layout === deck.slides[runStart].layout
    if (!same) {
      if (i - runStart >= 3) {
        push('info', runStart, deck.slides[runStart], '版式连续重复', `第 ${runStart + 1}–${i} 页连续 ${i - runStart} 页同版式——节奏单调，学生易走神；建议插入变式/练习/图形页调剂。`, undefined, { kind: 'none' })
      }
      runStart = i
    }
  }

  /* 6. 理解检查点密度：前 6 页内无 example/practice 类页面（确定性） */
  const checkLayouts = new Set(['example', 'variation', 'practice'])
  const first6 = deck.slides.slice(0, 6)
  if (deck.slides.length >= 6 && !first6.some((s) => checkLayouts.has(s.layout) || /检测|练|检查/.test(slideTitle(s)))) {
    push('info', 0, deck.slides[0], '缺少理解检查点', `连续 6 页都是讲授内容，没有任何当堂检查——建议在第 4–6 页之间插入 1 道 30 秒小题（当堂回收掌握度）。`, undefined, { kind: 'none' })
  }

  /* 7. 课堂任务：正文课件至少有一项可执行任务，且目标/行动/产出齐全。 */
  if (deck.slides.length > 0 && !deck.slides.some(hasExecutableTask)) {
    push('error', 0, deck.slides[0], '缺少课堂任务', '课件没有可执行课堂任务：必须明确任务目标、学生行动和可检查产出（并保留问题情境供教师引导）。', undefined, { kind: 'none' })
  }

  return issues
}

/** 应用修复：bump-font / split-slide（拆页：推导公式后半移入续页） */
export function applyFix(deck: V3Deck, issue: CheckIssue): 'applied' | 'unsupported' {
  const s = deck.slides[issue.slideIndex]
  if (!s) return 'unsupported'
  if (issue.fix?.kind === 'bump-font' && issue.elementId) {
    const e = s.elements.find((x) => x.id === issue.elementId)
    if (e && (e.type === 'text' || e.type === 'formula')) {
      e.font_size = issue.fix.font
      return 'applied'
    }
    return 'unsupported'
  }
  if (issue.fix?.kind === 'split-slide') {
    const formulas = s.elements.filter((e) => e.type === 'formula')
    if (formulas.length < 2) return 'unsupported'
    const half = Math.ceil(formulas.length / 2)
    const moving = formulas.slice(half)
    const titleEl = s.elements.find((e) => e.type === 'text')
    const next: V3Slide = {
      id: `sl-split-${Date.now()}`,
      layout: 'derivation',
      anchor_bar: `接上页 · ${slideTitle(s)}（续）`,
      elements: [
        { id: `e-split-t-${Date.now()}`, type: 'text', left: 70, top: 52, width: 640, height: 50, z: 1, html: `${slideTitle(s)}（续）`, font_size: 28, bold: true, color: '#3730a3' },
        ...moving.map((e) => ({ ...e, top: Math.max(130, (e.top % 500) + 60) })),
        { id: `e-split-p-${Date.now()}`, type: 'pageNo', left: 1180, top: 680, width: 60, height: 30, z: 1, no: issue.slideIndex + 2 },
      ],
    }
    if (titleEl) void titleEl
    const movingIds = new Set(moving.map((e) => e.id))
    s.elements = s.elements.filter((e) => !movingIds.has(e.id))
    deck.slides.splice(issue.slideIndex + 1, 0, next)
    return 'applied'
  }
  return 'unsupported'
}
