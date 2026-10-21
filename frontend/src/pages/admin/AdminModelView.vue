<template>
  <section class="adm-ws" aria-label="模型配置">
    <header class="adm-ws-head">
      <h1>模型配置</h1>
      <p class="adm-ws-desc">全局默认模型通道（primary=星火 / secondary=DeepSeek）；空值清除回退环境变量。</p>
    </header>

    <div v-if="loading" class="adm-skeleton" style="height: 200px"></div>
    <template v-else>
      <div v-for="ch in channels" :key="ch.key" class="adm-card">
        <div class="adm-card-head">
          <h3 class="adm-card-h">{{ ch.title }}</h3>
          <span class="adm-tag" :class="ch.form.source === 'global' ? 'ok' : ''">{{ ch.form.source === 'global' ? '已覆盖' : '环境默认' }}</span>
        </div>
        <div class="adm-form">
          <label class="adm-field-row">
            <span class="adm-lbl">Base URL</span>
            <input v-model="ch.form.base_url" class="adm-input" placeholder="留空回退环境变量" />
          </label>
          <label class="adm-field-row">
            <span class="adm-lbl">模型名</span>
            <input v-model="ch.form.model" class="adm-input" placeholder="留空回退环境变量" />
          </label>
          <label class="adm-field-row">
            <span class="adm-lbl">API Key</span>
            <input v-model="ch.form.api_key" class="adm-input" type="password" :placeholder="ch.form.api_key || '留空保持当前'" />
          </label>
          <div class="adm-field-row">
            <span class="adm-lbl">思考模式</span>
            <Toggle v-model="ch.form.thinking" />
          </div>
        </div>
        <div class="adm-actions">
          <button class="adm-btn primary" type="button" :disabled="saving" @click="saveChannel(ch)">保存</button>
          <button class="adm-btn" type="button" :disabled="testing" @click="testChannel(ch)">测试连接</button>
        </div>
        <div v-if="ch.test" class="adm-test" :class="ch.test.ok ? 'ok' : 'bad'">
          {{ ch.test.ok ? '连接正常' : '连接失败' }} · {{ ch.test.latency_ms ?? 0 }}ms<template v-if="ch.test.error"> · {{ ch.test.error }}</template>
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
const testing = ref(false)
const channels = ref([])

function blank(key, title) {
  return { key, title, form: reactive({ base_url: '', model: '', api_key: '', thinking: false, source: 'env' }), test: null }
}

async function load() {
  loading.value = true
  try {
    const d = await adminApi.getModel()
    channels.value = [
      { ...blank('primary', '主通道（星火）'), form: Object.assign(blank('primary', '').form, d.primary || {}) },
      { ...blank('secondary', '备通道（DeepSeek）'), form: Object.assign(blank('secondary', '').form, d.secondary || {}) },
    ]
  } catch (e) {
    toast.error(e?.message || '读取模型配置失败')
  } finally {
    loading.value = false
  }
}

async function saveChannel(ch) {
  saving.value = true
  try {
    await adminApi.putModel({
      [ch.key]: {
        base_url: ch.form.base_url || null,
        model: ch.form.model || null,
        api_key: ch.form.api_key || null,
        thinking: ch.form.thinking,
      },
    })
    toast.success('已保存')
    await load()
  } catch (e) {
    toast.error(e?.message || '保存失败')
  } finally {
    saving.value = false
  }
}

async function testChannel(ch) {
  testing.value = true
  ch.test = null
  try {
    ch.test = await adminApi.testModel()
  } catch (e) {
    ch.test = { ok: false, error: e?.message || '测试失败' }
  } finally {
    testing.value = false
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
.adm-btn.primary { background: var(--primary); border-color: var(--primary); color: #fff; }
.adm-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.adm-test { margin-top: 10px; font-size: var(--text-xs); padding: 6px 10px; border-radius: var(--radius-sm); }
.adm-test.ok { background: var(--ok-bg); color: var(--ok-deep); }
.adm-test.bad { background: var(--err-bg); color: var(--err-deep); }
.adm-skeleton { border-radius: var(--radius-lg); background: linear-gradient(90deg, var(--bg-subtle) 25%, var(--bg-muted) 50%, var(--bg-subtle) 75%); background-size: 200% 100%; animation: adm-shimmer 1.4s infinite; }
@keyframes adm-shimmer { 0% { background-position: 200% 0; } 100% { background-position: -200% 0; } }
</style>
