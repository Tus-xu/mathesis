/** WP-K · 沉浸式 surface 结构核验（响应式折叠 + 无障碍结构），产出可引用的硬证据。 */
import { chromium } from 'playwright'
import { writeFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))

const BASE = 'http://127.0.0.1:5176'
const SHELL = ['/api/tasks', '/api/notifications', '/api/student/growth/', '/api/agent/conversations']

async function token() {
  const r = await fetch('http://127.0.0.1:8100/api/teacher-v3/auth/login', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username: 'zhanglaoshi', password: 'teacher123' }),
  })
  return (await r.json()).data.token
}

const IDENT = {
  teacher: { id: 's', nickname: '张老师', active_role: 'teacher', identity_status: 'approved', roles: [{ role: 'teacher', status: 'approved' }] },
  researcher: { id: 's', nickname: '张老师', active_role: 'researcher', identity_status: 'approved', roles: [{ role: 'researcher', status: 'approved' }] },
}

const TARGETS = [
  { id: 'teacher-immersive', role: 'teacher', path: '/teacher-v3/prep-immersive' },
  { id: 'research-immersive', role: 'researcher', path: '/research-vnext/session' },
]

const PROBE = () => {
  const q = (s) => document.querySelector(s)
  const rect = (s) => { const e = q(s); if (!e) return null; const b = e.getBoundingClientRect(); return { w: Math.round(b.width), h: Math.round(b.height), top: Math.round(b.top), bottom: Math.round(b.bottom) } }
  const cs = (s) => { const e = q(s); return e ? getComputedStyle(e) : null }
  return {
    surfaceMode: q('[data-testid="prep-immersive-surface"]')?.getAttribute('data-surface-mode') ?? null,
    // 范式漂移检测：出现步进器/表单字段 = conversation 被折叠成 form
    stepper: !!q('.ptx-steps') || !!q('[class*="step-dot"]') || !!q('[class*="ailp-step"]'),
    plainFormFields: document.querySelectorAll('input[type=text],input[type=email],input[type=number],select').length,
    hasTextareaComposer: !!q('textarea, .composer input'),
    conversationPresent: !!q('[data-testid="prep-conversation-stage"]') || !!q('[data-testid="rv08-research-conversation"]'),
    conversationRect: rect('[data-testid="prep-conversation-stage"]') || rect('[data-testid="rv08-research-conversation"]'),
    scrollRegion: rect('[data-testid="prep-timeline-scroll"]'),
    column: rect('.stage__column') || rect('.conversation'),
    composer: rect('[data-testid="prep-composer-dock"]') || rect('.composer'),
    composerPosition: cs('[data-testid="prep-composer-dock"]')?.position ?? cs('.composer')?.position ?? null,
    globalNav: !!q('nav[aria-label="教师工作台导航"]'),
    horizontalOverflow: document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
    docScrollW: document.documentElement.scrollWidth,
    docClientW: document.documentElement.clientWidth,
    liveRegions: [...document.querySelectorAll('[aria-live]')].map((e) => `${e.tagName.toLowerCase()}[aria-live=${e.getAttribute('aria-live')}]`),
    roles: [...new Set([...document.querySelectorAll('[role]')].map((e) => e.getAttribute('role')))],
    viewport: { w: window.innerWidth, h: window.innerHeight },
  }
}

const jwt = await token()
const browser = await chromium.launch()
const out = {}
for (const t of TARGETS) {
  for (const [w, h] of [[760, 800], [1280, 800], [1440, 900], [1920, 1080]]) {
    const ctx = await browser.newContext({ viewport: { width: w, height: h } })
    await ctx.route((u) => u.pathname === '/api/auth/token/refresh', (r) => r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ code: 0, data: { access_token: jwt } }) }))
    await ctx.route((u) => u.pathname === '/api/auth/me', (r) => r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ code: 0, data: IDENT[t.role] }) }))
    await ctx.route((u) => SHELL.some((p) => u.pathname.startsWith(p)), (r) => r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ code: 0, data: { items: [], unread: 0 } }) }))
    const page = await ctx.newPage()
    await page.goto(`${BASE}${t.path}`, { waitUntil: 'domcontentloaded', timeout: 30000 })
    await page.waitForTimeout(2500)
    out[`${t.id}@${w}x${h}`] = await page.evaluate(PROBE)
    await ctx.close()
  }
}
await browser.close()
const OUT = path.resolve(__dirname, '..', '..', '..', 'artifacts', 'vnext-autonomous', 'evidence', 'WP-K-STRUCTURE-CHECK.json')
writeFileSync(OUT, JSON.stringify(out, null, 2), 'utf8')
for (const [k, v] of Object.entries(out)) {
  console.log(`${k}  surfaceMode=${v.surfaceMode} stepper=${v.stepper} formFields=${v.plainFormFields} conversation=${v.conversationPresent} nav=${v.globalNav} overflow=${v.horizontalOverflow}(${v.docScrollW}/${v.docClientW}) composerPos=${v.composerPosition}`)
  console.log(`   column=${JSON.stringify(v.column)} composer=${JSON.stringify(v.composer)} conversation=${JSON.stringify(v.conversationRect)}`)
  console.log(`   liveRegions=${JSON.stringify(v.liveRegions)} roles=${JSON.stringify(v.roles)}`)
}
