/**
 * PPT Studio V2 TeachingDeckSpec 契约测试（R10，前端侧）。
 *
 * 锚点（与后端 tests/unit/ppt_v2/test_contract_triple.py 共享）：
 * - 类型文件 src/features/ppt-studio-v2/types/deck-spec.ts 由后端 Pydantic 生成并复制，
 *   禁止手改；前端以该类型 + 生成 JSON Schema 双重锚定；
 * - 黄金 fixture 的规范序列化 SHA256 与后端常量一致（Python/TS 对同一 JSON 语义互证）。
 * 本文件不依赖 UI、不触网。
 */
import { describe, expect, it } from 'vitest'
import { createHash } from 'node:crypto'
import type { TeachingDeckSpec } from '@/features/ppt-studio-v2/types/deck-spec'
import schema from '@/features/ppt-studio-v2/fixtures/deck-spec.schema.json'
import goldenA from '@/features/ppt-studio-v2/fixtures/deck_golden_a.json'
import goldenB from '@/features/ppt-studio-v2/fixtures/deck_golden_b.json'
import { canonicalSerialize, validateBySchema } from '@/features/ppt-studio-v2/contract/validateSchema'

// 与后端 CANONICAL_SHA256 完全一致（见 test_contract_triple.py）
// R20 契约再生成：TeachingDeckSpec 增补可选 projection 血缘块 → fixtures 加 "projection": null，哈希同步前移
const EXPECTED_CANONICAL_SHA256: Record<string, string> = {
  'deck_golden_a.json': '4508dcc051e9f642ecc07aa543fe7b39e85044cb8672fc052db317af36a9d209',
  'deck_golden_b.json': '33175967ae1b2a39df5e9fbfec03986a8457431be951242419de9b61e4c97a17',
}

function sha256(utf8: string): string {
  return createHash('sha256').update(utf8, 'utf8').digest('hex')
}

describe('PPT Studio V2 TeachingDeckSpec 契约（R10）', () => {
  it('golden fixture 通过生成 JSON Schema 运行时校验', () => {
    expect(validateBySchema(goldenA, schema)).toEqual([])
    expect(validateBySchema(goldenB, schema)).toEqual([])
  })

  it('类型层锚定：fixture 结构必须满足 TeachingDeckSpec 类型（编译期 + 运行时）', () => {
    const a: TeachingDeckSpec = goldenA as TeachingDeckSpec
    const b: TeachingDeckSpec = goldenB as TeachingDeckSpec
    expect(a.schema_version).toBe('2.0.0')
    expect((b.slides ?? []).length).toBe(4)
    expect((a.slides ?? []).length).toBe(8)
    const first: NonNullable<TeachingDeckSpec['slides']>[number] | undefined = (a.slides ?? [])[0]
    expect(typeof first?.layout_hint).toBe('string' as unknown)
  })

  it('契约违规被校验器拦截：删 textbook_sources 被 minItems 捕获', () => {
    const missingSource = { ...goldenA, textbook_sources: [] }
    const errs = validateBySchema(missingSource, schema)
    expect(errs.length).toBeGreaterThan(0)
    expect(errs.join('|')).toContain('textbook_sources')
  })

  it('规范序列化 SHA256 与后端断言一致（Python/TS 语义互证）', async () => {
    const fixtures: Array<[string, unknown]> = [
      ['deck_golden_a.json', goldenA],
      ['deck_golden_b.json', goldenB],
    ]
    for (const [name, data] of fixtures) {
      const actual = sha256(canonicalSerialize(data))
      expect(actual).toBe(EXPECTED_CANONICAL_SHA256[name])
    }
    // 类型常量与 schema 声明的 const 一致
    const { DECK_SCHEMA_VERSION } = await import('@/features/ppt-studio-v2/types/deck-spec')
    expect(DECK_SCHEMA_VERSION).toBe('2.0.0')
  })
})