<template>
  <div ref="rootRef" class="rw-notif">
    <button
      type="button"
      class="rw-notif__btn"
      :aria-expanded="open"
      aria-haspopup="dialog"
      :aria-label="unread > 0 ? `通知，${unread} 条未读` : '通知'"
      @click="toggle"
    >
      <NotificationsOutline class="rw-notif__ico" aria-hidden="true" />
      <!-- 未读徽标只在**真实未读数 > 0** 时渲染（宪法 §7「通知是假红点」） -->
      <span v-if="unread > 0" class="rw-notif__badge" aria-hidden="true">{{ badgeText }}</span>
    </button>

    <div v-if="open" class="rw-notif__panel" role="dialog" aria-label="通知">
      <header class="rw-notif__head">
        <span class="rw-notif__title">通知</span>
        <button v-if="unread > 0" type="button" class="rw-notif__all" @click="markAll">全部已读</button>
      </header>

      <p v-if="loading && !items.length" class="rw-notif__hint">正在加载通知…</p>
      <ul v-else-if="items.length" class="rw-notif__list">
        <li v-for="n in items" :key="n.id">
          <button
            type="button"
            class="rw-notif__item"
            :class="{ 'rw-notif__item--unread': !n.read_at }"
            @click="pick(n)"
          >
            <span class="rw-notif__dot" aria-hidden="true" />
            <span class="rw-notif__body">
              <span class="rw-notif__item-title">{{ n.title || '通知' }}</span>
              <span v-if="n.body" class="rw-notif__item-text">{{ n.body }}</span>
            </span>
            <span class="rw-notif__item-time">{{ when(n.created_at) }}</span>
          </button>
        </li>
      </ul>
      <p v-else class="rw-notif__hint">{{ emptyText }}</p>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * 顶栏「通知」（VISUAL-SPEC §1.1 / 宪法 §7、§70）。
 *
 * 为什么直连 `notificationsApi` 而不用全局 `useTasksStore`：
 *   共享 store 为「轮询不打扰用户」刻意**吞掉异常**，导致视图无法区分
 *   「真的没有通知」与「通知服务挂了」。而本组件必须给出**诚实**的空态/失败态，
 *   并在拿不到数据时**不渲染任何红点**，故在此显式 try/catch（只 import，不改共享层）。
 *
 * 契约（`src/api/tasks.js`）：`GET /api/notifications?limit=` → `data.items[]`；
 * `GET /api/notifications/unread-count` → `data.count`；`POST /api/notifications/read-all`。
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { NotificationsOutline } from '@vicons/ionicons5'
import { notificationsApi } from '@/api/tasks'

interface NotifItem {
  id: string
  type?: string
  title?: string
  body?: string
  read_at?: string | null
  created_at?: string
  payload?: { jump?: unknown } | null
}

const router = useRouter()
const rootRef = ref<HTMLElement | null>(null)
const open = ref(false)
const loading = ref(false)
const failed = ref(false)
const items = ref<NotifItem[]>([])
const unread = ref(0)

const badgeText = computed(() => (unread.value > 99 ? '99+' : String(unread.value)))
const emptyText = computed(() =>
  failed.value
    ? '通知服务暂时不可用，请稍后重试。'
    : '暂无通知。任务与协作动态会显示在这里。',
)

async function refresh() {
  loading.value = true
  try {
    const [count, list] = await Promise.all([
      notificationsApi.unreadCount() as Promise<{ count?: number } | null>,
      notificationsApi.list({ limit: 20 }) as Promise<{ items?: NotifItem[] } | null>,
    ])
    unread.value = Number(count?.count) || 0
    items.value = Array.isArray(list?.items) ? list.items : []
    failed.value = false
  } catch {
    // 失败：不显示假红点、不伪造通知，只保留诚实文案（宪法 §60/§61）。
    failed.value = true
    unread.value = 0
    items.value = []
  } finally {
    loading.value = false
  }
}

function toggle() {
  open.value = !open.value
  if (open.value) void refresh()
}

async function pick(n: NotifItem) {
  const jump = n.payload?.jump
  if (!n.read_at) {
    try {
      await notificationsApi.markRead(n.id)
    } catch {
      /* 标记失败不阻塞跳转 */
    }
    n.read_at = new Date().toISOString()
    if (unread.value > 0) unread.value -= 1
  }
  if (typeof jump === 'string' && jump.startsWith('/')) {
    open.value = false
    void router.push(jump)
  }
}

async function markAll() {
  try {
    await notificationsApi.readAll()
  } catch {
    /* 失败时下方 refresh 会把真实状态取回来 */
  }
  await refresh()
}

function onDocClick(e: MouseEvent) {
  if (open.value && rootRef.value && !rootRef.value.contains(e.target as Node)) open.value = false
}
function onKey(e: KeyboardEvent) {
  if (e.key === 'Escape') open.value = false
}

onMounted(() => {
  void refresh()
  document.addEventListener('mousedown', onDocClick)
  document.addEventListener('keydown', onKey)
})
onBeforeUnmount(() => {
  document.removeEventListener('mousedown', onDocClick)
  document.removeEventListener('keydown', onKey)
})

/** 相对时间：今天 HH:mm / 昨天 HH:mm / M月D日。 */
function when(iso?: string): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const now = new Date()
  const pad = (n: number) => String(n).padStart(2, '0')
  const hm = `${pad(d.getHours())}:${pad(d.getMinutes())}`
  const sameDay = (a: Date, b: Date) =>
    a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate()
  if (sameDay(d, now)) return `今天 ${hm}`
  const y = new Date(now)
  y.setDate(now.getDate() - 1)
  if (sameDay(d, y)) return `昨天 ${hm}`
  return `${d.getMonth() + 1}月${d.getDate()}日`
}
</script>

<style scoped>
.rw-notif {
  position: relative;
  flex: 0 0 auto;
}
.rw-notif__btn {
  position: relative;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border: none;
  border-radius: var(--rw-radius);
  background: transparent;
  color: var(--rw-text);
  cursor: pointer;
}
.rw-notif__btn:hover {
  background: var(--rw-surface-2);
}
.rw-notif__ico {
  width: 20px;
  height: 20px;
}
.rw-notif__badge {
  position: absolute;
  top: 0;
  right: -1px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 18px;
  height: 18px;
  padding: 0 4px;
  border-radius: var(--rw-radius-pill);
  background: var(--rw-badge-red);
  color: var(--rw-surface);
  font-size: var(--rw-fs-xs);
  font-weight: var(--rw-fw-bold);
  line-height: 1;
}
.rw-notif__panel {
  position: absolute;
  top: 40px;
  right: 0;
  width: 320px;
  max-height: 384px;
  overflow-y: auto;
  padding: 8px;
  border: var(--rw-border-w) solid var(--rw-border);
  border-radius: var(--rw-radius-md);
  background: var(--rw-surface);
  box-shadow: var(--rw-shadow-pop);
  z-index: 30;
}
.rw-notif__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 2px 6px 8px;
}
.rw-notif__title {
  font-size: var(--rw-fs-h3);
  font-weight: var(--rw-fw-semibold);
  color: var(--rw-text);
}
.rw-notif__all {
  padding: 0;
  border: none;
  background: transparent;
  color: var(--rw-primary);
  font-size: var(--rw-fs-sm);
  cursor: pointer;
}
.rw-notif__list {
  list-style: none;
  margin: 0;
  padding: 0;
}
.rw-notif__item {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  width: 100%;
  padding: 8px;
  border: none;
  border-radius: var(--rw-radius);
  background: transparent;
  cursor: pointer;
  text-align: left;
}
.rw-notif__item:hover {
  background: var(--rw-surface-2);
}
.rw-notif__item--unread {
  background: var(--rw-primary-softer);
}
.rw-notif__dot {
  width: 6px;
  height: 6px;
  margin-top: 6px;
  flex: 0 0 auto;
  border-radius: var(--rw-radius-pill);
  background: var(--rw-border-strong);
}
.rw-notif__item--unread .rw-notif__dot {
  background: var(--rw-primary);
}
.rw-notif__body {
  flex: 1 1 auto;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.rw-notif__item-title {
  font-size: var(--rw-fs-body);
  font-weight: var(--rw-fw-medium);
  color: var(--rw-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.rw-notif__item-text {
  font-size: var(--rw-fs-sm);
  color: var(--rw-text-2);
  line-height: var(--rw-lh-label);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.rw-notif__item-time {
  flex: 0 0 auto;
  font-size: var(--rw-fs-xs);
  color: var(--rw-text-3);
}
.rw-notif__hint {
  margin: 0;
  padding: 12px 8px;
  font-size: var(--rw-fs-sm);
  line-height: var(--rw-lh-body);
  color: var(--rw-text-3);
}
</style>
