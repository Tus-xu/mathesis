/**
 * 后台任务中心 + 站内通知 + 学生端 AI 管家对话 端点封装
 * 契约（信封 {code:0,message,data}）：
 * - GET  /api/tasks?status=&since=&limit= → data.items[{task_id,kind,kind_label,role,status,progress,stage,payload,result,error,attempt,elapsed_ms,created_at,finished_at}]
 * - POST /api/tasks {kind,payload,idempotency_key?} → data 含 task_id/status/created
 * - GET  /api/tasks/{id}、POST /api/tasks/{id}/cancel、POST /api/tasks/{id}/retry
 * - GET  /api/notifications?unread_only=&limit= → items[{id,type,title,body,payload{jump},read_at,created_at}]
 * - GET  /api/notifications/unread-count → {count}
 * - POST /api/notifications/{id}/read、POST /api/notifications/read-all
 * - POST /api/butler/chat {message, client_request_id} → data.envelope={replies,actions,run_id}
 *   （学生端管家；404/50301 时调用方需降级为纯任务模式）
 */
import { api } from './client'

export const tasksApi = {
  list: (params = {}) => api.get('/tasks', params),
  create: (payload) => api.post('/tasks', payload),
  detail: (taskId) => api.get(`/tasks/${encodeURIComponent(taskId)}`),
  cancel: (taskId) => api.post(`/tasks/${encodeURIComponent(taskId)}/cancel`),
  retry: (taskId) => api.post(`/tasks/${encodeURIComponent(taskId)}/retry`),
}

export const notificationsApi = {
  list: (params = {}) => api.get('/notifications', params),
  unreadCount: () => api.get('/notifications/unread-count'),
  markRead: (id) => api.post(`/notifications/${encodeURIComponent(id)}/read`),
  readAll: () => api.post('/notifications/read-all'),
}

export const butlerChatApi = {
  /** message + client_request_id（幂等）；失败时抛 ApiError（code 404/50301 = 管家未开通，调用方降级） */
  chat: (message, clientRequestId) => api.post('/butler/chat', { message, client_request_id: clientRequestId }),
}
