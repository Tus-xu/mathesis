<template>
  <!-- T22 沙盒根组件：只提供路由出口 + 平台背景，不含任何业务假数据。 -->
  <div class="t22-root">
    <router-view />
  </div>
</template>

<script setup lang="ts">
/** 无逻辑：沙盒根组件只承载路由出口。 */
</script>

<style scoped>
.t22-root {
  min-height: 100vh;
  background: var(--ma-surface-page);
  color: var(--ma-text-primary);
}
</style>
