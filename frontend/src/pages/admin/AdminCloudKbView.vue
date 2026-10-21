<template>
  <section class="adm-ws" aria-label="云知识库">
    <header class="adm-ws-head">
      <h1>云知识库</h1>
      <p class="adm-ws-desc">云知识库（tencent_lkeap / aliyun_bailian）与 Embedding 向量服务配置；空值清除回退环境变量。</p>
    </header>

    <div v-if="loading" class="adm-skeleton" style="height: 200px"></div>
    <template v-else>
      <div class="adm-card">
        <div class="adm-card-head">
          <h3 class="adm-card-h">云知识库</h3>
          <span class="adm-tag" :class="kb.configured ? 'ok' : ''">{{ kb.configured ? '已覆盖' : '环境默认' }}</span>
        </div>
        <div class="adm-form">
          <div class="adm-field-row">
            <span class="adm-lbl">启用</span>
            <Toggle v-model="kbForm.enabled" :disabled="saving" />
          </div>
          <label class="adm-field-row">
            <span class="adm-lbl">Provider</span>
            <select v-model="kbForm.provider" class="adm-input" :disabled="saving">
              <option value="">环境默认</option>
              <option value="tencent_lkeap">腾讯 LKEAP</option>
              <option value="aliyun_bailian">阿里云百炼</option>
            </select>
          </label>
          <div class="adm-field-row">
            <span class="adm-lbl">凭证</span>
            <div class="adm-creds">
              <div v-for="(c, i) in credRows" :key="i" class="adm-cred-row">
                <input v-model="c.key" class="adm-input adm-cred-key" placeholder="key" :disabled="saving" />
                <input v-model="c.value" class="adm-input" type="password" :placeholder="c.masked ? '留空保持当前' : 'value'" :disabled="saving" />
                <button class="adm-btn slim" type="button" :disabled="saving" @click="credRows.splice(i, 1)">删除</button>
              </div>
              <button class="adm-btn slim" type="button" :disabled="saving" @click="credRows.push({ key: '', value: '', masked: false })">+ 添加凭证</button>
            </div>
          </div>
          <label class="adm-field-row">
            <span class="adm-lbl">知识库 ID</span>
            <input v-model="kbForm.knowledge_base_id" class="adm-input" placeholder="留空回退环境变量" :disabled="saving" />
          </label>
          <label class="adm-field-row">
            <span class="adm-lbl">Top K</span>
            <input v-model.number="kbForm.top_k" class="adm-input adm-num" type="number" min="1" max="50" :disabled="saving" />
          </label>
          <label class="adm-field-row">
            <span class="adm-lbl">阈值</span>
            <input v-model.number="kbForm.score_threshold" class="adm-input adm-num" type="number" min="0" max="1" step="0.05" :disabled="saving" />
          </label>
        </div>
        <div class="adm-actions">
          <button class="adm-btn primary" type="button" :disabled="saving" @click="saveKb">保存</button>
          <button class="adm-btn" type="button" :disabled="testingKb" @click="testKb">测试连接</button>
        </div>
        <div v-if="kbTest" class="adm-test" :class="kbTest.ok ? 'ok' : 'bad'">
          {{ kbTest.ok ? '连接正常' : '连接失败' }} · {{ kbTest.latency_ms ?? 0 }}ms<template v-if="kbTest.error"> · {{ kbTest.error }}</template>
          <template v-else-if="kbTest.records?.length"> · 检索 {{ kbTest.records.length }} 条</template>
        </div>
      </div>

      <div class="adm-card">
        <div class="adm-card-head">
          <h3 class="adm-card-h">Embedding 向量服务</h3>
          <span class="adm-tag" :class="emb.configured ? 'ok' : ''">{{ emb.configured ? '已覆盖' : '环境默认' }}</span>
        </div>
        <div class="adm-form">
          <label class="adm-field-row">
            <span class="adm-lbl">Provider</span>
            <select v-model="embForm.provider" class="adm-input" :disabled="saving">
              <option value="local">本地（BGE-M3）</option>
              <option value="aliyun">阿里云</option>
              <option value="tencent">腾讯</option>
            </select>
          </label>
          <label class="adm-field-row">
            <span class="adm-lbl">Base URL</span>
            <input v-model="embForm.base_url" class="adm-input" placeholder="留空回退环境变量" :disabled="saving" />
          </label>
          <label class="adm-field-row">
            <span class="adm-lbl">API Key</span>
            <input v-model="embForm.api_key" class="adm-input" type="password" :placeholder="embForm.api_key || '留空保持当前'" :disabled="saving" />
          </label>
          <label class="adm-field-row">
            <span class="adm-lbl">模型名</span>
            <input v-model="embForm.model" class="adm-input" placeholder="留空按 provider 默认" :disabled="saving" />
          </label>
          <label class="adm-field-row">
            <span class="adm-lbl">维度</span>
            <input v-model.number="embForm.dimension" class="adm-input adm-num" type="number" min="128" max="4096" :disabled="saving" />
          </label>
        </div>
        <div class="adm-actions">
          <button class="adm-btn primary" type="button" :disabled="saving" @click="saveEmb">保存</button>
          <button class="adm-btn" type="button" :disabled="testingEmb" @click="testEmb">测试连接</button>
        </div>
        <div v-if="embTest" class="adm-test" :class="embTest.ok ? 'ok' : 'bad'">
          {{ embTest.ok ? '连接正常' : '连接失败' }} · {{ embTest.latency_ms ?? 0 }}ms<template v-if="embTest.error"> · {{ embTest.error }}</template>
          <template v-else-if="embTest.model"> · {{ embTest.provider }}/{{ embTest.model }}</template>
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
const testingKb = ref(false)
const testingEmb = ref(false)
const kb = ref({})
const emb = ref({})
const kbTest = ref(null)
const embTest = ref(null)
const kbForm = reactive({ enabled: false, provider: '', knowledge_base_id: '', top_k: 4, score_threshold: 0.5 })
const embForm = reactive({ provider: 'local', base_url: '', api_key: '', model: '', dimension: 1024 })
const credRows = ref([])

async function load() {
  loading.value = true
  try {
    const [kbD, embD] = await Promise.all([adminApi.getCloudKb(), adminApi.getEmbedding()])
    kb.value = kbD
    emb.value = embD
    Object.assign(kbForm, {
      enabled: !!kbD.enabled,
      provider: kbD.provider || '',
      knowledge_base_id: kbD.knowledge_base_id || '',
      top_k: kbD.top_k ?? 4,
      score_threshold: kbD.score_threshold ?? 0.5,
    })
    Object.assign(embForm, {
      provider: embD.provider || 'local',
      base_url: embD.base_url || '',
      api_key: embD.api_key || '',
      model: embD.model || '',
      dimension: embD.dimension ?? 1024,
    })
    credRows.value = Object.entries(kbD.credentials || {}).map(([k, v]) => ({ key: k, value: '', masked: !!v }))
  } catch (e) {
    toast.error(e?.message || '读取配置失败')
  } finally {
    loading.value = false
  }
}

function credsPayload() {
  const out = {}
  for (const c of credRows.value) {
    if (!c.key) continue
    out[c.key] = c.value || null
  }
  return out
}

async function saveKb() {
  saving.value = true
  try {
    await adminApi.putCloudKb({
      enabled: kbForm.enabled,
      provider: kbForm.provider || null,
      credentials: credsPayload(),
      knowledge_base_id: kbForm.knowledge_base_id || null,
      top_k: kbForm.top_k,
      score_threshold: kbForm.score_threshold,
    })
    toast.success('已保存')
    await load()
  } catch (e) {
    toast.error(e?.message || '保存失败')
  } finally {
    saving.value = false
  }
}

async function testKb() {
  testingKb.value = true
  kbTest.value = null
  try {
    kbTest.value = await adminApi.testCloudKb()
  } catch (e) {
    kbTest.value = { ok: false, error: e?.message || '测试失败' }
  } finally {
    testingKb.value = false
  }
}

async function saveEmb() {
  saving.value = true
  try {
    await adminApi.putEmbedding({
      provider: embForm.provider,
      base_url: embForm.base_url || null,
      api_key: embForm.api_key || null,
      model: embForm.model || null,
      dimension: embForm.dimension,
    })
    toast.success('已保存')
    await load()
  } catch (e) {
    toast.error(e?.message || '保存失败')
  } finally {
    saving.value = false
  }
}

async function testEmb() {
  testingEmb.value = true
  embTest.value = null
  try {
    embTest.value = await adminApi.testEmbedding()
  } catch (e) {
    embTest.value = { ok: false, error: e?.message || '测试失败' }
  } finally {
    testingEmb.value = false
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
.adm-num { max-width: 160px; }
.adm-creds { flex: 1; display: flex; flex-direction: column; gap: 8px; }
.adm-cred-row { display: flex; gap: 8px; align-items: center; }
.adm-cred-key { max-width: 180px; }
.adm-actions { display: flex; gap: 10px; margin-top: 14px; }
.adm-btn { padding: 8px 18px; border-radius: var(--radius-sm); border: 1px solid var(--border); background: var(--card); cursor: pointer; font-size: var(--text-sm); }
.adm-btn.slim { padding: 4px 10px; font-size: var(--text-xs); }
.adm-btn.primary { background: var(--primary); border-color: var(--primary); color: #fff; }
.adm-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.adm-test { margin-top: 10px; font-size: var(--text-xs); padding: 6px 10px; border-radius: var(--radius-sm); }
.adm-test.ok { background: var(--ok-bg); color: var(--ok-deep); }
.adm-test.bad { background: var(--err-bg); color: var(--err-deep); }
.adm-skeleton { border-radius: var(--radius-lg); background: linear-gradient(90deg, var(--bg-subtle) 25%, var(--bg-muted) 50%, var(--bg-subtle) 75%); background-size: 200% 100%; animation: adm-shimmer 1.4s infinite; }
@keyframes adm-shimmer { 0% { background-position: 200% 0; } 100% { background-position: -200% 0; } }
</style>
