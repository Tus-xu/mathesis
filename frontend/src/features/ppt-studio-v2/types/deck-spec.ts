// 本文件由 python -m app.ppt_v2.domain.gen.gen_ts 生成——禁止手改；
// 权威源 = services/teacher-platform/app/ppt_v2/domain/spec.py（Pydantic）。
// 生成产物，用于前端类型层与 fixture 契约测试；漂移由后端 contract test 拦截。

export const DECK_SCHEMA_VERSION = "2.0.0"
export type DeckRoot = TeachingDeckSpec

export interface AssetRef {
  id: string
  kind: "TEXTBOOK" | "TEACHER_UPLOAD" | "MATH_RENDER" | "AI_IMAGE"
  object_key: string
  sha256: string
  mime_type: string
  size_bytes: number
  width?: number | null
  height?: number | null
  source_ref?: string | null
  rights_status?: "QUARANTINED" | "STAGED" | "ACTIVE"
  rights_evidence_ref?: string | null
  preserve_original?: boolean
  created_by?: string | null
  created_at?: string | null
  transform_history?: AssetTransform[]
}

export interface AssetTransform {
  op: "crop" | "resize" | "compress" | "watermark" | "bg_removal"
  version?: string
  params?: Record<string, unknown>
}

export interface ChangeLogEntry {
  revision: number
  ts: string
  kind: string
  summary: string
  patch_ids?: string[]
  affected_slide_ids?: string[]
}

export interface DeckMetadata {
  title: string
  grade?: string | null
  duration_minutes?: number | null
  language?: string
  course_context_ref?: string | null
}

export interface MathObjectSpec {
  kind: "ellipse"
  params?: Record<string, number>
  toggles?: MathObjectToggles
  style?: MathObjectStyle
}

export interface MathObjectStyle {
  color?: string
  stroke_px?: number
  dash?: "solid" | "dashed" | "dotted"
}

export interface MathObjectToggles {
  show_foci?: boolean
  show_axes?: boolean
  show_aux_lines?: boolean
  show_value_labels?: boolean
}

export interface OutlineEntry {
  id: string
  goal: string
  slide_ids?: string[]
}

export interface SelectedExample {
  problem_id: string
  usage?: "guided" | "demo" | "practice"
}

export interface SlideElement {
  role: "definition" | "formula" | "knowledge_card" | "figure" | "bullets" | "note" | "steps" | "table"
  text?: string | null
  latex?: string | null
  title?: string | null
  items?: string[]
  figure_ref?: "math_object" | null
}

export interface SlideSpec {
  id: string
  type: "title" | "introduction" | "concept" | "definition" | "derivation" | "worked-example" | "comparison" | "image-focus" | "practice" | "inquiry" | "summary"
  teaching_goal: string
  title: string
  content?: string
  elements?: SlideElement[]
  source_refs?: string[]
  assets?: string[]
  visual_intent?: string
  layout_hint?: "title" | "introduction" | "concept" | "definition" | "derivation" | "worked-example" | "comparison" | "image-focus" | "practice" | "inquiry" | "summary" | "overview" | null
  speaker_note?: string | null
  revision?: number
  status?: "draft" | "confirmed" | "generated" | "failed"
  qa_status?: "pending" | "passed" | "failed" | "unknown"
  math_object?: MathObjectSpec | null
}

export interface SpecProjectionRef {
  schema_version?: "1.0.0"
  projector: string
  approved_blueprint_id: string
  approved_blueprint_revision: number
  context_snapshot_hash: string
  prompt_release_id?: string | null
  ai_run_id?: string | null
  model?: string | null
  run_hash: string
  body_hash: string
  projected_at: string
}

export interface TeachingStrategy {
  intro?: string
  concept_progression?: string[]
  derivation_chain?: string[]
  checks_for_understanding?: string[]
}

export interface TextbookSourceRef {
  textbook_id: string
  section_id: string
  page_refs?: string[]
  chunk_refs?: string[]
  locator?: string | null
}

export interface ThemeRef {
  theme_id: string
  version: string
}

export interface TeachingDeckSpec {
  schema_version?: "2.0.0"
  id: string
  revision?: number
  metadata: DeckMetadata
  textbook_sources: TextbookSourceRef[]
  teaching_strategy?: TeachingStrategy
  outline?: OutlineEntry[]
  selected_examples?: SelectedExample[]
  theme?: ThemeRef | null
  slides?: SlideSpec[]
  assets?: AssetRef[]
  speaker_notes?: Record<string, string>
  change_log?: ChangeLogEntry[]
  projection?: SpecProjectionRef | null
}
