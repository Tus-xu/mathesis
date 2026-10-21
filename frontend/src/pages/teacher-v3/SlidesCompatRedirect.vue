<template>
  <!--
    WP-G · `/teacher-v3/slides`（旧「课件工坊」）兼容路由壳。

    默认**不渲染任何产品页面**：挂载即把 URL 替换成映射目标
    （TeachingSession 的 Artifact Focus 或「我的备课」），老师不会在这里停留。
    仅当 URL 显式带 `?editor=legacy` 时才渲染冻结的 legacy 五区编辑器
    （逃生舱：不进任何导航、不被任何深链指向，物理删除见后续清理包）。
  -->
  <div v-if="useLegacyEditorShell" class="tv3-slides-legacy" data-testid="tv3-slides-legacy">
    <component :is="legacyEditor" />
  </div>
  <div v-else class="tv3-slides-compat" data-testid="tv3-slides-compat" />
</template>

<script setup lang="ts">
import { computed, defineAsyncComponent, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { useToastStore } from '@/stores/toast'
import {
  SLIDES_RETIRED_NOTICE,
  isVNextDeckEditorRoute,
  isLegacyEditorEscape,
  markSlidesRetiredNoticeShown,
  resolveSlidesCompatTarget,
  slidesRetiredNoticeShown,
  type CompatQuery,
} from '@/router/slidesCompat'

const route = useRoute()
const router = useRouter()
const toast = useToastStore()

/** 动态 import：legacy 编辑器只在逃生舱路径下才进 chunk（默认路径零成本）。 */
const legacyEditor = defineAsyncComponent(() => import('@/pages/teacher-v3/SlidesView.vue'))

const legacyEscape = computed(() => isLegacyEditorEscape(route.query as CompatQuery))
// 新内核已生成 deck 时，旧 SlidesView 只作为编辑器壳使用；它自己的 VNext
// 分支会读取 session/deck/spec，不能被此兼容路由重定向回会话页面。
const useLegacyEditorShell = computed(() => (
  legacyEscape.value || isVNextDeckEditorRoute(route.query as CompatQuery)
))

/**
 * 一次性迁移提示：每个浏览器只提示一次（localStorage 只存这个 UI 偏好标志，
 * 不参与任何业务逻辑；读不到 storage 时宁可不提示也不阻塞跳转）。
 */
function announceRetiredOnce(): boolean {
  const storage = typeof localStorage === 'undefined' ? null : localStorage
  if (slidesRetiredNoticeShown(storage)) return false
  try {
    // null duration = 常驻直到用户关闭（迁移提示需要被读完，不是一闪而过的操作反馈）
    toast.show(SLIDES_RETIRED_NOTICE, 'info', null)
  } catch {
    /* 未安装 pinia 的隔离环境：提示失败不影响跳转 */
  }
  markSlidesRetiredNoticeShown(storage)
  return true
}

onMounted(() => {
  announceRetiredOnce()
  if (useLegacyEditorShell.value) return
  const target = resolveSlidesCompatTarget(route.query as CompatQuery, {
    hash: route.hash,
  })
  void router.replace({ path: target.path, query: target.query, hash: target.hash }).catch(() => {
    /* 导航被守卫取消：不需要在这里兜底，老师仍在原路由上 */
  })
})
</script>

<style scoped>
/* 兼容壳不可见：跳转前不产生任何可视内容（避免"闪一下旧产品页面"） */
.tv3-slides-compat {
  display: block;
  height: 100%;
  min-height: 0;
}
</style>
