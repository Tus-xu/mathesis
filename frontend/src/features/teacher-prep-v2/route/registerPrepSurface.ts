/**
 * T22.1 · 正式路由注册（**additive**，不修改 `src/router/index.js`）。
 *
 * 为什么用 addRoute 而不是改路由表：
 * - `src/router/index.js` 当前是 **REAL dirty**（hash `02052e0e…` ≠ HEAD `d0a6a8ea…`），
 *   owner = SHELL；AGENTS.md 第 3 条禁止覆盖他人未提交改动。
 * - 本模块保持"零改既有文件"：删掉调用点即 100% 回滚（见 ROLLBACK.md）。
 *
 * 路由形态（DESIGN-CORRECTION-R2 §1-1）：
 * - **不再挂 `TeacherV3Layout.vue`** —— 那个布局正是左侧"今日/备课中心/课件工坊/题库…"
 *   大目录的来源；进入 TeachingSession 后全局侧栏必须**完全消失**（不保留 icon rail）。
 * - 改为**直接路由到 `PrepSurfaceRoute`**，顶部信息栏由 surface 内的 `FocusShell` 承载。
 * - 路径保留 `/teacher-v3/prep-immersive` 前缀：route name 只是内部兼容标识，
 *   与用户看到的导航文案无关，本轮不改 `src/router/index.js`（REAL dirty，owner=SHELL）。
 *
 * 生产启用（待 SHELL owner 落地，登记为 SCR-1b）：
 * ```ts
 * // src/main.ts
 * import { registerPrepSurface } from '@/features/teacher-prep-v2/route/registerPrepSurface'
 * if (import.meta.env.VITE_PREP_SURFACE === 'immersive') registerPrepSurface(router)
 * ```
 * 未设该变量 → 行为与今天完全一致。
 */
import type { Router } from 'vue-router'

export const PREP_SURFACE_ROUTE_NAMES = {
  root: 'tv3-prep-immersive',
  withSession: 'tv3-prep-immersive-session',
} as const

export function registerPrepSurface(router: Router): void {
  if (router.hasRoute(PREP_SURFACE_ROUTE_NAMES.root)) return

  const meta = {
    teacher: true,
    requiresRole: 'teacher',
    scene: 'teacher.v3.prep.immersive',
    title: '沉浸式备课',
  }
  const view = () => import('@/features/teacher-prep-v2/surface/PrepSurfaceRoute.vue')

  // 注意：**不带 layout 父级** —— 全局侧栏在 Focus Mode 下必须完全消失。
  router.addRoute({ path: '/teacher-v3/prep-immersive', name: PREP_SURFACE_ROUTE_NAMES.root, component: view, meta })
  router.addRoute({
    path: '/teacher-v3/prep-immersive/:sessionId',
    name: PREP_SURFACE_ROUTE_NAMES.withSession,
    component: view,
    meta,
  })
}
