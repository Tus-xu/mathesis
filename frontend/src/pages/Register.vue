<template>
  <main class="simple-auth">
    <section class="panel">
      <p class="eyebrow">创建账号</p>
      <h1>注册智学数研</h1>
      <p class="hint">完成手机号验证后，立即开通所选的学习、教学或科研端口。</p>
      <form @submit.prevent="submit">
        <div class="form-item">
          <label class="form-label" for="register-role">注册身份</label>
          <select id="register-role" v-model="role" name="role" class="input" aria-label="注册身份">
            <option value="student">学生</option>
            <option value="teacher">教师</option>
            <option value="researcher">科研人员</option>
          </select>
        </div>
        <PhoneField v-model="phone" />
        <OtpField v-model="code" :phone="phone" purpose="registration" @challenge="challengeId = $event" @error="showError" />
        <template v-if="role !== 'student'">
          <div class="form-item">
            <label class="form-label" for="organization-name">学校或机构</label>
            <input id="organization-name" v-model="organizationName" name="organization_name" class="input" autocomplete="organization" :aria-label="'学校或机构'" placeholder="例如：示例中学" />
          </div>
          <div class="form-item">
            <label class="form-label" for="department">院系（选填）</label>
            <input id="department" v-model="department" name="department" class="input" autocomplete="organization-title" aria-label="院系（选填）" placeholder="例如：数学教研组" />
          </div>
          <div v-if="role === 'teacher'" class="form-item">
            <label class="form-label" for="teaching-stage">任教学段</label>
            <input id="teaching-stage" v-model="teachingStage" name="teaching_stage" class="input" aria-label="任教学段" placeholder="例如：高中" />
          </div>
          <div v-if="role === 'teacher'" class="form-item">
            <label class="form-label" for="subject">任教学科</label>
            <input id="subject" v-model="subject" name="subject" class="input" aria-label="任教学科" placeholder="例如：数学" />
          </div>
          <div v-if="role === 'teacher'" class="form-item">
            <label class="form-label" for="staff-id">工号（选填）</label>
            <input id="staff-id" v-model="staffId" name="staff_or_student_id" class="input" aria-label="工号（选填）" placeholder="教师工号" />
          </div>
          <div v-if="role === 'researcher'" class="form-item">
            <label class="form-label" for="research-direction">研究方向</label>
            <input id="research-direction" v-model="researchDirection" name="research_direction" class="input" aria-label="研究方向" placeholder="例如：数学认知诊断" />
          </div>
        </template>
        <label class="agree"><input v-model="agreed" type="checkbox" /> 我已阅读并同意服务协议与隐私政策</label>
        <p v-if="error" class="error" role="alert">{{ error }}</p>
        <button class="btn btn-primary submit" :disabled="loading || !valid">{{ loading ? '创建中…' : '注册并继续' }}</button>
      </form>
      <p class="back"><RouterLink to="/login">已有账号，返回登录</RouterLink></p>
    </section>
  </main>
</template>
<script setup>
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import PhoneField from '@/components/auth/PhoneField.vue'
import OtpField from '@/components/auth/OtpField.vue'
import { roleHome } from '@/router'
import { useAuthStore } from '@/stores/auth'

const CONSENT_VERSION = '2026-08-24'
const phone = ref(''), code = ref(''), challengeId = ref(''), agreed = ref(false), loading = ref(false), error = ref('')
const role = ref('student')
const organizationName = ref(''), department = ref(''), teachingStage = ref(''), subject = ref(''), staffId = ref(''), researchDirection = ref('')
const auth = useAuthStore(), router = useRouter(), route = useRoute()
const errorCopy = {
  AUTH_CHALLENGE_EXPIRED: '验证码已过期，请重新获取',
  AUTH_CHALLENGE_INVALID: '验证码不正确',
  AUTH_CHALLENGE_PURPOSE_MISMATCH: '验证码用途不匹配，请使用注册验证码',
}
function showError(value) { error.value = errorCopy[value?.errorKey] || value?.message || '操作失败，请稍后重试' }
const valid = computed(() => {
  if (!/^1[3-9]\d{9}$/.test(phone.value) || code.value.length !== 6 || !challengeId.value || !agreed.value) return false
  if (role.value !== 'student' && !organizationName.value.trim()) return false
  if (role.value === 'researcher' && !researchDirection.value.trim()) return false
  return true
})
async function submit() {
  if (!valid.value || loading.value) return
  loading.value = true; error.value = ''
  try {
    const payload = { phone: phone.value, challenge_id: challengeId.value, code: code.value, role: role.value, consent_version: CONSENT_VERSION }
    if (role.value === 'teacher') Object.assign(payload, { organization_name: organizationName.value.trim(), teaching_stage: teachingStage.value.trim(), subject: subject.value.trim() })
    if (role.value === 'researcher') Object.assign(payload, { organization_name: organizationName.value.trim(), research_direction: researchDirection.value.trim() })
    if (role.value !== 'student' && department.value.trim()) payload.department = department.value.trim()
    if (role.value === 'teacher' && staffId.value.trim()) payload.staff_or_student_id = staffId.value.trim()
    const data = await auth.registerSms(payload)
    const activeRole = data.user?.active_role
    let destination = data.onboarding_required && activeRole === 'student'
        ? '/onboarding/student'
        : roleHome(activeRole)
    // 深链注册（/login?redirect=/research… → 注册页）：注册成功且身份匹配时直接回到目标页，
    // 与 Login.redirectFor 同语义 —— 否则科研端深链注册完会落错端（重定向丢失）。
    const redirect = typeof route.query.redirect === 'string' ? route.query.redirect : ''
    if (
      redirect && activeRole && !data.onboarding_required &&
      ((activeRole === 'researcher' && redirect.startsWith('/research')) ||
        (activeRole === 'teacher' && redirect.startsWith('/teacher')) ||
        (activeRole === 'admin' && redirect.startsWith('/admin')) ||
        (activeRole === 'student' && !['/teacher', '/research', '/admin'].some((p) => redirect.startsWith(p))))
    ) destination = redirect
    if (destination.startsWith('/research')) { window.location.assign(destination); return }
    await router.push(destination)
  } catch (value) { showError(value) } finally { loading.value = false }
}
</script>
<style scoped>
.simple-auth { min-height: 100vh; display: grid; place-items: center; padding: 24px; background: linear-gradient(145deg, var(--brand-faint), var(--bg)); }
.panel { width: min(460px, 100%); background: white; border: 1px solid var(--line); border-radius: 24px; padding: 34px; box-shadow: var(--shadow-lg); max-height: 92vh; overflow: auto; }
h1 { margin: 5px 0 8px; }
.eyebrow { color: var(--brand-deep); font-weight: 800; font-size: 12px; }
.hint { color: var(--ink2); margin-bottom: 24px; }
.agree { display: flex; gap: 8px; font-size: 12px; color: var(--ink2); margin: 12px 0; }
.submit { width: 100%; height: 44px; }
.back { text-align: center; margin-top: 18px; }
.back a { color: var(--brand-deep); }
.error { padding: 9px 12px; background: var(--err-bg); color: var(--err-deep); border-radius: 9px; margin-bottom: 12px; font-size: 13px; }
</style>
