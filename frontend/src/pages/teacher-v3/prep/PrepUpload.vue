<template>
  <div class="pup">
    <header class="pup-head">
      <div class="pup-head__left">
        <button class="ptx-back" data-testid="ailp-up-back" @click="emit('back')">←</button>
        <div class="pup-head__div" />
        <h1>上传资料备课</h1>
      </div>
      <div class="ptx-steps">
        <div class="ailp-step-dot completed">✓</div><span>上传资料</span>
        <i class="ptx-line on" />
        <div class="ailp-step-dot active">✦</div><span class="on">AI 解析</span>
        <i class="ptx-line" />
        <div class="ailp-step-dot">3</div><span>确认生成</span>
      </div>
    </header>

    <main class="pup-main">
      <!-- 左：已上传文件 -->
      <aside class="pup-files ailp-fade">
        <div class="ailp-card" style="height:100%; display:flex; flex-direction:column; overflow:hidden">
          <div class="pup-files__head"><b>已上传文件</b><span>（{{ fileNames.length }}个）</span></div>
          <div class="pup-files__list">
            <div v-for="f in uploadedFiles" :key="f.name" class="pup-file" :class="{ 'is-current': f.name === fileNames[0] }">
              <div class="pup-file__ic">📄</div>
              <div class="pup-file__meta">
                <h3>{{ f.name }}</h3>
                <div class="pup-file__row"><span v-if="f.size">{{ f.size }}</span><i v-if="f.size && f.pages" /><span v-if="f.pages">{{ f.pages }} 页</span></div>
                <div class="pup-file__ok">{{ parsed ? '✓ 已解析' : '● 已记录 · 待解析' }}</div>
              </div>
            </div>
          </div>
          <div class="pup-files__add">
            <p>＋ 继续添加文件</p>
            <label class="pup-drop">
              <span class="pup-drop__ic">📤</span>
              <b>点击或拖拽上传</b>
              <small>PDF · Word · PPT · 图片 · 文本（单文件 ≤ 20MB）</small>
              <input type="file" multiple hidden accept=".pdf,.doc,.docx,.ppt,.pptx,.txt,.png" @change="onFiles">
            </label>
          </div>
        </div>
      </aside>

      <!-- 右：AI 解析预览 -->
      <section class="pup-parse ailp-fade" style="animation-delay:0.1s">
        <div class="ailp-card" style="height:100%; display:flex; flex-direction:column; overflow:hidden">
          <div class="pup-parse__head">
            <div class="pup-parse__title">
              <div class="pup-parse__ic">🔍</div>
              <div>
                <h2>AI 解析预览</h2>
                <p v-if="parsed">AI 已识别文档结构与核心内容</p>
                <p v-else>尚无可用的解析结果</p>
              </div>
            </div>
            <span v-if="parsed" class="ailp-tag ailp-tag--ok">● 解析完成</span>
            <span v-else class="ailp-tag ailp-tag--muted" data-testid="ailp-up-parse-unknown">○ 未解析</span>
          </div>
          <div class="pup-tabs">
            <button class="pup-tab" :class="{ 'is-on': tab === 'structure' }" @click="tab = 'structure'">🗂 内容结构</button>
            <button class="pup-tab" :class="{ 'is-on': tab === 'knowledge' }" @click="tab = 'knowledge'">🏷 知识点提取</button>
            <button class="pup-tab" :class="{ 'is-on': tab === 'examples' }" @click="tab = 'examples'">📖 题目/例题</button>
          </div>

          <div class="pup-body">
            <!-- Q-1：没有解析端点 → 诚实未知态，不展示演示解析结论与置信度 -->
            <div v-if="!parsed" class="pup-unknown" data-testid="ailp-up-parse-empty">
              <div class="pup-unknown__title">尚未生成解析结果</div>
              <p class="pup-unknown__desc">
                {{ fileNames.length ? '已记录你选择的文件。' : '还没有选择文件。' }}
                平台的"上传教案 → 解析结构/知识点/例题"通道尚未接入，因此这里<b>不展示任何解析结论与置信度</b>——
                解析会在点击"确认并生成教案"后由服务端进行。
              </p>
              <p class="pup-unknown__hint">想先预览可引用的资料？去「<router-link to="/teacher-v3/prep-templates">教案模板库</router-link>」。</p>
            </div>
            <template v-else>
            <!-- 内容结构 -->
            <div v-if="tab === 'structure'" class="pup-struct">
              <div class="pup-struct__file">📄 {{ fileNames[0] || '已上传文档' }}<span>共 {{ parsed?.structure.length || 0 }} 个一级章节</span></div>
              <div v-for="sec in parsed?.structure" :key="sec.no" class="pup-sec">
                <button class="pup-sec__head" @click="sec.collapsed = !sec.collapsed">
                  <span class="pup-sec__no" :class="{ 'is-core': sec.name.includes('过程') }">{{ sec.no }}</span>
                  <b>{{ sec.name }}</b>
                  <span class="pup-sec__count">{{ sec.count }}</span>
                  <i>{{ sec.collapsed ? '▸' : '▾' }}</i>
                </button>
                <div v-if="!sec.collapsed && sec.children" class="pup-sec__children">
                  <div v-for="c in sec.children" :key="c.text" class="pup-sec__child">
                    <span v-if="c.tag" class="pup-sec__tag" :class="c.tag === '重' ? 'is-major' : 'is-hard'">{{ c.tag }}</span>
                    <span>{{ c.text }}</span>
                  </div>
                </div>
              </div>
            </div>

            <!-- 知识点 -->
            <div v-else-if="tab === 'knowledge'" class="pup-know">
              <div class="ailp-honest" style="margin-bottom: 10px">⚠ 当前展示示例解析结果（置信度为参考值），正式生成时会重新解析</div>
              <div v-for="k in parsed?.knowledge" :key="k.name" class="pup-know__item" :class="{ 'is-partial': k.partial }">
                <div class="pup-know__top">
                  <span class="pup-know__ok">{{ k.partial ? '△' : '✓' }}</span>
                  <b>{{ k.name }}</b>
                  <span class="pup-know__conf" :class="{ 'is-warn': k.partial }">{{ k.confidence }}%</span>
                </div>
                <div class="pup-know__bar"><i :style="{ width: k.confidence + '%' }" /></div>
                <p>{{ k.desc }}</p>
              </div>
            </div>

            <!-- 例题 -->
            <div v-else class="pup-exs">
              <div class="pup-exs__head">共提取 <b>{{ parsed?.examples.length }}</b> 道例题</div>
              <div v-for="ex in parsed?.examples" :key="ex.no" class="pup-ex">
                <div class="pup-ex__head">
                  <span class="pup-ex__no" :class="`is-d${ex.no % 3}`">例 {{ ex.no }}</span>
                  <b>{{ ex.title }}</b>
                  <span class="ailp-tag ailp-tag--muted">{{ ex.difficulty }}</span>
                </div>
                <p class="pup-ex__stem">{{ ex.stem }}</p>
                <div class="pup-ex__from"><span>📄 {{ ex.from }}</span><button class="pup-ex__edit">✎</button></div>
              </div>
            </div>
            </template>
          </div>

          <div class="pup-parse__foot">
            <template v-if="parsed">
              <span>解析步骤：</span>
              <span class="pup-step-ok">✓ 识别版面布局</span>
              <span class="pup-step-ok">✓ 提取公式与图形</span>
              <span class="pup-step-ok">✓ 分析教学结构</span>
              <span class="pup-step-ok">✓ 生成大纲草稿</span>
              <span class="pup-parse__done">全部完成</span>
            </template>
            <!-- Q-1：无解析端点时不摆"全部完成"的假进度条 -->
            <span v-else data-testid="ailp-up-parse-foot-unknown">解析步骤：尚未开始（生成教案时由服务端执行）</span>
          </div>
        </div>
      </section>
    </main>

    <!-- 底部操作栏 -->
    <footer class="pup-foot pup-foot--col">
      <div class="pup-topic-row">
        <label class="pup-topic__label">本课课题（AI 生成大纲的依据，必填）</label>
        <input
          class="pup-topic__input"
          :value="topic"
          placeholder="例如：椭圆及其标准方程（第 1 课时）——不要用文件名"
          @input="onTopicInput(($event.target as HTMLInputElement).value)"
        />
      </div>
      <div class="pup-foot__left">
        <span>使用方式：</span>
        <div class="pup-foot__toggle">
          <button class="is-on">📖 作为参考资料使用</button>
          <button @click="useAsTemplate">🧩 提炼为我的模板</button>
        </div>
      </div>
      <div class="pup-foot__right">
        <button class="ailp-btn-ghost pup-reparse" @click="reparse">↻ 重新解析</button>
        <button class="ailp-btn-primary pup-go" data-testid="ailp-up-go" :disabled="!canGenerate" @click="go">确认并生成教案 →</button>
      </div>
    </footer>
  </div>
</template>

<script setup lang="ts">
/**
 * PrepUpload —— 上传资料备课（P，用户定稿 HTML 移植）
 * 文件名记录 + AI 解析三 Tab（结构/知识点/例题，确定性演示样例）+ 确认生成。
 * 诚实红线：解析与置信度均为演示样例（页面带标注），文件内容原型不解析。
 */
import { computed, ref } from 'vue'
import { buildParsedUpload, prepDemoEnabled, type ParsedUpload } from './ailpMock'
import { startBrief } from './prepChain'
import { useToastStore } from '@/stores/toast'

const emit = defineEmits<{ (e: 'back'): void; (e: 'start'): void }>()
const toast = useToastStore()
const fileNames = ref<string[]>([])
const parsed = ref<ParsedUpload | null>(null)
const tab = ref<'structure' | 'knowledge' | 'examples'>('structure')
const parseKey = ref(0)
/** Q-1：解析结论只能来自服务端；当前没有该端点 → 生产态如实显示"尚未解析"。 */
const demoOn = prepDemoEnabled()
const parseUnavailable = computed(() => !demoOn)
/** Q-1：解析预览不可用**不能**挡住生成——真正的门槛是"选了文件 + 填了课题"。 */
const canGenerate = computed(() => (parsed.value !== null) || fileNames.value.length > 0)
/** Q-1：解析结论只能来自服务端；当前没有该端点 → 生产态如实显示"尚未解析"。 */
/** Q-1：文件列表来自**真实选择**，不依赖解析结果（解析不可用时也要如实列出文件）。 */
const uploadedFiles = computed(() => {
  if (parsed.value) return parsed.value.files
  return fileNames.value.map((name) => ({ name, size: '', pages: 0 }))
})

const topic = ref('')
const topicTouched = ref(false)
function onTopicInput(v: string) { topic.value = v; topicTouched.value = true }
function _defaultTopic() { return (fileNames.value[0] || '未命名备课').replace(/\.(pdf|docx?|pptx?|txt|png|jpeg|jpg)$/i, '').slice(0, 30) }
// 课题默认取文件名但必须可编辑——文件名（尤其照片 IMG_xxxx）不是课题，AI 需要真实课题才能生成非模板大纲
function ensureTopic() { if (!topicTouched.value || !topic.value.trim()) topic.value = _defaultTopic() }

function onFiles(ev: Event) {
  const files = (ev.target as HTMLInputElement).files
  if (!files?.length) return
  fileNames.value = [...files].slice(0, 4).map((f) => f.name)
  parsed.value = buildParsedUpload(fileNames.value)
  if (!topicTouched.value) topic.value = _defaultTopic()
  toast.info(parsed.value
    ? '已记录文件，内容解析将在生成时进行；当前展示示例解析结果'
    : '已记录文件；内容解析将在生成时进行（当前无法预览解析结果，不展示示例）')
}
function reparse() {
  if (!fileNames.value.length) {
    toast.info('请先选择要解析的文件')
    return
  }
  parsed.value = buildParsedUpload(fileNames.value)
  parseKey.value += 1
  toast.info(parsed.value ? '重新解析完成' : '已重新记录文件；解析结论仍不可用（不展示示例）')
}
function useAsTemplate() {
  toast.info('「提炼为我的模板」功能即将在资源中心开放')
}
function go() {
  ensureTopic()
  if (!topic.value.trim() || topic.value.trim() === '未命名备课') {
    toast.error('请先填写本课课题——文件名不是课题，AI 需要真实课题才能生成不套模板的大纲')
    return
  }
  startBrief({
    source: 'upload',
    topic: topic.value.trim(),
    requirements: [`以下列上传材料为主要参考：${fileNames.value.join('、')}`],
    materials: fileNames.value.map((n) => ({ name: n, size: '已解析' })),
  })
  emit('start')
}
</script>

<style scoped>
.pup { padding-bottom: 90px; }
.pup-foot--col { flex-direction: column; align-items: stretch; gap: 10px; }
.pup-topic-row { display: flex; align-items: center; gap: 12px; }
.pup-topic__label { font-size: 13px; font-weight: 600; color: var(--ailp-gray-700); white-space: nowrap; }
.pup-topic__input { flex: 1; height: 38px; padding: 0 14px; border: 1px solid var(--ailp-gray-200); border-radius: 10px; font-size: 14px; outline: none; background: #fff; }
.pup-topic__input:focus { border-color: var(--ailp-primary-500); box-shadow: 0 0 0 3px rgba(79, 70, 229, .08); }
.pup-head { position: relative; z-index: 5; display: flex; align-items: center; justify-content: space-between; padding: 14px 32px; border-bottom: 1px solid var(--ailp-gray-200); background: rgba(255, 255, 255, 0.8); backdrop-filter: blur(6px); }
.pup-head__left { display: flex; align-items: center; gap: 12px; }
.pup-head__div { width: 1px; height: 22px; background: var(--ailp-gray-200); }
.pup-head h1 { font-size: 16px; font-weight: 700; }
.ptx-back { width: 34px; height: 34px; border-radius: 10px; border: none; background: none; cursor: pointer; font-size: 15px; color: var(--ailp-gray-600); }
.ptx-steps { display: flex; align-items: center; gap: 8px; font-size: 12.5px; color: var(--ailp-gray-500); }
.ptx-steps .on { color: var(--ailp-primary-600); font-weight: 600; }
.ptx-line { width: 36px; height: 2px; background: var(--ailp-gray-200); border-radius: 1px; }
.ptx-line.on { background: var(--ailp-success-500); }
.pup-main { position: relative; z-index: 5; display: flex; gap: 20px; padding: 22px 32px; max-width: 1400px; margin: 0 auto; height: calc(100vh - 210px); min-height: 520px; }
.pup-files { width: 36%; flex-shrink: 0; }
.pup-files__head { padding: 14px 16px; border-bottom: 1px solid var(--ailp-gray-200); font-size: 13.5px; }
.pup-files__head span { color: var(--ailp-gray-400); font-size: 12px; }
.pup-files__list { flex: 1; overflow-y: auto; padding: 14px; display: flex; flex-direction: column; gap: 10px; }
.pup-file { display: flex; gap: 11px; border: 1px solid var(--ailp-gray-200); border-radius: 12px; padding: 12px; background: #fff; }
.pup-file.is-current { border: 2px solid var(--ailp-primary-200); background: rgba(79, 70, 229, 0.03); }
.pup-file__ic { width: 40px; height: 40px; border-radius: 10px; display: grid; place-items: center; background: var(--ailp-error-50); font-size: 17px; flex-shrink: 0; }
.pup-file__meta { flex: 1; min-width: 0; }
.pup-file__meta h3 { font-size: 12.5px; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.pup-file__row { display: flex; gap: 6px; font-size: 10.5px; color: var(--ailp-gray-500); margin-top: 4px; }
.pup-file__row i { font-style: normal; color: var(--ailp-gray-300); }
.pup-file__ok { margin-top: 6px; font-size: 10.5px; font-weight: 600; color: var(--ailp-success-600); }
.pup-files__add { padding: 12px 14px; border-top: 1px solid var(--ailp-gray-200); }
.pup-files__add > p { font-size: 11px; color: var(--ailp-gray-500); margin-bottom: 8px; }
.pup-drop { display: flex; flex-direction: column; align-items: center; gap: 3px; padding: 14px; border: 2px dashed var(--ailp-gray-200); border-radius: 12px; cursor: pointer; background: rgba(248, 250, 252, 0.6); }
.pup-drop:hover { border-color: var(--ailp-primary-400); background: var(--ailp-primary-50); }
.pup-drop__ic { width: 32px; height: 32px; border-radius: 50%; background: #fff; box-shadow: 0 1px 4px rgba(0, 0, 0, 0.08); display: grid; place-items: center; font-size: 14px; }
.pup-drop b { font-size: 11.5px; color: var(--ailp-gray-600); }
.pup-drop small { font-size: 10px; color: var(--ailp-gray-400); }
.pup-parse { flex: 1; min-width: 0; }
.pup-parse__head { display: flex; align-items: center; justify-content: space-between; padding: 14px 18px 0; }
.pup-parse__title { display: flex; align-items: center; gap: 10px; }
.pup-parse__ic { width: 32px; height: 32px; border-radius: 10px; display: grid; place-items: center; color: #fff; background: linear-gradient(135deg, var(--ailp-primary-500), var(--ailp-accent-500)); font-size: 14px; }
.pup-parse__title h2 { font-size: 14.5px; font-weight: 700; }
.pup-parse__title p { font-size: 11px; color: var(--ailp-gray-500); }
.pup-tabs { display: flex; gap: 22px; padding: 10px 18px 0; border-bottom: 1px solid var(--ailp-gray-200); }
.pup-tab { border: none; background: none; padding: 10px 0; font-size: 13px; font-weight: 500; color: var(--ailp-gray-500); cursor: pointer; border-bottom: 2px solid transparent; }
.pup-tab.is-on { color: var(--ailp-primary-600); border-bottom-color: var(--ailp-primary-500); font-weight: 600; }
.pup-body { flex: 1; overflow-y: auto; padding: 16px 18px; }
.pup-unknown { border: 1px dashed var(--ailp-gray-300); border-radius: 12px; padding: 18px 18px 16px; background: var(--ailp-gray-50); }
.pup-unknown__title { font-size: 13.5px; font-weight: 700; color: var(--ailp-gray-800); margin-bottom: 8px; }
.pup-unknown__desc { font-size: 12px; color: var(--ailp-gray-600); line-height: 1.75; margin: 0 0 8px; }
.pup-unknown__hint { font-size: 11.5px; color: var(--ailp-gray-500); margin: 0; }
.pup-struct__file { display: flex; align-items: center; gap: 8px; font-size: 12.5px; font-weight: 600; background: var(--ailp-gray-50); border: 1px solid var(--ailp-gray-200); border-radius: 9px; padding: 9px 12px; margin-bottom: 12px; }
.pup-struct__file span { margin-left: auto; color: var(--ailp-gray-400); font-size: 11px; font-weight: 400; }
.pup-sec { margin-bottom: 7px; border: 1px solid var(--ailp-gray-200); border-radius: 10px; overflow: hidden; }
.pup-sec__head { width: 100%; display: flex; align-items: center; gap: 9px; background: #fff; border: none; padding: 10px 12px; cursor: pointer; }
.pup-sec__no { width: 22px; height: 22px; border-radius: 6px; display: grid; place-items: center; font-size: 10.5px; font-weight: 700; color: var(--ailp-primary-700); background: var(--ailp-primary-100); }
.pup-sec__no.is-core { color: #fff; background: linear-gradient(135deg, var(--ailp-primary-500), var(--ailp-accent-500)); }
.pup-sec__head b { font-size: 12.5px; color: var(--ailp-gray-800); }
.pup-sec__count { margin-left: auto; font-size: 10.5px; padding: 2px 7px; border-radius: 6px; background: var(--ailp-primary-50); color: var(--ailp-primary-600); font-weight: 600; }
.pup-sec__head i { font-style: normal; color: var(--ailp-gray-400); font-size: 11px; }
.pup-sec__children { padding: 2px 12px 10px 42px; display: flex; flex-direction: column; gap: 5px; }
.pup-sec__child { display: flex; align-items: flex-start; gap: 7px; font-size: 11.5px; color: var(--ailp-gray-600); line-height: 1.6; padding: 4px 6px; border-radius: 7px; }
.pup-sec__child:hover { background: var(--ailp-gray-50); }
.pup-sec__tag { flex-shrink: 0; font-size: 9.5px; font-weight: 700; color: #fff; padding: 1px 6px; border-radius: 5px; }
.pup-sec__tag.is-major { background: var(--ailp-primary-600); }
.pup-sec__tag.is-hard { background: var(--ailp-accent-500); }
.pup-know__item { border: 1px solid var(--ailp-gray-200); border-radius: 12px; padding: 12px 14px; margin-bottom: 10px; background: #fff; }
.pup-know__item.is-partial { border-color: var(--ailp-warning-100); background: rgba(255, 251, 235, 0.4); }
.pup-know__top { display: flex; align-items: center; gap: 8px; }
.pup-know__ok { width: 18px; height: 18px; border-radius: 50%; display: grid; place-items: center; font-size: 10px; color: var(--ailp-success-600); background: var(--ailp-success-50); }
.pup-know__top b { font-size: 12.5px; }
.pup-know__conf { margin-left: auto; font-size: 11.5px; font-weight: 700; color: var(--ailp-success-600); }
.pup-know__conf.is-warn { color: var(--ailp-warning-600); }
.pup-know__bar { height: 5px; border-radius: 3px; background: var(--ailp-gray-100); overflow: hidden; margin: 8px 0 6px; }
.pup-know__bar i { display: block; height: 100%; border-radius: 3px; background: linear-gradient(90deg, var(--ailp-primary-400), var(--ailp-accent-400)); }
.pup-know__item.is-partial .pup-know__bar i { background: linear-gradient(90deg, var(--ailp-warning-400), var(--ailp-warning-500)); }
.pup-know__item p { font-size: 10.5px; color: var(--ailp-gray-500); }
.pup-exs__head { font-size: 12.5px; color: var(--ailp-gray-600); margin-bottom: 12px; }
.pup-exs__head b { color: var(--ailp-primary-600); }
.pup-ex { border: 1px solid var(--ailp-gray-200); border-radius: 12px; overflow: hidden; margin-bottom: 12px; background: #fff; }
.pup-ex__head { display: flex; align-items: center; gap: 8px; padding: 9px 13px; background: var(--ailp-gray-50); border-bottom: 1px solid var(--ailp-gray-200); }
.pup-ex__no { font-size: 10.5px; font-weight: 700; color: #fff; padding: 2px 8px; border-radius: 6px; background: var(--ailp-primary-600); }
.pup-ex__no.is-d2 { background: var(--ailp-accent-500); }
.pup-ex__no.is-d0 { background: #8b5cf6; }
.pup-ex__head b { font-size: 12.5px; }
.pup-ex__head .ailp-tag { margin-left: auto; }
.pup-ex__stem { padding: 12px 14px; font-size: 12px; color: var(--ailp-gray-700); line-height: 1.7; }
.pup-ex__from { display: flex; align-items: center; justify-content: space-between; padding: 7px 13px; border-top: 1px dashed var(--ailp-gray-200); font-size: 10.5px; color: var(--ailp-gray-400); }
.pup-ex__edit { border: none; background: none; cursor: pointer; color: var(--ailp-gray-400); }
.pup-parse__foot { display: flex; align-items: center; gap: 14px; padding: 11px 18px; border-top: 1px solid var(--ailp-gray-200); background: rgba(248, 250, 252, 0.7); font-size: 11px; color: var(--ailp-gray-600); }
.pup-step-ok { display: inline-flex; align-items: center; gap: 4px; }
.pup-parse__done { margin-left: auto; color: var(--ailp-success-600); font-weight: 600; }
.pup-foot { position: fixed; left: var(--tv3-nav-w, 216px); right: 0; bottom: 0; z-index: 40; background: rgba(255, 255, 255, 0.94); backdrop-filter: blur(8px); border-top: 1px solid var(--ailp-gray-200); box-shadow: 0 -4px 20px rgba(0, 0, 0, 0.05); }
.pup-foot > div { max-width: 1400px; margin: 0 auto; padding: 0 32px; height: 62px; display: flex; align-items: center; justify-content: space-between; }
.pup-foot__left { display: flex; align-items: center; gap: 10px; font-size: 12.5px; color: var(--ailp-gray-600); }
.pup-foot__toggle { display: inline-flex; background: var(--ailp-gray-100); border-radius: 9px; padding: 3px; }
.pup-foot__toggle button { display: inline-flex; align-items: center; gap: 5px; border: none; background: none; font-size: 11.5px; font-weight: 500; color: var(--ailp-gray-500); padding: 6px 12px; border-radius: 7px; cursor: pointer; }
.pup-foot__toggle button.is-on { background: #fff; color: var(--ailp-primary-600); font-weight: 600; box-shadow: 0 1px 4px rgba(0, 0, 0, 0.08); border: 1px solid var(--ailp-gray-200); }
.pup-foot__right { display: flex; align-items: center; gap: 12px; }
.pup-reparse { padding: 8px 14px; border-radius: 10px; font-size: 12.5px; }
.pup-go { padding: 10px 22px; border-radius: 12px; font-size: 13px; }
</style>
