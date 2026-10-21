<template>
  <div class="tes">
    <div class="tes__art" aria-hidden="true">{{ icon }}</div>
    <h3 class="tes__title">{{ title }}</h3>
    <p v-if="desc" class="tes__desc">{{ desc }}</p>
    <TeacherButton v-if="cta" variant="primary" @click="$emit('cta-click')">{{ cta }}</TeacherButton>
  </div>
</template>

<script setup lang="ts">
import TeacherButton from './TeacherButton.vue'

withDefaults(defineProps<{ title: string; desc?: string; cta?: string; icon?: string }>(), { icon: '📚' })
defineEmits<{ (e: 'cta-click'): void }>()
</script>

<style scoped>
.tes {
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  gap: 8px; padding: 48px 24px; text-align: center;
}
.tes__art {
  width: 120px; height: 120px; border-radius: 50%; margin-bottom: 8px;
  display: grid; place-items: center; font-size: 48px; flex-shrink: 0;
  background: linear-gradient(135deg, var(--teacher-primary-lighter) 0%, var(--teacher-primary-light) 60%, #f5f3ff 100%);
  border: 1px solid var(--teacher-border-light);
  box-shadow: var(--teacher-shadow-primary);
}
.tes__title { font-size: 17px; font-weight: 700; color: var(--teacher-text); margin: 0; }
.tes__desc { font-size: 13px; color: var(--teacher-text-secondary); margin: 0 0 6px; line-height: 1.7; max-width: 380px; }
</style>
