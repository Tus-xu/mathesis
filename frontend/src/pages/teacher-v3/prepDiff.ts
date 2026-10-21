/**
 * B2 · 旧课二次备课 diff（创新 3）
 * 确定性规则生成差异建议：依据只允许「班级学情 / 教师指令 / 课时变化 / 教材版本 / AI 建议（显式标注）」，
 * 不得把 AI 改写伪装成教材事实（§7.2）。应用/拒绝/恢复全部纯函数，便于单测。
 */
import type { V3LessonPlan } from '@/types/teacherV3'

export type DiffKind = 'keep' | 'replace' | 'add'
export type DiffBasis = 'class_evidence' | 'teacher_directive' | 'duration' | 'ai_suggest'

export interface DiffItem {
  id: string
  kind: DiffKind
  basis: DiffBasis
  /** 应用位置：sectionId + 字段（或 objectives/homework） */
  target: { sectionId?: string; field: 'teacher_activity' | 'objectives' | 'homework' | 'examples' | 'student_activity'; index?: number }
  title: string
  reason: string
  newValue?: string
  newExample?: { label: string; stem_latex: string; answer: string; difficulty: string; source: string }
  status: 'pending' | 'accepted' | 'rejected'
}

export interface DiffContext {
  className: string
  weakKp: string
  duration: number
}

const BASIS_LABEL: Record<DiffBasis, string> = {
  class_evidence: '本班学情',
  teacher_directive: '教师指令',
  duration: '课时变化',
  ai_suggest: 'AI 建议',
}
export const basisLabel = (b: DiffBasis) => BASIS_LABEL[b]

/** 确定性生成差异建议：同一份旧教案 + 同一上下文 → 同一组建议（可复现） */
export function generateDiff(oldPlan: V3LessonPlan, ctx: DiffContext): DiffItem[] {
  const items: DiffItem[] = []
  const weak = ctx.weakKp || '本班薄弱点'
  const sec = (id: string) => oldPlan.sections.find((s) => s.id === id)

  // 1. 保留项（默认接受）：定义/推导类主干内容
  const explore = sec('bd-explore')
  if (explore) {
    items.push({
      id: 'd-keep-core', kind: 'keep', basis: 'class_evidence',
      target: { sectionId: explore.id, field: 'teacher_activity' },
      title: `保留「${explore.name}」：定义与推导主干未过时`,
      reason: '教材版本未变化，主干知识直接复用，减少重写',
      status: 'pending',
    })
  }

  // 2. 替换情境引入：按新班薄弱点定向（班级学情依据）
  const intro = sec('bd-intro')
  if (intro) {
    items.push({
      id: 'd-intro', kind: 'replace', basis: 'class_evidence',
      target: { sectionId: intro.id, field: 'teacher_activity' },
      title: `替换「${intro.name}」情境：对准本班薄弱点`,
      reason: `本班（${ctx.className}）薄弱：${weak}——旧情境未针对该点，建议换成能暴露该薄弱点的对比情境`,
      newValue: `结合本班「${weak}」薄弱，出示两个对比示例让学生先判断再引入：一个符合定义、一个踩中薄弱点，提问"差别在哪"。`,
      status: 'pending',
    })
  }

  // 3. 替换例题：旧例换成本班错因相关变式（班级学情 + AI 建议显式标注）
  const examples = sec('bd-examples')
  if (examples) {
    items.push({
      id: 'd-example', kind: 'replace', basis: 'class_evidence',
      target: { sectionId: examples.id, field: 'examples', index: 0 },
      title: `替换「${examples.name}」例 1：从"已知参数求方程"升级为本班错因变式`,
      reason: `往年该例正确率高，边际价值低；本班聚类显示「${weak}」错误集中，换为对应的判别/关系变式`,
      newExample: {
        label: '例 1（二备换入）',
        stem_latex: `结合本班「${weak}」易错点设置：先判别焦轴（或实轴）位置，再求标准方程；要求写出判别依据。`,
        answer: '按判别→定参→写方程三步给分',
        difficulty: 'medium',
        source: '二备·班级学情',
      },
      status: 'pending',
    })
  }

  // 4. 易错辨析补本班高频错例（班级学情）
  const pitfalls = sec('bd-pitfalls')
  if (pitfalls) {
    items.push({
      id: 'd-pitfall', kind: 'add', basis: 'class_evidence',
      target: { sectionId: pitfalls.id, field: 'teacher_activity' },
      title: `「${pitfalls.name}」追加一条本班高频错例`,
      reason: `作业聚类显示「概念混淆」人次上升，补一个针对「${weak}」的错例辨析`,
      newValue: `${pitfalls.teacher_activity}\n【二备追加】出示错例：混淆判别方式（套用椭圆"看分母大小"），让学生先判断错在哪一步，再说正确判别依据。`,
      status: 'pending',
    })
  }

  // 5. 巩固层作业按新班分层调整（班级学情）
  const consolidate = oldPlan.homework_tiers?.find((t) => t.tier === '巩固')
  if (consolidate && consolidate.items.length) {
    items.push({
      id: 'd-homework', kind: 'replace', basis: 'class_evidence',
      target: { field: 'homework', index: oldPlan.homework_tiers.indexOf(consolidate) },
      title: '替换「巩固」层作业第 1 条：对准本班薄弱',
      reason: `巩固层应落在班级中位水平——围绕「${weak}」出 2 题（1 题基本 + 1 题变式）`,
      newValue: `围绕「${weak}」：① 基本题（直接应用定义/方程）；② 变式题（改一个条件，检验迁移）`,
      status: 'pending',
    })
  }

  // 6. 课时对齐（课时变化依据）
  const total = oldPlan.sections.reduce((a, s) => a + (Number(s.minutes) || 0), 0)
  if (ctx.duration && total !== ctx.duration) {
    items.push({
      id: 'd-duration', kind: 'replace', basis: 'duration',
      target: { field: 'teacher_activity' },
      title: `环节时长合计 ${total} 分钟，与本节 ${ctx.duration} 分钟不一致`,
      reason: '旧课时长结构与本届课表不同：按比例压缩可压缩环节（导入/练习），保留主干',
      newValue: `（生成时将按 ${ctx.duration} 分钟等比缩放可授课环节，目标/重难点等非授课板块不计）`,
      status: 'pending',
    })
  }

  // 7. AI 建议（显式标注）：教学目标改为可检验行为
  if (oldPlan.objectives.length >= 2) {
    items.push({
      id: 'd-objective', kind: 'replace', basis: 'ai_suggest',
      target: { field: 'objectives', index: oldPlan.objectives.length - 1 },
      title: '最后一条教学目标改写为可检验行为',
      reason: 'AI 建议：目标含"理解/掌握"等不可观察动词，建议改为"能根据条件求…并写出判别依据"这类可出题的行为动词表述',
      newValue: `能根据条件求${oldPlan.topic.replace(/（.*）/, '')}的标准方程，并写出焦轴（实轴）位置的判别依据`,
      status: 'pending',
    })
  }
  return items
}

/** 应用已接受的建议：深拷贝不改旧 plan；拒绝项保持原样 */
export function applyDiff(oldPlan: V3LessonPlan, items: DiffItem[]): V3LessonPlan {
  const plan: V3LessonPlan = JSON.parse(JSON.stringify(oldPlan))
  for (const item of items) {
    if (item.status !== 'accepted') continue
    if (item.kind === 'keep') continue
    const { sectionId, field, index } = item.target
    if (field === 'objectives' && typeof index === 'number' && plan.objectives[index] !== undefined && item.newValue) {
      plan.objectives[index] = item.newValue
    } else if (field === 'homework' && typeof index === 'number' && plan.homework_tiers?.[index] && item.newValue) {
      plan.homework_tiers[index].items = [item.newValue]
    } else if (sectionId) {
      const s = plan.sections.find((x) => x.id === sectionId)
      if (!s) continue
      if (field === 'teacher_activity' && item.newValue) s.teacher_activity = item.newValue
      if (field === 'student_activity' && item.newValue) s.student_activity = item.newValue
      if (field === 'examples' && item.newExample) {
        s.examples = [{
          id: `ex-${item.id}`,
          label: item.newExample.label,
          q_type: 'solve',
          difficulty: item.newExample.difficulty as V3LessonPlan['sections'][number]['examples'] extends (infer E)[] | undefined ? (E extends { difficulty: infer D } ? D : never) : never,
          stem_latex: item.newExample.stem_latex,
          answer: item.newExample.answer,
          source: item.newExample.source as V3LessonPlan['sections'][number]['examples'] extends (infer E)[] | undefined ? (E extends { source: infer S } ? S : never) : never,
        }]
      }
    }
  }
  return plan
}

/** 汇总状态：用于 diff 抽屉头部（已接受 x / 待处理 y / 已拒绝 z） */
export function diffStats(items: DiffItem[]) {
  return {
    accepted: items.filter((i) => i.status === 'accepted').length,
    pending: items.filter((i) => i.status === 'pending').length,
    rejected: items.filter((i) => i.status === 'rejected').length,
  }
}
