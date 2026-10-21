/**
 * 项目 store（FRONTEND-ARCH §5）。
 * current / list / overview / recent / history；open / loadList / loadOverview / create / saveRecent。
 *
 * 端点缺失时不抛出到 UI，而是把 `error` 设为**中文人话** `RvError`，由视图渲染诚实空态。
 */
import { ref } from 'vue'
import { defineStore } from 'pinia'
import { toRvError, type RvError } from '../api/client'
import { newIdempotencyKey } from '@/api/idempotency'
import {
  createProject,
  getOverview,
  getProject,
  getRecent,
  listProjects,
  patchProject,
  putRecent,
  getHistory,
} from '../api/projects'
import type {
  CreateProjectInput,
  HistoryEntry,
  ProjectOverview,
  ProjectRecent,
  ResearchProject,
  UpdateProjectInput,
} from '../api/types'

export const useRwProjectStore = defineStore('rw.project', () => {
  const current = ref<ResearchProject | null>(null)
  const list = ref<ResearchProject[]>([])
  const overview = ref<ProjectOverview | null>(null)
  const recent = ref<ProjectRecent | null>(null)
  const history = ref<HistoryEntry[]>([])

  const loading = ref(false)
  /** 项目更新（改名 / 改描述）进行中 —— 供 UI 显示「正在保存…」。 */
  const saving = ref(false)
  const error = ref<RvError | null>(null)

  async function loadList(): Promise<ResearchProject[]> {
    loading.value = true
    error.value = null
    try {
      list.value = await listProjects()
      return list.value
    } catch (e) {
      error.value = toRvError(e)
      return []
    } finally {
      loading.value = false
    }
  }

  async function open(projectId: string): Promise<ResearchProject | null> {
    loading.value = true
    error.value = null
    try {
      current.value = await getProject(projectId)
      return current.value
    } catch (e) {
      current.value = null
      error.value = toRvError(e)
      return null
    } finally {
      loading.value = false
    }
  }

  async function loadOverview(projectId: string): Promise<ProjectOverview | null> {
    try {
      overview.value = await getOverview(projectId)
      return overview.value
    } catch (e) {
      overview.value = null
      error.value = toRvError(e)
      return null
    }
  }

  async function create(input: CreateProjectInput): Promise<ResearchProject | null> {
    loading.value = true
    error.value = null
    try {
      const p = await createProject(input)
      current.value = p
      list.value = [...list.value, p]
      return p
    } catch (e) {
      error.value = toRvError(e)
      return null
    } finally {
      loading.value = false
    }
  }

  /**
   * 更新项目（改名 / 改描述）—— 就地编辑与顶栏「重命名项目」共用的**唯一保存逻辑**。
   *
   * - 每次尝试生成**新的** `client_request_id`（后端 `ResearchProjectUpdate` 必填，
   *   min_length=8）—— 复用 `@/api/idempotency` 的统一生成器，不另造；
   * - `expected_revision` 取自项目对象（list / GET 均返回 `revision`），冲突时后端 409；
   * - 成功后**就地更新** `current` 与 `list`，顶栏名称与左栏卡片**立即刷新**（无需重拉）。
   */
  async function updateProject(
    projectId: string,
    patch: { name?: string; description?: string | null; archived?: boolean },
  ): Promise<ResearchProject | null> {
    if (!projectId) return null
    const base =
      current.value?.id === projectId
        ? current.value
        : list.value.find((p) => p.id === projectId)
    const body: UpdateProjectInput = {
      ...patch,
      expected_revision: base?.revision ?? 0,
      client_request_id: newIdempotencyKey(),
    }
    saving.value = true
    error.value = null
    try {
      const updated = await patchProject(projectId, body)
      if (current.value?.id === updated.id) current.value = updated
      const idx = list.value.findIndex((p) => p.id === updated.id)
      if (idx >= 0) {
        const next = list.value.slice()
        next[idx] = { ...next[idx], ...updated }
        list.value = next
      }
      return updated
    } catch (e) {
      error.value = toRvError(e)
      return null
    } finally {
      saving.value = false
    }
  }

  async function loadRecent(projectId: string): Promise<ProjectRecent | null> {
    try {
      recent.value = await getRecent(projectId)
      return recent.value
    } catch (e) {
      recent.value = null
      error.value = toRvError(e)
      return null
    }
  }

  async function saveRecent(projectId: string, value: ProjectRecent): Promise<ProjectRecent | null> {
    try {
      recent.value = await putRecent(projectId, value)
      return recent.value
    } catch (e) {
      error.value = toRvError(e)
      return null
    }
  }

  async function loadHistory(projectId: string): Promise<HistoryEntry[]> {
    try {
      history.value = await getHistory(projectId)
      return history.value
    } catch (e) {
      history.value = []
      error.value = toRvError(e)
      return []
    }
  }

  function reset() {
    current.value = null
    overview.value = null
    recent.value = null
    history.value = []
    error.value = null
  }

  return {
    current,
    list,
    overview,
    recent,
    history,
    loading,
    saving,
    error,
    loadList,
    open,
    loadOverview,
    create,
    updateProject,
    loadRecent,
    saveRecent,
    loadHistory,
    reset,
  }
})
