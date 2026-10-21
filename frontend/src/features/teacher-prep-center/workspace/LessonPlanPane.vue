<script setup lang="ts">
/**
 * 教案页（R5 · 黄金图对齐）。三栏：左大纲 211px ｜ 中区文档纸（工具条 + 居中纸 ≈1040px）｜ 右智能辅助 310px。
 *
 * - 数据全部真实：`GET /prep/conversations/{id}/specs` 定位 spec_id →
 *   `GET /artifacts/lesson-plans/{id}` 读全量 LessonPlanSpec（§F 扩展字段可选，缺失诚实退化）；
 * - 元信息（学科/教材/班级/课时）来自真实教材视图 / 会话快照，缺省段省略不造数；
 * - 教学反思编辑 → 块级 patch 真实落库；AI 建议卡/输入 → `POST /regen` 真实 LLM；
 * - 刷新/重进由 store 权威快照还原。
 */
import { computed, onMounted, ref, watch } from 'vue'
import { usePrepSessionStore } from '@/features/teacher-prep-center/stores/prepSession'
import PlanOutline from './plan/PlanOutline.vue'
import PlanToolbar from './plan/PlanToolbar.vue'
import PlanDocument from './plan/PlanDocument.vue'
import PlanAiPanel from './plan/PlanAiPanel.vue'
import GeneratePanel from '@/features/teacher-prep-center/workspace/GeneratePanel.vue'
import { PLAN_CHAPTERS, stripSectionNo, usePlanEditor, type AiSuggestion, type ChapterKey } from './plan/usePlanEditor'

const store = usePrepSessionStore()
const editor = usePlanEditor()

const docRef = ref<InstanceType<typeof PlanDocument> | null>(null)
const activeSection = ref<ChapterKey>('objectives')
const outlineCollapsed = ref(false)
const aiCollapsed = ref(false)

/** 当前会话最新教案 spec_id（living artifact 权威摘要）。 */
const specId = computed(() => store.current?.artifact.lesson_plan?.spec_id ?? null)
const sessionId = computed(() => store.currentSessionId ?? '')

const spec = editor.spec
const loading = editor.loading
const error = editor.error

/* ── 装载：spec_id 就绪即拉全量 spec，并解析教材元信息 ─────────────────── */
async function loadAll() {
  const id = specId.value
  if (!id) {
    editor.view.value = null
    return
  }
  await editor.load(id)
  const locator = editor.spec.value?.context_ref?.locator ?? null
  await editor.loadTextbookMeta(sessionId.value, locator)
}

watch(specId, () => void loadAll())
onMounted(loadAll)

/* ── 元信息（真实字段；缺省段省略） ───────────────────────────────────── */
const className = computed(() => store.currentCourseMeta.className)
const minutes = computed(() => store.currentCourseMeta.minutes)

/**
 * 学科标签（黄金图 `学科: 高中数学`）：备课中心面向高中数学教师。
 * 教材视图只给学科词「数学」，此处按学段补全为「高中数学」；取不到时仍显示该产品级固定值
 * （不编造班级 / 教材等会话数据）。
 */
const subjectLabel = computed(() => {
  const raw = editor.subject.value?.trim()
  if (!raw) return '高中数学'
  return raw.startsWith('高中') ? raw : `高中${raw}`
})

/**
 * 教材展示名（裁定 6.2：教案元信息取**完整展示名**，`·`→空格）。
 * 优先消费后端权威字段 `artifact.context.source.title`；取不到时回退 `textbook/view.source_title`
 * （W1.2 后两者一致）。另作渲染层防御：对重复段去重（如「…高二 选择性必修第一册」尾段重复），
 * 这是清洗不是拼接，不新增教材信息。
 */
const TEXTBOOK_YEAR_RE = /(?:19|20)\d{2}/g
/** 与「学科」列重复的学段学科段（教材名里的「高中数学」在学科已单独成列时属冗余）。 */
const SUBJECT_SEG_RE = /^(?:高中|初中)?数学$/

function dedupeName(raw: string): string {
  const segs = raw.split(/[·\s]+/).map((s) => s.trim()).filter(Boolean)
  const seen = new Set<string>()
  const out: string[] = []
  for (const seg of segs) {
    // ① 去版本年份（「人教A版2019」→「人教A版」）；② 去与「学科」重复的学段学科段；③ 去重
    const cleaned = seg.replace(TEXTBOOK_YEAR_RE, '').trim()
    if (!cleaned || SUBJECT_SEG_RE.test(cleaned) || seen.has(cleaned)) continue
    seen.add(cleaned)
    out.push(cleaned)
  }
  return out.join(' ')
}

const textbookTitle = computed(() => {
  const authoritative = store.current?.artifact.context?.source?.title?.trim()
  if (authoritative) return dedupeName(authoritative)
  const fallback = editor.textbookTitle.value?.trim()
  return fallback ? dedupeName(fallback) : null
})

/** 中文课时序号（1→一 … 10→十；超出用数字，真实数字不造假）。 */
const CN_HOURS = ['一', '二', '三', '四', '五', '六', '七', '八', '九', '十']

/**
 * 标题课时标签（L1：仅真实来源，反空壳——无真实来源返回 null，不编造「第一课时」）：
 * ① `spec.metadata.lesson_scope`（投影自会话 selection，与标题同源）中「第N课时」类说明；
 * ② 会话 `intent.lesson_hours` 真实数字。
 */
const lessonTag = computed<string | null>(() => {
  const scope = String(editor.spec.value?.metadata?.lesson_scope ?? '').trim()
  const m = /第\s*([一二三四五六七八九十]{1,3}|\d{1,2})\s*课时/.exec(scope)
  if (m) return m[0].replace(/\s+/g, '')
  const hours = store.normalizeLessonHours(store.current?.session.intent?.lesson_hours)
  if (hours != null) {
    return `第${CN_HOURS[hours - 1] ?? hours}课时`
  }
  return null
})

/* ── 章节标题 / 定位标签（右面板「当前定位」） ─────────────────────────── */
const chapterTitle = computed(() => PLAN_CHAPTERS.find((c) => c.key === activeSection.value)?.title ?? null)
const locatedLabel = computed(() => {
  const key = activeSection.value
  if (key === 'process') {
    const st = editor.stages.value[0]
    return st ? stripSectionNo(String(st.title)) || null : null
  }
  const block = editor.blockForChapter(key)
  if (!block) return null
  // 章节号不是定位信息（`3.1.2 椭圆的定义` → `椭圆的定义`），与文档标题同一剥离规则。
  const t = stripSectionNo((block.text || block.label_zh || '').trim())
  return t.length > 18 ? `${t.slice(0, 18)}…` : t || null
})

/* ── 工具条：作用于当前编辑块（反思块） ───────────────────────────────── */
const toolbarEnabled = computed(() => !!spec.value)
function onCommand(cmd: string, value?: string) {
  docRef.value?.applyFormat(cmd, value)
}

/* ── 左大纲点击 → 平滑滚动 ────────────────────────────────────────────── */
function onSelectChapter(key: ChapterKey) {
  activeSection.value = key
  docRef.value?.scrollToSection(key)
}
function onSectionVisible(key: ChapterKey) {
  activeSection.value = key
}

/* ── 教学反思保存（真实块级持久化） ───────────────────────────────────── */
async function onSaveReflection(text: string) {
  const ok = await editor.saveReflection(text)
  docRef.value?.finishEdit(ok)
}

/* ── AI：建议卡 / 输入 → 真实 regen（定位块） ─────────────────────────── */
const aiReady = computed(() => !!spec.value && editor.canUseAi.value && !!editor.blockForChapter(activeSection.value))
const aiDisabledReason = computed(() => {
  if (!spec.value) return '教案还没有生成，先等 AI 完成教案内容'
  if (!editor.canUseAi.value) return '教案的 AI 还没有准备好，稍等一下或刷新后再试'
  if (!editor.blockForChapter(activeSection.value)) return '这一章暂时没有可以定位的内容，先看看其它章节'
  return ''
})

async function runAi(instruction: string) {
  const block = editor.blockForChapter(activeSection.value)
  if (!block) return
  await editor.runInstruction(block.block_id, instruction)
}
function onSuggest(card: AiSuggestion) {
  void runAi(card.instruction)
}

/** G14 单块重试：只重试失败的那一块（lesson-plan /regen），其余块保持已生效内容。 */
function onRetryBlock() {
  void editor.retryFailedBlock()
}
</script>

<template>
  <section class="pc-plan">
    <!-- 左大纲栏 211px -->
    <PlanOutline
      v-if="!outlineCollapsed"
      :active="activeSection"
      @select="onSelectChapter"
      @collapse="outlineCollapsed = true"
    />
    <button v-else type="button" class="pc-plan__rail" title="展开教案大纲" @click="outlineCollapsed = false">
      <svg viewBox="0 0 12 12" aria-hidden="true"><path d="M4.5 2.5 8 6l-3.5 3.5" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" /></svg>
    </button>

    <!-- 中区：工具条 + 文档纸 -->
    <div class="pc-plan__main">
      <PlanToolbar :enabled="toolbarEnabled" @command="onCommand" />
      <div class="pc-plan__scroll">
        <PlanDocument
          v-if="spec"
          ref="docRef"
          :spec="spec"
          :subject="subjectLabel"
          :textbook-title="textbookTitle"
          :class-name="className"
          :minutes="minutes"
          :lesson-tag="lessonTag"
          :saving="editor.saving.value"
          :save-error="editor.saveError.value"
          @save-reflection="onSaveReflection"
          @section-visible="onSectionVisible"
        />
        <div v-else class="pc-plan__state">
          <template v-if="loading">正在加载教案…</template>
          <template v-else-if="error">教案加载失败：{{ error }}</template>
          <!-- G11 空态出路：真实下一步按钮（确认路径→同源投影，全链真实作业） -->
          <GeneratePanel
            v-else
            empty-title="教案尚未生成"
            empty-desc="确认教学路径后，会按它生成课件与教案。"
            gen-label="生成教案"
          />
        </div>
      </div>
    </div>

    <!-- 右智能辅助 310px -->
    <PlanAiPanel
      v-if="!aiCollapsed"
      :chapter-title="chapterTitle"
      :located-label="locatedLabel"
      :suggestions="editor.suggestions"
      :can-use-ai="aiReady"
      :disabled-reason="aiDisabledReason"
      :ai-busy="editor.aiBusy.value"
      :ai-error="editor.aiError.value"
      :ai-notice="editor.aiNotice.value"
      :can-retry-block="!!editor.aiFailedBlock.value"
      @suggest="onSuggest"
      @ask="runAi"
      @retry-block="onRetryBlock"
      @close="aiCollapsed = true"
    />
    <button v-else type="button" class="pc-plan__ai-rail" title="展开智能辅助" @click="aiCollapsed = false">
      <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 1.2 9.6 5.8 14.2 7.4 9.6 9 8 13.6 6.4 9 1.8 7.4 6.4 5.8z" fill="currentColor" /></svg>
    </button>
  </section>
</template>

<style scoped>
.pc-plan {
  /* --pc-viewport-h：嵌入外框后内容区可用高度（R1 G1；默认 100vh） */
  height: calc(var(--pc-viewport-h, 100vh) - var(--pc-header-h));
  display: flex;
  align-items: stretch;
  background: var(--pc-bg);
  font-family: var(--pc-font);
  overflow: hidden;
  box-sizing: border-box;
}
.pc-plan__main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  background: var(--pc-panel);
}
.pc-plan__scroll {
  flex: 1;
  overflow-y: auto;
  overflow-x: hidden;
  background: var(--pc-panel);
}
.pc-plan__state {
  padding: 96px 24px;
  text-align: center;
  font-size: 14px;
  color: var(--pc-text-faint);
}
.pc-plan__rail,
.pc-plan__ai-rail {
  flex: none;
  width: 32px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: none;
  background: #f6fbfe;
  color: var(--pc-text-muted);
  cursor: pointer;
}
.pc-plan__ai-rail {
  background: #fafdff;
  border-left: 1px solid var(--pc-line);
}
.pc-plan__rail {
  border-right: 1px solid var(--pc-line);
}
.pc-plan__rail svg {
  width: 14px;
  height: 14px;
}
.pc-plan__ai-rail svg {
  width: 16px;
  height: 16px;
  color: var(--pc-primary);
}
.pc-plan__rail:hover,
.pc-plan__ai-rail:hover {
  color: var(--pc-primary);
}
</style>
