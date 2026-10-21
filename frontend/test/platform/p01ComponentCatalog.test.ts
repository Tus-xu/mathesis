/**
 * P01 · 平台组件目录（component catalog）契约测试。
 *
 * 覆盖 P00 `ComponentCatalogContract` 的实现侧要求：
 * - 版本化注册表 + 校验 + 渲染器 + fallback；
 * - 模型输出**只能**引用已登记的 `component_type` + 类型化 props，
 *   **禁止**任意 HTML / CSS / JS（ADR-PLAT-10）；
 * - 域组件必须经域 manifest 注册，平台不得内建域业务组件。
 */
/// <reference types="vite/client" />
import { describe, expect, it, beforeEach } from 'vitest'

import {
  createCatalog,
  registerComponent,
  lookupComponent,
  validateCatalog,
  renderCatalogNode,
  type CatalogEntry,
} from '../../src/platform/component-catalog'
import { PLATFORM_CATALOG_ENTRIES } from '../../src/platform/component-catalog/catalog'

function entry(overrides: Partial<CatalogEntry> = {}): CatalogEntry {
  return {
    component_type: 'TestCard',
    version: '1.0.0',
    domain: 'platform',
    props_schema: { type: 'object', properties: {}, additionalProperties: true },
    event_schema: { type: 'object' },
    risk: 'LOW',
    renderer: { kind: 'PLATFORM_PRIMITIVE', component_name: 'TestCard' },
    accessibility: { role: 'article' },
    fallback: { strategy: 'TEXT_SUMMARY' },
    ai_generatable: true,
    ...overrides,
  }
}

describe('P01 · 组件目录注册表', () => {
  it('可注册并查找组件', () => {
    const catalog = createCatalog()
    expect(registerComponent(catalog, entry())).toBe(true)
    expect(lookupComponent(catalog, 'TestCard')?.version).toBe('1.0.0')
  })

  it('重复注册同一 component_type 必须被拒绝（不得静默覆盖）', () => {
    const catalog = createCatalog()
    registerComponent(catalog, entry())
    expect(registerComponent(catalog, entry({ version: '2.0.0' }))).toBe(false)
    expect(lookupComponent(catalog, 'TestCard')?.version).toBe('1.0.0')
  })

  it('查找未登记组件返回 undefined（不得返回占位实现）', () => {
    const catalog = createCatalog()
    expect(lookupComponent(catalog, 'NotRegistered')).toBeUndefined()
  })

  it('平台内置目录必须非空且全部通过校验', () => {
    expect(PLATFORM_CATALOG_ENTRIES.length).toBeGreaterThan(0)
    const result = validateCatalog(PLATFORM_CATALOG_ENTRIES)
    expect(result.ok).toBe(true)
    expect(result.errors).toEqual([])
  })
})

describe('P01 · 组件目录校验（禁止任意标记）', () => {
  it('renderer 声明任意 HTML 必须被拒绝', () => {
    const bad = [
      entry({
        component_type: 'BadHtml',
        renderer: { kind: 'PLATFORM_PRIMITIVE', component_name: 'Bad', arbitrary_html: true },
      }),
    ]
    const r = validateCatalog(bad)
    expect(r.ok).toBe(false)
    expect(r.errors.join()).toMatch(/arbitrary_html/)
  })

  it('renderer 声明任意 CSS 必须被拒绝', () => {
    const bad = [
      entry({
        component_type: 'BadCss',
        renderer: { kind: 'PLATFORM_PRIMITIVE', component_name: 'Bad', arbitrary_css: true },
      }),
    ]
    expect(validateCatalog(bad).ok).toBe(false)
  })

  it('renderer 声明任意 JS 必须被拒绝', () => {
    const bad = [
      entry({
        component_type: 'BadJs',
        renderer: { kind: 'PLATFORM_PRIMITIVE', component_name: 'Bad', arbitrary_js: true },
      }),
    ]
    expect(validateCatalog(bad).ok).toBe(false)
  })

  it('缺少 fallback 的组件必须被拒绝（模型输出需可降级）', () => {
    const bad = [entry({ component_type: 'NoFallback', fallback: undefined as never })]
    expect(validateCatalog(bad).ok).toBe(false)
  })

  it('缺少 accessibility 的组件必须被拒绝', () => {
    const bad = [entry({ component_type: 'NoA11y', accessibility: undefined as never })]
    expect(validateCatalog(bad).ok).toBe(false)
  })

  it('未知 domain 必须被拒绝', () => {
    const bad = [entry({ component_type: 'BadDomain', domain: 'hacker' as never })]
    expect(validateCatalog(bad).ok).toBe(false)
  })

  it('非 semver 版本必须被拒绝', () => {
    const bad = [entry({ component_type: 'BadVer', version: 'v1' })]
    expect(validateCatalog(bad).ok).toBe(false)
  })
})

describe('P01 · 渲染器（只允许登记类型 + 类型化 props）', () => {
  let catalog: ReturnType<typeof createCatalog>

  beforeEach(() => {
    catalog = createCatalog()
    PLATFORM_CATALOG_ENTRIES.forEach((e) => registerComponent(catalog, e))
  })

  it('合法的 catalog 节点应渲染为组件描述，而非原始标记', () => {
    const out = renderCatalogNode(catalog, {
      type: 'JobStatusProgress',
      props: { state: 'RUNNING' },
    })
    expect(out.kind).toBe('COMPONENT')
    if (out.kind !== 'COMPONENT') throw new Error('expected COMPONENT')
    expect(out.componentName).toBeTruthy()
  })

  it('引用未登记类型必须降级为 fallback，而不是报错中断渲染', () => {
    const out = renderCatalogNode(catalog, {
      type: 'TotallyUnknown',
      props: {},
    })
    expect(out.kind).toBe('FALLBACK')
  })

  it('节点携带 html/css/js 字段必须被拒绝（模型不得注入标记）', () => {
    const out = renderCatalogNode(catalog, {
      type: 'JobStatusProgress',
      props: { state: 'RUNNING' },
      html: '<img src=x onerror=alert(1)>',
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
    } as any)
    expect(out.kind).toBe('REJECTED')
  })

  it('props 中含 script 字符串不得被当作可执行内容（只透传为数据）', () => {
    const out = renderCatalogNode(catalog, {
      type: 'JobStatusProgress',
      props: { state: '<script>alert(1)</script>' },
    })
    // 允许作为**数据**透传，但绝不允许标记为可执行渲染
    expect(out.kind).toBe('COMPONENT')
    if (out.kind === 'COMPONENT') {
      expect(out.allowRawMarkup).toBe(false)
    }
  })
})
