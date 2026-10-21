<template>
  <main class="security-page">
    <header>
      <span class="tag">账号安全</span>
      <h1>登录设备与账号治理</h1>
      <p>管理当前会话、登录凭据、手机号和账号注销。</p>
      <button class="btn btn-danger" data-action="logout-all" @click="logoutEverywhere">退出所有设备</button>
    </header>
    <p v-if="message" class="message" role="status">{{ message }}</p>
    <section class="card">
      <div class="section-title">
        <div><h2>登录设备</h2><p>发现陌生设备时立即撤销。</p></div>
        <button class="btn btn-sm" @click="loadSessions">刷新</button>
      </div>
      <div v-if="sessions.length" class="sessions">
        <article v-for="item in sessions" :key="item.id">
          <div><strong>{{ item.device_name || '未知设备' }}</strong><p>{{ item.current ? '当前设备' : '其他设备' }} · {{ item.last_seen_at || '最近活跃时间未知' }}</p></div>
          <span v-if="item.revoked" class="tag gray">已撤销</span>
          <button v-else-if="!item.current" class="btn btn-danger btn-sm" data-action="revoke-session" @click="revoke(item)">撤销</button>
          <span v-else class="tag green">当前</span>
        </article>
      </div>
      <p v-else class="empty">暂无会话记录</p>
    </section>
    <section class="card">
      <h2>重置密码</h2><p>验证码仅用于本次密码重置，成功后所有设备都会退出。</p>
      <div class="form-grid">
        <input v-model.trim="passwordReset.phone" class="input" aria-label="重置密码手机号" placeholder="手机号" />
        <input v-model.trim="passwordReset.code" class="input" aria-label="重置密码验证码" placeholder="短信验证码" />
        <input v-model="passwordReset.password" class="input" type="password" aria-label="新密码" placeholder="至少 8 位的新密码" />
        <button class="btn" data-action="send-password-reset-code" @click="sendChallenge(passwordReset, passwordReset.phone, 'password_reset')">发送验证码</button>
        <button class="btn btn-primary" data-action="reset-password" @click="resetPassword">确认重置</button>
      </div>
    </section>
    <section class="card">
      <h2>更换手机号</h2><p>需分别验证当前手机号和新手机号，成功后所有设备都会退出。</p>
      <button class="btn" data-action="open-phone-change" @click="phoneOpen = !phoneOpen">{{ phoneOpen ? '收起' : '开始换绑' }}</button>
      <div v-if="phoneOpen" class="form-grid expanded">
        <input v-model.trim="phoneChange.old_phone" class="input" aria-label="当前手机号" placeholder="当前手机号" />
        <input v-model.trim="phoneChange.old_code" class="input" aria-label="当前手机号验证码" placeholder="当前号码验证码" />
        <button class="btn" data-action="send-old-phone-code" @click="sendChallenge(phoneChange, phoneChange.old_phone, 'phone_change_old', 'old_challenge_id')">验证当前号码</button>
        <input v-model.trim="phoneChange.new_phone" class="input" aria-label="新手机号" placeholder="新手机号" />
        <input v-model.trim="phoneChange.new_code" class="input" aria-label="新手机号验证码" placeholder="新号码验证码" />
        <button class="btn" data-action="send-new-phone-code" @click="sendChallenge(phoneChange, phoneChange.new_phone, 'phone_change_new', 'new_challenge_id')">验证新号码</button>
        <button class="btn btn-primary" data-action="submit-phone-change" @click="changePhone">确认换绑</button>
      </div>
    </section>
    <section class="card danger">
      <h2>注销账号</h2><p>申请后进入 7 天冷静期并立即退出普通业务。到期后身份数据会删除或匿名化。</p>
      <template v-if="isDeletionPending">
        <p>冷静期截止：{{ deletion?.execute_after || '待确认' }}</p>
        <div class="form-grid">
          <input v-model.trim="deletionCancel.phone" class="input" aria-label="注销账号手机号" placeholder="原手机号" />
          <input v-model.trim="deletionCancel.code" class="input" aria-label="注销取消验证码" placeholder="短信验证码" />
          <button class="btn" data-action="send-deletion-cancel-code" @click="sendChallenge(deletionCancel, deletionCancel.phone, 'deletion_cancel')">发送验证码</button>
          <button class="btn" data-action="cancel-deletion" @click="cancelDeletion">取消注销</button>
        </div>
      </template>
      <template v-else>
        <div class="form-grid">
          <input v-model.trim="deletionReauth.phone" class="input" aria-label="注销认证手机号" placeholder="当前手机号" />
          <input v-model="deletionReauth.password" class="input" type="password" aria-label="注销认证密码" placeholder="密码" />
          <input v-model.trim="deletionReauth.code" class="input" aria-label="注销认证验证码" placeholder="短信验证码" />
          <button class="btn" @click="sendDeletionReauth">发送验证码</button>
          <button class="btn" @click="completeDeletionReauth">完成近期认证</button>
          <button class="btn btn-danger" data-action="request-deletion" :disabled="!deletionReauth.ready" @click="requestDeletion">申请注销</button>
        </div>
      </template>
    </section>
  </main>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { authApi, securityApi } from '@/api/auth'
import { useAuthStore } from '@/stores/auth'

const props = defineProps({ initialSessions: { type: Array, default: () => [] }, initialDeletion: { type: Object, default: undefined } })
const auth = useAuthStore()
const sessions = ref([...props.initialSessions])
const deletion = ref(props.initialDeletion || null)
const phoneOpen = ref(false)
const message = ref('')
const passwordReset = reactive({ phone: '', code: '', password: '', challenge_id: '' })
const phoneChange = reactive({ old_phone: '', old_code: '', old_challenge_id: '', new_phone: '', new_code: '', new_challenge_id: '' })
const deletionCancel = reactive({ phone: '', code: '', challenge_id: '' })
const deletionReauth = reactive({ phone: '', password: '', code: '', challenge_id: '', ready: false })
const isDeletionPending = computed(() => ['pending', 'cooling_off', 'deletion_pending'].includes(deletion.value?.status))

function showError(error, fallback) { message.value = error?.message || fallback }
async function loadSessions() { try { sessions.value = await securityApi.sessions() } catch (error) { showError(error, '会话列表加载失败') } }
async function loadDeletion() { try { deletion.value = await securityApi.deletionStatus() } catch (error) { showError(error, '注销状态加载失败') } }
async function revoke(item) { try { await securityApi.revokeSession(item.id); item.revoked = true } catch (error) { showError(error, '撤销会话失败') } }
async function sendChallenge(target, phone, purpose, key = 'challenge_id') { try { const data = await authApi.challengeSms(phone, purpose); target[key] = data.challenge_id; message.value = '验证码已发送' } catch (error) { showError(error, '验证码发送失败') } }
async function logoutEverywhere() { try { await auth.logoutAll(); message.value = '已退出所有设备，请重新登录' } catch (error) { showError(error, '退出失败') } }
async function resetPassword() {
  try {
    await authApi.resetPassword({ phone: passwordReset.phone, challenge_id: passwordReset.challenge_id, code: passwordReset.code, password: passwordReset.password })
    try { await auth.logoutAll() } catch { /* reset already revoked the session */ }
    message.value = '密码已重置，请重新登录'
  } catch (error) { showError(error, '密码重置失败') }
}
async function changePhone() {
  try {
    await securityApi.changePhone({ old_challenge_id: phoneChange.old_challenge_id, old_code: phoneChange.old_code, new_phone: phoneChange.new_phone, new_challenge_id: phoneChange.new_challenge_id, new_code: phoneChange.new_code })
    try { await auth.logoutAll() } catch { /* phone change already revoked the session */ }
    message.value = '手机号已更换，请重新登录'
  } catch (error) { showError(error, '手机号更换失败') }
}
async function sendDeletionReauth() { await sendChallenge(deletionReauth, deletionReauth.phone, 'admin_reauth') }
async function completeDeletionReauth() { try { await securityApi.reauthenticate({ password: deletionReauth.password, challenge_id: deletionReauth.challenge_id, code: deletionReauth.code }); deletionReauth.ready = true; message.value = '近期认证已完成' } catch (error) { showError(error, '近期认证失败') } }
async function requestDeletion() {
  if (!deletionReauth.ready || !window.confirm('确认申请注销？将立即退出并进入 7 天冷静期。')) return
  try {
    deletion.value = await securityApi.requestDeletion()
    try { await auth.logoutAll() } catch { /* deletion already revoked the session */ }
    message.value = `注销冷静期至 ${deletion.value.execute_after}，可从登录页取消注销`
  } catch (error) { showError(error, '申请失败') }
}
async function cancelDeletion() { try { await securityApi.cancelDeletion({ phone: deletionCancel.phone, challenge_id: deletionCancel.challenge_id, code: deletionCancel.code }); deletion.value = { status: 'cancelled' }; message.value = '注销申请已取消' } catch (error) { showError(error, '取消注销失败') } }
onMounted(() => { if (!sessions.value.length) loadSessions(); if (props.initialDeletion === undefined) loadDeletion() })
</script>

<style scoped>
.security-page{max-width:920px;margin:0 auto;padding:42px 24px 70px}.security-page header>p,.card>p{color:var(--ink2)}header .btn{margin-top:14px}h1{margin:12px 0 4px}.card{background:white;border:1px solid var(--line);border-radius:18px;padding:24px;margin-top:18px;box-shadow:var(--shadow-sm)}.section-title,.sessions article{display:flex;justify-content:space-between;align-items:center;gap:16px}.sessions{margin-top:14px}.sessions article{padding:14px 0;border-top:1px solid var(--line2)}.sessions p{font-size:12px;color:var(--ink3)}.empty{padding:18px 0}.message{margin-top:16px;padding:12px;border-radius:10px;background:var(--warn-bg);color:var(--warn-deep)}.danger{border-color:var(--err-border)}.form-grid{display:grid;grid-template-columns:minmax(150px,1fr) minmax(150px,1fr) minmax(150px,1fr) auto auto;gap:10px;align-items:center;margin-top:14px}.expanded{grid-template-columns:1fr 1fr auto}.expanded .btn-primary{grid-column:1/-1}@media(max-width:760px){.form-grid,.expanded{grid-template-columns:1fr}.expanded .btn-primary{grid-column:auto}}
</style>
