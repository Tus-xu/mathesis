/**
 * B5 · 课堂状态机与分支逻辑（纯函数，可单测）
 * 状态机：未开始 → 等待学生 → 答题中 → 已停止 → 讲评中 → 已结束
 * 全部确定性：作答序列由种子生成（B0），学生加入序列固定，分支建议由正确率阈值推导。
 * 教师必须点选分支；系统只给建议，不自动切换（§15 课堂时间重排的改造形态）。
 */

export type ClassStatus = 'idle' | 'waiting' | 'collecting' | 'stopped' | 'revealed' | 'ended'

export const STATUS_LABEL: Record<ClassStatus, string> = {
  idle: '未开始',
  waiting: '等待学生',
  collecting: '答题中',
  stopped: '已停止',
  revealed: '讲评中',
  ended: '已结束',
}

/** 合法状态迁移表（非法迁移返回 null，UI 侧按钮天然禁用） */
const TRANSITIONS: Record<ClassStatus, ClassStatus[]> = {
  idle: ['waiting'],
  waiting: ['collecting', 'ended'],
  collecting: ['stopped', 'ended'],
  stopped: ['revealed', 'collecting', 'ended'],
  revealed: ['collecting', 'ended'],
  ended: [],
}
export function canTransition(from: ClassStatus, to: ClassStatus): boolean {
  return TRANSITIONS[from]?.includes(to) ?? false
}

/** 确定性学生加入序列：开课后按固定间隔显示加入（不伪造"实时"，界面标注演示） */
export function joinSequence(total: number, roster: string[]): { atMs: number; name: string }[] {
  const out: { atMs: number; name: string }[] = []
  const n = Math.max(1, total)
  for (let i = 0; i < n; i++) {
    out.push({ atMs: 400 + i * 260, name: roster[i % roster.length] + (i >= roster.length ? `（${Math.floor(i / roster.length) + 1}）` : '') })
  }
  return out
}

export type BranchKind = 'boost' | 'partial' | 'reteach'

export interface BranchCard {
  kind: BranchKind
  label: string
  trigger: string
  minutes: number
  action: string
  /** 执行动作的机器可读描述：发哪道题（难度）/ 展示什么 */
  sendDifficulty?: 'easy' | 'medium' | 'hard'
  demoId?: string
}

export const BRANCH_CARDS: BranchCard[] = [
  {
    kind: 'boost', label: '理解良好 · 进入提升',
    trigger: '正确率 ≥ 75%', minutes: 5,
    action: '发一道较难的提升题（变式迁移），快节奏对答案',
    sendDifficulty: 'hard',
  },
  {
    kind: 'partial', label: '部分卡住 · 对比例题',
    trigger: '正确率 45%–75%', minutes: 4,
    action: '投出标准答案逐步对照，重点讲分岔步骤，然后原地再练一题',
    sendDifficulty: 'medium',
  },
  {
    kind: 'reteach', label: '大面积未理解 · 回前置',
    trigger: '正确率 < 45%', minutes: 6,
    action: '回到定义页重新演示（动态图形），发一道基础题确认回炉效果',
    sendDifficulty: 'easy', demoId: 'conic/ellipse',
  },
]

/** 分支建议：只排序推荐（第一个为建议），教师点选才执行 */
export function suggestBranch(correctRate: number): BranchCard[] {
  const order: BranchKind[] = correctRate >= 75 ? ['boost', 'partial', 'reteach'] : correctRate >= 45 ? ['partial', 'reteach', 'boost'] : ['reteach', 'partial', 'boost']
  return [...BRANCH_CARDS].sort((a, b) => order.indexOf(a.kind) - order.indexOf(b.kind))
}

/** 限时默认与续时上限 */
export const QUESTION_TIME_DEFAULT = 90
export const QUESTION_TIME_EXTEND = 30
export const QUESTION_TIME_MAX = 180

export function fmtClock(sec: number): string {
  const m = Math.floor(Math.max(0, sec) / 60)
  const s = Math.max(0, sec) % 60
  return `${m}:${String(s).padStart(2, '0')}`
}

/** 课堂小结（下课后展示；全部来自已发生的确定性数据） */
export interface SessionSummary {
  questions: number
  avgCorrectRate: number
  topWrong: string
  branchesUsed: string[]
  durationMin: number
}

export function buildSummary(input: {
  askedCount: number
  rates: number[]
  wrongTags: string[]
  branchesUsed: BranchKind[]
  elapsedMs: number
}): SessionSummary {
  const rates = input.rates.length ? input.rates : [0]
  return {
    questions: input.askedCount,
    avgCorrectRate: Math.round(rates.reduce((a, b) => a + b, 0) / rates.length),
    topWrong: input.wrongTags[0] || '—',
    branchesUsed: input.branchesUsed.map((k) => BRANCH_CARDS.find((c) => c.kind === k)?.label || k),
    durationMin: Math.max(1, Math.round(input.elapsedMs / 60000)),
  }
}
