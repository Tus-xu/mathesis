<template>
  <!--
    T22 · 领域内共享原语：表面卡片。
    只消费平台语义 token（`--ma-*`），零字面色值；不新增第二套变量。
  -->
  <component
    :is="as"
    class="prep-card"
    :class="{ 'prep-card--interactive': interactive }"
    :data-testid="testId"
  >
    <header v-if="title" class="prep-card__head">
      <h3 class="prep-card__title">{{ title }}</h3>
      <slot name="extra" />
    </header>
    <div class="prep-card__body">
      <slot />
    </div>
    <footer v-if="$slots.footer" class="prep-card__foot">
      <slot name="footer" />
    </footer>
  </component>
</template>

<script setup lang="ts">
withDefaults(
  defineProps<{
    title?: string
    as?: string
    interactive?: boolean
    testId?: string
  }>(),
  { title: '', as: 'section', interactive: false, testId: undefined },
)
</script>

<style scoped>
.prep-card {
  background: var(--ma-surface-default);
  border: 1px solid var(--ma-surface-border);
  border-radius: var(--ma-radius-card);
  box-shadow: var(--ma-elevation-card);
  padding: var(--ma-space-3);
  color: var(--ma-text-primary);
}

.prep-card--interactive:hover {
  border-color: var(--ma-surface-border-strong);
}

.prep-card__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--ma-space-2);
  margin-bottom: var(--ma-space-2);
}

.prep-card__title {
  margin: 0;
  font-size: var(--ma-font-size-title);
  font-weight: 600;
  color: var(--ma-text-primary);
}

.prep-card__body {
  color: var(--ma-text-body);
  font-size: var(--ma-font-size-body);
}

.prep-card__foot {
  margin-top: var(--ma-space-3);
  display: flex;
  align-items: center;
  gap: var(--ma-space-2);
}

.prep-card:focus-visible {
  outline: var(--ma-focus-ring);
  outline-offset: 2px;
}
</style>
