<template>
  <aside class="rw-sidebar" aria-label="项目与草稿">
    <!-- 顶部项目条（h56）：缩略图 + 名称 + 副标题 + × -->
    <div class="rw-projectbar">
      <img v-if="cover" class="rw-projectbar__thumb" :src="cover" alt="" aria-hidden="true" />
      <span v-else class="rw-projectbar__thumb rw-projectbar__thumb--empty" aria-hidden="true">
        <DocumentOutline class="rw-projectbar__thumb-icon" />
      </span>
      <div class="rw-projectbar__meta">
        <p class="rw-projectbar__name">{{ projectName || '未命名项目' }}</p>
        <p class="rw-projectbar__sub">{{ projectSubtitle }}</p>
      </div>
      <button type="button" class="rw-projectbar__close" aria-label="关闭项目" @click="emit('close')">
        <CloseOutline aria-hidden="true" />
      </button>
    </div>

    <div class="rw-sidebar__body">
      <IconRail v-model="rail" />

      <div class="rw-sidebar__content">
        <!-- 图标轨「项目」= 真实文件树/论文结构；其余项给出诚实占位（不伪造内容，§70） -->
        <template v-if="rail === 'project'">
          <div class="rw-tabrow" role="tablist" aria-label="草稿视图">
            <button
              type="button"
              role="tab"
              class="rw-tabrow__tab"
              :class="{ 'rw-tabrow__tab--active': tab === 'tree' }"
              :aria-selected="tab === 'tree'"
              @click="tab = 'tree'"
            >
              文件树
            </button>
            <button
              type="button"
              role="tab"
              class="rw-tabrow__tab"
              :class="{ 'rw-tabrow__tab--active': tab === 'outline' }"
              :aria-selected="tab === 'outline'"
              @click="tab = 'outline'"
            >
              论文结构
            </button>
            <span class="rw-tabrow__actions">
              <button
                type="button"
                class="rw-tabrow__icon"
                aria-label="新建文件"
                :title="`在 ${createTarget} 下新建文件`"
                @click="onCreate"
              >
                <DocumentOutline aria-hidden="true" />
              </button>
              <button
                type="button"
                class="rw-tabrow__icon"
                aria-label="刷新文件树"
                @click="emit('refresh')"
              >
                <RefreshOutline aria-hidden="true" />
              </button>
            </span>
          </div>

          <div class="rw-sidebar__scroll">
            <FileTree
              v-show="tab === 'tree'"
              ref="treeRef"
              :nodes="tree"
              :active-path="activePath"
              :loading="treeLoading"
              :root-label="projectName"
              @open="(p) => emit('open', p)"
              @create="(p, k) => emit('create', p, k)"
              @rename="(f, t) => emit('rename', f, t)"
              @remove="(p) => emit('remove', p)"
            />

            <!-- 论文结构分区（黄金图：树下方常驻） -->
            <template v-if="tab === 'tree'">
              <div class="rw-outline-head">
                <span class="rw-outline-head__title">论文结构</span>
                <button
                  type="button"
                  class="rw-outline-head__toggle"
                  :aria-label="outlineOpen ? '收起论文结构' : '展开论文结构'"
                  @click="outlineOpen = !outlineOpen"
                >
                  <SwapVerticalOutline aria-hidden="true" />
                </button>
              </div>
              <OutlinePanel
                v-if="outlineOpen"
                :items="outline"
                :loading="outlineLoading"
                @navigate="(f, l) => emit('navigate', f, l)"
              />
            </template>

            <OutlinePanel
              v-else
              :items="outline"
              :loading="outlineLoading"
              @navigate="(f, l) => emit('navigate', f, l)"
            />
          </div>
        </template>

        <!-- 非项目轨：诚实占位（后续轮次接入；点击有真实内容切换，不伪造功能，§70） -->
        <div v-else class="rw-rail-empty">
          <p class="rw-rail-empty__title">{{ RAIL_PLACEHOLDER[rail].title }}</p>
          <p class="rw-rail-empty__desc">{{ RAIL_PLACEHOLDER[rail].desc }}</p>
        </div>
      </div>
    </div>
  </aside>
</template>

<script setup lang="ts">
/**
 * WRITE 左栏容器（宽 306，VISUAL-SPEC §5.1）。
 *
 * 结构：顶部项目条(h56) → 其下两列 = `IconRail`(64px) + 内容列。
 * 内容列：Tab 行（`文件树 | 论文结构`，选中浅蓝底）+ 右侧 2 个图标按钮；
 * 树下方常驻「论文结构」分区（与黄金图一致）。
 *
 * 只向上抛事件，业务由 Shell 落到 store（组件保持无副作用）。
 */
import { computed, ref } from 'vue'
import {
  CloseOutline,
  DocumentOutline,
  RefreshOutline,
  SwapVerticalOutline,
} from '@vicons/ionicons5'
import IconRail, { type RailKey } from './IconRail.vue'
import FileTree from './FileTree.vue'
import OutlinePanel from './OutlinePanel.vue'
import { useProjectCover } from '../shell/projectCover'
import type { FileKind, FileNode, OutlineItem } from '../api/types'

const props = withDefaults(
  defineProps<{
    projectName?: string
    projectSubtitle?: string
    /** 项目 id —— 封面 seed 的规范来源（与顶栏选择器、READ 项目卡一致） */
    projectId?: string
    tree: FileNode[]
    treeLoading?: boolean
    outline: OutlineItem[]
    outlineLoading?: boolean
    activePath?: string | null
    /** 新建文件的默认目录（当前文件所在目录） */
    createDir?: string
  }>(),
  {
    projectName: '',
    projectSubtitle: '学术论文项目 · 进行中',
    projectId: '',
    treeLoading: false,
    outlineLoading: false,
    activePath: null,
    createDir: '',
  },
)

/** 项目封面：取项目第一篇文献的 PDF 首页（`shell/projectCover.ts`）；取不到为 null。 */
const cover = useProjectCover(computed(() => props.projectId ?? ''))

const emit = defineEmits<{
  (e: 'open', path: string): void
  (e: 'create', path: string, kind: FileKind): void
  (e: 'rename', from: string, to: string): void
  (e: 'remove', path: string): void
  (e: 'navigate', file: string, line: number): void
  (e: 'refresh'): void
  (e: 'close'): void
}>()

const rail = ref<RailKey>('project')
const tab = ref<'tree' | 'outline'>('tree')
const outlineOpen = ref(true)
const treeRef = ref<InstanceType<typeof FileTree> | null>(null)

/** 非项目轨的诚实占位文案（后续轮次接入真实内容；§70 禁止伪造）。 */
const RAIL_PLACEHOLDER: Record<Exclude<RailKey, 'project'>, { title: string; desc: string }> = {
  literature: {
    title: '文献面板即将开放',
    desc: '当前版本的文献阅读与导入在「阅读」工作区进行。',
  },
  notes: {
    title: '笔记面板即将开放',
    desc: '当前版本的笔记在「阅读」工作区左侧查看。',
  },
  settings: {
    title: '设置功能即将开放',
    desc: '当前版本暂无可配置项。账号管理请使用右上角头像菜单。',
  },
}

const createTarget = computed(() => {
  if (props.createDir) return props.createDir
  const p = props.activePath
  if (!p) return '项目根目录'
  const i = p.lastIndexOf('/')
  return i > 0 ? p.slice(0, i) : '项目根目录'
})

function onCreate() {
  tab.value = 'tree'
  treeRef.value?.startCreate()
}

/** 供 Shell（编辑器 `＋` 按钮）触发新建文件。 */
function startCreate() {
  onCreate()
}
defineExpose({ startCreate })
</script>

<style scoped>
.rw-sidebar {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
  background: var(--rw-app-bg);
}

/* 项目条 */
.rw-projectbar {
  display: flex;
  align-items: center;
  gap: 9px;
  height: var(--rw-h-projectbar, 56px);
  padding: 0 10px;
  flex: 0 0 auto;
  background: var(--rw-app-bg);
}
.rw-projectbar__thumb {
  width: 38px;
  height: 38px;
  flex: 0 0 auto;
  border-radius: 11px;
  /* 封面 = 项目第一篇文献的 PDF 首页（`shell/projectCover.ts`），
     与顶栏胶囊、READ 项目卡**同一来源**（同一项目两页同封面）。
     取不到时渲染下面的 `--empty` 诚实兜底：**不是**品牌渐变、**不是**风景位图。 */
  object-fit: cover;
  background: var(--rw-surface-2);
}
.rw-projectbar__thumb--empty {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: var(--rw-inset);
  border: var(--rw-border-w) solid var(--rw-border);
}
.rw-projectbar__thumb-icon {
  width: 16px;
  height: 16px;
  color: var(--rw-text-3);
}
.rw-projectbar__meta {
  min-width: 0;
  flex: 1 1 auto;
}
.rw-projectbar__name {
  margin: 0;
  font-size: 13.5px;
  font-weight: 800;
  color: var(--rw-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.rw-projectbar__sub {
  margin: 1px 0 0;
  font-size: var(--rw-fs-sm);
  color: var(--rw-text-3);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.rw-projectbar__close {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  flex: 0 0 auto;
  border: none;
  border-radius: var(--rw-radius-sm);
  background: transparent;
  color: var(--rw-text-3);
  cursor: pointer;
}
.rw-projectbar__close:hover {
  background: var(--rw-surface-2);
}
.rw-projectbar__close :deep(svg) {
  width: 16px;
  height: 16px;
}

.rw-sidebar__body {
  display: flex;
  flex: 1 1 auto;
  min-height: 0;
  border-top: var(--rw-border-w) solid var(--rw-border);
}
.rw-sidebar__content {
  display: flex;
  flex-direction: column;
  flex: 1 1 auto;
  min-width: 0;
  min-height: 0;
}

/* 非项目轨的诚实占位（文献/笔记/设置；§70） */
.rw-rail-empty {
  flex: 1 1 auto;
  min-height: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 4px;
  padding: 16px;
  text-align: center;
}
.rw-rail-empty__title {
  margin: 0;
  font-size: var(--rw-fs-body);
  font-weight: var(--rw-fw-semibold);
  color: var(--rw-text);
}
.rw-rail-empty__desc {
  margin: 0;
  font-size: var(--rw-fs-sm);
  line-height: var(--rw-lh-body);
  color: var(--rw-text-3);
}

/* Tab 行 */
.rw-tabrow {
  display: flex;
  align-items: center;
  gap: 2px;
  height: var(--rw-h-tabbar, 36px);
  padding: 0 6px 0 8px;
  flex: 0 0 auto;
  border-bottom: var(--rw-border-w) solid var(--rw-border);
}
.rw-tabrow__tab {
  height: 28px;
  padding: 0 10px;
  border: 1px solid transparent;
  border-radius: 999px;
  background: transparent;
  color: var(--rw-text-2);
  font-family: var(--rw-font-sans);
  font-size: var(--rw-fs-sm);
  font-weight: 600;
  cursor: pointer;
}
.rw-tabrow__tab--active {
  background: var(--rw-surface);
  border-color: var(--rw-border);
  color: var(--rw-primary);
  font-weight: 800;
  box-shadow: var(--rw-shadow-card);
}
.rw-tabrow__actions {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  margin-left: auto;
}
.rw-tabrow__icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border: none;
  border-radius: var(--rw-radius-sm);
  background: transparent;
  color: var(--rw-text-2);
  cursor: pointer;
}
.rw-tabrow__icon:hover {
  background: var(--rw-surface-2);
  color: var(--rw-primary);
}
.rw-tabrow__icon :deep(svg) {
  width: 15px;
  height: 15px;
}

.rw-sidebar__scroll {
  flex: 1 1 auto;
  min-height: 0;
  overflow: auto;
}

/* 论文结构分区头 */
.rw-outline-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 32px;
  padding: 0 10px;
  border-top: var(--rw-border-w) solid var(--rw-border);
}
.rw-outline-head__title {
  font-size: var(--rw-fs-body);
  font-weight: var(--rw-fw-semibold);
  color: var(--rw-text);
}
.rw-outline-head__toggle {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border: none;
  border-radius: var(--rw-radius-sm);
  background: transparent;
  color: var(--rw-text-3);
  cursor: pointer;
}
.rw-outline-head__toggle:hover {
  background: var(--rw-surface-2);
}
.rw-outline-head__toggle :deep(svg) {
  width: 13px;
  height: 13px;
}
</style>
