<script setup>
/**
 * 知识库引用选择器（ima 式）：学生勾选本人「已就绪」资料，
 * 随消息携带 kb_doc_ids，回答基于真实切片并可回溯来源。
 */
import { computed, ref, watch } from 'vue'
import UiIcon from '@/components/common/UiIcon.vue'
import { studentKbApi } from '@/api'

const props = defineProps({
  open: { type: Boolean, default: false },
  selected: { type: Array, default: () => [] },
})
const emit = defineEmits(['close', 'toggle'])

const loading = ref(false)
const failed = ref(false)
const docs = ref([])
const q = ref('')

const selectedIds = computed(() => new Set((props.selected || []).map((d) => d.id)))
const filtered = computed(() => {
  const kw = q.value.trim().toLowerCase()
  const list = (docs.value || []).filter((d) => d.status === 'ready' && (d.chunk_count || 0) > 0)
  if (!kw) return list
  return list.filter((d) => String(d.title || '').toLowerCase().includes(kw))
})

function isSelected(doc) {
  return selectedIds.value.has(doc.id)
}

async function load() {
  loading.value = true
  failed.value = false
  try {
    const r = await studentKbApi.documents({ limit: 200 })
    docs.value = r?.items || r?.data?.items || []
  } catch {
    failed.value = true
  } finally {
    loading.value = false
  }
}

watch(
  () => props.open,
  (open) => {
    if (open) {
      q.value = ''
      load()
    }
  },
  { immediate: true },
)
</script>

<template>
  <Teleport to="body">
    <div v-if="open" class="kb-picker-backdrop" @click="$emit('close')"></div>
    <div v-if="open" class="kb-picker" role="dialog" aria-label="引用知识库资料">
      <div class="kb-picker__head">
        <b>引用知识库资料</b>
        <button type="button" class="kb-picker__close" aria-label="关闭" @click="$emit('close')">
          <UiIcon name="close" :size="14" />
        </button>
      </div>
      <input v-model="q" class="kb-picker__search" placeholder="搜索资料标题" />
      <div class="kb-picker__list">
        <div v-if="loading" class="kb-picker__empty">加载中…</div>
        <div v-else-if="failed" class="kb-picker__empty">资料加载失败，请稍后重试</div>
        <div v-else-if="!filtered.length" class="kb-picker__empty">
          知识库还没有可引用的资料，先去「知识库」上传一份
        </div>
        <button
          v-for="d in filtered"
          :key="d.id"
          type="button"
          class="kb-picker__item"
          :class="{ on: isSelected(d) }"
          @click="$emit('toggle', d)"
        >
          <span class="kb-picker__check" aria-hidden="true">{{ isSelected(d) ? '✓' : '' }}</span>
          <span class="kb-picker__title" :title="d.title">{{ d.title }}</span>
          <span class="kb-picker__meta">{{ d.category || '其他' }} · {{ d.chunk_count || 0 }} 片段</span>
        </button>
      </div>
      <div class="kb-picker__foot">最多引用 5 份资料 · 仅引用你本人的资料</div>
    </div>
  </Teleport>
</template>

<style scoped>
.kb-picker-backdrop { position: fixed; inset: 0; z-index: 90; background: rgba(15,23,42,.18); }
.kb-picker {
  position: fixed; z-index: 91; left: 50%; bottom: 132px; transform: translateX(-50%);
  width: min(460px, calc(100vw - 32px)); max-height: 60vh; display: flex; flex-direction: column;
  background: #fff; border: 1px solid var(--border, #e5e7eb); border-radius: 14px;
  box-shadow: 0 18px 48px rgba(15,23,42,.18); overflow: hidden;
}
.kb-picker__head { display: flex; align-items: center; justify-content: space-between; padding: 10px 12px; border-bottom: 1px solid var(--border, #eef0f4); }
.kb-picker__close { border: none; background: transparent; cursor: pointer; color: var(--ink3, #94a3b8); padding: 2px; border-radius: 6px; }
.kb-picker__search { margin: 10px 12px 6px; padding: 8px 10px; border: 1px solid var(--border, #e5e7eb); border-radius: 10px; font-size: 13px; outline: none; }
.kb-picker__search:focus { border-color: var(--primary, #6366f1); }
.kb-picker__list { overflow: auto; padding: 4px 8px 8px; }
.kb-picker__empty { padding: 18px 10px; text-align: center; color: var(--ink3, #94a3b8); font-size: 12.5px; }
.kb-picker__item { width: 100%; display: grid; grid-template-columns: 20px 1fr auto; gap: 6px; align-items: center; padding: 8px 10px; border: none; background: transparent; border-radius: 10px; cursor: pointer; text-align: left; }
.kb-picker__item:hover { background: var(--bg2, #f5f6fa); }
.kb-picker__item.on { background: #eef1ff; }
.kb-picker__check { color: var(--primary, #6366f1); font-weight: 700; text-align: center; }
.kb-picker__title { font-size: 13px; color: var(--ink, #1f2937); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.kb-picker__meta { font-size: 11px; color: var(--ink3, #94a3b8); white-space: nowrap; }
.kb-picker__foot { padding: 8px 12px; border-top: 1px solid var(--border, #eef0f4); font-size: 11px; color: var(--ink3, #94a3b8); }
</style>
