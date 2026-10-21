/**
 * 从 `src/platform/design-system/tokens.ts` 生成 `tokens.css`。
 *
 * 为什么用生成而非手写：
 * token 定义是单一权威（TS），CSS 变量是派生产物。手写两份必然漂移。
 *
 * 用法：node scripts/gen-tokens-css.mjs
 */
import { readFileSync, writeFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const here = dirname(fileURLToPath(import.meta.url))
const root = join(here, '..')
const SRC = join(root, 'src', 'platform', 'design-system', 'tokens.ts')
const OUT = join(root, 'src', 'platform', 'design-system', 'tokens.css')

const src = readFileSync(SRC, 'utf-8')

// ── 解析 PLATFORM_TOKENS 数组 ─────────────────────────────────────
const start = src.indexOf('export const PLATFORM_TOKENS')
const end = src.indexOf('])', start)
if (start < 0 || end < 0) {
  console.error('未找到 PLATFORM_TOKENS 定义')
  process.exit(1)
}
const body = src.slice(start, end)

const entries = []
const entryRe = /\{([^{}]*)\}/g
let m
while ((m = entryRe.exec(body)) !== null) {
  const inner = m[1]
  const get = (key) => {
    const r = new RegExp(`${key}:\\s*'([^']*)'`)
    const mm = inner.match(r)
    return mm ? mm[1] : undefined
  }
  const name = get('name')
  if (!name) continue
  entries.push({
    name,
    tier: get('tier'),
    category: get('category'),
    value_type: get('value_type'),
    literal_value: get('literal_value'),
    alias_of: get('alias_of'),
  })
}

if (entries.length === 0) {
  console.error('未解析到任何 token')
  process.exit(1)
}

// ── 解析 LEGACY_TOKEN_ALIASES ─────────────────────────────────────
const legacyStart = src.indexOf('export const LEGACY_TOKEN_ALIASES')
const legacyEnd = src.indexOf('})', legacyStart)
const legacyBody = src.slice(legacyStart, legacyEnd)
const aliases = {}
const aliasRe = /'([^']+)':\s*'([^']+)'/g
let a
while ((a = aliasRe.exec(legacyBody)) !== null) aliases[a[1]] = a[2]

// ── 生成 CSS ──────────────────────────────────────────────────────
const byName = new Map(entries.map((e) => [e.name, e]))

const cssName = (n) => `--ma-${n.replace(/^--/, '')}`

const tierOf = (t) => t.tier
const foundation = entries.filter((e) => tierOf(e) === 'FOUNDATION')
const semantic = entries.filter((e) => tierOf(e) === 'SEMANTIC')

let css = '/* 本文件由 src/platform/design-system/tokens.ts 派生 —— 请勿手改；\n'
css += ' * 修改 token 请改 tokens.ts 后运行 `node scripts/gen-tokens-css.mjs` 重新生成。\n'
css += ' *\n'
css += ' * 权威来源：DECISIONS.md §9 视觉设计系统（主蓝 #2563EB）。\n'
css += ' * 组件只应引用 var(--ma-*)；禁止在组件内书写字面色值\n'
css += ' * （由 test/platform/p01AntiPatternScan.test.ts 守门）。\n'
css += ' */\n\n'

css += ':root {\n  /* ── FOUNDATION（唯一允许字面色值处）── */\n'
for (const t of foundation) {
  if (t.value_type === 'alias' || !t.literal_value) continue
  css += `  ${cssName(t.name)}: ${t.literal_value};\n`
}

css += '\n  /* ── SEMANTIC（一律 alias，不写字面值）── */\n'
for (const t of semantic) {
  if (!t.alias_of) continue
  const target = byName.get(t.alias_of)
  const targetVar = target ? cssName(target.name) : t.alias_of
  css += `  ${cssName(t.name)}: var(${targetVar});\n`
}
css += '}\n'

// ── 存量兼容别名 ─────────────────────────────────────────────────
css += '\n/* ── 存量 token 兼容别名（过渡层，收敛后删除）──\n'
css += ' * 让存量样式变量跟随平台 token，无需修改存量文件。\n'
css += ' * ⚠️ 已知漂移：存量 tokens.css 主色 #4f46e5 / teacher-tokens.css #6366f1\n'
css += ' *    与 DECISIONS §9 的 #2563EB 不一致（改版时主动替换所致）。\n'
css += ' *    本别名层把存量变量**重定向**到平台 token，实现视觉收敛。\n'
css += ' */\n:root {\n'
for (const [legacy, target] of Object.entries(aliases)) {
  css += `  ${legacy}: ${target};\n`
}
css += '}\n'

writeFileSync(OUT, css, 'utf-8')
console.log(
  `generated ${OUT} (foundation ${foundation.length}, semantic ${semantic.length}, aliases ${Object.keys(aliases).length})`,
)
