import { describe, it, expect } from 'vitest'
import { handleTeacherV3Api } from '@/mock/teacherV3Server'

/**
 * 备小研工坊改版契约（IFC-WS-a，PROTOTYPE-ONLY）
 * - deck-outline 每页携带 minutes（kind→分钟确定性映射，大纲确认卡展示）
 * - recognition/preview：fixture 演示识别（可编辑文本预览；正式识别仍以生成链路为准）
 */

function toReq(over: { method: string; url: string; body?: unknown }) {
  const req: any = {
    method: over.method,
    url: over.url,
    headers: { authorization: 'Bearer mock-token-teacher-preview' },
  }
  if (over.body !== undefined) {
    req.on = (ev: string, cb: (c?: any) => void) => {
      if (ev === 'data') cb(JSON.stringify(over.body))
      if (ev === 'end') cb()
    }
  } else {
    req.on = () => {}
  }
  return req
}

function captureRes() {
  const res: any = {
    statusCode: 0,
    headers: {},
    chunks: [] as string[],
    body: null as any,
    setHeader(k: string, v: string) { this.headers[k] = v },
    writeHead(code: number) { this.statusCode = code },
    write(chunk: string) { this.chunks.push(String(chunk)); return true },
    end(body?: string) {
      if (typeof body === 'string' && body) {
        try { this.body = JSON.parse(body) } catch { this.body = body }
      }
      this.ended = true
    },
  }
  return res
}

async function call(path: string, body?: unknown) {
  const res = captureRes()
  await handleTeacherV3Api(toReq({ method: body === undefined ? 'GET' : 'POST', url: '/teacher-v3' + path, body }), res)
  return res
}

describe('ws · deck-outline 每页 minutes', () => {
  it('新授默认链每页带 minutes：cover=2、概念=10、例题=12', async () => {
    const res = await call('/generation/deck-outline', { topic: '椭圆及其标准方程', course_type: '新授课' })
    const outline = res.body.data.outline as { title: string; kind: string; minutes: number }[]
    expect(Array.isArray(outline)).toBe(true)
    expect(outline.length).toBeGreaterThanOrEqual(5)
    for (const o of outline) expect(typeof o.minutes).toBe('number')
    const byKind = Object.fromEntries(outline.map((o) => [o.kind, o.minutes]))
    if (byKind.cover !== undefined) expect(byKind.cover).toBe(2)
    if (byKind.definition !== undefined) expect(byKind.definition).toBe(10)
    if (byKind.example !== undefined) expect(byKind.example).toBe(12)
  })
})

describe('ws · recognition/preview（演示识别 fixture）', () => {
  it('返回与照片数一致的确认卡：置信度/可编辑题干/知识点，第二张低置信度告警', async () => {
    const res = await call('/recognition/preview', { photos: ['data:image/png;base64,A', 'data:image/png;base64,B'] })
    const items = res.body.data.items as { photo_id: string; confidence: number; warn?: boolean; text: string; kps: string[] }[]
    expect(items).toHaveLength(2)
    expect(items[0].photo_id).toBe('photo_01')
    expect(items[0].confidence).toBeGreaterThan(0.9)
    expect(items[1].warn).toBe(true)
    expect(items[1].text).toContain('双曲线')
    expect(items[0].kps.length).toBeGreaterThan(0)
    expect(res.body.data.note).toContain('演示识别')
  })
})
