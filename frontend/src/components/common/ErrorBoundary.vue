<script setup lang="ts">
/**
 * 页面级错误边界（防止“侧栏正常、主区全白”）。
 *
 * 学生端 / 双师课堂此前没有任何 onErrorCaptured：任意子组件 setup/render 抛错，
 * Vue 会卸载整棵子树，用户只看到空白主区，且拿不到任何诊断信息。
 * 这里捕获子树异常 → 显示可读错误 + 重试（重建子树）/ 回首页，并把错误打到控制台。
 */
import { onErrorCaptured, ref } from 'vue'

const props = withDefaults(defineProps<{ title?: string; compact?: boolean }>(), {
  title: '页面加载出现异常',
  compact: false,
})

const err = ref<Error | null>(null)
const resetKey = ref(0)

onErrorCaptured((e) => {
  err.value = e instanceof Error ? e : new Error(String(e))
  // eslint-disable-next-line no-console
  console.error('[ErrorBoundary]', e)
  return false
})

function retry() {
  err.value = null
  resetKey.value += 1
}
function goHome() {
  location.href = '/'
}
</script>

<template>
  <div v-if="err" class="eb" :class="{ 'eb--compact': props.compact }" role="alert">
    <div class="eb__card">
      <div class="eb__icon">⚠️</div>
      <div class="eb__title">{{ props.title }}</div>
      <p class="eb__msg">{{ err.message || '未知错误' }}</p>
      <p class="eb__hint">页面数据没有丢失；可先重试，若仍失败请刷新页面或联系管理员。</p>
      <div class="eb__actions">
        <button type="button" class="eb__btn eb__btn--primary" @click="retry">重试</button>
        <button type="button" class="eb__btn" @click="goHome">回到首页</button>
      </div>
    </div>
  </div>
  <div v-else :key="resetKey">
    <slot />
  </div>
</template>

<style scoped>
.eb { display: flex; justify-content: center; padding: 48px 24px; }
.eb--compact { padding: 20px; }
.eb__card {
  width: min(520px, 100%); display: grid; justify-items: center; gap: 8px;
  padding: 30px 28px; border: 1px solid var(--line, #e2e8f0); border-radius: 16px;
  background: var(--card, #fff); box-shadow: 0 12px 40px rgba(15, 23, 42, .06);
  text-align: center;
}
.eb__icon { font-size: 28px; }
.eb__title { font-size: 16px; font-weight: 800; color: var(--ink, #0f172a); }
.eb__msg {
  max-width: 100%; margin: 0; font-size: 12.5px; color: var(--err-deep, #b91c1c);
  word-break: break-word; line-height: 1.6;
}
.eb__hint { margin: 0; font-size: 12px; color: var(--ink3, #94a3b8); line-height: 1.6; }
.eb__actions { display: flex; gap: 8px; margin-top: 6px; }
.eb__btn {
  padding: 8px 18px; border: 1px solid var(--line, #e2e8f0); border-radius: 999px;
  background: #fff; color: var(--ink2, #475569); font: inherit; font-size: 12.5px; font-weight: 700; cursor: pointer;
}
.eb__btn--primary { border-color: var(--brand, #4f46e5); background: var(--brand, #4f46e5); color: #fff; }
.eb__btn:hover { filter: brightness(.98); }
</style>
