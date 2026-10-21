/**
 * 洞察文案模板引擎（阶段 2 内容资产清单 · 单一内核：Today/Classes 共用）。
 * 规则（红线 R2 / BUSINESS_OBJECTS）：
 * - 文案优先后端 summary 字段直传；字段缺失时按 kind 权威枚举给模板句；
 * - 数字一律来自后端字段，缺字段绝不拼凑（无 count 不出现数字）；
 * - 诊断字段（key=value / type=rule / confidence）不上屏，统一诚实降级句式。
 */

/** 洞察 kind 权威枚举（契约记录 2026-09-01，BE 答复：error_cluster/submission_trend/low_mastery/review_backlog） */
export const INSIGHT_KINDS = ['error_cluster', 'submission_trend', 'low_mastery', 'review_backlog'] as const
export type InsightKind = (typeof INSIGHT_KINDS)[number]

export interface InsightCopyInput {
  kind: string
  summary?: string
  count?: number
  total?: number
}

const KIND_TITLE: Record<InsightKind, string> = {
  error_cluster: '跨作业错题集中出现',
  review_backlog: '有待教师确认的作答',
  low_mastery: '知识点掌握偏弱',
  submission_trend: '作业提交情况变化',
}

/** 教学证据面向教师渲染：禁止裸露内部 key=value 诊断字段（单字符数学变量如 a=0 不误伤） */
export function evidenceText(value: unknown): string {
  if (typeof value !== 'string' || !value.trim()) return '暂无更多证据'
  const evidence = value.trim()
  if (/(?:^|[;；,，\s])[a-z][a-z0-9_]+\s*=/i.test(evidence)) {
    return '证据格式待更新，暂不展示内部诊断字段。'
  }
  return evidence
}

/** 洞察主文案：优先后端 summary；缺 summary 时按 kind 模板 + 真实字段生成 */
export function insightTitle(input: InsightCopyInput): string {
  if (input.summary && input.summary.trim()) return input.summary.trim()
  const base = KIND_TITLE[input.kind as InsightKind] || '班级学情提示'
  if (typeof input.count === 'number' && Number.isFinite(input.count)) {
    const scope = typeof input.total === 'number' && Number.isFinite(input.total) ? `（${input.count}/${input.total}）` : ''
    return `${base}：${input.count} 份${scope}`
  }
  return base
}

/** 未知 kind 判定（供视图决定是否落兜底动作并 console.warn） */
export function isKnownInsightKind(kind: string): kind is InsightKind {
  return (INSIGHT_KINDS as readonly string[]).includes(kind)
}
