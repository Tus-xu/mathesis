/**
 * WP-K · 视觉/可访问性量化审计（真实浏览器，计算样式 + DOM 度量）
 *
 * 为什么不是"看截图"：本 agent 的模型不接受图像输入，无法肉眼判图。
 * 与其假装看过，不如把**可以被反驳的硬指标**测出来：
 *   1. 排版层级（用到的 font-size / weight 档位）
 *   2. 间距节奏（gap / padding 的取值分布）
 *   3. 圆角与阴影档位（是否收敛在 token 体系）
 *   4. 卡片嵌套深度（Cardocalypse 检测）
 *   5. 正文对比度（WCAG AA 4.5:1 逐文本节点计算）
 *   6. 键盘焦点环（Tab 走一遍，看是否可见）
 *   7. aria-live / role / label 覆盖率
 *   8. 窄屏横向溢出
 *
 * 用法：node test/visual/wpk/audit.mjs
 */
import { chromium } from 'playwright'
import { writeFileSync, mkdirSync } from 'node:fs'

const BASE = process.env.WPK_BASE || 'http://127.0.0.1:5176'
const OUT = 'D:/math-arena/deliverables/vnext-autonomous/evidence'

async function token() {
  const r = await fetch('http://127.0.0.1:8100/api/teacher-v3/auth/login', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username: 'zhanglaoshi', password: 'teacher123' }),
  })
  return (await r.json()).data.token
}
const IDENT = {
  teacher: { id: 'seed', nickname: '张老师', active_role: 'teacher', identity_status: 'approved', roles: [{ role: 'teacher', status: 'approved' }] },
  researcher: { id: 'seed', nickname: '张老师', active_role: 'researcher', identity_status: 'approved', roles: [{ role: 'researcher', status: 'approved' }] },
}
const SHELL = ['/api/tasks', '/api/notifications', '/api/student/growth/', '/api/agent/conversations']

const TARGETS = [
  { id: 'teacher-immersive', role: 'teacher', path: '/teacher-v3/prep-immersive' },
  { id: 'research-immersive', role: 'researcher', path: '/research-vnext/session' },
  { id: 'teacher-today', role: 'teacher', path: '/teacher-v3/today' },
]

/** 审计用：在页面里跑的采集函数（序列化后 evaluate） */
const COLLECT = () => {
  const sroot = document.querySelector('[data-testid="prep-focus-shell"]')?.parentElement
    || document.querySelector('[data-testid="rv08-research-surface"]')?.parentElement
    || document.body
  const els = [...sroot.querySelectorAll('*')]
  const counter = (arr) => arr.reduce((m, k) => (m[k] = (m[k] || 0) + 1, m), {})

  // 1. 排版层级
  const textStyles = []
  for (const el of els) {
    const hasOwnText = [...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim().length > 1)
    if (!hasOwnText) continue
    const cs = getComputedStyle(el)
    textStyles.push({
      tag: el.tagName.toLowerCase(),
      size: cs.fontSize, weight: cs.fontWeight, lh: cs.lineHeight, color: cs.color,
      family: cs.fontFamily.split(',')[0].replace(/["']/g, ''),
    })
  }

  // 2. 间距节奏
  const gaps = [], pads = []
  for (const el of els) {
    const cs = getComputedStyle(el)
    if (cs.display.includes('flex') || cs.display.includes('grid')) gaps.push(cs.gap)
    if (cs.padding && cs.padding !== '0px') pads.push(cs.padding)
  }

  // 3. 圆角/阴影档位
  const radii = [], shadows = []
  for (const el of els) {
    const cs = getComputedStyle(el)
    if (cs.borderRadius && cs.borderRadius !== '0px') radii.push(cs.borderRadius)
    if (cs.boxShadow && cs.boxShadow !== 'none') shadows.push(cs.boxShadow)
  }

  /** 是否"看起来像卡"：有边框或背景色 + 至少一项内边距 */
  const isCardLike = (el) => {
    const cs = getComputedStyle(el)
    const hasBorder = parseFloat(cs.borderTopWidth) > 0
    const bg = cs.backgroundColor
    const hasBg = bg && bg !== 'rgba(0, 0, 0, 0)' && bg !== 'transparent'
    const hasPad = parseFloat(cs.paddingTop) > 0 || parseFloat(cs.paddingLeft) > 0
    const hasShadow = cs.boxShadow && cs.boxShadow !== 'none'
    return hasPad && (hasBorder || hasShadow || (hasBg && !/^(rgb|rgba)/.test('rgb') === false))
  }
  // 4. 卡片嵌套深度：从每个"卡"向上数还有几层"卡"
  let maxCardDepth = 0
  const cardNodes = els.filter(isCardLike)
  for (const el of cardNodes) {
    let d = 1, p = el.parentElement
    while (p && p !== sroot) { if (isCardLike(p)) d++; p = p.parentElement }
    if (d > maxCardDepth) maxCardDepth = d
  }

  // 5. 对比度
  const lum = (c) => {
    const [r, g, b] = c.map((v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4) })
    return 0.2126 * r + 0.7152 * g + 0.0722 * b
  }
  const parse = (s) => {
    const m = String(s).match(/rgba?\(([^)]+)\)/)
    if (!m) return null
    const p = m[1].split(',').map((x) => parseFloat(x))
    return { rgb: p.slice(0, 3), a: p.length > 3 ? p[3] : 1 }
  }
  /**
   * 解析元素背后的**实际**底色：
   * - 由外到内收集各层背景（含 alpha），从白纸开始做 alpha 合成；
   * - 若任意祖先带 background-image（渐变），标记 gradient=true ——
   *   此时对比度无法用单一底色判定，报告为 needs-manual，而不是伪造一个通过/失败。
   */
  const effBg = (el) => {
    const chain = []
    let p = el
    while (p) { chain.unshift(p); p = p.parentElement }
    let rgba = [255, 255, 255, 1]
    let gradient = false
    for (const node of chain) {
      const cs = getComputedStyle(node)
      if (cs.backgroundImage && cs.backgroundImage !== 'none') gradient = true
      const c = parse(cs.backgroundColor)
      if (!c || c.a <= 0) continue
      rgba = [
        c.rgb[0] * c.a + rgba[0] * (1 - c.a),
        c.rgb[1] * c.a + rgba[1] * (1 - c.a),
        c.rgb[2] * c.a + rgba[2] * (1 - c.a),
        1,
      ]
    }
    return { rgb: rgba.slice(0, 3), gradient }
  }
  const contrast = []
  for (const el of els) {
    const hasOwnText = [...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim().length > 1)
    if (!hasOwnText) continue
    const cs = getComputedStyle(el)
    const fg = parse(cs.color); if (!fg) continue
    const bgInfo = effBg(el)
    // 前景自身 alpha 也要合成到背景上
    const fgc = [
      fg.rgb[0] * fg.a + bgInfo.rgb[0] * (1 - fg.a),
      fg.rgb[1] * fg.a + bgInfo.rgb[1] * (1 - fg.a),
      fg.rgb[2] * fg.a + bgInfo.rgb[2] * (1 - fg.a),
    ]
    const L1 = lum(fgc), L2 = lum(bgInfo.rgb)
    const ratio = (Math.max(L1, L2) + 0.05) / (Math.min(L1, L2) + 0.05)
    const size = parseFloat(cs.fontSize), weight = parseInt(cs.fontWeight, 10) || 400
    const large = size >= 24 || (size >= 18.66 && weight >= 700)
    contrast.push({
      tag: el.tagName.toLowerCase(), cls: (el.className || '').toString().slice(0, 40),
      sample: (el.textContent || '').trim().slice(0, 24), ratio: Math.round(ratio * 100) / 100,
      size, weight, large,
      // 渐变背景无法用单一底色判定 → 单独归类，不混进 AA 结论
      verdict: bgInfo.gradient ? 'needs-manual(gradient)' : ((large ? ratio >= 3 : ratio >= 4.5) ? 'pass' : 'FAIL'),
      fg: `rgb(${fgc.map((v) => Math.round(v)).join(',')})`, bg: `rgb(${bgInfo.rgb.map((v) => Math.round(v)).join(',')})`,
    })
  }

  // 7. 语义/可达性
  const interactive = [...sroot.querySelectorAll('button,a[href],input,textarea,select,[role="button"]')]
  const a11y = {
    interactiveCount: interactive.length,
    unlabeled: interactive.filter((el) => {
      const txt = (el.textContent || '').trim()
      return !txt && !el.getAttribute('aria-label') && !el.getAttribute('title') && !(el.labels && el.labels.length)
    }).length,
    ariaLive: [...sroot.querySelectorAll('[aria-live]')].map((e) => `${e.tagName.toLowerCase()}[aria-live=${e.getAttribute('aria-live')}]`),
    roleStatus: sroot.querySelectorAll('[role="status"]').length,
    roleAlert: sroot.querySelectorAll('[role="alert"]').length,
    landmarks: [...sroot.querySelectorAll('header,main,nav,aside,footer,[role]')].map((e) => e.tagName.toLowerCase() + (e.getAttribute('role') ? `[${e.getAttribute('role')}]` : '')).slice(0, 20),
    h1: [...sroot.querySelectorAll('h1')].map((e) => (e.textContent || '').trim().slice(0, 40)),
    headings: [...sroot.querySelectorAll('h1,h2,h3,h4')].map((e) => e.tagName.toLowerCase()).join(','),
    imagesWithoutAlt: [...sroot.querySelectorAll('img')].filter((i) => !i.hasAttribute('alt')).length,
    svgWithoutHidden: [...sroot.querySelectorAll('svg')].filter((s) => !s.hasAttribute('aria-hidden') && !s.getAttribute('aria-label')).length,
  }

  // 8. 溢出
  const overflow = {
    docScrollW: document.documentElement.scrollWidth,
    docClientW: document.documentElement.clientWidth,
    horizontalOverflow: document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
  }

  return {
    textStyles, gaps: counter(gaps), pads: counter(pads), radii: counter(radii), shadows: counter(shadows),
    cardCount: cardNodes.length, maxCardDepth, contrast, a11y, overflow,
    documentBg: getComputedStyle(document.body).backgroundColor,
    htmlBg: getComputedStyle(document.documentElement).backgroundColor,
  }
}

async function focusWalk(page, limit = 14) {
  const walk = []
  for (let i = 0; i < limit; i++) {
    await page.keyboard.press('Tab')
    const info = await page.evaluate(() => {
      const el = document.activeElement
      if (!el || el === document.body) return { none: true }
      const cs = getComputedStyle(el)
      const ow = parseFloat(cs.outlineWidth) || 0
      const sw = parseFloat(cs.boxShadow) || 0
      const selfRing = (cs.outlineStyle !== 'none' && ow > 0) || (sw > 0 && cs.boxShadow !== 'none')
      // 焦点环可以画在祖先（例如外层输入卡）上 —— 逐级向上找最近 3 层
      let ancestorRingOn = null
      let p = el.parentElement
      for (let d = 0; d < 3 && p; d++, p = p.parentElement) {
        const pcs = getComputedStyle(p)
        const pw = parseFloat(pcs.outlineWidth) || 0
        const psw = parseFloat(pcs.boxShadow) || 0
        if ((pcs.outlineStyle !== 'none' && pw > 0) || (psw > 0 && pcs.boxShadow !== 'none')) {
          ancestorRingOn = `${p.tagName.toLowerCase()}.${(p.className || '').toString().split(' ')[0]}`
          break
        }
      }
      return {
        tag: el.tagName.toLowerCase(),
        testid: el.getAttribute('data-testid') || '',
        cls: (el.className || '').toString().slice(0, 44),
        label: (el.getAttribute('aria-label') || el.textContent || '').trim().slice(0, 26),
        outline: cs.outlineStyle === 'none' ? 'none' : `${cs.outlineStyle} ${cs.outlineWidth} ${cs.outlineColor}`,
        selfRing,
        ancestorRingOn,
        hasVisibleRing: selfRing || !!ancestorRingOn,
      }
    })
    walk.push(info)
  }
  return walk
}

async function run() {
  mkdirSync(OUT, { recursive: true })
  const jwt = await token()
  const browser = await chromium.launch()
  const report = {}

  for (const t of TARGETS) {
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 } })
    await context.route((u) => u.pathname === '/api/auth/token/refresh', (r) => r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ code: 0, data: { access_token: jwt } }) }))
    await context.route((u) => u.pathname === '/api/auth/me', (r) => r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ code: 0, data: IDENT[t.role] }) }))
    await context.route((u) => SHELL.some((p) => u.pathname.startsWith(p)), (r) => r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ code: 0, data: { items: [], unread: 0 } }) }))
    const page = await context.newPage()
    await page.goto(`${BASE}${t.path}`, { waitUntil: 'domcontentloaded', timeout: 30000 })
    await page.waitForTimeout(3000)
    const data = await page.evaluate(COLLECT)
    data.focusWalk = await focusWalk(page)
    await page.evaluate(() => { document.body.focus?.() })
    report[t.id] = { path: t.path, ...data }
    await context.close()
  }

  // 窄屏溢出检查
  report.narrow = {}
  for (const t of [{ id: 'teacher', role: 'teacher', path: '/teacher-v3/prep-immersive' }, { id: 'research', role: 'researcher', path: '/research-vnext/session' }]) {
    const context = await browser.newContext({ viewport: { width: 760, height: 800 } })
    await context.route((u) => u.pathname === '/api/auth/token/refresh', (r) => r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ code: 0, data: { access_token: jwt } }) }))
    await context.route((u) => u.pathname === '/api/auth/me', (r) => r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ code: 0, data: IDENT[t.role] }) }))
    await context.route((u) => SHELL.some((p) => u.pathname.startsWith(p)), (r) => r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ code: 0, data: { items: [], unread: 0 } }) }))
    const page = await context.newPage()
    await page.goto(`${BASE}${t.path}`, { waitUntil: 'domcontentloaded', timeout: 30000 })
    await page.waitForTimeout(2500)
    report.narrow[t.id] = await page.evaluate(() => {
      const col = document.querySelector('.stage__column') || document.querySelector('.conversation')
      const composer = document.querySelector('[data-testid="prep-composer"]') || document.querySelector('.composer')
      const conv = document.querySelector('[data-testid="prep-conversation-stage"]') || document.querySelector('[data-testid="rv08-research-conversation"]')
      const main = document.querySelector('[data-testid="rv08-research-conversation"]')
      const r = (el) => { if (!el) return null; const b = el.getBoundingClientRect(); return { w: Math.round(b.width), h: Math.round(b.height), top: Math.round(b.top) } }
      return {
        viewport: { w: window.innerWidth, h: window.innerHeight },
        horizontalOverflow: document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
        column: r(col), composer: r(composer), conversation: r(conv || main),
        // 折叠后 conversation 高度占比（范式漂移检测：若 conversation 被压到很小 = 退化为表单）
        conversationShare: (() => { const c = conv || main; return c ? Math.round((c.getBoundingClientRect().height / window.innerHeight) * 100) : null })(),
      }
    })
    await context.close()
  }
  await browser.close()
  writeFileSync(`${OUT}/WP-K-AUDIT.json`, JSON.stringify(report, null, 2), 'utf8')
  console.log('audit written to WP-K-AUDIT.json')
}

run().catch((e) => { console.error(e); process.exit(1) })
