<template>
  <!--
    T22 · ConfirmCard：提案动作区。
    按钮集合**完全**来自服务端 `available_actions`；文案由 `copy_key` 经 i18n 解析。
    `disabled` 由服务端给出（无权限/在途）→ 可见但禁用，不隐藏。
  -->
  <div class="confirm" role="group" :aria-label="copyFor('common.confirm')" data-testid="prep-confirm-card">
    <ActionButton
      v-for="a in actions"
      :key="`${a.action}:${a.target ?? ''}`"
      :variant="a.action === 'confirm_proposal' || a.action === 'publish_diagnostic' ? 'primary' : 'secondary'"
      :disabled="a.disabled || busy"
      :test-id="`prep-action-${a.action}`"
      @click="$emit('run', a.action, a.target)"
    >
      {{ copyFor(a.copy_key) }}
    </ActionButton>
  </div>
</template>

<script setup lang="ts">
import ActionButton from '../primitives/ActionButton.vue'
import { copyFor } from '@/features/teacher-prep-v2/i18n/copy'
import type { ActionView } from '@/features/teacher-prep-v2/types/workspace'

withDefaults(defineProps<{ actions: ActionView[]; busy?: boolean }>(), { busy: false })

defineEmits<{ run: [action: string, target: string | null] }>()
</script>

<style scoped>
.confirm {
  display: flex;
  align-items: center;
  gap: var(--ma-space-2);
  flex-wrap: wrap;
}
</style>
