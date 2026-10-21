<template>
  <!-- 全局确认弹窗宿主（替代 window.confirm），逻辑见 composables/useConfirm.js -->
  <ModalDialog :open="state.open" :title="state.title" width="400px" @close="close(false)">
    <div class="confirm-msg">{{ state.message }}</div>
    <template #footer>
      <button class="btn btn-sm" @click="close(false)">{{ state.cancelText }}</button>
      <button
        class="btn btn-sm"
        :class="state.danger ? 'btn-danger-solid' : 'btn-primary'"
        @click="close(true)"
      >{{ state.confirmText }}</button>
    </template>
  </ModalDialog>
</template>

<script setup>
import ModalDialog from '@/components/student/ModalDialog.vue'
import { useConfirm } from '@/composables/useConfirm'

const { state, close } = useConfirm()
</script>

<style scoped>
.confirm-msg { font-size: var(--text-sm); color: var(--text-secondary); line-height: var(--leading-relaxed); white-space: pre-wrap; }
/* 危险操作实心红按钮 */
.btn-danger-solid {
  background: var(--error); border-color: var(--error); color: #fff;
}
.btn-danger-solid:hover:not(:disabled) { background: #B91C1C; border-color: #B91C1C; color: #fff; }
</style>
