<template>
  <section class="adm-ws" aria-label="星辰与工作流">
    <header class="adm-ws-head">
      <h1>星辰与工作流</h1>
      <p class="adm-ws-desc">星辰全局凭证与工作流开关；总开关仅环境变量可控。</p>
    </header>

    <div v-if="loading" class="adm-skeleton" style="height: 200px"></div>
    <template v-else>
      <div class="adm-card">
        <div class="adm-card-head">
          <h3 class="adm-card-h">星辰全局凭证</h3>
          <span class="adm-tag" :class="xingchen.configured ? 'ok' : ''">{{ xingchen.configured ? '已覆盖' : '环境默认' }}</span>
        </div>
        <div class="adm-form">
          <div class="adm-field-row">
            <span class="adm-lbl">总开关</span>
            <Toggle v-model="xc.enabled" :disabled="saving" />
          </div>
          <label class="adm-field-row">
            <span class="adm-lbl">Base URL</span>
            <input v-model="xc.base_url" class="adm-input" placeholder="留空回退环境变量" />
          </label>
          <label class="adm-field-row">
            <span class="adm-lbl">API Key</span>
            <input v-model="xc.api_key" class="adm-input" type="password" :placeholder="xc.api_key || '留空保持当前'" />
          </label>
          <label class="adm-field-row">
            <span class="adm-lbl">API Secret</span>
            <input v-model="xc.api_secret" class="adm-input" type="password" :placeholder="xc.api_secret || '留空保持当前'" />
          </label>
        </div>
        <div class="adm-actions">
          <button class="adm-btn primary" type="button" :disabled="saving" @click="saveXingchen">保存</button>
        </div>
      </div>

      <div class="adm-card">
        <div class="adm-card-head">
          <h3 class="adm-card-h">工作流</h3>
          <span class="adm-tag" :class="workflows.master_enabled ? 'ok' : ''">星辰总开关 {{ workflows.master_enabled ? '开' : '关' }}</span>
        </div>
        <div class="adm-wf-list">
          <div v-for="wf in workflows.workflows || []" :key="wf.name" class="adm-wf">
            <div class="adm-wf-main">
              <div class="adm-wf-name">{{ wf.name }}</div>
              <div class="adm-wf-meta">
                flow_id: {{ wf.flow_id || '未配置' }} · timeout: {{ wf.timeout }}s · 今日 {{ wf.today_calls ?? 0 }} 次
              </div>
            </div>
            <div class="adm-wf-actions">
              <button class="adm-btn slim" type="button" :disabled="testing === wf.name" @click="testWorkflow(wf)">
                {{ testing === wf.name ? '测试中…' : '测试' }}
              </button>
              <Toggle v-model="wf.enabled" :disabled="saving" @update:modelValue="(v) => saveWorkflow(wf, v)" />
            </div>
          </div>
        </div>
        <div v-if="wfTest" class="adm-test" :class="wfTest.ok ? 'ok' : 'bad'">
          {{ wfTest.name }}：{{ wfTest.ok ? '正常' : '失败' }} · {{ wfTest.latency_ms ?? 0 }}ms<template v-if="wfTest.error"> · {{ wfTest.error }}</template>
        </div>
      </div>
    </template>
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
const testing = ref('')
const xingchen = ref({})
const workflows = ref({})
const wfTest = ref(null)
const xc = reactive({ enabled: false, base_url: '', api_key: '', api_secret: '' })

async function load() {
  loading.value = true
  try {
    const [x, w] = await Promise.all([adminApi.getXingchen(), adminApi.workflows()])
    xingchen.value = x
    xc.enabled = !!x.enabled
    xc.base_url = x.base_url || ''
    xc.api_key = x.api_key || ''
    xc.api_secret = x.api_secret || ''
    workflows.value = w
  } catch (e) {
    toast.error(e?.message || '读取星辰配置失败')
  } finally {
    loading.value = false
  }
}

async function saveXingchen() {
  saving.value = true
  try {
    await adminApi.putXingchen({
      enabled: xc.enabled,
      base_url: xc.base_url || null,
      api_key: xc.api_key || null,
      api_secret: xc.api_secret || null,
    })
    toast.success('已保存')
    await load()
  } catch (e) {
    toast.error(e?.message || '保存失败')
  } finally {
    saving.value = false
  }
}

async function saveWorkflow(wf, enabled) {
  saving.value = true
  try {
    await adminApi.putWorkflow(wf.name, { enabled })
    toast.success(`已更新 ${wf.name}`)
  } catch (e) {
    toast.error(e?.message || '更新失败')
    wf.enabled = !enabled
  } finally {
    saving.value = false
  }
}

async function testWorkflow(wf) {
  testing.value = wf.name
  wfTest.value = null
  try {
    const r = await adminApi.testWorkflow(wf.name)
    wfTest.value = { name: wf.name, ...r }
  } catch (e) {
    wfTest.value = { name: wf.name, ok: false, error: e?.message || '测试失败' }
  } finally {
    testing.value = ''
  }
}

onMounted(load)
</script>

<style scoped>
.adm-ws-head h1 { font-size: var(--text-2xl); margin: 0 0 4px; }
.adm-ws-desc { color: var(--text-secondary); margin: 0 0 20px; font-size: var(--text-sm); }
.adm-card { background: var(--card); border: 1px solid var(--border); border-radius: var(--radius-lg); padding: 18px; box-shadow: var(--shadow-sm); margin-bottom: 16px; }
.adm-card-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; }
.adm-card-h { margin: 0; font-size: var(--text-base); }
.adm-tag { padding: 2px 10px; border-radius: var(--radius-full); background: var(--bg-subtle); font-size: var(--text-xs); }
.adm-tag.ok { background: var(--ok-bg); color: var(--ok-deep); }
.adm-form { display: flex; flex-direction: column; gap: 10px; }
.adm-field-row { display: flex; align-items: center; gap: 12px; }
.adm-lbl { width: 110px; flex: none; color: var(--text-secondary); font-size: var(--text-sm); }
.adm-input { flex: 1; padding: 8px 12px; border: 1px solid var(--border); border-radius: var(--radius-sm); font-size: var(--text-sm); background: var(--card); }
.adm-actions { display: flex; gap: 10px; margin-top: 14px; }
.adm-btn { padding: 8px 18px; border-radius: var(--radius-sm); border: 1px solid var(--border); background: var(--card); cursor: pointer; font-size: var(--text-sm); }
.adm-btn.slim { padding: 4px 12px; font-size: var(--text-xs); }
.adm-btn.primary { background: var(--primary); border-color: var(--primary); color: #fff; }
.adm-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.adm-wf-list { display: flex; flex-direction: column; }
.adm-wf { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 10px 0; border-bottom: 1px solid var(--border-subtle); }
.adm-wf:last-child { border-bottom: none; }
.adm-wf-name { font-weight: var(--font-medium); font-size: var(--text-sm); }
.adm-wf-meta { color: var(--text-muted); font-size: var(--text-xs); margin-top: 2px; }
.adm-wf-actions { display: flex; align-items: center; gap: 10px; }
.adm-test { margin-top: 10px; font-size: var(--text-xs); padding: 6px 10px; border-radius: var(--radius-sm); }
.adm-test.ok { background: var(--ok-bg); color: var(--ok-deep); }
.adm-test.bad { background: var(--err-bg); color: var(--err-deep); }
.adm-skeleton { border-radius: var(--radius-lg); background: linear-gradient(90deg, var(--bg-subtle) 25%, var(--bg-muted) 50%, var(--bg-subtle) 75%); background-size: 200% 100%; animation: adm-shimmer 1.4s infinite; }
@keyframes adm-shimmer { 0% { background-position: 200% 0; } 100% { background-position: -200% 0; } }
</style>
