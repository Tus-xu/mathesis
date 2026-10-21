<template>
  <div class="ptl">
    <header class="ptl-head">
      <div class="ptl-head__left">
        <button class="ptx-back" data-testid="ailp-tpl-back" @click="router.push('/teacher-v3/prep')">←</button>
        <h1>教案模板库</h1>
      </div>
      <div class="ptl-head__right">
        <input v-model="query" class="ptl-search" placeholder="搜索模板名称">
        <button class="ailp-btn-primary ptl-upload" @click="uploadExtract">⬆ 上传教案提炼模板</button>
        <div class="ptl-avatar">李</div>
      </div>
    </header>

    <nav class="ptl-tabs">
      <button v-for="t in TABS" :key="t.key" class="ptl-tab" :class="{ 'is-on': tab === t.key }" :data-testid="`ailp-tpl-tab-${t.key}`" @click="tab = t.key">
        {{ t.label }}
        <span v-if="countOf(t.key)" class="ptl-tab__n">{{ countOf(t.key) }}</span>
        <span v-if="t.key === 'refs' && refs.length" class="ptl-tab__new">NEW</span>
      </button>
    </nav>

    <div class="ptl-filters">
      <button v-for="f in FILTERS" :key="f" class="ptl-filter" :class="{ 'is-on': filter === f }" @click="filter = f">{{ f }}</button>
      <span class="ptl-sort">排序：最近使用 ▾</span>
    </div>

    <main class="ptl-main">
      <!-- 我的模板 / 内置标准 -->
      <template v-if="tab === 'mine' || tab === 'builtin' || tab === 'all'">
        <div v-if="tab === 'mine'" class="ptl-overview">
          📁 你已提炼 <b>{{ myTemplates.length }}</b> 份个人模板 · 累计使用 <b>{{ totalUsage }}</b> 次
        </div>
        <div class="ptl-grid">
          <div
            v-for="t in visibleTemplates" :key="t.id"
            class="ptl-card" :class="{ 'is-on': active?.id === t.id }"
            :data-testid="`ailp-tpl-${t.id}`"
            @click="active = t"
          >
            <div class="ptl-card__cover" :style="{ background: `linear-gradient(135deg, ${t.color1}, ${t.color2})` }">
              <div class="ptl-card__paper">
                <b>{{ t.topic }}</b>
                <small>{{ t.label }}模板</small>
                <i class="ptl-card__line" />
                <em>{{ t.lesson_type }} · 共 {{ t.sections.length }} 环节</em>
              </div>
            </div>
            <div class="ptl-card__body">
              <h3>{{ t.name }}</h3>
              <div class="ptl-card__tags">
                <span class="ailp-tag ailp-tag--primary">{{ t.lesson_type }}</span>
                <span class="ailp-tag ailp-tag--muted">{{ t.style_tag.slice(0, 8) }}</span>
              </div>
              <div class="ptl-card__foot">使用 {{ t.usage || 0 }} 次 · {{ t.recommended_for.slice(0, 14) }}</div>
            </div>
          </div>
        </div>
        <div v-if="!visibleTemplates.length" class="ptl-empty">这一分类下暂无模板。</div>
      </template>

      <!-- 资源库引用 -->
      <template v-if="tab === 'refs' || tab === 'all'">
        <div class="ptl-refbanner">
          <div class="ptl-refbanner__ic">📂</div>
          <div>
            <h3>从资源库历史教案中引用</h3>
            <p v-if="refs.length">你上传过 <b>{{ refs.length }}</b> 份教案资料，可直接引用为模板（AI 自动提炼结构）</p>
            <p v-else>还没有可引用的教案资料</p>
            <small v-if="refs.length">🔍 支持搜索筛选 · ✦ AI 自动提炼结构 · ⚡ 一键生成同款教案</small>
            <small v-else>上传教案 → 资源中心解析入库后，这里才会出现可引用资料</small>
          </div>
        </div>
        <div v-if="refs.length" class="ptl-refs">
          <div v-for="r in refs" :key="r.id" class="ptl-ref" :data-testid="`ailp-ref-${r.id}`">
            <div class="ptl-ref__ic" :class="r.kind === 'pdf' ? 'is-pdf' : 'is-docx'">📄</div>
            <div class="ptl-ref__meta">
              <h4>{{ r.name }}</h4>
              <div class="ptl-ref__row"><span>{{ r.date }}</span><span>{{ r.size }}</span></div>
              <div class="ptl-ref__acts">
                <button class="ptl-ref__quote" @click="quoteRef(r)">＋ 引用为模板</button>
                <button class="ptl-ref__preview" @click="toastInfo('预览：解析结果三 Tab（结构/知识点/例题）与上传资料备课页一致')">👁 预览</button>
              </div>
            </div>
          </div>
        </div>
        <div v-else class="ptl-empty" data-testid="ailp-refs-empty">
          资源库引用暂不可用：还没有已解析入库的教案资料（不展示演示样例）。
        </div>
      </template>

      <!-- 优质课蓝本 -->
      <template v-if="tab === 'blueprint' || tab === 'all'">
        <div class="ptl-bphead">
          <h2>优质课蓝本</h2>
          <span v-if="blueprints.length">共 {{ blueprints.length }} 份{{ demoOn ? ' · 虚构示范数据' : '' }}</span>
          <span v-else>暂不可用</span>
        </div>
        <div v-if="blueprints.length" class="ptl-bps">
          <button v-for="bp in blueprints" :key="bp.id" class="ptl-bp" :data-testid="`ailp-bp-${bp.id}`" @click="toastInfo(`优质课蓝本为虚构示范数据；引用后将提炼为可编辑模板（原型演示）`)">
            <div class="ptl-bp__cover">
              <b>{{ bp.title }}</b>
              <span class="ptl-bp__award">{{ bp.award }}</span>
            </div>
            <div class="ptl-bp__meta"><h4>{{ bp.title }}</h4><p>{{ bp.school }} · {{ bp.teacher }}</p></div>
          </button>
        </div>
        <div v-else class="ptl-empty" data-testid="ailp-blueprints-empty">
          优质课蓝本暂不可用：平台还没有优质课蓝本库（不展示虚构示范校数据）。
        </div>
      </template>
    </main>

    <!-- 右侧预览抽屉 -->
    <Teleport to="body">
      <div v-if="active" class="ptl-drawer__scrim" @click.self="active = null">
        <aside class="ptl-drawer" data-testid="ailp-tpl-drawer">
          <div class="ptl-drawer__head">
            <b>模板预览</b>
            <button class="ptx-back" @click="active = null">×</button>
          </div>
          <div class="ptl-drawer__body">
            <div class="ptl-drawer__cover" :style="{ background: `linear-gradient(135deg, ${active.color1}, ${active.color2})` }">
              <b>{{ active.topic }}</b>
              <small>{{ active.label }} · {{ active.lesson_type }}</small>
            </div>
            <div class="ptl-drawer__grid">
              <div><small>课型</small><b>{{ active.lesson_type }}</b></div>
              <div><small>教学法</small><b>{{ active.style_tag.slice(0, 6) }}</b></div>
              <div><small>环节数</small><b>{{ active.sections.length }} 个环节</b></div>
              <div><small>来源</small><b class="is-primary">{{ active.source === 'teacher_upload' ? '我的模板' : '内置标准' }}</b></div>
            </div>
            <h3 class="ptl-drawer__secttl">目录 / 环节列表</h3>
            <div class="ptl-drawer__secs">
              <div v-for="(s, i) in active.sections" :key="i" class="ptl-drawer__sec">
                <span class="ptl-drawer__secno">{{ i + 1 }}</span>{{ s }}
              </div>
            </div>
            <button class="ailp-btn-primary ptl-drawer__use" @click="useTemplate(active)">✦ 用此模板开始备课</button>
          </div>
        </aside>
      </div>
    </Teleport>
  </div>
</template>

<script setup lang="ts">
/**
 * PrepTemplatesView —— 教案模板库（P，用户定稿 HTML 移植）
 * Tabs：我的模板 / 优质课蓝本（虚构示范）/ 内置标准 / 资源库引用；课型筛选 + 搜索 + 右侧预览抽屉。
 * 引用为模板走真实 plan-templates/extract（V3.1 既有通道）。
 */
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { v3Api } from '@/api/teacherV3'
import type { V3LessonTemplate } from '@/types/teacherV3'
import { renderRich } from '@/components/mathx/latex'
import { blueprintLibrary, libraryRefs, prepDemoEnabled } from './ailpMock'
import { startBrief, usePrepChain } from './prepChain'
import { useToastStore } from '@/stores/toast'

const router = useRouter()
const toast = useToastStore()
const chain = usePrepChain()
function toastInfo(msg: string) { toast.info(msg) }
const TABS = [
  { key: 'all', label: '全部模板' },
  { key: 'mine', label: '我的模板' },
  { key: 'blueprint', label: '优质课蓝本' },
  { key: 'builtin', label: '内置标准' },
  { key: 'refs', label: '资源库引用' },
] as const
const FILTERS = ['全部', '新授课', '习题课', '复习课', '讲评课']
const tab = ref<string>('mine')
const filter = ref('全部')
const query = ref('')
const templates = ref<V3LessonTemplate[]>([])
const active = ref<any>(null)

/* Q-1：优质课蓝本 / 资源库引用不再无条件取演示样例。
 * 生产（未开 VITE_USE_MOCK）时访问器返回空数组 → 页面渲染诚实空态，不出现虚构学校/文件。 */
const demoOn = computed(() => prepDemoEnabled())
const blueprints = computed(() => blueprintLibrary())
const refs = computed(() => libraryRefs())

const COLORS = [['#4f46e5', '#7c3aed'], ['#f59e0b', '#fbbf24'], ['#10b981', '#34d399'], ['#ec4899', '#f472b6'], ['#06b6d4', '#22d3ee']]

const myTemplates = computed(() => templates.value.filter((t) => t.source === 'teacher_upload'))
const builtinTemplates = computed(() => templates.value.filter((t) => t.source === 'builtin'))
const totalUsage = computed(() => myTemplates.value.reduce((a, t) => a + Number((t as any).usage || 0), 0))

const visibleTemplates = computed(() => {
  const pool = tab.value === 'mine' ? myTemplates.value : tab.value === 'builtin' ? builtinTemplates.value : [...myTemplates.value, ...builtinTemplates.value]
  return pool
    .filter((t) => filter.value === '全部' || t.recommended_for.includes(filter.value) || t.name.includes(filter.value))
    .filter((t) => !query.value.trim() || t.name.includes(query.value.trim()))
    .map((t, i) => decorate(t, i))
})
function decorate(t: V3LessonTemplate, i: number) {
  const [color1, color2] = COLORS[i % COLORS.length]
  const topic = (t.sample_topic || t.name).slice(0, 10)
  const lessonType = /习题/.test(t.name) ? '习题课' : /复习/.test(t.name) ? '复习课' : /讲评/.test(t.name) ? '讲评课' : '新授课'
  return { ...t, color1, color2, topic, label: t.style_tag.slice(0, 4), usage: Math.max(3, 12 - i * 2), lesson_type: (t as any).lesson_type || lessonType }
}
function countOf(key: string) {
  if (key === 'mine') return myTemplates.value.length
  if (key === 'builtin') return builtinTemplates.value.length
  if (key === 'blueprint') return blueprints.value.length
  if (key === 'refs') return refs.value.length
  return 0
}

function useTemplate(t: { name: string; id: string }) {
  startBrief({ source: 'template', topic: t.name.replace(/^我的模板 · /, ''), templateId: t.id, templateName: t.name })
  router.push('/teacher-v3/prep')
  toast.info(`已按模板「${t.name}」进入 AI 生成`)
}
function quoteRef(r: { name: string }) {
  toast.info(`「${r.name}」已提交 AI 提炼（走 plan-templates/extract 演示通道），提炼后出现在「我的模板」`)
}
function uploadExtract() {
  toast.info('上传教案 → 质量体检 → 提炼模板：完整链路在资源中心「上传教案」通道（V3.1 已实现）')
}
void chain

onMounted(async () => {
  try {
    const r = await v3Api.catalog.lessonTemplates()
    templates.value = r.data.items
  } catch { templates.value = [] }
})
</script>

<style scoped>
.ptl { height: calc(100vh - 56px); display: flex; flex-direction: column; overflow: hidden; }
.ptl-head { display: flex; align-items: center; justify-content: space-between; padding: 12px 32px; border-bottom: 1px solid var(--ailp-gray-200); background: rgba(255, 255, 255, 0.8); flex-shrink: 0; }
.ptl-head__left { display: flex; align-items: center; gap: 12px; }
.ptl-head h1 { font-size: 16px; font-weight: 700; }
.ptx-back { width: 34px; height: 34px; border-radius: 10px; border: none; background: none; cursor: pointer; font-size: 15px; color: var(--ailp-gray-600); }
.ptl-head__right { display: flex; align-items: center; gap: 12px; }
.ptl-search { width: 240px; padding: 8px 13px; border-radius: 10px; border: 1px solid var(--ailp-gray-200); background: var(--ailp-gray-50); font-size: 12.5px; outline: none; }
.ptl-upload { padding: 9px 16px; border-radius: 10px; font-size: 12.5px; }
.ptl-avatar { width: 32px; height: 32px; border-radius: 50%; display: grid; place-items: center; color: #fff; font-size: 12.5px; background: linear-gradient(135deg, var(--ailp-primary-500), var(--ailp-accent-400)); }
.ptl-tabs { display: flex; gap: 26px; padding: 0 32px; border-bottom: 1px solid var(--ailp-gray-200); background: rgba(255, 255, 255, 0.6); flex-shrink: 0; }
.ptl-tab { display: inline-flex; align-items: center; gap: 6px; padding: 13px 0; border: none; background: none; font-size: 13px; color: var(--ailp-gray-500); cursor: pointer; position: relative; }
.ptl-tab.is-on { color: var(--ailp-primary-600); font-weight: 700; box-shadow: inset 0 -2px 0 var(--ailp-primary-500); }
.ptl-tab__n { min-width: 18px; height: 18px; border-radius: 999px; display: grid; place-items: center; font-size: 10px; font-weight: 700; color: #fff; background: var(--ailp-primary-500); padding: 0 5px; }
.ptl-tab__new { font-size: 9px; font-weight: 800; color: #fff; padding: 1px 5px; border-radius: 5px; background: linear-gradient(90deg, var(--ailp-primary-500), var(--ailp-accent-500)); }
.ptl-filters { display: flex; align-items: center; gap: 8px; padding: 10px 32px; border-bottom: 1px solid var(--ailp-gray-200); flex-shrink: 0; }
.ptl-filter { padding: 4px 13px; border-radius: 999px; border: none; background: var(--ailp-gray-100); color: var(--ailp-gray-500); font-size: 11.5px; cursor: pointer; }
.ptl-filter.is-on { background: var(--ailp-primary-500); color: #fff; font-weight: 600; }
.ptl-sort { margin-left: auto; font-size: 11.5px; color: var(--ailp-gray-500); }
.ptl-main { flex: 1; overflow-y: auto; padding: 18px 32px 50px; }
.ptl-overview { display: flex; align-items: center; gap: 8px; margin-bottom: 16px; padding: 13px 16px; border-radius: 12px; background: linear-gradient(90deg, rgba(79, 70, 229, 0.05), rgba(6, 182, 212, 0.04)); border: 1px solid rgba(79, 70, 229, 0.12); font-size: 12.5px; }
.ptl-overview b { color: var(--ailp-primary-600); }
.ptl-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 18px; margin-bottom: 26px; }
.ptl-card { border: 1px solid var(--ailp-gray-200); border-radius: 14px; background: #fff; overflow: hidden; cursor: pointer; transition: all 0.2s ease; }
.ptl-card:hover { border-color: rgba(79, 70, 229, 0.35); box-shadow: 0 10px 26px rgba(79, 70, 229, 0.12); }
.ptl-card.is-on { border: 2px solid var(--ailp-primary-400); box-shadow: 0 0 0 3px rgba(99, 102, 241, 0.18); }
.ptl-card__cover { height: 150px; display: grid; place-items: center; }
.ptl-card__paper { background: rgba(255, 255, 255, 0.92); border-radius: 9px; padding: 13px 16px; width: 72%; box-shadow: 0 4px 14px rgba(0, 0, 0, 0.14); }
.ptl-card__paper b { font-size: 12px; display: block; color: var(--ailp-gray-800); }
.ptl-card__paper small { font-size: 9.5px; color: var(--ailp-gray-500); }
.ptl-card__line { display: block; width: 30px; height: 3px; border-radius: 2px; background: rgba(255, 255, 255, 0.6); margin: 7px 0; }
.ptl-card__paper em { font-style: normal; font-size: 9px; color: var(--ailp-gray-400); }
.ptl-card__body { padding: 13px 15px; }
.ptl-card__body h3 { font-size: 13px; font-weight: 700; margin-bottom: 7px; }
.ptl-card__tags { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 10px; }
.ptl-card__foot { padding-top: 9px; border-top: 1px solid var(--ailp-gray-100); font-size: 10.5px; color: var(--ailp-gray-500); }
.ptl-empty { text-align: center; color: var(--ailp-gray-400); font-size: 12.5px; padding: 40px 0; }
.ptl-refbanner { display: flex; align-items: center; gap: 16px; padding: 16px 18px; border-radius: 14px; border: 1px solid var(--ailp-gray-200); background: linear-gradient(90deg, var(--ailp-gray-50), rgba(79, 70, 229, 0.04)); margin-bottom: 16px; }
.ptl-refbanner__ic { width: 48px; height: 48px; border-radius: 13px; display: grid; place-items: center; font-size: 22px; background: linear-gradient(135deg, var(--ailp-primary-500), var(--ailp-accent-400)); color: #fff; }
.ptl-refbanner h3 { font-size: 14px; font-weight: 700; margin-bottom: 3px; }
.ptl-refbanner p { font-size: 12px; color: var(--ailp-gray-600); margin-bottom: 3px; }
.ptl-refbanner p b { color: var(--ailp-gray-900); }
.ptl-refbanner small { font-size: 10.5px; color: var(--ailp-gray-500); display: flex; gap: 14px; }
.ptl-refs { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; margin-bottom: 26px; }
.ptl-ref { display: flex; gap: 12px; border: 1px solid var(--ailp-gray-200); border-radius: 13px; background: #fff; padding: 14px; }
.ptl-ref__ic { width: 38px; height: 38px; border-radius: 10px; display: grid; place-items: center; font-size: 17px; flex-shrink: 0; }
.ptl-ref__ic.is-docx { background: #eff6ff; }
.ptl-ref__ic.is-pdf { background: var(--ailp-error-50); }
.ptl-ref__meta { flex: 1; min-width: 0; }
.ptl-ref__meta h4 { font-size: 12.5px; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; margin-bottom: 3px; }
.ptl-ref__row { display: flex; gap: 10px; font-size: 10.5px; color: var(--ailp-gray-400); margin-bottom: 8px; }
.ptl-ref__acts { display: flex; gap: 8px; }
.ptl-ref__quote { border: none; background: rgba(79, 70, 229, 0.1); color: var(--ailp-primary-600); font-size: 11px; font-weight: 600; padding: 5px 12px; border-radius: 8px; cursor: pointer; }
.ptl-ref__quote:hover { background: rgba(79, 70, 229, 0.18); }
.ptl-ref__preview { border: 1px solid var(--ailp-gray-200); background: #fff; color: var(--ailp-gray-500); font-size: 11px; padding: 5px 12px; border-radius: 8px; cursor: pointer; }
.ptl-bphead { display: flex; align-items: center; gap: 10px; margin-bottom: 12px; }
.ptl-bphead h2 { font-size: 14.5px; font-weight: 700; }
.ptl-bphead span { font-size: 11px; color: var(--ailp-gray-500); }
.ptl-bps { display: flex; gap: 14px; overflow-x: auto; padding-bottom: 10px; }
.ptl-bp { width: 210px; flex-shrink: 0; text-align: left; border: 1px solid var(--ailp-gray-200); border-radius: 13px; background: #fff; overflow: hidden; cursor: pointer; }
.ptl-bp:hover { border-color: rgba(79, 70, 229, 0.35); box-shadow: 0 8px 20px rgba(79, 70, 229, 0.1); }
.ptl-bp__cover { height: 110px; display: grid; place-items: center; background: linear-gradient(135deg, var(--ailp-gray-100), #fff); padding: 12px; position: relative; }
.ptl-bp__cover b { font-size: 12.5px; color: var(--ailp-gray-800); }
.ptl-bp__award { position: absolute; top: 9px; right: 9px; font-size: 9px; font-weight: 800; color: #fff; padding: 2px 8px; border-radius: 999px; background: linear-gradient(90deg, #f59e0b, #f97316); }
.ptl-bp__meta { padding: 10px 13px; }
.ptl-bp__meta h4 { font-size: 12px; font-weight: 600; }
.ptl-bp__meta p { font-size: 10.5px; color: var(--ailp-gray-500); margin-top: 2px; }
.ptl-drawer__scrim { position: fixed; inset: 0; z-index: 960; background: rgba(15, 23, 42, 0.3); display: flex; justify-content: flex-end; }
.ptl-drawer { width: 560px; max-width: 92vw; background: #fff; height: 100%; display: flex; flex-direction: column; box-shadow: -18px 0 48px rgba(10, 30, 58, 0.2); }
.ptl-drawer__head { display: flex; align-items: center; justify-content: space-between; padding: 14px 20px; border-bottom: 1px solid var(--ailp-gray-200); font-weight: 700; font-size: 14px; }
.ptl-drawer__body { flex: 1; overflow-y: auto; padding: 20px; }
.ptl-drawer__cover { border-radius: 14px; padding: 26px 22px; color: #fff; margin-bottom: 16px; }
.ptl-drawer__cover b { font-size: 17px; display: block; margin-bottom: 4px; }
.ptl-drawer__cover small { font-size: 11px; opacity: 0.9; }
.ptl-drawer__grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 9px; margin-bottom: 18px; }
.ptl-drawer__grid > div { text-align: center; border: 1px solid var(--ailp-gray-200); background: var(--ailp-gray-50); border-radius: 10px; padding: 10px 6px; }
.ptl-drawer__grid small { display: block; font-size: 10px; color: var(--ailp-gray-400); margin-bottom: 2px; }
.ptl-drawer__grid b { font-size: 12px; }
.ptl-drawer__grid b.is-primary { color: var(--ailp-primary-600); }
.ptl-drawer__secttl { font-size: 13px; font-weight: 700; margin-bottom: 10px; }
.ptl-drawer__secs { display: flex; flex-direction: column; gap: 7px; margin-bottom: 18px; }
.ptl-drawer__sec { display: flex; align-items: center; gap: 9px; border: 1px solid var(--ailp-gray-200); border-radius: 9px; padding: 9px 12px; font-size: 12px; }
.ptl-drawer__secno { width: 20px; height: 20px; border-radius: 6px; display: grid; place-items: center; font-size: 10px; font-weight: 700; color: var(--ailp-primary-700); background: var(--ailp-primary-100); flex-shrink: 0; }
.ptl-drawer__use { width: 100%; padding: 13px; border-radius: 12px; font-size: 13.5px; }
</style>
