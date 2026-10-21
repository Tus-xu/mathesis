/**
 * conv —— 全局会话列表 store（对话历史收纳进左侧全局侧边栏后，侧边栏与对话页共用）
 * 会话 CRUD/搜索/置顶/分页逻辑与原前端 useChat 的会话管理部分一致（同一后端契约），
 * 消息流/SSE 部分仍由 useChat 负责（manageConversations=false）。
 */
import { defineStore } from 'pinia'
import { agentApi } from '@/api'
import { useToastStore } from '@/stores/toast'
import { useConfirm } from '@/composables/useConfirm'

const CONV_PAGE = 30

export const useConvStore = defineStore('conv', {
  state: () => ({
    items: [],
    loading: false,
    hasMore: false,
    query: '',
    activeId: '',
  }),
  actions: {
    async load({ append = false } = {}) {
      if (this.loading) return
      this.loading = true
      try {
        const params = { limit: CONV_PAGE }
        if (this.query) params.q = this.query
        if (append && this.items.length) {
          const last = this.items[this.items.length - 1]
          params.before = last.updatedAt || last.updated_at || ''
        }
        const d = await agentApi.conversations(params)
        let items = d?.items || (Array.isArray(d) ? d : [])
        if (this.query) {
          const q = this.query.toLowerCase()
          items = items.filter((c) => (c.title || '').toLowerCase().includes(q))
        }
        const more = typeof d?.hasMore === 'boolean' ? d.hasMore : items.length >= CONV_PAGE
        if (append) {
          const seen = new Set(this.items.map((c) => c.id))
          const fresh = items.filter((c) => !seen.has(c.id))
          this.items = [...this.items, ...fresh]
          this.hasMore = fresh.length ? more : false
        } else {
          this.items = items
          this.hasMore = more
        }
      } catch (e) {
        useToastStore().error(e?.message || '会话列表加载失败')
      } finally {
        this.loading = false
      }
    },

    search(q) {
      this.query = q || ''
      return this.load()
    },

    loadMore() {
      if (!this.hasMore || this.loading) return Promise.resolve()
      return this.load({ append: true })
    },

    async create() {
      const c = await agentApi.createConversation('student')
      this.items = [c, ...this.items]
      this.activeId = c.id
      return c
    },

    async remove(id) {
      const ok = await useConfirm().confirm({
        title: '删除会话',
        message: '确定删除该会话吗？删除后不可恢复。',
        confirmText: '删除',
        danger: true,
      })
      if (!ok) return false
      try {
        await agentApi.deleteConversation(id)
        this.items = this.items.filter((c) => c.id !== id)
        useToastStore().success('会话已删除')
        if (this.activeId === id) this.activeId = ''
        return true
      } catch (e) {
        useToastStore().error(e?.message || '删除失败')
        return false
      }
    },

    async rename(id, title) {
      const t = (title || '').trim()
      if (!t) return false
      try {
        const updated = await agentApi.patchConversation(id, { title: t })
        const c = this.items.find((x) => x.id === id)
        if (c) c.title = updated?.title || t
        useToastStore().success('已重命名')
        return true
      } catch (e) {
        if (e?.status === 404 || e?.status === 405 || e?.code === 404 || e?.code === 405) {
          useToastStore().info('重命名功能后端尚未上线')
        } else {
          useToastStore().error(e?.message || '重命名失败')
        }
        return false
      }
    },

    async togglePin(conv) {
      const target = !conv.pinned
      conv.pinned = target
      try {
        await agentApi.patchConversation(conv.id, { pinned: target })
      } catch (e) {
        conv.pinned = !target
        if (e?.status === 404 || e?.status === 405 || e?.code === 404 || e?.code === 405) {
          useToastStore().info('置顶功能后端尚未上线')
        } else {
          useToastStore().error(e?.message || '操作失败')
        }
      }
    },

    select(id) {
      this.activeId = id
    },

    updateTitle(id, title) {
      if (!title) return
      const c = this.items.find((x) => x.id === id)
      if (c) c.title = title
    },
  },
})
