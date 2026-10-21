<template>
  <button
    class="tv3-butler-fab"
    type="button"
    data-testid="tv3-butler-fab"
    :aria-expanded="open"
    aria-label="AI 管家"
    @click="$emit('toggle')"
  >
    <span class="tv3-butler-fab__badge">∫</span>
    <span class="tv3-butler-fab__label">AI</span>
    <span v-if="unread > 0 && !open" class="tv3-butler-fab__dot" data-testid="tv3-butler-unread">{{ unread }}</span>
  </button>
</template>

<script setup lang="ts">
/**
 * ButlerFab —— AI 管家悬浮球（右下角常驻，Notion/飞书式入口）
 * P0 固定右下角；P1 可拖动换边贴边。
 */
defineProps<{ open: boolean; unread: number }>()
defineEmits<{ (e: 'toggle'): void }>()
</script>

<style scoped>
.tv3-butler-fab {
  position: fixed; right: 22px; bottom: 26px; z-index: 900;
  width: 56px; height: 56px; border-radius: 50%; border: none; cursor: pointer;
  background: linear-gradient(135deg, #4f46e5, #123a6d);
  color: #fff; display: grid; place-items: center; gap: 0;
  box-shadow: 0 8px 24px rgba(79, 70, 229, 0.38);
  transition: transform .18s ease, box-shadow .18s ease;
  padding: 0;
}
.tv3-butler-fab:hover { transform: translateY(-2px) scale(1.04); box-shadow: 0 12px 30px rgba(79, 70, 229, 0.46); }
.tv3-butler-fab__badge {
  font-family: Cambria Math, Georgia, serif; font-size: 26px; line-height: 1; font-weight: 600;
}
.tv3-butler-fab__label {
  position: absolute; bottom: 9px; right: 9px; font-size: 9px; font-weight: 700;
  background: var(--tv3-gold, #0891b2); color: #1c2b1a; border-radius: 999px; padding: 1px 5px; line-height: 1.3;
}
.tv3-butler-fab__dot {
  position: absolute; top: -2px; right: -2px; min-width: 18px; height: 18px; border-radius: 999px;
  background: var(--tv3-rose, #dc2646); color: #fff; font-size: 11px; font-weight: 700;
  display: grid; place-items: center; padding: 0 4px; border: 2px solid #fff;
}
</style>
