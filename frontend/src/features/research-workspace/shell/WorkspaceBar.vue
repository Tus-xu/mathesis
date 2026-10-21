<template>
  <!--
    工作区条（WRITE 专用）—— 2026-09-18 用户指令后的**收紧形态**。

    原形态：**只占中栏宽度**的高 48px 整条，左端放 `阅读|写作` 分段控件、右端放编译控制条
    （黄金图实测：分段控件 pill 落在 x≈413~510、编译控制条 x≈936~1351）。

    现形态：分段控件已上移到顶栏（见 `shell/GlobalHeader.vue`，用户要求「跟大标题平齐」），
    本组件**只剩编译控制条**，故整条 48px 不再占用 —— 由 Shell 以 `.rw-mid__ccb` 把本组件
    **浮在中栏顶部居右**（与编辑器文件 Tab 条同一行 y=62），把那 48px 全额还给编辑器/预览。
    本组件因此不再自带高度/底边框/左右 padding（定位与外距由 Shell 的 `.rw-mid__ccb` 承担）。
  -->
  <div class="rw-bar">
    <CompileControlBar
      :engine="engine"
      :status="compileStatus"
      :last-compiled-at="lastCompiledAt"
      :disabled="disabled"
      @recompile="emit('recompile')"
    />
  </div>
</template>

<script setup lang="ts">
/**
 * 工作区条（现为「编译控制条」容器）。只向上抛事件，业务由 Shell 落到 store。
 *
 * `阅读|写作` 分段控件已移除（上移到顶栏）；`mode` / `workspace` 两个 props 随之不再需要。
 */
import CompileControlBar from './CompileControlBar.vue'
import type { CompileStatus } from '../api/types'

withDefaults(
  defineProps<{
    engine?: string
    compileStatus?: CompileStatus | 'idle'
    lastCompiledAt?: string | null
    disabled?: boolean
  }>(),
  {
    engine: 'XeLaTeX',
    compileStatus: 'idle',
    lastCompiledAt: null,
    disabled: false,
  },
)
const emit = defineEmits<{
  (e: 'recompile'): void
}>()
</script>

<style scoped>
/* 紧凑行：只按内容宽度排布，垂直居中；无背景/边框/固定高度（由 Shell 的浮层承担定位）。 */
.rw-bar {
  display: inline-flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
}
</style>
