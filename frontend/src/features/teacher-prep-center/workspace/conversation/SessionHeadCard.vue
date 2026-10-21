<script setup lang="ts">
/**
 * S2 会话头卡（golden1 中栏顶部 · R2fix-S2 必修 ①②）。
 *
 * - 返回链「← 返回备课中心」：真实返回 /teacher-v3/prep（备课中心首页）；
 * - 会话状态 pill：真实 session.status 驱动（进行中=绿点/已完成=绿勾；draft/cancelled
 *   等真实状态域诚实映射，未知状态回落「进行中」不臆造新词）；
 * - 头卡 = 图标 + 课题标题 + 副行（教材 · 班级 · 课时，formatSessionMeta 唯一拼接口径，
 *   班级缺失诚实显示「未指定班级」）；
 * - 四步进度（带连接线；当前步高亮、完成步打勾）：
 *   对话交流 → 教学设计 → 生成PPT → 生成教案
 *   步进状态**完全来自真实备课状态**：对话已有消息 / 蓝图已确认 / 有 deck / 有 spec，
 *   禁止写死；当前步 = 最近完成步（全部未完成时停在第一步）。
 */
import { computed } from 'vue'
import { MINUTES_PER_LESSON_HOUR, usePrepSessionStore } from '@/features/teacher-prep-center/stores/prepSession'
import {
  CHAT_HEAD,
  SHELL,
  formatCourseTitle,
  formatSessionMeta,
  statusLabel,
  textbookVersionSegment,
} from '@/features/teacher-prep-center/copy/prepCopy'

const store = usePrepSessionStore()

/* ── 标题 / 副行（真实字段；缺则诚实占位/省略）────────────────────────── */
const titleText = computed(() =>
  formatCourseTitle(store.currentCourseTitle, store.lessonHoursDisplay) || CHAT_HEAD.titleFallback,
)

const metaText = computed(() => {
  const ws = store.current
  if (!ws) return ''
  const hours = store.lessonHoursDisplay
  return formatSessionMeta({
    textbook: textbookVersionSegment(ws.artifact.context?.source?.title ?? null),
    className: store.resolveClassName(ws.session) || null,
    duration: hours != null ? SHELL.minutesSuffix(hours * MINUTES_PER_LESSON_HOUR) : null,
  })
})

/* ── 会话状态 pill（真实 session.status）────────────────────────────────── */
const sessionStatus = computed(() => String(store.current?.session.status ?? ''))

const pill = computed<{ label: string; tone: 'doing' | 'done' | 'muted' | 'danger'; icon: 'check' | 'dot' }>(() => {
  const s = sessionStatus.value
  if (s === 'archived' || s === 'completed') return { label: CHAT_HEAD.statusDone, tone: 'done', icon: 'check' }
  if (s === 'draft') return { label: statusLabel(s), tone: 'muted', icon: 'dot' }
  if (s === 'cancelled' || s === 'failed') return { label: statusLabel(s), tone: 'danger', icon: 'dot' }
  // active / processing / 未知状态：进行中（与 statusLabel 同口径回落，不臆造新词）
  return { label: CHAT_HEAD.statusDoing, tone: 'doing', icon: 'dot' }
})

/* ── 四步进度（真实备课状态推导，禁止写死）────────────────────────────────
 * done 信号（后端权威快照）：
 *   ① 对话交流 = 会话已有真实消息（timeline 非空）
 *   ② 教学设计 = 蓝图已确认（blueprint.status === 'confirmed'）
 *   ③ 生成PPT  = 已有课件 deck（artifact.teaching_deck）
 *   ④ 生成教案 = 已有教案 spec（artifact.lesson_plan）
 * 当前步 = 最近完成步（全部未完成时停在第一步）：高亮蓝字；完成步打勾（含当前步
 * 已完成时的蓝底白勾，golden1「对话交流✓」样式）；未完成步灰圆无勾，禁止伪造完成态。 */
interface Step {
  key: string
  label: string
  done: boolean
  current: boolean
}

const steps = computed<Step[]>(() => {
  const ws = store.current
  const done: boolean[] = [
    (ws?.timeline?.length ?? 0) > 0,
    ws?.artifact.blueprint?.status === 'confirmed',
    !!ws?.artifact.teaching_deck,
    !!ws?.artifact.lesson_plan,
  ]
  let current = 0
  for (let i = 0; i < done.length; i += 1) if (done[i]) current = i
  const labels = [CHAT_HEAD.stepChat, CHAT_HEAD.stepDesign, CHAT_HEAD.stepPpt, CHAT_HEAD.stepPlan]
  return labels.map((label, i) => ({
    key: `s${i}`,
    label,
    done: done[i],
    current: i === current,
  }))
})
</script>

<template>
  <div class="pc-s2head">
    <!-- 返回链 + 会话状态 pill（同一行；真实导航 + 真实状态） -->
    <div class="pc-s2head__bar">
      <RouterLink class="pc-s2head__back" :to="'/teacher-v3/prep'" :aria-label="CHAT_HEAD.backAria">
        <svg viewBox="0 0 16 16" aria-hidden="true">
          <path d="M13 8H3m0 0 4-4M3 8l4 4" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" />
        </svg>
        {{ CHAT_HEAD.back }}
      </RouterLink>
      <span class="pc-s2head__pill" :class="`is-${pill.tone}`">
        <svg v-if="pill.icon === 'check'" viewBox="0 0 14 14" aria-hidden="true">
          <path d="M2.5 7.4 5.6 10.4 11.5 4" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" />
        </svg>
        <span v-else class="pc-s2head__pill-dot" aria-hidden="true"></span>
        {{ pill.label }}
      </span>
    </div>

    <!-- 会话头卡：图标 + 课题标题 + 副行 + 四步进度 -->
    <div class="pc-s2head__card">
      <span class="pc-s2head__icon" aria-hidden="true">
        <svg viewBox="0 0 24 24">
          <path d="M12 6.2C10.4 5 8.4 4.5 6 4.5c-.8 0-1.5.1-2 .2v12.6c.5-.1 1.2-.2 2-.2 2.4 0 4.4.5 6 1.7 1.6-1.2 3.6-1.7 6-1.7.8 0 1.5.1 2 .2V4.7c-.5-.1-1.2-.2-2-.2-2.4 0-4.4.5-6 1.7z" fill="currentColor" opacity="0.95" />
          <path d="M12 6.2v12.6" stroke="#fff" stroke-width="1.1" stroke-linecap="round" opacity="0.85" />
        </svg>
      </span>
      <div class="pc-s2head__titles">
        <p class="pc-s2head__title">{{ titleText }}</p>
        <p v-if="metaText" class="pc-s2head__meta">{{ metaText }}</p>
      </div>

      <ol class="pc-s2steps" aria-label="备课进度">
        <li
          v-for="(s, i) in steps"
          :key="s.key"
          class="pc-s2steps__item"
          :class="{ 'is-current': s.current, 'is-done': s.done && !s.current, 'is-todo': !s.done && !s.current }"
        >
          <span class="pc-s2steps__node" :class="{ 'is-checked': s.done }" aria-hidden="true">
            <svg v-if="s.done" viewBox="0 0 14 14" class="pc-s2steps__check">
              <path d="M2.8 7.3 5.7 10.2 11.2 4.2" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" />
            </svg>
            <svg v-else viewBox="0 0 14 14" class="pc-s2steps__next">
              <path d="M5.2 3.4 9.4 7l-4.2 3.6" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" />
            </svg>
          </span>
          <span class="pc-s2steps__label">{{ s.label }}</span>
          <span v-if="i > 0" class="pc-s2steps__line" aria-hidden="true"></span>
        </li>
      </ol>
    </div>
  </div>
</template>

<style scoped>
.pc-s2head {
  display: flex;
  flex-direction: column;
  gap: 10px;
  font-family: var(--pc-font);
}

/* ── 返回链 + 状态 pill ─────────────────────────────────────────── */
.pc-s2head__bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.pc-s2head__back {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 15px;
  font-weight: 700;
  color: var(--pc-text, #111827);
  text-decoration: none;
}
.pc-s2head__back:hover {
  color: var(--pc-primary, #2563eb);
}
.pc-s2head__back svg {
  width: 15px;
  height: 15px;
}
.pc-s2head__pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 5px 12px;
  border-radius: var(--pc-radius-full, 999px);
  font-size: 13px;
  font-weight: 600;
}
.pc-s2head__pill svg {
  width: 13px;
  height: 13px;
}
/* 进行中=绿点/绿时钟；已完成=绿勾（golden1 绿色状态 pill，像素取证 #15B893/#E3F9F4） */
.pc-s2head__pill.is-doing,
.pc-s2head__pill.is-done {
  background: #e3f9f4;
  color: #15b893;
}
.pc-s2head__pill.is-muted {
  background: var(--pc-bg, #f6f8fb);
  color: var(--pc-text-muted, #6b7280);
}
.pc-s2head__pill.is-danger {
  background: #fef2f2;
  color: var(--pc-danger, #dc2626);
}
.pc-s2head__pill-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: currentColor;
}

/* ── 头卡（图标 + 标题 + 副行 + 四步进度）────────────────────────── */
.pc-s2head__card {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 16px 22px;
  background: var(--pc-panel, #fff);
  border: 1px solid var(--pc-line, #e5e9f2);
  border-radius: var(--pc-radius-panel, 14px);
  box-shadow: var(--pc-shadow-card, 0 1px 2px rgba(15, 23, 42, 0.04));
}
.pc-s2head__icon {
  flex: none;
  width: 54px;
  height: 54px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 14px;
  background: linear-gradient(135deg, #4c8dff 0%, var(--pc-primary, #2563eb) 60%, #1d4fd8 100%);
  color: #fff;
  box-shadow: 0 6px 16px rgba(37, 99, 235, 0.28);
}
.pc-s2head__icon svg {
  width: 28px;
  height: 28px;
}
.pc-s2head__titles {
  flex: 1;
  min-width: 0;
}
.pc-s2head__title {
  margin: 0;
  font-size: 19px;
  font-weight: 700;
  line-height: 1.4;
  color: var(--pc-text, #111827);
  overflow-wrap: anywhere;
}
.pc-s2head__meta {
  margin: 4px 0 0;
  font-size: 14px;
  color: var(--pc-text-muted, #6b7280);
  overflow-wrap: anywhere;
}

/* ── 四步进度（连接线 + 当前步高亮 + 完成步打勾）────────────────────
 * 列宽 64px + 16px 间隙（golden1 步进行度紧凑等距）：给标题/副行留足单行宽度，
 * 1440 视口（中栏 ~645px）下「1.1 集合的概念 · 人教A版 · 未指定班级」不折行。 */
.pc-s2steps {
  flex: none;
  display: grid;
  grid-template-columns: repeat(4, 64px);
  column-gap: 16px;
  list-style: none;
  margin: 0;
  padding: 0;
}
.pc-s2steps__item {
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
}
/* 连接线：上一节点中心(-48px) → 本节点中心(+32px)，两端各缩 13px（节点半径）→ left -35px 宽 54px。 */
.pc-s2steps__line {
  position: absolute;
  top: 12px;
  left: -35px;
  width: 54px;
  height: 1.5px;
  background: var(--pc-line, #e5e9f2);
}
.pc-s2steps__line::after {
  content: '';
  position: absolute;
  left: 50%;
  top: 50%;
  transform: translate(-50%, -50%);
  width: 3px;
  height: 3px;
  border-radius: 50%;
  background: #c7d0de;
}
.pc-s2steps__node {
  position: relative;
  z-index: 1;
  width: 25px;
  height: 25px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
  box-sizing: border-box;
}
.pc-s2steps__check,
.pc-s2steps__next {
  width: 13px;
  height: 13px;
}
/* 完成步：打勾（蓝底白勾，golden1「对话交流✓」样式；已过步同样保留勾） */
.pc-s2steps__item.is-done .pc-s2steps__node,
.pc-s2steps__item.is-current .pc-s2steps__node.is-checked {
  background: linear-gradient(135deg, var(--pc-primary, #2563eb) 0%, #2f71fe 100%);
  border: none;
  color: #fff;
  box-shadow: 0 3px 8px rgba(37, 99, 235, 0.35);
}
/* 当前步（未完成）：高亮（蓝描边圆 + 蓝箭头 + 蓝字，无伪造勾） */
.pc-s2steps__item.is-current .pc-s2steps__node {
  background: var(--pc-primary-soft, #eff4ff);
  border: 1.5px solid var(--pc-primary, #2563eb);
  color: var(--pc-primary, #2563eb);
}
.pc-s2steps__item.is-todo .pc-s2steps__node {
  background: #f1f3f7;
  border: 1px solid var(--pc-line, #e5e9f2);
  color: var(--pc-text-faint, #9ca3af);
}
.pc-s2steps__label {
  font-size: 13px;
  line-height: 1.3;
  color: var(--pc-text-muted, #6b7280);
  white-space: nowrap;
}
.pc-s2steps__item.is-current .pc-s2steps__label {
  font-weight: 700;
  color: var(--pc-primary, #2563eb);
}
.pc-s2steps__item.is-todo .pc-s2steps__label {
  color: var(--pc-text-faint, #9ca3af);
}
</style>
