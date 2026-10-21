<template>
  <!-- socratic_complete 完成卡：最终答案 + 分步解答 + 结果徽标 + 统计小字 -->
  <div class="soc-complete">
    <div class="sc-head">
      <span class="sc-title">🎉 本次引导完成</span>
      <span class="tag" :class="outcomeCls">{{ outcomeText }}</span>
      <span v-if="card.verified" class="tag green">✓ 已验证</span>
    </div>

    <div v-if="card.final_answer" class="sc-answer">
      <div class="sc-answer-label">最终答案</div>
      <MarkdownView :text="tex(card.final_answer)" />
    </div>

    <div v-if="steps.length" class="sc-steps">
      <div class="sc-steps-label">分步解答（{{ steps.length }} 步）</div>
      <div v-for="(s, i) in steps" :key="i" class="sc-step">
        <div class="sc-step-no">{{ i + 1 }}</div>
        <div class="sc-step-body">
          <MarkdownView :text="tex(s.assertion)" />
          <div v-if="s.reason" class="sc-reason">
            <MarkdownView :text="tex(s.reason)" />
          </div>
        </div>
      </div>
    </div>

    <div class="sc-meta">
      <span v-if="diffText">难度「{{ diffText }}」</span>
      <span v-if="durationText">用时 {{ durationText }}</span>
      <span v-if="hintTotal">提示 {{ hintTotal }} 次</span>
      <span v-if="answerRequests">查看答案请求 {{ answerRequests }} 次</span>
    </div>

    <!-- v1.4 完成后续学按钮行（对齐愿景 03：讲解完必须有下一步出口，形成学习闭环） -->
    <div class="sc-actions">
      <button class="sc-act primary" @click="$emit('act', 'variant')">🔄 举一反三 · 再来一组变式</button>
      <button class="sc-act" @click="$emit('act', 'errorBook')">📖 查看错题本</button>
      <button class="sc-act" @click="$emit('act', 'mastery')">📈 看看我的学情</button>
      <button class="sc-act" @click="$emit('act', 'finish')">🏁 今天就到这</button>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import MarkdownView from '@/components/MarkdownView.vue'
import { wrapBareLatex } from '@/utils/latex'
import { difficultyName } from '@/components/student/labels'

const props = defineProps({
  card: { type: Object, required: true },
})
// v1.4：完成卡按钮行（variant/errorBook/mastery/finish，由 MessageBubble → ChatView 承接）
defineEmits(['act'])

// 裸 LaTeX（无 $ 定界符）自动包装后再渲染，避免 \dfrac 等源码直接显示
// v1.6：剥掉模型漏进步骤文本的 [[STEP]] / [[STEP] 标记（实测 step reason 里出现裸标记）
const stripStepMarks = (v) => String(v ?? '').replace(/\s*\[\[STEP\]\]?/g, ' ')
const tex = (v) => wrapBareLatex(stripStepMarks(v))

const steps = computed(() => (Array.isArray(props.card.steps) ? props.card.steps : []))

const outcomeText = computed(
  () => ({ independent: '独立完成', guided: '引导完成', revealed: '查看解答' }[props.card.outcome] || '已完成')
)
const outcomeCls = computed(
  () => ({ independent: 'green', guided: 'cyan', revealed: 'orange' }[props.card.outcome] || 'gray')
)

const diffText = computed(() => difficultyName(props.card.difficulty))

const durationText = computed(() => {
  const s = Number(props.card.duration_s)
  if (!Number.isFinite(s)) return ''
  if (s < 60) return `${s} 秒`
  return `${Math.floor(s / 60)} 分 ${s % 60} 秒`
})

const hintTotal = computed(() => Number(props.card.hint_stats?.total) || 0)
const answerRequests = computed(() => Number(props.card.hint_stats?.answer_requests) || 0)
</script>

<style scoped>
.soc-complete {
  margin-top: 10px; padding: 12px 14px; border-radius: var(--radius-md);
  border: 1px solid rgba(16, 185, 129, 0.35); background: rgba(236, 253, 245, 0.55);
}
.sc-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 10px; }
.sc-title { font-size: 13px; font-weight: 700; color: #059669; }

.sc-answer {
  padding: var(--space-3) var(--space-4); border-radius: var(--radius-md); margin-bottom: var(--space-3);
  background: var(--primary-subtle);
  border: 1px solid var(--primary-border);
}
.sc-answer-label { font-size: 11px; font-weight: 700; color: var(--primary); margin-bottom: 4px; }

.sc-steps-label { font-size: 12px; font-weight: 700; color: var(--text-secondary); margin-bottom: 8px; }
.sc-step { display: flex; gap: 8px; margin-bottom: 10px; }
.sc-step-no {
  width: 20px; height: 20px; border-radius: 50%; flex-shrink: 0; margin-top: 1px;
  background: var(--primary); color: #fff; font-size: 11px; font-weight: 700;
  display: flex; align-items: center; justify-content: center;
}
.sc-step-body { flex: 1; min-width: 0; font-size: 13px; }
.sc-reason {
  margin-top: 4px; padding: 6px 10px; border-radius: var(--radius-sm);
  background: rgba(255, 255, 255, 0.7); border-left: 3px solid var(--primary-light);
  font-size: 12px; color: var(--text-secondary);
}
.sc-reason :deep(.md-body) { font-size: 12px; color: var(--text-secondary); }

.sc-meta {
  display: flex; flex-wrap: wrap; gap: 10px; font-size: 11px; color: var(--text-muted);
  border-top: 1px dashed var(--border); padding-top: 8px;
}

/* v1.4 完成卡按钮行 */
.sc-actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; }
.sc-act {
  padding: 7px 14px; border-radius: var(--radius-md); cursor: pointer;
  border: 1px solid var(--border); background: var(--bg-white);
  font-size: 12.5px; font-weight: 600; color: var(--text-secondary);
  transition: all var(--transition-fast);
}
.sc-act:hover { border-color: var(--primary-border); color: var(--primary); background: var(--primary-subtle); }
.sc-act.primary {
  border-color: var(--primary-border); color: #fff;
  background: linear-gradient(135deg, var(--primary, #5b5bd6) 0%, #7c7ce0 100%);
}
.sc-act.primary:hover { filter: brightness(1.06); color: #fff; }
</style>
