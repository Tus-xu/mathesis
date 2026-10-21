<template>
  <!--
    T22.1 · 路由视图壳：唯一职责是把路由参数透传给 `ImmersivePrepSurface`。
    刻意保持为空壳 —— Focus surface 自己承载布局/导航，形态由 surface 决定。
  -->
  <div class="prep-route" data-testid="prep-surface-route">
    <ImmersivePrepSurface :session-id="sessionId" />
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import ImmersivePrepSurface from './ImmersivePrepSurface.vue'

const route = useRoute()

const sessionId = computed(() => {
  const v = route.params?.sessionId
  return typeof v === 'string' && v ? v : null
})
</script>

<style scoped>
.prep-route {
  height: 100%;
  min-height: 0;
}
</style>
