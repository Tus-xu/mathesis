/**
 * 顶栏「历史」数据源（宪法 §50 / VISUAL-SPEC §7-5「历史按钮没有历史」）。
 *
 * 背景（实测）：`GET /api/research-vnext/projects/{id}/history` 返回
 *   `{ recent: {workspace,paper_id,page,file_path,line,updated_at},
 *      recent_papers: [{id,title,page,updated_at}, ...] }`
 * 而 `api/projects.ts#getHistory` 按 `{ items }` 解析 → store 的 `history` 恒为空数组。
 *
 * 边界纪律：本任务只允许改 `shell/**` 等文件，**不得改共享的 `api/` 与 `stores/`**，
 * 因此这里用 `rwGet`（只 import，不修改）做一次**只读适配**，把 `recent_papers` 映射成
 * 顶栏历史条目。已同步向 team-lead 申请把该映射下沉回 `api/projects.ts#getHistory`，
 * 一旦下沉，本文件即可删除、改回 `project.loadHistory()`。
 */
import { ref } from 'vue'
import { rwGet } from '../api/client'

export interface HeaderHistoryItem {
  id: string
  title: string
  page: number | null
  updated_at: string
}

interface RawHistory {
  recent?: { workspace?: string; file_path?: string | null; line?: number | null } | null
  recent_papers?: Array<{ id?: string; title?: string; page?: number | null; updated_at?: string }> | null
}

export function useProjectHistory() {
  const items = ref<HeaderHistoryItem[]>([])
  const loading = ref(false)
  const failed = ref(false)

  async function load(projectId: string) {
    if (!projectId) {
      items.value = []
      failed.value = false
      return
    }
    loading.value = true
    try {
      const r = await rwGet<RawHistory>(`/projects/${projectId}/history`)
      const raw = Array.isArray(r?.recent_papers) ? r.recent_papers : []
      items.value = raw
        .filter((p) => !!p?.id)
        .map((p) => ({
          id: String(p.id),
          title: p.title || '未命名文献',
          page: typeof p.page === 'number' ? p.page : null,
          updated_at: p.updated_at || '',
        }))
      failed.value = false
    } catch {
      failed.value = true
      items.value = []
    } finally {
      loading.value = false
    }
  }

  return { items, loading, failed, load }
}
