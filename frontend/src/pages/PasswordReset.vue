<template>
  <main class="recovery-page">
    <section class="recovery-card">
      <span class="tag">账号恢复</span><h1>重置密码</h1>
      <p>验证绑定手机号后设置新密码，成功后现有会话全部失效。</p>
      <label>手机号<input v-model.trim="form.phone" class="input" aria-label="手机号" /></label>
      <div class="code-row">
        <label>短信验证码<input v-model.trim="form.code" class="input" aria-label="短信验证码" /></label>
        <button class="btn" data-action="send-reset-code" @click="sendCode">发送验证码</button>
      </div>
      <label>新密码<input v-model="form.password" class="input" type="password" aria-label="新密码" /></label>
      <p v-if="message" role="status" class="message">{{ message }}</p>
      <button class="btn btn-primary submit" data-action="submit-password-reset" @click="submit">确认重置</button>
      <RouterLink to="/login">返回登录</RouterLink>
    </section>
  </main>
</template>
<script setup>
import { reactive, ref } from 'vue'
import { authApi } from '@/api/auth'
const form = reactive({ phone: '', code: '', password: '', challenge_id: '' })
const message = ref('')
async function sendCode() { try { const data = await authApi.challengeSms(form.phone, 'password_reset'); form.challenge_id = data.challenge_id; message.value = '验证码已发送' } catch (error) { message.value = error?.message || '验证码发送失败' } }
async function submit() { try { await authApi.resetPassword({ phone: form.phone, challenge_id: form.challenge_id, code: form.code, password: form.password }); message.value = '密码已重置，请返回登录' } catch (error) { message.value = error?.message || '密码重置失败' } }
</script>
<style scoped>.recovery-page{min-height:100vh;display:grid;place-items:center;padding:24px;background:var(--bg)}.recovery-card{width:min(480px,100%);display:grid;gap:15px;background:white;border:1px solid var(--line);border-radius:22px;padding:32px;box-shadow:var(--shadow-lg)}label{display:grid;gap:7px;font-weight:600}.code-row{display:grid;grid-template-columns:1fr auto;gap:10px;align-items:end}.message{padding:10px;border-radius:9px;background:var(--warn-bg);color:var(--warn-deep)}.submit{height:44px}@media(max-width:520px){.code-row{grid-template-columns:1fr}}</style>
