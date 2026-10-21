<template>
  <Teleport to="body">
    <transition name="rc-fade">
      <div v-if="open" class="rc-scrim" @click.self="emit('close')">
        <transition name="rc-slide">
          <aside v-if="open" class="rc-panel" role="dialog" aria-label="伴随资源" data-testid="tv3-companion-panel">
            <!-- 头部：品牌感 + 关闭 + 快捷操作 -->
            <header class="rc-head">
              <div class="rc-head__brand">
                <div class="rc-head__icon">📚</div>
                <div class="rc-head__text">
                  <div class="rc-head__title">伴随资源</div>
                  <div class="rc-head__sub">随叫随用 · 不离开当前工作</div>
                </div>
              </div>
              <div class="rc-head__actions">
                <button class="rc-head__btn" title="收藏夹" data-testid="tv3-companion-favorites" @click="activeView = activeView === 'favorites' ? 'main' : 'favorites'">
                  <span :class="{ 'is-on': activeView === 'favorites' }">★</span>
                </button>
                <button class="rc-head__btn" title="历史记录" data-testid="tv3-companion-history" @click="activeView = activeView === 'history' ? 'main' : 'history'">
                  <span :class="{ 'is-on': activeView === 'history' }">⏱</span>
                </button>
                <button class="rc-head__btn rc-head__btn--close" data-testid="tv3-companion-close" @click="emit('close')" aria-label="关闭">×</button>
              </div>
            </header>

            <!-- 上下文头：工具必须知道"我要把东西放到哪里"；没有就诚实说 -->
            <div class="rc-ctx" data-testid="tv3-companion-ctx">
              <div class="rc-ctx__indicator">
                <span class="rc-ctx__dot" />
                <span class="rc-ctx__status">上下文跟随中</span>
              </div>
              <template v-if="ctxInfo.topic">
                <div class="rc-ctx__topic">
                  {{ ctxInfo.topic }}
                  <span v-if="ctxInfo.cls" class="rc-ctx__class">· {{ ctxInfo.cls }}</span>
                </div>
                <div class="rc-ctx__target">
                  正在为：<b>{{ ctxInfo.target || '（未定位到具体片段）' }}</b> 寻找资源
                </div>
              </template>
              <template v-else>
                <div class="rc-ctx__target rc-ctx__target--empty">
                  尚未打开具体教案 / 课件 —— 选一个教学意图也可以找
                </div>
              </template>
            </div>

            <!-- 主视图 / 收藏 / 历史 -->
            <template v-if="activeView === 'main'">
              <!-- 第一层是教学意图，不是搜索框 -->
              <div class="rc-intents" data-testid="tv3-companion-intents">
                <div class="rc-intents__head">
                  <span class="rc-intents__label">为这里找什么？</span>
                  <button v-if="intent" class="rc-intents__clear" @click="intent = null">清除</button>
                </div>
                <div class="rc-intents__chips">
                  <button
                    v-for="it in COMPANION_INTENTS" :key="it" type="button"
                    class="rc-intent" :class="{ 'is-on': intent === it }"
                    :data-testid="`tv3-companion-intent-${it}`"
                    @click="onIntentClick(it)"
                  >
                    <span class="rc-intent__icon">{{ intentIcon(it) }}</span>
                    <span class="rc-intent__text">{{ it }}</span>
                  </button>
                </div>
              </div>

              <!-- 搜索框 + 视图切换 -->
              <div class="rc-toolbar">
                <div class="rc-search">
                  <span class="rc-search__icon">🔍</span>
                  <input v-model="query" class="rc-search__input" placeholder="筛选关键词（题号 / 来源 / 用途）" data-testid="tv3-companion-search">
                  <button v-if="query" class="rc-search__clear" @click="query = ''" aria-label="清除搜索">×</button>
                </div>
              </div>

              <!-- 来源分层 Tabs -->
              <div class="rc-tabs" role="tablist">
                <button
                  v-for="l in COMPANION_LAYERS" :key="l.key" type="button"
                  class="rc-tab" :class="{ 'is-on': layer === l.key }"
                  :data-testid="`tv3-companion-layer-${l.key}`"
                  @click="layer = l.key"
                  role="tab"
                  :aria-selected="layer === l.key"
                >
                  <span class="rc-tab__label">{{ l.label }}</span>
                  <span class="rc-tab__n">{{ layerCount(l.key) }}</span>
                </button>
              </div>

              <!-- 资源列表 -->
              <div class="rc-list" ref="listRef">
                <!-- Q-1：数据源未接入 ≠ 没搜到。必须分开说，否则教师会以为"是我关键词写错了" -->
                <div v-if="!sourceConnected" class="rc-empty" data-testid="tv3-companion-no-source">
                  <div class="rc-empty__icon">🔌</div>
                  <div class="rc-empty__title">资源台未接入</div>
                  <div class="rc-empty__desc">
                    还没有连接到你的资源库 / 校本题库 / 教材切片，因此<b>不展示任何候选</b>——不编造推荐。
                    接入后，这里会按教学意图列出可引用资源并说明推荐理由。
                  </div>
                </div>
                <div v-else-if="!intent && !stashCandidates.length" class="rc-empty">
                  <div class="rc-empty__icon">💡</div>
                  <div class="rc-empty__title">先选一个「教学意图」</div>
                  <div class="rc-empty__desc">候选资源会按它重排，并说明推荐理由</div>
                </div>
                <div v-else-if="!candidates.length" class="rc-empty">
                  <div class="rc-empty__icon">🔍</div>
                  <div class="rc-empty__title">暂无匹配的候选</div>
                  <div class="rc-empty__desc">换个来源层或放宽关键词试试</div>
                </div>

                <transition-group name="rc-card" tag="div" class="rc-cards">
                  <div v-for="c in candidates" :key="c.id" class="rc-card" :data-testid="`tv3-companion-cand-${c.id}`">
                    <div class="rc-card__head">
                      <span class="rc-kind" :data-kind="c.kind">{{ kindLabel(c.kind) }}</span>
                      <button class="rc-card__fav" :class="{ 'is-fav': favorites.has(c.id) }" @click="toggleFavorite(c)" :aria-label="favorites.has(c.id) ? '取消收藏' : '收藏'">
                        {{ favorites.has(c.id) ? '★' : '☆' }}
                      </button>
                    </div>

                    <!-- 视频缩略图（仅视频类型） -->
                    <div v-if="c.video && c.video.thumb" class="rc-video-thumb" @click="toggleExpand(c.id)">
                      <div class="rc-video-thumb__img" v-html="c.video.thumb" />
                      <div class="rc-video-thumb__overlay">
                        <div class="rc-video-thumb__play">
                          <svg viewBox="0 0 24 24" fill="currentColor" width="28" height="28"><path d="M8 5v14l11-7z"/></svg>
                        </div>
                      </div>
                      <span class="rc-video-thumb__duration">{{ c.video.duration || (c.video.start + '–' + c.video.end) }}</span>
                      <span v-if="c.video.grade" class="rc-video-thumb__grade">{{ c.video.grade }}</span>
                    </div>

                    <div class="rc-card__title">{{ c.title }}</div>
                    <div class="rc-card__meta">
                      <span class="rc-card__src">{{ c.source }}</span>
                      <span v-if="c.minutes" class="rc-card__time">⏱ {{ c.minutes }} 分钟</span>
                    </div>

                    <!-- 视频 UP 主 + 数据（仅视频类型） -->
                    <div v-if="c.video" class="rc-video-meta">
                      <div class="rc-video-meta__up">
                        <span class="rc-video-meta__avatar">{{ c.video.upAvatar || '🎬' }}</span>
                        <span class="rc-video-meta__upname">{{ c.video.up }}</span>
                      </div>
                      <div class="rc-video-meta__stats">
                        <span v-if="c.video.views" class="rc-video-meta__stat">▶ {{ c.video.views }}</span>
                        <span v-if="c.video.published" class="rc-video-meta__stat">{{ c.video.published }}</span>
                      </div>
                    </div>

                    <!-- 教学标签（仅视频类型） -->
                    <div v-if="c.video && c.video.teachingTags && c.video.teachingTags.length" class="rc-video-tags">
                      <span v-for="tag in c.video.teachingTags" :key="tag" class="rc-video-tag">{{ tag }}</span>
                    </div>
                    <div class="rc-card__why">
                      <span class="rc-card__why-label">推荐理由</span>
                      {{ c.why }}
                    </div>
                    <div v-if="c.note" class="rc-card__note">
                      <span class="rc-card__note-icon">⚠</span>
                      {{ c.note }}
                    </div>

                    <!-- 预览区域 -->
                    <transition name="rc-expand">
                      <div v-if="expanded === c.id" class="rc-preview" :data-testid="`tv3-companion-preview-${c.id}`">
                        <div v-if="c.preview?.latex" class="rc-preview__latex" v-html="renderLatex(c.preview.latex)" />
                        <div v-if="c.preview?.thumb || c.figure?.thumb" class="rc-preview__thumb" v-html="c.preview?.thumb || c.figure?.thumb" />
                        <div v-if="c.figure" class="rc-fraghint">
                          <span class="rc-fraghint__icon">📐</span>
                          结构化构造：插入后为图形素材；点「在数学绘图中继续编辑」可改构造再回插。
                        </div>
                        <!-- B 站内嵌播放器 -->
                        <div v-if="c.video" class="rc-video-player" data-testid="tv3-companion-video-card">
                          <div class="rc-video-player__frame">
                            <iframe
                              v-if="expanded === c.id && playerActive"
                              :src="'https://player.bilibili.com/player.html?bvid=&t=&high_quality=1&danmaku=0&autoplay=0'"
                              scrolling="no"
                              frameborder="no"
                              framespacing="0"
                              allowfullscreen="true"
                              class="rc-video-player__iframe"
                            />
                            <div v-else class="rc-video-player__placeholder" @click="playerActive = true; activeVideoId = c.id">
                              <div class="rc-video-player__thumb" v-html="c.video.thumb" />
                              <div class="rc-video-player__overlay">
                                <div class="rc-video-player__play-btn">
                                  <svg viewBox="0 0 24 24" fill="currentColor" width="36" height="36"><path d="M8 5v14l11-7z"/></svg>
                                </div>
                                <div class="rc-video-player__hint">点击播放 · B站官方播放器</div>
                              </div>
                            </div>
                          </div>
                          <div class="rc-video-player__info">
                            <div class="rc-video-player__time">
                              <span class="rc-video-player__time-label">建议观看</span>
                              <span class="rc-video-player__time-val">{{ c.video.start }} — {{ c.video.end }}（{{ c.video.duration }}）</span>
                            </div>
                            <div class="rc-video-player__source">
                              <span class="rc-video-player__source-dot" />
                              外部引用 · 官方站外播放器 · 不下载不转存
                            </div>
                          </div>
                        </div>

                        <!-- 教学切片设计 -->
                        <div v-if="c.video" class="rc-video-slice">
                          <div class="rc-video-slice__title">
                            <span class="rc-video-slice__icon">🎯</span>
                            教学切片设计
                          </div>
                          <div class="rc-video-slice__item rc-video-slice__item--pre">
                            <div class="rc-video-slice__label">播放前问</div>
                            <div class="rc-video-slice__content">{{ c.video.pre }}</div>
                          </div>
                          <div v-if="c.video.pause" class="rc-video-slice__item rc-video-slice__item--pause">
                            <div class="rc-video-slice__label">暂停点</div>
                            <div class="rc-video-slice__content">{{ c.video.pause }}</div>
                          </div>
                          <div class="rc-video-slice__item rc-video-slice__item--post">
                            <div class="rc-video-slice__label">播放后问</div>
                            <div class="rc-video-slice__content">{{ c.video.post }}</div>
                          </div>
                        </div>
                        <div v-if="c.kind === 'plan-fragment' || c.kind === 'deck-fragment'" class="rc-fraghint">
                          <span class="rc-fraghint__icon">📝</span>
                          对照说明：与当前版本相比请以预览内容为准取用，整份不会被覆盖（教师保留决定权）。
                        </div>
                      </div>
                    </transition>

                    <div class="rc-card__acts">
                      <button class="rc-btn rc-btn--ghost" @click="toggleExpand(c.id)">
                        {{ expanded === c.id ? '收起 ▴' : '预览 ▾' }}
                      </button>
                      <div class="rc-card__acts-right">
                        <button
                          v-if="c.kind === 'figure'" class="rc-btn rc-btn--ghost"
                          @click="continueInDraw(c)"
                        >继续编辑</button>
                        <button
                          v-if="c.kind === 'video' || c.kind === 'link'" class="rc-btn rc-btn--ghost"
                          @click="openOriginal(c)"
                        >原站打开</button>
                        <button
                          v-if="canInsert(c)" class="rc-btn rc-btn--primary"
                          :data-testid="`tv3-companion-insert-${c.id}`"
                          @click="insert(c)"
                        >{{ insertLabel(c) }}</button>
                      </div>
                    </div>
                  </div>
                </transition-group>
              </div>
            </template>

            <!-- 收藏夹视图 -->
            <template v-else-if="activeView === 'favorites'">
              <div class="rc-view-head">
                <div class="rc-view-head__title">★ 我的收藏</div>
                <div class="rc-view-head__count">{{ favoriteCandidates.length }} 项资源</div>
              </div>
              <div class="rc-list">
                <div v-if="!favoriteCandidates.length" class="rc-empty">
                  <div class="rc-empty__icon">⭐</div>
                  <div class="rc-empty__title">还没有收藏</div>
                  <div class="rc-empty__desc">遇到好资源点一下星号，随时能找到</div>
                </div>
                <div v-for="c in favoriteCandidates" :key="c.id" class="rc-card">
                  <div class="rc-card__head">
                    <span class="rc-kind" :data-kind="c.kind">{{ kindLabel(c.kind) }}</span>
                    <button class="rc-card__fav is-fav" @click="toggleFavorite(c)" aria-label="取消收藏">★</button>
                  </div>
                  <div class="rc-card__title">{{ c.title }}</div>
                  <div class="rc-card__meta">
                    <span class="rc-card__src">{{ c.source }}</span>
                  </div>
                  <div class="rc-card__why">{{ c.why }}</div>
                  <div class="rc-card__acts">
                    <button class="rc-btn rc-btn--ghost" @click="activeView = 'main'; intent = detectIntentFromKind(c.kind)">去推荐</button>
                    <div class="rc-card__acts-right">
                      <button v-if="canInsert(c)" class="rc-btn rc-btn--primary" @click="insert(c)">{{ insertLabel(c) }}</button>
                    </div>
                  </div>
                </div>
              </div>
            </template>

            <!-- 历史记录视图 -->
            <template v-else-if="activeView === 'history'">
              <div class="rc-view-head">
                <div class="rc-view-head__title">⏱ 最近插入</div>
                <div class="rc-view-head__count">{{ insertHistory.length }} 条记录</div>
                <button v-if="insertHistory.length" class="rc-view-head__clear" @click="insertHistory = []">清空</button>
              </div>
              <div class="rc-list">
                <div v-if="!insertHistory.length" class="rc-empty">
                  <div class="rc-empty__icon">📋</div>
                  <div class="rc-empty__title">暂无插入记录</div>
                  <div class="rc-empty__desc">插入过的资源会出现在这里，方便复用</div>
                </div>
                <div v-for="h in insertHistory" :key="h.id" class="rc-history-item">
                  <div class="rc-history-item__main">
                    <span class="rc-kind rc-kind--sm" :data-kind="h.kind">{{ kindLabel(h.kind) }}</span>
                    <span class="rc-history-item__title">{{ h.title }}</span>
                  </div>
                  <div class="rc-history-item__time">{{ h.time }}</div>
                </div>
              </div>
            </template>

            <!-- 插入回执：真实落稿 + 定位 + 撤销，不是一句话 Toast -->
            <transition name="rc-receipt">
              <footer v-if="companion.receipt" class="rc-receipt" data-testid="tv3-companion-receipt">
                <div class="rc-receipt__icon" :class="{ 'is-ok': companion.receipt.ok }">
                  {{ companion.receipt.ok ? '✓' : 'ℹ️' }}
                </div>
                <div class="rc-receipt__body">
                  <div class="rc-receipt__message">{{ companion.receipt.message }}</div>
                  <div v-if="companion.receipt.locationLabel" class="rc-receipt__location">位置：{{ companion.receipt.locationLabel }}</div>
                </div>
                <div class="rc-receipt__actions">
                  <button v-if="companion.receipt.locationLabel && companion.receipt.ok" class="rc-btn rc-btn--sm rc-btn--ghost" @click="locate">定位</button>
                  <button v-if="companion.receipt.undoLabel" class="rc-btn rc-btn--sm rc-btn--ghost" data-testid="tv3-companion-undo" @click="onUndo()">{{ companion.receipt.undoLabel }}</button>
                </div>
              </footer>
            </transition>
          </aside>
        </transition>
      </div>
    </transition>
  </Teleport>
</template>

<script setup lang="ts">
/**
 * ResourceCompanionPanel —— 伴随资源台（C2，IFC-C2-a）
 * 【2026 升级】参考飞书 AI 侧边栏 + Notion 数据库 + 微软 Copilot 面板设计
 * - 视觉：品牌头、卡片精致化、分类标签体系化
 * - 交互：意图微动效、预览展开动画、收藏/历史双视图
 * - 功能：收藏夹、插入历史、快捷操作
 *
 * 审计/方案依据：《教师端V3-伴随式资源库与B站教学素材创新方案》§5-§8、§11
 *  - 覆盖式右侧抽屉（不永久压缩主编辑区）；上下文头说清"正在为哪里找"
 *  - 第一层是教学意图（镜头），第二层才是小搜索框；来源分四层；候选 3-5 条
 *  - 插入经 tv3-companion-insert 事件总线由当前工作页真实落稿，回执带定位/撤销
 *  - 外部资源只做引用卡（B站官方站外播放器语义），不下载不转存不伪装自有
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { renderLatex } from '@/components/mathx/latex'
import { useTv3Context } from '@/stores/teacherContext'
import { useCompanion, stashFigure, openTool, setDrawReopen, setReceipt, undoLast } from '@/stores/companion'
import { COMPANION_INTENTS, COMPANION_LAYERS, buildCandidates, companionDemoEnabled, detectIntent, type CompanionCandidate, type CompanionIntent, type CompanionLayer } from '@/pages/teacher-v3/companionData'

const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{ (e: 'close'): void }>()

/** Q-1：真实资源库接入前，面板用"未接入"态而非空候选列表（两者含义不同）。 */
const sourceConnected = companionDemoEnabled()

const route = useRoute()
const ctx = useTv3Context()
const companion = useCompanion()

const intent = ref<CompanionIntent | null>(null)
const layer = ref<CompanionLayer>('mine')
const query = ref('')
const expanded = ref('')
const listRef = ref<HTMLElement | null>(null)

/* 视频播放器状态：点击播放按钮后才加载 iframe，避免一次性加载过多 */
const playerActive = ref(false)
const activeVideoId = ref('')
function toggleExpand(id: string) {
  expanded.value = expanded.value === id ? '' : id
  // 收起时重置播放器状态
  if (expanded.value !== id) {
    playerActive.value = false
    activeVideoId.value = ''
  }
}

/* ---------- 新功能：收藏夹 & 历史记录 ---------- */
const activeView = ref<'main' | 'favorites' | 'history'>('main')
const favorites = ref<Set<string>>(new Set())
const insertHistory = ref<Array<{ id: string; title: string; kind: string; time: string }>>([])

function toggleFavorite(c: CompanionCandidate) {
  const next = new Set(favorites.value)
  if (next.has(c.id)) {
    next.delete(c.id)
  } else {
    next.add(c.id)
  }
  favorites.value = next
}

const favoriteCandidates = computed(() => {
  const all: CompanionCandidate[] = []
  for (const l of COMPANION_LAYERS) {
    for (const it of COMPANION_INTENTS) {
      all.push(...buildCandidates(it, l.key, ''))
    }
  }
  // 去重 + 只保留收藏的
  const seen = new Set<string>()
  return all.filter((c) => {
    if (seen.has(c.id)) return false
    seen.add(c.id)
    return favorites.value.has(c.id)
  })
})

function addToHistory(c: CompanionCandidate) {
  const now = new Date()
  const time = `${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}`
  insertHistory.value = [
    { id: c.id + Date.now(), title: c.title, kind: c.kind, time },
    ...insertHistory.value.slice(0, 19), // 最多 20 条
  ]
}

function detectIntentFromKind(kind: string): CompanionIntent | null {
  // 根据资源类型猜测一个意图，用于从收藏页跳回推荐
  if (kind === 'question') return '例题精讲'
  if (kind === 'figure') return '动态演示'
  if (kind === 'video') return '概念发生'
  return COMPANION_INTENTS[0] || null
}

/* ---------- 上下文 ---------- */
const PAGE_KEYS: Record<string, string> = {
  '/teacher-v3/today': 'today', '/teacher-v3/prep': 'prep', '/teacher-v3/slides': 'slides',
  '/teacher-v3/bank': 'bank', '/teacher-v3/quiz': 'quiz', '/teacher-v3/assign': 'assign',
  '/teacher-v3/classroom': 'classroom', '/teacher-v3/insights': 'insights', '/teacher-v3/resources': 'resources',
}
const pageKey = computed(() => PAGE_KEYS[route.path] || 'other')

const ctxInfo = computed(() => {
  const selectionSummary = ctx.selection?.summary || ''
  let sectionName = ''
  const m = selectionSummary.match(/环节「(.+?)」/)
  if (m) sectionName = m[1]
  const topic = ctx.deck_title || ctx.topic || ''
  const cls = ctx.class_name || ''
  let target = ''
  if (pageKey.value === 'prep') target = sectionName ? `环节「${sectionName}」` : ''
  else if (pageKey.value === 'slides') {
    target = ctx.slide_index != null ? `第 ${ctx.slide_index + 1} 页${selectionSummary && !sectionName ? ' · ' + selectionSummary : ''}` : (ctx.deck_id ? '当前打开的课件' : '')
  } else if (pageKey.value === 'bank') target = ctx.extra || ''
  else if (pageKey.value === 'quiz') target = ctx.extra || ''
  return { topic, cls, target, sectionName, selectionSummary }
})

/** 意图自动镜头：上下文里能猜就猜，猜不中让教师手选（不假装知道） */
watch(() => props.open, (v) => {
  if (!v) return
  intent.value = detectIntent({ selectionSummary: ctxInfo.value.selectionSummary, sectionName: ctxInfo.value.sectionName })
  query.value = ''
  expanded.value = ''
  activeView.value = 'main'
})

/* ---------- 意图图标 ---------- */
function intentIcon(it: string): string {
  const map: Record<string, string> = {
    '概念发生': '💡',
    '例题精讲': '📖',
    '变式迁移': '🔄',
    '理解检查': '✅',
    '板书素材': '📋',
    '动态演示': '🎬',
  }
  return map[it] || '📌'
}

function onIntentClick(it: CompanionIntent) {
  intent.value = intent.value === it ? null : it
  expanded.value = ''
}

/* ---------- 暂存图形 → 「我的」层置顶候选 ---------- */
const stashCandidates = computed<CompanionCandidate[]>(() => companion.stash.map((s) => ({
  id: `stash-${s.id}`,
  kind: 'figure',
  layer: 'mine',
  title: `${s.name}${s.used ? ' · 已用于课件' : ' · 未插入'}`,
  source: `暂存图形 · ${s.created_at}`,
  why: '画一半关掉的图形：可继续编辑构造，或直接插入当前片段',
  minutes: 2,
  becomes: '插入为图形素材 / 绘图台继续编辑',
  intents: [...COMPANION_INTENTS],
  figure: { kind: s.kind, thumb: s.thumb, records: s.records as any, expr: s.expr },
})))

const candidates = computed(() => {
  const pool = intent.value ? buildCandidates(intent.value, layer.value, query.value) : []
  return layer.value === 'mine' ? [...stashCandidates.value, ...pool] : pool
})
const layerCount = (key: CompanionLayer) => {
  const base = intent.value ? buildCandidates(intent.value, key, query.value).length : 0
  return key === 'mine' ? base + stashCandidates.value.length : base
}

const KIND_LABEL: Record<string, string> = { question: '题', 'plan-fragment': '教案片段', 'deck-fragment': '课件片段', figure: '图形', video: '视频卡', link: '外链' }
const kindLabel = (k: string) => KIND_LABEL[k] || k

/* ---------- 可插入目标 ---------- */
function canInsert(c: CompanionCandidate): boolean {
  if (c.kind === 'question') return pageKey.value === 'prep'
  if (c.kind === 'figure') return ['prep', 'slides'].includes(pageKey.value)
  return ['prep', 'slides'].includes(pageKey.value)
}
function insertLabel(c: CompanionCandidate): string {
  if (c.kind === 'question') return pageKey.value === 'quiz' ? '加入试卷' : '插入当前片段'
  if (c.kind === 'figure') return pageKey.value === 'slides' ? '插入当前页' : '插入当前片段'
  if (c.kind === 'video') return '挂到当前片段'
  if (c.kind === 'link') return '引用来源'
  return '只取这一段'
}

let insertSeq = 0
let lastReqId = ''
let lastCandidate: CompanionCandidate | null = null

function insert(c: CompanionCandidate) {
  lastCandidate = c
  const reqId = `res-${++insertSeq}`
  lastReqId = reqId
  addToHistory(c)
  window.dispatchEvent(new CustomEvent('tv3-companion-insert', {
    detail: { reqId, kind: c.kind, candidate: c, target: { sectionName: ctxInfo.value.sectionName, targetLabel: ctxInfo.value.target, page: pageKey.value } },
  }))
}



let handledSeq = ''
function onInserted(ev: Event) {
  const d = (ev as CustomEvent).detail as { reqId: string; handled: boolean } | undefined
  if (!d || d.reqId !== lastReqId || d.reqId === handledSeq) return
  handledSeq = d.reqId
  if (d.handled) return
  const c = lastCandidate
  if (c?.kind === 'figure' && c.figure) {
    stashFigure({ name: c.title, kind: c.figure.kind, thumb: c.figure.thumb, records: c.figure.records, expr: c.figure.expr })
    setReceipt({ ok: false, message: '当前页面暂不支持直接插入图形：已存入「暂存图形」（可在绘图台 / 本面板「我的」继续编辑）', locationLabel: '暂存区' })
  } else {
    setReceipt({ ok: false, message: '当前页面暂不支持直接插入该类型：请到备课 / 课件 / 组卷页使用，此处未做任何改动' })
  }
}

function continueInDraw(c: CompanionCandidate) {
  if (c.figure) setDrawReopen({ mode: c.figure.kind === 'fx' ? 'fx' : 'free', records: (c.figure.records as any) || [], expr: c.figure.expr || '', elementId: '' })
  openTool('draw')
}
function openOriginal(c: CompanionCandidate) {
  if (c.url) window.open(c.url, '_blank', 'noopener')
}
function locate() {
  window.dispatchEvent(new CustomEvent('tv3-companion-locate'))
}
function onUndo() {
  undoLast()
}

function onKeydown(ev: KeyboardEvent) {
  if (!props.open) return
  if (ev.key === 'Escape') emit('close')
}

onMounted(() => {
  window.addEventListener('tv3-companion-inserted', onInserted as EventListener)
  window.addEventListener('keydown', onKeydown)
})
onBeforeUnmount(() => {
  window.removeEventListener('tv3-companion-inserted', onInserted as EventListener)
  window.removeEventListener('keydown', onKeydown)
})
</script>

<style scoped>
/* ==========================================================================
   遮罩与面板容器
   ========================================================================== */
.rc-scrim {
  position: fixed;
  inset: 0;
  z-index: 940;
  background: rgba(7, 26, 50, 0.22);
  backdrop-filter: blur(2px);
}
.rc-fade-enter-active,
.rc-fade-leave-active {
  transition: opacity .25s ease;
}
.rc-fade-enter-from,
.rc-fade-leave-to {
  opacity: 0;
}

.rc-panel {
  position: fixed;
  top: 0;
  right: 0;
  bottom: 0;
  z-index: 945;
  width: 420px;
  max-width: 94vw;
  background: #fff;
  border-left: 1px solid var(--tv3-line2);
  box-shadow: -18px 0 48px rgba(10, 30, 58, 0.22);
  display: flex;
  flex-direction: column;
}
.rc-slide-enter-active,
.rc-slide-leave-active {
  transition: transform .3s cubic-bezier(0.34, 1.56, 0.64, 1);
}
.rc-slide-enter-from,
.rc-slide-leave-to {
  transform: translateX(100%);
}

/* ==========================================================================
   头部（品牌感升级）
   ========================================================================== */
.rc-head {
  display: flex;
  align-items: center;
  padding: 14px 16px 12px;
  border-bottom: 1px solid var(--tv3-line2);
  background: linear-gradient(180deg, #fbfcfe 0%, #fff 100%);
}
.rc-head__brand {
  display: flex;
  align-items: center;
  gap: 10px;
  flex: 1;
  min-width: 0;
}
.rc-head__icon {
  width: 38px;
  height: 38px;
  border-radius: 10px;
  background: linear-gradient(135deg, #eaf1fb, #d8e6f8);
  display: grid;
  place-items: center;
  font-size: 18px;
  flex-shrink: 0;
}
.rc-head__text {
  min-width: 0;
}
.rc-head__title {
  font-size: 15px;
  font-weight: 800;
  color: var(--tv3-ink);
  line-height: 1.2;
}
.rc-head__sub {
  font-size: 11px;
  color: var(--tv3-ink3);
  margin-top: 2px;
}
.rc-head__actions {
  display: flex;
  align-items: center;
  gap: 4px;
}
.rc-head__btn {
  width: 32px;
  height: 32px;
  border: none;
  background: transparent;
  border-radius: 8px;
  cursor: pointer;
  font-size: 15px;
  color: var(--tv3-ink3);
  display: grid;
  place-items: center;
  transition: all .15s ease;
}
.rc-head__btn:hover {
  background: var(--tv3-bg2);
  color: var(--tv3-ink2);
}
.rc-head__btn .is-on {
  color: var(--tv3-gold);
}
.rc-head__btn--close {
  font-size: 18px;
  font-weight: 600;
}
.rc-head__btn--close:hover {
  background: var(--tv3-rose-soft);
  color: var(--tv3-rose);
}

/* ==========================================================================
   上下文头
   ========================================================================== */
.rc-ctx {
  padding: 10px 16px 12px;
  border-bottom: 1px dashed var(--tv3-line);
  background: linear-gradient(180deg, #f8fafd 0%, #fff 100%);
}
.rc-ctx__indicator {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 6px;
}
.rc-ctx__dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--tv3-teal, #0e9488);
  box-shadow: 0 0 0 3px rgba(14, 148, 136, 0.15);
  animation: rc-dot-pulse 2s ease-in-out infinite;
}
@keyframes rc-dot-pulse {
  0%, 100% { box-shadow: 0 0 0 3px rgba(14, 148, 136, 0.15); }
  50% { box-shadow: 0 0 0 5px rgba(14, 148, 136, 0.25); }
}
.rc-ctx__status {
  font-size: 10.5px;
  font-weight: 600;
  color: var(--tv3-teal);
  text-transform: uppercase;
  letter-spacing: 0.5px;
}
.rc-ctx__topic {
  font-size: 13.5px;
  font-weight: 700;
  color: var(--tv3-ink);
  line-height: 1.4;
}
.rc-ctx__class {
  font-weight: 400;
  color: var(--tv3-ink3);
  font-size: 12px;
}
.rc-ctx__target {
  font-size: 12px;
  color: var(--tv3-ink2);
  margin-top: 4px;
  line-height: 1.6;
}
.rc-ctx__target b {
  color: var(--tv3-navy);
  font-weight: 600;
}
.rc-ctx__target--empty {
  color: var(--tv3-ink3);
  font-style: italic;
}

/* ==========================================================================
   教学意图区
   ========================================================================== */
.rc-intents {
  padding: 12px 16px 8px;
  border-bottom: 1px solid var(--tv3-line2);
}
.rc-intents__head {
  display: flex;
  align-items: center;
  margin-bottom: 8px;
}
.rc-intents__label {
  font-size: 11px;
  color: var(--tv3-ink3);
  font-weight: 700;
  letter-spacing: 0.3px;
}
.rc-intents__clear {
  margin-left: auto;
  border: none;
  background: none;
  font-size: 11px;
  color: var(--tv3-ink3);
  cursor: pointer;
  padding: 2px 6px;
  border-radius: 4px;
}
.rc-intents__clear:hover {
  background: var(--tv3-bg2);
  color: var(--tv3-ink2);
}
.rc-intents__chips {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}
.rc-intent {
  display: flex;
  align-items: center;
  gap: 5px;
  border: 1px solid var(--tv3-line);
  background: #fff;
  border-radius: 999px;
  padding: 5px 12px;
  font-size: 12px;
  cursor: pointer;
  color: var(--tv3-ink2);
  transition: all .18s ease;
}
.rc-intent:hover {
  border-color: var(--tv3-primary-border);
  background: var(--tv3-primary-soft);
  color: var(--tv3-navy);
  transform: translateY(-1px);
}
.rc-intent.is-on {
  border-color: var(--tv3-navy);
  background: linear-gradient(135deg, #4f46e5, #1a5aa8);
  color: #fff;
  font-weight: 600;
  box-shadow: 0 3px 10px rgba(79, 70, 229, 0.25);
}
.rc-intent__icon {
  font-size: 13px;
}

/* ==========================================================================
   工具栏（搜索）
   ========================================================================== */
.rc-toolbar {
  padding: 10px 16px;
  border-bottom: 1px solid var(--tv3-line2);
}
.rc-search {
  position: relative;
  display: flex;
  align-items: center;
}
.rc-search__icon {
  position: absolute;
  left: 12px;
  font-size: 13px;
  color: var(--tv3-ink3);
  pointer-events: none;
}
.rc-search__input {
  width: 100%;
  border: 1px solid var(--tv3-line);
  border-radius: 10px;
  padding: 8px 32px 8px 34px;
  font-size: 12.5px;
  outline: none;
  background: #fbfcfe;
  transition: all .15s ease;
}
.rc-search__input:focus {
  border-color: var(--tv3-navy);
  background: #fff;
  box-shadow: 0 0 0 3px rgba(79, 70, 229, 0.08);
}
.rc-search__clear {
  position: absolute;
  right: 8px;
  width: 20px;
  height: 20px;
  border: none;
  background: var(--tv3-ink3);
  color: #fff;
  border-radius: 50%;
  font-size: 12px;
  cursor: pointer;
  display: grid;
  place-items: center;
  opacity: 0.6;
}
.rc-search__clear:hover {
  opacity: 1;
}

/* ==========================================================================
   来源分层 Tabs
   ========================================================================== */
.rc-tabs {
  display: flex;
  gap: 4px;
  padding: 0 16px 10px;
  border-bottom: 1px solid var(--tv3-line);
}
.rc-tab {
  flex: 1;
  border: 1px solid var(--tv3-line);
  background: #fff;
  border-radius: 8px;
  padding: 6px 4px;
  font-size: 11.5px;
  cursor: pointer;
  color: var(--tv3-ink2);
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
  transition: all .18s ease;
}
.rc-tab:hover {
  border-color: var(--tv3-primary-border);
  background: var(--tv3-primary-soft);
}
.rc-tab.is-on {
  border-color: var(--tv3-gold);
  background: linear-gradient(180deg, var(--tv3-gold-soft), #fff);
  color: var(--tv3-ink);
  font-weight: 700;
  box-shadow: 0 2px 8px rgba(6, 182, 212, 0.12);
}
.rc-tab__label {
  font-size: 11.5px;
}
.rc-tab__n {
  font-family: var(--tv3-font-num);
  font-size: 10px;
  color: var(--tv3-ink3);
  background: var(--tv3-bg2);
  padding: 1px 6px;
  border-radius: 999px;
}
.rc-tab.is-on .rc-tab__n {
  background: var(--tv3-gold);
  color: #fff;
}

/* ==========================================================================
   视图头（收藏/历史）
   ========================================================================== */
.rc-view-head {
  display: flex;
  align-items: center;
  padding: 12px 16px;
  border-bottom: 1px solid var(--tv3-line2);
  gap: 8px;
}
.rc-view-head__title {
  font-size: 14px;
  font-weight: 700;
  color: var(--tv3-ink);
}
.rc-view-head__count {
  font-size: 11px;
  color: var(--tv3-ink3);
  background: var(--tv3-bg2);
  padding: 2px 8px;
  border-radius: 999px;
}
.rc-view-head__clear {
  margin-left: auto;
  border: none;
  background: none;
  font-size: 11px;
  color: var(--tv3-rose);
  cursor: pointer;
  padding: 4px 8px;
  border-radius: 6px;
}
.rc-view-head__clear:hover {
  background: var(--tv3-rose-soft);
}

/* ==========================================================================
   资源列表
   ========================================================================== */
.rc-list {
  flex: 1;
  overflow-y: auto;
  padding: 12px 16px;
}
.rc-cards {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

/* 空状态 */
.rc-empty {
  text-align: center;
  padding: 36px 20px;
  color: var(--tv3-ink3);
}
.rc-empty__icon {
  font-size: 36px;
  margin-bottom: 10px;
  opacity: 0.7;
}
.rc-empty__title {
  font-size: 14px;
  font-weight: 600;
  color: var(--tv3-ink2);
  margin-bottom: 4px;
}
.rc-empty__desc {
  font-size: 12px;
  line-height: 1.7;
}

/* ==========================================================================
   资源卡片（升级视觉）
   ========================================================================== */
.rc-card {
  border: 1px solid var(--tv3-line);
  border-radius: 12px;
  padding: 12px 14px;
  background: #fff;
  transition: all .2s ease;
}
.rc-card:hover {
  border-color: var(--tv3-primary-border);
  box-shadow: 0 4px 16px rgba(79, 70, 229, 0.1);
  transform: translateY(-1px);
}
.rc-card__head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}
.rc-kind {
  flex-shrink: 0;
  font-size: 10px;
  font-weight: 700;
  padding: 2px 8px;
  border-radius: 999px;
  border: 1px solid var(--tv3-line);
  color: var(--tv3-ink3);
  letter-spacing: 0.3px;
}
.rc-kind--sm {
  font-size: 9.5px;
  padding: 1px 6px;
}
.rc-kind[data-kind='question'] {
  color: #4f46e5;
  border-color: #a5b4fc;
  background: #eef4fb;
}
.rc-kind[data-kind='video'] {
  color: #b1382c;
  border-color: #eec7c2;
  background: #fdf1ef;
}
.rc-kind[data-kind='figure'] {
  color: #6d28d9;
  border-color: #d8c9f5;
  background: #f6f2fd;
}
.rc-kind[data-kind='plan-fragment'] {
  color: #0e9488;
  border-color: #b9e0dc;
  background: #e6f5f3;
}
.rc-kind[data-kind='deck-fragment'] {
  color: #b45309;
  border-color: #f3ddb4;
  background: #fdf3e3;
}
.rc-card__fav {
  margin-left: auto;
  border: none;
  background: none;
  font-size: 15px;
  cursor: pointer;
  color: var(--tv3-ink3);
  padding: 2px 4px;
  transition: all .15s ease;
}
.rc-card__fav:hover {
  color: var(--tv3-gold);
  transform: scale(1.15);
}
.rc-card__fav.is-fav {
  color: var(--tv3-gold);
}
.rc-card__title {
  font-size: 13px;
  font-weight: 600;
  color: var(--tv3-ink);
  line-height: 1.4;
}
.rc-card__meta {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 4px;
  font-size: 11px;
  color: var(--tv3-ink3);
}
.rc-card__src {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.rc-card__time {
  flex-shrink: 0;
  font-family: var(--tv3-font-num);
}
.rc-card__why {
  font-size: 12px;
  color: var(--tv3-ink2);
  margin-top: 6px;
  line-height: 1.6;
}
.rc-card__why-label {
  font-weight: 600;
  color: var(--tv3-navy);
  margin-right: 4px;
}
.rc-card__note {
  display: flex;
  gap: 6px;
  font-size: 11px;
  color: #0e7490;
  background: var(--tv3-gold-soft, #fdf8ec);
  border-radius: 8px;
  padding: 6px 10px;
  margin-top: 8px;
  line-height: 1.5;
}
.rc-card__note-icon {
  flex-shrink: 0;
}

/* ==========================================================================
   预览区域
   ========================================================================== */
.rc-expand-enter-active,
.rc-expand-leave-active {
  transition: all .25s ease;
  overflow: hidden;
}
.rc-expand-enter-from,
.rc-expand-leave-to {
  opacity: 0;
  max-height: 0;
  margin-top: 0;
  padding-top: 0;
  padding-bottom: 0;
}
.rc-expand-enter-to,
.rc-expand-leave-from {
  max-height: 400px;
}

.rc-preview {
  margin-top: 10px;
  border: 1px dashed var(--tv3-line);
  border-radius: 10px;
  padding: 10px 12px;
  background: linear-gradient(180deg, #fbfcfe, #fff);
}
.rc-preview__latex {
  font-size: 14px;
  overflow-x: auto;
}
.rc-preview__thumb {
  text-align: center;
}
.rc-preview__thumb :deep(svg) {
  max-width: 100%;
  height: 80px;
}
.rc-fraghint {
  display: flex;
  gap: 6px;
  font-size: 11px;
  color: var(--tv3-ink3);
  margin-top: 8px;
  line-height: 1.5;
}
.rc-fraghint__icon {
  flex-shrink: 0;
}

/* ==========================================================================
   视频缩略图（卡片主体）
   ========================================================================== */
.rc-video-thumb {
  position: relative;
  border-radius: 10px;
  overflow: hidden;
  margin: 8px 0 10px;
  cursor: pointer;
  aspect-ratio: 16 / 9;
  background: #0f172a;
  transition: transform .2s ease, box-shadow .2s ease;
}
.rc-video-thumb:hover {
  transform: translateY(-1px);
  box-shadow: 0 6px 20px rgba(0, 0, 0, 0.2);
}
.rc-video-thumb:hover .rc-video-thumb__play {
  transform: scale(1.15);
}
.rc-video-thumb__img {
  width: 100%;
  height: 100%;
}
.rc-video-thumb__img :deep(svg) {
  width: 100%;
  height: 100%;
  display: block;
}
.rc-video-thumb__overlay {
  position: absolute;
  inset: 0;
  background: linear-gradient(180deg, rgba(0,0,0,0.1) 0%, rgba(0,0,0,0.4) 100%);
  display: grid;
  place-items: center;
  transition: background .2s ease;
}
.rc-video-thumb:hover .rc-video-thumb__overlay {
  background: linear-gradient(180deg, rgba(0,0,0,0.2) 0%, rgba(0,0,0,0.55) 100%);
}
.rc-video-thumb__play {
  width: 52px;
  height: 52px;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.95);
  color: #0f172a;
  display: grid;
  place-items: center;
  transition: transform .25s cubic-bezier(0.34, 1.56, 0.64, 1);
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.3);
}
.rc-video-thumb__play svg {
  margin-left: 3px; /* 视觉居中修正 */
}
.rc-video-thumb__duration {
  position: absolute;
  right: 8px;
  bottom: 8px;
  background: rgba(0, 0, 0, 0.78);
  color: #fff;
  font-size: 11px;
  font-weight: 600;
  padding: 2px 7px;
  border-radius: 4px;
  font-family: var(--tv3-font-num);
}
.rc-video-thumb__grade {
  position: absolute;
  left: 8px;
  top: 8px;
  background: rgba(245, 158, 11, 0.92);
  color: #1c2b1a;
  font-size: 10.5px;
  font-weight: 700;
  padding: 2px 8px;
  border-radius: 999px;
}

/* ==========================================================================
   视频 Meta 信息（UP主 + 数据）
   ========================================================================== */
.rc-video-meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 6px;
  padding: 6px 10px;
  background: var(--tv3-bg2);
  border-radius: 8px;
}
.rc-video-meta__up {
  display: flex;
  align-items: center;
  gap: 6px;
}
.rc-video-meta__avatar {
  width: 22px;
  height: 22px;
  border-radius: 50%;
  background: linear-gradient(135deg, #fb923c, #f59e0b);
  display: grid;
  place-items: center;
  font-size: 12px;
}
.rc-video-meta__upname {
  font-size: 11.5px;
  color: var(--tv3-ink2);
  font-weight: 500;
}
.rc-video-meta__stats {
  display: flex;
  gap: 8px;
}
.rc-video-meta__stat {
  font-size: 10.5px;
  color: var(--tv3-ink3);
  font-family: var(--tv3-font-num);
}

/* ==========================================================================
   视频教学标签
   ========================================================================== */
.rc-video-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
  margin-top: 8px;
}
.rc-video-tag {
  font-size: 10.5px;
  padding: 2px 8px;
  border-radius: 999px;
  background: var(--tv3-teal-soft, #e6f5f3);
  color: var(--tv3-teal, #0e9488);
  font-weight: 500;
  border: 1px solid var(--tv3-teal-border, #b9e0dc);
}

/* ==========================================================================
   B 站内嵌播放器
   ========================================================================== */
.rc-video-player {
  margin-top: 10px;
  border-radius: 12px;
  overflow: hidden;
  background: #0f172a;
  border: 1px solid #1e293b;
}
.rc-video-player__frame {
  position: relative;
  aspect-ratio: 16 / 9;
  width: 100%;
  background: #000;
}
.rc-video-player__iframe {
  width: 100%;
  height: 100%;
  border: none;
  display: block;
}
.rc-video-player__placeholder {
  position: absolute;
  inset: 0;
  cursor: pointer;
}
.rc-video-player__thumb {
  width: 100%;
  height: 100%;
  opacity: 0.85;
}
.rc-video-player__thumb :deep(svg) {
  width: 100%;
  height: 100%;
  display: block;
}
.rc-video-player__overlay {
  position: absolute;
  inset: 0;
  background: linear-gradient(180deg, rgba(0,0,0,0.15) 0%, rgba(0,0,0,0.6) 100%);
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 10px;
  transition: background .2s ease;
}
.rc-video-player__placeholder:hover .rc-video-player__overlay {
  background: linear-gradient(180deg, rgba(0,0,0,0.25) 0%, rgba(0,0,0,0.7) 100%);
}
.rc-video-player__play-btn {
  width: 64px;
  height: 64px;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.95);
  color: #0f172a;
  display: grid;
  place-items: center;
  transition: transform .25s cubic-bezier(0.34, 1.56, 0.64, 1);
  box-shadow: 0 6px 24px rgba(0, 0, 0, 0.4);
}
.rc-video-player__play-btn svg {
  margin-left: 4px;
}
.rc-video-player__placeholder:hover .rc-video-player__play-btn {
  transform: scale(1.1);
}
.rc-video-player__hint {
  font-size: 12px;
  color: rgba(255, 255, 255, 0.85);
  font-weight: 500;
}
.rc-video-player__info {
  padding: 10px 12px;
  background: linear-gradient(180deg, #1e293b, #0f172a);
}
.rc-video-player__time {
  display: flex;
  align-items: center;
  gap: 8px;
}
.rc-video-player__time-label {
  font-size: 10.5px;
  color: #94a3b8;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  font-weight: 600;
}
.rc-video-player__time-val {
  font-size: 12.5px;
  color: #fbbf24;
  font-weight: 700;
  font-family: var(--tv3-font-num);
}
.rc-video-player__source {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 10.5px;
  color: #64748b;
  margin-top: 4px;
}
.rc-video-player__source-dot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: #22c55e;
}

/* ==========================================================================
   教学切片设计
   ========================================================================== */
.rc-video-slice {
  margin-top: 12px;
  border: 1px solid var(--tv3-line);
  border-radius: 10px;
  overflow: hidden;
  background: #fff;
}
.rc-video-slice__title {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 12px;
  background: linear-gradient(135deg, #fef3c7, #fde68a);
  font-size: 12px;
  font-weight: 700;
  color: #92400e;
  border-bottom: 1px solid #fcd34d;
}
.rc-video-slice__icon {
  font-size: 14px;
}
.rc-video-slice__item {
  padding: 8px 12px;
  border-bottom: 1px solid var(--tv3-line2);
}
.rc-video-slice__item:last-child {
  border-bottom: none;
}
.rc-video-slice__label {
  font-size: 10.5px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  margin-bottom: 4px;
}
.rc-video-slice__item--pre .rc-video-slice__label {
  color: #0f766e;
}
.rc-video-slice__item--pause .rc-video-slice__label {
  color: #b45309;
}
.rc-video-slice__item--post .rc-video-slice__label {
  color: #7c3aed;
}
.rc-video-slice__content {
  font-size: 12px;
  color: var(--tv3-ink2);
  line-height: 1.6;
}

/* ==========================================================================
   卡片操作区
   ========================================================================== */
.rc-card__acts {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px solid var(--tv3-line2);
}
.rc-card__acts-right {
  margin-left: auto;
  display: flex;
  gap: 6px;
}

/* 按钮体系 */
.rc-btn {
  border: 1px solid var(--tv3-line);
  background: #fff;
  border-radius: 8px;
  padding: 5px 12px;
  font-size: 11.5px;
  cursor: pointer;
  color: var(--tv3-ink2);
  transition: all .15s ease;
  font-weight: 500;
}
.rc-btn:hover {
  border-color: var(--tv3-navy);
  color: var(--tv3-navy);
  background: var(--tv3-primary-soft);
}
.rc-btn--sm {
  padding: 4px 10px;
  font-size: 11px;
}
.rc-btn--ghost {
  background: transparent;
  border-color: var(--tv3-line);
}
.rc-btn--primary {
  background: linear-gradient(135deg, #4f46e5, #1a5aa8);
  border-color: #4f46e5;
  color: #fff;
  font-weight: 600;
  box-shadow: 0 2px 8px rgba(79, 70, 229, 0.2);
}
.rc-btn--primary:hover {
  background: linear-gradient(135deg, #1a5aa8, #4f46e5);
  color: #fff;
  box-shadow: 0 4px 12px rgba(79, 70, 229, 0.3);
  transform: translateY(-1px);
}
.rc-btn--primary:active {
  transform: translateY(0);
}

/* ==========================================================================
   历史记录项
   ========================================================================== */
.rc-history-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border: 1px solid var(--tv3-line);
  border-radius: 10px;
  background: #fff;
  margin-bottom: 8px;
}
.rc-history-item__main {
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 8px;
}
.rc-history-item__title {
  font-size: 12.5px;
  color: var(--tv3-ink2);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.rc-history-item__time {
  font-size: 11px;
  color: var(--tv3-ink3);
  font-family: var(--tv3-font-num);
  flex-shrink: 0;
}

/* ==========================================================================
   卡片列表动画
   ========================================================================== */
.rc-card-enter-active,
.rc-card-leave-active {
  transition: all .25s ease;
}
.rc-card-enter-from {
  opacity: 0;
  transform: translateY(8px);
}
.rc-card-leave-to {
  opacity: 0;
  transform: translateX(20px);
}
.rc-card-move {
  transition: transform .3s ease;
}

/* ==========================================================================
   插入回执（升级）
   ========================================================================== */
.rc-receipt-enter-active,
.rc-receipt-leave-active {
  transition: all .3s ease;
}
.rc-receipt-enter-from,
.rc-receipt-leave-to {
  opacity: 0;
  transform: translateY(100%);
}

.rc-receipt {
  border-top: 1px solid var(--tv3-line2);
  padding: 12px 16px;
  background: linear-gradient(180deg, #fff, #f8fafd);
  display: flex;
  align-items: flex-start;
  gap: 10px;
  box-shadow: 0 -4px 12px rgba(10, 30, 58, 0.06);
}
.rc-receipt__icon {
  width: 28px;
  height: 28px;
  border-radius: 50%;
  background: var(--tv3-teal-soft);
  color: var(--tv3-teal);
  display: grid;
  place-items: center;
  font-size: 14px;
  font-weight: 700;
  flex-shrink: 0;
}
.rc-receipt__icon.is-ok {
  background: var(--tv3-teal-soft);
  color: var(--tv3-teal-deep);
  animation: rc-receipt-pop .4s cubic-bezier(0.34, 1.56, 0.64, 1);
}
@keyframes rc-receipt-pop {
  0% { transform: scale(0.5); }
  60% { transform: scale(1.15); }
  100% { transform: scale(1); }
}
.rc-receipt__body {
  flex: 1;
  min-width: 0;
}
.rc-receipt__message {
  font-size: 12.5px;
  color: var(--tv3-ink);
  line-height: 1.5;
  font-weight: 500;
}
.rc-receipt__location {
  font-size: 11px;
  color: var(--tv3-ink3);
  margin-top: 3px;
}
.rc-receipt__actions {
  display: flex;
  gap: 4px;
  flex-shrink: 0;
}
</style>
