// 本文件由 python -m app.teacher_prep_v2.contracts.gen.gen_ts 生成——禁止手改；
// 权威源 = services/teacher-platform/app/teacher_prep_v2/contracts/models.py（Pydantic）。
// 生成产物，用于前端类型层与 fixture 契约测试；漂移由后端 contract test 拦截。

export const PREP_CONTRACTS_SCHEMA_VERSION = "1.0.0"
export type PrepContractsCatalog = PreClassContractsBundle

export interface AnswerItem {
  item_no: number
  answer_text?: string | null
  source_locator?: SourceLocatorRef | null
  confidence?: number | null
}

export interface AssignmentProvision {
  source?: "8000_ASSIGNMENT"
  external_id?: string | null
  mapping_version?: number
  synced_at?: string | null
}

export interface AssignmentRef {
  task_assignment_id: string
}

export interface AssignmentTarget {
  path?: "WHOLE_CLASS" | "SPECIFIC_STUDENTS"
  student_ids?: string[]
}

export interface AuthorityRef {
  service?: "api"
  external_id: string
}

export interface ClassLearningProfile {
  schema_version?: "1.0.0"
  kind?: "CLASS_LEARNING_PROFILE"
  id: string
  tenant_id: string
  class_id: string
  session_id?: string | null
  owner_id: string
  scope?: ProfileScope
  status?: "collecting" | "preview" | "published" | "archived"
  aggregation?: ProfileAggregation
  computed_from?: ComputationInfo | null
  revision?: number
  provenance?: Provenance | null
  data_retention?: DataRetention | null
  unknown_flags?: string[]
}

export interface ClassroomTask {
  schema_version?: "1.0.0"
  kind?: "CLASSROOM"
  id: string
  tenant_id: string
  class_id: string
  session_id: string
  owner_id: string
  title: string
  description?: string | null
  class_mode?: "interactive" | "presentation" | "practice"
  duration_minutes?: number | null
  window: WindowRef
  attempts_allowed?: number
  resubmit_allowed?: boolean
  items?: DiagnosticItem[]
  status?: "draft" | "published" | "closed" | "cancelled" | "archived"
  revision?: number
  provenance?: Provenance | null
  data_retention?: DataRetention | null
  unknown_flags?: string[]
}

export interface ComputationInfo {
  run_id: string
  provider_version: string
  input_refs?: string[]
  computed_at: string
  note?: string | null
}

export interface ConfidenceDistribution {
  low?: number
  mid?: number
  high?: number
  unknown?: number
}

export interface DataRetention {
  policy: "TERM" | "TERM_1YEAR" | "ACADEMIC_CYCLE" | "ANONYMIZED"
  ttl_days?: number | null
  owner_delete_allowed?: boolean
  justification?: string | null
}

export interface DiagnosticItem {
  item_no: number
  problem_locator?: SourceLocatorRef | null
  prompt?: string | null
  max_score?: number | null
}

export interface Evidence {
  schema_version?: "1.0.0"
  kind?: "EVIDENCE"
  id: string
  tenant_id: string
  class_id: string
  session_id?: string | null
  student_id: string
  submission_ref: SubmissionRef
  ev_kind: "PHOTO" | "OCR" | "ATTACHMENT" | "AUDIO" | "MATH_RENDER"
  mime_type: string
  size_bytes?: number
  sha256?: string | null
  source_locator?: SourceLocatorRef | null
  object_ref?: ObjectRef | null
  captured_at?: string | null
  device?: string | null
  revision?: number
  provenance?: Provenance | null
  data_retention?: DataRetention | null
  unknown_flags?: string[]
}

export interface KpMastery {
  kp_code: string
  mastery?: number | null
  sample_size?: number
  confidence?: number | null
}

export interface LatePolicy {
  allow_late?: boolean
  grace_minutes?: number | null
}

export interface Misconception {
  kp_code: string
  label: string
  count?: number
}

export interface ObjectRef {
  service?: "minio"
  bucket: string
  key: string
}

export interface Participation {
  submitted?: number
  not_submitted?: number
}

export interface PreClassDiagnosticTask {
  schema_version?: "1.0.0"
  kind?: "PRE_CLASS_DIAGNOSTIC"
  id: string
  tenant_id: string
  class_id: string
  session_id?: string | null
  owner_id: string
  title: string
  description?: string | null
  window: WindowRef
  attempts_allowed?: number
  resubmit_allowed?: boolean
  items?: DiagnosticItem[]
  status?: "draft" | "published" | "closed" | "cancelled" | "archived"
  revision?: number
  provenance?: Provenance | null
  data_retention?: DataRetention | null
  unknown_flags?: string[]
}

export interface ProfileAggregation {
  submission_count?: number
  active_student_count?: number
  confidence_distribution?: ConfidenceDistribution
  mastery_by_kp?: KpMastery[]
  top_misconceptions?: Misconception[]
  participation?: Participation
}

export interface ProfileScope {
  kind?: "SESSION" | "COURSE" | "PERIOD"
  start?: string | null
  end?: string | null
}

export interface Provenance {
  producer_service: string
  producer_version: string
  actor_id: string
  idempotency_key: string
  created_at: string
  source?: "teacher" | "student" | "system" | "ai_suggestion" | "migration"
  note?: string | null
}

export interface SourceLocatorRef {
  schema_version?: string
  ref: string
  preview?: string | null
}

export interface StudentSubmission {
  schema_version?: "1.0.0"
  kind?: "STUDENT_SUBMISSION"
  id: string
  authority?: AuthorityRef | null
  tenant_id: string
  class_id: string
  session_id?: string | null
  student_id: string
  task_ref: TaskRef
  assignment_ref: AssignmentRef
  attempt_number?: number | null
  client_submit_id: string
  submitted_at: string
  due_at?: string | null
  late_seconds?: number | null
  status?: "drafted" | "submitted" | "late" | "returned" | "resubmitted" | "grading" | "graded" | "archived"
  answers?: AnswerItem[]
  grading?: SubmissionGrading | null
  evidence_refs?: string[]
  revision?: number
  provenance?: Provenance | null
  data_retention?: DataRetention | null
  unknown_flags?: string[]
}

export interface SubmissionGrading {
  verdict?: "correct" | "wrong" | "pending_review" | "unknown"
  score?: number | null
  ai_pregraded?: boolean
  suggested_score?: number | null
  teacher_final_score?: number | null
  needs_review?: boolean
  grader_provenance?: Provenance | null
}

export interface SubmissionRef {
  external_submission_id?: string | null
  contract_submission_id?: string | null
}

export interface TaskAssignment {
  schema_version?: "1.0.0"
  kind?: "TASK_ASSIGNMENT"
  id: string
  tenant_id: string
  class_id: string
  session_id?: string | null
  owner_id: string
  task_ref: TaskRef
  target?: AssignmentTarget
  due_at: string
  late_policy?: LatePolicy
  attempts_allowed?: number
  provision?: AssignmentProvision | null
  status?: "pending" | "provisioned" | "active" | "done" | "closed" | "cancelled" | "archived"
  idempotency_key: string
  revision?: number
  provenance?: Provenance | null
  data_retention?: DataRetention | null
  unknown_flags?: string[]
}

export interface TaskRef {
  task_id: string
  task_kind: "PRE_CLASS_DIAGNOSTIC" | "CLASSROOM"
}

export interface WindowRef {
  open_at: string
  due_at: string
}

export interface PreClassContractsBundle {
  schema_version?: string
  pre_class_diagnostic_task?: PreClassDiagnosticTask | null
  classroom_task?: ClassroomTask | null
  task_assignment?: TaskAssignment | null
  student_submission?: StudentSubmission | null
  evidence?: Evidence | null
  class_learning_profile?: ClassLearningProfile | null
}
