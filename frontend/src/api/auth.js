import { api } from './client'
import { authScopeHeaders, refreshAccessToken, setAccessToken, setAuthScope } from './authSession'

export const authApi = {
  challengeSms: (phone, purpose) => api.post('/auth/challenges/sms', { phone, purpose }),
  loginSms: async (payload) => {
    const data = await api.post('/auth/login/sms', payload)
    setAccessToken(data.access_token)
    if (data?.user?.active_role) setAuthScope(data.user.active_role)
    return data
  },
  loginPassword: async (payload) => {
    const data = await api.post('/auth/login/password', payload)
    setAccessToken(data.access_token)
    if (data?.user?.active_role) setAuthScope(data.user.active_role)
    return data
  },
  registerSms: async (payload) => {
    const data = await api.post('/auth/register/sms', payload)
    setAccessToken(data.access_token)
    if (data?.user?.active_role) setAuthScope(data.user.active_role)
    return data
  },
  refreshSession: () => refreshAccessToken({ allowFallback: true }),
  me: () => api.get('/auth/me'),
  logout: () => api.post('/auth/logout', undefined, undefined, { headers: authScopeHeaders() }),
  logoutAll: () => api.post('/auth/logout-all', undefined, undefined, { headers: authScopeHeaders() }),
  switchRole: async (role) => {
    const data = await api.post('/auth/role/switch', { role })
    setAccessToken(data.access_token || data.token)
    if (data?.active_role) setAuthScope(data.active_role)
    return data
  },
  setPassword: (password) => api.post('/auth/password/set', { password }),
  resetPassword: (payload) => api.post('/auth/password/reset', payload),
  onboardStudent: (payload) => api.post('/identity/onboarding/student', payload),
  submitRoleApplication: (payload) => api.post('/identity/role-applications', payload),
  currentRoleApplications: () => api.get('/identity/role-applications/current'),
}

export const securityApi = {
  sessions: () => api.get('/auth/sessions'),
  revokeSession: (id) => api.del(`/auth/sessions/${id}`, { headers: authScopeHeaders() }),
  changePhone: (payload) => api.post('/identity/phone/change', payload),
  requestDeletion: () => api.post('/identity/account/deletion'),
  deletionStatus: () => api.get('/identity/account/deletion'),
  cancelDeletion: (payload) => api.post('/identity/account/deletion/cancel', payload),
  reauthenticate: (payload) => api.post('/auth/reauth', payload),
  sendReauthCode: (phone) => authApi.challengeSms(phone, 'admin_reauth'),
}

export const adminIdentityApi = {
  applications: (query = {}) => api.get('/admin/identity/applications', query),
  approve: (id, note = '') => api.post(`/admin/identity/applications/${id}/approve`, { note }),
  reject: (id, note = '') => api.post(`/admin/identity/applications/${id}/reject`, { note }),
  requestMoreInfo: (id, note = '') => api.post(`/admin/identity/applications/${id}/request-more-info`, { note }),
}
