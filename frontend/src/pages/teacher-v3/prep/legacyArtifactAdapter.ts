/**
 * VNext spec → incumbent editor view models.
 *
 * The editors still speak their historical presentation model, but this
 * adapter is intentionally one-way: spec ids, revisions and lineage remain
 * attached to the caller and no legacy generator or local persistence is
 * involved. Missing optional fields stay empty rather than being invented.
 */
import type { V3Deck, V3LessonPlan, V3Slide } from '@/types/teacherV3'

type Dict = Record<string, unknown>

export interface LegacyArtifactIdentity {
  id: string
  revision: number | null
  lineage: Dict | null
}

export function readLegacyArtifactIdentity(spec: Dict): LegacyArtifactIdentity {
  const rawRevision = spec.revision
  return {
    id: text(spec.id) || text(spec.spec_id) || text(spec.deck_id),
    revision: typeof rawRevision === 'number' && Number.isFinite(rawRevision) ? rawRevision : null,
    lineage: spec.projection && typeof spec.projection === 'object' ? spec.projection as Dict : null,
  }
}

function text(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

function items(value: unknown): string[] {
  if (!Array.isArray(value)) return []
  return value.map((item) => item && typeof item === 'object' ? text((item as Dict).text) : text(item)).filter(Boolean)
}

export function adaptLessonPlanSpecToLegacyPlan(spec: Dict, classId?: string | null): V3LessonPlan {
  const metadata = (spec.metadata && typeof spec.metadata === 'object' ? spec.metadata : {}) as Dict
  const flow = Array.isArray(spec.lesson_flow) ? spec.lesson_flow : []
  const difficult = items(spec.key_difficult_points)
  return {
    id: text(spec.id) || text(spec.spec_id),
    topic: text(metadata.title) || text(spec.title) || '教案',
    class_id: classId || '',
    // These are display-only fields required by the incumbent shell. The VNext
    // overlay prevents them from ever becoming a persistence payload.
    lesson_type: text(metadata.lesson_scope) || '',
    template_id: '',
    objectives: items(spec.objectives),
    key_points: difficult,
    sections: flow.map((stage, index) => {
      const row = (stage && typeof stage === 'object' ? stage : {}) as Dict
      const stageItems = items(row.items)
      return {
        id: text(row.id) || `spec-stage-${index + 1}`,
        name: text(row.title) || `教学环节 ${index + 1}`,
        minutes: typeof row.duration_minutes === 'number' ? row.duration_minutes : 0,
        teacher_activity: stageItems.join('；'),
        student_activity: '',
        design_intent: stageItems.join('；'),
      }
    }),
    board_design_note: items(spec.board_design).join('；'),
    homework_tiers: [],
    refs: items(spec.source_refs),
  }
}

export function adaptTeachingDeckSpecToLegacyDeck(spec: Dict, className = ''): V3Deck {
  const metadata = (spec.metadata && typeof spec.metadata === 'object' ? spec.metadata : {}) as Dict
  const slides = Array.isArray(spec.slides) ? spec.slides : []
  const projected: V3Slide[] = slides.map((slide, index) => {
    const row = (slide && typeof slide === 'object' ? slide : {}) as Dict
    const title = text(row.title) || `第 ${index + 1} 页`
    const content = text(row.content)
    const elements = [
      // E-3R3：element.html 约定存**原文**——画布 renderRich 自带 escapeSegment+白名单消毒，
      // 先逃逸会二次转义（数学不等式 0<e<1 显示成 0&lt;e&lt;1）。
      { id: `vnext-title-${index + 1}`, type: 'text' as const, left: 72, top: 48, width: 1120, height: 64, z: 1, html: title, font_size: 30, bold: true },
      ...(content ? [{ id: `vnext-content-${index + 1}`, type: 'text' as const, left: 72, top: 140, width: 1120, height: 420, z: 1, html: content, font_size: 22 }] : []),
    ]
    return { id: text(row.id) || `spec-slide-${index + 1}`, layout: text(row.layout_hint || row.type) as V3Slide['layout'], elements, notes: text(row.speaker_note) }
  })
  return {
    id: text(spec.id) || text(spec.deck_id),
    title: text(metadata.title) || '课件',
    // Display-only incumbent fields; VNext never sends this projection back.
    template_id: '',
    source: 'topic',
    brief_context: { course_type: '', chapter: '', material: className },
    slides: projected,
    updated_at: '',
  }
}
