/**
 * P01 · 工作区布局契约（运行时描述 + 自校验）。
 *
 * 契约来源：P00 `WorkspaceLayoutContract`
 * （权威 Pydantic：`services/teacher-platform/app/platform_contracts/workspace_layout.py`）。
 *
 * 用途：
 * - `WorkspaceLayout.vue` 依据本描述渲染 named slots；
 * - 校验函数把 P00 契约规则**在运行时也生效**（不只靠后端契约测试），
 *   使"布局漂移"在开发期即被拦截。
 *
 * 关键事实（以权威代码为准，P00 文档表格有误，详见 CONTRACT-DRIFT-FINDINGS.md）：
 * - `PLATFORM_REGION_ROLES` 实为 7 值，含 `artifact-canvas` / `evidence-rail` / `toolbar`；
 * - 三大必备 region 为 `navigation-or-conversation` + `artifact-canvas` + `evidence-rail`。
 */

/** 与权威代码一致的 region role 枚举（7 值）。 */
export const PLATFORM_REGION_ROLES = [
  'navigation',
  'navigation-or-conversation',
  'conversation',
  'artifact-canvas',
  'evidence-rail',
  'toolbar',
  'status',
] as const

export type PlatformRegionRole = (typeof PLATFORM_REGION_ROLES)[number]

/** 折叠行为（与权威代码一致）。 */
export const COLLAPSE_BEHAVIOURS = [
  'COLLAPSE_TO_RAIL',
  'OVERLAY_DRAWER',
  'HIDE',
  'STACK',
  'KEEP',
] as const

export type CollapseBehaviour = (typeof COLLAPSE_BEHAVIOURS)[number]

/** 三大必备 region（与权威代码 `_must_have_core_regions` 一致）。 */
export const REQUIRED_CORE_REGIONS = [
  'navigation-or-conversation',
  'artifact-canvas',
  'evidence-rail',
] as const

export interface RegionSpecLite {
  /** 必须取自 `PLATFORM_REGION_ROLES`。 */
  role: PlatformRegionRole
  /** kebab-case，唯一；作为 Vue slot 名。 */
  slot_name: string
  /** 展示顺序。 */
  order: number
  collapsible?: boolean
  default_collapsed?: boolean
  min_width_px?: number | null
  max_width_px?: number | null
  scrollable?: boolean
}

export interface ResponsiveRuleLite {
  breakpoint_px: number
  name: string
  /** 键**必须**是已登记 role（不得用 slot_name）。 */
  collapse: Partial<Record<PlatformRegionRole, CollapseBehaviour>>
}

export interface WorkspaceLayoutShape {
  layout_id: string
  regions: RegionSpecLite[]
  responsive_rules: ResponsiveRuleLite[]
  conversation_first: boolean
  stepper_allowed: boolean
  wizard_allowed: boolean
  domain_artifact_enum_embedded: boolean
}

const SLOT_RE = /^[a-z][a-z0-9-]*$/
const LAYOUT_ID_RE = /^[a-z][a-z0-9-]*$/

/**
 * 构造平台布局描述。
 *
 * 域无关：这里**不出现**任何 Teacher/Research 业务区域名。
 * 域通过 manifest 往 slots 里填内容，而不是改布局。
 */
export function buildWorkspaceLayout(): WorkspaceLayoutShape {
  return {
    layout_id: 'platform-workspace',
    regions: [
      // 导航与对话共享位置 —— conversation-first 的核心：
      // 导航不是独立一等区域，而是与对话同处一槽
      { role: 'navigation-or-conversation', slot_name: 'left', order: 0, collapsible: true },
      { role: 'artifact-canvas', slot_name: 'center', order: 1, collapsible: false },
      { role: 'evidence-rail', slot_name: 'right', order: 2, collapsible: true },
      { role: 'toolbar', slot_name: 'toolbar', order: 3, collapsible: false },
      { role: 'status', slot_name: 'status', order: 4, collapsible: false },
    ],
    responsive_rules: [
      {
        breakpoint_px: 1280,
        name: 'compact',
        collapse: { 'evidence-rail': 'OVERLAY_DRAWER' },
      },
      {
        breakpoint_px: 900,
        name: 'narrow',
        collapse: { 'navigation-or-conversation': 'COLLAPSE_TO_RAIL', 'evidence-rail': 'HIDE' },
      },
    ],
    conversation_first: true,
    stepper_allowed: false,
    wizard_allowed: false,
    domain_artifact_enum_embedded: false,
  }
}

/**
 * 自校验：把 P00 的 7 条机器校验规则在运行时复现。
 * 返回问题列表（空 = 通过）。
 */
export function validateLayoutContract(layout: WorkspaceLayoutShape | null | undefined): string[] {
  const problems: string[] = []
  if (!layout || typeof layout !== 'object') return ['layout 缺失']

  // 规则 1
  if (layout.conversation_first !== true) {
    problems.push('必须 conversation-first（conversation_first 必须为 true）')
  }
  // 规则 2 & 3
  if (layout.stepper_allowed !== false) problems.push('禁止 Stepper（stepper_allowed 必须为 false）')
  if (layout.wizard_allowed !== false) problems.push('禁止 Wizard（wizard_allowed 必须为 false）')
  // 规则 4
  if (layout.domain_artifact_enum_embedded !== false) {
    problems.push('不得内嵌域 artifact 枚举（domain_artifact_enum_embedded 必须为 false）')
  }

  // layout_id 形状
  if (typeof layout.layout_id !== 'string' || !LAYOUT_ID_RE.test(layout.layout_id)) {
    problems.push(`layout_id 非法：${String(layout.layout_id)}`)
  }

  // regions 形状 + 规则 5
  const regions = Array.isArray(layout.regions) ? layout.regions : []
  if (regions.length === 0) problems.push('regions 不得为空')

  const seenSlots = new Set<string>()
  for (const r of regions) {
    if (!PLATFORM_REGION_ROLES.includes(r.role as never)) {
      problems.push(`未登记的 region role：${String(r.role)}`)
    }
    if (typeof r.slot_name !== 'string' || !SLOT_RE.test(r.slot_name)) {
      problems.push(`slot_name 非法：${String(r.slot_name)}（应为 kebab-case）`)
    } else if (seenSlots.has(r.slot_name)) {
      problems.push(`重复 slot_name：${r.slot_name}`)
    } else {
      seenSlots.add(r.slot_name)
    }
    if (typeof r.order !== 'number' || !Number.isInteger(r.order) || r.order < 0 || r.order > 99) {
      problems.push(`${r.slot_name}: order 必须在 0..99`)
    }
    if (
      r.min_width_px != null &&
      r.max_width_px != null &&
      r.min_width_px > r.max_width_px
    ) {
      problems.push(`${r.slot_name}: min_width 不得大于 max_width`)
    }
  }

  // 规则 7：三大核心 region
  const roles = new Set(regions.map((r) => r.role))
  for (const required of REQUIRED_CORE_REGIONS) {
    if (!roles.has(required)) problems.push(`必须包含 ${required} region`)
  }

  // 规则 6：collapse 必须引用 role
  const rules = Array.isArray(layout.responsive_rules) ? layout.responsive_rules : []
  for (const rule of rules) {
    if (
      typeof rule.breakpoint_px !== 'number' ||
      rule.breakpoint_px < 320 ||
      rule.breakpoint_px > 3840
    ) {
      problems.push(`断点非法：${String(rule.breakpoint_px)}（须在 320..3840）`)
    }
    if (typeof rule.name !== 'string' || !SLOT_RE.test(rule.name)) {
      problems.push(`响应式规则名非法：${String(rule.name)}`)
    }
    const collapse = rule.collapse || {}
    for (const key of Object.keys(collapse)) {
      if (!PLATFORM_REGION_ROLES.includes(key as never)) {
        problems.push(
          `响应式规则引用了未登记的 region role ${key}（不得使用 slot_name 指代区域）`,
        )
      } else if (!COLLAPSE_BEHAVIOURS.includes(collapse[key as PlatformRegionRole] as never)) {
        problems.push(`未知折叠行为：${String(collapse[key as PlatformRegionRole])}`)
      }
    }
  }

  return problems
}
