const API = {
  'teacher.today.read': ({ baseUrl }) => ({ url: `${baseUrl}/api/teacher/today`, init: { method: 'GET' } }),
  'teacher.quiz.create_draft': ({ baseUrl, arguments_ }) => ({
    url: `${baseUrl}/api/teacher/quizzes/generate`,
    init: {
      method: 'POST',
      body: JSON.stringify({
        class_id: arguments_.class_id,
        knowledge_points: arguments_.knowledge_points,
        count: arguments_.count,
        question_types: arguments_.question_types,
        difficulty: arguments_.difficulty,
        exclude_hashes: [],
      }),
    },
  }),
  'teacher.assignment.publish': ({ baseUrl, arguments_ }) => ({
    url: `${baseUrl}/api/teacher/assignments/${arguments_.assignment_id}/publish`,
    init: {
      method: 'POST',
      body: JSON.stringify({
        client_request_id: arguments_.client_request_id,
        idempotency_key: arguments_.idempotency_key,
      }),
    },
  }),
}

/** A transport-only adapter. It never owns teacher identity, scope or persistence. */
export class FastApiTeacherPlatform {
  constructor({ baseUrl, bearerToken, fetchImpl = fetch }) {
    this.baseUrl = baseUrl.replace(/\/$/, '')
    this.bearerToken = bearerToken
    this.fetchImpl = fetchImpl
  }

  async invoke(name, arguments_) {
    const build = API[name]
    if (!build) throw new Error(`unmapped teacher tool: ${name}`)
    const request = build({ baseUrl: this.baseUrl, arguments_ })
    const response = await this.fetchImpl(request.url, {
      ...request.init,
      headers: {
        authorization: `Bearer ${this.bearerToken}`,
        ...(request.init.body ? { 'content-type': 'application/json' } : {}),
      },
    })
    const envelope = await response.json()
    if (!response.ok || envelope.code !== 0) {
      throw new Error(envelope.message || 'teacher API request failed')
    }
    return envelope.data
  }
}
