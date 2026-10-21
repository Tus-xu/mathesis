/**
 * R13 跨端契约 consumer contract test（前端侧）。
 *
 * 与后端 tests/unit/teacher_prep_v2/test_fixtures_and_schema.py 双向互证：
 * 1. 同一份 prep-contracts.schema.json：valid fixtures 必须通过、invalid 必须被拒；
 * 2. golden（每契约 full 变体）规范 sha256 与后端钉死的常量一致（Python↔TS 语义互证）；
 * 3. 生成 TS 类型在本文件 import（随 pnpm typecheck 编译，杜绝三端漂移）。
 */
/// <reference types="vite/client" />
import { createHash } from 'node:crypto'

import { describe, expect, it } from 'vitest'

import {
  PREP_CONTRACTS_SCHEMA_VERSION,
  type ClassLearningProfile,
  type ClassroomTask,
  type Evidence,
  type PreClassDiagnosticTask,
  type PrepContractsCatalog,
  type StudentSubmission,
  type TaskAssignment,
} from '../../src/features/teacher-prep-v2/types/prep-contracts'
import { canonicalSerialize, validateBySchema } from '../../src/features/teacher-prep-v2/contract/validateSchema'

import prepSchema from '../../src/features/teacher-prep-v2/fixtures/prep-contracts.schema.json'

const KIND_TO_CONTRACT: Record<string, string> = {
  PRE_CLASS_DIAGNOSTIC: 'PreClassDiagnosticTask',
  CLASSROOM: 'ClassroomTask',
  TASK_ASSIGNMENT: 'TaskAssignment',
  STUDENT_SUBMISSION: 'StudentSubmission',
  EVIDENCE: 'Evidence',
  CLASS_LEARNING_PROFILE: 'ClassLearningProfile',
}

// 与后端 test_fixtures_and_schema.CANONICAL_SHA256 共享（机器互证锚点，禁止单侧漂移）
const GOLDEN_SHA256: Record<string, string> = {
  diagnostic_full: 'b1b95506b341d47108961f01e1d3122ebed4a1240f21f67699a963651d8fb90e',
  classroom_full: '7f29e5d2ce2b63b8a85bfc7854fd8fa798d10fb5bff6c235742956cbe1bb6f8f',
  assignment_provisioned: '17979de23ac7f67465f08e2a92031af460e2a5eba6816b07125e7cdf7b01f378',
  submission_graded: '1ff891a57c7bb646e5d7a61f62c4f0d714bfd6fb47cf8f5319c22edb6fe63d89',
  evidence_photo: 'db8d0cd49ec75d7ad21ac926fb30b0d66c0c9844073cee897d2825d3f63f4eca',
  profile_full: 'e1a1a291ac84ead3f30844ea615aa56d663b02527e1cd70cf8041e19d0dee4cc',
}

function schemaFor(contract: string) {
  const defs = prepSchema.$defs as Record<string, unknown> | undefined
  if (!defs || !defs[contract]) {
    throw new Error(`prep-contracts.schema.json 缺少 $defs.${contract}`)
  }
  return { $defs: prepSchema.$defs, $ref: `#/$defs/${contract}` }
}

function contractOf(data: unknown): string {
  const kind = (data as { kind?: string }).kind ?? ''
  const contract = KIND_TO_CONTRACT[kind]
  if (!contract) throw new Error(`fixture 无法推断契约（kind=${kind}）`)
  return contract
}

describe('R13 跨端契约 consumer 测试', () => {
  it('schema 版本常量与 TS 导出一致', () => {
    expect(PREP_CONTRACTS_SCHEMA_VERSION).toBe('1.0.0')
    // bundle 根 schema_version 由 Field(default=...) 产出（default 语义 = 常量）
    expect(prepSchema.properties.schema_version.default).toBe(PREP_CONTRACTS_SCHEMA_VERSION)
  })

  it('全部 valid fixtures 通过各自 JSON Schema（provider/consumer 单向一致）', () => {
    for (const file of VALID_FIXTURES) {
      const data = file.data
      const contract = contractOf(data)
      const errors = validateBySchema(data, schemaFor(contract))
      expect(errors, `${file.name} 对 ${contract} 应通过`).toEqual([])
    }
  })

  it('全部 invalid fixtures 被各自 JSON Schema 拒绝', () => {
    for (const file of INVALID_FIXTURES) {
      const data = file.data
      const contract = contractOf(data)
      const errors = validateBySchema(data, schemaFor(contract))
      expect(errors.length, `${file.name} 对 ${contract} 应被拒绝`).toBeGreaterThan(0)
    }
  })

  it('golden fixtures 规范 sha256 与后端钉死常量一致（Python↔TS 语义互证）', () => {
    for (const [name, expected] of Object.entries(GOLDEN_SHA256)) {
      const data = (VALID_FIXTURES.find((f) => f.name === name) ?? { data: null }).data
      if (data === null) throw new Error(`缺少 golden fixture ${name}`)
      const actual = createHash('sha256').update(canonicalSerialize(data)).digest('hex')
      expect(actual, `${name} 规范哈希漂移`).toBe(expected)
    }
  })

  it('TS 契约类型可编译并承载根目录（typecheck 覆盖生成层）', () => {
    // 运行时无法测 TS 类型；此处引用全部生成接口，保证 tsc 编译覆盖、三端零漂移
    const probe: PrepContractsCatalog = { schema_version: PREP_CONTRACTS_SCHEMA_VERSION }
    expect(typeof probe).toBe('object')
    const fn = (_: PreClassDiagnosticTask | ClassroomTask | TaskAssignment | StudentSubmission | Evidence | ClassLearningProfile): string => 'ok'
    expect(fn(probe as unknown as StudentSubmission)).toBe('ok')
  })
})

// ── fixtures 载入（flat 拷贝：fixtures/valid|invalid/*.json） ──────────────
interface FixtureEntry {
  name: string
  data: unknown
}

const importersValid = import.meta.glob('../../src/features/teacher-prep-v2/fixtures/valid/*.json', { eager: true })
const importersInvalid = import.meta.glob('../../src/features/teacher-prep-v2/fixtures/invalid/*.json', { eager: true })

const VALID_FIXTURES: FixtureEntry[] = Object.entries(importersValid).map(([path, mod]) => ({
  name: path.split('/').pop()!.replace('.json', ''),
  data: (mod as { default: unknown }).default,
}))
const INVALID_FIXTURES: FixtureEntry[] = Object.entries(importersInvalid).map(([path, mod]) => ({
  name: path.split('/').pop()!.replace('.json', ''),
  data: (mod as { default: unknown }).default,
}))