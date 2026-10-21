<template>
  <!-- 居中模态弹窗 -->
  <transition name="modal">
    <div v-if="open" class="modal-mask" @click.self="$emit('close')">
      <div class="modal glass-card-strong" :style="{ width }">
        <header class="modal-head">
          <div class="modal-title">{{ title }}</div>
          <button class="modal-close" title="关闭" @click="$emit('close')">✕</button>
        </header>
        <div class="modal-body">
          <slot />
        </div>
        <footer v-if="$slots.footer" class="modal-foot">
          <slot name="footer" />
        </footer>
      </div>
    </div>
  </transition>
</template>

<script setup>
defineProps({
  open: { type: Boolean, default: false },
  title: { type: String, default: '' },
  width: { type: String, default: '480px' },
})
defineEmits(['close'])
</script>

<style scoped>
.modal-mask {
  position: fixed; inset: 0; z-index: 300; background: rgba(24, 24, 27, 0.4);
  display: flex; align-items: center; justify-content: center; padding: var(--space-6);
}
.modal {
  max-width: 94vw; max-height: 88vh; display: flex; flex-direction: column;
  border-radius: var(--radius-xl); box-shadow: var(--shadow-lg);
  background: var(--bg-white);
  border: 1px solid var(--border);
}
.modal-head {
  display: flex; align-items: center; justify-content: space-between;
  padding: var(--space-5) var(--space-5) var(--space-3);
}
.modal-title { font-size: var(--text-base); font-weight: var(--font-semibold); letter-spacing: -0.01em; }
.modal-close {
  width: 28px; height: 28px; border-radius: var(--radius-full); border: none;
  background: var(--bg-muted); color: var(--text-secondary); cursor: pointer; font-size: 14px;
  transition: all var(--transition-fast); display: inline-flex; align-items: center; justify-content: center;
}
.modal-close:hover { background: var(--error-subtle); color: var(--error); }
.modal-body { padding: var(--space-2) var(--space-5) var(--space-5); overflow-y: auto; }
.modal-foot {
  padding: var(--space-3) var(--space-5) var(--space-4); border-top: 1px solid var(--border-subtle);
  display: flex; justify-content: flex-end; gap: var(--space-3);
}
.modal-enter-active, .modal-leave-active { transition: opacity var(--transition-base); }
.modal-enter-active .modal, .modal-leave-active .modal { transition: transform var(--transition-base); }
.modal-enter-from, .modal-leave-to { opacity: 0; }
.modal-enter-from .modal, .modal-leave-to .modal { transform: translateY(8px) scale(0.98); }
</style>
