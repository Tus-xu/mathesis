/** 仅本地预览模式使用的本地预览身份；默认真实 API 模式绝不预置 mock 身份 */
export const MOCK_TOKEN_STUDENT = 'mock-token-preview'
export const MOCK_TOKEN_TEACHER = 'mock-token-teacher-preview'
export const MOCK_TOKEN_RESEARCHER = 'mock-token-researcher-preview'
export const MOCK_TOKEN_ADMIN = 'mock-token-admin-preview'

type MockRole = 'student' | 'teacher' | 'researcher' | 'admin'
type MockState = 'approved' | 'pending' | 'needs_more_info' | 'rejected' | 'suspended'

type MockUser = { nickname: string; roles: { role: MockRole }[]; active_role: MockRole; grade: string }

const previewUsers: Record<MockRole, MockUser> = {
  student: { nickname: '小婷', roles: [{ role: 'student' }], active_role: 'student', grade: '高二（3）班' },
  teacher: { nickname: '李老师', roles: [{ role: 'teacher' }], active_role: 'teacher', grade: '' },
  researcher: { nickname: '陈研究员', roles: [{ role: 'researcher' }], active_role: 'researcher', grade: '' },
  admin: { nickname: '管理员', roles: [{ role: 'admin' }], active_role: 'admin', grade: '' },
}

function isMockRole(value: unknown): value is MockRole {
  return typeof value === 'string' && value in previewUsers
}

function isMockState(value: unknown): value is MockState {
  return ['approved', 'pending', 'needs_more_info', 'rejected', 'suspended'].includes(String(value))
}

function cookieValue(cookie: string, name: string): string | null {
  return cookie.match(new RegExp(`(?:^|;\\s*)${name}=([^;]+)`))?.[1] || null
}

function parseStoredUser(raw: string | null): Record<string, unknown> | null {
  try {
    const parsed = JSON.parse(raw || 'null')
    return parsed && typeof parsed === 'object' ? parsed : null
  } catch {
    return null
  }
}

export function resolveMockStartupIdentity(defaultRole: string, cookie: string, storedUserJson: string | null) {
  const storedUser = parseStoredUser(storedUserJson)
  const cookieRole = cookieValue(cookie, 'ma_mock_role')
  const cookieState = cookieValue(cookie, 'ma_mock_state')
  const role = isMockRole(cookieRole) ? cookieRole : isMockRole(storedUser?.active_role) ? storedUser.active_role : isMockRole(defaultRole) ? defaultRole : 'student'
  const state = isMockState(cookieState) && cookieState === 'suspended' ? 'suspended' : 'approved'
  return { role, state, activeRole: role, storedUser }
}

export function resolveMockUser(mockRole: string): MockUser {
  if (mockRole in previewUsers) {
    return previewUsers[mockRole as MockRole]
  }
  return previewUsers.student
}

export function isPreviewToken(token: string | null): boolean {
  return [MOCK_TOKEN_STUDENT, MOCK_TOKEN_TEACHER, MOCK_TOKEN_RESEARCHER, MOCK_TOKEN_ADMIN].includes(token || '')
}
