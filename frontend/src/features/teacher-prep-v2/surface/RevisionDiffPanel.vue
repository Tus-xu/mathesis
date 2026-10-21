<template>
  <!--
    T22.1 · ProposalDiff 面板（UX-CONTRACT D）。
    先展示**变更原因 / 依据 / diff**，教师**接受后**才用 expected_revision 应用 patch；
    禁止无提示全文重写；接受前 diff 不落库。
  -->
  <section class="diff" data-testid="prep-revision-diff">
    <header class="diff__head">
      <h3 class="diff__title">{{ copyFor('prep.diff.title') }}</h3>
      <span class="diff__badge">{{ copyFor('prep.diff.badge') }}</span>
    </header>

    <p class="diff__reason">
      <span class="diff__label">{{ copyFor('prep.diff.reason') }}</span>
      {{ diff.reason }}
    </p>

    <p class="diff__evidence">
      <span class="diff__label">{{ copyFor('prep.diff.sources') }}</span>
      {{ diff.evidenceCount }} {{ copyFor('prep.diff.evidence_count_suffix') }}
      <button type="button" class="diff__link" data-testid="prep-diff-evidence" @click="emit('open-evidence')">
        {{ copyFor('prep.diff.open_evidence') }}
      </button>
    </p>

    <ul class="diff__list">
      <li v-for="(c, i) in diff.changes" :key="`${c.path}-${i}`" class="diff__change">
        <div class="diff__path">{{ c.path }}</div>
        <div class="diff__row diff__row--before">
          <span class="diff__tag">{{ copyFor('prep.diff.before') }}</span>
          <span class="diff__text">{{ c.before || copyFor('prep.diff.empty') }}</span>
        </div>
        <div class="diff__row diff__row--after">
          <span class="diff__tag">{{ copyFor('prep.diff.after') }}</span>
          <span class="diff__text">{{ c.after || copyFor('prep.diff.empty') }}</span>
        </div>
      </li>
    </ul>

    <footer class="diff__actions">
      <button
        type="button"
        class="diff__accept"
        :disabled="busy"
        data-testid="prep-diff-accept"
        @click="emit('accept')"
      >
        {{ copyFor('prep.diff.accept') }}
      </button>
      <button type="button" class="diff__reject" :disabled="busy" data-testid="prep-diff-reject" @click="emit('reject')">
        {{ copyFor('prep.diff.reject') }}
      </button>
      <span class="diff__note">{{ copyFor('prep.diff.note') }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { copyFor } from '@/features/teacher-prep-v2/i18n/copy'

export interface DiffView {
  reason: string
  evidenceCount: number
  changes: Array<{ path: string; before: string; after: string }>
}

const props = withDefaults(defineProps<{ diff: DiffView; busy?: boolean }>(), { busy: false })
const emit = defineEmits<{ (e: 'accept'): void; (e: 'reject'): void; (e: 'open-evidence'): void }>()
void props
</script>

<style scoped>
.diff {
  margin-top: var(--ma-space-4);
  padding: var(--ma-space-3);
  border: 1px solid var(--ma-surface-border-strong);
  border-radius: var(--ma-radius-card);
  background: var(--ma-surface-default);
  box-shadow: var(--ma-elevation-sm);
}

.diff__head {
  display: flex;
  align-items: center;
  gap: var(--ma-space-2);
  margin-bottom: var(--ma-space-2);
}

.diff__title {
  margin: 0;
  font-size: var(--ma-font-size-body);
  color: var(--ma-text-primary);
}

.diff__badge {
  height: 20px;
  padding: 0 var(--ma-space-2);
  border-radius: var(--ma-radius-tag);
  background: var(--ma-surface-subtle);
  color: var(--ma-text-muted);
  font-size: var(--ma-font-size-body);
}

.diff__reason,
.diff__evidence {
  margin: 0 0 var(--ma-space-2);
  font-size: var(--ma-font-size-body);
  line-height: var(--ma-typography-body);
  color: var(--ma-text-body);
}

.diff__label {
  display: inline-block;
  margin-right: var(--ma-space-2);
  color: var(--ma-text-muted);
}

.diff__link {
  border: 0;
  background: transparent;
  padding: 0;
  margin-left: var(--ma-space-2);
  color: var(--ma-action-primary);
  font-size: var(--ma-font-size-body);
  cursor: pointer;
  text-decoration: underline;
}

.diff__list {
  list-style: none;
  margin: 0 0 var(--ma-space-3);
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--ma-space-2);
}

.diff__change {
  border: 1px solid var(--ma-surface-border);
  border-radius: var(--ma-radius-md);
  padding: var(--ma-space-2);
  background: var(--ma-surface-subtle);
}

.diff__path {
  font-family: var(--ma-font-family-mono);
  font-size: var(--ma-font-size-body);
  color: var(--ma-text-muted);
  margin-bottom: var(--ma-space-1);
}

.diff__row {
  display: flex;
  gap: var(--ma-space-2);
  font-size: var(--ma-font-size-body);
  line-height: var(--ma-typography-body);
}

.diff__tag {
  flex: 0 0 auto;
  color: var(--ma-text-muted);
}

.diff__row--before .diff__text {
  color: var(--ma-text-muted);
  text-decoration: line-through;
}

.diff__row--after .diff__text {
  color: var(--ma-text-primary);
}

.diff__actions {
  display: flex;
  align-items: center;
  gap: var(--ma-space-2);
  flex-wrap: wrap;
}

.diff__accept {
  height: 32px;
  padding: 0 var(--ma-space-3);
  border: 0;
  border-radius: var(--ma-radius-btn);
  background: var(--ma-action-primary);
  color: var(--ma-color-white-000);
  font-size: var(--ma-font-size-body);
  cursor: pointer;
}

.diff__reject {
  height: 32px;
  padding: 0 var(--ma-space-3);
  border: 1px solid var(--ma-surface-border);
  border-radius: var(--ma-radius-btn);
  background: var(--ma-surface-default);
  color: var(--ma-text-body);
  font-size: var(--ma-font-size-body);
  cursor: pointer;
}

.diff__note {
  font-size: var(--ma-font-size-body);
  color: var(--ma-text-muted);
}

.diff__accept:focus-visible,
.diff__reject:focus-visible,
.diff__link:focus-visible {
  outline: none;
  outline: 2px solid var(--ma-focus-ring);
  outline-offset: 1px;
}
</style>
