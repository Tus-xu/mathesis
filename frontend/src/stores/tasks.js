/**
 * 后台任务中心 + 站内通知 store
 * - 轮询 5s：仅登录态（auth.isLoggedIn）时拉取任务列表与未读数；
 *   页面 visibilitychange（重新可见）时立即刷新一次；
 *   登出（tick 时发现非登录态）自动停止轮询并清空状态。
 * - timer 挂在模块级变量而非 state，避免被 reactive 代理。
 */
import { defineStore } from 'pinia'
import { tasksApi, notificationsApi } from '@/api/tasks'
import { useAuthStore } from '@/stores/auth'

const POLL_INTERVAL = 5000
const TASK_LIMIT = 30
const NOTIFY_LIMIT = 30

let pollTimer = null
let pollStarted = false

function byCreatedDesc(a, b) {
  return String(b?.created_at || '').localeCompare(String(a?.created_at || ''))
}

export const useTasksStore = defineStore('tasks', {
  state: () => ({
    items: [],          // 后台任务列表（created_at 倒序）
    notifications: [],  // 站内通知列表（created_at 倒序）
    unread: 0,          // 未读通知数（悬浮球角标）
    tasksLoading: false,
    notifyLoading: false,
  }),
  getters: {
    /** 进行中（queued/running）任务数，用于「任务」Tab 角标 */
    activeCount: (state) => state.items.filter((t) => t?.status === 'queued' || t?.status === 'running').length,
  },
  actions: {
    /* ===== 查询 ===== */
    async refreshTasks() {
      const auth = useAuthStore()
      if (!auth.isLoggedIn) return
      this.tasksLoading = true
      try {
        const data = await tasksApi.list({ limit: TASK_LIMIT })
        const items = Array.isArray(data?.items) ? data.items : []
        this.items = items.slice().sort(byCreatedDesc)
      } catch { /* 轮询静默失败，等下一轮 */ } finally {
        this.tasksLoading = false
      }
    },
    async refreshNotifications() {
      const auth = useAuthStore()
      if (!auth.isLoggedIn) return
      this.notifyLoading = true
      try {
        const [countData, listData] = await Promise.all([
          notificationsApi.unreadCount(),
          notificationsApi.list({ limit: NOTIFY_LIMIT }),
        ])
        this.unread = Number(countData?.count) || 0
        const items = Array.isArray(listData?.items) ? listData.items : []
        this.notifications = items.slice().sort(byCreatedDesc)
      } catch { /* 轮询静默失败，等下一轮 */ } finally {
        this.notifyLoading = false
      }
    },
    refreshAll() {
      return Promise.all([this.refreshTasks(), this.refreshNotifications()])
    },

    /* ===== 轮询控制 ===== */
    startPolling() {
      if (pollStarted) return
      const auth = useAuthStore()
      if (!auth.isLoggedIn) return
      pollStarted = true
      document.addEventListener('visibilitychange', onVisibility)
      this.refreshAll()
      pollTimer = setInterval(() => {
        if (!useAuthStore().isLoggedIn) { this.stopPolling(); return }
        this.refreshAll()
      }, POLL_INTERVAL)
    },
    stopPolling() {
      pollStarted = false
      if (pollTimer) { clearInterval(pollTimer); pollTimer = null }
      document.removeEventListener('visibilitychange', onVisibility)
    },
    /** 登出后清空本地态（配合 stopPolling 由 tick/调用方触发） */
    reset() {
      this.stopPolling()
      this.items = []
      this.notifications = []
      this.unread = 0
    },

    /* ===== 写操作 ===== */
    async createTask(kind, payload, idempotencyKey) {
      const data = await tasksApi.create({
        kind,
        payload,
        ...(idempotencyKey ? { idempotency_key: idempotencyKey } : {}),
      })
      await this.refreshTasks()
      return data
    },
    async cancel(taskId) {
      await tasksApi.cancel(taskId)
      await this.refreshTasks()
    },
    async retry(taskId) {
      await tasksApi.retry(taskId)
      await this.refreshTasks()
    },
    async markRead(id) {
      const n = this.notifications.find((x) => String(x?.id) === String(id))
      if (n && !n.read_at) { n.read_at = new Date().toISOString(); if (this.unread > 0) this.unread -= 1 }
      try { await notificationsApi.markRead(id) } finally { this.refreshNotifications() }
    },
    async readAll() {
      this.notifications = this.notifications.map((n) => ({ ...n, read_at: n.read_at || new Date().toISOString() }))
      this.unread = 0
      try { await notificationsApi.readAll() } finally { this.refreshNotifications() }
    },
  },
})

/** 页面重新可见时立即刷新（挂在模块级，start/stop 时增删） */
function onVisibility() {
  if (document.visibilityState !== 'visible') return
  if (!useAuthStore().isLoggedIn) return
  const store = useTasksStore()
  store.refreshAll()
}
