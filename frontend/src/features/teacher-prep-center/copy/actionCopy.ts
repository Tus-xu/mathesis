/**
 * 备课中心「动作」文案表。
 *
 * 唯一来源：服务端 `available_actions[].copy_key`（状态机权威推导）。
 * 本表**只收录前端已实现的动作**：`actionLabel` 对未收录的 copy_key 返回 null，
 * 调用方据此执行「未实现不渲染」——绝不把英文动作名或内部码直接上屏。
 *
 * 文案与旧命名空间 teacher-prep-v2/i18n/copy.ts 保持一致（同一产品语义），
 * 但本文件独立维护，不跨命名空间 import。
 */
export const ACTION_COPY: Record<string, string> = {
  'common.retry': '重试',
  'prep.action.confirm_proposal': '采用这份方案',
  'prep.action.reject_proposal': '不要这份方案',
  // 该动作一次产出**两份** spec（payload kinds=['lesson_plan','deck']）：教案 + 课件
  'prep.action.project_specs': '生成教案与课件',
  // cancel_run 只中断本次生成（可重来），与 cancel_session（不可逆结束会话）语义不同
  'prep.action.cancel_run': '停止生成',
}

/** copy_key → 文案；未收录返回 null（用于「未实现不渲染」）。 */
export function actionLabel(copyKey: string | null | undefined): string | null {
  if (!copyKey) return null
  return ACTION_COPY[copyKey] ?? null
}