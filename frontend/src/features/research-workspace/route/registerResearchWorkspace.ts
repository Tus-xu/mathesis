/**
 * 科研工作台路由注册（additive、幂等、可整段回滚）。
 *
 * 路由（FRONTEND-ARCH §8）：
 *   `/research-workspace`                    → 最近项目 / 诚实空态
 *   `/research-workspace/:projectId/read`    → READ
 *   `/research-workspace/:projectId/write`   → WRITE
 * 三条路由共用同一 `ResearchWorkspaceShell.vue`（内部按路径判定工作区），
 * `meta` 统一 `{ research:true, requiresRole:'researcher', researchWorkspace:true }`。
 *
 * 由 `main.js` 与 `router/index.js` 在首个冷启动导航前调用；对测试桩（无 addRoute）
 * 静默跳过，`WeakSet` 保证同一 router 只注册一次。
 */
import type { Router } from 'vue-router'

export const RESEARCH_WORKSPACE_ROUTE_NAMES = {
  root: 'research-workspace',
  workspace: 'research-workspace-entry',
  account: 'research-workspace-account',
  read: 'research-workspace-read',
  write: 'research-workspace-write',
} as const

export const RESEARCH_WORKSPACE_PATH = '/research-workspace'

/** 科研端工作台统一 meta（App.vue 依 `researchWorkspace` 分叉渲染新 Shell）。 */
const META = {
  research: true,
  requiresRole: 'researcher',
  researchWorkspace: true,
} as const

const applied = new WeakSet<object>()

function isRealRouter(router: unknown): router is Router {
  const r = router as Record<string, unknown> | null | undefined
  return !!r && typeof r.hasRoute === 'function' && typeof r.addRoute === 'function'
}

export function registerResearchWorkspace(router: Router): void {
  if (!isRealRouter(router)) return
  if (applied.has(router)) return
  applied.add(router)

  const Shell = () => import('../shell/ResearchWorkspaceShell.vue')
  const Landing = () => import('../landing/ResearchLanding.vue')
  const Account = () => import('../landing/ResearchAccount.vue')

  if (!router.hasRoute(RESEARCH_WORKSPACE_ROUTE_NAMES.root)) {
    // 登录后的科研端启动页：先讲清能做什么、给出最近项目与三个起点，
    // 用户点击“进入科研工作台”才挂载重型三栏工作台（PDF.js + CodeMirror）。
    router.addRoute({
      path: RESEARCH_WORKSPACE_PATH,
      name: RESEARCH_WORKSPACE_ROUTE_NAMES.root,
      component: Landing,
      meta: { ...META, title: '科研端 · 启动页' },
    })
  }
  if (!router.hasRoute(RESEARCH_WORKSPACE_ROUTE_NAMES.workspace)) {
    router.addRoute({
      path: `${RESEARCH_WORKSPACE_PATH}/workspace`,
      name: RESEARCH_WORKSPACE_ROUTE_NAMES.workspace,
      component: Shell,
      meta: { ...META, title: '科研工作台' },
    })
  }
  if (!router.hasRoute(RESEARCH_WORKSPACE_ROUTE_NAMES.account)) {
    router.addRoute({
      path: `${RESEARCH_WORKSPACE_PATH}/account`,
      name: RESEARCH_WORKSPACE_ROUTE_NAMES.account,
      component: Account,
      meta: { ...META, title: '科研端 · 个人中心' },
    })
  }
  if (!router.hasRoute(RESEARCH_WORKSPACE_ROUTE_NAMES.read)) {
    router.addRoute({
      path: '/research-workspace/:projectId/read',
      name: RESEARCH_WORKSPACE_ROUTE_NAMES.read,
      component: Shell,
      props: true,
      meta: { ...META, title: '科研工作台 · 阅读' },
    })
  }
  if (!router.hasRoute(RESEARCH_WORKSPACE_ROUTE_NAMES.write)) {
    router.addRoute({
      path: '/research-workspace/:projectId/write',
      name: RESEARCH_WORKSPACE_ROUTE_NAMES.write,
      component: Shell,
      props: true,
      meta: { ...META, title: '科研工作台 · 写作' },
    })
  }
}
