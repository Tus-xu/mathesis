<template>
  <!--
    P1 对话内视频推荐卡（card.type === 'video_cards'）：
    - 数据全部来自后端 bilibili.fetch_videos（B站真实检索/缓存/降级源），前端不造任何链接；
      url 由后端拼好，缺失时前端仅按 bvid 兜底拼接（同后端规则）。
    - 视觉复用资源中心视频卡语言（ResourceCenterView rc-card）：16:9 封面 + 时长角标 +
      播放量 + UP主 + 标题两行截断 + match_reason；整卡新标签打开B站。
    - loading：SSE 前置 status 事件（stage=video_search「正在从B站找相关讲解视频…」）
      由 MessageBubble 过程条承接；卡片内补无数据/封面加载失败降级态。
    - 历史重放：envelope.blocks {type:'card', data:<同构>} 经 messageModel 还原为同构卡。
  -->
  <div class="vc-card">
    <div class="vc-head">
      <span class="vc-head-title">📺 B站讲解视频</span>
      <span v-if="query" class="vc-head-q">关于「{{ query }}」</span>
      <span v-if="sourceTag" class="vc-src-tag">{{ sourceTag }}</span>
    </div>

    <!-- 无数据态：后端检索为空时本卡不应下发（只回文字），此处兜底如实提示 -->
    <div v-if="!items.length" class="vc-empty">
      这次没挑到合适的视频。可以去「资源中心」按知识点浏览，或换个说法再问一次～
    </div>

    <div v-else class="vc-grid">
      <a
        v-for="v in items"
        :key="v.bvid || v.url"
        class="vc-item"
        :href="urlOf(v)"
        target="_blank"
        rel="noopener noreferrer"
        :title="v.title"
        @click.stop
      >
        <div class="vc-cover">
          <!-- hdslb 封面有 referer 防盗链：必须 no-referrer，否则非B站来源 403 -->
          <img
            v-if="v.cover && !broken.has(v.bvid || v.url)"
            :src="v.cover" alt="" loading="lazy" referrerpolicy="no-referrer"
            @error="broken.add(v.bvid || v.url)"
          >
          <div v-else class="vc-cover-fallback">▶</div>
          <span v-if="durationText(v)" class="vc-dur">{{ durationText(v) }}</span>
          <span class="vc-play">▶ {{ playText(v.play) }}</span>
        </div>
        <div class="vc-body">
          <div class="vc-title">{{ v.title }}</div>
          <div class="vc-meta">
            <span class="vc-up">@{{ v.author || 'B站UP主' }}</span>
          </div>
          <div v-if="v.match_reason" class="vc-why">🎯 {{ v.match_reason }}</div>
        </div>
      </a>
    </div>
  </div>
</template>

<script setup>
import { computed, reactive } from 'vue'

const props = defineProps({
  card: { type: Object, required: true }, // {type:'video_cards', query, source, items[]}
})

const query = computed(() => String(props.card?.query || ''))
const source = computed(() => String(props.card?.source || ''))

/** 数据源如实标注：fallback/stale 是降级/缓存数据，bilibili/cache（新鲜）不打扰 */
const SOURCE_TAG_ZH = { fallback: '离线推荐', stale: '缓存结果' }
const sourceTag = computed(() => SOURCE_TAG_ZH[source.value] || '')

const items = computed(() => {
  const list = Array.isArray(props.card?.items) ? props.card.items : []
  return list
    .filter((v) => v && typeof v === 'object' && (v.url || v.bvid))
    .slice(0, 4)
})

// 封面加载失败（防盗链/过期）：按 bvid 记录，退回纯色占位，不整卡崩
const broken = reactive(new Set())

function urlOf(v) {
  if (v.url) return String(v.url)
  return `https://www.bilibili.com/video/${v.bvid}`
}

/* 播放量万/亿格式化（与资源中心 playText 同口径，ResourceCenterView:85-90） */
function playText(n) {
  const v = Number(n) || 0
  if (v >= 100000000) return `${(v / 100000000).toFixed(1)} 亿`
  if (v >= 10000) return `${(v / 10000).toFixed(1)} 万`
  return String(v)
}

/* 超长 duration（合集 >6h）显示「合集」；mm:ss / h:mm:ss 均可直接展示
   （与资源中心 durationText 同口径，ResourceCenterView:92-98，补个位分/秒补零） */
function durationText(v) {
  const sec = Number(v.duration_sec) || 0
  if (sec > 6 * 3600) return '合集'
  if (v.duration) return String(v.duration).replace(/^(\d+):(\d)$/, '$1:0$2')
  if (sec > 0) {
    const h = Math.floor(sec / 3600)
    const m = Math.floor((sec % 3600) / 60)
    const s = sec % 60
    const mm = String(m).padStart(2, '0')
    const ss = String(s).padStart(2, '0')
    return h > 0 ? `${h}:${mm}:${ss}` : `${m}:${ss}`
  }
  return ''
}
</script>

<style scoped>
.vc-card {
  margin-top: 10px; padding: 10px;
  border: 1px solid var(--border); border-radius: var(--radius-md);
  background: var(--bg-subtle, #f8fafc);
}
.vc-head { display: flex; align-items: center; gap: 8px; padding: 0 2px 8px; flex-wrap: wrap; }
.vc-head-title { font-size: 13px; font-weight: 700; color: var(--ink, #1e293b); }
.vc-head-q { font-size: 12px; color: var(--ink3, #94a3b8); }
.vc-src-tag {
  font-size: 10.5px; color: var(--ink3, #94a3b8);
  border: 1px solid var(--border); border-radius: 999px; padding: 1px 7px;
}
.vc-empty {
  padding: 16px 10px; text-align: center; font-size: 12.5px; color: var(--ink3, #94a3b8);
  border: 1px dashed var(--border); border-radius: var(--radius-sm);
}
.vc-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
@media (max-width: 560px) { .vc-grid { grid-template-columns: 1fr; } }

.vc-item {
  display: flex; flex-direction: column; overflow: hidden; text-decoration: none;
  background: var(--bg-white, #fff); border: 1px solid var(--border);
  border-radius: 10px;
  transition: transform 0.18s ease, box-shadow 0.18s ease, border-color 0.18s ease;
}
.vc-item:hover {
  transform: translateY(-2px); border-color: var(--primary-border, #c7d2fe);
  box-shadow: 0 6px 18px rgba(15, 23, 42, 0.09);
}
.vc-cover { position: relative; aspect-ratio: 16 / 9; background: var(--bg2, #f1f5f9); }
.vc-cover img { width: 100%; height: 100%; object-fit: cover; display: block; }
.vc-cover-fallback {
  width: 100%; height: 100%; display: flex; align-items: center; justify-content: center;
  color: var(--ink3, #94a3b8); font-size: 22px;
}
.vc-dur, .vc-play {
  position: absolute; bottom: 6px; padding: 2px 6px; border-radius: 5px;
  font-size: 10.5px; color: #fff; background: rgba(15, 23, 42, 0.78); line-height: 1.5;
}
.vc-dur { right: 6px; }
.vc-play { left: 6px; }
.vc-body { padding: 9px 11px 11px; display: flex; flex-direction: column; gap: 5px; flex: 1; }
.vc-title {
  font-size: 12.5px; font-weight: 600; color: var(--ink, #1e293b); line-height: 1.5;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
}
.vc-meta { display: flex; align-items: center; gap: 8px; }
.vc-up { font-size: 11px; color: var(--ink3, #94a3b8); }
.vc-why {
  font-size: 11px; color: var(--primary, #4f46e5); background: var(--primary-subtle, #eef2ff);
  border-radius: 6px; padding: 4px 7px; line-height: 1.5;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
}
</style>
