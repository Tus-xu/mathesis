/**
 * P01 · 边界与反模式静态扫描测试。
 *
 * 这是**守门测试**：把 P00 ADR-PLAT-10 的反模式从"口头约定"变成"CI 可拦截"。
 *
 * 覆盖：
 * - 平台层**不得** import 任何域代码（Teacher/Research）—— 域无关；
 * - 平台层组件**不得**出现 raw hex / rgb / shadow 字面量（须经设计 token）；
 * - 平台层**不得**用 localStorage 承载业务真相；
 * - 平台层**不得**出现 Stepper / Wizard 范式；
 * - 平台层**不得**自建 SSE 流（per-card stream）。
 */
/// <reference types="vite/client" />
import { describe, expect, it } from 'vitest'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'

const ROOT = join(__dirname, '..', '..')
const PLATFORM_DIR = join(ROOT, 'src', 'platform')

/** 递归收集目录下指定扩展名文件。 */
function collect(dir: string, exts: string[] = ['.ts', '.vue', '.js']): string[] {
  const out: string[] = []
  const walk = (d: string) => {
    for (const name of readdirSync(d)) {
      const p = join(d, name)
      if (statSync(p).isDirectory()) {
        walk(p)
      } else if (exts.some((e) => name.endsWith(e))) {
        out.push(p)
      }
    }
  }
  walk(dir)
  return out
}

const platformFiles = collect(PLATFORM_DIR)
const rel = (p: string) => relative(ROOT, p).replace(/\\/g, '/')

/** 去掉注释后的源码（避免注释里的示例代码造成误报）。 */
function stripComments(src: string): string {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/(^|[^:])\/\/.*$/gm, '$1')
}

describe('P01 · 反模式扫描：平台层边界', () => {
  it('平台层目录存在且含源文件', () => {
    expect(platformFiles.length).toBeGreaterThan(0)
  })

  it('平台层不得 import 任何域代码（Teacher/Research 业务）', () => {
    const offenders: string[] = []
    const forbidden = [
      /from\s+['"]@\/features\/teacher-/,
      /from\s+['"]@\/features\/research-/,
      /from\s+['"]@\/pages\//,
      /from\s+['"]@\/stores\/research/,
      /from\s+['"]@\/stores\/companion/,
    ]
    for (const f of platformFiles) {
      const src = stripComments(readFileSync(f, 'utf-8'))
      for (const re of forbidden) {
        if (re.test(src)) offenders.push(`${rel(f)}: ${re}`)
      }
    }
    expect(offenders).toEqual([])
  })

  it('平台层不得出现 raw hex / rgb / hsl 颜色字面量（唯一例外：tokens.ts 定义处）', () => {
    const offenders: string[] = []
    const colorRe = /#[0-9a-fA-F]{3,8}\b|\brgba?\s*\(|\bhsla?\s*\(/
    for (const f of platformFiles) {
      if (rel(f).endsWith('platform/design-system/tokens.ts')) continue
      const src = stripComments(readFileSync(f, 'utf-8'))
      const lines = src.split('\n')
      lines.forEach((line, i) => {
        if (colorRe.test(line)) offenders.push(`${rel(f)}:${i + 1}`)
      })
    }
    expect(offenders).toEqual([])
  })

  it('平台层不得用 localStorage 承载业务真相', () => {
    const offenders: string[] = []
    for (const f of platformFiles) {
      const src = stripComments(readFileSync(f, 'utf-8'))
      if (/\blocalStorage\b/.test(src)) offenders.push(rel(f))
    }
    expect(offenders).toEqual([])
  })

  it('平台层不得出现 Stepper / Wizard 组件或术语（范式漂移）', () => {
    const offenders: string[] = []
    const re = /\b(stepper|Stepper|NSteps|wizard|Wizard)\b/
    for (const f of platformFiles) {
      const src = stripComments(readFileSync(f, 'utf-8'))
      // 逐行判定：排除「契约声明禁止」的语境（校验规则文案 / 字段名）
      const lines = src.split('\n').filter((line) => {
        // 契约字段名（我们声明它们必须为 false）
        if (/stepper_allowed|wizard_allowed/.test(line)) return false
        // 校验规则文案（描述"禁止 Stepper/Wizard"）
        if (/(禁止|必须为\s*false|不得)/.test(line)) return false
        return true
      })
      if (lines.some((line) => re.test(line))) offenders.push(rel(f))
    }
    expect(offenders).toEqual([])
  })

  it('平台层不得自建 EventSource / SSE 流（复用由契约层保证）', () => {
    const offenders: string[] = []
    for (const f of platformFiles) {
      const src = stripComments(readFileSync(f, 'utf-8'))
      if (/new\s+EventSource\b|EventSource\s*\(/.test(src)) offenders.push(rel(f))
    }
    expect(offenders).toEqual([])
  })

  it('平台层不得直接 fetch 物理地址（须经统一 API client）', () => {
    const offenders: string[] = []
    for (const f of platformFiles) {
      const src = stripComments(readFileSync(f, 'utf-8'))
      if (/\bfetch\s*\(/.test(src)) offenders.push(rel(f))
    }
    expect(offenders).toEqual([])
  })
})

describe('P01 · 反模式扫描：设计 token 单一入口', () => {
  it('tokens.ts 必须是平台层唯一含字面色值的文件', () => {
    const withColor = platformFiles.filter((f) => {
      const src = stripComments(readFileSync(f, 'utf-8'))
      return /#[0-9a-fA-F]{6}\b/.test(src)
    })
    expect(withColor.map(rel)).toEqual(['src/platform/design-system/tokens.ts'])
  })
})
