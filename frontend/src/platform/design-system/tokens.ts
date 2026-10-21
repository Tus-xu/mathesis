/**
 * P01 · 平台设计 token 单一入口。
 *
 * 权威来源：
 * - 色值：`DECISIONS.md §9` 视觉设计系统（主蓝 `#2563EB` 等）；
 * - 契约形状：P00 `PlatformDesignTokenContract`
 *   （见 `src/features/platform-contracts/types/platform-contracts.ts`）。
 *
 * 两类 tier（契约要求）：
 * - `FOUNDATION`：基础色阶/尺寸，**唯一**允许出现 `literal_value` 的层级；
 * - `SEMANTIC`：语义槽位，必须 `alias_of` 某个 FOUNDATION token。
 *
 * 铁律（frontend AGENTS.md + AP-08 RAW_COLOR_IN_COMPONENT）：
 * - 组件内**禁止**新增 raw hex / rgb / shadow，一律 `var(--ma-*)`；
 * - 本文件是平台层**唯一**允许书写字面色值的位置；
 * - `business_page_hex_included` 必须为 `false`（不得内联业务页颜色）。
 *
 * ⚠️ 已知视觉漂移（详见 P01/EVIDENCE-INDEX.md）：
 * 存量 `src/styles/tokens.css`（主色 `#4f46e5`）与 `teacher-tokens.css`（`#6366f1`）
 * 与 DECISIONS §9 的 `#2563EB` 不一致 —— 由科研端/教师端改版主动替换所致。
 * P01 **不改动存量视觉**（避免超范围大面积变更），改以兼容别名桥接，
 * 并把收敛登记为待办（见 `LEGACY_TOKEN_ALIASES`）。
 */

/** token 类别（契约要求的 9 类，`research-accent` 可选）。 */
export const DESIGN_TOKEN_CATEGORIES = [
  'text',
  'surface',
  'action',
  'status',
  'focus',
  'elevation',
  'spacing',
  'radius',
  'typography',
  'research-accent',
] as const

export type DesignTokenCategory = (typeof DESIGN_TOKEN_CATEGORIES)[number]

/** 契约要求的 9 类（不含可选的 research-accent）。 */
export const REQUIRED_DESIGN_TOKEN_CATEGORIES = [
  'text',
  'surface',
  'action',
  'status',
  'focus',
  'elevation',
  'spacing',
  'radius',
  'typography',
] as const

export type DesignTokenTier = 'FOUNDATION' | 'SEMANTIC'

export type DesignTokenValueType =
  | 'color'
  | 'alias'
  | 'shadow'
  | 'dimension'
  | 'font-family'

/** 契约形状的单条 token。 */
export interface DesignToken {
  name: string
  tier: DesignTokenTier
  category: DesignTokenCategory
  value_type: DesignTokenValueType
  literal_value?: string
  alias_of?: string
}

/**
 * 平台 token 表（**唯一**字面色值来源）。
 *
 * 命名：FOUNDATION 用 `--color-<hue>-<step>` / `--space-<n>` / `--radius-<n>`；
 * SEMANTIC 用 `--<category>-<role>`，值一律 `alias_of` FOUNDATION。
 */
export const PLATFORM_TOKENS: readonly DesignToken[] = Object.freeze([
  // ── FOUNDATION · color ──────────────────────────────────────────
  { name: '--color-blue-600', tier: 'FOUNDATION', category: 'action', value_type: 'color', literal_value: '#2563EB' },
  { name: '--color-blue-50', tier: 'FOUNDATION', category: 'action', value_type: 'color', literal_value: '#EFF6FF' },
  { name: '--color-white-000', tier: 'FOUNDATION', category: 'surface', value_type: 'color', literal_value: '#FFFFFF' },
  { name: '--color-slate-50', tier: 'FOUNDATION', category: 'surface', value_type: 'color', literal_value: '#F8FAFC' },
  { name: '--color-slate-100', tier: 'FOUNDATION', category: 'surface', value_type: 'color', literal_value: '#F1F5F9' },
  { name: '--color-slate-800', tier: 'FOUNDATION', category: 'text', value_type: 'color', literal_value: '#1E293B' },
  { name: '--color-slate-500', tier: 'FOUNDATION', category: 'text', value_type: 'color', literal_value: '#64748B' },
  { name: '--color-slate-400', tier: 'FOUNDATION', category: 'text', value_type: 'color', literal_value: '#94A3B8' },
  /* WP-K / 可达性：原有 text 基色在浅底上达不到 WCAG AA。
   * 实测对比度（白 / #F8FAFC / #F1F5F9）：#94A3B8 = 2.56 / 2.45 / 2.34（正文级文字全部不达标）。
   * 新增一级仍然"明显更淡、但读得清"的中间色：#5B6B7F = 5.45 / 5.21 / 4.97（AA 全达标），
   * 用于 `--text-muted`（状态文案 / 空态说明 / 辅助说明等**真实内容**）。
   * `--color-slate-400` 保留，只允许用于非文字装饰（分隔线、占位图标等）。 */
  { name: '--color-slate-550', tier: 'FOUNDATION', category: 'text', value_type: 'color', literal_value: '#5B6B7F' },
  { name: '--color-slate-600', tier: 'FOUNDATION', category: 'text', value_type: 'color', literal_value: '#475569' },
  { name: '--color-green-500', tier: 'FOUNDATION', category: 'status', value_type: 'color', literal_value: '#22C55E' },
  { name: '--color-amber-500', tier: 'FOUNDATION', category: 'status', value_type: 'color', literal_value: '#F59E0B' },
  { name: '--color-red-500', tier: 'FOUNDATION', category: 'status', value_type: 'color', literal_value: '#EF4444' },
  { name: '--color-violet-500', tier: 'FOUNDATION', category: 'status', value_type: 'color', literal_value: '#8B5CF6' },
  { name: '--color-cyan-500', tier: 'FOUNDATION', category: 'status', value_type: 'color', literal_value: '#06B6D4' },
  { name: '--color-slate-200', tier: 'FOUNDATION', category: 'surface', value_type: 'color', literal_value: '#E2E8F0' },
  { name: '--color-slate-300', tier: 'FOUNDATION', category: 'surface', value_type: 'color', literal_value: '#CBD5E1' },

  // ── FOUNDATION · elevation / spacing / radius / typography ──────
  { name: '--elevation-none', tier: 'FOUNDATION', category: 'elevation', value_type: 'shadow', literal_value: 'none' },
  { name: '--elevation-sm', tier: 'FOUNDATION', category: 'elevation', value_type: 'shadow', literal_value: '0 1px 2px rgba(15,23,42,.06)' },
  { name: '--elevation-md', tier: 'FOUNDATION', category: 'elevation', value_type: 'shadow', literal_value: '0 4px 12px rgba(15,23,42,.08)' },
  { name: '--elevation-lg', tier: 'FOUNDATION', category: 'elevation', value_type: 'shadow', literal_value: '0 12px 32px rgba(15,23,42,.12)' },

  { name: '--space-1', tier: 'FOUNDATION', category: 'spacing', value_type: 'dimension', literal_value: '4px' },
  { name: '--space-2', tier: 'FOUNDATION', category: 'spacing', value_type: 'dimension', literal_value: '8px' },
  { name: '--space-3', tier: 'FOUNDATION', category: 'spacing', value_type: 'dimension', literal_value: '12px' },
  { name: '--space-4', tier: 'FOUNDATION', category: 'spacing', value_type: 'dimension', literal_value: '16px' },
  /* RV16 X4 存量漂移修复：既有组件已引用 `--ma-space-5/8`，tokens.ts 未定义。加法式补齐。 */
  { name: '--space-5', tier: 'FOUNDATION', category: 'spacing', value_type: 'dimension', literal_value: '20px' },
  { name: '--space-6', tier: 'FOUNDATION', category: 'spacing', value_type: 'dimension', literal_value: '24px' },
  { name: '--space-8', tier: 'FOUNDATION', category: 'spacing', value_type: 'dimension', literal_value: '32px' },

  { name: '--radius-sm', tier: 'FOUNDATION', category: 'radius', value_type: 'dimension', literal_value: '4px' },
  { name: '--radius-md', tier: 'FOUNDATION', category: 'radius', value_type: 'dimension', literal_value: '8px' },
  { name: '--radius-lg', tier: 'FOUNDATION', category: 'radius', value_type: 'dimension', literal_value: '12px' },
  { name: '--radius-full', tier: 'FOUNDATION', category: 'radius', value_type: 'dimension', literal_value: '999px' },
  /* RV16 X3：卡片圆角 14px（加法式，`--radius-lg`(12px) 保持不动，不影响他端）。 */
  { name: '--radius-card', tier: 'FOUNDATION', category: 'radius', value_type: 'dimension', literal_value: '14px' },
  /* RV16 X4：补齐既有组件已引用但未定义的 `--ma-radius-inner/btn/tag`（存量漂移修复）。 */
  { name: '--radius-inner', tier: 'FOUNDATION', category: 'radius', value_type: 'dimension', literal_value: '12px' },
  { name: '--radius-btn', tier: 'FOUNDATION', category: 'radius', value_type: 'dimension', literal_value: '8px' },
  { name: '--radius-tag', tier: 'FOUNDATION', category: 'radius', value_type: 'dimension', literal_value: '6px' },

  { name: '--font-family-ui', tier: 'FOUNDATION', category: 'typography', value_type: 'font-family', literal_value: 'PingFang SC, Microsoft YaHei, Inter, system-ui, sans-serif' },
  { name: '--font-family-mono', tier: 'FOUNDATION', category: 'typography', value_type: 'font-family', literal_value: 'JetBrains Mono, SFMono-Regular, Consolas, monospace' },
  /* RV16 X4：`--ma-font-sans` 已被既有组件引用但未定义，补齐为 UI 字体栈别名。 */
  { name: '--font-sans', tier: 'FOUNDATION', category: 'typography', value_type: 'font-family', literal_value: 'PingFang SC, Microsoft YaHei, Inter, system-ui, sans-serif' },
  { name: '--font-size-body', tier: 'FOUNDATION', category: 'typography', value_type: 'dimension', literal_value: '14px' },
  { name: '--font-size-title', tier: 'FOUNDATION', category: 'typography', value_type: 'dimension', literal_value: '16px' },
  { name: '--font-size-display', tier: 'FOUNDATION', category: 'typography', value_type: 'dimension', literal_value: '20px' },
  /* RV16 X2：Display-L 42px（加法式，`--font-size-display`(20px) 保持不动，避免影响教师/学生两端）。 */
  { name: '--font-size-display-lg', tier: 'FOUNDATION', category: 'typography', value_type: 'dimension', literal_value: '42px' },
  /* RV16 §2.2 排版阶梯：Title 22px / Caption 12px。 */
  { name: '--font-size-heading', tier: 'FOUNDATION', category: 'typography', value_type: 'dimension', literal_value: '22px' },
  { name: '--font-size-caption', tier: 'FOUNDATION', category: 'typography', value_type: 'dimension', literal_value: '12px' },

  // ── SEMANTIC（一律 alias，不得直接写字面色值）──────────────────
  { name: '--surface-default', tier: 'SEMANTIC', category: 'surface', value_type: 'alias', alias_of: '--color-white-000' },
  { name: '--surface-page', tier: 'SEMANTIC', category: 'surface', value_type: 'alias', alias_of: '--color-slate-50' },
  { name: '--surface-subtle', tier: 'SEMANTIC', category: 'surface', value_type: 'alias', alias_of: '--color-slate-100' },
  { name: '--surface-border', tier: 'SEMANTIC', category: 'surface', value_type: 'alias', alias_of: '--color-slate-200' },
  { name: '--surface-border-strong', tier: 'SEMANTIC', category: 'surface', value_type: 'alias', alias_of: '--color-slate-300' },

  { name: '--text-primary', tier: 'SEMANTIC', category: 'text', value_type: 'alias', alias_of: '--color-slate-800' },
  /* `--text-body` 的取值由 DECISIONS §9 冻结契约钉死（test/platform/p01DesignAndLayout.test.ts），
     不得改动；"正文色放在 --surface-subtle 上只有 4.34:1" 由组件层解决（见 WP-K 报告 §视觉修复）。 */
  { name: '--text-body', tier: 'SEMANTIC', category: 'text', value_type: 'alias', alias_of: '--color-slate-500' },
  { name: '--text-muted', tier: 'SEMANTIC', category: 'text', value_type: 'alias', alias_of: '--color-slate-550' },

  { name: '--action-primary', tier: 'SEMANTIC', category: 'action', value_type: 'alias', alias_of: '--color-blue-600' },
  { name: '--action-primary-soft', tier: 'SEMANTIC', category: 'action', value_type: 'alias', alias_of: '--color-blue-50' },

  { name: '--status-success', tier: 'SEMANTIC', category: 'status', value_type: 'alias', alias_of: '--color-green-500' },
  { name: '--status-warning', tier: 'SEMANTIC', category: 'status', value_type: 'alias', alias_of: '--color-amber-500' },
  { name: '--status-danger', tier: 'SEMANTIC', category: 'status', value_type: 'alias', alias_of: '--color-red-500' },
  { name: '--status-ai', tier: 'SEMANTIC', category: 'status', value_type: 'alias', alias_of: '--color-violet-500' },
  { name: '--status-accent', tier: 'SEMANTIC', category: 'status', value_type: 'alias', alias_of: '--color-cyan-500' },

  { name: '--focus-ring', tier: 'SEMANTIC', category: 'focus', value_type: 'alias', alias_of: '--color-blue-600' },

  { name: '--elevation-card', tier: 'SEMANTIC', category: 'elevation', value_type: 'alias', alias_of: '--elevation-sm' },
  { name: '--elevation-popover', tier: 'SEMANTIC', category: 'elevation', value_type: 'alias', alias_of: '--elevation-md' },
  { name: '--elevation-modal', tier: 'SEMANTIC', category: 'elevation', value_type: 'alias', alias_of: '--elevation-lg' },

  { name: '--spacing-inline', tier: 'SEMANTIC', category: 'spacing', value_type: 'alias', alias_of: '--space-2' },
  { name: '--spacing-block', tier: 'SEMANTIC', category: 'spacing', value_type: 'alias', alias_of: '--space-4' },

  { name: '--radius-control', tier: 'SEMANTIC', category: 'radius', value_type: 'alias', alias_of: '--radius-md' },
  /* `--radius-card` 于 RV16 由 SEMANTIC 升为 FOUNDATION(14px)（X3）；此处不再重复定义，
     否则 validateTokenContract() 会报「重复 token 名」。 */

  { name: '--typography-body', tier: 'SEMANTIC', category: 'typography', value_type: 'alias', alias_of: '--font-size-body' },
  { name: '--typography-title', tier: 'SEMANTIC', category: 'typography', value_type: 'alias', alias_of: '--font-size-title' },
  { name: '--typography-display-lg', tier: 'SEMANTIC', category: 'typography', value_type: 'alias', alias_of: '--font-size-display-lg' },
  { name: '--typography-caption', tier: 'SEMANTIC', category: 'typography', value_type: 'alias', alias_of: '--font-size-caption' },

  // 可选类别：科研端强调色（P00 允许；**默认沿用平台主色**，
  // 不引入新色板、不改全局紫色主题）
  { name: '--research-accent', tier: 'SEMANTIC', category: 'research-accent', value_type: 'alias', alias_of: '--color-blue-600' },
])

/** 字体栈（契约 `font_stacks`）。 */
export const PLATFORM_FONT_STACKS = Object.freeze([
  {
    name: 'ui-cn',
    stack: ['PingFang SC', 'Microsoft YaHei', 'Inter', 'Arial', 'system-ui'],
    usage: 'global',
  },
])

/** 契约要求的 token 入口模块。 */
export const TOKEN_ENTRY_MODULE = 'src/platform/design-system/tokens.css'

// ── 校验 ────────────────────────────────────────────────────────────

const HEX_RE = /^#[0-9a-fA-F]{6}$/
const DIMENSION_RE = /^\d+(\.\d+)?(px|rem|em|%)$/
const FOUNDATION_NAME_RE = /^--(color|space|radius|font|elevation)-[a-z0-9-]+$/

/**
 * 阴影合法性判定。
 *
 * 采用"分段 + 逐 token 校验"而非一条长正则：
 * 阴影语法允许无单位 0、小数省略前导 0（`.06`）、颜色可为 rgba/hsl/hex，
 * 用单条正则容易在边界上误判（本项目已踩过两次）。
 *
 * 合法形态：`none`，或「1~4 个长度（可含无单位 0） + 可选颜色」。
 */
function isValidShadow(value: string): boolean {
  if (value === 'none') return true

  const LENGTH = /^(?:0|-?\d+(?:\.\d+)?(?:px|rem|em))$/
  const COLOR = /^(?:rgba?\([^)]*\)|hsla?\([^)]*\)|#[0-9a-fA-F]{3,8})$/

  const parts = value.trim().split(/\s+/)
  if (parts.length < 2 || parts.length > 5) return false

  // 末尾若为颜色则剥离
  let lengths = parts
  if (COLOR.test(parts[parts.length - 1])) {
    lengths = parts.slice(0, -1)
  }
  if (lengths.length < 2 || lengths.length > 4) return false

  return lengths.every((p) => LENGTH.test(p))
}

/** 按类别取 token。 */
export function tokensByCategory(category: DesignTokenCategory): DesignToken[] {
  return PLATFORM_TOKENS.filter((t) => t.category === category)
}

/**
 * 校验 token 契约。
 *
 * 检查项（对齐 P00 `PlatformDesignTokenContract`）：
 * 1. 9 类必需类别齐备（`research-accent` 可选）；
 * 2. `FOUNDATION` 必须有合法 `literal_value`，且**不得**有 `alias_of`；
 * 3. `SEMANTIC` 必须 `alias_of`，且目标必须**存在**且为 `FOUNDATION`；
 * 4. `literal_value` 类型与 `value_type` 匹配；
 * 5. 名称唯一。
 */
export function validateTokenContract(): string[] {
  const problems: string[] = []
  const byName = new Map<string, DesignToken>()

  for (const t of PLATFORM_TOKENS) {
    if (byName.has(t.name)) problems.push(`重复 token 名：${t.name}`)
    byName.set(t.name, t)
  }

  // 1) 类别齐备
  const present = new Set(PLATFORM_TOKENS.map((t) => t.category))
  for (const required of REQUIRED_DESIGN_TOKEN_CATEGORIES) {
    if (!present.has(required)) problems.push(`缺少必需类别：${required}`)
  }

  for (const t of PLATFORM_TOKENS) {
    // 2) FOUNDATION
    if (t.tier === 'FOUNDATION') {
      if (!t.literal_value) problems.push(`${t.name}: FOUNDATION 必须有 literal_value`)
      if (t.alias_of) problems.push(`${t.name}: FOUNDATION 不得有 alias_of`)
      if (!FOUNDATION_NAME_RE.test(t.name)) {
        problems.push(`${t.name}: FOUNDATION 命名不符合 --<kind>-<scale> 约定`)
      }
    }

    // 3) SEMANTIC
    if (t.tier === 'SEMANTIC') {
      if (!t.alias_of) {
        problems.push(`${t.name}: SEMANTIC 必须 alias_of 某个 FOUNDATION token`)
      } else {
        const target = byName.get(t.alias_of)
        if (!target) problems.push(`${t.name}: alias_of 指向不存在的 token ${t.alias_of}`)
        else if (target.tier !== 'FOUNDATION') {
          problems.push(`${t.name}: alias_of 必须指向 FOUNDATION（实际 ${target.tier}）`)
        }
      }
      if (t.literal_value) {
        problems.push(`${t.name}: SEMANTIC 不得直接写 literal_value（须走 alias）`)
      }
    }

    // 4) 值类型校验
    if (t.literal_value) {
      if (t.value_type === 'color' && !HEX_RE.test(t.literal_value)) {
        problems.push(`${t.name}: color 值非法 ${t.literal_value}`)
      }
      if (t.value_type === 'dimension' && !DIMENSION_RE.test(t.literal_value)) {
        problems.push(`${t.name}: dimension 值非法 ${t.literal_value}`)
      }
      if (t.value_type === 'shadow' && !isValidShadow(t.literal_value)) {
        problems.push(`${t.name}: shadow 值非法 ${t.literal_value}`)
      }
    }
  }

  return problems
}

// ── 导出 ────────────────────────────────────────────────────────────

/** name → literal_value 解析（跟随 alias 直到 FOUNDATION）。 */
export function resolveLiteral(name: string): string | undefined {
  const byName = new Map(PLATFORM_TOKENS.map((t) => [t.name, t]))
  let cur = byName.get(name)
  let guard = 0
  while (cur && cur.value_type === 'alias' && cur.alias_of && guard < 10) {
    cur = byName.get(cur.alias_of)
    guard += 1
  }
  return cur?.literal_value
}

/**
 * 产出组件用的 CSS 变量映射（`--ma-*` 前缀）。
 *
 * 组件只应引用这些变量；`--ma-*` 是本平台对外的稳定契约面。
 */
export function tokenCssVars(): Record<string, string> {
  const out: Record<string, string> = {}
  const byName = new Map(PLATFORM_TOKENS.map((t) => [t.name, t]))
  for (const t of PLATFORM_TOKENS) {
    const key = `--ma-${t.name.replace(/^--/, '')}`
    if (t.value_type === 'alias' && t.alias_of) {
      // 语义层指向**平台 CSS 变量**（而非直接指向 foundation 变量），
      // 保证组件只依赖 --ma-* 这一层接口。
      const target = byName.get(t.alias_of)
      out[key] = target ? `var(--ma-${target.name.replace(/^--/, '')})` : `var(${t.alias_of})`
    } else {
      out[key] = t.literal_value as string
    }
  }
  return out
}

/**
 * 存量 token 兼容别名（平台变量 → 指向平台变量）。
 *
 * 用途：让存量样式的变量跟随平台 token，**不修改存量文件本身**。
 * ⚠️ 这些是**过渡层**，收敛完成后应删除（见 P01/DESIGN-TOKEN-MAP.md）。
 */
export const LEGACY_TOKEN_ALIASES: Readonly<Record<string, string>> = Object.freeze({
  '--teach-color-primary': 'var(--ma-action-primary)',
  '--teacher-primary': 'var(--ma-action-primary)',
  '--tv3-color-primary': 'var(--ma-action-primary)',
  '--re-color-primary': 'var(--ma-action-primary)',
  '--brand': 'var(--ma-action-primary)',
  '--brand-soft': 'var(--ma-action-primary-soft)',
})
