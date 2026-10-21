<template>
  <div class="view rc">
    <div class="rc-head">
      <div>
        <div class="rc-title">📺 资源中心</div>
        <div class="rc-sub">
          <template v-if="mode === 'reco'">
            针对「{{ kpName || '你的学情' }}」从 B 站挑出的讲解视频，点封面直接去 B 站看。
          </template>
          <template v-else>
            按知识点浏览 B 站高中数学讲解视频（{{ topicLabel }}）。
          </template>
        </div>
      </div>
      <div class="rc-actions">
        <input
          v-model="keyword" class="rc-search" type="text" placeholder="搜「数列求和」…"
          @keyup.enter="searchByKeyword"
        >
        <button class="rc-btn" :disabled="loading" @click="reload">↻ 刷新</button>
      </div>
    </div>

    <div class="rc-modes">
      <button :class="{ active: mode === 'reco' }" @click="switchMode('reco')">🎯 为你推荐</button>
      <button :class="{ active: mode === 'topic' }" @click="switchMode('topic')">🧭 按知识点浏览</button>
    </div>

    <!-- 分类 chips（按知识点浏览模式） -->
    <div v-if="mode === 'topic'" class="rc-chips">
      <span
        v-for="t in topics" :key="t.key"
        class="rc-chip" :class="{ active: t.key === activeTopic }"
        @click="switchTopic(t.key)"
      >{{ t.icon }} {{ t.label }}</span>
    </div>

    <div v-if="loading" class="rc-state">正在从 B 站取视频…</div>
    <div v-else-if="error" class="rc-state">
      {{ error }}<button class="rc-btn" style="margin-left:10px" @click="reload">重试</button>
    </div>
    <div v-else-if="!items.length" class="rc-state">没有取到视频，换个知识点或关键词试试。</div>

    <div v-else class="rc-grid">
      <a
        v-for="v in items" :key="v.bvid + '_' + v.url"
        class="rc-card" :href="v.url" target="_blank" rel="noopener noreferrer"
        :title="v.title"
      >
        <div class="rc-cover">
          <!-- hdslb 图片有 referer 防盗链：非 bilibili 来源会 403，必须 no-referrer -->
          <img :src="v.cover" alt="" loading="lazy" referrerpolicy="no-referrer">
          <span v-if="durationText(v)" class="rc-dur">{{ durationText(v) }}</span>
          <span class="rc-play">▶ {{ playText(v.play) }}</span>
        </div>
        <div class="rc-body">
          <div class="rc-card-title">{{ v.title }}</div>
          <div class="rc-meta">
            <span class="rc-up">@{{ v.author || 'B 站 UP 主' }}</span>
          </div>
          <div v-if="v.match_reason" class="rc-why">🎯 {{ v.match_reason }}</div>
        </div>
      </a>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { api, ApiError } from '@/api/client'

const mode = ref('reco')
const loading = ref(true)
const error = ref('')
const items = ref([])
const topics = ref([])
const activeTopic = ref('featured')
const kpName = ref('')
const keyword = ref('')

const topicLabel = computed(
  () => topics.value.find((t) => t.key === activeTopic.value)?.label || '精选中',
)

function playText(n) {
  const v = Number(n) || 0
  if (v >= 100000000) return `${(v / 100000000).toFixed(1)} 亿`
  if (v >= 10000) return `${(v / 10000).toFixed(1)} 万`
  return String(v)
}

/** 超长 duration（合集，如 176:02:25）显示为「合集」，避免卡片上出现无意义长数字 */
function durationText(v) {
  const sec = Number(v.duration_sec) || 0
  if (sec > 6 * 3600) return '合集'
  if (v.duration) return String(v.duration).replace(/^(\d+):(\d)$/, '$1:0$2')
  return ''
}

async function loadRecommend() {
  loading.value = true
  error.value = ''
  try {
    const d = await api.post('/student/videos/recommend', { max_results: 8 })
    items.value = d?.items || []
    kpName.value = d?.kp_name || ''
    // 工作流不可用时静默降级为本地推荐，只留控制台线索，不在页面上打扰学生
    if (d?.unavailable) console.warn('[资源中心] 讯飞推荐工作流不可用，已用本地推荐：', d?.notice || '')
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : '加载失败，请稍后重试'
    items.value = []
  } finally {
    loading.value = false
  }
}

async function loadTopic() {
  loading.value = true
  error.value = ''
  try {
    const d = await api.get('/student/videos', { topic: activeTopic.value, page_size: 12 })
    items.value = d?.items || []
    topics.value = d?.topics || []
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : '加载失败，请稍后重试'
    items.value = []
  } finally {
    loading.value = false
  }
}

function reload() {
  return mode.value === 'reco' ? loadRecommend() : loadTopic()
}

function switchMode(m) {
  if (mode.value === m) return
  mode.value = m
  reload()
}

function switchTopic(key) {
  activeTopic.value = key
  loadTopic()
}

async function searchByKeyword() {
  const kw = keyword.value.trim()
  if (!kw) return
  mode.value = 'topic'
  loading.value = true
  error.value = ''
  try {
    const d = await api.get('/student/videos', { keyword: kw, page_size: 12 })
    items.value = d?.items || []
    topics.value = d?.topics || topics.value
    activeTopic.value = 'custom'
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : '搜索失败，请稍后重试'
    items.value = []
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  // 先取分类列表（顺带填充 chips），推荐流单独请求
  try {
    const d = await api.get('/student/videos', { page_size: 1 })
    topics.value = d?.topics || []
  } catch { /* 分类拿不到不影响推荐流 */ }
  await loadRecommend()
})
</script>

<style scoped>
.rc-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; margin-bottom: 14px; }
.rc-title { font-size: 17px; font-weight: 700; color: var(--ink); }
.rc-sub { font-size: 12.5px; color: var(--ink3); margin-top: 5px; line-height: 1.6; }
.rc-actions { display: flex; gap: 8px; align-items: center; }
.rc-search {
  width: 190px; padding: 7px 12px; border-radius: 9px; border: 1px solid var(--line);
  background: var(--card); font: inherit; font-size: 12.5px; color: var(--ink);
}
.rc-btn {
  padding: 7px 14px; border-radius: 9px; border: 1px solid var(--line);
  background: var(--card); font: inherit; font-size: 12.5px; color: var(--ink2); cursor: pointer;
}
.rc-btn:disabled { opacity: .55; cursor: not-allowed; }

.rc-modes { display: flex; gap: 8px; margin-bottom: 12px; }
.rc-modes button {
  padding: 8px 18px; border-radius: 999px; border: 1px solid var(--line);
  background: var(--card); font: inherit; font-size: 13px; font-weight: 600;
  color: var(--ink2); cursor: pointer; transition: all .18s ease;
}
.rc-modes button.active {
  background: var(--primary-subtle, #eef2ff); border-color: var(--primary-border, #c7d2fe);
  color: var(--primary, #4f46e5);
}

.rc-chips { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 12px; }
.rc-chip {
  padding: 5px 13px; border-radius: 999px; font-size: 12.5px; cursor: pointer;
  background: var(--bg2, #f1f5f9); color: var(--ink2); border: 1px solid transparent;
}
.rc-chip.active { background: var(--primary-subtle, #eef2ff); color: var(--primary, #4f46e5); border-color: var(--primary-border, #c7d2fe); }

.rc-state {
  padding: 44px 20px; text-align: center; color: var(--ink3); font-size: 13px;
  background: var(--card); border: 1px dashed var(--line); border-radius: 12px;
}

.rc-grid {
  display: grid; gap: 16px;
  grid-template-columns: repeat(auto-fill, minmax(230px, 1fr));
}
.rc-card {
  display: flex; flex-direction: column; overflow: hidden; text-decoration: none;
  background: var(--card); border: 1px solid var(--line); border-radius: 13px;
  transition: transform .18s ease, box-shadow .18s ease, border-color .18s ease;
}
.rc-card:hover {
  transform: translateY(-3px); border-color: var(--primary-border, #c7d2fe);
  box-shadow: 0 8px 22px rgba(15, 23, 42, .09);
}
.rc-cover { position: relative; aspect-ratio: 16 / 9; background: var(--bg2, #f1f5f9); }
.rc-cover img { width: 100%; height: 100%; object-fit: cover; display: block; }
.rc-dur, .rc-play {
  position: absolute; bottom: 7px; padding: 2px 7px; border-radius: 5px;
  font-size: 11px; color: #fff; background: rgba(15, 23, 42, .78); line-height: 1.5;
}
.rc-dur { right: 7px; }
.rc-play { left: 7px; }
.rc-body { padding: 11px 13px 13px; display: flex; flex-direction: column; gap: 7px; flex: 1; }
.rc-card-title {
  font-size: 13px; font-weight: 600; color: var(--ink); line-height: 1.5;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
}
.rc-meta { display: flex; align-items: center; gap: 8px; }
.rc-up { font-size: 11.5px; color: var(--ink3); }
.rc-why {
  font-size: 11.5px; color: var(--primary, #4f46e5); background: var(--primary-subtle, #eef2ff);
  border-radius: 7px; padding: 5px 8px; line-height: 1.55;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
}
</style>
