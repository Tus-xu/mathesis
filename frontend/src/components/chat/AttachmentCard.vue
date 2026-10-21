<template>
  <div class="att-card" :class="{ failed: task.status === 'failed', ready: task.status === 'parsed' }">
    <!-- 图片附件：本地缩略图（4.4）；其他类型：emoji 图标 -->
    <img v-if="thumbUrl" :src="thumbUrl" :alt="task.filename" class="att-thumb" @click.stop="openLightbox(thumbUrl)" />
    <span v-else class="att-icon">{{ icon }}</span>
    <div class="att-main">
      <div class="att-name" :title="task.filename">{{ task.filename }}</div>
      <div class="att-status">
        <template v-if="task.status === 'hashing'"><span class="spinner"></span> 计算摘要…</template>
        <template v-else-if="task.status === 'uploading'">上传中 {{ task.progress }}%</template>
        <template v-else-if="task.status === 'uploaded'">待解析…</template>
        <template v-else-if="task.status === 'parsing'">
          <span class="spinner"></span> 解析中<span v-if="task.engine">（{{ task.engine }}）</span>
        </template>
        <template v-else-if="task.status === 'parsed'">
          ✅ 已就绪<span v-if="task.engine" class="att-engine">{{ task.engine }}</span>
        </template>
        <template v-else-if="task.status === 'failed'">
          <span class="att-err" :title="task.error">❌ {{ task.error || '失败' }}</span>
        </template>
      </div>
      <div v-if="task.status === 'uploading'" class="progress-bar att-bar">
        <div class="fill" :style="{ width: task.progress + '%' }"></div>
      </div>
    </div>
    <button v-if="task.status === 'failed'" class="att-act retry" title="重试" @click="$emit('retry', task)">↻</button>
    <button class="att-act" title="移除" @click="$emit('remove', task)">×</button>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { openLightbox } from '@/utils/lightbox'

const props = defineProps({
  task: { type: Object, required: true },
})
defineEmits(['remove', 'retry'])

/* 本地缩略图：图片任务用 objectURL 立即出图（4.4），组件卸载时回收 */
const thumbUrl = ref('')
watch(
  () => props.task.file,
  (f) => {
    if (thumbUrl.value) URL.revokeObjectURL(thumbUrl.value)
    thumbUrl.value = f && String(props.task.mime || '').startsWith('image/') ? URL.createObjectURL(f) : ''
  },
  { immediate: true }
)
onBeforeUnmount(() => { if (thumbUrl.value) URL.revokeObjectURL(thumbUrl.value) })

const icon = computed(() => {
  const m = props.task.mime || ''
  if (m.startsWith('image/')) return '🖼️'
  if (m === 'application/pdf') return '📄'
  if (m.includes('word')) return '📝'
  if (m.includes('presentation')) return '📽️'
  if (m.includes('sheet')) return '📊'
  return '📎'
})
</script>

<style scoped>
.att-card {
  display: flex; align-items: center; gap: 8px; padding: 6px 10px; min-width: 180px; max-width: 260px;
  border: 1px solid var(--border); border-radius: var(--radius-md);
  background: var(--bg-white); font-size: 12px;
}
.att-card.ready { border-color: rgba(16, 185, 129, 0.5); background: #f0fdf9; }
.att-card.failed { border-color: rgba(239, 68, 68, 0.5); background: #fff7f7; }
.att-icon { font-size: 18px; flex-shrink: 0; }
.att-thumb {
  width: 40px; height: 40px; object-fit: cover; border-radius: var(--radius-sm);
  flex-shrink: 0; cursor: zoom-in; border: 1px solid var(--border);
}
.att-main { flex: 1; min-width: 0; }
.att-name { font-weight: 600; color: var(--text-primary); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.att-status { color: var(--text-muted); display: flex; align-items: center; gap: 4px; margin-top: 1px; }
.att-engine { color: var(--accent-green); margin-left: 4px; }
.att-err { color: var(--accent-red); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 150px; }
.att-bar { margin-top: 4px; }
.att-act {
  border: none; background: none; cursor: pointer; color: var(--text-muted);
  font-size: 14px; padding: 2px 5px; border-radius: var(--radius-sm); flex-shrink: 0;
}
.att-act:hover { background: #f1f5f9; color: var(--text-primary); }
.att-act.retry { color: var(--primary); }
</style>
