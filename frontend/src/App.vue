<template>
  <!-- 引导期（深链 bootstrap 未完成）只渲染中性加载态：不挂任何角色外壳，
       避免学生端外壳闪白、以及角色不符的接口（growth/panel、conversations）被误发 -->
  <div v-if="bootstrapping" class="app-boot" aria-busy="true">正在进入工作台…</div>
  <!-- 死路由兜底（2026-10-15 卡死修复）：首次导航被中止（无限重定向/资源加载失败）时，
       route 停在未解析的初始态（matched 为空）——此前这里会错误套上学生端外壳并空渲染，
       用户看起来就是"白屏卡死且无路可逃"。改为渲染自愈兜底，一键重进。 -->
  <div v-else-if="route.matched.length === 0" class="app-boot app-boot--broken">
    <div class="app-boot__card">
      <p class="app-boot__title">页面未能加载</p>
      <p class="app-boot__hint">可能是网络波动或页面资源已更新</p>
      <button type="button" class="app-boot__btn" @click="reloadApp">重新进入</button>
    </div>
  </div>
  <!-- 公共页面（登录/认证流）直接渲染；student 走 V4Layout；teacher 走 TeacherLayout；admin 走 AdminLayout；research 走新科研工作台（research-workspace） -->
  <!-- 沉浸式页面（双师课堂 /dual）脱离全局学生壳：自带顶栏/大纲/画布/助教抽屉，不显示今日任务等侧栏 -->
  <ErrorBoundary v-else-if="route.meta.immersive" title="课堂页面加载出现异常">
    <router-view />
  </ErrorBoundary>
  <!-- 教师工作台 V3：路由内部自带 TeacherV3Layout，不套旧版 TeacherLayout -->
  <router-view v-else-if="route.meta.teacherV3" />
  <V4Layout v-else-if="!route.meta.public && !route.meta.teacher && !route.meta.admin && !route.meta.research && !route.meta.authFlow" />
  <AdminLayout v-else-if="route.meta.admin" />
  <!-- RV17 · 科研工作台（research-workspace 新命名空间）：由 route.meta.researchWorkspace
       分叉渲染新 Shell（自带 Header + 工作区条 + 三/四栏）。
       【已隔离 2026-09-17】上一代沉浸式科研面（features/research-vnext）及其
       ResearchAppShell 分支已移出；旧入口统一 redirect 到 /research-workspace。
       详见 D:\research-legacy-quarantine\README.md -->
  <router-view v-else-if="route.meta.researchWorkspace" />
  <router-view v-else />
  <div v-if="routeLoading" class="route-loading-mask" role="status" aria-live="polite">
    <div class="route-loading-card"><span class="route-loading-spinner"></span>正在加载页面…</div>
  </div>
  <ToastHost />
  <ConfirmDialog />
  <ImmersiveQuiz />
</template>

<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { routeLoading } from '@/composables/routeLoading'
import V4Layout from '@/components/V4Layout.vue'
import AdminLayout from '@/layouts/AdminLayout.vue'
import ToastHost from '@/components/ToastHost.vue'
import ConfirmDialog from '@/components/common/ConfirmDialog.vue'
import ErrorBoundary from '@/components/common/ErrorBoundary.vue'
import ImmersiveQuiz from '@/components/ImmersiveQuiz.vue'

const route = useRoute()
const auth = useAuthStore()

// 未登录态走 public 路由不受影响；受保护路由在身份 bootstrap 期间不渲染角色外壳
const bootstrapping = computed(() => !route.meta.public && ['idle', 'bootstrapping'].includes(auth.status))

// 死路由兜底的恢复动作：整页重载走全新 bootstrap，绕开任何已死的导航状态
const reloadApp = () => window.location.reload()

</script>

<style>
.app-boot { min-height: 100vh; display: flex; align-items: center; justify-content: center; color: var(--ma-text-muted); font-size: 14px; letter-spacing: .04em; background: var(--ma-surface-page); }
.app-boot--broken { padding: 24px; }
.app-boot__card { display: grid; justify-items: center; gap: 10px; padding: 36px 44px; border: 1px solid rgba(226, 232, 240, .9); border-radius: 18px; background: rgba(255, 255, 255, .92); box-shadow: 0 18px 60px rgba(15, 23, 42, .08); }
.app-boot__title { margin: 0; font-size: 17px; font-weight: 700; color: var(--ma-text, #0f172a); letter-spacing: .02em; }
.app-boot__hint { margin: 0; font-size: 13px; color: var(--ma-text-muted, #64748b); }
.app-boot__btn { margin-top: 6px; height: 38px; padding: 0 26px; border: 0; border-radius: 10px; background: var(--ma-brand, #4f46e5); color: #fff; font-size: 14px; font-weight: 700; cursor: pointer; }
.app-boot__btn:hover { filter: brightness(1.06); }
.route-loading-mask { position: fixed; inset: 58px 0 0; z-index: 90; display: flex; align-items: flex-start; justify-content: center; padding-top: 18vh; background: rgba(246, 248, 252, .72); backdrop-filter: blur(2px); }
.route-loading-card { display: inline-flex; align-items: center; gap: 10px; padding: 14px 22px; border: 1px solid var(--ma-line, #e2e8f0); border-radius: 14px; background: #fff; color: var(--ma-text-muted, #64748b); font-size: 13px; box-shadow: 0 16px 48px rgba(15, 23, 42, .1); }
.route-loading-spinner { width: 16px; height: 16px; border: 2px solid #c7d2fe; border-top-color: #4f46e5; border-radius: 50%; animation: route-spin .8s linear infinite; }
@keyframes route-spin { to { transform: rotate(360deg); } }
</style>
