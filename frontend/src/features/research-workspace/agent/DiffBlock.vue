<template>
  <div class="rw-diff">
    <div v-for="(p, i) in rendered" :key="i" class="rw-diff__patch">
      <p v-if="p.line >= 1" class="rw-diff__loc">
        <CodeSlashOutline class="rw-diff__loc-icon" aria-hidden="true" />
        <span>{{ p.file }} · 第 {{ p.line }} 行</span>
      </p>
      <!-- 注意：容器**不能**用 <pre> —— 模板里的换行/缩进会被 <pre> 保留成空白行，
           使两条 Diff 之间出现大段空隙。改用普通块级容器，逐行 `white-space: pre-wrap`。 -->
      <div class="rw-diff__code"><span v-if="p.delText" class="rw-diff__line rw-diff__line--del"><span class="rw-diff__sign">-</span><span class="rw-diff__frag">{{ p.delText }}</span></span><span v-if="p.addText" class="rw-diff__line rw-diff__line--add"><span class="rw-diff__sign">+</span><span class="rw-diff__frag">{{ p.addText }}</span></span><span v-if="!p.delText && !p.addText && p.reason" class="rw-diff__line rw-diff__line--note">{{ p.reason }}</span></div>
    </div>
    <div v-if="pending" class="rw-diff__confirm">
      <button type="button" class="rw-diff__btn rw-diff__btn--accept" @click="emit('accept')">接受修改</button>
      <button type="button" class="rw-diff__btn rw-diff__btn--reject" @click="emit('reject')">拒绝</button>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * Diff 块（VISUAL-SPEC §4.4「建议修改 · Diff」/ 宪法 §40 AI Patch）。
 *
 * 删除行 = `--rw-danger-soft` 底 + `--rw-danger` 字；新增行 = `--rw-success-soft` 底
 * + `--rw-success` 字；等宽 12px。所有改动在此**只是预览**：真正写入必须由用户
 * 通过「接受修改 / 应用修复」显式确认（宪法 §32/§40），`拒绝` 则丢弃。
 *
 * `before/after` 直接来自后端 `patches[]`；当后端未给出 `before`（如某些
 * LaTeX 错误补丁），可由上层通过 `lineResolver` 注入**真实的原文行**用于展示
 * （真实数据，绝不臆造改动内容）。
 */
import { computed } from 'vue'
import { CodeSlashOutline } from '@vicons/ionicons5'
import type { ResultPatch } from '../api/types'

const props = withDefaults(
  defineProps<{
    patches: ResultPatch[]
    /** 是否显示「接受 / 拒绝」确认行（仅在有改动可应用时） */
    pending?: boolean
    /** 真实原文行解析器（patch.before 为空时兜底；返回空串表示不可得） */
    lineResolver?: (file: string, line: number) => string
  }>(),
  { pending: false, lineResolver: undefined },
)

const emit = defineEmits<{ (e: 'accept'): void; (e: 'reject'): void }>()

interface RenderedPatch {
  file: string
  line: number
  delText: string
  addText: string
  reason: string
}

const rendered = computed<RenderedPatch[]>(() =>
  (props.patches ?? []).map((p) => {
    const reason = String(p.reason ?? '').trim()
    const before = String(p.before ?? '')
    const after = String(p.after ?? '')
    let delText = before
    if (!delText && props.lineResolver) {
      delText = String(props.lineResolver(p.file, p.line) ?? '')
    }
    return {
      file: p.file || 'main.tex',
      line: p.line || 0,
      delText,
      addText: after,
      reason,
    }
  }),
)
</script>

<style scoped>
.rw-diff {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.rw-diff__patch {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.rw-diff__loc {
  display: flex;
  align-items: center;
  gap: 5px;
  margin: 0;
  font-size: var(--rw-fs-sm);
  color: var(--rw-text-2);
}
.rw-diff__loc-icon {
  width: 14px;   /* Diff 位置行前图标 14px（原仅 font-size） */
  height: 14px;
  font-size: 14px;
  color: var(--rw-primary);
}
.rw-diff__code {
  margin: 0;
  border-radius: var(--rw-radius);
  overflow: hidden;
  font-family: var(--rw-font-mono);
  font-size: var(--rw-fs-sm);
  line-height: 1.6;
}
.rw-diff__line {
  display: block;
  padding: 2px 8px;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.rw-diff__sign {
  display: inline-block;
  width: 12px;
  margin-right: 6px;
  user-select: none;
}
.rw-diff__frag {
  border-radius: 2px;
}
.rw-diff__line--del {
  background: var(--rw-danger-soft);
  color: var(--rw-danger);
}
.rw-diff__line--del .rw-diff__frag {
  /* 「被改片段加深底」：以字重强调代替字面色值（VISUAL-SPEC §7.9 禁字面色值） */
  font-weight: var(--rw-fw-semibold);
}
.rw-diff__line--add {
  background: var(--rw-success-soft);
  color: var(--rw-success);
}
.rw-diff__line--add .rw-diff__frag {
  font-weight: var(--rw-fw-semibold);
}
.rw-diff__line--note {
  background: var(--rw-inset);
  color: var(--rw-text-2);
}
.rw-diff__confirm {
  display: flex;
  gap: 8px;
}
.rw-diff__btn {
  height: 30px;
  padding: 0 14px;
  border-radius: var(--rw-radius);
  font-size: var(--rw-fs-body);
  font-weight: var(--rw-fw-medium);
  cursor: pointer;
}
.rw-diff__btn--accept {
  border: var(--rw-border-w) solid var(--rw-primary);
  background: var(--rw-primary);
  color: var(--rw-surface);
}
.rw-diff__btn--reject {
  border: var(--rw-border-w) solid var(--rw-border-strong);
  background: var(--rw-surface);
  color: var(--rw-text);
}

/* ── WRITE 314px 紧凑态：Diff 等宽 11px、收紧内距（VISUAL-SPEC §1.5）── */
@container rw-agent (max-width: 400px) {
  .rw-diff__code {
    font-size: var(--rw-fs-xs);
  }
  .rw-diff__line {
    padding: 2px 6px;
  }
}
</style>
