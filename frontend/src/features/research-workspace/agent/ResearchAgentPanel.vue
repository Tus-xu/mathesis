<template>
  <div class="rw-agent" data-rw-agent>
    <AgentPanelHeader :mode="mode" @menu-action="onMenuAction" @new-chat="onNewChat" />

    <div v-if="projectId" class="rw-agent__context">
      <SelectionContextCard
        v-if="hasSelection"
        :mode="mode"
        :selection="context.selection || ''"
        :paper-title="activePaper?.title || ''"
        :page="context.page"
        :section="context.section"
        :file-path="context.filePath"
        :line="context.line"
        @judge="onSelectionJudge"
        @explain="onSelectionExplain"
        @translate="onSelectionTranslate"
        @note="onSelectionNote"
        @locate="locateSelection"
        @clear="clearSelection"
      />
      <ContextChips
        :mode="mode"
        :context="context"
        :paper="activePaper"
        :bib-file="bibFile"
        :paper-count="paperCount"
        :draft-file="draftFile"
        :draft-line="draftLine"
        @locate="onChipLocate"
      />
    </div>

    <div class="rw-agent__scroll">
      <div v-if="!projectId" class="rw-agent__pad">
        <EmptyState title="还没有科研项目" description="创建项目后，Agent 会基于你的文献、草稿与笔记工作。" />
      </div>

      <template v-else>
        <ChatThread
          v-if="agent.thread.length || running || error"
          :items="agent.thread"
          :mode="mode"
          :running="running"
          :activity="activity"
          :preset="preset"
          :error="error"
          :line-resolver="resolveLine"
          :busy="applying"
          :feedback="actionFeedback"
          @action="onCardAction"
          @source="onSource"
          @retry="onRetry"
        />

        <!-- 空态：先说明角色与当前上下文，再给少量基于上下文的起点；不做能力工具墙。 -->
        <div v-else class="rw-agent__empty">
          <div class="rw-agent__orb" aria-hidden="true">π</div>
          <h3>今天想推进什么？</h3>
          <p>选中论文中的一句话或公式，我会在这里帮你解释、评判、找证据。</p>

          <!-- 有选区时不再铺四张工具卡：直接围绕这段内容给三个动作 -->
          <div v-if="hasSelection" class="rw-agent__ready">
            <button type="button" class="rw-agent__ready-main" @click="onSelectionJudge">
              <CheckmarkCircle />
              评判这段
            </button>
            <button type="button" @click="onSelectionExplain">解释</button>
            <button type="button" @click="onRun('citation')">找证据</button>
          </div>
          <template v-else>
            <div class="rw-agent__prompts">
              <button
                v-for="p in promptCards" :key="p.preset"
                type="button" class="rw-agent__prompt"
                @click="onRun(p.preset)"
              >
                <span class="rw-agent__prompt-ico" :class="`tone-${p.tone}`">
                  <component :is="p.icon" />
                </span>
                <span class="rw-agent__prompt-copy">
                  <b>{{ p.title }}</b>
                  <small>{{ p.desc }}</small>
                </span>
                <span class="rw-agent__prompt-arrow" aria-hidden="true">→</span>
              </button>
            </div>
            <div v-if="extraCaps.length" class="rw-agent__more">
              <span>更多能力</span>
              <button v-for="c in extraCaps" :key="c.preset" type="button" @click="onRun(c.preset)">
                {{ c.label }}
              </button>
            </div>
          </template>
        </div>
      </template>
    </div>

    <p v-if="actionFeedback" class="rw-agent__feedback" role="status" aria-live="polite">{{ actionFeedback }}</p>

    <div v-if="projectId && agent.thread.length" class="rw-agent__dock">
      <span class="rw-agent__dock-label">快捷能力</span>
      <div class="rw-agent__dock-chips">
        <button v-for="c in caps" :key="c.preset" type="button" :title="c.activity" @click="onRun(c.preset)">
          <component :is="c.icon" />
          {{ c.label }}
        </button>
      </div>
    </div>

    <Composer
      :mode="mode"
      :selection="context.selection || ''"
      :paper-title="activePaper ? activePaper.title : ''"
      :disabled="!projectId"
      :busy="agent.chatPending"
      @send="onSend"
    />
  </div>
</template>

<script setup lang="ts">
/**
 * Research Agent 面板（2026-10-21 对话优先重构）。
 *
 * 结构：面板头 → 选中上下文/来源 chips → 对话线程（含结构化结果）→ 快捷能力 → 输入框。
 * - Context first：选中文本以引文卡出现，可评判/解释/翻译/记笔记/回原文；
 * - Conversation first：自由对话与能力结果进入同一条时间线，不再用五张 Accordion 占首屏；
 * - Result DS：结构化结果只保留一层容器，证据、定位、写笔记仍走真实 ResultCard 动作；
 * - 空态：一句角色说明 + 4 个起点 + 折叠的更多能力。
 */
import { computed, onMounted, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { CheckmarkCircle } from '@vicons/ionicons5'
import AgentPanelHeader from './AgentPanelHeader.vue'
import SelectionContextCard from './SelectionContextCard.vue'
import ContextChips from './ContextChips.vue'
import ChatThread from './ChatThread.vue'
import Composer from './Composer.vue'
import EmptyState from '../ui/EmptyState.vue'
import { presetMeta, presetsFor, resetAgentTask, type PresetView } from './useAgentTask'
import { useRwAgentStore, type AgentThreadItem } from '../stores/agent'
import { useRwPapersStore } from '../stores/papers'
import { useRwFilesStore } from '../stores/files'
import { useRwNotesStore } from '../stores/notes'
import { useRwProjectStore } from '../stores/project'
import { getFile } from '../api/files'
import type { AgentPreset, EvidenceSource as EvidenceSourceType, FileNode } from '../api/types'

const props = defineProps<{ projectId: string }>()

export interface LocateTarget {
  paperId?: string
  page?: number
  section?: string
  file?: string
  line?: number
  selectedText?: string
  bbox?: [number, number, number, number] | null
}

const emit = defineEmits<{
  (e: 'locate', target: LocateTarget): void
  (e: 'recompile'): void
}>()

const agent = useRwAgentStore()
const papers = useRwPapersStore()
const files = useRwFilesStore()
const notes = useRwNotesStore()
const project = useRwProjectStore()
const { context, preset, result, activity, running, error } = storeToRefs(agent)

const applying = ref(false)
const actionFeedback = ref('')
let feedbackTimer: ReturnType<typeof setTimeout> | null = null
const fileCache = ref<Record<string, string>>({})

function showFeedback(msg: string) {
  actionFeedback.value = msg
  if (feedbackTimer) clearTimeout(feedbackTimer)
  feedbackTimer = setTimeout(() => (actionFeedback.value = ''), 3000)
}

const mode = computed(() => context.value.mode)
const hasSelection = computed(() => Boolean((context.value.selection || '').trim()))
const caps = computed<PresetView[]>(() => presetsFor(mode.value).map((p) => presetMeta(p, mode.value)))

const PROMPT_COPY: Record<string, { title: string; desc: string }> = {
  derivation_check: { title: '评判选中内容', desc: '先选中一段推导或结论，检查是否成立' },
  citation: { title: '找相关证据', desc: '在项目文献中寻找支持或反对的证据' },
  formula_check: { title: '检查公式', desc: '检查当前公式的符号、条件与推导' },
  translate: { title: '翻译与改写', desc: '翻译当前选中内容，保持学术表达' },
  latex_error: { title: '解释编译错误', desc: '读取编译日志，定位并解释错误' },
  suggestion: { title: '改进建议', desc: '分析草稿结构与论证，给出可执行建议' },
  polish: { title: '语言润色', desc: '润色当前段落，改动先出 diff' },
}
const primaryPresets = computed(() => presetsFor(mode.value).slice(0, 4))
const promptCards = computed(() =>
  primaryPresets.value.map((p) => {
    const meta = presetMeta(p, mode.value)
    const copy = PROMPT_COPY[p] || { title: meta.label, desc: meta.activity }
    return { ...meta, ...copy }
  }),
)
const extraCaps = computed(() => caps.value.slice(4))

const activePaper = computed(() => {
  const p = papers.activePaper
  return p ? { id: p.id, title: p.title, authors: p.authors ?? [], year: p.year } : null
})
const paperCount = computed(() => papers.list.length)

function flatten(nodes: FileNode[]): FileNode[] {
  const out: FileNode[] = []
  const walk = (list: FileNode[]) => {
    for (const n of list) {
      out.push(n)
      if (n.children?.length) walk(n.children)
    }
  }
  walk(nodes)
  return out
}
const bibFile = computed(() => flatten(files.tree).find((n) => n.kind === 'bib')?.path ?? null)
const entryTex = computed(() => {
  const all = flatten(files.tree).filter((n) => n.type === 'file' && n.kind === 'tex')
  return all.find((n) => n.path === 'main.tex')?.path ?? all[0]?.path ?? null
})
const draftFile = computed(() => project.recent?.file_path || files.activePath || entryTex.value || null)
const draftLine = computed(() => project.recent?.line ?? null)

function resolveLine(file: string, line: number): string {
  const content = fileCache.value[file] ?? files.contents[file] ?? ''
  if (!content) return ''
  return content.split('\n')[line - 1] ?? ''
}
async function ensureFileContent(paths: string[]): Promise<void> {
  if (!props.projectId) return
  for (const path of paths) {
    if (!path || fileCache.value[path] || files.contents[path]) continue
    try {
      const fc = await getFile(props.projectId, path)
      fileCache.value = { ...fileCache.value, [path]: fc.content }
    } catch { /* DiffBlock 退化为 reason */ }
  }
}
watch([result, preset], ([r]) => {
  if (r?.patches?.length) void ensureFileContent(r.patches.map((p) => p.file))
})

onMounted(() => void agent.restoreLatest(props.projectId))
watch(
  () => props.projectId,
  (id) => { if (id) void agent.restoreLatest(id) },
)

/* ── 能力执行 ── */
async function onRun(target: string): Promise<void> {
  if (running.value) {
    showFeedback('当前分析还在进行，请稍候。')
    return
  }
  const p = target as AgentPreset
  const extra: Record<string, unknown> = {}
  const sel = (context.value.selection || '').trim()
  if (p === 'derivation_check' && !sel) {
    showFeedback('先在论文或草稿中选中一段内容，再点「评判」。')
    return
  }
  if (sel) extra.query = sel
  if (context.value.paperId) extra.paper_id = context.value.paperId
  if (context.value.page) extra.page = context.value.page
  if (context.value.section) extra.section = context.value.section
  if (context.value.filePath) extra.file = context.value.filePath
  if (context.value.line) extra.line = context.value.line
  if (p === 'translate' || p === 'polish') extra.text = sel
  // statement 是被核验对象的名称/标识，内容走 selection/query（后端 _selected_text 优先取内容）。
  if ((p === 'derivation_check' || p === 'formula_check') && sel) extra.statement = sel
  if (p === 'citation' && !extra.query) extra.query = activePaper.value?.title ?? ''
  await agent.runPreset(props.projectId, p, extra)
}

function onRetry() {
  if (preset.value) void onRun(preset.value)
}

function onNewChat() {
  if (running.value) {
    showFeedback('当前分析还在进行，完成后再开始新对话。')
    return
  }
  agent.clearThread()
  resetAgentTask()
  agent.setExpanded(null)
  showFeedback('已开始新对话，当前项目与上下文保留。')
}

/* ── 选中文本动作 ── */
function selectionSource(): string {
  if (mode.value === 'write') {
    return context.value.filePath ? `${context.value.filePath}${context.value.line ? ` 第 ${context.value.line} 行` : ''}` : '当前草稿'
  }
  const bits = []
  if (activePaper.value?.title) bits.push(`《${activePaper.value.title}》`)
  if (context.value.page) bits.push(`第 ${context.value.page} 页`)
  if (context.value.section) bits.push(`§${context.value.section}`)
  return bits.join(' · ') || '当前文献'
}

function onSelectionJudge() {
  // 评判针对“选中的文本”，READ/WRITE 都走 derivation_check；不把项目级建议混进来。
  void onRun('derivation_check')
}

function onSelectionExplain() {
  const sel = (context.value.selection || '').trim()
  if (!sel) return
  const message = `请解释这段内容（${selectionSource()}）：\n\n> ${sel.replace(/\n/g, '\n> ')}\n\n请先说明它在论证中的作用，再解释关键概念。`
  void agent.ask(props.projectId, message)
}

function onSelectionTranslate() {
  void onRun('translate')
}

async function onSelectionNote() {
  const sel = (context.value.selection || '').trim()
  if (!sel) return
  const created = await notes.create(props.projectId, {
    title: '选中文本笔记',
    body: sel,
    paper_id: context.value.paperId ?? null,
    page: context.value.page ?? null,
    section: context.value.section ?? null,
    selected_text: sel,
    xref_kind: 'manual',
  })
  showFeedback(created ? '已加入笔记，可在左侧「笔记」中查看。' : notes.error?.message || '笔记保存失败，请重试。')
}

function locateSelection() {
  if (mode.value === 'write') {
    emit('locate', { file: context.value.filePath ?? undefined, line: context.value.line ?? undefined })
    return
  }
  if (!context.value.paperId && !context.value.selection) {
    showFeedback('当前还没有选中的文献内容。')
    return
  }
  emit('locate', {
    paperId: context.value.paperId ?? undefined,
    page: context.value.page ?? undefined,
    section: context.value.section ?? undefined,
    selectedText: context.value.selection || '',
  })
}

function clearSelection() {
  agent.setContext({ selection: '' })
  showFeedback('已清除选中上下文。')
}

function onSend(text: string) {
  void agent.ask(props.projectId, text)
}

function onChipLocate(target: { kind?: string; label: string; path?: string }): void {
  if (target.kind === 'paper' && activePaper.value) {
    emit('locate', { paperId: activePaper.value.id })
  } else if (target.kind === 'file') {
    emit('locate', {
      file: target.path || context.value.filePath || undefined,
      line: target.path ? undefined : (context.value.line ?? undefined),
    })
  } else if (target.kind === 'selection') {
    locateSelection()
  }
}

function onMenuAction(key: 'clear-chat' | 'clear-result'): void {
  if (key === 'clear-chat') {
    onNewChat()
  } else if (key === 'clear-result') {
    agent.clearResults()
    resetAgentTask()
    agent.setExpanded(null)
    showFeedback('已清空结果记录。')
  }
}

function onSource(ev: EvidenceSourceType): void {
  const loc = ev.locator ?? null
  emit('locate', {
    paperId: ev.paper_id,
    page: ev.page || loc?.page || undefined,
    section: ev.section || loc?.section || undefined,
    selectedText: loc?.selected_text || '',
    bbox: loc?.bbox ?? null,
  })
}

function citeKeyOf(ev: EvidenceSourceType | undefined): string | null {
  if (!ev) return null
  const first = String(ev.authors ?? '').split(/[,;]/)[0]?.trim() ?? ''
  const last = first ? (first.split(/\s+/).pop() ?? '') : ''
  const base = (last.replace(/[^A-Za-z]/g, '') || '').toLowerCase() || 'ref'
  const year = ev.year ? String(ev.year) : ''
  const key = `${base}${year}`
  return key.length > 3 || base !== 'ref' ? key : null
}

async function onCardAction(key: string, item?: AgentThreadItem): Promise<void> {
  const r = item?.result || result.value
  if (!r) return
  if (key === 'locate') {
    if (mode.value === 'write' && r.source_locations?.length) {
      emit('locate', { file: r.source_locations[0].file, line: r.source_locations[0].line })
    } else if (r.evidence?.length) {
      onSource(r.evidence[0])
    }
    return
  }
  if (key === 'view_evidence') {
    if (r.evidence?.length) onSource(r.evidence[0])
    return
  }
  if (key === 'add_note') {
    const created = await notes.create(props.projectId, {
      title: presetMeta(r.kind as AgentPreset, mode.value).label,
      body: r.summary || (r.findings ?? []).map((f) => f.detail || f.label).join('\n'),
      paper_id: context.value.paperId ?? null,
      page: context.value.page ?? null,
      section: context.value.section ?? null,
      selected_text: context.value.selection ?? null,
      xref_kind: 'agent',
    })
    showFeedback(created ? '已加入笔记，可在左侧「笔记」中查看。' : notes.error?.message || '笔记保存失败，请重试。')
    return
  }
  if (key === 'insert_citation') {
    const key0 = citeKeyOf(r.evidence?.[0])
    if (!key0) {
      showFeedback('该结果没有可插入的引用来源。')
      return
    }
    const filePath = files.activePath || context.value.filePath || 'main.tex'
    const line = context.value.line ?? 1
    const ok = await agent.insertCitation(props.projectId, filePath, line, key0)
    showFeedback(ok ? `已把 \\cite{${key0}} 插入 ${filePath}。` : agent.error?.message || '插入引用失败，请重试。')
    return
  }
  if (key === 'apply_patch') {
    applying.value = true
    try {
      for (const patch of r.patches ?? []) await agent.applyPatch(props.projectId, patch)
      emit('recompile')
    } finally {
      applying.value = false
    }
    return
  }
  if (key === 'recompile') emit('recompile')
}
</script>

<style scoped>
.rw-agent {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  background: var(--rw-surface);
  container: rw-agent / inline-size;
}
.rw-agent__context { flex: 0 0 auto; }
.rw-agent__scroll {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
}
.rw-agent__pad { padding: 12px; }
.rw-agent__empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  padding: 30px 18px 18px;
}
.rw-agent__orb {
  width: 52px; height: 52px; display: grid; place-items: center; border-radius: 50%;
  background: var(--gradient-brand); color: #fff;
  font: italic 900 26px Georgia, "Times New Roman", serif;
  box-shadow: 0 12px 28px -14px rgba(79, 70, 229, .65);
}
.rw-agent__empty h3 { margin: 13px 0 5px; font-size: 16px; font-weight: 900; color: var(--rw-text); }
.rw-agent__empty > p { margin: 0 0 16px; max-width: 300px; font-size: 11.5px; line-height: 1.65; color: var(--rw-text-3); }
.rw-agent__ready { width: 100%; display: flex; flex-wrap: wrap; gap: 6px; justify-content: center; }
.rw-agent__ready button {
  display: inline-flex; align-items: center; gap: 4px; height: 30px; padding: 0 11px;
  border: 1px solid var(--rw-border); border-radius: 999px; background: var(--rw-surface);
  color: var(--rw-text-2); font: inherit; font-size: 10.5px; font-weight: 700; cursor: pointer;
}
.rw-agent__ready button:hover { border-color: var(--rw-primary); color: var(--rw-primary); }
.rw-agent__ready button :deep(svg) { width: 13px; height: 13px; }
.rw-agent__ready-main { border-color: transparent !important; background: var(--gradient-brand) !important; color: #fff !important; box-shadow: 0 4px 12px -8px rgba(79,70,229,.6); }
.rw-agent__prompts { width: 100%; display: flex; flex-direction: column; gap: 6px; text-align: left; }
.rw-agent__prompt {
  display: flex; align-items: center; gap: 9px; width: 100%; padding: 9px 10px;
  border: 1px solid var(--rw-border); border-radius: 12px; background: var(--rw-surface);
  font: inherit; text-align: left; cursor: pointer; transition: all .16s ease;
}
.rw-agent__prompt:hover { border-color: var(--rw-primary); background: var(--rw-primary-soft); transform: translateY(-1px); }
.rw-agent__prompt-ico { width: 30px; height: 30px; flex: none; display: grid; place-items: center; border-radius: 9px; background: var(--rw-primary-soft); color: var(--rw-primary); }
.rw-agent__prompt-ico.tone-success { background: var(--rw-success-soft); color: var(--rw-success); }
.rw-agent__prompt-ico.tone-warning { background: var(--rw-warning-soft); color: var(--rw-warning); }
.rw-agent__prompt-ico.tone-danger { background: var(--rw-danger-soft); color: var(--rw-danger); }
.rw-agent__prompt-ico.tone-purple { background: #f5f3ff; color: #7c3aed; }
.rw-agent__prompt-ico :deep(svg) { width: 16px; height: 16px; }
.rw-agent__prompt-copy { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 1px; }
.rw-agent__prompt-copy b { font-size: 11.5px; font-weight: 800; color: var(--rw-text); }
.rw-agent__prompt-copy small { font-size: 10px; line-height: 1.45; color: var(--rw-text-3); }
.rw-agent__prompt-arrow { flex: none; color: var(--rw-text-3); font-size: 14px; }
.rw-agent__prompt:hover .rw-agent__prompt-arrow { color: var(--rw-primary); }
.rw-agent__more { width: 100%; margin-top: 14px; display: flex; align-items: center; flex-wrap: wrap; gap: 5px; }
.rw-agent__more > span { font-size: 9.5px; color: var(--rw-text-3); margin-right: 2px; }
.rw-agent__more button { height: 24px; padding: 0 8px; border: 1px solid var(--rw-border); border-radius: 999px; background: var(--rw-surface); color: var(--rw-text-2); font: inherit; font-size: 9.5px; cursor: pointer; }
.rw-agent__more button:hover { border-color: var(--rw-primary); color: var(--rw-primary); }

.rw-agent__feedback {
  margin: 0 14px 6px; padding: 7px 10px; border-radius: 9px;
  background: var(--rw-primary-soft); color: var(--rw-primary);
  font-size: 10.5px; line-height: 1.5;
}
.rw-agent__dock {
  flex: 0 0 auto;
  padding: 8px 12px 6px;
  border-top: 1px dashed var(--rw-border);
  background: rgba(248, 250, 252, .76);
}
.rw-agent__dock-label { display: block; margin-bottom: 5px; font-size: 9px; font-weight: 800; color: var(--rw-text-3); letter-spacing: .04em; }
.rw-agent__dock-chips { display: flex; flex-wrap: wrap; gap: 4px; }
.rw-agent__dock-chips button {
  display: inline-flex; align-items: center; gap: 3px; height: 25px; padding: 0 8px;
  border: 1px solid var(--rw-border); border-radius: 999px; background: var(--rw-surface);
  color: var(--rw-text-2); font: inherit; font-size: 9.5px; font-weight: 600; cursor: pointer;
}
.rw-agent__dock-chips button:hover { border-color: var(--rw-primary); color: var(--rw-primary); background: var(--rw-primary-soft); }
.rw-agent__dock-chips button :deep(svg) { width: 12px; height: 12px; }

@container rw-agent (max-width: 400px) {
  .rw-agent__empty { padding: 22px 10px 14px; }
  .rw-agent__orb { width: 44px; height: 44px; font-size: 22px; }
  .rw-agent__empty h3 { font-size: 14px; }
  .rw-agent__feedback { margin: 0 10px 5px; }
  .rw-agent__dock { padding: 7px 8px 5px; }
}
</style>
