/** WP-K · 审计结果紧凑摘要（避免 PowerShell 解析大 JSON 的坑）。 */
import { readFileSync, writeFileSync } from 'node:fs'

const P = 'D:/math-arena/deliverables/vnext-autonomous/evidence/WP-K-AUDIT.json'
const j = JSON.parse(readFileSync(P, 'utf8'))
const top = (obj, n = 6) => Object.entries(obj).sort((a, b) => b[1] - a[1]).slice(0, n).map(([k, v]) => `${k}×${v}`).join(', ')
const lines = []

for (const key of Object.keys(j)) {
  const t = j[key]
  if (key === 'narrow') continue
  lines.push(`\n===== ${key}  (${t.path})`)
  lines.push(`radii   : ${top(t.radii, 8)}`)
  lines.push(`shadows : ${top(t.shadows, 8)}`)
  lines.push(`gaps    : ${top(t.gaps, 8)}`)
  lines.push(`cards   : count=${t.cardCount} maxDepth=${t.maxCardDepth}`)
  const fs = {}
  for (const s of t.textStyles) fs[`${s.size}/${s.weight}`] = (fs[`${s.size}/${s.weight}`] || 0) + 1
  lines.push(`type    : ${top(fs, 10)}`)
  const fails = t.contrast.filter((c) => c.verdict === 'FAIL')
  const manual = t.contrast.filter((c) => String(c.verdict).startsWith('needs-manual'))
  lines.push(`contrast: nodes=${t.contrast.length} FAIL=${fails.length} needs-manual=${manual.length}`)
  for (const f of fails.slice(0, 15)) lines.push(`   FAIL ${f.ratio}:1 ${f.size}px <${f.tag}.${f.cls}> "${f.sample}" fg=${f.fg} bg=${f.bg}`)
  const a = t.a11y
  lines.push(`a11y    : interactive=${a.interactiveCount} unlabeled=${a.unlabeled} ariaLive=[${a.ariaLive.join(' ')}] role=status:${a.roleStatus} alert:${a.roleAlert} imgNoAlt=${a.imagesWithoutAlt} svgNoHide=${a.svgWithoutHidden}`)
  lines.push(`headings: h1=[${a.h1.join(' | ')}] order=${a.headings}`)
  lines.push(`overflow: ${t.overflow.horizontalOverflow} (${t.overflow.docScrollW}/${t.overflow.docClientW})`)
  const noRing = t.focusWalk.filter((f) => !f.none && !f.hasVisibleRing)
  lines.push(`focus   : tabbed=${t.focusWalk.length} noVisibleRing=${noRing.length}`)
  for (const f of t.focusWalk) lines.push(`   ${f.none ? '(body/none)' : `${f.tag} [${f.testid}] ring=${f.hasVisibleRing} outline=${f.outline} "${f.label}"`}`)
}
lines.push('\n===== narrow (760x800)')
for (const [k, v] of Object.entries(j.narrow)) {
  lines.push(`${k}: overflow=${v.horizontalOverflow} column=${v.column?.w}px conversationH=${v.conversation?.h} shareOfViewport=${v.conversationShare}% composer=${JSON.stringify(v.composer)}`)
}
const out = lines.join('\n')
writeFileSync('D:/math-arena/deliverables/vnext-autonomous/evidence/WP-K-AUDIT.txt', out, 'utf8')
console.log(out)
