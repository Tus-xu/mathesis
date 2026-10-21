<script setup lang="ts">
/**
 * ④ 板书设计（黄金图：三列虚线分隔 文字要点 | 数学图形 | 公式区）。
 * §F.3：三列均须有内容方可判 PASS；`math_object` 为 null 时中列给诚实空态（不贴图占位）。
 */
import { computed } from 'vue'
import KatexText from '@/features/teacher-prep-center/math/KatexText.vue'
import BoardMathFigure from './BoardMathFigure.vue'
import type { PlanBoardDesign } from '@/features/teacher-prep-center/api/lessonPlanApi'

const props = defineProps<{ board: PlanBoardDesign | null }>()

const textPoints = computed(() =>
  (props.board?.text_points ?? []).map((it) => String(it.text ?? '').trim()).filter((t) => t.length > 0),
)
const formulas = computed(() =>
  (props.board?.formulas ?? []).map((it) => String(it.text ?? '').trim()).filter((t) => t.length > 0),
)
const mathObject = computed(() => props.board?.math_object ?? null)

const hasAny = computed(() => textPoints.value.length > 0 || formulas.value.length > 0 || !!mathObject.value)
</script>

<template>
  <div v-if="hasAny" class="pc-board">
    <div class="pc-board__col">
      <div v-if="textPoints.length" class="pc-board__texts">
        <p v-for="(t, i) in textPoints" :key="i" class="pc-board__para">
          <KatexText :text="t" />
        </p>
      </div>
      <p v-else class="pc-board__empty">文字要点待补充</p>
    </div>

    <div class="pc-board__col pc-board__col--fig">
      <BoardMathFigure v-if="mathObject" :object="mathObject" show-labels />
      <p v-else class="pc-board__empty">本课无参数化图形</p>
    </div>

    <div class="pc-board__col">
      <div v-if="formulas.length" class="pc-board__formulas">
        <div v-for="(t, i) in formulas" :key="i" class="pc-board__formula">
          <KatexText :text="t" block />
        </div>
      </div>
      <p v-else class="pc-board__empty">公式区待补充</p>
    </div>
  </div>
  <p v-else class="pc-board__placeholder">板书设计来自教学路径的板书要点与教材公式：确认教学方案后自动呈现文字要点 / 数学图形 / 公式三栏。</p>
</template>

<style scoped>
.pc-board {
  display: grid;
  grid-template-columns: 1fr 1fr 1fr;
  background: var(--pc-section-bg);
  border: 1px dashed #cdd8ea;
  border-radius: var(--pc-radius-btn);
  overflow: hidden;
}
.pc-board__col {
  padding: 14px 16px;
  min-height: 118px;
}
.pc-board__col + .pc-board__col {
  border-left: 1px dashed #cdd8ea;
}
.pc-board__col--fig {
  display: flex;
  align-items: center;
  justify-content: center;
}
.pc-board__texts {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.pc-board__para {
  margin: 0;
  font-size: 13.5px;
  line-height: 1.7;
  color: var(--pc-text-2);
}
.pc-board__formulas {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.pc-board__formula {
  font-size: 14px;
  color: var(--pc-text-2);
}
.pc-board__empty {
  margin: 0;
  font-size: 12.5px;
  color: var(--pc-text-faint);
}
.pc-board__placeholder {
  margin: 0;
  padding: 16px;
  border: 1px dashed #d5deec;
  border-radius: var(--pc-radius-btn);
  font-size: 13px;
  color: var(--pc-text-faint);
  text-align: center;
}
</style>
