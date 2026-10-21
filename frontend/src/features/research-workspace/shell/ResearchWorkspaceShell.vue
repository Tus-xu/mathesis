<template>
  <div class="rw-shell" data-research-app :style="cssVars">
    <!-- 与学生端同源的氛围光斑：只做背景，不参与交互 -->
    <div class="rw-ambient" aria-hidden="true">
      <span class="rw-ambient__b1"></span>
      <span class="rw-ambient__b2"></span>
      <span class="rw-ambient__b3"></span>
    </div>
    <!-- 用户菜单 / 退出登录由 GlobalHeader 自包含实现（统一 auth store），无需上抛 -->
    <GlobalHeader
      :mode="workspace"
      :project="project.current"
      :projects="project.list"
      :project-id="projectId"
      :user-name="userName"
      :user-avatar="userAvatar"
      :sync-text="syncText"
      @select-project="onSelectProject"
      @locate-paper="onLocatePaper"
      @change-workspace="onWorkspaceChange"
    />
    <!-- 工作区条**只在中栏内**：黄金图实测 WRITE 的左栏与 Research Agent 都从 y=62 起全高，
         该条不横跨它们 —— 因此它由下方 `rw-mid` 的首行承载（见 `rw-mid` 的 grid-template-rows）。 -->

    <!-- 统一 grid：最后一列恒为 Research Agent（两页共用单一实例，切换不 remount） -->
    <div class="rw-body" :class="workspace === 'read' ? 'rw-body--read' : 'rw-body--write'">
      <!-- ───────────── READ 两栏（330 / 1fr）─────────────
           `ReadWorkspace` 根为 `display:contents`，其两子节点（左栏 / 中栏）直接落位
           本三列 grid 的第 1/2 项；第 3 项恒为下方共享的 Research Agent section。 -->
      <ReadWorkspace
        v-if="workspace === 'read'"
        ref="readRef"
        :project-id="projectId"
        @create-project="createFirstProject"
      />

      <!-- ───────────── WRITE 四栏 + 底部日志 ───────────── -->
      <template v-else>
      <!-- 左栏：项目条 + 图标轨 + 文件树 / 论文结构 -->
      <section class="rw-pane rw-pane--left">
        <WriteSidebar
          ref="sidebarRef"
          :project-name="project.current ? project.current.name : ''"
          :project-id="project.current ? project.current.id : ''"
          :tree="files.tree"
          :tree-loading="files.treeLoading"
          :outline="files.outline"
          :outline-loading="files.outlineLoading"
          :active-path="files.activePath"
          @open="onOpenFile"
          @create="onCreateFile"
          @rename="onRenameFile"
          @remove="onRemoveFile"
          @navigate="onNavigate"
          @refresh="onRefreshTree"
          @close="onCloseProject"
        />
        <Resizer
          class="rw-handle rw-handle--right"
          side="left"
          :value="panel.widths.writeLeft"
          :min="bounds.writeLeft.min"
          :max="bounds.writeLeft.max"
          aria-label="调整左栏宽度"
          @update:value="(v) => panel.setWidth('writeLeft', v)"
        />
      </section>

      <!-- 中栏：Editor + Preview（上）/ CompileLog（下） -->
      <section class="rw-mid">
        <div class="rw-mid__top">
          <section class="rw-pane rw-pane--editor">
            <LatexEditorPane
              ref="editorRef"
              :active-path="files.activePath"
              :open-docs="files.openDocs"
              :doc="files.activePath ? files.contentOf(files.activePath) : ''"
              :save-state="files.saveState"
              :editable="true"
              @select="onSelectTab"
              @close="onCloseTab"
              @new-file="onNewFileFromEditor"
              @update:doc="onDocChange"
              @cursor-line="onCursorLine"
              @selection-change="onEditorSelection"
            />
            <Resizer
              class="rw-handle rw-handle--right"
              side="left"
              :value="panel.widths.writeEditor"
              :min="bounds.writeEditor.min"
              :max="bounds.writeEditor.max"
              aria-label="调整编辑器宽度"
              @update:value="(v) => panel.setWidth('writeEditor', v)"
            />
          </section>

          <section class="rw-pane rw-pane--preview">
            <PdfPreviewPane :project-id="projectId" :run-id="files.previewRunId" />
          </section>
        </div>

        <div class="rw-compile-log">
          <CompileLog
            :lines="files.logLines"
            :run="files.compileRun"
            :status="files.compileStatus"
            :expanded="panel.logExpanded"
            :entry="'main.tex'"
            @locate="onLogLocate"
            @clear="files.clearLog"
            @toggle="panel.toggleLog"
          />
        </div>

        <!-- 编译控制条：**浮在中栏顶部居右**，与编辑器文件 Tab 条（.rw-editor__tabs）同一行
             （y=62）。2026-09-18 用户要求把 `阅读|写作` 上移顶栏后，原高 48px 的「工作区条」
             整条塌缩，编译控制条改浮于此，从而把那 48px 全额还给编辑器/预览
             （`.rw-editor` / `.rw-mid__top` 由 662/667 → 710/715）。 -->
        <div v-if="workspace === 'write'" class="rw-mid__ccb">
          <WorkspaceBar
            :compile-status="files.compileStatus"
            :last-compiled-at="compileFinishedText"
            :disabled="!project.current"
            @recompile="onRecompile"
          />
        </div>
      </section>

      </template>

      <!-- ───────────── Research Agent（READ/WRITE 共用单一实例）───────────── -->
      <section key="agent" class="rw-pane rw-pane--agent">
        <ResearchAgentPanel
          :project-id="projectId"
          @locate="onAgentLocate"
          @recompile="onRecompile"
        />
        <Resizer
          v-if="!panel.agentCollapsed"
          class="rw-handle rw-handle--left"
          side="right"
          :value="agentWidth"
          :min="agentBounds.min"
          :max="agentBounds.max"
          aria-label="调整 Agent 面板宽度"
          @update:value="onAgentResize"
        />
      </section>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * 顶层装配：GlobalHeader(62，内含 `阅读|写作` 分段控件) + 三/四栏 grid（FRONTEND-ARCH §2）。
 * 根元素写 `data-research-app` 使 `--rw-*` token 生效（不进 `:root`）。
 *
 * 2026-09-18（eng-topbar-seg）：`阅读|写作` 分段控件按用户指令从**栏内**上移到**顶栏**
 * （与大标题平齐）；READ 的 `rw-read__bar`(64px) 与 WRITE 的 `WorkspaceBar`(48px) 两条
 * 栏内首行随之塌缩，垂直空间全额还给工作区（编译控制条改浮中栏顶部，见 `.rw-mid__ccb`）。
 *
 * T04：WRITE 分支接线真实草稿能力（文件树 / LaTeX 编辑器 / 真实编译 / PDF 预览 / 编译日志）。
 * READ 分支保持 T01 骨架不变。右栏 Agent 的**展示位**归 agent/ 批次（T05），
 * 本分支仅提供「LaTeX 错误解释」的**触发与上下文传递**。
 */
import { computed, defineAsyncComponent, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import GlobalHeader from './GlobalHeader.vue'
import Resizer from './Resizer.vue'
// 重型面板按需加载：READ 不再为首屏预载 CodeMirror / PDF 预览，WRITE 也不预载阅读器。
// 这是「科研端点开像空壳、点击缓慢」的直接优化之一（壳 chunk 由 876KB 拆小）。
const WorkspaceBar = defineAsyncComponent(() => import('./WorkspaceBar.vue'))
const WriteSidebar = defineAsyncComponent(() => import('../write/WriteSidebar.vue'))
const LatexEditorPane = defineAsyncComponent(() => import('../write/LatexEditorPane.vue'))
const PdfPreviewPane = defineAsyncComponent(() => import('../write/PdfPreviewPane.vue'))
const CompileLog = defineAsyncComponent(() => import('../write/CompileLog.vue'))
const ReadWorkspace = defineAsyncComponent(() => import('../read/ReadWorkspace.vue'))
const ResearchAgentPanel = defineAsyncComponent(() => import('../agent/ResearchAgentPanel.vue'))
import { useRwPanelStore, WIDTH_BOUNDS } from '../stores/panel'
import { useRwProjectStore } from '../stores/project'
import { useRwPapersStore } from '../stores/papers'
import { useRwFilesStore } from '../stores/files'
import { useRwAgentStore } from '../stores/agent'
import { useAuthStore } from '@/stores/auth'
import type { FileKind, Locator, WorkspaceMode } from '../api/types'
import '../tokens/research-tokens.css'

const route = useRoute()
const router = useRouter()
const panel = useRwPanelStore()
const project = useRwProjectStore()
const papers = useRwPapersStore()
const files = useRwFilesStore()
const agent = useRwAgentStore()
const auth = useAuthStore()

const bounds = WIDTH_BOUNDS
const sidebarRef = ref<InstanceType<typeof WriteSidebar> | null>(null)
const editorRef = ref<InstanceType<typeof LatexEditorPane> | null>(null)
const readRef = ref<InstanceType<typeof ReadWorkspace> | null>(null)
const lastSyncAt = ref<Date | null>(null)
const compileFinishedAt = ref<Date | null>(null)

const projectId = computed(() => {
  const raw = route.params.projectId
  return typeof raw === 'string' && raw ? raw : ''
})

const workspace = computed<WorkspaceMode>(() => (route.path.endsWith('/write') ? 'write' : 'read'))

const userName = computed(() => {
  const u = auth.user as { nickname?: string; name?: string } | null
  const nm = u?.nickname || u?.name || ''
  if (nm) return nm
  return u ? '研究者' : '未登录'
})

/** 真实头像 URL（后端 `avatar_url`；为空时顶栏渲染姓名首字，不伪造头像）。 */
const userAvatar = computed(() => {
  const u = auth.user as { avatar_url?: string | null } | null
  return u?.avatar_url || ''
})

/** READ 与 WRITE 的 Agent 宽度/边界不同（WRITE 必须更窄，宪法 §82）。 */
const agentWidth = computed(() =>
  workspace.value === 'read' ? panel.widths.readAgent : panel.widths.writeAgent,
)
const agentBounds = computed(() =>
  workspace.value === 'read' ? bounds.readAgent : bounds.writeAgent,
)

function fmtTime(d: Date): string {
  const p = (n: number) => String(n).padStart(2, '0')
  return `${p(d.getHours())}:${p(d.getMinutes())}`
}
/** 同步时间文案：`今天 HH:mm` / `昨天 HH:mm` / `M月D日`（黄金图为「今天 10:24」）。 */
function fmtSyncTime(d: Date): string {
  const now = new Date()
  const sameDay = (a: Date, b: Date) =>
    a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate()
  if (sameDay(d, now)) return `今天 ${fmtTime(d)}`
  const y = new Date(now)
  y.setDate(now.getDate() - 1)
  if (sameDay(d, y)) return `昨天 ${fmtTime(d)}`
  return `${d.getMonth() + 1}月${d.getDate()}日`
}
const syncText = computed(() => (lastSyncAt.value ? fmtSyncTime(lastSyncAt.value) : ''))
const compileFinishedText = computed(() => (compileFinishedAt.value ? fmtTime(compileFinishedAt.value) : null))

const cssVars = computed<Record<string, string>>(() => ({
  '--rw-w-sidebar': panel.sidebarCollapsed ? 'var(--rw-w-collapsed)' : `${panel.widths.readSidebar}px`,
  '--rw-w-agent': panel.agentCollapsed ? 'var(--rw-w-collapsed)' : `${panel.widths.readAgent}px`,
  '--rw-w-left': `${panel.widths.writeLeft}px`,
  '--rw-w-agent-w': panel.agentCollapsed ? 'var(--rw-w-collapsed)' : `${panel.widths.writeAgent}px`,
  '--rw-w-editor': `${panel.widths.writeEditor}px`,
  '--rw-h-log': panel.logExpanded ? `${panel.widths.logHeight}px` : '34px',
}))

/* ───────────── 工作区 / 项目 ───────────── */

function rememberLocation(pid: string, mode: WorkspaceMode) {
  try {
    if (pid) localStorage.setItem('ma_research_last_project', pid)
    localStorage.setItem('ma_research_last_workspace', mode)
  } catch { /* 隐私模式忽略 */ }
}

function onWorkspaceChange(mode: WorkspaceMode) {
  panel.setWorkspace(mode)
  // 切换工作区**只改 agent.context.mode**，不清空 READ/WRITE 锚点（宪法 §6）。
  agent.setContext({ mode })
  if (projectId.value) {
    rememberLocation(projectId.value, mode)
    void router.push(`/research-workspace/${projectId.value}/${mode}`)
  } else {
    void router.push('/research-workspace/workspace')
  }
}

function onAgentResize(v: number) {
  panel.setWidth(workspace.value === 'read' ? 'readAgent' : 'writeAgent', v)
}

/** Agent「定位原文 / 定位到第 N 行」：改写上下文锚点并选中来源文献（宪法 §14/§24）。 */
function onAgentLocate(target: {
  paperId?: string
  page?: number
  section?: string
  file?: string
  line?: number
  selectedText?: string
  bbox?: [number, number, number, number] | null
}) {
  if (target.paperId) papers.setActive(target.paperId)
  // READ：真正跳转到该页/该章节（证据「定位原文」、最近笔记跳回共用同一定位入口，宪法 §14）。
  if (workspace.value === 'read' && target.paperId) {
    const loc: Locator = {
      paper_id: target.paperId,
      page: target.page ?? 1,
      // B8：优先用证据自带 bbox；没有就传 [0,0,0,0]（阅读器会把全零 bbox 判为无效，
      // 然后回退到按 selected_text 匹配真实文本块 → 高亮），绝不伪造坐标（宪法 §14/§33）。
      bbox: target.bbox ?? [0, 0, 0, 0],
      selected_text: target.selectedText ?? '',
      section: target.section,
    }
    void readRef.value?.locate(loc)
  }
  if (target.file && editorRef.value && workspace.value === 'write') {
    void files.open(projectId.value, target.file).then(() => {
      if (target.line) editorRef.value?.scrollToLine(target.line)
    })
  }
  agent.setContext({
    ...(target.paperId ? { paperId: target.paperId } : {}),
    ...(target.page ? { page: target.page } : {}),
    ...(target.section ? { section: target.section } : {}),
    ...(target.file ? { filePath: target.file } : {}),
    ...(target.line ? { line: target.line } : {}),
  })
  void persistRecent()
}

/** 记录最近状态（宪法 §84）；失败静默（不阻塞定位）。 */
async function persistRecent() {
  const pid = projectId.value
  if (!pid) return
  const c = agent.context
  await project.saveRecent(pid, {
    workspace: workspace.value,
    paper_id: c.paperId ?? null,
    page: c.page ?? null,
    file_path: c.filePath ?? null,
    line: c.line ?? null,
    updated_at: new Date().toISOString(),
  })
}

function onSelectProject(id: string) {
  panel.setCurrentProject(id)
  rememberLocation(id, workspace.value)
  void router.push(`/research-workspace/${id}/${workspace.value}`)
}

async function onRecompile() {
  if (projectId.value) await files.compile(projectId.value)
}

/**
 * 顶栏「历史」点击条目 → 复用统一「定位原文」链路（宪法 §11/§14/§50）。
 * READ：切到该文献并跳到该页；WRITE：先把上下文切到该文献，再回到 READ 打开它。
 */
function onLocatePaper(payload: { paperId: string; page: number | null; title: string }) {
  onAgentLocate({ paperId: payload.paperId, page: payload.page ?? 1 })
  if (workspace.value !== 'read' && projectId.value) {
    void router.push(`/research-workspace/${projectId.value}/read`)
  }
}

async function createFirstProject() {
  const id = `rw-${Date.now().toString(36)}`
  const created = await project.create({
    name: '未命名项目',
    description: '',
    client_request_id: id.padEnd(8, '0'),
  })
  if (created) {
    panel.setCurrentProject(created.id)
    rememberLocation(created.id, 'read')
    void router.push(`/research-workspace/${created.id}/read`)
  }
}

/* ───────────── WRITE：文件树 / 编辑器 / 编译 ───────────── */

async function onOpenFile(path: string) {
  if (!projectId.value) return
  await files.open(projectId.value, path)
}

async function onSelectTab(path: string) {
  await onOpenFile(path)
}

function onCloseTab(path: string) {
  files.closeDoc(path)
}

function onNewFileFromEditor() {
  sidebarRef.value?.startCreate()
}

async function onCreateFile(path: string, kind: FileKind) {
  if (!projectId.value) return
  const ok = await files.create(projectId.value, path, kind, '')
  if (ok) await files.open(projectId.value, path)
}

async function onRenameFile(from: string, to: string) {
  if (!projectId.value) return
  await files.rename(projectId.value, from, to)
  if (files.activePath === to) await files.open(projectId.value, to)
}

async function onRemoveFile(path: string) {
  if (!projectId.value) return
  await files.remove(projectId.value, path)
}

async function onRefreshTree() {
  if (!projectId.value) return
  await Promise.all([files.loadTree(projectId.value), files.loadOutline(projectId.value)])
}

/** Outline / 日志定位：跨文件先 open 再 scrollToLine（FRONTEND-ARCH §7）。 */
async function onNavigate(file: string, line: number) {
  if (!projectId.value) return
  await files.open(projectId.value, file)
  editorRef.value?.scrollToLine(line)
  agent.setContext({ mode: 'write', filePath: file, line })
}

function onDocChange(path: string, doc: string) {
  files.setContent(path, doc)
  if (projectId.value) void files.save(projectId.value, path)
}

function onCursorLine(line: number) {
  agent.setContext({ mode: 'write', filePath: files.activePath, line })
}

/**
 * 编辑器选区 → **自动装载**进 Research Context（宪法 §26/§46，与 READ 侧
 * `ReadWorkspace.onSelectionChange` 同语义）。
 *
 * 这是「在编辑器里选中公式/段落 → 直接点『公式检查 / 翻译』就能跑」的前提：
 * 后端 `agent.py::_selected_text()` 读的是 `context.selection`，此前 WRITE 侧
 * 从不装载，导致恒返回 needs_conditions（「当前没有可校验的公式/可翻译的文本」）。
 * 选区为空时**如实清空**，不残留上一次的选区。
 */
function onEditorSelection(payload: { text: string; line: number }) {
  agent.setContext({
    mode: 'write',
    filePath: files.activePath,
    ...(payload.line ? { line: payload.line } : {}),
    selection: payload.text ?? '',
  })
}

/** 点击编译日志中的错误行 → 定位 + 触发「LaTeX 错误解释」能力（宪法 §24/§39）。 */
async function onLogLocate(payload: { file: string; line: number }) {
  await onNavigate(payload.file, payload.line)
  if (!projectId.value || agent.running) return
  // 交由统一状态机执行；面板会在「LaTeX 错误解释」能力卡上展开并显示结果卡。
  void agent.runPreset(projectId.value, 'latex_error', { file: payload.file, line: payload.line })
}

function onCloseProject() {
  void router.push('/research-workspace')
}

/* ───────────── 启动 ───────────── */

async function bootstrap(pid: string) {
  if (!pid) {
    project.reset()
    files.reset()
    panel.setCurrentProject('')
    const list = await project.loadList()
    lastSyncAt.value = new Date()
    if (list.length) {
      const storedId = (() => { try { return localStorage.getItem('ma_research_last_project') || '' } catch { return '' } })()
      const picked = list.find((p) => p.id === storedId) || list[0]
      const storedWs = (() => {
        try { return localStorage.getItem('ma_research_last_workspace') === 'write' ? 'write' : 'read' } catch { return 'read' }
      })()
      panel.setCurrentProject(picked.id)
      panel.setWorkspace(storedWs)
      rememberLocation(picked.id, storedWs)
      void router.replace(`/research-workspace/${picked.id}/${storedWs}`)
    }
    return
  }
  panel.setCurrentProject(pid)
  panel.setWorkspace(workspace.value)
  rememberLocation(pid, workspace.value)
  agent.setContext({ mode: workspace.value })
  await project.loadList()
  await project.open(pid)
  await Promise.all([project.loadOverview(pid), project.loadRecent(pid), papers.load(pid), files.loadTree(pid)])
  if (workspace.value === 'write') {
    await Promise.all([files.loadOutline(pid), files.loadLatest(pid)])
    await files.resume(pid, project.recent?.file_path ?? null)
  }
  lastSyncAt.value = new Date()
}

onMounted(() => void bootstrap(projectId.value))

watch(projectId, (pid) => void bootstrap(pid))

watch(
  () => files.compileStatus,
  (s) => {
    if (s === 'success' || s === 'completed') compileFinishedAt.value = new Date()
  },
)
</script>

<!-- 非 scoped：布局与 token 选择器均以 `[data-research-app]` / 唯一 `rw-` 前缀为作用域 -->
<style>
.rw-shell {
  position: relative;
  display: flex;
  flex-direction: column;
  height: 100vh;
  min-height: 0;
  overflow: hidden;
  background: var(--rw-app-bg);
  color: var(--rw-text);
  font-family: var(--rw-font-sans);
  font-size: var(--rw-fs-body);
  line-height: var(--rw-lh-body);
}
.rw-ambient { position: absolute; inset: 0; z-index: 0; pointer-events: none; overflow: hidden; }
.rw-ambient span { position: absolute; border-radius: 50%; filter: blur(80px); opacity: .55; }
.rw-ambient__b1 { width: 420px; height: 420px; top: -120px; left: -80px; background: rgba(99, 102, 241, .12); }
.rw-ambient__b2 { width: 380px; height: 380px; top: -80px; right: -100px; background: rgba(6, 182, 212, .08); }
.rw-ambient__b3 { width: 460px; height: 460px; bottom: -160px; left: 32%; background: rgba(124, 58, 237, .07); }
.rw-shell > .rw-header,
.rw-shell > .rw-body { position: relative; z-index: 1; }
.rw-shell *,
.rw-shell *::before,
.rw-shell *::after {
  box-sizing: border-box;
}

.rw-body {
  flex: 1 1 auto;
  min-height: 0;
  overflow: hidden;
}
[data-research-app] .rw-body--read {
  display: grid;
  grid-template-columns: var(--rw-w-sidebar, 300px) minmax(0, 1fr) var(--rw-w-agent, 420px);
}
[data-research-app] .rw-body--write {
  display: grid;
  grid-template-columns: var(--rw-w-left, 306px) minmax(0, 1fr) var(--rw-w-agent-w, 314px);
}
[data-research-app] .rw-mid {
  /* 供 `.rw-mid__ccb`（编译控制条浮层）绝对定位 */
  position: relative;
  display: grid;
  /* 顶栏(62) 之下直接是 Editor/Preview（上，1fr）+ 编译日志（下，auto）。
     原「工作区条」行（48px）已随 `阅读|写作` 上移顶栏而删除（2026-09-18）。 */
  grid-template-rows: minmax(0, 1fr) auto;
  min-width: 0;
  min-height: 0;
}
/* 编译控制条浮层：贴中栏顶部、居右，与编辑器文件 Tab 条（.rw-editor__tabs，高 37）同一条水平带。
   绝对定位 → 不占网格行，故 `.rw-mid__top` 从顶栏正下方（y=62）起全高。
   pointer-events 交给子元素（浮层仅内容宽度，不影响其下预览区交互）。 */
[data-research-app] .rw-mid__ccb {
  position: absolute;
  top: 0;
  right: 0;
  z-index: 6;
  height: 37px;
  display: flex;
  align-items: center;
  padding: 0 12px;
  pointer-events: none;
}
[data-research-app] .rw-mid__ccb > * {
  pointer-events: auto;
}
[data-research-app] .rw-mid__top {
  display: grid;
  grid-template-columns: var(--rw-w-editor, 590px) minmax(0, 1fr);
  min-width: 0;
  min-height: 0;
}

.rw-pane {
  position: relative;
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
}
.rw-pane--sidebar {
  background: var(--rw-app-bg);
  border-right: var(--rw-border-w) solid var(--rw-border);
}
.rw-pane--pdf,
.rw-pane--preview {
  background: var(--rw-page-bg);
}
.rw-pane--agent {
  background: var(--rw-surface);
  border-left: var(--rw-border-w) solid var(--rw-border);
}
.rw-pane--left {
  background: var(--rw-app-bg);
  border-right: var(--rw-border-w) solid var(--rw-border);
}
.rw-pane--editor {
  background: var(--rw-surface);
  border-right: var(--rw-border-w) solid var(--rw-border);
}
.rw-pane__pad {
  padding: 12px;
  overflow: auto;
  flex: 1 1 auto;
  min-height: 0;
}
.rw-pane__pad--center {
  display: flex;
  align-items: center;
  justify-content: center;
}
.rw-pane__title {
  margin: 0 0 8px;
  padding: 2px 4px;
  font-size: var(--rw-fs-h2);
  font-weight: var(--rw-fw-semibold);
  color: var(--rw-text);
}

/* 面板拖动把手：贴在 pane 内边缘（pane 已 position:relative） */
.rw-handle {
  position: absolute;
  top: 0;
  height: 100%;
  z-index: 5;
}
.rw-handle--right { right: 0; }
.rw-handle--left { left: 0; }

/* 项目身份卡（READ，真实数据） */
.rw-identity {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 12px;
  padding: 10px;
  border-radius: var(--rw-radius-md);
  background: var(--rw-surface-2);
}
.rw-identity__thumb {
  width: 44px;
  height: 44px;
  flex: 0 0 auto;
  border-radius: var(--rw-radius);
  background: linear-gradient(135deg, var(--rw-primary), var(--rw-primary-hover));
}
.rw-identity__meta { min-width: 0; }
.rw-identity__name {
  margin: 0;
  font-size: var(--rw-fs-h2);
  font-weight: var(--rw-fw-semibold);
  color: var(--rw-text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.rw-identity__desc {
  margin: 2px 0 0;
  font-size: var(--rw-fs-sm);
  color: var(--rw-text-2);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* Agent 面板头（READ 壳） */
.rw-agent-head {
  display: flex;
  align-items: center;
  gap: 10px;
  height: 64px;
  padding: 0 14px;
  background: var(--rw-primary-softer);
  border-bottom: var(--rw-border-w) solid var(--rw-border);
  flex: 0 0 auto;
}
.rw-agent-head__logo {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  border-radius: 50%;
  background: var(--rw-primary);
  color: var(--rw-surface);
  font-size: var(--rw-fs-sm);
  font-weight: var(--rw-fw-bold);
  flex: 0 0 auto;
}
.rw-agent-head__meta { min-width: 0; }
.rw-agent-head__title {
  margin: 0;
  font-size: var(--rw-fs-h2);
  font-weight: var(--rw-fw-bold);
  color: var(--rw-text);
}
.rw-agent-head__sub {
  margin-left: 6px;
  font-size: var(--rw-fs-h2);
  font-weight: var(--rw-fw-bold);
  color: var(--rw-primary);
}
.rw-agent-head__desc {
  margin: 2px 0 0;
  font-size: var(--rw-fs-xs);
  color: var(--rw-text-3);
}

/* 编译日志栏容器（高度由 --rw-h-log 控制） */
.rw-compile-log {
  height: var(--rw-h-log, 164px);
  min-height: 0;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

/* ── WRITE 右栏 Agent（触发 + 状态展示位；完整 Result DS 由 agent/ 批次接管）── */
.rw-wagent__head {
  display: flex;
  align-items: center;
  gap: 9px;
  height: 64px;
  padding: 0 12px;
  flex: 0 0 auto;
  background: var(--rw-primary-softer);
  border-bottom: var(--rw-border-w) solid var(--rw-border);
}
.rw-wagent__logo {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  flex: 0 0 auto;
  border-radius: 50%;
  background: var(--rw-primary);
  color: var(--rw-surface);
  font-size: var(--rw-fs-xs);
  font-weight: var(--rw-fw-bold);
}
.rw-wagent__meta { min-width: 0; flex: 1 1 auto; }
.rw-wagent__title {
  margin: 0;
  font-size: var(--rw-fs-h3);
  font-weight: var(--rw-fw-bold);
  color: var(--rw-text);
  white-space: nowrap;
}
.rw-wagent__online {
  margin-left: 5px;
  font-size: var(--rw-fs-sm);
  font-weight: var(--rw-fw-regular);
  color: var(--rw-success);
}
.rw-wagent__desc {
  margin: 1px 0 0;
  font-size: var(--rw-fs-xs);
  color: var(--rw-text-3);
}
.rw-wagent__more {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  flex: 0 0 auto;
  border: var(--rw-border-w) solid var(--rw-border);
  border-radius: var(--rw-radius);
  background: var(--rw-surface);
  color: var(--rw-text-2);
  cursor: pointer;
}
.rw-wagent__body {
  flex: 1 1 auto;
  min-height: 0;
  overflow: auto;
  padding: 10px;
}
.rw-wagent__cap {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px;
  border: var(--rw-border-w) solid var(--rw-border);
  border-radius: var(--rw-radius-lg);
}
.rw-wagent__cap-ico {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  flex: 0 0 auto;
  border-radius: var(--rw-radius-md);
  background: var(--rw-danger-soft);
  color: var(--rw-danger);
  font-weight: var(--rw-fw-bold);
}
.rw-wagent__cap-title {
  flex: 1 1 auto;
  font-size: var(--rw-fs-h3);
  font-weight: var(--rw-fw-semibold);
  color: var(--rw-text);
}
.rw-wagent__cap-run {
  height: 26px;
  padding: 0 10px;
  border: none;
  border-radius: var(--rw-radius);
  background: var(--rw-primary);
  color: var(--rw-surface);
  font-size: var(--rw-fs-sm);
  font-weight: var(--rw-fw-semibold);
  cursor: pointer;
}
.rw-wagent__cap-run:disabled {
  opacity: .55;
  cursor: not-allowed;
}
.rw-wagent__activity,
.rw-wagent__error {
  margin: 8px 0 0;
  font-size: var(--rw-fs-sm);
  color: var(--rw-text-2);
}
.rw-wagent__error { color: var(--rw-danger); }
.rw-wagent__err-title {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  margin: 10px 0 6px;
  font-size: var(--rw-fs-h3);
  font-weight: var(--rw-fw-bold);
  color: var(--rw-danger);
}
.rw-wagent__err-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  flex: 0 0 auto;
  border-radius: 50%;
  background: var(--rw-danger);
  color: var(--rw-surface);
  font-size: var(--rw-fs-xs);
}
.rw-wagent__loc,
.rw-wagent__diff {
  margin-top: 8px;
  padding: 8px 10px;
  border-radius: var(--rw-radius-md);
  background: var(--rw-inset);
}
.rw-wagent__loc-title {
  margin: 0 0 4px;
  font-size: var(--rw-fs-sm);
  font-weight: var(--rw-fw-semibold);
  color: var(--rw-text-2);
}
.rw-wagent__loc-line {
  margin: 0;
  font-size: var(--rw-fs-sm);
  color: var(--rw-text);
}
.rw-wagent__loc-line b { color: var(--rw-primary); }
.rw-wagent__diff {
  background: transparent;
  padding: 0;
  border: var(--rw-border-w) solid var(--rw-border);
  border-radius: var(--rw-radius-md);
  overflow: hidden;
}
.rw-wagent__diff .rw-wagent__loc-title { padding: 8px 10px 0; }
.rw-wagent__diff-line {
  margin: 0;
  padding: 3px 10px;
  font-family: var(--rw-font-mono);
  font-size: var(--rw-fs-sm);
  white-space: pre-wrap;
  word-break: break-word;
}
.rw-wagent__diff-line--del {
  background: var(--rw-danger-soft);
  color: var(--rw-danger);
}
.rw-wagent__diff-line--add {
  background: var(--rw-success-soft);
  color: var(--rw-success);
}
.rw-wagent__finding {
  margin: 8px 0 0;
  font-size: var(--rw-fs-sm);
  line-height: var(--rw-lh-body);
  color: var(--rw-text-2);
}
.rw-wagent__actions {
  display: flex;
  gap: 6px;
  margin-top: 10px;
  flex-wrap: wrap;
}
.rw-wagent__act {
  height: 30px;
  padding: 0 12px;
  border: var(--rw-border-w) solid var(--rw-border-strong);
  border-radius: var(--rw-radius);
  background: var(--rw-surface);
  color: var(--rw-text);
  font-size: var(--rw-fs-sm);
  font-weight: var(--rw-fw-medium);
  cursor: pointer;
}
.rw-wagent__act--primary {
  border-color: var(--rw-primary);
  background: var(--rw-primary);
  color: var(--rw-surface);
  font-weight: var(--rw-fw-semibold);
}
.rw-wagent__act:disabled {
  opacity: .5;
  cursor: not-allowed;
}

.rw-hidden-input {
  display: none;
}
</style>
