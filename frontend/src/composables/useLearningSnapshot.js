import { computed, ref } from 'vue'
import { studentApi } from '@/api'
import { compactQuestionPreview } from '@/utils/questionPreview'

/**
 * 首页「学情脉搏」数据源：BKT 掌握度 + FSRS 到期队列 + 薄弱点 + 本周概览。
 * 各接口独立降级——任一失败只隐藏对应区块，不影响对话本身。
 */
export function useLearningSnapshot() {
  const loading = ref(true)
  const overview = ref(null)
  const mastery = ref(null)
  const dueQueue = ref([])
  const reviewPlan = ref(null)
  const weakPoints = ref([])
  const failures = ref(0)

  async function load() {
    loading.value = true
    const [ov, ms, dq, rp, wp] = await Promise.allSettled([
      studentApi.growthOverview(),
      studentApi.masterySummary(),
      studentApi.dueQueue(),
      studentApi.reviewPlan(),
      studentApi.weakPoints(),
    ])
    overview.value = ov.status === 'fulfilled' ? (ov.value || null) : null
    mastery.value = ms.status === 'fulfilled' ? (ms.value || null) : null
    dueQueue.value = dq.status === 'fulfilled' ? (dq.value?.items || []) : []
    reviewPlan.value = rp.status === 'fulfilled' ? (rp.value || null) : null
    weakPoints.value = wp.status === 'fulfilled' ? (wp.value?.items || []) : []
    failures.value = [ov, ms, dq, rp, wp].filter((r) => r.status === 'rejected').length
    loading.value = false
  }

  const masteredCount = computed(() => Number(overview.value?.mastered_kp_count) || 0)
  const totalKpCount = computed(() => Number(overview.value?.total_kp_count) || 0)
  const streakDays = computed(() => Number(overview.value?.streak_days) || 0)
  const weekAnswered = computed(() => Number(overview.value?.week_answer_count) || 0)
  const weekCorrect = computed(() => Number(overview.value?.week_correct_count) || 0)
  const weekAccuracy = computed(() =>
    weekAnswered.value ? Math.round((weekCorrect.value / weekAnswered.value) * 100) : null,
  )
  const delta7d = computed(() => {
    const v = mastery.value?.delta_vs_7d
    return Number.isFinite(Number(v)) ? Number(v) : null
  })

  /** Top 薄弱点：优先用带 AI 归因的 report/weak-points，失败退回掌握度雷达。 */
  const weakest = computed(() => {
    if (weakPoints.value.length) {
      return [...weakPoints.value]
        .sort((a, b) => (Number(a.mastery) || 0) - (Number(b.mastery) || 0))
        .slice(0, 3)
    }
    const rows = mastery.value?.radar || mastery.value?.top_weak || []
    return [...rows]
      .sort((a, b) => (Number(a.mastery) || 0) - (Number(b.mastery) || 0))
      .slice(0, 3)
  })

  const dueCount = computed(
    () => Number(reviewPlan.value?.due_today ?? overview.value?.error_due_count) || 0,
  )
  const upcomingCount = computed(() =>
    (reviewPlan.value?.upcoming || []).reduce((sum, d) => sum + (Number(d.count) || 0), 0),
  )

  /** 到期题：review-plan 提供全量题干（复习链路要用），due-queue 提供 FSRS 保持率。 */
  const dueItems = computed(() => {
    const byId = new Map(dueQueue.value.map((d) => [String(d.record_id), d]))
    const full = reviewPlan.value?.due_items || []
    if (!full.length) {
      return dueQueue.value.slice(0, 3).map((d) => ({
        record_id: d.record_id,
        question_text: d.question_preview || '',
        preview: compactQuestionPreview(d.question_preview || '', 72),
        kp_name: d.kp_name || '',
        retrievability: d.retrievability,
        urgency_label: d.urgency_label || '',
        source: d,
      }))
    }
    return full.slice(0, 3).map((item) => {
      const q = byId.get(String(item.record_id)) || {}
      return {
        ...item,
        preview: compactQuestionPreview(q.question_preview || item.question_text || '', 72),
        kp_name: q.kp_name || item.kp_name || '',
        retrievability: q.retrievability,
        urgency_label: q.urgency_label || '',
        source: item,
      }
    })
  })

  const topDue = computed(() => dueItems.value[0] || null)
  const topWeak = computed(() => weakest.value[0] || null)

  const suggestions = computed(() => {
    const list = []
    if (topDue.value) {
      const pct = Number.isFinite(Number(topDue.value.retrievability))
        ? `${Math.round(Number(topDue.value.retrievability) * 100)}%`
        : ''
      list.push({
        text: `复习「${topDue.value.kp_name || '到期错题'}」${pct ? ` · 保持率 ${pct}` : ''}`,
        reason: 'FSRS 到期',
        action: 'review',
        item: topDue.value.source,
      })
    }
    if (topWeak.value) {
      const pct = Math.round((Number(topWeak.value.mastery) || 0) * 100)
      list.push({
        text: `专攻「${topWeak.value.kp_name || '薄弱点'}」 · 掌握度 ${pct}%`,
        reason: 'BKT 薄弱点',
        skill: 'quiz_gen',
        ask: `请针对「${topWeak.value.kp_name || '这个知识点'}」先诊断我的薄弱环节，再出 3 道难度递进的变式题带我巩固。`,
      })
      const second = weakest.value[1]
      if (second) {
        list.push({
          text: `出 3 道「${second.kp_name || '薄弱点'}」变式`,
          reason: '依据错题归因',
          skill: 'quiz_gen',
          ask: `请针对「${second.kp_name || '这个知识点'}」出 3 道由易到难的变式题，并在每题后告诉我考查点。`,
        })
      }
    }
    if (weekAccuracy.value !== null) {
      list.push({
        text: `保持手感 · 来一组 15 分钟练习`,
        reason: `本周正确率 ${weekAccuracy.value}%`,
        skill: '',
        ask: '根据我近期的错题和薄弱点，给我一组 15 分钟能做完的巩固练习，先出第一题。',
      })
    }
    return list.slice(0, 4)
  })

  const hasData = computed(
    () =>
      !!overview.value ||
      !!mastery.value ||
      dueItems.value.length > 0 ||
      weakest.value.length > 0,
  )

  return {
    loading,
    failures,
    overview,
    mastery,
    dueQueue,
    reviewPlan,
    weakPoints,
    masteredCount,
    totalKpCount,
    streakDays,
    weekAnswered,
    weekAccuracy,
    delta7d,
    weakest,
    dueCount,
    upcomingCount,
    dueItems,
    topDue,
    topWeak,
    suggestions,
    hasData,
    load,
  }
}
