/**
 * 备课中心（teacher-prep-center）命名空间出口。
 * 引入本命名空间即注入 --pc-* 视觉 token（唯一来源 tokens/prep.css）。
 */
import '@/features/teacher-prep-center/tokens/prep.css'

export { registerPrepCenter } from '@/features/teacher-prep-center/route/registerPrepCenter'
export { usePrepSessionStore } from '@/features/teacher-prep-center/stores/prepSession'
export {
  PrepCenterApiError,
  createConversation,
  getWorkspace,
  listSessionSpecs,
  listSessions,
  sendMessage,
  type PrepSessionSummary,
  type PrepWorkspaceSnapshot,
} from '@/features/teacher-prep-center/api/prepCenterApi'
