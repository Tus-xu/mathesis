<template>
  <div class="view">
    <div class="greeting">
      <div class="hello">👤 我的</div>
      <div class="sub">学情分析、知识掌握地图、个人设置——都收在这里。</div>
    </div>

    <div class="me-tabs">
      <button :class="{ active: tab === 'report' }" @click="tab = 'report'">📈 学情报告</button>
      <button :class="{ active: tab === 'graph' }" @click="tab = 'graph'">🧠 掌握地图</button>
      <button :class="{ active: tab === 'trial' }" @click="tab = 'trial'">📝 体验反馈</button>
      <button :class="{ active: tab === 'profile' }" @click="tab = 'profile'">⚙️ 个人设置</button>
    </div>

    <ReportView v-if="tab === 'report'" />
    <GraphView v-else-if="tab === 'graph'" />
    <TrialFeedbackView v-else-if="tab === 'trial'" />
    <ProfileView v-else />
  </div>
</template>

<script setup>
import { ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import ReportView from '@/pages/student/ReportView.vue'
import GraphView from '@/pages/student/GraphView.vue'
import TrialFeedbackView from '@/pages/student/TrialFeedbackView.vue'
import ProfileView from '@/pages/student/ProfileView.vue'

const route = useRoute()
const router = useRouter()
const valid = ['report', 'graph', 'trial', 'profile']
const tab = ref(valid.includes(route.query.tab) ? route.query.tab : 'report')

watch(() => route.query.tab, (t) => { if (valid.includes(t)) tab.value = t })
watch(tab, (t) => router.replace({ query: { ...route.query, tab: t } }))
</script>

<style scoped>
.me-tabs { display: flex; gap: 8px; margin-bottom: 14px; }
.me-tabs button {
  padding: 9px 20px; border-radius: 999px; border: 1px solid var(--line); background: var(--card);
  font: inherit; font-size: 13.5px; font-weight: 600; color: var(--ink2); cursor: pointer;
  transition: all .18s ease;
}
.me-tabs button.active { background: var(--primary-subtle); border-color: var(--primary-border); color: var(--primary); }

/* 容器页：隐藏子页面的内部 greeting（容器已提供页头上下文） */
:deep(.view > .greeting) { display: none; }
</style>
