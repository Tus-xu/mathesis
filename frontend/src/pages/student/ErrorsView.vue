<template>
  <div class="view">
    <!-- S5 v3：竞品式错题本头部（统计三卡 + 主操作行） -->
    <div class="err-stats">
      <div class="es-card">
        <div class="es-num">{{ totalErrors }}</div>
        <div class="es-lbl">全部错题</div>
      </div>
      <div class="es-card warn">
        <div class="es-num">{{ dueTotal }}</div>
        <div class="es-lbl">今天该复习</div>
      </div>
      <div class="es-card ok">
        <div class="es-num">{{ masteredCount }}</div>
        <div class="es-lbl">记得较牢</div>
      </div>
      <div class="es-ops">
        <button class="es-primary" @click="openEntryCard">📷 拍错题入本</button>
        <!-- 闪卡复习按钮已移除：点错题即进浮窗复习（P1-14） -->
      </div>
    </div>

    <FlashcardReview v-if="flashcardMode" @close="onFcClose" />

    <!-- P1-14 入本方式引导卡：先让用户选择拍照/相册 / 手动录入 -->
    <div v-if="entryCard" class="entry-card">
      <div class="entry-card-box">
        <div class="entry-title">✍ 如何把这道错题入本？</div>
        <button class="entry-btn" @click="pickPhoto">📷 拍照 / 选择图片（OCR 自动识别）</button>
        <button class="entry-btn plain" @click="pickManual">⌨ 手动录入（粘贴/输入题干）</button>
        <button class="entry-close" @click="entryCard = false">取消</button>
      </div>
    </div>

    <!-- 拍错题入本（Vision03：主动收录闭环；OCR 识别题干 + 原图随记录保存） -->
    <div class="card" style="padding:14px 18px;margin-bottom:14px;">
      <div style="display:flex;align-items:center;gap:10px;">
        <strong style="font-size:14px;">📷 拍错题入本</strong>
        <span style="font-size:12px;color:var(--ink3);">把做错/不会的题拍下来自动识别题干——入本自动排期复习</span>
        <button v-if="!manualOpen" class="secondary" style="margin-left:auto;" @click="manualOpen = true">＋ 打开录入</button>
        <button v-else class="secondary" style="margin-left:auto;" @click="manualOpen = false">收起</button>
      </div>
      <template v-if="manualOpen">
        <textarea v-model="manual.question" class="input" rows="2" style="margin-top:10px;" placeholder="OCR 识别出的题干（可修改），或直接粘贴题目"></textarea>
        <textarea v-model="manual.note" class="input" rows="2" style="margin-top:8px;" placeholder="备注：当时怎么错的 / 卡在哪一步（选填）"></textarea>
        <div style="display:flex;gap:8px;margin-top:8px;flex-wrap:wrap;">
          <select v-model="manual.errorType" class="input" style="flex:1;min-width:140px;">
            <option value="">错因：未分类</option>
            <option v-for="t in ERROR_TYPES" :key="t.value" :value="t.value">{{ t.label }}</option>
          </select>
          <select v-model="manual.kpCode" class="input" style="flex:1;min-width:140px;">
            <option value="">知识点：暂不标注</option>
            <option v-for="k in kpOptions" :key="k.value" :value="k.value">{{ k.label }}</option>
          </select>
          <button class="secondary" style="flex:0 0 auto;" :disabled="kpGuessing || !manual.question.trim()" title="根据题干自动识别知识点（可修改，S5：AI 分类+人工修正）" @click="guessKp(manual.question, true)">🔍 AI 识别知识点</button>
        </div>
        <HomeworkPhotos ref="hpRef" v-model="manual.photos" :max="1" @ocr="onManualOcr" />
        <div v-if="ocrNote" style="font-size:12px;margin-top:6px;" :style="{ color: ocrNote.ok ? 'var(--ok,#16a34a)' : 'var(--err,#dc2626)' }">{{ ocrNote.msg }}</div>
        <div v-if="enhancedUrl" style="display:flex;gap:8px;margin-top:6px;align-items:center;">
          <span style="font-size:11.5px;color:var(--ink3);">✨ 已生成增强图（更清晰，原图仍保留）：</span>
          <img :src="enhancedUrl" alt="增强图" style="height:64px;border-radius:8px;cursor:zoom-in;border:1px solid var(--border,#e2e8f0);" @click="openLightbox(enhancedUrl)" />
        </div>
        <div v-else-if="enhancing" style="font-size:11.5px;color:var(--ink3);margin-top:6px;">✨ 正在生成增强图…</div>
        <div style="display:flex;gap:10px;margin-top:10px;">
          <button class="primary" :disabled="manualSubmitting" @click="submitManual">📌 入本</button>
          <span style="font-size:11.5px;color:var(--ink3);align-self:center;">入本后自动排期，到期自动提醒复习</span>
        </div>
      </template>
    </div>
    <!-- FSRS 记忆稳定性热力图（独家创新） -->

    <!-- 今日待复习队列 -->
    <div class="errors-split">
    <div class="errors-left" :class="{ 'modal-mode': detailModalOpen }" @click.self="clickMask">
    <div class="due-queue">
      <div class="head">
        <h4>📌 今日到期 {{ dueTotal }} 道 · 按"再不做就忘"程度排序</h4>
        <span style="font-size:11.5px;color:var(--ink3);">FSRS 算法 · 自动排期</span>
      </div>
      <div v-if="dueLoading" style="padding:20px;text-align:center;color:var(--ink3);font-size:13px;">到期队列加载中…</div>
      <div v-else-if="dueError" style="padding:20px;text-align:center;color:var(--ink3);font-size:13px;">{{ dueError }}</div>
      <div v-else-if="!dueItems.length" style="padding:24px;text-align:center;font-size:13px;">
        <div style="font-size:15px;font-weight:700;margin-bottom:4px;">🎉 今日无到期错题</div>
        <div style="color:var(--ink3);">记忆还很稳，不用刷，去练新题吧！</div>
      </div>
      <div v-else class="items">
        <div v-for="d in dueItems" :key="d.record_id" class="item" @click="openDetail(d.record_id, d.seq)">
          <div class="num">{{ String(d.seq).padStart(2, '0') }}</div>
          <div class="body">
            <div class="q"><LatexText :text="d.question_preview" /></div>
            <div class="meta">
              <span>📚 {{ d.kp_name || d.kp_code || '未标注知识点' }}</span>
              <span>📅 入本 {{ fmtMD(d.created_at) }} · 已答错 {{ d.wrong_count }} 次</span>
            </div>
          </div>
          <div class="next">{{ forgetLabel(d) }}</div>
          <div class="arrow">›</div>
        </div>
      </div>
    </div>

    <div class="detail-modal-wrap">
    <!-- 视图切换 -->
    <div class="section-head">
      <h2>错题详情 · 第 {{ String(selectedSeq).padStart(2, '0') }} 题</h2>
      <button v-if="detail" class="del-err" style="margin-right:10px;color:var(--err,#dc2626);background:transparent;border:none;cursor:pointer;font-size:12px;padding:4px 6px;border-radius:8px;" title="删除这道错题（移出错题本，不再复习）" @click="onDelClick">
        {{ delConfirming ? '确认删除？再点一次' : '🗑 删除' }}</button>
      <button class="modal-close next" title="下一题" @click="openNextInList" :disabled="!hasNextInList">⏭ 下一题</button>
      <button class="modal-close" title="关闭详情" @click="detailModalOpen = false">✕ 关闭</button>
      <div class="view-tabs" style="margin:0;">
        <button
          v-for="t in tabs" :key="t"
          :class="{ active: activeTab === t }"
          @click="switchTab(t)"
        >{{ t }}</button>
      </div>
    </div>

    <!-- 错题卡（三段式 · Anki 风格） -->
    <div class="error-card">
      <div v-if="detailLoading" style="padding:32px;text-align:center;color:var(--ink3);font-size:13px;">错题详情加载中…</div>
      <div v-else-if="detailError" style="padding:32px;text-align:center;color:var(--ink3);font-size:13px;">{{ detailError }}</div>
      <div v-else-if="!detail" style="padding:32px;text-align:center;font-size:13px;">
        <div style="font-size:15px;font-weight:700;margin-bottom:4px;">📒 暂无错题，继续保持</div>
        <div style="color:var(--ink3);">做错的题会自动收录到这里，并附上 AI 错因分析。</div>
      </div>
      <template v-else>
        <div class="head">
          <div class="lbl">
            <span class="num">{{ String(selectedSeq).padStart(2, '0') }} / {{ totalErrors }}</span>
            <span class="topic">{{ kpZh(detail) }}</span>
            <span class="days">入本 {{ fmtMD(detail.entered_at) }} · 已答错 {{ detail.wrong_count }} 次 · 复习 {{ detail.review_count }} 次</span>
          </div>
        </div>
        <div class="body">
          <!-- 左：题目 + 正解 -->
          <div class="q-side">
            <h5>原题</h5>
            <MarkdownView class="q-text" :text="detail.question_text" />
            <div v-if="(detail.image && detail.image.length) || photoUrl" class="q-fig">
              <DynamicFigureViewer :items="figItems" :label="'题目配图'" :height="300" />
            </div>
            <template v-if="detail && figureEligible">
              <button class="secondary" style="margin-top:10px;display:block;" @click="genDynamicFigure" :disabled="figBusy">
                {{ figBusy ? '⏳ AI 生成动态图形中…' : (hasGgb ? '🔄 重新生成动态图形' : '🔍 生成动态图形（可拖动/旋转/缩放）') }}
              </button>
              <div v-if="figFailNote" style="font-size:11.5px;color:var(--warn-deep,#92400e);margin-top:6px;">⚠ {{ figFailNote }}</div>
            </template>
            <h5>正解
              <span v-if="detailFullLoading" class="text-muted text-xs" style="font-weight:400;">(正解加载中…)</span>
              <span v-else-if="detailFull?.cached" class="text-muted text-xs" style="font-weight:400;">(已缓存，秒开)</span>
            </h5>
            <!-- 正解示意图（后端持久化；与原题图独立渲染、独立错误态） -->
            <div v-if="solutionFigItems.length" style="margin:8px 0 4px;">
              <div style="font-size:12px;font-weight:700;color:var(--ink2, #4b5563);margin-bottom:4px;">📈 正解示意图</div>
              <DynamicFigureViewer :items="solutionFigItems" :label="'正解示意图'" :height="260" />
            </div>
            <div class="options" :style="reviewing ? { filter: 'blur(6px)', userSelect: 'none' } : {}">
              <div v-if="detailFullLoading" class="opt right" style="color:var(--ink3);">正解加载中…（首次打开由 AI 生成，之后直接读缓存）</div>
              <div v-else-if="detailFullError" class="opt right" style="color:var(--ink3);">正解暂不可用（{{ detailFullError }}），稍后重新打开本题可重试。</div>
              <template v-else>
                <MarkdownView class="opt right" :text="detailFull.generated_answer || '暂无正解文本'" style="display:block;" />
                <AiGeneratedMark :content-id="String(selectedRecordId || '')" />
              </template>
            </div>
            <div style="font-size:11.5px;color:var(--ink3);display:flex;gap:14px;padding-top:8px;border-top:1px dashed var(--line);">
              <span>📅 {{ detail.next_review_at ? '下次复习 ' + fmtMD(detail.next_review_at) : '已毕业，不再排期' }}</span>
              <span v-if="originZh">来源：{{ originZh }}</span>
            </div>
          </div>
          <!-- 右：错因对话化 + 巩固建议 -->
          <div class="a-side">
            <h5>AI 问诊 · 错因</h5>
            <div v-if="detail.note" class="ai-says" style="border:1px dashed var(--line);border-radius:8px;padding:8px 10px;margin-bottom:8px;">
              <div style="font-weight:700;color:var(--brand-deep);margin-bottom:4px;">我的错因自述</div>
              <div style="white-space:pre-line;line-height:1.7;">{{ detail.note }}</div>
            </div>
            <div v-if="diagLoading" class="ai-says" style="color:var(--ink3);">AI 正在分析这道题的错因…</div>
            <div v-else-if="diag" class="ai-says">
              <div style="font-weight:700;color:var(--brand-deep);margin-bottom:6px;">「{{ diag.subtype_zh || errorTypeZh(detail.error_type) }}」· AI 诊断</div>
              <div style="white-space:pre-line;line-height:1.7;">{{ diag.diagnosis }}</div>
            </div>
            <div v-else class="ai-says">
              这道题错因类型为<b>"{{ errorTypeZh(detail.error_type) }}"</b>。
              <template v-if="detail.note">{{ detail.note }}</template>
              <template v-else>建议对照左侧正解复盘当时的思路，把关键一步写在错题卡上。</template>
              <br/><br/>
              💡 <b>巩固建议</b>：<i>{{ detail.variants_hint }}</i>
            </div>

            <!-- AI 答疑：Khanmigo 苏格拉底引导式 chat（不直给答案） -->
            <h5 style="margin-top:14px;">💬 AI 答疑 · 举一反三</h5>
            <div class="ai-tutor">
              <div v-if="!tutorHistory.length && !tutorSending" class="tutor-tip">
                卡住了？把你的思路告诉 AI（<b>不会直接给答案</b>，会一步步引导你独立解答）。
              </div>
              <div v-for="(m, i) in tutorHistory" :key="i" class="tutor-msg" :class="m.role">
                <span class="tutor-role">{{ m.role === 'user' ? '我' : '老师' }}</span>
                <div class="tutor-bubble">{{ m.content }}</div>
              </div>
              <div v-if="tutorSending" class="tutor-msg assistant">
                <span class="tutor-role">老师</span>
                <div class="tutor-bubble tutor-loading">AI 正在引导你…</div>
              </div>
              <div class="tutor-input">
                <input
                  v-model="tutorInput"
                  type="text"
                  placeholder="输入你的思路或疑问…回车发送"
                  @keyup.enter="sendTutorMsg"
                  :disabled="tutorSending"
                />
                <button
                  class="btn btn-sm btn-primary"
                  :disabled="!tutorInput.trim() || tutorSending"
                  @click="sendTutorMsg"
                >发送</button>
              </div>
            </div>

            <div class="tags">
              <span class="tag-pill err">{{ errorTypeZh(detail.error_type) }}</span>
              <span class="tag-pill warn">已答错 {{ detail.wrong_count }} 次</span>
              <span class="tag-pill purple">{{ kpZh(detail) }}</span>
            </div>
            <!-- FSRS 稳定性区块（旧数据未回填时为 null，整块隐藏） -->
            <div v-if="detail.memory_stability != null" class="variants">
              <h6>🧠 记忆状态（FSRS）</h6>
              <ol>
                <li><b>遗忘风险</b> · S ≈ {{ fmtStability(detail.memory_stability) }} 天</li>
                <li><b>当前记得概率</b> · {{ pct(detail.retrievability) }}%</li>
                <li><b>记忆等级</b> · {{ levelZh(detail.fsrs_level) }}</li>
              </ol>
            </div>
            <div v-if="!reviewing" style="display:flex;gap:10px;">
              <button class="redo-btn" style="flex:1;" @click="redo">🔄 隐藏答案重做</button>
              <button class="redo-btn" style="flex:1;background:var(--brand-bg,#fff7e6);border-color:var(--brand,#f59e0b);" @click="redoWithSocratic">🎯 引导重解</button>
            </div>
            <div v-else style="display:flex;gap:10px;">
              <button class="redo-btn" style="flex:1;" :disabled="reviewSubmitting" @click="submitReview('remembered')">✅ 记住了</button>
              <button class="redo-btn" style="flex:1;background:var(--err-bg);border-color:var(--err-border);color:var(--err-deep,#b91c1c);" :disabled="reviewSubmitting" @click="submitReview('forgotten')">❌ 没记住</button>
            </div>
          </div>
        </div>
      </template>
    </div>
    </div>

    <!-- 错题列表（多维筛选） -->
    </div>
    <div class="errors-right">
    <div class="card" style="padding:0;overflow:hidden;">
      <div style="padding:14px 20px;border-bottom:1px solid var(--line);display:flex;align-items:center;justify-content:space-between;">
        <strong style="font-size:14px;">📚 全部 {{ listTotal }} 道 · 点击展开详情</strong>
        <span style="font-size:12px;color:var(--ink3);">按入本时间倒序</span>
      </div>
      <!-- 维度取值（选中"按错因/知识点/时间/遗忘风险"后出现） -->
      <div v-if="activeTab !== '全部'" style="padding:10px 20px;border-bottom:1px dashed var(--line);display:flex;gap:8px;flex-wrap:wrap;">
        <button
          v-for="opt in subOptions" :key="opt.value"
          class="tag-pill"
          :class="subFilter === opt.value ? 'warn' : ''"
          style="cursor:pointer;"
          @click="switchSub(opt.value)"
        >{{ opt.label }}</button>
        <span v-if="!subOptions.length" style="font-size:12px;color:var(--ink3);">暂无可选{{ activeTab.slice(1) }}，先入本一些错题</span>
      </div>
      <div v-if="listLoading" style="padding:20px;text-align:center;color:var(--ink3);font-size:13px;">错题列表加载中…</div>
      <div v-else-if="listError" style="padding:20px;text-align:center;color:var(--ink3);font-size:13px;">{{ listError }}</div>
      <div v-else-if="!listItems.length" style="padding:24px;text-align:center;font-size:13px;color:var(--ink3);">
        {{ hasFilter ? '该条件下没有错题' : '暂无错题，继续保持 🎉' }}
      </div>
      <div v-else style="padding:8px 0;">
        <div
          v-for="(o, i) in listItems" :key="o.record_id"
          style="padding:10px 20px;display:flex;align-items:center;gap:14px;border-bottom:1px dashed var(--line);cursor:pointer;"
          :style="o.fsrs_level === 'lv4' || o.fsrs_level === 'lv3' ? { color: 'var(--ink3)' } : {}"
          @click="openDetail(o.record_id, i + 1)"
        >
          <span class="tag-pill" :class="levelTagCls(o.fsrs_level)" style="flex-shrink:0;">{{ levelZh(o.fsrs_level) }}</span>
          <div class="list-thumb" title="题目图片（点击查看原图与详情）">🖼</div>
          <LatexText style="flex:1;font-size:13px;font-weight:600;" :style="o.fsrs_level === 'lv4' || o.fsrs_level === 'lv3' ? { fontWeight: 400 } : {}" :text="o.question_preview" />
          <span style="font-size:11.5px;color:var(--ink3);">{{ o.kp_name || o.kp_code || '未标注' }} · 复习 {{ o.review_count }} 次 · 记得 {{ pct(o.retrievability) }}%</span>
          <span style="color:var(--ink3);">›</span>
        </div>
      </div>
    </div>
  </div>
    </div><!-- /errors-right -->
    </div><!-- /errors-split -->
</template>

<script setup>
import { ref, computed, onMounted, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '@/api/client'
import { butlerApi, filesApi, studentApi } from '@/api'
import { openLightbox } from '@/utils/lightbox'
import { setPageContext } from '@/composables/usePageContext'
import { useAuthStore } from '@/stores/auth'
import { useToastStore } from '@/stores/toast'
import LatexText from '@/components/LatexText.vue'
import MarkdownView from '@/components/MarkdownView.vue'
import AiGeneratedMark from '@/components/chat/AiGeneratedMark.vue'
import HomeworkPhotos from '@/components/student/HomeworkPhotos.vue'
import FlashcardReview from '@/components/student/FlashcardReview.vue'
import DynamicFigureViewer from '@/components/DynamicFigureViewer.vue'

const router = useRouter()
const toast = useToastStore()
const auth = useAuthStore()
const nickname = computed(() => auth.nickname)

/* 热力图图例：色块 + 语义文字（色越深记忆越稳，红色为衰减临忘） */
const HEAT_LEGEND = [
  { label: '稳固', swatch: 'background:#d97706;' },
  { label: '良好', swatch: 'background:#f59e0b;' },
  { label: '模糊', swatch: 'background:#fbbf24;' },
  { label: '危险', swatch: 'background:#fde68a;' },
  { label: '遗忘', swatch: 'background:#fef2f2;border:1px solid #fecaca;' },
  { label: '刚收录', swatch: 'background:#e5e7eb;border:1px solid #d1d5db;' },
]

/* ===== 维度筛选 tabs（全部/错因/知识点/时间/遗忘风险 → GET /error-records/filter） ===== */
const tabs = ['全部', '按错因', '按知识点', '按时间', '按遗忘风险']
const activeTab = ref('全部')
const subFilter = ref('')

const ERROR_TYPES = [
  { value: 'concept', label: '概念不清' },
  { value: 'formula', label: '公式记错' },
  { value: 'calculation', label: '计算失误' },
  { value: 'logic', label: '思路错误' },
  { value: 'reading', label: '审题偏差' },
]
const STABILITY_OPTS = [
  { value: 'stable', label: '稳定区' },
  { value: 'decaying', label: '衰减中' },
  { value: 'critical', label: '临忘（衰减红）' },
]
const TIME_OPTS = [
  { value: '7', label: '近 7 天' },
  { value: '30', label: '近 30 天' },
  { value: '90', label: '近 90 天' },
]

/* ===== 状态 ===== */
const heatmap = ref([])
const heatLoading = ref(true)
const heatError = ref('')

const dueItems = ref([])
const dueTotal = ref(0)
const dueLoading = ref(true)
const dueError = ref('')

const listItems = ref([])
const listTotal = ref(0)
const listLoading = ref(true)
const listError = ref('')
const totalErrors = ref(0)
const kpOptions = ref([])
// S5 v3：统计三卡数据
const masteredCount = computed(() => {
  // 记得较牢 = 当前列表中记忆保持率 ≥90% 的题（诚实口径：只数当前可见数据）
  try {
    return (listItems.value || []).filter((r) => (r.retrievability ?? 0) >= 0.9).length
  } catch { return 0 }
})
// 动态演示门控：仅函数/立体几何/圆锥曲线类题显示按钮（其余题型没有可动画化的图形对象）
const figureEligible = computed(() => {
  const s = `${detail.value?.question_text || ''}${detail.value?.kp_code || ''}`
  return /函数|导数|椭圆|双曲线|抛物线|圆锥|几何|三角形|棱|圆|抛物|单调|极值|切线|轨迹/.test(s)
})
// B5（V2 文档）：题库父码→中文名（存量父码错题在 knowledge_points 无行，前端映射）
const KP_ZH = { analytic: '解析几何', derivative: '导数与单调性', function: '函数', trig: '三角函数',
  sequence: '数列', probability: '概率统计', geometry: '立体几何', exponential: '指数对数',
  set_logic: '集合逻辑', inequality: '不等式', complex: '复数', custom: '自定义' }
const kpZh = (d) => (d && (d.kp_name || KP_ZH[d.kp_code])) || '未标注知识点' 

const detailModalOpen = ref(false) // P1-13：详情浮窗开关（初始不弹出，列表为主）
const selectedRecordId = ref('') // 当前详情 record_id（浮窗内下一题导航）
const hpRef = ref(null) // 拍错题入本：HomeworkPhotos 实例（相册/拍照 pick）
const entryCard = ref(false) // P1-14：入本方式引导卡
const detail = ref(null)
// 手动拍照入本的原图 URL（file_id -> /files/{id}/content 预签名 URL）
const photoUrl = ref('')
// 动态图形（AI → GeoGebra 交互构造）：渲染源 = image 列（data-URI + ggb 对象）+ 原图照片
const figBusy = ref(false)
const figItems = computed(() => {
  const arr = Array.isArray(detail.value?.image) ? detail.value.image.slice() : []
  if (photoUrl.value) arr.push(photoUrl.value)
  return arr
})
const hasGgb = computed(() =>
  Array.isArray(detail.value?.image) && detail.value.image.some((e) => e && typeof e === 'object' && e.type === 'ggb')
)
const figFailNote = ref('')
async function genDynamicFigure() {
  if (!detail.value || figBusy.value) return
  figFailNote.value = ''
  figBusy.value = true
  try {
    const data = await api.post(`/student/error-records/${detail.value.record_id}/figure`)
    if (data?.ggb) {
      toast.success(data.generated ? '动态图形已生成，可拖动/旋转/缩放 🎉' : '已展示动态图形')
      await openDetail(detail.value.record_id, selectedSeq.value)
    } else {
      // S5 v3：生成不出时的降级说明（不静默、不用假图凑数）
      figFailNote.value = '这道题暂时生成不出可交互图形（AI 未能从题面构造出可靠的图形对象）——原题图与解析不受影响，可继续复习'
    }
  } catch (e) {
    figFailNote.value = `动态图形生成失败（${e?.message || 'AI 通道波动'}）——原题图与解析不受影响，稍后可再试`
  } finally {
    figBusy.value = false
  }
}
const detailLoading = ref(false)
const detailError = ref('')
const selectedSeq = ref(1)
const reviewing = ref(false)
const reviewSubmitting = ref(false)
// 迭代17：AI 错因诊断（根因/记忆口诀/补救建议；接口异常回退规则模板）
const diag = ref(null)
const diagLoading = ref(false)
// 迭代17：AI 详情（正解，取代"暂无正解文本"）+ AI 答疑苏格拉底 chat
// om8：正解后端持久化缓存——loading/error 独立状态，缓存命中不再显示"生成中"
const detailFull = ref(null)
const detailFullLoading = ref(false)
const detailFullError = ref('')
// 正解示意图（后端 solution_figure，规范 image/ggb 契约；空数组不渲染区块）
const solutionFigItems = computed(() => {
  const arr = Array.isArray(detailFull.value?.solution_figure) ? detailFull.value.solution_figure : []
  return arr.filter((e) => e && typeof e === 'object' && (e.type === 'image' || e.type === 'ggb'))
})
const tutorInput = ref('')
const tutorHistory = ref([])
const tutorSending = ref(false)

const heatAllEmpty = computed(() => heatmap.value.length > 0 && heatmap.value.every((c) => c.level === 'empty'))
const hasFilter = computed(() => activeTab.value !== '全部' && !!subFilter.value)

const subOptions = computed(() => {
  if (activeTab.value === '按错因') return ERROR_TYPES
  if (activeTab.value === '按知识点') return kpOptions.value
  if (activeTab.value === '按时间') return TIME_OPTS
  if (activeTab.value === '按遗忘风险') return STABILITY_OPTS
  return []
})

/* ===== 工具 ===== */
function fmtMD(iso) {
  if (!iso) return '--'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '--'
  return `${d.getMonth() + 1}/${d.getDate()}`
}
function pct(v) {
  return Math.round((Number(v) || 0) * 100)
}
function fmtStability(s) {
  const n = Number(s)
  return Number.isFinite(n) ? (n >= 10 ? Math.round(n) : n.toFixed(1)) : '--'
}
function errorTypeZh(t) {
  return ERROR_TYPES.find((e) => e.value === t)?.label || '未分类'
}
function levelZh(lv) {
  return { lv4: '非常稳', lv3: '稳定', lv2: '一般', lv1: '偏弱', decay: '快忘了', new: '刚收录' }[lv] || '待评估'
}
function levelTagCls(lv) {
  if (lv === 'lv4' || lv === 'lv3') return 'ok'
  if (lv === 'lv2' || lv === 'lv1') return 'warn'
  if (lv === 'decay') return 'err'
  if (lv === 'new') return 'brand'
  return 'brand'
}
function forgetLabel(d) {
  const h = Number(d.hours_to_forget)
  if (!Number.isFinite(h)) return `⚠ ${d.urgency_label || '待复习'}`
  if (h < 1) return '⚠ 1 小时内就会忘'
  if (h < 48) return `⚠ 还剩 ${Math.round(h)} 小时会忘`
  return `📍 约 ${Math.round(h / 24)} 天后会忘`
}

/* ===== 数据加载（各块独立 loading/error，互不影响） ===== */
async function loadHeatmap() {
  heatLoading.value = true
  heatError.value = ''
  try {
    const data = await api.get('/student/error-records/memory-heatmap')
    heatmap.value = data?.cells || []
  } catch (e) {
    heatError.value = `热力图加载失败：${e.message || '请稍后重试'}`
  } finally {
    heatLoading.value = false
  }
}

async function loadDue() {
  dueLoading.value = true
  dueError.value = ''
  try {
    const data = await api.get('/student/error-records/due-queue')
    dueItems.value = data?.items || []
    dueTotal.value = data?.total ?? dueItems.value.length
    if (dueItems.value.length && !detail.value) {
      openDetail(dueItems.value[0].record_id, dueItems.value[0].seq)
    }
  } catch (e) {
    dueError.value = `到期队列加载失败：${e.message || '请稍后重试'}`
  } finally {
    dueLoading.value = false
  }
}

function buildFilterQuery() {
  const q = { page: 1, size: 50 }
  if (!subFilter.value) return q
  if (activeTab.value === '按错因') q.error_type = subFilter.value
  else if (activeTab.value === '按知识点') q.kp_code = subFilter.value
  else if (activeTab.value === '按遗忘风险') q.stability = subFilter.value
  else if (activeTab.value === '按时间') {
    const d = new Date()
    d.setDate(d.getDate() - Number(subFilter.value))
    q.date_from = d.toISOString().slice(0, 10)
  }
  return q
}

async function loadFilter() {
  listLoading.value = true
  listError.value = ''
  try {
    const data = await api.get('/student/error-records/filter', buildFilterQuery())
    listItems.value = data?.items || []
    listTotal.value = data?.total ?? listItems.value.length
    // 无筛选时同步总错题数 + 知识点选项
    if (!hasFilter.value) {
      totalErrors.value = listTotal.value
      const map = new Map()
      listItems.value.forEach((it) => {
        if (it.kp_code && !map.has(it.kp_code)) map.set(it.kp_code, it.kp_name || it.kp_code)
      })
      kpOptions.value = [...map.entries()].map(([value, label]) => ({ value, label }))
      if (!detail.value && !dueItems.value.length && listItems.value.length) {
        openDetail(listItems.value[0].record_id, 1)
      }
    }
  } catch (e) {
    listError.value = `错题列表加载失败：${e.message || '请稍后重试'}`
  } finally {
    listLoading.value = false
  }
}

function closeDetailModal() { detailModalOpen.value = false }
function openEntryCard() { entryCard.value = true }
function pickPhoto() {
  entryCard.value = false
  // 表单在 v-if="manualOpen" 内：先展开再触发选图，否则 hpRef 未挂载导致 pick 静默失效
  manualOpen.value = true
  nextTick(() => hpRef.value?.pick())
}
function pickManual() { entryCard.value = false; manualOpen.value = true }
// P1-14：浮窗内下一题（按当前列表顺序）
const hasNextInList = computed(() => listItems.value.length > 1)
function openNextInList() {
  if (!hasNextInList.value) return
  const idx = listItems.value.findIndex((o) => o.record_id === detail.value?.id || o.record_id === selectedRecordId.value)
  const nxt = listItems.value[(idx + 1) % listItems.value.length]
  if (nxt) openDetail(nxt.record_id, ((idx + 1) % listItems.value.length) + 1)
}
function clickMask() { if (detailModalOpen.value) detailModalOpen.value = false }

async function openDetail(recordId, seq = 1) {
  if (!recordId) return
  detailModalOpen.value = true
  selectedRecordId.value = recordId
  selectedSeq.value = seq
  reviewing.value = false
  detailLoading.value = true
  detailError.value = ''
  diag.value = null
  detailFull.value = null
  tutorHistory.value = []
  photoUrl.value = ''
  diagLoading.value = true
  try {
    detail.value = await api.get(`/student/error-records/${recordId}/detail`)
  setPageContext({ route: '/errors', title: '错题本', detail: `第 ${String(selectedSeq.value).padStart(2, '0')} 题：${(detail.value?.question_text || detail.value?.text || '').slice(0, 120)}` })
  } catch (e) {
    detail.value = null
    detailError.value = `详情加载失败：${e.message || '请稍后重试'}`
  } finally {
    detailLoading.value = false
  }
  // 手动拍照入本的原图：file_id -> 预签名内容 URL（与 AssignmentView 照片解析一致）
  if (detail.value?.file_id) {
    try {
      const d = await filesApi.contentUrl(detail.value.file_id)
      if (d?.url) photoUrl.value = d.url
    } catch { /* 图片暂不可用：不阻塞详情 */ }
  }
  // AI 错因诊断 + AI 详情（正解）best-effort，并发拉取
  detailFullLoading.value = true
  detailFullError.value = ''
  Promise.allSettled([
    butlerApi.errorDiagnosis(recordId).then((d) => { diag.value = d }).catch((e) => { diag.value = null; console.warn('[ErrorsView] error-diagnosis 失败：', e?.message || e) }),
    butlerApi.errorDetail(recordId).then((d) => { detailFull.value = d }).catch((e) => { detailFull.value = null; detailFullError.value = e?.message || '请稍后重试'; console.warn('[ErrorsView] error-detail 失败：', e?.message || e) }),
  ]).finally(() => { diagLoading.value = false; detailFullLoading.value = false })
}

async function sendTutorMsg() {
  if (!tutorInput.value.trim() || tutorSending.value || !detail.value) return
  const msg = tutorInput.value.trim()
  tutorHistory.value = [...tutorHistory.value, { role: 'user', content: msg }]
  tutorInput.value = ''
  tutorSending.value = true
  try {
    const res = await butlerApi.errorTutor({
      recordId: detail.value.record_id,
      student_message: msg,
      history: tutorHistory.value.slice(0, -1),
    })
    tutorHistory.value = [...tutorHistory.value, { role: 'assistant', content: res.tutor_reply || '（AI 没回复）' }]
  } catch (e) {
    tutorHistory.value = [...tutorHistory.value, { role: 'assistant', content: `（暂时没回复：${e.message || '稍后重试'}）` }]
  } finally {
    tutorSending.value = false
  }
}

/* ===== 交互 ===== */
/* 格子 hover 弹层文案：等级语义 + 该格题数（空格提示无到期错题） */
function heatCellTip(c) {
  if (!c || !c.count) return '无到期错题'
  const label = { lv4: '稳固', lv3: '良好', lv2: '模糊', lv1: '危险', decay: '遗忘', new: '刚收录' }[c.level]
  return label ? `${label} · ${c.count} 题` : `${c.count} 题`
}
function heatTip(c) {
  if (!c || !c.count) return // 空格无到期错题，hover 弹层已提示
  openDetail(c.record_ids[0], selectedSeq.value)
}
function switchTab(t) {
  activeTab.value = t
  subFilter.value = ''
  loadFilter()
}
function switchSub(v) {
  subFilter.value = subFilter.value === v ? '' : v
  loadFilter()
}
function redo() {
  reviewing.value = true
  toast.info('已隐藏答案，请独立重做后选择结果 ⏱')
}
// S6 错题删除：两段式确认（防误触，兼容自动化）→ 软删端点 → 清详情 + 刷新到期/筛选两列表
const delConfirming = ref(false)
let _delTimer = null
function onDelClick() {
  if (!detail.value?.record_id) return
  if (!delConfirming.value) {
    delConfirming.value = true
    clearTimeout(_delTimer)
    _delTimer = setTimeout(() => { delConfirming.value = false }, 4000)
    return
  }
  delConfirming.value = false
  removeError()
}
async function removeError() {
  if (!detail.value?.record_id) return
  try {
    await studentApi.deleteErrorRecord(detail.value.record_id)
    toast.info('已删除错题')
    detail.value = null
    loadDue()
    loadFilter()
  } catch (e) {
    toast.info('删除失败：' + (e?.message || '请稍后再试'))
  }
}
// om5 修复轮 D2：错题 → 引导重解（深链 /dialog?explain=，DialogView 已有该入口处理）
function redoWithSocratic() {
  const q = new URLSearchParams()
  q.set('explain', detail.value.question_text || '')
  if (detail.value.answer_text) q.set('answer', detail.value.answer_text)
  if (detail.value.error_type) q.set('error_type', detail.value.error_type)
  if (detail.value.kp_code) q.set('kp', detail.value.kp_code)
  if (detail.value.file_id) q.set('file_id', detail.value.file_id)
  router.push('/dialog?' + q.toString())
}
const _ORIGIN_ZH = {
  self_test: '自测练题', chat_quiz: '对话出题', socratic: '引导解题',
  mock_exam: '模拟考试', variant: '变式巩固', retry: '错题重练',
  assignment: '老师作业', manual: '手动录入',
}
const originZh = computed(() => {
  const o = detail.value.origin || (detail.value.source_channel === 'manual_photo' ? 'manual' : '')
  return _ORIGIN_ZH[o] || (detail.value.source_channel === 'auto_judge' ? '练题判分' : detail.value.source_channel)
})
async function submitReview(result) {
  if (!detail.value || reviewSubmitting.value) return
  reviewSubmitting.value = true
  try {
    const data = await api.post(`/student/error-records/${detail.value.record_id}/review`, { result })
    if (data?.graduated) {
      toast.success('🎓 这道错题已毕业，不再排期！')
    } else if (result === 'forgotten') {
      toast.info('已重置复习进度，明天再来一次 💪')
    } else {
      toast.success(`复习完成，下次复习 ${fmtMD(data?.next_review_at)}`)
    }
    reviewing.value = false
    // 复习改变了 FSRS 状态，刷新热力图 / 到期队列 / 详情
    loadHeatmap()
    loadDue()
    loadFilter()
    openDetail(detail.value.record_id, selectedSeq.value)
  } catch (e) {
    toast.error(`复习提交失败：${e.message || '请稍后重试'}`)
  } finally {
    reviewSubmitting.value = false
  }
}
function goPractice() {
  router.push('/practice')
}


/* ===== 错题闪卡复习（OpenTutor 同款沉浸复习） ===== */
const flashcardMode = ref(false)
function onFcClose() {
  flashcardMode.value = false
  loadHeatmap()
  loadDue()
  loadFilter()
}

/* ===== 拍错题入本（Vision03）===== */
const manualOpen = ref(false)
const manualSubmitting = ref(false)
const manual = ref({ question: '', note: '', errorType: '', kpCode: '', photos: [] })

// S5 识别确认卡：OCR 状态显式反馈（不再"只说文件上传"）
const ocrNote = computed(() => {
  const p = manual.value.photos[0]
  if (!p) return null
  if (p.status === 'parsing') return { ok: true, msg: '🔍 正在识别题干…（识别完成后自动填入，可修改）' }
  if (p.status === 'ready' && p.ocr_text) return { ok: true, msg: `✓ 已识别题干 ${p.ocr_text.length} 字，已填入上方文本框——请确认无误或手动修正` }
  if (p.status === 'ready' && !p.ocr_text) return { ok: false, msg: '⚠ 这张照片没识别出文字：请离近一点、光线充足再拍，或在上方手动粘贴题目' }
  return null
})

// S5 AI 知识点预识别：确定性词表（POST /api/v1/kp/guess），零幻觉；识别错可人工改
const kpGuessing = ref(false)
async function guessKp(text, force = false) {
  const t = (text || '').trim()
  if (!t || kpGuessing.value) return
  if (!force && manual.value.kpCode) return  // 已有标注不覆盖
  kpGuessing.value = true
  try {
    const r = await api.post('/v1/kp/guess', { text: t.slice(0, 500) })
    if (r?.matched && r.code) {
      if (!kpOptions.value.some((k) => k.value === r.code)) kpOptions.value = [{ value: r.code, label: `${r.name}（AI 识别）` }, ...kpOptions.value]
      if (force || !manual.value.kpCode) manual.value.kpCode = r.code
      toast.info(`AI 识别知识点：${r.name}（可修改）`)
    } else if (force) {
      toast.info('没认出知识点，请手动选择')
    }
  } catch { /* 识别不可达：学生仍可手动选（诚实降级，不阻塞录入） */ }
  finally { kpGuessing.value = false }
}

function onManualOcr(text) {
  if (!text) return
  if (!manual.value.question.trim()) manual.value.question = text
  guessKp(text)  // OCR 完成 → 自动识别知识点（已有标注不覆盖）
  ensureEnhanced()  // S5 后半：拍照清晰化预览（原图/增强双轨，不覆盖原图）
}

// S5 后半：图像增强（B4 算法，:8000 files 域代理 → FileAsset 持久化，幂等）
const enhancing = ref(false)
const enhancedUrl = ref('')
async function ensureEnhanced() {
  const p = manual.value.photos[0]
  if (!p?.file_id || enhancing.value) return
  enhancing.value = true
  try {
    const r = await api.post(`/files/${p.file_id}/enhance`)
    if (r?.asset_id) {
      const u = await api.get(`/files/${p.file_id}/assets/${r.asset_id}/url`)
      enhancedUrl.value = u?.url || ''
    }
  } catch { /* 增强不可达：只少预览，不阻塞录入 */ } finally { enhancing.value = false }
}

async function submitManual() {
  const q = manual.value.question.trim()
  if (!q && !manual.value.photos.length) { toast.error('请先粘贴题干或拍照'); return }
  if (manualSubmitting.value) return
  manualSubmitting.value = true
  try {
    const fileId = manual.value.photos[0]?.file_id || null
    const kpLabel = kpOptions.value.find((k) => k.value === manual.value.kpCode)?.label || ''
    const created = await studentApi.createErrorRecord({
      question_text: q || '（拍照错题）',
      error_type: manual.value.errorType || null,
      kp_code: manual.value.kpCode || null,
      kp_name: kpLabel.replace(/（AI 识别）$/, '') || null,
      file_id: fileId,
      source_channel: 'manual_photo',
      note: manual.value.note.trim() || null,
    })
    const nr = created?.next_review_at
    const nrMsg = nr ? `，首次复习 ${fmtMD(nr)}` : ''
    toast.success(`已收录${nrMsg}，自动排入复习队列`)
    manual.value = { question: '', note: '', errorType: '', kpCode: '', photos: [] }
    manualOpen.value = false
    loadHeatmap(); loadDue(); loadFilter()
  } catch (e) { toast.error(`入本失败：${e?.message || '请稍后重试'}`) }
  finally { manualSubmitting.value = false }
}
onMounted(() => {
  loadHeatmap()
  loadDue()
  loadFilter()
})
</script>

<style scoped>
/* 图例：色块 + 文字成组排列 */
.legend-item { display: inline-flex; align-items: center; gap: 5px; font-weight: 600; }
/* 格子 hover 弹层：显示该格题数（不拦截点击，点格子仍打开详情） */
.heatmap-cell { position: relative; }
/* ===== P1-14 入本方式引导卡 ===== */
.entry-card { position: fixed; inset: 0; z-index: 120; display: flex; align-items: center; justify-content: center; background: rgba(15,23,42,.45); }
.entry-card-box { width: min(420px, 92vw); background: var(--bg-white); border-radius: 16px; padding: 22px 24px; box-shadow: 0 18px 60px rgba(0,0,0,.28); text-align: center; }
.entry-title { font-size: 15px; font-weight: 700; margin-bottom: 16px; color: var(--ink); }
.entry-btn { display: block; width: 100%; margin-bottom: 10px; padding: 12px 14px; border-radius: 10px; border: none; background: var(--brand); color: #fff; font-size: 14px; cursor: pointer; }
.entry-btn.plain { background: var(--brand-soft); color: var(--brand-deep); }
.entry-btn:hover { filter: brightness(1.05); }
.entry-close { margin-top: 4px; background: none; border: none; color: var(--ink3); cursor: pointer; font-size: 12.5px; }
/* ===== P1-13 浮窗模式：点击列表项后弹出，不再平铺割裂 ===== */
.errors-left.modal-mode {
  position: fixed; inset: 0; z-index: 90;
  display: flex; align-items: flex-start; justify-content: center;
  background: rgba(15, 23, 42, .5); padding: 26px 16px;
}
.errors-left.modal-mode .detail-modal-wrap {
  width: min(1080px, 96vw); max-height: 86vh; overflow-y: auto;
  background: var(--bg-white); border-radius: 18px;
  padding: 18px 22px 22px; box-shadow: 0 18px 60px rgba(0, 0, 0, .28);
}
.errors-left.modal-mode .due-queue { display: none; }
.errors-left:not(.modal-mode) .detail-modal-wrap { display: none; }
.modal-close {
  margin-right: 4px; border: 1px solid var(--line); background: var(--bg2);
  color: var(--ink2); border-radius: 8px; padding: 4px 10px; cursor: pointer; font-size: 12px;
}
.modal-close:hover { border-color: var(--brand); color: var(--brand); }
.list-thumb {
  width: 46px; height: 58px; flex-shrink: 0; border-radius: 8px;
  background: var(--brand-soft); border: 1px solid var(--line);
  display: inline-flex; align-items: center; justify-content: center;
  font-size: 16px;
}
.q-fig { margin: 10px 0; text-align: center; }
.q-fig img { max-width: 100%; max-height: 260px; border: 1px solid var(--line); border-radius: 8px; background: #fff; }
.heatmap-cell .cell-tip {
  display: none;
  position: absolute;
  bottom: calc(100% + 6px);
  left: 50%;
  transform: translateX(-50%);
  padding: 3px 8px;
  background: var(--ink, #111827);
  color: #fff;
  font-size: 10.5px;
  line-height: 1.4;
  border-radius: 6px;
  white-space: nowrap;
  pointer-events: none;
  z-index: 5;
}
.heatmap-cell:hover .cell-tip { display: block; }

/* ===== AI 答疑（Khanmigo 苏格拉底 chat） ===== */
.ai-tutor { display: flex; flex-direction: column; gap: 8px; margin-top: 6px; }
.tutor-tip { color: var(--ink3); font-size: 12.5px; line-height: 1.55; padding: 6px 0; }
.tutor-msg { display: flex; flex-direction: column; gap: 3px; }
.tutor-msg.user { align-items: flex-end; }
.tutor-msg.assistant { align-items: flex-start; }
.tutor-role { font-size: 10.5px; color: var(--ink3); font-weight: 600; }
.tutor-bubble {
  max-width: 100%; padding: 8px 12px; font-size: 12.5px; line-height: 1.6;
  border-radius: 10px; word-break: break-word;
  background: var(--bg2); color: var(--ink); border: 1px solid var(--line);
}
.tutor-msg.user .tutor-bubble { background: var(--brand-soft); border-color: var(--warn-border); color: var(--ink); }
.tutor-msg.assistant .tutor-bubble { background: var(--ok-bg); border-color: var(--ok-border); }
.tutor-loading { color: var(--ink3); font-style: italic; }
.tutor-input { display: flex; gap: 6px; margin-top: 4px; }
.tutor-input input {
  flex: 1; padding: 7px 10px; font-size: 12.5px;
  border: 1px solid var(--line); border-radius: 8px;
  background: var(--card); color: var(--ink);
}
.tutor-input input:focus { outline: none; border-color: var(--brand); }
.tutor-input input:disabled { opacity: 0.5; }

/* S5 v2：左右分栏（列表左/详情右 sticky），热力图已删除 */
.errors-split { display: block; }
@media (max-width: 1100px) { .errors-split { grid-template-columns: 1fr; } }
.errors-right { margin-top: 14px; }

/* S5 v3：统计三卡 + 主操作行 */
.err-stats { display: flex; gap: 12px; align-items: stretch; margin-bottom: 14px; flex-wrap: wrap; }
.es-card {
  min-width: 118px; padding: 12px 18px; background: var(--card, #fff);
  border: 1px solid var(--line, #e2e8f0); border-radius: 14px; text-align: center;
}
.es-card.warn { border-color: var(--warn-border, #fde68a); background: #fffbeb; }
.es-card.ok { border-color: var(--ok-border, #a7f3d0); background: #ecfdf5; }
.es-num { font-size: 24px; font-weight: 900; font-family: var(--font-num, Inter); color: var(--ink, #0f172a); }
.es-card.warn .es-num { color: var(--warn-deep, #92400e); }
.es-card.ok .es-num { color: var(--ok-deep, #047857); }
.es-lbl { font-size: 11.5px; color: var(--ink3, #94a3b8); font-weight: 700; margin-top: 2px; }
.es-ops { margin-left: auto; display: flex; flex-direction: column; gap: 8px; justify-content: center; }
.es-primary {
  padding: 10px 22px; border-radius: 12px; border: none; cursor: pointer;
  background: var(--gradient-brand, linear-gradient(135deg, #4f46e5, #06b6d4)); color: #fff;
  font: inherit; font-size: 13.5px; font-weight: 800;
}
.es-ghost {
  padding: 9px 18px; border-radius: 12px; border: 1px solid var(--line, #e2e8f0);
  background: var(--card, #fff); font: inherit; font-size: 12.5px; font-weight: 700; color: var(--ink2, #475569); cursor: pointer;
}
.es-ghost:hover { border-color: var(--primary-border, #c7d2fe); color: var(--primary, #4f46e5); }
</style>
