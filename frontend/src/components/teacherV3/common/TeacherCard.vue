<template>
  <div class="tcard"><slot /></div>
</template>

<script setup lang="ts">
defineOptions({ name: 'TeacherCard' })
</script>

<style scoped>
.tcard {
  background: var(--teacher-surface);
  border: 1px solid var(--teacher-border);
  border-radius: var(--teacher-radius-lg);
  box-shadow: var(--teacher-shadow-md);
  padding: 24px;
  transition: box-shadow 0.2s ease;
}
.tcard:hover { box-shadow: var(--teacher-shadow-lg); }
</style>
