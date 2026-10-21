/** WP-K · 前后对比摘要：把 before-/after- manifest 的关键状态差异打出来（用于报告取证）。 */
import { readFileSync } from 'node:fs'

const OUT = 'D:/math-arena/deliverables/vnext-autonomous/evidence/WP-K-screenshots'
const before = JSON.parse(readFileSync(`${OUT}/before-manifest.json`, 'utf8'))
const after = JSON.parse(readFileSync(`${OUT}/after-manifest.json`, 'utf8'))
const key = (m) => `${m.id}@${m.viewport}`

// 只放**真正的编造业务事实**关键词，避免把诚实空态文案里的词（如"不展示虚构示范校数据"）误判为命中
const FORBIDDEN = ['人大附中', '华师大二附中', '北京四中', '上海中学', '全国一等奖', '省优课', '部级优课', '市优课', '1.8MB', '2.3MB', '980KB', '立体几何复习课-参考教案', '导数应用-单元教学设计', '王老师', 'UP主「一数」', 'BV1GJ411x7h7', 'BV1xx411c7mD', 'netpad.net.cn', 'geogebra.org', '128.5万', '56.2万', '去年同课', '备课组共案', '主备人王老师', '李老师公开课件', '人教A版选择性必修一 P48', '人教A版选择性必修一 P46']
const IMPORTANT = ['ailp-blueprints-empty', 'ailp-refs-empty', 'ailp-up-parse-empty', 'ailp-up-parse-unknown', 'ailp-up-parse-foot-unknown', 'tv3-companion-no-source']

const bmap = new Map(before.map((m) => [key(m), m]))
const amap = new Map(after.map((m) => [key(m), m]))

console.log('=== 每张 after 截图中是否仍出现"编造内容"关键词 ===')
for (const m of after) {
  const hits = FORBIDDEN.filter((w) => (m.bodyText || '').includes(w))
  const marks = IMPORTANT.filter((w) => (m.bodyText || '').includes('')) // placeholder
  console.log(`${key(m).padEnd(52)} forbiddenHits=${JSON.stringify(hits)}`)
}

console.log('\n=== 关键状态：before vs after 正文 ===')
for (const id of ['teacher-prep-templates-blueprint', 'teacher-prep-templates-refs', 'teacher-prep-upload-parse', 'teacher-prep-immersive-missing-session', 'research-immersive-missing-session', 'teacher-recent-nav', 'teacher-slides-migration-notice']) {
  for (const k of [key({ id, viewport: '1440x900' })]) {
    const b = bmap.get(k); const a = amap.get(k)
    console.log(`\n--- ${k}`)
    if (b) console.log(`BEFORE url=${b.finalUrl}\n  ${JSON.stringify((b.bodyText || '').replace(/\s+/g, ' ').slice(0, 620))}`)
    if (a) console.log(`AFTER  url=${a.finalUrl}\n  ${JSON.stringify((a.bodyText || '').replace(/\s+/g, ' ').slice(0, 620))}`)
  }
}

console.log('\n=== testid 存在性（after） ===')
for (const id of ['teacher-prep-templates-blueprint', 'teacher-prep-templates-refs', 'teacher-prep-upload-parse']) {
  const a = amap.get(`${id}@1440x900`)
  if (!a) continue
  const ids = await0(a)
  console.log(`${id}: ${JSON.stringify(ids.filter((t) => t && (t.includes('empty') || t.includes('unknown') || t.includes('parse'))))}`)
}
function await0(a) { return a.testids || [] }

console.log('\n=== 关键 api 调用（after） ===')
for (const id of ['teacher-prep-templates-blueprint', 'teacher-prep-upload-parse', 'research-immersive-missing-session', 'teacher-recent-nav']) {
  const a = amap.get(`${id}@1440x900`)
  if (a) console.log(`${id}: ${JSON.stringify(a.api)}`)
}
