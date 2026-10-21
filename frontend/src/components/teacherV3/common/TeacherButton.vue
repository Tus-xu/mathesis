<template>
  <button class="tbtn" :class="'tbtn--' + variant" :disabled="disabled" type="button">
    <slot />
  </button>
</template>

<script setup lang="ts">
withDefaults(defineProps<{ variant?: 'primary' | 'secondary' | 'text' | 'danger'; disabled?: boolean }>(), { variant: 'primary' })
</script>

<style scoped>
.tbtn {
  display: inline-flex; align-items: center; justify-content: center; gap: 7px;
  height: 40px; padding: 0 20px; border-radius: var(--teacher-radius-md);
  border: none; font-size: 14px; font-weight: 600; cursor: pointer;
  font-family: var(--teacher-font-family); white-space: nowrap;
  transition: all 0.15s ease;
}
.tbtn--primary { background: var(--teacher-primary); color: #fff; box-shadow: var(--teacher-shadow-primary); }
.tbtn--primary:hover:not(:disabled) { background: var(--teacher-primary-hover); box-shadow: 0 6px 18px rgba(99, 102, 241, 0.35); }
.tbtn--secondary { background: var(--teacher-primary-light); color: var(--teacher-primary); }
.tbtn--secondary:hover:not(:disabled) { background: #d3ddfb; }
.tbtn--text { background: transparent; color: var(--teacher-primary); padding: 0 12px; }
.tbtn--text:hover:not(:disabled) { background: var(--teacher-primary-lighter); }
.tbtn--danger { background: var(--teacher-danger); color: #fff; }
.tbtn--danger:hover:not(:disabled) { background: #dc2626; }
.tbtn:disabled { opacity: 0.5; cursor: not-allowed; }
</style>
