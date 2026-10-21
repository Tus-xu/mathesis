<template>
  <section class="adm-ws" aria-label="Butler 授权">
    <header class="adm-ws-head">
      <h1>Butler 授权</h1>
      <p class="adm-ws-desc">控制外部工具与联网搜索的服务端能力开关；用户单条请求 opt-in 由前端联网按钮控制。</p>
    </header>

    <div v-if="loading" class="adm-skeleton" style="height: 160px"></div>
    <div v-else class="adm-card">
      <div class="adm-field">
        <div class="adm-field-main">
          <div class="adm-field-label">联网搜索（web_search_enabled）</div>
          <div class="adm-field-hint">服务端能力开关，默认回退环境变量。开启后前端联网按钮可见；用户仍需单条请求授权。</div>
        </div>
        <Toggle v-model="form.web_search_enabled" :disabled="saving" />
      </div>

      <div class="adm-field">
        <div class="adm-field-main">
          <div class="adm-field-label">外部工具（external_allowed）</div>
          <div class="adm-field-hint">EXTERNAL 风险工具授权开关，默认关闭。</div>
        </div>
        <Toggle v-model="form.external_allowed" :disabled="saving" />
      </div>

      <div class="adm-note">
        <template v-if="data.configured">已持久化覆盖默认值（system_configs["butler.authorization"]）。</template>
        <template v-else>未持久化，当前为环境变量默认值。</template>
      </div>

      <div class="adm-actions">
        <button class="adm-btn primary" type="button" :disabled="saving" @click="save">
          {{ saving ? '保存中…' : '保存' }}
        </button>
        <button class="adm-btn" type="button" :disabled="saving" @click="load">重置</button>
      </div>
    </div>
  </section>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { adminApi } from '@/api'
import { useToastStore } from '@/stores/toast'
import Toggle from '@/components/common/Toggle.vue'

const toast = useToastStore()
const loading = ref(true)
const saving = ref(false)
const data = ref({})
const form = reactive({ web_search_enabled: false, external_allowed: false })

async function load() {
  loading.value = true
  try {
    const d = await adminApi.getButler()
    data.value = d
    form.web_search_enabled = !!d.web_search_enabled
    form.external_allowed = !!d.external_allowed
  } catch (e) {
    toast.error(e?.message || '读取 Butler 授权配置失败')
  } finally {
    loading.value = false
  }
}

async function save() {
  saving.value = true
  try {
    await adminApi.putButler({
      web_search_enabled: form.web_search_enabled,
      external_allowed: form.external_allowed,
    })
    toast.success('已保存')
    await load()
  } catch (e) {
    toast.error(e?.message || '保存失败')
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.adm-ws { max-width: 720px; }
.adm-ws-head h1 { font-size: var(--text-2xl); margin: 0 0 4px; }
.adm-ws-desc { color: var(--text-secondary); margin: 0 0 20px; font-size: var(--text-sm); }
.adm-card { background: var(--card); border: 1px solid var(--border); border-radius: var(--radius-lg); padding: 20px; box-shadow: var(--shadow-sm); }
.adm-field { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 14px 0; border-bottom: 1px solid var(--border-subtle); }
.adm-field:last-of-type { border-bottom: none; }
.adm-field-label { font-weight: var(--font-semibold); }
.adm-field-hint { color: var(--text-secondary); font-size: var(--text-xs); margin-top: 4px; max-width: 480px; line-height: var(--leading-relaxed); }
.adm-note { font-size: var(--text-xs); color: var(--text-muted); margin-top: 12px; }
.adm-actions { display: flex; gap: 10px; margin-top: 16px; }
.adm-btn { padding: 8px 18px; border-radius: var(--radius-sm); border: 1px solid var(--border); background: var(--card); cursor: pointer; font-size: var(--text-sm); }
.adm-btn.primary { background: var(--primary); border-color: var(--primary); color: #fff; }
.adm-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.adm-skeleton { border-radius: var(--radius-lg); background: linear-gradient(90deg, var(--bg-subtle) 25%, var(--bg-muted) 50%, var(--bg-subtle) 75%); background-size: 200% 100%; animation: adm-shimmer 1.4s infinite; }
@keyframes adm-shimmer { 0% { background-position: 200% 0; } 100% { background-position: -200% 0; } }
</style>
