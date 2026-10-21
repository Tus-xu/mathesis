import type { ArtifactStatus } from '@/types/teacher'

export type ArtifactAction = 'edit' | 'regenerate' | 'confirm' | 'derive' | 'publish' | 'archive' | 'view' | 'copy_draft'
export type ArtifactActionLabel = Record<ArtifactAction, string>

export const ARTIFACT_ACTION_LABEL: ArtifactActionLabel = {
  edit: '编辑', regenerate: '重新生成', confirm: '确认', derive: '生成衍生产物',
  publish: '发布', archive: '归档', view: '查看', copy_draft: '复制为新草稿',
}

/** Artifact 状态 -> 允许动作（产品 SSOT v2.1 第十一节） */
const MAP: Record<ArtifactStatus, ArtifactAction[]> = {
  draft: ['edit', 'regenerate', 'confirm', 'archive'],
  confirmed: ['derive', 'publish', 'archive'],
  published: ['view', 'copy_draft', 'archive'],
  archived: ['view', 'copy_draft'],
}

export function allowedActions(status: ArtifactStatus): ArtifactAction[] {
  return MAP[status] ?? []
}