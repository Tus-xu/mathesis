import assert from 'node:assert/strict'
import test from 'node:test'

import { FastApiTeacherPlatform } from './fastapi-platform.js'

test('maps a teacher quiz draft tool to the existing strict quiz API', async () => {
  const requests = []
  const platform = new FastApiTeacherPlatform({
    baseUrl: 'http://api.example',
    bearerToken: 'teacher-token',
    fetchImpl: async (url, init) => {
      requests.push({ url, init })
      return new Response(JSON.stringify({ code: 0, data: { artifact_id: 'draft-1', insufficient: true } }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      })
    },
  })

  const result = await platform.invoke('teacher.quiz.create_draft', {
    class_id: 'class-1',
    knowledge_points: ['函数单调性'],
    count: 8,
    question_types: { choice: 4, blank: 2, text: 2 },
    difficulty: { easy: 0.5, medium: 0.4, hard: 0.1 },
  })

  assert.deepEqual(result, { artifact_id: 'draft-1', insufficient: true })
  assert.equal(requests[0].url, 'http://api.example/api/teacher/quizzes/generate')
  assert.equal(requests[0].init.headers.authorization, 'Bearer teacher-token')
  assert.deepEqual(JSON.parse(requests[0].init.body), {
    class_id: 'class-1',
    knowledge_points: ['函数单调性'],
    count: 8,
    question_types: { choice: 4, blank: 2, text: 2 },
    difficulty: { easy: 0.5, medium: 0.4, hard: 0.1 },
    exclude_hashes: [],
  })
})
