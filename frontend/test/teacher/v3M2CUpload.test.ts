// A0 M2-C · 预签名直传 + Butler KNR 两 slot（IFC-004 / 02-ARCHITECTURE §4.10 前端映射）
//  A. presignUpload：demo（url=null 只发 key）/ real（XHR PUT 裸文件体+进度）/ 失败重新 presign 重试一次
//  B. mock presign 契约（教师门禁）
//  C. ButlerPanel：KNR 上下文条（meta.session_context chips，缺省隐藏）+ 依据折叠（source_type/ref，缺省现状）
//  D. BankView：直传 key 优先随 scan-import 发送
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { createRouter, createMemoryHistory } from 'vue-router'

import { handleTeacherV3Api } from '@/mock/teacherV3Server'
// 本文件下方组件测试会 vi.mock('@/api/teacherV3Upload')；助手直测须用 importActual 取真实现

function toReq(over: { method: string; url: string; body?: unknown }) {
  const req: any = { method: over.method, url: over.url, headers: { authorization: 'Bearer mock-token-teacher-preview' } }
  req.on = (ev: string, cb: (c?: any) => void) => {
    if (ev === 'data' && over.body !== undefined) cb(JSON.stringify(over.body))
    if (ev === 'end') cb()
  }
  return req
}
function captureRes() {
  const res: any = {
    statusCode: 0, headers: {}, writableEnded: false, destroyed: false, chunks: [] as string[], body: null as any,
    setHeader() {}, writeHead() { this.statusCode = 200 }, write() { return true }, on() {},
    end(body?: string) { if (typeof body === 'string' && body) { try { this.body = JSON.parse(body) } catch {} } this.statusCode ||= 200; this.writableEnded = true },
  }
  return res
}

const FILE = new File(['original-bytes'], '题目.png', { type: 'image/png' })

describe('presignUpload · 直传助手（IFC-004）', () => {
  afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })

  it('demo 模式（url=null）：只登记 key，不发字节', async () => {
    const fetchFn = vi.fn(async (_url: unknown, init: any) =>
      new Response(JSON.stringify({ code: 0, message: 'ok', data: { key: 'mock-uploads/u-1/题目.png', url: null, demo: true } }), { status: 200, headers: { 'content-type': 'application/json' } }))
    vi.stubGlobal('fetch', fetchFn)
    const { presignUpload } = await vi.importActual<typeof import('@/api/teacherV3Upload')>('@/api/teacherV3Upload')
    const h = await presignUpload(FILE)
    expect(h).toEqual({ key: 'mock-uploads/u-1/题目.png', demo: true })
    expect(fetchFn).toHaveBeenCalledTimes(1) // 仅 presign，无 PUT
    expect(JSON.parse(fetchFn.mock.calls[0][1].body)).toMatchObject({ filename: '题目.png', content_type: 'image/png' })
  })

  it('real 模式：XHR PUT 裸文件体 + 进度回调；失败重新 presign 重试一次', async () => {
    let putCalls = 0
    let presignCalls = 0
    const lastBody: any = { uploaded: null }
    class FakeXhr {
      status = 0; response = ''; onload: (() => void) | null = null; onerror: (() => void) | null = null
      upload = { onprogress: null as ((e: any) => void) | null }
      open() {}
      setRequestHeader() {}
      send(body: any) {
        putCalls += 1
        lastBody.uploaded = body
        if (putCalls === 1) { this.onerror?.(); return } // 第一次网络失败 → 重新 presign
        this.upload.onprogress?.({ lengthComputable: true, loaded: 60, total: 100 })
        this.status = 200
        this.onload?.()
      }
      abort() {}
      addEventListener() {}
    }
    vi.stubGlobal('XMLHttpRequest', FakeXhr as any)
    const fetchFn = vi.fn(async (_url: unknown, init: any) => {
      const body = JSON.parse(init.body)
      presignCalls += 1 // 第 2 次 presign 发生在第 2 次 PUT 之前
      const n = presignCalls
      return new Response(JSON.stringify({
        code: 0, message: 'ok',
        data: { key: `bucket/originals/u-${n}/题目.png`, url: `https://minio.test/put-${n}`, headers: { 'Content-Type': 'image/png' } },
      }), { status: 200, headers: { 'content-type': 'application/json' } })
    })
    vi.stubGlobal('fetch', fetchFn)

    const { presignUpload } = await vi.importActual<typeof import('@/api/teacherV3Upload')>('@/api/teacherV3Upload')
    const pcts: number[] = []
    const h = await presignUpload(FILE, { onProgress: (p) => pcts.push(p) })
    expect(h.key).toBe('bucket/originals/u-2/题目.png')
    expect(h.demo).toBe(false)
    expect(putCalls).toBe(2) // 失败重试一次
    expect(lastBody.uploaded).toBe(FILE) // 裸文件体（原图原样，红线 3）
    expect(pcts).toContain(0.6)
    // 第二次 presign 带 retry_of 指向首次 key
    expect(JSON.parse(fetchFn.mock.calls[1][1].body).retry_of).toBe('bucket/originals/u-1/题目.png')
  })
})

describe('mock 契约 · POST /upload/presign', () => {
  it('demo key + url=null + note；非教师 403', async () => {
    const ok = captureRes()
    await handleTeacherV3Api(toReq({ method: 'POST', url: '/teacher-v3/upload/presign', body: { filename: '作业.jpg', content_type: 'image/jpeg' } }), ok)
    expect(ok.body.data.key).toMatch(/^mock-uploads\//)
    expect(ok.body.data.url).toBeNull()
    expect(ok.body.data.demo).toBe(true)

    const denied = captureRes()
    await handleTeacherV3Api({ ...toReq({ method: 'POST', url: '/teacher-v3/upload/presign', body: {} }), headers: { authorization: 'Bearer student-x' } }, denied)
    expect(denied.statusCode).toBe(403)
  })
})

/* ---------- C/D. 组件层 ---------- */
const rawFn = vi.fn()
vi.mock('@/api/teacherV3', () => ({
  v3Api: {
    butler: {
      chat: vi.fn(),
      voiceFormula: vi.fn(),
      confirmAction: vi.fn(async () => ({ code: 0, data: { ok: true } })),
    },
    catalog: {
      classes: vi.fn(async () => ({ data: { items: [{ class_id: 'c2-03', name: '高二(3)班', students: 44 }] } })),
      quizQuestions: vi.fn(async () => ({ data: { items: [], total: 0 } })),
      quizScanImport: vi.fn(async () => ({ data: { id: 'q-1' } })),
      quizKpTree: vi.fn(async () => ({ data: { tree: [] } })),
      folders: { list: vi.fn(async () => ({ data: { items: [] } })) },
    },
    decks: { list: vi.fn(async () => ({ data: { items: [] } })) },
  },
}))
const presignFn = vi.fn()
const uploadFn = vi.fn()
vi.mock('@/api/teacherV3Upload', () => ({
  presignUpload: (...a: unknown[]) => presignFn(...a),
  // 迭代20 门禁同步：BankView 2026-10-05 起改走服务端中转 uploadFile
  // （浏览器 → 8100 → MinIO；presign 直传线上 PUT 不到）。组件 import 的是
  // 整模块，mock 少一个导出就会在选图时炸 undefined——补齐真实模块的两个导出。
  uploadFile: (...a: unknown[]) => uploadFn(...a),
}))

import ButlerPanel from '@/components/teacherV3/ButlerPanel.vue'
import BankView from '@/pages/teacher-v3/BankView.vue'
import { v3Api } from '@/api/teacherV3'

function mountPanel() {
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/:pathMatch(.*)*', component: { template: '<div/>' } }] })
  router.push('/teacher-v3/slides')
  return mount(ButlerPanel, { props: { open: true }, global: { plugins: [router, createPinia()] } })
}

function sseReply(events: { event: string; data: any }[]) {
  return vi.fn((_body: any, onEvent: (e: string, d: any) => void) => {
    for (const { event, data } of events) onEvent(event, data)
    return { abort: () => {}, finished: Promise.resolve() }
  })
}

beforeEach(() => { vi.clearAllMocks() })

describe('ButlerPanel · KNR 上下文条（meta.session_context）', () => {
  it('meta 带 session_context → 输入框上方 chips；点击展开明细', async () => {
    ;(v3Api.butler.chat as any).mockImplementationOnce(sseReply([
      { event: 'meta', data: { session_id: 's1', intent: 'chat', session_context: { textbook: '人教A版选择性必修一', chapter: '圆锥曲线 ▸ 椭圆', class_name: '高二(3)班', curriculum: '课标（2017版2020修订）', preferences: ['板书推导优先'] } } },
      { event: 'done', data: {} },
    ]))
    const w = mountPanel()
    await w.find('[data-testid="tv3-butler-input"]').setValue('椭圆的定义')
    await w.find('[data-testid="tv3-butler-send"]').trigger('click')
    await flushPromises()

    const strip = w.find('[data-testid="tv3-butler-sctx"]')
    expect(strip.exists()).toBe(true)
    expect(strip.text()).toContain('人教A版选择性必修一')
    expect(strip.text()).toContain('高二(3)班')
    await strip.find('.tv3-butler__chip').trigger('click')
    expect(w.find('[data-testid="tv3-butler-sctx-detail"]').text()).toContain('✓ 板书推导优先')
  })

  it('meta 无 session_context → 整条隐藏（缺省不动布局）', async () => {
    ;(v3Api.butler.chat as any).mockImplementationOnce(sseReply([{ event: 'meta', data: { session_id: 's2', intent: 'chat' } }, { event: 'done', data: {} }]))
    const w = mountPanel()
    await w.find('[data-testid="tv3-butler-input"]').setValue('你好')
    await w.find('[data-testid="tv3-butler-send"]').trigger('click')
    await flushPromises()
    expect(w.find('[data-testid="tv3-butler-sctx"]').exists()).toBe(false)
  })
})

describe('ButlerPanel · KNR 依据折叠（citation source_type/ref）', () => {
  it('来源带类型 → 「依据 N 份资料」折叠，展开区分类型与定位', async () => {
    ;(v3Api.butler.chat as any).mockImplementationOnce(sseReply([
      { event: 'citation', data: { sources: [
        { index: 1, title: '人教A版 · 椭圆', url: 'https://pep.com.cn/x', source_type: 'web', ref: 'P42' },
        { index: 2, title: '课标 · 圆锥曲线', url: 'http://moe.gov.cn', source_type: 'curriculum', ref: '2.2' },
      ] } },
      { event: 'done', data: {} },
    ]))
    const w = mountPanel()
    await w.find('[data-testid="tv3-butler-input"]').setValue('椭圆第一定义')
    await w.find('[data-testid="tv3-butler-send"]').trigger('click')
    await flushPromises()

    expect(w.find('[data-testid="tv3-butler-evid-toggle"]').text()).toContain('依据 2 份资料')
    await w.find('[data-testid="tv3-butler-evid-toggle"]').trigger('click')
    const evid = w.find('[data-testid="tv3-butler-evid"]')
    expect(evid.text()).toContain('课标')
    expect(evid.text()).toContain('P42')
  })

  it('来源无 source_type/ref → 保持现状（无折叠按钮）', async () => {
    ;(v3Api.butler.chat as any).mockImplementationOnce(sseReply([
      { event: 'citation', data: { sources: [{ index: 1, title: '某网页', url: 'https://x.example' }] } },
      { event: 'done', data: {} },
    ]))
    const w = mountPanel()
    await w.find('[data-testid="tv3-butler-input"]').setValue('引用')
    await w.find('[data-testid="tv3-butler-send"]').trigger('click')
    await flushPromises()
    expect(w.find('[data-testid="tv3-butler-evid-toggle"]').exists()).toBe(false)
    expect(w.find('[data-testid="tv3-butler-citations"]').exists()).toBe(true) // 现状渲染保留
  })
})

describe('BankView · 直传 key 优先随 scan-import 发送', () => {
  it('选图后 uploadFile（服务端中转）被调用；导入时 src= key', async () => {
    // 2026-10-05 组件契约：选图即走 uploadFile（浏览器 → 8100 → MinIO），
    // 导入必须携带返回的对象存储 key（阻断 base64 回落，防裂图垃圾行）。
    uploadFn.mockResolvedValue({ key: 'mock-uploads/u-9/题干.png', demo: false })
    const quizScanImport = v3Api.catalog.quizScanImport as any
    const w = mount(BankView, { global: { plugins: [createRouter({ history: createMemoryHistory(), routes: [{ path: '/:pathMatch(.*)*', component: { template: '<div/>' } }] }), createPinia()] } })
    await flushPromises()
    await w.find('[data-testid="tv3-bank-scan-open"]').trigger('click')
    const stemInput = w.find('[data-testid="tv3-bank-scan-input"]')
    Object.defineProperty(stemInput.element, 'files', { value: [FILE] })
    vi.spyOn(window, 'FileReader').mockImplementation(() => {
      const fake: any = { readAsDataURL: () => Promise.resolve().then(() => fake.onload?.()), result: 'data:image/png;base64,PREVIEW' }
      return fake
    })
    await stemInput.trigger('change')
    await flushPromises()
    expect(uploadFn).toHaveBeenCalledWith(FILE, expect.objectContaining({ onProgress: expect.any(Function) }))
    await w.find('[data-testid="tv3-bank-scan-import"]').trigger('click')
    await flushPromises()
    expect(quizScanImport.mock.calls[0][0].src).toBe('mock-uploads/u-9/题干.png')
    vi.restoreAllMocks()
  })
})
