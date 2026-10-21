// A0 M1-3 · Classroom 双端投影 reducer 单测（G7：状态服务端权威，前端只做 projection）
// 覆盖：snapshot 初始化／事件投影（名单/活动/统计整体替换）／seq 幂等与乱序回放／未知事件容忍／重连 snapshot 重置
import { describe, it, expect } from 'vitest'
import {
  applyStudentEvent, applyTeacherEvent, studentStateFromSnapshot, teacherStateFromSnapshot,
  type StudentClassroomState, type TeacherClassroomState,
} from '@/pages/teacher-v3/classroomReducer'
import type { V3ClassroomSnapshot } from '@/types/teacherV3'

type SnapshotOverrides = {
  session?: Record<string, any>
  questions?: V3ClassroomSnapshot['questions']
  seq?: number
  summary?: V3ClassroomSnapshot['summary']
}

function snapshot(over: SnapshotOverrides = {}): V3ClassroomSnapshot {
  return {
    session: {
      session_id: 'cs-1', class_id: 'c2-03', class_name: '高二(3)班', join_code: '538201',
      status: 'open', participants: [], activities: [],
      ...(over.session || {}),
    },
    questions: over.questions ?? [],
    seq: over.seq ?? 0,
    summary: over.summary ?? null,
  }
}

function durable(seq: number, event_type: string, payload: Record<string, any>) {
  return { event: 'event', data: { seq, event_id: `ev-${seq}`, event_type, payload, created_at: '2026-09-07T08:00:00Z' } }
}

const ACT1 = { activity_id: 'act-1', kind: 'question' as const, ord: 1, status: 'collecting' as const, stats: { answered: 3 } }
const ACT1_SERVER = { ...ACT1, stats: { answered: 5, distribution: { A: 2, B: 3 } } } // 服务端权威聚合

function teacherState(): TeacherClassroomState {
  return teacherStateFromSnapshot(snapshot({
    session: { participants: [{ participant_id: 'p1', student_name: '王小明', joined_at: 'T0' }] },
    questions: [{ question_id: 'q1', stem_latex: '椭圆定义', options: ['A', 'B'] }],
    seq: 0,
  }))
}

describe('teacher reducer · snapshot 初始化与事件投影', () => {
  it('snapshot 初始化：session/questions/seq/summary', () => {
    const s = teacherState()
    expect(s.session.session_id).toBe('cs-1')
    expect(s.questions.q1.stem_latex).toBe('椭圆定义')
    expect(s.summary).toBeNull()
  })

  it('participant_joined：追加名单；重复加入幂等', () => {
    let s = teacherState()
    s = applyTeacherEvent(s, durable(1, 'participant_joined', { participant: { participant_id: 'p2', student_name: '李小红', joined_at: 'T1' } }))
    expect(s.session.participants.map((p) => p.participant_id)).toEqual(['p1', 'p2'])
    const before = s
    s = applyTeacherEvent(s, durable(1, 'participant_joined', { participant: { participant_id: 'p2', student_name: '李小红', joined_at: 'T1' } }))
    expect(s).toBe(before) // 同 seq 重复投递：引用不变（幂等跳过）
  })

  it('response_submitted：统计整体替换为服务端聚合（绝不本地 ++）', () => {
    let s = teacherState()
    s = applyTeacherEvent(s, durable(2, 'activity_pushed', { activity: ACT1 }))
    expect(s.session.activities[0].stats!.answered).toBe(3)
    // 本地若自持权威会算 4；服务端权威投影必须是 5
    s = applyTeacherEvent(s, durable(3, 'response_submitted', { activity: ACT1_SERVER }))
    expect(s.session.activities[0].stats!.answered).toBe(5)
    expect(s.session.activities[0].stats!.distribution).toEqual({ A: 2, B: 3 })
  })

  it('activity_revealed：活动替换 + 题面答案更新', () => {
    let s = teacherState()
    s = applyTeacherEvent(s, durable(4, 'activity_revealed', {
      activity: { ...ACT1, status: 'revealed' },
      question: { question_id: 'q1', stem_latex: '椭圆定义', options: ['A', 'B'], answer: 'B' },
    }))
    expect(s.session.activities[0].status).toBe('revealed')
    expect(s.questions.q1.answer).toBe('B')
  })

  it('session_ended / summary_ready / 未知事件容忍', () => {
    let s = teacherState()
    s = applyTeacherEvent(s, durable(5, 'session_ended', { ended_at: 'T5' }))
    expect(s.session.status).toBe('ended')
    s = applyTeacherEvent(s, durable(6, 'summary_ready', { summary: { stats: { questions: 3 }, insight: '变式有效' } }))
    expect(s.summary!.insight).toBe('变式有效')
    const before = s
    s = applyTeacherEvent(s, durable(7, 'future_unknown_event' as any, { x: 1 }))
    expect(s).toBe(before)
  })

  it('乱序回放（seq 回退）幂等跳过；presence live 投影更新在线数', () => {
    let s = teacherState()
    s = applyTeacherEvent(s, durable(9, 'participant_joined', { participant: { participant_id: 'p9', student_name: '晚加入', joined_at: 'T9' } }))
    const at9 = s
    s = applyTeacherEvent(s, durable(2, 'participant_joined', { participant: { participant_id: 'p2', student_name: '迟到回放', joined_at: 'T2' } }))
    expect(s).toBe(at9) // 旧 seq 重放不生效
    s = applyTeacherEvent(s, { event: 'presence', data: { online_count: 42 } })
    expect(s.session.online_count).toBe(42)
  })

  it('重连恢复：snapshot 事件整体重置（§12.4）', () => {
    let s = teacherState()
    s = applyTeacherEvent(s, durable(10, 'participant_joined', { participant: { participant_id: 'pX', student_name: 'X', joined_at: 'TX' } }))
    s = applyTeacherEvent(s, { event: 'snapshot', data: snapshot({ session: { activities: [ACT1_SERVER] }, seq: 11 }) })
    expect(s.session.participants).toHaveLength(0) // 本地残留清空
    expect(s.session.activities[0].stats!.answered).toBe(5)
    expect(s.seq).toBe(11)
  })
})

describe('student reducer · 学生视角投影', () => {
  function studentState(): StudentClassroomState {
    return studentStateFromSnapshot(snapshot({
      session: { activities: [{ ...ACT1, stats: { answered: 3, my_submitted: true } }, { activity_id: 'act-2', kind: 'poll', ord: 2, status: 'pushed' as const }] },
      seq: 0,
    }), 'p-me')
  }

  it('snapshot：my_submitted 重建「我已作答」集合（重连不丢本人作答）', () => {
    const s = studentState()
    expect(s.myAnsweredActivityIds).toEqual(['act-1'])
    expect(s.participantId).toBe('p-me')
  })

  it('response_submitted：本人提交入集合，他人提交不影响', () => {
    let s = studentState()
    s = applyStudentEvent(s, durable(1, 'response_submitted', { participant_id: 'p-other', activity_id: 'act-2', stats: { answered: 4 } }))
    expect(s.myAnsweredActivityIds).toEqual(['act-1'])
    s = applyStudentEvent(s, durable(2, 'response_submitted', { participant_id: 'p-me', activity_id: 'act-2' }))
    expect(s.myAnsweredActivityIds).toEqual(['act-1', 'act-2'])
  })

  it('revealed 前 question.answer 不存在即可（学生视角由服务端投影控制）', () => {
    const s = studentState()
    expect(s.questions.q1?.answer).toBeUndefined()
  })

  it('重连 snapshot 重置保留 participantId', () => {
    let s = studentState()
    s = applyStudentEvent(s, { event: 'snapshot', data: snapshot({ session: { activities: [ACT1_SERVER] }, seq: 3 }) })
    expect(s.participantId).toBe('p-me')
    expect(s.seq).toBe(3)
  })
})
