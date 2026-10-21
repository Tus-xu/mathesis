/**
 * P01 · Naive UI 主题覆盖 + 存量 token 兼容别名。
 *
 * 目的：
 * 1. 把平台语义 token 映射到 Naive UI 的 `GlobalThemeOverrides`，
 *    使 Naive 组件与自研组件同源取色（不再各自硬编码）；
 * 2. 为存量 `src/styles/tokens.css` / `teacher-tokens.css` 提供**兼容别名**，
 *    避免一次性大改存量样式（最小 diff 原则），逐步收敛。
 *
 * 边界：本文件是 token 的**消费者**，取值一律来自 `./tokens`，不写字面新色值。
 */


/** Naive UI 主题覆盖结构（仅声明我们实际用到的部分，避免过度约束类型）。 */
export interface NaiveThemeOverrides {
  common: Record<string, string>
  Button?: Record<string, string>
  Card?: Record<string, string>
  Input?: Record<string, string>
  Tag?: Record<string, string>
  [key: string]: unknown
}

/**
 * 构建 Naive UI 主题覆盖。
 *
 * 全部取值来自 `PLATFORM_TOKENS`，保证"改 token 即改全站"。
 */
export function buildThemeOverrides(): NaiveThemeOverrides {
  // 取值一律走 --ma-* CSS 变量，保证 Naive 组件与自研组件**同源**
  // （改 tokens.ts 一处，全站跟随）。
  const c = {
    primary: 'var(--ma-action-primary)',
    primarySoft: 'var(--ma-action-primary-soft)',
    bg: 'var(--ma-surface-page)',
    bgSubtle: 'var(--ma-surface-subtle)',
    surface: 'var(--ma-surface-default)',
    text: 'var(--ma-text-primary)',
    textBody: 'var(--ma-text-body)',
    textMuted: 'var(--ma-text-muted)',
    success: 'var(--ma-status-success)',
    warning: 'var(--ma-status-warning)',
    danger: 'var(--ma-status-danger)',
    border: 'var(--ma-surface-border)',
  }
  const r = {
    sm: 'var(--ma-radius-sm)',
    md: 'var(--ma-radius-md)',
    lg: 'var(--ma-radius-lg)',
  }

  return {
    common: {
      primaryColor: c.primary,
      primaryColorHover: c.primary,
      primaryColorPressed: c.primary,
      primaryColorSuppl: c.primary,

      bodyColor: c.bg,
      cardColor: c.surface,
      modalColor: c.surface,
      popoverColor: c.surface,

      textColorBase: c.text,
      textColor1: c.text,
      textColor2: c.textBody,
      textColor3: c.textMuted,
      borderColor: c.border,

      successColor: c.success,
      warningColor: c.warning,
      errorColor: c.danger,
      infoColor: c.primary,

      borderRadius: r.md,
      borderRadiusSmall: r.sm,
    },
    Button: {
      borderRadiusMedium: r.md,
      borderRadiusSmall: r.sm,
    },
    Card: {
      borderRadius: r.lg,
    },
    Input: {
      borderRadius: r.md,
    },
    Tag: {
      borderRadius: r.sm,
    },
  }
}

/** 预构建实例（应用启动时直接使用）。 */
export const NAIVE_THEME_OVERRIDES: NaiveThemeOverrides = buildThemeOverrides()

/**
 * 存量 token 兼容别名。
 *
 * 键 = 存量 CSS 变量名（`src/styles/tokens.css` / `teacher-tokens.css` 中的写法）；
 * 值 = 对应的平台 CSS 变量（`--ma-*`）。
 *
 * 用途：让存量样式在不改动的前提下跟随平台 token 变化。
 * 用法：在 `src/platform/design-system/tokens.css` 中以
 *   `--teach-color-primary: var(--ma-color-primary);`
 * 形式声明别名。
 */
/* 兼容别名映射已收敛到 `./tokens` 的 LEGACY_TOKEN_ALIASES（单一事实源），
 * 此处仅做再导出，避免两处维护导致漂移。 */
export { LEGACY_TOKEN_ALIASES } from './tokens'
