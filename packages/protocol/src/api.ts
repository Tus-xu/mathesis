/**
 * API 请求/响应类型定义（§5）
 */

/** 聊天请求 */
export interface ChatRequest {
  conversation_id: string | null
  message: string
  context: {
    page: string
    workspace: string
    client_msg_id: string
  }
}

/** 通用 API 响应 */
export interface ApiResponse<T = unknown> {
  code: number
  data: T
  message?: string
  request_id?: string
}

/** 用户信息 */
export interface User {
  id: string
  nickname: string
  active_role: string
  roles: string[]
  verified: boolean
}

/** 登录响应 */
export interface LoginResponse {
  token: string
  user: User
}

/** 会话信息 */
export interface Conversation {
  id: string
  title: string
  summary: string
  message_count: number
  created_at: string
  updated_at: string
}

/** 班级信息 */
export interface ClassInfo {
  id: string
  name: string
  invite_code: string
  owner_id: string
  grade: string
  subject: string
  status: string
  created_at: string
  updated_at: string
}

/** 班级成员 */
export interface ClassMember {
  id: string
  class_id: string
  user_id: string
  member_role: 'student' | 'teacher'
  confirmed: boolean
  join_via: string
  nickname_in_class: string
  joined_at: string
}

/** 记忆信息 */
export interface Memory {
  id: string
  content: string
  importance: number
  created_at: string
}
