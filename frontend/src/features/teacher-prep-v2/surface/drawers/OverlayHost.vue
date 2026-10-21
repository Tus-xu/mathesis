<template>
  <!--
    T22.1 · 按需互斥面板宿主（UX-CONTRACT D/E）。
    **结构上保证互斥**：全页只有一个 drawer 实例，按 `activeOverlay` 切换内容，
    同一时刻 `[data-testid^="prep-drawer-"]` 数量恒 ≤ 1。
    错误聚焦态下 `activeOverlay` 被 projection 强制置为 none → 不会出现重复主错误。
  -->
  <n-drawer
    :show="overlay !== 'none'"
    :width="420"
    placement="right"
    :auto-focus="true"
    @update:show="onClose"
  >
    <n-drawer-content :title="title" closable @close="onClose">
      <!-- evidence -->
      <section v-if="overlay === 'evidence'" data-testid="prep-drawer-evidence">
        <p class="oh__hint">{{ copyFor('prep.overlay.evidence_hint') }}</p>
        <ul v-if="evidence.length" class="oh__list">
          <li v-for="(e, i) in evidence" :key="`${String(e.ref)}-${i}`" class="oh__item">
            <div class="oh__item-head">
              <span class="oh__item-kind">{{ String(e.kind ?? '—') }}</span>
              <code class="oh__item-ref">{{ String(e.ref ?? '') }}</code>
            </div>
            <p class="oh__item-text">{{ String(e.snippet ?? '') }}</p>
          </li>
        </ul>
        <p v-else class="oh__empty" data-testid="prep-evidence-empty">
          {{ copyFor('prep.evidence.empty') }}
        </p>
      </section>

      <!-- version history -->
      <section v-else-if="overlay === 'version_history'" data-testid="prep-drawer-version">
        <p class="oh__hint">{{ copyFor('prep.overlay.revision_hint_prefix') }}：rev {{ revision }}{{ copyFor('prep.overlay.revision_hint_suffix') }}</p>
        <ul class="oh__list">
          <li class="oh__item">
            <div class="oh__item-head">
              <span class="oh__item-kind">{{ copyFor('prep.overlay.current') }}</span>
              <code class="oh__item-ref">rev {{ revision }}</code>
            </div>
          </li>
        </ul>
        <p class="oh__todo" data-coming-from-contract="version_history">
          {{ copyFor('prep.overlay.revision_todo') }}
        </p>
      </section>

      <!-- qa -->
      <section v-else-if="overlay === 'qa'" data-testid="prep-drawer-qa">
        <p class="oh__todo" data-coming-from-contract="qa">
          {{ copyFor('prep.overlay.qa_todo') }}
        </p>
      </section>

      <!-- export -->
      <section v-else-if="overlay === 'export'" data-testid="prep-drawer-export">
        <p class="oh__todo" data-coming-from-contract="export">
          {{ copyFor('prep.overlay.export_todo') }}
        </p>
      </section>
    </n-drawer-content>
  </n-drawer>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { NDrawer, NDrawerContent } from 'naive-ui'
import { copyFor } from '@/features/teacher-prep-v2/i18n/copy'
import type { ActiveOverlay } from '@/features/teacher-prep-v2/projection/types'

const props = defineProps<{
  overlay: ActiveOverlay
  evidence: Array<Record<string, unknown>>
  revision: number
}>()

const emit = defineEmits<{ (e: 'close'): void }>()

const title = computed(() => {
  switch (props.overlay) {
    case 'evidence':
      return copyFor('prep.overlay.evidence')
    case 'version_history':
      return copyFor('prep.overlay.revision')
    case 'qa':
      return copyFor('prep.overlay.qa')
    case 'export':
      return copyFor('prep.overlay.export')
    default:
      return ''
  }
})

function onClose() {
  emit('close')
}
</script>

<style scoped>
.oh__hint {
  margin: 0 0 var(--ma-space-3);
  font-size: var(--ma-font-size-body);
  color: var(--ma-text-muted);
}

.oh__list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--ma-space-2);
}

.oh__item {
  padding: var(--ma-space-2);
  border: 1px solid var(--ma-surface-border);
  border-radius: var(--ma-radius-md);
  background: var(--ma-surface-subtle);
}

.oh__item-head {
  display: flex;
  align-items: center;
  gap: var(--ma-space-2);
  margin-bottom: var(--ma-space-1);
}

.oh__item-kind {
  font-size: var(--ma-font-size-body);
  color: var(--ma-text-muted);
}

.oh__item-ref {
  font-family: var(--ma-font-family-mono);
  font-size: var(--ma-font-size-body);
  color: var(--ma-text-body);
  overflow: hidden;
  text-overflow: ellipsis;
}

.oh__item-text {
  margin: 0;
  font-size: var(--ma-font-size-body);
  line-height: var(--ma-typography-body);
  color: var(--ma-text-body);
}

.oh__empty,
.oh__todo {
  margin: 0;
  font-size: var(--ma-font-size-body);
  line-height: var(--ma-typography-body);
  color: var(--ma-text-muted);
}
</style>
