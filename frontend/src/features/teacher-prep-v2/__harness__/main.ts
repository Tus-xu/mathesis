/**
 * T22 · 集成沙盒入口（仅开发/E2E；不进生产产物，见同目录 index.html 说明）。
 *
 * 与生产入口的区别：**只**多了一份最小路由表（T22 workspace 路由），
 * 其余（平台 token、域 manifest 注册、store）与生产完全一致 —— 避免"沙盒能过、生产不过"。
 */
import { createApp } from 'vue'
import { createRouter, createWebHashHistory } from 'vue-router'
import { createPinia } from 'pinia'

// 平台设计 token（生产由 src/main.js 加载；沙盒必须同样加载，否则 var(--ma-*) 全悬空）
import '@/platform/design-system/tokens.css'
import '@/platform/design-system/alias.css'

import HarnessRoot from './HarnessRoot.vue'
import PrepSurfaceRoute from '../surface/PrepSurfaceRoute.vue'
import { usePrepWorkspaceStore } from '../stores/prepWorkspace'
import { usePrepSurfaceStore } from '../surface/usePrepSurfaceStore'
import { registerTeacherPrepV2 } from '../manifest'

const routes = [
  {
    path: '/teacher-v3/prep-immersive/:sessionId?',
    name: 't22-prep-immersive',
    component: PrepSurfaceRoute,
  },
  { path: '/:pathMatch(.*)*', redirect: '/teacher-v3/prep-immersive' },
]

// 沙盒页不在根路径下（vite dev 直接服务 /src/**），故用 hash 历史；
// 这只影响沙盒的 URL 形态，不改变任何状态/数据路径。
const router = createRouter({ history: createWebHashHistory(), routes })

const app = createApp(HarnessRoot)
const pinia = createPinia()
app.use(pinia)
app.use(router)

/** 域 manifest 注册（P01 通道 2/3）；暴露给 E2E 断言"注册确实发生"。 */
const registration = registerTeacherPrepV2()
// eslint-disable-next-line @typescript-eslint/no-explicit-any
;(window as any).__T22_MANIFEST__ = {
  homeRegistered: registration.homeRegistered,
  errors: registration.errors,
  entryCount: registration.catalog.entries.size,
}

// Explicitly harness-only: E2E visual evidence can exercise the real Vue store's
// reconnecting presentation without changing production transport semantics.
;(window as any).__T22_PREP_STORE__ = usePrepWorkspaceStore(pinia)
;(window as any).__T22_PREP_SURFACE_STORE__ = usePrepSurfaceStore(pinia)

app.mount('#app')
