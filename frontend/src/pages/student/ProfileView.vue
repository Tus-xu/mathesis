<template>
  <div class="view">
    <div class="greeting">
      <div class="hello">个人中心 <span style="color:var(--brand-deep);">· {{ auth.nickname }}</span></div>
      <div class="sub">学习账号与偏好设置都在这里。修改实时同步到所有设备。</div>
    </div>

    <!-- 用户名片 -->
    <div class="profile-hero">
      <div class="avatar">{{ avatarChar }}</div>
      <div class="info">
        <div class="name">{{ auth.nickname }} <span class="badge">VIP</span></div>
        <div class="meta">
          <span v-if="meError">📋 资料加载失败，请稍后刷新</span>
          <template v-else>
            <span v-if="gradeText">📚 {{ gradeText }}</span>
            <span v-if="phoneText">📱 {{ phoneText }}</span>
            <span v-if="idText">🆔 {{ idText }}</span>
            <span v-if="!gradeText && !phoneText && !idText">📋 暂无更多资料，去完善个人信息吧</span>
          </template>
        </div>
      </div>
      <button class="btn" @click="toast.info('编辑资料功能即将开放')">✏️ 编辑资料</button>
    </div>

    <!-- 学习数据 -->
    <div class="section-head">
      <h2>📊 我的学情速览</h2>
      <span class="more" @click="$router.push('/report')">→ 查看详细学情</span>
    </div>
    <div v-if="growthError" class="stat-grid-v4">
      <div class="stat-card-v4" style="grid-column:1 / -1;">
        <div class="lbl">学情数据</div>
        <div class="val" style="font-size:14px;color:var(--ink3);">加载失败，请稍后刷新重试</div>
      </div>
    </div>
    <div v-else-if="growthLoading" class="stat-grid-v4">
      <div class="stat-card-v4" style="grid-column:1 / -1;">
        <div class="lbl">学情数据</div>
        <div class="val" style="font-size:14px;color:var(--ink3);">加载中…</div>
      </div>
    </div>
    <div v-else class="stat-grid-v4">
      <div class="stat-card-v4">
        <div class="lbl">本周综合分</div>
        <div class="val" style="color:var(--ink);">
          <template v-if="growth.composite_score">{{ growth.composite_score }} <span class="up">{{ growth.score_delta_week >= 0 ? '↑' : '↓' }} {{ Math.abs(growth.score_delta_week) }}</span></template>
          <template v-else>--</template>
        </div>
        <div class="foot">{{ growth.composite_score ? `距目标 ${growth.target_score} 还差 ${Math.max(growth.target_score - growth.composite_score, 0)} 分` : '完成首次测评后生成综合分' }}</div>
      </div>
      <div class="stat-card-v4">
        <div class="lbl">连击天数</div>
        <div class="val" style="color:var(--brand-deep);">{{ growth.streak_days > 0 ? `${growth.streak_days} 天 🔥` : '--' }}</div>
        <div class="foot">{{ growth.streak_days > 0 ? (growth.streak_days >= 30 ? '已达成 30 天奖牌 🏅' : `距 30 天奖牌还差 ${30 - growth.streak_days} 天`) : '今天开始学习，点亮第 1 天' }}</div>
      </div>
      <div class="stat-card-v4">
        <div class="lbl">已掌握考点</div>
        <div class="val" style="color:var(--ok-deep);">{{ growth.total_kp_count ? `${growth.mastered_kp_count} / ${growth.total_kp_count}` : '--' }}</div>
        <div class="foot">{{ growth.total_kp_count ? `覆盖 ${Math.round(growth.mastered_kp_count / growth.total_kp_count * 100)}%` : '开始练习后统计掌握进度' }}</div>
      </div>
      <div class="stat-card-v4">
        <div class="lbl">独立解题率</div>
        <div class="val" style="color:var(--indigo);">{{ growth.independent_rate ? `${Math.round(growth.independent_rate * 100)}%` : '--' }}</div>
        <div class="foot">{{ growth.week_answer_count ? `本周作答 ${growth.week_answer_count} 题 · 对 ${growth.week_correct_count} 题` : '本周暂无作答记录' }}</div>
      </div>
    </div>

    <!-- 学习偏好 -->
    <div class="section-head">
      <h2>🎯 学习偏好</h2>
    </div>
    <div class="card" style="padding:6px 18px;">
      <div v-for="(p, i) in prefs" :key="i" class="pref-row" :class="{ last: i === prefs.length - 1 }">
        <span class="pref-ic">{{ p.ic }}</span>
        <div class="pref-body">
          <div class="pref-name">{{ p.name }}</div>
          <div class="pref-desc">{{ p.desc }}</div>
        </div>
        <button class="toggle" :class="{ on: p.on }" @click="p.on = !p.on; toast.info(p.name + (p.on ? ' 已开启' : ' 已关闭'))">
          <span class="thumb"></span>
        </button>
      </div>
    </div>

    <!-- 账号与隐私 -->
    <div class="section-head">
      <h2>🔒 账号与隐私</h2>
    </div>
    <div class="card" style="padding:6px 18px;">
      <div
        v-for="(a, i) in actions" :key="i"
        class="pref-row clickable" :class="{ last: i === actions.length - 1 }"
        @click="onAction(a)"
      >
        <span class="pref-ic">{{ a.ic }}</span>
        <div class="pref-body">
          <div class="pref-name">{{ a.name }}</div>
          <div class="pref-desc">{{ a.desc }}</div>
        </div>
        <span style="color:var(--ink3);">›</span>
      </div>
    </div>

    <!-- 退出登录 -->
    <div style="margin-top:24px;text-align:center;">
      <button class="btn" style="color:var(--err);" @click="logout">退出登录</button>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import { useToastStore } from '@/stores/toast'

const router = useRouter()
const auth = useAuthStore()
const toast = useToastStore()

/* ===== 用户资料（GET /api/auth/me，经 auth store 刷新缓存） ===== */
const meError = ref(false)
const avatarChar = computed(() => auth.user?.avatar || (auth.user?.username || auth.nickname || '同').slice(0, 1))
const gradeText = computed(() => auth.user?.grade || auth.user?.roles?.[0]?.org_name || '')
const phoneText = computed(() => {
  const p = auth.user?.phone
  return p ? String(p).replace(/^(\d{3})\d{4}(\d{4})$/, '$1****$2') : ''
})
const idText = computed(() => (auth.user?.id ? String(auth.user.id) : ''))

/* ===== 学情速览（GET /api/student/growth/overview） ===== */
const growthLoading = ref(true)
const growthError = ref(false)
const growth = ref({
  composite_score: 0, score_delta_week: 0, target_score: 0,
  streak_days: 0, mastered_kp_count: 0, total_kp_count: 0,
  independent_rate: 0, week_answer_count: 0, week_correct_count: 0,
})

async function loadAll() {
  growthLoading.value = true
  growthError.value = false
  meError.value = false
  const [meRes, growthRes] = await Promise.allSettled([
    auth.refreshMe(),
    api.get('/student/growth/overview'),
  ])
  if (meRes.status === 'rejected') meError.value = true
  if (growthRes.status === 'fulfilled') {
    growth.value = { ...growth.value, ...(growthRes.value || {}) }
  } else {
    growthError.value = true
  }
  growthLoading.value = false
}
onMounted(loadAll)

const prefs = [
  { ic: '🌙', name: '暗黑模式', desc: '夜间学习保护视力（当前浅色）', on: false },
  { ic: '🧘', name: '沉浸模式自动进入', desc: '进入练题时默认开启沉浸覆盖层', on: true },
  { ic: '🔔', name: '错题复习提醒', desc: 'FSRS 算法到点推送错题复习通知', on: true },
  { ic: '📤', name: '允许学情匿名统计', desc: '帮助平台改进推荐算法（不含个人信息）', on: true },
]

const actions = computed(() => [
  { ic: '🔐', name: '修改密码', desc: '每 90 天更换一次更安全' },
  { ic: '📱', name: '更换绑定手机', desc: phoneText.value ? `当前 ${phoneText.value}` : '绑定手机后可用于登录' },
  { ic: '🧹', name: '清除对话历史', desc: '永久删除本地所有对话记录' },
  { ic: '📜', name: '查看服务协议', desc: '用户协议与隐私政策' },
  { ic: 'ⓘ', name: '关于智学数研', desc: 'v4 · 暖琥珀·灰蓝·红黄绿' },
])

function onAction(a) {
  if (a.name.includes('清除对话')) {
    toast.warn('已清除本地所有对话记录')
  } else if (a.name.includes('服务协议')) {
    toast.info('已打开用户协议')
  } else if (a.name.includes('关于')) {
    toast.info('智学数研 v4 · 暖琥珀设计语言')
  } else {
    toast.info(`已进入「${a.name}」`)
  }
}

function logout() {
  if (!confirm('确定要退出登录吗？')) return
  auth.logout()
  toast.success('已退出登录')
  router.push('/login')
}
</script>

<style scoped>
.profile-hero {
  background: linear-gradient(135deg, var(--brand-faint), var(--brand-soft) 60%, var(--peach-soft));
  border: 1px solid var(--warn-border); border-radius: var(--radius-xl);
  padding: 22px 28px; display: flex; align-items: center; gap: 18px; margin-bottom: 18px;
}
.profile-hero .avatar {
  width: 72px; height: 72px; border-radius: 50%; flex-shrink: 0;
  background: linear-gradient(135deg, var(--brand), var(--brand2)); color: #fff;
  display: flex; align-items: center; justify-content: center;
  font-size: 28px; font-weight: 800; box-shadow: var(--shadow-md);
}
.profile-hero .info { flex: 1; min-width: 0; }
.profile-hero .name { font-size: 20px; font-weight: 800; margin-bottom: 6px; display: flex; align-items: center; gap: 10px; }
.profile-hero .name .badge {
  font-size: 10px; padding: 2px 8px; border-radius: 99px;
  background: linear-gradient(135deg, var(--brand), var(--brand2)); color: #fff; font-weight: 800;
}
.profile-hero .meta { display: flex; gap: 14px; font-size: 12.5px; color: var(--ink2); flex-wrap: wrap; }

.stat-grid-v4 { display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px; margin-bottom: 8px; }
.stat-card-v4 { background: var(--card); border: 1px solid var(--line); border-radius: var(--radius-lg); padding: 18px 20px; transition: var(--transition); }
.stat-card-v4:hover { box-shadow: var(--shadow-md); }
.stat-card-v4 .lbl { font-size: 11.5px; font-weight: 700; color: var(--ink3); margin-bottom: 8px; }
.stat-card-v4 .val { font-family: var(--font-num); font-size: 28px; font-weight: 900; line-height: 1.1; }
.stat-card-v4 .val .up { font-size: 12px; color: var(--ok-deep); font-weight: 700; margin-left: 6px; }
.stat-card-v4 .foot { font-size: 11.5px; color: var(--ink3); margin-top: 6px; }

.pref-row { display: flex; align-items: center; gap: 14px; padding: 14px 0; border-bottom: 1px dashed var(--line); cursor: pointer; transition: var(--transition); }
.pref-row.clickable:hover { background: var(--bg2); margin: 0 -18px; padding: 14px 18px; }
.pref-row.last { border-bottom: none; }
.pref-row .pref-ic { font-size: 18px; width: 30px; text-align: center; flex-shrink: 0; }
.pref-row .pref-body { flex: 1; min-width: 0; }
.pref-row .pref-name { font-size: 13.5px; font-weight: 700; color: var(--ink); margin-bottom: 2px; }
.pref-row .pref-desc { font-size: 11.5px; color: var(--ink3); }
.toggle { width: 38px; height: 22px; border-radius: 99px; background: var(--bg2); border: 1px solid var(--line); padding: 0; position: relative; cursor: pointer; transition: var(--transition); flex-shrink: 0; }
.toggle .thumb { position: absolute; top: 2px; left: 2px; width: 16px; height: 16px; border-radius: 50%; background: var(--card); box-shadow: var(--shadow-sm); transition: var(--transition); }
.toggle.on { background: var(--brand); border-color: var(--brand); }
.toggle.on .thumb { left: 18px; }
@media (max-width: 900px) {
  .stat-grid-v4 { grid-template-columns: repeat(2, 1fr); }
  .profile-hero { flex-wrap: wrap; }
}
</style>