<template>
  <div class="form-item">
    <label class="form-label" :for="id">{{ label }}</label>
    <div class="password-row">
      <input :id="id" class="input" :type="visible ? 'text' : 'password'" autocomplete="current-password" :value="modelValue" aria-label="密码" placeholder="请输入密码" @input="emit('update:modelValue', $event.target.value)" />
      <button type="button" class="reveal" :aria-label="visible ? '隐藏密码' : '显示密码'" @click="visible = !visible">{{ visible ? '隐藏' : '显示' }}</button>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
defineProps({ modelValue: { type: String, default: '' }, label: { type: String, default: '密码' }, id: { type: String, default: 'auth-password' } })
const emit = defineEmits(['update:modelValue'])
const visible = ref(false)
</script>

<style scoped>
.password-row { position: relative; }
.password-row .input { padding-right: 58px; }
.reveal { position: absolute; right: 10px; top: 50%; transform: translateY(-50%); border: 0; background: none; color: var(--brand-deep); cursor: pointer; }
</style>
