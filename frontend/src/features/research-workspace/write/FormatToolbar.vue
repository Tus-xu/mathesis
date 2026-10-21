<template>
  <div class="rw-fmt" role="toolbar" aria-label="LaTeX 格式工具条">
    <button
      type="button"
      class="rw-fmt__btn"
      aria-label="插入章节结构"
      title="插入章节结构 \\section"
      @click="emit('insert', 'section')"
    >
      <DocumentTextOutline aria-hidden="true" />
    </button>

    <span class="rw-fmt__sep" aria-hidden="true" />

    <button type="button" class="rw-fmt__btn" aria-label="撤销" title="撤销 (Ctrl+Z)" @click="emit('undo')">
      <ArrowUndoOutline aria-hidden="true" />
    </button>
    <button type="button" class="rw-fmt__btn" aria-label="重做" title="重做 (Ctrl+Y)" @click="emit('redo')">
      <ArrowRedoOutline aria-hidden="true" />
    </button>

    <span class="rw-fmt__sep" aria-hidden="true" />

    <button type="button" class="rw-fmt__btn rw-fmt__btn--glyph" aria-label="加粗" title="加粗 \\textbf" @click="emit('wrap', 'bold')">
      <b>B</b>
    </button>
    <button type="button" class="rw-fmt__btn rw-fmt__btn--glyph" aria-label="斜体" title="斜体 \\textit" @click="emit('wrap', 'italic')">
      <i>I</i>
    </button>

    <span class="rw-fmt__sep" aria-hidden="true" />

    <button type="button" class="rw-fmt__btn rw-fmt__btn--mono" aria-label="插入花括号" title="分组 {}" @click="emit('wrap', 'brace')">
      {}
    </button>
    <button type="button" class="rw-fmt__btn rw-fmt__btn--mono" aria-label="插入行内公式" title="行内公式 $…$" @click="emit('wrap', 'math')">
      π
    </button>
    <button type="button" class="rw-fmt__btn rw-fmt__btn--mono" aria-label="插入环境" title="插入环境 \\begin{}" @click="emit('wrap', 'env')">
      ⌐
    </button>

    <span class="rw-fmt__sep" aria-hidden="true" />

    <button type="button" class="rw-fmt__btn" aria-label="插入链接" title="插入链接 \\href" @click="emit('insert', 'link')">
      <LinkOutline aria-hidden="true" />
    </button>
    <button type="button" class="rw-fmt__btn" aria-label="插入图片" title="插入图片 \\includegraphics" @click="emit('insert', 'image')">
      <ImageOutline aria-hidden="true" />
    </button>
    <button type="button" class="rw-fmt__btn" aria-label="插入表格" title="插入表格" @click="emit('insert', 'table')">
      <GridOutline aria-hidden="true" />
    </button>

    <span class="rw-fmt__sep" aria-hidden="true" />

    <button type="button" class="rw-fmt__btn" aria-label="查找" title="在此文件中查找" @click="emit('search')">
      <SearchOutline aria-hidden="true" />
    </button>
    <button type="button" class="rw-fmt__btn" aria-label="取消缩进" title="取消缩进" @click="emit('outdent')">
      <ReturnUpBackOutline aria-hidden="true" />
    </button>

    <!-- 行号开关：黄金图位于格式条**最右端**（`margin-left:auto` 右靠），主蓝实心 toggle -->
    <span class="rw-fmt__sep rw-fmt__sep--tail" aria-hidden="true" />
    <label class="rw-fmt__switch">
      <span class="rw-fmt__switch-label">行号</span>
      <button
        type="button"
        class="rw-fmt__toggle"
        :class="{ 'rw-fmt__toggle--on': lineNumbers }"
        role="switch"
        :aria-checked="lineNumbers"
        aria-label="行号显示开关"
        @click="emit('update:lineNumbers', !lineNumbers)"
      >
        <span class="rw-fmt__knob" aria-hidden="true" />
      </button>
    </label>
  </div>
</template>

<script setup lang="ts">
/**
 * LaTeX 格式工具条（VISUAL-SPEC §5.2，h36）。
 * `↺ ↻ ｜ B I {} π ⌐ ｜ 🔗 🖼 ⊞ ｜ 🔍 ｜ 行号 [开关] ｜ ⇤`
 *
 * `行号` 为**主蓝实心 toggle**（开态）。所有按钮只抛事件，由 `LatexEditorPane` 落到编辑器命令。
 */
import {
  ArrowRedoOutline,
  ArrowUndoOutline,
  DocumentTextOutline,
  GridOutline,
  ImageOutline,
  LinkOutline,
  ReturnUpBackOutline,
  SearchOutline,
} from '@vicons/ionicons5'

export type WrapKind = 'bold' | 'italic' | 'brace' | 'math' | 'env'
export type InsertKind = 'link' | 'image' | 'table' | 'section'

withDefaults(defineProps<{ lineNumbers?: boolean }>(), { lineNumbers: true })

const emit = defineEmits<{
  (e: 'undo'): void
  (e: 'redo'): void
  (e: 'wrap', kind: WrapKind): void
  (e: 'insert', kind: InsertKind): void
  (e: 'search'): void
  (e: 'outdent'): void
  (e: 'update:lineNumbers', v: boolean): void
}>()
</script>

<style scoped>
/*
 * LaTeX 格式工具条（黄金图 `WRITE-GOLDEN` 实测）：
 *   白底条带 y156~202（h≈46），内容带 y170~187（图标/文字 ≈18px，垂直居中）。
 *   分组：`[文档] ｜ ↺ ↻ ｜ B I ｜ {} π ⌐ ｜ 🔗 🖼 ⊞ ｜ 🔍 ⇤ ……（弹性留白）…… ｜ 行号 [主蓝 toggle]`
 *   图标按钮 30×30（黄金图相邻图标中心距 ≈36px），图标本体 18px；组间 1px 竖分隔线（h18）。
 *   行号开关在黄金图中贴**右端**（toggle 右边框 x≈893 ≈ 编辑器栏右缘 896），故用 `margin-left:auto`。
 */
.rw-fmt {
  display: flex;
  align-items: center;
  gap: 0;
  height: 46px;
  padding: 0 6px 0 16px;
  flex: 0 0 auto;
  background: var(--rw-surface);
  border-bottom: var(--rw-border-w) solid var(--rw-border);
  overflow: hidden;
}
.rw-fmt__btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  flex: 0 0 auto;
  border: none;
  border-radius: var(--rw-radius-md);
  background: transparent;
  color: var(--rw-text);
  cursor: pointer;
  font-family: var(--rw-font-sans);
  font-size: var(--rw-fs-h2);
}
.rw-fmt__btn:hover {
  background: var(--rw-surface-2);
  color: var(--rw-primary);
}
.rw-fmt__btn :deep(svg) {
  width: 18px;
  height: 18px;
}
.rw-fmt__btn--glyph b,
.rw-fmt__btn--glyph i {
  font-size: var(--rw-fs-h1);
  color: var(--rw-text);
}
.rw-fmt__btn--mono {
  font-family: var(--rw-font-mono);
  font-size: var(--rw-fs-h3);
  color: var(--rw-text);
}
.rw-fmt__sep {
  width: 1px;
  height: 18px;
  margin: 0 6px;
  flex: 0 0 auto;
  background: var(--rw-border);
}
/* 行号组右靠（黄金图实测） */
.rw-fmt__sep--tail {
  margin-left: auto;
}
.rw-fmt__switch {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  flex: 0 0 auto;
}
.rw-fmt__switch-label {
  font-size: var(--rw-fs-h3);
  color: var(--rw-text);
}
.rw-fmt__toggle {
  position: relative;
  width: 34px;
  height: 18px;
  padding: 0;
  border: none;
  border-radius: 9px;
  background: var(--rw-border-strong);
  cursor: pointer;
  transition: background-color .14s ease;
}
.rw-fmt__toggle--on {
  background: var(--rw-primary);
}
.rw-fmt__knob {
  position: absolute;
  top: 2px;
  left: 2px;
  width: 14px;
  height: 14px;
  border-radius: 50%;
  background: var(--rw-surface);
  transition: transform .14s ease;
}
.rw-fmt__toggle--on .rw-fmt__knob {
  transform: translateX(16px);
}
</style>
