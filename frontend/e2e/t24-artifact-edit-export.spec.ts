/**
 * T24 · 结构化编辑 / 局部重生成 / 逐页 QA / 真实导出 —— 真实浏览器 E2E。
 *
 * 运行（**harness 模式**，必须显式开启）：
 *   cross-env E2E_HARNESS=1 npx playwright test e2e/t24-artifact-edit-export.spec.ts
 *
 * harness 语义（与 t22/t23 spec 一致）：
 * - 页面走**真实** Vue 组件 / 真实 Pinia store / 真实 fetch 路径；
 * - 只在**网络边界**用 `page.route` 注入服务端契约形状的响应（mock 的是 8100，不是前端）；
 * - 不依赖真实登录态与真实 artifact 种子；真实 8100 联调由后端 T8 负责。
 */
import { expect, test, type Page } from '@playwright/test'
import { appendFileSync, mkdirSync } from 'node:fs'

const evidenceRoot = process.env.T24_EVIDENCE_DIR ?? 'D:/ppt-rescue-control/20260911-math-ai-platform-vnext/T24/screenshots'
const consoleLog = `${evidenceRoot}/console-network.log`

const viewports = [
  { name: '1280x800', width: 1280, height: 800 },
  { name: '1920x1080', width: 1920, height: 1080 },
]

/** 服务端侧的 deck 事实（路由处理器按真实契约回应，并由断言驱动变化）。 */
interface ServerDeck {
  revision: number
  slides: Array<{ id: string; title: string; content: string; notes: string; qa: 'pass' | 'fail' | 'unknown' }>
}

interface ServerState {
  deck: ServerDeck
  revisions: Array<Record<string, unknown>>
  qa: Record<string, unknown>
  lessonRevision: number
  conflictPayload: Record<string, unknown> | null
  /** 已受理的提案操作（apply 时落库，模拟服务端 INV-4 的两阶段）。 */
  pendingOps: Array<Record<string, unknown>>
  /** 最近一次局部重生成的目标页（apply 时只改它）。 */
  regenTargetId: string
  exportPolls: number
  ticketCount: number
  ticketTtlMs: number
  calls: string[]
  /** 最近一次收到的请求体（断言前端确实发了契约要求的字段）。 */
  lastPatchBody: Record<string, unknown> | null
  lastRegenBody: Record<string, unknown> | null
}

function freshState(): ServerState {
  const deck: ServerDeck = {
    revision: 3,
    slides: [
      { id: 'slide-1', title: '情境引入', content: '二次函数与抛物线', notes: '提问导入', qa: 'pass' },
      { id: 'slide-2', title: '图像探究', content: '描点作图', notes: '小组探究', qa: 'fail' },
      { id: 'slide-3', title: '归纳小结', content: '顶点式与一般式', notes: '板书', qa: 'pass' },
    ],
  }
  return {
    deck,
    revisions: [
      { revision: 1, kind: 'projection', summary: '投影生成', actor_id: 'system', created_at: '2026-09-13T01:00:00Z', affected_slide_ids: [], spec_sha256: 'sha256:deck-1' },
      { revision: 3, kind: 'patch', summary: '局部修改：第 2 页', actor_id: 'teacher-1', created_at: '2026-09-13T02:00:00Z', affected_slide_ids: ['slide-2'], spec_sha256: 'sha256:deck-3' },
    ],
    qa: {
      deck_id: 'deck-1',
      spec_revision: 3,
      artifact_id: null,
      baseline: { name: 'tp-h1', vpl_version: 'r21.1-vpl-1.0.0', rubric: 'VISUAL-RUBRIC.md', schema: 'tp-h1-visual-qa/v1' },
      checks: ['overflow', 'overlap', 'font', 'formula', 'graphic', 'duplicate', 'contrast', 'off_slide', 'blank', 'density'],
      pages: [
        { page_no: 1, status: 'pass', defects: [], metrics: { ink_pct: 5.1, cv: 0.2, shape_count: 8, has_picture: false }, attempts: 1 },
        {
          page_no: 2,
          status: 'fail',
          defects: [
            { kind: 'overflow', severity: 'p0', message: '文本框超出页面下边界 0.12in', shape_ids: ['shape-7'] },
            { kind: 'contrast', severity: 'p0', message: '正文对比度 2.1 低于 3.0', shape_ids: ['shape-3'] },
            { kind: 'font', severity: 'p1', message: '缺少数学字形', shape_ids: ['shape-9'] },
          ],
          metrics: { ink_pct: 14.5, cv: 0.9, shape_count: 21, has_picture: true },
          attempts: 1,
        },
        { page_no: 3, status: 'pass', defects: [], metrics: { ink_pct: 4.4, cv: 0.15, shape_count: 6, has_picture: false }, attempts: 1 },
      ],
      summary: { p0_count: 2, p1_count: 1, failed_pages: [2], by_kind: { overflow: 1, contrast: 1, font: 1 } },
    },
    lessonRevision: 4,
    conflictPayload: null,
    pendingOps: [],
    regenTargetId: '',
    exportPolls: 0,
    ticketCount: 0,
    ticketTtlMs: 60_000,
    calls: [],
    lastPatchBody: null,
    lastRegenBody: null,
  }
}

function deckView(state: ServerState) {
  return {
    deck_id: 'deck-1',
    spec_id: 'deck-1',
    revision: state.deck.revision,
    schema_version: 'tp-h1-deck/v1',
    spec_sha256: `sha256:deck-${state.deck.revision}`,
    metadata: { title: '二次函数复习课件' },
    slide_count: state.deck.slides.length,
    spec: { slides: state.deck.slides.map((s) => ({ id: s.id, content: s.content, notes: s.notes })) },
    slides: state.deck.slides.map((s, index) => ({ id: s.id, index, title: s.title, layout_hint: 'title_body', qa_status: s.qa })),
    lineage: {
      approved_blueprint_id: 'bp-1',
      blueprint_revision: 7,
      spec_revision: state.deck.revision,
      prompt_ref: { id: 'prep-review', version: 'v2' },
      created_by_run_id: 'run-1',
      context_manifest_id: 'ctx-1',
      evidence_refs: ['chunk-9'],
      source_refs: ['AC切片/函数'],
    },
    recent_change_log: [{ seq: 1, kind: 'patch', summary: '局部修改：第 2 页' }],
  }
}

function lessonView(state: ServerState) {
  return {
    spec_id: 'spec-1',
    revision: state.lessonRevision,
    schema_version: 'tp-h1-lesson/v1',
    spec_sha256: `sha256:lesson-${state.lessonRevision}`,
    metadata: { title: '二次函数教案' },
    spec: {
      blocks: [
        { id: 'block-1', content: '理解图像与表达式的对应关系' },
        { id: 'block-2', content: '小组描点作图' },
      ],
    },
    blocks: [
      { id: 'block-1', kind: 'objective', title: '教学目标', source_locator: 'AC切片/函数/1' },
      { id: 'block-2', kind: 'activity', title: '探究活动', source_locator: 'AC切片/函数/2' },
    ],
    lineage: {
      approved_blueprint_id: 'bp-1',
      blueprint_revision: 7,
      spec_revision: state.lessonRevision,
      created_by_run_id: 'run-1',
    },
    recent_change_log: [],
  }
}

const deckSnapshot = {
  session: { id: 'harness-session', title: '二次函数复习课', status: 'artifact_ready', revision: 9, last_event_seq: 2, class_name: '高二(3)班' },
  timeline: [],
  artifact: {
    context: { source: { title: 'AC切片教材' }, section: { title: '二次函数' } },
    blueprint: null,
    diagnostic: null,
    lesson_plan: null,
    teaching_deck: { id: 'deck-1', spec_id: 'deck-1', revision: 3, metadata: { title: '二次函数复习课件' }, slides: [] },
  },
  evidence: [],
  available_actions: [],
  job_summary: [],
  basic_mode: false,
}

const lessonSnapshot = {
  ...deckSnapshot,
  artifact: {
    ...deckSnapshot.artifact,
    teaching_deck: null,
    lesson_plan: {
      id: 'spec-1',
      spec_id: 'spec-1',
      revision: 4,
      metadata: { title: '二次函数教案' },
      blocks: [
        { id: 'block-1', title: '教学目标', kind: 'objective', content: '理解图像与表达式的对应关系' },
        { id: 'block-2', title: '探究活动', kind: 'activity', content: '小组描点作图' },
      ],
    },
  },
}

function envelope(data: unknown) {
  return JSON.stringify({ code: 0, message: 'ok', data, request_id: 'req-e2e' })
}

function conflictEnvelope(data: unknown) {
  return JSON.stringify({ code: 40900, message: 'revision conflict', data, request_id: 'req-409' })
}

async function installRoutes(page: Page, state: ServerState, snapshot: unknown) {
  // 8100 prep 会话（真实 snapshot 形状；SSE 只回心跳）
  await page.route('**/api/teacher-v3/prep/conversations/**', async (route) => {
    if (route.request().url().includes('/events')) {
      await route.fulfill({ status: 200, contentType: 'text/event-stream', body: ': ping\n\n' })
      return
    }
    await route.fulfill({ status: 200, contentType: 'application/json', body: envelope(snapshot) })
  })

  // T24 artifacts 命名空间（契约 S2.4）
  await page.route('**/api/teacher-v3/artifacts/**', async (route) => {
    const request = route.request()
    const method = request.method().toUpperCase()
    const path = new URL(request.url()).pathname.replace('/api/teacher-v3/artifacts', '')
    const body = request.postDataJSON?.() ?? null
    state.calls.push(`${method} ${path}`)
    const json = (data: unknown, status = 200) =>
      route.fulfill({ status, contentType: 'application/json', body: status === 409 ? conflictEnvelope(data) : envelope(data) })

    if (path === '/decks/deck-1' && method === 'GET') return json(deckView(state))
    if (path === '/decks/deck-1/revisions' && method === 'GET') return json({ current_revision: state.deck.revision, revisions: state.revisions })
    if (path === '/decks/deck-1/qa' && method === 'GET') return json(state.qa)
    if (path === '/decks/deck-1/qa/retry' && method === 'POST') {
      const pageNo = Number((body as Record<string, unknown>)?.page_no)
      const pages = (state.qa.pages as Array<Record<string, unknown>>).map((p) =>
        p.page_no === pageNo ? { ...p, status: 'pass', defects: [], attempts: 2, metrics: { ink_pct: 4, cv: 0.1, shape_count: 9, has_picture: true } } : p,
      )
      state.qa = { ...state.qa, pages, summary: { p0_count: 0, p1_count: 0, failed_pages: [], by_kind: {} } }
      const target = state.deck.slides[pageNo - 1]
      if (target) target.qa = 'pass'
      return json({ page_no: pageNo, attempts: 2, status: 'pass', defects: [], metrics: { ink_pct: 4, cv: 0.1, shape_count: 9, has_picture: true }, others_unchanged: true })
    }
    if (path === '/decks/deck-1/patches' && method === 'POST') {
      if (state.conflictPayload) {
        const payload = state.conflictPayload
        state.conflictPayload = null
        return json(payload, 409)
      }
      const operations = ((body as { operations?: Array<Record<string, unknown>> }).operations ?? [])
      state.pendingOps = operations
      return json({
        patch_id: 'patch-e2e-1',
        deck_id: 'deck-1',
        status: 'proposed',
        base_revision: state.deck.revision,
        expected_revision: (body as Record<string, unknown>)?.expected_revision ?? null,
        affected: { slide_ids: [operations[0]?.target] },
        operations,
      })
    }
    if (path.startsWith('/decks/deck-1/patches/') && path.endsWith('/apply') && method === 'POST') {
      const patchId = path.slice('/decks/deck-1/patches/'.length, -'/apply'.length)
      if (patchId === 'patch-regen-1') {
        const target = state.deck.slides.find((s) => s.id === state.regenTargetId)
        if (target) target.content = '重生成后的第 2 页内容'
      } else {
        for (const op of state.pendingOps) {
          const slide = state.deck.slides.find((s) => s.id === op.target)
          if (slide && typeof op.value === 'string') {
            if (op.field === 'content') slide.content = op.value
            if (op.field === 'notes') slide.notes = op.value
          }
        }
        state.pendingOps = []
      }
      state.deck.revision += 1
      state.revisions = [...state.revisions, { revision: state.deck.revision, kind: patchId === 'patch-regen-1' ? 'regen' : 'patch', summary: '局部修改', actor_id: 'teacher-1', created_at: '2026-09-13T03:00:00Z', affected_slide_ids: [state.regenTargetId || 'slide-2'], spec_sha256: `sha256:deck-${state.deck.revision}` }]
      return json({ deck_id: 'deck-1', revision: state.deck.revision, applied_patch_id: patchId, affected: { slide_ids: [state.regenTargetId || 'slide-2'] }, change_log_entry: { seq: 2 } })
    }
    if (path === '/decks/deck-1/regen' && method === 'POST') {
      const target = (body as { target?: Record<string, unknown> }).target ?? {}
      state.regenTargetId = String(target.slide_id ?? '')
      return json({
        patch_id: 'patch-regen-1',
        target,
        changed_slide_ids: [String(target.slide_id)],
        proposal: {
          patch_id: 'patch-regen-1',
          status: 'proposed',
          base_revision: state.deck.revision,
          expected_revision: (body as Record<string, unknown>)?.expected_revision ?? null,
          affected: { slide_ids: [String(target.slide_id)] },
          operations: [{ op: 'set_slide', target: String(target.slide_id), field: 'content', value: '重生成后的第 2 页内容' }],
        },
        refs_echo: (body as Record<string, unknown>)?.refs ?? {},
        provider: { model: 'mimo-v2.5-pro', run_id: 'run-regen-1' },
      })
    }
    if (path === '/decks/deck-1/revisions/1/revert' && method === 'POST') {
      state.deck.revision += 1
      state.revisions = [...state.revisions, { revision: state.deck.revision, kind: 'revert', summary: '撤销到第 1 版', actor_id: 'teacher-1', created_at: '2026-09-13T04:00:00Z', affected_slide_ids: ['slide-2'], spec_sha256: `sha256:deck-${state.deck.revision}` }]
      return json({ deck_id: 'deck-1', revision: state.deck.revision, reverted_to: 1, affected: { slide_ids: ['slide-2'] } })
    }
    if (path === '/decks/deck-1/exports' && method === 'POST') {
      state.exportPolls = 0
      return json({ job_id: 'job-e2e-1', format: (body as Record<string, unknown>)?.format ?? 'pptx', status: 'pending', task_id: 'task-1' })
    }
    if (path === '/exports/job-e2e-1' && method === 'GET') {
      state.exportPolls += 1
      if (state.exportPolls < 2) {
        return json({ job_id: 'job-e2e-1', format: 'pptx', status: 'running', progress: { stage: 'render', page_no: 3, page_count: 12 }, events: [] })
      }
      return json({ job_id: 'job-e2e-1', format: 'pptx', status: 'done', artifact_id: 'artifact-e2e-1', events: [{ seq: 2, type: 'done', ts: '2026-09-13T05:00:00Z', detail: null }] })
    }
    if (path === '/artifact-e2e-1/tickets' && method === 'POST') {
      state.ticketCount += 1
      return json({
        ticket: `ticket-e2e-${state.ticketCount}`,
        url: `/api/teacher-v3/artifacts/downloads/ticket-e2e-${state.ticketCount}`,
        expires_at: new Date(Date.now() + state.ticketTtlMs).toISOString(),
        single_use: true,
      })
    }
    if (path === '/lesson-plans/spec-1' && method === 'GET') return json(lessonView(state))
    if (path === '/lesson-plans/spec-1/revisions' && method === 'GET') {
      return json({ current_revision: state.lessonRevision, revisions: [{ revision: state.lessonRevision, kind: 'patch', summary: '局部修改：教学目标', actor_id: 'teacher-1', created_at: '2026-09-13T02:00:00Z', affected_slide_ids: [], spec_sha256: `sha256:lesson-${state.lessonRevision}` }] })
    }
    if (path === '/lesson-plans/spec-1/patches' && method === 'POST') {
      state.lastPatchBody = body
      return json({ patch_id: 'lp-patch-1', status: 'proposed', base_revision: state.lessonRevision, expected_revision: (body as Record<string, unknown>)?.expected_revision ?? null, affected: { block_ids: ['block-1'] }, operations: (body as Record<string, unknown>)?.operations ?? [] })
    }
    if (path === '/lesson-plans/spec-1/regen' && method === 'POST') {
      state.lastRegenBody = body
      const target = (body as { target?: Record<string, unknown> }).target ?? {}
      const blockId = String(target.block_id ?? '')
      return json({
        patch_id: 'lp-regen',
        target,
        changed_block_ids: [blockId],
        proposal: {
          patch_id: 'lp-regen',
          status: 'proposed',
          base_revision: state.lessonRevision,
          expected_revision: (body as Record<string, unknown>)?.expected_revision ?? null,
          affected: { block_ids: [blockId] },
          operations: [{ op: 'set_block', target: blockId, field: 'content', value: '把活动拆成两步' }],
        },
        refs_echo: (body as Record<string, unknown>)?.refs ?? {},
        provider: { model: 'mimo-v2.5-pro', run_id: 'run-regen-lp' },
      })
    }
    if (path === '/lesson-plans/spec-1/patches/lp-patch-1/apply' && method === 'POST') {
      state.lessonRevision += 1
      return json({ spec_id: 'spec-1', revision: state.lessonRevision, applied_patch_id: 'lp-patch-1', affected: { block_ids: ['block-1'] } })
    }
    return route.fulfill({ status: 404, contentType: 'application/json', body: JSON.stringify({ code: 40400, message: `unrouted ${method} ${path}`, data: null }) })
  })
}

function trackConsole(page: Page, label: string) {
  const errors: string[] = []
  const failures: string[] = []
  const badResponses: string[] = []
  page.on('console', (message) => {
    if (message.type() === 'error') errors.push(message.text())
  })
  page.on('requestfailed', (request) => {
    failures.push(`${request.method()} ${request.url()} :: ${request.failure()?.errorText ?? 'unknown'}`)
  })
  page.on('response', (response) => {
    if (response.status() >= 400) badResponses.push(`${response.status()} ${response.request().method()} ${new URL(response.url()).pathname}`)
  })
  return (state?: ServerState) => {
    appendFileSync(
      consoleLog,
      `${label}\nconsoleErrors=${JSON.stringify(errors)}\nnetworkFailures=${JSON.stringify(failures)}\nhttpErrors=${JSON.stringify(badResponses)}\nt24Calls=${JSON.stringify(state?.calls ?? [])}\n`,
    )
    return { errors, failures, badResponses }
  }
}

/** 无论成败都留下控制台/网络存证（失败时必须能回答"卡在哪一步"）。 */
async function withEvidence<T>(dump: () => unknown, body: () => Promise<T>): Promise<T> {
  try {
    return await body()
  } finally {
    dump()
  }
}

/** 进入 artifact_focus（真实 store.openFocus 路径，不做测试后门）。 */
async function openFocus(page: Page, snapshot: unknown, state: ServerState) {
  await installRoutes(page, state, snapshot)
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await page.goto('/src/features/teacher-prep-v2/__harness__/index.html#/teacher-v3/prep-immersive/harness-session?focus=artifact')
  await expect(page.getByTestId('prep-artifact-focus')).toBeVisible()
}

test.describe.configure({ mode: 'serial' })

// ── A · deck：编辑 → 提案 → 应用；QA 失败页定位 + 单页重试 ────────────────

test.describe('T24 · TeachingDeckSpec 编辑 / QA / 导出', () => {
  for (const viewport of viewports) {
    test(`deck 编辑 + 逐页 QA + 冲突 + 导出 @${viewport.name}`, async ({ page }) => {
      test.skip(process.env.E2E_HARNESS !== '1', 'harness-only: intercepts the network boundary')
      mkdirSync(evidenceRoot, { recursive: true })
      const state = freshState()
      const dump = trackConsole(page, `t24-deck@${viewport.name}`)
      await withEvidence(() => dump(state), async () => {
      await page.setViewportSize({ width: viewport.width, height: viewport.height })
      await openFocus(page, deckSnapshot, state)

      // 两栏形态：主画布 + 可收起助手（不是 stepper / wizard / 卡片墙）
      await expect(page.getByTestId('prep-artifact-canvas')).toBeVisible()
      await expect(page.getByTestId('prep-focus-assistant')).toBeVisible()
      await expect(page.getByTestId('t24-teaching-deck-focus')).toBeVisible()
      await expect(page.getByTestId('t24-slide-navigator')).toBeVisible()

      // 逐页 QA 状态标记来自服务端（第 2 页 fail）
      await expect(page.getByTestId('t24-slide-qa-2')).toHaveAttribute('data-state', 'fail')

      // QA 失败页缺陷（中文维度）+ 单页重试（只作用于当前页）
      await expect(page.getByTestId('t24-qa-page-2')).toContainText('内容溢出')
      await expect(page.getByTestId('t24-qa-page-2')).toContainText('对比度不足')
      await expect(page.getByTestId('t24-qa-retry-2')).toBeDisabled()
      await page.getByTestId('t24-qa-locate-2').click()
      await expect(page.getByTestId('t24-qa-retry-2')).toBeEnabled()
      await page.getByTestId('t24-qa-retry-2').click()
      await expect(page.getByTestId('t24-slide-qa-2')).toHaveAttribute('data-state', 'pass')

      // 页/元素级编辑 → 提案（真实 POST）+ 应用（真实 POST）
      await expect(page.getByTestId('t24-slide-content')).toHaveValue('描点作图')
      await page.getByTestId('t24-slide-content').fill('描点作图（补充对称性）')
      await page.getByTestId('t24-slide-content-propose').click()
      await expect(page.getByTestId('t24-pending-proposal')).toBeVisible()
      await expect(page.getByTestId('t24-revision-value')).toContainText('3')
      await page.getByTestId('t24-apply-patch').click()
      await expect(page.getByTestId('t24-revision-value')).toContainText('4')
      await expect(page.getByTestId('t24-slide-content')).toHaveValue('描点作图（补充对称性）')

      // 局部重生成：只针对当前页
      await page.getByTestId('t24-slide-regen-instruction').fill('把这一页的推导补全')
      await page.getByTestId('t24-slide-regen-submit').click()
      await expect(page.getByTestId('t24-pending-proposal')).toBeVisible()
      await page.getByTestId('t24-apply-patch').click()
      await expect(page.getByTestId('t24-slide-content')).toHaveValue('重生成后的第 2 页内容')

      // 409 冲突：服务端 diff + 三个明确动作（禁止静默覆盖）
      state.calls.length = 0
      state.conflictPayload = {
        kind: 'revision_conflict',
        expected_revision: state.deck.revision,
        current_revision: state.deck.revision + 2,
        server_spec_sha256: 'sha256:deck-server',
        diff: {
          summary_zh: '第 2 页内容已在别处更新',
          fields: [{ target: 'slide-2', field: 'content', base_value: '描点作图（补充对称性）', server_value: '服务端已经改好的内容' }],
          slide_ids: ['slide-2'],
        },
        operations: [{ index: 0, status: 'conflict', message: '目标页已被更新' }],
        server_snapshot: { revision: state.deck.revision + 2 },
      }
      await page.getByTestId('t24-slide-content').fill('我要覆盖的改动')
      await page.getByTestId('t24-slide-content-propose').click()
      const resolver = page.getByTestId('t24-conflict-resolver')
      await expect(resolver).toBeVisible()
      await expect(page.getByTestId('t24-conflict-fields')).toContainText('服务端已经改好的内容')
      await expect(page.getByTestId('t24-conflict-summary')).toContainText('第 2 页内容已在别处更新')
      await expect(page.getByTestId('t24-conflict-reload')).toBeVisible()
      await expect(page.getByTestId('t24-conflict-reapply')).toBeVisible()
      await expect(page.getByTestId('t24-conflict-discard')).toBeVisible()
      // 冲突未解决 → 没有可应用的提案（apply 入口消失），且**没有**发出任何 apply 请求
      await expect(page.getByTestId('t24-apply-patch')).toHaveCount(0)
      expect(state.calls.filter((c) => c.endsWith('/apply'))).toEqual([])
      await page.getByTestId('t24-conflict-reload').click()
      await expect(page.getByTestId('t24-conflict-reloaded')).toBeVisible()
      await page.getByTestId('t24-conflict-reapply').click()
      await expect(resolver).toBeHidden()
      // 重提成功 → 重新出现待应用提案（以服务端最新 revision 为基准）
      await expect(page.getByTestId('t24-pending-proposal')).toBeVisible()

      // 版本历史来自服务端 + 撤销生成**新** revision
      await page.getByTestId('t24-revision-history-toggle').click()
      await expect(page.getByTestId('t24-revision-history')).toBeVisible()
      await page.getByTestId('t24-revert-1').click()
      await expect(page.getByTestId('t24-revision-value')).toContainText(String(state.deck.revision))

      // 导出：真实作业状态机 → 短时票据 → 下载链接；过期后"重新获取"
      await page.getByTestId('t24-export-pptx').click()
      await expect(page.getByTestId('t24-export-progress')).toContainText('3/12')
      const download = page.getByTestId('t24-export-download-pptx')
      await expect(download).toBeVisible({ timeout: 15_000 })
      expect(state.ticketCount).toBe(1)
      expect(state.calls.some((c) => c.startsWith('POST /decks/deck-1/exports'))).toBe(true)
      expect(state.calls.some((c) => c.startsWith('GET /exports/job-e2e-1'))).toBe(true)
      expect(state.calls.some((c) => c.startsWith('POST /artifact-e2e-1/tickets'))).toBe(true)

      await page.screenshot({ path: `${evidenceRoot}/t24-deck-${viewport.name}.png`, fullPage: true })
      const { errors } = dump()
      // 允许的两类"错误"都是**刻意**的：
      //  - naive-ui 图标在 harness 下的解析告警（非业务错误）；
      //  - 浏览器对**故意注入的 409** 的资源级日志（正是冲突流程要证明的）。
      const unexpected = errors.filter((e) => !e.includes('n-icon') && !e.includes('409'))
      expect(unexpected).toEqual([])
      })
    })
  }
})

// ── B · 导出短时链接过期 → 重新获取（INV-7） ─────────────────────────────

test.describe('T24 · 导出下载票据过期', () => {
  test('expires_at 到期后展示"已过期"并提供重新获取', async ({ page }) => {
    test.skip(process.env.E2E_HARNESS !== '1', 'harness-only: intercepts the network boundary')
    mkdirSync(evidenceRoot, { recursive: true })
    const state = freshState()
    // 短时票据：3 秒后到期（契约默认 300s，这里为了可测而缩短）
    state.ticketTtlMs = 3_000
    const dump = trackConsole(page, 't24-export-expiry@1280x800')
    await withEvidence(() => dump(state), async () => {
      await page.setViewportSize({ width: 1280, height: 800 })
      await openFocus(page, deckSnapshot, state)

      await page.getByTestId('t24-export-pptx').click()
      await expect(page.getByTestId('t24-export-download-pptx')).toBeVisible({ timeout: 15_000 })
      expect(state.ticketCount).toBe(1)

      // 到期由服务端 expires_at 决定（前端只读事实，不用定时器伪造）
      await expect(page.getByTestId('t24-export-expired')).toBeVisible({ timeout: 15_000 })
      await expect(page.getByTestId('t24-export-download-pptx')).toHaveCount(0)

      await page.getByTestId('t24-export-refresh-pptx').click()
      await expect(page.getByTestId('t24-export-download-pptx')).toBeVisible({ timeout: 15_000 })
      expect(state.ticketCount).toBe(2)
      expect(state.calls.filter((c) => c.startsWith('POST /artifact-e2e-1/tickets')).length).toBe(2)

      await page.screenshot({ path: `${evidenceRoot}/t24-export-refreshed-1280x800.png`, fullPage: true })
      const { errors } = dump()
      expect(errors.filter((e) => !e.includes('n-icon') && !e.includes('409'))).toEqual([])
    })
  })
})

// ── C · lesson plan：块级编辑 → 提案（含 before） ────────────────────────

test.describe('T24 · LessonPlanSpec 块级编辑', () => {
  test('lesson plan 块级 propose 携带 operations/before 与 expected_revision', async ({ page }) => {
    test.skip(process.env.E2E_HARNESS !== '1', 'harness-only: intercepts the network boundary')
    mkdirSync(evidenceRoot, { recursive: true })
    const state = freshState()
    const dump = trackConsole(page, 't24-lesson-plan@1440x900')
    await withEvidence(() => dump(state), async () => {
    await page.setViewportSize({ width: 1440, height: 900 })
    await openFocus(page, lessonSnapshot, state)

    await expect(page.getByTestId('t24-lesson-plan-focus')).toBeVisible()
    const editors = page.getByTestId('t24-block-editor')
    await expect(editors).toHaveCount(2)
    await editors.first().fill('能说出图像与表达式的对应关系')
    await page.getByTestId('t24-block-propose').first().click()
    await expect(page.getByTestId('t24-pending-proposal')).toContainText('lp-patch-1')
    await expect(page.getByTestId('t24-revision-value')).toContainText('4')

    // 服务端确实收到了 operations（含 before）与 expected_revision
    const posts = state.calls.filter((c) => c === 'POST /lesson-plans/spec-1/patches')
    expect(posts.length).toBe(1)
    expect(state.lastPatchBody).toMatchObject({
      expected_revision: 4,
      operations: [{ op: 'set_block', target: 'block-1', field: 'content', value: '能说出图像与表达式的对应关系', before: '理解图像与表达式的对应关系' }],
    })

    // 局部重生成：只针对选中块；服务端返回后被采纳为新的待应用提案
    await expect(page.getByTestId('t24-lesson-regen')).toBeVisible()
    await editors.nth(1).click()
    await page.getByTestId('t24-lesson-regen-instruction').fill('把活动拆成两步')
    await page.getByTestId('t24-lesson-regen-submit').click()
    await expect(page.getByTestId('t24-pending-proposal')).toContainText('lp-regen')
    expect(state.calls.filter((c) => c === 'POST /lesson-plans/spec-1/regen').length).toBe(1)
    expect(state.lastRegenBody).toMatchObject({
      expected_revision: 4,
      target: { kind: 'block', block_id: 'block-2' },
      instruction: '把活动拆成两步',
      refs: { approved_blueprint_id: 'bp-1', blueprint_revision: 7, spec_revision: 4 },
    })

    await page.screenshot({ path: `${evidenceRoot}/t24-lesson-plan-1440x900.png`, fullPage: true })
    const { errors } = dump()
    expect(errors.filter((e) => !e.includes('n-icon') && !e.includes('409'))).toEqual([])
    })
  })
})
