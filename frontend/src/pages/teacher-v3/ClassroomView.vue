<template>
  <div class="tv3-page-shell" data-testid="tv3-classroom">
    <TeacherPageHeader title="课堂" subtitle="双师课堂 · 实时互动 · 班级" icon="🎓" />

    <!-- 一级页签（Phase D IA 收敛）：双师课堂（默认）/ 实时互动（原主体）/ 班级（深链） -->
    <div class="tv3-seg tv3-classroom__tabs" role="tablist" aria-label="课堂视图" data-testid="tv3-classroom-tabs">
      <button
        type="button"
        role="tab"
        class="tv3-seg__btn"
        :class="{ 'is-active': activeTab === 'dual' }"
        :aria-selected="activeTab === 'dual'"
        data-testid="tv3-tab-dual"
        @click="setTab('dual')"
      >双师课堂</button>
      <button
        type="button"
        role="tab"
        class="tv3-seg__btn"
        :class="{ 'is-active': activeTab === 'live' }"
        :aria-selected="activeTab === 'live'"
        data-testid="tv3-tab-live"
        @click="setTab('live')"
      >实时互动</button>
      <!-- 班级不复制一套班级管理：深链到既有 /teacher-v3/classes -->
      <router-link
        to="/teacher-v3/classes"
        role="tab"
        class="tv3-seg__btn tv3-classroom__tab-link"
        data-testid="tv3-tab-class"
      >班级</router-link>
    </div>

    <!-- 双师课堂：已发布课件 + 真实学情（默认视图） -->
    <div v-if="activeTab === 'dual'" data-testid="tv3-dual-pane">
      <CoursewareMonitorPane />
    </div>

    <!-- 实时互动：现有主体原样保留（v-show 保状态：切 tab 不丢发题/分布/点名现场） -->
    <div v-show="activeTab === 'live'" data-testid="tv3-live-pane">
      <!-- 未开课 -->
      <div v-if="!session" class="tv3-card" style="max-width: 640px; margin: 40px auto; text-align: center; padding: 40px">
        <div style="font-size: 44px; margin-bottom: 10px">🎓</div>
        <div style="font-size: 18px; font-weight: 800; margin-bottom: 6px">课堂互动</div>
        <!-- 无班级：真实引导态（tel §8.2①：删除 'c2-03' mock 默认值后，空目录不给开课假入口） -->
        <template v-if="classLoadError && !classes.length">
          <div style="font-size: 13px; color: #b1382c; margin-bottom: 20px; line-height: 1.8" data-testid="tv3-classroom-class-error">
            班级加载失败：{{ classLoadError }}<br>这不是“没有班级”，请重试或检查教师服务。
          </div>
          <button class="tv3-btn tv3-btn--primary" type="button" @click="reloadClasses">重试</button>
        </template>
        <template v-else-if="classLoaded && !classes.length">
          <div style="font-size: 13px; color: var(--tv3-ink3); margin-bottom: 20px; line-height: 1.8" data-testid="tv3-classroom-no-class">
            还没有班级：课堂互动按真实班级开课。<br>先到「班级管理」建班并把邀请码给学生；学生加入后回这里开课。
          </div>
          <router-link to="/teacher-v3/classes" class="tv3-btn tv3-btn--primary" data-testid="tv3-classroom-to-classes">去班级管理建班 / 加入</router-link>
        </template>
        <template v-else>
          <div style="font-size: 13px; color: var(--tv3-ink3); margin-bottom: 20px">服务端权威状态机 · 学生 H5 输课堂码实时加入 · 教师选择的分支卡</div>
          <div style="display: flex; gap: 10px; justify-content: center; align-items: center">
            <select v-model="pickClass" class="tv3-input" style="width: 150px" data-testid="tv3-classroom-class">
              <option v-for="c in classes" :key="c.class_id" :value="c.class_id">{{ c.name }}</option>
            </select>
            <input v-model="topicDraft" class="tv3-input" style="width: 220px" placeholder="本节课题（可改）" />
            <button class="tv3-btn tv3-btn--primary" :disabled="!pickClass" data-testid="tv3-open-session" @click="openSession">开课</button>
          </div>
        </template>
      </div>

      <!-- 课堂中 -->
      <template v-else>
      <div class="tv3-card" style="margin-bottom: 14px">
        <div class="tv3-card__head">
          <span class="tv3-tag" :class="sessionStatus === 'waiting' ? 'tv3-tag--warn' : sessionStatus === 'ended' ? '' : 'tv3-tag--ok'" data-testid="tv3-status-tag">
            {{ sessionStatus === 'waiting' ? '◦ 等待学生' : sessionStatus === 'collecting' ? '● 答题中' : sessionStatus === 'stopped' ? '▪ 已停止' : sessionStatus === 'revealed' ? '◈ 讲评中' : '▪ 已结束' }}
          </span>
          <span class="tv3-tag" style="font-size: 10px" data-testid="tv3-join-code" title="学生打开 H5（/classroom-h5），输入课堂码和姓名即可加入，无需账号">课堂码 {{ session.join_code }} · 学生 H5 输码加入</span>
          <input v-model="sessionTopic" class="tv3-input" style="max-width: 260px; font-weight: 700" title="本节课题（可改）" />
          <span class="tv3-card__sub" data-testid="tv3-elapsed">⏱ {{ fmtClock(elapsedSec) }}</span>
          <span class="tv3-card__sub" data-testid="tv3-joined">{{ joinedCount }}/{{ total }} 人已加入</span>
          <div class="tv3-card__spacer" />
          <button class="tv3-btn tv3-btn--sm" data-testid="tv3-podium-open" @click="podiumOpen = true">🖥 讲台模式</button>
          <button class="tv3-btn tv3-btn--sm" data-testid="tv3-pick-student" @click="pickStudent">🎲 随机点名</button>
          <button class="tv3-btn tv3-btn--sm tv3-btn--danger" data-testid="tv3-end-session" @click="endSession">下课</button>
        </div>
        <!-- 等待学生：加入流 -->
        <div v-if="sessionStatus === 'waiting'" class="tv3-card__body" style="padding-top: 0">
          <div style="display: flex; gap: 6px; flex-wrap: wrap; align-items: center">
            <span class="tv3-tag" v-for="n in joinedNames.slice(-6)" :key="n" style="font-size: 10.5px">{{ n }} 已加入</span>
            <span v-if="joinedCount < total" class="tv3-pulse-dot" style="color: var(--tv3-ink3); font-size: 11px">正在加入…</span>
            <span v-else class="tv3-tag tv3-tag--ok" style="font-size: 10.5px">全班已加入，可以发题</span>
          </div>
        </div>
      </div>

      <div style="display: grid; grid-template-columns: 1fr 1.4fr; gap: 14px; align-items: start">
        <!-- 发题 -->
        <div class="tv3-card">
          <div class="tv3-card__head"><span class="tv3-card__title">发题</span><span class="tv3-card__sub">题干公式结构化推送 · 限时 {{ fmtClock(qTimeLeft) }}<template v-if="extendedTimes"> · 已续 {{ extendedTimes }} 次</template></span></div>
          <div class="tv3-card__body" style="display: flex; flex-direction: column; gap: 8px">
            <TeacherLoading v-if="poolLoading" />
            <TeacherEmptyState
              v-else-if="!quizPool.length"
              title="暂无题目可发"
              desc="发题池来自题库中的选择题，先去题库录入题目吧。"
              cta="去题库录入"
              @cta-click="$router.push('/teacher-v3/bank')"
            />
            <div
              v-for="q in quizPool" :key="q.id"
              class="tv3-row" :class="{ 'is-selected': activeQ?.id === q.id }" style="cursor: pointer; align-items: flex-start"
              :data-testid="`tv3-question-${q.id}`"
              @click="sendQuestion(q)"
            >
              <span class="tv3-tag" :class="q.difficulty === 'hard' ? 'tv3-tag--danger' : q.difficulty === 'medium' ? 'tv3-tag--warn' : 'tv3-tag--ok'">{{ diffLabel(q.difficulty) }}</span>
              <div style="flex: 1; min-width: 0">
                <div style="font-size: 12.5px; line-height: 1.5" v-html="renderStem(q.stem_latex)" />
                <div v-if="q.options" style="font-size: 11.5px; color: var(--tv3-ink3); margin-top: 2px">{{ q.options.length }} 个选项</div>
              </div>
              <span v-if="activeQ?.id === q.id" class="tv3-tag" :class="sessionStatus === 'collecting' ? 'tv3-tag--gold tv3-pulse-dot' : 'tv3-tag'">
                {{ sessionStatus === 'collecting' ? '答题中' : sessionStatus === 'stopped' ? '已停止' : '已公布' }}
              </span>
            </div>
          </div>
        </div>

        <!-- 实时分布 -->
        <div class="tv3-card">
          <div class="tv3-card__head">
            <span class="tv3-card__title">实时作答分布</span>
            <span class="tv3-card__sub" data-testid="tv3-live-count">{{ answered }} / {{ total }} 已作答<template v-if="sessionStatus !== 'collecting'">（已冻结）</template></span>
            <div class="tv3-card__spacer" />
            <button v-if="activeQ && sessionStatus === 'collecting'" class="tv3-btn tv3-btn--sm" data-testid="tv3-close-q" @click="stopQuestion">停止作答</button>
            <button v-if="activeQ && sessionStatus === 'collecting'" class="tv3-btn tv3-btn--sm" data-testid="tv3-extend-time" @click="extendTime">+30s 续时</button>
            <button v-else-if="activeQ && sessionStatus === 'stopped'" class="tv3-btn tv3-btn--sm tv3-btn--gold" data-testid="tv3-reveal-q" @click="revealQuestion">公布答案 → 讲评</button>
            <span v-else-if="activeQ && sessionStatus === 'revealed'" class="tv3-tag tv3-tag--ok">已公布 · 讲评中</span>
          </div>
          <div class="tv3-card__body">
            <div v-if="!activeQ" style="text-align: center; color: var(--tv3-ink3); padding: 60px 0; font-size: 13px">左侧发题后，这里实时显示作答分布</div>
            <template v-else>
              <div class="tv3-live__options" data-testid="tv3-live-options">
                <div v-for="(o, i) in activeOptions" :key="i" class="tv3-live__opt" :class="{ 'is-top': topIdx === i }">
                  <span class="tv3-live__letter">{{ String.fromCharCode(65 + i) }}</span>
                  <div class="tv3-live__barwrap"><div class="tv3-live__bar" :style="{ width: (dist[i] / Math.max(1, answered)) * 100 + '%' }" /></div>
                  <span class="tv3-live__pct">{{ answered ? Math.round((dist[i] / answered) * 100) : 0 }}%</span>
                  <span v-if="sessionStatus === 'revealed' && activeQ.answer === String.fromCharCode(65 + i)" class="tv3-tag tv3-tag--ok" style="font-size: 10px">正确</span>
                </div>
              </div>
              <div v-if="sessionStatus === 'stopped'" class="tv3-live__reveal" style="border-color: var(--tv3-gold-border, #e5c96a)">
                <span class="tv3-tag tv3-tag--warn">已停止收答 · 答案尚未公布（{{ total - answered }} 人未作答）</span>
                <button class="tv3-btn tv3-btn--sm tv3-btn--gold" @click="revealQuestion">公布答案 → 讲评</button>
              </div>
              <template v-if="sessionStatus === 'revealed'">
                <div class="tv3-live__reveal">
                  <span class="tv3-tag tv3-tag--ok">正确率 {{ correctRate }}%</span>
                  <span class="tv3-tag tv3-tag--warn">易错项 {{ String.fromCharCode(65 + topIdx) }}（{{ dist[topIdx] }} 人）</span>
                  <button class="tv3-btn tv3-btn--sm tv3-btn--gold" @click="$router.push('/teacher-v3/prep')">拍照典型错例 → 讲评</button>
                </div>
                <!-- B5 分支卡：系统只建议排序，教师点选才执行 -->
                <div style="margin-top: 12px" data-testid="tv3-branch-cards">
                  <div style="font-size: 11.5px; color: var(--tv3-ink3); margin-bottom: 6px">
                    下一步走向 · <b>由你决定</b>（系统按正确率排序建议；第一张为推荐，点击才执行，不做自动切换）
                  </div>
                  <div style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px">
                    <div
                      v-for="(card, bi) in branchCards" :key="card.kind"
                      class="tv3-prep__diffitem" style="padding: 8px 10px"
                      :style="bi === 0 ? { borderColor: 'var(--tv3-gold, #0891b2)', background: 'var(--tv3-gold-soft, #fdf6e3)' } : {}"
                      :data-testid="`tv3-branch-${card.kind}`"
                    >
                      <div style="display: flex; gap: 5px; align-items: center">
                        <span v-if="bi === 0" class="tv3-tag tv3-tag--gold" style="font-size: 9.5px">推荐</span>
                        <b style="font-size: 12px">{{ card.label }}</b>
                      </div>
                      <div style="font-size: 10.5px; color: var(--tv3-ink3); margin: 4px 0">触发：{{ card.trigger }} · 约 {{ card.minutes }} 分钟</div>
                      <div style="font-size: 11px; color: var(--tv3-ink2); line-height: 1.55; min-height: 46px">{{ card.action }}</div>
                      <button
                        class="tv3-btn tv3-btn--sm" :class="bi === 0 ? 'tv3-btn--gold' : ''" style="width: 100%"
                        :data-testid="`tv3-branch-run-${card.kind}`" @click="runBranch(card)"
                      >执行此分支</button>
                    </div>
                  </div>
                </div>
              </template>
              <!-- 点名结果 -->
              <div v-if="picked" class="tv3-live__picked" data-testid="tv3-picked">
                🎯 本轮点名：<b>{{ picked }}</b> <span style="color: var(--tv3-ink3); font-size: 11.5px">（公平随机来自服务端，已点 {{ pickedCount }} 次）</span>
              </div>
            </template>
          </div>
        </div>
      </div>

      <!-- 动态演示大屏 -->
      <div class="tv3-card" style="margin-top: 14px">
        <div class="tv3-card__head">
          <span class="tv3-card__title">动态演示</span>
          <span class="tv3-card__sub">课堂大屏 · 学生可扫码跟看 · 图形可拖</span>
          <div class="tv3-card__spacer" />
          <div class="tv3-seg">
            <button v-for="d in demos" :key="d.id" class="tv3-seg__btn" :class="{ 'is-active': demoId === d.id }" @click="demoId = d.id">{{ d.name }}</button>
          </div>
        </div>
        <div class="tv3-card__body" style="display: grid; grid-template-columns: 1fr 320px; gap: 14px">
          <GeoFigure :key="demoId" :preset-id="demoId" :params="demoParams" :interactive="true" :height="360" boxed show-badge @update:params="demoParams = $event" />
          <div>
            <div class="tv3-form-label">当前演示说明</div>
            <div style="font-size: 12.5px; color: var(--tv3-ink2); line-height: 1.8">{{ demoDesc }}</div>
            <div class="tv3-form-label" style="margin-top: 12px">教学建议</div>
            <div style="font-size: 12px; color: var(--tv3-ink3); line-height: 1.8">
              拖动参数滑杆让学生先<b>预测</b>变化，再验证；配合随机点名追问「为什么」。
            </div>
            <button class="tv3-btn tv3-btn--sm tv3-btn--ghost-ai" style="margin-top: 10px" @click="pushToSlides">存入当前课件</button>
          </div>
        </div>
      </div>
      </template>
    </div>

    <!-- 讲台模式：高对比 · 大字号 · 少操作（响应式投屏视图；Esc 退出） -->
    <div v-if="podiumOpen && session" class="tv3-podium" data-testid="tv3-podium" @keydown.esc="podiumOpen = false" tabindex="0">
      <div style="position: absolute; top: 18px; right: 22px; display: flex; gap: 10px; align-items: center">
        <span style="color: #ffd77a; font-size: 15px; font-weight: 700" data-testid="tv3-podium-clock">{{ activeQ && sessionStatus === 'collecting' ? '⏱ ' + fmtClock(qTimeLeft) : '⏱ ' + fmtClock(elapsedSec) }}</span>
        <span style="color: #9fb4d8; font-size: 14px">{{ session.class_name }} · {{ joinedCount }}/{{ total }} 人</span>
        <button class="tv3-btn tv3-btn--sm" style="background: rgba(255,255,255,.12); color: #fff; border-color: rgba(255,255,255,.3)" data-testid="tv3-podium-exit" @click="podiumOpen = false">退出讲台 (Esc)</button>
      </div>
      <div style="position: absolute; top: 18px; left: 26px">
        <span style="color: #9fb4d8; font-size: 14px">{{ sessionTopic }}</span>
        <div style="color: #ffd77a; font-size: 20px; font-weight: 800; margin-top: 4px">{{ sessionStatus === 'collecting' ? '● 答题中' : sessionStatus === 'stopped' ? '▪ 已停止' : sessionStatus === 'revealed' ? '◈ 讲评中' : '◦ 等待学生' }}</div>
      </div>
      <div style="flex: 1; display: flex; flex-direction: column; justify-content: center; align-items: center; width: 100%; padding: 0 6vw">
        <template v-if="activeQ">
          <div style="color: #fff; font-size: clamp(24px, 3.4vw, 44px); font-weight: 800; text-align: center; line-height: 1.4" data-testid="tv3-podium-question" v-html="renderStem(activeQ.stem_latex)" />
          <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 3vh 4vw; width: 100%; max-width: 1200px; margin-top: 5vh">
            <div v-for="(o, i) in activeOptions" :key="i" style="display: flex; align-items: center; gap: 16px">
              <span style="width: 56px; height: 56px; border-radius: 16px; display: grid; place-items: center; font-size: 26px; font-weight: 800; flex-shrink: 0; background: sessionStatus === 'revealed' && activeQ.answer === String.fromCharCode(65 + i) ? '#1f8a5f' : 'rgba(255,255,255,.14)'; color: #fff">{{ String.fromCharCode(65 + i) }}</span>
              <div style="flex: 1; height: 30px; border-radius: 10px; background: rgba(255,255,255,.12); overflow: hidden">
                <div style="height: 100%; background: linear-gradient(90deg, #3b74cf, #6ea3ff); transition: width .4s" :style="{ width: (dist[i] / Math.max(1, answered)) * 100 + '%' }" />
              </div>
              <span style="color: #fff; font-size: 24px; font-weight: 800; width: 84px; text-align: right">{{ answered ? Math.round((dist[i] / answered) * 100) : 0 }}%</span>
            </div>
          </div>
        </template>
        <div v-else style="color: #cfe0ff; font-size: clamp(22px, 2.6vw, 34px)">左侧发题后，此处大屏显示题目与实时分布</div>
      </div>
      <div style="padding: 24px; display: flex; gap: 16px; justify-content: center; width: 100%">
        <button v-if="activeQ && sessionStatus === 'collecting'" class="tv3-btn tv3-btn--lg" style="background: #c9531f; color: #fff; border: none; font-size: 20px; padding: 16px 38px" data-testid="tv3-podium-stop" @click="stopQuestion">停止作答</button>
        <button v-else-if="activeQ && sessionStatus === 'stopped'" class="tv3-btn tv3-btn--lg" style="background: #1f8a5f; color: #fff; border: none; font-size: 20px; padding: 16px 38px" data-testid="tv3-podium-reveal" @click="revealQuestion">公布答案</button>
        <button class="tv3-btn tv3-btn--lg" style="background: rgba(255,255,255,.14); color: #fff; border: none; font-size: 20px; padding: 16px 38px" @click="pickStudent">🎲 点名</button>
      </div>
    </div>

    <!-- 课堂小结（已结束；只在「实时互动」页签内展示） -->
    <div v-if="summaryShown && summary && activeTab === 'live'" class="tv3-card" style="max-width: 640px; margin: 24px auto" data-testid="tv3-session-summary">
      <div class="tv3-card__head"><span class="tv3-card__title">课堂小结</span><span class="tv3-card__sub">来自服务端结课聚合（真实作答数据）</span></div>
      <div class="tv3-card__body" style="display: flex; flex-direction: column; gap: 6px; font-size: 13px; line-height: 1.8">
        <div>· 发题 <b>{{ summary.questions }}</b> 题 · 平均正确率 <b>{{ summary.avgCorrectRate }}%</b></div>
        <div>· 最高频错因：<b>{{ summary.topWrong }}</b></div>
        <div v-if="summary.branchesUsed.length">· 分支使用：{{ summary.branchesUsed.join(' → ') }}</div>
        <div>· 课堂时长：约 {{ summary.durationMin }} 分钟</div>
        <div style="display: flex; gap: 8px; margin-top: 8px; align-items: center">
          <button class="tv3-btn tv3-btn--sm tv3-btn--primary" data-testid="tv3-save-deck" :disabled="!!deckSaved" @click="saveDeck">{{ deckSaved ? '已存入课件 ✓' : '存入课件' }}</button>
          <button class="tv3-btn tv3-btn--sm tv3-btn--primary" @click="$router.push({ path: '/teacher-v3/insights' })">去学情洞察回流</button>
          <button class="tv3-btn tv3-btn--sm" @click="summaryShown = false">关闭</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * ClassroomView —— 课堂互动（M2-A：真实 classroom 契约，02-ARCHITECTURE §12 / IFC-002）
 * 服务端权威（G7）：教师端只是 projection——加入/作答/统计/状态全部来自
 * GET snapshot 与教师 SSE（applyTeacherEvent reducer），本地零自持权威状态。
 * 布局与交互保持原型验收版 1:1；讲台模式/限时倒计时为纯呈现层。
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { v3Api, type V3QuizQuestion } from '@/api/teacherV3'
import { TeacherPageHeader, TeacherEmptyState, TeacherLoading } from '@/components/teacherV3/common'
import { renderLatex, renderStem } from '@/components/mathx/latex'
import GeoFigure from '@/components/mathx/GeoFigure.vue'
import { useToastStore } from '@/stores/toast'
import type { V3ClassInfo, V3ClassroomActivity, V3Slide } from '@/types/teacherV3'
import { updateTv3Context, useTv3Context } from '@/stores/teacherContext'
import CoursewareMonitorPane from '@/pages/teacher-v3/CoursewareMonitorPane.vue'
import { applyTeacherEvent, teacherStateFromSnapshot, type TeacherClassroomState } from './classroomReducer'
import { fmtClock, QUESTION_TIME_DEFAULT, QUESTION_TIME_EXTEND, QUESTION_TIME_MAX, type BranchCard, type ClassStatus } from './classroomLogic'

/* 课堂一级页签（Phase D）：
 * - 双师课堂（默认）：备课课件发布后的真实学情（CoursewareMonitorPane）；
 * - 实时互动：原课堂互动主体（开课/发题/分布/点名/讲台），v-show 保现场；
 * - 班级：不复制班级管理，深链到既有 /teacher-v3/classes。
 * 页签写回 `?tab=live`，刷新/收藏可直达；缺省即双师课堂。 */
const route = useRoute()
const router = useRouter()
type ClassroomTab = 'dual' | 'live'
const activeTab = computed<ClassroomTab>(() => (route.query.tab === 'live' ? 'live' : 'dual'))
function setTab(tab: ClassroomTab) {
  const query = { ...route.query }
  if (tab === 'live') query.tab = 'live'
  else delete query.tab
  void router.replace({ path: '/teacher-v3/classroom', query })
}

/* toast 惰性获取：setup 顶层实例化会要求测试环境安装 Pinia */
let toast: ReturnType<typeof useToastStore> | null = null
const toastOf = () => (toast ??= useToastStore())
const classes = ref<V3ClassInfo[]>([])
const quizPool = ref<V3QuizQuestion[]>([])
const poolLoading = ref(true)
const classLoaded = ref(false)
const classLoadError = ref('')
/* tel §8.2①：mock 默认班 'c2-03' 已删——无班级时渲染引导态，有班级时取列表第一项真实 UUID */
const pickClass = ref('')
const topicDraft = ref('椭圆及其标准方程 · 习题课')
const sessionTopic = ref('')

/* 服务端权威状态（reducer 投影） */
const state = ref<TeacherClassroomState | null>(null)
const session = computed(() => state.value?.session ?? null)
const sessionStatus = computed<ClassStatus>(() => {
  const s = state.value
  if (!s) return 'idle'
  if (s.session.status !== 'open') return 'ended'
  const act = currentActivity.value
  if (!act) return 'waiting'
  return act.status === 'collecting' ? 'collecting' : act.status === 'locked' ? 'stopped' : act.status === 'revealed' ? 'revealed' : 'waiting'
})
/** 当前焦点活动：进行中的（collecting/locked）优先，否则最近一个 */
const currentActivity = computed<V3ClassroomActivity | null>(() => {
  const acts = state.value?.session.activities || []
  const live = acts.find((a) => a.status === 'collecting' || a.status === 'locked')
  const latest = [...acts].sort((a, b) => b.ord - a.ord)[0]
  return live || (latest?.status === 'revealed' ? latest : null) || null
})
const activeQ = computed<V3QuizQuestion | null>(() => {
  const qid = currentActivity.value?.question_id
  return qid ? quizPool.value.find((q) => q.id === qid) || null : null
})
const dist = computed<number[]>(() => {
  const d = (currentActivity.value?.stats?.distribution || {}) as Record<string, number>
  return [d.A || 0, d.B || 0, d.C || 0, d.D || 0]
})
const answered = computed(() => currentActivity.value?.stats?.answered ?? 0)
const picked = ref('')
const pickedCount = ref(0)
const demoId = ref('conic/ellipse')
const demoParams = ref<Record<string, number>>({})
const podiumOpen = ref(false)

/* presence：学生名单来自服务端（H5 输码加入） */
const joinedNames = computed(() => (session.value?.participants || []).map((p) => p.student_name))

/* 限时与续时（纯呈现：到 0 提示教师，权威状态在服务端） */
const qTimeLeft = ref(QUESTION_TIME_DEFAULT)
const extendedTimes = ref(0)
let qTimer: number | undefined
let clockTimer: number | undefined
const elapsedSec = ref(0)
const sessionStartedAt = ref(0)

/* 记录：分支为教师本人的操作史（本地 UI 记录），作答统计全部来自服务端 */
const branchesUsed = ref<string[]>([])
const summaryShown = ref(false)
const summary = ref<{ questions: number; avgCorrectRate: number; topWrong: string; branchesUsed: string[]; durationMin: number } | null>(null)

const demos = [
  { id: 'conic/ellipse', name: '椭圆定义', desc: '两定点距离之和为定值 2a：拖动参数观察 a 与 b 的关系，焦点联动，退化为线段的临界时刻是教学关键点。' },
  { id: 'function/sine', name: '正弦三参数', desc: 'y = a·sin(bx+c)：先固定两个参数只动一个，建立「一次只变一件事」的实验意识。' },
  { id: 'solid/cube-section', name: '正方体截面', desc: '三点定面：让学生先猜截面形状（三角/四边/五边/六边），再拖点验证，破除「截面一定是三角形」的迷思。' },
]

/* tel §8.2②：'?? 44' 假兜底已删——students 计数来自 8000 /classes/mine（A1 confirmed 学生数），没有就如实 0 */
const total = computed(() => classes.value.find((c) => c.class_id === pickClass.value)?.students ?? 0)
const joinedCount = computed(() => joinedNames.value.length)
const activeOptions = computed(() => activeQ.value?.options || ['A', 'B', 'C', 'D'])
const topIdx = computed(() => dist.value.indexOf(Math.max(...dist.value)))
const correctRate = computed(() => {
  const cr = currentActivity.value?.stats?.correct_rate
  if (typeof cr === 'number') return cr
  if (!activeQ.value) return 0
  const ai = activeQ.value.answer.charCodeAt(0) - 65
  return answered.value ? Math.round((dist.value[ai] / answered.value) * 100) : 0
})
const demoDesc = computed(() => demos.find((d) => d.id === demoId.value)?.desc || '')
const diffLabel = (d: string) => ({ easy: '容易', medium: '中等', hard: '较难' } as Record<string, string>)[d] || d
const branchCards = computed(() => suggestBranchLocal(correctRate.value))
/** 分支建议（呈现层）：按正确率排序，教师点选才执行（不做自动切换） */
function suggestBranchLocal(rate: number): BranchCard[] {
  const cards: BranchCard[] = [
    { kind: 'boost', label: '理解良好 · 进入提升', trigger: '正确率 ≥ 75%', minutes: 5, action: '发一道较难的提升题（变式迁移），快节奏对答案', sendDifficulty: 'hard' },
    { kind: 'partial', label: '部分卡住 · 对比例题', trigger: '正确率 45%–75%', minutes: 4, action: '投出标准答案逐步对照，重点讲分岔步骤，然后原地再练一题', sendDifficulty: 'medium' },
    { kind: 'reteach', label: '大面积未理解 · 回前置', trigger: '正确率 < 45%', minutes: 6, action: '回到定义页重新演示（动态图形），发一道基础题确认回炉效果', sendDifficulty: 'easy', demoId: 'conic/ellipse' },
  ]
  const order: BranchCard['kind'][] = rate >= 75 ? ['boost', 'partial', 'reteach'] : rate >= 45 ? ['partial', 'reteach', 'boost'] : ['reteach', 'partial', 'boost']
  return [...cards].sort((a, b) => order.indexOf(a.kind) - order.indexOf(b.kind))
}

/* ---------- 存入课件（S16 审计补齐：后端真实端点 + 前端客户端方法均已就位） ---------- */
const deckSaved = ref('')
const lastSessionId = ref('')
async function saveDeck() {
  // endSession 收尾会清 state（teardown），存课件用独立会话锚点
  if (!lastSessionId.value || deckSaved.value) return
  try {
    const r = await v3Api.classroom.saveToDeck(lastSessionId.value, { title: (sessionTopic.value || '课堂课件') })
    deckSaved.value = r.data.deck_id
    toastOf().success('已存入课件，可在资源中心查看')
  } catch {
    toastOf().error('存入课件失败：请重试')
  }
}

async function reloadClasses() {
  classLoadError.value = ''
  try {
    const r = await v3Api.catalog.classes()
    classes.value = r.data.items || []
    if (!pickClass.value) pickClass.value = classes.value[0]?.class_id ?? ''
  } catch (e) {
    classLoadError.value = e instanceof Error ? e.message : '请稍后重试'
  } finally {
    classLoaded.value = true
  }
}

onMounted(async () => {
  try {
    const [c, q] = await Promise.all([
      v3Api.catalog.classes()
        .then((r) => r.data.items)
        .catch((e) => { classLoadError.value = e instanceof Error ? e.message : '班级接口不可用'; return [] }),
      v3Api.catalog.quizQuestions().then((r) => r.data.items).catch(() => []),
    ])
  classes.value = c
  // tel §8.2①：默认选中第一个真实班级（无 mock 默认）；tel §8.2③：发题池放宽到全部 choice 题上限 20
  if (!pickClass.value) pickClass.value = c[0]?.class_id ?? ''
  classLoaded.value = true
  quizPool.value = q.filter((x) => x.q_type === 'choice').slice(0, 20)
  // 刷新/重进恢复（S16 审计：此前刷新即丢会话）——本地只存 session_id 锚点，
  // 状态全部从权威快照重建（G7：前端仅 projection）
  try {
    const saved = JSON.parse(localStorage.getItem('tv3_classroom_last_session') || 'null') as { session_id: string; topic?: string } | null
    if (saved?.session_id && !state.value) {
      const snap = await v3Api.classroom.snapshot(saved.session_id)
      const snapSession = snap.data.session
      if (snapSession && snapSession.status !== 'archived') {
        state.value = teacherStateFromSnapshot(snap.data)
        sessionTopic.value = saved.topic || ''
        sessionStartedAt.value = Date.now()
        lastSessionId.value = saved.session_id
        subscribe(saved.session_id)
        clockTimer = window.setInterval(() => { elapsedSec.value = Math.floor((Date.now() - sessionStartedAt.value) / 1000) }, 1000)
      } else {
        localStorage.removeItem('tv3_classroom_last_session')
      }
    }
  } catch { /* 快照取不到就当无历史课堂 */ }
  } finally {
    poolLoading.value = false
  }
})
onBeforeUnmount(() => { clearTimers(); streamAbort?.(); streamAbort = null })
function clearTimers() {
  for (const t of [qTimer, clockTimer]) if (t) window.clearInterval(t)
}

/* ---------- B6 上下文写入 + 管家快捷动作 ---------- */
const ctxStore = useTv3Context()
watch([session, sessionStatus, joinedCount], () => {
  ctxStore.route = '/teacher-v3/classroom'
  updateTv3Context({
    route: '/teacher-v3/classroom',
    class_name: session.value?.class_name,
    topic: sessionTopic.value,
    extra: (session.value ? joinedCount.value + '/' + total.value + ' 人' : '') + (session.value ? ' · ' + sessionStatus.value : ''),
  })
})
function onButlerQuick(ev: Event) {
  const action = (ev as CustomEvent).detail?.action
  if (action === 'podium') podiumOpen.value = true
}
onMounted(() => window.addEventListener('tv3-butler-quick', onButlerQuick as EventListener))

/* ---------- 教师流（常驻 SSE：断线自动重连 + snapshot 重置，§12.4） ---------- */
let streamAbort: (() => void) | null = null
function subscribe(sessionId: string) {
  const { abort, finished } = v3Api.classroom.stream(
    sessionId,
    (event, data) => {
      if (state.value) state.value = applyTeacherEvent(state.value, { event, data })
    },
    undefined,
    { onRecover: () => toastOf().info('连接已恢复，正在补齐进度…') },
  )
  streamAbort = abort
  finished.catch(() => { if (state.value) toastOf().error('课堂连接中断：正在尝试恢复，多次失败请刷新页面') })
}

async function openSession() {
  const cls = classes.value.find((c) => c.class_id === pickClass.value)
  try {
    const r = await v3Api.classroom.createSession({
      class_id: pickClass.value,
      class_name: cls?.name,
      topic: topicDraft.value || '椭圆及其标准方程 · 习题课',
    })
    const s = r.data
    state.value = teacherStateFromSnapshot({ session: s, questions: [], seq: 0, summary: null })
    sessionTopic.value = s.topic || topicDraft.value || '椭圆及其标准方程 · 习题课'
    sessionStartedAt.value = Date.now()
    branchesUsed.value = []
    summaryShown.value = false
    summary.value = null
    subscribe(s.session_id)
    lastSessionId.value = s.session_id
    try { localStorage.setItem('tv3_classroom_last_session', JSON.stringify({ session_id: s.session_id, topic: sessionTopic.value })) } catch { /* 隐私模式允许失败 */ }
    clockTimer = window.setInterval(() => { elapsedSec.value = Math.floor((Date.now() - sessionStartedAt.value) / 1000) }, 1000)
    toastOf().success(`已开课 · 课堂码 ${s.join_code}（学生 H5 输码加入）`)
  } catch {
    toastOf().error('开课失败：请确认网络与登录状态后重试')
  }
}

async function endSession() {
  const s = state.value
  if (!s) return
  if (sessionStatus.value === 'collecting' && !window.confirm('正在收答中，确定直接下课？（未公布的作答将不保留）')) return
  try {
    await v3Api.classroom.endSession(s.session.session_id)
    // 小结从权威快照取（避免 SSE 事件竞态）；字段映射对齐后端真实聚合（accuracy/activities）
    const snap = await v3Api.classroom.snapshot(s.session.session_id)
    const st = (snap.data.summary?.stats || {}) as Record<string, any>
    const acts = (st.activities || []) as any[]
    const weak = acts.filter((a) => a.correct != null && a.responses && a.correct / a.responses < 0.6)
    summary.value = {
      questions: acts.length,
      avgCorrectRate: st.accuracy == null ? 0 : Math.round(Number(st.accuracy) * 100),
      topWrong: weak[0]
        ? `第 ${acts.indexOf(weak[0]) + 1} 题（正确率 ${Math.round((weak[0].correct / weak[0].responses) * 100)}%）`
        : '—',
      branchesUsed: [...branchesUsed.value],
      durationMin: Math.max(1, Math.round(elapsedSec.value / 60)),
    }
    summaryShown.value = true
  } catch {
    toastOf().error('结课失败：请重试')
    return
  }
  streamAbort?.(); streamAbort = null
  clearTimers()
  state.value = null
  podiumOpen.value = false
}

/* ---------- 发题 / 停止 / 公布：全部走服务端状态机，结果经 SSE 回投影 ---------- */
watch(() => currentActivity.value?.activity_id, () => {
  qTimeLeft.value = QUESTION_TIME_DEFAULT
  extendedTimes.value = 0
})

async function sendQuestion(q: V3QuizQuestion) {
  const s = state.value
  if (!s || sessionStatus.value === 'ended') return
  if (sessionStatus.value === 'collecting' && !window.confirm('正在收答中，发新题将替换当前题目焦点')) return
  try {
    await v3Api.classroom.pushActivity(s.session.session_id, { kind: 'question', question_id: q.id, config: { duration: QUESTION_TIME_DEFAULT } })
    if (qTimer) window.clearInterval(qTimer)
    qTimeLeft.value = QUESTION_TIME_DEFAULT
    extendedTimes.value = 0
    qTimer = window.setInterval(() => {
      if (sessionStatus.value !== 'collecting') { window.clearInterval(qTimer); return }
      qTimeLeft.value -= 1
      if (qTimeLeft.value <= 0) {
        window.clearInterval(qTimer)
        toastOf().info('限时已到：可停止作答或续时（权威状态在服务端）')
      }
    }, 1000)
    toastOf().success(`已发题：${diffLabel(q.difficulty)} · 限时 ${fmtClock(QUESTION_TIME_DEFAULT)}`)
  } catch {
    toastOf().error('发题失败：请重试')
  }
}

async function stopQuestion() {
  const s = state.value
  const act = currentActivity.value
  if (!s || !act || act.status !== 'collecting') return
  try { await v3Api.classroom.lockActivity(s.session.session_id, act.activity_id) } catch { toastOf().error('停止失败：请重试') }
}

async function revealQuestion() {
  const s = state.value
  const act = currentActivity.value
  if (!s || !act) return
  try {
    await v3Api.classroom.revealActivity(s.session.session_id, act.activity_id)
    toastOf().success(`已公布答案：正确率 ${correctRate.value}%（下方分支卡由你决定走向）`)
  } catch { toastOf().error('公布失败：请重试') }
}

function extendTime() {
  if (sessionStatus.value !== 'collecting') return
  const next = Math.min(QUESTION_TIME_MAX, qTimeLeft.value + QUESTION_TIME_EXTEND)
  if (next === qTimeLeft.value) { toastOf().info(`已达单题时间上限 ${fmtClock(QUESTION_TIME_MAX)}`); return }
  qTimeLeft.value = next
  extendedTimes.value += 1
  toastOf().info(`已续时 +${QUESTION_TIME_EXTEND}s（当前 ${fmtClock(qTimeLeft.value)}）`)
}

/* ---------- B5 分支卡：建议排序，教师点选执行 ---------- */
function runBranch(card: BranchCard) {
  branchesUsed.value.push(card.kind)
  if (card.demoId) demoId.value = card.demoId
  const pool = quizPool.value.filter((x) => x.id !== activeQ.value?.id)
  const target =
    (card.sendDifficulty === 'hard' && pool.find((x) => x.difficulty === 'hard')) ||
    (card.sendDifficulty === 'easy' && pool.find((x) => x.difficulty === 'easy')) ||
    pool[0]
  toastOf().info(`已执行「${card.label}」：${card.action}（已记入课堂小结）`)
  if (target) {
    window.setTimeout(() => sendQuestion(target), 600)
  }
}

async function pickStudent() {
  const s = state.value
  if (!s) return
  try {
    const r = await v3Api.classroom.callRandom(s.session.session_id)
    picked.value = r.data.participant.student_name
    pickedCount.value += 1
  } catch {
    toastOf().error('点名失败：还没有学生加入或请重试')
  }
}

/** 存入当前课件：把当前演示图形作为「课堂演示页」真实写入最近编辑的课件（B0） */
async function pushToSlides() {
  if (!demoId.value) return
  const demoName = demos.find((d) => d.id === demoId.value)?.name || demoId.value
  try {
    const r = await v3Api.decks.list()
    const target = r.data.items?.[0]
    if (!target) { toastOf().error('当前没有课件：请先在备课会话里创建一份课件'); return }
    const stamp = Date.now()
    const slide: V3Slide = {
      id: `cl-${stamp}`,
      layout: 'definition',
      elements: [
        { id: `cle-${stamp}-t`, z: 1, type: 'text', left: 70, top: 44, width: 620, height: 48, html: `课堂演示 · ${demoName}`, font_size: 26, bold: true },
        { id: `cle-${stamp}-g`, z: 2, type: 'geometry', left: 240, top: 120, width: 800, height: 480, preset_id: demoId.value, params: { ...demoParams.value }, teacher_confirmed: false },
        { id: `cle-${stamp}-n`, z: 3, type: 'text', left: 70, top: 500, width: 500, height: 60, html: '来源：课堂互动 · 动态演示（参数为课堂现场值）', font_size: 15, color: '#64748b' },
      ],
    }
    await v3Api.decks.addSlide(target.id, { slide })
    toastOf().success(`已把「课堂演示 · ${demoName}」存入课件《${target.title}》（未确认元素，可编辑）`)
  } catch {
    toastOf().error('存入失败（服务未启动？）')
  }
}

/* Esc 退出讲台 */
watch(podiumOpen, (v) => {
  if (v) window.addEventListener('keydown', onPodiumKey)
  else window.removeEventListener('keydown', onPodiumKey)
})
function onPodiumKey(e: KeyboardEvent) { if (e.key === 'Escape') podiumOpen.value = false }
onBeforeUnmount(() => window.removeEventListener('keydown', onPodiumKey))
</script>

<style scoped>
.tv3-page-shell {
  margin: -22px; padding: 22px; min-height: calc(100vh - var(--tv3-topbar-h));
  background: var(--teacher-bg-gradient);
  box-sizing: border-box;
}
/* 课堂一级页签：与既有 tv3-seg 同视觉，进入内容前只占一行 */
.tv3-classroom__tabs { margin-bottom: 14px; }
.tv3-classroom__tab-link { display: inline-flex; align-items: center; text-decoration: none; }
.tv3-podium {
  position: fixed; inset: 0; z-index: 1500;
  background: radial-gradient(1200px 700px at 50% 30%, #123a6d, #071a35 75%);
  display: flex; flex-direction: column; align-items: center;
  outline: none;
}
</style>
