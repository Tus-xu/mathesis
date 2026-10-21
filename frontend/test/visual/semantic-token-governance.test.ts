import { readFileSync, readdirSync } from 'node:fs'
import { resolve } from 'node:path'

function vueFilesUnder(...segments: string[]): string[] {
  const root = resolve(process.cwd(), ...segments)
  const files: string[] = []
  const visit = (dir: string) => {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const path = resolve(dir, entry.name)
      if (entry.isDirectory()) visit(path)
      else if (entry.isFile() && entry.name.endsWith('.vue')) files.push(path)
    }
  }
  visit(root)
  return files
}

/** 逐文件断言：`<style>` 内不得有字面色值 / 渐变，box-shadow 必须取自 token。 */
function assertNoRawLiterals(files: string[]) {
  for (const file of files) {
    const source = readFileSync(file, 'utf8')
    const style = source.match(/<style[\s\S]*?<\/style>/)?.[0] ?? ''
    if (!style) continue
    expect(style).not.toMatch(/#[0-9a-f]{3,8}\b/i)
    expect(style).not.toMatch(/\brgba?\s*\(/i)
    expect(style).not.toMatch(/\b(?:linear|radial)-gradient\s*\(/i)
    const shadows = [...style.matchAll(/box-shadow\s*:\s*([^;]+)/gi)]
    for (const shadow of shadows) expect(shadow[1].trim()).toMatch(/^var\(--ma-/)
  }
}

describe('Checkpoint 1 semantic-token governance', () => {
  it('keeps all Teacher prep surface/component styles on semantic platform variables', () => {
    const files = [
      ...vueFilesUnder('src/features/teacher-prep-v2', 'surface'),
      ...vueFilesUnder('src/features/teacher-prep-v2', 'components'),
    ]
    expect(files.length).toBeGreaterThan(0)
    assertNoRawLiterals(files)
  })

  // 【已隔离 2026-09-17】原第二条用例扫描 `src/features/research-vnext`（上一代沉浸式科研面），
  // 该目录已整体移出至 D:\research-legacy-quarantine，用例随之作废并移除。
  // 顺带说明：这条用例此前**长期是红**的（扫到 research-vnext/focus/*.vue 里的
  // `box-shadow: inset 2px 0 0 var(--ma-action-primary)`），随文件移出一并消失。

  it('documents the semantic-token source and raw-literal boundary', () => {
    const design = readFileSync(resolve(process.cwd(), 'DESIGN.md'), 'utf8')
    expect(design).toContain('var(--ma-*)')
    expect(design).toContain('Foundation literals remain in the token source only')
    const sidecar = JSON.parse(readFileSync(resolve(process.cwd(), '.impeccable/design.json'), 'utf8')) as { tokens?: { semanticPrefix?: string } }
    expect(sidecar.tokens?.semanticPrefix).toBe('--ma-')
  })
})
