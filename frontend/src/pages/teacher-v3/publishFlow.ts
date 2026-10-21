/**
 * 发布四步编排（tel PLAN §1.2② / §8.2 B5）：选班 → 组卷 → 教师确认 → 发布。
 * 纯函数编排（无 Vue 依赖），AssignView 消费、teacher-two-end-link.test.ts 状态机锁。
 * 契约：8000 POST /api/teacher/quizzes/generate（GenerateQuizRequest）→
 * POST /api/teacher/artifacts/{id}/confirm（教师显式确认，无静默）→
 * POST /api/teacher/assignments（client_assignment_id 前端稳定幂等键）→
 * POST /api/teacher/assignments/{id}/publish（学生任务中心可见）。
 */
import type { ApiAssignment, ApiAssignmentType, ApiQuizSetArtifact } from '@/api/api8000'
import type { ApiClassItem } from '@/api/api8000'
import { isTeachingClass } from '@/utils/teachingClasses'

export interface PublishFlowInput {
  classId: string
  title: string
  type: ApiAssignmentType
  deadline?: string
  knowledgePoints: string[]
  count: number
  questionTypes: { choice: number; blank: number; text: number }
  difficulty: { easy: number; medium: number; hard: number }
  /** 前端生成稳定键：重试/断网重发不重复建作业（assessment.py:335-347 幂等回读） */
  clientAssignmentId: string
  /** tel E2E 修复：教师已预览的 draft artifact。确认发布必须复用它——否则 runPublishFlow
   *  会再次 generate（GenerateQuizRequest 无种子，两次供给结果不同），教师确认的题集被
   *  静默替换成另一份，破坏「确认什么就发什么」语义（实测 artifact 与学生 quiz_items 不一致）。 */
  artifact?: ApiQuizSetArtifact
}

export type PublishStage =
  | 'idle' | 'generating' | 'generated' | 'confirming'
  | 'confirmed' | 'creating' | 'publishing' | 'published' | 'failed'

export interface PublishFlowDeps {
  generate: (input: PublishFlowInput) => Promise<ApiQuizSetArtifact>
  confirm: (artifactId: string, clientRequestId: string, idempotencyKey?: string) => Promise<ApiQuizSetArtifact>
  create: (body: {
    class_id: string
    title: string
    artifact_id: string
    client_assignment_id: string
    deadline?: string
    type?: ApiAssignmentType
  }) => Promise<ApiAssignment>
  publish: (assignmentId: string, clientRequestId: string, idempotencyKey?: string) => Promise<ApiAssignment>
  onStage?: (stage: PublishStage, detail?: { error?: unknown; artifact?: ApiQuizSetArtifact; assignment?: ApiAssignment }) => void
}

export interface PublishFlowResult {
  artifact: ApiQuizSetArtifact
  assignment: ApiAssignment
}

/** 驱动四步：任一步失败 → onStage('failed') 并上抛（调用方如实呈现，不假成功）。 */
export async function runPublishFlow(input: PublishFlowInput, deps: PublishFlowDeps): Promise<PublishFlowResult> {
  try {
    deps.onStage?.('generating')
    // 已有教师预览过的 draft → 直接复用，不再重新组卷（见 PublishFlowInput.artifact 注释）
    const draft = input.artifact ?? (await deps.generate(input))
    deps.onStage?.('generated', { artifact: draft })

    deps.onStage?.('confirming')
    const confirmed = await deps.confirm(draft.artifact_id, `confirm-${input.clientAssignmentId}`, `confirm:${input.clientAssignmentId}`)
    if (confirmed.status !== 'confirmed') {
      throw new Error('题集确认未生效（后端未返回 confirmed 状态），已停止发布')
    }
    deps.onStage?.('confirmed', { artifact: confirmed })

    deps.onStage?.('creating')
    const created = await deps.create({
      class_id: input.classId,
      title: input.title,
      artifact_id: confirmed.artifact_id,
      client_assignment_id: input.clientAssignmentId,
      deadline: input.deadline || undefined,
      type: input.type,
    })
    // 幂等回读（replayed=true）也继续发布：终态一致，publish 自带幂等
    deps.onStage?.('publishing', { assignment: created })

    const published = await deps.publish(created.assignment_id, `publish-${input.clientAssignmentId}`, `publish:${input.clientAssignmentId}`)
    if (published.status !== 'published') {
      throw new Error(`发布未生效（当前状态 ${published.status}）`)
    }
    deps.onStage?.('published', { assignment: published })
    return { artifact: confirmed, assignment: published }
  } catch (error) {
    deps.onStage?.('failed', { error })
    throw error
  }
}

/**
 * pickClass 引导态（tel §8.2 ClassroomView① / §9.3）：
 * 无班级 → ''（页面渲染「先到班级管理建班/加入」引导态）；
 * 有班级 → 取第一个「本人在班且已确认」的教学角色班级真实 UUID（无 mock 默认值）。
 */
export function pickDefaultClassId(items: ApiClassItem[]): string {
  const usable = items.filter(isTeachingClass)
  return usable[0]?.id ?? ''
}

/** 作业类型展示映射（学生端 TYPE_MAP 对齐：pre_class 🎓 / homework 📝 / quiz 课堂练习） */
export const ASSIGNMENT_TYPE_LABEL: Record<ApiAssignmentType, string> = {
  pre_class: '🎓 学前作业',
  homework: '📝 课后习题',
  quiz: '✏️ 课堂练习',
}

export const ASSIGNMENT_STATUS_LABEL: Record<string, string> = {
  draft: '草稿（未发布）',
  published: '已发布',
  closed: '已截止',
  archived: '已归档',
}
