/**
 * 全部后端端点封装（M0–M2 全量 + admin 管理后台）
 * 页面组件只允许调用这里导出的函数，禁止散落 fetch。
 */
import { api } from './client'
import { setHttpPolicy } from './httpPolicy'
import { uuid } from '../utils/uuid.js'

// LLM 重接口超时策略（2026-10-11 学生端出题 30s 超时事故）：
// 出题/批改/错题分析等服务端真实耗时 35-90s，全局默认 30s 会把成功请求掐死在前端。
// 匹配按注册序首个前缀命中：具体前缀在前，宽前缀兜底在后。
setHttpPolicy({
  timeoutByPrefix: {
    '/student/practice/start': 180000,
    '/student/practice/submit': 180000,
    '/student/practice/smart-score': 180000,
    '/student/': 120000,
    '/agent/': 180000,
  },
})
export { authApi } from './auth'

/* ===== 认证 /api/auth ===== */

/* ===== 智能体 /api/agent ===== */
export const agentApi = {
  // M2：支持 limit / before(updated_at 游标) / q 搜索；旧后端忽略未知参数，前端配合去重兜底
  conversations: (params = {}) => api.get('/agent/conversations', params),
  createConversation: (workspace = 'student') => api.post('/agent/conversations', { workspace }),
  // M2：支持 limit / before(消息 id 游标)
  conversationMessages: (id, params = {}) => api.get(`/agent/conversations/${id}/messages`, params),
  deleteConversation: (id) => api.del(`/agent/conversations/${id}`),
  // M2 新增：重命名 / 置顶（旧后端 405/404，调用方需优雅降级）
  patchConversation: (id, payload) => api.patch(`/agent/conversations/${id}`, payload),
  // M2：body 新旧双写（message_id/value 新契约，target_msg_id 旧契约；FastAPI 忽略多余字段）
  feedback: (msgId, value, reason = '') =>
    api.post('/agent/feedback', { message_id: msgId, target_msg_id: msgId, value, reason }),
  // M2 新增端点（旧后端 404，调用方降级）
  stopChat: (conversation_id, client_msg_id = '') =>
    api.post('/agent/chat/stop', { conversation_id, ...(client_msg_id ? { client_msg_id } : {}) }),
  activateMessage: (messageId) => api.post(`/agent/messages/${messageId}/activate`),
  memories: () => api.get('/agent/memories'),
  deleteMemory: (id) => api.del(`/agent/memories/${id}`),
  // 平台功能地图 + 能力开关（阶段 6A）：capabilities.web_search_opt_in_enabled
  features: () => api.get('/agent/features'),
}

/* ===== 文件 /api/files ===== */
export const filesApi = {
  uploadInit: (payload) => api.post('/files/upload', payload),
  complete: (fileId, payload) => api.post(`/files/${fileId}/complete`, payload),
  parse: (fileId, payload = {}) => api.post(`/files/${fileId}/parse`, payload),
  detail: (fileId) => api.get(`/files/${fileId}`),
  assetUrl: (fileId, assetId) => api.get(`/files/${fileId}/assets/${assetId}/url`),
  // M2 新增：取原文件预签名 GET / 代理内容（旧后端 404，前端降级图标）
  contentUrl: (fileId) => api.get(`/files/${fileId}/content`),
}

/* ===== 语音 /api/agent/speech ===== */
export const speechApi = {
  asrToken: () => api.post('/agent/speech/asr-token'),
  // 后端契约：{asr_text, session_id, conversation_id?, context_kp?}
  toLatex: (asr_text, { session_id, conversation_id, context_kp } = {}) =>
    api.post('/agent/speech/to-latex', {
      asr_text,
      session_id: session_id || `web_${uuid()}`,
      ...(conversation_id ? { conversation_id } : {}),
      ...(context_kp ? { context_kp } : {}),
    }),
}

/* ===== 学生端 /api/student ===== */
export const studentApi = {
  // F4 错题本
  errorRecords: (params = {}) => api.get('/student/error-records', params),
  createErrorRecord: (payload) => api.post('/student/error-records', payload),
  reviewErrorRecord: (id, payload) => api.post(`/student/error-records/${id}/review`, payload),
  // S6 错题删除（后端软删；越权 404 不泄露存在性）
  deleteErrorRecord: (id) => api.del(`/student/error-records/${id}`),
  // 迭代15 L0-3：学习事件总线（判分上报 → 错题/学情/复习服务端统一分发）
  reportLearningEvent: (payload) => api.post('/student/learning-events', payload),
  // F3/F5 刷题作答
  practiceStart: (payload) => api.post('/student/practice/start', payload),
  practiceSubmit: (payload) => api.post('/student/practice/submit', payload),
  practiceDaily: () => api.get('/student/practice/daily'),
  streak: () => api.get('/student/streak'),
  // F6 学情
  masterySummary: () => api.get('/student/mastery/summary'),
  masteryTrend: (days = 30) => api.get('/student/mastery/trend', { days }),
  weakPoints: () => api.get('/student/report/weak-points'),
  growthOverview: () => api.get('/student/growth/overview'),
  // 迭代15 B6：今日 3 件事行动清单（学情页首屏行动化）+ 复习计划（对话内复习提醒）
  todayActions: () => api.get('/student/mastery/today-actions'),
  reviewPlan: () => api.get('/student/error-records/review-plan'),
  // R2：到期错题队列（FSRS 实时保持率 retrievability/urgency_label，对话复习提醒卡用）
  dueQueue: () => api.get('/student/error-records/due-queue'),
  // F7 知识图谱
  knowledgeGraph: () => api.get('/student/knowledge-graph'),
  knowledgeNode: (kpCode) => api.get(`/student/knowledge-graph/nodes/${encodeURIComponent(kpCode)}`),
  // 模拟试卷（题库优先组卷）
  examGenerate: (payload) => api.post('/student/exam/generate', payload),
  examHistory: (params = {}) => api.get('/student/exam/history', params),
  examDetail: (id) => api.get(`/student/exam/${id}`),
  // F8 学习路径
  dailyPlan: () => api.get('/student/daily-plan'),
  // F10 任务
  assignments: (status) => api.get('/student/assignments', status ? { status } : {}),
  assignmentDetail: (id) => api.get(`/student/assignments/${id}`),
  assignmentResult: (id) => api.get(`/student/assignments/${id}/result`),
  // 闭环迭代13：练题中心首页推荐聚合（薄弱点+模拟卷+待复习+连击+今日一题）
  labRecommend: () => api.get('/student/lab/recommend'),
  // 老师课堂（教师发布到班级的课件）：我班已发布课件列表 / 打开我的课堂会话
  // 契约：GET  → data {items:[{courseware_id,title,class_id,class_name,teacher_name,
  //                          page_count,published_at,opened,progress_pct,practice_accuracy,last_session_id}]}
  //       POST → data {session_id,title}
  studentCoursewares: () => api.get('/student/classroom-coursewares'),
  openStudentCourseware: (id) => api.post(`/student/classroom-coursewares/${encodeURIComponent(id)}/open`),
}

/* ===== AI 管家 /api/butler（迭代17 全域驱动）===== */
export const butlerApi = {
  dashboard: () => api.get('/butler/dashboard'),
  dailyPlan: () => api.get('/butler/daily-plan'),
  weeklyReport: () => api.get('/butler/weekly-report'),
  errorDiagnosis: (recordId) => api.get(`/butler/error-diagnosis/${recordId}`),
  errorDetail: (recordId) => api.get(`/butler/error-detail/${recordId}`),
  errorTutor: ({ recordId, student_message, history }) =>
    api.post('/butler/error-tutor', { record_id: recordId, student_message, history: history || [] }),
  pathPlan: () => api.get('/butler/path-plan'),
  recommend: () => api.get('/butler/recommend'),
  actions: (limit = 20) => api.get('/butler/actions', { limit }),
  feedback: (actionId, feedback) => api.post(`/butler/actions/${actionId}/feedback`, { feedback }),
  settings: () => api.get('/butler/settings'),
  updateSettings: (payload) => api.patch('/butler/settings', payload),
}

/* ===== AI 数学课堂 /api/classroom（OpenMAIC 融合：大纲→逐页内容） ===== */
export const classroomApi = {
  // 会话列表（支持筛选：status/source_type/kp_code/date_from/limit）
  sessions: (params = {}) => api.get('/classroom/sessions', params),
  createSession: (payload) => api.post('/classroom/sessions', payload),
  session: (id) => api.get(`/classroom/sessions/${encodeURIComponent(id)}`),
  // 历史闭环端点
  updateProgress: (id, payload) => api.patch(`/classroom/sessions/${encodeURIComponent(id)}/progress`, payload),
  updateNotes: (id, notes) => api.patch(`/classroom/sessions/${encodeURIComponent(id)}/notes`, { notes }),
  appendQa: (id, payload) => api.post(`/classroom/sessions/${encodeURIComponent(id)}/qa`, payload),
  answerPractice: (id, payload) => api.post(`/classroom/sessions/${encodeURIComponent(id)}/practice-answer`, payload),
  regenSlide: (id, order) => api.post(`/classroom/sessions/${encodeURIComponent(id)}/slides/${order}/regenerate`),
  cloneSession: (id) => api.post(`/classroom/sessions/${encodeURIComponent(id)}/clone`),
  deleteSession: (id) => api.delete(`/classroom/sessions/${encodeURIComponent(id)}`),
}
/* ===== 班级 /api/classes ===== */
export const classApi = {
  create: (payload) => api.post('/classes', payload),
  join: (invite_code) => api.post('/classes/join', { invite_code }),
  mine: () => api.get('/classes/mine'),
  members: (classId) => api.get(`/classes/${classId}/members`),
}

/* ===== 知识库 /api/kb（teacher/researcher/admin 试验台） ===== */
export const kbApi = {
  importDoc: (payload) => api.post('/kb/docs/import', payload),
  docs: (params = {}) => api.get('/kb/docs', params),
  chunks: (docId) => api.get(`/kb/docs/${docId}/chunks`),
  retrieve: (payload) => api.post('/kb/retrieve', payload),
  evalRecall: (params = {}) => api.get('/kb/eval/recall', params),
}

/* ===== 搜索 /api/search ===== */
export const searchApi = {
  web: (query) => api.post('/search/web', { query }),
}

/* ===== 学生知识库 /api/v1/kb（学生端 · 迭代20 P3） =====
   契约：PATCH /api/v1/kb/documents/{doc_id} body={category}，
   响应 {doc_id, category}；40400 不存在 / 40301 非属主 / 40001 非法分类 */
export const studentKbApi = {
  documents: (params = {}) => api.get('/v1/kb/documents', { kb_name: 'default', ...params }),
  chunks: (docId, params = {}) => api.get(`/v1/kb/documents/${docId}/chunks`, params),
  patchDocument: (docId, category) => api.patch(`/v1/kb/documents/${docId}`, { category }),
  removeDocument: (docId) => api.delete(`/v1/kb/documents/${docId}`),
  reparse: (docId) => api.post(`/v1/kb/documents/${docId}/reparse`),
}

/* ===== 学生题库 /api/v1/questions（只读；题目详情用于知识库点开） ===== */
export const questionBankApi = {
  list: (params = {}) => api.get('/v1/questions', params),
  detail: (id) => api.get(`/v1/questions/${id}`),
}

/* ===== 运维 /api/ops ===== */
export const opsApi = {
  xingchenUsage: () => api.get('/ops/xingchen/usage'),
  event: (payload) => api.post('/ops/events', payload),
}

/* ===== 管理后台 /api/admin ===== */
export const adminApi = {
  overview: () => api.get('/admin/overview'),
  // 模型配置
  getModel: () => api.get('/admin/system/model'),
  putModel: (payload) => api.put('/admin/system/model', payload),
  testModel: () => api.post('/admin/system/model/test'),
  // 星辰配置
  getXingchen: () => api.get('/admin/system/xingchen'),
  putXingchen: (payload) => api.put('/admin/system/xingchen', payload),
  // 云知识库
  getCloudKb: () => api.get('/admin/system/cloud-kb'),
  putCloudKb: (payload) => api.put('/admin/system/cloud-kb', payload),
  testCloudKb: () => api.post('/admin/system/cloud-kb/test'),
  // Embedding 向量服务
  getEmbedding: () => api.get('/admin/system/embedding'),
  putEmbedding: (payload) => api.put('/admin/system/embedding', payload),
  testEmbedding: () => api.post('/admin/system/embedding/test'),
  // Butler 授权
  getButler: () => api.get('/admin/system/butler'),
  putButler: (payload) => api.put('/admin/system/butler', payload),
  // 工作流（10 个）
  workflows: () => api.get('/admin/workflows'),
  putWorkflow: (name, payload) => api.put(`/admin/workflows/${encodeURIComponent(name)}`, payload),
  testWorkflow: (name) => api.post(`/admin/workflows/${encodeURIComponent(name)}/test`),
}
