<template>
  <div class="ra-page" data-research-app>
    <ResearchHomeHeader active="account" />

    <main class="ra-main">
      <section class="ra-profile">
        <div class="ra-avatar">
          <img v-if="avatarUrl" :src="avatarUrl" alt="" />
          <span v-else>{{ avatarChar }}</span>
        </div>
        <div class="ra-profile-copy">
          <div class="ra-name">{{ displayName }}<span class="ra-role">{{ roleLabel }}</span></div>
          <div class="ra-meta">
            <span v-if="user?.phone_masked">📱 {{ user.phone_masked }}</span>
            <span v-if="orgName">🏛 {{ orgName }}</span>
            <span v-if="joinedText">📅 {{ joinedText }} 加入</span>
            <span v-if="loadError" class="ra-error">资料刷新失败，显示本地缓存</span>
          </div>
        </div>
        <div class="ra-profile-actions">
          <button type="button" class="ra-btn ra-btn--primary" @click="router.push('/research-workspace/workspace')">进入科研工作台</button>
          <button type="button" class="ra-btn" @click="onLogout">退出登录</button>
        </div>
      </section>

      <nav class="ra-tabs" aria-label="个人中心分类">
        <button type="button" :class="{ active: tab === 'profile' }" @click="setTab('profile')">个人资料</button>
        <button type="button" :class="{ active: tab === 'security' }" @click="setTab('security')">账号安全</button>
        <button type="button" :class="{ active: tab === 'overview' }" @click="setTab('overview')">科研概览</button>
      </nav>

      <!-- 个人资料 -->
      <section v-if="tab === 'profile'" class="ra-grid">
        <div class="ra-card">
          <div class="ra-card-head"><h2>账号信息</h2><span>来自统一身份认证</span></div>
          <dl class="ra-fields">
            <div><dt>昵称</dt><dd>{{ displayName }}</dd></div>
            <div><dt>手机号</dt><dd>{{ user?.phone_masked || '—' }}</dd></div>
            <div><dt>当前身份</dt><dd>{{ roleLabel }}</dd></div>
            <div><dt>账号 ID</dt><dd class="mono">{{ user?.id || '—' }}</dd></div>
            <div><dt>加入时间</dt><dd>{{ joinedFull || '—' }}</dd></div>
            <div><dt>账号状态</dt><dd>{{ statusLabel }}</dd></div>
          </dl>
          <p class="ra-hint">修改手机号、注销账号等全局设置请前往 <a href="/account/security">账号与安全</a>。</p>
        </div>

        <div class="ra-card">
          <div class="ra-card-head"><h2>身份与机构</h2><span>已批准的身份才能切换</span></div>
          <div v-if="approvedRoles.length" class="ra-roles">
            <div v-for="r in approvedRoles" :key="r.role" class="ra-role-row" :class="{ current: r.role === auth.activeRole }">
              <div>
                <b>{{ roleName(r.role) }}</b>
                <span>{{ r.org_name || '未绑定机构' }}</span>
              </div>
              <span v-if="r.role === auth.activeRole" class="ra-badge">当前</span>
              <button v-else type="button" :disabled="switching === r.role" @click="switchRole(r.role)">
                {{ switching === r.role ? '切换中…' : '切换' }}
              </button>
            </div>
          </div>
          <p v-else class="ra-hint">还没有已批准的身份绑定。</p>
          <p class="ra-hint">科研端与讲台、学生端共享同一账号；切换身份会进入对应端口。</p>
        </div>

        <div class="ra-card ra-card--wide">
          <div class="ra-card-head"><h2>快捷入口</h2><span>常用动作</span></div>
          <div class="ra-quick">
            <button type="button" @click="router.push('/research-workspace/workspace')"><b>进入科研工作台</b><span>继续最近项目</span></button>
            <button type="button" @click="router.push('/research-workspace')"><b>科研启动页</b><span>最近项目与三个起点</span></button>
            <button type="button" @click="setTab('security')"><b>账号安全</b><span>密码与登录设备</span></button>
            <a href="/account/security"><b>全局账号设置</b><span>手机号 / 注销</span></a>
          </div>
        </div>
      </section>

      <!-- 账号安全 -->
      <section v-else-if="tab === 'security'" class="ra-grid">
        <div class="ra-card">
          <div class="ra-card-head"><h2>修改密码</h2><span>至少 15 个字符</span></div>
          <form class="ra-form" @submit.prevent="submitPassword">
            <label>
              <span>新密码</span>
              <input v-model="newPassword" type="password" autocomplete="new-password" placeholder="至少 15 个字符" />
            </label>
            <label>
              <span>确认新密码</span>
              <input v-model="confirmPassword" type="password" autocomplete="new-password" placeholder="再次输入" />
            </label>
            <p v-if="pwdError" class="ra-form-error">{{ pwdError }}</p>
            <button type="submit" class="ra-btn ra-btn--primary" :disabled="pwdSaving">
              {{ pwdSaving ? '正在保存…' : '设置新密码' }}
            </button>
          </form>
          <p class="ra-hint">设置密码后，可在登录页选择「密码登录」。忘记密码请从登录页重置。</p>
        </div>

        <div class="ra-card">
          <div class="ra-card-head">
            <h2>登录设备</h2>
            <button type="button" class="ra-link" :disabled="sessionsLoading" @click="loadSessions">刷新</button>
          </div>
          <div v-if="sessionsLoading" class="ra-loading">设备加载中…</div>
          <div v-else-if="sessionsError" class="ra-inline-error">{{ sessionsError }} <button type="button" @click="loadSessions">重试</button></div>
          <div v-else-if="!sessions.length" class="ra-hint">暂无登录设备记录。</div>
          <div v-else class="ra-sessions">
            <div v-for="s in sessions" :key="s.id" class="ra-session">
              <div class="ra-session-main">
                <b>{{ s.device_name || '未知设备' }} <span v-if="s.current" class="ra-badge">当前</span></b>
                <span>{{ roleName(s.active_role) }} · {{ s.ip_prefix || 'IP 未记录' }} · {{ fmtTime(s.last_seen_at) }}</span>
              </div>
              <button v-if="!s.current && !s.revoked" type="button" :disabled="revoking === s.id" @click="revoke(s)">
                {{ revoking === s.id ? '退出中…' : '退出该设备' }}
              </button>
            </div>
          </div>
          <div class="ra-danger-row">
            <div><b>退出全部设备</b><span>会结束所有设备上的登录，需要重新验证。</span></div>
            <button type="button" class="ra-btn ra-btn--danger" :disabled="loggingOutAll" @click="logoutAll">
              {{ loggingOutAll ? '正在退出…' : '退出全部' }}
            </button>
          </div>
        </div>

        <div class="ra-card ra-card--wide">
          <div class="ra-card-head"><h2>更多账号设置</h2><span>统一身份认证</span></div>
          <p class="ra-hint ra-hint--flat">更换绑定手机、下载个人数据、申请注销账号等全局安全操作，在平台统一的账号与安全页完成。</p>
          <a class="ra-more-link" href="/account/security">前往账号与安全 →</a>
        </div>
      </section>

      <!-- 科研概览 -->
      <section v-else class="ra-overview">
        <div class="ra-stats">
          <div><b>{{ aggregate.projects }}</b><span>项目</span></div>
          <div><b>{{ aggregate.papers }}</b><span>文献</span></div>
          <div><b>{{ aggregate.drafts }}</b><span>草稿</span></div>
          <div><b>{{ aggregate.notes }}</b><span>笔记</span></div>
        </div>
        <div class="ra-card">
          <div class="ra-card-head"><h2>最近项目</h2><button type="button" class="ra-link" @click="router.push('/research-workspace')">启动页</button></div>
          <div v-if="projectsLoading" class="ra-loading">项目加载中…</div>
          <div v-else-if="!projects.length" class="ra-hint">还没有项目。去启动页创建第一个 Research Project。</div>
          <div v-else class="ra-projects">
            <div v-for="p in projects.slice(0, 5)" :key="p.id" class="ra-project">
              <div>
                <b>{{ p.name }}</b>
                <span>{{ p.description || '暂无描述' }} · {{ fmtTime(p.updated_at) }}</span>
              </div>
              <div class="ra-project-actions">
                <button type="button" @click="openProject(p, 'read')">阅读</button>
                <button type="button" @click="openProject(p, 'write')">写作</button>
              </div>
            </div>
          </div>
        </div>
        <p class="ra-hint ra-hint--center">统计只展示真实项目数据；没有数据的项目显示为 —。</p>
      </section>
    </main>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import ResearchHomeHeader from './ResearchHomeHeader.vue'
import { useAuthStore } from '@/stores/auth'
import { useRwProjectStore } from '../stores/project'
import { useToastStore } from '@/stores/toast'
import { useConfirm } from '@/composables/useConfirm'
import { authApi, securityApi } from '@/api/auth'
import { roleHome } from '@/router'
import type { ResearchProject, WorkspaceMode } from '../api/types'
import '../tokens/research-tokens.css'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const projectStore = useRwProjectStore()
const toast = useToastStore()
const { confirm } = useConfirm()

const LAST_PROJECT_KEY = 'ma_research_last_project'
const LAST_WORKSPACE_KEY = 'ma_research_last_workspace'
const validTabs = ['profile', 'security', 'overview']
const tab = ref(validTabs.includes(String(route.query.tab || '')) ? String(route.query.tab) : 'profile')

const loadError = ref(false)
const newPassword = ref('')
const confirmPassword = ref('')
const pwdError = ref('')
const pwdSaving = ref(false)
const sessions = ref<any[]>([])
const sessionsLoading = ref(false)
const sessionsError = ref('')
const revoking = ref('')
const loggingOutAll = ref(false)
const switching = ref('')
const projectsLoading = ref(false)

const user = computed<any>(() => auth.user)
const avatarUrl = computed(() => String(user.value?.avatar_url || ''))
const displayName = computed(() => String(user.value?.nickname || '').trim() || '研究者')
const avatarChar = computed(() => displayName.value.slice(0, 1))
const roleLabel = computed(() => roleName(auth.activeRole))
const approvedRoles = computed<any[]>(() => auth.approvedRoleBindings || [])
const orgName = computed(() => approvedRoles.value.find((r: any) => r.role === auth.activeRole)?.org_name || '')
const joinedFull = computed(() => fmtDate(user.value?.created_at))
const joinedText = computed(() => {
  const d = user.value?.created_at ? new Date(user.value.created_at) : null
  return d && !Number.isNaN(d.getTime()) ? `${d.getFullYear()}/${d.getMonth() + 1}` : ''
})
const statusLabel = computed(() => ({
  authenticated: '正常',
  onboarding: '待完善资料',
  pending_review: '身份审核中',
  deletion_pending: '注销处理中',
})[auth.status] || auth.status || '—')
const projects = computed(() => projectStore.list)
const aggregate = computed(() => {
  const acc = { projects: projects.value.length, papers: 0, drafts: 0, notes: 0 }
  for (const p of projects.value) {
    acc.papers += Number(p.counts?.papers) || 0
    acc.drafts += Number(p.counts?.drafts) || 0
    acc.notes += Number(p.counts?.notes) || 0
  }
  return acc
})

function roleName(role?: string | null) {
  return ({ researcher: '科研端 · 研究者', student: '学生端', teacher: '教师端', admin: '管理端' })[role || ''] || '未识别身份'
}
function fmtDate(iso?: string) {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  return `${d.getFullYear()}/${String(d.getMonth() + 1).padStart(2, '0')}/${String(d.getDate()).padStart(2, '0')}`
}
function fmtTime(iso?: string) {
  if (!iso) return '时间未记录'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '时间未记录'
  const min = Math.floor((Date.now() - d.getTime()) / 60000)
  if (min < 1) return '刚刚'
  if (min < 60) return `${min} 分钟前`
  const h = Math.floor(min / 60)
  if (h < 24) return `${h} 小时前`
  return `${d.getMonth() + 1}月${d.getDate()}日`
}
function setTab(t: string) {
  tab.value = t
  router.replace({ query: { ...route.query, tab: t } })
  if (t === 'security' && !sessions.value.length) loadSessions()
}

async function loadSessions() {
  sessionsLoading.value = true
  sessionsError.value = ''
  try {
    sessions.value = await securityApi.sessions()
  } catch (e: any) {
    sessionsError.value = e?.message || '登录设备加载失败'
  } finally {
    sessionsLoading.value = false
  }
}

async function submitPassword() {
  pwdError.value = ''
  if (newPassword.value.length < 15) {
    pwdError.value = '密码至少需要 15 个字符'
    return
  }
  if (newPassword.value !== confirmPassword.value) {
    pwdError.value = '两次输入的密码不一致'
    return
  }
  pwdSaving.value = true
  try {
    await authApi.setPassword(newPassword.value)
    newPassword.value = ''
    confirmPassword.value = ''
    toast.success('密码已设置，下次可使用密码登录')
  } catch (e: any) {
    pwdError.value = e?.message || '密码设置失败'
  } finally {
    pwdSaving.value = false
  }
}

async function revoke(s: any) {
  const ok = await confirm({ title: '退出该设备', message: '确定让该设备上的登录失效吗？', confirmText: '退出设备', danger: true })
  if (!ok) return
  revoking.value = s.id
  try {
    await securityApi.revokeSession(s.id)
    toast.success('该设备已退出')
    await loadSessions()
  } catch (e: any) {
    toast.error(e?.message || '退出设备失败')
  } finally {
    revoking.value = ''
  }
}

async function switchRole(role: string) {
  if (switching.value) return
  switching.value = role
  try {
    await auth.switchRole(role)
    router.push(roleHome(role))
  } catch (e: any) {
    toast.error(e?.message || '身份切换失败')
  } finally {
    switching.value = ''
  }
}

function openProject(p: ResearchProject, mode: WorkspaceMode) {
  try {
    localStorage.setItem(LAST_PROJECT_KEY, p.id)
    localStorage.setItem(LAST_WORKSPACE_KEY, mode)
  } catch { /* 隐私模式忽略 */ }
  router.push(`/research-workspace/${p.id}/${mode}`)
}

async function onLogout() {
  const ok = await confirm({ title: '退出登录', message: '确定退出当前账号吗？' })
  if (!ok) return
  try { await auth.logout() } catch { /* 忽略网络失败 */ }
  window.location.assign('/login')
}
async function logoutAll() {
  const ok = await confirm({ title: '退出全部设备', message: '所有设备上的登录都会失效，需要重新验证。', confirmText: '全部退出', danger: true })
  if (!ok) return
  loggingOutAll.value = true
  try { await auth.logoutAll() } catch { /* 忽略 */ }
  window.location.assign('/login')
}

async function load() {
  const results = await Promise.allSettled([
    auth.refreshMe(),
    projectStore.loadList(),
  ])
  loadError.value = results[0].status === 'rejected'
  projectsLoading.value = false
  if (tab.value === 'security') loadSessions()
}

onMounted(() => {
  projectsLoading.value = true
  void load()
})
</script>

<style scoped>
.ra-page { min-height: 100vh; background: var(--rw-app-bg); color: var(--rw-text); }
.ra-main { max-width: 1080px; margin: 0 auto; padding: 34px 28px 64px; }
.ra-profile {
  display: flex; align-items: center; gap: 16px; flex-wrap: wrap;
  padding: 20px 22px; border: 1px solid var(--rw-border); border-radius: 18px;
  background: linear-gradient(120deg, rgba(238,242,255,.8), rgba(255,255,255,.96) 45%), #fff;
  box-shadow: var(--rw-shadow-card);
}
.ra-avatar { width: 66px; height: 66px; flex: none; border-radius: 50%; overflow: hidden; display: grid; place-items: center; background: var(--gradient-brand); color: #fff; font-size: 25px; font-weight: 900; box-shadow: 0 10px 24px -12px rgba(79,70,229,.55); }
.ra-avatar img { width: 100%; height: 100%; object-fit: cover; }
.ra-profile-copy { flex: 1; min-width: 200px; }
.ra-name { display: flex; align-items: center; gap: 9px; font-size: 21px; font-weight: 900; color: var(--rw-text); }
.ra-role { font-size: 9.5px; font-weight: 800; padding: 2px 8px; border-radius: 999px; background: var(--rw-primary-soft); color: var(--rw-primary); }
.ra-meta { display: flex; flex-wrap: wrap; gap: 12px; margin-top: 7px; font-size: 11px; color: var(--rw-text-3); }
.ra-error { color: var(--rw-danger); }
.ra-profile-actions { display: flex; gap: 8px; flex-wrap: wrap; }
.ra-btn { height: 34px; padding: 0 14px; border: 1px solid var(--rw-border-strong); border-radius: 9px; background: var(--rw-surface); color: var(--rw-text-2); font: inherit; font-size: 11.5px; font-weight: 700; cursor: pointer; }
.ra-btn:hover:not(:disabled) { border-color: var(--rw-primary); color: var(--rw-primary); }
.ra-btn--primary { border-color: transparent; background: var(--gradient-brand); color: #fff; }
.ra-btn--primary:hover:not(:disabled) { color: #fff; filter: brightness(1.04); }
.ra-btn--danger { color: var(--rw-danger); border-color: #fecaca; }
.ra-btn:disabled { opacity: .55; cursor: wait; }
.ra-tabs { display: flex; gap: 4px; margin: 20px 0 14px; padding-bottom: 10px; border-bottom: 1px solid var(--rw-border); }
.ra-tabs button { height: 32px; padding: 0 14px; border: 0; border-radius: 999px; background: transparent; color: var(--rw-text-2); font: inherit; font-size: 12px; font-weight: 600; cursor: pointer; }
.ra-tabs button:hover { background: var(--rw-surface-2); }
.ra-tabs button.active { background: var(--rw-primary-soft); color: var(--rw-primary); font-weight: 800; }
.ra-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }
.ra-card { padding: 18px 19px; border: 1px solid var(--rw-border); border-radius: 15px; background: var(--rw-surface); }
.ra-card-head { display: flex; align-items: center; justify-content: space-between; gap: 10px; margin-bottom: 12px; }
.ra-card-head h2 { margin: 0; font-size: 14px; font-weight: 800; color: var(--rw-text); }
.ra-card-head > span { font-size: 9.5px; color: var(--rw-text-3); }
.ra-fields { display: flex; flex-direction: column; gap: 0; margin: 0; }
.ra-fields > div { display: flex; justify-content: space-between; gap: 14px; padding: 9px 0; border-bottom: 1px dashed var(--rw-border); }
.ra-fields > div:last-child { border-bottom: 0; }
.ra-fields dt { font-size: 11px; color: var(--rw-text-3); }
.ra-fields dd { margin: 0; font-size: 11.5px; font-weight: 600; color: var(--rw-text); text-align: right; }
.ra-fields dd.mono { font-family: var(--rw-font-mono); font-size: 10px; color: var(--rw-text-2); }
.ra-hint { margin: 12px 0 0; font-size: 10.5px; line-height: 1.6; color: var(--rw-text-3); }
.ra-hint--flat { margin-top: 0; }
.ra-card--wide { grid-column: 1 / -1; }
.ra-quick { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px; }
.ra-quick button, .ra-quick a { display: flex; flex-direction: column; gap: 3px; padding: 11px 12px; border: 1px solid var(--rw-border); border-radius: 10px; background: var(--rw-surface); text-align: left; text-decoration: none; cursor: pointer; font: inherit; }
.ra-quick button:hover, .ra-quick a:hover { border-color: var(--rw-primary); background: var(--rw-primary-soft); }
.ra-quick b { font-size: 11.5px; color: var(--rw-text); }
.ra-quick span { font-size: 9.5px; color: var(--rw-text-3); }
.ra-more-link { display: inline-block; margin-top: 10px; font-size: 11px; font-weight: 800; color: var(--rw-primary); text-decoration: none; }
.ra-hint a { color: var(--rw-primary); font-weight: 700; }
.ra-hint--center { text-align: center; }
.ra-roles { display: flex; flex-direction: column; gap: 7px; }
.ra-role-row { display: flex; align-items: center; gap: 10px; padding: 9px 10px; border: 1px solid var(--rw-border); border-radius: 10px; }
.ra-role-row.current { border-color: var(--rw-primary); background: var(--rw-primary-soft); }
.ra-role-row > div { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
.ra-role-row b { font-size: 12px; color: var(--rw-text); }
.ra-role-row span { font-size: 10px; color: var(--rw-text-3); }
.ra-badge { flex: none; font-size: 9px; font-weight: 800; padding: 1px 7px; border-radius: 999px; background: var(--rw-primary); color: #fff; }
.ra-role-row button { flex: none; height: 26px; padding: 0 9px; border: 1px solid var(--rw-border); border-radius: 7px; background: var(--rw-surface); color: var(--rw-text-2); font: inherit; font-size: 10px; font-weight: 700; cursor: pointer; }
.ra-role-row button:hover:not(:disabled) { border-color: var(--rw-primary); color: var(--rw-primary); }
.ra-form { display: flex; flex-direction: column; gap: 10px; }
.ra-form label { display: flex; flex-direction: column; gap: 5px; }
.ra-form label > span { font-size: 10.5px; color: var(--rw-text-3); }
.ra-form input { height: 36px; padding: 0 11px; border: 1px solid var(--rw-border); border-radius: 9px; background: var(--rw-surface); font: inherit; font-size: 12px; color: var(--rw-text); outline: none; }
.ra-form input:focus { border-color: var(--rw-primary); box-shadow: 0 0 0 3px rgba(79,70,229,.1); }
.ra-form-error { margin: 0; padding: 7px 9px; border-radius: 8px; background: var(--rw-danger-soft); color: var(--rw-danger); font-size: 10.5px; }
.ra-form .ra-btn { align-self: flex-start; }
.ra-link { border: 0; background: none; padding: 0; color: var(--rw-primary); font: inherit; font-size: 10.5px; font-weight: 700; cursor: pointer; }
.ra-loading { padding: 14px; text-align: center; color: var(--rw-text-3); font-size: 11px; }
.ra-inline-error { padding: 10px; border-radius: 8px; background: var(--rw-danger-soft); color: var(--rw-danger); font-size: 10.5px; }
.ra-inline-error button { margin-left: 6px; border: 0; background: none; color: inherit; font: inherit; font-weight: 800; cursor: pointer; }
.ra-sessions { display: flex; flex-direction: column; gap: 6px; }
.ra-session { display: flex; align-items: center; gap: 10px; padding: 9px 10px; border: 1px solid var(--rw-border); border-radius: 9px; }
.ra-session-main { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
.ra-session-main b { font-size: 11.5px; color: var(--rw-text); display: flex; align-items: center; gap: 6px; }
.ra-session-main span { font-size: 9.5px; color: var(--rw-text-3); }
.ra-session > button { flex: none; height: 26px; padding: 0 8px; border: 1px solid var(--rw-border); border-radius: 7px; background: var(--rw-surface); color: var(--rw-text-2); font: inherit; font-size: 9.5px; font-weight: 700; cursor: pointer; }
.ra-session > button:hover:not(:disabled) { border-color: var(--rw-danger); color: var(--rw-danger); }
.ra-danger-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-top: 14px; padding-top: 12px; border-top: 1px dashed var(--rw-border); }
.ra-danger-row > div { display: flex; flex-direction: column; gap: 2px; }
.ra-danger-row b { font-size: 11px; color: var(--rw-text); }
.ra-danger-row span { font-size: 9.5px; color: var(--rw-text-3); }
.ra-overview { display: flex; flex-direction: column; gap: 14px; }
.ra-stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; }
.ra-stats > div { padding: 16px; border: 1px solid var(--rw-border); border-radius: 13px; background: var(--rw-surface); }
.ra-stats b { display: block; font-size: 24px; font-weight: 900; color: var(--rw-text); font-family: var(--rw-font-mono); line-height: 1.1; }
.ra-stats span { font-size: 10px; color: var(--rw-text-3); }
.ra-projects { display: flex; flex-direction: column; gap: 6px; }
.ra-project { display: flex; align-items: center; gap: 10px; padding: 9px 10px; border: 1px solid var(--rw-border); border-radius: 9px; }
.ra-project > div:first-child { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
.ra-project b { font-size: 11.5px; color: var(--rw-text); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.ra-project span { font-size: 9.5px; color: var(--rw-text-3); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.ra-project-actions { display: flex; gap: 5px; flex: none; }
.ra-project-actions button { height: 25px; padding: 0 8px; border: 1px solid var(--rw-border); border-radius: 6px; background: var(--rw-surface); color: var(--rw-text-2); font: inherit; font-size: 9.5px; font-weight: 700; cursor: pointer; }
.ra-project-actions button:hover { border-color: var(--rw-primary); color: var(--rw-primary); }
@media (max-width: 860px) {
  .ra-main { padding: 24px 16px 48px; }
  .ra-grid { grid-template-columns: 1fr; }
  .ra-stats { grid-template-columns: repeat(2, 1fr); }
  .ra-profile-actions { width: 100%; }
  .ra-quick { grid-template-columns: repeat(2, 1fr); }
}
</style>
