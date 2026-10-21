<template>
  <section class="rw-ctx">
    <div class="rw-ctx__head">
      <LockClosedOutline class="rw-ctx__lock" aria-hidden="true" />
      <span class="rw-ctx__title">当前上下文</span>
      <span class="rw-ctx__hint">（基于选定文献篇）</span>
    </div>
    <div class="rw-ctx__chips">
      <!-- 可点击 chip（paper/file/selection）= 定位动作；knowledge 类 chip 是**上下文展示**，
           不承载动作 → 渲染为静态标签而非按钮（§26/§70：移除假可点击暗示） -->
      <!-- 可点击 chip（paper/file/selection）= 定位动作；knowledge 类 chip 是**上下文展示**，
           不承载动作 → 渲染为静态标签而非按钮（§26/§70：移除假可点击暗示） -->
      <component
        :is="isInteractive(chip) ? 'button' : 'span'"
        v-for="chip in chips"
        :key="chip.key"
        :type="isInteractive(chip) ? 'button' : undefined"
        class="rw-ctx__chip"
        :class="{ 'rw-ctx__chip--static': !isInteractive(chip) }"
        :title="chip.title || chip.label"
        @click="isInteractive(chip) ? onChip(chip) : undefined"
      >
        <component :is="chip.icon" v-if="chip.icon" class="rw-ctx__icon" aria-hidden="true" />
        <span class="rw-ctx__label">{{ chip.label }}</span>
      </component>
    </div>
  </section>
</template>

<script setup lang="ts">
/**
 * 当前上下文 chips（VISUAL-SPEC §4.4-2 / 宪法 §26）。
 *
 * 这些 chip **不是用户手选的 Filter，而是系统从 Research Context 自动装载**：
 * 全部来自 `agent.context`（paperId/page/section/selection/filePath/line）与当前
 * 文献/文件树的真实数据。前端不提供任何「选择论文 / 选择章节」的输入控件（宪法 §26/§27）。
 *
 * READ 期望 chips：`＋ 当前选定 §<章节>`｜`<作者 年份>`（蓝色文献图标）｜`main.tex L<行>`｜`项目知识库`。
 * 其中「章节号 / 草稿文件行」严格取自真实 context；当 context 尚未装载（刚进入、未选文）时，
 * READ 的草稿文件退回**后端最近状态**（`GET /recent`，真实数据）而非臆造（宪法 §70）。
 */
import { computed, type Component } from 'vue'
import {
  AddOutline,
  CodeSlashOutline,
  DocumentTextOutline,
  LibraryOutline,
  LayersOutline,
  LockClosedOutline,
  ReaderOutline,
} from '@vicons/ionicons5'
import type { AgentContext, WorkspaceMode } from '../api/types'

interface PaperBrief {
  id: string
  title: string
  authors: string[]
  year: number | null
}

interface Chip {
  key: string
  label: string
  title?: string
  icon?: Component
  /** 是否可点击定位（paper/file/selection 可点；knowledge 为静态展示） */
  target?: 'paper' | 'file' | 'selection' | 'knowledge'
  /** file 类 chip 的明确目标文件（如 refs.bib），未提供时回落 context.filePath */
  path?: string
}

const props = defineProps<{
  mode: WorkspaceMode
  context: AgentContext
  /** 当前打开文献（来自 papers store，非用户输入） */
  paper?: PaperBrief | null
  /** 项目内 BibTeX 文件名（来自文件树真实数据；不存在则不显示） */
  bibFile?: string | null
  /** 项目文献总数（用于「项目知识库」提示，真实计数） */
  paperCount?: number
  /** 项目草稿文件（真实数据：后端最近状态 / 当前打开文件 / 项目入口 .tex），context 未装载时的 READ 兜底 */
  draftFile?: string | null
  /** 项目草稿文件的行号（真实数据） */
  draftLine?: number | null
}>()

const emit = defineEmits<{
  (e: 'locate', target: { kind: Chip['target']; label: string; path?: string }): void
}>()

function baseName(path: string): string {
  const seg = path.split('/').pop() ?? path
  return seg || path
}

function shortAuthors(authors: string[]): string {
  const first = (authors ?? [])[0] ?? ''
  if (!first) return ''
  return first.split(/[,;]/)[0].trim()
}

const chips = computed<Chip[]>(() => {
  const ctx = props.context ?? { mode: props.mode }
  const out: Chip[] = []

  // 草稿文件 + 行：优先真实 context，其次项目草稿文件（均为真实数据，绝不臆造）。
  const filePath = ctx.filePath || props.draftFile || ''
  const lineNo = ctx.line ?? props.draftLine ?? null
  const fileLabel = filePath
    ? `${baseName(filePath)}${lineNo ? ` L${lineNo}` : ''}`
    : ''

  if (props.mode === 'read') {
    // 1. 当前选定（选区 / 章节）—— 有具体选区时由 SelectionContextCard 承载引文，
    //    这里只保留“当前选定”定位 chip，避免同一上下文出现两次。
    if (!ctx.selection) {
      const selLabel = ctx.section ? `当前选定 §${ctx.section}` : '当前选定'
      out.push({ key: 'selection', label: selLabel, icon: AddOutline, target: 'selection' })
    }

    // 2. 当前文献（真实数据）
    if (props.paper) {
      const au = shortAuthors(props.paper.authors)
      const yr = props.paper.year ? String(props.paper.year) : ''
      const label = [au, yr].filter(Boolean).join(' ') || props.paper.title
      out.push({
        key: 'paper',
        label,
        title: props.paper.title,
        icon: ReaderOutline,
        target: 'paper',
      })
    }

    // 3. 当前 tex 文件 + 行（READ 也能对照草稿；真实数据）
    if (fileLabel) {
      out.push({
        key: 'file',
        label: fileLabel,
        title: filePath,
        icon: CodeSlashOutline,
        target: 'file',
        path: filePath,
      })
    }

    // 4. 项目知识库（真实计数；上下文展示，不承载动作 → 静态标签）
    const kbLabel = props.paperCount ? `项目知识库 · ${props.paperCount} 篇` : '项目知识库'
    out.push({ key: 'knowledge', label: kbLabel, icon: LibraryOutline, target: 'knowledge' })
  } else {
    // WRITE：当前 tex 文件 + 行
    out.push({
      key: 'file',
      label: fileLabel || 'main.tex',
      title: filePath || 'main.tex',
      icon: CodeSlashOutline,
      target: 'file',
      path: filePath || 'main.tex',
    })

    // 当前章节（真实 context）
    out.push({
      key: 'section',
      label: ctx.section ? `§${ctx.section}` : '当前 Section',
      icon: LayersOutline,
      target: 'selection',
    })

    // 引用库（真实文件；无则不显示，不伪造）
    if (props.bibFile) {
      out.push({ key: 'bib', label: baseName(props.bibFile), title: props.bibFile, icon: DocumentTextOutline, target: 'file', path: props.bibFile })
    }

    // 相关论文（项目知识；静态展示）
    const kbLabel = props.paperCount ? `相关论文 · ${props.paperCount} 篇` : '相关论文'
    out.push({ key: 'knowledge', label: kbLabel, icon: LibraryOutline, target: 'knowledge' })
  }

  return out
})

/** chip 是否为可点击动作（knowledge 是上下文展示，非按钮，§26）。 */
function isInteractive(chip: Chip): boolean {
  return Boolean(chip.target) && chip.target !== 'knowledge'
}

function onChip(chip: Chip): void {
  if (!isInteractive(chip)) return
  emit('locate', { kind: chip.target, label: chip.label, path: chip.path })
}
</script>

<style scoped>
.rw-ctx {
  flex: 0 0 auto;
  /* 黄金图 READ 实测：`当前上下文` 标签内容带 y 156–169，chips 背景带 y 186–218（高 32）。
     复算：tmp/lead_bands.py read 1065 1672 56 320 */
  padding: 9px 12px 10px;
  border-bottom: var(--rw-border-w) solid var(--rw-border);
}
.rw-ctx__head {
  display: flex;
  align-items: center;
  gap: 5px;
  margin-bottom: 6px;
  min-width: 0;
}
.rw-ctx__lock {
  width: 15px;   /* 显式尺寸：@vicons <svg> 无 width/height，仅 font-size 无效 */
  height: 15px;
  font-size: 15px;
  color: var(--rw-primary);
  flex: 0 0 auto;
}
.rw-ctx__title {
  font-size: 11.5px;
  font-weight: 800;
  color: var(--rw-text-2);
  white-space: nowrap;
}
.rw-ctx__hint {
  font-size: 9.5px;
  color: var(--rw-text-3);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.rw-ctx__chips {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.rw-ctx__chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 24px;
  max-width: 100%;
  padding: 0 8px;
  border: 1px solid var(--rw-border);
  border-radius: 999px;
  background: var(--rw-surface);
  color: var(--rw-text-2);
  font-size: 10px;
  font-weight: 600;
  cursor: pointer;
}
.rw-ctx__chip:hover {
  background: var(--rw-primary-softer);
}
/* 静态上下文标签（knowledge 类）：保留视觉，但无按钮暗示（§26/§70） */
.rw-ctx__chip--static {
  cursor: default;
}
.rw-ctx__icon {
  width: 14px;   /* §4.4-2 chips 内图标 14px（仅 font-size 无法约束 @vicons <svg>） */
  height: 14px;
  font-size: 14px;
  flex: 0 0 auto;
}
.rw-ctx__label {
  min-width: 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* ── WRITE 314px 紧凑态：**单行**压缩（主理人裁决）──
   宪法 §26 要求 WRITE 也显示「当前上下文」，但黄金图 WRITE 面板**没有**此行
   （从面板头直接进入 `LaTeX 错误解释` 卡）。
   裁决：保留内容、把视觉成本压到最小 —— 标签与 chips **同行**、**不换行**、可横向滚动，
   整行高 ≤ 28px。依据：§26 是产品行为要求，黄金图是视觉参考；成本约 28px 高度。 */
@container rw-agent (max-width: 400px) {
  .rw-ctx {
    display: flex;
    align-items: center;
    gap: 8px;
    height: 28px;
    padding: 0 10px;
  }
  .rw-ctx__head {
    margin-bottom: 0;
    flex: 0 0 auto;
  }
  .rw-ctx__hint {
    display: none;
  }
  .rw-ctx__chips {
    flex: 1 1 auto;
    flex-wrap: nowrap;
    overflow-x: auto;
    gap: 6px;
    scrollbar-width: none;
  }
  .rw-ctx__chips::-webkit-scrollbar {
    display: none;
  }
  .rw-ctx__chip {
    flex: 0 0 auto;
    height: 22px;
    padding: 0 7px;
    font-size: var(--rw-fs-xs);
  }
}
</style>
