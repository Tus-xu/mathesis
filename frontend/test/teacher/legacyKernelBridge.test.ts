import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  mapLegacyPrepSnapshot,
  createLegacyTeachingSession,
  getLegacyTeachingSession,
  runLegacyTeachingAction,
  sendLegacyTeachingMessage,
  type LegacyTeachingSnapshot,
} from '@/pages/teacher-v3/prep/legacyKernelBridge'
import { __resetPrepChainForTest, applyKernelProjection, usePrepChain } from '@/pages/teacher-v3/prep/prepChain'

const snapshot: LegacyTeachingSnapshot = {
  session: {
    id: '11111111-1111-4111-8111-111111111111',
    session_id: '11111111-1111-4111-8111-111111111111',
    class_id: '22222222-2222-4222-8222-222222222222',
    class_name: '高二一班',
    title: '椭圆的标准方程',
    status: 'active',
    revision: 3,
    context_snapshot_id: '33333333-3333-4333-8333-333333333333',
    last_event_seq: 4,
  },
  timeline: [],
  artifact: {
    context: {
      source_title: '人教A版 · 必修一',
      section_title: '椭圆及其标准方程',
      lesson_scope: '第1课时',
      knowledge_points: ['椭圆定义'],
      objectives: ['理解椭圆定义'],
    },
    blueprint: {
      blueprint_id: '44444444-4444-4444-8444-444444444444',
      status: 'proposal',
      revision: 1,
      title: '椭圆的标准方程',
      buckets: {
        objectives: [{ text: '理解椭圆定义' }],
        key_points: [{ text: '焦点与长轴' }],
        difficult_points: [{ text: '方程推导' }],
        lesson_flow: [{ stage: 'introduction', title: '情境引入', duration_minutes: 5, items: [{ text: '观察轨迹' }] }],
      },
    },
    diagnostic: null,
    lesson_plan: {
      spec_id: '55555555-5555-4555-8555-555555555555',
      revision: 2,
      title: '椭圆教案',
    },
  },
  evidence: [],
  available_actions: [
    { action: 'confirm', target: '44444444-4444-4444-8444-444444444444', copy_key: 'prep.action.confirm_proposal', disabled: false },
  ],
  job_summary: [],
  basic_mode: false,
}
const SESSION_ID = '11111111-1111-4111-8111-111111111111'
const CLASS_ID = '22222222-2222-4222-8222-222222222222'

describe('legacy Teacher kernel bridge', () => {
  beforeEach(() => {
    __resetPrepChainForTest()
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ data: snapshot }), { status: 200 })))
  })

  it('projects a server snapshot into the legacy chain without demo class or fake progress', () => {
    applyKernelProjection(mapLegacyPrepSnapshot(snapshot))
    const chain = usePrepChain()
    expect(chain.sessionId).toBe(snapshot.session.session_id)
    expect(chain.sessionRevision).toBe(3)
    expect(chain.brief?.classId).toBe(snapshot.session.class_id)
    expect(chain.brief?.className).toBe('高二一班')
    expect(chain.stage).toBe('outline')
    expect(chain.kernelError).toBe('')
  })

  it('maps authoritative session/context/blueprint/spec ids into legacy view state without defaults', () => {
    const mapped = mapLegacyPrepSnapshot(snapshot)
    expect(mapped.sessionId).toBe(snapshot.session.session_id)
    expect(mapped.revision).toBe(3)
    expect(mapped.brief).toMatchObject({ topic: '椭圆的标准方程', classId: snapshot.session.class_id, className: '高二一班' })
    expect(mapped.brief).not.toMatchObject({ classId: 'c2-05', className: '高二(5)班' })
    expect(mapped.outline?.sections[0]).toMatchObject({ name: '情境引入', minutes: 5 })
    expect(mapped.lessonPlanSpec?.spec_id).toBe('55555555-5555-4555-8555-555555555555')
    expect(mapped.availableActions[0]).toMatchObject({ action: 'confirm', disabled: false })
  })

  it('E-3: a missing class identity degrades gracefully; only a missing brief fails closed', () => {
    const mapped = mapLegacyPrepSnapshot({ ...snapshot, session: { ...snapshot.session, class_id: null, class_name: null }, artifact: { ...snapshot.artifact, blueprint: null } })
    expect(mapped.brief?.classId).toBeUndefined()
    expect(mapped.brief?.className).toBeUndefined()
    expect(mapped.blockedReason).toBeNull()
    const noBrief = mapLegacyPrepSnapshot({ ...snapshot, session: { ...snapshot.session, title: null }, artifact: { ...snapshot.artifact, context: null } })
    expect(noBrief.blockedReason).toContain('服务端')
  })

  it('uses the authenticated 8100 prep contract and preserves expected_revision', async () => {
    await createLegacyTeachingSession({ classId: CLASS_ID, message: '请准备椭圆第一课时' })
    await sendLegacyTeachingMessage(SESSION_ID, '增加一个探究环节', 3)
    const blueprintId = String(snapshot.artifact.blueprint!.blueprint_id)
    await runLegacyTeachingAction(SESSION_ID, 'confirm', blueprintId, 3)
    await getLegacyTeachingSession(SESSION_ID)

    const calls = vi.mocked(fetch).mock.calls
    expect(calls.map(([url]) => String(url))).toEqual([
      '/api/teacher-v3/prep/conversations',
      `/api/teacher-v3/prep/conversations/${SESSION_ID}/messages`,
      `/api/teacher-v3/prep/conversations/${SESSION_ID}/actions`,
      `/api/teacher-v3/prep/conversations/${SESSION_ID}`,
    ])
    expect(JSON.parse(String(calls[0][1]?.body))).toMatchObject({ class_id: snapshot.session.class_id, message: '请准备椭圆第一课时' })
    expect(JSON.parse(String(calls[1][1]?.body))).toMatchObject({ message: '增加一个探究环节', expected_revision: 3 })
    expect(JSON.parse(String(calls[2][1]?.body))).toMatchObject({ action: 'confirm', target: blueprintId, expected_revision: 3 })
  })
})
