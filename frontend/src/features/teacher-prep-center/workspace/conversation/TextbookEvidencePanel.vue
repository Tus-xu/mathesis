<script setup lang="ts">
/**
 * S2 对话三栏右栏「教材依据 / 相关资料」面板（golden1 右栏 ≈300px；G21 硬清单）。
 *
 * tabs「教材依据 / 相关资料」：
 * - 教材依据：教材封面 + 书名 + 版本 +「查看教材」链（真实切片原文抽屉）
 *   + 章节树（章 > 节 > 小节，当前小节高亮）+「相关资源」文件行（类型图标 + 文件名 + 大小 + 下载）。
 * - 相关资料：本会话真实 evidence（SourceLocator 引用）列表。
 *
 * 数据来源（全部真实，缺数据诚实留白）：
 * - 封面/书名/版本：workspace 快照 `artifact.context.source.title`（教材查看端点兜底）；
 *   真实封面资产缺位（切片零图片资产）→ 共用样式化封面块 TextbookCover（留报告）。
 * - 章节树：对话消息流中 selection_card 的真实章节选项（章号由编号推导，与后端
 *   list_section_options 同口径）；无选项时回退到本会话章节（textbook/view 的
 *   section_path + title）单链树，绝不编造兄弟章节。
 * - 相关资源：本节切片插图（真实文件，slice-images 只读出口）；大小取真实
 *   Content-Length，取不到留空；下载为真实字节下载。无插图 → 「暂无」。
 * - 相关资料：workspace `evidence[]`（与资料抽屉同源），文本经 sliceMarkdown 清洗（G22）。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { getAccessToken } from '@/api/authSession'
import { usePrepSessionStore } from '@/features/teacher-prep-center/stores/prepSession'
import {
  collectFigures,
  cleanInlineSlice,
  parseSliceBlocks,
  refSectionId,
  scopeBlocksToSection,
  sectionNoOf,
  sectionRef,
  sliceImageUrl,
  slicePreviewText,
} from '@/features/teacher-prep-center/math/sliceMarkdown'
import KatexText from '@/features/teacher-prep-center/math/KatexText.vue'
import TextbookCover, { textbookDisplayName } from '@/features/teacher-prep-center/workspace/classroom/TextbookCover.vue'
import { textbookVersionSegment } from '@/features/teacher-prep-center/copy/prepCopy'
import { useConversation } from '@/features/teacher-prep-center/workspace/conversation/useConversation'

const props = withDefaults(defineProps<{ sessionId: string }>(), { sessionId: '' })

const store = usePrepSessionStore()
const { state: convState } = useConversation()

/* ── tabs（golden1 右栏顶部）─────────────────────────────────────────── */
type PanelTab = 'evidence' | 'resources'
const tab = ref<PanelTab>('evidence')

/* ── 教材锚点（context 投影）────────────────────────────────────────── */
const context = computed(() => store.current?.artifact.context ?? null)
const locator = computed<string | null>(() => {
  const explicit = context.value?.locator || null
  if (explicit) return explicit
  const src = store.current?.session.intent?.source_id ?? null
  const sec = context.value?.section?.id || store.current?.session.intent?.section_id || null
  return sectionRef(src, sec)
})

/** 视图数据（教材查看端点）：与 TextbookEvidenceCard 同一 store 通道，不新开请求状态。 */
const view = computed(() => {
  const v = store.viewer
  return v.ref === locator.value ? v.data : null
})
const viewLoading = computed(() => store.viewer.loading && store.viewer.ref === locator.value)

watch(
  () => [props.sessionId, locator.value] as const,
  ([sid, ref], prev) => {
    if (ref && sid && (!view.value || prev?.[1] !== ref)) {
      void store.openTextbookViewer(sid, ref)
      store.viewer.open = false // 面板只需数据，不弹原文抽屉
    }
  },
  { immediate: true },
)

/* ── 封面 / 书名 / 版本 ─────────────────────────────────────────────── */
const rawBook = computed(() => store.currentCourseMeta.textbook || view.value?.source_title || '')
const bookDisplay = computed(() => textbookDisplayName(rawBook.value) || null)
/** 版本行：版本段（去尾年份）+ 册次段（教材名规范三段式「出版社 · 学科 · 册次」）。 */
const versionLine = computed(() => {
  const segs = (rawBook.value || '').split('·').map((s) => s.trim()).filter(Boolean)
  const version = textbookVersionSegment(rawBook.value)
  const volume = segs.length >= 3 ? segs[segs.length - 1] : ''
  return [version, volume].filter(Boolean).join(' · ') || null
})

function openViewer() {
  if (locator.value && props.sessionId) void store.openTextbookViewer(props.sessionId, locator.value)
}

/* ── 章节树（章 > 节 > 小节；当前小节高亮）──────────────────────────── */
interface SectionEntry {
  id: string | null
  no: string
  text: string
}

/** 章节条目来源 A：对话消息流中 selection_card 的真实章节选项（后端下发，前端不造选项）。 */
const optionEntries = computed<SectionEntry[]>(() => {
  const msgs = convState.messages as { component?: { type?: string; props?: Record<string, unknown> } | null }[]
  for (let i = msgs.length - 1; i >= 0; i -= 1) {
    const c = msgs[i]?.component
    if (c?.type !== 'selection_card') continue
    const raw = c.props?.options
    if (!Array.isArray(raw)) continue
    const out: SectionEntry[] = []
    for (const o of raw) {
      const id = String((o as { id?: unknown })?.id ?? '')
      const label = String((o as { label?: unknown })?.label ?? '').trim()
      const no = sectionNoOf(label)
      if (!id || !label || !no) continue
      out.push({ id, no, text: (label.replace(/^\s*\d+(?:\.\d+)*\s*/, '') || label).trim() })
    }
    if (out.length) return out
  }
  return []
})

/** 当前小节（真实投影：context.section > intent.section_id）。 */
const currentSectionId = computed(
  () => context.value?.section?.id || store.current?.session.intent?.section_id || null,
)
const currentSectionNo = computed(() => {
  const sec = context.value?.section
  return sectionNoOf(sec?.section_path || '') || sectionNoOf(sec?.title || '')
})
const currentSectionTitle = computed(() => (context.value?.section?.title || '').trim())

/** 章节条目来源 B（回退）：本会话章节单链（textbook/view 的编号 + 标题），不编造兄弟章节。 */
const fallbackEntries = computed<SectionEntry[]>(() => {
  const sec = context.value?.section
  const no = currentSectionNo.value
  const title = (sec?.title || '').trim()
  if (!no) return []
  return [{ id: sec?.id || currentSectionId.value, no, text: title.replace(/^\s*\d+(?:\.\d+)*\s*/, '') }]
})

const entries = computed<SectionEntry[]>(() =>
  optionEntries.value.length ? optionEntries.value : fallbackEntries.value,
)

interface TreeNode {
  key: string
  no: string
  label: string
  level: 1 | 2 | 3
  current: boolean
  sectionId: string | null
  children: TreeNode[]
}

/** 章号 → 「第 N 章」（与后端 list_section_options 同口径：由真实编号推导，不编造章节名）。 */
function chapterLabel(no: string, text: string): string {
  return text ? `${no} ${text}` : `第 ${no} 章`
}

type MutableNode = Omit<TreeNode, 'children'> & { knownText: string; children: MutableNode[] }

const tree = computed<TreeNode[]>(() => {
  const nodeByKey = new Map<string, MutableNode>()
  const roots: MutableNode[] = []
  const getNode = (no: string, level: 1 | 2 | 3): MutableNode => {
    const key = `${level}:${no}`
    let n = nodeByKey.get(key)
    if (!n) {
      n = { key, no, level, label: '', current: false, sectionId: null, children: [], knownText: '' }
      nodeByKey.set(key, n)
    }
    return n
  }
  for (const e of entries.value) {
    const segs = e.no.split('.').filter(Boolean)
    if (!segs.length) continue
    const depth = Math.min(segs.length, 3)
    let parent: MutableNode | null = null
    for (let d = 1; d <= depth; d += 1) {
      const no = segs.slice(0, d).join('.')
      const node = getNode(no, d as 1 | 2 | 3)
      if (d === depth) {
        // 叶子 = 该条目所在层（章/节/小节）；编号更深的条目并入「小节」层（golden 三层）
        if (!node.knownText) node.knownText = e.text
        if (!node.sectionId) node.sectionId = e.id
        if (e.id === currentSectionId.value || e.no === currentSectionNo.value) node.current = true
      }
      if (!parent) {
        if (!roots.includes(node)) roots.push(node)
      } else if (!parent.children.includes(node)) {
        parent.children.push(node)
      }
      parent = node
    }
  }
  const finish = (n: MutableNode) => {
    // 标题文本经 sliceMarkdown 清洗（G22：`\-`/`![](` 等源码不上屏；$…$ 留给 KaTeXText 渲染）
    const text = cleanInlineSlice(n.knownText)
    n.label = n.level === 1 ? chapterLabel(n.no, text) : text ? `${n.no} ${text}` : n.no
    n.children.forEach(finish)
  }
  roots.forEach(finish)
  // 兜底：当前小节未按 id 命中 → 按编号定位（保证「当前小节高亮」不空转）
  if (currentSectionNo.value && ![...nodeByKey.values()].some((n) => n.current)) {
    const hit =
      nodeByKey.get(`3:${currentSectionNo.value}`) ||
      nodeByKey.get(`2:${currentSectionNo.value}`) ||
      nodeByKey.get(`1:${currentSectionNo.value}`)
    if (hit) hit.current = true
  }
  return roots
})

const hasTree = computed(() => tree.value.length > 0)

function onNodeClick(node: TreeNode) {
  const sec = node.sectionId || (node.current ? currentSectionId.value : null)
  const ref = sectionRef(store.current?.session.intent?.source_id ?? null, sec)
  if (ref && props.sessionId) void store.openTextbookViewer(props.sessionId, ref)
}

/* ── 相关资源（本节切片插图 = 真实文件；大小/下载均真实）──────────────── */
interface ResourceRow {
  name: string
  url: string
  caption: string
  sizeText: string
  blob: Blob | null
}

const figures = computed(() => {
  const v = view.value
  if (!v) return []
  const text = v.chunks.map((c) => c.text || '').join('\n') || v.text || ''
  const blocks = scopeBlocksToSection(parseSliceBlocks(text), currentSectionNo.value, {
    chunkScoped: true,
    targetTitle: currentSectionTitle.value,
  })
  return collectFigures(blocks)
})

const resourceRows = ref<ResourceRow[]>([])
const resourceTotal = computed(() => figures.value.length)
const RESOURCE_VISIBLE = 3

function formatBytes(n: number): string {
  if (!Number.isFinite(n) || n <= 0) return ''
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}

/** 真实文件清单：图源解析成功才成行；大小取真实 Content-Length（失败留空，不造数）。 */
watch(
  figures,
  async (figs) => {
    const rows: ResourceRow[] = []
    for (const f of figs.slice(0, RESOURCE_VISIBLE)) {
      const url = sliceImageUrl(f.src)
      if (!url) continue
      const name = String(f.src || '').split(/[\\/]/).pop() || '插图'
      let blob: Blob | null = null
      try {
        const token = getAccessToken()
        const res = await fetch(url, { headers: token ? { Authorization: `Bearer ${token}` } : {} })
        if (res.ok) blob = await res.blob()
      } catch {
        blob = null
      }
      rows.push({
        name,
        url,
        caption: f.caption || f.alt || '',
        sizeText: blob ? formatBytes(blob.size) : '',
        blob,
      })
    }
    resourceRows.value = rows
  },
  { immediate: true },
)

function downloadResource(row: ResourceRow) {
  if (!row.blob) return
  const url = URL.createObjectURL(row.blob)
  const a = document.createElement('a')
  a.href = url
  a.download = row.name
  a.style.display = 'none'
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}

/* ── 相关资料（真实 evidence；与资料抽屉同源同清洗）──────────────────── */
const evidenceItems = computed(() => {
  const sid = context.value?.section?.id || null
  return (store.current?.evidence || [])
    .filter((e) => !sid || refSectionId(e.ref) === sid)
    .map((e) => ({
      ref: e.ref,
      label: slicePreviewText(e.title || '').slice(0, 60),
      snippet: cleanInlineSlice((e.snippet || e.title || '').slice(0, 400)),
    }))
})

/* ── 「查看教材依据」按钮 → 聚焦/高亮本面板（R4b：抽屉移除后的唯一去向）── */
function flashPanel() {
  tab.value = 'evidence'
  rootEl.value?.classList.add('is-flash')
  window.setTimeout(() => rootEl.value?.classList.remove('is-flash'), 2400)
}
const rootEl = ref<HTMLElement | null>(null)

/** 树区内滚定位到「当前小节」（限高后当前节点必须可见，高亮不空转）。 */
function scrollTreeToCurrent() {
  const treeEl = rootEl.value?.querySelector('.pc-tree')
  const cur = rootEl.value?.querySelector('.pc-tree__node.is-current')
  if (!treeEl || !cur) return
  const tr = treeEl.getBoundingClientRect()
  const cr = cur.getBoundingClientRect()
  if (cr.top < tr.top || cr.bottom > tr.bottom) {
    treeEl.scrollTop += cr.top - tr.top - 90
  }
}

onMounted(() => {
  window.addEventListener('pc:show-evidence-panel', flashPanel)
  void nextTick(scrollTreeToCurrent)
})
onBeforeUnmount(() => window.removeEventListener('pc:show-evidence-panel', flashPanel))
watch(tree, () => void nextTick(scrollTreeToCurrent))
watch(
  () => props.sessionId,
  () => {
    tab.value = 'evidence'
    resourceRows.value = []
  },
)
</script>

<template>
  <aside ref="rootEl" class="pc-evidence-panel" aria-label="教材依据面板">
    <!-- tabs -->
    <div class="pc-evidence-panel__tabs" role="tablist" aria-label="教材依据与相关资料">
      <button
        type="button"
        role="tab"
        class="pc-evidence-panel__tab"
        :class="{ 'is-active': tab === 'evidence' }"
        :aria-selected="tab === 'evidence'"
        @click="tab = 'evidence'"
      >
        教材依据
      </button>
      <button
        type="button"
        role="tab"
        class="pc-evidence-panel__tab"
        :class="{ 'is-active': tab === 'resources' }"
        :aria-selected="tab === 'resources'"
        @click="tab = 'resources'"
      >
        相关资料
        <span v-if="evidenceItems.length" class="pc-evidence-panel__tab-count">{{ evidenceItems.length }}</span>
      </button>
    </div>

    <!-- 教材依据 -->
    <div v-if="tab === 'evidence'" class="pc-evidence-panel__body">
      <template v-if="locator">
        <!-- 封面 + 书名 + 版本 + 查看教材 -->
        <div class="pc-evidence-panel__book">
          <TextbookCover :name="rawBook" />
          <div class="pc-evidence-panel__book-info">
            <p class="pc-evidence-panel__book-title">{{ bookDisplay || '—' }}</p>
            <p v-if="versionLine" class="pc-evidence-panel__book-version">{{ versionLine }}</p>
            <button type="button" class="pc-evidence-panel__link" :disabled="!locator" @click="openViewer">
              查看教材 <span aria-hidden="true">›</span>
            </button>
          </div>
        </div>

        <!-- 章节树（章 > 节 > 小节；当前小节高亮） -->
        <section class="pc-evidence-panel__section">
          <h3 class="pc-evidence-panel__h3">章节结构</h3>
          <p v-if="viewLoading && !hasTree" class="pc-evidence-panel__hint">正在读取章节…</p>
          <ul v-if="hasTree" class="pc-tree">
            <template v-for="ch in tree" :key="ch.key">
              <li class="pc-tree__row pc-tree__row--l1">
                <button
                  type="button"
                  class="pc-tree__node"
                  :class="{ 'is-current': ch.current }"
                  :disabled="!ch.sectionId && !ch.current"
                  @click="onNodeClick(ch)"
                >
                  <KatexText class="pc-tree__label" :text="ch.label" />
                </button>
                <ul v-if="ch.children.length" class="pc-tree__children">
                  <template v-for="sec in ch.children" :key="sec.key">
                    <li class="pc-tree__row pc-tree__row--l2">
                      <button
                        type="button"
                        class="pc-tree__node"
                        :class="{ 'is-current': sec.current }"
                        :disabled="!sec.sectionId && !sec.current"
                        @click="onNodeClick(sec)"
                      >
                        <KatexText class="pc-tree__label" :text="sec.label" />
                      </button>
                      <ul v-if="sec.children.length" class="pc-tree__children">
                        <li v-for="leaf in sec.children" :key="leaf.key" class="pc-tree__row pc-tree__row--l3">
                          <button
                            type="button"
                            class="pc-tree__node"
                            :class="{ 'is-current': leaf.current }"
                            :disabled="!leaf.sectionId && !leaf.current"
                            @click="onNodeClick(leaf)"
                          >
                            <KatexText class="pc-tree__label" :text="leaf.label" />
                          </button>
                        </li>
                      </ul>
                    </li>
                  </template>
                </ul>
              </li>
            </template>
          </ul>
          <p v-else-if="!viewLoading" class="pc-evidence-panel__hint">教材定位后展示章节结构。</p>
        </section>

        <!-- 相关资源（真实切片插图文件） -->
        <section class="pc-evidence-panel__section">
          <h3 class="pc-evidence-panel__h3">相关资源</h3>
          <ul v-if="resourceRows.length" class="pc-res">
            <li v-for="row in resourceRows" :key="row.name" class="pc-res__row">
              <span class="pc-res__icon" aria-hidden="true">
                <svg viewBox="0 0 16 16">
                  <rect x="2.2" y="3" width="11.6" height="10" rx="1.6" fill="none" stroke="currentColor" stroke-width="1.2" />
                  <circle cx="5.8" cy="6.4" r="1.1" fill="none" stroke="currentColor" stroke-width="1.1" />
                  <path d="M3.4 11.6 6.6 8.6l2 1.8 2.2-2 2 2.2" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round" />
                </svg>
              </span>
              <span class="pc-res__name" :title="row.caption || row.name">{{ row.name }}</span>
              <span class="pc-res__size">{{ row.sizeText }}</span>
              <button
                type="button"
                class="pc-res__act"
                :disabled="!row.blob"
                :title="row.blob ? '下载该文件' : '文件暂不可下载'"
                @click="downloadResource(row)"
              >
                <svg viewBox="0 0 16 16" aria-hidden="true">
                  <path d="M8 2.6v7.2m0 0 2.6-2.6M8 9.8 5.4 7.2M3.2 12.6h9.6" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round" />
                </svg>
              </button>
            </li>
          </ul>
          <p v-else class="pc-evidence-panel__hint">暂无相关资源。</p>
          <p v-if="resourceTotal > resourceRows.length" class="pc-evidence-panel__hint">
            本节共 {{ resourceTotal }} 项插图，仅列前 {{ resourceRows.length }} 项。
          </p>
        </section>
      </template>
      <p v-else class="pc-evidence-panel__hint pc-evidence-panel__hint--pad">
        完成教材定位后，这里展示教材依据（封面 / 章节结构 / 相关资源）。
      </p>
    </div>

    <!-- 相关资料 -->
    <div v-else class="pc-evidence-panel__body">
      <ul v-if="evidenceItems.length" class="pc-res-list">
        <li v-for="(item, i) in evidenceItems" :key="`${item.ref}-${i}`" class="pc-res-list__item" :title="item.ref">
          <p class="pc-res-list__title">{{ item.label }}</p>
          <KatexText class="pc-res-list__snippet" :text="item.snippet" />
        </li>
      </ul>
      <p v-else class="pc-evidence-panel__hint pc-evidence-panel__hint--pad">暂无相关资料。</p>
    </div>
  </aside>
</template>

<style scoped>
.pc-evidence-panel {
  display: flex;
  flex-direction: column;
  width: 300px;
  flex: none;
  min-height: 0;
  background: var(--pc-panel);
  border: 1px solid var(--pc-line);
  border-radius: var(--pc-radius-panel);
  font-family: var(--pc-font);
  overflow: hidden;
}
/* 「查看教材依据」→ 聚焦高亮（真实可见反馈，2.4s 自消退） */
.pc-evidence-panel.is-flash {
  border-color: var(--pc-primary);
  box-shadow: 0 0 0 3px var(--pc-primary-soft);
}

.pc-evidence-panel__tabs {
  display: flex;
  gap: 2px;
  padding: 8px 10px 0;
  border-bottom: 1px solid var(--pc-line);
}
.pc-evidence-panel__tab {
  position: relative;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 34px;
  padding: 0 12px;
  border: none;
  background: transparent;
  color: var(--pc-text-muted);
  font-size: 13px;
  font-family: inherit;
  cursor: pointer;
}
.pc-evidence-panel__tab.is-active {
  color: var(--pc-primary);
  font-weight: 600;
}
.pc-evidence-panel__tab.is-active::after {
  content: '';
  position: absolute;
  left: 10px;
  right: 10px;
  bottom: 0;
  height: var(--pc-tab-underline);
  border-radius: var(--pc-tab-underline);
  background: var(--pc-primary);
}
.pc-evidence-panel__tab-count {
  min-width: 16px;
  padding: 0 5px;
  border-radius: var(--pc-radius-full);
  background: var(--pc-primary-soft);
  color: var(--pc-primary);
  font-size: 11px;
  line-height: 16px;
  text-align: center;
}

.pc-evidence-panel__body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 12px;
}

.pc-evidence-panel__book {
  display: flex;
  gap: 12px;
}
.pc-evidence-panel__book-info {
  min-width: 0;
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.pc-evidence-panel__book-title {
  margin: 0;
  font-size: 13.5px;
  font-weight: 700;
  color: var(--pc-text);
  line-height: 1.5;
}
.pc-evidence-panel__book-version {
  margin: 0;
  font-size: 12px;
  color: var(--pc-text-muted);
}
.pc-evidence-panel__link {
  align-self: flex-start;
  margin-top: 2px;
  padding: 2px 4px;
  border: none;
  border-radius: 4px;
  background: transparent;
  color: var(--pc-primary);
  font-size: 12.5px;
  font-family: inherit;
  cursor: pointer;
}
.pc-evidence-panel__link:disabled {
  color: var(--pc-text-faint);
  cursor: not-allowed;
}

.pc-evidence-panel__section {
  margin-top: 14px;
}
.pc-evidence-panel__h3 {
  margin: 0 0 6px;
  font-size: 12.5px;
  font-weight: 700;
  color: var(--pc-text-2);
}
.pc-evidence-panel__hint {
  margin: 4px 0 0;
  font-size: 12px;
  line-height: 1.6;
  color: var(--pc-text-faint);
}
.pc-evidence-panel__hint--pad {
  padding: 8px 2px;
}

/* 章节树 */
.pc-tree,
.pc-tree__children {
  list-style: none;
  margin: 0;
  padding: 0;
}
/* 树区限高内滚：真实章节常多于 20 条，限高保证 封面 + 章节树 + 相关资源 同屏可见
   （golden1 右栏一屏构图）；超出部分在树区内滚动，不把「相关资源」挤出面板。 */
.pc-tree {
  max-height: 300px;
  overflow-y: auto;
  padding-right: 2px;
}
.pc-tree__children {
  margin-left: 12px;
  border-left: 1px solid var(--pc-line);
  padding-left: 8px;
}
.pc-tree__node {
  display: flex;
  align-items: center;
  width: 100%;
  padding: 4px 6px;
  border: none;
  border-radius: 6px;
  background: transparent;
  text-align: left;
  font-family: inherit;
  color: var(--pc-text-2);
  cursor: pointer;
}
.pc-tree__node:hover:not(:disabled) {
  background: var(--pc-section-bg);
  color: var(--pc-primary);
}
.pc-tree__node:disabled {
  cursor: default;
}
.pc-tree__row--l1 > .pc-tree__node .pc-tree__label {
  font-size: 12.5px;
  font-weight: 600;
  color: var(--pc-text);
}
.pc-tree__row--l2 > .pc-tree__node .pc-tree__label {
  font-size: 12.5px;
}
.pc-tree__row--l3 > .pc-tree__node .pc-tree__label {
  font-size: 12px;
  color: var(--pc-text-2);
}
.pc-tree__label {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
/* 当前小节高亮（golden1 右栏章节树） */
.pc-tree__node.is-current {
  background: var(--pc-selected);
}
.pc-tree__node.is-current .pc-tree__label {
  color: var(--pc-primary);
  font-weight: 600;
}

/* 相关资源行（类型图标 + 文件名 + 大小 + 下载） */
.pc-res {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.pc-res__row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 8px;
  border: 1px solid var(--pc-line);
  border-radius: 8px;
}
.pc-res__icon {
  flex: none;
  display: inline-flex;
  width: 16px;
  height: 16px;
  color: var(--pc-primary);
}
.pc-res__icon svg {
  width: 16px;
  height: 16px;
}
.pc-res__name {
  flex: 1;
  min-width: 0;
  font-size: 12px;
  color: var(--pc-text-2);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.pc-res__size {
  flex: none;
  font-size: 11px;
  color: var(--pc-text-faint);
  font-variant-numeric: tabular-nums;
}
.pc-res__act {
  flex: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: var(--pc-primary);
  cursor: pointer;
}
.pc-res__act:hover:not(:disabled) {
  background: var(--pc-primary-soft);
}
.pc-res__act:disabled {
  color: var(--pc-text-faint);
  cursor: not-allowed;
}
.pc-res__act svg {
  width: 14px;
  height: 14px;
}

/* 相关资料（真实 evidence 列表） */
.pc-res-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.pc-res-list__item {
  padding: 8px 10px;
  border: 1px solid var(--pc-line);
  border-radius: 8px;
}
.pc-res-list__title {
  margin: 0 0 3px;
  font-size: 12px;
  font-weight: 600;
  color: var(--pc-primary);
}
.pc-res-list__snippet {
  font-size: 12px;
  line-height: 1.6;
  color: var(--pc-text-2);
}
</style>
