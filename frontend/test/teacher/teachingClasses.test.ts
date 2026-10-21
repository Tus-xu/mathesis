import { describe, it, expect } from 'vitest'
import { isTeachingClass, teachingClasses } from '@/utils/teachingClasses'

const base = {
  name: '高二2班', grade: '高二', inviteCode: null, createdAt: null, students: 2,
}

describe('teachingClasses · 教师端教学班口径', () => {
  it('保留 teacher / owner / assistant 且 confirmed', () => {
    const rows = [
      { ...base, id: 'c1', myRole: 'teacher', confirmed: true },
      { ...base, id: 'c2', myRole: 'owner', confirmed: true },
      { ...base, id: 'c3', myRole: 'assistant', confirmed: true },
    ]
    expect(teachingClasses(rows).map((c) => c.id)).toEqual(['c1', 'c2', 'c3'])
  })

  it('排除学生身份班与未确认成员（class_scope_denied 的根因）', () => {
    const rows = [
      { ...base, id: 'student-class', myRole: 'student', confirmed: true },
      { ...base, id: 'pending-teacher', myRole: 'teacher', confirmed: false },
      { ...base, id: 'ok', myRole: 'teacher', confirmed: true },
    ]
    expect(teachingClasses(rows).map((c) => c.id)).toEqual(['ok'])
    expect(isTeachingClass(rows[0])).toBe(false)
    expect(isTeachingClass(rows[1])).toBe(false)
    expect(isTeachingClass(rows[2])).toBe(true)
  })

  it('空目录/缺字段不崩溃', () => {
    expect(teachingClasses(null)).toEqual([])
    expect(isTeachingClass(null)).toBe(false)
  })
})
