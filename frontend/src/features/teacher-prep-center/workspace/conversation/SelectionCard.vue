<script setup lang="ts">
/**
 * 章节选择卡（W1-C · LEAD-BAR §3.2 选择卡样式）。
 *
 * - 白卡 + 发丝线 + 12px 圆角；
 * - 卡片顶部说明按卡类型（kind）区分：教材卡「选一本教材开始」、章节卡
 *   「教材已定位，请选择具体章节后继续」、其它卡通用文案；非活跃历史卡
 *   回显已选中的真实名称（option label / sourceTitle，缺失诚实省略）；
 * - 章节卡按**章节编号分组**（`3.1.2` → 第 3 章），教材卡 / 通用卡平铺；
 *   选项为浅蓝胶囊；hover 描边变主蓝；
 * - 已选项主蓝实心（点选后主蓝实心回显，非活跃态只读展示选中项）；
 * - 「查看全部章节」折叠（长列表默认收起 + 滚动）；
 * - 选项数据 100% 来自后端 selection_card.options，不在前端造选项。
 */
import { computed, ref } from 'vue'
import KatexText from '@/features/teacher-prep-center/math/KatexText.vue'

const props = withDefaults(
  defineProps<{
    options: { id: string; label: string }[]
    /** 是否为「待选择」活跃态（false = 已选择，只读回显）。 */
    active?: boolean
    /** 最近一次点选的选项 id（已选回显）。 */
    selectedId?: string | null
    /** 所属教材展示名（真实投影，缺失则省略该行）。 */
    sourceTitle?: string | null
    /** 卡类型（后端 card props.kind：class / section / textbook / knowledge_points；存量卡缺省 → 按章节卡渲染）。 */
    kind?: string | null
    /** 卡所属教材源 id（后端 card props.source_id；随卡透传，供分流与回溯）。 */
    sourceId?: string | null
  }>(),
  { active: true, selectedId: null, sourceTitle: null, kind: null, sourceId: null },
)

const emit = defineEmits<{ (e: 'pick', option: { id: string; label: string }): void }>()

interface OptionGroup {
  key: string
  heading: string
  options: { id: string; label: string }[]
}

/** 按章节编号首段分组（保持后端返回顺序 = 阅读顺序）；无编号归「其他章节」。 */
/** 防御式选项（服务端脏数据 / 缺字段绝不抛错；无 id 的项丢弃）。 */
const safeOptions = computed<{ id: string; label: string }[]>(() =>
  Array.isArray(props.options)
    ? props.options.filter((o): o is { id: string; label: string } => !!o && typeof (o as { id?: unknown }).id === 'string')
    : [],
)

/** 卡类型归一：缺省 / 'section' → 章节卡（向后兼容存量卡）；教材卡与通用卡区分文案与分组。 */
const cardKind = computed<'section' | 'textbook' | 'generic'>(() => {
  if (props.kind === 'textbook') return 'textbook'
  if (props.kind && props.kind !== 'section') return 'generic'
  return 'section'
})

/** 已选中的选项展示名（真实 option label；未选 / 缺失 → ''，诚实省略不造名）。 */
const selectedLabel = computed<string>(() => {
  if (!props.selectedId) return ''
  return safeOptions.value.find((o) => o.id === props.selectedId)?.label ?? ''
})

/**
 * 卡头标题（老师的话，禁止系统机制话术）：
 * - 教材卡：活跃「选一本教材开始」；非活跃回显真实教材名（sourceTitle 优先，缺了用选项名）；
 * - 章节卡：活跃保持原句；非活跃单行摘要回显章节名；
 * - 其它卡：通用文案；名称缺失一律诚实省略，不写占位词。
 */
const title = computed<string>(() => {
  if (cardKind.value === 'textbook') {
    if (props.active) return '选一本教材开始'
    const name = props.sourceTitle || selectedLabel.value
    return name ? `已选教材·${name}` : '已选教材'
  }
  if (cardKind.value === 'section') {
    if (props.active) return '教材已定位，请选择具体章节后继续'
    return selectedLabel.value ? `已选章节《${selectedLabel.value}》` : '已选择备课章节'
  }
  if (props.active) return '请选择一项，我们继续'
  return selectedLabel.value ? `已选「${selectedLabel.value}」` : '已选择'
})

/** 折叠开关的名词随卡类型走（教材卡说「教材」，不说「章节」）。 */
const optionNoun = computed(() =>
  cardKind.value === 'textbook' ? '教材' : cardKind.value === 'section' ? '章节' : '选项',
)

const groups = computed<OptionGroup[]>(() => {
  // 章节编号分组只属于章节卡；教材卡 / 通用卡的选项不是章节，平铺展示不套「第 X 章」分组
  if (cardKind.value !== 'section') {
    return safeOptions.value.length ? [{ key: 'all', heading: '', options: [...safeOptions.value] }] : []
  }
  const out: OptionGroup[] = []
  const byKey = new Map<string, OptionGroup>()
  for (const opt of safeOptions.value) {
    const m = /^\s*(\d+)\./.exec(opt.label)
    const key = m ? m[1] : 'other'
    let group = byKey.get(key)
    if (!group) {
      group = { key, heading: m ? `第 ${m[1]} 章` : '其他章节', options: [] }
      byKey.set(key, group)
      out.push(group)
    }
    group.options.push(opt)
  }
  return out
})

const totalCount = computed(() => safeOptions.value.length)
/** 折叠阈值：章节数 ≤ 2 且选项 ≤ 12 时直接全展开，避免不必要的收起。 */
const needsCollapse = computed(() => safeOptions.value.length > 12 || groups.value.length > 3)
const expanded = ref(false)
const visibleGroups = computed<OptionGroup[]>(() => {
  if (!needsCollapse.value || expanded.value || !props.active) return groups.value
  // 默认收起：展示前 12 个选项（按组保持顺序）
  const out: OptionGroup[] = []
  let count = 0
  for (const g of groups.value) {
    const rest = 12 - count
    if (rest <= 0) break
    const slice = g.options.slice(0, rest)
    out.push({ ...g, options: slice })
    count += slice.length
  }
  return out
})
const hiddenCount = computed(() => Math.max(0, totalCount.value - 12))

const hasSelection = computed(() => !!props.selectedId)
function isSelected(id: string): boolean {
  return props.selectedId === id
}
function onPick(opt: { id: string; label: string }): void {
  if (!props.active) return
  emit('pick', opt)
}
</script>

<template>
  <div class="pc-selcard" :class="{ 'is-readonly': !active }">
    <div class="pc-selcard__head">
      <span class="pc-selcard__icon" aria-hidden="true">
        <svg viewBox="0 0 16 16">
          <path d="M3 2.4h7.2L13 5.2v8.4H3z" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round" />
          <path d="M9.8 2.4v3h3" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round" />
        </svg>
      </span>
      <div class="pc-selcard__head-text">
        <p class="pc-selcard__title">
          {{ title }}
        </p>
        <!-- 所属教材行只属于章节卡；教材卡的教材名已在标题回显，通用卡（班级/知识点）无所属教材 -->
        <p v-if="sourceTitle && cardKind === 'section'" class="pc-selcard__source">
          所属教材：<span>{{ sourceTitle }}</span>
        </p>
        <p v-else-if="!sourceTitle && cardKind === 'section'" class="pc-selcard__source pc-selcard__source--faint">章节来自当前教材的授权目录</p>
      </div>
      <span v-if="!active && hasSelection" class="pc-selcard__done">已选择</span>
    </div>

    <div class="pc-selcard__body">
      <section v-for="group in visibleGroups" :key="group.key" class="pc-selcard__group">
        <h4 v-if="group.heading" class="pc-selcard__group-head">{{ group.heading }}</h4>
        <div class="pc-selcard__opts">
          <button
            v-for="opt in group.options"
            :key="opt.id"
            type="button"
            class="pc-selcard__opt"
            :class="{ 'is-selected': isSelected(opt.id) }"
            :disabled="!active"
            :aria-pressed="isSelected(opt.id)"
            @click="onPick(opt)"
          >
            <KatexText :text="opt.label" />
            <span v-if="isSelected(opt.id)" class="pc-selcard__check" aria-hidden="true">
              <svg viewBox="0 0 12 12"><path d="M2.5 6.2 5 8.7l4.5-5" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" /></svg>
            </span>
          </button>
        </div>
      </section>
    </div>

    <div v-if="needsCollapse && active" class="pc-selcard__foot">
      <button type="button" class="pc-selcard__toggle" @click="expanded = !expanded">
        {{ expanded ? `收起${optionNoun}列表` : `查看全部${optionNoun}（共 ${totalCount} 个）` }}
      </button>
    </div>
  </div>
</template>

<style scoped>
.pc-selcard {
  --pc-selcard-line: var(--pc-line, #e5e9f2);
  background: var(--pc-panel, #fff);
  border: 1px solid var(--pc-selcard-line);
  border-radius: 12px;
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04);
  padding: 16px 18px 14px;
  font-family: var(--pc-font);
}
.pc-selcard__head {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  margin-bottom: 14px;
}
.pc-selcard__icon {
  flex: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border-radius: 8px;
  background: var(--pc-primary-soft, #eff4ff);
  color: var(--pc-primary, #2563eb);
}
.pc-selcard__icon svg {
  width: 17px;
  height: 17px;
}
.pc-selcard__head-text {
  flex: 1;
  min-width: 0;
}
.pc-selcard__title {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
  color: var(--pc-text, #0f172a);
  line-height: 1.5;
}
.pc-selcard__source {
  margin: 4px 0 0;
  font-size: 12px;
  color: var(--pc-text-muted, #6b7280);
}
.pc-selcard__source > span {
  color: var(--pc-text-2, #1f2937);
  font-weight: 500;
}
.pc-selcard__source--faint {
  color: var(--pc-text-faint, #9ca3af);
}
.pc-selcard__done {
  flex: none;
  align-self: center;
  padding: 3px 10px;
  border-radius: 999px;
  background: var(--pc-primary-soft, #eff4ff);
  color: var(--pc-primary, #2563eb);
  font-size: 12px;
  font-weight: 600;
}
.pc-selcard__body {
  display: flex;
  flex-direction: column;
  gap: 14px;
  max-height: 320px;
  overflow-y: auto;
  overflow-x: hidden;
  padding-right: 2px;
}
.pc-selcard__group-head {
  margin: 0 0 8px;
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.02em;
  color: var(--pc-text-muted, #6b7280);
}
.pc-selcard__opts {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.pc-selcard__opt {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  max-width: 100%;
  padding: 8px 14px;
  border: 1px solid var(--pc-selcard-line);
  border-radius: 999px;
  background: var(--pc-primary-soft, #eff4ff);
  font-size: 13px;
  font-family: inherit;
  color: var(--pc-text-2, #1f2937);
  cursor: pointer;
  text-align: left;
  transition: border-color 0.12s ease, background 0.12s ease, color 0.12s ease;
}
.pc-selcard__opt:hover:not(:disabled) {
  border-color: var(--pc-primary, #2563eb);
  color: var(--pc-primary, #2563eb);
}
.pc-selcard__opt:focus-visible {
  outline: 2px solid var(--pc-primary, #2563eb);
  outline-offset: 1px;
}
.pc-selcard__opt.is-selected {
  background: var(--pc-primary, #2563eb);
  border-color: var(--pc-primary, #2563eb);
  color: #fff;
  font-weight: 600;
}
.pc-selcard__opt:disabled {
  cursor: default;
}
.pc-selcard__opt:disabled:not(.is-selected) {
  opacity: 0.55;
}
.pc-selcard__check {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 14px;
  height: 14px;
}
.pc-selcard__check svg {
  width: 14px;
  height: 14px;
}
.pc-selcard__foot {
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid var(--pc-selcard-line);
}
.pc-selcard__toggle {
  border: none;
  background: transparent;
  padding: 0;
  font-size: 13px;
  font-family: inherit;
  color: var(--pc-primary, #2563eb);
  cursor: pointer;
}
.pc-selcard__toggle:hover {
  text-decoration: underline;
}
</style>
