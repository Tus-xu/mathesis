<template>
  <div class="adl-layout">
    <header class="adl-topbar">
      <button class="adl-icobtn" type="button" :aria-expanded="navOpen" aria-label="切换导航" @click="navOpen = !navOpen">☰</button>
      <div class="adl-brand">
        <span class="adl-mark" aria-hidden="true">π</span>
        <span class="adl-brand-name">智学数研</span>
        <span class="adl-sub">管理后台</span>
      </div>
      <div class="adl-spacer"></div>
      <span class="adl-role muted">{{ auth.nickname }}</span>
      <router-link class="adl-icobtn" to="/overview" title="返回学生端">🏠</router-link>
    </header>

    <aside class="adl-nav" :class="{ open: navOpen }" aria-hidden="false">
      <AdminNav />
    </aside>
    <div v-if="navOpen" class="adl-scrim" @click="navOpen = false"></div>

    <main class="adl-main">
      <RouterView class="adl-view" />
    </main>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useAuthStore } from '@/stores/auth'
import AdminNav from '@/components/admin/AdminNav.vue'

const auth = useAuthStore()
const navOpen = ref(false)
</script>

<style scoped>
.adl-layout { display: grid; grid-template-columns: 220px 1fr; grid-template-rows: 56px 1fr; min-height: 100vh; }
.adl-topbar {
  grid-column: 1 / -1; display: flex; align-items: center; gap: 12px;
  padding: 0 16px; border-bottom: 1px solid var(--border, #e5e7eb);
  background: var(--bg-card, #fff);
}
.adl-icobtn {
  border: none; background: none; font-size: 18px; cursor: pointer;
  padding: 6px; border-radius: 6px; color: var(--text-secondary, #4b5563);
  text-decoration: none; display: inline-flex;
}
.adl-icobtn:hover { background: var(--bg-hover, #f3f4f6); }
.adl-brand { display: flex; align-items: center; gap: 8px; }
.adl-mark {
  width: 28px; height: 28px; border-radius: 8px; display: grid; place-items: center;
  background: var(--primary, #2563eb); color: #fff; font-weight: 700;
}
.adl-brand-name { font-weight: 600; }
.adl-sub { font-size: 12px; color: var(--text-secondary, #6b7280); }
.adl-spacer { flex: 1; }
.adl-role { font-size: 13px; }
.adl-nav { border-right: 1px solid var(--border, #e5e7eb); background: var(--bg-card, #fff); }
.adl-main { padding: 20px; overflow-y: auto; background: var(--bg-page, #f8fafc); }
@media (max-width: 768px) {
  .adl-layout { grid-template-columns: 1fr; }
  .adl-nav { position: fixed; left: 0; top: 56px; bottom: 0; width: 220px; transform: translateX(-100%); transition: transform 0.2s; z-index: 30; }
  .adl-nav.open { transform: translateX(0); }
  .adl-scrim { position: fixed; inset: 56px 0 0 0; background: rgba(0,0,0,0.3); z-index: 20; }
}
</style>
