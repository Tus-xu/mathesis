/**
 * mock/server.js —— Vite 开发中间件模拟后端（挂载于 /api）
 * 完整模拟：会话 CRUD / 消息分页 / SSE 流式对话（meta→status→token→card→done）/
 * 文件上传解析 / 语音转公式 / 学生端学情接口。
 * 业务逻辑层（useChat / streamChat / api）零改动，仅替换数据来源。
 */
import {
  MOCK_USER, seedConversations, seedMessages, reviewPlan, masterySummary, labRecommend, knowledgeGraph, iso,
  growthOverview, growthPanel, routeIntentReply, loopProgress,
  practiceGroupRecommend, difficultyMix, smartScore, practiceSummary,
  memoryHeatmap, dueQueue, errorDetailExt, errorFilter, mockGgb,
  reportHighlights, reportWeakPoints, masteryTrendForecast, errorDistribution, reportHonesty,
  kgPie, kgTree, kgNodeDeps, kgNodeRecommend,
  today3, scoreTrend, featureEntries,
  classFeed, classHotErrors, videoBrowseMock, videoRecommendMock, assignmentsList,
} from './data'
import { handleTeacherV3Api } from './teacherV3Server'

/* ================= 内存仓库 ================= */
const conversations = seedConversations.map((c) => ({ ...c }))
const messagesByConv = new Map(Object.entries(seedMessages).map(([id, msgs]) => [id, msgs.map((m) => ({ ...m }))]))

function readBody(req) {
  return new Promise((resolve) => {
    let buf = ''
    req.on('data', (chunk) => { buf += chunk })
    req.on('end', () => {
      try { resolve(buf ? JSON.parse(buf) : {}) } catch { resolve({}) }
    })
  })
}

function ok(res, data) {
  if (res.headersSent || res.writableEnded) return
  res.setHeader('Content-Type', 'application/json')
  res.end(JSON.stringify({ code: 0, message: 'ok', data }))
}
function fail(res, status, code, message) {
  if (res.headersSent || res.writableEnded) return
  res.statusCode = status
  res.setHeader('Content-Type', 'application/json')
  res.end(JSON.stringify({ code, message, data: null }))
}

/* ================= SSE 对话生成 ================= */
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

function sendSse(res, event, data) {
  if (res.writableEnded || res.destroyed) return
  res.write(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`)
}

function buildReply(message = '') {
  const text = String(message || '')
  const has = (...ks) => ks.some((k) => text.includes(k))

  if (has('变式', '再来一组', '出几道') || text.includes('出') && has('题')) {
    return {
      skill: 'smart_quiz',
      thinking: '根据该生薄弱点（导数与函数单调性 12%）出 3 道难度递进的变式题，第 1 题换数字，第 2 题换条件，第 3 题综合应用。',
      lead: '好的，围绕**导数与单调性**给你出一组 3 道变式，难度递进：\n\n第 1 道先热热身（换数字）👇',
      card: {
        type: 'quiz_set',
        chain: 'variant',
        chain_id: `chain-${Date.now()}`,
        items: [
          {
            item_no: 1, q_type: 'choice', difficulty: 'easy',
            kp_name: '导数与函数单调性', kp_code: 'DR-02',
            variant_note: '变式 1 · 换数字',
            question_text: '函数 $f(x)=x^3-3x+1$ 在 $[-2,2]$ 上的最大值是？',
            options: ['$3$', '$7$', '$-1$', '$2$'],
            answer: 'B',
            answer_analysis: '令 $f\'(x)=3x^2-3=0$ 得 $x=\\pm1$。$f(-2)=-1$，$f(-1)=3$，$f(1)=-1$，$f(2)=7$，故最大值 $7$（端点 $x=2$ 必比）。',
          },
          {
            item_no: 2, q_type: 'choice', difficulty: 'medium',
            kp_name: '导数与函数单调性', kp_code: 'DR-02',
            variant_note: '变式 2 · 换条件（开区间）',
            question_text: '函数 $f(x)=x^3-3x$ 在开区间 $(-1,3)$ 上的最值情况是？',
            options: ['有最大值 18，无最小值', '无最大值，有最小值 -2', '最大值 18、最小值 -2 都在', '无最大值也无最小值'],
            answer: 'D',
            answer_analysis: '开区间取不到端点值 $x=3$，且 $f(-1)=2$、$f(1)=-2$ 仍在区间内，故最值不落在区间端点上——本题既无最大值也无最小值（极值只是局部）。',
          },
          {
            item_no: 3, q_type: 'choice', difficulty: 'hard',
            kp_name: '导数与不等式综合', kp_code: 'DR-06',
            variant_note: '变式 3 · 综合应用',
            question_text: '已知 $f(x)=x^3-3x$，若不等式 $f(x) \\le a$ 在 $x \\in [-1,3]$ 恒成立，则 $a$ 的最小值为？',
            options: ['$2$', '$18$', '$-2$', '$0$'],
            answer: 'B',
            answer_analysis: '恒成立 ⇔ $a \\ge f(x)_{max}$。由前两问知闭区间 $[-1,3]$ 上最大值为 $f(3)=18$，故 $a$ 最小取 $18$。',
          },
        ],
      },
      tail: '全部做完我来帮你对答案，错了的自动进错题本 📕',
    }
  }

  if (has('讲解', '举一反三', '错题', '复习', '分析我的错题')) {
    return {
      skill: 'socratic_solver',
      thinking: '定位错因：漏判区间端点 x=3。采用苏格拉底式提问，先确认方法，再引导代入端点比较，最后出变式确认掌握。',
      lead: '好，我们把这题彻底弄懂。\n\n这道题你错在 **"漏判区间端点 $x=3$"**。闭区间上的极值必须**同时比较极值点和端点**——口诀 → *"先找导零，再代端点，谁大谁小"*。\n\n你还记得求闭区间最值的三个步骤吗？先告诉我你的思路，我再帮你核对。',
      card: null,
      tail: '把这三步走通后，我再给你 2-3 道变式确认你真的掌握了 👌',
    }
  }

  if (has('学情', '分析')) {
    return {
      skill: '',
      thinking: '汇总本周学情：综合分 58→67，独立解题率 35%→52%，导数薄弱点 12%。',
      lead: '本周你的学情小结 📊：\n\n• 综合分 **58 → 67**（+9 分），距期末目标 75 还差 8 分\n• 独立解题率 **35% → 52%**，超过班级平均 47%\n• 薄弱 Top1：**导数与函数单调性（12%）**，本周建议每天 20 分钟专项\n• 3 道错题今天到期，建议先去错题本复习（约 15 分钟）\n\n要继续的话，我可以带你练一道导数的题，或者帮你分析某类错题。',
      card: null,
      tail: '',
    }
  }

  /* 默认：苏格拉底引导（对齐 v4 演示）+ F13 figure 事件演示 */
  return {
    skill: 'socratic_solver',
    thinking: '闭区间最值问题：f(x)=x³-3x，先求导找极值点，再代入端点比较，最后输出最大值最小值。',
    lead: '小婷，这道题确实需要一步步想清楚。\n\n我们先看题目要求的是什么——在闭区间 **$[-1, 3]$** 上找 **$f(x) = x^3 - 3x$** 的最大值和最小值。\n\n**你记得吗？求闭区间上函数的最值，通常会用到什么方法？**',
    card: null,
    tail: '想清楚这三步后告诉我，我帮你核对下一步，再给你变式巩固。',
    figures: [
      {
        step_no: 1,
        caption: '先观察这条抛物线的形状，找找它与 x 轴的交点',
        frames: parabolaFrames(),
      },
    ],
  }
}

/* ===== F13 figure 事件构造助手（mock：抛物线 2 帧渐进揭示） ===== */
function svgDataUri(svg) {
  return 'data:image/svg+xml;base64,' + Buffer.from(svg, 'utf-8').toString('base64')
}

function parabolaFrames() {
  const body = (dots) => `<svg xmlns="http://www.w3.org/2000/svg" width="460" height="330" viewBox="0 0 460 330">
  <rect width="460" height="330" fill="#ffffff"/>
  <line x1="46" y1="169" x2="446" y2="169" stroke="#222222" stroke-width="1.3"/>
  <line x1="146" y1="16" x2="146" y2="296" stroke="#222222" stroke-width="1.3"/>
  <text x="452" y="161" font-size="13" font-style="italic" font-family="Georgia" text-anchor="start">x</text>
  <text x="158" y="12" font-size="13" font-style="italic" font-family="Georgia">y</text>
  <text x="136" y="184" font-size="11" font-family="Georgia" text-anchor="middle">O</text>
  <path d="M146 169 Q246 373 346 169" fill="none" stroke="#1a5fb4" stroke-width="1.9"/>
  ${dots}
</svg>`
  return [
    { data_uri: svgDataUri(body('')), label: '坐标系与曲线' },
    {
      data_uri: svgDataUri(
        body(
          '<circle cx="146" cy="169" r="3.5" fill="#c01c28" stroke="#ffffff"/><text x="154" y="165" font-size="12" fill="#c01c28" font-family="Georgia">(-1,0)</text>' +
          '<circle cx="346" cy="169" r="3.5" fill="#c01c28" stroke="#ffffff"/><text x="354" y="165" font-size="12" fill="#c01c28" font-family="Georgia">(3,0)</text>' +
          '<circle cx="246" cy="271" r="3.5" fill="#c01c28" stroke="#ffffff"/><text x="254" y="284" font-size="12" fill="#c01c28" font-family="Georgia">(1,-4)</text>'
        )
      ),
      label: '标注关键点',
    },
  ]
}

/** 把回复拆成 token 片段流式下发 */
function streamReply(res, reply, { onDone } = {}) {
  const msgId = `msg_${Date.now()}_${Math.floor(Math.random() * 1e6)}`
  const steps = [
    { stage: 'solving', text: '正在理解题意…' },
    { stage: 'guiding', text: '定位你的薄弱点（导数 12%）…' },
  ]
  let i = 0
  let timers = []
  const clear = () => { timers.forEach((t) => clearTimeout(t)); timers = [] }

  const push = (event, data, delay) => {
    timers.push(setTimeout(() => sendSse(res, event, data), delay))
  }

  push('meta', { msg_id: msgId, skill: reply.skill, confidence: reply.skill === 'smart_quiz' ? 0.9 : 0.95 }, 40)
  steps.forEach((s, si) => push('status', s, 120 + si * 120))
  if (reply.thinking) {
    const tk = reply.thinking.slice(0, 40)
    push('thinking', { text: tk }, 360)
  }
  const lead = reply.lead
  let offset = 480
  const chunkSize = 8
  for (let p = 0; p < lead.length; p += chunkSize) {
    push('token', { text: lead.slice(p, p + chunkSize) }, offset)
    offset += 22
  }
  // F13：figure 事件在讲解 token 之后、卡片/收尾之前下发（图文顺序 = 讲解顺序）
  let figDelay = offset + 60
  for (const fig of reply.figures || []) {
    push('figure', fig, figDelay)
    figDelay += 180
  }
  if (reply.card) {
    push('card', reply.card, figDelay + 60)
  }
  if (reply.tail) {
    let t = figDelay + 180
    for (let p = 0; p < reply.tail.length; p += chunkSize) {
      push('token', { text: reply.tail.slice(p, p + chunkSize) }, t)
      t += 20
    }
  }
  const endDelay = figDelay + 200 + (reply.card ? 200 : 0) + (reply.tail ? Math.ceil(reply.tail.length / 8) * 20 : 0)
  push('done', {
    message_id: msgId,
    title: lead.replace(/[#*$]/g, '').slice(0, 16) || '新对话',
    usage: { tokens_in: 320, tokens_out: lead.length + (reply.tail || '').length },
    latency_ms: 4200,
    meta: { skill: reply.skill, confidence: 0.95 },
  }, endDelay)

  return { clear, msgId }
}

/* ================= 中间件入口 ================= */
export function mockApi(req, res, next) {
  const url = req.url.split('?')[0]
  const qs = new URLSearchParams(req.url.split('?')[1] || '')
  const method = req.method
  const seg = url.split('/').filter(Boolean)

  const json = async (fn) => { const body = await readBody(req); return fn(body) }

  const route = async () => {
    /* ---------- 认证 ---------- */
    const cookie = req.headers?.cookie || ''
    const cookieValue = (name) => cookie.match(new RegExp(`(?:^|;\\s*)${name}=([^;]+)`))?.[1] || ''
    const mockRole = cookieValue('ma_mock_role') || 'student'
    const mockState = cookieValue('ma_mock_state') || 'approved'
    const mockDual = cookieValue('ma_mock_dual') === '1'
    const supportedRoles = ['student', 'teacher', 'researcher', 'admin']
    const professionalRoles = ['teacher', 'researcher']
    const tokenFor = (role) => role === 'student' ? 'mock-token-preview' : `mock-token-${role}-preview`
    const statusFor = () => 'authenticated'
    const identityFor = (requestedRole, state = mockState) => {
      const role = supportedRoles.includes(requestedRole) ? requestedRole : 'student'
      const identityStatus = statusFor(role, state)
      const isProfessionalPending = false
      const activeRole = role
      const approvedRole = { role: activeRole, status: 'approved', verified: true }
      const user = activeRole === 'student'
        ? {
            id: isProfessionalPending ? `mock-${role}-applicant` : 'mock-student',
            ...MOCK_USER,
            status: 'active',
            onboarding_status: 'completed',
            roles: [approvedRole, ...(isProfessionalPending ? [{ role, status: state, verified: false }] : mockDual ? [{ role: 'teacher', status: 'approved', verified: true }] : [])],
            active_role: 'student',
            grade: MOCK_USER.grade || '',
          }
        : {
            id: `mock-${role}`,
            nickname: role === 'teacher' ? '李老师' : role === 'researcher' ? '陈研究员' : '管理员',
            status: 'active',
            onboarding_status: 'completed',
            roles: [approvedRole],
            active_role: activeRole,
            grade: '',
          }
      return { user, identity_status: identityStatus, ...(isProfessionalPending ? { pending_role: role } : {}) }
    }
    const sessionResponse = (requestedRole, state = mockState, onboardingRequired = false) => {
      const identity = identityFor(requestedRole, state)
      return { access_token: tokenFor(identity.user.active_role), expires_in: 900, onboarding_required: onboardingRequired, ...identity }
    }
    const persistMockIdentity = (role, state) => {
      res.setHeader('Set-Cookie', [
        `ma_mock_role=${role}; Path=/; SameSite=Lax`,
        `ma_mock_state=${state}; Path=/; SameSite=Lax`,
      ])
    }
    const mockIdentity = identityFor(mockRole)
    if (method === 'POST' && url === '/auth/token/refresh') return ok(res, { access_token: tokenFor(mockIdentity.user.active_role), expires_in: 900 })
    if (method === 'GET' && url === '/auth/me') {
      const authz = req.headers?.authorization || ''
      const tokenRole = ['admin', 'researcher', 'teacher'].find((role) => authz.includes(`mock-token-${role}-preview`))
      const identity = identityFor(tokenRole || mockRole)
      return ok(res, { ...identity.user, identity_status: identity.identity_status, ...(identity.pending_role ? { pending_role: identity.pending_role } : {}) })
    }
    if (method === 'POST' && url === '/auth/challenges/sms') return ok(res, { challenge_id: 'mock-challenge', expires_in: 300, retry_after: 1, demo_code: '123456' })
    if (method === 'POST' && url === '/auth/register/sms') return json((b) => {
      const role = b.role || mockRole
      const state = 'approved'
      persistMockIdentity(role, state)
      return ok(res, sessionResponse(role, state, role === 'student'))
    })
    if (method === 'POST' && url === '/auth/login/sms') return json((b) => {
      const role = b.preferred_role || mockRole
      persistMockIdentity(role, mockState)
      return ok(res, sessionResponse(role))
    })
    if (method === 'POST' && url === '/auth/login/password') return json((b) => {
      const role = b.preferred_role || mockRole
      persistMockIdentity(role, mockState)
      return ok(res, sessionResponse(role))
    })
    if (method === 'POST' && url === '/identity/onboarding/student') return ok(res, { onboarding_required: false })
    if (method === 'POST' && url === '/identity/role-applications') return json((b) => ok(res, { id: 'mock-application', role: b.role, status: 'pending' }))
    if (method === 'GET' && url === '/identity/role-applications/current') return ok(res, [{ id: 'mock-application', role: 'teacher', status: 'pending', organization_name: '示例中学' }])
    if (method === 'POST' && url === '/auth/reauth') return ok(res, { reauthenticated: true, valid_for: 600 })
    if (method === 'POST' && url === '/auth/password/reset') return ok(res, { password_reset: true })
    if (method === 'POST' && url === '/auth/role/switch') return json((b) => {
      const role = supportedRoles.includes(b.role) ? b.role : mockRole
      persistMockIdentity(role, 'approved')
      return ok(res, sessionResponse(role, 'approved'))
    })
    if (method === 'GET' && url === '/auth/sessions') return ok(res, [
      { id: 'current-session', device_name: 'Chrome · Windows', current: true, revoked: false, last_seen_at: '刚刚' },
      { id: 'other-session', device_name: 'Firefox · macOS', current: false, revoked: false, last_seen_at: '昨天' },
    ])
    if (method === 'DELETE' && url === '/auth/sessions/other-session') return ok(res, { revoked: true })
    if (method === 'POST' && url === '/auth/logout-all') return ok(res, { logged_out: true })
    if (method === 'POST' && url === '/identity/phone/change') return ok(res, { phone: '13900000000', sessions_revoked: true })
    if (method === 'POST' && url === '/identity/account/deletion') return ok(res, { status: 'cooling_off', execute_after: '2026-08-29T00:00:00Z' })
    if (method === 'GET' && url === '/identity/account/deletion') return ok(res, null)
    if (method === 'POST' && url === '/identity/account/deletion/cancel') return ok(res, { status: 'cancelled' })
    if (method === 'GET' && url === '/admin/identity/applications') return ok(res, [
      { id: 'teacher-application', role: 'teacher', status: 'pending', organization_name: '示例中学' },
      { id: 'researcher-application', role: 'researcher', status: 'pending', organization_name: '示例研究院' },
    ])
    if (method === 'POST' && /^\/admin\/identity\/applications\/[^/]+\/(approve|reject|request-more-info)$/.test(url)) {
      const status = url.endsWith('/approve') ? 'approved' : url.endsWith('/reject') ? 'rejected' : 'needs_more_info'
      return ok(res, { status })
    }
    if (method === 'POST' && url === '/auth/login') return json((b) => ok(res, { token: 'mock-token-' + Date.now(), user: { ...MOCK_USER } }))
    if (method === 'POST' && url === '/auth/login-by-code') return json((b) => ok(res, { token: 'mock-token-' + Date.now(), user: { ...MOCK_USER } }))
    if (method === 'POST' && url === '/auth/sms-code') return ok(res, { sent: true })

    /* ---------- 会话 ---------- */
    if (method === 'GET' && url === '/agent/conversations') {
      let items = [...conversations]
      const q = (qs.get('q') || '').toLowerCase()
      if (q) items = items.filter((c) => (c.title || '').toLowerCase().includes(q))
      items.sort((a, b) => String(b.updated_at).localeCompare(String(a.updated_at)))
      return ok(res, { items: items.slice(0, Number(qs.get('limit')) || 30), hasMore: false })
    }
    if (method === 'POST' && url === '/agent/conversations') {
      return json((b) => {
        const c = {
          id: `conv_${Date.now()}`,
          title: '新对话',
          workspace: b.workspace || 'student',
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
          message_count: 0,
          pinned: false,
        }
        conversations.unshift(c)
        messagesByConv.set(c.id, [])
        return ok(res, c)
      })
    }
    if (method === 'DELETE' && seg[0] === 'agent' && seg[1] === 'conversations' && seg[2]) {
      const id = seg[2]
      const i = conversations.findIndex((c) => c.id === id)
      if (i >= 0) conversations.splice(i, 1)
      messagesByConv.delete(id)
      return ok(res, { deleted: true })
    }
    if (method === 'PATCH' && seg[0] === 'agent' && seg[1] === 'conversations' && seg[2]) {
      return json((b) => {
        const c = conversations.find((x) => x.id === seg[2])
        if (!c) return fail(res, 404, 404, '会话不存在')
        if (b.title !== undefined) c.title = b.title
        if (b.pinned !== undefined) c.pinned = !!b.pinned
        return ok(res, { ...c })
      })
    }
    if (method === 'GET' && seg[0] === 'agent' && seg[1] === 'conversations' && seg[2] && seg[3] === 'messages') {
      const id = seg[2]
      let items = messagesByConv.get(id) || []
      const before = qs.get('before') || ''
      if (before) items = items.filter((m) => m.id !== before && String(m.id).localeCompare(before) < 0)
      const limit = Number(qs.get('limit')) || 20
      // 历史分页：返回更早的 limit 条（时间正序）
      const tail = items.slice(-limit)
      return ok(res, { items: tail, hasMore: items.length > limit })
    }

    /* ---------- 消息级操作 ---------- */
    if (method === 'POST' && url === '/agent/feedback') return json(() => ok(res, { ok: true }))
    if (method === 'POST' && url === '/agent/chat/stop') return ok(res, { stopped: true })
    if (method === 'POST' && seg[0] === 'agent' && seg[1] === 'messages' && seg[2] === 'activate') return ok(res, { ok: true })
    if (method === 'GET' && url === '/agent/memories') return ok(res, { items: [] })
    if (method === 'DELETE' && seg[0] === 'agent' && seg[1] === 'memories') return ok(res, { deleted: true })

    /* ---------- SSE 对话（chat / regenerate / edit） ---------- */
    if (method === 'POST' && ['/agent/chat', '/agent/chat/regenerate', '/agent/chat/edit'].includes(url)) {
      return json((b) => {
        const message = b.message || (b.message_id ? '重新生成该回复' : '请帮我复习这道题')
        res.writeHead(200, {
          'Content-Type': 'text/event-stream; charset=utf-8',
          'Cache-Control': 'no-cache',
          Connection: 'keep-alive',
          'X-Accel-Buffering': 'no',
        })
        res.write('retry: 3000\n\n')
        const reply = buildReply(message)
        const { clear } = streamReply(res, reply, {})
        // 追加消息到会话仓库（供历史刷新）
        const convId = b.conversation_id
        const cmid = b.context?.client_msg_id || `cm_${Date.now()}`
        if (convId && messagesByConv.has(convId)) {
          const list = messagesByConv.get(convId)
          const ts = new Date().toISOString()
          const c = conversations.find((x) => x.id === convId)
          if (c) { c.updated_at = ts; c.title = c.title === '新对话' ? reply.lead.replace(/[#*$]/g, '').slice(0, 12) : c.title }
          list.push({
            id: `um_${Date.now()}`, role: 'user', clientMsgId: cmid, createdAt: ts,
            envelope: { msg_id: `um_${Date.now()}`, meta: {}, blocks: [{ type: 'markdown', content: message }] },
          })
          list.push({
            id: `am_${Date.now()}`, role: 'assistant', clientMsgId: cmid, createdAt: ts,
            envelope: {
              msg_id: `am_${Date.now()}`, meta: { skill: reply.skill, confidence: 0.95 },
              blocks: [
                { type: 'markdown', content: reply.lead + (reply.tail ? '\n\n' + reply.tail : '') },
                // F13：figure block 持久化（历史回显还原图形卡）
                ...(reply.figures || []).map((f) => ({ type: 'figure', ...f })),
              ],
            },
          })
        }
        req.on('close', clear)
      })
    }

    /* ---------- 文件 ---------- */
    if (method === 'POST' && url === '/files/upload') {
      return json((b) => {
        const fid = `file_${Date.now()}`
        return ok(res, { file_id: fid, upload_url: `/api/_mock_put/${fid}`, upload_id: '', deduplicated: false, expires_at: new Date(Date.now() + 3600e3).toISOString() })
      })
    }
    if (method === 'PUT' && seg[0] === '_mock_put') { res.statusCode = 200; return res.end() }
    if (method === 'POST' && seg[0] === 'files' && seg[2] === 'complete') return ok(res, { ok: true })
    if (method === 'POST' && seg[0] === 'files' && seg[2] === 'parse') return ok(res, { ok: true, parse_engine: 'mock' })
    if (method === 'GET' && seg[0] === 'files' && seg[1]) {
      return ok(res, { file_id: seg[1], status: 'parsed', parse_engine: 'mock', assets: [], content_text: '（模拟解析内容：这是一道关于导数与单调性的题目…）' })
    }
    if (method === 'GET' && seg[0] === 'files' && seg[1] && seg[2] === 'assets' && seg[4] === 'url') return ok(res, { url: '' })
    if (method === 'GET' && seg[0] === 'files' && seg[1] && seg[2] === 'content') return ok(res, { url: '', text: '' })

    /* ---------- 语音 ---------- */
    if (method === 'POST' && url === '/agent/speech/asr-token') return ok(res, { token: 'mock', expire_time: 3600 })
    if (method === 'POST' && url === '/agent/speech/to-latex') {
      return json((b) => ok(res, { latex: 'x^2', normalized_text: b.asr_text || 'x的平方', ambiguous: false }))
    }

    /* ---------- 学生端 ---------- */
    if (method === 'GET' && url === '/student/error-records/review-plan') return ok(res, reviewPlan)
    if (method === 'POST' && url === '/student/learning-events') return ok(res, { accepted: true })
    if (method === 'GET' && url === '/student/streak') return ok(res, { days: 7 })
    if (method === 'GET' && url === '/student/mastery/summary') return ok(res, masterySummary)
    if (method === 'GET' && url === '/student/mastery/trend') return ok(res, { items: [{ date: '2026-08-01', score: 58 }, { date: '2026-08-07', score: 67 }] })
    if (method === 'GET' && url === '/student/mastery/today-actions') return ok(res, { items: [] })
    if (method === 'GET' && url === '/student/lab/recommend') return ok(res, labRecommend)
    if (method === 'GET' && url === '/student/knowledge-graph') return ok(res, knowledgeGraph)
    if (method === 'GET' && url === '/student/practice/daily') return ok(res, { items: [] })
    if (method === 'GET' && url === '/student/error-records') return ok(res, { items: [] })
    if (method === 'POST' && seg[0] === 'student' && seg[1] === 'error-records' && seg[2] && seg[3] === 'review') return ok(res, { graduated: false, next_review: '2026-08-14' })
    if (method === 'POST' && url === '/student/exam/generate') return ok(res, { exam_id: 'exam_' + Date.now() })
    /* GeoGebra 动态图形（mock） */
    if (method === 'POST' && seg[0] === 'student' && seg[1] === 'error-records' && seg[2] && seg[3] === 'figure') return ok(res, { ggb: mockGgb, generated: true })
    if (method === 'POST' && url === '/figures/ggb') return ok(res, { ggb: mockGgb })

    /* ---------- M2 迭代16 · 学情聚合（模块0/1/6 growth） ---------- */
    if (method === 'GET' && url === '/student/growth/overview') return ok(res, growthOverview)
    if (method === 'GET' && url === '/student/growth/panel') return ok(res, growthPanel)
    if (method === 'GET' && url === '/student/growth/loop-progress') return ok(res, loopProgress)
    if (method === 'GET' && url === '/student/growth/today-3') return ok(res, today3)
    if (method === 'GET' && url === '/student/growth/score-trend') return ok(res, scoreTrend)
    if (method === 'GET' && url === '/student/growth/feature-entries') return ok(res, featureEntries)
    /* 功能跳转意图识别：关键词命中返回 route，未命中 matched:false（由对话主链路兜底） */
    if (method === 'POST' && url === '/agent/route-intent') {
      return json((b) => {
        const text = String(b.text || '')
        const hit = routeIntentReply.rules.find((r) => r.keywords.some((k) => text.includes(k)))
        if (!hit) return ok(res, { ...routeIntentReply.miss })
        const { keywords, ...rest } = hit
        return ok(res, { matched: true, ...rest })
      })
    }

    /* ---------- M2 迭代16 · 模块2 练题中心（查询参数可忽略，直接回 mock） ---------- */
    if (method === 'GET' && url === '/student/practice/group-recommend') return ok(res, practiceGroupRecommend)
    if (method === 'GET' && url === '/student/practice/difficulty-mix') return ok(res, difficultyMix)
    if (method === 'GET' && url === '/student/practice/smart-score') return ok(res, smartScore)
    if (method === 'GET' && url === '/student/practice/summary') return ok(res, practiceSummary)

    /* ---------- M2 迭代16 · 模块3 错题本 ---------- */
    if (method === 'GET' && url === '/student/error-records/memory-heatmap') return ok(res, memoryHeatmap)
    if (method === 'GET' && url === '/student/error-records/due-queue') return ok(res, dueQueue)
    if (method === 'GET' && url === '/student/error-records/filter') return ok(res, errorFilter)
    if (method === 'GET' && seg[0] === 'student' && seg[1] === 'error-records' && seg[2] && seg[3] === 'detail') return ok(res, { ...errorDetailExt, record_id: seg[2] })

    /* ---------- M2 迭代16 · 模块4 学情报告 ---------- */
    if (method === 'GET' && url === '/student/report/highlights') return ok(res, reportHighlights)
    if (method === 'GET' && url === '/student/report/weak-points') return ok(res, reportWeakPoints)
    if (method === 'GET' && url === '/student/report/mastery-trend-forecast') return ok(res, masteryTrendForecast)
    if (method === 'GET' && url === '/student/report/error-distribution') return ok(res, errorDistribution)
    if (method === 'GET' && url === '/student/report/honesty') return ok(res, reportHonesty)

    /* ---------- M2 迭代16 · 模块5 知识图谱 ---------- */
    if (method === 'GET' && url === '/student/knowledge-graph/pie') return ok(res, kgPie)
    if (method === 'GET' && url === '/student/knowledge-graph/tree') return ok(res, kgTree)
    if (method === 'GET' && seg[0] === 'student' && seg[1] === 'knowledge-graph' && seg[2] === 'nodes' && seg[3] && seg[4] === 'deps') return ok(res, { ...kgNodeDeps, kp_code: seg[3] })
    if (method === 'GET' && seg[0] === 'student' && seg[1] === 'knowledge-graph' && seg[2] === 'nodes' && seg[3] && seg[4] === 'recommend') return ok(res, kgNodeRecommend)

    /* ---------- M2 迭代16 第二批 · 模块7 扩展页面 ---------- */
    /* 班级动态 / 班级高频错题（class_id 任意值均回同一 mock，路径段忽略） */
    if (method === 'GET' && seg[0] === 'classes' && seg[1] && seg[2] === 'feed') return ok(res, classFeed)
    if (method === 'GET' && seg[0] === 'classes' && seg[1] && seg[2] === 'hot-errors') return ok(res, classHotErrors)
    /* 资源中心：B 站视频（分类浏览 + 学情推荐） */
    if (method === 'GET' && url === '/student/videos') return ok(res, videoBrowseMock)
    if (method === 'POST' && url === '/student/videos/recommend') return ok(res, videoRecommendMock)
    /* 课堂任务真实形态：status=todo 过滤掉已完成（mock 3 条全部未完成，原样返回） */
    if (method === 'GET' && url === '/student/assignments') return ok(res, assignmentsList)

    return fail(res, 404, 404, `mock 未实现：${method} ${url}`)
  }

  handleTeacherV3Api(req, res).then((handled) => {
    if (handled) return
    return route()
  }).catch((e) => {
    console.error('[mock] unhandled route error:', req.method, req.url, e?.stack || e)
    if (!res.headersSent) fail(res, 500, 500, e?.message || 'mock 内部错误')
    else if (!res.writableEnded) { try { res.end() } catch { /* socket already gone */ } }
  })
}
