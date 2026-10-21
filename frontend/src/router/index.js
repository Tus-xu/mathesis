import { createRouter, createWebHistory } from 'vue-router'
import { beginRouteLoading, endRouteLoading } from '@/composables/routeLoading'
import { useAuthStore } from '@/stores/auth'
import { useToastStore } from '@/stores/toast'
import { registerResearchWorkspace } from '@/features/research-workspace/route/registerResearchWorkspace'
import { registerPrepCenter } from '@/features/teacher-prep-center/route/registerPrepCenter'

const studentMeta = (title) => ({ title, requiresRole: 'student' })
const studentRoutes = [
  // ===== 第二轮布局重构：6 大入口 + 旧地址全量重定向（V2 文档 §6 强制） =====
  { path: '/dialog', component: () => import('@/pages/student/DialogView.vue'), meta: studentMeta('首页 · 对话学习') },
  { path: '/dialog/:id?', component: () => import('@/pages/student/DialogView.vue'), meta: studentMeta('首页 · 对话学习') },
  { path: '/practice', component: () => import('@/pages/student/PracticeView.vue'), meta: studentMeta('练题') },
  { path: '/errors', component: () => import('@/pages/student/ErrorsView.vue'), meta: studentMeta('错题') },
  { path: '/library', component: () => import('@/pages/student/LibraryHomeView.vue'), meta: studentMeta('知识库') },
  { path: '/resources', component: () => import('@/pages/student/ResourceCenterView.vue'), meta: studentMeta('资源中心') },
  { path: '/classroom', component: () => import('@/pages/student/ClassroomHomeView.vue'), meta: studentMeta('课堂') },
  { path: '/me', component: () => import('@/pages/student/MeView.vue'), meta: studentMeta('我的') },
  { path: '/exam/:id', component: () => import('@/pages/student/ExamPaperView.vue'), meta: studentMeta('模考作答') },
  { path: '/tasks/:id', component: () => import('@/pages/student/AssignmentView.vue'), meta: studentMeta('作业作答') },
  { path: '/dual', component: () => import('@/pages/student/DualView.vue'), meta: { ...studentMeta('双师课堂'), immersive: true } },
  { path: '/dual/:sessionId', component: () => import('@/pages/student/DualView.vue'), meta: { ...studentMeta('双师课堂'), immersive: true } },
  // 旧地址重定向（兼容历史深链与已发出的通知/作业路由）
  { path: '/overview', redirect: '/me?tab=report' },
  { path: '/report', redirect: '/me?tab=report' },
  { path: '/graph', redirect: '/me?tab=graph' },
  { path: '/profile', redirect: '/me?tab=profile' },
  { path: '/exam', redirect: '/practice?tab=exam' },
  { path: '/resource', redirect: '/resources' },
  { path: '/kb', redirect: '/library?tab=materials' },
  { path: '/class', redirect: '/classroom?tab=class' },
  { path: '/tasks', redirect: '/classroom?tab=tasks' },
]

const routes = [
  { path: '/login', name: 'login', component: () => import('@/pages/Login.vue'), meta: { public: true, authFlow: true } },
  { path: '/register', name: 'register', component: () => import('@/pages/Register.vue'), meta: { public: true, authFlow: true } },
  { path: '/account/password', component: () => import('@/pages/PasswordReset.vue'), meta: { public: true, authFlow: true } },
  { path: '/account/deletion/cancel', component: () => import('@/pages/DeletionCancellation.vue'), meta: { public: true, authFlow: true } },
  { path: '/onboarding/student', component: () => import('@/pages/StudentOnboarding.vue'), meta: { authFlow: true } },
  { path: '/identity/apply', component: () => import('@/pages/RoleApplication.vue'), meta: { authFlow: true } },
  { path: '/identity/pending', component: () => import('@/pages/PendingReview.vue'), meta: { authFlow: true } },
  { path: '/account/security', component: () => import('@/pages/AccountSecurity.vue'), meta: { authFlow: true } },
  { path: '/admin/identity/applications', component: () => import('@/pages/admin/AdminIdentityReview.vue'), meta: { authFlow: true, admin: true, requiresRole: 'admin' } },
  /* ===== 科研端：新科研工作台 `/research-workspace` 为唯一落地 =====
   *
   * 历史：旧 `/research/*` 六页工作台按 RV-UX0 冻结契约退役后，被 RV16 的
   * paper-workbench 接管（`/research` → `/research-vnext/session` → 前置守卫改写 →
   * `/research-vnext/workbench`）。该工作台渲染的是旧「表单式」页面，与已冻结的
   * 新工作台并存且抢默认落地 —— P1 已将其整体退役（`VITE_PAPER_WORKBENCH=1` 可回滚）。
   *
   * 现在：以下旧入口一律收敛到 `/research-workspace`，保留 path 与 name 使外部深链 /
   * 书签 / 已发出的通知不 404，路由名继续可用（`router.push({name:'research'})` 不会炸）。
   * 旧页面组件**不删除**，仍保留在磁盘上，仅不再是活跃目标。 */
  { path: '/research', name: 'research', redirect: '/research-workspace', meta: { research: true, requiresRole: 'researcher', title: '科研端' } },
  { path: '/research/library', name: 'research-library', redirect: '/research-workspace', meta: { research: true, requiresRole: 'researcher', title: '文献库' } },
  { path: '/research/import', name: 'research-import', redirect: '/research-workspace', meta: { research: true, requiresRole: 'researcher', title: '收录论文' } },
  { path: '/research/reader/:paperId', name: 'research-reader', redirect: '/research-workspace', meta: { research: true, requiresRole: 'researcher', title: '阅读器' } },
  { path: '/research/writing', name: 'research-writing', redirect: '/research-workspace', meta: { research: true, requiresRole: 'researcher', title: '写作' } },
  { path: '/research/writing/:manuscriptId', name: 'research-writing-id', redirect: '/research-workspace', meta: { research: true, requiresRole: 'researcher', title: '写作' } },
  { path: '/research/tasks', name: 'research-tasks', redirect: '/research-workspace', meta: { research: true, requiresRole: 'researcher', title: '任务中心' } },
  { path: '/', redirect: '/dialog' },  // S1：首页即对话学习（V2 文档原则一）
  ...studentRoutes,
  // Teacher session is a focused surface: it must not inherit the legacy navigation shell.
  // The route is intentionally additive so historical Prep/Slides URLs remain stable.
  {
    path: '/teacher-v3/prep/session/:sessionId',
    name: 'tv3-prep-session',
    component: () => import('@/features/teacher-prep-v2/surface/PrepSurfaceRoute.vue'),
    meta: { teacher: true, requiresRole: 'teacher', scene: 'teacher.v3.prep.session', title: '备课会话' },
  },
  /* ===== 教师工作台 V3（V2.1 SPEC 落地：数学编辑内核 + 五区编辑器 + 拍照链路 + 批改三视图） ===== */
  {
    path: '/teacher-v3',
    name: 'tv3-layout',
    component: () => import('@/layouts/TeacherV3Layout.vue'),
    children: [
      { path: 'today', name: 'tv3-today', redirect: '/teacher-v3/prep', meta: { teacher: true, teacherV3: true, requiresRole: 'teacher', scene: 'teacher.v3.today', title: '备课中心' } },
      /* 备课中心首页（G1 外框不变量）：挂进 TeacherV3Layout —— 平台侧栏/顶栏/悬浮 AI 助教
         在每个备课页可见；备课自己的顶栏与左导航保留为外框内容区内部组件。
         路径/名称保持不变，历史深链与 roleHome('teacher') 落点不受影响；
         工作台 /teacher-v3/prep/w/** 与 /history 由 registerPrepCenter 挂到本父路由。 */
      { path: 'prep', name: 'tv3-prep', component: () => import('@/features/teacher-prep-center/home/PrepHome.vue'), meta: { teacher: true, teacherV3: true, requiresRole: 'teacher', scene: 'teacher.v3.prep', title: '备课中心' } },
      { path: 'prep-templates', name: 'tv3-prep-templates', component: () => import('@/pages/teacher-v3/prep/PrepTemplatesView.vue'), meta: { teacher: true, teacherV3: true, requiresRole: 'teacher', scene: 'teacher.v3.prep.templates', title: '教案模板库' } },
      /* Checkpoint 9：恢复成熟旧课件编辑器作为正式 Teacher UI；VNext deck kernel 通过 adapter 接入。 */
      { path: 'slides', name: 'tv3-slides', component: () => import('@/pages/teacher-v3/SlidesCompatRedirect.vue'), meta: { teacher: true, teacherV3: true, requiresRole: 'teacher', scene: 'teacher.v3.slides', title: '备课产物兼容入口' } },
      { path: 'bank', name: 'tv3-bank', component: () => import('@/pages/teacher-v3/BankView.vue'), meta: { teacher: true, teacherV3: true, requiresRole: 'teacher', scene: 'teacher.v3.bank', title: '题库' } },
      { path: 'quiz', name: 'tv3-quiz', component: () => import('@/pages/teacher-v3/QuizView.vue'), meta: { teacher: true, teacherV3: true, requiresRole: 'teacher', scene: 'teacher.v3.quiz', title: '组卷中心' } },
      { path: 'assign', name: 'tv3-assign', component: () => import('@/pages/teacher-v3/AssignView.vue'), meta: { teacher: true, teacherV3: true, requiresRole: 'teacher', scene: 'teacher.v3.assign', title: '作业与批改' } },
      /* tel PLAN §1.2①③：班级管理（建班/成员确认）与批改工作台（题聚焦/原图/终审）独立成页 */
      { path: 'classes', name: 'tv3-classes', component: () => import('@/pages/teacher-v3/ClassesView.vue'), meta: { teacher: true, teacherV3: true, requiresRole: 'teacher', scene: 'teacher.v3.classes', title: '班级管理' } },
      { path: 'grading', name: 'tv3-grading', component: () => import('@/pages/teacher-v3/GradingWorkspaceView.vue'), meta: { teacher: true, teacherV3: true, requiresRole: 'teacher', scene: 'teacher.v3.grading', title: '批改工作台' } },
      { path: 'classroom', name: 'tv3-classroom', component: () => import('@/pages/teacher-v3/ClassroomView.vue'), meta: { teacher: true, teacherV3: true, requiresRole: 'teacher', scene: 'teacher.v3.classroom', title: '课堂互动' } },
      { path: 'insights', name: 'tv3-insights', component: () => import('@/pages/teacher-v3/InsightsView.vue'), meta: { teacher: true, teacherV3: true, requiresRole: 'teacher', scene: 'teacher.v3.insights', title: '学情洞察' } },
      { path: 'resources', name: 'tv3-resources', component: () => import('@/pages/teacher-v3/ResourcesView.vue'), meta: { teacher: true, teacherV3: true, requiresRole: 'teacher', scene: 'teacher.v3.resources', title: '资源中心' } },
    ],
  },
  /* 40301 role_denied 统一提示页（A0 M1-2）：不带 teacherV3/teacher meta，避免破坏路由契约测试 */
  { path: '/teacher-v3/denied', name: 'tv3-denied', component: () => import('@/pages/teacher-v3/AccessDeniedPage.vue'), meta: { title: '无教师权限' } },
  /* 学生课堂 H5（M2-A · 课堂域唯一新增页，02-ARCHITECTURE §12）：无账号 join_code 进入 */
  { path: '/classroom-h5', name: 'classroom-h5', component: () => import('@/pages/classroom/StudentH5.vue'), meta: { public: true, title: '课堂互动 · 学生端' } },

  /* ===== 管理后台（唯一 admin 布局） ===== */
  { path: '/admin/overview', name: 'admin-overview', component: () => import('@/pages/admin/AdminOverviewView.vue'), meta: { admin: true, requiresRole: 'admin', title: '总览' } },
  { path: '/admin/model', name: 'admin-model', component: () => import('@/pages/admin/AdminModelView.vue'), meta: { admin: true, requiresRole: 'admin', title: '模型配置' } },
  { path: '/admin/xingchen', name: 'admin-xingchen', component: () => import('@/pages/admin/AdminXingchenView.vue'), meta: { admin: true, requiresRole: 'admin', title: '星辰与工作流' } },
  { path: '/admin/cloud-kb', name: 'admin-cloud-kb', component: () => import('@/pages/admin/AdminCloudKbView.vue'), meta: { admin: true, requiresRole: 'admin', title: '云知识库' } },
  { path: '/admin/kb-bench', name: 'admin-kb-bench', component: () => import('@/pages/admin/AdminKbBenchView.vue'), meta: { admin: true, requiresRole: 'admin', title: '检索试验台' } },
  { path: '/admin/butler', name: 'admin-butler', component: () => import('@/pages/admin/AdminButlerView.vue'), meta: { admin: true, requiresRole: 'admin', title: 'Butler 授权' } },

  
  /* ===== 科研端旧占位 stub 已随科研端并入三端移除（2026-09-09）：/research/* 全部由上方新路由承载，
     旧 dashboard/project/literature/verify/lean/writing/review/education/runs 深链由 catch-all → '/' → roleHome 收敛。 ===== */

  { path: '/:pathMatch(.*)*', redirect: '/' },
]

/*
 * P01 · App Shell 集成沙盒路由（⚠️ 仅测试可见）。
 *
 * 只在 Vite 测试/E2E 构建下注册（`import.meta.env.MODE === 'test'` 或
 * `VITE_PLATFORM_HARNESS === '1'`），**生产构建下该路由不存在**，
 * 因此不存在"沙盒页被误发布到线上"的口子。
 *
 * 守卫：该页自己声明 `public: true`，避免被 auth bootstrap 拦掉，
 * 因为 harness 的"身份"完全由 URL query 注入，不读真实会话。
 */
if (import.meta.env.MODE === 'test' || import.meta.env.VITE_PLATFORM_HARNESS === '1') {
  routes.push({
    path: '/__platform-harness',
    name: 'platform-harness',
    component: () => import('@/pages/platform-harness/PlatformShellHarness.vue'),
    meta: { public: true, title: '平台外壳集成沙盒（测试用）', workspace: 'teacher' },
  })
}

/**
 * 教师端首页解析。
 *
 * 与科研端同理（见下方 researchHome）：VNext 沉浸式备课 surface 已**取代**
 * 旧 teacher-v3 工作台成为教师默认入口。无 sessionId 时该 surface 渲染备课首页
 * （空态文案「这节课，你想怎么上？」），因此可以直接作为落地页。
 *
 * 同样必须做 `hasRoute` 存在性检查：`/teacher-v3/prep-immersive` 只在
 * `VITE_PREP_SURFACE=immersive` 时注册（见 main.js），若不加检查，
 * flag 关闭时会形成 `/` ↔ roleHome 的**无限重定向**。
 */
function teacherHome() {
  return '/teacher-v3/prep'
}

/**
 * 科研端首页解析。
 *
 * P1：科研端默认落地页是新的 Research Workspace 工作台（`/research-workspace`，
 * RV17 引入，P1 起为**唯一**落地）。旧 paper-workbench（`/research-vnext/workbench`）
 * 已退役，不再参与落地解析。
 *
 * 仍保留 `hasRoute` 存在性检查：路由未注册时逐级退回，避免
 * `/` ↔ roleHome 之间的**无限重定向**（与下方 teacherHome 同理）。
 * 这里用 `hasRoute` 而不是重读 env —— 路由是否真的注册才是唯一权威，
 * 避免"flag 开了但注册没生效"时同样掉进循环。
 */
function researchHome() {
  if (router.hasRoute('research-workspace')) return '/research-workspace'
  if (router.hasRoute('research-vnext-session')) return '/research-vnext/session'
  return '/research'
}

export function roleHome(role) {
  return role === 'teacher'
    ? teacherHome()
    : role === 'researcher'
      ? researchHome()
      : role === 'admin'
        ? '/admin/identity/applications'
        : '/dialog'
}

export function resolveAuthNavigation(to, auth) {
  if (to.meta.public) return true
  if (auth.status === 'anonymous') return { path: '/login', query: to.fullPath !== '/' ? { redirect: to.fullPath } : {} }
  if (auth.status === 'onboarding' && to.path !== '/onboarding/student') return { path: '/onboarding/student' }
  if (['pending_review', 'needs_more_info', 'rejected'].includes(auth.status) && !['/identity/pending', '/identity/apply'].includes(to.path)) return { path: '/identity/pending' }
  if (auth.status === 'deletion_pending' && to.path !== '/account/security') return { path: '/account/security' }
  if (to.path === '/') return { path: roleHome(auth.activeRole) }
  if (to.meta.requiresRole && (auth.activeRole !== to.meta.requiresRole || !auth.roles.includes(to.meta.requiresRole))) return { path: roleHome(auth.activeRole) }
  return true
}

/*
 * P1 · 旧 paper-workbench 入口收敛（默认关闭旧工作台时生效）。
 *
 * 旧工作台退役后其 4 条入口路径不再注册；这里显式把它们重定向到新工作台，
 * 而不是依赖 `/:pathMatch(.*)*` 兜底 —— 旧书签 / 通知深链不 404，且不产生
 * 「先落 catch-all → '/' → roleHome」的双跳。
 *
 * 【已隔离 2026-09-17】回滚开关随旧工作台一并移出，本段现在无条件生效。
 * 详见 D:\research-legacy-quarantine\README.md
 */
{
  const retiredResearchPaths = [
    '/research-vnext/welcome',
    '/research-vnext/workbench',
    '/research-vnext/reader',
    '/research-vnext/writing',
  ]
  // 放在 catch-all 之前，语义上明确「先匹配这些收敛路由」。
  routes.unshift(
    ...retiredResearchPaths.map((path) => ({
      path,
      redirect: '/research-workspace',
      meta: { research: true, requiresRole: 'researcher' },
    })),
  )
}

export const router = createRouter({ history: createWebHistory(), routes })

// RV17 · 科研工作台（research-workspace 新命名空间）：加法式注册，幂等；
// 保证 `/research-workspace[/:projectId/{read|write}]` 在首个冷启动导航前已就绪。
registerResearchWorkspace(router)

// 备课中心（teacher-prep-center 新命名空间）：加法式注册，幂等；
// 工作台 `/teacher-v3/prep/w/:sessionId/:tab?` 与 `/teacher-v3/prep/history` 挂到
// `tv3-layout`（TeacherV3Layout）子级 —— G1 外框不变量（首页由上方 tv3-prep 子路由指向 PrepHome）。
registerPrepCenter(router)

/**
 * 角色权限不足的跳转必须「可理解」：此前守卫把学生身份访问 /research-workspace
 * 静默打回 roleHome，用户只看到页面换掉了、不知道为什么。
 *
 * 判定条件刻意收窄（避免对未登录跳 /login、onboarding 劫持等场景误报）：
 *  - 目标路由声明了 requiresRole（角色受保护路由）；
 *  - 用户已登录且 activeRole 不满足该角色；
 *  - 守卫给出的落点恰是 roleHome(activeRole)（即确实是「权限打回」而非其它劫持）。
 */
const ROLE_LABELS = { student: '学生端', teacher: '教师端', researcher: '科研端', admin: '管理端' }
let lastDeniedToastAt = 0

function notifyRoleDenied(auth) {
  const now = Date.now()
  // 简单节流：同一短时间内连续命中（如手速快的多次点击）不刷屏
  if (now - lastDeniedToastAt < 1500) return
  lastDeniedToastAt = now
  const roleLabel = ROLE_LABELS[auth.activeRole] || '当前'
  useToastStore().warning(`你当前是「${roleLabel}」身份，没有该端口的访问权限，已返回${roleLabel}首页`)
}

router.beforeEach(async (to) => {
  beginRouteLoading()
  const auth = useAuthStore()
  if (!to.meta.public && auth.status === 'idle') await auth.bootstrap()
  // 角色自愈（2026-10-15 线上卡死修复）：activeRole 失去已批准绑定时，守卫会把
  // roleHome(activeRole) 再拒回去形成无限重定向（vue-router 中止后路由停在未解析态，
  // 学生壳空渲染、所有导航反复失败、退出入口不可达）。这里在导航前先修复身份：
  // 切回任一已批准角色；一个都没有则强制登出去 /login，绝不把用户推进死循环。
  if (!to.meta.public && auth.isLoggedIn && (!auth.activeRole || !auth.roles.includes(auth.activeRole))) {
    const healed = await auth.recoverActiveRole()
    if (!healed) {
      useToastStore().warning('当前账号没有可用身份，请重新登录')
      return { path: '/login', query: to.fullPath !== '/' ? { redirect: to.fullPath } : {} }
    }
  }
  const nav = resolveAuthNavigation(to, auth)
  if (
    nav !== true &&
    to.meta.requiresRole &&
    auth.isLoggedIn &&
    auth.activeRole !== to.meta.requiresRole &&
    nav.path === roleHome(auth.activeRole)
  ) {
    notifyRoleDenied(auth)
  }
  return nav
})

// 懒加载 chunk 失败自愈（2026-10-15）：部署更新后旧标签页持有的 chunk 引用会 404，
// 导航被静默中止且页面无任何反馈。这里给一次性自动刷新（15s 节流防循环），让用户无感恢复。
// 防御：页面级单测会把 vue-router 换成最小桩（只有 beforeEach），此时静默跳过（与 afterEach 同款）。
if (typeof router.onError === 'function') {
  router.onError((error) => {
    endRouteLoading()
    const message = String(error?.message || '')
    if (/Failed to fetch dynamically imported module|Importing a module script failed|Loading( CSS)? chunk \d+ failed/i.test(message)) {
      console.error('[router] chunk 加载失败，准备整页刷新：', message)
      const key = 'ma_chunk_reload_at'
      let last = 0
      try { last = Number(sessionStorage.getItem(key) || 0) } catch { /* 隐私模式忽略 */ }
      if (Date.now() - last > 15000) {
        try { sessionStorage.setItem(key, String(Date.now())) } catch { /* 同上 */ }
        useToastStore().info('页面资源已更新，正在刷新…')
        window.setTimeout(() => window.location.reload(), 600)
      }
    }
  })
}

// RV17 · 文档标题：科研工作台路由使用自己的标题（`科研工作台 · 阅读/写作`），
// 其余路由保持 index.html 的静态标题不变 —— 加法式，不影响学生端/教师端。
// 防御：页面级单测会把 vue-router 换成最小桩（只有 beforeEach），此时静默跳过。
const STATIC_TITLE = document.title
if (typeof router.afterEach === 'function') {
  router.afterEach((to) => {
    endRouteLoading()
    if (to.meta && to.meta.researchWorkspace && to.meta.title) {
      document.title = to.meta.title
    } else if (document.title !== STATIC_TITLE) {
      document.title = STATIC_TITLE
    }
  })
}
