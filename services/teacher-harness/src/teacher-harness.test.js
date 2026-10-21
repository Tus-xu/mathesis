import assert from 'node:assert/strict'
import test from 'node:test'

import { CallId } from '@deepseek-ai/dsh-llm'

import { createTeacherHarness } from './teacher-harness.js'

test('a scoped read tool calls the FastAPI domain adapter', async () => {
  const calls = []
  const harness = await createTeacherHarness({
    platform: {
      async invoke(name, arguments_) {
        calls.push({ name, arguments: arguments_ })
        return { class_id: arguments_.class_id, pending_grading: 3 }
      },
    },
  })

  const result = await harness.execute('teacher.today.read', { class_id: 'class-1' })

  assert.equal(result.isError, false)
  assert.deepEqual(result.value, { class_id: 'class-1', pending_grading: 3 })
  assert.deepEqual(calls, [{
    name: 'teacher.today.read',
    arguments: { class_id: 'class-1' },
  }])
})

test('student-visible publishing fails closed until a teacher approval channel exists', async () => {
  let invoked = false
  const harness = await createTeacherHarness({
    platform: {
      async invoke() {
        invoked = true
        return { status: 'published' }
      },
    },
  })

  const result = await harness.ctx.tools.execute({
    signal: new AbortController().signal,
    callId: CallId('publish-without-approval'),
    name: 'teacher.assignment.publish',
    arguments: { class_id: 'class-1', assignment_id: 'assignment-1' },
  })

  assert.equal(result.isError, true)
  assert.equal(invoked, false)
  assert.match(result.content[0].text, /教师确认/)
})
