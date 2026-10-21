<template>
  <section class="adm-ws" aria-label="总览">
    <header class="adm-ws-head">
      <h1>总览</h1>
      <p class="adm-ws-desc">组件存活 / 通道健康 / 今日调用 / 核心计数。</p>
    </header>

    <div v-if="loading" class="adm-skeleton" style="height: 200px"></div>
    <template v-else>
      <div class="adm-grid">
        <div class="adm-card adm-stat">
          <div class="adm-stat-label">今日 AI 调用</div>
          <div class="adm-stat-value">{{ data.ai_calls_today?.total ?? 0 }}</div>
        </div>
        <div class="adm-card adm-stat">
          <div class="adm-stat-label">用户数</div>
          <div class="adm-stat-value">{{ data.counts?.users ?? 0 }}</div>
        </div>
        <div class="adm-card adm-stat">
          <div class="adm-stat-label">班级数</div>
          <div class="adm-stat-value">{{ data.counts?.classes ?? 0 }}</div>
        </div>
        <div class="adm-card adm-stat">
          <div class="adm-stat-label">今日提交</div>
          <div class="adm-stat-value">{{ data.counts?.submissions_today ?? 0 }}</div>
        </div>
      </div>

      <div class="adm-card">
        <h3 class="adm-card-h">基础设施</h3>
        <div class="adm-pill-row">
          <span class="adm-pill" :class="okCls(data.db?.ok)">数据库 {{ data.db?.ok ? '正常' : '异常' }}</span>
          <span class="adm-pill" :class="okCls(data.redis?.ok)">Redis {{ data.redis?.ok ? '正常' : '异常' }}</span>
        </div>
      </div>

      <div class="adm-card">
        <h3 class="adm-card-h">通道健康</h3>
        <div class="adm-pill-row">
          <span v-for="(v, k) in data.channels || {}" :key="k" class="adm-pill" :class="okCls(v?.ok)">
            {{ channelName(k) }} {{ v?.ok ? '正常' : '异常' }}
            <template v-if="k === 'cloud_kb' && v?.detail">（{{ v.detail }}）</template>
          </span>
        </div>
        <div v-if="data.ai_calls_today?.by_provider" class="adm-byprov">
          <span v-for="(n, p) in data.ai_calls_today.by_provider" :key="p" class="adm-tag">{{ p }}: {{ n }}</span>
        </div>
      </div>
    </template>
  </section>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { adminApi } from '@/api'
import { useToastStore } from '@/stores/toast'

const toast = useToastStore()
const loading = ref(true)
const data = ref({})

function okCls(ok) {
  return ok ? 'ok' : 'bad'
}
function channelName(k) {
  return { spark: '星火', deepseek: 'DeepSeek', embedding: 'Embedding', reranker: 'Reranker', xingchen: '星辰', cloud_kb: '云知识库' }[k] || k
}

async function load() {
  loading.value = true
  try {
    data.value = await adminApi.overview()
  } catch (e) {
    toast.error(e?.message || '读取总览失败')
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.adm-ws-head h1 { font-size: var(--text-2xl); margin: 0 0 4px; }
.adm-ws-desc { color: var(--text-secondary); margin: 0 0 20px; font-size: var(--text-sm); }
.adm-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 14px; margin-bottom: 16px; }
.adm-card { background: var(--card); border: 1px solid var(--border); border-radius: var(--radius-lg); padding: 18px; box-shadow: var(--shadow-sm); margin-bottom: 16px; }
.adm-stat-label { color: var(--text-secondary); font-size: var(--text-xs); }
.adm-stat-value { font-size: var(--text-3xl); font-weight: var(--font-bold); margin-top: 4px; }
.adm-card-h { margin: 0 0 12px; font-size: var(--text-base); }
.adm-pill-row { display: flex; flex-wrap: wrap; gap: 8px; }
.adm-pill { padding: 4px 12px; border-radius: var(--radius-full); font-size: var(--text-xs); border: 1px solid var(--border); }
.adm-pill.ok { background: var(--ok-bg); color: var(--ok-deep); border-color: var(--ok-border); }
.adm-pill.bad { background: var(--err-bg); color: var(--err-deep); border-color: var(--err-border); }
.adm-byprov { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }
.adm-tag { padding: 2px 10px; border-radius: var(--radius-full); background: var(--bg-subtle); font-size: var(--text-xs); }
.adm-skeleton { border-radius: var(--radius-lg); background: linear-gradient(90deg, var(--bg-subtle) 25%, var(--bg-muted) 50%, var(--bg-subtle) 75%); background-size: 200% 100%; animation: adm-shimmer 1.4s infinite; }
@keyframes adm-shimmer { 0% { background-position: 200% 0; } 100% { background-position: -200% 0; } }
</style>
