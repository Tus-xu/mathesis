<template>
  <div ref="rootRef" class="rw-psel">
    <button
      ref="capsuleRef"
      type="button"
      class="rw-psel__capsule"
      :aria-expanded="open"
      aria-haspopup="listbox"
      @click="toggle"
    >
      <img v-if="thumb" class="rw-psel__thumb" :src="thumb" alt="" aria-hidden="true" />
      <span v-else class="rw-psel__thumb rw-psel__thumb--empty" aria-hidden="true" />
      <span class="rw-psel__name">{{ current ? current.name : '暂无项目' }}</span>
      <ChevronDownOutline class="rw-psel__chev" aria-hidden="true" />
    </button>

    <!-- 菜单 teleport 到 body：顶栏胶囊外层有 `transform`（居中用）→ 建立层叠上下文，
         若菜单留在原地会被中栏 PDF 文本层（position:absolute; z-index:2）盖住、无法点击。
         teleport + fixed 定位让它进入根层叠上下文，稳定浮于工作区之上。 -->
    <Teleport to="body">
      <div
        v-if="open"
        ref="menuRef"
        class="rw-psel__menu"
        :style="menuStyle"
        role="listbox"
      >
        <p v-if="!projects.length" class="rw-psel__empty">还没有科研项目</p>
        <button
          v-for="p in projects"
          :key="p.id"
          type="button"
          role="option"
          class="rw-psel__item"
          :class="{ 'rw-psel__item--active': current && p.id === current.id }"
          :aria-selected="current ? p.id === current.id : false"
          @click="pick(p.id)"
        >
          <img
            v-if="thumbFor(p.id)"
            class="rw-psel__item-thumb"
            :src="thumbFor(p.id)!"
            alt=""
            aria-hidden="true"
          />
          <span v-else class="rw-psel__item-thumb rw-psel__item-thumb--empty" aria-hidden="true" />
          <span class="rw-psel__item-name">{{ p.name }}</span>
        </button>

        <!-- 重命名项目：与左栏就地编辑共用同一保存逻辑（store.updateProject）——宪法 §70 禁摆设动作 -->
        <div v-if="current" class="rw-psel__footer" role="presentation">
          <button v-if="!renaming" type="button" class="rw-psel__action" @click="startRename">
            <CreateOutline class="rw-psel__action-ico" aria-hidden="true" />
            重命名项目
          </button>
          <div v-else class="rw-psel__rename">
            <input
              ref="renameInputRef"
              v-model="draftName"
              class="rw-psel__rename-input"
              type="text"
              maxlength="120"
              aria-label="项目名称"
              @keydown.enter.prevent="commitRename"
              @keydown.esc.prevent="cancelRename"
            />
            <button
              type="button"
              class="rw-psel__rename-save"
              :disabled="store.saving"
              @click="commitRename"
            >
              {{ store.saving ? '保存中…' : '保存' }}
            </button>
            <button
              type="button"
              class="rw-psel__rename-cancel"
              :disabled="store.saving"
              @click="cancelRename"
            >
              取消
            </button>
            <p v-if="renameError" class="rw-psel__rename-err" role="status" aria-live="polite">
              {{ renameError }}
            </p>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<script setup lang="ts">
/**
 * READ 顶栏中槽 · 项目选择器胶囊（VISUAL-SPEC §1.2）。
 * ≈300×34，radius8，bg `--rw-primary-softer`，border 1px `--rw-border-strong`；
 * 内容：24×24 缩略图 + 项目名 14/600 + chevron。
 *
 * 缩略图是**真实封面位图**（`projectCover.ts`），不再是 `linear-gradient` 纯蓝方块
 * —— 后者是探针里唯一的 `banned.gradient` 命中项（VISUAL-SPEC §7-1）。
 * 多项目：菜单列出 `projects` 全部项目，选中即切换（宪法 §70 禁「永远只有一个项目」）。
 *
 * **重命名项目**（本轮新增）：菜单底部动作 → 内联输入 → 保存，走
 * `useRwProjectStore().updateProject`（与左栏 `ProjectIdentityCard` 就地编辑共用同一
 * 保存逻辑：新的 `client_request_id` + 真实 `expected_revision`）。成功后顶栏胶囊名称
 * 立即更新（store 就地改写 `current`）。空名称前端拦下，不发非法请求。
 *
 * 菜单经 `Teleport to body` + `position:fixed`：绕过顶栏胶囊的 `transform` 层叠上下文，
 * 否则菜单会被中栏 PDF 文本层盖住而无法点击。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ChevronDownOutline, CreateOutline } from '@vicons/ionicons5'
import { getProjectCover, useProjectCover } from './projectCover'
import { useRwProjectStore } from '../stores/project'
import type { ResearchProject } from '../api/types'

const props = defineProps<{
  projects: ResearchProject[]
  current: ResearchProject | null
}>()
const emit = defineEmits<{ (e: 'select', id: string): void }>()

const store = useRwProjectStore()

/** 当前项目封面：取项目第一篇文献的 PDF 首页；取不到为 null → 中性兜底。 */
const thumb = useProjectCover(computed(() => props.current?.id ?? ''))

/** 菜单里每个项目的缩略图（懒加载 + 缓存，`getProjectCover` 内部已去重）。 */
const menuThumbs = ref<Record<string, string | null>>({})
function thumbFor(id: string): string | null {
  if (!(id in menuThumbs.value)) {
    menuThumbs.value[id] = null
    void getProjectCover(id).then((v) => {
      menuThumbs.value = { ...menuThumbs.value, [id]: v }
    })
  }
  return menuThumbs.value[id] ?? null
}

const open = ref(false)
const rootRef = ref<HTMLElement | null>(null)
const capsuleRef = ref<HTMLButtonElement | null>(null)
const menuRef = ref<HTMLElement | null>(null)
/** 菜单为 fixed 定位（teleport 到 body），位置由胶囊矩形实时计算。 */
const menuStyle = ref<Record<string, string>>({})

/* ── 重命名项目（与左栏就地编辑共享 store.updateProject） ── */
const renaming = ref(false)
const draftName = ref('')
const renameError = ref('')
const renameInputRef = ref<HTMLInputElement | null>(null)

function updateMenuPos() {
  const el = capsuleRef.value
  if (!el) return
  const r = el.getBoundingClientRect()
  menuStyle.value = {
    position: 'fixed',
    top: `${Math.round(r.bottom + 6)}px`,
    left: `${Math.round(r.left)}px`,
    width: '300px',
  }
}

function toggle() {
  open.value = !open.value
}
function pick(id: string) {
  open.value = false
  if (!props.current || props.current.id !== id) emit('select', id)
}

function startRename() {
  if (!props.current || store.saving) return
  renaming.value = true
  draftName.value = props.current.name
  renameError.value = ''
  nextTick(() => {
    renameInputRef.value?.focus()
    renameInputRef.value?.select()
  })
}

function cancelRename() {
  renaming.value = false
  draftName.value = ''
  renameError.value = ''
}

async function commitRename() {
  const c = props.current
  if (!c) return
  const value = draftName.value.trim()
  if (!value) {
    renameError.value = '项目名称不能为空。'
    nextTick(() => renameInputRef.value?.focus())
    return
  }
  if (value === c.name) {
    renaming.value = false
    return
  }
  renameError.value = ''
  const updated = await store.updateProject(c.id, { name: value })
  if (updated) {
    renaming.value = false
    open.value = false
  } else {
    renameError.value = store.error?.message || '保存失败，请稍后重试。'
  }
}

/** 菜单开合：定位 + 监听视口变化；关闭时复位重命名态。 */
watch(open, (v) => {
  if (v) {
    updateMenuPos()
    window.addEventListener('resize', updateMenuPos)
    window.addEventListener('scroll', updateMenuPos, true)
  } else {
    window.removeEventListener('resize', updateMenuPos)
    window.removeEventListener('scroll', updateMenuPos, true)
    cancelRename()
  }
})

function onDocClick(e: MouseEvent) {
  if (!open.value) return
  const target = e.target as Node
  if (rootRef.value?.contains(target)) return
  if (menuRef.value?.contains(target)) return
  open.value = false
}
onMounted(() => document.addEventListener('mousedown', onDocClick))
onBeforeUnmount(() => {
  document.removeEventListener('mousedown', onDocClick)
  window.removeEventListener('resize', updateMenuPos)
  window.removeEventListener('scroll', updateMenuPos, true)
})
</script>

<style scoped>
.rw-psel {
  position: relative;
}
.rw-psel__capsule {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  width: 260px;
  height: 32px;
  padding: 0 10px;
  border: 1px solid var(--rw-border);
  border-radius: 999px;
  background: var(--rw-surface);
  box-shadow: var(--rw-shadow-card);
  cursor: pointer;
}
.rw-psel__capsule:hover { border-color: var(--rw-primary-border, var(--rw-border-strong)); }
.rw-psel__thumb {
  width: 22px;
  height: 22px;
  border-radius: 7px;
  object-fit: cover;
  flex: 0 0 auto;
}
/* 无真实封面时的诚实兜底：中性浅底 + 细描边（不是品牌渐变、不是风景位图） */
.rw-psel__thumb--empty,
.rw-psel__item-thumb--empty {
  display: inline-block;
  background: var(--rw-inset);
  border: var(--rw-border-w) solid var(--rw-border);
}
.rw-psel__name {
  flex: 1 1 auto;
  overflow: hidden;
  text-align: left;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12.5px;
  font-weight: 700;
  color: var(--rw-text);
}
.rw-psel__chev {
  width: 15px;
  height: 15px;
  color: var(--rw-text-2);
  flex: 0 0 auto;
}
/* 菜单为 fixed（teleport 到 body，位置由胶囊矩形计算）—— z-index 高于 PDF 文本层 */
.rw-psel__menu {
  position: fixed;
  max-height: 60vh;
  overflow-y: auto;
  padding: 6px;
  border: var(--rw-border-w) solid var(--rw-border);
  border-radius: var(--rw-radius-md);
  background: var(--rw-surface);
  box-shadow: var(--rw-shadow-pop);
  z-index: 1000;
}
.rw-psel__empty {
  margin: 0;
  padding: 10px 8px;
  font-size: var(--rw-fs-sm);
  color: var(--rw-text-3);
}
.rw-psel__item {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  height: 34px;
  padding: 0 8px;
  border: none;
  border-radius: var(--rw-radius);
  background: transparent;
  cursor: pointer;
  text-align: left;
}
.rw-psel__item:hover {
  background: var(--rw-surface-2);
}
.rw-psel__item--active {
  background: var(--rw-primary-soft);
}
.rw-psel__item-thumb {
  width: 20px;
  height: 20px;
  border-radius: var(--rw-radius-sm);
  object-fit: cover;
  flex: 0 0 auto;
}
.rw-psel__item-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: var(--rw-fs-body);
  color: var(--rw-text);
}

/* ── 菜单底部「重命名项目」动作 ── */
.rw-psel__footer {
  margin-top: 6px;
  padding-top: 6px;
  border-top: var(--rw-border-w) solid var(--rw-border);
}
.rw-psel__action {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  height: 32px;
  padding: 0 8px;
  border: none;
  border-radius: var(--rw-radius);
  background: transparent;
  color: var(--rw-text);
  font-size: var(--rw-fs-sm);
  font-weight: var(--rw-fw-medium);
  cursor: pointer;
  text-align: left;
}
.rw-psel__action:hover {
  background: var(--rw-surface-2);
}
.rw-psel__action-ico {
  width: 15px;
  height: 15px;
  color: var(--rw-text-2);
  flex: 0 0 auto;
}
.rw-psel__rename {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  padding: 2px;
}
.rw-psel__rename-input {
  flex: 1 1 auto;
  min-width: 0;
  height: 30px;
  padding: 0 8px;
  border: var(--rw-border-w) solid var(--rw-border-strong);
  border-radius: var(--rw-radius-sm);
  background: var(--rw-surface);
  color: var(--rw-text);
  font-size: var(--rw-fs-sm);
}
.rw-psel__rename-input:focus {
  outline: none;
  border-color: var(--rw-primary);
  box-shadow: 0 0 0 2px color-mix(in srgb, var(--rw-primary) 18%, transparent);
}
.rw-psel__rename-save,
.rw-psel__rename-cancel {
  height: 30px;
  padding: 0 10px;
  border-radius: var(--rw-radius-sm);
  font-size: var(--rw-fs-sm);
  cursor: pointer;
}
.rw-psel__rename-save {
  border: none;
  background: var(--rw-primary);
  color: var(--rw-surface);
  font-weight: var(--rw-fw-semibold);
}
.rw-psel__rename-save:disabled {
  opacity: 0.6;
  cursor: default;
}
.rw-psel__rename-cancel {
  border: var(--rw-border-w) solid var(--rw-border-strong);
  background: var(--rw-surface);
  color: var(--rw-text);
}
.rw-psel__rename-cancel:disabled {
  opacity: 0.6;
  cursor: default;
}
.rw-psel__rename-err {
  flex: 1 1 100%;
  margin: 2px 0 0;
  font-size: var(--rw-fs-xs);
  color: var(--rw-danger);
}
</style>
