<template>
  <main class="recovery-page">
    <section class="recovery-card">
      <span class="tag">注销冷静期</span><h1>取消账号注销</h1>
      <p>使用原绑定手机号验证，无需仍然有效的登录会话。</p>
      <label>手机号<input v-model.trim="form.phone" class="input" aria-label="手机号" /></label>
      <div class="code-row">
        <label>短信验证码<input v-model.trim="form.code" class="input" aria-label="短信验证码" /></label>
        <button class="btn" data-action="send-cancel-code" @click="sendCode">发送验证码</button>
      </div>
      <p v-if="message" role="status" class="message">{{ message }}</p>
      <button class="btn btn-primary submit" data-action="submit-deletion-cancel" @click="submit">取消注销</button>
      <RouterLink to="/login">返回登录</RouterLink>
    </section>
  </main>
</template>
<script setup>
import { reactive, ref } from 'vue'
import { authApi, securityApi } from '@/api/auth'
const form = reactive({ phone: '', code: '', challenge_id: '' })
const message = ref('')
async function sendCode() { try { const data = await authApi.challengeSms(form.phone, 'deletion_cancel'); form.challenge_id = data.challenge_id; message.value = '验证码已发送' } catch (error) { message.value = error?.message || '验证码发送失败' } }
async function submit() { try { await securityApi.cancelDeletion({ phone: form.phone, challenge_id: form.challenge_id, code: form.code }); message.value = '注销申请已取消，现在可以重新登录' } catch (error) { message.value = error?.message || '取消注销失败' } }
</script>
<style scoped>.recovery-page{min-height:100vh;display:grid;place-items:center;padding:24px;background:var(--bg)}.recovery-card{width:min(480px,100%);display:grid;gap:15px;background:white;border:1px solid var(--line);border-radius:22px;padding:32px;box-shadow:var(--shadow-lg)}label{display:grid;gap:7px;font-weight:600}.code-row{display:grid;grid-template-columns:1fr auto;gap:10px;align-items:end}.message{padding:10px;border-radius:9px;background:var(--warn-bg);color:var(--warn-deep)}.submit{height:44px}@media(max-width:520px){.code-row{grid-template-columns:1fr}}</style>
