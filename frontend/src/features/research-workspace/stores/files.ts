/**
 * 草稿文件 store（FRONTEND-ARCH §5）。
 * tree / openDocs / activePath / contents / revision / dirty / outline / compileRun /
 * logLines / compileStatus / lastPdfId；loadTree / open / rename / remove / create /
 * save(debounce) / loadOutline / compile / loadLatest。
 *
 * 纪律：
 * - 自动保存 800ms debounce → `PUT /files/{path}` 带 `expected_revision`（宪法 §51）；
 * - 409 → 提示「文件已在别处修改，已为你刷新最新版本」并静默重拉最新基线；
 * - 编译失败**保留上次成功产物**（宪法 §23）：`lastGoodRunId` 记住最近一次成功 run，
 *   失败时 preview 继续取该 run 的 PDF（后端同步阻塞，POST 返回即终态）。
 */
import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { toRvError, type RvError } from '../api/client'
import {
  createFile as apiCreateFile,
  deleteFile as apiDeleteFile,
  getFile,
  getOutline,
  getTree,
  putFile,
  renameFile as apiRenameFile,
} from '../api/files'
import { compile as apiCompile, COMPILE_IN_PROGRESS_MESSAGE, isCompileInProgress, latestRun } from '../api/compile'
import { isCompileDone, isCompileSuccess } from '../api/types'
import type {
  CompileLogLine,
  CompileRun,
  CompileStatus,
  FileKind,
  FileNode,
  OutlineItem,
} from '../api/types'

export type SaveState = 'idle' | 'unsaved' | 'saving' | 'saved' | 'conflict'

const SAVE_DEBOUNCE_MS = 800

export const useRwFilesStore = defineStore('rw.files', () => {
  const tree = ref<FileNode[]>([])
  const openDocs = ref<string[]>([])
  const activePath = ref<string | null>(null)
  /** path → 编辑器当前串 */
  const contents = ref<Record<string, string>>({})
  /** path → 已保存基线串 */
  const savedContents = ref<Record<string, string>>({})
  const revisions = ref<Record<string, number>>({})
  const outline = ref<OutlineItem[]>([])
  /** outline 的来源文件（后端 `source_file`，无 main.tex 时为 fallback）。 */
  const outlineSource = ref<string>('main.tex')

  const compileRun = ref<CompileRun | null>(null)
  const logLines = ref<CompileLogLine[]>([])
  const compileStatus = ref<CompileStatus | 'idle'>('idle')
  /** 最近一次**成功**运行的 id —— 失败时 preview 继续用它（宪法 §23）。 */
  const lastGoodRunId = ref<string | null>(null)
  const lastPdfId = ref<string | null>(null)

  const saveState = ref<SaveState>('idle')
  const savedAt = ref<string>('')
  const loading = ref(false)
  const treeLoading = ref(false)
  const outlineLoading = ref(false)
  /** 编译侧的**人话提示**（如「正在编译中，请稍候」）；无提示为空串。 */
  const compileNotice = ref<string>('')
  const error = ref<RvError | null>(null)

  const activeContent = computed(() => (activePath.value ? contents.value[activePath.value] ?? '' : ''))
  const activeRevision = computed(() => (activePath.value ? revisions.value[activePath.value] ?? 0 : 0))
  const dirty = computed(() => {
    const p = activePath.value
    if (!p) return false
    return (contents.value[p] ?? '') !== (savedContents.value[p] ?? '')
  })
  /** preview 应显示的 run：优先最近成功 run，否则当前 run（成功时）。 */
  const previewRunId = computed<string | null>(() => {
    if (lastGoodRunId.value) return lastGoodRunId.value
    if (compileRun.value && isCompileSuccess(compileRun.value.status)) return compileRun.value.run_id
    return null
  })

  let saveTimer: ReturnType<typeof setTimeout> | null = null

  function contentOf(path: string): string {
    return contents.value[path] ?? ''
  }

  async function loadTree(projectId: string): Promise<FileNode[]> {
    treeLoading.value = true
    error.value = null
    try {
      tree.value = await getTree(projectId)
      return tree.value
    } catch (e) {
      tree.value = []
      error.value = toRvError(e)
      return []
    } finally {
      treeLoading.value = false
    }
  }

  async function open(projectId: string, path: string): Promise<boolean> {
    error.value = null
    if (!openDocs.value.includes(path)) openDocs.value = [...openDocs.value, path]
    activePath.value = path
    if (contents.value[path] !== undefined) return true
    loading.value = true
    try {
      const fc = await getFile(projectId, path)
      contents.value = { ...contents.value, [path]: fc.content }
      savedContents.value = { ...savedContents.value, [path]: fc.content }
      revisions.value = { ...revisions.value, [path]: fc.revision }
      savedAt.value = fc.saved_at
      saveState.value = 'saved'
      return true
    } catch (e) {
      error.value = toRvError(e)
      return false
    } finally {
      loading.value = false
    }
  }

  /** 强制重拉某文件（不改变 activePath 之外状态）。 */
  async function reload(projectId: string, path: string): Promise<void> {
    try {
      const fc = await getFile(projectId, path)
      contents.value = { ...contents.value, [path]: fc.content }
      savedContents.value = { ...savedContents.value, [path]: fc.content }
      revisions.value = { ...revisions.value, [path]: fc.revision }
    } catch (e) {
      error.value = toRvError(e)
    }
  }

  function closeDoc(path: string) {
    openDocs.value = openDocs.value.filter((p) => p !== path)
    if (activePath.value === path) activePath.value = openDocs.value[0] ?? null
  }

  /** 编辑器内容变更（本地即时生效，随后 debounce 保存）。 */
  function setContent(path: string, content: string) {
    contents.value = { ...contents.value, [path]: content }
    saveState.value = (savedContents.value[path] ?? '') === content ? 'saved' : 'unsaved'
  }

  /**
   * 触发自动保存（debounce）。`path` 缺省用 activePath。
   * 返回一个在 debounce 结束后 resolve 的 Promise，便于测试。
   */
  function save(projectId: string, path?: string, content?: string): Promise<void> {
    const target = path ?? activePath.value
    if (!target) return Promise.resolve()
    if (content !== undefined) setContent(target, content)
    if (saveTimer) clearTimeout(saveTimer)
    return new Promise((resolve) => {
      saveTimer = setTimeout(() => {
        void flushSave(projectId, target).finally(resolve)
      }, SAVE_DEBOUNCE_MS)
    })
  }

  async function flushSave(projectId: string, path: string): Promise<void> {
    const content = contents.value[path] ?? ''
    if ((savedContents.value[path] ?? '') === content) {
      saveState.value = 'saved'
      return
    }
    saveState.value = 'saving'
    try {
      const fc = await putFile(projectId, path, content, revisions.value[path] ?? 0)
      savedContents.value = { ...savedContents.value, [path]: content }
      revisions.value = { ...revisions.value, [path]: fc.revision }
      savedAt.value = fc.saved_at
      saveState.value = 'saved'
    } catch (e) {
      const err = toRvError(e)
      error.value = err
      if (err.status === 409) {
        saveState.value = 'conflict'
        // 静默重拉最新基线（保留用户编辑串，供其重新保存）
        try {
          const latest = await getFile(projectId, path)
          savedContents.value = { ...savedContents.value, [path]: latest.content }
          revisions.value = { ...revisions.value, [path]: latest.revision }
        } catch {
          /* 二次失败保持 conflict 态 */
        }
      } else {
        saveState.value = 'unsaved'
      }
    }
  }

  async function create(
    projectId: string,
    path: string,
    kind: FileKind = 'tex',
    content = '',
  ): Promise<boolean> {
    error.value = null
    try {
      const fc = await apiCreateFile(projectId, path, kind, content)
      contents.value = { ...contents.value, [path]: fc.content }
      savedContents.value = { ...savedContents.value, [path]: fc.content }
      revisions.value = { ...revisions.value, [path]: fc.revision }
      openDocs.value = openDocs.value.includes(path) ? openDocs.value : [...openDocs.value, path]
      activePath.value = path
      saveState.value = 'saved'
      await loadTree(projectId)
      return true
    } catch (e) {
      error.value = toRvError(e)
      return false
    }
  }

  async function rename(projectId: string, from: string, to: string): Promise<boolean> {
    error.value = null
    try {
      await apiRenameFile(projectId, from, to)
      openDocs.value = openDocs.value.map((p) => (p === from ? to : p))
      if (activePath.value === from) activePath.value = to
      if (contents.value[from] !== undefined) {
        contents.value = { ...contents.value, [to]: contents.value[from] }
        savedContents.value = { ...savedContents.value, [to]: savedContents.value[from] ?? '' }
        revisions.value = { ...revisions.value, [to]: revisions.value[from] ?? 0 }
        const { [from]: _c, ...restC } = contents.value
        const { [from]: _s, ...restS } = savedContents.value
        const { [from]: _r, ...restR } = revisions.value
        contents.value = restC
        savedContents.value = restS
        revisions.value = restR
      }
      await Promise.all([loadTree(projectId), loadOutline(projectId)])
      return true
    } catch (e) {
      error.value = toRvError(e)
      return false
    }
  }

  async function remove(projectId: string, path: string): Promise<boolean> {
    error.value = null
    try {
      await apiDeleteFile(projectId, path)
      closeDoc(path)
      await Promise.all([loadTree(projectId), loadOutline(projectId)])
      return true
    } catch (e) {
      error.value = toRvError(e)
      return false
    }
  }

  async function loadOutline(projectId: string, entry = 'main.tex'): Promise<OutlineItem[]> {
    outlineLoading.value = true
    try {
      outline.value = await getOutline(projectId, entry)
      outlineSource.value = entry
      return outline.value
    } catch (e) {
      outline.value = []
      error.value = toRvError(e)
      return []
    } finally {
      outlineLoading.value = false
    }
  }

  function applyRun(run: CompileRun) {
    compileRun.value = run
    logLines.value = run.log_lines ?? []
    compileStatus.value = run.status
    // 失败时保留上次成功产物（宪法 §23）
    if (isCompileSuccess(run.status) && run.pdf_artifact_id) {
      lastGoodRunId.value = run.run_id
      lastPdfId.value = run.pdf_artifact_id
    } else if (run.pdf_artifact_id) {
      lastPdfId.value = run.pdf_artifact_id
    }
  }

  /**
   * 触发编译。后端**同步阻塞**至完成（≈15~25s）；期间本地状态置 `running`，
   * 头部编译条显示「编译中…」。返回本次运行 id（失败返回 id 或 null）。
   *
   * 并发去重（宪法 §31/§54）：若同一入口已有编译在跑，后端回 409 +
   * `COMPILE_IN_PROGRESS`——此时**不当作失败**，而是给出人话提示并复用既有 run
   * 继续轮询到终态（不白屏、不报裸错误、不静默丢弃）。
   */
  async function compile(projectId: string, entry = 'main.tex'): Promise<string | null> {
    error.value = null
    compileNotice.value = ''
    compileStatus.value = 'running'
    try {
      const run = await apiCompile(projectId, entry)
      if (run) {
        applyRun(run)
        return run.run_id
      }
      compileStatus.value = 'failed'
      return null
    } catch (e) {
      if (isCompileInProgress(e)) {
        return await reuseRunningCompile(projectId)
      }
      error.value = toRvError(e)
      compileStatus.value = 'failed'
      return null
    }
  }

  /**
   * 已有编译在跑时的前台表现：给人话提示 + 复用既有 run 轮询到终态。
   * 轮询只读 `latest`，不改后端；终态后清提示并返回 run id（失败也返回，便于 UI 展示日志）。
   */
  async function reuseRunningCompile(
    projectId: string,
    intervalMs = 1500,
    timeoutMs = 120000,
  ): Promise<string | null> {
    compileNotice.value = COMPILE_IN_PROGRESS_MESSAGE
    compileStatus.value = 'running'
    const deadline = Date.now() + timeoutMs
    let run: CompileRun | null = null
    try {
      run = await latestRun(projectId)
      if (run) applyRun(run)
      while (Date.now() < deadline) {
        if (run && isCompileDone(run.status)) break
        await new Promise((r) => setTimeout(r, intervalMs))
        run = await latestRun(projectId)
        if (run) applyRun(run)
      }
    } catch (e) {
      error.value = toRvError(e)
      compileStatus.value = 'failed'
      return null
    } finally {
      compileNotice.value = ''
    }
    return run?.run_id ?? null
  }

  async function loadLatest(projectId: string): Promise<CompileRun | null> {
    try {
      const run = await latestRun(projectId)
      if (run) {
        if (isCompileSuccess(run.status) && run.pdf_artifact_id) lastGoodRunId.value = run.run_id
        applyRun(run)
      }
      return run
    } catch (e) {
      error.value = toRvError(e)
      return null
    }
  }

  /** 清空编译日志（仅本地视图，不动后端 run）。 */
  function clearLog() {
    logLines.value = []
  }

  /** 恢复最近状态（宪法 §84）：打开上次文件。 */
  async function resume(projectId: string, path?: string | null): Promise<boolean> {
    if (path) return open(projectId, path)
    const first = tree.value.find((n) => n.type === 'file')
    const main = tree.value.flatMap((n) => [n, ...(n.children ?? [])]).find((n) => n.path === 'main.tex')
    const target = main?.path ?? first?.path
    return target ? open(projectId, target) : false
  }

  function reset() {
    tree.value = []
    openDocs.value = []
    activePath.value = null
    contents.value = {}
    savedContents.value = {}
    revisions.value = {}
    outline.value = []
    compileRun.value = null
    logLines.value = []
    compileStatus.value = 'idle'
    compileNotice.value = ''
    lastGoodRunId.value = null
    lastPdfId.value = null
    saveState.value = 'idle'
    error.value = null
  }

  return {
    tree,
    openDocs,
    activePath,
    contents,
    savedContents,
    revisions,
    outline,
    outlineSource,
    compileRun,
    logLines,
    compileStatus,
    compileNotice,
    lastGoodRunId,
    lastPdfId,
    previewRunId,
    saveState,
    savedAt,
    loading,
    treeLoading,
    outlineLoading,
    error,
    activeContent,
    activeRevision,
    dirty,
    contentOf,
    loadTree,
    open,
    reload,
    closeDoc,
    setContent,
    save,
    flushSave,
    create,
    rename,
    remove,
    loadOutline,
    compile,
    loadLatest,
    clearLog,
    resume,
    reset,
  }
})
