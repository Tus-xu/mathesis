<template>
  <input
    class="tinput"
    :value="modelValue"
    :placeholder="placeholder"
    @input="$emit('update:modelValue', ($event.target as HTMLInputElement).value)"
  />
</template>

<script setup lang="ts">
withDefaults(defineProps<{ modelValue?: string; placeholder?: string }>(), { modelValue: '', placeholder: '' })
defineEmits<{ (e: 'update:modelValue', v: string): void }>()
</script>

<style scoped>
.tinput {
  width: 100%; height: 44px; padding: 0 14px; box-sizing: border-box;
  border-radius: var(--teacher-radius-md); border: 1px solid var(--teacher-border);
  background: var(--teacher-surface); font-size: 14px; color: var(--teacher-text);
  font-family: var(--teacher-font-family); outline: none;
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
}
.tinput::placeholder { color: var(--teacher-text-tertiary); }
.tinput:focus {
  border-color: var(--teacher-primary);
  box-shadow: 0 0 0 3px rgba(99, 102, 241, 0.15);
}
</style>
