/* 体验测试留痕 API（trial_router） */
import { api } from '@/api/client'

export const trialApi = {
  submitFeedback: (body) => api.post('/api/trial/feedback', body),
  getMetrics: () => api.get('/api/trial/metrics'),
}
