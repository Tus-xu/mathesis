<template>
  <button
    type="button"
    class="toggle"
    :class="{ on: modelValue }"
    role="switch"
    :aria-checked="modelValue"
    :disabled="disabled"
    @click="$emit('update:modelValue', !modelValue)"
  >
    <span class="toggle-knob" />
  </button>
</template>

<script setup>
defineProps({
  modelValue: { type: Boolean, default: false },
  disabled: { type: Boolean, default: false },
})
defineEmits(['update:modelValue'])
</script>

<style scoped>
.toggle {
  position: relative;
  width: 40px;
  height: 22px;
  border-radius: 11px;
  border: 1px solid var(--border, #d1d5db);
  background: var(--bg-muted, #e5e7eb);
  cursor: pointer;
  transition: background 0.2s, border-color 0.2s;
  padding: 0;
  flex: none;
}
.toggle:disabled { opacity: 0.5; cursor: not-allowed; }
.toggle.on {
  background: var(--primary, #2563eb);
  border-color: var(--primary, #2563eb);
}
.toggle-knob {
  position: absolute;
  top: 2px;
  left: 2px;
  width: 16px;
  height: 16px;
  border-radius: 50%;
  background: #fff;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.2);
  transition: transform 0.2s;
}
.toggle.on .toggle-knob { transform: translateX(18px); }
</style>
