<template>
  <div class="view">
    <!-- 加载中 -->
    <div v-if="classLoading" class="empty-state">
      <div class="es-icon">⏳</div>
      <div class="es-text">班级信息加载中…</div>
    </div>

    <!-- 加载失败 -->
    <div v-else-if="classError" class="empty-state">
      <div class="es-icon">⚠️</div>
      <div class="es-text">{{ classError }}</div>
    </div>

    <!-- 空态：未加入班级 + 加入入口 -->
    <div v-else-if="!currentClass" class="empty-state">
      <div class="es-icon">🏫</div>
      <div class="es-text">你还没有加入班级，输入老师给的邀请码加入吧</div>
      <div class="join-value">
        <div class="jv-title">加入班级后，你可以：</div>
        <div class="jv-item">📥 接收老师布置的作业和课堂任务（含截止提醒）</div>
        <div class="jv-item">📄 查看老师分享的课件和复习资料</div>
        <div class="jv-item">📊 看到自己的班级位置（仅自己可见，不公开）</div>
        <div class="jv-item">❓ 作业做错自动进错题本，AI 帮你补漏</div>
      </div>
      <div class="join-box">
        <input
          v-model.trim="inviteCode"
          class="join-input"
          maxlength="8"
          placeholder="输入 6~8 位班级邀请码"
          @keyup.enter="joinClass"
        />
        <button class="join-btn" :disabled="joining || inviteCode.length < 6" @click="joinClass">
          {{ joining ? '提交中…' : '加入班级' }}
        </button>
      </div>
    </div>

    <template v-else>
      <div class="class-hero">
        <span class="cl-tag">🏫 {{ currentClass.name }}<template v-if="currentClass.grade"> · {{ currentClass.grade }}</template> · 共 {{ memberTotal }} 人</span>
        <h1>{{ currentClass.name }} · 数学学习共同体</h1>
        <div class="meta">
          <div>我的角色 <b>{{ currentClass.myRole === 'teacher' ? '老师' : '学生' }}</b></div>
          <div>班级成员 <b>{{ memberTotal }}</b></div>
          <div>任课老师 <b>{{ teacherNames || '—' }}</b></div>
          <div v-if="classes.length > 1">
            切换班级
            <select v-model="selectedClassId" class="class-select" @change="onClassChange">
              <option v-for="c in classes" :key="c.id" :value="c.id">{{ c.name }}</option>
            </select>
          </div>
        </div>
      </div>

      <div class="class-grid">
        <div class="class-section">
          <h3>👥 班级成员</h3>
          <div v-if="membersLoading" class="empty-state"><div class="es-text">成员加载中…</div></div>
          <div v-else-if="membersError" class="empty-state"><div class="es-icon">⚠️</div><div class="es-text">{{ membersError }}</div></div>
          <div v-else-if="!members.length" class="empty-state"><div class="es-icon">👥</div><div class="es-text">还没有成员</div></div>
          <div v-else class="member-list">
            <div v-for="m in members" :key="m.id" class="member-item">
              <div class="av" :style="{ background: m.av }">{{ m.char }}</div>
              <div>
                <div class="nm">{{ m.name }}</div>
                <div :style="{ fontSize: '10.5px', color: m.color || 'var(--ink3)', fontWeight: m.bold ? 700 : 400 }">{{ m.sub }}</div>
              </div>
            </div>
          </div>
        </div>

        <div>
          <div class="class-section" style="margin-bottom:14px;">
            <h3>📢 班级动态 · 近 14 天</h3>
            <div v-if="feedLoading" class="empty-state"><div class="es-text">动态加载中…</div></div>
            <div v-else-if="feedError" class="empty-state"><div class="es-icon">⚠️</div><div class="es-text">{{ feedError }}</div></div>
            <div v-else-if="!events.length" class="empty-state"><div class="es-icon">🍃</div><div class="es-text">班级还很安静，期待第一条动态</div></div>
            <div v-else class="class-timeline">
              <div v-for="(ev, i) in events" :key="i" class="tl-evt">
                <div class="time">{{ ev.time }}</div>
                <div class="what">{{ ev.icon }} {{ ev.what }}</div>
              </div>
            </div>
          </div>

          <div v-if="hotErrorsLoading || hotError || shares.length" class="class-section">
            <h3>🤝 错题共享 · 全班高频错题（近 30 天）</h3>
            <div v-if="hotErrorsLoading" class="empty-state"><div class="es-text">统计中…</div></div>
            <div v-else-if="hotError" class="empty-state"><div class="es-icon">⚠️</div><div class="es-text">{{ hotError }}</div></div>
            <div v-else style="font-size:11.5px;color:var(--ink2);line-height:1.65;padding:8px 0;">
              <div v-for="(s, i) in shares" :key="i" class="share-item" :class="s.cls">
                <b :style="{ color: s.color }">{{ s.count }} 人</b> 错了 · <b style="color:var(--ink);">{{ s.text }}</b>
                <span v-if="s.typeText" style="color:var(--ink3);">（多为{{ s.typeText }}问题）</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { api, ApiError } from '@/api/client'
import { useToastStore } from '@/stores/toast'

const toast = useToastStore()

/* ---------- 我的班级 ---------- */
const classes = ref([])
const classLoading = ref(true)
const classError = ref('')
const selectedClassId = ref('')
const currentClass = computed(() => classes.value.find((c) => c.id === selectedClassId.value) || null)

const AVATARS = [
  'linear-gradient(135deg,#f59e0b,#fb923c)',
  'linear-gradient(135deg,#3b82f6,#60a5fa)',
  'linear-gradient(135deg,#ec4899,#f472b6)',
  'linear-gradient(135deg,#10b981,#34d399)',
  'linear-gradient(135deg,#8b5cf6,#a78bfa)',
  'linear-gradient(135deg,#06b6d4,#22d3ee)',
]

async function loadClasses() {
  classLoading.value = true
  classError.value = ''
  try {
    const data = await api.get('/classes/mine')
    classes.value = data?.items || []
    selectedClassId.value = classes.value[0]?.id || ''
  } catch (e) {
    classError.value = e instanceof ApiError ? e.message : '班级信息加载失败'
  } finally {
    classLoading.value = false
  }
}

/* ---------- 加入班级 ---------- */
const inviteCode = ref('')
const joining = ref(false)

async function joinClass() {
  if (inviteCode.value.length < 6 || joining.value) return
  joining.value = true
  try {
    const data = await api.post('/classes/join', { invite_code: inviteCode.value })
    toast.success(`已申请加入「${data?.className || '班级'}」，等待老师确认`)
    inviteCode.value = ''
    await loadClasses()
  } catch (e) {
    toast.error(e instanceof ApiError ? e.message : '加入失败，请检查邀请码')
  } finally {
    joining.value = false
  }
}

/* ---------- 班级成员 ---------- */
const members = ref([])
const memberTotal = ref(0)
const membersLoading = ref(false)
const membersError = ref('')

const teacherNames = computed(() =>
  members.value.filter((m) => m.role === 'teacher').map((m) => m.name).join('、')
)

async function loadMembers() {
  if (!selectedClassId.value) return
  membersLoading.value = true
  membersError.value = ''
  try {
    const data = await api.get(`/classes/${selectedClassId.value}/members`)
    const items = data?.items || []
    memberTotal.value = data?.total ?? items.length
    members.value = items.map((m, i) => {
      const name = m.nicknameInClass || (m.memberRole === 'teacher' ? '老师' : `同学 ${String(m.userId || '').slice(0, 4)}`)
      return {
        id: m.id,
        role: m.memberRole,
        char: name.slice(0, 1),
        name,
        av: AVATARS[i % AVATARS.length],
        sub: m.memberRole === 'teacher' ? '任课老师' : `加入于 ${fmtDay(m.joinedAt)}`,
        color: m.memberRole === 'teacher' ? 'var(--brand-deep)' : '',
        bold: m.memberRole === 'teacher',
      }
    })
  } catch (e) {
    membersError.value = e instanceof ApiError ? e.message : '成员加载失败'
  } finally {
    membersLoading.value = false
  }
}

/* ---------- 班级动态 ---------- */
const events = ref([])
const feedLoading = ref(false)
const feedError = ref('')

const FEED_ICONS = { event: '📢', practice: '📝', member_join: '👋' }

async function loadFeed() {
  if (!selectedClassId.value) return
  feedLoading.value = true
  feedError.value = ''
  try {
    const data = await api.get(`/classes/${selectedClassId.value}/feed`)
    events.value = (data?.items || []).map((it) => ({
      time: fmtMinute(it.created_at),
      icon: FEED_ICONS[it.kind] || '📌',
      what: it.text || '',
    }))
  } catch (e) {
    feedError.value = e instanceof ApiError ? e.message : '动态加载失败'
  } finally {
    feedLoading.value = false
  }
}

/* ---------- 班级高频错题 ---------- */
const shares = ref([])
const hotErrorsLoading = ref(false)
const hotError = ref('')

const ERROR_TYPE_TEXT = {
  logic: '思路', calculation: '计算', concept: '概念', reading: '审题', formula: '公式',
}

async function loadHotErrors() {
  if (!selectedClassId.value) return
  hotErrorsLoading.value = true
  hotError.value = ''
  try {
    const data = await api.get(`/classes/${selectedClassId.value}/hot-errors`, { days: 30, limit: 5 })
    shares.value = (data?.items || []).map((it, i) => ({
      cls: i === 0 ? 'err' : 'warn',
      color: i === 0 ? 'var(--err-deep)' : 'var(--warn-deep)',
      count: it.member_count ?? 0,
      text: it.kp_name || it.kp_code, // 孤儿 kp 码兜底显示 kp_code
      typeText: ERROR_TYPE_TEXT[it.top_error_type] || '',
    }))
  } catch (e) {
    hotError.value = e instanceof ApiError ? e.message : '高频错题加载失败'
  } finally {
    hotErrorsLoading.value = false
  }
}

/* ---------- 工具 ---------- */
function pad(n) { return String(n).padStart(2, '0') }
function fmtDay(iso) {
  if (!iso) return '—'
  const d = new Date(iso)
  return isNaN(d) ? '—' : `${d.getMonth() + 1}/${d.getDate()}`
}
function fmtMinute(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  if (isNaN(d)) return ''
  const now = new Date()
  const sameDay = d.toDateString() === now.toDateString()
  const hm = `${pad(d.getHours())}:${pad(d.getMinutes())}`
  return sameDay ? `今天 ${hm}` : `${d.getMonth() + 1}/${d.getDate()} ${hm}`
}

function onClassChange() {
  loadMembers()
  loadFeed()
  loadHotErrors()
}

onMounted(async () => {
  await loadClasses()
  if (selectedClassId.value) onClassChange()
})
</script>

<style scoped>
.share-item { padding: 8px 10px; border-radius: 6px; margin-bottom: 6px; }
.share-item.err { background: var(--err-bg); }
.share-item.warn { background: var(--warn-bg); }
.join-box { display: flex; gap: 8px; justify-content: center; margin-top: 16px; }
.join-input { padding: 8px 12px; border: 1px solid var(--line); border-radius: 8px; font-size: 13px; width: 220px; }
.join-btn { padding: 8px 18px; border: none; border-radius: 8px; background: var(--brand); color: #fff; font-size: 13px; cursor: pointer; }
.join-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.class-select { margin-left: 6px; padding: 2px 6px; border: 1px solid var(--line); border-radius: 6px; font-size: 12px; }

/* S10 空态价值引导 */
.join-value { margin-top: 16px; text-align: left; max-width: 380px; margin-left: auto; margin-right: auto;
  background: var(--bg2, #f1f5f9); border-radius: 12px; padding: 14px 18px; }
.jv-title { font-size: 12.5px; font-weight: 800; color: var(--ink2); margin-bottom: 8px; }
.jv-item { font-size: 12.5px; color: var(--ink2); line-height: 2; }
</style>
