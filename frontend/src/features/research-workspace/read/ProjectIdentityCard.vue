<template>
  <section class="rw-idc" aria-label="项目信息">
    <img v-if="thumb" class="rw-idc__thumb" :src="thumb" alt="" aria-hidden="true" />
    <!-- 无真实封面时的**诚实兜底**：浅底 + 文档图标。
         不再用打包风景位图（会读成"这是项目封面照片"，违 §70 假 UI 精神），
         也不用品牌渐变（会读成"没做完"）。 -->
    <span v-else class="rw-idc__thumb rw-idc__thumb--empty" aria-hidden="true">
      <DocumentOutline class="rw-idc__thumb-icon" />
    </span>
    <div class="rw-idc__meta">
      <!-- 项目名：点击就地编辑（Enter 保存 / Esc 取消） -->
      <input
        v-if="editing === 'name'"
        ref="nameInputRef"
        v-model="draftName"
        class="rw-idc__input rw-idc__input--name"
        type="text"
        maxlength="120"
        aria-label="项目名称"
        @keydown.enter.prevent="commitName(false)"
        @keydown.esc.prevent="cancelEdit"
        @blur="commitName(true)"
      />
      <button
        v-else
        type="button"
        class="rw-idc__name"
        :title="name ? `点击重命名：${name}` : '点击为项目命名'"
        @click="startEdit('name')"
      >
        {{ name || '未命名项目' }}
      </button>

      <!-- 描述：点击就地编辑（Enter 保存 / Esc 取消） -->
      <input
        v-if="editing === 'desc'"
        ref="descInputRef"
        v-model="draftDesc"
        class="rw-idc__input rw-idc__input--desc"
        type="text"
        maxlength="500"
        aria-label="项目描述"
        @keydown.enter.prevent="commitDesc(false)"
        @keydown.esc.prevent="cancelEdit"
        @blur="commitDesc(true)"
      />
      <button
        v-else
        type="button"
        class="rw-idc__desc"
        :title="description ? `点击编辑描述：${description}` : '点击添加描述'"
        @click="startEdit('desc')"
      >
        {{ description || '暂无描述' }}
      </button>

      <!-- 保存状态：进行中 / 成功 / 失败（人话）——禁止静默 no-op（宪法 §70） -->
      <p
        v-if="status"
        class="rw-idc__status"
        :class="`rw-idc__status--${status.kind}`"
        role="status"
        aria-live="polite"
      >
        {{ status.text }}
      </p>
    </div>
  </section>
</template>

<script setup lang="ts">
/**
 * 项目身份卡（VISUAL-SPEC §4.1-1）：h≈62、bg `--rw-surface-2`、radius 8、padding 10。
 * 缩略图 44×44 radius 6；项目名 15/600；描述 12/400 单行省略。
 *
 * 缩略图取**项目第一篇文献的 PDF 首页**（`shell/projectCover.ts`）——
 * 这是黄金图那个"封面"的真实语义。取不到时渲染浅底 + 文档图标的诚实空态。
 *
 * **就地编辑**（本轮新增）：点击项目名 / 描述 → 输入框 → Enter 保存 / Esc 取消；
 * 保存走 `useRwProjectStore().updateProject`（与顶栏「重命名项目」共用同一逻辑），
 * 成功后顶栏名称与本卡**立即更新**（store 就地改写 current/list）。空名称在前端拦下，
 * 不发非法请求（后端 min_length=1）。全程有「正在保存… / 已保存 / 失败人话」回执。
 */
import { computed, nextTick, ref, toRef } from 'vue'
import { DocumentOutline } from '@vicons/ionicons5'
import { useProjectCover } from '../shell/projectCover'
import { useRwProjectStore } from '../stores/project'

const props = withDefaults(
  defineProps<{
    name?: string
    description?: string | null
    /**
     * 项目 id —— **封面 seed 的规范来源**。
     * 必须与顶栏项目选择器（`ProjectSelector` 用 `p.id`）一致，
     * 否则同一项目在顶栏胶囊与左栏项目卡会拿到**不同封面**。
     */
    projectId?: string
    /** 封面图 URL（后端 `cover_url`；暂未提供，传入即优先使用） */
    cover?: string
  }>(),
  { name: '', description: '', projectId: '', cover: '' },
)

const store = useRwProjectStore()

/** 真实封面：项目第一篇文献的 PDF 首页（取不到为 null → 渲染诚实兜底）。 */
const thumb = useProjectCover(toRef(props, 'projectId'))

type EditField = 'name' | 'desc'
type StatusKind = 'saving' | 'saved' | 'error'

const editing = ref<EditField | null>(null)
const draftName = ref('')
const draftDesc = ref('')
const nameInputRef = ref<HTMLInputElement | null>(null)
const descInputRef = ref<HTMLInputElement | null>(null)
const status = ref<{ kind: StatusKind; text: string } | null>(null)
let statusTimer: ReturnType<typeof setTimeout> | null = null

/** Esc 取消后紧跟的 blur 不应再提交。 */
let suppressBlur = false

function setStatus(kind: StatusKind, text: string, autoClearMs = 0) {
  status.value = { kind, text }
  if (statusTimer) clearTimeout(statusTimer)
  if (autoClearMs > 0) {
    statusTimer = setTimeout(() => {
      status.value = null
      statusTimer = null
    }, autoClearMs)
  }
}

function startEdit(field: EditField) {
  if (store.saving) return
  suppressBlur = false
  editing.value = field
  if (field === 'name') draftName.value = props.name ?? ''
  else draftDesc.value = props.description ?? ''
  status.value = null
  nextTick(() => {
    const el = field === 'name' ? nameInputRef.value : descInputRef.value
    el?.focus()
    el?.select()
  })
}

function cancelEdit() {
  suppressBlur = true
  editing.value = null
  draftName.value = ''
  draftDesc.value = ''
}

/** 唯一保存入口：调用 store（生成新的幂等键 + 真 revision），并给出人话回执。 */
async function persist(patch: { name?: string; description?: string | null }): Promise<void> {
  if (!props.projectId) {
    setStatus('error', '项目尚未就绪，请稍后重试。', 3200)
    return
  }
  setStatus('saving', '正在保存…')
  const updated = await store.updateProject(props.projectId, patch)
  if (updated) {
    setStatus('saved', '已保存', 1800)
  } else {
    setStatus('error', store.error?.message || '保存失败，请稍后重试。', 3200)
  }
}

async function commitName(viaBlur: boolean) {
  if (editing.value !== 'name') return
  if (suppressBlur) {
    suppressBlur = false
    return
  }
  const value = draftName.value.trim()
  if (!value) {
    if (viaBlur) {
      editing.value = null
      draftName.value = ''
      setStatus('error', '项目名称不能为空，未保存。', 3200)
      return
    }
    setStatus('error', '项目名称不能为空。', 3200)
    nextTick(() => nameInputRef.value?.focus())
    return
  }
  // 先退出编辑态，避免随后 blur 二次提交。
  editing.value = null
  if (value === (props.name ?? '')) {
    setStatus('saved', '已保存', 1500)
    return
  }
  await persist({ name: value })
}

async function commitDesc(viaBlur: boolean) {
  if (editing.value !== 'desc') return
  if (suppressBlur) {
    suppressBlur = false
    return
  }
  const value = draftDesc.value.trim()
  const current = props.description ?? ''
  editing.value = null
  if (value === current) {
    setStatus('saved', '已保存', 1500)
    return
  }
  if (value.length > 500) {
    setStatus('error', '项目描述过长（最多 500 字），未保存。', 3200)
    return
  }
  void viaBlur
  await persist({ description: value })
}

const statusText = computed(() => status.value?.text ?? '')

defineExpose({ statusText })
</script>

<style scoped>
.rw-idc {
  display: flex;
  align-items: center;
  gap: 11px;
  min-height: 66px;
  padding: 11px;
  border-radius: 16px;
  background:
    linear-gradient(135deg, rgba(238, 242, 255, .65), rgba(255, 255, 255, .95) 58%),
    var(--rw-surface);
  border: 1px solid var(--rw-border);
  box-shadow: var(--rw-shadow-card);
}
.rw-idc__thumb {
  width: 46px;
  height: 46px;
  flex: 0 0 auto;
  border-radius: 14px;
  object-fit: cover;
  border: 1px solid rgba(255, 255, 255, .7);
}
/* 无真实封面时的诚实兜底：中性浅底 + 文档图标（**不是**品牌渐变、**不是**风景位图） */
.rw-idc__thumb--empty {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: var(--rw-inset);
  border: var(--rw-border-w) solid var(--rw-border);
}
.rw-idc__thumb-icon {
  width: 20px;
  height: 20px;
  color: var(--rw-text-3);
}
.rw-idc__meta {
  flex: 1 1 auto;
  min-width: 0;
}
/* 名称 / 描述：默认为可点击的文本按钮（点击进入就地编辑） */
.rw-idc__name,
.rw-idc__desc {
  display: block;
  width: 100%;
  margin: 0;
  padding: 0;
  border: none;
  background: transparent;
  text-align: left;
  cursor: text;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.rw-idc__name {
  font-size: 14.5px;
  font-weight: 800;
  color: var(--rw-text);
}
.rw-idc__desc {
  margin-top: 3px;
  font-size: var(--rw-fs-sm);
  color: var(--rw-text-2);
}
.rw-idc__name:hover,
.rw-idc__desc:hover {
  text-decoration: underline dotted color-mix(in srgb, var(--rw-primary) 55%, transparent);
  text-underline-offset: 2px;
}
/* 就地编辑输入框 */
.rw-idc__input {
  display: block;
  width: 100%;
  padding: 1px 4px;
  border: var(--rw-border-w) solid var(--rw-border-strong);
  border-radius: var(--rw-radius-sm);
  background: var(--rw-surface);
  color: var(--rw-text);
  font-family: inherit;
}
.rw-idc__input:focus {
  outline: none;
  border-color: var(--rw-primary);
  box-shadow: 0 0 0 2px color-mix(in srgb, var(--rw-primary) 18%, transparent);
}
.rw-idc__input--name {
  font-size: var(--rw-fs-h2);
  font-weight: var(--rw-fw-semibold);
}
.rw-idc__input--desc {
  margin-top: 3px;
  font-size: var(--rw-fs-sm);
}
/* 保存状态回执 */
.rw-idc__status {
  margin: 3px 0 0;
  font-size: var(--rw-fs-xs);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.rw-idc__status--saving {
  color: var(--rw-text-3);
}
.rw-idc__status--saved {
  color: var(--rw-success);
}
.rw-idc__status--error {
  color: var(--rw-danger);
}
</style>
