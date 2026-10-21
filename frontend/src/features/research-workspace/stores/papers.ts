/**
 * 文献 store（FRONTEND-ARCH §5）。
 * list / activeId / readState / pageCache；load / importPaper / remove / setActive / setReadState / loadText。
 *
 * `pageCache` 以 `paperId → page → PageText` 缓存解析文本，供阅读器定位与选文使用。
 */
import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { toRvError, type RvError } from '../api/client'
import { deletePaper, getPageText, listPapers, patchPaper, uploadPaper } from '../api/papers'
import type { Paper, PageText } from '../api/types'

export const useRwPapersStore = defineStore('rw.papers', () => {
  const list = ref<Paper[]>([])
  const activeId = ref<string | null>(null)
  /** paperId → 阅读状态（未读/在读/已读），与后端 read_state 对齐。 */
  const readState = ref<Record<string, string>>({})
  /** paperId → page → PageText */
  const pageCache = ref<Record<string, Record<number, PageText>>>({})

  const loading = ref(false)
  const error = ref<RvError | null>(null)

  /**
   * 被「×」关闭的文献 Tab（会话态，§70：× 必须真实生效，关闭不删除文献）。
   * `closeTab` / `setActive` / `reset` 与阅读器 Tab 条（PdfReaderPane）共用。
   */
  const closedIds = ref<string[]>([])
  /** Tab 条可见文献 = 列表 − 已关闭；全部关闭时 Tab 条为空但文献列表仍在。 */
  const openTabs = computed<Paper[]>(() => list.value.filter((p) => !closedIds.value.includes(p.id)))

  const activePaper = computed<Paper | null>(
    () => list.value.find((p) => p.id === activeId.value) ?? null,
  )

  async function load(projectId: string): Promise<Paper[]> {
    loading.value = true
    error.value = null
    try {
      const items = await listPapers(projectId)
      list.value = items
      const rs: Record<string, string> = {}
      for (const p of items) rs[p.id] = p.read_state
      readState.value = rs
      if (items.length && !items.some((p) => p.id === activeId.value)) {
        // 优先恢复一个未被 × 关闭的 Tab；全部被关时回退第一篇（Tab 条为空但列表仍在）
        const first = items.find((p) => !closedIds.value.includes(p.id)) ?? items[0]
        activeId.value = first.id
      }
      return items
    } catch (e) {
      list.value = []
      error.value = toRvError(e)
      return []
    } finally {
      loading.value = false
    }
  }

  async function importPaper(projectId: string, file: File): Promise<Paper | null> {
    error.value = null
    try {
      const paper = await uploadPaper(projectId, file)
      list.value = [...list.value, paper]
      return paper
    } catch (e) {
      error.value = toRvError(e)
      return null
    }
  }

  async function remove(projectId: string, paperId: string): Promise<boolean> {
    error.value = null
    try {
      await deletePaper(projectId, paperId)
      list.value = list.value.filter((p) => p.id !== paperId)
      if (activeId.value === paperId) {
        activeId.value = list.value[0]?.id ?? null
      }
      return true
    } catch (e) {
      error.value = toRvError(e)
      return false
    }
  }

  function setActive(paperId: string | null) {
    activeId.value = paperId
    // 从文献列表重新打开 = 恢复 Tab（清除 × 关闭标记）
    if (paperId && closedIds.value.includes(paperId)) {
      closedIds.value = closedIds.value.filter((id) => id !== paperId)
    }
  }

  /**
   * 关闭一个文档 Tab：从 Tab 条移除（不删除项目文献）；
   * 若关的是当前打开的文献，自动切到剩余 Tab 的第一个。
   */
  function closeTab(paperId: string) {
    closedIds.value = [...closedIds.value, paperId]
    if (activeId.value === paperId) {
      activeId.value = openTabs.value.find((p) => p.id !== paperId)?.id ?? null
    }
  }

  async function setReadState(
    projectId: string,
    paperId: string,
    state: string,
    page?: number,
  ): Promise<void> {
    const prev = readState.value[paperId]
    readState.value[paperId] = state
    try {
      await patchPaper(projectId, paperId, { read_state: state, last_page: page })
    } catch (e) {
      readState.value[paperId] = prev
      error.value = toRvError(e)
    }
  }

  async function loadText(projectId: string, paperId: string, page: number): Promise<PageText | null> {
    if (pageCache.value[paperId]?.[page]) return pageCache.value[paperId][page]
    try {
      const text = await getPageText(projectId, paperId, page)
      const forPaper = pageCache.value[paperId] ?? {}
      forPaper[page] = text
      pageCache.value = { ...pageCache.value, [paperId]: forPaper }
      return text
    } catch (e) {
      error.value = toRvError(e)
      return null
    }
  }

  function reset() {
    list.value = []
    activeId.value = null
    closedIds.value = []
    readState.value = {}
    pageCache.value = {}
    error.value = null
  }

  return {
    list,
    activeId,
    closedIds,
    openTabs,
    readState,
    pageCache,
    loading,
    error,
    activePaper,
    load,
    importPaper,
    remove,
    setActive,
    closeTab,
    setReadState,
    loadText,
    reset,
  }
})
