<template>
  <div class="poc">
    <header class="poc-head">
      <div class="poc-head__left">
        <button class="ptx-back" data-testid="ailp-oc-back" @click="emit('back')">←</button>
        <h1>AI 生成教案 · 大纲确认</h1>
      </div>
      <div class="ptx-steps">
        <div class="ailp-step-dot completed">✓</div><span>配置</span>
        <i class="ptx-line on" />
        <div class="ailp-step-dot completed">✓</div><span>AI生成</span>
        <i class="ptx-line on" />
        <div class="ailp-step-dot active">3</div><span class="on">确认编辑</span>
      </div>
      <div class="poc-head__user"><div class="poc-avatar">李</div><span>李老师</span></div>
    </header>

    <main class="poc-main">
      <!-- 成功头 -->
      <div class="poc-success ailp-fade">
        <div class="poc-success__icon">✓</div>
        <h2>教案大纲已生成 🎉</h2>
        <p class="poc-success__line">{{ chain.brief?.topic }} · {{ chain.brief?.courseType }} · {{ outline?.total_minutes || chain.brief?.duration }}分钟</p>
        <p class="poc-success__eta">⚡ 本大纲由 AI 基于课题与课标约束真实生成——每一处都可改，定稿前请逐环节核对</p>
      </div>

      <!-- 大纲预览卡 -->
      <div class="poc-card ailp-fade" style="animation-delay:0.15s">
        <div class="poc-card__head">
          <div class="poc-card__headic">📄</div>
          <div><h3>教案大纲预览</h3><p>共 5 个模块 · {{ outline?.sections.length || 0 }} 个教学环节</p></div>
          <span class="ailp-honest" style="margin-left:auto">AI 已按课标与课题编排，确认后并行生成全部模块</span>
        </div>
        <div class="poc-card__body">
          <!-- 教学目标 -->
          <section class="poc-sec" data-testid="ailp-oc-objectives">
            <button class="poc-sec__head" @click="toggle('obj')">
              <span class="poc-sec__ic is-primary">🎯</span>
              <div><h4>教学目标</h4><small>{{ outline?.objectives?.length || 0 }} 条目标</small></div>
              <i class="poc-sec__chev">{{ open.obj ? '▾' : '▸' }}</i>
            </button>
            <ol v-if="open.obj" class="poc-sec__ol">
              <li v-for="(o, i) in outline?.objectives" :key="i"><b>{{ i + 1 }}.</b> {{ o }}</li>
            </ol>
          </section>

          <!-- 重难点 -->
          <section class="poc-sec">
            <button class="poc-sec__head" @click="toggle('kp')">
              <span class="poc-sec__ic is-warn">⚠️</span>
              <div><h4>教学重点与难点</h4><small>重点 {{ outline?.keypoints?.major.length || 0 }} 项 · 难点 {{ outline?.keypoints?.hard.length || 0 }} 项</small></div>
              <i class="poc-sec__chev">{{ open.kp ? '▾' : '▸' }}</i>
            </button>
            <div v-if="open.kp" class="poc-kpgrid">
              <div class="poc-kp is-major"><b>★ 教学重点</b><p v-for="(k, i) in outline?.keypoints?.major" :key="i">{{ k }}</p></div>
              <div class="poc-kp is-hard"><b>🔥 教学难点</b><p v-for="(k, i) in outline?.keypoints?.hard" :key="i">{{ k }}</p></div>
            </div>
          </section>

          <!-- 教学过程 -->
          <section class="poc-sec">
            <button class="poc-sec__head" @click="toggle('process')">
              <span class="poc-sec__ic is-accent">🗂</span>
              <div><h4>教学过程</h4><small>{{ outline?.sections.length }} 个环节 · 共 {{ outline?.total_minutes }} 分钟</small></div>
              <i class="poc-sec__chev">{{ open.process ? '▾' : '▸' }}</i>
            </button>
            <div v-if="open.process" class="poc-phases">
              <div v-for="(s, i) in processSections" :key="s.id" class="poc-phase" :class="phaseCls(s.name)">
                <div class="poc-phase__head">
                  <span class="poc-phase__no">{{ i + 1 }}</span>
                  <h5>{{ s.name }}</h5>
                  <span class="poc-phase__min">{{ s.minutes }} 分钟</span>
                </div>
                <p>{{ s.goal }}</p>
                <p v-if="s.example_suggestion" class="poc-phase__ex">📌 {{ s.example_suggestion }}</p>
                <!-- P2：AI 预配资源（题目=真实题库匹配；公式/图形仅显式演示模式下出现，随初稿带入编辑器） -->
                <div v-if="resOf(s.id).questions.length || resOf(s.id).formulas.length || resOf(s.id).figure" class="poc-res">
                  <div class="poc-res__badge">✦ AI 预配 <small>题目=题库检索匹配<template v-if="resOf(s.id).formulas.length || resOf(s.id).figure"> · 公式/图形=演示素材</template> · 随初稿带入，编辑器可调整</small></div>
                  <div v-for="(q, qi) in resOf(s.id).questions" :key="`q${qi}`" class="poc-res__card">
                    <span class="poc-res__kind is-q">题</span>
                    <div class="poc-res__body">
                      <div class="poc-res__latex" v-html="renderLatex(q.stem)" />
                      <small>{{ q.source }} · {{ q.difficulty }}</small>
                    </div>
                  </div>
                  <div v-for="(f, fi) in resOf(s.id).formulas" :key="`f${fi}`" class="poc-res__card">
                    <span class="poc-res__kind is-f">σ</span>
                    <div class="poc-res__body"><div class="poc-res__latex" v-html="renderLatex(f)" /><small>核心公式</small></div>
                  </div>
                  <div v-if="resOf(s.id).figure" class="poc-res__card poc-res__card--fig">
                    <span class="poc-res__kind is-g">📐</span>
                    <div class="poc-res__body">
                      <div class="poc-res__fig" v-html="resOf(s.id).figure!.svg" />
                      <small>{{ resOf(s.id).figure!.name }}（示意图）</small>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </section>

          <!-- 板书设计 -->
          <section class="poc-sec">
            <button class="poc-sec__head" @click="toggle('board')">
              <span class="poc-sec__ic is-violet">🧩</span>
              <div><h4>板书设计</h4><small>主板书 + 副板书</small></div>
              <i class="poc-sec__chev">{{ open.board ? '▾' : '▸' }}</i>
            </button>
            <div v-if="open.board" class="poc-board">
              <div class="poc-board__main"><small>主板书（左 2/3）</small><p v-for="(l, i) in outline?.blackboard?.main" :key="i">{{ l }}</p></div>
              <div class="poc-board__side"><small>副板书（右 1/3）</small><p v-for="(l, i) in outline?.blackboard?.side" :key="i">{{ l }}</p></div>
            </div>
          </section>

          <!-- 作业布置 -->
          <section class="poc-sec">
            <button class="poc-sec__head" @click="toggle('hw')">
              <span class="poc-sec__ic is-pink">📚</span>
              <div><h4>作业布置</h4><small>基础 + 提升 + 拓展 · 3 层</small></div>
              <i class="poc-sec__chev">{{ open.hw ? '▾' : '▸' }}</i>
            </button>
            <div v-if="open.hw" class="poc-hwgrid">
              <div v-for="h in outline?.homework" :key="h.tier" class="poc-hw" :class="`is-${h.tier}`">
                <b>{{ h.label }}</b>
                <p v-for="(it, i) in h.items" :key="i">{{ it }}</p>
                <small>{{ h.minutes }}</small>
              </div>
            </div>
          </section>
        </div>
      </div>

      <!-- AI 快捷调整 -->
      <div class="poc-adjust ailp-fade" style="animation-delay:0.25s">
        <h4>✦ AI 快捷调整 <small>点击快速微调，AI 将重新编排对应部分</small></h4>
        <div class="poc-adjust__chips">
          <button class="ailp-chip" data-testid="ailp-oc-chip-难度" @click="adjust('难度再加深一些')">🎚 调整难度</button>
          <button class="ailp-chip" data-testid="ailp-oc-chip-例题" @click="adjust('增加例题')">➕ 增加例题</button>
          <button class="ailp-chip" data-testid="ailp-oc-chip-互动" @click="adjust('增加互动环节')">💡 增加互动环节</button>
          <button class="ailp-chip" data-testid="ailp-oc-chip-学情" @click="adjust('针对本班学情调整')">👥 针对本班学情调整</button>
          <button class="ailp-chip" data-testid="ailp-oc-chip-真题" @click="adjust('增加高考真题')">🎓 增加高考真题</button>
        </div>
        <div class="poc-adjust__custom">
          <input v-model="custom" class="poc-adjust__input" placeholder="或直接输入调整要求，例如：去掉作业布置环节" data-testid="ailp-oc-adjust-input" @keydown.enter="adjust(custom)">
          <button class="ailp-chip" data-testid="ailp-oc-adjust" @click="adjust(custom)">✦ 按要求重排</button>
        </div>
        <div v-if="adjustNotes.length" class="poc-adjust__notes" data-testid="ailp-oc-adjust-notes">
          <div v-for="(n, i) in adjustNotes" :key="i">• {{ n }}</div>
        </div>
      </div>

      <!-- 底部动作 -->
      <div class="poc-actions ailp-fade" style="animation-delay:0.35s">
        <button class="ailp-btn-ghost poc-regen" @click="regen">↻ 重新生成</button>
        <div class="poc-actions__right">
          <button class="ailp-btn-primary poc-go" data-testid="ailp-oc-continue" :disabled="entering" @click="continueEdit">
            {{ entering ? '正在生成完整教案…' : '继续编辑 →' }}
          </button>
          <small>进入编辑器后可自由修改所有内容</small>
        </div>
      </div>
    </main>
  </div>
</template>

<script setup lang="ts">
/**
 * PrepOutlineConfirm —— 生成完成 + 大纲确认（P，用户定稿 HTML 移植）
 * 五模块卡（目标/重难点/过程/板书/作业，可折叠）+ AI 快捷调整 chips（词表编译回 notes）+ 继续编辑。
 * 继续编辑 = 真实走 generation/plan SSE → plan_id → 进入编辑器。
 */
import { computed, onMounted, reactive, ref } from 'vue'
import { v3Api } from '@/api/teacherV3'
import { mapLegacyPrepSnapshot, runLegacyTeachingAction, sendLegacyTeachingMessage } from './legacyKernelBridge'
import { renderLatex } from '@/components/mathx/latex'
import { applyKernelProjection, enterEditor, usePrepChain } from './prepChain'
import { matchPhaseResources, type PhaseResource } from './ailpMock'
import { useToastStore } from '@/stores/toast'

const emit = defineEmits<{ (e: 'back'): void }>()
const chain = usePrepChain()
const toast = useToastStore()
const outline = computed(() => chain.outline)
/** 教学过程只显示授课环节（过滤 non_instructional 板块），与用户定稿 HTML 的 6 环节形态一致 */
const NON_TEACHING = ['课标与学情', '教学目标', '教学重难点']
const processSections = computed(() => (outline.value?.sections || []).filter((s) => !NON_TEACHING.includes(s.name) && s.minutes > 0))

/* P2：环节资源预配——题目（真题库匹配）/ 公式 / 图形（演示） */
const bank = ref<{ stem_latex: string; source?: string; difficulty: string; kp_name: string }[]>([])
const lib = ref<{ name: string; thumb: string }[]>([])
onMounted(async () => {
  try { const r = await v3Api.catalog.quizQuestions(); bank.value = r.data.items } catch { bank.value = [] }
  try { const r = await v3Api.draw.library(); lib.value = r.data.items } catch { lib.value = [] }
})
const resMap = computed<Record<string, PhaseResource>>(() => {
  const m: Record<string, PhaseResource> = {}
  for (const s of processSections.value) m[s.id] = matchPhaseResources(bank.value, lib.value, outline.value?.topic || '', s.name)
  return m
})
const resOf = (id: string) => resMap.value[id] || { questions: [], formulas: [], figure: undefined }
const open = reactive({ obj: true, kp: true, process: true, board: false, hw: false })
const custom = ref('')
const adjustNotes = ref<string[]>([])
const entering = ref(false)

function toggle(k: keyof typeof open) { open[k] = !open[k] }
function phaseCls(name: string) {
  if (/引入|情境/.test(name)) return 'is-intro'
  if (/概念|定义|探究|形成/.test(name)) return 'is-concept'
  if (/例题|精讲/.test(name)) return 'is-example'
  if (/练习|训练|检测|互动/.test(name)) return 'is-practice'
  if (/小结|归纳/.test(name)) return 'is-summary'
  return 'is-homework'
}

async function reoutline(extra: string[]) {
  const base = chain.brief
  if (!base) return
  const requirements = [...base.requirements, ...extra]
  if (!chain.sessionId || !chain.sessionRevision) {
    toast.error('当前备课尚未建立服务端会话，无法重排。')
    return
  }
  try {
    const snapshot = await sendLegacyTeachingMessage(chain.sessionId, requirements.join('；'), chain.sessionRevision)
    const projection = mapLegacyPrepSnapshot(snapshot)
    applyKernelProjection(projection)
    if (!projection.outline) throw new Error(projection.blockedReason || '服务端未返回可确认蓝图')
    adjustNotes.value = projection.outline.notes || []
  } catch (e: any) {
    toast.error(e?.message ? `服务端重排失败：${e.message}` : '服务端重排失败，请稍后重试')
  }
}
function adjust(text: string) {
  const t = text.trim()
  if (!t) { toast.info('输入调整要求后再重排'); return }
  void reoutline([t])
}
function regen() { void reoutline([]) }

async function continueEdit() {
  if (!chain.outline || !chain.sessionId || !chain.sessionRevision || !chain.blueprint) return
  entering.value = true
  try {
    const blueprintId = String(chain.blueprint.blueprint_id || '')
    const confirmAction = chain.availableActions.find((a) => a.action === 'confirm_proposal' && !a.disabled)
    if (!confirmAction || confirmAction.target !== blueprintId) throw new Error('服务端当前未提供蓝图确认动作')
    const confirmed = await runLegacyTeachingAction(chain.sessionId, 'confirm_proposal', blueprintId, chain.sessionRevision)
    const confirmedProjection = mapLegacyPrepSnapshot(confirmed)
    applyKernelProjection(confirmedProjection)
    const revision = chain.sessionRevision
    const projectAction = chain.availableActions.find((a) => a.action === 'project_specs' && !a.disabled)
    if (!projectAction || projectAction.target !== blueprintId || !revision) throw new Error('服务端当前未提供教案与课件投影动作')
    const projected = await runLegacyTeachingAction(chain.sessionId, 'project_specs', blueprintId, revision, { kinds: ['lesson_plan', 'deck'] })
    const projection = mapLegacyPrepSnapshot(projected)
    applyKernelProjection(projection)
    const planId = projection.lessonPlanSpec?.spec_id
    if (typeof planId !== 'string' || !planId) throw new Error('服务端尚未生成 LessonPlanSpec，编辑器保持只读')
    enterEditor(planId)
  } catch (e: any) {
    toast.error(e?.message ? `服务端教案投影失败：${e.message}——大纲仍保留，可重试` : '服务端教案投影失败——大纲仍保留，可重试')
  } finally { entering.value = false }
}
</script>

<style scoped>
.poc { padding-bottom: 50px; }
.poc-head { position: relative; z-index: 5; display: flex; align-items: center; justify-content: space-between; padding: 13px 32px; border-bottom: 1px solid var(--ailp-gray-200); background: rgba(255, 255, 255, 0.75); backdrop-filter: blur(6px); }
.poc-head__left { display: flex; align-items: center; gap: 12px; }
.poc-head h1 { font-size: 16px; font-weight: 700; }
.ptx-back { width: 34px; height: 34px; border-radius: 10px; border: none; background: none; cursor: pointer; font-size: 15px; color: var(--ailp-gray-600); }
.ptx-steps { display: flex; align-items: center; gap: 8px; font-size: 12.5px; color: var(--ailp-gray-500); }
.ptx-steps .on { color: var(--ailp-primary-600); font-weight: 600; }
.ptx-line { width: 36px; height: 2px; background: var(--ailp-gray-200); }
.ptx-line.on { background: linear-gradient(90deg, var(--ailp-success-500), var(--ailp-primary-500)); }
.poc-head__user { display: flex; align-items: center; gap: 8px; font-size: 12.5px; font-weight: 500; }
.poc-avatar { width: 32px; height: 32px; border-radius: 50%; display: grid; place-items: center; color: #fff; background: linear-gradient(135deg, #818cf8, #c084fc); font-size: 12.5px; }
.poc-main { position: relative; z-index: 5; max-width: 900px; margin: 0 auto; padding: 28px 24px 70px; }
.poc-success { text-align: center; margin-bottom: 26px; }
.poc-success__icon { width: 76px; height: 76px; border-radius: 50%; display: grid; place-items: center; font-size: 32px; color: #fff; margin: 0 auto 16px; background: linear-gradient(135deg, #6366f1, #4f46e5 50%, #7c3aed); box-shadow: 0 10px 40px rgba(79, 70, 229, 0.4); animation: ailp-pulse 2.2s ease-in-out infinite; }
.poc-success h2 { font-size: 30px; font-weight: 800; margin-bottom: 8px; }
.poc-success__line { font-size: 15px; font-weight: 600; color: var(--ailp-gray-700); margin-bottom: 4px; }
.poc-success__eta { font-size: 12.5px; color: var(--ailp-gray-500); }
.poc-success__eta b { color: var(--ailp-gray-700); }
.poc-card { background: #fff; border: 1px solid var(--ailp-gray-200); border-radius: 18px; box-shadow: 0 14px 36px rgba(15, 23, 42, 0.08); overflow: hidden; }
.poc-card__head { display: flex; align-items: center; gap: 11px; padding: 15px 20px; border-bottom: 1px solid var(--ailp-gray-200); background: linear-gradient(90deg, rgba(79, 70, 229, 0.05), rgba(6, 182, 212, 0.04)); }
.poc-card__headic { width: 38px; height: 38px; border-radius: 11px; display: grid; place-items: center; font-size: 17px; background: linear-gradient(135deg, #4f46e5, #7c3aed); }
.poc-card__head h3 { font-size: 14.5px; font-weight: 700; }
.poc-card__head p { font-size: 11px; color: var(--ailp-gray-500); }
.poc-card__body { padding: 16px 18px; display: flex; flex-direction: column; gap: 11px; }
.poc-sec { border: 1px solid var(--ailp-gray-200); border-radius: 14px; background: #fff; transition: border-color 0.2s ease; }
.poc-sec:hover { border-color: rgba(99, 102, 241, 0.3); }
.poc-sec__head { width: 100%; display: flex; align-items: center; gap: 11px; background: none; border: none; padding: 13px 16px; cursor: pointer; text-align: left; }
.poc-sec__ic { width: 32px; height: 32px; border-radius: 9px; display: grid; place-items: center; font-size: 14px; flex-shrink: 0; }
.poc-sec__ic.is-primary { background: var(--ailp-primary-100); }
.poc-sec__ic.is-warn { background: rgba(245, 158, 11, 0.15); }
.poc-sec__ic.is-accent { background: rgba(6, 182, 212, 0.14); }
.poc-sec__ic.is-violet { background: rgba(139, 92, 246, 0.14); }
.poc-sec__ic.is-pink { background: rgba(236, 72, 153, 0.14); }
.poc-sec__head h4 { font-size: 13.5px; font-weight: 700; color: var(--ailp-gray-900); }
.poc-sec__head small { font-size: 10.5px; color: var(--ailp-gray-500); }
.poc-sec__chev { margin-left: auto; color: var(--ailp-gray-400); font-style: normal; font-size: 13px; }
.poc-sec__ol { list-style: none; margin: 0; padding: 0 16px 14px 16px; display: flex; flex-direction: column; gap: 9px; border-top: 1px solid var(--ailp-gray-100); padding-top: 12px; }
.poc-sec__ol li { font-size: 12.5px; color: var(--ailp-gray-700); line-height: 1.7; }
.poc-sec__ol li b { color: var(--ailp-primary-600); margin-right: 4px; }
.poc-kpgrid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; padding: 0 16px 14px; }
.poc-kp { border-radius: 11px; padding: 12px 14px; }
.poc-kp b { display: block; font-size: 12px; margin-bottom: 6px; }
.poc-kp p { font-size: 11.5px; line-height: 1.65; color: var(--ailp-gray-700); margin-bottom: 3px; }
.poc-kp.is-major { background: var(--ailp-success-50); border: 1px solid rgba(16, 185, 129, 0.2); }
.poc-kp.is-major b { color: var(--ailp-success-700); }
.poc-kp.is-hard { background: var(--ailp-error-50); border: 1px solid rgba(239, 68, 68, 0.18); }
.poc-kp.is-hard b { color: var(--ailp-error-700); }
.poc-phases { padding: 2px 16px 14px; display: flex; flex-direction: column; gap: 9px; }
.poc-phase { border-left: 3px solid var(--ailp-primary-300); background: var(--ailp-gray-50); border-radius: 10px; padding: 11px 14px; }
.poc-phase.is-intro { border-left-color: #f59e0b; }
.poc-phase.is-concept { border-left-color: #8b5cf6; }
.poc-phase.is-example { border-left-color: #4f46e5; }
.poc-phase.is-practice { border-left-color: #06b6d4; }
.poc-phase.is-summary { border-left-color: #10b981; }
.poc-phase.is-homework { border-left-color: #ec4899; }
.poc-phase__head { display: flex; align-items: center; gap: 8px; margin-bottom: 5px; }
.poc-phase__no { font-size: 10px; font-weight: 800; padding: 1px 7px; border-radius: 6px; color: var(--ailp-gray-700); background: rgba(0, 0, 0, 0.05); }
.poc-phase__head h5 { font-size: 13px; font-weight: 700; }
.poc-phase__min { margin-left: auto; font-size: 10.5px; padding: 1px 8px; border-radius: 6px; background: #fff; border: 1px solid var(--ailp-gray-200); color: var(--ailp-gray-500); }
.poc-phase p { font-size: 12px; color: var(--ailp-gray-600); line-height: 1.7; padding-left: 26px; }
.poc-phase__ex { color: var(--ailp-primary-700); }
.poc-res { margin: 8px 0 0 26px; display: flex; flex-direction: column; gap: 6px; }
.poc-res__badge { font-size: 10.5px; font-weight: 700; color: var(--ailp-primary-600); }
.poc-res__badge small { color: var(--ailp-gray-400); font-weight: 400; margin-left: 6px; }
.poc-res__card { display: flex; align-items: flex-start; gap: 9px; background: #fff; border: 1px solid var(--ailp-gray-200); border-radius: 9px; padding: 8px 11px; }
.poc-res__card--fig { align-items: center; }
.poc-res__kind { flex-shrink: 0; width: 20px; height: 20px; border-radius: 6px; display: grid; place-items: center; font-size: 10px; font-weight: 800; color: #fff; background: var(--ailp-primary-500); }
.poc-res__kind.is-f { background: var(--ailp-accent-500); }
.poc-res__kind.is-g { background: #8b5cf6; }
.poc-res__body { flex: 1; min-width: 0; }
.poc-res__latex { font-size: 12.5px; line-height: 1.6; overflow-x: auto; }
.poc-res__body small { font-size: 10px; color: var(--ailp-gray-400); }
.poc-res__fig { display: flex; justify-content: center; }
.poc-board { display: grid; grid-template-columns: 2fr 1fr; gap: 10px; padding: 0 16px 14px; }
.poc-board__main, .poc-board__side { border-radius: 10px; border: 1px solid var(--ailp-gray-200); background: var(--ailp-gray-50); padding: 11px 13px; }
.poc-board__side { border-style: dashed; }
.poc-board small { font-size: 10px; font-weight: 700; color: var(--ailp-gray-500); display: block; margin-bottom: 7px; }
.poc-board p { font-size: 11.5px; color: var(--ailp-gray-600); line-height: 1.8; }
.poc-hwgrid { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 9px; padding: 0 16px 14px; }
.poc-hw { border-radius: 10px; border: 1px solid var(--ailp-gray-200); padding: 11px 12px; }
.poc-hw b { display: block; font-size: 11.5px; margin-bottom: 5px; }
.poc-hw p { font-size: 10.5px; color: var(--ailp-gray-600); line-height: 1.6; margin-bottom: 4px; }
.poc-hw small { font-size: 9.5px; color: var(--ailp-gray-400); }
.poc-hw.is-basic { background: var(--ailp-success-50); border-color: rgba(16, 185, 129, 0.2); }
.poc-hw.is-basic b { color: var(--ailp-success-700); }
.poc-hw.is-raise { background: var(--ailp-warning-50); border-color: rgba(245, 158, 11, 0.2); }
.poc-hw.is-raise b { color: var(--ailp-warning-600); }
.poc-hw.is-expand { background: var(--ailp-primary-50); border-color: rgba(79, 70, 229, 0.2); }
.poc-hw.is-expand b { color: var(--ailp-primary-700); }
.poc-adjust { margin-top: 24px; }
.poc-adjust h4 { font-size: 14px; font-weight: 700; margin-bottom: 10px; display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.poc-adjust h4 small { font-size: 10.5px; color: var(--ailp-gray-500); font-weight: 400; }
.poc-adjust__chips { display: flex; gap: 10px; flex-wrap: wrap; }
.poc-adjust__custom { display: flex; gap: 10px; margin-top: 12px; }
.poc-adjust__input { flex: 1; padding: 10px 14px; border-radius: 999px; border: 1px solid var(--ailp-gray-200); font-size: 12.5px; outline: none; background: #fff; }
.poc-adjust__input:focus { border-color: var(--ailp-primary-400); }
.poc-adjust__notes { margin-top: 10px; padding: 10px 14px; border: 1px solid rgba(79, 70, 229, 0.2); background: rgba(79, 70, 229, 0.04); border-radius: 10px; font-size: 11.5px; color: var(--ailp-gray-700); line-height: 1.8; }
.poc-actions { margin-top: 26px; display: flex; align-items: center; justify-content: space-between; }
.poc-regen { padding: 10px 18px; border-radius: 12px; font-size: 13px; }
.poc-actions__right { display: flex; flex-direction: column; align-items: flex-end; gap: 5px; }
.poc-go { padding: 12px 30px; border-radius: 13px; font-size: 14.5px; }
.poc-actions__right small { font-size: 10.5px; color: var(--ailp-gray-500); }
</style>
