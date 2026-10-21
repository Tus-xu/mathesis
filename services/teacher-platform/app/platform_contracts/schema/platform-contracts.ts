// 本文件由 python -m app.platform_contracts.gen.gen_ts 生成——禁止手改；
// 权威源 = services/teacher-platform/app/platform_contracts/*.py（Pydantic）。
// 生成产物，用于前端类型层与 fixture 契约测试；漂移由后端 contract test 拦截。

export const PLATFORM_CONTRACTS_SCHEMA_VERSION = "1.0.0"

export interface AIRunRecord {
  schema_version?: string
  run_id: string
  domain: string
  session_id?: string | null
  actor_id: string
  prompt_ref: PromptRefInline
  context_manifest_ref: string
  model: ModelRef
  tool_calls?: ToolCallRecord[]
  tokens: TokenUsage
  cost: Record<string, unknown>
  latency_ms: number
  schema_validation: SchemaValidation
  redaction: RedactionInfo
  status: "SUCCEEDED" | "PARTIAL" | "FAILED" | "CANCELLED"
  trace_id: string
  environment?: "PRODUCTION" | "STAGING" | "DEV" | "TEST"
  proposal_only?: boolean
  [key: string]: unknown
}

export interface AccessContext {
  schema_version?: string
  actor_id: string
  tenant_id: string
  session_id?: string | null
  workspace: "student" | "teacher" | "research" | "admin"
  domain: string
  capabilities?: string[]
  resource_scopes?: ResourceScope[]
  security_version: number
  policy_version: string
  trace_id: string
  expires_at?: string | null
  authority?: "api"
  [key: string]: unknown
}

export interface AccessibilitySpec {
  schema_version?: string
  role: string
  label_i18n_key?: string | null
  focusable?: boolean
  keyboard_operable?: boolean
  aria_live?: "off" | "polite" | "assertive" | null
  [key: string]: unknown
}

export interface AntiPatternRule {
  schema_version?: string
  rule_id: string
  description: string
  severity?: "BLOCKING" | "WARNING"
  detection?: "STATIC_SCAN" | "REVIEW" | "RUNTIME"
  [key: string]: unknown
}

export interface ArtifactACL {
  schema_version?: string
  visibility?: "PRIVATE" | "SESSION" | "PROJECT" | "CLASS" | "PUBLIC_INTERNAL"
  subject_ids?: string[]
  tenant_id?: string | null
  [key: string]: unknown
}

export interface ArtifactLineage {
  schema_version?: string
  created_by_run_id: string
  derived_from?: string[]
  inputs?: string[]
  [key: string]: unknown
}

export interface ArtifactRef {
  schema_version?: string
  artifact_id: string
  version: number
  hash: string
  media_type: string
  size_bytes: number
  storage_locator: StorageLocator
  owner_id: string
  acl: ArtifactACL
  lineage: ArtifactLineage
  created_by_run_id: string
  retention: ArtifactRetention
  created_at?: string | null
  content_schema_id?: string | null
  [key: string]: unknown
}

export interface ArtifactRetention {
  schema_version?: string
  policy: "TERM" | "TERM_1YEAR" | "ACADEMIC_CYCLE" | "ANONYMIZED" | "PERMANENT_UNTIL_DELETE"
  ttl_days?: number | null
  owner_delete_allowed?: boolean
  [key: string]: unknown
}

export interface BBox {
  schema_version?: string
  x: number
  y: number
  w: number
  h: number
  [key: string]: unknown
}

export interface CommandEnvelope {
  schema_version?: string
  client_request_id: string
  expected_revision: number
  actor_id: string
  scope: ResourceScope
  traceparent: string
  deadline: string
  idempotency_fingerprint: string
  command_type: string
  payload_ref: string
  tenant_id?: string | null
  [key: string]: unknown
}

export interface ComponentCatalogContract {
  schema_version?: string
  catalog_id?: string
  components: ComponentEntry[]
  ai_may_invent_types?: boolean
  [key: string]: unknown
}

export interface ComponentEntry {
  schema_version?: string
  component_type: string
  version: string
  domain: "platform" | "teacher" | "research" | "student"
  props_schema: Record<string, unknown>
  event_schema: Record<string, unknown>
  risk: "LOW" | "MEDIUM" | "HIGH"
  renderer: RendererSpec
  accessibility: AccessibilitySpec
  fallback: FallbackSpec
  deprecated?: boolean
  deprecated_since?: string | null
  replaced_by?: string | null
  registered_by_manifest?: string | null
  ai_generatable?: boolean
  [key: string]: unknown
}

export interface ContextManifest {
  schema_version?: string
  manifest_id: string
  request_context: RequestContext
  recent_conversation: ConversationWindow
  session_summary?: SessionSummary | null
  domain_state_refs?: string[]
  preference_refs?: string[]
  evidence_refs?: string[]
  trust_labels?: TrustLabels
  token_count: number
  truncation: TruncationInfo
  policy_version: string
  built_at: string
  raw_conversation_retained?: boolean
  [key: string]: unknown
}

export interface ConversationWindow {
  schema_version?: string
  window_size: number
  items?: Record<string, unknown>[]
  truncated?: boolean
  [key: string]: unknown
}

export interface CopyPolicy {
  schema_version?: string
  i18n_root?: string
  stable_copy_catalog_id: string
  dynamic_business_content_source?: "SERVER_SNAPSHOT" | "SERVER_EVENT" | "JOB" | "CONTRACT"
  hardcoding_all_ui_text_forbidden?: boolean
  stable_copy_examples?: string[]
  dynamic_content_examples?: string[]
  [key: string]: unknown
}

export interface CostRef {
  schema_version?: string
  currency?: "CNY" | "USD"
  amount?: number
  [key: string]: unknown
}

export interface DataPolicy {
  schema_version?: string
  retention: "NONE" | "SESSION" | "TERM" | "PROVIDER_DEFAULT"
  training_use?: boolean
  region: string
  pii_allowed?: boolean
  [key: string]: unknown
}

export interface DesignToken {
  schema_version?: string
  name: string
  tier: "FOUNDATION" | "SEMANTIC"
  category: "text" | "surface" | "action" | "status" | "focus" | "elevation" | "spacing" | "radius" | "typography" | "research-accent"
  value_type: "color" | "dimension" | "shadow" | "font-family" | "font-weight" | "number" | "alias"
  alias_of?: string | null
  literal_value?: string | null
  deprecated?: boolean
  deprecated_since?: string | null
  replaced_by?: string | null
  compat_note?: string | null
  [key: string]: unknown
}

export interface DomainEventEnvelope {
  schema_version: string
  event_id: string
  event_type: string
  session_id: string
  revision: number
  occurred_at: string
  trace_id: string
  payload_ref: string
  actor_id?: string | null
  aggregate_type?: "session" | "artifact" | "job" | "claim" | "evidence" | "memory" | null
  [key: string]: unknown
}

export interface EvalSetRef {
  schema_version?: string
  eval_set_id: string
  held_out?: boolean
  passed_at?: string | null
  score?: number | null
  report_ref?: string | null
  [key: string]: unknown
}

export interface EvidenceRef {
  schema_version?: string
  evidence_id: string
  provider: string
  source_type: "TEXTBOOK" | "LITERATURE" | "WEB" | "DATASET" | "EXPERIMENT" | "USER_INPUT"
  source_id: string
  section_id?: string | null
  chunk_id?: string | null
  unit_id?: string | null
  problem_id?: string | null
  asset_id?: string | null
  page?: number | null
  bbox?: BBox | null
  anchor?: StructuralAnchor | null
  quote_hash: string
  verbatim_excerpt?: string | null
  normalized_statement?: string | null
  support_relation?: "SUPPORTS" | "CHALLENGES" | "CONTEXT_ONLY"
  rights: RightsInfo
  provenance: ProvenanceInfo
  validity: ValidityInfo
  created_by_run_id?: string | null
  locator_version?: string
  [key: string]: unknown
}

export interface FallbackSpec {
  schema_version?: string
  strategy: "TEXT_SUMMARY" | "PLACEHOLDER" | "HIDE_WITH_NOTICE" | "RAW_JSON_LINK"
  reason_i18n_key?: string | null
  min_supported_version?: string | null
  [key: string]: unknown
}

export interface FontStack {
  schema_version?: string
  name: string
  stack: string[]
  usage?: "global" | "numeric" | "code" | "display"
  network_font?: boolean
  [key: string]: unknown
}

export interface FrontendStateAndCopyContract {
  schema_version?: string
  states: StateBoundary[]
  localStorage_policy: LocalStoragePolicy
  copy_policy: CopyPolicy
  anti_patterns: AntiPatternRule[]
  stream_reuse_per_workspace?: boolean
  [key: string]: unknown
}

export interface IdempotencyPolicy {
  schema_version?: string
  mode?: "NONE" | "FINGERPRINT" | "NATURAL_KEY"
  key_field?: string | null
  window_seconds?: number | null
  [key: string]: unknown
}

export interface JobContract {
  schema_version?: string
  job_id: string
  job_type: string
  owner_service: string
  tenant_id?: string | null
  input_hash: string
  status: "QUEUED" | "RUNNING" | "RETRYING" | "PARTIAL" | "SUCCEEDED" | "FAILED" | "CANCELLED"
  stage?: string | null
  progress?: number | null
  attempt: number
  max_attempts: number
  lease_expires_at: string
  fencing_token: number
  heartbeat_at: string
  timeout_seconds: number
  cancel_requested?: boolean
  cancel_requested_at?: string | null
  result_ref?: string | null
  error_ref?: string | null
  trace_id: string
  cost?: CostRef
  partial_reason?: PartialReason | null
  dlq?: boolean
  unknown_flags?: string[]
  [key: string]: unknown
}

export interface LocalStoragePolicy {
  schema_version?: string
  allowed_kinds?: string[]
  forbidden_kinds?: string[]
  [key: string]: unknown
}

export interface MemoryConsent {
  schema_version?: string
  granted: boolean
  scope: string
  granted_at?: string | null
  revoked_at?: string | null
  [key: string]: unknown
}

export interface MemoryNamespace {
  schema_version?: string
  tenant_id: string
  user_id: string
  domain: string
  purpose: string
  project_id?: string | null
  thread_id?: string | null
  [key: string]: unknown
}

export interface MemoryProvenance {
  schema_version?: string
  source: "USER_EXPLICIT" | "INFERRED" | "SYSTEM" | "MIGRATION"
  created_by_run_id?: string | null
  note?: string | null
  [key: string]: unknown
}

export interface MemoryRecord {
  schema_version?: string
  memory_id: string
  namespace: MemoryNamespace
  kind: "PREFERENCE" | "FACT" | "SUMMARY" | "DOMAIN_STATE_REF" | "CONVERSATION_SUMMARY"
  content_hash: string
  provenance: MemoryProvenance
  consent: MemoryConsent
  created_at: string
  updated_at: string
  expires_at?: string | null
  redaction: MemoryRedaction
  tombstone?: MemoryTombstone | null
  content_ref?: string | null
  is_long_term?: boolean
  [key: string]: unknown
}

export interface MemoryRedaction {
  schema_version?: string
  status: "APPLIED" | "NOT_REQUIRED" | "FAILED"
  policy_version: string
  redacted_fields?: string[]
  [key: string]: unknown
}

export interface MemoryTombstone {
  schema_version?: string
  deleted_at: string
  reason: "USER_DELETE" | "RETENTION_EXPIRED" | "POLICY_REVOKED" | "ADMIN_DELETE"
  requested_by?: string | null
  [key: string]: unknown
}

export interface ModelPolicy {
  schema_version?: string
  provider: string
  model_id: string
  temperature?: number | null
  max_tokens?: number | null
  fallback_model_id?: string | null
  [key: string]: unknown
}

export interface ModelRef {
  schema_version?: string
  provider: string
  model_id: string
  parameters?: Record<string, unknown>
  [key: string]: unknown
}

export interface PartialReason {
  schema_version?: string
  succeeded_stages?: string[]
  failed_stages?: string[]
  note?: string | null
  [key: string]: unknown
}

export interface PlatformDesignTokenContract {
  schema_version?: string
  tokens: DesignToken[]
  font_stacks?: FontStack[]
  token_entry_module?: string
  business_page_hex_included?: boolean
  [key: string]: unknown
}

export interface PromptRef {
  schema_version?: string
  prompt_id: string
  version: string
  hash: string
  input_schema_id: string
  output_schema_id: string
  model_policy: ModelPolicy
  eval_set_id?: string | null
  eval_set?: EvalSetRef | null
  release_status: "DRAFT" | "CANDIDATE" | "RELEASED" | "DEPRECATED" | "ROLLED_BACK"
  rollback_version?: string | null
  released_at?: string | null
  deprecated_at?: string | null
  owner?: string | null
  [key: string]: unknown
}

export interface PromptRefInline {
  schema_version?: string
  prompt_id: string
  version: string
  hash: string
  release_status?: "DRAFT" | "CANDIDATE" | "RELEASED" | "DEPRECATED"
  [key: string]: unknown
}

export interface ProvenanceInfo {
  schema_version?: string
  retrieved_at: string
  parser_version: string
  provider_version?: string | null
  index_version?: string | null
  asset_hash?: string | null
  [key: string]: unknown
}

export interface ProviderContract {
  schema_version?: string
  name: string
  version: string
  capabilities: string[]
  data_policy: DataPolicy
  timeout_seconds: number
  rate_limit: RateLimit
  cost?: ProviderCost
  retry: RetryPolicy
  rights: Record<string, unknown>
  health: ProviderHealthInfo
  read_only?: boolean
  [key: string]: unknown
}

export interface ProviderCost {
  schema_version?: string
  currency?: "CNY" | "USD"
  per_call?: number
  per_1k_tokens?: number | null
  budget_per_session?: number | null
  [key: string]: unknown
}

export interface ProviderHealthInfo {
  schema_version?: string
  status: "HEALTHY" | "DEGRADED" | "UNAVAILABLE" | "UNKNOWN"
  checked_at: string
  note?: string | null
  [key: string]: unknown
}

export interface RateLimit {
  schema_version?: string
  rpm?: number | null
  tpm?: number | null
  concurrent?: number | null
  [key: string]: unknown
}

export interface RedactionInfo {
  schema_version?: string
  status: "APPLIED" | "NOT_REQUIRED" | "FAILED"
  policy_version: string
  redacted_fields?: string[]
  [key: string]: unknown
}

export interface RegionSpec {
  schema_version?: string
  role: "navigation" | "navigation-or-conversation" | "conversation" | "artifact-canvas" | "evidence-rail" | "toolbar" | "status"
  slot_name: string
  order: number
  collapsible?: boolean
  default_collapsed?: boolean
  min_width_px?: number | null
  max_width_px?: number | null
  scrollable?: boolean
  [key: string]: unknown
}

export interface RendererSpec {
  schema_version?: string
  kind: "PLATFORM_PRIMITIVE" | "REGISTERED_DOMAIN_COMPONENT" | "TEXT_FALLBACK"
  component_name: string
  arbitrary_html?: boolean
  arbitrary_css?: boolean
  arbitrary_js?: boolean
  [key: string]: unknown
}

export interface RequestContext {
  schema_version?: string
  command_type: string
  budget?: Record<string, unknown>
  locale?: string | null
  [key: string]: unknown
}

export interface ResourceScope {
  schema_version?: string
  kind: "tenant" | "school" | "class" | "session" | "project" | "artifact" | "job" | "self"
  id?: string | null
  role?: "owner" | "editor" | "reviewer" | "viewer" | "member" | null
  [key: string]: unknown
}

export interface ResponsiveRule {
  schema_version?: string
  breakpoint_px: number
  name: string
  collapse?: Record<string, "COLLAPSE_TO_RAIL" | "OVERLAY_DRAWER" | "HIDE" | "STACK" | "KEEP">
  [key: string]: unknown
}

export interface RetryPolicy {
  schema_version?: string
  max_attempts: number
  backoff?: "NONE" | "FIXED" | "EXPONENTIAL" | "EXPONENTIAL_JITTER"
  retry_on?: "TIMEOUT" | "RATE_LIMIT" | "UPSTREAM_5XX" | "TRANSIENT_NETWORK"[]
  [key: string]: unknown
}

export interface RightsInfo {
  schema_version?: string
  rights_evidence_ref: string
  redistributable?: boolean
  license_id?: string | null
  quote_length_limit?: number | null
  [key: string]: unknown
}

export interface SSECursor {
  schema_version?: string
  session_id: string
  last_event_id?: string | null
  fallback?: "NONE" | "SNAPSHOT_REQUIRED"
  revision: number
  stream_epoch?: number
  [key: string]: unknown
}

export interface SchemaValidation {
  schema_version?: string
  status: "PASSED" | "FAILED" | "NOT_APPLICABLE"
  schema_id?: string | null
  errors?: string[]
  [key: string]: unknown
}

export interface SessionSummary {
  schema_version?: string
  version: number
  text: string
  updated_at: string
  pending_items?: string[]
  [key: string]: unknown
}

export interface StateBoundary {
  schema_version?: string
  category: "server" | "stream" | "client"
  examples: string[]
  authority: "SERVER" | "STREAM_TRANSIENT" | "CLIENT_UI_ONLY"
  localStorage_allowed?: boolean
  may_change_business_truth?: boolean
  refresh_recoverable?: boolean
  [key: string]: unknown
}

export interface StorageLocator {
  schema_version?: string
  service?: "minio" | "local_fs" | "object_store" | "external"
  bucket: string
  key: string
  [key: string]: unknown
}

export interface StructuralAnchor {
  schema_version?: string
  section?: string | null
  table?: string | null
  figure?: string | null
  formula?: string | null
  paragraph?: string | null
  [key: string]: unknown
}

export interface TokenUsage {
  schema_version?: string
  in?: number
  out?: number
  [key: string]: unknown
}

export interface ToolAudit {
  schema_version?: string
  emit_event?: boolean
  redact_fields?: string[]
  retain_days?: number | null
  [key: string]: unknown
}

export interface ToolCallRecord {
  schema_version?: string
  tool_name: string
  tool_version?: string
  level?: "READ" | "PROPOSE" | "EXECUTE_ISOLATED" | "MUTATE_CONFIRMED" | "ADMIN"
  input_hash?: string | null
  output_ref?: string | null
  status?: "SUCCEEDED" | "FAILED" | "SKIPPED" | "UNKNOWN"
  duration_ms?: number | null
  [key: string]: unknown
}

export interface ToolPolicy {
  schema_version?: string
  tool_name: string
  tool_version?: string
  level: "READ" | "PROPOSE" | "EXECUTE_ISOLATED" | "MUTATE_CONFIRMED" | "ADMIN"
  description?: string | null
  input_schema: Record<string, unknown>
  output_schema: Record<string, unknown>
  required_scopes?: string[]
  timeout_seconds: number
  idempotency: IdempotencyPolicy
  side_effect: "NONE" | "WRITE" | "EXTERNAL_CALL" | "EXECUTE" | "DELETE"
  risk: "LOW" | "MEDIUM" | "HIGH" | "CRITICAL"
  requires_confirmation: boolean
  recheck_authorization_at_execute: boolean
  audit: ToolAudit
  agent_invocable?: boolean
  network_access?: "NONE" | "ALLOWLISTED" | "FULL"
  [key: string]: unknown
}

export interface TruncationInfo {
  schema_version?: string
  truncated: boolean
  reason?: "TOKEN_BUDGET" | "WINDOW_LIMIT" | "RETENTION_EXPIRED" | "POLICY_FILTER" | "NONE"
  [key: string]: unknown
}

export interface TrustLabels {
  schema_version?: string
  request_context?: "TRUSTED_SYSTEM" | "TRUSTED_POLICY" | "TRUSTED_USER_INSTRUCTION" | "UNTRUSTED_DATA"
  recent_conversation?: "TRUSTED_SYSTEM" | "TRUSTED_POLICY" | "TRUSTED_USER_INSTRUCTION" | "UNTRUSTED_DATA"
  session_summary?: "TRUSTED_SYSTEM" | "TRUSTED_POLICY" | "TRUSTED_USER_INSTRUCTION" | "UNTRUSTED_DATA"
  domain_state_refs?: "TRUSTED_SYSTEM" | "TRUSTED_POLICY" | "TRUSTED_USER_INSTRUCTION" | "UNTRUSTED_DATA"
  preference_refs?: "TRUSTED_SYSTEM" | "TRUSTED_POLICY" | "TRUSTED_USER_INSTRUCTION" | "UNTRUSTED_DATA"
  evidence_refs?: "TRUSTED_SYSTEM" | "TRUSTED_POLICY" | "TRUSTED_USER_INSTRUCTION" | "UNTRUSTED_DATA"
  [key: string]: unknown
}

export interface ValidityInfo {
  schema_version?: string
  status?: "VALID" | "STALE" | "REVOKED" | "UNKNOWN"
  checked_at: string
  note?: string | null
  [key: string]: unknown
}

export interface WorkspaceLayoutContract {
  schema_version?: string
  layout_id: string
  regions: RegionSpec[]
  responsive_rules?: ResponsiveRule[]
  conversation_first?: boolean
  stepper_allowed?: boolean
  wizard_allowed?: boolean
  domain_artifact_enum_embedded?: boolean
  [key: string]: unknown
}

export interface PlatformContractsBundle {
  schema_version?: string | null
  access_context?: AccessContext | null | null
  command_envelope?: CommandEnvelope | null | null
  domain_event_envelope?: DomainEventEnvelope | null | null
  sse_cursor?: SSECursor | null | null
  job_contract?: JobContract | null | null
  artifact_ref?: ArtifactRef | null | null
  evidence_ref?: EvidenceRef | null | null
  provider_contract?: ProviderContract | null | null
  ai_run_record?: AIRunRecord | null | null
  tool_policy?: ToolPolicy | null | null
  prompt_ref?: PromptRef | null | null
  memory_record?: MemoryRecord | null | null
  context_manifest?: ContextManifest | null | null
  platform_design_token_contract?: PlatformDesignTokenContract | null | null
  workspace_layout_contract?: WorkspaceLayoutContract | null | null
  component_catalog_contract?: ComponentCatalogContract | null | null
  frontend_state_and_copy_contract?: FrontendStateAndCopyContract | null | null
}
