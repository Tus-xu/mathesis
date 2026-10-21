<template>
  <header class="rh-head">
    <div class="rh-brand" role="link" tabindex="0" @click="go('/research-workspace')" @keydown.enter="go('/research-workspace')">
      <span class="rh-mark">π</span>
      <span class="rh-name">智学数研</span>
      <span class="rh-sub">科研端</span>
    </div>

    <nav class="rh-nav" aria-label="科研端导航">
      <button type="button" :class="{ active: active === 'home' }" @click="go('/research-workspace')">启动页</button>
      <button type="button" :class="{ active: active === 'workspace' }" @click="go('/research-workspace/workspace')">工作台</button>
      <button type="button" :class="{ active: active === 'account' }" @click="go('/research-workspace/account')">个人中心</button>
    </nav>

    <div class="rh-right">
      <span class="rh-role">科研身份 · {{ roleLabel }}</span>
      <div class="rh-user-wrap">
        <button type="button" class="rh-user" :aria-expanded="menuOpen" aria-haspopup="menu" @click.stop="menuOpen = !menuOpen">
          <span class="rh-avatar">
            <img v-if="avatarUrl" :src="avatarUrl" alt="" />
            <span v-else>{{ avatarChar }}</span>
          </span>
          <span class="rh-uname">{{ displayName }}</span>
          <span class="rh-chevron">⌄</span>
        </button>
        <div v-if="menuOpen" class="rh-menu" role="menu" @click.stop>
          <div class="rh-menu-head">
            <b>{{ displayName }}</b>
            <span>{{ user?.phone_masked || roleLabel }}</span>
          </div>
          <button type="button" role="menuitem" @click="go('/research-workspace/account')">个人中心</button>
          <button type="button" role="menuitem" @click="go('/research-workspace/account?tab=security')">账号与安全</button>
          <button type="button" role="menuitem" @click="go('/research-workspace/workspace')">进入科研工作台</button>
          <button type="button" role="menuitem" class="danger" :disabled="loggingOut" @click="onLogout">
            {{ loggingOut ? '正在退出…' : '退出登录' }}
          </button>
        </div>
      </div>
    </div>
  </header>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

withDefaults(defineProps<{ active?: 'home' | 'workspace' | 'account' }>(), { active: 'home' })

const router = useRouter()
const auth = useAuthStore()
const menuOpen = ref(false)
const loggingOut = ref(false)

const user = computed<any>(() => auth.user)
const ROLE_MAP: Record<string, string> = { researcher: '研究者', student: '学生', teacher: '教师', admin: '管理员' }
const roleLabel = computed(() => ROLE_MAP[String(auth.activeRole || '')] || '研究者')
const avatarUrl = computed(() => String(user.value?.avatar_url || ''))
const displayName = computed(() => String(user.value?.nickname || '').trim() || '研究者')
const avatarChar = computed(() => displayName.value.slice(0, 1))

function go(path: string) {
  menuOpen.value = false
  router.push(path)
}
async function onLogout() {
  if (loggingOut.value) return
  loggingOut.value = true
  try { await auth.logout() } catch { /* 网络失败也清空本地会话 */ }
  window.location.assign('/login')
}
function onDocClick() { menuOpen.value = false }
onMounted(() => document.addEventListener('click', onDocClick))
onBeforeUnmount(() => document.removeEventListener('click', onDocClick))
</script>

<style scoped>
.rh-head {
  position: sticky; top: 0; z-index: 40;
  display: flex; align-items: center; gap: 22px;
  height: 64px; padding: 0 28px;
  background: rgba(255, 255, 255, .9); border-bottom: 1px solid var(--rw-border);
  backdrop-filter: blur(14px);
}
.rh-brand { display: flex; align-items: center; gap: 9px; cursor: pointer; flex: none; }
.rh-mark {
  width: 34px; height: 34px; display: grid; place-items: center;
  border-radius: 10px; background: var(--gradient-brand); color: #fff;
  font: 900 20px Georgia, serif; box-shadow: 0 6px 16px -9px rgba(79,70,229,.6);
}
.rh-name { font-size: 16px; font-weight: 900; color: var(--rw-text); letter-spacing: .01em; }
.rh-sub { font-size: 10px; font-weight: 700; color: var(--rw-text-3); padding: 2px 7px; border-radius: 999px; background: var(--rw-surface-2); }
.rh-nav { display: flex; gap: 4px; flex: 1; }
.rh-nav button {
  height: 32px; padding: 0 13px; border: 0; border-radius: 999px; background: transparent;
  color: var(--rw-text-2); font: inherit; font-size: 12.5px; font-weight: 600; cursor: pointer;
}
.rh-nav button:hover { background: var(--rw-surface-2); color: var(--rw-text); }
.rh-nav button.active { background: var(--rw-primary-soft); color: var(--rw-primary); font-weight: 800; }
.rh-right { display: flex; align-items: center; gap: 14px; flex: none; }
.rh-role { font-size: 10.5px; color: var(--rw-text-3); }
.rh-user-wrap { position: relative; }
.rh-user {
  display: inline-flex; align-items: center; gap: 7px; height: 38px; padding: 0 6px 0 2px;
  border: 0; border-radius: 999px; background: transparent; cursor: pointer;
}
.rh-user:hover { background: var(--rw-surface-2); }
.rh-avatar {
  width: 30px; height: 30px; border-radius: 50%; overflow: hidden; display: grid; place-items: center;
  background: var(--rw-primary-soft); color: var(--rw-primary); font-size: 12px; font-weight: 800;
}
.rh-avatar img { width: 100%; height: 100%; object-fit: cover; }
.rh-uname { max-width: 120px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 12.5px; font-weight: 600; color: var(--rw-text); }
.rh-chevron { font-size: 13px; color: var(--rw-text-3); }
.rh-menu {
  position: absolute; right: 0; top: calc(100% + 8px); width: 220px; padding: 6px;
  background: #fff; border: 1px solid var(--rw-border); border-radius: 12px;
  box-shadow: var(--rw-shadow-pop); z-index: 50;
}
.rh-menu-head { display: flex; flex-direction: column; gap: 2px; padding: 8px 9px 9px; border-bottom: 1px solid var(--rw-border); margin-bottom: 5px; }
.rh-menu-head b { font-size: 12.5px; color: var(--rw-text); }
.rh-menu-head span { font-size: 10.5px; color: var(--rw-text-3); }
.rh-menu button { width: 100%; height: 33px; padding: 0 9px; border: 0; border-radius: 8px; background: none; color: var(--rw-text-2); font: inherit; font-size: 12px; text-align: left; cursor: pointer; }
.rh-menu button:hover { background: var(--rw-surface-2); color: var(--rw-text); }
.rh-menu button.danger { color: var(--rw-danger); }
@media (max-width: 760px) {
  .rh-head { padding: 0 14px; gap: 10px; }
  .rh-sub, .rh-role, .rh-uname { display: none; }
  .rh-nav { justify-content: flex-end; }
  .rh-nav button { padding: 0 9px; font-size: 11.5px; }
}
</style>
