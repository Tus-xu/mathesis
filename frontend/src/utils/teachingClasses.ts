/**
 * 教师端「教学班」口径（单一事实源）。
 *
 * 8000 `/api/classes/mine` 返回账号的全部成员关系，包含“全局是教师、但在某班是学生”的行。
 * 教师端课堂/作业/学情只能操作自己作为教师/助教/班主任的班；否则 8000 会以
 * class_scope_denied 拒绝，用户只会看到莫名其妙的失败。
 */
import type { ApiClassItem } from '@/api/api8000'

export const TEACHING_ROLES = ['teacher', 'owner', 'assistant'] as const

export type TeachingRole = (typeof TEACHING_ROLES)[number]

/** 是否可作为教师操作该班（必须已确认 + 教学角色；后端给 canTeach 时以后端为准）。 */
export function isTeachingClass(item: Pick<ApiClassItem, 'myRole' | 'confirmed' | 'canTeach'> | null | undefined): boolean {
  if (!item) return false
  if (typeof item.canTeach === 'boolean') return item.canTeach
  if (!item.confirmed) return false
  return (TEACHING_ROLES as readonly string[]).includes(String(item.myRole || ''))
}

/** 从班级目录中筛出教学班（保持原顺序）。 */
export function teachingClasses(items: ApiClassItem[] | null | undefined): ApiClassItem[] {
  return (items || []).filter(isTeachingClass)
}
