<template>
  <!--
    T22.3-C · 已确认上下文 → 紧凑 chips（UX-CONTRACT A）。
    铁律：点击只「发起对话/打开小型选择器」，**绝不展开成整页表单**；
    班级只显示 `classes.name`（session.class_name），**禁止** UUID 截断冒充；
    无真实数据时渲染**诚实空提示**，不填假班级/假教材。
  -->
  <div v-if="chips.length" class="chips" data-testid="prep-context-chips">
    <button
      v-for="c in chips"
      :key="c.key"
      type="button"
      class="chips__item"
      :data-testid="`prep-chip-${c.key}`"
      @click="emit('change', c)"
    >
      <span class="chips__label">{{ c.label }}</span>
      <span class="chips__value">{{ c.value }}</span>
      <n-icon :size="13" class="chips__edit"><CreateOutline /></n-icon>
    </button>
  </div>
  <p v-else class="chips__empty" data-testid="prep-context-empty">
    {{ copyFor('prep.chips.empty') }}
  </p>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { CreateOutline } from '@vicons/ionicons5'
import { NIcon } from 'naive-ui'
import { copyFor } from '@/features/teacher-prep-v2/i18n/copy'

export interface ContextChip {
  key: string
  label: string
  value: string
  /** 点击后回填输入框的引导语（不跳表单）。 */
  prompt: string
}

const props = defineProps<{
  context: Record<string, unknown> | null
  session: Record<string, unknown> | null
}>()

const emit = defineEmits<{ (e: 'change', chip: ContextChip): void }>()

function str(v: unknown): string | null {
  return typeof v === 'string' && v.trim() ? v : null
}

const chips = computed<ContextChip[]>(() => {
  const out: ContextChip[] = []
  const ctx = props.context ?? {}
  const source = (ctx.source ?? {}) as Record<string, unknown>
  const section = (ctx.section ?? {}) as Record<string, unknown>

  const sourceTitle = str(source.title)
  if (sourceTitle) {
    out.push({
      key: 'textbook',
      label: copyFor('prep.chips.textbook'),
      value: sourceTitle,
      prompt: copyFor('prep.chips.prompt.textbook'),
    })
  }

  const sectionTitle = str(section.title) ?? str(section.section_path)
  if (sectionTitle) {
    out.push({
      key: 'section',
      label: copyFor('prep.chips.section'),
      value: sectionTitle,
      prompt: copyFor('prep.chips.prompt.section'),
    })
  }

  const kps = (ctx.knowledge_points as unknown[]) ?? []
  if (kps.length) {
    out.push({
      key: 'knowledge',
      label: copyFor('prep.chips.knowledge'),
      value: `${kps.length} ${copyFor('prep.chips.unit.items')}`,
      prompt: copyFor('prep.chips.prompt.knowledge'),
    })
  }

  const objectives = (ctx.objectives as Array<{ text?: unknown }>) ?? []
  if (objectives.length) {
    out.push({
      key: 'objectives',
      label: copyFor('prep.chips.objectives'),
      value: `${objectives.length} ${copyFor('prep.chips.unit.lines')}`,
      prompt: copyFor('prep.chips.prompt.objectives'),
    })
  }

  // 班级：只认 classes.name；无名则不显示（禁止 class_id.slice 冒充）
  const className = str((props.session ?? {}).class_name)
  if (className) {
    out.push({
      key: 'class',
      label: copyFor('prep.chips.class'),
      value: className,
      prompt: copyFor('prep.chips.prompt.class'),
    })
  }
  return out
})
</script>

<style scoped>
.chips {
  display: flex;
  flex-wrap: wrap;
  gap: var(--ma-space-1);
  padding: 0 var(--ma-space-4) var(--ma-space-2);
}

.chips__item {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 28px;
  padding: 0 var(--ma-space-2);
  border: 1px solid var(--ma-surface-border);
  border-radius: var(--ma-radius-tag);
  background: var(--ma-surface-default);
  font-size: var(--ma-font-size-body);
  color: var(--ma-text-body);
  cursor: pointer;
}

.chips__item:hover {
  border-color: var(--ma-action-primary);
  color: var(--ma-text-primary);
}

.chips__item:focus-visible {
  outline: none;
  outline: 2px solid var(--ma-focus-ring);
  outline-offset: 1px;
}

.chips__label {
  color: var(--ma-text-body);
}

.chips__value {
  color: var(--ma-text-primary);
  max-width: 220px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.chips__edit {
  color: var(--ma-text-muted);
}

.chips__empty {
  margin: 0;
  padding: 0 var(--ma-space-4) var(--ma-space-2);
  font-size: var(--ma-font-size-body);
  color: var(--ma-text-body);
}
</style>
