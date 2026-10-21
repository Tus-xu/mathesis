/**
 * Checkpoint 9 · legacy Teacher UI ↔ VNext teacher-prep kernel bridge.
 *
 * This is the only translation boundary owned by the legacy Prep Center. It
 * reuses the existing authenticated 8100 client and keeps server ids,
 * revisions, actions and specs as projections in memory. It deliberately has
 * no demo/default class, timer, or legacy-generation fallback.
 */
import {
  createConversation,
  getWorkspace,
  postAction,
  postMessage,
} from '@/features/teacher-prep-v2/api/prepApi'
import type {
  ActionView,
  ArtifactSnapshot,
  MessageView,
  WorkspaceSnapshot,
} from '@/features/teacher-prep-v2/types/workspace'
import { newIdempotencyKey } from '@/api/idempotency'
import type { PrepBrief, PrepRichOutline } from './prepChain'
import { adaptLessonPlanSpecToLegacyPlan } from './legacyArtifactAdapter'

export type LegacyTeachingSnapshot = WorkspaceSnapshot

export interface LegacyPrepProjection {
  sessionId: string | null
  revision: number | null
  brief: Partial<PrepBrief> | null
  outline: PrepRichOutline | null
  context: Record<string, unknown> | null
  blueprint: Record<string, unknown> | null
  lessonPlanSpec: Record<string, unknown> | null
  teachingDeckSpec: Record<string, unknown> | null
  availableActions: ActionView[]
  timeline: MessageView[]
  blockedReason: string | null
}

function stringValue(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim() ? value : undefined
}

function numberValue(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined
}

function listValue(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value)
    ? value.filter((item): item is Record<string, unknown> => Boolean(item && typeof item === 'object'))
    : []
}

function textItems(value: unknown): string[] {
  return listValue(value)
    .map((item) => stringValue(item.text))
    .filter((item): item is string => Boolean(item))
}

function blueprintOutline(blueprint: Record<string, unknown> | null): PrepRichOutline | null {
  if (!blueprint) return null
  const buckets = (blueprint.buckets && typeof blueprint.buckets === 'object'
    ? blueprint.buckets
    : blueprint) as Record<string, unknown>
  const flow = listValue(buckets.lesson_flow ?? blueprint.lesson_flow)
  if (!flow.length) return null

  const sections = flow.map((stage, index) => {
    const items = textItems(stage.items)
    const minutes = numberValue(stage.duration_minutes)
    return {
      id: stringValue(stage.id) || `server-stage-${index + 1}`,
      name: stringValue(stage.title) || `教学环节 ${index + 1}`,
      minutes: minutes || 0,
      goal: items.join('；'),
      example_suggestion: stringValue(stage.example_suggestion),
    }
  })
  const total = sections.reduce((sum, section) => sum + section.minutes, 0)
  return {
    topic: stringValue(blueprint.title) || '',
    duration: total || 0,
    sections,
    total_minutes: total || 0,
    notes: textItems(blueprint.warnings_ack),
    objectives: textItems(buckets.objectives),
    keypoints: {
      major: textItems(buckets.key_points),
      hard: textItems(buckets.difficult_points),
    },
  }
}

function specOutline(spec: Record<string, unknown> | null, classId?: string | null): PrepRichOutline | null {
  if (!spec) return null
  const plan = adaptLessonPlanSpecToLegacyPlan(spec, classId)
  if (!plan.sections.length) return null
  return {
    topic: plan.topic,
    duration: plan.sections.reduce((sum, section) => sum + section.minutes, 0),
    total_minutes: plan.sections.reduce((sum, section) => sum + section.minutes, 0),
    sections: plan.sections.map((section) => ({
      id: section.id,
      name: section.name,
      minutes: section.minutes,
      goal: section.design_intent || section.teacher_activity,
    })),
    notes: [],
    objectives: plan.objectives,
    keypoints: { major: plan.key_points, hard: [] },
  }
}

function makeBrief(snapshot: LegacyTeachingSnapshot, context: Record<string, unknown> | null): Partial<PrepBrief> | null {
  const session = snapshot.session as Record<string, unknown>
  const title = stringValue(session.title) || stringValue(context?.section_title)
  if (!title) return null
  const classId = stringValue(session.class_id)
  const className = stringValue(session.class_name)
  const source = stringValue(context?.source_title)
  const chapter = stringValue(context?.section_path) || stringValue(context?.section_title)
  const scope = stringValue(context?.lesson_scope)
  return {
    topic: title,
    classId,
    className,
    textbook: source,
    chapter,
    courseType: scope,
    requirements: [],
    source: 'textbook',
    ...(numberValue(context?.duration) ? { duration: numberValue(context?.duration) } : {}),
  }
}

export function mapLegacyPrepSnapshot(snapshot: LegacyTeachingSnapshot): LegacyPrepProjection {
  const artifact = snapshot.artifact as ArtifactSnapshot
  const context = artifact.context
  const blueprint = artifact.blueprint
  const session = snapshot.session as Record<string, unknown>
  const brief = makeBrief(snapshot, context)
  const classId = stringValue(session.class_id)
  // E-3 可选班级：未绑定班级不再是阻断，只是班级域能力降级（画像/发布诊断）
  const blockedReason = !brief
    ? '服务端尚未提供可识别的课题信息，当前备课只读。'
    : null
  return {
    sessionId: stringValue(session.session_id) || stringValue(session.id) || null,
    revision: numberValue(session.revision) ?? null,
    brief,
    outline: blueprintOutline(blueprint) ?? specOutline(artifact.lesson_plan ?? null, classId),
    context,
    blueprint,
    lessonPlanSpec: artifact.lesson_plan ?? null,
    teachingDeckSpec: artifact.teaching_deck ?? null,
    availableActions: snapshot.available_actions ?? [],
    timeline: snapshot.timeline ?? [],
    blockedReason,
  }
}

/** Create a real 8100 TeachingSession. E-3: classId optional (class-less sessions allowed). */
export function createLegacyTeachingSession(input: { classId?: string; message: string }, signal?: AbortSignal) {
  return createConversation({
    class_id: input.classId,
    message: input.message,
    idempotency_key: newIdempotencyKey(),
  }, signal)
}

export function getLegacyTeachingSession(sessionId: string, signal?: AbortSignal) {
  return getWorkspace(sessionId, signal)
}

export function sendLegacyTeachingMessage(sessionId: string, message: string, expectedRevision: number, signal?: AbortSignal) {
  return postMessage(sessionId, {
    message,
    expected_revision: expectedRevision,
    idempotency_key: newIdempotencyKey(),
  }, signal)
}

export function runLegacyTeachingAction(
  sessionId: string,
  action: string,
  target: string | null,
  expectedRevision: number,
  payload?: Record<string, unknown> | null,
  signal?: AbortSignal,
) {
  return postAction(sessionId, {
    action,
    target,
    expected_revision: expectedRevision,
    idempotency_key: newIdempotencyKey(),
    payload: payload ?? null,
  }, signal)
}
