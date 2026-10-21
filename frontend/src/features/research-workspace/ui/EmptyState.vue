<template>
  <div class="rw-empty" role="status">
    <span class="rw-empty__icon" aria-hidden="true">
      <slot name="icon">
        <component :is="icon" />
      </slot>
    </span>
    <p class="rw-empty__title">{{ title }}</p>
    <p v-if="description" class="rw-empty__desc">{{ description }}</p>
    <button
      v-if="actionText"
      type="button"
      class="rw-empty__action"
      @click="emit('action')"
    >
      {{ actionText }}
    </button>
  </div>
</template>

<script setup lang="ts">
/**
 * 诚实空态（VISUAL-SPEC §6 / ACCEPTANCE E3）。
 *
 * 任何区域没有真实数据时都必须渲染本组件并**给出下一步动作**，
 * 严禁用假数据、假成功态填充（宪法 §70）。
 */
import type { Component } from 'vue'
import { InformationCircleOutline } from '@vicons/ionicons5'

withDefaults(
  defineProps<{
    /** 主标题（必填，说明「这里是什么」） */
    title: string
    /** 说明文案（可选，说明「为什么为空」） */
    description?: string
    /** 下一步动作按钮文案（可选；给了才渲染按钮） */
    actionText?: string
    /** 图标组件（默认中性信息图标） */
    icon?: Component
  }>(),
  {
    description: '',
    actionText: '',
    icon: InformationCircleOutline,
  },
)

const emit = defineEmits<{ (e: 'action'): void }>()
</script>

<style scoped>
.rw-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 24px 18px;
  text-align: center;
}
.rw-empty__icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  border-radius: var(--rw-radius-md);
  color: var(--rw-text-3);
  background: var(--rw-surface-2);
  font-size: 20px;
}
/* 空态图标本体 20px（容器 34×34）；slot 内可能为 @vicons <svg>，须显式约束 */
.rw-empty__icon :deep(svg) {
  width: 20px;
  height: 20px;
}
.rw-empty__title {
  margin: 2px 0 0;
  font-size: var(--rw-fs-body);
  font-weight: var(--rw-fw-semibold);
  color: var(--rw-text);
}
.rw-empty__desc {
  margin: 0;
  max-width: 260px;
  font-size: var(--rw-fs-sm);
  line-height: var(--rw-lh-body);
  color: var(--rw-text-2);
}
.rw-empty__action {
  margin-top: 10px;
  height: 28px;
  padding: 0 14px;
  border: none;
  border-radius: var(--rw-radius);
  background: var(--rw-primary);
  color: var(--rw-surface);
  font-size: var(--rw-fs-sm);
  font-weight: var(--rw-fw-semibold);
  cursor: pointer;
}
.rw-empty__action:hover {
  background: var(--rw-primary-hover);
}
</style>
