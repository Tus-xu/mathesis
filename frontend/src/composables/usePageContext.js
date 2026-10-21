/**
 * 页面上下文注册表（S16：悬浮球全局上下文感知）。
 *
 * 各页面在内容变化时写入当前上下文摘要（题目/错题/会话），
 * AI 悬浮球发送时读取并拼进消息——学生在哪页提问，AI 就知道那页的内容。
 * 只存摘要文本（≤200 字），不存引用/回调，页面卸载时清空。
 */
import { reactive } from 'vue'

const state = reactive({
  route: '',
  title: '',
  detail: '', // ≤200 字内容摘要
})

export function setPageContext({ route, title, detail }) {
  if (route !== undefined) state.route = route
  if (title !== undefined) state.title = String(title || '').slice(0, 60)
  if (detail !== undefined) state.detail = String(detail || '').slice(0, 200)
}

export function clearPageContext(route) {
  if (!route || state.route === route) {
    state.route = ''
    state.title = ''
    state.detail = ''
  }
}

/** 组装进消息的上下文前缀（无上下文返回空串） */
export function contextPrefix() {
  if (!state.detail && !state.title) return ''
  const parts = [`[学生在「${state.title || state.route}」页面，正在看：${state.detail}。回答请结合这个上下文]`]
  return parts.join('') + '\n'
}

export function getPageContext() {
  return state
}
