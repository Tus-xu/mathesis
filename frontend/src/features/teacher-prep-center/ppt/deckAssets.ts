/**
 * 课件图片资产 URL 解析（R8c）。
 *
 * 背景：编辑器此前只渲染 `slide.math_object`（椭圆，前端确定性绘制）；走 MATH_RENDER
 * 资产通道的图形（直线 / 二次函数 / 幂函数图象）是服务端渲染好的 PNG，而编辑器**完全
 * 没有取图能力** → 教师看到「有图页没图」，只有导出 PPTX 才可见。
 *
 * 这里做模块级缓存：同一 (deckId, assetId) 在 TTL 内只请求一次（缩略图 + 大图共享同一
 * URL，避免 N 次重复请求）；失败不写缓存（允许重试），失败项交给调用方**显式**渲染
 * 失败态（禁止静默空白，遵守 UI Reality Contract）。
 */
import { ref, watch, type Ref } from 'vue'
import { fetchDeckAssetUrl } from '@/features/teacher-prep-center/api/deckApi'

/** 后端 presign TTL 为 30min；前端保守缓存 10min 后重取，避免过期 URL 静默失效。 */
const URL_TTL_MS = 10 * 60 * 1000
const cache = new Map<string, { url: string; at: number }>()
const inflight = new Map<string, Promise<string>>()

function keyOf(deckId: string, assetId: string): string {
  return `${deckId}::${assetId}`
}

/** 解析单个资产 URL（带缓存与并发去重）。失败抛出且不写缓存。 */
export async function resolveDeckAssetUrl(deckId: string, assetId: string): Promise<string> {
  const key = keyOf(deckId, assetId)
  const hit = cache.get(key)
  if (hit && Date.now() - hit.at < URL_TTL_MS) return hit.url
  const running = inflight.get(key)
  if (running) return running
  const task = fetchDeckAssetUrl(deckId, assetId)
    .then((r) => {
      cache.set(key, { url: r.url, at: Date.now() })
      return r.url
    })
    .finally(() => {
      inflight.delete(key)
    })
  inflight.set(key, task)
  return task
}

/** 测试/登出时清缓存（URL 与账号绑定，切换账号不得复用）。 */
export function clearDeckAssetCache(): void {
  cache.clear()
  inflight.clear()
}

/**
 * 批量解析某一页的资产 URL。
 *
 * @returns `urls` = assetId → URL；`failed` = 取图失败的 assetId 列表
 *          （调用方据此渲染「图片加载失败」而非留白）。
 */
export function useDeckAssetUrls(
  deckId: Ref<string | null>,
  assetIds: Ref<string[]>,
): { urls: Ref<Record<string, string>>; failed: Ref<string[]> } {
  const urls = ref<Record<string, string>>({})
  const failed = ref<string[]>([])

  watch(
    () => [deckId.value, assetIds.value.join(',')] as const,
    async () => {
      const id = deckId.value
      const ids = assetIds.value.filter(Boolean)
      if (!id || ids.length === 0) {
        urls.value = {}
        failed.value = []
        return
      }
      const next: Record<string, string> = {}
      const bad: string[] = []
      await Promise.all(
        ids.map(async (aid) => {
          try {
            next[aid] = await resolveDeckAssetUrl(id, aid)
          } catch {
            bad.push(aid)
          }
        }),
      )
      urls.value = next
      failed.value = bad
    },
    { immediate: true },
  )

  return { urls, failed }
}
