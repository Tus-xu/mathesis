<template>
  <div data-testid="tv3-assign">
    <!-- 页头（保留既有视觉框架） -->
    <div class="tv3-hero" style="margin-bottom: 18px">
      <div style="display: flex; gap: 24px; align-items: center">
        <div style="flex: 1">
          <div class="tv3-hero__title">作业与批改</div>
          <div class="tv3-hero__sub">学前作业 / 课后习题 · 四步发布 · 批改工作台人工终审</div>
        </div>
        <span class="tv3-tag tv3-tag--gold">选择自动判 · 主观题人工批</span>
        <router-link to="/teacher-v3/grading" class="tv3-btn tv3-btn--sm tv3-btn--primary" data-testid="tv3-assign-to-grading">批改工作台 →</router-link>
        <router-link to="/teacher-v3/insights" class="tv3-btn tv3-btn--sm" data-testid="tv3-assign-to-insights">学情洞察 →</router-link>
      </div>
    </div>

    <!-- ===== 发布四步编排（选班 → 组卷 → 确认 → 发布；直调 8000 唯一权威） ===== -->
    <div class="tv3-card" style="margin-bottom: 14px" data-testid="tv3-publish-card">
      <div class="tv3-card__head">
        <span class="tv3-card__title">发布作业</span>
        <span class="tv3-card__sub">组卷 → 确认题集 → 建作业 → 发布（每步真实落库，可中断）</span>
        <div class="tv3-card__spacer" />
        <div class="tv3-seg">
          <button
            v-for="(s, i) in PUBLISH_STEPS" :key="s"
            class="tv3-seg__btn" :class="{ 'is-active': stageIndex >= i }"
            :data-testid="`tv3-pub-step-${s}`"
          >{{ i + 1 }}. {{ s }}</button>
        </div>
      </div>
      <div class="tv3-card__body" style="display: flex; flex-direction: column; gap: 10px">
        <!-- 无班级引导（联动班级管理） -->
        <div v-if="classLoaded && !myClasses.length" class="tv3-prep__diffitem" style="display: flex; gap: 10px; align-items: center" data-testid="tv3-assign-no-class">
          <span style="font-size: 12.5px; color: var(--tv3-ink2)">还没有班级：先到班级管理建班，学生确认入班后才能发布。</span>
          <router-link to="/teacher-v3/classes" class="tv3-btn tv3-btn--sm tv3-btn--primary">去班级管理</router-link>
        </div>

        <!-- 第 1 步：选班 + 类型 + 标题 + 截止 -->
        <div style="display: flex; gap: 10px; flex-wrap: wrap; align-items: center">
          <select v-model="form.classId" class="tv3-input" style="width: 180px; background: #fff; border-color: var(--tv3-line); color: var(--tv3-ink)" data-testid="tv3-pub-class">
            <option v-for="c in myClasses" :key="c.id" :value="c.id">{{ c.name }}（{{ c.students }} 人）</option>
          </select>
          <select v-model="form.type" class="tv3-input" style="width: 160px; background: #fff; border-color: var(--tv3-line); color: var(--tv3-ink)" data-testid="tv3-pub-type">
            <option value="pre_class">🎓 学前作业</option>
            <option value="homework">📝 课后习题</option>
            <option value="quiz">✏️ 课堂练习</option>
          </select>
          <input v-model="form.title" class="tv3-input" style="flex: 1; min-width: 200px; background: #fff; border-color: var(--tv3-line); color: var(--tv3-ink)" placeholder="作业标题（如：椭圆标准方程 · 学前诊断）" data-testid="tv3-pub-title">
          <label style="font-size: 12px; color: var(--tv3-ink3); display: inline-flex; gap: 6px; align-items: center">
            截止
            <input v-model="form.deadline" type="datetime-local" class="tv3-input" style="width: 210px; background: #fff; border-color: var(--tv3-line); color: var(--tv3-ink)" data-testid="tv3-pub-deadline">
          </label>
        </div>

        <!-- 第 2 步：组卷（知识点 + 题量 + 题型配比） -->
        <div style="display: flex; gap: 10px; flex-wrap: wrap; align-items: flex-start">
          <div style="flex: 1; min-width: 260px">
            <div class="tv3-form-label">知识点（题库有题的章节；可多选）</div>
            <div v-if="kpLoading" style="font-size: 12px; color: var(--tv3-ink3)">知识点目录加载中…</div>
            <div v-else-if="!kpOptions.length" style="font-size: 12px; color: var(--tv3-ink3); display: flex; gap: 8px; align-items: center; flex-wrap: wrap" data-testid="tv3-pub-kp-empty">
              <span>当前 8000 题库没有可出题知识点——直接去「题库与组卷」选 786 道校本题，发布后学生课堂任务立即可见。</span>
              <router-link to="/teacher-v3/quiz" class="tv3-btn tv3-btn--sm tv3-btn--gold" data-testid="tv3-pub-goto-quiz">去组卷并发布 →</router-link>
            </div>
            <div v-else style="display: flex; flex-wrap: wrap; gap: 6px; max-height: 96px; overflow: auto; padding: 4px" data-testid="tv3-pub-kps">
              <button
                v-for="kp in kpOptions" :key="kp.code"
                class="tv3-tag" style="cursor: pointer; font-size: 11px"
                :class="form.kps.includes(kp.code) ? 'tv3-tag--primary' : ''"
                :data-testid="`tv3-pub-kp-${kp.code}`"
                @click="toggleKp(kp.code)"
              >{{ kp.name }}（{{ kp.bank_count }} 题）</button>
            </div>
          </div>
          <div>
            <div class="tv3-form-label">题量</div>
            <input v-model.number="form.count" type="number" min="1" max="30" class="tv3-input" style="width: 84px; background: #fff; border-color: var(--tv3-line); color: var(--tv3-ink)" data-testid="tv3-pub-count">
          </div>
          <div>
            <div class="tv3-form-label">题型配比（选择/填空/解答）</div>
            <div style="display: flex; gap: 6px">
              <input v-model.number="form.types.choice" type="number" min="0" class="tv3-input" style="width: 64px; background: #fff; border-color: var(--tv3-line); color: var(--tv3-ink)" title="选择题数量" data-testid="tv3-pub-type-choice">
              <input v-model.number="form.types.blank" type="number" min="0" class="tv3-input" style="width: 64px; background: #fff; border-color: var(--tv3-line); color: var(--tv3-ink)" title="填空题数量" data-testid="tv3-pub-type-blank">
              <input v-model.number="form.types.text" type="number" min="0" class="tv3-input" style="width: 64px; background: #fff; border-color: var(--tv3-line); color: var(--tv3-ink)" title="解答题数量" data-testid="tv3-pub-type-text">
            </div>
          </div>
          <div style="align-self: flex-end">
            <button class="tv3-btn tv3-btn--primary" :disabled="!canGenerate || busy" data-testid="tv3-pub-generate" @click="generate">
              {{ stage === 'generating' ? '组卷中…' : '② 生成题集' }}
            </button>
          </div>
        </div>

        <!-- 组卷结果（degraded/warnings 如实呈现，不假成功） -->
        <div v-if="genWarning" class="tv3-tag tv3-tag--warn" style="padding: 8px 12px; border-radius: 8px; white-space: normal; line-height: 1.6" data-testid="tv3-pub-warning">{{ genWarning }}</div>
        <div v-if="flowError" class="tv3-tag tv3-tag--danger" style="padding: 8px 12px; border-radius: 8px; white-space: normal; line-height: 1.6" data-testid="tv3-pub-error">{{ flowError }}</div>

        <!-- 第 3 步：确认题目清单（教师显式确认，无静默） -->
        <template v-if="artifact">
          <div class="tv3-form-label" style="margin-top: 4px">题集预览（{{ artifactItems.length }} 题 · 确认后才能发布）</div>
          <div style="display: flex; flex-direction: column; gap: 6px; max-height: 260px; overflow: auto" data-testid="tv3-pub-items">
            <div v-for="it in artifactItems" :key="it.item_no" class="tv3-row" style="align-items: flex-start">
              <span class="tv3-tag" :class="it.q_type === 'choice' ? 'tv3-tag--ok' : it.q_type === 'blank' ? 'tv3-tag--warn' : 'tv3-tag--danger'" style="font-size: 10px; flex-shrink: 0">
                {{ it.q_type === 'choice' ? '选择·自动判' : it.q_type === 'blank' ? '填空·可拍照人工批' : '解答·人工批' }}
              </span>
              <div style="flex: 1; min-width: 0; font-size: 12.5px; line-height: 1.6" v-html="renderStem(it.question_text)" />
              <span style="font-size: 10.5px; color: var(--tv3-ink4); flex-shrink: 0">{{ it.kp_code || '' }}</span>
            </div>
          </div>
          <div v-if="stage !== 'published'" style="display: flex; justify-content: flex-end; gap: 8px">
            <button class="tv3-btn" data-testid="tv3-pub-reset" @click="resetFlow">放弃重组</button>
            <button class="tv3-btn tv3-btn--gold" :disabled="busy" data-testid="tv3-pub-confirm-publish" @click="confirmAndPublish">
              {{ busy && stageIndex >= 2 ? '确认发布中…' : '③ 确认题集并发布 → 班级任务中心' }}
            </button>
          </div>
        </template>

        <!-- 第 4 步：发布成功回执 -->
        <div v-if="published" class="tv3-prep__diffitem" style="border-color: var(--tv3-teal, #0e9488); background: #f4fbfa" data-testid="tv3-pub-success">
          <div style="display: flex; gap: 8px; align-items: center; flex-wrap: wrap">
            <span class="tv3-tag tv3-tag--ok">✓ 已发布</span>
            <b style="font-size: 13px">{{ published.title }}</b>
            <span class="tv3-tag" style="font-size: 10.5px">{{ typeLabel(published.type) }}</span>
            <span style="font-size: 12px; color: var(--tv3-ink2)">学生将在「课堂 → 课堂任务」中心看到这份作业{{ published.deadline ? `，截止 ${fmtDate(published.deadline)}` : '' }}。</span>
            <div class="tv3-card__spacer" />
            <router-link to="/teacher-v3/classroom" class="tv3-btn tv3-btn--sm">去课堂看学情</router-link>
            <router-link to="/teacher-v3/grading" class="tv3-btn tv3-btn--sm tv3-btn--primary">去批改 →</router-link>
          </div>
        </div>
      </div>
    </div>

    <!-- ===== 作业列表（8000 唯一口径；8100 平行发布域 UI 已移除入口，D4） ===== -->
    <div class="tv3-card" data-testid="tv3-assign-list">
      <div class="tv3-card__head">
        <span class="tv3-card__title">作业列表</span>
        <span class="tv3-card__sub">来自班级任务权威（8000）· 学生提交与批改进度在批改工作台</span>
        <div class="tv3-card__spacer" />
        <select v-model="listClassId" class="tv3-input" style="width: 170px; background: #fff; border-color: var(--tv3-line); color: var(--tv3-ink)" data-testid="tv3-assign-list-class">
          <option value="">全部班级</option>
          <option v-for="c in myClasses" :key="c.id" :value="c.id">{{ c.name }}</option>
        </select>
        <button class="tv3-btn tv3-btn--sm" @click="loadAssignments">刷新</button>
      </div>
      <div class="tv3-card__body" style="display: flex; flex-direction: column; gap: 8px">
        <TeacherLoading v-if="listLoading" />
        <div v-if="listError" class="tv3-tag tv3-tag--danger" style="padding: 8px 12px; border-radius: 8px" data-testid="tv3-assign-load-error">
          作业列表加载失败：{{ listError }}
          <button class="tv3-btn tv3-btn--sm" style="margin-left: 8px" @click="loadAssignments">重试</button>
        </div>
        <div v-if="!listLoading && !assignments.length && !listError" style="padding: 24px; text-align: center; color: var(--tv3-ink3); font-size: 13px" data-testid="tv3-assign-empty">
          还没有作业。用上方「发布作业」四步编排，或去「题库与组卷」选题发布——两条路都会进入同一个学生课堂任务中心。
          <div style="margin-top: 10px"><router-link to="/teacher-v3/quiz" class="tv3-btn tv3-btn--sm tv3-btn--gold">去题库与组卷 →</router-link></div>
        </div>
        <div v-for="a in assignments" :key="a.assignment_id" class="tv3-row" :data-testid="`tv3-assign-row-${a.assignment_id}`">
          <span class="tv3-tag" style="font-size: 10.5px">{{ typeLabel(a.type) }}</span>
          <div style="flex: 1; min-width: 0">
            <div style="font-size: 14px; font-weight: 600">{{ a.title }}</div>
            <div style="font-size: 11.5px; color: var(--tv3-ink3)">
              {{ classLabel(a.class_id) }} · 创建于 {{ fmtDate(a.created_at) }}{{ a.deadline ? ' · 截止 ' + fmtDate(a.deadline) : '' }}<template v-if="a.replayed"> · 幂等回读</template>
            </div>
          </div>
          <span class="tv3-tag" :class="a.status === 'published' ? 'tv3-tag--ok' : a.status === 'draft' ? 'tv3-tag--warn' : ''" style="font-size: 10.5px" :data-testid="`tv3-assign-status-${a.status}`">
            {{ statusLabel(a.status) }}
          </span>
          <button
            v-if="a.status === 'draft'"
            class="tv3-btn tv3-btn--sm tv3-btn--gold"
            :disabled="publishingId === a.assignment_id"
            :data-testid="`tv3-assign-publish-${a.assignment_id}`"
            @click="republish(a)"
          >{{ publishingId === a.assignment_id ? '发布中…' : '发布到课堂' }}</button>
          <router-link v-if="a.status !== 'draft'" to="/teacher-v3/grading" class="tv3-btn tv3-btn--sm">批改进度 →</router-link>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * AssignView —— 作业与批改（tel PLAN §1.2②：8000 四步编排发布）
 * 发布链直调 8000（D1：身份/成员/任务唯一权威；经既有 /api 代理 + 全站单 token）：
 *   generate（题库供给组卷）→ confirm（教师显式确认题集）→ create（client_assignment_id 幂等）
 *   → publish（学生课堂任务中心可见）。
 * 8100 平行批改/发布域 UI（simulate 按钮、聚类批改卡、8100 作业列表）整体移除入口（D4
 * 前端停止消费，后端 KEEP）；批改工作台独立成页 GradingWorkspaceView。
 */
import { computed, onMounted, ref, watch } from 'vue'
import { api8000, telKey, type ApiAssignment, type ApiAssignmentType, type ApiClassItem, type ApiQuizSetArtifact, type ApiQuizSetItem } from '@/api/api8000'
import { ASSIGNMENT_STATUS_LABEL, ASSIGNMENT_TYPE_LABEL, pickDefaultClassId, runPublishFlow, type PublishStage } from './publishFlow'
import { TeacherLoading } from '@/components/teacherV3/common'
import { renderStem } from '@/components/mathx/latex'
import { useToastStore } from '@/stores/toast'
import { updateTv3Context } from '@/stores/teacherContext'
import { teachingClasses } from '@/utils/teachingClasses'

const PUBLISH_STEPS = ['选班', '组卷', '确认发布', '完成'] as const

const toast = useToastStore()

/* ---------- 班级（真实目录，无默认 mock 班级） ---------- */
const myClasses = ref<ApiClassItem[]>([])
const classLoaded = ref(false)
const form = ref({
  classId: '',
  type: 'pre_class' as ApiAssignmentType,
  title: '',
  deadline: '',
  kps: [] as string[],
  count: 6,
  types: { choice: 4, blank: 1, text: 1 },
})
const listClassId = ref('')

/* ---------- 知识点目录（题库有题的章节，真实计数） ---------- */
const kpOptions = ref<{ code: string; name: string; bank_count: number }[]>([])
const kpLoading = ref(false)

/* ---------- 四步编排状态 ---------- */
const stage = ref<PublishStage>('idle')
const artifact = ref<ApiQuizSetArtifact | null>(null)
const published = ref<ApiAssignment | null>(null)
const flowError = ref('')
const busy = ref(false)
const clientAssignmentId = ref('')

const stageIndex = computed(() => ({ generating: 1, generated: 2, confirming: 2, confirmed: 2, creating: 2, publishing: 2, published: 3 } as Record<string, number>)[stage.value] ?? 0)
const artifactItems = computed<ApiQuizSetItem[]>(() => artifact.value?.content?.items ?? [])
const canGenerate = computed(() =>
  !!form.value.classId && !!form.value.title.trim() && form.value.kps.length > 0
  && form.value.count > 0 && form.value.count <= 30,
)
const genWarning = computed(() => {
  if (!artifact.value) return ''
  const parts: string[] = []
  if (artifact.value.degraded || artifact.value.content?.insufficient) parts.push('题库严格命中不足，本卷未凑满目标题量。')
  for (const w of artifact.value.warnings || []) parts.push(w)
  return parts.join(' ')
})

const typeLabel = (t: string) => ASSIGNMENT_TYPE_LABEL[t as ApiAssignmentType] ?? t
const statusLabel = (s: string) => ASSIGNMENT_STATUS_LABEL[s] ?? s
function fmtDate(iso: string | null | undefined): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return String(iso).slice(0, 16)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}
const classLabel = (classId: string) => myClasses.value.find((c) => c.id === classId)?.name || classId.slice(0, 8)

function toggleKp(code: string) {
  const i = form.value.kps.indexOf(code)
  if (i >= 0) form.value.kps.splice(i, 1)
  else form.value.kps.push(code)
}

/* ---------- 第 2 步：组卷（draft artifact，含不足如实警示） ---------- */
async function generate() {
  if (!canGenerate.value) return
  flowError.value = ''
  artifact.value = null
  published.value = null
  clientAssignmentId.value = telKey('assign')
  stage.value = 'generating'
  try {
    const art = await api8000.quizzes.generate({
      class_id: form.value.classId,
      knowledge_points: form.value.kps,
      count: form.value.count,
      question_types: { ...form.value.types },
      difficulty: { easy: 0, medium: 0, hard: 0 },
    })
    artifact.value = art
    stage.value = 'generated'
  } catch (e) {
    stage.value = 'failed'
    flowError.value = e instanceof Error ? `组卷失败：${e.message}` : '组卷失败，请调整知识点或题量后重试'
  }
}

/* ---------- 第 3+4 步：确认题集 → 建作业 → 发布 ---------- */
async function confirmAndPublish() {
  if (!artifact.value || !form.value.classId) return
  if (!window.confirm(`确认发布《${form.value.title}》？\n· 题集一经确认即锁定（${artifactItems.value.length} 题）\n· 发布后学生课堂任务中心立即可见\n\n确认？`)) return
  busy.value = true
  flowError.value = ''
  try {
    await runPublishFlow(
      {
        classId: form.value.classId,
        title: form.value.title.trim(),
        type: form.value.type,
        deadline: form.value.deadline ? new Date(form.value.deadline).toISOString() : undefined,
        knowledgePoints: form.value.kps,
        count: form.value.count,
        questionTypes: { ...form.value.types },
        difficulty: { easy: 0, medium: 0, hard: 0 },
        clientAssignmentId: clientAssignmentId.value || telKey('assign'),
        // tel E2E 修复：把教师预览并确认的这份题集传入发布链，禁止链内二次 generate 换题
        artifact: artifact.value ?? undefined,
      },
      {
        generate: (input) => api8000.quizzes.generate({
          class_id: input.classId,
          knowledge_points: input.knowledgePoints,
          count: input.count,
          question_types: input.questionTypes,
          difficulty: input.difficulty,
        }),
        confirm: (id, crid, idem) => api8000.artifacts.confirm(id, crid, idem),
        create: (body) => api8000.assignments.create(body),
        publish: (id, crid, idem) => api8000.assignments.publish(id, crid, idem),
        onStage: (s, detail) => {
          stage.value = s
          if (s === 'published' && detail?.assignment) published.value = detail.assignment
        },
      },
    )
    // runPublishFlow 内 onStage('published') 已把终态写入 stage；回执对象从 publish 步取
    toast.success('已发布：学生将在课堂任务中心看到这份作业')
    await loadAssignments()
  } catch (e) {
    flowError.value = e instanceof Error ? `发布失败：${e.message}` : '发布失败，请重试'
    toast.error('发布未完成：请检查上方错误信息后重试（已生成的题集仍可再次确认发布）')
  } finally {
    busy.value = false
  }
}

function resetFlow() {
  artifact.value = null
  published.value = null
  stage.value = 'idle'
  flowError.value = ''
  clientAssignmentId.value = ''
}

/* ---------- 作业列表（8000） ---------- */
const assignments = ref<ApiAssignment[]>([])
const listLoading = ref(false)
const listError = ref('')
const publishingId = ref('')

/** 发布失败留下的 draft：列表内直接重发（复用唯一权威 publish 端点，不新建链路）。 */
async function republish(a: ApiAssignment) {
  if (!a?.assignment_id || publishingId.value) return
  publishingId.value = a.assignment_id
  try {
    await api8000.assignments.publish(a.assignment_id, telKey('assign-republish'), telKey('assign-republish-idem'))
    toast.success('已发布：学生课堂任务中心现在可见')
    await loadAssignments()
  } catch (e) {
    toast.error(e instanceof Error ? `发布失败：${e.message}` : '发布失败，请重试')
  } finally {
    publishingId.value = ''
  }
}

async function loadAssignments() {
  listLoading.value = true
  listError.value = ''
  try {
    const r = await api8000.assignments.list({ class_id: listClassId.value || undefined })
    assignments.value = r.assignments
  } catch (e) {
    listError.value = e instanceof Error ? e.message : '加载失败'
  } finally {
    listLoading.value = false
  }
}

onMounted(async () => {
  try {
    const r = await api8000.classes.list()
    myClasses.value = teachingClasses(r.items)
    const def = pickDefaultClassId(myClasses.value)
    if (def) form.value.classId = def
  } catch { /* 班级加载失败：发布卡显示无班级引导 */ } finally {
    classLoaded.value = true
  }
  kpLoading.value = true
  try {
    const r = await api8000.kpCatalog()
    kpOptions.value = r.items
  } catch { /* 空目录诚实呈现 */ } finally {
    kpLoading.value = false
  }
  await loadAssignments()
})

watch(listClassId, () => loadAssignments())

/* B6 上下文写入（管家联动保留） */
watch([assignments, published], () => {
  updateTv3Context({
    route: '/teacher-v3/assign',
    topic: published.value?.title,
    class_name: classLabel(form.value.classId),
    extra: `作业 ${assignments.value.length} 份${published.value ? ' · 刚发布一份' : ''}`,
  })
})
</script>
