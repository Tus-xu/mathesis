<template>
  <div class="toolbar-wrap">
    <section class="toolbar" data-testid="t24-revision-toolbar" :aria-label="copyFor('prep.t24.aria.revision')">
      <span class="toolbar__revision" data-testid="t24-revision-value">{{ copyFor('prep.t24.revision.current') }} {{ revision }}</span>
      <span v-if="stale" class="toolbar__state" data-state="stale">{{ copyFor('prep.t24.revision.stale') }}</span>
      <span v-if="pendingPatchId" class="toolbar__pending" data-testid="t24-pending-proposal">
        {{ copyFormat('prep.t24.revision.pending', pendingPatchId) }}
      </span>
      <button type="button" class="toolbar__button" :disabled="readOnly || busy || !dirty" data-testid="t24-propose-patch" @click="emit('propose')">
        {{ copyFor('prep.t24.revision.propose') }}
      </button>
      <button v-if="hasPendingProposal" type="button" class="toolbar__button" :disabled="readOnly || busy || blockedByConflict" data-testid="t24-apply-patch" @click="emit('apply')">
        {{ copyFor('prep.t24.revision.apply') }}
      </button>
      <button v-if="hasPendingProposal" type="button" class="toolbar__button toolbar__button--quiet" :disabled="busy" data-testid="t24-reject-patch" @click="emit('reject')">
        {{ copyFor('prep.t24.revision.reject') }}
      </button>
      <button v-if="dirty" type="button" class="toolbar__button toolbar__button--quiet" :disabled="busy" data-testid="t24-discard-draft" @click="emit('discard')">
        {{ copyFor('prep.t24.revision.discard') }}
      </button>
      <button type="button" class="toolbar__button toolbar__button--quiet" :disabled="busy" data-testid="t24-reload" @click="emit('reload')">
        {{ copyFor('prep.t24.revision.reload') }}
      </button>
      <button
        v-if="revisions.length"
        type="button"
        class="toolbar__button toolbar__button--quiet"
        :aria-expanded="historyOpen"
        data-testid="t24-revision-history-toggle"
        @click="historyOpen = !historyOpen"
      >
        {{ copyFor('prep.t24.revision.history') }}
      </button>
    </section>

    <!-- 版本历史：来自服务端 `listRevisions`；撤销 = 服务端生成**新** revision（不做本地数组撤销） -->
    <section v-if="historyOpen" class="history" data-testid="t24-revision-history" :aria-label="copyFor('prep.t24.revision.history')">
      <p v-if="!revisions.length" class="history__empty">{{ copyFor('prep.t24.revision.history_empty') }}</p>
      <ol v-else class="history__list">
        <li v-for="item in revisions" :key="item.revision" :data-revision="item.revision" class="history__item">
          <span class="history__rev">{{
            copyFormat('prep.t24.revision.item', item.revision)
          }}</span>
          <span class="history__kind">{{ copyFor(`prep.t24.revision.kind.${item.kind}`) }}</span>
          <span class="history__summary">{{ item.summary || copyFor('prep.t24.conflict.empty_value') }}</span>
          <span v-if="item.actorId" class="history__actor">{{ item.actorId }}</span>
          <button
            type="button"
            class="history__revert"
            :disabled="readOnly || busy || item.revision === revision"
            :data-testid="`t24-revert-${item.revision}`"
            @click="emit('revert', item.revision)"
          >
            {{ copyFor('prep.t24.revision.revert') }}
          </button>
        </li>
      </ol>
    </section>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { copyFor, copyFormat } from '@/features/teacher-prep-v2/i18n/copy'
import type { RevisionListItem } from '@/features/teacher-prep-v2/types/t24'

withDefaults(defineProps<{
  revision: number
  dirty?: boolean
  readOnly?: boolean
  stale?: boolean
  busy?: boolean
  /** 服务端历史（`GET .../revisions`）；为空则不显示历史入口。 */
  revisions?: RevisionListItem[]
  /** 已提交待决策的 proposal（INV-4：apply 才写 revision）。 */
  hasPendingProposal?: boolean
  pendingPatchId?: string | null
  /** 冲突未解决时 apply 必须被挡住（服务端也会 409）。 */
  blockedByConflict?: boolean
}>(), {
  dirty: false,
  readOnly: false,
  stale: false,
  busy: false,
  revisions: () => [],
  hasPendingProposal: false,
  pendingPatchId: null,
  blockedByConflict: false,
})

const emit = defineEmits<{
  (e: 'propose'): void
  (e: 'discard'): void
  (e: 'reload'): void
  (e: 'apply'): void
  (e: 'reject'): void
  (e: 'revert', revision: number): void
}>()

const historyOpen = ref(false)
</script>

<style scoped>
.toolbar { display: flex; align-items: center; gap: var(--ma-space-2); flex-wrap: wrap; padding: var(--ma-space-2) 0; border-bottom: 1px solid var(--ma-surface-border); }
.toolbar__revision, .toolbar__state, .toolbar__pending { color: var(--ma-text-muted); font-size: var(--ma-font-size-body); }
.toolbar__state { color: var(--ma-status-warning); }
.toolbar__pending { color: var(--ma-action-primary); }
.toolbar__button { min-height: 30px; padding: 0 var(--ma-space-3); border: 1px solid var(--ma-action-primary); border-radius: var(--ma-radius-btn); background: var(--ma-action-primary); color: var(--ma-color-white-000); cursor: pointer; font-size: var(--ma-font-size-body); }
.toolbar__button--quiet { border-color: var(--ma-surface-border); background: var(--ma-surface-default); color: var(--ma-text-body); }
.toolbar__button:disabled { cursor: not-allowed; opacity: .55; }
.toolbar__button:focus-visible { outline: 2px solid var(--ma-focus-ring); outline-offset: 1px; }
.history { margin-top: var(--ma-space-2); padding: var(--ma-space-2); border: 1px solid var(--ma-surface-border); border-radius: var(--ma-radius-card); background: var(--ma-surface-subtle); }
.history__empty { margin: 0; color: var(--ma-text-muted); }
.history__list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: var(--ma-space-1); }
.history__item { display: flex; align-items: baseline; gap: var(--ma-space-2); flex-wrap: wrap; padding: var(--ma-space-1) 0; border-bottom: 1px solid var(--ma-surface-border); }
.history__rev { font-family: var(--ma-font-family-mono); color: var(--ma-text-primary); }
.history__kind { color: var(--ma-action-primary); }
.history__summary, .history__actor { color: var(--ma-text-muted); }
.history__summary { flex: 1 1 200px; overflow-wrap: anywhere; }
.history__revert { margin-left: auto; min-height: 26px; padding: 0 var(--ma-space-2); border: 1px solid var(--ma-surface-border); border-radius: var(--ma-radius-btn); background: var(--ma-surface-default); color: var(--ma-text-body); cursor: pointer; }
.history__revert:disabled { cursor: not-allowed; opacity: .55; }
</style>
