/**
 * projectCover.ts —— 项目封面解析（**不再使用打包位图 / 品牌渐变**）。
 *
 * ══════════════════════════════════════════════════════════════════════
 * 为什么重写（主理人裁决，2026-09-16）
 * ══════════════════════════════════════════════════════════════════════
 * 上一版行为（fe-shell 实现）：
 *   1. 优先用后端 `cover_url`（**后端 `ResearchProject` 根本没有该字段** → 永远走不到）；
 *   2. 退回随前端打包的 6 张**柔焦风景位图**（按项目 id 稳定映射）。
 *
 * 问题：那 6 张风景图不是项目的真实封面，却会让人以为"这个项目有封面照片" ——
 * 触碰宪法 §70「不允许假 UI」的精神。（品牌渐变同样不合格：会读成"没做完"。）
 *
 * 新行为（**真实封面 → 中性兜底**，两级）：
 *   1. **真实封面**：取项目**第一篇文献的 PDF 首页**，用 pdfjs 渲染成小图 → 这是黄金图里
 *      那个"封面"的**真实语义**（科研项目封面的自然来源就是它的论文首页）。
 *      渲染结果按 `paperId` 缓存，避免同一项目重复解析 PDF。
 *   2. **中性兜底**：项目无文献 / PDF 未解析 / 渲染失败 → 返回 `null`，
 *      由调用方渲染「浅底 + 文档图标」的**诚实空态**。
 *
 * ⚠️ 后端若将来提供 `cover_url` / `cover` 字段，`resolveCover(explicit, …)` 会优先使用它。
 *
 * 用法：
 *   const cov = useProjectCover(() => props.projectId)      // 响应式
 *   cov.value  // string | null
 */
import { onBeforeUnmount, ref, watch, type Ref } from 'vue'
import { RESEARCH_API_BASE, rwGet } from '../api/client'
import { getAccessToken } from '@/api/authSession'

/** 已解析封面缓存：projectId → objectURL | null（null 表示"确认没有真实封面"，不再重试）。 */
const CACHE = new Map<string, string | null>()
/** 同项目并发去重。 */
const INFLIGHT = new Map<string, Promise<string | null>>()

interface PaperBrief {
  id: string
  asset_id?: string | null
  parse_status?: string | null
}

/**
 * 取项目第一篇**已解析**文献的 PDF 首页，渲染成 dataURL 封面。
 * 失败一律返回 `null`（调用方渲染中性兜底），**不抛错、不打扰用户**。
 */
async function renderFirstPageCover(projectId: string): Promise<string | null> {
  try {
    const res = await rwGet<{ items?: PaperBrief[] }>(`/projects/${projectId}/papers`)
    const papers = (res?.items ?? []).filter((p) => p?.asset_id)
    if (!papers.length) return null
    // 优先已解析的（能拿到文本/尺寸），否则退回第一篇带 asset 的
    const paper = papers.find((p) => (p.parse_status ?? '') === 'parsed') ?? papers[0]

    // 拉 PDF 字节（走 8000 网关，带 Bearer）
    const token = getAccessToken()
    const url = `${RESEARCH_API_BASE}/assets/${encodeURIComponent(String(paper.asset_id))}/content`
    const r = await fetch(url, { headers: token ? { Authorization: `Bearer ${token}` } : {} })
    if (!r.ok) return null
    const bytes = new Uint8Array(await r.arrayBuffer())
    if (bytes.length < 5 || String.fromCharCode(...bytes.slice(0, 5)) !== '%PDF-') return null

    const pdfjs = await import('pdfjs-dist')
    const { configurePdfWorker } = await import('../read/usePdfDocument')
    configurePdfWorker()
    const doc = await pdfjs.getDocument({ data: bytes }).promise
    try {
      const page = await doc.getPage(1)
      // 渲染到 128px 宽的缩略图（2× DPR 保证在高分屏不糊）
      const base = page.getViewport({ scale: 1 })
      const targetW = 128
      const scale = (targetW * 2) / base.width
      const vp = page.getViewport({ scale })
      const canvas = document.createElement('canvas')
      canvas.width = Math.max(1, Math.floor(vp.width))
      canvas.height = Math.max(1, Math.floor(vp.height))
      const cx = canvas.getContext('2d')
      if (!cx) return null
      cx.fillStyle = '#FFFFFF'
      cx.fillRect(0, 0, canvas.width, canvas.height)
      // 参数类型与 `read/usePdfDocument.ts::renderPageToCanvas` 保持一致
      // （不同 pdfjs 版本对 `canvas` 字段的声明不一致，用 Parameters 取签名最稳）
      const params: Parameters<typeof page.render>[0] = { canvasContext: cx, viewport: vp }
      await page.render(params).promise
      return canvas.toDataURL('image/png')
    } finally {
      await doc.destroy().catch(() => {})
    }
  } catch {
    return null
  }
}

/** 取（并缓存）某项目的真实封面；无真实封面时解析为 `null`。 */
export function getProjectCover(projectId: string): Promise<string | null> {
  const id = (projectId || '').trim()
  if (!id) return Promise.resolve(null)
  if (CACHE.has(id)) return Promise.resolve(CACHE.get(id) ?? null)
  const inflight = INFLIGHT.get(id)
  if (inflight) return inflight
  const p = renderFirstPageCover(id)
    .then((v) => {
      CACHE.set(id, v)
      return v
    })
    .finally(() => INFLIGHT.delete(id))
  INFLIGHT.set(id, p)
  return p
}

/**
 * 响应式封面：`Ref<string | null>`。
 * 项目 id 变化时自动重取；组件卸载不做额外清理（缓存是跨组件共享的）。
 */
export function useProjectCover(projectId: Ref<string | undefined | null>): Ref<string | null> {
  const cover = ref<string | null>(null)
  let seq = 0
  const load = async (id: string) => {
    const mine = ++seq
    cover.value = id ? (CACHE.get(id) ?? null) : null
    if (!id) return
    // 极小概率：先显示缓存，再异步刷新
    const v = await getProjectCover(id)
    if (mine === seq) cover.value = v
  }
  watch(() => projectId.value ?? '', (v) => void load(String(v)), { immediate: true })
  onBeforeUnmount(() => {
    seq++
  })
  return cover
}
