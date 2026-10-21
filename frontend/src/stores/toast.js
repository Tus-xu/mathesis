import { defineStore } from 'pinia'

let seq = 0

export const useToastStore = defineStore('toast', {
  state: () => ({ items: [] }),
  actions: {
    /**
     * @param {string} text
     * @param {string} [type]
     * @param {number|null} [duration] null = 不自动消失（须显式关闭），用于一次性迁移提示等需被读完的通知
     */
    show(text, type = 'info', duration = 2600) {
      const id = ++seq
      this.items.push({ id, text, type })
      if (duration === null) return id
      setTimeout(() => {
        this.items = this.items.filter((t) => t.id !== id)
      }, duration)
      return id
    },
    dismiss(id) {
      this.items = this.items.filter((t) => t.id !== id)
    },
    success(text) { this.show(text, 'success') },
    error(text) { this.show(text, 'error', 3600) },
    info(text) { this.show(text, 'info') },
    warning(text) { this.show(text, 'warning') },
  },
})
