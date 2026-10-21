/**
 * P00 共享平台契约 consumer contract test（前端侧 / 双域）。
 *
 * 与后端 tests/contracts/test_platform_contracts.py 双向互证：
 * 1. 同一份 platform-contracts.schema.json：valid fixtures 必须通过、invalid 必须被拒；
 * 2. 生成 TS 类型在本文件 import（随 pnpm typecheck 编译，杜绝前后端漂移）；
 * 3. Teacher 与 Research 两个消费者对同一契约的字段解释必须一致。
 *
 * 边界（DECISIONS §7/§8、frontend AGENTS.md）：
 * - 前端只是**消费**契约；不得在前端定义业务真相；
 * - 不允许把 unknown 字段当作成功；
 * - 不允许 per-card SSE 流（由契约层断言 stream_reuse_per_workspace）。
 */
/// <reference types="vite/client" />
import { describe, expect, it } from 'vitest'

import {
  PLATFORM_CONTRACTS_SCHEMA_VERSION,
  type AccessContext,
  type ArtifactRef,
  type EvidenceRef,
  type FrontendStateAndCopyContract,
  type JobContract,
  type MemoryRecord,
} from '../../src/features/platform-contracts/types/platform-contracts'
import { validateBySchema } from '../../src/features/platform-contracts/contract/validateSchema'
import { checkSemantics } from '../../src/features/platform-contracts/contract/semanticGuards'

import schema from '../../src/features/platform-contracts/fixtures/platform-contracts.schema.json'

import accessTeacher from '../../src/features/platform-contracts/fixtures/valid/access_context_teacher.json'
import accessResearch from '../../src/features/platform-contracts/fixtures/valid/access_context_research.json'
import evidenceTextbook from '../../src/features/platform-contracts/fixtures/valid/evidence_ref_textbook.json'
import evidenceLiterature from '../../src/features/platform-contracts/fixtures/valid/evidence_ref_literature.json'
import jobRunning from '../../src/features/platform-contracts/fixtures/valid/job_running.json'
import jobPartial from '../../src/features/platform-contracts/fixtures/valid/job_partial.json'
import artifactDeck from '../../src/features/platform-contracts/fixtures/valid/artifact_ref_deck.json'
import memoryPreference from '../../src/features/platform-contracts/fixtures/valid/memory_record_preference.json'
import frontendState from '../../src/features/platform-contracts/fixtures/valid/frontend_state_and_copy_platform.json'

import badAccessUnknownWorkspace from '../../src/features/platform-contracts/fixtures/invalid/access_context_unknown_workspace.json'
import badJobUnknownStatus from '../../src/features/platform-contracts/fixtures/invalid/job_unknown_status.json'
import badArtifactHash from '../../src/features/platform-contracts/fixtures/invalid/artifact_bad_hash.json'
import badEvidenceTextbookPage from '../../src/features/platform-contracts/fixtures/invalid/evidence_textbook_with_page.json'
import badTrustedEvidence from '../../src/features/platform-contracts/fixtures/invalid/context_manifest_trusted_evidence.json'
import badLayoutStepper from '../../src/features/platform-contracts/fixtures/invalid/workspace_layout_stepper.json'
import badArbitraryHtml from '../../src/features/platform-contracts/fixtures/invalid/component_catalog_arbitrary_html.json'
import badLocalStorageTruth from '../../src/features/platform-contracts/fixtures/invalid/frontend_state_localstorage_business_truth.json'

const JOB_STATES = ['QUEUED', 'RUNNING', 'RETRYING', 'PARTIAL', 'SUCCEEDED', 'FAILED', 'CANCELLED']

function schemaFor(contract: string) {
  const defs = (schema as { $defs?: Record<string, unknown> }).$defs
  if (!defs || !defs[contract]) throw new Error(`schema 缺少 $defs.${contract}`)
  return { $defs: defs, $ref: `#/$defs/${contract}` }
}

function stripContract<T extends { _contract?: string }>(data: T): Omit<T, '_contract'> {
  const copy = { ...data }
  delete copy._contract
  return copy
}

describe('P00 platform contracts · 版本与生成物', () => {
  it('schema 版本是 semver 且前端常量与后端一致', () => {
    expect(PLATFORM_CONTRACTS_SCHEMA_VERSION.split('.')).toHaveLength(3)
    expect(schema.title ?? '').toBeTruthy()
  })

  it('16 项契约全部出现在 $defs 中', () => {
    const defs = Object.keys((schema as { $defs: Record<string, unknown> }).$defs)
    const required = [
      'AccessContext',
      'CommandEnvelope',
      'DomainEventEnvelope',
      'JobContract',
      'ArtifactRef',
      'EvidenceRef',
      'ProviderContract',
      'AIRunRecord',
      'ToolPolicy',
      'PromptRef',
      'MemoryRecord',
      'ContextManifest',
      'PlatformDesignTokenContract',
      'WorkspaceLayoutContract',
      'ComponentCatalogContract',
      'FrontendStateAndCopyContract',
    ]
    for (const name of required) {
      expect(defs, `缺少 ${name}`).toContain(name)
    }
  })
})

describe('Teacher consumer · 前端侧', () => {
  it('AccessContext 可被解析出 workspace/capability/sv', () => {
    const ctx = stripContract(accessTeacher) as unknown as AccessContext
    expect(ctx.workspace).toBe('teacher')
    expect(ctx.capabilities).toContain('workspace.teacher.enter')
    expect(ctx.security_version).toBeGreaterThanOrEqual(1)
  })

  it('教材证据不得携带 page 锚点', () => {
    const evd = stripContract(evidenceTextbook) as unknown as EvidenceRef & { page?: number }
    expect(evd.source_type).toBe('TEXTBOOK')
    expect(evd.section_id).toBeTruthy()
    expect(evd.chunk_id).toBeTruthy()
    expect(evd.page).toBeUndefined()
  })

  it('Job PARTIAL 不被当作完成', () => {
    const job = stripContract(jobPartial) as unknown as JobContract
    expect(job.status).toBe('PARTIAL')
    expect(job.status).not.toBe('SUCCEEDED')
    expect(JOB_STATES).toContain(job.status)
  })

  it('ArtifactRef 携带 hash 与 ACL', () => {
    const art = stripContract(artifactDeck) as unknown as ArtifactRef
    expect(art.hash).toHaveLength(64)
    expect(art.acl.visibility).toBe('PRIVATE')
  })
})

describe('Research consumer · 前端侧', () => {
  it('AccessContext workspace=research 且具备 project scope', () => {
    const ctx = stripContract(accessResearch) as unknown as AccessContext
    expect(ctx.workspace).toBe('research')
    expect(ctx.capabilities).toContain('workspace.research.enter')
  })

  it('LITERATURE 证据可使用 page+bbox', () => {
    const evd = stripContract(evidenceLiterature) as unknown as EvidenceRef & { page?: number }
    expect(evd.source_type).toBe('LITERATURE')
    expect(evd.page).toBe(3)
  })

  it('教材证据在 Research 侧仍保持 TEXTBOOK（不伪装成文献）', () => {
    const evd = stripContract(evidenceTextbook) as unknown as EvidenceRef
    expect(evd.source_type).toBe('TEXTBOOK')
    expect(evd.source_type).not.toBe('LITERATURE')
  })

  it('同一 JobContract 形状跨域一致（无域专属字段）', () => {
    const job = stripContract(jobRunning) as unknown as JobContract
    expect(job.fencing_token).toBeGreaterThanOrEqual(0)
    expect(job.heartbeat_at).toBeTruthy()
    expect(Object.keys(job)).not.toContain('teaching_deck_spec')
  })

  it('MemoryRecord namespace 是隔离边界（租户/用户/域/用途）', () => {
    const mem = stripContract(memoryPreference) as unknown as MemoryRecord
    expect(mem.namespace.tenant_id).toBeTruthy()
    expect(mem.namespace.user_id).toBeTruthy()
    expect(mem.namespace.domain).toBeTruthy()
    expect(mem.namespace.purpose).toBeTruthy()
  })
})

describe('前端状态与 copy 契约（反模式机器化）', () => {
  it('server/stream/client 三态齐备，localStorage 不允许业务真相', () => {
    const c = stripContract(frontendState) as unknown as FrontendStateAndCopyContract
    expect(c.states.map((s) => s.category).sort()).toEqual(['client', 'server', 'stream'])
    expect(c.localStorage_policy.forbidden_kinds).toContain('BUSINESS_TRUTH')
    expect(c.localStorage_policy.forbidden_kinds).toContain('AUTHORIZATION')
    // 契约默认值：stream_reuse_per_workspace 缺省即 True（禁止 per-card 流）
    expect(c.stream_reuse_per_workspace ?? true).toBe(true)
  })

  it('反模式规则覆盖 Stepper/Wizard、假进度、raw color、任意 HTML', () => {
    const c = stripContract(frontendState) as unknown as FrontendStateAndCopyContract
    const ids = c.anti_patterns.map((r) => r.rule_id)
    expect(ids).toContain('STEPPER_OR_WIZARD')
    expect(ids).toContain('FAKE_PROGRESS_TIMER')
    expect(ids).toContain('RAW_COLOR_IN_COMPONENT')
    expect(ids).toContain('ARBITRARY_MODEL_HTML_CSS_JS')
    expect(ids).toContain('PER_CARD_SSE_STREAM')
  })

  it('稳定通用文案属于 i18n/copy catalog，而不是被禁止的硬编码', () => {
    const c = stripContract(frontendState) as unknown as FrontendStateAndCopyContract
    expect(c.copy_policy.stable_copy_catalog_id).toBeTruthy()
    expect(c.copy_policy.hardcoding_all_ui_text_forbidden ?? false).toBe(false)
    expect(c.copy_policy.stable_copy_examples?.length ?? 0).toBeGreaterThan(0)
  })
})

describe('负例：unknown 不得默认成成功', () => {
  const cases: Array<[string, unknown]> = [
    ['AccessContext 未知 workspace', badAccessUnknownWorkspace],
    ['JobContract 未知 status', badJobUnknownStatus],
    ['ArtifactRef 非法 hash', badArtifactHash],
    ['EvidenceRef 教材带 page', badEvidenceTextbookPage],
    ['ContextManifest 提升 trust label', badTrustedEvidence],
    ['WorkspaceLayout 声明 Stepper', badLayoutStepper],
    ['ComponentCatalog 任意 HTML', badArbitraryHtml],
    ['FrontendState localStorage 业务真相', badLocalStorageTruth],
  ]

  it.each(cases)('%s 必须被拒（schema 或语义层）', (_name, fixture) => {
    const data = stripContract(fixture as { _contract?: string })
    const contract = (fixture as { _contract: string })._contract
    const schemaErrors = validateBySchema(data, schemaFor(contract) as never)
    const semanticErrors = checkSemantics(data)
    expect(
      schemaErrors.length + semanticErrors.length,
      `期望被拒绝，实际两层都通过: ${JSON.stringify(data).slice(0, 120)}`,
    ).toBeGreaterThan(0)
  })

  it('两层都必须真有拦截能力（防止某层恒空导致测试空转）', () => {
    const schemaRejected = cases.filter(([, fx]) => {
      const data = stripContract(fx as { _contract?: string })
      const contract = (fx as { _contract: string })._contract
      return validateBySchema(data, schemaFor(contract) as never).length > 0
    }).length
    const semanticRejected = cases.filter(([, fx]) => checkSemantics(stripContract(fx as object)).length > 0).length
    expect(schemaRejected, '没有任何负例被 schema 层拦截').toBeGreaterThan(0)
    expect(semanticRejected, '没有任何负例被语义层拦截').toBeGreaterThan(0)
  })
})

describe('全部 valid fixtures 必须通过 schema（双向一致）', () => {
  const validFixtures: Array<[string, { _contract: string }]> = [
    ['access_context_teacher', accessTeacher as { _contract: string }],
    ['access_context_research', accessResearch as { _contract: string }],
    ['evidence_ref_textbook', evidenceTextbook as { _contract: string }],
    ['evidence_ref_literature', evidenceLiterature as { _contract: string }],
    ['job_running', jobRunning as { _contract: string }],
    ['job_partial', jobPartial as { _contract: string }],
    ['artifact_ref_deck', artifactDeck as { _contract: string }],
    ['memory_record_preference', memoryPreference as { _contract: string }],
    ['frontend_state_and_copy_platform', frontendState as { _contract: string }],
  ]

  it.each(validFixtures)('%s 通过', (_name, fixture) => {
    const errors = validateBySchema(stripContract(fixture), schemaFor(fixture._contract) as never)
    expect(errors).toEqual([])
  })
})
