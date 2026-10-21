/**
 * 技能配置 —— 点亮 = 路由偏好（out-of-band），发送时经请求体 context.skills 下发，
 * 不再拼 slash 前缀；用户手输 "/解题 xxx" 仍由后端 L0 处理。
 * chat 为默认态（未点亮任何技能）：不劫持路由，后端 LLM 智能判定技能。
 */
export const SKILLS = [
  { key: 'socratic', icon: '💡', title: '引导式解题', prefix: '/解题' },
  { key: 'quiz_gen', icon: '📝', title: '智能出题', prefix: '/出题' },
  { key: 'kb_qa', icon: '📚', title: '知识库答疑', prefix: '/答疑' },
  { key: 'chat', icon: '💬', title: '自由对话', prefix: '' }, // 默认：智能路由
]

export const DEFAULT_SKILL = 'chat' // 默认自由对话：不点亮任何技能，交给后端智能路由

/** 前端技能 key ↔ 后端 skill_id 映射（点亮集合经 context.skills 下发，chat 不下发） */
export const SKILL_ID_BY_KEY = { socratic: 'socratic_solver', quiz_gen: 'smart_quiz', kb_qa: 'qa_rag' }
export const SKILL_KEY_BY_ID = { socratic_solver: 'socratic', smart_quiz: 'quiz_gen', qa_rag: 'kb_qa' }

export function skillByKey(key) {
  return SKILLS.find((s) => s.key === key) || SKILLS[SKILLS.length - 1]
}
