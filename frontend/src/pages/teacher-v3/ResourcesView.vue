<template>
  <div class="tv3-page-shell" data-testid="tv3-resources">
    <TeacherPageHeader title="资源中心" subtitle="与高中数学教学相关的 B 站备课视频，点封面直接去 B 站看" icon="📺">
      <span class="tv3-tag tv3-tag--primary">{{ sourceLabel }}</span>
    </TeacherPageHeader>

    <div class="rv3-head">
      <div class="rv3-chips">
        <span
          v-for="t in topics" :key="t.key"
          class="rv3-chip" :class="{ active: t.key === activeTopic }"
          :title="t.desc" @click="switchTopic(t.key)"
        >{{ t.icon }} {{ t.label }}</span>
      </div>
      <div class="rv3-actions">
        <input
          v-model="keyword" class="tv3-input" style="width: 200px" type="text"
          placeholder="搜「椭圆定义」「导数应用」…" data-testid="tv3-res-search"
          @keyup.enter="searchByKeyword"
        >
        <button class="tv3-btn tv3-btn--sm" :disabled="loading" data-testid="tv3-res-refresh" @click="reload">↻ 刷新</button>
      </div>
    </div>

    <div class="tv3-card">
      <div class="tv3-card__body">
        <TeacherLoading v-if="loading" />
        <TeacherEmptyState
          v-else-if="error"
          title="视频加载失败"
          :desc="error"
          cta="重试"
          @cta-click="reload"
        />
        <TeacherEmptyState
          v-else-if="!items.length"
          title="没有取到视频"
          desc="换个分类或关键词试试。"
          cta="返回备课精选"
          @cta-click="switchTopic('featured')"
        />
        <div v-else class="rv3-grid" data-testid="tv3-res-grid">
          <a
            v-for="v in items" :key="v.bvid + '_' + v.url"
            class="rv3-card" :href="v.url" target="_blank" rel="noopener noreferrer"
            :title="v.title" data-testid="tv3-res-card"
          >
            <div class="rv3-cover">
              <!-- hdslb 图片有 referer 防盗链：非 bilibili 来源会 403，必须 no-referrer -->
              <img :src="v.cover" alt="" loading="lazy" referrerpolicy="no-referrer">
              <span v-if="durationText(v)" class="rv3-dur">{{ durationText(v) }}</span>
              <span class="rv3-play">▶ {{ playText(v.play) }}</span>
            </div>
            <div class="rv3-body">
              <div class="rv3-card-title">{{ v.title }}</div>
              <div class="rv3-meta"><span class="rv3-up">@{{ v.author || 'B 站 UP 主' }}</span></div>
            </div>
          </a>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * ResourcesView —— 教师端「资源中心」（2026-10-05 产品裁决：整体替换为 B 站备课视频推荐）。
 *
 * 为什么替换：原页面聚合 decks/plans/recipes（备课产物）冒充「资源」，
 * 缩略图是 CSS 渐变 + emoji 占位、上传是纯前端假进度（setTimeout），
 * 教师真正要的「备课资源」一直没有真实接入。
 *
 * 实现：照抄学生端 ResourceCenterView（卡片 <a target=_blank> + hdslb 封面
 * `referrerpolicy="no-referrer"` 防盗链），数据走 8000 新增的
 * `/api/teacher/videos`（复用学生端 bilibili 服务，只换教师备课视角的检索词与分类）。
 */
import { computed, onMounted, ref } from 'vue'
import { api, ApiError } from '@/api/client'
import { TeacherPageHeader, TeacherEmptyState, TeacherLoading } from '@/components/teacherV3/common'

interface VideoItem {
  bvid: string
  title: string
  author: string
  cover: string
  duration: string
  duration_sec: number
  play: number
  url: string
  match_reason?: string
}
interface TopicMeta { key: string; label: string; icon: string; desc: string }

const loading = ref(true)
const error = ref('')
const items = ref<VideoItem[]>([])
const topics = ref<TopicMeta[]>([])
const activeTopic = ref('featured')
const keyword = ref('')

const sourceLabel = computed(() => {
  const t = topics.value.find((x) => x.key === activeTopic.value)
  return t ? `${t.icon} ${t.label}` : '📺 备课视频'
})

function playText(n: number) {
  const v = Number(n) || 0
  if (v >= 100000000) return `${(v / 100000000).toFixed(1)} 亿`
  if (v >= 10000) return `${(v / 10000).toFixed(1)} 万`
  return String(v)
}

/** 超长 duration（合集）显示为「合集」，避免卡片上出现无意义长数字 */
function durationText(v: VideoItem) {
  const sec = Number(v.duration_sec) || 0
  if (sec > 6 * 3600) return '合集'
  if (v.duration) return String(v.duration).replace(/^(\d+):(\d)$/, '$1:0$2')
  return ''
}

async function loadTopic() {
  loading.value = true
  error.value = ''
  try {
    const d = await api.get('/teacher/videos', { topic: activeTopic.value, page_size: 12 })
    items.value = d?.items || []
    topics.value = d?.topics || []
    activeTopic.value = d?.topic?.key || activeTopic.value
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : '加载失败，请稍后重试'
    items.value = []
  } finally {
    loading.value = false
  }
}

function switchTopic(key: string) {
  activeTopic.value = key
  loadTopic()
}

async function searchByKeyword() {
  const kw = keyword.value.trim()
  if (!kw) return
  loading.value = true
  error.value = ''
  try {
    const d = await api.get('/teacher/videos', { keyword: kw, page_size: 12 })
    items.value = d?.items || []
    topics.value = d?.topics || topics.value
    activeTopic.value = d?.topic?.key || 'custom'
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : '搜索失败，请稍后重试'
    items.value = []
  } finally {
    loading.value = false
  }
}

function reload() {
  return loadTopic()
}

onMounted(loadTopic)
</script>

<style scoped>
.rv3-head { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-bottom: 14px; flex-wrap: wrap; }
.rv3-chips { display: flex; flex-wrap: wrap; gap: 8px; }
.rv3-chip {
  padding: 5px 13px; border-radius: 999px; font-size: 12px; cursor: pointer;
  background: var(--tv3-card-bg, #fff); color: var(--tv3-ink2, #5b6b83); border: 1px solid var(--tv3-line, #e5ecf4);
}
.rv3-chip.active { background: var(--tv3-primary-soft, #eaf1fb); color: var(--tv3-primary, #2f6bff); border-color: var(--tv3-primary, #2f6bff); font-weight: 600; }
.rv3-actions { display: flex; gap: 8px; align-items: center; }

.rv3-grid { display: grid; gap: 14px; grid-template-columns: repeat(auto-fill, minmax(228px, 1fr)); }
.rv3-card {
  display: flex; flex-direction: column; overflow: hidden; text-decoration: none;
  background: var(--tv3-card-bg, #fff); border: 1px solid var(--tv3-line, #e5ecf4); border-radius: 12px;
  transition: transform .16s ease, box-shadow .16s ease, border-color .16s ease;
}
.rv3-card:hover { transform: translateY(-3px); border-color: var(--tv3-primary, #2f6bff); box-shadow: 0 8px 20px rgba(15, 23, 42, .08); }
.rv3-cover { position: relative; aspect-ratio: 16 / 9; background: #f1f5f9; }
.rv3-cover img { width: 100%; height: 100%; object-fit: cover; display: block; }
.rv3-dur, .rv3-play {
  position: absolute; bottom: 7px; padding: 2px 7px; border-radius: 5px;
  font-size: 11px; color: #fff; background: rgba(15, 23, 42, .78); line-height: 1.5;
}
.rv3-dur { right: 7px; }
.rv3-play { left: 7px; }
.rv3-body { padding: 10px 12px 12px; display: flex; flex-direction: column; gap: 6px; flex: 1; }
.rv3-card-title {
  font-size: 12.5px; font-weight: 600; color: var(--tv3-ink, #101828); line-height: 1.5;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
}
.rv3-meta { display: flex; align-items: center; gap: 8px; }
.rv3-up { font-size: 11px; color: var(--tv3-ink3, #8b9cb8); }
</style>
