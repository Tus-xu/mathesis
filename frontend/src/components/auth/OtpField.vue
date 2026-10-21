<template>
  <div class="form-item">
    <label class="form-label" for="auth-otp">短信验证码</label>
    <div class="otp-row">
      <input id="auth-otp" class="input" inputmode="numeric" autocomplete="one-time-code" maxlength="6" aria-label="短信验证码" :value="modelValue" placeholder="6 位验证码" @input="emit('update:modelValue', $event.target.value.replace(/\D/g, '').slice(0, 6))" />
      <button type="button" class="btn" :disabled="sending || seconds > 0 || !validPhone" @click="send">
        {{ seconds > 0 ? `${seconds} 秒后重试` : sending ? '发送中…' : '获取验证码' }}
      </button>
    </div>
    <p v-if="demoCode" class="demo-note">演示环境验证码：{{ demoCode }}</p>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, ref } from 'vue'
import { authApi } from '@/api/auth'

const props = defineProps({ modelValue: { type: String, default: '' }, phone: { type: String, required: true }, purpose: { type: String, default: 'login' } })
const emit = defineEmits(['update:modelValue', 'challenge', 'error'])
const sending = ref(false)
const seconds = ref(0)
const demoCode = ref('')
let timer
const validPhone = computed(() => /^1[3-9]\d{9}$/.test(props.phone))
function countdown(value) {
  seconds.value = value
  clearInterval(timer)
  timer = setInterval(() => {
    seconds.value -= 1
    if (seconds.value <= 0) clearInterval(timer)
  }, 1000)
}
async function send() {
  if (!validPhone.value || sending.value) return
  sending.value = true
  try {
    const data = await authApi.challengeSms(props.phone, props.purpose)
    demoCode.value = data.demo_code || ''
    countdown(data.retry_after || 60)
    emit('challenge', data.challenge_id, data)
  } catch (error) {
    if (error?.retryAfter) countdown(error.retryAfter)
    emit('error', error)
  } finally { sending.value = false }
}
onBeforeUnmount(() => clearInterval(timer))
</script>

<style scoped>
.otp-row { display: flex; gap: 8px; }
.otp-row .btn { flex: 0 0 126px; }
.demo-note { margin-top: 6px; color: var(--warn-deep); font-size: 12px; }
</style>
