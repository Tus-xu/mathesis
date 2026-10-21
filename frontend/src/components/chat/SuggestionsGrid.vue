<template>
  <!-- 建议卡：首轮发送后收起为 slim 条（可点击展开），空会话默认展开 -->
  <div v-if="collapsed && !expanded" class="sugg-slim" @click="expanded = true">
    <span class="sugg-slim-text">💡 试试这些提问</span>
    <span class="sugg-slim-arrow">▸</span>
  </div>
  <div v-else class="sugg-wrap">
    <div v-if="collapsed" class="sugg-fold" @click="expanded = false">
      <span>收起建议</span><span class="sugg-slim-arrow">▾</span>
    </div>
    <div class="sugg-grid">
      <button
        v-for="s in suggestions" :key="s.text" class="sugg-chip"
        :class="{ 'sugg-chip--rec': !!s.reason }" :title="s.reason || ''"
        @click="$emit('pick', s)"
      >
        {{ s.text }}
        <span v-if="s.reason" class="sugg-reason">{{ s.reason }}</span>
        <span v-else-if="s.rec" class="sugg-rec">个性化</span>
        <span v-if="s.skill" class="sugg-skill">{{ skillTag(s.skill) }}</span>
      </button>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { api } from '@/api/client'
import { skillByKey } from '@/config/skills'

const props = defineProps({
  /** 首轮发送后收起（由父级按 messages.length 驱动） */
  collapsed: { type: Boolean, default: false },
  /**
   * 学情驱动的推荐（父级 HomeLearningPanel 同源数据）。
   * 每项 { text, reason, skill?, action?, ask?, item? }：reason 是可见的“为什么推荐”。
   * 传 null 时才回退到组件内自取薄弱点（保持组件可独立使用）。
   */
  personalized: { type: Array, default: null },
})
defineEmits(['pick']) // pick({ text, skill, reason, action, ask, item })

const expanded = ref(false)
// 新会话（collapsed 变 false）时重置为展开态
watch(() => props.collapsed, (v) => { if (!v) expanded.value = false })

// 兜底：父级未传数据时，仍按真实 weak-points 生成（不伪造学情）
const fetched = ref([])
onMounted(async () => {
  if (props.personalized !== null) return
  try {
    const data = await api.get('/student/report/weak-points')
    const items = data?.items || data || []
    const name = (w) => w.kp_name || w.name || ''
    const list = []
    for (const w of items.slice(0, 2)) {
      if (!name(w)) continue
      list.push({ text: `出 3 道「${name(w)}」的变式题`, skill: 'quiz_gen', reason: '薄弱点' })
    }
    if (name(items[0])) {
      list.push({ text: `引导我攻一下「${name(items[0])}」`, skill: 'socratic', reason: '薄弱点' })
    }
    fetched.value = list
  } catch { /* 无学情/接口不可用：保持常青建议，不报错 */ }
})
const suggestions = computed(() => [
  ...(props.personalized !== null ? props.personalized : fetched.value),
  ...EVERGREEN,
])

const EVERGREEN = [
  { text: '分析我的错题规律', skill: '' },
  { text: '我贴道题，帮我出几道变式巩固', skill: 'quiz_gen' },
  { text: '引导我解一道二次函数题', skill: 'socratic' },
  { text: '讲讲定积分的几何意义', skill: 'kb_qa' },
  { text: '来一场 60 分钟全真模拟', skill: '' },
  { text: '帮我梳理本周该复习什么', skill: '' },
]

function skillTag(key) {
  return skillByKey(key).icon
}
</script>

<style scoped>
.sugg-grid { display: flex; flex-wrap: wrap; gap: var(--space-2); }
.sugg-chip {
  padding: var(--space-2) var(--space-4); border-radius: var(--radius-full); font-size: var(--text-xs); cursor: pointer;
  border: 1px solid var(--border); background: var(--bg-white); color: var(--text-secondary);
  transition: all var(--transition-fast); font-family: var(--font);
  display: inline-flex; align-items: center; gap: var(--space-1);
}
.sugg-chip:hover {
  border-color: var(--border-strong); color: var(--text-primary);
  background: var(--bg-subtle);
}
.sugg-skill {
  font-size: 10px; padding: 0 var(--space-1); border-radius: var(--radius-full);
  background: var(--primary-subtle); color: var(--primary);
}
.sugg-rec {
  font-size: 10px; padding: 0 var(--space-1); border-radius: var(--radius-full);
  background: linear-gradient(135deg, #eef2ff, #ecfeff); color: #4f46e5; font-weight: 700;
}
.sugg-chip--rec {
  border-color: var(--primary-border);
  background: linear-gradient(135deg, rgba(238, 242, 255, .9), rgba(255, 255, 255, .95));
  color: var(--ink);
}
.sugg-reason {
  font-size: 9.5px; padding: 1px 7px; border-radius: 999px; font-weight: 800; letter-spacing: .01em;
  background: var(--brand-soft); color: var(--brand-deep); white-space: nowrap;
}
/* slim 收起条 */
.sugg-slim {
  display: flex; align-items: center; justify-content: space-between;
  padding: var(--space-1) var(--space-3); margin: 0 var(--space-4) var(--space-2);
  border: 1px dashed var(--border); border-radius: var(--radius-full);
  font-size: var(--text-xs); color: var(--text-muted); cursor: pointer;
  transition: all var(--transition-fast); user-select: none;
}
.sugg-slim:hover { border-color: var(--border-strong); color: var(--text-secondary); background: var(--bg-subtle); }
.sugg-slim-arrow { font-size: 10px; }
.sugg-fold {
  display: flex; align-items: center; justify-content: flex-end; gap: 4px;
  padding: 0 var(--space-4) var(--space-1);
  font-size: 11px; color: var(--text-muted); cursor: pointer; user-select: none;
}
.sugg-fold:hover { color: var(--text-secondary); }
</style>
