import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import { router } from './router'
import { setAccessToken } from './api/authSession'
import { resolveMockStartupIdentity, resolveMockUser } from './config/mockIdentity'
import { registerPrepSurface } from '@/features/teacher-prep-v2/route/registerPrepSurface'
import { registerResearchWorkspace } from '@/features/research-workspace/route/registerResearchWorkspace'
import 'katex/dist/katex.min.css'
import 'highlight.js/styles/github-dark.css'
// ── 平台设计系统：全局生效的基础层（必须最先加载）──
// 置于 legacy styles 之前：平台 :root 仅定义 --ma-* 与兼容别名，
// 后续 legacy :root 仍覆盖同名变量，确保现有页面配色零突变（见平台接线报告）。
import './platform/design-system/tokens.css'
import './platform/design-system/alias.css'
import './styles/tokens.css'
import './styles/editorial.css'
import './styles/base.css'
import './styles/v4.css'
import './styles/teacher-v3.css'
import './styles/teacher-tokens.css'
// 【已隔离 2026-09-17】上一代科研端的样式（styles/research.css、styles/research-end.css，
// 合计 92KB，定义 .rs-* / .re-* 类与 15 个 --re-* 变量）已随旧面移出。
// 已核实：幸存代码对 .rs-* 的引用为 0、对 var(--re-*) 的引用为 0、无 :root 级定义。
// 详见 D:\research-legacy-quarantine\README.md

// 本地预览便捷引导：仅本地预览模式才预置 mock 身份（默认真实 API 模式绝不预置）。
// VITE_MOCK_ROLE=teacher 预置教师；默认/student 保持学生。
const useMock = import.meta.env.VITE_USE_MOCK === '1'
let mockRole = (import.meta.env.VITE_MOCK_ROLE || 'student').toString()
if (useMock) {
  const startup = resolveMockStartupIdentity(mockRole, document.cookie, localStorage.getItem('ma_user'))
  mockRole = startup.role
  document.cookie = `ma_mock_role=${startup.role}; path=/; SameSite=Lax`
  document.cookie = `ma_mock_state=${startup.state}; path=/; SameSite=Lax`
  document.cookie = 'ma_csrf=mock-csrf; path=/; SameSite=Lax'
  const mockToken = startup.activeRole === 'student' ? 'mock-token-preview' : `mock-token-${startup.activeRole}-preview`
  setAccessToken(mockToken)
  try {
    if (!startup.storedUser) localStorage.setItem('ma_user', JSON.stringify(resolveMockUser(mockRole)))
  } catch { /* ignore */ }
}

const app = createApp(App)
app.use(createPinia())

// T22.3 SCR-1b：先注册沉浸式路由，再 use(router)，避免首跳落到 catch-all → /today
if (import.meta.env.VITE_PREP_SURFACE === 'immersive') {
  registerPrepSurface(router)
}
// RV17 · 科研工作台（research-workspace 新命名空间）——加法式注册，幂等。
// 【已隔离 2026-09-17】上一代沉浸式科研面（features/research-vnext）已移出，
// 其 registerResearchSurface 调用随之删除。详见 D:\research-legacy-quarantine\README.md
registerResearchWorkspace(router)

app.use(router)
app.mount('#app')
