<template>
  <header class="rw-header">
    <!-- 左：Logo + 产品名 + 标语（黄金图：无竖分隔线，实测 10× 放大确认） -->
    <div class="rw-header__left">
      <!-- 三端统一品牌标：学生端同款 π 渐变方块（2026-10-21 视觉统一） -->
      <span class="rw-header__logo-mark" aria-hidden="true">π</span>
      <span class="rw-header__brand">智学数研</span>
      <span class="rw-header__role-pill">科研端</span>
    </div>

    <!-- 阅读 | 写作 分段控件（2026-09-18 用户要求从**栏内**移到**顶栏**、与大标题同排：
         「跟大标题平齐那样，不然会极大的压缩研究者的工作空间」。黄金图的分段控件在栏内，
         本形态是用户指令驱动的**有意偏离**，详见 SegmentedWorkspace.vue 与本节注释。
         放在左簇之后 = 大标题右侧；走与原栏内实例**完全相同**的 update:model-value →
         Shell.onWorkspaceChange 链路，切换只改工作区，不动 Project / Agent 上下文。） -->
    <SegmentedWorkspace
      class="rw-header__seg"
      variant="header"
      :model-value="mode"
      @update:model-value="emit('change-workspace', $event)"
    />

    <!-- 中：按工作区形态分叉（§1.2 + 黄金图实测） -->
    <div class="rw-header__center">
      <!-- READ：胶囊在**视口正中**（黄金图实测胶囊中心 x=836 = 1672/2），
           故用绝对定位居中，而不是在「左簇/右簇之间的剩余空间」里居中。 -->
      <div v-if="mode === 'read'" class="rw-header__pillwrap">
        <ProjectSelector
          :projects="projects"
          :current="project"
          @select="emit('select-project', $event)"
        />
      </div>
      <!-- WRITE：**顶栏中槽为空**（编译控制条不在顶栏 —— 2026-09-18 起它下沉到
           中栏顶部 `.rw-mid__ccb`（与编辑器文件 Tab 条同一行、居右），见 Shell 注释；
           分段控件则上移到左簇之后。黄金图原布局为中栏下一行的「工作区条」。 -->
    </div>

    <!-- 右：同步态（仅 READ）/ 历史 / 通知 / 用户（§1.1 + 黄金图实测） -->
    <div class="rw-header__right">
      <span v-if="mode === 'read'" class="rw-header__sync">
        <CloudDoneOutline class="rw-header__cloud" aria-hidden="true" />
        <span class="rw-header__sync-label">已同步</span>
        <span v-if="syncText" class="rw-header__sync-time">{{ syncText }}</span>
      </span>

      <HeaderHistory :project-id="projectId" @open="emit('locate-paper', $event)" />

      <span class="rw-header__sep" aria-hidden="true" />

      <HeaderNotifications />

      <span class="rw-header__sep" aria-hidden="true" />

      <button
        ref="userBtnRef"
        type="button"
        class="rw-header__user"
        :aria-expanded="menuOpen"
        aria-haspopup="menu"
        @click="toggleMenu"
      >
        <span class="rw-header__avatar" aria-hidden="true">
          <img v-if="userAvatar" :src="userAvatar" alt="" class="rw-header__avatar-img" />
          <span v-else class="rw-header__avatar-txt">{{ avatarChar }}</span>
        </span>
        <span class="rw-header__uname">{{ userName || '未登录' }}</span>
        <ChevronDownOutline class="rw-header__ico" aria-hidden="true" />
      </button>

      <!-- 统一会话用户菜单：账号信息来自统一 auth store（与登录页同一会话），
           提供退出登录（清 access token + 轮换 refresh cookie 失效）→ 回 /login。
           之前此按钮只 emit('user-menu') 且 Shell 绑定 noop，导致科研端没有任何退出入口。 -->
      <div v-if="menuOpen" ref="menuRootRef" class="rw-header__menu" role="menu" aria-label="账号菜单">
        <div class="rw-header__menu-head">
          <span class="rw-header__menu-avatar" aria-hidden="true">
            <img v-if="userAvatar" :src="userAvatar" alt="" class="rw-header__avatar-img" />
            <span v-else class="rw-header__avatar-txt">{{ avatarChar }}</span>
          </span>
          <span class="rw-header__menu-meta">
            <span class="rw-header__menu-name">{{ userName || '未登录' }}</span>
            <span class="rw-header__menu-role">当前身份：{{ roleLabel }}</span>
          </span>
        </div>
        <button type="button" class="rw-header__menu-item" role="menuitem" @click="onAccount">
          个人中心
        </button>
        <button type="button" class="rw-header__menu-item" role="menuitem" @click="onHome">
          科研启动页
        </button>
        <button
          type="button"
          class="rw-header__menu-item"
          role="menuitem"
          :disabled="loggingOut"
          @click="onLogout"
        >
          <LogOutOutline class="rw-header__menu-ico" aria-hidden="true" />
          {{ loggingOut ? '正在退出…' : '退出登录' }}
        </button>
      </div>
    </div>
  </header>
</template>

<script setup lang="ts">
/**
 * 全局顶栏（VISUAL-SPEC §1.1，h56 含 1px 底边框，bg `--rw-header-bg`）。
 *
 * 与黄金图对齐的要点：
 *  - 左：Logo + `智学数研` 22/700 + 标语 `让研究更高效，让思想走更远`（**无竖分隔线** ——
 *    ⚠️ 品牌名按用户指令用「智学数研」与三端统一，偏离黄金图的「科研工作台」（2026-09-18）。 ——
 *    黄金图 10× 放大实测品牌与标语之间为纯白，故不渲染 §1.1 文字描述里的 1px 竖线）；
 *  - 左簇之后：`阅读 | 写作` 分段控件（**2026-09-18 用户要求从栏内上移到顶栏、与大标题平齐**，
 *    `variant="header"` 收紧到 34px；黄金图的原栏内位置为有意偏离，见 SegmentedWorkspace.vue）；
 *  - 中：READ=项目选择器胶囊；WRITE=（空，编译控制条已下沉到中栏顶部，见 Shell 的 `.rw-mid__ccb`）；
 *  - 右：READ=`☁ 已同步 今天 10:24` + `🕘 历史` + `|` + `🔔` + `|` + 头像 + 姓名 + chevron；
 *    WRITE 黄金图**没有同步态**（实测），故同步块仅在 READ 渲染；
 *    两条 1px 竖分隔线出现在「历史｜通知」与「通知｜用户」之间（黄金图 x≈1449 / x≈1506）。
 *
 * 纪律：不出现「学生端/教师端/科研端」切换，不放全局大搜索框（§1.1）；
 * 通知**绝不画假红点** —— 徽标数字取自真实未读数，未读为 0 时不渲染徽标（宪法 §7/§70）。
 */
import {
  CloudDoneOutline,
  ChevronDownOutline,
  LogOutOutline,
} from '@vicons/ionicons5'
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import ProjectSelector from './ProjectSelector.vue'
import SegmentedWorkspace from './SegmentedWorkspace.vue'
import HeaderHistory from './HeaderHistory.vue'
import HeaderNotifications from './HeaderNotifications.vue'
import type { ResearchProject, WorkspaceMode } from '../api/types'
import { useAuthStore } from '@/stores/auth'

const props = withDefaults(
  defineProps<{
    mode: WorkspaceMode
    project: ResearchProject | null
    projects: ResearchProject[]
    /** 当前项目 id（历史面板取真实历史用） */
    projectId?: string
    userName?: string
    /** 真实头像 URL（后端 `avatar_url`；为空时渲染姓名首字） */
    userAvatar?: string
    /** 最近成功同步时间文案（真实；空则不显示） */
    syncText?: string
  }>(),
  {
    projectId: '',
    userName: '',
    userAvatar: '',
    syncText: '',
  },
)

const emit = defineEmits<{
  (e: 'select-project', id: string): void
  (e: 'user-menu'): void
  (e: 'locate-paper', payload: { paperId: string; page: number | null; title: string }): void
  /** 顶栏 `阅读 | 写作` 分段控件切换工作区（与 READ 栏内实例同链路，只改工作区） */
  (e: 'change-workspace', mode: WorkspaceMode): void
}>()

const avatarChar = computed(() => {
  const n = (props.userName || '').trim()
  return n ? n.slice(0, 1) : '—'
})

/* ── 统一会话用户菜单（头像/姓名来自 props（Shell 从统一 auth store 取）；
      身份标签与退出登录直接读统一 auth store，与登录页同一会话来源） ── */
const auth = useAuthStore()
const router = useRouter()
const menuOpen = ref(false)
const loggingOut = ref(false)
const userBtnRef = ref<HTMLButtonElement | null>(null)
const menuRootRef = ref<HTMLDivElement | null>(null)

const ROLE_LABELS: Record<string, string> = {
  student: '学生端',
  teacher: '教师端',
  researcher: '科研端',
  admin: '管理端',
}
const roleLabel = computed(() => {
  const role = auth.activeRole || ''
  return ROLE_LABELS[role] || '未识别身份'
})

function toggleMenu() {
  menuOpen.value = !menuOpen.value
}

function closeMenu() {
  menuOpen.value = false
}

function onAccount() {
  closeMenu()
  void router.push('/research-workspace/account')
}

function onHome() {
  closeMenu()
  void router.push('/research-workspace')
}

/**
 * 退出登录：调用统一会话的 logout（后端使轮换 refresh cookie 失效 + 前端清内存
 * access token），成功或失败都强制整页跳回 /login —— 整页跳转保证科研工作台的
 * 各 pinia store（project/files/agent…）不会残留上一个账号的状态。
 */
async function onLogout() {
  if (loggingOut.value) return
  loggingOut.value = true
  try {
    await auth.logout()
  } catch {
    /* logout 网络失败也继续登出：内存 token 已由 store 清空 */
  } finally {
    window.location.assign('/login')
  }
}

function onDocumentPointerDown(event: MouseEvent): void {
  if (!menuOpen.value) return
  const target = event.target as Node | null
  if (!target) return
  const inButton = userBtnRef.value?.contains(target) ?? false
  const inMenu = menuRootRef.value?.contains(target) ?? false
  if (!inButton && !inMenu) closeMenu()
}

function onDocumentKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape') closeMenu()
}

onMounted(() => {
  document.addEventListener('pointerdown', onDocumentPointerDown)
  document.addEventListener('keydown', onDocumentKeydown)
})

onBeforeUnmount(() => {
  document.removeEventListener('pointerdown', onDocumentPointerDown)
  document.removeEventListener('keydown', onDocumentKeydown)
})
</script>

<style scoped>
.rw-header {
  position: relative;
  z-index: 20;
  display: flex;
  align-items: center;
  gap: 16px;
  height: var(--rw-h-header);
  padding: 0 20px;
  border-bottom: var(--rw-border-w) solid var(--rw-border);
  background: rgba(255, 255, 255, .9);
  backdrop-filter: blur(14px);
  flex: 0 0 auto;
}
.rw-header__left {
  display: flex;
  align-items: center;
  gap: 9px;
  flex: 0 0 auto;
}
/* 与学生端同款 π 品牌标：32×32、9px 圆角、靛蓝渐变 */
.rw-header__logo-mark {
  width: 32px;
  height: 32px;
  flex: 0 0 auto;
  display: grid;
  place-items: center;
  border-radius: 9px;
  background: var(--gradient-brand);
  color: #fff;
  font: italic 900 17px Georgia, "Times New Roman", serif;
  box-shadow: var(--rw-shadow-card);
}
.rw-header__brand {
  font-size: 15px;
  font-weight: 800;
  color: var(--rw-text);
  letter-spacing: .01em;
  white-space: nowrap;
}
.rw-header__role-pill {
  flex: 0 0 auto;
  padding: 2px 8px;
  border: 1px solid var(--rw-border);
  border-radius: 999px;
  background: var(--rw-primary-soft);
  color: var(--rw-primary);
  font-size: 10px;
  font-weight: 800;
  white-space: nowrap;
}
/* 顶栏分段控件（`阅读 | 写作`）：紧接左簇，与 `智学数研` 大标题同一垂直中轴
   （顶栏 `align-items: center`，故其中心线与标题中心线一致 = 用户「跟大标题平齐」）。
   flex 0 0 auto：不参与中间弹性槽的伸缩，也不挤压右侧 388px 功能区。
   2026-09-18 用户要求上移，属**有意偏离黄金图**（黄金图在栏内）。 */
.rw-header__seg {
  flex: 0 0 auto;
  align-self: center;
  margin-left: 6px;
}
.rw-header__center {
  flex: 1 1 auto;
  display: flex;
  align-items: center;
  justify-content: flex-end;
  min-width: 0;
}
/* READ 胶囊：相对**顶栏**居中（视口正中），与黄金图一致 */
.rw-header__pillwrap {
  position: absolute;
  left: 50%;
  top: 50%;
  transform: translate(-50%, -50%);
}
.rw-header__right {
  display: flex;
  align-items: center;
  flex: 0 0 auto;
}
.rw-header__sep {
  width: 1px;
  height: 20px;
  margin: 0 10px;
  background: var(--rw-border);
  flex: 0 0 auto;
}

/* ── 同步态：云图标（主蓝）+ `已同步`（成功绿）+ 时间（三级文本）── */
.rw-header__sync {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  margin-right: 12px;
  padding: 5px 10px;
  border-radius: 999px;
  background: var(--rw-success-soft);
  white-space: nowrap;
}
.rw-header__cloud {
  width: 15px;
  height: 15px;
  color: var(--rw-success);
  flex: 0 0 auto;
}
.rw-header__sync-label {
  font-size: 11px;
  font-weight: 700;
  color: var(--rw-success);
}
.rw-header__sync-time {
  font-size: 10.5px;
  color: var(--rw-text-3);
}

/* ── 用户：头像 + 姓名 + chevron ── */
.rw-header__user {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  height: 38px;
  padding: 3px 12px 3px 3px;
  border: 1px solid var(--rw-border);
  border-radius: 999px;
  background: var(--rw-surface);
  cursor: pointer;
  transition: background-color .16s ease, border-color .16s ease;
}
.rw-header__user:hover {
  background: var(--rw-surface-2);
  border-color: var(--rw-primary-border, var(--rw-border-strong));
}
.rw-header__avatar {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  flex: 0 0 auto;
  border-radius: var(--rw-radius-pill);
  background: var(--gradient-brand);
  overflow: hidden;
}
.rw-header__avatar-img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}
.rw-header__avatar-txt {
  font-size: 12px;
  font-weight: 800;
  color: #fff;
}
.rw-header__uname {
  font-size: var(--rw-fs-h3);
  font-weight: var(--rw-fw-medium);
  color: var(--rw-text);
  white-space: nowrap;
  max-width: 140px;
  overflow: hidden;
  text-overflow: ellipsis;
}
.rw-header__ico {
  width: 16px;
  height: 16px;
  color: var(--rw-text-2);
  flex: 0 0 auto;
}

/* ── 用户菜单（统一会话）：锚定顶栏右下，浮层盖过工作区 ── */
.rw-header__menu {
  position: absolute;
  top: calc(100% + 6px);
  right: var(--rw-header-pad);
  z-index: 60;
  min-width: 232px;
  padding: 6px;
  border: var(--rw-border-w) solid var(--rw-border);
  border-radius: var(--rw-radius-lg);
  background: var(--rw-surface);
  box-shadow: 0 12px 32px rgba(15, 23, 42, .14);
}
.rw-header__menu-head {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 8px 10px;
  border-bottom: var(--rw-border-w) solid var(--rw-border);
  margin-bottom: 6px;
}
.rw-header__menu-avatar {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  flex: 0 0 auto;
  border-radius: var(--rw-radius-pill);
  background: var(--rw-primary-soft);
  overflow: hidden;
}
.rw-header__menu-meta {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.rw-header__menu-name {
  font-size: var(--rw-fs-h3);
  font-weight: var(--rw-fw-semibold);
  color: var(--rw-text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.rw-header__menu-role {
  font-size: var(--rw-fs-xs);
  color: var(--rw-text-3);
  white-space: nowrap;
}
.rw-header__menu-item {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  height: 34px;
  padding: 0 10px;
  border: none;
  border-radius: var(--rw-radius-md);
  background: transparent;
  color: var(--rw-text);
  font-size: var(--rw-fs-sm);
  font-weight: var(--rw-fw-medium);
  cursor: pointer;
  text-align: left;
}
.rw-header__menu-item:hover:not(:disabled) {
  background: var(--rw-surface-2);
}
.rw-header__menu-item:disabled {
  opacity: .6;
  cursor: default;
}
.rw-header__menu-ico {
  width: 16px;
  height: 16px;
  color: var(--rw-text-2);
  flex: 0 0 auto;
}
</style>
