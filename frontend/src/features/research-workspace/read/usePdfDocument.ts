/**
 * pdfjs 封装（FRONTEND-ARCH §6 / BACKEND-CONTRACT §3.1）。
 *
 * 铁律：
 *  1. 加载走 **`fetch → Uint8Array → getDocument({ data })`**；
 *     **禁用** `getDocument({ url })`（Vite dev 下 worker 私有字段报错，见既有
 *     `paper-workbench/components/PdfPane.vue` 的实测结论）；
 *  2. `PDFDocumentProxy` 含 `#private` 字段 → 调用方必须用 `shallowRef` 持有，
 *     严禁放进深层响应式 `ref()`，否则 `this` 变 Proxy → 私有字段不可读；
 *  3. 文本层用 pdfjs 官方 `TextLayer` 构建（承载选文与坐标），坐标换算统一走
 *     `PageViewport.convertToPdfPoint / convertToViewportPoint`（PDF 坐标系：
 *     原点在左下、y 向上，与后端 `/text` 返回的 `bbox` 同一空间，可直接换算）；
 *  4. **同一 canvas 的渲染必须串行**：`PdfRenderController` 负责取消上一个
 *     `RenderTask` + 代际令牌作废过期结果，禁止并发 `page.render()`（否则 pdf.js
 *     抛 `Cannot use the same canvas during multiple render() operations.`）。
 */
import * as pdfjs from 'pdfjs-dist'
import type { PDFDocumentProxy, PDFPageProxy, PageViewport, RenderTask } from 'pdfjs-dist'
// eslint-disable-next-line import/no-unresolved -- Vite `?url` 资源说明符
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url'
import { getAccessToken } from '@/api/authSession'
import type { PageBlock } from '../api/types'

let workerReady = false

/** 幂等设置 worker（必须在任何 `getDocument` 之前调用）。 */
export function configurePdfWorker(): void {
  if (workerReady) return
  pdfjs.GlobalWorkerOptions.workerSrc = workerUrl
  workerReady = true
}

/** 统一错误对象——恒中文，不泄露内部细节。 */
export class PdfLoadError extends Error {
  readonly status: number

  constructor(message: string, status = 0) {
    super(message)
    this.name = 'PdfLoadError'
    this.status = status
  }
}

/**
 * 拉取真实 PDF 字节。`url` 走同源 `/api/research-vnext/...`（经 8000 网关），
 * 因此需要与 API 相同的 Bearer 头（端点也在网关鉴权范围内）。
 */
export async function fetchPdfBytes(url: string): Promise<Uint8Array> {
  configurePdfWorker()
  const headers: Record<string, string> = {}
  const token = getAccessToken()
  if (token) headers.Authorization = `Bearer ${token}`
  let res: Response
  try {
    res = await fetch(url, { headers, credentials: 'include' })
  } catch {
    throw new PdfLoadError('无法连接文献服务，请检查网络后重试。', 0)
  }
  if (!res.ok) {
    if (res.status === 401 || res.status === 403) {
      throw new PdfLoadError('没有该文献的访问权限。', res.status)
    }
    if (res.status === 404) throw new PdfLoadError('文献文件不存在或已被删除。', 404)
    throw new PdfLoadError('文献加载失败，请稍后重试。', res.status)
  }
  const buf = await res.arrayBuffer()
  return new Uint8Array(buf)
}

/** 由字节加载文档（禁用 `{ url }`）。 */
export async function loadPdfDocument(data: Uint8Array): Promise<PDFDocumentProxy> {
  configurePdfWorker()
  const task = pdfjs.getDocument({ data })
  return task.promise
}

export interface RenderedPage {
  viewport: PageViewport
  width: number
  height: number
}

/**
 * 判定是否为「渲染被取消」异常。
 *
 * pdf.js 在 `RenderTask.cancel()` / `TextLayer.cancel()` 后会让进行中的 promise
 * 以 `RenderingCancelledException` 拒绝 —— 这是**正常控制流**（用户快速翻页/缩放时
 * 我们主动取消上一个渲染），**绝不能**冒泡到 UI 变成「文献暂时无法显示」。
 */
export function isRenderCancelled(err: unknown): boolean {
  if (err instanceof pdfjs.RenderingCancelledException) return true
  if (err && typeof err === 'object') {
    const e = err as { name?: string; message?: string }
    if (e.name === 'RenderingCancelledException') return true
    return /cancel?led/i.test(`${e.name ?? ''} ${e.message ?? ''}`)
  }
  return typeof err === 'string' && /cancel?led/i.test(err)
}

/** 可取消对象（`RenderTask` / `TextLayer` 共有 `cancel()`）。 */
interface Cancellable {
  cancel: () => void
}

/**
 * 单 canvas 渲染控制器 —— 把「同一 canvas 上的 pdf.js 渲染」强制串行化。
 *
 * 背景（用户实测报障 P0）：`page.render()` 对**同一 canvas** 并发调用会抛
 * `Cannot use the same canvas during multiple render() operations. Use different
 * canvas or ensure previous operations were cancelled or completed.`
 * PdfReaderPane 的 `render()` 被翻页 / 缩放 / 适宽 / 定位 / resize 等 ≥8 处调用，
 * 首屏尚未渲染完就再次触发时，两个 `render()` 并行写同一 canvas → 报错。
 *
 * 控制器给出三条硬保证（对应修复要求 1–4）：
 *  1. 发起新渲染前**同步** `cancel()` 上一个 `RenderTask`；pdf.js 在 `cancel()` 内
 *     同步释放 canvas 占用，故新的 `render()` 不会再撞上旧任务（真取消）；
 *  2. **代际令牌**：`begin()` 每次自增；过期代际的 await 结果一律作废，
 *     **不得写 canvas、不得改 UI 状态**（真作废，不闪屏、不丢滚动位置）；
 *  3. `renderCanvas` / `renderText` 内部吞掉取消异常并返回 `null` / `false`，
 *     调用方据此中止后续状态写入 —— `render()` 因此**可重入安全**：
 *     并发调用时后者接管、前者作废，而不是并行渲染同一 canvas；
 *  4. 文本层同理由 `textLayer` 引用跟踪并取消（批注/命中叠层由调用方按同一代际令牌
 *     决定是否重建，见 `refreshOverlays`）。
 *
 * ⚠️ 不使用「给 canvas 换 key 强制重建」的粗暴手段 —— 那会闪屏且丢滚动位置。
 */
export class PdfRenderController {
  private generation = 0
  private task: RenderTask | null = null
  private textLayer: Cancellable | null = null
  private disposed = false

  /** 作废进行中的渲染并开启新一代际，返回新代际号（同步；须在任何新 render 之前调用）。 */
  begin(): number {
    this.cancel()
    this.generation += 1
    return this.generation
  }

  /** 该代际是否仍是最新且未销毁（过期代际不得再触碰 canvas / UI 状态）。 */
  isValid(gen: number): boolean {
    return !this.disposed && gen === this.generation
  }

  /** 取消进行中的 canvas 渲染与文本层渲染（幂等、不抛异常）。 */
  cancel(): void {
    const t = this.task
    this.task = null
    if (t) {
      try {
        t.cancel()
      } catch {
        /* 忽略取消失败：任务可能已自行结束 */
      }
    }
    const tl = this.textLayer
    this.textLayer = null
    if (tl) {
      try {
        tl.cancel()
      } catch {
        /* 忽略取消失败 */
      }
    }
  }

  /** 组件卸载时调用：取消一切并标记销毁，之后的代际一律无效。 */
  dispose(): void {
    this.disposed = true
    this.cancel()
  }

  /**
   * 把 page 渲染进 canvas（含 DPR 缩放）。
   * 返回 `null` 表示**本次渲染已被取消 / 已过期** → 调用方必须据此中止后续状态写入。
   */
  async renderCanvas(
    page: PDFPageProxy,
    canvas: HTMLCanvasElement,
    scale: number,
    dpr: number,
    gen: number,
  ): Promise<RenderedPage | null> {
    if (!this.isValid(gen)) return null
    const viewport = page.getViewport({ scale })
    const outputScale = dpr > 0 ? dpr : 1
    canvas.width = Math.max(1, Math.floor(viewport.width * outputScale))
    canvas.height = Math.max(1, Math.floor(viewport.height * outputScale))
    canvas.style.width = `${Math.floor(viewport.width)}px`
    canvas.style.height = `${Math.floor(viewport.height)}px`
    const ctx = canvas.getContext('2d')
    if (!ctx) throw new PdfLoadError('浏览器不支持 PDF 渲染（画布不可用）。')
    // 尺寸改写后再确认一次代际：若已被更新的渲染接管，则**不发起** render，
    // 避免在错误尺寸 / 过期画布上留下半成品。
    if (!this.isValid(gen)) return null
    const params: Parameters<PDFPageProxy['render']>[0] = { canvasContext: ctx, viewport }
    if (outputScale !== 1) params.transform = [outputScale, 0, 0, outputScale, 0, 0]
    const task = page.render(params)
    this.task = task
    try {
      await task.promise
    } catch (e) {
      // 取消是正常控制流：吞掉，返回 null（不上报 UI）。
      if (isRenderCancelled(e)) return null
      throw e
    } finally {
      if (this.task === task) this.task = null
    }
    return { viewport, width: viewport.width, height: viewport.height }
  }

  /**
   * 构建文本层（pdfjs 官方 `TextLayer`），并纳入同一代际令牌。
   * 容器需带 `textLayer` 类名，且 `--scale-factor` 必须等于 `scale`，否则 pdfjs 的
   * `calc(var(--scale-factor) * Npx)` 定位会错位。
   * 返回 `false` 表示**已取消 / 已过期**（调用方须中止后续状态写入）。
   */
  async renderText(
    page: PDFPageProxy,
    container: HTMLElement,
    viewport: PageViewport,
    scale: number,
    gen: number,
  ): Promise<boolean> {
    if (!this.isValid(gen)) return false
    container.replaceChildren()
    container.style.setProperty('--scale-factor', String(scale))
    container.style.width = `${Math.floor(viewport.width)}px`
    container.style.height = `${Math.floor(viewport.height)}px`
    const textContent = await page.getTextContent()
    if (!this.isValid(gen)) return false
    const layer = new pdfjs.TextLayer({ textContentSource: textContent, container, viewport })
    this.textLayer = layer
    try {
      await layer.render()
    } catch (e) {
      if (isRenderCancelled(e)) {
        container.replaceChildren()
        return false
      }
      throw e
    } finally {
      if (this.textLayer === layer) this.textLayer = null
    }
    if (!this.isValid(gen)) {
      // 过期代际可能已把部分 span 写入容器 → 清空，交由最新代际重建。
      container.replaceChildren()
      return false
    }
    return true
  }
}

/**
 * 单次渲染到 canvas（无外部并发控制），保留旧签名兼容一次性调用方。
 * 需要防竞态的调用方应持有 `PdfRenderController` 并用 `renderCanvas`。
 */
export async function renderPageToCanvas(
  page: PDFPageProxy,
  canvas: HTMLCanvasElement,
  scale: number,
  dpr: number,
): Promise<RenderedPage> {
  const ctrl = new PdfRenderController()
  const gen = ctrl.begin()
  const r = await ctrl.renderCanvas(page, canvas, scale, dpr, gen)
  if (!r) throw new PdfLoadError('页面渲染未完成，请重试。')
  return r
}

/**
 * 构建文本层（无外部并发控制），保留旧签名兼容一次性调用方。
 * 需要防竞态的调用方应由 `PdfRenderController.renderText` 负责。
 */
export async function buildTextLayer(
  page: PDFPageProxy,
  container: HTMLElement,
  viewport: PageViewport,
  scale: number,
): Promise<void> {
  const ctrl = new PdfRenderController()
  const gen = ctrl.begin()
  await ctrl.renderText(page, container, viewport, scale, gen)
}

/** 整页纯文本（搜索计数用）。 */
export async function pagePlainText(page: PDFPageProxy): Promise<string> {
  const content = await page.getTextContent()
  return content.items
    .map((it) => ('str' in it ? (it as { str: string }).str : ''))
    .join('')
}

/**
 * 后端 bbox 的坐标系：**左上原点、y 向下、单位 PDF point（未缩放）**
 * （见后端 `research_workspace/pdfs.py`：`top = height - y1`）。
 *
 * pdf.js 的 `convertToPdfPoint / convertToViewportPoint` 使用**左下原点、y 向上**的
 * PDF 空间，因此两侧换算都必须做一次纵向翻转 `y' = (viewBox[1] + viewBox[3]) - y`。
 * 统一在本模块内完成翻转，使 locator / 笔记 / 证据的 bbox 与后端 `/text` 完全同空间。
 */
function flipY(viewport: PageViewport, y: number): number {
  const vb = viewport.viewBox
  return vb[1] + vb[3] - y
}

/** DOM 矩形（浏览器视口坐标）→ 后端同空间 bbox `[x0,y0,x1,y1]`（左上原点、y 向下）。 */
export function rectToPdfBbox(
  rect: { left: number; top: number; right: number; bottom: number },
  canvasRect: { left: number; top: number },
  viewport: PageViewport,
): [number, number, number, number] {
  const lx0 = rect.left - canvasRect.left
  const ly0 = rect.top - canvasRect.top
  const lx1 = rect.right - canvasRect.left
  const ly1 = rect.bottom - canvasRect.top
  // 视口 → 左下原点 PDF 空间
  const [px0, py0] = viewport.convertToPdfPoint(lx0, ly0)
  const [px1, py1] = viewport.convertToPdfPoint(lx1, ly1)
  // 翻转回后端（左上原点）
  const ty0 = flipY(viewport, py0)
  const ty1 = flipY(viewport, py1)
  return [Math.min(px0, px1), Math.min(ty0, ty1), Math.max(px0, px1), Math.max(ty0, ty1)]
}

/** 后端 bbox（左上原点、y 向下）→ 文本层/画布局部坐标矩形（同样左上原点）。 */
export function pdfBboxToLocalRect(
  bbox: [number, number, number, number],
  viewport: PageViewport,
): { left: number; top: number; width: number; height: number } {
  const [x0, y0, x1, y1] = bbox
  // 翻转到 pdf.js 的左下原点空间后取包围盒
  const [vx0, vy0] = viewport.convertToViewportPoint(x0, flipY(viewport, y0))
  const [vx1, vy1] = viewport.convertToViewportPoint(x1, flipY(viewport, y1))
  return {
    left: Math.min(vx0, vx1),
    top: Math.min(vy0, vy1),
    width: Math.abs(vx1 - vx0),
    height: Math.abs(vy1 - vy0),
  }
}

/**
 * 由选区在**后端坐标系**（左上原点、y 向下）中的 y 反查所属章节（用于 locator.section）。
 *
 * 判据：后端 `/text` 的标题块满足 `text === section`；标题位于正文**上方**
 * （左上原点下即 y 更小）。取「位于选点上方、且纵向最近」的标题；
 * 若无则退化为纵向最近块的 section。
 */
export function sectionAt(pdfY: number, blocks: PageBlock[]): string | undefined {
  if (!blocks.length) return undefined
  let bestSec: string | undefined
  let bestDelta = Number.POSITIVE_INFINITY
  for (const b of blocks) {
    const sec = (b.section ?? '').trim()
    if (!sec) continue
    const isHeading = (b.text ?? '').trim() === sec
    if (!isHeading) continue
    const cy = (b.bbox[1] + b.bbox[3]) / 2
    // 左上原点：标题在选点上方 ⇒ cy < pdfY ⇒ delta = pdfY - cy > 0
    const delta = pdfY - cy
    if (delta >= -2 && delta < bestDelta) {
      bestDelta = delta
      bestSec = sec
    }
  }
  if (bestSec) return bestSec
  let nearest: PageBlock | null = null
  let nd = Number.POSITIVE_INFINITY
  for (const b of blocks) {
    const cy = (b.bbox[1] + b.bbox[3]) / 2
    const d = Math.abs(cy - pdfY)
    if (d < nd) {
      nd = d
      nearest = b
    }
  }
  return nearest?.section
}

export type { PDFDocumentProxy, PDFPageProxy, PageViewport }
