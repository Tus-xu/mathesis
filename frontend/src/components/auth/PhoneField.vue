<template>
  <div class="form-item">
    <label class="form-label" :for="id">{{ label }}</label>
    <input
      :id="id"
      class="input"
      type="tel"
      inputmode="numeric"
      autocomplete="tel"
      maxlength="11"
      aria-label="手机号"
      :value="modelValue"
      placeholder="请输入中国大陆手机号"
      @input="onInput"
    />
    <p v-if="error" class="field-error" role="alert">{{ error }}</p>
  </div>
</template>

<script setup>
defineProps({ modelValue: { type: String, default: '' }, label: { type: String, default: '手机号' }, error: { type: String, default: '' }, id: { type: String, default: 'auth-phone' } })
const emit = defineEmits(['update:modelValue'])
function onInput(event) {
  let value = event.target.value.replace(/\D/g, '')
  if (value.startsWith('86') && value.length > 11) value = value.slice(2)
  value = value.slice(0, 11)
  event.target.value = value
  emit('update:modelValue', value)
}
</script>

<style scoped>.field-error { color: var(--err); font-size: 12px; margin-top: 5px; }</style>
