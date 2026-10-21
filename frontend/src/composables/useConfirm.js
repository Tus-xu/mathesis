/**
 * 全局确认弹窗（替代 window.confirm），Promise 风格：
 *   const ok = await confirm({ title: '删除会话', message: '删除后不可恢复', danger: true })
 * 宿主组件 <ConfirmDialog /> 挂在 App.vue 根部。
 */
import { reactive } from 'vue'

const state = reactive({
  open: false,
  title: '确认操作',
  message: '',
  confirmText: '确定',
  cancelText: '取消',
  danger: false,
  _resolve: null,
})

export function useConfirm() {
  function confirm({ title = '确认操作', message = '', confirmText = '确定', cancelText = '取消', danger = false } = {}) {
    // 连续调用：先了结前一个（按取消处理）
    if (state._resolve) close(false)
    return new Promise((resolve) => {
      Object.assign(state, { open: true, title, message, confirmText, cancelText, danger, _resolve: resolve })
    })
  }

  function close(result) {
    state.open = false
    const r = state._resolve
    state._resolve = null
    r?.(result)
  }

  return { state, confirm, close }
}
