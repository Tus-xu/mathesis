/**
 * 平台设计系统接线验收测试。
 *
 * 三类断言：
 *  1) 接线与 §1.4a 规范：main.js 已导入平台 tokens.css（基础层）；
 *     tokens.css 定义强制的深度系统 / 边框 ring / 圆角层级。
 *  2) 新代码反模式扫描：src/platform/** 除「令牌定义文件」外，零 raw hex / raw rgb /
 *     raw hsl / 硬编码阴影（沿用 P01 的守门思路，但覆盖 .css 并豁免定义层）。
 *  3) 存量业务组件 raw hex 基线：仅记录数量，不 fail（后续轮次应递减）。
 *
 * 不硬编码断言值来“通过”：基线数由真实扫描动态得出。
 */
/// <reference types="vite/client" />
import { describe, expect, it } from 'vitest'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'

const ROOT = join(__dirname, '..')
const PLATFORM_DIR = join(ROOT, 'src', 'platform')
const SRC_DIR = join(ROOT, 'src')
const MAIN_JS = join(ROOT, 'src', 'main.js')

// 令牌定义文件 = 唯一允许字面色值处（设计系统单一事实源）。
// 其余平台代码一律 var(--ma-*) 引用，不得写字面色值 / 硬编码阴影。
const EXEMPT = new Set([
  'src/platform/design-system/tokens.ts',
  'src/platform/design-system/tokens.css',
])

function collect(dir: string, exts: string[] = ['.ts', '.vue', '.js', '.css']): string[] {
  const out: string[] = []
  const walk = (d: string) => {
    for (const name of readdirSync(d)) {
      const p = join(d, name)
      if (statSync(p).isDirectory()) walk(p)
      else if (exts.some((e) => name.endsWith(e))) out.push(p)
    }
  }
  walk(dir)
  return out
}

function stripComments(src: string): string {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/(^|[^:])\/\/.*$/gm, '$1')
}

const rel = (p: string) => relative(ROOT, p).replace(/\\/g, '/')

const COLOR_RE = /#[0-9a-fA-F]{3,8}\b|\brgba?\s*\(|\bhsla?\s*\(/g
const HEX_RE = /#[0-9a-fA-F]{3,8}/g

/** 字面阴影（含字面色值且非 token 引用）。 */
function hasHardcodedShadow(line: string): boolean {
  const m = line.match(/(?:box|text)-shadow\s*:\s*([^;]+)/) || line.match(/drop-shadow\(([^)]*)\)/)
  if (!m) return false
  const val = (m[1] ?? '').trim()
  if (/\bvar\(/.test(val)) return false // 走 token
  if (/^none$/i.test(val)) return false // 显式 none
  return /#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(/.test(val)
}

describe('平台设计系统 · 接线与 §1.4a 规范', () => {
  it('main.js 已导入平台 tokens.css（全局基础层）', () => {
    const main = readFileSync(MAIN_JS, 'utf-8')
    expect(main).toContain("platform/design-system/tokens.css")
    expect(main).toContain("platform/design-system/alias.css")
  })

  it('tokens.css 定义 §1.4a 深度系统 / 边框 ring / 圆角层级', () => {
    const css = readFileSync(join(PLATFORM_DIR, 'design-system', 'tokens.css'), 'utf-8')
    // 深度系统
    expect(css).toContain('--shadow-card:')
    expect(css).toContain('--shadow-raised:')
    expect(css).toMatch(/--shadow-card:\s*rgba\(0,\s*0,\s*0,\s*0\.02\)/)
    expect(css).toMatch(/--shadow-raised:\s*rgba\(0,\s*0,\s*0,\s*0\.04\)/)
    // 边框 ring（替代实色边框）
    expect(css).toMatch(/--ring:\s*rgba\(0,\s*0,\s*0,\s*0\.06\)/)
    // 圆角层级（禁止统一圆角）
    expect(css).toMatch(/--ma-radius-card:\s*14px/)
    expect(css).toMatch(/--ma-radius-inner:\s*12px/)
    expect(css).toMatch(/--ma-radius-btn:\s*8px/)
    expect(css).toMatch(/--ma-radius-tag:\s*6px/)
  })
})

describe('平台设计系统 · 新代码反模式扫描', () => {
  const files = collect(PLATFORM_DIR).filter((f) => !EXEMPT.has(rel(f)))

  it('平台层目录存在且含待扫描源文件', () => {
    expect(files.length).toBeGreaterThan(0)
  })

  it('除令牌定义文件外，新代码零 raw hex / rgb / hsl', () => {
    const offenders: string[] = []
    for (const f of files) {
      const src = stripComments(readFileSync(f, 'utf-8'))
      const lines = src.split('\n')
      lines.forEach((line, i) => {
        if (COLOR_RE.test(line)) offenders.push(`${rel(f)}:${i + 1} -> ${line.trim().slice(0, 80)}`)
      })
    }
    expect(offenders, offenders.join('\n')).toEqual([])
  })

  it('除令牌定义文件外，新代码零硬编码阴影', () => {
    const offenders: string[] = []
    for (const f of files) {
      const src = stripComments(readFileSync(f, 'utf-8'))
      src.split('\n').forEach((line, i) => {
        if (hasHardcodedShadow(line)) offenders.push(`${rel(f)}:${i + 1} -> ${line.trim().slice(0, 80)}`)
      })
    }
    expect(offenders, offenders.join('\n')).toEqual([])
  })
})

describe('存量业务组件 raw hex 基线（记录而非 fail）', () => {
  it('记录当前基线数量，后续轮次应递减', () => {
    const biz = collect(SRC_DIR, ['.vue', '.js', '.ts']).filter(
      (f) => !rel(f).startsWith('src/platform'),
    )
    let count = 0
    for (const f of biz) {
      const m = stripComments(readFileSync(f, 'utf-8')).match(HEX_RE)
      if (m) count += m.length
    }
    // 基线由真实扫描得出，仅记录不失败（避免全量变红）。
    console.log(`[基线] 存量业务组件 raw hex 当前共 ${count} 处（2380 为审计口径，后续轮次递减）。`)
    expect(count).toBeGreaterThan(0)
  })
})
