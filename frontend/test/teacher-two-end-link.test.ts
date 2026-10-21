/**
 * tel PLAN §8.3 B5：教师端两端贯通测试（离线 jsdom，无真实网络/LLM）。
 * 1) api8000.ts 方法 → URL/载荷快照（8000 M3 契约锁定）；
 * 2) 发布四步编排状态机（runPublishFlow，mock 依赖注入）；
 * 3) pickClass 引导态计算（无班级 → ''，有班级 → 首个 confirmed 教学班）。
 * 契约事实源：services/api/app/domains/teacher/{router,schemas,assessment}.py（本会话读码）。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api8000, type ApiAssignment, type ApiClassItem, type ApiQuizSetArtifact } from '@/api/api8000'
import { pickDefaultClassId, runPublishFlow, type PublishFlowDeps, type PublishFlowInput, type PublishStage } from '@/pages/teacher-v3/publishFlow'

function mockFetch(routes: Record<string, unknown>) {
  const fn = vi.fn(async (url: unknown, init?: { method?: string; body?: string }) => {
    const method = (init?.method || 'GET').toUpperCase()
    const key = `${method} ${String(url)}`
    const payload = routes[key]
    if (payload === undefined) {
      throw new Error(`unexpected request: ${key} body=${init?.body ?? ''}`)
    }
    return new Response(JSON.stringify({ code: 0, message: 'ok', data: payload, request_id: 't' }), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    })
  })
  vi.stubGlobal('fetch', fn)
  return fn
}

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })

describe('api8000 client → 8000 M3 URL/载荷契约', () => {
  it('classes.list → GET /api/classes/mine（A1 students 计数在载荷里）', async () => {
    const fn = mockFetch({
      'GET /api/classes/mine': { items: [{ id: 'c1', name: '高二(3)班', grade: '高二', myRole: 'teacher', confirmed: true, inviteCode: 'AB12CD', createdAt: null, students: 46 }], total: 1 },
    })
    const r = await api8000.classes.list()
    expect(fn.mock.calls[0][0]).toBe('/api/classes/mine')
    expect(r.items[0].students).toBe(46)
    expect(r.items[0].inviteCode).toBe('AB12CD')
  })

  it('classes.setMemberConfirmed → PATCH /api/classes/{id}/members/{uid}（D3 确认入班）', async () => {
    const fn = mockFetch({ 'PATCH /api/classes/c1/members/u9': { confirmed: true } })
    await api8000.classes.setMemberConfirmed('c1', 'u9', true)
    const init = fn.mock.calls[0][1] as { method: string; body: string }
    expect(init.method).toBe('PATCH')
    expect(JSON.parse(init.body)).toEqual({ confirmed: true })
  })

  it('quizzes.generate → POST /api/teacher/quizzes/generate（GenerateQuizRequest 契约）', async () => {
    const fn = mockFetch({
      'POST /api/teacher/quizzes/generate': {
        artifact_id: 'a1', artifact_type: 'quiz_set', status: 'draft', content: { items: [] }, warnings: [], degraded: false,
      },
    })
    await api8000.quizzes.generate({
      class_id: 'c1',
      knowledge_points: ['MATH-PEP-3.1.1'],
      count: 6,
      question_types: { choice: 4, blank: 1, text: 1 },
      difficulty: { easy: 0, medium: 0, hard: 0 },
    })
    const [, init] = fn.mock.calls[0] as [string, { method: string; body: string }]
    expect(init.method).toBe('POST')
    const body = JSON.parse(init.body)
    expect(Object.keys(body).sort()).toEqual(['class_id', 'count', 'difficulty', 'knowledge_points', 'question_types'])
    expect(body.question_types).toEqual({ choice: 4, blank: 1, text: 1 })
  })

  it('assignments.create 载荷收敛为 8000 契约（class_id/title/artifact_id/client_assignment_id/deadline/type，无 8100 平行域字段）', async () => {
    const fn = mockFetch({
      'POST /api/teacher/assignments': {
        assignment_id: 'as1', client_assignment_id: 'assign-x', class_id: 'c1', title: '学前诊断', type: 'pre_class', status: 'draft', deadline: null, created_at: null, replayed: false,
      },
    })
    await api8000.assignments.create({
      class_id: 'c1', title: '学前诊断', artifact_id: 'a1', client_assignment_id: 'assign-x', type: 'pre_class',
    })
    const [, init] = fn.mock.calls[0] as [string, { method: string; body: string }]
    const body = JSON.parse(init.body)
    expect(Object.keys(body).sort()).toEqual(['artifact_id', 'class_id', 'client_assignment_id', 'title', 'type'])
    expect(body.type).toBe('pre_class')
  })

  it('artifacts.confirm / assignments.publish 携带 client_request_id + Idempotency-Key', async () => {
    const fn = mockFetch({
      'POST /api/teacher/artifacts/a1/confirm': { artifact_id: 'a1', status: 'confirmed' },
      'POST /api/teacher/assignments/as1/publish': { assignment_id: 'as1', status: 'published' },
    })
    await api8000.artifacts.confirm('a1', 'confirm-k1', 'confirm:k1')
    await api8000.assignments.publish('as1', 'publish-k1', 'publish:k1')
    const confirmInit = fn.mock.calls[0][1] as { headers: Record<string, string>; body: string }
    const publishInit = fn.mock.calls[1][1] as { headers: Record<string, string>; body: string }
    expect(JSON.parse(confirmInit.body)).toEqual({ client_request_id: 'confirm-k1' })
    expect(confirmInit.headers['Idempotency-Key']).toBe('confirm:k1')
    expect(fn.mock.calls[1][0]).toBe('/api/teacher/assignments/as1/publish')
    expect(JSON.parse(publishInit.body)).toEqual({ client_request_id: 'publish-k1' })
  })

  it('grading.confirm → POST /api/teacher/grading/{item}/confirm（唯一正式分入口；decision=override 时带终分）', async () => {
    const fn = mockFetch({
      'POST /api/teacher/grading/i1/confirm': { suggestion_id: 's1', decision: 'override', version: 1 },
    })
    await api8000.grading.confirm('i1', { suggestion_id: 's1', decision: 'override', final_score: 7, teacher_feedback: '设参思路对', version: 1 })
    const [, init] = fn.mock.calls[0] as [string, { method: string; body: string }]
    expect(fn.mock.calls[0][0]).toBe('/api/teacher/grading/i1/confirm')
    expect(JSON.parse(init.body)).toEqual({ suggestion_id: 's1', decision: 'override', final_score: 7, teacher_feedback: '设参思路对', version: 1 })
  })

  it('insights.roster / learningOverview → A4/A5 端点', async () => {
    const fn = mockFetch({
      'GET /api/teacher/classes/c1/roster': { class_id: 'c1', students: [] },
      'GET /api/teacher/classes/c1/learning-overview?window_days=14': {
        class_id: 'c1', window_days: 14, submission_trend: [], kp_heat: [], pending_review_count: 0, avg_mastery: null, error_clusters: [],
      },
    })
    await api8000.insights.roster('c1')
    await api8000.insights.learningOverview('c1', 14)
    expect(fn.mock.calls[0][0]).toBe('/api/teacher/classes/c1/roster')
    expect(fn.mock.calls[1][0]).toBe('/api/teacher/classes/c1/learning-overview?window_days=14')
  })
})

describe('发布四步编排状态机（runPublishFlow）', () => {
  const baseInput: PublishFlowInput = {
    classId: 'c1',
    title: '学前诊断',
    type: 'pre_class',
    knowledgePoints: ['MATH-PEP-3.1.1'],
    count: 2,
    questionTypes: { choice: 2, blank: 0, text: 0 },
    difficulty: { easy: 0, medium: 0, hard: 0 },
    clientAssignmentId: 'assign-t0',
  }

  function makeDeps(overrides: Partial<PublishFlowDeps> = {}, onStage?: (s: PublishStage) => void) {
    const artifact: ApiQuizSetArtifact = {
      artifact_id: 'a1', artifact_type: 'quiz_set', scene: 'teacher.assessment', class_id: 'c1', owner_id: 't1',
      status: 'draft', version: 1,
      content: { items: [{ item_no: 1, q_type: 'choice', question_text: '1+1=?', options: null, answer: '2', analysis: null, kp_code: 'K1', difficulty: 'easy' }] },
      source_refs: [], validation: {}, warnings: [], degraded: false, engine: 'local',
      confirmed_by: null, confirmed_at: null, created_at: null, updated_at: null,
    }
    const confirmed = { ...artifact, status: 'confirmed' as const }
    const created: ApiAssignment = {
      assignment_id: 'as1', client_assignment_id: 'assign-t0', class_id: 'c1', title: '学前诊断',
      type: 'pre_class', status: 'draft', deadline: null, created_at: null,
    }
    const published = { ...created, status: 'published' as const }
    const deps: PublishFlowDeps = {
      generate: vi.fn(async () => artifact),
      confirm: vi.fn(async () => confirmed),
      create: vi.fn(async () => created),
      publish: vi.fn(async () => published),
      ...(onStage ? { onStage: (s: PublishStage) => onStage(s) } : {}),
      ...overrides,
    }
    return { deps, artifact, confirmed, created, published }
  }

  it('happy path：idle→generating→generated→confirming→confirmed→creating→publishing→published', async () => {
    const stages: PublishStage[] = []
    const { deps } = makeDeps({}, (s) => stages.push(s))
    const result = await runPublishFlow(baseInput, deps)
    expect(stages).toEqual(['generating', 'generated', 'confirming', 'confirmed', 'creating', 'publishing', 'published'])
    expect(result.assignment.status).toBe('published')
    // create 载荷契约：client_assignment_id 稳定键 + type 透传
    expect(deps.create).toHaveBeenCalledWith(expect.objectContaining({
      class_id: 'c1', title: '学前诊断', artifact_id: 'a1', client_assignment_id: 'assign-t0', type: 'pre_class',
    }))
  })

  it('题集确认未生效（status != confirmed）→ failed，不建作业不发布', async () => {
    const stages: PublishStage[] = []
    const { deps, artifact } = makeDeps({ confirm: vi.fn(async () => ({ ...artifact, status: 'draft' as const })) }, (s) => stages.push(s))
    await expect(runPublishFlow(baseInput, deps)).rejects.toThrow(/确认未生效/)
    expect(stages[stages.length - 1]).toBe('failed')
    expect(deps.create).not.toHaveBeenCalled()
    expect(deps.publish).not.toHaveBeenCalled()
  })

  it('publish 返回非 published → failed（不假成功）', async () => {
    const { deps, created } = makeDeps({ publish: vi.fn(async () => ({ ...created, status: 'draft' as const })) })
    await expect(runPublishFlow(baseInput, deps)).rejects.toThrow(/发布未生效/)
    expect(deps.publish).toHaveBeenCalledTimes(1)
  })

  it('generate 抛错 → failed 且 confirm/create/publish 均不触发', async () => {
    const { deps } = makeDeps({ generate: vi.fn(async () => { throw new Error('题库不足') }) })
    await expect(runPublishFlow(baseInput, deps)).rejects.toThrow('题库不足')
    expect(deps.confirm).not.toHaveBeenCalled()
    expect(deps.create).not.toHaveBeenCalled()
  })
})

describe('pickClass 引导态计算（tel §8.2 ClassroomView① / AssignView）', () => {
  const mk = (over: Partial<ApiClassItem>): ApiClassItem => ({
    id: 'x', name: '班', grade: '', myRole: 'teacher', confirmed: true, inviteCode: null, createdAt: null, students: 0, ...over,
  })

  it('空班级 → 空串（页面渲染引导态，绝无 mock 默认班）', () => {
    expect(pickDefaultClassId([])).toBe('')
  })

  it('有班级 → 取第一个 confirmed 教学角色的真实 id', () => {
    const items = [
      mk({ id: 'c-pending', confirmed: false }),
      mk({ id: 'c-student', myRole: 'student' }),
      mk({ id: 'c-real' }),
    ]
    expect(pickDefaultClassId(items)).toBe('c-real')
  })

  it('全是不合格班级 → 空串', () => {
    expect(pickDefaultClassId([mk({ confirmed: false }), mk({ myRole: 'student' })])).toBe('')
  })
})
