<script setup lang="ts">
/**
 * ③ 教学过程（黄金图：全宽表格 环节/时间/教师活动/学生活动/设计意图，浅蓝表头 + 行内 KaTeX）。
 * 行与教学路径节点（`lesson_flow`）一一对应；§F.2 `columns` 缺失时诚实退化（不编造）。
 */
import { computed } from 'vue'
import KatexText from '@/features/teacher-prep-center/math/KatexText.vue'
import type { PlanItem, PlanStage } from '@/features/teacher-prep-center/api/lessonPlanApi'

const props = defineProps<{ stages: PlanStage[] }>()

const CN_NUM = ['一', '二', '三', '四', '五', '六', '七', '八', '九', '十']

/** 单元格内容：优先结构化列；教师活动退化时回退到环节 items（真实内容），其余留空显示「—」。 */
function cellItems(primary: PlanItem[] | undefined, fallback: PlanItem[] | null | undefined): string[] {
  const pick = (arr?: PlanItem[] | null) =>
    (arr ?? []).map((it) => String(it.text ?? '').trim()).filter((t) => t.length > 0)
  if (primary && primary.length) return pick(primary)
  return fallback ? pick(fallback) : []
}

const rows = computed(() =>
  props.stages.map((s, i) => {
    const title = String(s.title ?? '').trim()
    const hasNumeral = /^[一二三四五六七八九十0-9]/.test(title)
    return {
      key: `${s.stage}-${i}`,
      name: hasNumeral ? title : `${CN_NUM[i] ?? i + 1}、${title}`,
      minutes: typeof s.duration_minutes === 'number' && s.duration_minutes > 0 ? `${s.duration_minutes} min` : '—',
      teacher: cellItems(s.columns?.teacher_activity, s.items),
      student: cellItems(s.columns?.student_activity, null),
      intent: cellItems(s.columns?.design_intent, null),
    }
  }),
)

const totalMinutes = computed(() =>
  props.stages.reduce(
    (sum, s) => sum + (typeof s.duration_minutes === 'number' && s.duration_minutes > 0 ? s.duration_minutes : 0),
    0,
  ),
)
</script>

<template>
  <div class="pc-proc">
    <div v-if="rows.length" class="pc-proc__wrap">
      <table class="pc-proc__table">
        <colgroup>
          <col style="width: 14%" />
          <col style="width: 8%" />
          <col style="width: 30%" />
          <col style="width: 25%" />
          <col style="width: 23%" />
        </colgroup>
        <thead>
          <tr>
            <th scope="col">环节</th>
            <th scope="col">时间</th>
            <th scope="col">教师活动</th>
            <th scope="col">学生活动</th>
            <th scope="col">设计意图</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in rows" :key="r.key">
            <td class="pc-proc__name">{{ r.name }}</td>
            <td class="pc-proc__time">{{ r.minutes }}</td>
            <td>
              <div v-if="r.teacher.length" class="pc-proc__cell">
                <KatexText v-for="(t, i) in r.teacher" :key="i" :text="t" />
              </div>
              <span v-else class="pc-proc__dash">—</span>
            </td>
            <td>
              <div v-if="r.student.length" class="pc-proc__cell">
                <KatexText v-for="(t, i) in r.student" :key="i" :text="t" />
              </div>
              <span v-else class="pc-proc__dash">—</span>
            </td>
            <td>
              <div v-if="r.intent.length" class="pc-proc__cell">
                <KatexText v-for="(t, i) in r.intent" :key="i" :text="t" />
              </div>
              <!-- L4：设计意图缺失不留裸「—」，给教育性空态（数据由投影层 hints/summary 补充）。 -->
              <span v-else class="pc-proc__pending">待 AI 补充设计意图：确认教学方案后自动生成</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <p v-else class="pc-proc__empty">教学过程还没有生成：确认教学路径后会逐环节填入本表。</p>
    <p v-if="rows.length && totalMinutes > 0" class="pc-proc__total">合计 {{ totalMinutes }} 分钟</p>
  </div>
</template>

<style scoped>
/* 表格外框：圆角 + 发丝线（黄金图教学过程表为全宽圆角表） */
.pc-proc__wrap {
  border: 1px solid var(--pc-line);
  border-radius: var(--pc-radius-btn);
  overflow: hidden;
}
.pc-proc__table {
  width: 100%;
  border-collapse: collapse;
  table-layout: fixed;
}
.pc-proc__table th {
  padding: 9px 12px;
  background: #eff4ff;
  border-bottom: 1px solid #e2e9f5;
  border-right: 1px solid #e2e9f5;
  font-size: 13px;
  font-weight: 600;
  color: var(--pc-navy);
  text-align: center;
}
.pc-proc__table th:last-child {
  border-right: none;
}
.pc-proc__table td {
  padding: 9px 12px;
  border-top: 1px solid var(--pc-line);
  border-right: 1px solid var(--pc-line);
  font-size: 13.5px;
  line-height: 1.65;
  color: var(--pc-text-2);
  vertical-align: top;
}
.pc-proc__table td:last-child {
  border-right: none;
}
.pc-proc__table tbody tr:first-child td {
  border-top: none;
}
.pc-proc__name {
  font-weight: 600;
  color: var(--pc-text);
}
.pc-proc__time {
  color: var(--pc-text-muted);
  white-space: nowrap;
}
.pc-proc__cell {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.pc-proc__dash {
  color: #b7c0cd;
}
.pc-proc__pending {
  font-size: 12px;
  color: var(--pc-text-faint);
}
.pc-proc__empty {
  margin: 0;
  padding: 16px;
  border: 1px dashed #d5deec;
  border-radius: var(--pc-radius-btn);
  font-size: 13px;
  color: var(--pc-text-faint);
  text-align: center;
}
.pc-proc__total {
  margin: 8px 2px 0;
  font-size: 12px;
  color: var(--pc-text-muted);
  text-align: right;
}
</style>
