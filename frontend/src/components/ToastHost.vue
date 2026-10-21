<template>
  <!-- role=status + aria-live：新通知出现时被读屏播报（文案自带语义，不依赖颜色/图标） -->
  <div class="toast-wrap" role="status" aria-live="polite" data-testid="toast-host">
    <transition-group name="toast">
      <div v-for="t in store.items" :key="t.id" class="toast" :class="t.type" :data-testid="`toast-${t.type}`">
        <span class="t-icon" aria-hidden="true">{{ icons[t.type] || 'ℹ️' }}</span>
        <span class="t-text">{{ t.text }}</span>
        <button
          class="t-close"
          type="button"
          :aria-label="`关闭提示：${t.text}`"
          data-testid="toast-dismiss"
          @click="store.dismiss(t.id)"
        >✕</button>
      </div>
    </transition-group>
  </div>
</template>

<script setup>
import { useToastStore } from '@/stores/toast'
const store = useToastStore()
const icons = { success: '✅', error: '⚠️', info: 'ℹ️', warning: '🔔' }
</script>

<style scoped>
.toast-wrap {
  position: fixed; top: var(--space-5); right: var(--space-5); z-index: 9999;
  display: flex; flex-direction: column; gap: var(--space-3); pointer-events: none;
}
.toast {
  display: flex; align-items: center; gap: var(--space-2);
  padding: var(--space-3) var(--space-4); border-radius: var(--radius-md);
  background: var(--bg-white);
  border: 1px solid var(--border); box-shadow: var(--shadow-md);
  font-size: var(--text-sm); color: var(--text-primary); max-width: 360px;
}
.toast.success { border-left: 3px solid var(--success); }
.toast.error { border-left: 3px solid var(--error); }
.toast.info { border-left: 3px solid var(--primary); }
.toast.warning { border-left: 3px solid var(--warning, #f59e0b); }
.toast .t-text { flex: 1; }
/* 关闭按钮必须可点：容器 pointer-events:none，只给按钮单独放行（不改变既有"toast 不拦截点击"行为） */
.toast .t-close {
  pointer-events: auto; flex-shrink: 0; margin-left: var(--space-1);
  border: 0; background: none; cursor: pointer; line-height: 1;
  font-size: var(--text-sm); color: var(--text-secondary, #64748b); padding: 2px 4px; border-radius: var(--radius-sm, 4px);
}
.toast .t-close:focus-visible { outline: 2px solid var(--primary); outline-offset: 1px; }
.toast-enter-active, .toast-leave-active { transition: all var(--transition-base); }
.toast-enter-from { opacity: 0; transform: translateX(16px); }
.toast-leave-to { opacity: 0; transform: translateY(-8px); }
</style>
