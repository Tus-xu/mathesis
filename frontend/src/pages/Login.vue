<template>
  <main class="auth-page">
    <section class="auth-intro">
      <div class="brand-mark">π</div>
      <p class="eyebrow">智学数研</p>
      <h1>一个账号，连接学习、教学与科研</h1>
      <p>完成手机号验证后，直接进入你选择的学习、教学或科研端口。</p>
      <ul><li>学生个性化学习与成长画像</li><li>教师备课、课堂与评价闭环</li><li>科研推导验证与知识协作</li></ul>
    </section>
    <section class="auth-card" aria-labelledby="login-title">
      <p class="eyebrow">统一身份认证</p>
      <h2 id="login-title">欢迎回来</h2>
      <div class="tabs" role="tablist" aria-label="登录方式">
        <button role="tab" :aria-selected="mode === 'sms'" :class="{ active: mode === 'sms' }" @click="mode = 'sms'">短信验证码</button>
        <button role="tab" :aria-selected="mode === 'password'" :class="{ active: mode === 'password' }" @click="mode = 'password'">密码登录</button>
      </div>
      <form @submit.prevent="submit">
        <div class="form-item">
          <label class="form-label" for="preferred-role">进入身份</label>
          <select id="preferred-role" v-model="preferredRole" name="preferred_role" class="input" aria-label="进入身份">
            <option value="student">学生端</option>
            <option value="teacher">教师端</option>
            <option value="researcher">科研端</option>
          </select>
          <p class="role-note">登录后将进入所选身份对应的端口。</p>
        </div>
        <PhoneField v-model="phone" :error="phoneError" />
        <p v-if="lastPhone" class="role-note last-phone">上次登录：<button type="button" class="last-phone-fill" @click="fillLastPhone">{{ maskPhone(lastPhone) }}</button></p>
        <p v-if="phoneMismatch" class="warn" role="status">本次输入的号码与上次登录（{{ maskPhone(lastPhone) }}）不同，将进入另一个账号；如果并非有意，请核对号码。</p>
        <OtpField v-if="mode === 'sms'" v-model="code" :phone="phone" purpose="login" @challenge="onChallenge" @error="showError" />
        <PasswordField v-else v-model="password" />
        <p v-if="mode === 'sms' && accountExists === false" class="warn" role="status">该号码还未注册过，验证通过后将自动创建新账号；如果你以为已有账号，请核对号码是否输错。</p>
        <label class="remember"><input v-model="remember" type="checkbox" /> 在这台设备上保持登录</label>
        <p v-if="error" class="error" role="alert">{{ error }}</p>
        <button class="btn btn-primary submit" :disabled="loading || !canSubmit">{{ loading ? '正在验证…' : '安全登录' }}</button>
      </form>
      <p class="register"><RouterLink to="/account/password">忘记密码？</RouterLink> · <RouterLink to="/account/deletion/cancel">取消账号注销</RouterLink></p>
      <p class="register">第一次使用？<RouterLink :to="registerLink">注册新账号</RouterLink></p>
      <p class="terms">登录即表示你同意平台服务协议与隐私政策。</p>
    </section>
  </main>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import OtpField from '@/components/auth/OtpField.vue'
import PasswordField from '@/components/auth/PasswordField.vue'
import PhoneField from '@/components/auth/PhoneField.vue'
import { roleHome } from '@/router'
import { useAuthStore } from '@/stores/auth'

const LAST_PHONE_KEY = 'ma_last_login_phone'
const router = useRouter(); const route = useRoute(); const auth = useAuthStore()
const mode = ref('sms'); const phone = ref(''); const code = ref(''); const password = ref('')
const challengeId = ref(''); const remember = ref(false); const loading = ref(false); const error = ref('')
const preferredRole = ref('student')
const lastPhone = ref(''); const accountExists = ref(null)
onMounted(() => {
  try { lastPhone.value = localStorage.getItem(LAST_PHONE_KEY) || '' } catch { lastPhone.value = '' }
  // 科研端深链（/research 或 /research-app/…）直达：默认选中"科研端"身份，用户无需手动切换
  const redirect = typeof route.query.redirect === 'string' ? route.query.redirect : ''
  if (redirect.startsWith('/research')) preferredRole.value = 'researcher'
})
watch(phone, () => { accountExists.value = null })
const phoneError = computed(() => phone.value && !/^1[3-9]\d{9}$/.test(phone.value) ? '请输入有效的 11 位手机号' : '')
const phoneMismatch = computed(() => /^1[3-9]\d{9}$/.test(phone.value) && lastPhone.value !== '' && phone.value !== lastPhone.value)
const canSubmit = computed(() => !phoneError.value && /^1[3-9]\d{9}$/.test(phone.value) && (mode.value === 'sms' ? code.value.length === 6 && !!challengeId.value : password.value.length >= 1))
function maskPhone(value) { return value ? `${value.slice(0, 3)}****${value.slice(7)}` : '' }
function fillLastPhone() { phone.value = lastPhone.value }
function onChallenge(id, data) {
  challengeId.value = id
  accountExists.value = typeof data?.account_exists === 'boolean' ? data.account_exists : null
}
const errorCopy = {
  AUTH_PASSWORD_INVALID: '手机号或密码不正确',
  AUTH_PASSWORD_LOCKED: '失败次数过多，请 15 分钟后再试',
  AUTH_CHALLENGE_EXPIRED: '验证码已过期，请重新获取',
  AUTH_CHALLENGE_INVALID: '验证码不正确',
  AUTH_ROLE_NOT_AVAILABLE: '该手机号尚未申请此身份，请先注册或切换为学生端',
}
function showError(value) { error.value = errorCopy[value?.errorKey] || value?.message || '操作失败，请稍后重试' }

function redirectFor(activeRole) {
  const redirect = typeof route.query.redirect === 'string' ? route.query.redirect : ''
  if (!redirect) return roleHome(activeRole)
  const professional = redirect.startsWith('/teacher') || redirect.startsWith('/research') || redirect.startsWith('/admin')
  if (activeRole === 'teacher') return redirect.startsWith('/teacher') ? redirect : roleHome(activeRole)
  if (activeRole === 'researcher') return redirect.startsWith('/research') ? redirect : roleHome(activeRole)
  if (activeRole === 'admin') return redirect.startsWith('/admin') ? redirect : roleHome(activeRole)
  return professional ? roleHome(activeRole) : redirect
}

// 深链登录 → 点「注册新账号」：redirect 不能在跳注册页时丢掉，否则注册完会落错端
const registerLink = computed(() => {
  const redirect = typeof route.query.redirect === 'string' ? route.query.redirect : ''
  return redirect ? { path: '/register', query: { redirect } } : '/register'
})

async function submit() {
  if (!canSubmit.value || loading.value) return
  loading.value = true; error.value = ''
  try {
    const payload = { remember: remember.value, preferred_role: preferredRole.value }
    const data = mode.value === 'sms'
      ? await auth.loginSms({ ...payload, phone: phone.value, challenge_id: challengeId.value, code: code.value })
      : await auth.loginPassword({ ...payload, phone: phone.value, password: password.value })
    lastPhone.value = phone.value
    try { localStorage.setItem(LAST_PHONE_KEY, phone.value) } catch { /* 隐私模式下允许失败 */ }
    const activeRole = data.user?.active_role
    const destination = data.onboarding_required && activeRole === 'student'
        ? '/onboarding/student'
        : redirectFor(activeRole)
    // 科研端深链：整页跳转（location.assign）直达 5176/research-app/… 科研应用；
    // 平台 SPA 的 vue-router 不承载 /research-app 路径，SPA push 会被 catch-all 吞掉。
    if (destination.startsWith('/research')) { window.location.assign(destination); return }
    await router.push(destination)
  } catch (value) { showError(value) } finally { loading.value = false }
}
</script>

<style scoped>
.auth-page { min-height: 100vh; display: grid; grid-template-columns: minmax(320px, 1fr) minmax(380px, 500px); gap: 72px; align-items: center; padding: 64px max(6vw, 32px); background: radial-gradient(circle at 12% 12%, #fef3c7, transparent 36%), linear-gradient(145deg, #f8fafc, #fff7ed); }
.auth-intro { max-width: 650px; }
.brand-mark { width: 54px; height: 54px; display: grid; place-items: center; border-radius: 17px; color: white; font: 900 28px Georgia; background: linear-gradient(135deg, var(--brand), var(--brand2)); box-shadow: var(--shadow-lg); margin-bottom: 24px; }
.eyebrow { color: var(--brand-deep); font-weight: 800; letter-spacing: .08em; font-size: 12px; text-transform: uppercase; }
.auth-intro h1 { font-size: clamp(34px, 4vw, 58px); line-height: 1.12; letter-spacing: -.04em; margin: 10px 0 18px; }
.auth-intro > p { color: var(--ink2); font-size: 17px; max-width: 580px; }
.auth-intro ul { list-style: none; display: grid; gap: 10px; margin-top: 30px; color: var(--ink2); }
.auth-intro li::before { content: '✓'; color: var(--ok); font-weight: 900; margin-right: 10px; }
.auth-card { background: rgba(255,255,255,.94); border: 1px solid rgba(226,232,240,.9); border-radius: 26px; padding: 36px; box-shadow: 0 24px 70px rgba(15,23,42,.12); backdrop-filter: blur(18px); }
.auth-card h2 { font-size: 28px; margin: 4px 0 22px; }
.tabs { display: grid; grid-template-columns: 1fr 1fr; padding: 4px; background: var(--bg2); border-radius: 12px; margin-bottom: 24px; }
.tabs button { border: 0; padding: 10px; border-radius: 9px; background: transparent; color: var(--ink2); cursor: pointer; font-weight: 600; }
.tabs button.active { background: white; color: var(--ink); box-shadow: var(--shadow-sm); }
.role-note { margin-top: 6px; color: var(--ink3); font-size: 12px; }
.last-phone { display: flex; gap: 4px; align-items: center; }
.last-phone-fill { border: 0; background: none; padding: 0; color: var(--brand-deep); font-weight: 700; font-size: 12px; cursor: pointer; }
.warn { padding: 9px 12px; background: #fff7ed; color: var(--warn-deep, #b45309); border-radius: 9px; margin-bottom: 12px; font-size: 13px; }
.remember { display: flex; gap: 8px; align-items: center; color: var(--ink2); font-size: 13px; margin: 4px 0 16px; }
.submit { width: 100%; height: 46px; font-weight: 700; }
.error { padding: 9px 12px; background: var(--err-bg); color: var(--err-deep); border-radius: 9px; margin-bottom: 12px; font-size: 13px; }
.register { text-align: center; margin-top: 20px; color: var(--ink2); }.register a { color: var(--brand-deep); font-weight: 700; }.terms { text-align: center; color: var(--ink3); font-size: 11px; margin-top: 10px; }
@media (max-width: 820px) { .auth-page { grid-template-columns: 1fr; padding: 28px 18px; gap: 28px; }.auth-intro ul { display: none; }.auth-intro h1 { font-size: 32px; }.auth-card { padding: 26px 22px; } }
</style>
