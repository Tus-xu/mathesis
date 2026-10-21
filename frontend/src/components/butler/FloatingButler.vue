<template>
  <div ref="rootRef" class="fb-root">
    <!-- ===== 浮层面板（学生/通用：管家对话 + 任务 + 通知） ===== -->
    <transition name="fb-pop">
      <section v-if="panelOpen" class="fb-panel" role="dialog" aria-label="AI 管家">
        <header class="fb-head">
          <div class="fb-orb" aria-hidden="true">✦</div>
          <div class="fb-head-txt">
            <b>AI 管家</b>
            <span>后台任务 · 站内通知</span>
          </div>
          <button class="fb-close" type="button" aria-label="关闭" @click="close">×</button>
        </header>

        <nav class="fb-tabs">
          <button type="button" :class="{ active: tab === 'butler' }" @click="tab = 'butler'">
            管家<span v-if="chatPending" class="fb-tab-ell">…</span>
          </button>
          <button type="button" :class="{ active: tab === 'tasks' }" @click="tab = 'tasks'">
            任务<span v-if="store.activeCount" class="fb-tab-badge">{{ store.activeCount }}</span>
          </button>
          <button type="button" :class="{ active: tab === 'notify' }" @click="tab = 'notify'">
            通知<span v-if="store.unread" class="fb-tab-badge">{{ badgeText }}</span>
          </button>
          <button type="button" :class="{ active: tab === 'study' }" @click="openStudy">
            学情
          </button>
        </nav>

        <!-- a) 管家对话区 -->
        <div v-show="tab === 'butler'" class="fb-pane">
          <div ref="chatRef" class="fb-chat">
            <div v-if="!messages.length" class="fb-empty">
              你好，我是 AI 管家。可以直接告诉我你想做什么（出题、上课、解题、看学情），
              <button type="button" class="fb-link" @click="openQuickForm">或直接创建出题任务 →</button>
            </div>
            <template v-for="(m, i) in messages" :key="i">
              <div class="fb-msg" :class="m.role">{{ m.text }}</div>
              <!-- R2：planner 降级透传（envelope.degraded=true）→ 可见降级提示条 -->
              <div v-if="m.degraded" class="fb-degraded">⚠️ AI 分析暂不可用，以下为降级结果</div>
              <!-- R2：数据可视化卡（env.blocks type=card 且为学情卡类型时渲染；
                   与对话流同一组卡片组件，同一份后端结构化数据） -->
              <div v-for="(c, ci) in (m.cards || [])" :key="i + '-c' + ci" class="fb-card-wrap">
                <RetentionCurveCard v-if="c.type === 'fsrs_retention'" :card="c" />
                <MasteryBktCard v-else-if="c.type === 'mastery_bkt'" :card="c" />
              </div>
              <div v-if="m.actions && m.actions.length" class="fb-msg-actions">
                <button
                  v-for="(a, ai) in m.actions" :key="ai" type="button" class="fb-act-btn"
                  @click="onAction(a)"
                >{{ actionLabel(a) }}</button>
              </div>
            </template>
            <div v-if="chatPending" class="fb-msg ai">思考中…</div>
          </div>

          <!-- 快捷下单表单（管家用不了时的兜底 + 「出题」芯片直达） -->
          <div v-if="quickFormOpen" class="fb-order">
            <div class="fb-order-head">
              <b>快捷出题</b>
              <button type="button" aria-label="收起" @click="quickFormOpen = false">×</button>
            </div>
            <div v-if="kpMode === 'select'" class="fb-field">
              <label>知识点</label>
              <select v-model="orderKp">
                <option disabled value="">请选择知识点</option>
                <option v-for="k in kpList" :key="k.code" :value="k.code">{{ k.name }}（{{ k.code }}）</option>
              </select>
            </div>
            <div v-else-if="kpMode === 'input'" class="fb-field">
              <label>知识点编码 kp_code</label>
              <input v-model="orderKp" type="text" placeholder="如 HS-01" />
              <p class="fb-hint">{{ kpError }}</p>
            </div>
            <div v-else class="fb-field"><p class="fb-hint">知识点列表加载中…</p></div>
            <div class="fb-field">
              <label>题量</label>
              <div class="fb-count">
                <button
                  v-for="c in COUNT_OPTIONS" :key="c" type="button"
                  :class="{ active: orderCount === c }" @click="orderCount = c"
                >{{ c }} 题</button>
              </div>
            </div>
            <button class="fb-order-btn" type="button" :disabled="!canOrder || orderSubmitting" @click="submitOrder">
              {{ orderSubmitting ? '提交中…' : '创建出题任务' }}
            </button>
          </div>

          <!-- 输入区 + 快捷芯片（管家可用时） -->
          <div v-if="butlerAvailable" class="fb-inputarea">
            <div class="fb-chips">
              <button v-for="c in CHIPS" :key="c.key" type="button" @click="applyChip(c)">{{ c.label }}</button>
            </div>
            <div class="fb-inputrow">
              <input
                ref="inputRef" v-model="draft" type="text"
                placeholder="告诉管家你想做什么…"
                @keydown.enter.prevent="sendChat"
              />
              <button type="button" :disabled="!draft.trim() || chatPending" @click="sendChat">发送</button>
            </div>
          </div>
        </div>

        <!-- b) 任务列表区 -->
        <div v-show="tab === 'tasks'" class="fb-pane fb-pane-list">
          <div class="fb-pane-head">
            <span>后台任务 {{ store.items.length ? `(${store.items.length})` : '' }}</span>
            <button type="button" class="fb-link" @click="store.refreshTasks()">刷新</button>
          </div>
          <div class="fb-scroll">
            <div v-if="!store.items.length" class="fb-empty">暂无后台任务，可在「管家」页发起出题等任务。</div>
            <div
              v-for="t in store.items" :key="t.task_id"
              class="fb-task" :class="{ highlight: String(t.task_id) === highlightId }"
            >
              <div class="fb-task-top">
                <span class="fb-task-kind">{{ kindLabel(t) }}</span>
                <span class="fb-task-status" :class="statusMeta(t).cls">{{ statusMeta(t).label }}</span>
              </div>
              <div v-if="t.status === 'queued' || t.status === 'running'" class="fb-task-prog">
                <div class="fb-bar"><div class="fb-bar-fill" :class="{ indet: barWidth(t) === '' }" :style="{ width: barWidth(t) }"></div></div>
                <span class="fb-stage">
                  {{ t.status === 'queued' ? '排队等待中' : (t.stage || '处理中') }}<template v-if="t.progress != null"> · {{ Math.round(t.progress) }}%</template>
                </span>
              </div>
              <div v-if="t.status === 'failed' && t.error" class="fb-task-err">{{ t.error }}</div>
              <div class="fb-task-foot">
                <span class="fb-task-time">{{ fmtTime(t.created_at) }}</span>
                <div class="fb-task-ops">
                  <button v-if="t.status === 'succeeded' && resultJumpOf(t)" type="button" class="fb-op" @click="onResultJump(t)">查看</button>
                  <button
                    v-if="t.status === 'failed'" type="button" class="fb-op" :disabled="!!opPending[t.task_id]"
                    @click="onRetry(t)"
                  >重试</button>
                  <button
                    v-if="t.status === 'queued' || t.status === 'running'" type="button" class="fb-op danger" :disabled="!!opPending[t.task_id]"
                    @click="onCancel(t)"
                  >取消</button>
                </div>
              </div>
            </div>
          </div>
        </div>

        <!-- c) 通知列表区 -->
        <!-- d) 学情概览（V2 文档 §6：原右栏信息归悬浮球，点开看一眼 + AI 提醒） -->
        <div v-show="tab === 'study'" class="fb-pane fb-pane-list">
          <div class="fb-pane-head"><span>学习概览</span></div>
          <div class="fb-scroll">
            <div v-if="studyLoading" class="fb-empty">加载中…</div>
            <div v-else-if="!studyPanel" class="fb-empty">学情数据加载失败，稍后再试</div>
            <template v-else>
              <div v-if="studyPanel.golden_window" style="background:linear-gradient(135deg,#fef3c7,#fde68a);padding:10px 12px;border-radius:10px;margin-bottom:10px;">
                <div style="font-size:11px;font-weight:800;color:var(--warn-deep);">⏰ 今晚 {{ studyPanel.golden_window.start }} - {{ studyPanel.golden_window.end }}</div>
                <div style="font-size:11.5px;color:var(--ink);margin-top:2px;">{{ studyPanel.golden_window.label || '黄金记忆窗口' }}</div>
              </div>
              <div v-if="(studyPanel.today_actions || []).length" style="margin-bottom:10px;">
                <div style="font-size:11px;font-weight:800;color:var(--ink3);margin-bottom:6px;">📋 今日行动</div>
                <div v-for="a in studyPanel.today_actions" :key="a.key"
                     style="display:flex;align-items:center;gap:8px;padding:8px 10px;border:1px solid var(--line);border-radius:10px;margin-bottom:6px;cursor:pointer;font-size:12.5px;"
                     @click="$router.push(a.route || actionRoute(a.key))">
                  <span>{{ actionEmoji(a.key) }}</span><span style="flex:1;">{{ a.title }}</span>
                  <span v-if="a.count" style="font-weight:800;color:var(--primary);">{{ a.count }}</span>
                </div>
              </div>
              <div style="display:flex;gap:6px;margin-bottom:10px;">
                <div style="flex:1;padding:8px;border:1px solid var(--line);border-radius:10px;text-align:center;">
                  <div style="font-size:15px;font-weight:800;">{{ studyPanel.gaokao_countdown?.days ?? '--' }}</div>
                  <div style="font-size:10.5px;color:var(--ink3);">距高考(天)</div>
                </div>
                <div style="flex:1;padding:8px;border:1px solid var(--line);border-radius:10px;text-align:center;">
                  <div style="font-size:15px;font-weight:800;color:var(--ok-deep);">{{ studyPanel.week_brief?.streak_days ?? '--' }}</div>
                  <div style="font-size:10.5px;color:var(--ink3);">连击(天)</div>
                </div>
                <div style="flex:1;padding:8px;border:1px solid var(--line);border-radius:10px;text-align:center;">
                  <div style="font-size:15px;font-weight:800;">{{ studyPanel.week_brief?.score_delta ?? '--' }}</div>
                  <div style="font-size:10.5px;color:var(--ink3);">本周提分</div>
                </div>
              </div>
              <div v-if="studyPanel.encouragement" style="font-size:12px;color:var(--ink2);line-height:1.6;padding:2px 4px;">🌱 {{ studyPanel.encouragement }}</div>
            </template>
          </div>
        </div>

        <div v-show="tab === 'notify'" class="fb-pane fb-pane-list">
          <div class="fb-pane-head">
            <span>站内通知<template v-if="store.unread"> · 未读 {{ store.unread }}</template></span>
            <button type="button" class="fb-link" :disabled="!store.unread" @click="store.readAll()">全部已读</button>
          </div>
          <div class="fb-scroll">
            <div v-if="!store.notifications.length" class="fb-empty">暂无通知。</div>
            <div
              v-for="n in store.notifications" :key="n.id"
              class="fb-ntf" :class="{ unread: !n.read_at }" @click="onNotifClick(n)"
            >
              <div class="fb-ntf-top">
                <span v-if="!n.read_at" class="fb-ntf-dot" aria-hidden="true"></span>
                <span class="fb-ntf-title">{{ n.title || '通知' }}</span>
                <span class="fb-ntf-time">{{ fmtTime(n.created_at) }}</span>
              </div>
              <div v-if="n.body" class="fb-ntf-body">{{ n.body }}</div>
            </div>
          </div>
        </div>
      </section>
    </transition>

    <!-- ===== 悬浮球 ===== -->
    <button class="fb-ball" type="button" aria-label="AI 管家" title="AI 管家" @click="onBallClick">
      <span class="fb-ball-ic" aria-hidden="true">✦</span>
      <span v-if="store.unread > 0" class="fb-badge">{{ badgeText }}</span>
    </button>
  </div>
</template>

<script setup>
/**
 * AI 管家悬浮球 + 后台任务中心 + 站内通知
 * - 悬浮球（56px，右下角，未读角标）→ 点开面板：管家对话 / 任务列表 / 通知列表 三个区
 * - 管家对话调 POST /api/butler/chat；404/50301 降级为纯任务模式（显示快捷下单表单）
 * - 快捷芯片：出题（直达快捷下单表单）/ 上课 / 解题 / 问学情（预填话术）
 * - delegateOpen=true（教师端）：点击悬浮球仅 emit('ball-click')，由父组件复用 ButlerPanel 展开逻辑
 * - 轮询由 useTasksStore 提供（5s，仅登录态；visibilitychange 立即刷）
 */
import { computed, defineAsyncComponent, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '@/api/client'
import { butlerChatApi } from '@/api/tasks'
import { contextPrefix } from '@/composables/usePageContext'
import { newIdempotencyKey } from '@/api/idempotency'
import { useAuthStore } from '@/stores/auth'
import { useToastStore } from '@/stores/toast'
import { useTasksStore } from '@/stores/tasks'

// R2：数据卡异步引入——悬浮球全局挂载，静态引入会把 echarts 打进首屏 index 包；
// 异步后 echarts 只在真正渲染学情卡时按需加载（对话页 MessageBubble 走自身路由分包）
const RetentionCurveCard = defineAsyncComponent(() => import('@/components/chat/RetentionCurveCard.vue'))
const MasteryBktCard = defineAsyncComponent(() => import('@/components/chat/MasteryBktCard.vue'))

const props = defineProps({
  /** 教师端：悬浮球点击不上浮本面板，而是交由父组件打开 ButlerPanel */
  delegateOpen: { type: Boolean, default: false },
})
const emit = defineEmits(['ball-click'])

const router = useRouter()
const auth = useAuthStore()
const toast = useToastStore()
const store = useTasksStore()

/* ===== 面板/球 显隐 ===== */
const rootRef = ref(null)
const panelOpen = ref(false)
const tab = ref('butler') // butler | tasks | notify | study
// ===== 学情 tab（V2 文档 §6：原右栏信息收编悬浮球，点开即看 + 主动提醒宿主） =====
const studyPanel = ref(null)
const studyLoading = ref(false)
async function openStudy() {
  tab.value = 'study'
  if (studyPanel.value || studyLoading.value) return
  studyLoading.value = true
  try {
    studyPanel.value = await api.get('/student/growth/panel')
  } catch { studyPanel.value = null } finally { studyLoading.value = false }
}
function actionEmoji(key) {
  return ({ review_errors: '📕', variant: '🔁', challenge: '🎯', homework: '📋' }[key] || '•')
}
function actionRoute(key) {
  return ({ review_errors: '/errors', variant: '/practice', challenge: '/practice', homework: '/tasks' }[key] || '/overview')
}

const badgeText = computed(() => (store.unread > 99 ? '99+' : String(store.unread)))

function openPanel() {
  panelOpen.value = true
  store.refreshAll()
}
function close() {
  panelOpen.value = false
}
function onBallClick() {
  if (props.delegateOpen) { emit('ball-click'); return }
  panelOpen.value ? close() : openPanel()
}

/* ===== 管家对话 ===== */
const CHIPS = [
  { key: 'practice', label: '出题', template: '' },
  { key: 'classroom', label: '上课', template: '帮我上一节「函数概念与性质」的 AI 互动课' },
  { key: 'socratic', label: '解题', template: '帮我解这道题：（把题目贴在这里）' },
  { key: 'mastery', label: '问学情', template: '我的学情怎么样？最薄弱的知识点是什么？' },
]
const draft = ref('')
const messages = ref([]) // {role:'user'|'ai', text, actions?}
const chatPending = ref(false)
const butlerAvailable = ref(true)
const chatRef = ref(null)
const inputRef = ref(null)

function scrollChat() {
  nextTick(() => {
    if (chatRef.value) chatRef.value.scrollTop = chatRef.value.scrollHeight
  })
}

function applyChip(c) {
  // 「出题」芯片直达快捷下单表单；其余芯片预填话术模板
  if (c.key === 'practice') { openQuickForm(); return }
  draft.value = c.template
  nextTick(() => inputRef.value?.focus())
}

function makeRequestId() {
  try { return newIdempotencyKey() } catch { return `butler_${Date.now()}_${Math.random().toString(36).slice(2, 10)}` }
}

async function sendChat() {
  const msg = draft.value.trim()
  if (!msg || chatPending.value || !butlerAvailable.value) return
  messages.value.push({ role: 'user', text: msg })
  const outgoing = contextPrefix() + msg  // S16：带页面上下文（在哪页问，AI 知道那页在看什么）
  draft.value = ''
  chatPending.value = true
  scrollChat()
  try {
    const data = await butlerChatApi.chat(outgoing, makeRequestId())
    // 契约：data.envelope = { replies:[{kind:"text",text}], actions:[{type,label,task_id?,jump?}], run_id }
    //   R2 追加（additive）：envelope.blocks:[{type:'card',data:<同构卡>}]、envelope.degraded:bool
    const env = data?.envelope || data || {}
    const replies = (Array.isArray(env.replies) ? env.replies : [])
      .map((r) => (typeof r === 'string' ? r : r?.text || ''))
      .filter(Boolean)
    // R2：blocks 里的学情数据卡（fsrs_retention / mastery_bkt）→ 与对话流同构渲染；
    // 其它未知 block 类型忽略（面板窄，不硬塞），绝不伪造数据卡
    const cards = (Array.isArray(env.blocks) ? env.blocks : [])
      .filter((b) => b && b.type === 'card' && b.data && typeof b.data === 'object')
      .map((b) => b.data)
      .filter((c) => c.type === 'fsrs_retention' || c.type === 'mastery_bkt')
    const text = replies.join('\n')
      || (cards.length ? '这是从你的学习记录里查到的：' : '（管家没有返回可展示的内容）')
    messages.value.push({
      role: 'ai',
      text,
      actions: Array.isArray(env.actions) ? env.actions : [],
      cards,
      degraded: env.degraded === true,
    })
  } catch (e) {
    degradeOnButlerError(e)
  } finally {
    chatPending.value = false
    scrollChat()
  }
}

/** 404/50301 → 永久降级纯任务模式；其他错误 → 提示 + 显示兜底表单（保留对话） */
function degradeOnButlerError(e) {
  const code = e?.code
  messages.value.push({
    role: 'ai',
    text: '管家暂不可用，可直接创建任务：',
    actions: [],
  })
  if (code === 404 || code === 50301) butlerAvailable.value = false
  openQuickForm()
  scrollChat()
}

function actionLabel(a) {
  if (a?.label) return a.label
  if (a?.type === 'task_created') return '查看进度'
  return '前往'
}

function onAction(a) {
  if (!a) return
  if (a.type === 'task_created' && a.task_id) { highlightTask(a.task_id); return }
  if (a.type === 'jump' && goJump(a.jump)) close()
}

/** jump 兼容字符串 / location 对象 */
function goJump(jump) {
  let target = null
  if (typeof jump === 'string' && jump.trim()) target = jump.trim()
  else if (jump && typeof jump === 'object' && (jump.path || jump.name)) target = jump
  if (!target) return false
  router.push(target).catch(() => {})
  return true
}

/* ===== 快捷下单（practice.generate） ===== */
const COUNT_OPTIONS = [5, 10, 20]
const quickFormOpen = ref(false)
const kpList = ref([]) // [{code,name}]
const kpMode = ref('loading') // loading | select | input
const kpError = ref('')
const orderKp = ref('')
const orderCount = ref(10)
const orderSubmitting = ref(false)
let orderKey = '' // 幂等键：同一表单会话内复用，成功后更换

/** 常用 8 个高中数学知识点：label 用真实 kp_name，value 必须是接口返回的真实 kp_code */
const COMMON_KP = [
  { label: '集合与常用逻辑用语', keys: ['集合', '常用逻辑'] },
  { label: '函数概念与性质', keys: ['函数的概念', '函数概念', '函数的性质', '函数基本性质', '函数'] },
  { label: '三角函数', keys: ['三角函数', '三角'] },
  { label: '平面向量', keys: ['向量'] },
  { label: '复数', keys: ['复数'] },
  { label: '数列', keys: ['数列'] },
  { label: '立体几何', keys: ['立体几何', '空间几何', '立体'] },
  { label: '直线与圆', keys: ['直线与圆', '直线的方程', '圆的方程', '直线', '圆'] },
]

function openQuickForm() {
  quickFormOpen.value = true
  orderKey = '' // 新的一次下单动作 → 重置幂等键
  ensureKpList()
}

function extractNodes(data) {
  if (!data || typeof data !== 'object') return []
  const out = []
  const push = (n) => {
    const code = n?.kp_code || n?.code
    const name = n?.kp_name || n?.name || n?.title || code
    if (code && name && !out.some((x) => x.code === code)) out.push({ code, name })
  }
  if (Array.isArray(data.nodes)) data.nodes.forEach(push)
  if (Array.isArray(data.chapters)) data.chapters.forEach((ch) => Array.isArray(ch?.nodes) && ch.nodes.forEach(push))
  if (Array.isArray(data.items)) data.items.forEach(push)
  return out
}

/** 常用 8 点按固定顺序置顶（label 用真实 kp_name），其余知识点追加在后 */
function buildOptions(nodes) {
  const used = new Set()
  const picked = []
  for (const c of COMMON_KP) {
    const hit = nodes.find((n) => !used.has(n.code) && c.keys.some((k) => n.name.includes(k)))
    if (hit) { used.add(hit.code); picked.push(hit) }
  }
  const rest = nodes.filter((n) => !used.has(n.code))
  return [...picked, ...rest]
}

async function ensureKpList() {
  if (kpMode.value === 'select' || kpMode.value === 'input') return
  kpMode.value = 'loading'
  kpError.value = ''
  let nodes = []
  try { nodes = extractNodes(await api.get('/student/knowledge-graph')) } catch { /* 换备用端点 */ }
  if (!nodes.length) {
    try { nodes = extractNodes(await api.get('/student/knowledge-graph/tree')) } catch { /* 降级输入框 */ }
  }
  if (nodes.length) {
    kpList.value = buildOptions(nodes)
    orderKp.value = kpList.value[0]?.code || ''
    kpMode.value = 'select'
  } else {
    kpMode.value = 'input'
    kpError.value = '知识点接口暂不可用：请手动填写真实 kp_code（可在「知识图谱」页查看编码，如 HS-01）'
  }
}

const canOrder = computed(() => !!orderKp.value.trim() && COUNT_OPTIONS.includes(orderCount.value))

async function submitOrder() {
  const kp = orderKp.value.trim()
  if (!kp || orderSubmitting.value) return
  if (!orderKey) {
    try { orderKey = newIdempotencyKey() } catch { orderKey = `order_${Date.now()}_${Math.random().toString(36).slice(2, 10)}` }
  }
  orderSubmitting.value = true
  try {
    const data = await store.createTask('practice.generate', { kp_code: kp, count: orderCount.value }, orderKey)
    orderKey = '' // 成功后换新键
    toast.success('出题任务已创建，正在后台生成')
    quickFormOpen.value = false
    if (data?.task_id) highlightTask(data.task_id)
    else { tab.value = 'tasks'; store.refreshTasks() }
  } catch (e) {
    toast.error(e?.message || '创建任务失败，请稍后重试') // 失败保留 orderKey，重试复用同一幂等键
  } finally {
    orderSubmitting.value = false
  }
}

/* ===== 任务列表 ===== */
const KIND_FALLBACK = {
  'practice.generate': '练习出题',
  'classroom.session': 'AI 课堂',
  'socratic.autosolve': '解题辅导',
}
function kindLabel(t) { return t?.kind_label || KIND_FALLBACK[t?.kind] || t?.kind || '任务' }

function statusMeta(t) {
  switch (t?.status) {
    case 'queued': return { label: '排队中', cls: 'is-queued' }
    case 'running': return { label: t?.stage || '进行中', cls: 'is-running' }
    case 'succeeded': return { label: '已完成', cls: 'is-ok' }
    case 'failed': return { label: '失败', cls: 'is-failed' }
    case 'cancelled': return { label: '已取消', cls: 'is-cancelled' }
    default: return { label: t?.status || '未知', cls: 'is-queued' }
  }
}

function barWidth(t) {
  if (t?.status === 'queued') return '0%'
  const p = Number(t?.progress)
  if (Number.isFinite(p)) return `${Math.min(Math.max(p, 0), 100)}%`
  return '' // 未知进度 → 不确定态动画
}

function resultJumpOf(t) {
  const r = t?.result
  if (!r || typeof r !== 'object') return null
  return r.jump || r.path || null
}
function onResultJump(t) {
  if (goJump(resultJumpOf(t))) close()
}

const highlightId = ref('')
const opPending = ref({})
let highlightTimer = null

function highlightTask(id) {
  highlightId.value = String(id)
  tab.value = 'tasks'
  clearTimeout(highlightTimer)
  highlightTimer = setTimeout(() => { highlightId.value = '' }, 4000)
  nextTick(() => {
    document.querySelector('.fb-task.highlight')?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  })
}

async function onRetry(t) {
  if (opPending.value[t.task_id]) return
  opPending.value = { ...opPending.value, [t.task_id]: true }
  try {
    await store.retry(t.task_id)
    toast.success('已重新排队')
  } catch (e) {
    toast.error(e?.message || '重试失败')
  } finally {
    opPending.value = { ...opPending.value, [t.task_id]: false }
  }
}

async function onCancel(t) {
  if (opPending.value[t.task_id]) return
  opPending.value = { ...opPending.value, [t.task_id]: true }
  try {
    await store.cancel(t.task_id)
    toast.info('任务已取消')
  } catch (e) {
    toast.error(e?.message || '取消失败')
  } finally {
    opPending.value = { ...opPending.value, [t.task_id]: false }
  }
}

/* ===== 通知列表 ===== */
/** payload 兼容：{jump} / {jumps: string} / {jumps: [...]} */
function firstJump(payload) {
  if (!payload || typeof payload !== 'object') return null
  if (payload.jump) return payload.jump
  const j = payload.jumps
  if (typeof j === 'string') return j
  if (Array.isArray(j) && j.length) return typeof j[0] === 'string' ? j[0] : (j[0]?.jump || j[0]?.path || null)
  if (j && typeof j === 'object') return j.jump || j.path || null
  return null
}

function onNotifClick(n) {
  if (!n) return
  if (!n.read_at) store.markRead(n.id)
  const jump = firstJump(n.payload)
  if (jump && goJump(jump)) close()
}

/* ===== 时间显示 ===== */
function fmtTime(s) {
  if (!s) return ''
  const d = new Date(s)
  if (Number.isNaN(d.getTime())) return String(s)
  const now = new Date()
  const hm = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
  if (d.toDateString() === now.toDateString()) return hm
  return `${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')} ${hm}`
}

/* ===== 全局事件与轮询生命周期 ===== */
function onKeydown(e) {
  if (e.key === 'Escape' && panelOpen.value) close()
}
function onDocMousedown(e) {
  if (!panelOpen.value) return
  if (rootRef.value && !rootRef.value.contains(e.target)) close()
}

watch(() => auth.isLoggedIn, (v) => {
  if (v) store.startPolling()
  else store.reset() // 登出：停止轮询并清空任务/通知
})

onMounted(() => {
  if (auth.isLoggedIn) store.startPolling()
  document.addEventListener('keydown', onKeydown)
  document.addEventListener('mousedown', onDocMousedown)
})

onUnmounted(() => {
  document.removeEventListener('keydown', onKeydown)
  document.removeEventListener('mousedown', onDocMousedown)
  clearTimeout(highlightTimer)
  // B0 修复轮询泄漏：组件卸载（如切换教师端）后必须停掉模块级 5s 轮询，
  // 否则 /api/tasks、/api/notifications 会在教师页面持续 404（审计 DEF-10）
  store.stopPolling()
})
</script>

<style scoped>
.fb-root {
  position: fixed;
  right: 24px;
  bottom: 24px;
  z-index: 1300;
  font-family: var(--font);
}

/* ===== 悬浮球 ===== */
.fb-ball {
  position: relative;
  width: 56px;
  height: 56px;
  border: none;
  border-radius: 50%;
  background: var(--brand);
  color: #fff;
  font-size: 22px;
  line-height: 1;
  cursor: pointer;
  box-shadow: 0 6px 18px rgba(245, 158, 11, .38), 0 2px 6px rgba(15, 23, 42, .12);
  transition: var(--transition);
  display: flex;
  align-items: center;
  justify-content: center;
}
.fb-ball:hover { background: var(--brand2); transform: translateY(-2px); }
.fb-ball:active { transform: translateY(0) scale(.96); }
.fb-badge {
  position: absolute;
  top: -4px;
  right: -4px;
  min-width: 20px;
  height: 20px;
  padding: 0 5px;
  border-radius: 999px;
  border: 2px solid #fff;
  background: var(--err);
  color: #fff;
  font-family: var(--font-num);
  font-size: 11px;
  font-weight: 800;
  display: flex;
  align-items: center;
  justify-content: center;
  box-sizing: content-box;
}

/* ===== 面板 ===== */
.fb-panel {
  position: absolute;
  right: 0;
  bottom: 68px;
  width: 384px;
  max-width: calc(100vw - 32px);
  height: 420px;
  max-height: calc(100vh - 120px);
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background: var(--card);
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  box-shadow: 0 18px 48px rgba(15, 23, 42, .16);
}
.fb-pop-enter-active, .fb-pop-leave-active { transition: opacity .18s ease, transform .18s ease; }
.fb-pop-enter-from, .fb-pop-leave-to { opacity: 0; transform: translateY(10px) scale(.97); }

.fb-head {
  flex: none;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 14px;
  background: var(--brand-faint);
  border-bottom: 1px solid var(--line);
}
.fb-orb {
  width: 32px; height: 32px;
  border-radius: 50%;
  background: var(--brand);
  color: #fff;
  font-size: 15px;
  display: flex; align-items: center; justify-content: center;
  flex: none;
}
.fb-head-txt { display: flex; flex-direction: column; gap: 1px; min-width: 0; flex: 1; }
.fb-head-txt b { font-size: 14px; color: var(--ink); }
.fb-head-txt span { font-size: 11px; color: var(--ink3); }
.fb-close {
  flex: none;
  width: 26px; height: 26px;
  border: none; border-radius: 8px;
  background: transparent;
  color: var(--ink3);
  font-size: 16px;
  cursor: pointer;
}
.fb-close:hover { background: var(--bg2); color: var(--ink); }

.fb-tabs {
  flex: none;
  display: flex;
  border-bottom: 1px solid var(--line);
  background: var(--card);
}
.fb-tabs button {
  flex: 1;
  position: relative;
  padding: 9px 0;
  border: none;
  background: transparent;
  color: var(--ink2);
  font: inherit;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
}
.fb-tabs button + button { border-left: 1px solid var(--line2); }
.fb-tabs button.active { color: var(--brand-deep); }
.fb-tabs button.active::after {
  content: '';
  position: absolute;
  left: 25%; right: 25%; bottom: -1px;
  height: 2px;
  border-radius: 2px;
  background: var(--brand);
}
.fb-tab-badge {
  margin-left: 5px;
  padding: 0 6px;
  border-radius: 999px;
  background: var(--err);
  color: #fff;
  font-size: 10px;
  font-weight: 800;
  font-family: var(--font-num);
}
.fb-tab-ell { margin-left: 2px; color: var(--brand-deep); }

.fb-pane { flex: 1; display: flex; flex-direction: column; min-height: 0; }
.fb-pane-list { padding: 0; }
.fb-scroll { flex: 1; overflow-y: auto; padding: 10px 12px 12px; min-height: 0; }
.fb-pane-head {
  flex: none;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 12px;
  border-bottom: 1px solid var(--line2);
  font-size: 12px;
  color: var(--ink3);
}

.fb-empty {
  margin: 6px 2px;
  padding: 12px;
  border-radius: var(--radius);
  background: var(--bg2);
  color: var(--ink2);
  font-size: 12.5px;
  line-height: 1.7;
}
.fb-link {
  border: none;
  background: transparent;
  padding: 0;
  color: var(--brand-deep);
  font: inherit;
  font-size: 12px;
  font-weight: 700;
  cursor: pointer;
}
.fb-link:hover { text-decoration: underline; }
.fb-link:disabled { color: var(--ink3); cursor: default; text-decoration: none; }

/* ===== 管家对话 ===== */
.fb-chat { flex: 1; overflow-y: auto; padding: 12px; min-height: 0; display: flex; flex-direction: column; gap: 8px; }
.fb-msg {
  max-width: 86%;
  padding: 8px 11px;
  border-radius: 12px;
  font-size: 12.5px;
  line-height: 1.65;
  white-space: pre-wrap;
  word-break: break-word;
}
.fb-msg.user { align-self: flex-end; background: var(--brand-soft); color: var(--brand-deep); border-bottom-right-radius: 4px; }
.fb-msg.ai { align-self: flex-start; background: var(--bg2); color: var(--ink); border-bottom-left-radius: 4px; }
/* R2：降级提示条 + 数据卡容器（卡自带宽高，面板 384px 内可完整展示） */
.fb-degraded {
  align-self: flex-start;
  max-width: 96%;
  padding: 5px 9px;
  border: 1px solid var(--warn-border);
  border-radius: 8px;
  background: var(--warn-bg);
  color: var(--warn-deep);
  font-size: 11.5px;
  line-height: 1.5;
}
.fb-card-wrap { align-self: stretch; }
.fb-msg-actions { align-self: flex-start; display: flex; flex-wrap: wrap; gap: 6px; margin-top: -2px; }
.fb-act-btn {
  padding: 5px 11px;
  border: 1px solid var(--warn-border);
  border-radius: 999px;
  background: var(--warn-bg);
  color: var(--warn-deep);
  font: inherit;
  font-size: 12px;
  font-weight: 700;
  cursor: pointer;
  transition: var(--transition);
}
.fb-act-btn:hover { background: var(--brand-soft); border-color: var(--brand); }

/* 快捷下单表单 */
.fb-order {
  flex: none;
  margin: 0 12px 8px;
  padding: 10px 12px;
  border: 1px solid var(--line);
  border-radius: var(--radius);
  background: var(--bg2);
}
.fb-order-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; }
.fb-order-head b { font-size: 12.5px; color: var(--ink); }
.fb-order-head button {
  width: 22px; height: 22px;
  border: none; border-radius: 6px;
  background: transparent; color: var(--ink3);
  font-size: 14px; cursor: pointer;
}
.fb-order-head button:hover { background: var(--line2); color: var(--ink); }
.fb-field { margin-bottom: 8px; }
.fb-field label { display: block; margin-bottom: 4px; font-size: 11px; font-weight: 700; color: var(--ink2); }
.fb-field select,
.fb-field input[type="text"] {
  width: 100%;
  padding: 7px 9px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--card);
  color: var(--ink);
  font: inherit;
  font-size: 12.5px;
  box-sizing: border-box;
  outline: none;
}
.fb-field select:focus,
.fb-field input[type="text"]:focus { border-color: var(--brand); }
.fb-hint { margin: 4px 0 0; font-size: 11px; color: var(--ink3); line-height: 1.6; }
.fb-count { display: flex; gap: 6px; }
.fb-count button {
  flex: 1;
  padding: 6px 0;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--card);
  color: var(--ink2);
  font: inherit;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  transition: var(--transition);
}
.fb-count button.active { border-color: var(--brand); background: var(--brand-soft); color: var(--brand-deep); }
.fb-order-btn {
  width: 100%;
  padding: 8px 0;
  border: none;
  border-radius: 8px;
  background: var(--brand);
  color: #fff;
  font: inherit;
  font-size: 12.5px;
  font-weight: 700;
  cursor: pointer;
  transition: var(--transition);
}
.fb-order-btn:hover:not(:disabled) { background: var(--brand2); }
.fb-order-btn:disabled { opacity: .5; cursor: not-allowed; }

/* 输入区 */
.fb-inputarea { flex: none; border-top: 1px solid var(--line); padding: 8px 12px 10px; }
.fb-chips { display: flex; gap: 6px; margin-bottom: 8px; flex-wrap: wrap; }
.fb-chips button {
  padding: 4px 10px;
  border: 1px solid var(--line);
  border-radius: 999px;
  background: var(--card);
  color: var(--ink2);
  font: inherit;
  font-size: 11.5px;
  cursor: pointer;
  transition: var(--transition);
}
.fb-chips button:hover { border-color: var(--brand); color: var(--brand-deep); background: var(--brand-faint); }
.fb-inputrow { display: flex; gap: 8px; }
.fb-inputrow input {
  flex: 1;
  min-width: 0;
  padding: 8px 10px;
  border: 1px solid var(--line);
  border-radius: 10px;
  background: var(--card);
  color: var(--ink);
  font: inherit;
  font-size: 12.5px;
  outline: none;
}
.fb-inputrow input:focus { border-color: var(--brand); }
.fb-inputrow button {
  flex: none;
  padding: 0 14px;
  border: none;
  border-radius: 10px;
  background: var(--brand);
  color: #fff;
  font: inherit;
  font-size: 12.5px;
  font-weight: 700;
  cursor: pointer;
  transition: var(--transition);
}
.fb-inputrow button:hover:not(:disabled) { background: var(--brand2); }
.fb-inputrow button:disabled { opacity: .45; cursor: not-allowed; }

/* ===== 任务列表 ===== */
.fb-task {
  padding: 9px 11px;
  border: 1px solid var(--line);
  border-radius: var(--radius);
  background: var(--card);
  margin-bottom: 8px;
  transition: var(--transition);
}
.fb-task.highlight { border-color: var(--brand); box-shadow: 0 0 0 2px var(--brand-soft); }
.fb-task-top { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.fb-task-kind { font-size: 12.5px; font-weight: 700; color: var(--ink); }
.fb-task-status { flex: none; font-size: 11px; font-weight: 700; padding: 2px 8px; border-radius: 999px; }
.fb-task-status.is-queued { background: var(--bg2); color: var(--ink2); }
.fb-task-status.is-running { background: var(--brand-soft); color: var(--brand-deep); }
.fb-task-status.is-ok { background: var(--ok-bg); color: var(--ok-deep); }
.fb-task-status.is-failed { background: var(--err-bg); color: var(--err-deep); }
.fb-task-status.is-cancelled { background: var(--bg2); color: var(--ink3); }
.fb-task-prog { margin-top: 7px; }
.fb-bar { height: 6px; border-radius: 999px; background: var(--line2); overflow: hidden; }
.fb-bar-fill { height: 100%; border-radius: 999px; background: var(--brand); transition: width .4s ease; }
.fb-bar-fill.indet { width: 32%; animation: fb-indet 1.2s ease-in-out infinite alternate; }
@keyframes fb-indet { from { margin-left: 0; } to { margin-left: 66%; } }
.fb-stage { display: block; margin-top: 4px; font-size: 11px; color: var(--ink3); }
.fb-task-err { margin-top: 6px; font-size: 11.5px; color: var(--err-deep); line-height: 1.6; word-break: break-word; }
.fb-task-foot { display: flex; align-items: center; justify-content: space-between; margin-top: 7px; }
.fb-task-time { font-family: var(--font-num); font-size: 11px; color: var(--ink3); }
.fb-task-ops { display: flex; gap: 6px; }
.fb-op {
  padding: 4px 10px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--card);
  color: var(--ink2);
  font: inherit;
  font-size: 11.5px;
  font-weight: 600;
  cursor: pointer;
  transition: var(--transition);
}
.fb-op:hover:not(:disabled) { border-color: var(--brand); color: var(--brand-deep); background: var(--brand-faint); }
.fb-op.danger:hover:not(:disabled) { border-color: var(--err-border); color: var(--err-deep); background: var(--err-bg); }
.fb-op:disabled { opacity: .5; cursor: not-allowed; }

/* ===== 通知列表 ===== */
.fb-ntf {
  padding: 9px 11px;
  border: 1px solid var(--line);
  border-radius: var(--radius);
  background: var(--card);
  margin-bottom: 8px;
  cursor: pointer;
  transition: var(--transition);
}
.fb-ntf:hover { border-color: var(--brand); background: var(--brand-faint); }
.fb-ntf-top { display: flex; align-items: center; gap: 6px; }
.fb-ntf-dot { flex: none; width: 7px; height: 7px; border-radius: 50%; background: var(--err); }
.fb-ntf-title { flex: 1; min-width: 0; font-size: 12.5px; color: var(--ink2); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.fb-ntf.unread .fb-ntf-title { color: var(--ink); font-weight: 800; }
.fb-ntf-time { flex: none; font-family: var(--font-num); font-size: 11px; color: var(--ink3); }
.fb-ntf-body {
  margin-top: 4px;
  font-size: 11.5px;
  color: var(--ink3);
  line-height: 1.6;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
</style>
