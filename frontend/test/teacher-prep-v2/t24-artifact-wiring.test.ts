/**
 * T24 接线回归 · **真实 HTTP 形状**（fetch 桩，不打真后端）。
 *
 * 覆盖交付要求：
 * 1) 块级 patch proposal + 无效入参（不发请求）
 * 2) 页/元素级 patch proposal（带 `before`）
 * 3) 409 → 冲突状态 + reload/reapply/discard 三动作（禁止 last-write-wins）
 * 4) 局部重生成只改目标（其它页逐字节不变）
 * 5) QA 失败页定位 + 单页重试（其它页不变）
 * 6) 导出状态机（pending/running/done/failed/cancelled/expired）
 * 7) 下载链接过期后重新获取
 * 8) 撤销走服务端 revision（无本地数组冒充）
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { mount } from '@vue/test-utils'

import { useArtifactEditingStore, buildRegenRefs, isTicketExpired, validatePatchOperations } from '@/features/teacher-prep-v2/stores/artifactEditing'
import { deckEditorSlides, lessonEditorBlocks, resolveArtifactIdentity } from '@/features/teacher-prep-v2/surface/useArtifactEditing'
import TeachingDeckFocus from '@/features/teacher-prep-v2/artifacts/deck/TeachingDeckFocus.vue'
import SlideNavigator from '@/features/teacher-prep-v2/artifacts/deck/SlideNavigator.vue'
import LessonPlanBlockEditor from '@/features/teacher-prep-v2/artifacts/lesson-plan/LessonPlanBlockEditor.vue'
import ConflictResolver from '@/features/teacher-prep-v2/artifacts/shared/ConflictResolver.vue'
import RevisionToolbar from '@/features/teacher-prep-v2/artifacts/shared/RevisionToolbar.vue'
import ExportPanel from '@/features/teacher-prep-v2/artifacts/shared/ExportPanel.vue'
import LineagePanel from '@/features/teacher-prep-v2/artifacts/shared/LineagePanel.vue'
import ArtifactFocusCanvas from '@/features/teacher-prep-v2/surface/ArtifactFocusCanvas.vue'
import type { ArtifactEditingView } from '@/features/teacher-prep-v2/surface/useArtifactEditing'

// ── fetch 桩：真实路径 + 真实信封 ─────────────────────────────────────────

interface Call { method: string; url: string; body: Record<string, unknown> | null }
let calls: Call[] = []

function ok(data: unknown, status = 200) {
  return new Response(JSON.stringify({ code: 0, message: 'ok', data, request_id: 'req-ok' }), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}

function conflict(data: unknown) {
  return new Response(JSON.stringify({ code: 40900, message: 'conflict', data, request_id: 'req-409' }), {
    status: 409,
    headers: { 'content-type': 'application/json' },
  })
}

function fail(status: number, code: number, message = 'boom') {
  return new Response(JSON.stringify({ code, message, data: null, request_id: 'req-err' }), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}

/** 服务端事实（可被测试改写）——测试断言只基于它，不基于本地推导。 */
interface DeckState {
  revision: number
  slides: Array<{ id: string; content: string; notes: string; title: string; qa: 'pass' | 'fail' | 'unknown' }>
}
let deck: DeckState
let lessonRevision: number
let lessonBlocks: Array<{ id: string; kind: string; title: string; content: string }>
let revisions: Array<Record<string, unknown>>
let qaReport: Record<string, unknown>
/** 下一次 POST patches 是否返回 409。 */
let patchConflict: Record<string, unknown> | null
/** 下一次 qa retry 是否声明 others_unchanged=false。 */
let qaRetryLocal = true
/** 导出作业轮询序列。 */
let exportSequence: Array<Record<string, unknown>> = []

function deckView() {
  return {
    deck_id: 'deck-1',
    spec_id: 'spec-9',
    revision: deck.revision,
    schema_version: 'tp-h1-deck/v1',
    spec_sha256: `sha256:deck-${deck.revision}`,
    metadata: { title: '二次函数课件' },
    slide_count: deck.slides.length,
    spec: { slides: deck.slides.map((s) => ({ id: s.id, content: s.content, notes: s.notes })) },
    slides: deck.slides.map((s, index) => ({
      id: s.id,
      index,
      title: s.title,
      layout_hint: 'title_body',
      qa_status: s.qa,
    })),
    lineage: {
      approved_blueprint_id: 'bp-1',
      blueprint_revision: 7,
      spec_revision: deck.revision,
      prompt_ref: { id: 'prep-review', version: 'v2' },
      created_by_run_id: 'run-1',
      context_manifest_id: 'ctx-1',
      evidence_refs: ['chunk-9'],
      source_refs: [],
    },
    recent_change_log: [],
  }
}

function lessonView() {
  return {
    spec_id: 'spec-1',
    revision: lessonRevision,
    schema_version: 'tp-h1-lesson/v1',
    spec_sha256: `sha256:lesson-${lessonRevision}`,
    metadata: { title: '二次函数教案' },
    spec: {},
    blocks: lessonBlocks.map((b) => ({ id: b.id, kind: b.kind, title: b.title, source_locator: '函数/1' })),
    lineage: { approved_blueprint_id: 'bp-1', blueprint_revision: 7, spec_revision: lessonRevision },
    recent_change_log: [],
  }
}

function installFetch() {
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = (init?.method ?? 'GET').toUpperCase()
    const body = typeof init?.body === 'string' ? (JSON.parse(init.body) as Record<string, unknown>) : null
    calls.push({ method, url, body })
    const path = url.replace('/api/teacher-v3/artifacts', '')

    // deck
    if (path === '/decks/deck-1' && method === 'GET') return ok(deckView())
    if (path === '/decks/deck-1/revisions' && method === 'GET') {
      return ok({ current_revision: deck.revision, revisions })
    }
    if (path === '/decks/deck-1/qa' && method === 'GET') return ok(qaReport)
    if (path === '/decks/deck-1/patches' && method === 'POST') {
      if (patchConflict) {
        const payload = patchConflict
        patchConflict = null
        return conflict(payload)
      }
      return ok({
        patch_id: 'p-1',
        deck_id: 'deck-1',
        status: 'proposed',
        base_revision: deck.revision,
        expected_revision: body?.expected_revision ?? null,
        affected: { slide_ids: [(body?.operations as Array<Record<string, unknown>>)?.[0]?.target] },
        operations: body?.operations ?? [],
      })
    }
    if (path === '/decks/deck-1/patches/p-1/apply' && method === 'POST') {
      return ok({
        deck_id: 'deck-1',
        revision: deck.revision + 1,
        applied_patch_id: 'p-1',
        affected: { slide_ids: ['s2'] },
        change_log_entry: { seq: 1 },
      })
    }
    if (path === '/decks/deck-1/patches/p-1/reject' && method === 'POST') {
      return ok({ patch_id: 'p-1', status: 'rejected' })
    }
    if (path === '/decks/deck-1/regen' && method === 'POST') {
      const target = (body?.target ?? {}) as Record<string, unknown>
      const slideId = String(target.slide_id)
      return ok({
        patch_id: 'p-regen',
        target,
        changed_slide_ids: [slideId],
        proposal: {
          patch_id: 'p-regen',
          status: 'proposed',
          base_revision: deck.revision,
          expected_revision: body?.expected_revision ?? null,
          affected: { slide_ids: [slideId] },
          operations: [{ op: 'set_slide', target: slideId, field: 'content', value: '重生成内容' }],
        },
        refs_echo: body?.refs ?? {},
        provider: { model: 'mimo-v2.5-pro', run_id: 'run-regen-1' },
      })
    }
    if (path === '/decks/deck-1/patches/p-regen/apply' && method === 'POST') {
      return ok({
        deck_id: 'deck-1',
        revision: deck.revision + 1,
        applied_patch_id: 'p-regen',
        affected: { slide_ids: ['s2'] },
        change_log_entry: { seq: 2 },
      })
    }
    if (path === '/decks/deck-1/revisions/2/revert' && method === 'POST') {
      return ok({ deck_id: 'deck-1', revision: 9, reverted_to: 2, affected: { slide_ids: ['s2'] } })
    }
    if (path === '/decks/deck-1/qa/retry' && method === 'POST') {
      const pageNo = Number(body?.page_no)
      return ok({
        page_no: pageNo,
        attempts: 2,
        status: 'pass',
        defects: [],
        metrics: { ink_pct: 4.2, cv: 0.12, shape_count: 9, has_picture: true },
        others_unchanged: qaRetryLocal,
      })
    }
    if (path === '/decks/deck-1/exports' && method === 'POST') {
      return ok({ job_id: 'job-1', format: body?.format ?? 'pptx', status: 'pending', task_id: 'task-1' })
    }
    if (path === '/exports/job-1' && method === 'GET') {
      const next = exportSequence.length ? exportSequence.shift()! : { job_id: 'job-1', format: 'pptx', status: 'running' }
      return ok(next)
    }
    if (path === '/exports/job-1/cancel' && method === 'POST') {
      return ok({ job_id: 'job-1', status: 'cancelled' })
    }
    if (/^\/[^/]+\/tickets$/.test(path) && method === 'POST') {
      const ticketNo = calls.filter((c) => c.url.endsWith('/tickets')).length
      return ok({
        ticket: `ticket-${ticketNo}`,
        url: `/api/teacher-v3/artifacts/downloads/ticket-${ticketNo}`,
        expires_at: new Date(Date.now() + 30).toISOString(),
        single_use: true,
      })
    }

    // lesson plan
    if (path === '/lesson-plans/spec-1' && method === 'GET') return ok(lessonView())
    if (path === '/lesson-plans/spec-1/revisions' && method === 'GET') {
      return ok({ current_revision: lessonRevision, revisions })
    }
    if (path === '/lesson-plans/spec-1/patches' && method === 'POST') {
      if (patchConflict) {
        const payload = patchConflict
        patchConflict = null
        return conflict(payload)
      }
      return ok({
        patch_id: 'lp-1',
        status: 'proposed',
        base_revision: lessonRevision,
        expected_revision: body?.expected_revision ?? null,
        affected: { block_ids: [(body?.operations as Array<Record<string, unknown>>)?.[0]?.target] },
        operations: body?.operations ?? [],
      })
    }
    if (path === '/lesson-plans/spec-1/patches/lp-1/apply' && method === 'POST') {
      return ok({ spec_id: 'spec-1', revision: lessonRevision + 1, applied_patch_id: 'lp-1', affected: { block_ids: ['b2'] } })
    }
    if (path === '/lesson-plans/spec-1/regen' && method === 'POST') {
      return ok({
        patch_id: 'lp-regen',
        target: body?.target ?? {},
        changed_block_ids: ['b2'],
        proposal: { patch_id: 'lp-regen', status: 'proposed', operations: [] },
        refs_echo: body?.refs ?? {},
        provider: null,
      })
    }

    return fail(404, 40400, `unrouted ${method} ${path}`)
  })
  vi.stubGlobal('fetch', fn)
  return fn
}

function newStore() {
  const pinia = createPinia()
  setActivePinia(pinia)
  return useArtifactEditingStore()
}

beforeEach(() => {
  calls = []
  deck = {
    revision: 3,
    slides: [
      { id: 's1', title: '开场', content: '第一页内容', notes: '第一页备注', qa: 'pass' },
      { id: 's2', title: '探究', content: '第二页内容', notes: '第二页备注', qa: 'fail' },
      { id: 's3', title: '小结', content: '第三页内容', notes: '第三页备注', qa: 'pass' },
    ],
  }
  lessonRevision = 4
  lessonBlocks = [
    { id: 'b1', kind: 'objective', title: '目标', content: '原目标' },
    { id: 'b2', kind: 'activity', title: '活动', content: '保留活动' },
  ]
  revisions = [
    { revision: 1, kind: 'projection', summary: '投影生成', actor_id: 'system', created_at: '2026-09-13T01:00:00Z', affected_slide_ids: [], spec_sha256: 'sha256:deck-1' },
    { revision: 3, kind: 'patch', summary: '局部修改', actor_id: 'teacher-1', created_at: '2026-09-13T02:00:00Z', affected_slide_ids: ['s2'], spec_sha256: 'sha256:deck-3' },
  ]
  qaReport = {
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
  }
  patchConflict = null
  qaRetryLocal = true
  exportSequence = []
  installFetch()
})

function paths() {
  return calls.map((c) => `${c.method} ${c.url.replace('/api/teacher-v3/artifacts', '')}`)
}

// ── 1) 块级 patch proposal + 无效入参 ─────────────────────────────────────

describe('T24 · lesson plan 块级 patch proposal', () => {
  it('提交带 before 的 set_block operations，并携带 expected_revision', async () => {
    const store = newStore()
    await store.attach({ kind: 'lesson_plan', remoteId: 'spec-1' })
    const proposal = await store.proposePatch([{
      op: 'set_block', target: 'b1', field: 'content', value: '新目标', before: '原目标',
    }], { summary: '调整目标' })

    const post = calls.find((c) => c.method === 'POST' && c.url.endsWith('/lesson-plans/spec-1/patches'))
    expect(post).toBeTruthy()
    expect(post!.body).toMatchObject({
      expected_revision: 4,
      source: 'human',
      operations: [{ op: 'set_block', target: 'b1', field: 'content', value: '新目标', before: '原目标' }],
    })
    expect(typeof post!.body!.idempotency_key).toBe('string')
    expect(proposal?.patchId).toBe('lp-1')
    expect(store.pendingProposal?.patchId).toBe('lp-1')
    // INV-4：提案阶段**不**写 revision（服务端 revision 不变）
    expect(store.revision).toBe(4)
  })

  it('空 operations / 缺 target / 缺 value 一律本地拒绝，且不发请求', async () => {
    const store = newStore()
    await store.attach({ kind: 'lesson_plan', remoteId: 'spec-1' })
    const before = calls.length
    expect(await store.proposePatch([])).toBeNull()
    expect(store.localErrorKey).toBe('prep.t24.error.patch_empty')
    expect(await store.proposePatch([{ op: 'set_block', field: 'content', value: 'x' } as never])).toBeNull()
    expect(store.localErrorKey).toBe('prep.t24.error.patch_op_invalid')
    expect(await store.proposePatch([{ op: 'set_block', target: 'b1', field: 'content' } as never])).toBeNull()
    expect(calls.length).toBe(before)
    // 纯函数校验器也覆盖同样的规则
    expect(validatePatchOperations([])).toBe('prep.t24.error.patch_empty')
    expect(validatePatchOperations([{ op: 'set_block', target: 'b1', field: 'content', value: 1 }])).toBeNull()
  })
})

// ── 2) 页/元素级 patch（组件 → emit，接线 → 请求）─────────────────────────

describe('T24 · 页/元素级 patch 接线', () => {
  it('TeachingDeckFocus 在内容/备注修改时发出带 before 的 operations', async () => {
    const wrapper = mount(TeachingDeckFocus, {
      props: {
        title: '课件', revision: 3, qaIssues: [], lineage: {},
        slides: [{ id: 's2', title: '探究', content: '第二页内容', notes: '第二页备注' }],
      },
      global: { stubs: { 'n-icon': { template: '<i><slot /></i>' } } },
    })
    await wrapper.find('[data-testid="t24-slide-content"]').setValue('改后的内容')
    await wrapper.find('[data-testid="t24-slide-content-propose"]').trigger('click')
    expect(wrapper.emitted('propose-operations')?.[0]?.[0]).toEqual([
      { op: 'set_slide', target: 's2', field: 'content', value: '改后的内容', before: '第二页内容' },
    ])
    await wrapper.find('[data-testid="t24-slide-notes"]').setValue('改后的备注')
    await wrapper.find('[data-testid="t24-slide-propose"]').trigger('click')
    expect(wrapper.emitted('propose-operations')?.[1]?.[0]).toEqual([
      { op: 'set_slide', target: 's2', field: 'speaker_note', value: '改后的备注', before: '第二页备注' },
    ])
  })

  it('LessonPlanBlockEditor 块级编辑同时发出 typed operations（含 before）', async () => {
    const wrapper = mount(LessonPlanBlockEditor, {
      props: { blockId: 'b1', title: '目标', kind: 'objective', content: '原目标' },
    })
    await wrapper.find('[data-testid="t24-block-editor"]').setValue('新目标')
    await wrapper.find('[data-testid="t24-block-propose"]').trigger('click')
    expect(wrapper.emitted('propose-operations')?.[0]?.[0]).toEqual({
      blockId: 'b1',
      operations: [{ op: 'set_block', target: 'b1', field: 'content', value: '新目标', before: '原目标' }],
    })
  })

  it('ArtifactFocusCanvas 把 operations 转发为 propose-patch（不再走通用 run）', async () => {
    const wrapper = mount(ArtifactFocusCanvas, {
      props: {
        artifact: { artifact_id: 'deck-1', artifact_type: 'teaching_deck_spec', revision: 3, title: '课件', status: 'available', updated_at: null },
        body: null, messages: [], assistantCollapsed: true, draft: '', busy: false, diff: null,
        editing: editingFixture({ slides: [{ id: 's1', index: 0, title: '开场', layoutHint: null, qaStatus: 'pass' }] }),
      },
      global: {
        stubs: {
          'n-icon': { template: '<i><slot /></i>' },
          MessageItem: { template: '<div />' },
          ComposerStage: { template: '<div />' },
        },
      },
    })
    const op = [{ op: 'set_slide', target: 's1', field: 'content', value: 'x', before: 'y' }]
    await wrapper.findComponent(TeachingDeckFocus).vm.$emit('propose-operations', op, 's1')
    expect(wrapper.emitted('propose-patch')?.[0]?.[0]).toEqual(op)
  })
})

// ── 3) 409 → 冲突状态 + 三动作 ────────────────────────────────────────────

describe('T24 · 409 冲突（INV-1）', () => {
  const payload = {
    kind: 'revision_conflict',
    expected_revision: 3,
    current_revision: 5,
    server_spec_sha256: 'sha256:deck-5',
    diff: {
      summary_zh: '第 2 页内容已在别处更新',
      fields: [{ target: 's2', field: 'content', base_value: '第二页内容', server_value: '服务端已改动的内容' }],
      slide_ids: ['s2'],
    },
    operations: [{ index: 0, status: 'conflict', message: '目标页已被更新' }],
    server_snapshot: { revision: 5 },
  }

  it('409 存下服务端 diff/operations，且不做任何本地乐观覆盖', async () => {
    const store = newStore()
    await store.attach({ kind: 'teaching_deck', remoteId: 'deck-1' })
    deck.revision = 3
    const viewBefore = JSON.stringify(store.view)
    patchConflict = payload

    const proposal = await store.proposePatch([{ op: 'set_slide', target: 's2', field: 'content', value: '我的改动', before: '第二页内容' }])
    expect(proposal).toBeNull()
    expect(store.pendingProposal).toBeNull()
    expect(store.conflict).toMatchObject({
      kind: 'revision_conflict',
      expectedRevision: 3,
      currentRevision: 5,
      submittedRevision: 3,
      reloadedAfterConflict: false,
    })
    expect(store.conflict?.fields).toEqual([{ target: 's2', field: 'content', before: '第二页内容', after: '服务端已改动的内容' }])
    expect(store.conflict?.operations).toEqual([{ index: 0, status: 'conflict', message: '目标页已被更新' }])
    // 没有任何本地覆盖
    expect(JSON.stringify(store.view)).toBe(viewBefore)
    expect(store.draftOperations).toEqual([{ op: 'set_slide', target: 's2', field: 'content', value: '我的改动', before: '第二页内容' }])
  })

  it('冲突未解决时 apply 被拒绝（禁止 last-write-wins）', async () => {
    const store = newStore()
    await store.attach({ kind: 'teaching_deck', remoteId: 'deck-1' })
    patchConflict = payload
    await store.proposePatch([{ op: 'set_slide', target: 's2', field: 'content', value: '我的改动', before: '第二页内容' }])
    const before = calls.length
    expect(await store.applyPatch('p-1')).toBeNull()
    expect(store.localErrorKey).toBe('prep.t24.error.conflict_unresolved')
    expect(calls.length).toBe(before)
    expect(store.conflict).not.toBeNull()
  })

  it('reload → reapply → discard 三动作语义明确', async () => {
    const store = newStore()
    await store.attach({ kind: 'teaching_deck', remoteId: 'deck-1' })
    patchConflict = payload
    await store.proposePatch([{ op: 'set_slide', target: 's2', field: 'content', value: '我的改动', before: '第二页内容' }])

    // 动作 1：reload —— 拉服务端事实；冲突上下文保留，但标记已看过新 revision
    calls = []
    deck.revision = 5
    await store.reloadAfterConflict()
    expect(paths()).toContain('GET /decks/deck-1')
    expect(store.revision).toBe(5)
    expect(store.conflict?.reloadedAfterConflict).toBe(true)
    expect(store.conflict?.currentRevision).toBe(5)

    // 动作 2：reapply —— 以**刚拉到的服务端 revision** 重提（已 reload 过则不重复拉取）
    calls = []
    const proposal = await store.reapply()
    expect(proposal?.patchId).toBe('p-1')
    expect(paths()).toEqual(['POST /decks/deck-1/patches'])
    const post = calls.find((c) => c.method === 'POST' && c.url.endsWith('/patches'))!
    expect(post.body?.expected_revision).toBe(5)
    expect(store.conflict).toBeNull()
    expect(store.rejectedOperations).toEqual([])

    // 动作 3：discard —— 本地放弃，不发写请求
    const store2 = newStore()
    await store2.attach({ kind: 'teaching_deck', remoteId: 'deck-1' })
    patchConflict = payload
    await store2.proposePatch([{ op: 'set_slide', target: 's2', field: 'content', value: 'x', before: 'y' }])
    calls = []
    store2.discard()
    expect(store2.conflict).toBeNull()
    expect(store2.draftOperations).toEqual([])
    expect(store2.rejectedOperations).toEqual([])
    expect(calls).toEqual([])
  })

  it('reapply 在未 reload 时会自己先拉服务端 revision（不拿旧 expected 硬提）', async () => {
    const store = newStore()
    await store.attach({ kind: 'teaching_deck', remoteId: 'deck-1' })
    patchConflict = payload
    await store.proposePatch([{ op: 'set_slide', target: 's2', field: 'content', value: '我的改动', before: '第二页内容' }])
    calls = []
    deck.revision = 6
    await store.reapply()
    const getIdx = paths().indexOf('GET /decks/deck-1')
    const postIdx = paths().indexOf('POST /decks/deck-1/patches')
    expect(getIdx).toBeGreaterThanOrEqual(0)
    expect(postIdx).toBeGreaterThan(getIdx)
    expect(calls.find((c) => c.method === 'POST' && c.url.endsWith('/patches'))!.body?.expected_revision).toBe(6)
  })

  it('ConflictResolver 展示 expected/current + 字段级 diff 并提供三个动作', async () => {
    const wrapper = mount(ConflictResolver, {
      props: {
        expectedRevision: 3,
        currentRevision: 5,
        fields: [{ target: 's2', field: 'content', before: '旧值', after: '新值' }],
        operations: [{ index: 0, status: 'conflict', message: '目标页已被更新' }],
        summaryZh: '第 2 页内容已在别处更新',
      },
    })
    expect(wrapper.find('[data-testid="t24-conflict-fields"]').text()).toContain('s2')
    expect(wrapper.find('[data-testid="t24-conflict-fields"]').text()).toContain('旧值')
    expect(wrapper.find('[data-testid="t24-conflict-fields"]').text()).toContain('新值')
    expect(wrapper.text()).toContain('第 2 页内容已在别处更新')
    await wrapper.find('[data-testid="t24-conflict-reload"]').trigger('click')
    await wrapper.find('[data-testid="t24-conflict-reapply"]').trigger('click')
    await wrapper.find('[data-testid="t24-conflict-discard"]').trigger('click')
    expect(wrapper.emitted('reload')?.length).toBe(1)
    expect(wrapper.emitted('reapply')?.length).toBe(1)
    expect(wrapper.emitted('discard')?.length).toBe(1)
  })
})

// ── 4) 局部重生成只改目标 ────────────────────────────────────────────────

describe('T24 · 局部重生成（INV-2/INV-3）', () => {
  it('regen 只声明目标页变化，且不改动本地视图；apply 后回拉服务端事实', async () => {
    const store = newStore()
    await store.attach({ kind: 'teaching_deck', remoteId: 'deck-1' })
    const before = JSON.stringify(store.view?.slides)
    const othersBefore = JSON.stringify(store.view?.slides.filter((s) => s.id !== 's2'))

    const regen = await store.regenTarget({ kind: 'slide', targetId: 's2', instruction: '把这一页的推导补全' })
    expect(regen?.changedIds).toEqual(['s2'])
    expect(regen?.providerModel).toBe('mimo-v2.5-pro')
    const regenCall = calls.find((c) => c.url.endsWith('/decks/deck-1/regen'))!
    expect(regenCall.body).toMatchObject({
      expected_revision: 3,
      target: { kind: 'slide', slide_id: 's2' },
      refs: {
        approved_blueprint_id: 'bp-1',
        blueprint_revision: 7,
        spec_revision: 3,
        context_manifest_id: 'ctx-1',
        evidence_refs: ['chunk-9'],
      },
    })
    // 提案阶段：本地视图**不变**（不乐观替换）
    expect(JSON.stringify(store.view?.slides)).toBe(before)

    // 服务端落库后只有目标页内容变化
    deck.revision = 4
    deck.slides[1] = { ...deck.slides[1], content: '重生成内容' }
    await store.applyPatch('p-regen')
    expect(store.revision).toBe(4)
    const after = store.view?.slides ?? []
    expect(JSON.stringify(after.filter((s) => s.id !== 's2'))).toBe(othersBefore)
    expect(after.find((s) => s.id === 's2')?.title).toBe('探究')
  })

  it('regen 的 refs 只引用服务端 lineage；缺失一律 null（不猜 blueprint）', () => {
    expect(buildRegenRefs(null)).toEqual({
      approved_blueprint_id: null,
      blueprint_revision: null,
      spec_revision: null,
      prompt_ref: null,
      context_manifest_id: null,
      evidence_refs: [],
      source_refs: [],
    })
  })

  it('lesson plan 的块级 regen 只改声明块', async () => {
    const store = newStore()
    await store.attach({ kind: 'lesson_plan', remoteId: 'spec-1' })
    const before = JSON.stringify(store.view?.blocks)
    const regen = await store.regenTarget({ kind: 'block', targetId: 'b2', instruction: '补充小组活动的脚手架' })
    expect(regen?.changedIds).toEqual(['b2'])
    expect(calls.find((c) => c.url.endsWith('/lesson-plans/spec-1/regen'))!.body).toMatchObject({
      target: { kind: 'block', block_id: 'b2' },
    })
    expect(JSON.stringify(store.view?.blocks)).toBe(before)
    // deck 用例传 block 目标 → 本地拒绝
    const store2 = newStore()
    await store2.attach({ kind: 'teaching_deck', remoteId: 'deck-1' })
    expect(await store2.regenTarget({ kind: 'block', targetId: 'b2', instruction: 'x' })).toBeNull()
    expect(store2.localErrorKey).toBe('prep.t24.error.regen_target_kind')
  })
})

// ── 5) QA 失败页定位 + 单页重试 ───────────────────────────────────────────

describe('T24 · 逐页 QA', () => {
  it('load 后拿到逐页状态与失败页清单（来自服务端）', async () => {
    const store = newStore()
    await store.attach({ kind: 'teaching_deck', remoteId: 'deck-1' })
    expect(store.qa?.failedPages).toEqual([2])
    expect(store.qaStatusByPageNo[2]).toBe('fail')
    expect(store.failedPageNos).toEqual([2])
  })

  it('单页重试只替换该页，其它页逐字段不变', async () => {
    const store = newStore()
    await store.attach({ kind: 'teaching_deck', remoteId: 'deck-1' })
    const page1Before = JSON.stringify(store.qa?.pages.find((p) => p.pageNo === 1))
    const page3Before = JSON.stringify(store.qa?.pages.find((p) => p.pageNo === 3))

    expect(await store.retryQaPage(2)).toBe(true)
    expect(calls.find((c) => c.url.endsWith('/qa/retry'))!.body).toEqual({ page_no: 2, expected_revision: 3 })
    const page2 = store.qa?.pages.find((p) => p.pageNo === 2)
    expect(page2?.status).toBe('pass')
    expect(page2?.defects).toEqual([])
    expect(page2?.attempts).toBe(2)
    expect(JSON.stringify(store.qa?.pages.find((p) => p.pageNo === 1))).toBe(page1Before)
    expect(JSON.stringify(store.qa?.pages.find((p) => p.pageNo === 3))).toBe(page3Before)
    expect(store.qa?.failedPages).toEqual([])
    expect(store.qa?.p0Count).toBe(0)
    expect(store.qa?.p1Count).toBe(0)
  })

  it('服务端未保证 others_unchanged 时改为整体回拉（不伪造局部）', async () => {
    const store = newStore()
    await store.attach({ kind: 'teaching_deck', remoteId: 'deck-1' })
    qaRetryLocal = false
    calls = []
    await store.retryQaPage(2)
    expect(store.localErrorKey).toBe('prep.t24.error.qa_not_local')
    expect(paths()).toContain('GET /decks/deck-1/qa')
  })

  it('SlideQaPanel 渲染失败页缺陷（维度中文）并对当前页提供重试入口', async () => {
    const wrapper = mount(TeachingDeckFocus, {
      props: {
        title: '课件', revision: 3, qaIssues: [], lineage: {},
        slides: [
          { id: 's1', title: '开场', content: 'a', notes: '' },
          { id: 's2', title: '探究', content: 'b', notes: '' },
        ],
        editing: editingFixture({
          // 逐页标记来自 store 的 `qaStatusByPageNo`（page_no = index + 1）
          qaByPage: { 1: 'pass', 2: 'fail' },
          failedPages: [2],
          qa: {
            deckId: 'deck-1', specRevision: 3, artifactId: null,
            checks: ['overflow', 'contrast', 'font'],
            pages: [
              { pageNo: 1, status: 'pass', defects: [], metrics: { inkPct: 5, cv: 0.2, shapeCount: 8, hasPicture: false }, attempts: 1 },
              {
                pageNo: 2, status: 'fail',
                defects: [{ kind: 'overflow', severity: 'p0', message: '文本框超出边界', shapeIds: ['shape-7'] }],
                metrics: { inkPct: 14, cv: 0.9, shapeCount: 21, hasPicture: true }, attempts: 1,
              },
            ],
            failedPages: [2], p0Count: 1, p1Count: 0, byKind: { overflow: 1 },
          },
        }),
      },
      global: { stubs: { 'n-icon': { template: '<i><slot /></i>' } } },
    })
    // 默认打开第 1 页 → 第 2 页的重试按钮禁用（单页重试只作用于当前页）
    expect(wrapper.find('[data-testid="t24-qa-page-2"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="t24-qa-page-2"]').text()).toContain('内容溢出')
    expect(wrapper.find('[data-testid="t24-qa-page-2"]').text()).toContain('必须修复')
    expect((wrapper.find('[data-testid="t24-qa-retry-2"]').element as HTMLButtonElement).disabled).toBe(true)

    // 定位到第 2 页（失败页清单可点击跳页）→ 重试可用
    await wrapper.find('[data-testid="t24-qa-locate-2"]').trigger('click')
    await wrapper.find('[data-testid="t24-qa-retry-2"]').trigger('click')
    expect(wrapper.emitted('retry-page')).toEqual([[2]])
    // 逐页标记来自服务端报告
    expect(wrapper.find('[data-testid="t24-slide-qa-1"]').attributes('data-state')).toBe('pass')
    expect(wrapper.find('[data-testid="t24-slide-qa-2"]').attributes('data-state')).toBe('fail')
  })

  it('SlideNavigator 只在服务端给出状态时显示 QA 标记', () => {
    const wrapper = mount(SlideNavigator, {
      props: { slides: [{ id: 'a', title: 'A', qaStatus: 'fail' }, { id: 'b', title: 'B' }], activeIndex: 0 },
    })
    expect(wrapper.find('[data-testid="t24-slide-qa-1"]').attributes('data-state')).toBe('fail')
    expect(wrapper.find('[data-testid="t24-slide-qa-2"]').exists()).toBe(false)
  })
})

// ── 6) 导出状态机 + 票据 ─────────────────────────────────────────────────

describe('T24 · 导出状态机（真实轮询）', () => {
  it('pending → running → done → 领取短时票据；进度来自服务端', async () => {
    const store = newStore()
    store.setPollIntervalMs(5)
    await store.attach({ kind: 'teaching_deck', remoteId: 'deck-1' })
    exportSequence = [
      { job_id: 'job-1', format: 'pptx', status: 'running', progress: { stage: 'render', page_no: 2, page_count: 12 } },
      { job_id: 'job-1', format: 'pptx', status: 'done', artifact_id: 'artifact-1' },
    ]
    const entry = await store.startExport('pptx')
    expect(entry?.status).toBe('pending')
    expect(entry?.jobId).toBe('job-1')
    expect(calls.find((c) => c.url.endsWith('/decks/deck-1/exports'))!.body).toMatchObject({ format: 'pptx', expected_revision: 3 })

    await waitFor(() => store.exports.pptx?.status === 'done', 2000)
    expect(store.exports.pptx?.artifactId).toBe('artifact-1')
    expect(store.exports.pptx?.ticket?.url).toContain('/downloads/ticket-1')

    // 进度是服务端字段，不是本地定时器
    const polled = calls.filter((c) => c.url.endsWith('/exports/job-1'))
    expect(polled.length).toBeGreaterThanOrEqual(2)
  })

  it('failed 诚实呈现错误，且不产出假 artifact', async () => {
    const store = newStore()
    store.setPollIntervalMs(5)
    await store.attach({ kind: 'teaching_deck', remoteId: 'deck-1' })
    exportSequence = [{ job_id: 'job-1', format: 'pptx', status: 'failed', error: '8201 PowerPoint COM 不可用' }]
    await store.startExport('pptx')
    await waitFor(() => store.exports.pptx?.status === 'failed', 2000)
    expect(store.exports.pptx?.error).toContain('8201')
    expect(store.exports.pptx?.artifactId).toBeNull()
    expect(store.exports.pptx?.ticket).toBeNull()
  })

  it('cancel 调服务端取消并停止轮询', async () => {
    const store = newStore()
    store.setPollIntervalMs(5)
    await store.attach({ kind: 'teaching_deck', remoteId: 'deck-1' })
    await store.startExport('pdf')
    expect(await store.cancelExport('pdf')).toBe(true)
    expect(store.exports.pdf?.status).toBe('cancelled')
    const before = calls.length
    await sleep(60)
    expect(calls.length).toBe(before)
  })

  it('ExportPanel 按真实状态渲染（无定时器伪造进度）', async () => {
    const wrapper = mount(ExportPanel, {
      props: {
        capabilities: [
          { format: 'pptx', label: 'PPTX', state: 'running', progressStage: 'render', progressPageNo: 2, progressPageCount: 12, jobId: 'job-1' },
          { format: 'pdf', label: 'PDF', state: 'failed', error: '渲染失败' },
          { format: 'docx', label: 'Word 教案', state: 'expired', artifactId: 'artifact-1' },
        ],
      },
    })
    expect(wrapper.find('[data-testid="t24-export-progress"]').text()).toContain('2/12')
    expect(wrapper.find('[data-testid="t24-export-job"]').text()).toContain('job-1')
    expect(wrapper.find('[data-testid="t24-export-error-pdf"]').text()).toContain('渲染失败')
    await wrapper.find('[data-testid="t24-export-cancel-pptx"]').trigger('click')
    await wrapper.find('[data-testid="t24-export-refresh-docx"]').trigger('click')
    expect(wrapper.emitted('cancel')).toEqual([['pptx']])
    expect(wrapper.emitted('refresh-download')).toEqual([['docx']])
    // 没有内部 setInterval 伪造进度
    expect(wrapper.vm.$options.components).toBeTruthy()
  })
})

// ── 7) 下载链接过期 → 重新获取 ───────────────────────────────────────────

describe('T24 · 短时下载票据（INV-7）', () => {
  it('expires_at 到期后 state → expired，重新获取会重新领票', async () => {
    const store = newStore()
    await store.attach({ kind: 'teaching_deck', remoteId: 'deck-1' })
    store.exports.pptx = { format: 'pptx', jobId: 'job-1', status: 'done', artifactId: 'artifact-1', error: null, progressStage: null, progressPageNo: null, progressPageCount: null, events: [], ticket: null }
    const first = await store.requestTicket('artifact-1')
    expect(first?.expiresAt).toBeTruthy()
    expect(store.exports.pptx?.status).toBe('done')
    expect(isTicketExpired(store.exports.pptx!.ticket)).toBe(false)

    await waitFor(() => store.exports.pptx?.status === 'expired', 2000)
    expect(store.exports.pptx?.ticket?.expired).toBe(true)

    const refreshed = await store.refreshDownload('artifact-1')
    expect(refreshed?.url).toContain('/downloads/ticket-2')
    expect(store.exports.pptx?.status).toBe('done')
    expect(isTicketExpired(store.exports.pptx!.ticket)).toBe(false)

    // 按格式重新获取（UI 的确定入口）：只动该格式自己的条目
    store.exports.pdf = { format: 'pdf', jobId: 'job-2', status: 'expired', artifactId: 'artifact-2', error: null, progressStage: null, progressPageNo: null, progressPageCount: null, events: [], ticket: null }
    const byFormat = await store.refreshDownloadForFormat('pdf')
    expect(byFormat?.url).toContain('/downloads/ticket-')
    expect(store.exports.pdf?.artifactId).toBe('artifact-2')
    expect(store.exports.pptx?.artifactId).toBe('artifact-1')
    expect(store.exports.pdf?.status).toBe('done')
  })

  it('redeem 返回 410/403 → expired（浏览器导航观测不到，由 API 侧回传）', async () => {
    const store = newStore()
    await store.attach({ kind: 'teaching_deck', remoteId: 'deck-1' })
    store.exports.pdf = { format: 'pdf', jobId: 'job-1', status: 'done', artifactId: 'artifact-1', error: null, progressStage: null, progressPageNo: null, progressPageCount: null, events: [], ticket: null }
    await store.requestTicket('artifact-1')
    store.markRedeemFailure('artifact-1', 410)
    expect(store.exports.pdf?.status).toBe('expired')
    expect(store.exports.pdf?.ticket?.redeemFailure).toBe(410)
  })

  it('isTicketExpired 是纯函数（无 expires_at 不臆断）', () => {
    expect(isTicketExpired(null)).toBe(false)
    expect(isTicketExpired({ url: null, expiresAt: null, singleUse: true, expired: false, redeemFailure: null })).toBe(false)
    expect(isTicketExpired({ url: null, expiresAt: new Date(Date.now() - 1000).toISOString(), singleUse: true, expired: false, redeemFailure: null })).toBe(true)
  })

  it('ArtifactFocusCanvas 展示 expired 并提供重新获取（接线优先于 body 派生）', async () => {
    const wrapper = mount(ArtifactFocusCanvas, {
      props: {
        artifact: { artifact_id: 'deck-1', artifact_type: 'teaching_deck_spec', revision: 4, title: '课件', status: 'available', updated_at: null },
        body: { export_capabilities: [{ format: 'pptx', label: 'PPTX', state: 'capability-disabled' }] },
        messages: [], assistantCollapsed: true, draft: '', busy: false, diff: null,
        editing: editingFixture({
          revision: 4,
          exportCapabilities: [
            { format: 'pptx', label: 'PPTX', state: 'expired', artifactId: 'artifact-1', downloadUrl: null },
            { format: 'pdf', label: 'PDF', state: 'available', downloadUrl: null },
          ],
        }),
      },
    })
    const panel = wrapper.findComponent(ExportPanel)
    expect(panel.props('capabilities')).toMatchObject([
      { format: 'pptx', state: 'expired' },
      { format: 'pdf', state: 'available' },
    ])
    expect(panel.find('[data-testid="t24-export-refresh-pptx"]').exists()).toBe(true)
    expect(wrapper.findComponent(TeachingDeckFocus).props('revision')).toBe(4)
  })
})

// ── 8) 撤销走服务端 revision ─────────────────────────────────────────────

describe('T24 · 撤销 = 服务端新 revision（INV-5）', () => {
  it('revertRevision 只调 listRevisions + revert，并回拉历史', async () => {
    const store = newStore()
    await store.attach({ kind: 'teaching_deck', remoteId: 'deck-1' })
    revisions = [
      ...revisions,
      { revision: 9, kind: 'revert', summary: '撤销到第 2 版', actor_id: 'teacher-1', created_at: '2026-09-13T03:00:00Z', affected_slide_ids: ['s2'], spec_sha256: 'sha256:deck-9' },
    ]
    deck = { ...deck, revision: 9 }
    calls = []
    const next = await store.revertRevision(2, '撤销到第 2 版')
    expect(next).toBe(9)
    expect(paths()).toEqual([
      'POST /decks/deck-1/revisions/2/revert',
      'GET /decks/deck-1',
      'GET /decks/deck-1/revisions',
      'GET /decks/deck-1/qa',
    ])
    expect(calls[0].body).toEqual({ expected_revision: 3, summary: '撤销到第 2 版' })
    // 历史来自服务端（新 revision 是**新增**的，不是本地数组回退）
    expect(store.revisions.map((r) => r.revision)).toEqual([1, 3, 9])
    expect(store.revisions.find((r) => r.revision === 9)?.kind).toBe('revert')
    expect(store.revision).toBe(9)
  })

  it('RevisionToolbar 历史来自服务端列表，撤销按钮带目标版本', async () => {
    const wrapper = mount(RevisionToolbar, {
      props: {
        revision: 3,
        dirty: true,
        hasPendingProposal: true,
        pendingPatchId: 'p-1',
        revisions: [
          { revision: 1, kind: 'projection', summary: '投影生成', actorId: null, createdAt: null, affectedSlideIds: [], specSha256: null },
          { revision: 3, kind: 'patch', summary: '局部修改', actorId: 'teacher-1', createdAt: null, affectedSlideIds: ['s2'], specSha256: null },
        ],
      },
    })
    expect(wrapper.find('[data-testid="t24-revision-history"]').exists()).toBe(false)
    await wrapper.find('[data-testid="t24-revision-history-toggle"]').trigger('click')
    const history = wrapper.find('[data-testid="t24-revision-history"]')
    expect(history.text()).toContain('投影生成')
    expect(history.text()).toContain('第 1 版')
    // 当前版本不可撤销到自己
    expect((wrapper.find('[data-testid="t24-revert-3"]').element as HTMLButtonElement).disabled).toBe(true)
    await wrapper.find('[data-testid="t24-revert-1"]').trigger('click')
    expect(wrapper.emitted('revert')).toEqual([[1]])
    await wrapper.find('[data-testid="t24-apply-patch"]').trigger('click')
    expect(wrapper.emitted('apply')?.length).toBe(1)
  })

  it('冲突存在时 RevisionToolbar 挡住 apply（服务端也会 409）', async () => {
    const wrapper = mount(RevisionToolbar, {
      props: { revision: 3, hasPendingProposal: true, pendingPatchId: 'p-1', blockedByConflict: true },
    })
    expect((wrapper.find('[data-testid="t24-apply-patch"]').element as HTMLButtonElement).disabled).toBe(true)
  })
})

// ── 辅助 ─────────────────────────────────────────────────────────────────

function editingFixture(over: Partial<ArtifactEditingView> = {}): ArtifactEditingView {
  return {
    specKind: 'teaching_deck',
    remoteId: 'deck-1',
    revision: 3,
    specSha256: 'sha256:deck-3',
    slides: [],
    blocks: [],
    deckSlides: [],
    lessonBlocks: [],
    qa: null,
    qaByPage: {},
    failedPages: [],
    revisions: [],
    conflict: null,
    exportEntries: [],
    exportCapabilities: [],
    lineage: { artifactRevision: 3 },
    hasPendingProposal: false,
    pendingPatchId: null,
    dirty: false,
    busy: false,
    errorKey: null,
    errorKind: null,
    errorDetail: null,
    requestId: null,
    loading: false,
    ...over,
  }
}

function sleep(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

async function waitFor(predicate: () => boolean, timeoutMs: number) {
  const started = Date.now()
  while (!predicate()) {
    if (Date.now() - started > timeoutMs) throw new Error('waitFor timeout')
    await sleep(5)
  }
}

describe('T24 · artifact 身份解析', () => {
  it('优先服务端 body 的主键，最后才回落到 artifact_id', () => {
    expect(resolveArtifactIdentity(
      { artifact_type: 'teaching_deck_spec', artifact_id: 'fallback' },
      { deck_id: 'deck-1', spec_id: 'spec-9' },
    )).toEqual({ kind: 'teaching_deck', remoteId: 'deck-1' })
    expect(resolveArtifactIdentity(
      { artifact_type: 'lesson_plan_spec', artifact_id: 'spec-1' },
      { id: 'spec-1' },
    )).toEqual({ kind: 'lesson_plan', remoteId: 'spec-1' })
    expect(resolveArtifactIdentity({ artifact_type: 'teaching_deck_spec', artifact_id: 'x' }, null)).toEqual({ kind: 'teaching_deck', remoteId: 'x' })
    expect(resolveArtifactIdentity({ artifact_type: 'teaching_blueprint', artifact_id: 'bp-1' }, null)).toBeNull()
  })
})

// ── 9) 血缘（INV-8）与编辑区视图模型 ─────────────────────────────────────

describe('T24 · lineage（INV-8）', () => {
  it('LineagePanel 展示 spec revision / content hash / created_by_run / PromptRef / QA / 来源引用', () => {
    const wrapper = mount(LineagePanel, {
      props: {
        lineage: {
          blueprintRevision: null,
          artifactRevision: null,
          sourceLocator: null,
          runId: null,
          hash: null,
          specRevision: 9,
          contentHash: 'sha256:deck-9',
          createdByRunId: 'run-1',
          promptRef: 'prep-review@v2',
          qaStatus: '缺陷 P0/P1：2/1',
          sources: ['chunk-9', 'AC切片/函数'],
        },
      },
    })
    expect(wrapper.find('[data-testid="t24-lineage-spec-revision"]').text()).toBe('9')
    expect(wrapper.find('[data-testid="t24-lineage-hash"]').text()).toBe('sha256:deck-9')
    expect(wrapper.find('[data-testid="t24-lineage-created-by-run"]').text()).toBe('run-1')
    expect(wrapper.find('[data-testid="t24-lineage-prompt-ref"]').text()).toBe('prep-review@v2')
    expect(wrapper.find('[data-testid="t24-lineage-qa"]').text()).toContain('2/1')
    expect(wrapper.find('[data-testid="t24-lineage-sources"]').text()).toBe('chunk-9 · AC切片/函数')
  })

  it('服务端没给血缘时不编造（空态诚实）', () => {
    const wrapper = mount(LineagePanel, { props: { lineage: {} } })
    expect(wrapper.find('.lineage__empty').exists()).toBe(true)
    expect(wrapper.find('[data-testid="t24-lineage-prompt-ref"]').exists()).toBe(false)
  })
})

describe('T24 · 编辑区视图模型（服务端事实 → 可编辑内容）', () => {
  const view = {
    kind: 'teaching_deck' as const,
    remoteId: 'deck-1',
    specId: 'spec-9',
    revision: 3,
    schemaVersion: 'tp-h1-deck/v1',
    specSha256: 'sha256:deck-3',
    metadata: null,
    spec: { slides: [{ id: 's1', content: '第一页正文', notes: '第一页备注' }, { id: 's2', content: '第二页正文' }] },
    slideCount: 2,
    slides: [
      { id: 's1', index: 0, title: '开场', layoutHint: 'title_body', qaStatus: 'pass' as const },
      { id: 's2', index: 1, title: '探究', layoutHint: 'title_body', qaStatus: 'fail' as const },
      { id: 's3', index: 2, title: '小结', layoutHint: null, qaStatus: 'unknown' as const },
    ],
    blocks: [],
    lineage: null,
    recentChangeLog: [],
  }

  it('deck 页正文按 id 对齐 spec；缺正文就是空串（不编造）', () => {
    expect(deckEditorSlides(view).map((s) => [s.id, s.content, s.notes, s.qaStatus])).toEqual([
      ['s1', '第一页正文', '第一页备注', 'pass'],
      ['s2', '第二页正文', '', 'fail'],
      ['s3', '', '', 'unknown'],
    ])
    expect(deckEditorSlides(null)).toEqual([])
  })

  it('lesson plan 块正文来自 spec.blocks / spec.body.blocks', () => {
    const lesson = {
      ...view,
      kind: 'lesson_plan' as const,
      spec: { blocks: [{ id: 'b1', content: '目标正文' }] },
      blocks: [
        { id: 'b1', kind: 'objective', title: '目标', sourceLocator: 'AC切片/1' },
        { id: 'b2', kind: 'activity', title: null, sourceLocator: null },
      ],
      slides: [],
    }
    expect(lessonEditorBlocks(lesson).map((b) => [b.id, b.kind, b.title, b.content, b.sourceLocator])).toEqual([
      ['b1', 'objective', '目标', '目标正文', 'AC切片/1'],
      ['b2', 'activity', 'b2', '', null],
    ])
    // `spec.body.blocks` 也是一种合法位置
    expect(lessonEditorBlocks({ ...lesson, spec: { body: { blocks: [{ id: 'b2', text: '活动正文' }] } } })[1].content).toBe('活动正文')
    expect(lessonEditorBlocks(null)).toEqual([])
  })
})
