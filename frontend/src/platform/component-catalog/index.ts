/**
 * P01 · 平台组件目录（registry / validation / renderer）。
 *
 * 契约来源：P00 `ComponentCatalogContract`（生成 TS：`src/features/platform-contracts/types`）。
 *
 * 核心不变量（ADR-PLAT-10）：
 * 1. 模型输出**只能**表达为「已登记的 component_type + 类型化 props」；
 * 2. **禁止**任意 HTML / CSS / JS（renderer 上三个 `arbitrary_*` 必须为 false/缺省）；
 * 3. 每个条目必须有 `fallback`（未知类型要能降级，而不是白屏）与 `accessibility`；
 * 4. 域组件由**域 manifest** 注册，平台层不内置 Teacher/Research 业务组件；
 * 5. 重复注册**不覆盖**（静默覆盖会让"谁拥有该类型"不可追溯）。
 *
 * 本模块是纯逻辑（不依赖 Vue 运行时），便于单测与在 worker 中复用；
 * 真正的 Vue 渲染由 `WorkspaceLayout` / 消费端按 `componentName` 查找组件表完成。
 */
import type {
  AccessibilitySpec,
  ComponentEntry,
} from '@/features/platform-contracts/types/platform-contracts'

/** 目录条目（与契约 `ComponentEntry` 同形，此处显式声明便于检查）。 */
export interface CatalogEntry extends Omit<ComponentEntry, 'schema_version'> {
  schema_version?: string
}

/** 平台可接受的域枚举。 */
export const CATALOG_DOMAINS = ['platform', 'teacher', 'research', 'student'] as const

/** 可接受的渲染器种类。 */
export const RENDERER_KINDS = [
  'PLATFORM_PRIMITIVE',
  'REGISTERED_DOMAIN_COMPONENT',
  'TEXT_FALLBACK',
] as const

/** 风险等级。 */
export const RISK_LEVELS = ['LOW', 'MEDIUM', 'HIGH'] as const

/** 渲染器声明。 */
export interface RendererSpecLite {
  kind: (typeof RENDERER_KINDS)[number]
  component_name: string
  arbitrary_html?: boolean
  arbitrary_css?: boolean
  arbitrary_js?: boolean
}

/** 降级策略。 */
export interface FallbackSpecLite {
  strategy: 'TEXT_SUMMARY' | 'PLACEHOLDER' | 'HIDE'
  message_i18n_key?: string
}

/** 目录（版本化注册表）。 */
export interface Catalog {
  readonly catalogId: string
  readonly version: string
  readonly entries: Map<string, CatalogEntry>
}

const SEMVER_RE = /^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$/
const TYPE_RE = /^[A-Z][A-Za-z0-9]*$/

export function createCatalog(catalogId = 'platform-component-catalog', version = '1.0.0'): Catalog {
  return { catalogId, version, entries: new Map() }
}

/** 校验单个条目。返回错误信息数组（空 = 通过）。 */
export function validateEntry(e: CatalogEntry): string[] {
  const errors: string[] = []

  if (!e || typeof e !== 'object') return ['entry 不是对象']

  if (typeof e.component_type !== 'string' || !TYPE_RE.test(e.component_type)) {
    errors.push(`component_type 非法：${String(e.component_type)}（应为 PascalCase）`)
  }
  if (typeof e.version !== 'string' || !SEMVER_RE.test(e.version)) {
    errors.push(`${e.component_type}: version 必须为 semver，实际 ${String(e.version)}`)
  }
  if (!CATALOG_DOMAINS.includes(e.domain as never)) {
    errors.push(`${e.component_type}: 未知 domain ${String(e.domain)}`)
  }
  if (!RISK_LEVELS.includes(e.risk as never)) {
    errors.push(`${e.component_type}: 未知 risk ${String(e.risk)}`)
  }

  const r = e.renderer as RendererSpecLite | undefined
  if (!r || typeof r !== 'object') {
    errors.push(`${e.component_type}: 缺少 renderer`)
  } else {
    if (!RENDERER_KINDS.includes(r.kind as never)) {
      errors.push(`${e.component_type}: 未知 renderer.kind ${String(r.kind)}`)
    }
    if (typeof r.component_name !== 'string' || r.component_name.length === 0) {
      errors.push(`${e.component_type}: renderer.component_name 为空`)
    }
    // ⚠️ 三条硬性禁止
    if (r.arbitrary_html === true) errors.push(`${e.component_type}: 禁止 arbitrary_html`)
    if (r.arbitrary_css === true) errors.push(`${e.component_type}: 禁止 arbitrary_css`)
    if (r.arbitrary_js === true) errors.push(`${e.component_type}: 禁止 arbitrary_js`)
  }

  const fb = e.fallback as FallbackSpecLite | undefined
  if (!fb || typeof fb !== 'object') {
    errors.push(`${e.component_type}: 缺少 fallback（未知类型必须可降级）`)
  } else if (!['TEXT_SUMMARY', 'PLACEHOLDER', 'HIDE'].includes(fb.strategy)) {
    errors.push(`${e.component_type}: 未知 fallback.strategy ${String(fb.strategy)}`)
  }

  const a = e.accessibility as AccessibilitySpec | undefined
  if (!a || typeof a !== 'object' || typeof a.role !== 'string' || a.role.length === 0) {
    errors.push(`${e.component_type}: 缺少 accessibility.role`)
  }

  if (e.props_schema === undefined || e.props_schema === null) {
    errors.push(`${e.component_type}: 缺少 props_schema`)
  }
  if (e.event_schema === undefined || e.event_schema === null) {
    errors.push(`${e.component_type}: 缺少 event_schema`)
  }

  return errors
}

/** 校验整份目录。 */
export function validateCatalog(entries: readonly CatalogEntry[]): {
  ok: boolean
  errors: string[]
} {
  const errors: string[] = []
  const seen = new Set<string>()

  if (!Array.isArray(entries) || entries.length === 0) {
    return { ok: false, errors: ['目录为空'] }
  }

  for (const e of entries) {
    errors.push(...validateEntry(e))
    const t = e?.component_type
    if (typeof t === 'string') {
      if (seen.has(t)) errors.push(`重复 component_type：${t}`)
      seen.add(t)
    }
  }

  return { ok: errors.length === 0, errors }
}

/**
 * 注册条目。返回 `false` 表示被拒绝（重复 / 校验不过）。
 * **不覆盖**已存在的同类目。
 */
export function registerComponent(catalog: Catalog, e: CatalogEntry): boolean {
  const errors = validateEntry(e)
  if (errors.length > 0) return false
  // 契约类型带 index signature，字段运行时可能是任意值；此处显式收窄为 string。
  const type = typeof e.component_type === 'string' ? e.component_type : ''
  if (type === '') return false
  if (catalog.entries.has(type)) return false
  catalog.entries.set(type, e)
  return true
}

/** 查找条目；未登记返回 `undefined`（调用方负责降级）。 */
export function lookupComponent(catalog: Catalog, type: string): CatalogEntry | undefined {
  if (typeof type !== 'string') return undefined
  return catalog.entries.get(type)
}

// ── 渲染器 ─────────────────────────────────────────────────────────

/** 模型/服务端下发的 catalog 节点（仅允许 type + props）。 */
export interface CatalogNode {
  type: string
  props?: Record<string, unknown>
  /** 禁止字段（出现即拒绝） */
  html?: unknown
  css?: unknown
  js?: unknown
  script?: unknown
  innerHTML?: unknown
}

/** 渲染结果。 */
export type RenderResult =
  | {
      kind: 'COMPONENT'
      componentName: string
      props: Record<string, unknown>
      /** 恒为 false —— 显式表达"任何组件都不得渲染原始标记"。 */
      allowRawMarkup: false
      entry: CatalogEntry
    }
  | { kind: 'FALLBACK'; strategy: string; reason: string; type: string }
  | { kind: 'REJECTED'; reason: string }

/** 出现任一字段即视为模型试图注入标记 → 拒绝。 */
const FORBIDDEN_NODE_FIELDS = ['html', 'css', 'js', 'script', 'innerHTML'] as const

/**
 * 将 catalog 节点解析为可渲染描述。
 *
 * - 携带禁止字段 → `REJECTED`；
 * - 类型未登记 → `FALLBACK`（用户体验降级，而不是渲染中断）；
 * - 正常 → `COMPONENT`，props 原样透传为**数据**（永不当作标记/脚本）。
 */
export function renderCatalogNode(catalog: Catalog, node: CatalogNode): RenderResult {
  if (!node || typeof node !== 'object' || typeof node.type !== 'string') {
    return { kind: 'REJECTED', reason: 'INVALID_NODE' }
  }

  const rawNode = node as unknown as Record<string, unknown>
  for (const field of FORBIDDEN_NODE_FIELDS) {
    if (field in node && rawNode[field] !== undefined) {
      return { kind: 'REJECTED', reason: `FORBIDDEN_FIELD:${field}` }
    }
  }

  const entry = lookupComponent(catalog, node.type)
  if (!entry) {
    return { kind: 'FALLBACK', strategy: 'PLACEHOLDER', reason: 'UNKNOWN_TYPE', type: node.type }
  }

  const props =
    node.props && typeof node.props === 'object' ? (node.props as Record<string, unknown>) : {}

  const renderer = entry.renderer as RendererSpecLite
  return {
    kind: 'COMPONENT',
    componentName: renderer.component_name,
    props,
    allowRawMarkup: false,
    entry,
  }
}
