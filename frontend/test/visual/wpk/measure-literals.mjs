/**
 * WP-K · Q-4 原始色值/阴影字面量测量（可复现口径）
 *
 * 口径（与报告一致）：
 *   colour  = `#rgb/#rrggbb/#rrggbbaa` 出现次数
 *           + `rgb(/rgba(/hsl(/hsla(` 出现次数
 *           + `linear-gradient(/radial-gradient(/conic-gradient(` 出现次数
 *   shadow  = `box-shadow:` / `text-shadow:` 声明中，值**不以 `var(--` 开头**的次数
 *   scope   = `src/**\/*.{vue,ts,js,css}`
 *
 * 用法：node test/visual/wpk/measure-literals.mjs
 */
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'

const ROOT = 'D:/frontend/src'

function walk(dir) {
  const out = []
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    const p = join(dir, e.name)
    if (e.isDirectory()) out.push(...walk(p))
    else if (/\.(vue|ts|js|css)$/.test(e.name)) out.push(p)
  }
  return out
}

function count(text) {
  const hex = (text.match(/#[0-9a-fA-F]{3,8}\b/g) || []).length
  const rgb = (text.match(/\b(?:rgba?|hsla?)\(/g) || []).length
  const grad = (text.match(/\b(?:linear|radial|conic)-gradient\(/g) || []).length
  const shadows = [...text.matchAll(/(?:box|text)-shadow\s*:\s*([^;]+)/g)]
    .filter((m) => !/^\s*var\(--/.test(m[1]))
    .length
  return { colour: hex + rgb + grad, hex, rgb, grad, shadow: shadows }
}

const files = walk(ROOT)
const rows = []
for (const f of files) {
  const c = count(readFileSync(f, 'utf8'))
  rows.push({ file: f.replace(/\\/g, '/').replace(/^D:\/frontend\//, ''), ...c })
}

const sum = (list, k) => list.reduce((a, r) => a + r[k], 0)
const dirSum = (prefix) => {
  const list = rows.filter((r) => r.file.startsWith(prefix))
  return { files: list.length, colour: sum(list, 'colour'), hex: sum(list, 'hex'), rgb: sum(list, 'rgb'), grad: sum(list, 'grad'), shadow: sum(list, 'shadow') }
}

console.log('=== 每个文件的明细（供 grep 复核）===')
for (const r of rows) {
  if (r.colour || r.shadow) console.log(`COLOUR=${String(r.colour).padStart(4)} SHADOW=${String(r.shadow).padStart(3)} ${r.file}`)
}

console.log('\n=== 两个沉浸式 feature 目录（本包 Q-4 范围）===')
for (const d of ['src/features/teacher-prep-v2']) {  // research-vnext 已于 2026-09-17 隔离移出
  const s = dirSum(d)
  console.log(`${d.padEnd(34)} files=${String(s.files).padStart(3)} colour=${String(s.colour).padStart(4)} (hex ${s.hex} / rgb ${s.rgb} / grad ${s.grad})  shadow=${s.shadow}`)
}
console.log(`\n仓库整体（src/**）: files=${rows.length} colour=${sum(rows, 'colour')} (hex ${sum(rows, 'hex')} / rgb ${sum(rows, 'rgb')} / grad ${sum(rows, 'grad')})  shadow=${sum(rows, 'shadow')}`)

console.log('\n=== 仓库 TOP 15 颜色字面量文件（未处理的 backlog）===')
for (const r of [...rows].sort((a, b) => b.colour - a.colour).slice(0, 15)) {
  console.log(`  ${String(r.colour).padStart(5)}  (hex ${String(r.hex).padStart(4)} / rgb ${String(r.rgb).padStart(4)} / grad ${String(r.grad).padStart(3)})  ${r.file}`)
}
console.log('\n=== 仓库 TOP 10 原始阴影文件（未处理的 backlog）===')
for (const r of [...rows].sort((a, b) => b.shadow - a.shadow).slice(0, 10)) {
  console.log(`  ${String(r.shadow).padStart(4)}  ${r.file}`)
}
