<script setup lang="ts">
/**
 * 展开卡左栏单组（教师提问/学生活动/板书要点）：查看态 + 编辑态。
 *
 * - 查看态：线性图标 + 组名 + 内容（KaTeX 行内公式；编号/圆点列表），右上「✎ 编辑」；
 * - 编辑态：每条一个 textarea + 删除 + 添加；保存 = 真实落库（PATCH node，父组件处理），
 *   取消 = 丢弃本地修改；失败由父组件回滚并展示错误。
 */
import { computed, reactive, watch } from 'vue'
import KatexText from '@/features/teacher-prep-center/math/KatexText.vue'
import type { PrepNodeItem } from '@/features/teacher-prep-center/api/prepCenterApi'

const props = withDefaults(
  defineProps<{
    title: string
    icon: 'question' | 'student' | 'board'
    items: PrepNodeItem[]
    ordered?: boolean
    saving?: boolean
  }>(),
  { ordered: false, saving: false },
)

const emit = defineEmits<{
  save: [items: { text: string }[]]
}>()

const editing = defineModel<boolean>('editing', { default: false })

const drafts = reactive<{ text: string }[]>([])
let savedJson = ''

watch(
  editing,
  (on) => {
    if (on) {
      const seed = props.items.length ? props.items.map((it) => ({ text: it.text })) : [{ text: '' }]
      drafts.splice(0, drafts.length, ...seed.map((x) => ({ text: x.text })))
      savedJson = JSON.stringify(drafts.map((d) => d.text))
    }
  },
  { immediate: true },
)

const dirty = computed(() => editing.value && JSON.stringify(drafts.map((d) => d.text)) !== savedJson)

function startEdit() {
  editing.value = true
}
function cancelEdit() {
  editing.value = false
}
function removeDraft(i: number) {
  drafts.splice(i, 1)
}
function addDraft() {
  drafts.push({ text: '' })
}
function save() {
  const items = drafts.map((d) => ({ text: d.text.trim() })).filter((d) => d.text.length > 0)
  emit('save', items)
  editing.value = false
}
</script>

<template>
  <div class="pc-group">
    <div class="pc-group__head">
      <span class="pc-group__icon" aria-hidden="true">
        <svg v-if="icon === 'question'" viewBox="0 0 16 16">
          <circle cx="8" cy="8" r="6.4" fill="none" stroke="currentColor" stroke-width="1.3" />
          <path d="M6.3 6.2c.2-1 1-1.6 1.9-1.5.9 0 1.7.7 1.7 1.6 0 1.1-1.2 1.3-1.6 2.1-.1.2-.1.4-.1.6" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" />
          <circle cx="8.1" cy="11.4" r="0.8" fill="currentColor" />
        </svg>
        <svg v-else-if="icon === 'student'" viewBox="0 0 16 16">
          <circle cx="8" cy="5.4" r="2.6" fill="none" stroke="currentColor" stroke-width="1.3" />
          <path d="M2.8 13.4c.7-2.4 2.8-3.8 5.2-3.8s4.5 1.4 5.2 3.8" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" />
        </svg>
        <svg v-else viewBox="0 0 16 16">
          <path d="M3.2 2.8h9.6v10.4H3.2z" fill="none" stroke="currentColor" stroke-width="1.3" />
          <path d="M5.4 6h5.2M5.4 8.4h5.2M5.4 10.8h3.2" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" />
        </svg>
      </span>
      <span class="pc-group__name">{{ title }}</span>
      <span class="pc-group__spacer"></span>
      <button v-if="!editing" type="button" class="pc-group__edit" @click="startEdit">
        <svg viewBox="0 0 14 14" aria-hidden="true">
          <path d="M9.8 2.2 11.8 4.2 5.4 10.6 3 11l.4-2.4z" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linejoin="round" />
        </svg>
        编辑
      </button>
      <template v-else>
        <button type="button" class="pc-group__btn" :disabled="!dirty || saving" @click="save">
          {{ saving ? '保存中…' : '保存' }}
        </button>
        <button type="button" class="pc-group__btn pc-group__btn--ghost" @click="cancelEdit">取消</button>
      </template>
    </div>

    <!-- 查看态/编辑态内容统一置于浅灰圆角内容盒（黄金图：盒底 #F6F9FC vs 白面板） -->
    <div class="pc-group__box">
      <!-- 查看态：KaTeX 渲染，禁止裸 LaTeX 上屏 -->
      <ol v-if="!editing && items.length" class="pc-group__list" :class="{ 'is-ordered': ordered }">
        <li v-for="(item, i) in items" :key="i">
          <KatexText :text="item.text" />
        </li>
      </ol>
      <p v-else-if="!editing" class="pc-group__empty">暂无内容，可点击「编辑」补充，或让 AI 修改本环节。</p>

      <!-- 编辑态：列表编辑（每条一个输入区） -->
      <div v-else class="pc-group__editor">
        <div v-for="(d, i) in drafts" :key="i" class="pc-group__draft">
          <span v-if="ordered" class="pc-group__draft-no">{{ i + 1 }}.</span>
          <textarea v-model="d.text" rows="2" :placeholder="`输入${title}内容（支持 $...$ LaTeX 行内公式）`"></textarea>
          <button type="button" class="pc-group__draft-del" title="删除本条" @click="removeDraft(i)">×</button>
        </div>
        <button type="button" class="pc-group__add" @click="addDraft">＋ 添加一条</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.pc-group {
  min-width: 0;
}
.pc-group + .pc-group {
  margin-top: 20px;
}
.pc-group__head {
  display: flex;
  align-items: center;
  gap: 8px;
}
.pc-group__icon {
  display: inline-flex;
  width: 18px;
  height: 18px;
  color: var(--pc-primary);
}
.pc-group__icon svg {
  width: 18px;
  height: 18px;
}
.pc-group__name {
  font-size: 14px;
  font-weight: 700;
  color: var(--pc-text);
}
.pc-group__spacer {
  flex: 1;
}
.pc-group__edit {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  border: none;
  background: transparent;
  color: var(--pc-primary);
  font-size: 13px;
  font-family: inherit;
  cursor: pointer;
  padding: 2px 4px;
  border-radius: 4px;
}
.pc-group__edit:hover {
  background: var(--pc-primary-soft);
}
.pc-group__edit svg {
  width: 12px;
  height: 12px;
}
.pc-group__btn {
  padding: 4px 12px;
  border: none;
  border-radius: var(--pc-radius-btn);
  background: var(--pc-primary);
  color: #fff;
  font-size: 12px;
  font-family: inherit;
  cursor: pointer;
}
.pc-group__btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.pc-group__btn--ghost {
  background: transparent;
  color: var(--pc-text-muted);
  border: 1px solid var(--pc-line);
}
/* 浅灰圆角内容盒（黄金图：盒底 #F6F9FC vs 白面板 Δ8 可见；R6-S3fix #6） */
.pc-group__box {
  margin-top: 8px;
  padding: 12px 16px;
  background: var(--pc-content-box);
  border-radius: var(--pc-radius-btn);
}
.pc-group__list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
  counter-reset: pc-group-item;
}
.pc-group__list li {
  position: relative;
  padding-left: 22px;
  font-size: 14px;
  line-height: 1.75;
  color: var(--pc-text-2);
}
.pc-group__list.is-ordered li::before {
  counter-increment: pc-group-item;
  content: counter(pc-group-item) '.';
  position: absolute;
  left: 2px;
  color: var(--pc-text-muted);
}
.pc-group__list:not(.is-ordered) li::before {
  content: '';
  position: absolute;
  left: 8px;
  top: 0.75em;
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--pc-primary);
}
.pc-group__empty {
  margin: 0;
  font-size: 13px;
  color: var(--pc-text-faint);
}
.pc-group__editor {
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.pc-group__draft {
  display: flex;
  align-items: flex-start;
  gap: 6px;
}
.pc-group__draft-no {
  font-size: 13px;
  color: var(--pc-text-muted);
  line-height: 2.2;
}
.pc-group__draft textarea {
  flex: 1;
  resize: vertical;
  min-height: 40px;
  padding: 6px 10px;
  border: 1px solid var(--pc-line);
  border-radius: var(--pc-radius-btn);
  font-size: 13px;
  font-family: inherit;
  line-height: 1.6;
  color: var(--pc-text-2);
  background: var(--pc-panel);
}
.pc-group__draft textarea:focus {
  outline: none;
  border-color: var(--pc-primary);
}
.pc-group__draft-del {
  border: none;
  background: transparent;
  color: var(--pc-text-faint);
  font-size: 16px;
  cursor: pointer;
  line-height: 1.6;
}
.pc-group__draft-del:hover {
  color: var(--pc-danger);
}
.pc-group__add {
  align-self: flex-start;
  border: 1px dashed var(--pc-line);
  background: transparent;
  border-radius: var(--pc-radius-btn);
  padding: 4px 10px;
  font-size: 12px;
  font-family: inherit;
  color: var(--pc-text-muted);
  cursor: pointer;
}
.pc-group__add:hover {
  color: var(--pc-primary);
  border-color: var(--pc-primary);
}
</style>
