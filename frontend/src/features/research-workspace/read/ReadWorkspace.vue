<template>
  <div class="rw-read">
    <!-- 左栏 330：五件套（ProjectSidebar 自带右缘 slot 供拖拽把手） -->
    <ProjectSidebar
      class="rw-read__sidebar"
      :project-id="projectId"
      @open-note="onOpenNote"
      @create-project="emit('create-project')"
    >
      <Resizer
        class="rw-read__handle rw-read__handle--right"
        side="left"
        :value="panel.widths.readSidebar"
        :min="bounds.readSidebar.min"
        :max="bounds.readSidebar.max"
        aria-label="调整左栏宽度"
        @update:value="(v) => panel.setWidth('readSidebar', v)"
      />
    </ProjectSidebar>

    <!-- 中栏 1fr：文档 Tab + PDF 工具条 + 页面
         （`阅读|写作` 分段控件已于 2026-09-18 按用户指令上移到顶栏，见 GlobalHeader.vue；
          原中栏首行 `rw-read__bar`（64px）随之**塌缩删除**，该 64px 全额还给 PDF 阅读区） -->
    <div class="rw-read__center">
      <PdfReaderPane
        ref="paneRef"
        class="rw-read__pdf"
        :project-id="projectId"
        :papers="papers.list"
        :active-paper-id="papers.activeId"
        @select-paper="onSelectPaper"
        @close-tab="onCloseTab"
        @add-paper="onAddPaper"
        @ask-ai="onAskAi"
        @explain-ai="onExplainAi"
        @evidence-ai="onEvidenceAi"
        @selection-change="onSelectionChange"
      />
    </div>

    <input ref="fileInput" type="file" accept="application/pdf,.pdf" class="rw-read__file" @change="onFileChosen" />

    <!-- 导入反馈 toast：成功/失败都必须有回执（宪法 §70 禁 silently no-op） -->
    <Transition name="rw-toast-fade">
      <p v-if="importToast" class="rw-read__toast" role="status" aria-live="polite">{{ importToast }}</p>
    </Transition>
  </div>
</template>

<script setup lang="ts">
/**
 * READ 工作区（T03）——**左栏 330 + 中栏 1fr** 两块；右栏 604 的 Research Agent
 * 由 Shell 的**共享单实例** `<ResearchAgentPanel>` 承载（切 READ/WRITE 不 remount，宪法 §54）。
 *
 * 根元素 `display: contents`：使本组件的两个子节点直接成为 Shell `rw-body--read`
 * 三列 grid 的第 1/2 项（第 3 项恒为共享 Agent），从而 Shell 接线只需 1 行 `<ReadWorkspace/>`，
 * 即便 shell 被其它批次重写，重建成本也最小。
 *
 * 布局（黄金图 READ-GOLDEN 实测）：Header 之下即三栏 `330 / 737 / 604`。
 * ~~「阅读|写作」分段控件位于中栏首行~~ → 2026-09-18 用户要求上移到**顶栏**（与大标题平齐，
 * 见 `shell/GlobalHeader.vue`）；原中栏首行（64px）已删除，空间全额还给 PDF 阅读区。
 *
 * 真实能力：左栏五件套走 T02 store；中栏 PDF 渲染/翻页/缩放/搜索/选文→locator 由
 * `PdfReaderPane` 承载；「问 AI」把当前 locator 灌进 Agent 上下文并直接开跑。
 */
import { ref } from 'vue'
import Resizer from '../shell/Resizer.vue'
import ProjectSidebar from './ProjectSidebar.vue'
import PdfReaderPane from './PdfReaderPane.vue'
import { useImportPaper } from './useImportPaper'
import { useRwPanelStore, WIDTH_BOUNDS } from '../stores/panel'
import { useRwProjectStore } from '../stores/project'
import { useRwPapersStore } from '../stores/papers'
import { useRwAgentStore } from '../stores/agent'
import type { Locator, Note } from '../api/types'

const props = defineProps<{ projectId: string }>()
const emit = defineEmits<{
  (e: 'create-project'): void
}>()

const panel = useRwPanelStore()
const project = useRwProjectStore()
const papers = useRwPapersStore()
const agent = useRwAgentStore()

const bounds = WIDTH_BOUNDS
const paneRef = ref<{ locate: (loc: Locator | null) => Promise<void> } | null>(null)
const fileInput = ref<HTMLInputElement | null>(null)
/** 导入回执（成功/失败都提示；与左栏文献卡入口共享同一组合式函数） */
const import_ = useImportPaper()
const importToast = import_.toast

function onSelectPaper(id: string) {
  papers.setActive(id)
}

function onCloseTab(id: string) {
  // READ 的「×」= 从 Tab 条关闭（不删除项目文献）；关的是当前文献时自动切到剩余 Tab。
  papers.closeTab(id)
}

/** 最近笔记点击 → 阅读器定位原文（宪法 §11/§44）。 */
function onOpenNote(note: Note) {
  if (!note.paper_id) return
  void paneRef.value?.locate({
    paper_id: note.paper_id,
    page: note.page ?? 1,
    bbox: [0, 0, 0, 0],
    selected_text: note.selected_text ?? '',
    section: note.section ?? undefined,
  })
}

/**
 * 选区变化 → **自动装载**进 Research Context（宪法 §26/§46）。
 *
 * 这是「选中即校验」能成立的前提：用户在 PDF 里选中一段数学陈述后，
 * **直接点 Agent 面板的『推导校验』**就能拿到真实结论 —— 不需要先点浮动工具条的「问 AI」。
 * 上下文里没有选区时（`null`）清掉 selection，避免把上一篇文献的选区当成当前选区。
 */
function onSelectionChange(loc: Locator | null) {
  if (!loc || !loc.selected_text) {
    agent.setContext({ selection: '' })
    return
  }
  agent.setContext({
    mode: 'read',
    paperId: loc.paper_id,
    page: loc.page,
    section: loc.section ?? null,
    selection: loc.selected_text,
  })
}

/** 选区「问 AI」→ Agent 自动装载 paper/page/section/selected_text 并直接开跑（无需用户复制原文）。 */
function onAskAi(loc: Locator) {  agent.setContext({
    mode: 'read',
    paperId: loc.paper_id,
    page: loc.page,
    section: loc.section ?? null,
    selection: loc.selected_text,
  })
  // 后端上下文契约使用下划线字段：把 locator 原样随请求带上（含 `bbox`），
  // 用户无需复制原文（宪法 §13 / ACCEPTANCE B7）。`bbox` 不在 `AgentContext` 的
  // 强类型内，故经 `runPreset` 的 extraContext 透传，不新增/修改 api 层类型。
  void agent.runPreset(props.projectId, 'derivation_check', {
    paper_id: loc.paper_id,
    page: loc.page,
    section: loc.section ?? '',
    selected_text: loc.selected_text,
    bbox: loc.bbox,
  })
}

/** 选区「解释」→ 进入真实对话线程；引文进入消息，不静默发送整篇文献。 */
function onExplainAi(loc: Locator) {
  agent.setContext({
    mode: 'read',
    paperId: loc.paper_id,
    page: loc.page,
    section: loc.section ?? null,
    selection: loc.selected_text,
  })
  const source = [loc.section ? `§${loc.section}` : '', `第 ${loc.page} 页`].filter(Boolean).join(' · ')
  void agent.ask(
    props.projectId,
    `请解释这段内容（${source}）：\n\n> ${loc.selected_text.replace(/\n/g, '\n> ')}\n\n请先说明它在论证中的作用，再解释关键概念。`,
  )
}

/** 选区「找证据」→ citation 能力，结果进入对话线程并带原文锚点。 */
function onEvidenceAi(loc: Locator) {
  agent.setContext({
    mode: 'read',
    paperId: loc.paper_id,
    page: loc.page,
    section: loc.section ?? null,
    selection: loc.selected_text,
  })
  void agent.runPreset(props.projectId, 'citation', {
    paper_id: loc.paper_id,
    page: loc.page,
    section: loc.section ?? '',
    selected_text: loc.selected_text,
    query: loc.selected_text,
    bbox: loc.bbox,
  })
}

function onAddPaper() {
  fileInput.value?.click()
}

async function onFileChosen(e: Event) {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  if (file && props.projectId) {
    await import_.run(props.projectId, file)
  }
  input.value = ''
}

/** 对外暴露 `locate`：供 Shell（Agent「定位原文」/最近笔记）跨组件定位到具体页面与位置。 */
defineExpose({ locate: (loc: Locator | null) => paneRef.value?.locate(loc) })
</script>

<style scoped>
/*
 * `display: contents`：本容器不产生盒子，其两个子节点（左栏 / 中栏）直接参与
 * Shell `rw-body--read` 的三列 grid —— 从而共享右栏 Agent 仍是同一 grid 的第 3 项。
 */
.rw-read {
  display: contents;
}
.rw-read__sidebar {
  position: relative;
  min-width: 0;
  min-height: 0;
  border-right: var(--rw-border-w) solid var(--rw-border);
}
.rw-read__handle {
  position: absolute;
  top: 0;
  height: 100%;  z-index: 5;
}
.rw-read__handle--right { right: 0; }
.rw-read__center {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  background: var(--rw-page-bg);
}
/*
 * 中栏首行（原 `阅读|写作` 分段控件的 64px 行）已于 2026-09-18 按用户指令**删除**：
 * 分段控件上移到顶栏（GlobalHeader.vue），该行整块塌缩，64px 全额还给 PDF 阅读区
 * （`.rw-read__pdf` 由 815 → 879）。文档 Tab（DocTabs）本就在 `PdfReaderPane` 内，
 * 不受影响、无需收行高。此为对黄金图的**有意偏离**（黄金图该行存在）。
 */
.rw-read__pdf {
  flex: 1 1 auto;
  min-height: 0;
}
.rw-read__file {
  display: none;
}

/* 导入回执 toast（中栏底部悬浮；成功绿边 / 失败红边由内容决定，统一中性壳） */
.rw-read__toast {
  /* 根元素 display:contents 不产生盒子 → 用 fixed 相对视口悬浮 */
  position: fixed;
  left: 50%;
  bottom: 24px;
  transform: translateX(-50%);
  z-index: 30;
  max-width: 72%;
  padding: 10px 18px;
  border-radius: var(--rw-radius-sm, 8px);
  background: var(--rw-surface, #fff);
  border: var(--rw-border-w) solid var(--rw-border);
  box-shadow: 0 8px 24px rgb(0 0 0 / 0.12);
  font-size: var(--rw-fs-body, 14px);
  color: var(--rw-text-1, #1f2329);
}
.rw-toast-fade-enter-active,
.rw-toast-fade-leave-active {
  transition: opacity 0.25s ease;
}
.rw-toast-fade-enter-from,
.rw-toast-fade-leave-to {
  opacity: 0;
}
</style>
