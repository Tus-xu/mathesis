<template>
  <nav class="anav" aria-label="管理后台导航">
    <router-link
      v-for="item in adminNav"
      :key="item.to"
      :to="item.to"
      class="anav-item"
      :class="{ active: $route?.path === item.to }"
    >
      <span class="anav-icon" aria-hidden="true">{{ item.icon }}</span>
      <span class="anav-txt">{{ item.title }}</span>
    </router-link>
  </nav>
</template>

<script setup>
// 管理后台导航项（原 config/nav.js 死配置清理时内联：studentNav/studentImmersive 引用
// 的 /student/* 路由早已不存在，随文件一并删除；本数组为唯一幸存消费者）
const adminNav = [
  { key: 'identity', icon: '🪪', title: '身份审核', to: '/admin/identity/applications' },
  { key: 'overview', icon: '📈', title: '总览', to: '/admin/overview' },
  { key: 'model', icon: '🤖', title: '模型配置', to: '/admin/model' },
  { key: 'xingchen', icon: '✨', title: '星辰与工作流', to: '/admin/xingchen' },
  { key: 'cloud-kb', icon: '☁️', title: '云知识库', to: '/admin/cloud-kb' },
  { key: 'kb-bench', icon: '🔍', title: '检索试验台', to: '/admin/kb-bench' },
  { key: 'butler', icon: '🔐', title: 'Butler 授权', to: '/admin/butler' },
]
</script>

<style scoped>
.anav { display: flex; flex-direction: column; gap: 2px; padding: 8px; }
.anav-item {
  display: flex; align-items: center; gap: 10px;
  padding: 9px 12px; border-radius: 8px;
  color: var(--text-secondary, #4b5563);
  text-decoration: none; font-size: var(--text-sm, 14px);
  transition: background 0.15s, color 0.15s;
}
.anav-item:hover { background: var(--bg-hover, #f3f4f6); color: var(--text-primary, #111827); }
.anav-item.active { background: var(--primary-soft, #dbeafe); color: var(--primary, #2563eb); font-weight: 600; }
.anav-icon { font-size: 16px; }
</style>
