/**
 * PPT Studio V2 ↔ Legacy 边界守卫（R03 工程宪法）。
 *
 * 静态断言：`src/features/ppt-studio-v2/**` 不得引用旧 `SlidesView.vue`
 * （Legacy Freeze Manifest D2/D3）。目录尚不存在时测试必须通过（不误报）。
 * 纯源码文本扫描，无网络、无 DOM 依赖。
 */
import { describe, expect, it } from 'vitest'
import fs from 'node:fs'
import path from 'node:path'

// Vite 运行时以仓库根为 cwd
const SRC_ROOT = path.resolve(process.cwd(), 'src')
const V2_DIR = path.join(SRC_ROOT, 'features', 'ppt-studio-v2')
// 禁止被 V2 引用的 legacy 符号（相对 src/ 的模块路径）
const FORBIDDEN_LEGACY_REFS = ['pages/teacher-v3/SlidesView.vue']

function collectSourceFiles(dir: string): string[] {
  if (!fs.existsSync(dir)) return []
  const out: string[] = []
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name)
    if (entry.isDirectory()) {
      out.push(...collectSourceFiles(full))
    } else if (/\.(ts|tsx|js|jsx|vue)$/.test(entry.name)) {
      out.push(full)
    }
  }
  return out
}

describe('PPT Studio V2 禁止引用 legacy SlidesView', () => {
  it('目录不存在（未施工）时守卫待命，不误报', () => {
    // V2 目录当前不存在是合法状态（R03 禁止预造空目录）；
    // 不存在时本用例不产生任何断言，仅当 V2 代码出现后下述扫描用例生效。
    expect(true).toBe(true)
  })

  it('src/features/ppt-studio-v2/** 不得引用 legacy 模块（含 import/动态路由/硬路径）', () => {
    const files = collectSourceFiles(V2_DIR)
    if (files.length === 0) return // V2 目录尚未创建：守卫待命，不误报

    const violations: string[] = []
    for (const file of files) {
      const text = fs.readFileSync(file, 'utf-8')
      for (const legacyRef of FORBIDDEN_LEGACY_REFS) {
        const quotedVariants = [
          legacyRef.replace(/\\/g, '/'),
          legacyRef.replace(/\\/g, '/').replace('.vue', ''),
          `@/${legacyRef.replace(/\\/g, '/')}`,
        ]
        for (const needle of quotedVariants) {
          if (text.includes(needle)) {
            violations.push(`${path.relative(SRC_ROOT, file)} : 引用 ${needle}`)
          }
        }
      }
    }
    expect(violations).toEqual([])
  })
})