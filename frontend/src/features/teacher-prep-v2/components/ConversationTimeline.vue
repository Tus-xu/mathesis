<template>
  <!--
    T22 · Conversation timeline（conversation-first 的主叙事）。
    语义结构：`role="log"` + `aria-live="polite"`，新消息由读屏播报；
    按 seq 升序渲染（服务端全序权威），不做客户端重排。
  -->
  <div class="timeline" data-testid="prep-timeline">
    <ol
      class="timeline__list"
      role="log"
      aria-live="polite"
      aria-relevant="additions"
      :aria-label="copyFor('prep.region.conversation')"
    >
      <li v-for="m in ordered" :key="m.message_id">
        <MessageCard :message="m" @retry="(id) => $emit('retry', id)" @apply-edits="(id, t) => $emit('apply-edits', id, t)" />
      </li>
    </ol>

    <p v-if="!ordered.length" class="timeline__empty">{{ copyFor('prep.state.empty') }}</p>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import MessageCard from './MessageCard.vue'
import { copyFor } from '@/features/teacher-prep-v2/i18n/copy'
import type { MessageView } from '@/features/teacher-prep-v2/types/workspace'

const props = defineProps<{ messages: MessageView[] }>()

defineEmits<{
  retry: [messageId: string]
  'apply-edits': [messageId: string, text: string]
}>()

/** 服务端 seq 全序权威；客户端只做稳定升序，不解释业务顺序。 */
const ordered = computed(() => [...props.messages].sort((a, b) => a.seq - b.seq))
</script>

<style scoped>
.timeline__list {
  list-style: none;
  margin: 0;
  padding: 0;
}

.timeline__empty {
  margin: var(--ma-space-4) 0;
  color: var(--ma-text-muted);
  font-size: var(--ma-font-size-body);
}
</style>
