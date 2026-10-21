<template>
  <aside class="rw-sb" aria-label="项目侧栏">
    <template v-if="project.current">
      <ProjectIdentityCard
        :name="project.current.name"
        :description="project.current.description"
        :project-id="project.current.id"
      />
      <ProjectOverviewCard
        :summary="project.overview?.summary"
        :stats="project.overview?.stats ?? null"
        :loading="project.loading"
      />
      <PrimaryNav v-model="navKey" />

      <!-- 一级导航切换左栏内容（宪法 §9：文献默认；其余项必须真实切换，禁止摆设） -->
      <!-- 文献：文献列表 + 最近笔记（默认） -->
      <template v-if="navKey === 'literature'">
        <LiteratureList
          :papers="papers.list"
          :active-id="papers.activeId"
          :loading="papers.loading"
          :error="papers.error?.message ?? null"
          @select="onSelect"
          @remove="onRemove"
          @import-file="onImport"
        />
        <RecentNotes
          :notes="notes.recent"
          :loading="notesLoading"
          :expanded="notesExpanded"
          @open="(n) => emit('open-note', n)"
          @more="toggleNotesExpanded"
        />
      </template>

      <!-- 笔记：全部笔记（真实 listNotes，点击回原文） -->
      <template v-else-if="navKey === 'notes'">
        <section class="rw-nav-notes" aria-label="全部笔记">
          <div v-if="notesLoadingAll" class="rw-nav-notes__hint">正在加载笔记…</div>
          <ul v-else-if="notes.list.length" class="rw-nav-notes__list">
            <li v-for="n in notes.list" :key="n.id">
              <button type="button" class="rw-nav-notes__item" :title="n.title" @click="emit('open-note', n)">
                <span class="rw-nav-notes__label">{{ n.title || '未命名笔记' }}</span>
                <span class="rw-nav-notes__time">{{ shortTime(n.updated_at) }}</span>
              </button>
            </li>
          </ul>
          <p v-else class="rw-nav-notes__hint">还没有笔记。阅读时选中文字即可添加。</p>
        </section>
      </template>

      <!-- 项目：项目概览明细（真实数据） -->
      <template v-else-if="navKey === 'project'">
        <section class="rw-nav-project" aria-label="项目信息">
          <p class="rw-nav-project__row"><span class="rw-nav-project__k">项目名称</span>{{ project.current.name }}</p>
          <p class="rw-nav-project__row"><span class="rw-nav-project__k">描述</span>{{ project.current.description || '暂无描述' }}</p>
          <p v-for="s in statRows" :key="s.key" class="rw-nav-project__row">
            <span class="rw-nav-project__k">{{ s.label }}</span>{{ s.value }}
          </p>
        </section>
      </template>

      <!-- 设置：诚实占位（后续轮次开放；§70 禁止伪造设置项） -->
      <template v-else>
        <section class="rw-nav-settings" aria-label="设置">
          <p class="rw-nav-settings__title">设置功能即将开放</p>
          <p class="rw-nav-settings__desc">当前版本暂无可配置项。账号管理请使用右上角头像菜单。</p>
        </section>
      </template>
    </template>

    <div v-else class="rw-sb__empty">
      <RegionSkeleton v-if="project.loading" :rows="4" label="项目加载中" />
      <EmptyState
        v-else
        :title="project.error ? project.error.message : '还没有科研项目'"
        description="创建一个项目来组织你的文献、草稿与笔记。"
        action-text="新建项目"
        @action="emit('create-project')"
      />
    </div>

    <!-- 供 Shell 挂载右缘拖拽把手（本组件不感知布局细节） -->
    <slot />

    <!-- 导入回执 toast：成功/失败都必须有反馈（宪法 §70） -->
    <Transition name="rw-sb-toast-fade">
      <p v-if="importer.toast.value" class="rw-sb__toast" role="status" aria-live="polite">{{ importer.toast.value }}</p>
    </Transition>
  </aside>
</template>

<script setup lang="ts">
/**
 * READ 左栏容器（VISUAL-SPEC §4.1，宽 330px，bg `--app-bg`，右边框 1px）。
 *
 * 五件套顺序固定：项目身份卡 → AI 项目概览卡 → 一级导航 → 文献列表 → 最近笔记。
 * 数据/动作全部走 T02 已交付的 Pinia store；本组件不直连 HTTP。
 * 笔记「最近」列表由本组件负责加载（Shell 的 bootstrap 不含 notes）。
 */
import { computed, onMounted, ref, watch } from 'vue'
import ProjectIdentityCard from './ProjectIdentityCard.vue'
import ProjectOverviewCard from './ProjectOverviewCard.vue'
import PrimaryNav from './PrimaryNav.vue'
import LiteratureList from './LiteratureList.vue'
import RecentNotes from './RecentNotes.vue'
import EmptyState from '../ui/EmptyState.vue'
import RegionSkeleton from '../ui/RegionSkeleton.vue'
import { useRwProjectStore } from '../stores/project'
import { useRwPapersStore } from '../stores/papers'
import { useRwNotesStore } from '../stores/notes'
import { useImportPaper } from './useImportPaper'
import type { Note } from '../api/types'

const props = defineProps<{ projectId: string }>()

const emit = defineEmits<{
  (e: 'open-note', note: Note): void
  (e: 'create-project'): void
}>()

const project = useRwProjectStore()
const papers = useRwPapersStore()
const notes = useRwNotesStore()

/** 文献导入 + 人话回执（toast 见模板底部）。 */
const importer = useImportPaper()

const navKey = ref('literature')
const notesLoading = ref(false)
/** 「更多」展开态：默认最近 6 条，展开后拉全部笔记（真实请求，§70）。 */
const notesExpanded = ref(false)
const notesLoadingAll = ref(false)

/** 项目 Tab 的明细行（真实 overview 数据）。 */
const statRows = computed(() => {
  const s = project.overview?.stats ?? null
  return [
    { key: 'papers', label: '文献', value: s?.papers ?? 0 },
    { key: 'drafts', label: '草稿', value: s?.drafts ?? 0 },
    { key: 'notes', label: '笔记', value: s?.notes ?? 0 },
  ]
})

function shortTime(iso: string): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getMonth() + 1}月${d.getDate()}日 ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

/** 最近笔记「更多 / 收起」：切换加载范围（最近 6 条 ↔ 全量），真实请求 + 可见变化。 */
async function toggleNotesExpanded() {
  notesExpanded.value = !notesExpanded.value
  if (!props.projectId) return
  if (notesExpanded.value) {
    notesLoadingAll.value = true
    try {
      await notes.load(props.projectId)
    } finally {
      notesLoadingAll.value = false
    }
  } else {
    await notes.loadRecent(props.projectId, 6)
  }
}

async function loadNotes(pid: string) {
  if (!pid) {
    notes.reset()
    return
  }
  notesLoading.value = true
  try {
    await notes.loadRecent(pid, 6)
  } finally {
    notesLoading.value = false
  }
}

function onSelect(id: string) {
  papers.setActive(id)
}

async function onRemove(id: string) {
  if (!props.projectId) return
  await papers.remove(props.projectId, id)
  notes.loadRecent(props.projectId, 6)
}

async function onImport(file: File) {
  if (!props.projectId) return
  // 导入 + 人话回执（成功/失败 toast）——与右上「＋」入口共享 useImportPaper
  await importer.run(props.projectId, file)
}

onMounted(() => void loadNotes(props.projectId))
watch(
  () => props.projectId,
  (pid) => void loadNotes(pid),
)
</script>

<style scoped>
.rw-sb {
  display: flex;
  flex-direction: column;
  gap: 12px;
  height: 100%;
  min-height: 0;
  padding: 12px;
  overflow-y: auto;
  overflow-x: hidden;
  background: var(--rw-app-bg);
}
.rw-sb__empty {
  display: flex;
  flex-direction: column;
  justify-content: center;
  flex: 1 1 auto;
}

/* ── 一级导航「笔记」Tab：全部笔记列表 ── */
.rw-nav-notes__hint {
  margin: 4px 2px;
  font-size: var(--rw-fs-sm);
  color: var(--rw-text-3);
}
.rw-nav-notes__list {
  list-style: none;
  margin: 4px 0 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 1px;
}
.rw-nav-notes__item {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  height: 30px;
  padding: 0 4px;
  border: none;
  border-radius: var(--rw-radius-sm);
  background: transparent;
  cursor: pointer;
  text-align: left;
}
.rw-nav-notes__item:hover {
  background: var(--rw-surface-2);
}
.rw-nav-notes__label {
  flex: 1 1 auto;
  min-width: 0;
  font-size: var(--rw-fs-sm);
  color: var(--rw-text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.rw-nav-notes__time {
  flex: 0 0 auto;
  font-size: var(--rw-fs-xs);
  color: var(--rw-text-3);
}

/* ── 一级导航「项目」Tab：项目信息明细（真实数据） ── */
.rw-nav-project {
  padding: 10px;
  border-radius: var(--rw-radius-md);
  background: var(--rw-surface-2);
  border: var(--rw-border-w) solid var(--rw-border);
}
.rw-nav-project__row {
  display: flex;
  gap: 8px;
  margin: 0 0 6px;
  font-size: var(--rw-fs-sm);
  color: var(--rw-text);
  overflow-wrap: anywhere;
}
.rw-nav-project__row:last-child {
  margin-bottom: 0;
}
.rw-nav-project__k {
  flex: 0 0 56px;
  color: var(--rw-text-3);
}

/* ── 一级导航「设置」Tab：诚实占位（功能未开放，§70） ── */
.rw-nav-settings {
  padding: 12px;
  border-radius: var(--rw-radius-md);
  background: var(--rw-surface-2);
  border: var(--rw-border-w) solid var(--rw-border);
}
.rw-nav-settings__title {
  margin: 0 0 4px;
  font-size: var(--rw-fs-body);
  font-weight: var(--rw-fw-semibold);
  color: var(--rw-text);
}
.rw-nav-settings__desc {
  margin: 0;
  font-size: var(--rw-fs-sm);
  line-height: var(--rw-lh-body);
  color: var(--rw-text-3);
}

/* 导入回执 toast（视口底部悬浮；aside 为滚动容器故用 fixed） */
.rw-sb__toast {
  position: fixed;
  left: 190px;
  bottom: 24px;
  z-index: 30;
  max-width: 44vw;
  padding: 10px 16px;
  border-radius: var(--rw-radius-sm, 8px);
  background: var(--rw-surface, #fff);
  border: var(--rw-border-w) solid var(--rw-border);
  box-shadow: 0 8px 24px rgb(0 0 0 / 0.12);
  font-size: var(--rw-fs-body, 14px);
  color: var(--rw-text-1, #1f2329);
}
.rw-sb-toast-fade-enter-active,
.rw-sb-toast-fade-leave-active {
  transition: opacity 0.25s ease;
}
.rw-sb-toast-fade-enter-from,
.rw-sb-toast-fade-leave-to {
  opacity: 0;
}
</style>
