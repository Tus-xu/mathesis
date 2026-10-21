<template>
  <div class="rw-result" :class="{ 'rw-result--empty': !hasResult }" data-rw-result>
    <template v-if="hasResult">
      <!-- WRITE · LaTeX 错误解释（宪法 §39）：红圈 `!` + 错误标题行（无状态药丸，
           能力名由 Accordion 头部承载）→ 解释正文 → 文件位置块 → 建议修改 Diff → 补充建议 -->
      <template v-if="isLatexError">
        <div class="rw-result__status">
          <!-- 无错误（status=verified/supported）时**不能**渲染成红「! 编译成功」——
               改用绿色 ✓ + 中性标题，与「成功态不走错误卡语义」的后端约定一致。 -->
          <span v-if="latexOk" class="rw-result__ok-mark" aria-hidden="true">
            <CheckmarkCircle />
          </span>
          <span v-else class="rw-result__error-mark" aria-hidden="true">!</span>
          <span class="rw-result__name" :class="{ 'rw-result__name--danger': !latexOk }">
            {{ errorTitle }}
          </span>
        </div>

        <p v-if="summary" class="rw-result__body" v-html="summaryHtml" />
        <div v-else-if="findings.length" class="rw-result__body">
          <p v-for="(f, i) in findings" :key="i" v-html="rich(f.detail || f.label)" />
        </div>

        <div v-if="firstSource" class="rw-result__block">
          <p class="rw-result__block-title">
            <DocumentTextOutline class="rw-result__block-icon" aria-hidden="true" />
            文件位置
          </p>
          <p class="rw-result__loc">
            {{ firstSource.file }} · 第 <span class="rw-result__loc-line">{{ firstSource.line }}</span> 行
          </p>
        </div>

        <div v-if="patches.length" class="rw-result__block">
          <p class="rw-result__block-title">建议修改</p>
          <DiffBlock
            :patches="patches"
            :pending="pendingConfirm"
            :line-resolver="lineResolver"
            @accept="emit('action', 'apply_patch')"
            @reject="pendingConfirm = false"
          />
        </div>

        <p v-if="suggestion" class="rw-result__suggest">建议：<span v-html="rich(suggestion)" /></p>
      </template>

      <!-- 通用结果（宪法 §58/§59）：正文 → 关键信息块 → 引用/证据 → 建议修改 → 动作行 -->
      <template v-else>
        <p v-if="summary" class="rw-result__body" v-html="summaryHtml" />

        <div v-if="findings.length" class="rw-result__block">
          <p class="rw-result__block-title">关键信息</p>
          <ol class="rw-result__findings">
            <FindingRow v-for="(f, i) in findings" :key="i" :index="i + 1" :finding="f" />
          </ol>
        </div>

        <!-- 引用补全：文献/作者/年份/支持程度/来源位置（宪法 §36） -->
        <div v-if="isCitation && citations.length" class="rw-result__cites">
          <CitationRow v-for="(c, i) in citations" :key="i" :item="c" />
        </div>

        <!-- 证据来源块（宪法 §33）：黄金图中「证据来源」整行坐在**内嵌浅块**上
             （bg --rw-inset / radius 8 / 与「关键信息」块同族），不是裸行。 -->
        <div v-if="!isCitation && evidence.length" class="rw-result__block rw-result__block--evidence">
          <EvidenceSource
            v-for="(ev, i) in evidence"
            :key="i"
            :source="ev"
            :show-label="i === 0"
            @select="emit('source', $event)"
          />
        </div>

        <div v-if="patches.length" class="rw-result__block">
          <p class="rw-result__block-title">建议修改</p>
          <DiffBlock
            :patches="patches"
            :pending="pendingConfirm"
            :line-resolver="lineResolver"
            @accept="emit('action', 'apply_patch')"
            @reject="pendingConfirm = false"
          />
        </div>
      </template>
    </template>

    <!-- 诚实空态：尚未产出结果时，给出下一步而非伪造结论（宪法 §70 / 黄金图默认展开态）。
         执行中不在这里重复 activity —— 过程状态由 Accordion 的 Task 状态条承载（§54/§55），
         否则同一句「正在检查……」会渲染两次。 -->
    <p v-else-if="!running" class="rw-result__empty">{{ emptyHint }}</p>

    <ActionBar
      v-if="result && actionList.length"
      class="rw-result__actions"
      :actions="actionList"
      :disabled="busy"
      @action="onAction"
    />

    <!-- 动作回执（诚实反馈：加入笔记 / 插入引用等的真实结果，§70） -->
    <p v-if="feedback" class="rw-result__feedback" role="status" aria-live="polite">{{ feedback }}</p>
  </div>
</template>

<script setup lang="ts">
/**
 * 结果卡（VISUAL-SPEC §4.4「结果卡内容结构」/ 宪法 §58 Structured Output + §59 Result DS）。
 *
 * 单一容器按 `kind` 渲染结构化 `ResearchResult`，统一「Finding / Evidence / Source /
 * Diff / Action」语言，**每加一个能力不再新造卡片**（宪法 §59）。
 *
 * - `derivation_check` / `formula_check` / `latex_compare`：正文 + 关键信息 + 证据来源 + 动作行；
 *   （其「状态行：状态图标 + 能力名 + 状态药丸」按黄金图**合并进 Accordion 头部**，见 CapabilityAccordion）
 * - `citation`：引用候选行（§36）+ 未验证引用显式标注（§37）；
 * - `latex_error`：红圈 `!` + 错误标题行 + 文件位置块 + `建议修改` Diff + 动作行（§39）；
 * - `translate` / `polish` / `suggestion`：正文 + Diff（改动需确认，§40/§41）。
 *
 * `result` 为 `null`（尚未执行 / 刚进入面板）时，渲染**诚实空态**，绝不伪造结论（§70）。
 */
import { computed, ref, watch } from 'vue'
import { CheckmarkCircle, DocumentTextOutline } from '@vicons/ionicons5'
import type { AgentPreset, EvidenceSource as EvidenceSourceType, ResearchResult, ResultKind, WorkspaceMode } from '../api/types'
import { actionMeta } from './useAgentTask'
import { renderRich } from '@/components/mathx/latex'
import FindingRow from '../ui/FindingRow.vue'
import EvidenceSource from '../ui/EvidenceSource.vue'
import ActionBar, { type AgentActionResult } from '../ui/ActionBar.vue'
import CitationRow, { type CitationItem } from '../ui/CitationRow.vue'
import DiffBlock from './DiffBlock.vue'

const props = withDefaults(
  defineProps<{
    /** 结构化结果；`null` = 尚未产出（渲染诚实空态） */
    result?: ResearchResult | null
    mode: WorkspaceMode
    /** 当前能力 preset（用于空态文案；可选） */
    preset?: AgentPreset | null
    /** 是否正在执行（空态切换为 activity 文案） */
    running?: boolean
    /** 供 DiffBlock 解析真实原文行（patch.before 为空时） */
    lineResolver?: (file: string, line: number) => string
    /** 正在应用改动（禁用动作） */
    busy?: boolean
    /** 动作执行的诚实回执文案（空串 = 无） */
    feedback?: string
  }>(),
  { result: null, preset: null, running: false, lineResolver: undefined, busy: false, feedback: '' },
)

const emit = defineEmits<{
  (e: 'action', key: string): void
  (e: 'source', ev: EvidenceSourceType): void
}>()

const pendingConfirm = ref(false)

watch(
  () => props.result,
  () => {
    pendingConfirm.value = false
  },
)

const hasResult = computed(() => Boolean(props.result))
const findings = computed(() => props.result?.findings ?? [])
const evidence = computed(() => props.result?.evidence ?? [])
const firstSource = computed(() => (props.result?.source_locations ?? [])[0] ?? null)

/**
 * 是否存在「定位」目标 —— 与 `ResearchAgentPanel.onCardAction('locate')` 的**可执行条件逐一对应**：
 * `write` 且带 `source_locations`（定位到文件行），或带 `evidence`（定位到文献页）。
 * 两者皆无时该按钮点击不会有任何反应 → **不渲染**（假能力守卫，§70；
 * 与同函数内 `apply_patch` 在无补丁时跳过同族）。
 */
const hasLocateTarget = computed(() => {
  if (!props.result) return false
  if (props.mode === 'write' && firstSource.value) return true
  return evidence.value.length > 0
})
const isLatexError = computed(() => props.result?.kind === 'latex_error')
const isCitation = computed(() => props.result?.kind === 'citation')
/** 编译无错误：`latex_error` 卡改走「成功」语义（不渲染红 `!` / 不渲染 Diff）。 */
const latexOk = computed(
  () =>
    props.result?.kind === 'latex_error' &&
    (props.result.status === 'verified' || props.result.status === 'supported'),
)
/** 安全访问器（模板中避免 `result` 空值告警）。 */
const summary = computed(() => String(props.result?.summary ?? ''))
const patches = computed(() => props.result?.patches ?? [])

/**
 * 后端自由文本 → KaTeX 富文本 HTML（`$..$` 行内公式 + 白名单标签 + DOMPurify）。
 *
 * ⚠️ 结果卡的**散文类**字段（`summary` / latex_error 的 `findings` 兜底正文 /
 * `suggestion`）都不能再用 `{{ }}` 裸插值：后端 prompt 要求「公式写在 `$...$` 里」，
 * 裸插值会把 `$z$`、`$\frac{\partial C}{\partial z}=0$` 当普通文本吐出来
 * （用户实测「乱码」；`findings[].math` 的红色报错是同源问题的另一处，见 FindingRow）。
 *
 * 例外：`DiffBlock` 里的 patch 文本是**草稿源码 diff**，按源码原样显示才对，不走这里。
 */
function rich(text: unknown): string {
  return renderRich(String(text ?? ''))
}
const summaryHtml = computed(() => rich(summary.value))

/** 未产出结果时的诚实下一步文案（绝不伪造结论，宪法 §70）。 */
const EMPTY_HINT: Record<AgentPreset, string> = {
  derivation_check: '选中论文中的公式，或直接在下方提问，我会在这里给出推导校验结论。',
  citation: '选中需要补充引用的语句，我会在这里给出可核验的引用来源。',
  latex_compare: '选中草稿片段，我会在这里对照文献给出差异说明。',
  formula_check: '选中公式，我会在这里给出公式检查结论。',
  translate: '选中需要翻译或改写的文字，我会在这里给出结果。',
  latex_error: '编译出现错误后，我会在这里解释错误原因并给出修复建议。',
  suggestion: '我会在这里给出基于当前项目与草稿的改进建议。',
  polish: '选中需要润色的段落，我会在这里给出润色结果。',
}

const emptyHint = computed(() => {
  const p = props.preset
  if (p && EMPTY_HINT[p]) return EMPTY_HINT[p]
  return '点击上方能力或直接在下方提问，我会在这里给出结论。'
})

const errorTitle = computed(() => {
  if (latexOk.value) return '编译通过'
  const first = findings.value[0]
  const label = String(first?.label ?? '').trim()
  if (label) return label
  return String(props.result?.summary ?? '').trim() || '编译错误'
})

const suggestion = computed(() => {
  const p = (props.result?.patches ?? [])[0]
  const reason = String(p?.reason ?? '').trim().replace(/^请修复[:：]?\s*/, '')
  // 后端 `summary` 已含「解释 + 可能原因 + 建议」；若正文里已出现同一句，不再重复渲染。
  if (!reason || summary.value.includes(reason)) return ''
  return reason
})

const citations = computed<CitationItem[]>(() => {
  const items: CitationItem[] = evidence.value.map((ev) => ({
    title: ev.title,
    authors: ev.authors,
    year: ev.year,
    page: ev.page,
    section: ev.section,
    snippet: (ev as EvidenceSourceType & { snippet?: string }).snippet,
    support: supportText(props.result?.status ?? 'insufficient'),
  }))
  const unverified = findings.value.find((f) => /未验证引用/.test(String(f.label)))
  if (unverified) {
    items.push({ title: String(unverified.detail || '未验证的引用候选'), unverified: true })
  }
  return items
})

function supportText(s: ResearchResult['status']): string {
  const map: Partial<Record<ResearchResult['status'], string>> = {
    verified: '已核验',
    supported: '支持',
    likely: '可能支持',
    needs_conditions: '需补充条件',
    insufficient: '无法核验',
    conflict: '冲突',
  }
  return map[s] ?? ''
}

/**
 * 动作集合（黄金图逐卡比对）。
 * 以后端 `actions[]` 为准，但保证黄金图规定的**动作语言**齐全：
 *  - READ 结果卡：`定位原文`(描边) `查看证据`(描边) `加入笔记`(主蓝实心)；
 *  - WRITE LaTeX 错误卡：`定位到第 N 行`(主蓝实心) `应用修复`(描边) `加入笔记`(描边)。
 */
function canonicalActions(kind: ResultKind, hasPatches: boolean): string[] {
  if (kind === 'latex_error') {
    return hasPatches ? ['locate', 'apply_patch', 'add_note'] : ['locate', 'add_note']
  }
  if (kind === 'translate' || kind === 'polish' || kind === 'suggestion') {
    return hasPatches ? ['locate', 'apply_patch', 'add_note'] : ['locate', 'add_note']
  }
  if (kind === 'citation') {
    return ['locate', 'add_note']
  }
  return ['locate', 'view_evidence', 'add_note']
}

const actionList = computed<AgentActionResult[]>(() => {
  const r = props.result
  if (!r) return []
  const hasPatches = (r.patches ?? []).length > 0
  // 成功态（编译通过）**只认后端 actions[]**：不再强制补齐错误卡的动作语言，
  // 否则会出现「应用修复」却没有可应用改动的情况。
  const canonical = latexOk.value ? [] : canonicalActions(r.kind, hasPatches)
  const merged = [...canonical]
  for (const key of r.actions ?? []) {
    if (!merged.includes(key)) merged.push(key)
  }
  const out: AgentActionResult[] = []
  for (const key of merged) {
    if (key === 'apply_patch' && !hasPatches) continue
    // 无定位目标时不渲染「定位原文」：点击本就无任何反应（§70 假能力守卫）。
    if (key === 'locate' && !hasLocateTarget.value) continue
    // 「查看证据」的可执行条件恒为「有证据」（`onCardAction`: `if (r.evidence?.length)`）→ 无证据时不渲染。
    if (key === 'view_evidence' && evidence.value.length === 0) continue
    const meta = actionMeta(key)
    let label = meta.label
    let variant = meta.variant
    // latex_error：主操作是「定位到第 N 行」，加入笔记退为描边（对齐黄金图）
    if (isLatexError.value) {
      if (key === 'locate' && firstSource.value) {
        label = `定位到第 ${firstSource.value.line} 行`
        variant = 'primary'
      } else if (key === 'add_note') {
        variant = 'outline'
      }
    }
    out.push({ key, label, variant, icon: meta.icon })
  }
  return out
})

function onAction(key: string): void {
  const r = props.result
  if (key === 'apply_patch' && r && !pendingConfirm.value && r.patches.length) {
    // 修改论文的动作用户必须先确认（宪法 §32/§40）：先展开「接受 / 拒绝」。
    pendingConfirm.value = true
    return
  }
  emit('action', key)
}
</script>

<style scoped>
.rw-result {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 12px 0 2px;
}
.rw-result__status {
  display: flex;
  align-items: center;
  gap: 8px;
}
.rw-result__name {
  flex: 1 1 auto;
  min-width: 0;
  font-size: var(--rw-fs-h2);
  font-weight: var(--rw-fw-bold);
  color: var(--rw-text);
}
.rw-result__name--danger { color: var(--rw-danger); }
.rw-result__body {
  margin: 0;
  /* 结果卡正文 = **15px / 1.7**（保留，不改）。
     r6 以黄金图为准复判：黄金图正文行带高 15–16px、行距 ≈26px → 字号 15px；
     VISUAL-SPEC §4.4 记的 13px 经实测**证伪**（比黄金图明显偏小），故不采纳。
     复算：tmp/r6_scan3.py（结果卡行带扫描，y250–470）。 */
  font-size: var(--rw-fs-h2);
  line-height: 1.7;
  color: var(--rw-text);
  overflow-wrap: anywhere;
}
.rw-result__block {
  padding: 10px;
  border-radius: var(--rw-radius-md);
  background: var(--rw-inset);
}
.rw-result__block-title {
  display: flex;
  align-items: center;
  gap: 5px;
  margin: 0 0 6px;
  font-size: var(--rw-fs-h3);
  font-weight: var(--rw-fw-semibold);
  color: var(--rw-text);
}
.rw-result__block-icon {
  width: 15px;   /* 「关键信息 / 文件位置」块标题前图标 15px（原仅 font-size） */
  height: 15px;
  font-size: 15px;
  color: var(--rw-primary);
}
.rw-result__findings {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin: 0;
  padding: 0;
  list-style: none;
}
/* 证据来源块：内距比「关键信息」块略小（黄金图该行高 ≈ 34px） */
.rw-result__block--evidence {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 7px 10px;
}
.rw-result__cites {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.rw-result__actions {
  margin-top: 2px;
}
/* LaTeX 错误标题行（宪法 §39）——红圈 `!` + 错误标题 */
.rw-result__error-mark {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  flex: 0 0 auto;
  border-radius: 50%;
  background: var(--rw-danger);
  color: var(--rw-surface);
  font-size: 13px;
  font-weight: var(--rw-fw-bold);
}
/* 编译通过：绿圈 ✓（同一 20px 规格，语义对调） */
.rw-result__ok-mark {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  flex: 0 0 auto;
  border-radius: 50%;
  background: var(--rw-success);
  color: var(--rw-surface);
}
.rw-result__ok-mark :deep(svg) {
  width: 14px;
  height: 14px;
}
.rw-result__loc {
  margin: 0;
  font-size: var(--rw-fs-body);
  color: var(--rw-text);
  font-family: var(--rw-font-mono);
}
.rw-result__loc-line {
  color: var(--rw-primary);
  font-weight: var(--rw-fw-semibold);
}
.rw-result__suggest {
  margin: 0;
  font-size: var(--rw-fs-body);
  line-height: var(--rw-lh-body);
  color: var(--rw-text-2);
}
/* 诚实空态 */
.rw-result__empty {
  margin: 0;
  font-size: var(--rw-fs-body);
  line-height: 1.7;
  color: var(--rw-text-3);
}
/* 动作回执 */
.rw-result__feedback {
  margin: 0;
  font-size: var(--rw-fs-sm);
  color: var(--rw-success);
}

/* ── WRITE 314px 紧凑态：块内距收紧、证据行纵向堆叠（VISUAL-SPEC §1.5）── */
@container rw-agent (max-width: 400px) {
  .rw-result {
    gap: 10px;
  }
  .rw-result__block {
    padding: 8px;
  }
  .rw-result__block--evidence {
    gap: 4px;
  }
}
</style>
