/**
 * P01 · 平台设计系统导出入口。
 *
 * 组件只应通过本模块取 token（或使用 `--ma-*` CSS 变量），
 * 不得书写字面色值（由 `test/platform/p01AntiPatternScan.test.ts` 守门）。
 */
export {
  PLATFORM_TOKENS,
  PLATFORM_FONT_STACKS,
  TOKEN_ENTRY_MODULE,
  DESIGN_TOKEN_CATEGORIES,
  REQUIRED_DESIGN_TOKEN_CATEGORIES,
  tokenCssVars,
  tokensByCategory,
  validateTokenContract,
  resolveLiteral,
  type DesignToken,
  type DesignTokenCategory,
  type DesignTokenTier,
  type DesignTokenValueType,
} from './tokens'

export {
  NAIVE_THEME_OVERRIDES,
  buildThemeOverrides,
  LEGACY_TOKEN_ALIASES,
  type NaiveThemeOverrides,
} from './themeOverrides'
