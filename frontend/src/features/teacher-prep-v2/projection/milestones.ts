/**
 * T22.2 · 动态 Milestone Registry（纯函数，零 Vue / 零 DOM）。
 *
 * 人工裁决（DESIGN-CORRECTION-R2 §1-2）：
 *  - **不用固定 "4/6"**，也**不按 artifact 数量简单计数**；
 *  - 采用**动态 registry**：本节课实际启用哪些里程碑就显示哪些；可选项未启用**不进分母**；
 *  - UI 默认显示 "N 项已就绪 · M 项待确认"，点击后才展开 —— 不像 Wizard；
 *  - 非线性可跳：抽屉里每一项都能直接点，不强制顺序。
 *
 * 诚实性：后端未暴露端点的里程碑（lesson_plan / deck）`available=false`
 * → 不进 registry、不进分母，**不造假"已就绪"**。
 */

import type { ProjectionFacts } from './types'

export type MilestoneState = 'ready' | 'confirming' | 'generating' | 'pending'

export const MILESTONE_KEYS = [
  'textbook_context',
  'diagnostic',
  'profile',
  'blueprint',
  'lesson_plan',
  'deck',
] as const

export type MilestoneKey = (typeof MILESTONE_KEYS)[number]

export interface Milestone {
  key: MilestoneKey
  label: string
  state: MilestoneState
  /** 人类可读的补充说明（来自服务端事实；没有就不显示）。 */
  detail: string | null
}

interface MilestoneDef {
  key: MilestoneKey
  label: string
  /** 后端是否已暴露该能力；false → 不进 registry（诚实占位，不造假）。 */
  available: boolean
  /** 本节课是否启用了该里程碑；false → 不进分母。 */
  enabled: (f: ProjectionFacts) => boolean
  state: (f: ProjectionFacts) => MilestoneState
  detail: (f: ProjectionFacts) => string | null
}

const DEFS: readonly MilestoneDef[] = [
  {
    key: 'textbook_context',
    label: '教材上下文',
    available: true,
    enabled: () => true, // 每节备课都以教材为起点
    state: (f) =>
      f.contextSnapshotId ? 'ready' : f.hasRunningJob ? 'generating' : 'pending',
    detail: (f) => (f.contextSnapshotId ? `snapshot ${f.contextSnapshotId.slice(0, 8)}` : null),
  },
  {
    key: 'diagnostic',
    label: '学前诊断',
    available: true,
    enabled: (f) => f.diagnosticId !== null || f.diagnosticStatus !== null,
    state: (f) => {
      if (f.diagnosticStatus === 'published' || f.diagnosticStatus === 'draft') return 'ready'
      if (f.diagnosticStatus === 'proposal') return 'confirming'
      return f.hasRunningJob ? 'generating' : 'pending'
    },
    detail: (f) => (f.diagnosticStatus ? `status: ${f.diagnosticStatus}` : null),
  },
  {
    key: 'profile',
    label: '班级学情',
    // snapshot 未投影 ClassLearningProfile → 暂无事实来源，不进分母
    available: false,
    enabled: () => false,
    state: () => 'pending',
    detail: () => null,
  },
  {
    key: 'blueprint',
    label: '教学蓝图',
    available: true,
    enabled: (f) => f.blueprintId !== null || f.blueprintStatus !== null,
    state: (f) => {
      if (f.blueprintStatus === 'confirmed') return 'ready'
      if (f.blueprintStatus === 'proposal') return f.pendingActionId ? 'confirming' : 'confirming'
      if (f.blueprintStatus === 'rejected') return 'pending'
      return f.hasRunningJob ? 'generating' : 'pending'
    },
    detail: (f) =>
      f.blueprintRevision !== null ? `revision ${f.blueprintRevision}` : null,
  },
  {
    key: 'lesson_plan',
    label: '教案',
    // L-221-2：/prep/conversations/{id}/specs + artifact.lesson_plan 已暴露
    available: true,
    enabled: (f) => Boolean(f.lessonPlanId || f.blueprintStatus === 'confirmed'),
    state: (f) => {
      if (f.lessonPlanId) return 'ready'
      if (f.blueprintStatus === 'confirmed') return 'pending'
      return 'pending'
    },
    detail: (f) => (f.lessonPlanRevision ? `rev ${f.lessonPlanRevision}` : null),
  },
  {
    key: 'deck',
    label: 'PPT',
    // TeachingDeckSpec 权威未闭合（T23/T24）→ 不进 registry
    available: false,
    enabled: () => false,
    state: () => 'pending',
    detail: () => null,
  },
]

export interface MilestoneSummary {
  items: Milestone[]
  readyCount: number
  pendingCount: number
  /** 文案原型："N 项已就绪 · M 项待确认" */
  label: string
}

/**
 * 从服务端事实派生本节课的里程碑。**只含已启用的项**（未启用不进分母）。
 */
export function buildMilestones(f: ProjectionFacts): MilestoneSummary {
  const items: Milestone[] = []
  for (const d of DEFS) {
    if (!d.available) continue
    if (!d.enabled(f)) continue
    items.push({ key: d.key, label: d.label, state: d.state(f), detail: d.detail(f) })
  }
  const readyCount = items.filter((i) => i.state === 'ready').length
  const pendingCount = items.length - readyCount
  return {
    items,
    readyCount,
    pendingCount,
    label: `${readyCount} 项已就绪 · ${pendingCount} 项待确认`,
  }
}

/** 供契约测试与 UI 共用：registry 中"当前可用且已启用"的 key。 */
export function enabledMilestoneKeys(f: ProjectionFacts): MilestoneKey[] {
  return buildMilestones(f).items.map((i) => i.key)
}
