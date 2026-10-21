<template>
  <transition name="tv3-butler-slide">
    <aside v-if="open" class="tv3-butler" role="dialog" aria-label="AI 管家" data-testid="tv3-butler-panel">
      <!-- 头部 -->
      <header class="tv3-butler__head">
        <div class="tv3-butler__orb">✦</div>
        <div class="tv3-butler__head-txt">
          <b>AI 管家</b>
          <span>对话 · 搜索 · 语音公式 · 应用内操作</span>
        </div>
        <button class="tv3-butler__close" type="button" aria-label="收起" data-testid="tv3-butler-close" @click="$emit('close')">─</button>
      </header>

      <!-- 上下文条：自动附加当前页面上下文（Copilot 式），教师可展开查看 -->
      <button class="tv3-butler__ctx" type="button" data-testid="tv3-butler-ctx" @click="ctxOpen = !ctxOpen">
        <span class="tv3-butler__ctx-dot" />
        <span>正在跟随：{{ ctxLabel }}</span>
        <span class="tv3-butler__ctx-arrow" :class="{ open: ctxOpen }">▾</span>
      </button>
      <div v-if="ctxOpen" class="tv3-butler__ctx-detail" data-testid="tv3-butler-ctx-detail">
        <div>页面：{{ ctxLabel }}</div>
        <div>班级：{{ context.class_id || '尚未选择' }}</div>
        <div>课题/工件：{{ ctxTopic || '尚未打开' }}</div>
        <div>课件：{{ ctxSlideLabel || '尚未打开（打开后可插入/优化）' }}</div>
        <div>选中对象：{{ context.selection ? context.selection.summary : '未选中（点选画布元素/文字后跟随）' }}</div>
        <div v-if="ctxExtra">当前状态：{{ ctxExtra }}</div>
        <div class="tv3-butler__ctx-note">以上信息随每条消息自动发送给管家；未采集项如实标注，不用演示值冒充</div>
      </div>

      <!-- 对话流 -->
      <div ref="flowRef" class="tv3-butler__flow">
        <div v-if="!messages.length" class="tv3-butler__empty">
          <div class="tv3-butler__empty-title">您好，李老师</div>
          <div>可以直接吩咐我做：</div>
          <div class="tv3-butler__chips" data-testid="tv3-butler-chips">
            <button v-for="q in quickAsks" :key="q.label" class="tv3-butler__chip" type="button" :data-testid="`tv3-butler-chip-${q.action || 'ask'}`" @click="onQuick(q)">{{ q.label }}</button>
          </div>
        </div>

        <template v-for="m in messages" :key="m.id">
          <div class="tv3-butler__msg" :class="m.role" :data-testid="`tv3-butler-msg-${m.role}`">
            <template v-if="m.role === 'butler'">
              <div v-if="m.thinking" class="tv3-butler__thinking">{{ m.thinking }}</div>
              <div v-if="m.text" class="tv3-butler__bubble" v-html="renderRich(m.text)" />
              <div v-for="line in m.toolLines" :key="line" class="tv3-butler__toolline" :data-testid="'tv3-butler-toolline'">{{ line }}</div>
              <span v-if="m.pending" class="tv3-butler__caret" />

              <!-- 公式卡片：可拖拽进画布 / 点开编辑 / 插入本页 -->
              <div
                v-for="c in formulaCards(m)" :key="c.id"
                class="tv3-butler__card tv3-butler__card--formula"
                draggable="true"
                :data-testid="'tv3-butler-card-formula'"
                :title="'按住拖到课件画布即可插入；插入后仍可编辑'"
                @dragstart="onFormulaDrag($event, c)"
              >
                <div class="tv3-butler__formula" v-html="renderLatex(c.latex, true)" />
                <div class="tv3-butler__card-meta">
                  <!-- B0 诚实化：mock 解析不显示伪造的精确置信度；插入按钮仅在存在课件上下文时可用 -->
                  <span class="tv3-butler__conf" title="由识别解析生成，可手动编辑后插入">解析候选</span>
                  <span v-if="c.source === 'voice'" class="tv3-butler__src">🎤 语音</span>
                  <span v-else-if="c.source === 'photo'" class="tv3-butler__src">📷 识别</span>
                  <span class="tv3-butler__spacer" />
                  <!-- B6 §10.3：插入前预览目标（课件+页码），确认才执行，结果有回执 -->
                  <template v-if="context.deck_id">
                    <template v-if="insertConfirm === c.id">
                      <span class="tv3-butler__conf" style="color: #b45309">到 {{ context.deck_title || '当前课件' }} 第 {{ (context.slide_index ?? 0) + 1 }} 页</span>
                      <button class="tv3-butler__mini tv3-butler__mini--go" type="button" :data-testid="`tv3-butler-insert-confirm-${c.id}`" @click="confirmInsert(c)">✓ 确认</button>
                      <button class="tv3-butler__mini" type="button" @click="insertConfirm = ''">取消</button>
                    </template>
                    <button v-else class="tv3-butler__mini" type="button" title="插入当前课件页（先预览目标）" @click="insertConfirm = c.id">插入本页</button>
                  </template>
                  <button v-else class="tv3-butler__mini" type="button" disabled title="尚未打开课件：请先在备课会话里打开一份课件，或把卡片直接拖入画布">未打开课件</button>
                </div>
                <div v-if="c.alternatives?.length" class="tv3-butler__alts">备选：{{ c.alternatives.join('　') }}</div>
              </div>

              <!-- 动作卡：写操作必须教师点执行（红线：AI 不直接落库） -->
              <div v-for="c in actionCards(m)" :key="c.id" class="tv3-butler__card tv3-butler__card--action" data-testid="tv3-butler-card-action">
                <div class="tv3-butler__card-title">⚙ {{ c.title }}</div>
                <div class="tv3-butler__card-body">{{ c.summary }}</div>
                <div class="tv3-butler__card-meta">
                  <span class="tv3-butler__warn">执行前请核对，结果可再编辑</span>
                  <span class="tv3-butler__spacer" />
                  <template v-if="c.status === 'pending'">
                    <button class="tv3-butler__mini" type="button" @click="cancelAction(m, c)">取消</button>
                    <button class="tv3-butler__mini tv3-butler__mini--go" type="button" data-testid="tv3-butler-action-confirm" @click="execAction(m, c)">执行</button>
                  </template>
                  <span v-else-if="c.status === 'executed'" class="tv3-butler__ok">✓ 已执行</span>
                  <span v-else class="tv3-butler__dim">已取消</span>
                </div>
              </div>

              <!-- 链接卡：跳转入口留底 -->
              <button
                v-for="c in linkCards(m)" :key="c.id"
                class="tv3-butler__card tv3-butler__card--link" type="button"
                data-testid="tv3-butler-card-link"
                @click="goLink(c)"
              >
                <div class="tv3-butler__card-title">→ {{ c.title }}</div>
                <div class="tv3-butler__card-body" v-if="c.note">{{ c.note }}</div>
              </button>

              <!-- C2 工具卡：AI 调用伴随资源台 / 数学绘图 -->
              <button
                v-for="c in toolCards(m)" :key="c.id"
                class="tv3-butler__card tv3-butler__card--link" type="button"
                :data-testid="`tv3-butler-card-tool-${c.tool}`"
                @click="openToolCard(c)"
              >
                <div class="tv3-butler__card-title">{{ c.tool === 'resource' ? '📚' : '📐' }} {{ c.title }}</div>
                <div class="tv3-butler__card-body" v-if="c.summary">{{ c.summary }}</div>
              </button>

              <!-- 搜索引用 -->
              <div v-if="m.citations?.length" class="tv3-butler__cites" data-testid="tv3-butler-citations">
                <span v-for="s in m.citations" :key="s.index" class="tv3-butler__cite" :title="s.snippet">[{{ s.index }}] {{ s.title }}</span>
              </div>

              <!-- KNR 依据折叠（V2.1 §4.10 前端映射）：来源带 source_type/ref 时给出「依据 N 份资料」；无则保持现状 -->
              <template v-if="hasEvidence(m)">
                <button class="tv3-butler__evid-toggle" type="button" data-testid="tv3-butler-evid-toggle" @click="evidOpen[m.id] = !evidOpen[m.id]">
                  {{ evidOpen[m.id] ? '▾' : '▸' }} 依据 {{ m.citations!.length }} 份资料
                </button>
                <div v-if="evidOpen[m.id]" class="tv3-butler__evid" data-testid="tv3-butler-evid">
                  <div v-for="s in m.citations" :key="s.index" class="tv3-butler__evid-item">
                    <span class="tv3-butler__evid-type" :data-type="s.source_type">{{ evidTypeLabel(s.source_type) }}</span>
                    <a v-if="s.url" :href="s.url" target="_blank" rel="noopener">{{ s.title }}</a>
                    <span v-else>{{ s.title }}</span>
                    <span v-if="s.ref" class="tv3-butler__evid-ref">{{ s.ref }}</span>
                  </div>
                </div>
              </template>
            </template>
            <template v-else>
              <div class="tv3-butler__bubble" v-text="m.text" />
              <div v-if="m.images?.length" class="tv3-butler__thumbs">
                <img v-for="(img, i) in m.images" :key="i" :src="img" alt="附件" class="tv3-butler__thumb" />
              </div>
            </template>
          </div>
        </template>

        <!-- 语音公式实时转写（教师能看见 AI 听到了什么） -->
        <div v-if="asrLive" class="tv3-butler__asr" data-testid="tv3-butler-asr">🎤 {{ asrLive }}<span class="tv3-butler__caret" /></div>
      </div>

      <!-- 输入区 -->
      <footer class="tv3-butler__input">
        <div v-if="voiceMode" class="tv3-butler__voice-box" data-testid="tv3-butler-voice-box">
          <div class="tv3-butler__voice-hint">语音识别即将上线：当前可输入公式文本，说“公式”即可</div>
          <input
            v-model="voiceText"
            class="tv3-butler__voice-input"
            placeholder="例如：负b加减根号下b平方减4ac，除以2a"
            data-testid="tv3-butler-voice-input"
            @keydown.enter.prevent="sendVoice"
          />
          <div class="tv3-butler__voice-ops">
            <button class="tv3-butler__mini tv3-butler__mini--go" type="button" data-testid="tv3-butler-voice-send" @click="sendVoice">🎤 转公式</button>
            <button class="tv3-butler__mini" type="button" @click="voiceMode = false">收起</button>
          </div>
        </div>

        <!-- KNR 上下文条（服务端 meta.session_context）：输入框上方一排 chips，点击展开明细；无该字段整条隐藏 -->
        <div v-if="sessionContext" class="tv3-butler__sctx" data-testid="tv3-butler-sctx">
          <div class="tv3-butler__sctx-chips">
            <button v-for="chip in sctxChips" :key="chip" class="tv3-butler__chip" type="button" @click="sctxOpen = !sctxOpen">{{ chip }}</button>
          </div>
          <div v-if="sctxOpen" class="tv3-butler__sctx-detail" data-testid="tv3-butler-sctx-detail">
            <div>当前教材：{{ sessionContext.textbook || '未关联（可在资源中心上传教材后关联）' }}</div>
            <div>当前章节：{{ sessionContext.chapter || '未定位' }}</div>
            <div>当前课件：{{ sessionContext.deck_title || '尚未打开' }}</div>
            <div>课标：{{ sessionContext.curriculum ? `✓ ${sessionContext.curriculum}` : '未关联' }}</div>
            <div>教师偏好：{{ sessionContext.preferences?.length ? sessionContext.preferences.map((p) => `✓ ${p}`).join('　') : '暂无' }}</div>
          </div>
        </div>

        <div v-if="images.length" class="tv3-butler__attach">
          <img v-for="(img, i) in images" :key="i" :src="img" alt="" class="tv3-butler__thumb" />
          <button class="tv3-butler__mini" type="button" @click="clearImages">清空</button>
        </div>

        <div class="tv3-butler__row">
          <textarea
            v-model="input"
            class="tv3-butler__ta"
            rows="2"
            placeholder="问我数学、让我做课件、或拖入题目照片说“存入题库”…"
            data-testid="tv3-butler-input"
            @keydown.enter.exact.prevent="send"
          />
        </div>
        <div class="tv3-butler__ops">
          <button class="tv3-butler__op" type="button" :class="{ on: webSearch }" title="联网搜索" data-testid="tv3-butler-web" @click="webSearch = !webSearch">🌐 联网</button>
          <button class="tv3-butler__op" type="button" :class="{ on: kbSearch }" title="个人知识库检索" @click="kbSearch = !kbSearch">📚 知识库</button>
          <span class="tv3-butler__spacer" />
          <button class="tv3-butler__op" type="button" title="语音公式" data-testid="tv3-butler-mic" @click="voiceMode = !voiceMode">🎤</button>
          <button class="tv3-butler__op" type="button" title="上传图片" @click="fileRef?.click()">📎</button>
          <button
            class="tv3-butler__send" type="button" :disabled="streaming || !input.trim()"
            data-testid="tv3-butler-send" @click="send"
          >{{ streaming ? '…' : '发送' }}</button>
        </div>
        <input ref="fileRef" type="file" accept="image/*" multiple hidden @change="onFiles" />
      </footer>
    </aside>
  </transition>
</template>

<script setup lang="ts">
/**
 * ButlerPanel —— AI 管家侧边栏（V3 教师端 chat-to-action）
 * 三区：上下文条（自动附加页面上下文）/ 对话流（气泡+四类卡片）/ 输入区（文本·语音·图片·开关）。
 * 红线落实：识别与生成结果只出卡片（可拖入画布、teacher_confirmed=false）；
 *          写操作（入库等）只出确认卡，教师点「执行」才调后端；跳转走前端 Action Registry。
 * 卡片拖拽：公式卡双通道（mx/latex 复用画布既有 drop；x-v3-element 通用元素通道）。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { v3Api } from '@/api/teacherV3'
import { presignUpload } from '@/api/teacherV3Upload'
import { renderLatex, renderRich } from '@/components/mathx/latex'
import { useToastStore } from '@/stores/toast'
import { useTv3Context, contextBelongsTo, logDecision, decisionLog } from '@/stores/teacherContext'
import type { V3ButlerAction, V3ButlerCard, V3ButlerContext, V3ButlerMessage } from '@/types/teacherV3'

type Msg = V3ButlerMessage & { thinking?: string; toolLines?: string[]; images?: string[] }

const props = defineProps<{ open: boolean }>()

defineExpose({ ask })
const emit = defineEmits<{ (e: 'close'): void; (e: 'activity'): void }>()

const route = useRoute()
const router = useRouter()
const toast = useToastStore()

const messages = ref<Msg[]>([])
const input = ref('')
const images = ref<string[]>([])
/* M2-C 预签名直传（IFC-004）：预览 dataURL 仅本地显示；发送给后端的是直传后的对象 key */
const imageKeys = ref<string[]>([])
const webSearch = ref(false)
const kbSearch = ref(false)
const streaming = ref(false)
const voiceMode = ref(false)
const voiceText = ref('')
const asrLive = ref('')
const ctxOpen = ref(false)
const flowRef = ref<HTMLElement | null>(null)
const fileRef = ref<HTMLInputElement | null>(null)
let sseCtrl: { abort: () => void; finished: Promise<unknown> } | null = null

/* ---------- 上下文采集（Copilot 式自动附加） ---------- */
const ROUTE_TITLES: Record<string, string> = {
  '/teacher-v3/today': '今日工作台', '/teacher-v3/prep': '备课中心', '/teacher-v3/slides': '课件工坊',
  '/teacher-v3/bank': '题库', '/teacher-v3/quiz': '组卷中心', '/teacher-v3/assign': '作业与批改',
  '/teacher-v3/classroom': '课堂互动', '/teacher-v3/insights': '学情洞察', '/teacher-v3/resources': '资源中心',
}
/* B6：上下文 = 路由 + 各视图实时写入的工件上下文（teacherContext）。
   route 不匹配时工件字段视为未采集（显示尚未打开/未选择，不用演示值冒充）。 */
const ctxStore = useTv3Context()
/* 本地扩展视图类型：shared V3ButlerContext 不扩字段（IFC-PRODUCT-02 登记中）；发送时按需截断 */
interface ButlerCtxView extends V3ButlerContext { deck_title?: string; slide_count?: number; class_id?: string }
const context = computed<ButlerCtxView>(() => {
  const trusted = contextBelongsTo(route.path)
  const deckId = typeof route.query.deck === 'string' ? route.query.deck : (trusted ? ctxStore.deck_id : undefined)
  return {
    route: route.path,
    route_title: ROUTE_TITLES[route.path] || '教师工作台',
    deck_id: deckId,
    deck_title: trusted ? ctxStore.deck_title : undefined,
    slide_index: trusted ? ctxStore.slide_index : undefined,
    slide_count: trusted ? ctxStore.slide_count : undefined,
    selection: trusted && ctxStore.selection ? { ...ctxStore.selection, type: ctxStore.selection.type as any } : undefined,
    class_id: trusted && ctxStore.class_name ? ctxStore.class_name : undefined,
  }
})
const ctxTopic = computed(() => (contextBelongsTo(route.path) ? ctxStore.topic : undefined))
const ctxExtra = computed(() => (contextBelongsTo(route.path) ? ctxStore.extra : undefined))
const ctxSlideLabel = computed(() => {
  if (!context.value.deck_id) return undefined
  const idx = context.value.slide_index
  const n = context.value.slide_count
  return (context.value.deck_title || context.value.deck_id) + (idx !== undefined ? ' · 第 ' + (idx + 1) + (n ? '/' + n : '') + ' 页' : '')
})
const ctxLabel = computed(() => context.value.route_title || context.value.route)

/* B6 §10.4：快捷动作随页面变化，且每条都做真实的事（事件派发给页面 / 带上下文跳转），不是换位置的聊天框 */
interface QuickAsk { label: string; action?: string; ask?: string }
const QUICK_BY_ROUTE: Record<string, QuickAsk[]> = {
  '/teacher-v3/prep': [
    { label: '✎ 打开数学输入台', action: 'open-dock' },
    { label: '◎ 目标覆盖检查', action: 'trace-check' },
    { label: '✦ AI 起草新教案', action: 'new-plan' },
  ],
  '/teacher-v3/slides': [
    { label: '🩺 可讲性体检', action: 'run-check' },
    { label: '＋ 从题库插入题目', action: 'open-bank' },
    { label: '✦ AI 优化本页', action: 'ai-element' },
  ],
  '/teacher-v3/quiz': [
    { label: '📋 发布前清单', action: 'publish-check' },
    { label: '📷 扫描入库', action: 'scan-open' },
  ],
  '/teacher-v3/assign': [
    { label: '✦ 生成讲评课件', action: 'gen-review' },
  ],
  '/teacher-v3/bank': [{ label: '→ 带筛选去组卷', action: 'goto-quiz' }],
  '/teacher-v3/classroom': [{ label: '🖥 打开讲台模式', action: 'podium' }],
  '/teacher-v3/insights': [{ label: '✎ 把薄弱点纳入下一课备课', action: 'goto-prep' }],
  '/teacher-v3/resources': [{ label: '⚙ 打开构造配方库', action: 'goto-slides' }],
  '/teacher-v3/today': [
    { label: '→ 备课中心', action: 'goto-prep' },
  ],
}
const quickAsks = computed<QuickAsk[]>(() => QUICK_BY_ROUTE[route.path] || [
  { label: '帮我做一个《椭圆及其标准方程》的课件', ask: '帮我做一个《椭圆及其标准方程》的课件' },
  { label: '根号下x平方加y平方', ask: '根号下x平方加y平方' },
])
function onQuick(q: QuickAsk) {
  if (q.ask) { ask(q.ask); return }
  if (!q.action) return
  if (q.action.startsWith('goto-')) {
    // WP-G：goto-slides 语义改为"去备课会话"（课件产物只在会话里），不再绕兼容路由
    const map: Record<string, string> = { 'goto-quiz': '/teacher-v3/quiz', 'goto-prep': '/teacher-v3/prep', 'goto-slides': '/teacher-v3/prep' }
    void router.push(map[q.action] || route.path)
    return
  }
  window.dispatchEvent(new CustomEvent('tv3-butler-quick', { detail: { action: q.action } }))
  logDecision('execute', '快捷动作 ' + q.action)
}

/* ---------- 卡片过滤 ---------- */
const formulaCards = (m: Msg) => (m.cards || []).filter((c): c is Extract<V3ButlerCard, { type: 'formula' }> => c.type === 'formula')
const actionCards = (m: Msg) => (m.cards || []).filter((c): c is Extract<V3ButlerCard, { type: 'action' }> => c.type === 'action')
const linkCards = (m: Msg) => (m.cards || []).filter((c): c is Extract<V3ButlerCard, { type: 'link' }> => c.type === 'link')
const toolCards = (m: Msg) => (m.cards || []).filter((c): c is Extract<V3ButlerCard, { type: 'tool' }> => c.type === 'tool')
function openToolCard(c: Extract<V3ButlerCard, { type: 'tool' }>) {
  /* C2：AI 调用工具——让伴随资源台/数学绘图带上下文打开，而不是在聊天里输出一堆题目 */
  window.dispatchEvent(new CustomEvent('tv3-open-companion', { detail: { tool: c.tool } }))
  toast.info(c.tool === 'resource' ? '伴随资源台已打开：候选按当前上下文给出，插入需你确认' : '数学绘图已打开：完成后插入当前工作位置')
}

/* ---------- 对话发送（SSE） ---------- */
function ask(q: string) { input.value = q; void send() }

async function send() {
  const text = input.value.trim()
  if (!text || streaming.value) return
  const teacherMsg: Msg = { id: `m${Date.now()}`, role: 'teacher', text, images: images.value.length ? [...images.value] : undefined }
  messages.value.push(teacherMsg, { id: `m${Date.now()}b`, role: 'butler', text: '', cards: [], pending: true, toolLines: [] })
  /* push 入参的原始对象不触发响应式：从 reactive 数组取 proxy 引用用于流式更新 */
  const butlerMsg = messages.value[messages.value.length - 1] as Msg
  input.value = ''
  // 附件：有直传 key 发 key（后端可从 MinIO 取原图）；key 未就绪回落 dataURL（P0 兼容）
  const sentImages = imageKeys.value.length === images.value.length && imageKeys.value.length > 0 ? [...imageKeys.value] : [...images.value]
  images.value = []
  imageKeys.value = []
  streaming.value = true
  scrollToBottom()
  try {
    /* 多轮上下文：本地会话前 N 轮上传（后端注入 LLM messages） */
    const history = messages.value
      .slice(0, -2)
      .filter((mm) => mm.text && !mm.pending)
      .map((mm) => ({ role: (mm.role === 'teacher' ? 'user' : 'assistant') as 'user' | 'assistant', text: (mm.text || '').slice(0, 400) }))
      .slice(-8)
    sseCtrl = v3Api.butler.chat(
      { message: text, context: context.value as V3ButlerContext, history, images: sentImages.length ? sentImages : undefined, web_search: webSearch.value, kb_search: kbSearch.value },
      (event, data) => handleEvent(butlerMsg, event, data),
    )
    await sseCtrl.finished
  } catch (err) {
    butlerMsg.text = (butlerMsg.text || '') + `\n\n（连接中断：${(err as Error).message}）`
  } finally {
    butlerMsg.pending = false
    streaming.value = false
    sseCtrl = null
    emit('activity')
    scrollToBottom()
  }
}

function handleEvent(m: Msg, event: string, data: any) {
  if (event === 'meta') {
    // KNR 上下文条（§4.10）：服务端可选下发 session_context；缺省整条隐藏（不动布局）
    sessionContext.value = data.session_context || null
    return
  }
  if (event === 'thinking') { m.thinking = data.text; return }
  if (event === 'token') { m.text = (m.text || '') + String(data.text); scrollToBottom(); return }
  if (event === 'tool_call') { m.toolLines = [...(m.toolLines || []), `⚙ ${data.label}…`]; return }
  if (event === 'tool_result') { m.toolLines = [...(m.toolLines || []), `${data.ok ? '✓' : '✗'} ${data.summary}`]; return }
  if (event === 'card') { m.cards = [...(m.cards || []), data as V3ButlerCard]; scrollToBottom(); return }
  if (event === 'citation') { m.citations = data.sources; return }
  if (event === 'action') { runAction(data as V3ButlerAction); return }
}

/* ---------- 前端 Action Registry ---------- */
function runAction(a: V3ButlerAction) {
  if (a.action === 'navigate' && a.route) {
    void router.push({ path: a.route, query: a.query || {} })
    if (a.toast) toast.success(a.toast)
  }
}

function goLink(c: Extract<V3ButlerCard, { type: 'link' }>) {
  void router.push({ path: c.route, query: c.query || {} })
}

/* ---------- 写动作：教师确认后执行（红线） ---------- */
async function execAction(m: Msg, c: Extract<V3ButlerCard, { type: 'action' }>) {
  try {
    await v3Api.butler.confirmAction(c.id, { params: c.params })
    c.status = 'executed'
    toast.success(`${c.title}已完成，可在对应模块继续编辑`)
    messages.value.push({ id: `m${Date.now()}b`, role: 'butler', text: `已完成「${c.title}」。${c.title.includes('题库') ? '可在【题库】中查看与修改该题。' : ''}` })
    emit('activity')
  } catch (err) {
    toast.error(`执行失败：${(err as Error).message}`)
  }
  void m
}
function cancelAction(_m: Msg, c: Extract<V3ButlerCard, { type: 'action' }>) { c.status = 'cancelled' }

/* ---------- 语音公式链（P0 文本模拟；P1 真 ASR） ---------- */
async function sendVoice() {
  const text = voiceText.value.trim()
  if (!text || streaming.value) return
  voiceText.value = ''
  asrLive.value = ''
  const butlerMsg: Msg = { id: `m${Date.now()}b`, role: 'butler', text: '', cards: [], pending: true, toolLines: [] }
  messages.value.push({ id: `m${Date.now()}`, role: 'teacher', text: `🎤（语音）${text}` }, butlerMsg)
  streaming.value = true
  try {
    sseCtrl = v3Api.butler.voiceFormula({ text, context: context.value }, (event, data) => {
      if (event === 'asr_partial') { asrLive.value += String(data.text); scrollToBottom() }
      else if (event === 'asr_final') { asrLive.value = String(data.text) }
      else handleEvent(butlerMsg, event, data)
    })
    await sseCtrl.finished
  } catch (err) {
    butlerMsg.text = (butlerMsg.text || '') + `\n\n（连接中断：${(err as Error).message}）`
  } finally {
    asrLive.value = ''
    butlerMsg.pending = false
    streaming.value = false
    sseCtrl = null
    emit('activity')
    scrollToBottom()
  }
}

/* ---------- 公式卡片：拖拽 + 插入 ---------- */
function onFormulaDrag(ev: DragEvent, c: Extract<V3ButlerCard, { type: 'formula' }>) {
  if (!ev.dataTransfer) return
  ev.dataTransfer.setData('mx/latex', c.latex)
  ev.dataTransfer.setData(
    'application/x-v3-element',
    JSON.stringify({ type: 'formula', latex: c.latex, width: 460, height: 56, font_size: 24, teacher_confirmed: false }),
  )
}
/** 插入当前课件页：画布在工作台内嵌层级，用全局事件送达（SlidesView 监听后落布并提示结果） */
/* B6：插入确认状态 + 决策记录（仅本机 localStorage，不入库） */
const insertConfirm = ref('')
function confirmInsert(c: Extract<V3ButlerCard, { type: 'formula' }>) {
  insertFormula(c)
  insertConfirm.value = ''
  logDecision('execute', '管家公式卡插入当前课件页')
}
function insertFormula(c: Extract<V3ButlerCard, { type: 'formula' }>) {
  window.dispatchEvent(new CustomEvent('tv3-butler-insert', {
    detail: { type: 'formula', latex: c.latex, width: 460, height: 56, font_size: 24, teacher_confirmed: false },
  }))
}

/* ---------- 图片附件：预签名直传（M2-C，原图原样进 MinIO，红线 3） ---------- */
function onFiles(ev: Event) {
  const files = (ev.target as HTMLInputElement).files
  if (!files) return
  for (const f of [...files].slice(0, 4)) {
    if (!f.type.startsWith('image/')) continue
    const rd = new FileReader()
    rd.onload = () => { if (typeof rd.result === 'string') images.value.push(rd.result) }
    rd.readAsDataURL(f)
    presignUpload(f)
      .then((h) => { imageKeys.value.push(h.key) })
      .catch(() => { toast.info('原图直传失败：将随消息以内联方式发送') })
  }
  ;(ev.target as HTMLInputElement).value = ''
}
function clearImages() { images.value = []; imageKeys.value = [] }

/* ---------- KNR 两 slot（V2.1 §4.10，additive；不动既有布局） ---------- */
const sessionContext = ref<{ textbook?: string; chapter?: string; class_name?: string; deck_title?: string; curriculum?: string; preferences?: string[] } | null>(null)
const sctxOpen = ref(false)
const sctxChips = computed(() => {
  const s = sessionContext.value
  if (!s) return []
  return [s.textbook, s.chapter, s.class_name, s.deck_title].filter((x): x is string => !!x)
})
/** 依据折叠：来源带 source_type/ref 才出现；无可选字段保持现状渲染 */
function hasEvidence(m: Msg) { return !!m.citations?.some((s) => s.source_type || s.ref) }
const evidOpen = ref<Record<string, boolean>>({})
function evidTypeLabel(t?: string) {
  return ({ textbook: '教材', curriculum: '课标', benchmark: '基准', quiz: '题库', deck: '课件', class_data: '学情', web: '网页' } as Record<string, string>)[t || ''] || '资料'
}

function scrollToBottom() {
  void nextTick(() => { if (flowRef.value) flowRef.value.scrollTop = flowRef.value.scrollHeight })
}

onMounted(() => window.addEventListener('keydown', onKey))
onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKey)
  sseCtrl?.abort()
})
function onKey(ev: KeyboardEvent) {
  if (ev.key === 'Escape' && props.open) emit('close')
}
watch(() => props.open, (v) => { if (v) scrollToBottom() })
</script>

<style scoped>
.tv3-butler {
  position: fixed; right: 0; top: 0; bottom: 0; z-index: 880; width: 400px; max-width: 92vw;
  display: flex; flex-direction: column;
  background: var(--tv3-card, #fff); border-left: 1px solid var(--tv3-line, #e2e7ef);
  box-shadow: -12px 0 36px rgba(15, 32, 60, 0.12);
}
.tv3-butler-slide-enter-active, .tv3-butler-slide-leave-active { transition: transform .22s ease; }
.tv3-butler-slide-enter-from, .tv3-butler-slide-leave-to { transform: translateX(100%); }

.tv3-butler__head { display: flex; align-items: center; gap: 10px; padding: 14px 14px 10px; border-bottom: 1px solid var(--tv3-line, #e2e7ef); }
.tv3-butler__orb {
  width: 34px; height: 34px; border-radius: 50%; display: grid; place-items: center; font-size: 16px; color: #fff;
  background: linear-gradient(135deg, #4f46e5, #123a6d); flex: none;
}
.tv3-butler__head-txt b { font-size: 14px; display: block; line-height: 1.3; }
.tv3-butler__head-txt span { font-size: 11px; color: var(--tv3-ink3, #8a94a6); display: block; }
.tv3-butler__close { margin-left: auto; border: none; background: none; cursor: pointer; font-size: 15px; color: var(--tv3-ink3, #8a94a6); padding: 4px 8px; border-radius: 6px; }
.tv3-butler__close:hover { background: var(--tv3-bg2, #f4f6fa); }

.tv3-butler__ctx {
  display: flex; align-items: center; gap: 8px; width: 100%; text-align: left;
  border: none; border-bottom: 1px solid var(--tv3-line, #e2e7ef); background: #f7f9fd; cursor: pointer;
  padding: 8px 14px; font-size: 11.5px; color: var(--tv3-ink2, #4a5568);
}
.tv3-butler__ctx-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--tv3-teal, #0e9488); flex: none; }
.tv3-butler__ctx-arrow { margin-left: auto; transition: transform .15s; }
.tv3-butler__ctx-arrow.open { transform: rotate(180deg); }
.tv3-butler__ctx-detail { padding: 8px 14px 10px; font-size: 11.5px; color: var(--tv3-ink3, #8a94a6); background: #f7f9fd; border-bottom: 1px solid var(--tv3-line, #e2e7ef); line-height: 1.7; }
.tv3-butler__ctx-note { color: var(--tv3-gold, #0891b2); }

.tv3-butler__flow { flex: 1; overflow-y: auto; padding: 14px; display: flex; flex-direction: column; gap: 12px; }
.tv3-butler__empty { text-align: center; color: var(--tv3-ink3, #8a94a6); font-size: 12.5px; padding: 26px 8px; line-height: 1.8; }
.tv3-butler__empty-title { font-size: 15px; font-weight: 700; color: var(--tv3-ink, #16233b); margin-bottom: 4px; }
.tv3-butler__chips { display: flex; flex-wrap: wrap; gap: 8px; justify-content: center; margin-top: 12px; }
.tv3-butler__chip {
  border: 1px solid var(--tv3-line, #e2e7ef); background: #fff; border-radius: 999px; cursor: pointer;
  padding: 6px 12px; font-size: 12px; color: var(--tv3-ink2, #4a5568);
}
.tv3-butler__chip:hover { border-color: var(--tv3-navy, #4f46e5); color: var(--tv3-navy, #4f46e5); }

.tv3-butler__msg { display: flex; flex-direction: column; gap: 6px; }
.tv3-butler__msg.teacher { align-items: flex-end; }
.tv3-butler__msg.butler { align-items: flex-start; }
.tv3-butler__bubble {
  max-width: 92%; padding: 9px 12px; border-radius: 12px; font-size: 13px; line-height: 1.65;
  overflow-wrap: anywhere;
}
.tv3-butler__msg.teacher .tv3-butler__bubble { background: var(--tv3-navy, #4f46e5); color: #fff; border-bottom-right-radius: 4px; }
.tv3-butler__msg.butler .tv3-butler__bubble { background: var(--tv3-bg2, #f4f6fa); color: var(--tv3-ink, #16233b); border-bottom-left-radius: 4px; }
.tv3-butler__thinking { font-size: 11.5px; color: var(--tv3-ink3, #8a94a6); font-style: italic; padding: 0 2px; }
.tv3-butler__toolline { font-size: 11.5px; color: var(--tv3-teal, #0e9488); }
.tv3-butler__caret { display: inline-block; width: 2px; height: 13px; background: var(--tv3-navy, #4f46e5); animation: tv3-blink .9s infinite; vertical-align: -2px; margin-left: 2px; }
@keyframes tv3-blink { 50% { opacity: 0; } }

.tv3-butler__card { border: 1px solid var(--tv3-line, #e2e7ef); border-radius: 10px; background: #fff; max-width: 96%; }
.tv3-butler__card--formula { padding: 10px 12px; cursor: grab; }
.tv3-butler__card--formula:active { cursor: grabbing; }
.tv3-butler__card--formula:hover { border-color: var(--tv3-gold, #0891b2); box-shadow: 0 4px 14px rgba(6, 182, 212, 0.18); }
.tv3-butler__formula { font-size: 17px; overflow-x: auto; padding: 2px 0; }
.tv3-butler__card-meta { display: flex; align-items: center; gap: 8px; margin-top: 8px; font-size: 11px; }
.tv3-butler__conf { color: var(--tv3-teal, #0e9488); font-weight: 600; }
.tv3-butler__conf.low { color: var(--tv3-rose, #dc2646); }
.tv3-butler__src { color: var(--tv3-ink3, #8a94a6); }
.tv3-butler__alts { font-size: 11px; color: var(--tv3-ink3, #8a94a6); margin-top: 6px; }
.tv3-butler__mini {
  border: 1px solid var(--tv3-line, #e2e7ef); background: #fff; border-radius: 6px; cursor: pointer;
  font-size: 11.5px; padding: 3px 10px; color: var(--tv3-ink2, #4a5568);
}
.tv3-butler__mini:hover { border-color: var(--tv3-navy, #4f46e5); color: var(--tv3-navy, #4f46e5); }
.tv3-butler__mini--go { background: var(--tv3-navy, #4f46e5); border-color: var(--tv3-navy, #4f46e5); color: #fff; }
.tv3-butler__mini--go:hover { color: #fff; opacity: .92; }
.tv3-butler__card--action { padding: 10px 12px; border-left: 3px solid var(--tv3-gold, #0891b2); }
.tv3-butler__card-title { font-size: 12.5px; font-weight: 700; }
.tv3-butler__card-body { font-size: 12px; color: var(--tv3-ink2, #4a5568); line-height: 1.6; margin-top: 4px; }
.tv3-butler__warn { color: var(--tv3-amber, #b45309); }
.tv3-butler__ok { color: var(--tv3-teal, #0e9488); font-weight: 600; }
.tv3-butler__dim { color: var(--tv3-ink3, #8a94a6); }
.tv3-butler__card--link { padding: 10px 12px; text-align: left; cursor: pointer; border-left: 3px solid var(--tv3-navy, #4f46e5); }
.tv3-butler__card--link:hover { background: #f7f9fd; }
.tv3-butler__cites { display: flex; flex-wrap: wrap; gap: 6px; font-size: 11px; }
.tv3-butler__cite { color: var(--tv3-navy, #4f46e5); background: #f0f4fb; border-radius: 6px; padding: 2px 8px; max-width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tv3-butler__thumbs { display: flex; gap: 6px; }
.tv3-butler__thumb { width: 52px; height: 52px; object-fit: cover; border-radius: 8px; border: 1px solid var(--tv3-line, #e2e7ef); }
.tv3-butler__asr { font-size: 12.5px; color: var(--tv3-ink2, #4a5568); background: #f7f9fd; border-radius: 10px; padding: 8px 12px; }

.tv3-butler__input { border-top: 1px solid var(--tv3-line, #e2e7ef); padding: 10px 12px 12px; display: flex; flex-direction: column; gap: 8px; }
.tv3-butler__voice-box { background: #f7f9fd; border: 1px solid var(--tv3-line, #e2e7ef); border-radius: 10px; padding: 10px; display: flex; flex-direction: column; gap: 8px; }
.tv3-butler__voice-hint { font-size: 11px; color: var(--tv3-ink3, #8a94a6); }
.tv3-butler__voice-input { border: 1px solid var(--tv3-line, #e2e7ef); border-radius: 8px; padding: 8px 10px; font-size: 13px; outline: none; }
.tv3-butler__voice-input:focus { border-color: var(--tv3-navy, #4f46e5); }
.tv3-butler__voice-ops { display: flex; gap: 8px; }
.tv3-butler__attach { display: flex; gap: 6px; align-items: center; }
/* KNR 上下文条（输入框上方一排 chips，点击展开明细） */
.tv3-butler__sctx { display: flex; flex-direction: column; gap: 6px; }
.tv3-butler__sctx-chips { display: flex; flex-wrap: wrap; gap: 6px; }
.tv3-butler__sctx-chips .tv3-butler__chip { font-size: 11px; padding: 3px 10px; background: #f0f4fb; border-color: #dce6f5; cursor: pointer; }
.tv3-butler__sctx-detail { font-size: 11.5px; color: var(--tv3-ink3, #8a94a6); background: #f7f9fd; border: 1px solid var(--tv3-line, #e2e7ef); border-radius: 8px; padding: 8px 10px; line-height: 1.7; }
/* 依据折叠（AI 产物卡底部） */
.tv3-butler__evid-toggle { border: none; background: none; cursor: pointer; font-size: 11px; color: var(--tv3-ink3, #8a94a6); padding: 2px 0; text-align: left; }
.tv3-butler__evid-toggle:hover { color: var(--tv3-navy, #4f46e5); }
.tv3-butler__evid { display: flex; flex-direction: column; gap: 4px; border-left: 2px solid var(--tv3-line, #e2e7ef); padding: 2px 0 2px 10px; }
.tv3-butler__evid-item { display: flex; align-items: center; gap: 6px; font-size: 11.5px; }
.tv3-butler__evid-item a { color: var(--tv3-navy, #4f46e5); text-decoration: none; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 240px; }
.tv3-butler__evid-item a:hover { text-decoration: underline; }
.tv3-butler__evid-type { flex: none; font-size: 10px; border-radius: 4px; padding: 1px 6px; background: #f0f4fb; color: var(--tv3-ink2, #4a5568); }
.tv3-butler__evid-type[data-type='textbook'], .tv3-butler__evid-type[data-type='curriculum'] { background: #ecfdf5; color: #047857; }
.tv3-butler__evid-type[data-type='class_data'], .tv3-butler__evid-type[data-type='deck'] { background: #f0f4fb; color: #4f46e5; }
.tv3-butler__evid-ref { color: var(--tv3-ink3, #8a94a6); font-size: 10.5px; flex: none; }
.tv3-butler__row { display: flex; }
.tv3-butler__ta {
  flex: 1; border: 1px solid var(--tv3-line, #e2e7ef); border-radius: 10px; padding: 9px 11px;
  font-size: 13px; font-family: inherit; resize: none; outline: none; background: #fff;
}
.tv3-butler__ta:focus { border-color: var(--tv3-navy, #4f46e5); }
.tv3-butler__ops { display: flex; align-items: center; gap: 8px; }
.tv3-butler__op {
  border: 1px solid var(--tv3-line, #e2e7ef); background: #fff; border-radius: 999px; cursor: pointer;
  font-size: 12px; padding: 4px 11px; color: var(--tv3-ink3, #8a94a6);
}
.tv3-butler__op.on { border-color: var(--tv3-navy, #4f46e5); color: var(--tv3-navy, #4f46e5); background: #f0f4fb; }
.tv3-butler__send {
  border: none; border-radius: 999px; cursor: pointer; font-size: 12.5px; padding: 6px 16px;
  background: linear-gradient(135deg, #4f46e5, #123a6d); color: #fff; font-weight: 600;
}
.tv3-butler__send:disabled { opacity: .5; cursor: not-allowed; }
.tv3-butler__spacer { flex: 1; }
</style>
