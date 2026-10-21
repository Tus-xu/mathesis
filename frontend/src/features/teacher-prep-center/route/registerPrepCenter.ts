/**
 * 备课中心路由注册（加法式、幂等）—— R1 外框嵌入（G1 外框不变量）。
 *
 * 挂载层级：全部作为 `/teacher-v3`（TeacherV3Layout）的子路由注册，
 * 即 平台侧栏 | [备课顶栏 + 备课左导航 + 主区]：
 * - `/teacher-v3/prep` 首页 → 首页挂载由 router/index.js 的 `tv3-prep` 子路由
 *   指向 PrepHome（保持原路径/名称不变，roleHome('teacher') 落点不受影响）。
 * - `/teacher-v3/prep/w/:sessionId/:tab?` → 工作台（tab ∈ classroom|ppt|plan|materials|overview，
 *   缺省 classroom）。
 * - `/teacher-v3/prep/history` → 备课历史（跨会话，S8）；首页态左导航「我的备课」入口。
 * - 旧 `/teacher-v3/prep/session/:id`（teacher-prep-v2）不受影响。
 *
 * 历史注记：R0 之前这三条路由顶层注册（自带整屏外壳、不进 TeacherV3Layout）。
 * 宪法 v2 §2 外框不变量 G1 裁决推翻该形态：备课中心是教师工作台内的功能区，
 * 平台侧栏/顶栏/悬浮 AI 助教必须在每个备课页可见；备课自己的顶栏与左导航
 * 保留为外框内容区内部组件（G2 单实现冻结：不新增第二套 Shell）。
 */
import type { Router } from 'vue-router'

/** 测试桩（最小 router stub 无 hasRoute/addRoute）静默跳过；与科研端注册器同防御。 */
function isRealRouter(router: unknown): router is Router {
  const r = router as Record<string, unknown> | null | undefined
  return !!r && typeof r.hasRoute === 'function' && typeof r.addRoute === 'function'
}

const applied = new WeakSet<object>()

/** 父路由（TeacherV3Layout）在 router/index.js 中的名称。 */
const TV3_LAYOUT = 'tv3-layout'

export function registerPrepCenter(router: Router): void {
  if (!isRealRouter(router)) return
  if (applied.has(router)) return
  if (router.hasRoute('prep-center-workspace')) return
  // 父路由必须先存在（真实 router 由 createRouter(routes) 静态声明）；不存在则不注册，
  // 绝不回退成顶层注册（那会再次脱离平台外框）。
  if (!router.hasRoute(TV3_LAYOUT)) return
  applied.add(router)
  router.addRoute(TV3_LAYOUT, {
    path: 'prep/w/:sessionId/:tab?',
    name: 'prep-center-workspace',
    component: () => import('@/features/teacher-prep-center/workspace/PrepWorkspace.vue'),
    meta: {
      teacher: true,
      teacherV3: true,
      requiresRole: 'teacher',
      scene: 'teacher.v3.prep.center.workspace',
      title: '备课工作台',
    },
  })
  router.addRoute(TV3_LAYOUT, {
    path: 'prep/history',
    name: 'prep-center-history',
    component: () => import('@/features/teacher-prep-center/home/PrepHistoryView.vue'),
    meta: {
      teacher: true,
      teacherV3: true,
      requiresRole: 'teacher',
      scene: 'teacher.v3.prep.center.history',
      title: '备课历史',
    },
  })
}
