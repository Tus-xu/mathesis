/**
 * P01 · 设计 token 与工作区布局契约测试。
 *
 * 覆盖：
 * - `src/platform/design-system/` 语义 token 单一入口（对齐 P00 `PlatformDesignTokenContract`）；
 * - 禁止组件内 raw hex（由反模式扫描守门）；
 * - `src/platform/workspace-layout/` 的 WorkspaceLayoutContract 运行时描述。
 */
/// <reference types="vite/client" />
import { describe, expect, it } from 'vitest'

import {
  PLATFORM_TOKENS,
  PLATFORM_FONT_STACKS,
  TOKEN_ENTRY_MODULE,
  DESIGN_TOKEN_CATEGORIES,
  REQUIRED_DESIGN_TOKEN_CATEGORIES,
  tokenCssVars,
  validateTokenContract,
  resolveLiteral,
  tokensByCategory,
} from '../../src/platform/design-system/tokens'
import {
  buildWorkspaceLayout,
  validateLayoutContract,
  PLATFORM_REGION_ROLES,
  REQUIRED_CORE_REGIONS,
} from '../../src/platform/workspace-layout/layoutContract'

describe('P01 · 设计 token 契约（PlatformDesignTokenContract）', () => {
  it('token 契约自校验通过（无问题项）', () => {
    expect(validateTokenContract()).toEqual([])
  })

  it('9 类必需类别齐备（research-accent 可选）', () => {
    const present = new Set(PLATFORM_TOKENS.map((t) => t.category))
    for (const required of REQUIRED_DESIGN_TOKEN_CATEGORIES) {
      expect(present.has(required)).toBe(true)
    }
    // 类别闭集校验：不得出现未登记类别
    for (const t of PLATFORM_TOKENS) {
      expect(DESIGN_TOKEN_CATEGORIES).toContain(t.category)
    }
  })

  it('SEMANTIC 层一律 alias，不写字面色值（保证主题可替换）', () => {
    const semantic = PLATFORM_TOKENS.filter((t) => t.tier === 'SEMANTIC')
    expect(semantic.length).toBeGreaterThan(0)
    for (const t of semantic) {
      expect(t.alias_of).toBeTruthy()
      expect(t.literal_value).toBeUndefined()
    }
  })

  it('alias 必须指向已存在的 FOUNDATION token（无悬空引用）', () => {
    const byName = new Map(PLATFORM_TOKENS.map((t) => [t.name, t]))
    for (const t of PLATFORM_TOKENS.filter((x) => x.value_type === 'alias')) {
      const target = byName.get(t.alias_of as string)
      expect(target, `${t.name} → ${t.alias_of}`).toBeTruthy()
      expect(target?.tier).toBe('FOUNDATION')
    }
  })

  it('平台主色（action）必须解析为 DECISIONS §9 规定的 #2563EB', () => {
    expect(resolveLiteral('--action-primary')?.toUpperCase()).toBe('#2563EB')
  })

  it('背景/文字语义 token 解析为 DECISIONS §9 规定值', () => {
    expect(resolveLiteral('--surface-page')?.toUpperCase()).toBe('#F8FAFC')
    expect(resolveLiteral('--text-primary')?.toUpperCase()).toBe('#1E293B')
    expect(resolveLiteral('--text-body')?.toUpperCase()).toBe('#64748B')
  })

  it('research-accent 默认沿用平台主色（不引入新色板）', () => {
    const accent = tokensByCategory('research-accent')
    expect(accent).toHaveLength(1)
    expect(resolveLiteral('--research-accent')?.toUpperCase()).toBe('#2563EB')
  })

  it('tokenCssVars 产出 --ma-* 变量，语义层指向 --ma-* 而非字面值', () => {
    const vars = tokenCssVars()
    expect(vars['--ma-action-primary']).toBe('var(--ma-color-blue-600)')
    expect(vars['--ma-color-blue-600'].toUpperCase()).toBe('#2563EB')
  })

  it('字体栈必须登记且含中文优先字体', () => {
    expect(PLATFORM_FONT_STACKS.length).toBeGreaterThan(0)
    expect(PLATFORM_FONT_STACKS[0].stack[0]).toBe('PingFang SC')
  })

  it('token 入口模块已登记', () => {
    expect(TOKEN_ENTRY_MODULE).toBe('src/platform/design-system/tokens.css')
  })
})

describe('P01 · WorkspaceLayoutContract 运行时描述', () => {
  it('平台布局必须含三大核心 region', () => {
    const layout = buildWorkspaceLayout()
    const roles = layout.regions.map((r) => r.role)
    for (const required of REQUIRED_CORE_REGIONS) expect(roles).toContain(required)
  })

  it('布局必须 conversation-first 且禁止 stepper/wizard', () => {
    const layout = buildWorkspaceLayout()
    expect(layout.conversation_first).toBe(true)
    expect(layout.stepper_allowed).toBe(false)
    expect(layout.wizard_allowed).toBe(false)
  })

  it('布局不得内嵌域 artifact 枚举', () => {
    expect(buildWorkspaceLayout().domain_artifact_enum_embedded).toBe(false)
  })

  it('所有 region.role 必须取自 PLATFORM_REGION_ROLES', () => {
    for (const r of buildWorkspaceLayout().regions) {
      expect(PLATFORM_REGION_ROLES).toContain(r.role)
    }
  })

  it('responsive_rules.collapse 的键必须是已登记 role（不得用 slot_name）', () => {
    for (const rule of buildWorkspaceLayout().responsive_rules) {
      for (const key of Object.keys(rule.collapse)) {
        expect(PLATFORM_REGION_ROLES).toContain(key)
      }
    }
  })

  it('slot_name 必须唯一且符合 kebab-case', () => {
    const names = buildWorkspaceLayout().regions.map((r) => r.slot_name)
    expect(new Set(names).size).toBe(names.length)
    for (const n of names) expect(n).toMatch(/^[a-z][a-z0-9-]*$/)
  })

  it('自校验函数对合法布局返回空', () => {
    expect(validateLayoutContract(buildWorkspaceLayout())).toEqual([])
  })

  it('自校验能捕获 conversation_first=false（范式漂移）', () => {
    const bad = { ...buildWorkspaceLayout(), conversation_first: false }
    expect(validateLayoutContract(bad).join()).toMatch(/conversation/i)
  })

  it('自校验能捕获缺 evidence-rail 的布局', () => {
    const base = buildWorkspaceLayout()
    const bad = { ...base, regions: base.regions.filter((r) => r.role !== 'evidence-rail') }
    expect(validateLayoutContract(bad).join()).toMatch(/evidence-rail/)
  })

  it('自校验能捕获 collapse 用 slot_name 而非 role', () => {
    const base = buildWorkspaceLayout()
    const bad = {
      ...base,
      responsive_rules: [{ breakpoint_px: 900, name: 'narrow', collapse: { left: 'HIDE' } }],
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
    } as any
    expect(validateLayoutContract(bad).join()).toMatch(/slot_name|未登记/)
  })
})
