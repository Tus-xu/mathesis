<template>
  <div class="src-card" :class="{ open }" @click="$emit('toggle')">
    <div class="src-head">
      <span class="src-idx">【{{ c.n }}】</span>
      <span v-if="c.kind" class="src-kind">{{ c.kind }}</span>
      <span v-if="c.title" class="src-title">{{ c.title }}</span>
      <span v-else class="src-title muted">{{ c.source || '未知来源' }}</span>
      <span v-if="c.url" class="src-open" :title="c.url" @click.stop="openUrl(c.url)">↗</span>
    </div>
    <div v-if="open" class="src-body">
      <div v-if="c.snippet" class="src-snippet">{{ c.snippet }}</div>
      <div v-if="c.url" class="src-meta"><b>链接：</b><a :href="c.url" target="_blank" rel="noopener noreferrer">{{ c.url }}</a></div>
      <div v-if="c.retrieved_at" class="src-meta"><b>检索时间：</b>{{ fmtTime(c.retrieved_at) }}</div>
      <div v-if="c.source" class="src-meta"><b>来源：</b>{{ c.source }}</div>
      <div v-if="c.loc" class="src-meta"><b>位置：</b>{{ c.loc }}</div>
      <div v-if="c.chunk_id" class="src-meta chunk"><b>切片：</b>{{ c.chunk_id }}</div>
    </div>
  </div>
</template>

<script setup>
import { fmtTime } from './messageModel'

defineProps({
  c: { type: Object, required: true },
  open: { type: Boolean, default: false },
})
defineEmits(['toggle'])

function openUrl(url) {
  try {
    const u = new URL(url)
    if (u.protocol === 'http:' || u.protocol === 'https:') {
      window.open(url, '_blank', 'noopener,noreferrer')
    }
  } catch {
    /* 非法 URL 静默忽略 */
  }
}
</script>

<style scoped>
.src-card {
  font-size: 12px; border: 1px solid var(--border); border-radius: var(--radius-sm);
  background: var(--bg-white); cursor: pointer; transition: all 0.15s;
  max-width: 320px; min-width: 200px;
}
.src-card:hover { border-color: var(--primary-border); }
.src-head {
  display: flex; align-items: center; gap: 6px; padding: 6px 10px;
  color: var(--primary); white-space: nowrap; overflow: hidden;
}
.src-idx { flex: none; }
.src-kind {
  flex: none; padding: 1px 6px; border-radius: 999px;
  background: var(--primary-bg, rgba(79, 70, 229, 0.08));
  color: var(--primary); font-size: 11px;
}
.src-title { overflow: hidden; text-overflow: ellipsis; }
.src-title.muted { color: var(--text-muted); }
.src-open { flex: none; cursor: pointer; color: var(--text-muted); }
.src-open:hover { color: var(--primary); }
.src-body {
  border-top: 1px solid var(--border); padding: 8px 10px;
  display: flex; flex-direction: column; gap: 4px;
}
.src-snippet { color: var(--text-secondary); line-height: 1.6; }
.src-meta { color: var(--text-muted); word-break: break-all; }
.src-meta b { color: var(--text-secondary); font-weight: 500; }
.src-meta a { color: var(--primary); text-decoration: none; }
.src-meta a:hover { text-decoration: underline; }
</style>
