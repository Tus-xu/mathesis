<template>
  <div class="tph">
    <div class="tph__main">
      <div v-if="icon" class="tph__icon" aria-hidden="true">{{ icon }}</div>
      <div class="tph__text">
        <h1 class="tph__title">{{ title }}</h1>
        <p v-if="subtitle" class="tph__sub">{{ subtitle }}</p>
      </div>
    </div>
    <div class="tph__actions"><slot /></div>
  </div>
</template>

<script setup lang="ts">
defineProps<{ title: string; subtitle?: string; icon?: string }>()
</script>

<style scoped>
.tph {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 18px;
}
.tph__main { display: flex; align-items: center; gap: 12px; min-width: 0; }
.tph__icon {
  width: 46px; height: 46px; border-radius: 14px; flex-shrink: 0;
  display: grid; place-items: center; font-size: 22px; color: #fff;
  background: linear-gradient(135deg, var(--teacher-primary) 0%, var(--teacher-secondary) 100%);
  box-shadow: var(--teacher-shadow-primary);
}
.tph__text { min-width: 0; }
.tph__title { font-size: 26px; font-weight: 800; letter-spacing: 0.5px; color: var(--teacher-text); margin: 0; line-height: 1.25; }
.tph__sub { font-size: 14px; color: var(--teacher-text-secondary); margin: 4px 0 0; }
.tph__actions { display: flex; align-items: center; gap: 10px; flex-shrink: 0; }
</style>
