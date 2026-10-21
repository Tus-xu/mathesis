<template>
  <!-- ================= 课件工坊·备小研风格（WorkshopFlow：首页/照片/教案映射/大纲确认/模板/生成） ================= -->
  <WorkshopFlow v-if="view !== 'editor'" :ws="ws" />

  <!--
    VNext 备课课件：唯一工作面是 **PPT 内核真实渲染** 的逐页预览（2026-09-14）。
    必须放在 legacy 五区编辑器之前 —— vnext 分支不再复用旧编辑器画布
    （旧画布会把 spec 文本摊成两个文本框，正是"简单很丑"的来源）。
  -->
  <KernelDeckPreview
    v-else-if="vnextMode"
    :deck-id="vnextArtifact?.artifact_id ?? null"
    :revision="vnextRevisionHint"
    :spec="vnextBody"
    :title="vnextArtifact?.title ?? ''"
    @back="onVnextBack"
  />

  <!-- ================= 五区编辑器 ================= -->
  <div v-else-if="view === 'editor' && deck" class="tv3-editor" data-testid="tv3-editor">
    <!-- 区① 顶栏 -->
    <div class="tv3-editor__topbar">
      <button class="tv3-btn tv3-btn--sm" data-testid="tv3-editor-back" @click="view = 'list'">← 课件库</button>
      <input v-model="deck.title" class="tv3-editor__title-input" :readonly="vnextMode" data-testid="tv3-deck-title">
      <span class="tv3-tag" :class="deck.source === 'photo' ? 'tv3-tag--gold' : 'tv3-tag--primary'">{{ sourceLabel(deck.source) }}</span>
      <span class="tv3-tag">{{ templateName(deck.template_id) }}</span>
      <span v-if="deck.photo_context" class="tv3-tag tv3-tag--gold" title="原图已锚定">⚓ 原图 ×{{ deck.photo_context.photos }}</span>
      <span v-if="deck.brief_context?.course_type" class="tv3-tag" data-testid="tv3-deck-coursetype">{{ deck.brief_context.course_type }}</span>
      <span v-if="deck.brief_context?.chapter" class="tv3-tag" :title="deck.brief_context.chapter">📖 {{ chapterShort(deck.brief_context.chapter) }}</span>
      <div class="tv3-card__spacer" />
      <button class="tv3-btn tv3-btn--sm tv3-btn--gold" data-testid="tv3-open-drawboard" @click="openDrawBoard()">📐 绘图</button>
      <button class="tv3-btn tv3-btn--sm tv3-btn--ghost-ai" data-testid="tv3-ai-element" @click="runAiElement">✦ AI 优化本页</button>
      <button class="tv3-btn tv3-btn--sm tv3-btn--gold" data-testid="tv3-photo-insert" @click="photoOpen = true">📷 拍照插入</button>
      <button class="tv3-btn tv3-btn--sm" @click="saveDeck" data-testid="tv3-save">保存</button>
      <button class="tv3-btn tv3-btn--sm" :disabled="vnextMode" @click="addSlide">＋ 页</button>
      <button class="tv3-btn tv3-btn--sm tv3-btn--primary" data-testid="tv3-present" @click="presenting = true">▶ 预演</button>
      <button class="tv3-btn tv3-btn--sm" data-testid="tv3-add-bank-q" @click="openBankPick">＋ 题库题目</button>
      <button class="tv3-btn tv3-btn--sm" data-testid="tv3-deck-check-open" @click="openDeckCheck">🩺 体检</button>
      <button class="tv3-btn tv3-btn--sm" :disabled="exporting" @click="exportDeck">{{ exporting ? '导出中…' : '导出' }}</button>
    </div>

    <div class="tv3-editor__main">
      <!-- 区② 大纲 -->
      <aside class="tv3-editor__outline" :style="{ width: outlineW + 'px' }">
        <div class="tv3-resizer" title="拖拽调整宽度" @mousedown="startResize($event, 'outline')" />
        <div
          v-for="(s, i) in deck.slides" :key="s.id"
          class="tv3-outline__slide" :class="{ 'is-active': i === slideIdx }"
          data-testid="tv3-outline-slide"
          @click="slideIdx = i; selectedId = ''"
        >
          <SlideCanvasV3 :slide="s" :width="146" thumb />
          <span class="tv3-outline__no">{{ i + 1 }}</span>
          <div v-if="s.anchor_bar" class="tv3-outline__anchor" :title="s.anchor_bar">⚓ 续页</div>
          <div class="tv3-outline__fillbar" :style="fillBarStyle(s)" />
          <button v-if="!vnextMode && deck.slides.length > 1" class="tv3-outline__del" title="删除本页" @click.stop="deleteSlide(i)">×</button>
        </div>
        <button class="tv3-btn tv3-btn--sm" style="justify-content: center" :disabled="vnextMode" @click="addSlide">＋ 新页</button>
      </aside>

      <!-- 区③ 画布 -->
      <div class="tv3-editor__canvas-wrap">
        <SlideCanvasV3
          :slide="currentSlide" :width="860" editable
          :selected-id="selectedId"
          testid="tv3-main-canvas"
          @select-element="selectedId = $event"
          @delete-element="deleteElementById"
          @move-element="onElementMoved"
          @drop-latex="onDropLatex"
        />
        <div class="tv3-editor__hint">点击元素选中 · 拖动移动 · 右上角 × 或 Delete 删除 · 图形/函数/立体图走绘图工作台</div>
      </div>

      <!-- 区④ 属性面板 -->
      <aside class="tv3-editor__props" :style="{ width: propsW + 'px' }">
        <div class="tv3-resizer" title="拖拽调整宽度" @mousedown="startResize($event, 'props')" />
        <div v-if="!selectedEl" class="tv3-props__empty">
          <div style="font-size: 30px; margin-bottom: 8px">🎛</div>
          <div>选中画布中的元素后</div>
          <div>在此编辑属性</div>
          <div style="margin-top: 12px; font-size: 11.5px; color: var(--tv3-ink3)">数学元素（公式/图形）编辑后即时重渲染，保持结构化存储</div>
        </div>

        <template v-else>
          <div class="tv3-props__head">
            <span class="tv3-tag" :class="isMathEl(selectedEl) ? 'tv3-tag--gold' : ''">{{ typeLabel(selectedEl.type) }}</span>
            <span v-if="selectedEl.teacher_confirmed" class="tv3-tag tv3-tag--ok">已确认</span>
            <div class="tv3-card__spacer" />
            <button class="tv3-btn tv3-btn--sm" @click="confirmElement">✓ 确认</button>
          </div>

          <!-- text 属性 -->
          <template v-if="selectedEl.type === 'text'">
            <div class="tv3-form-label">内容（支持 $..$ 内联公式）</div>
            <textarea v-model="(selectedEl as any).html" class="tv3-textarea" rows="4" style="font-size: 12.5px" />
            <div class="mx-slider"><span class="mx-slider__label">字号</span><input type="range" min="14" max="48" step="1" :value="(selectedEl as any).font_size" @input="(selectedEl as any).font_size = Number(($event.target as HTMLInputElement).value)"><span class="mx-slider__value">{{ (selectedEl as any).font_size }}pt</span></div>
            <div class="mx-slider"><span class="mx-slider__label">颜色</span><input type="color" :value="(selectedEl as any).color || '#16233b'" style="width: 44px; height: 26px; border: none; background: none" @input="(selectedEl as any).color = ($event.target as HTMLInputElement).value"><label style="font-size: 11.5px; display: inline-flex; gap: 4px; align-items: center; margin-left: 8px"><input type="checkbox" :checked="!!(selectedEl as any).bold" style="accent-color: var(--tv3-gold)" @change="(selectedEl as any).bold = ($event.target as HTMLInputElement).checked">加粗</label></div>
          </template>

          <!-- formula 属性：MathLive 编辑 -->
          <template v-else-if="selectedEl.type === 'formula'">
            <div class="tv3-form-label">公式（MathLive 可视化编辑 · 键盘点按或拖入结构）</div>
            <MathField ref="propsMathField" v-model="(selectedEl as any).latex" :font-size="22" testid="tv3-props-mathfield" @focus="mathFieldHot = true" @blur="mathFieldHot = false" />
            <div class="mx-slider" style="margin-top: 10px"><span class="mx-slider__label">字号</span><input type="range" min="14" max="40" step="1" :value="(selectedEl as any).font_size" @input="(selectedEl as any).font_size = Number(($event.target as HTMLInputElement).value)"><span class="mx-slider__value">{{ (selectedEl as any).font_size }}pt</span></div>
            <label style="display: inline-flex; gap: 6px; align-items: center; font-size: 12px; cursor: pointer"><input type="checkbox" :checked="!!(selectedEl as any).display" style="accent-color: var(--tv3-gold)" @change="(selectedEl as any).display = ($event.target as HTMLInputElement).checked">独立行（display）</label>
          </template>

          <!-- geometry 属性：参数滑杆 -->
          <template v-else-if="selectedEl.type === 'geometry'">
            <div class="tv3-form-label">图形参数（拖滑杆实时联动）</div>
            <div v-for="sp in geoParams(selectedEl)" :key="sp.key" class="mx-slider">
              <span class="mx-slider__label" :title="sp.label">{{ sp.label }}</span>
              <input type="range" :min="sp.min" :max="sp.max" :step="sp.step" :value="(selectedEl.params[sp.key] ?? sp.def)" @input="setGeoParam(selectedEl, sp.key, Number(($event.target as HTMLInputElement).value))">
              <span class="mx-slider__value">{{ selectedEl.params[sp.key] ?? sp.def }}</span>
            </div>
            <div v-for="tg in geoToggles(selectedEl)" :key="tg.key" class="mx-geo__toggle" style="font-size: 12px">
              <input type="checkbox" :checked="(selectedEl.toggles?.[tg.key] ?? tg.def)" style="accent-color: var(--tv3-gold)" @change="setGeoToggle(selectedEl, tg.key, ($event.target as HTMLInputElement).checked)">{{ tg.label }}
            </div>
            <div v-if="selectedEl.recipe_id" class="tv3-tag tv3-tag--gold" style="margin-top: 8px">⚙ 来自构造配方 {{ selectedEl.recipe_id }}</div>
          </template>

          <!-- image 属性：绘图配方可重开 -->
          <template v-else-if="selectedEl.type === 'image'">
            <template v-if="(selectedEl as any).draw_recipe">
              <div class="tv3-form-label">绘图配方（结构化可重开）</div>
              <div style="font-size: 12px; color: var(--tv3-ink3); margin-bottom: 10px; line-height: 1.7">
                含 {{ (selectedEl as any).draw_recipe.records.length }} 条结构化记录（画笔/直线/圆/规整图形）。<br>重开工作台可继续修改，修改后回写本元素。
              </div>
              <button class="tv3-btn tv3-btn--sm tv3-btn--gold" data-testid="tv3-reopen-draw" @click="reopenDraw(selectedEl as any)">↺ 重开绘图编辑</button>
            </template>
            <div v-else style="font-size: 12.5px; color: var(--tv3-ink3)">普通图片元素，无结构化配方。</div>
          </template>

          <!-- anchorPhoto 属性：升级链路 -->
          <template v-else-if="selectedEl.type === 'anchorPhoto'">
            <div class="tv3-form-label">原图升级（P1）</div>
            <div style="font-size: 12px; color: var(--tv3-ink3); margin-bottom: 8px">原图永久保留；升级为结构化图形后仍可对照核验。</div>
            <button class="tv3-btn tv3-btn--sm" data-testid="tv3-rebuild-btn" @click="loadRebuildCandidates">查看重建候选</button>
            <div v-for="c in rebuildCandidates" :key="c.id" class="tv3-row" style="margin-top: 6px; cursor: pointer" @click="applyRebuild(selectedEl, c)">
              <span class="tv3-tag" :class="c.passed_validation ? 'tv3-tag--ok' : 'tv3-tag--danger'">{{ c.passed_validation ? '通过校验' : '未通过' }}</span>
              <div style="flex: 1; min-width: 0">
                <div style="font-size: 12.5px; font-weight: 600">{{ presetNameOf(c.preset_id) }}</div>
                <div style="font-size: 11px; color: var(--tv3-ink3)">{{ c.match_note }}</div>
              </div>
            </div>
          </template>

          <template v-else>
            <div style="font-size: 12.5px; color: var(--tv3-ink3)">该元素类型无额外属性。</div>
          </template>

          <!-- 通用几何属性 -->
          <div class="tv3-props__geom">
            <div class="tv3-form-label">位置与尺寸（1280×720 逻辑坐标）</div>
            <div class="tv3-props__grid">
              <label>X<input type="number" class="tv3-input" :value="selectedEl.left" @change="selectedEl.left = Number(($event.target as HTMLInputElement).value)"></label>
              <label>Y<input type="number" class="tv3-input" :value="selectedEl.top" @change="selectedEl.top = Number(($event.target as HTMLInputElement).value)"></label>
              <label>W<input type="number" class="tv3-input" :value="selectedEl.width" @change="selectedEl.width = Number(($event.target as HTMLInputElement).value)"></label>
              <label>H<input type="number" class="tv3-input" :value="selectedEl.height" @change="selectedEl.height = Number(($event.target as HTMLInputElement).value)"></label>
            </div>
          </div>
          <button class="tv3-btn tv3-btn--sm" style="margin-top: 10px; color: var(--tv3-rose); border-color: var(--tv3-rose-border)" @click="deleteElement">删除元素</button>
        </template>

        <!-- AI diff 审定（R5：不覆写，逐条采纳） -->
        <div v-if="aiDiffs.length" class="tv3-props__aidiff">
          <div class="tv3-ai-badge" style="margin-bottom: 8px">AI 建议待审定</div>
          <div v-for="(d, i) in aiDiffs" :key="i" class="tv3-aidiff__item">
            <div style="font-size: 12px">{{ d.reason }}</div>
            <div class="tv3-aidiff__actions">
              <button class="tv3-btn tv3-btn--sm tv3-btn--primary" @click="adoptDiff(i)">采纳</button>
              <button class="tv3-btn tv3-btn--sm" @click="aiDiffs.splice(i, 1)">忽略</button>
            </div>
          </div>
        </div>
      </aside>
    </div>

    <!-- 区⑤ 元素坞已按「工具球收纳」设计移除（课件编辑器·工具球收纳.html）：
         绘图工作台 = 顶栏「📐 绘图」+ 右下角数学绘图悬浮球；公式键盘 = 选中公式时左下角自动唤出的浮层 -->

    <!-- 公式键盘浮层：选中公式元素自动唤出（可手动关闭；同一元素不再重复弹） -->
    <div v-if="kbdOpen && selectedEl?.type === 'formula'" class="ws-kbdfloat" data-testid="tv3-kbd-float">
      <div class="ws-kbdfloat__head">
        <b>⌨ 公式键盘</b>
        <span>点击插入当前公式 · 编辑公式时自动唤出</span>
        <button title="收起（选中其他公式会再次弹出）" data-testid="tv3-kbd-float-close" @click="closeKbd">×</button>
      </div>
      <MathKeyboard @insert="onKbdInsert" />
    </div>

    <!-- 预演覆盖层 -->
    <div v-if="presenting" class="tv3-present" data-testid="tv3-present-overlay">
      <div class="tv3-present__bar">
        <span style="font-weight: 700">{{ deck.title }}</span>
        <span class="tv3-tag tv3-tag--gold">放映态：图形可拖点 · 函数图参数实时联动</span>
        <div class="tv3-card__spacer" />
        <button class="tv3-btn tv3-btn--sm" @click="presentIdx = Math.max(0, presentIdx - 1)">←</button>
        <span style="font-family: var(--tv3-font-num); font-size: 13px">{{ presentIdx + 1 }} / {{ deck.slides.length }}</span>
        <button class="tv3-btn tv3-btn--sm" @click="presentIdx = Math.min(deck.slides.length - 1, presentIdx + 1)">→</button>
        <button class="tv3-btn tv3-btn--sm tv3-btn--primary" data-testid="tv3-present-exit" @click="presenting = false">退出预演</button>
      </div>
      <div class="tv3-present__stage">
        <SlideCanvasV3 :slide="deck.slides[presentIdx]" :width="1120" presenting />
      </div>
    </div>

    <!-- 绘图工作台（函数绘图 / 自由画布 / 手写公式） -->
    <DrawBoard v-model:open="drawOpen" :reopen="drawReopen" @insert="onDrawInsert" />

    <!-- 拍照插入（P2）：扫描增强 + 图片素材/公式识别/手写原样三选一 -->
    <PhotoInsertPanel v-model:open="photoOpen" @insert="onPhotoInsert" />

    <div v-if="vnextMode" class="tv3-vnext-readonly" data-testid="tv3-vnext-readonly" role="status">
      <div class="tv3-vnext-readonly__panel">
        <strong>{{ copyFor('prep.vnext.deck.readonly.title') }}</strong>
        <span>{{ copyFor('prep.vnext.deck.readonly.body') }}</span>
      </div>
    </div>
  </div>

  <!-- B3 可讲性体检抽屉（确定性检查，无总分、不冒充 AI 评分） -->
  <div v-if="checkOpen" class="tv3-push" @click.self="checkOpen = false">
    <div class="tv3-card tv3-push__panel" style="max-width: 680px; max-height: 84vh; overflow-y: auto" data-testid="tv3-deck-check">
      <div class="tv3-card__head">
        <span class="tv3-card__title">可讲性体检</span>
        <span class="tv3-tag" style="font-size: 10px">确定性检查 · 不打总分</span>
        <div class="tv3-card__spacer" />
        <button class="tv3-btn tv3-btn--sm" data-testid="tv3-deck-check-rerun" @click="runDeckCheck">↻ 重跑</button>
        <button class="tv3-btn tv3-btn--sm" @click="checkOpen = false">×</button>
      </div>
      <div class="tv3-card__body" style="display: flex; flex-direction: column; gap: 8px">
        <div style="font-size: 11.5px; color: var(--tv3-ink3); line-height: 1.6">
          回答一个问题：<b>这份课件明天能直接照着讲吗？</b>覆盖字号可读性 / 溢出 / 例题完整 / 推导密度 / 版式节奏 / 理解检查点。
          讲解角色标注与 AI 语义检查属后端能力，原型不做假装。
        </div>
        <div v-if="!visibleIssues.length" class="tv3-empty" style="padding: 20px 0" data-testid="tv3-check-empty">
          未发现确定性问题（字号 / 溢出 / 例题完整 / 推导密度 / 版式节奏 / 检查点）。
        </div>
        <div v-for="iss in visibleIssues" :key="iss.id" class="tv3-prep__diffitem" :data-testid="`tv3-check-${iss.id}`">
          <div style="display: flex; gap: 6px; align-items: center; flex-wrap: wrap">
            <span class="tv3-tag" :class="iss.severity === 'error' ? 'tv3-tag--danger' : iss.severity === 'warn' ? 'tv3-tag--warn' : ''" style="font-size: 10px">
              {{ iss.severity === 'error' ? '错误' : iss.severity === 'warn' ? '建议' : '提示' }}
            </span>
            <span class="tv3-tag" style="font-size: 10px">第 {{ iss.slideIndex + 1 }} 页 · {{ iss.slideTitle }}</span>
            <b style="font-size: 12.5px; flex: 1; min-width: 160px">{{ iss.rule }}</b>
            <button class="tv3-btn tv3-btn--sm" :data-testid="`tv3-check-locate-${iss.id}`" @click="locateIssue(iss)">定位</button>
          </div>
          <div style="font-size: 11.5px; color: var(--tv3-ink2); line-height: 1.6">{{ iss.why }}</div>
          <div style="display: flex; gap: 6px; justify-content: flex-end">
            <button v-if="iss.fix && iss.fix.kind !== 'none'" class="tv3-btn tv3-btn--sm tv3-btn--gold" :data-testid="`tv3-check-fix-${iss.id}`" @click="fixIssue(iss)">
              {{ iss.fix.kind === 'bump-font' ? '修复：放大字号' : '修复：拆成两页' }}
            </button>
            <button class="tv3-btn tv3-btn--sm" @click="ignoredIssues.add(iss.id); ignoredIssues = new Set(ignoredIssues)">忽略</button>
          </div>
        </div>
        <div v-if="ignoredIssues.size" style="font-size: 11px; color: var(--tv3-ink4)">已忽略 {{ ignoredIssues.size }} 条（重跑体检会重新检查）</div>
      </div>
    </div>
  </div>

  <!-- B3 题库复用弹层 -->
  <div v-if="bankPickOpen" class="tv3-push" @click.self="bankPickOpen = false">
    <div class="tv3-card tv3-push__panel" style="max-width: 680px; max-height: 82vh; overflow-y: auto" data-testid="tv3-bank-pick">
      <div class="tv3-card__head">
        <span class="tv3-card__title">从题库插入当前页</span>
        <div class="tv3-card__spacer" />
        <button class="tv3-btn tv3-btn--sm" @click="bankPickOpen = false">×</button>
      </div>
      <div class="tv3-card__body" style="display: flex; flex-direction: column; gap: 6px">
        <div style="font-size: 11.5px; color: var(--tv3-ink3)">题干落为可编辑文本元素，参考答案以小字随行（teacher_confirmed=false，审定后生效）。</div>
        <div v-for="q in bankQuestions" :key="q.id" class="tv3-row" style="cursor: pointer; align-items: flex-start" :data-testid="`tv3-bankq-${q.id}`" @click="insertBankQuestion(q)">
          <span class="tv3-tag" :class="q.difficulty === 'hard' ? 'tv3-tag--danger' : q.difficulty === 'medium' ? 'tv3-tag--warn' : 'tv3-tag--ok'" style="font-size: 10px; flex-shrink: 0; margin-top: 2px">{{ q.difficulty === 'hard' ? '较难' : q.difficulty === 'medium' ? '中等' : '容易' }}</span>
          <div style="flex: 1; min-width: 0">
            <div style="font-size: 12.5px; line-height: 1.6" v-html="renderStem(q.stem_latex)" />
            <div style="font-size: 10.5px; color: var(--tv3-ink4); margin-top: 2px">{{ q.kp_name }} · {{ q.source }}</div>
          </div>
          <span class="tv3-btn tv3-btn--sm tv3-btn--gold" style="flex-shrink: 0">插入本页</span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * SlidesView —— 课件工坊（SPEC §5 全链路）
 * 三入口：主题生成（SSE）/ 拍照出课件（SSE，原图锚定）/ 教案直通
 * 五区：顶栏 · 大纲 · 画布 · 属性面板 · 元素坞（公式键盘+绘图工作台入口）
 * 红线落实：AI 草稿待确认（teacher_confirmed）、原图锚定不可删、分页不缩内容（fill_rate 可视）
 */
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { v3Api, type V3TextbookChapters, type V3DeckSummary, type V3PlanSummary, type V3QuizQuestion } from '@/api/teacherV3'
import { FIGURE_PRESETS, type FigurePresetDef } from '@/components/mathx/presets'
import { cleanPlaceholder, renderLatex, renderStem } from '@/components/mathx/latex'
import { useToastStore } from '@/stores/toast'
import SlideCanvasV3 from '@/components/teacherV3/SlideCanvasV3.vue'
import MathField from '@/components/mathx/MathField.vue'
import MathKeyboard from '@/components/mathx/MathKeyboard.vue'
import WorkshopFlow from '@/components/teacherV3/workshop/WorkshopFlow.vue'
import DrawBoard, { type DrawReopen } from '@/components/mathx/draw/DrawBoard.vue'
import PhotoInsertPanel from '@/components/mathx/PhotoInsertPanel.vue'
import { checkDeck, applyFix, type CheckIssue } from '@/pages/teacher-v3/deckCheck'
import { updateTv3Context } from '@/stores/teacherContext'
import { setReceipt, registerUndo } from '@/stores/companion'
import { getLegacyTeachingSession } from '@/pages/teacher-v3/prep/legacyKernelBridge'
import { adaptTeachingDeckSpecToLegacyDeck } from '@/pages/teacher-v3/prep/legacyArtifactAdapter'
import type { PptStudioArtifact, PptStudioDeckView, PptStudioJob, PptStudioPage } from '@/features/ppt-studio-v2/api/pptStudioApi'
import { restoreTeachingDeck } from '@/features/ppt-studio-v2/recovery/restoreTeachingDeck'
import KernelDeckPreview from '@/features/ppt-studio-v2/preview/KernelDeckPreview.vue'
import { copyFor } from '@/features/teacher-prep-v2/i18n/copy'
import { useArtifactEditing } from '@/features/teacher-prep-v2/surface/useArtifactEditing'
import type { ActiveArtifact } from '@/features/teacher-prep-v2/projection/types'
import type { PatchOperation } from '@/features/teacher-prep-v2/types/t24'
import type { CompanionCandidate } from '@/pages/teacher-v3/companionData'
import type { V3DrawInsert } from '@/components/mathx/draw/drawCore'
import type { V3BriefPayload, V3ClassInfo, V3Deck, V3Element, V3FigureRebuildCandidate, V3Slide, V3TodayData, V3LessonPlan } from '@/types/teacherV3'

type View = 'list' | 'new' | 'editor'

const route = useRoute()
const router = useRouter()
const toast = useToastStore()
const vnextMode = computed(() => route.query.mode === 'vnext')
const vnextStudio = ref<{ deck: PptStudioDeckView | null; artifacts: PptStudioArtifact[]; job: PptStudioJob | null; pages: PptStudioPage[]; events: Record<string, unknown>[]; recovery: string }>({ deck: null, artifacts: [], job: null, pages: [], events: [], recovery: '' })
/** VNext 兼容壳的唯一写入口：编辑 store 只读/写 8100 T24，绝不回落旧 generation。 */
const vnextArtifact = ref<ActiveArtifact | null>(null)
const vnextBody = ref<Record<string, unknown> | null>(null)
const vnextRevisionHint = ref<number | null>(null)

/**
 * VNext 内核预览的「返回」：回到产生这份课件的备课会话（而不是课件库列表）。
 * 会话号来自 URL query，与进入时一致；缺失才回落到备课中心首页。
 */
function onVnextBack() {
  const sid = typeof route.query.session === 'string' ? route.query.session : ''
  if (sid) {
    void router.push(`/teacher-v3/prep/session/${encodeURIComponent(sid)}`)
    return
  }
  void router.push('/teacher-v3/prep')
}

const { editing: vnextEditing, handlers: vnextEditingHandlers } = useArtifactEditing(
  vnextArtifact,
  vnextBody,
  vnextRevisionHint,
)
const vnextSaving = ref(false)
const vnextExporting = ref(false)

const view = ref<View>('list')
const newMode = ref<'photo' | 'topic' | 'plan'>('photo')
const step = ref(1)

const decks = ref<V3DeckSummary[]>([])
const classes = ref<V3ClassInfo[]>([])
const plans = ref<V3PlanSummary[]>([])
const deck = ref<V3Deck | null>(null)
const slideIdx = ref(0)
const selectedId = ref('')
const presenting = ref(false)
const presentIdx = ref(0)
const mathFieldHot = ref(false)

/* ---------- C1 AI 备课台（BriefComposer）接入与今日授课 ---------- */
const todaySchedule = ref<V3TodayData['schedule']>([])
/* Brief 台提交记录的接地上下文：大纲请求 / 生成请求 / 大纲门与教案直通的诚实标注共用。
   C1.1：requirements 不再丢弃——教师的自由文本要求参与大纲编译（词表规则，诚实标注）。 */
const briefCtx = ref<{ course_type: string; chapter: string; docs: string[]; requirements: string }>({ course_type: '新授课', chapter: '', docs: [], requirements: '' })
const chapterShort = (p: string) => p.split('▸').pop()?.trim() || p

/** 课题抽取：《》书名号优先，其次首个短句；整段要求不会被当成课题 */
function extractTopic(text: string): string {
  const book = text.match(/[《「“"]([^》」”"]+)[》」”"]/)
  if (book) return book[1]
  const first = text.split(/[，。；,;.\n]/)[0].trim()
  if (first.length >= 4 && first.length <= 30) return first
  return first.slice(0, 30) || '未命名课题'
}

/* ---------- 备小研工坊（WorkshopFlow 展示层的动作与状态；IFC-WS-a，PROTOTYPE-ONLY） ---------- */
const heroText = ref('')
const chapters = ref<V3TextbookChapters>({ textbooks: [] })
const recogCards = ref<{ photo_id: string; confidence: number; warn?: boolean; text: string; kps: string[] }[]>([])
const recogLoading = ref(false)
const recogNote = ref('')
const applyScope = ref<'all' | 'cover'>('all')
const tplFilter = ref('全部')
const planDetail = ref<V3LessonPlan | null>(null)
const planMap = ref<{ name: string; minutes: number; pages: number }[]>([])

/** 首页 hero 提交：课题抽取 + 原话进 requirements，直达大纲确认台（需求→大纲→模板→生成） */
function submitHero() {
  const text = heroText.value.trim()
  if (!text) { toast.info('先描述这节课怎么上，或点模式芯片换入口（拍照 / 教案直通）'); return }
  briefCtx.value = { course_type: briefCtx.value.course_type, chapter: briefCtx.value.chapter, docs: [...briefCtx.value.docs], requirements: text }
  form.value.topic = extractTopic(text)
  openNew('topic')
  void prepareOutline()
}
function onDocFiles(ev: Event) {
  const files = (ev.target as HTMLInputElement).files
  if (!files) return
  for (const f of [...files]) briefCtx.value.docs.push(f.name)
  ;(ev.target as HTMLInputElement).value = ''
}
function openHome() { view.value = 'list' }
function removePhoto(i: number) { photos.value.splice(i, 1); recogCards.value.splice(i, 1) }
/** 识别确认步：fixture 演示识别（诚实标注），生成时后端按原图重新识别 */
async function runRecogPreview() {
  recogLoading.value = true
  try {
    const r = await v3Api.recognition.preview({ photos: photos.value })
    const d = r.data as { items: typeof recogCards.value; note: string }
    recogCards.value = d.items
    recogNote.value = d.note
  } catch { recogCards.value = []; recogNote.value = '识别服务暂时不可用，请稍后重试' } finally { recogLoading.value = false }
}
function nextPhoto() { if (photos.value.length) step.value = 2 }
/** 教案直通：选定教案 → 拉环节结构 → 页数映射行 */
async function selectPlan(id: string) {
  form.value.plan_id = id
  try {
    const r = await v3Api.plans.get(id)
    planDetail.value = r.data
    planMap.value = (r.data.sections || []).map((sec) => ({ name: sec.name, minutes: sec.minutes, pages: 1 }))
  } catch { planMap.value = [] }
}
function nextPlan() {
  if (!planDetail.value || !planMap.value.length) return
  briefCtx.value.course_type = planDetail.value.lesson_type || '新授课'
  gateOutline.value = planMap.value.flatMap((r) =>
    Array.from({ length: r.pages }, (_, j) => ({
      title: r.pages > 1 ? `${r.name}（${j + 1}）` : r.name,
      kind: /定义|概念/.test(r.name) ? 'definition' : /推导|探究/.test(r.name) ? 'derivation' : /例题/.test(r.name) ? 'example' : /变式|练习/.test(r.name) ? 'variation' : /小结|回顾/.test(r.name) ? 'summary' : 'blank',
      minutes: Math.max(2, Math.round(r.minutes / r.pages)),
    })),
  )
  gateMatched.value = false
  gateNote.value = '教案直通：以已确认教案的环节结构为骨架（原型结构直通，不解析教案正文）'
  gateReqs.value = []
  step.value = 4
}
function addOutlinePage() { gateOutline.value.push({ title: '新页', kind: 'blank', minutes: 6 }) }
function delOutlinePage(i: number) { gateOutline.value.splice(i, 1) }
function moveOutlinePage(i: number, d: number) {
  const j = i + d
  if (j < 0 || j >= gateOutline.value.length) return
  const [x] = gateOutline.value.splice(i, 1)
  gateOutline.value.splice(j, 0, x)
}
function quickAdjust(t: string) { gateAdjust.value = t; regenWithAdjust() }
function prevTemplate() { step.value = newMode.value === 'topic' || newMode.value === 'plan' ? 4 : 1 }

/** 今日授课「去备」：预填首页输入台（课题+班级），不隐藏跳转 */
function prefillLesson(s: { class_name: string; topic: string }) {
  const cid = classes.value.find((c) => c.name === s.class_name)?.class_id || 'c2-05'
  heroText.value = s.topic
  form.value.class_id = cid
  view.value = 'list'
  window.scrollTo({ top: 0, behavior: 'smooth' })
  toast.info(`已把「${s.topic}」预填进输入台：补选章节后点发送开始`)
}

/** 从已有课件改编：定位到我的课件列表（母本 Diff 建议属下一批次，先诚实引导） */
function adaptFromDeck() {
  toast.info('从下方「我的课件」选择母本打开编辑器改编：结构化元素保留、模板可换')
}

/* 大纲门环节语义标签（课型→环节结构由 server 下发 kind，这里只做展示映射） */
const GATE_KIND_LABEL: Record<string, string> = { cover: '封面', review: '复习', definition: '概念', derivation: '推导', example: '例题', variation: '练习', summary: '小结', blank: '板书' }
const kindLabel = (k: string) => GATE_KIND_LABEL[k] || k

/* 大纲/属性面板宽度可拖拽（localStorage 持久化） */
const outlineW = ref(Number(localStorage.getItem('tv3-outline-w')) || 168)
const propsW = ref(Number(localStorage.getItem('tv3-props-w')) || 264)
function startResize(ev: MouseEvent, which: 'outline' | 'props') {
  ev.preventDefault()
  const startX = ev.clientX
  const startW = which === 'outline' ? outlineW.value : propsW.value
  const wRef = which === 'outline' ? outlineW : propsW
  const onMove = (e2: MouseEvent) => {
    const delta = which === 'outline' ? e2.clientX - startX : startX - e2.clientX
    wRef.value = Math.round(Math.max(which === 'outline' ? 132 : 240, Math.min(which === 'outline' ? 300 : 560, startW + delta)))
  }
  const onUp = () => {
    window.removeEventListener('mousemove', onMove)
    window.removeEventListener('mouseup', onUp)
    document.body.style.cursor = ''
    localStorage.setItem(which === 'outline' ? 'tv3-outline-w' : 'tv3-props-w', String(wRef.value))
  }
  document.body.style.cursor = 'col-resize'
  window.addEventListener('mousemove', onMove)
  window.addEventListener('mouseup', onUp)
}
const rebuildCandidates = ref<V3FigureRebuildCandidate[]>([])
const aiDiffs = ref<{ op: string; after?: V3Element; element_id?: string; reason?: string }[]>([])

const photos = ref<string[]>([])
const photoDragOver = ref(false)
const fileInput = ref<HTMLInputElement | null>(null)
const propsMathField = ref<InstanceType<typeof MathField> | null>(null)

const form = ref({
  topic: '双曲线及其标准方程（第1课时）',
  class_id: 'c2-05',
  plan_id: '',
  scope: 'stem+solution' as 'stem' | 'stem+solution' | 'stem+keypoints',
  mode: 'full-solution' as 'blank-board' | 'full-solution' | 'keypoints',
  template_id: 'tpl-academic-blue',
  font_tier: 'standard' as 'compact' | 'standard' | 'large',
  margin_notes: true,
})

const genProgress = ref(0)
const genStage = ref('')
const genFailed = ref(false)
const genBlocks = ref<{ type: string; latex?: string; text?: string; confidence: number }[]>([])
let sseCtrl: { abort: () => void } | null = null

/* ---------- 生成流程持久化（P：切换界面/刷新不丢，重挂载恢复） ---------- */
const WS_DRAFT_KEY = 'tv3-slides-draft-v1'
interface WsDraft {
  view: 'new'
  newMode: 'photo' | 'topic' | 'plan'
  step: number
  form: typeof form.value
  briefCtx: typeof briefCtx.value
  heroText: string
  gateOutline: typeof gateOutline.value
  gateReqs: typeof gateReqs.value
  gateNote: string
  gateMatched: boolean
  genStage: string
  genProgress: number
  genFailed: boolean
  savedAt: number
}
function persistWorkshopDraft() {
  if (view.value !== 'new') { try { localStorage.removeItem(WS_DRAFT_KEY) } catch { /* ignore */ } return }
  const d: WsDraft = {
    view: 'new', newMode: newMode.value, step: step.value,
    form: JSON.parse(JSON.stringify(form.value)),
    briefCtx: JSON.parse(JSON.stringify(briefCtx.value)),
    heroText: heroText.value,
    gateOutline: JSON.parse(JSON.stringify(gateOutline.value)),
    gateReqs: JSON.parse(JSON.stringify(gateReqs.value)),
    gateNote: gateNote.value, gateMatched: gateMatched.value,
    genStage: genStage.value, genProgress: genProgress.value, genFailed: genFailed.value,
    savedAt: Date.now(),
  }
  try { localStorage.setItem(WS_DRAFT_KEY, JSON.stringify(d)) } catch { /* ignore */ }
}
function restoreWorkshopDraft(): void {
  let d: WsDraft | null = null
  try {
    const raw = localStorage.getItem(WS_DRAFT_KEY)
    d = raw ? (JSON.parse(raw) as WsDraft) : null
  } catch { d = null }
  if (!d || d.view !== 'new') return
  // 只恢复进行中的流程（步骤 ≥ 大纲确认；已完成的生成由落库后的列表承载，不残留）
  if (d.step < 4 && d.step !== 3) { try { localStorage.removeItem(WS_DRAFT_KEY) } catch { /* ignore */ } return }
  view.value = 'new'
  newMode.value = d.newMode
  step.value = d.step
  form.value = { ...form.value, ...d.form }
  briefCtx.value = d.briefCtx
  heroText.value = d.heroText
  gateOutline.value = d.gateOutline
  gateReqs.value = d.gateReqs
  gateNote.value = d.gateNote
  gateMatched.value = d.gateMatched
  genStage.value = d.genStage
  genProgress.value = d.genProgress
  genFailed.value = d.genFailed
  // 恢复到步骤4（大纲确认）但大纲为空（可能是生成中切走）→ 自动重新拉取大纲
  if (step.value === 4 && !gateOutline.value.length && !genFailed.value) {
    step.value = 1
    void prepareOutline()
  }
  // 恢复到步骤3（逐页生成中）→ 基于已确认的大纲自动重新发起生成
  if (step.value === 3 && gateOutline.value.length && !genFailed.value) {
    genStage.value = '检测到未完成的生成任务，正在继续…'
    genProgress.value = 4
    void startGenerate()
  }
}

const templates = ref<{ id: string; name: string; style: string; swatch: { bg: string; primary: string; accent: string; light: boolean }; page_kinds: string[]; recommended_for: string }[]>([])
/* V3.3：模板兜底——即使接口加载失败也始终渲染多套主题，杜绝「只有一档/空白」 */
const DECK_TEMPLATE_FALLBACK: typeof templates.value = [
  { id: 'tpl-academic-blue', name: '学术蓝·严谨版', style: 'academic', swatch: { bg: '#4f46e5', primary: '#4f46e5', accent: '#0891b2', light: true }, page_kinds: ['cover', 'definition', 'derivation', 'example', 'summary'], recommended_for: '新授课·概念课' },
  { id: 'tpl-chalkboard', name: '黑板绿·手写感', style: 'chalkboard', swatch: { bg: '#1e3a2f', primary: '#2d5546', accent: '#e8c56a', light: false }, page_kinds: ['cover', 'derivation', 'example', 'keypoints'], recommended_for: '推导课·习题课' },
  { id: 'tpl-geometric', name: '几何灰·图纸感', style: 'geometric', swatch: { bg: '#eceff4', primary: '#37474f', accent: '#e65100', light: true }, page_kinds: ['cover', 'definition', 'variation', 'blank'], recommended_for: '立体几何·图形密集课' },
  { id: 'tpl-classic-navy', name: '经典藏青·正式', style: 'classic', swatch: { bg: '#12264d', primary: '#c2a75a', accent: '#e3c877', light: false }, page_kinds: ['cover', 'review', 'summary'], recommended_for: '公开课·示范课' },
  { id: 'tpl-minimal-white', name: '极简白·留白', style: 'minimal', swatch: { bg: '#ffffff', primary: '#23272e', accent: '#3b82c4', light: true }, page_kinds: ['cover', 'definition', 'example', 'blank'], recommended_for: '复习课·概念梳理' },
]

const scopeCards = [
  { value: 'stem', name: '仅题干', swatch: { bg: '#ffffff', primary: '#4f46e5', accent: '#0891b2', light: true }, fit: '课堂即讲即用', note: '只识别题目，解答教师现场写' },
  { value: 'stem+solution', name: '题干 + 解答', swatch: { bg: '#ffffff', primary: '#4f46e5', accent: '#0891b2', light: true }, fit: '例题精讲', note: '完整解答步骤，自动分页' },
  { value: 'stem+keypoints', name: '题干 + 关键步骤', swatch: { bg: '#ffffff', primary: '#4f46e5', accent: '#0891b2', light: true }, fit: '作业讲评', note: '只保留关键步骤，留白给学生' },
] as const
const modeCards = [
  { value: 'blank-board', name: '板书留白', swatch: { bg: '#1e3a2f', primary: '#2d5546', accent: '#e8c56a', light: false }, fit: '推导课', note: '只出结构与题干，过程课堂生成' },
  { value: 'full-solution', name: '完整解答', swatch: { bg: '#3730a3', primary: '#4f46e5', accent: '#0891b2', light: true }, fit: '自学/复习', note: '解答完整呈现，逐页展开' },
  { value: 'keypoints', name: '要点提炼', swatch: { bg: '#f4f4f2', primary: '#37474f', accent: '#e65100', light: true }, fit: '讲评课', note: '错因+关键步骤卡片' },
] as const
const fontCards = [
  { value: 'compact', px: 18, name: '紧凑档', swatch: { bg: '#ffffff', primary: '#37474f', accent: '#0e9488', light: true }, note: '信息密度高，教室后排慎用' },
  { value: 'standard', px: 22, name: '标准档', swatch: { bg: '#ffffff', primary: '#4f46e5', accent: '#0891b2', light: true }, note: '推荐：一般教室' },
  { value: 'large', px: 26, name: '大字号', swatch: { bg: '#ffffff', primary: '#b45309', accent: '#dc2646', light: true }, note: '阶梯教室 / 视力关注班级' },
] as const

const currentSlide = computed<V3Slide>(() => deck.value?.slides[slideIdx.value] ?? { id: 'empty', layout: 'blank', elements: [] })
const selectedEl = computed<V3Element | null>(() => currentSlide.value.elements.find((e) => e.id === selectedId.value) ?? null)

function decodeLegacyHtml(value: string): string {
  if (typeof document === 'undefined') return value
  const node = document.createElement('textarea')
  node.innerHTML = value
  return node.value
}

function vnextPatchOperations(): PatchOperation[] {
  const editing = vnextEditing.value
  const current = deck.value
  if (!editing || !current) return []
  const operations: PatchOperation[] = []
  const serverById = new Map(editing.deckSlides.map((slide) => [slide.id, slide]))
  current.slides.forEach((slide) => {
    const serverSlide = serverById.get(slide.id)
    if (!serverSlide) return
    const titleElement = slide.elements.find((element) => element.type === 'text' && element.id.startsWith('vnext-title-'))
    const contentElement = slide.elements.find((element) => element.type === 'text' && element.id.startsWith('vnext-content-'))
    const titleAfter = titleElement && titleElement.type === 'text' ? decodeLegacyHtml(titleElement.html) : serverSlide.title
    const contentAfter = contentElement && contentElement.type === 'text' ? decodeLegacyHtml(contentElement.html) : serverSlide.content
    if (titleAfter !== serverSlide.title) {
      operations.push({ op: 'set_slide', target: slide.id, field: 'title', value: titleAfter, before: serverSlide.title })
    }
    if (contentAfter !== serverSlide.content) {
      operations.push({ op: 'set_slide', target: slide.id, field: 'content', value: contentAfter, before: serverSlide.content })
    }
    const notesAfter = slide.notes || ''
    const notesBefore = serverSlide.notes || ''
    if (notesAfter !== notesBefore) {
      operations.push({ op: 'set_slide', target: slide.id, field: 'speaker_note', value: notesAfter || null, before: notesBefore })
    }
  })
  const metadataTitle = typeof editing.metadata?.title === 'string' ? editing.metadata.title : null
  if (metadataTitle !== null && current.title !== metadataTitle) {
    operations.push({ op: 'set_metadata', target: editing.remoteId, field: 'title', value: current.title, before: metadataTitle })
  }
  return operations
}

function syncLegacyDeckFromVnext(): void {
  const editing = vnextEditing.value
  const current = deck.value
  if (!editing || !current) return
  const serverById = new Map(editing.deckSlides.map((slide) => [slide.id, slide]))
  current.slides.forEach((slide) => {
    const serverSlide = serverById.get(slide.id)
    if (!serverSlide) return
    const titleElement = slide.elements.find((element) => element.type === 'text' && element.id.startsWith('vnext-title-'))
    const contentElement = slide.elements.find((element) => element.type === 'text' && element.id.startsWith('vnext-content-'))
    // E-3R3：element.html 约定存原文（renderRich 负责消毒），与适配器一致
    if (titleElement && titleElement.type === 'text') titleElement.html = serverSlide.title
    if (contentElement && contentElement.type === 'text') contentElement.html = serverSlide.content
    slide.notes = serverSlide.notes
  })
  const title = typeof editing.metadata?.title === 'string' ? editing.metadata.title : null
  if (title) current.title = title
}

const canNext = computed(() => {
  if (newMode.value === 'photo') return photos.value.length > 0
  if (newMode.value === 'topic') return form.value.topic.trim().length > 1
  return !!form.value.plan_id
})

/* ---------- 工具 ---------- */
const sourceLabel = (s: string) => (s === 'photo' ? '拍照生成' : s === 'lesson-push' ? '教案直通' : s === 'review-notes' ? '讲评笔记' : '主题生成')
const templateName = (id: string) => templates.value.find((t) => t.id === id)?.name || id
const typeLabel = (t: string) => ({ text: '文本', formula: '公式', geometry: '几何图形', functionPlot: '函数图像', dynamicDemo: '动态演示', image: '图片', anchorPhoto: '原图锚定', pageNo: '页码' } as Record<string, string>)[t] || t
const isMathEl = (e: V3Element) => ['formula', 'geometry', 'functionPlot', 'dynamicDemo'].includes(e.type)
const presetNameOf = (id?: string) => FIGURE_PRESETS.find((p) => p.id === id)?.name || '自定义构造'

const geoParams = (e: Extract<V3Element, { type: 'geometry' }>) => FIGURE_PRESETS.find((p) => p.id === e.preset_id)?.params || []
const geoToggles = (e: Extract<V3Element, { type: 'geometry' }>) => FIGURE_PRESETS.find((p) => p.id === e.preset_id)?.toggles || []
function setGeoParam(e: Extract<V3Element, { type: 'geometry' }>, key: string, v: number) { e.params = { ...e.params, [key]: v } }
function setGeoToggle(e: Extract<V3Element, { type: 'geometry' }>, key: string, v: boolean) { e.toggles = { ...(e.toggles || {}), [key]: v } }

function defaultParams(p: FigurePresetDef) {
  const o: Record<string, number> = {}
  for (const sp of p.params) o[sp.key] = sp.def
  return o
}
function fillBarStyle(s: V3Slide) {
  const r = s.fill_rate ?? 0.5
  return { width: `${Math.round(r * 100)}%`, background: r > 0.92 ? 'var(--tv3-rose)' : r > 0.6 ? 'var(--tv3-teal)' : 'var(--tv3-amber)' }
}

/* ---------- B6 上下文写入（管家跟随当前课件/页/选中元素） ---------- */
function elSummary(e: V3Element): string {
  if (e.type === 'formula') return '公式 ' + e.latex.slice(0, 18)
  if (e.type === 'text') return '文本「' + e.html.replace(/<[^>]+>/g, '').slice(0, 12) + '…」'
  if (e.type === 'geometry') return '几何 ' + (e.preset_id || '')
  return e.type
}
watch([deck, slideIdx, selectedId, view], () => {
  updateTv3Context({
    route: '/teacher-v3/slides',
    deck_id: deck.value?.id,
    deck_title: deck.value?.title,
    slide_index: slideIdx.value,
    slide_count: deck.value?.slides.length,
    selection: selectedEl.value ? { type: selectedEl.value.type, summary: elSummary(selectedEl.value) } : undefined,
  })
})

/* B6 管家快捷动作（本页能真实执行的） */
function onButlerQuick(ev: Event) {
  const action = (ev as CustomEvent).detail?.action
  if (action === 'run-check') openDeckCheck()
  else if (action === 'open-bank') openBankPick()
  else if (action === 'ai-element') runAiElement()
}

/* ---------- 数据加载 ---------- */
const routeQ = new URLSearchParams(window.location.hash.split('?')[1] || '')
if (routeQ.get('deck')) {
  void nextTick(() => openDeck(routeQ.get('deck') as string))
}
onMounted(async () => {
  window.addEventListener('tv3-butler-insert', onButlerInsert as EventListener)
  window.addEventListener('tv3-butler-quick', onButlerQuick as EventListener)
  const q = route.query

  /* VNext is an isolated read-only branch. It must run before historical
     draft/catalog/generation work, so a TeachingDeckSpec cannot fall through. */
  const vnextSession = typeof q.session === 'string' ? q.session : ''
  if (q.mode === 'vnext' && vnextSession) {
    try {
      const snapshot = await getLegacyTeachingSession(vnextSession)
      const spec = snapshot.artifact?.teaching_deck
      if (spec) {
        const session = snapshot.session as Record<string, unknown>
        const specId = typeof spec.deck_id === 'string' && spec.deck_id
          ? spec.deck_id
          : typeof spec.spec_id === 'string' && spec.spec_id
            ? spec.spec_id
            : typeof spec.id === 'string' && spec.id
              ? spec.id
              : ''
        if (!specId) {
          toast.error(copyFor('prep.error.deck_restore'))
          return
        }
        vnextBody.value = spec
        vnextRevisionHint.value = typeof spec.revision === 'number' ? spec.revision : null
        const metadata = spec.metadata && typeof spec.metadata === 'object' ? spec.metadata as Record<string, unknown> : {}
        vnextArtifact.value = {
          artifact_id: specId,
          artifact_type: 'teaching_deck_spec',
          revision: typeof spec.revision === 'number' ? spec.revision : 0,
          title: typeof metadata.title === 'string' ? metadata.title : (typeof spec.title === 'string' ? spec.title : '课件'),
          status: typeof spec.status === 'string' ? spec.status : null,
          updated_at: typeof spec.updated_at === 'string' ? spec.updated_at : null,
        }
        // TeachingDeckSpec.id is not the 8200 persisted deck id. The recovery
        // boundary ingests the complete spec first, then reads only with the
        // returned deck_id. Partial 8200 failures never hide the 8100 spec.
        const restored = await restoreTeachingDeck(spec)
        vnextStudio.value = restored
        deck.value = adaptTeachingDeckSpecToLegacyDeck(spec, typeof session.class_name === 'string' ? session.class_name : '')
        slideIdx.value = 0
        selectedId.value = ''
        view.value = 'editor'
        genStage.value = copyFor('prep.vnext.deck.restored')
        return
      }
      toast.info(copyFor('prep.error.deck_missing'))
    } catch {
      toast.error(copyFor('prep.error.deck_restore'))
    }
    return
  }

  restoreWorkshopDraft()
  const [d, c, p, t, td] = await Promise.all([
    v3Api.decks.list().then((r) => r.data.items).catch(() => []),
    v3Api.catalog.classes().then((r) => r.data.items).catch(() => []),
    v3Api.plans.list().then((r) => r.data.items).catch(() => []),
    v3Api.catalog.deckTemplates().then((r) => r.data.items).catch(() => []),
    v3Api.catalog.today().then((r) => r.data).catch(() => null),
  ])
  decks.value = d
  classes.value = c
  plans.value = p
  templates.value = (t && t.length ? t : DECK_TEMPLATE_FALLBACK) as typeof templates.value
  todaySchedule.value = td?.schedule || []
  void v3Api.catalog.textbookChapters().then((r) => { chapters.value = r.data }).catch(() => { /* 章节接口不可用时 pill 留空 */ })
  /* 管家 navigate 落点（剧本A）：
   *  deck=<id>          → 直接打开该课件编辑器
   *  mode=topic&topic=… → 落在 AI 备课台预填（C1：大纲确认仍在，不直开向导）
   *  mode=photo/plan    → 打开对应向导 */
   if (typeof q.deck === 'string' && q.deck) {
    void openDeck(q.deck)
  } else if (q.mode === 'photo' || q.mode === 'plan') {
    openNew(q.mode)
    if (typeof q.class_id === 'string' && q.class_id) form.value.class_id = q.class_id
    if (typeof q.template_id === 'string' && q.template_id) form.value.template_id = q.template_id
  } else if (q.mode === 'topic') {
    heroText.value = typeof q.topic === 'string' ? q.topic : ''
    if (typeof q.class_id === 'string' && q.class_id) form.value.class_id = q.class_id
    if (typeof q.template_id === 'string' && q.template_id) form.value.template_id = q.template_id
    toast.info('管家已把课题预填进输入台：补选章节/材料后点发送开始')
  }
})
onBeforeUnmount(() => window.removeEventListener('tv3-butler-quick', onButlerQuick as EventListener))

async function openDeck(id: string) {
  try {
    const r = await v3Api.decks.get(id)
    deck.value = r.data
    slideIdx.value = 0
    selectedId.value = ''
    view.value = 'editor'
  } catch { /* mock 不可用 */ }
}

/* ---------- B3 大纲 Gate（C1.1：要求回应单 + 调整指令重出） ---------- */
const outlineLoading = ref(false)
const gateMatched = ref(false)
const gateNote = ref('')
const gateOutline = ref<{ title: string; kind: string; minutes?: number }[]>([])
const gateReqs = ref<{ id: number; text: string; status: string; pages: number[]; note?: string }[]>([])
const gateAdjust = ref('')
async function prepareOutline(adjust?: string) {
  step.value = 4
  outlineLoading.value = true
  persistWorkshopDraft()
  try {
    /* C1.1：教师的原始要求 + 追加调整指令一起送编译（词表规则，server 诚实回台账） */
    const requirements = [briefCtx.value.requirements, adjust]
      .map((s) => (s || '').trim())
      .filter(Boolean)
    /* C1：携带章节/课型——章节决定内容源路由，课型决定大纲环节结构 */
    const r = await v3Api.generation.deckOutline({
      topic: form.value.topic,
      class_id: form.value.class_id,
      template_id: form.value.template_id,
      chapter: briefCtx.value.chapter || undefined,
      course_type: briefCtx.value.course_type,
      requirements: requirements.length ? requirements : undefined,
    })
    gateOutline.value = (r.data as { outline: { title: string; kind: string }[] }).outline
    gateMatched.value = !!(r.data as { matched?: boolean }).matched
    gateNote.value = (r.data as { note?: string }).note || ''
    gateReqs.value = (r.data as { reqs?: { id: number; text: string; status: string; pages: number[]; note?: string }[] }).reqs || []
    persistWorkshopDraft()
  } catch {
    gateOutline.value = [
      { title: '情境引入', kind: 'cover' }, { title: '概念定义', kind: 'definition' },
      { title: '例题精讲', kind: 'example' }, { title: '变式训练', kind: 'variation' }, { title: '课堂小结', kind: 'summary' },
    ]
    gateMatched.value = false
    gateNote.value = '大纲生成服务暂时不可用，已为你加载标准课件结构，可手动调整'
    gateReqs.value = []
    persistWorkshopDraft()
  } finally { outlineLoading.value = false }
}
function regenWithAdjust() {
  const t = gateAdjust.value.trim()
  if (!t) { toast.info('先输入调整要求，再重出大纲（例如：去掉复习回顾，加一道当堂检测）'); return }
  void prepareOutline(t)
}
function confirmOutline() {
  if (gateOutline.value.length < 2) return
  /* 备小研链路：需求 → 大纲确认 → 选择模板 → 生成 */
  step.value = 2
}

/* ---------- B3 可讲性体检（确定性检查，无总分） ---------- */
const checkOpen = ref(false)
const checkIssues = ref<CheckIssue[]>([])
const ignoredIssues = ref(new Set<string>())
const visibleIssues = computed(() => checkIssues.value.filter((i) => !ignoredIssues.value.has(i.id)))
function openDeckCheck() {
  if (!deck.value) return
  runDeckCheck()
  checkOpen.value = true
}
function runDeckCheck() {
  if (!deck.value) return
  ignoredIssues.value = new Set()
  checkIssues.value = checkDeck(deck.value)
}
function locateIssue(iss: CheckIssue) {
  if (!deck.value) return
  view.value = 'editor'
  slideIdx.value = iss.slideIndex
  selectedId.value = iss.elementId || ''
}
function fixIssue(iss: CheckIssue) {
  if (!deck.value) return
  const r = applyFix(deck.value, iss)
  if (r === 'applied') {
    toast.success(iss.fix?.kind === 'split-slide' ? `已拆成两页：第 ${iss.slideIndex + 2} 页为推导续页（可继续编辑）` : '已放大该元素字号（元素仍可继续调整）')
  } else {
    toast.error('该修复暂不支持')
  }
  runDeckCheck()
}

/* ---------- B3 题库复用：题目落到当前页（题干 + 参考答案小字） ---------- */
const bankPickOpen = ref(false)
const bankQuestions = ref<V3QuizQuestion[]>([])
async function openBankPick() {
  if (!deck.value) { toast.info('请先打开一个课件，再插入题库题目'); return }
  bankPickOpen.value = true
  try { const r = await v3Api.catalog.quizQuestions(); bankQuestions.value = r.data.items } catch { bankQuestions.value = [] }
}
function insertBankQuestion(q: V3QuizQuestion) {
  if (!deck.value) return
  const sid = Date.now()
  currentSlide.value.elements.push(
    { id: `ebq-${sid}-s`, type: 'text', left: 70, top: 120, width: 940, height: 130, z: 5, html: renderStem(q.stem_latex), font_size: 20, teacher_confirmed: false } as V3Element,
    { id: `ebq-${sid}-a`, type: 'text', left: 70, top: 300, width: 940, height: 44, z: 5, html: `<b>参考答案：</b>${renderStem(q.answer || '待补')}`, font_size: 15, color: '#0e9488', teacher_confirmed: false } as V3Element,
  )
  bankPickOpen.value = false
  toast.success(`已把「${q.kp_name}」题目插入当前页（题干可编辑，答案为参考小字）`)
}

/* ---------- 新建流程 ---------- */
function openNew(mode: 'photo' | 'topic' | 'plan') {
  newMode.value = mode
  step.value = 1
  genProgress.value = 0
  genStage.value = ''
  genBlocks.value = []
  genFailed.value = false
  view.value = 'new'
  persistWorkshopDraft()
}

function onFileChange(ev: Event) {
  const files = (ev.target as HTMLInputElement).files
  if (!files) return
  void readPhotos(files)
}
function onPhotoDrop(ev: DragEvent) {
  photoDragOver.value = false
  const files = ev.dataTransfer?.files
  if (files?.length) void readPhotos(files)
}
function readPhotos(files: FileList) {
  const imgs = [...files].filter((f) => f.type.startsWith('image/')).slice(0, 6)
  for (const f of imgs) {
    const rd = new FileReader()
    rd.onload = () => { if (typeof rd.result === 'string') photos.value.push(rd.result) }
    rd.readAsDataURL(f)
  }
}

async function startGenerate() {
  step.value = 3
  genProgress.value = 4
  genBlocks.value = []
  persistWorkshopDraft()
  const onEvent = (event: string, data: any) => {
    if (event === 'meta') { genStage.value = '已接收任务，开始处理…'; genProgress.value = 10 }
    else if (event === 'photo') { genStage.value = `接收原图 ${data.index + 1}：${data.note}`; genProgress.value = Math.min(24, genProgress.value + 8) }
    else if (event === 'block') { genBlocks.value.push(data); genProgress.value = Math.min(82, genProgress.value + 7); genStage.value = `识别块 ${genBlocks.value.length}：${data.type === 'figure' ? '图形区域' : '公式/文本'}` }
    else if (event === 'outline') { genStage.value = `生成大纲：${data.items?.join(' / ') || ''}`; genProgress.value = 38 }
    else if (event === 'slide') { genStage.value = data.note || `草稿页 ${data.index + 1}（未确认）`; genProgress.value = Math.min(86, 40 + data.index * Math.max(4, Math.floor(46 / Math.max(1, gateOutline.value.length)))) }
    else if (event === 'paginate') { genStage.value = `分页引擎：${data.note}`; genProgress.value = 90 }
    else if (event === 'error') {
      /* 后端诚实失败（识别链未部署/生成未实现/LLM 异常）——如实呈现，绝不假成功 */
      genFailed.value = true
      genProgress.value = 0
      genStage.value = data?.message || '生成失败，请重试。你的大纲与选择都已保留。'
    }
    else if (event === 'done') {
      if (data?.finish_reason && data.finish_reason !== 'stop') {
        genFailed.value = true
        genProgress.value = 0
        genStage.value = data.finish_reason === 'dependency_missing'
          ? '依赖的推理服务未就绪，生成已中止（原图与选择已保留，可稍后重试）。'
          : `生成未完成（${data.finish_reason}）。你的大纲与选择都已保留，可重试。`
        return
      }
      genProgress.value = 100
      genStage.value = '完成，正在打开编辑器…'
      try { localStorage.removeItem(WS_DRAFT_KEY) } catch { /* ignore */ }
      window.setTimeout(() => openDeck(data.deck_id), 500)
    }
  }
  try {
    if (newMode.value === 'photo') {
      sseCtrl = v3Api.recognition.photoIngest(
        { photos: photos.value, question_label: `${form.value.class_id === 'c2-05' ? '高二(5)班' : '高二(3)班'}例题（拍照）`, config: { scope: form.value.scope, mode: form.value.mode, template_id: form.value.template_id, font_tier: form.value.font_tier, margin_notes: form.value.margin_notes } },
        onEvent,
      )
    } else {
      /* B3 大纲 Gate：教师确认的大纲决定页数与每页标题。
         教案直通（plan）也走同一链路：以教案课题/课型为入参，outline 来自映射后的环节结构。
         IFC-PRODUCT-01a / IFC-C1-a：outline/chapter/course_type/material_name 类型层 as any 过渡。 */
      const genTopic = newMode.value === 'plan' ? (planDetail.value?.topic || form.value.topic) : form.value.topic
      const genType = newMode.value === 'plan' ? (planDetail.value?.lesson_type || briefCtx.value.course_type) : briefCtx.value.course_type
      sseCtrl = v3Api.generation.deck({
        topic: genTopic, class_id: form.value.class_id, template_id: form.value.template_id,
        outline: gateOutline.value.map((o) => ({ title: o.title, kind: o.kind })),
        chapter: briefCtx.value.chapter || undefined,
        course_type: genType,
        material_name: briefCtx.value.docs[0] || undefined,
      } as any, onEvent)
    }
  } catch {
    genFailed.value = true
    genStage.value = 'AI 服务暂时不可用，生成已中断。你的大纲与选择都已保留，可点击重试。'
  }
}
/** M3 错误态兜底：生成中断后一键重试（保留大纲/模板选择） */
function retryGenerate() {
  genFailed.value = false
  void startGenerate()
}

/* ---------- 备小研工坊控制器：注入 WorkshopFlow 的响应式状态 + 动作（IFC-WS-a） ---------- */
const wsScreen = computed(() => {
  if (view.value === 'list') return 'home'
  if (step.value === 3) return 'generating'
  if (step.value === 4) return 'outline'
  if (step.value === 2) return 'template'
  return newMode.value
})
/** 模板缩略图加载失败标记（WorkshopFlow template 页使用；缺失会导致渲染 TypeError） */
const thumbFail = ref<Record<string, boolean>>({})
/** 工坊顶栏副标题（按当前屏幕给文案） */
const screenTitleSub = computed(() => {
  const map: Record<string, string> = {
    home: '从需求或教案一键出课件',
    photo: '拍照/选择题目图片，识别后生成课件',
    plan: '从已有教案直通课件',
    outline: '逐页确认结构，可增删排序后进入模板选择',
    template: '选择版式风格，生成后可更换',
    generating: 'AI 正在逐页生成，可稍后回来查看',
  }
  return map[wsScreen.value] || ''
})
const tplFiltered = computed(() => {
  const f = tplFilter.value
  if (f === '全部') return templates.value
  const m: Record<string, string> = { 学术风: 'academic', 手写板书: 'chalkboard', 简约: 'minimal', 公开课: 'classic' }
  return templates.value.filter((t) => t.style === m[f])
})
const currentTemplate = computed(() => templates.value.find((t) => t.id === form.value.template_id))
const previewTopic = computed(() => (newMode.value === 'plan' ? planDetail.value?.topic || '' : form.value.topic) || '未命名课题')
const className = computed(() => classes.value.find((c) => c.class_id === form.value.class_id)?.name || '')

const ws = reactive({
  get screen() { return wsScreen.value },
  view, newMode, step, form, briefCtx, heroText, photos, recogCards, recogLoading, recogNote,
  plans, planDetail, planMap, decks, todaySchedule, classes, chapters, templates,
  gateOutline, gateReqs, gateAdjust, gateMatched, gateNote, outlineLoading,
  genStage, genProgress, genBlocks, genFailed, retryGenerate, applyScope, tplFilter, currentTemplate, previewTopic, className, tplFiltered,
  thumbFail, screenTitleSub, onFileChange,
  kindLabel, chapterShort, sourceLabel, templateName, renderLatex, scopeCards, modeCards, fontCards,
  openHome, openNew, submitHero, onDocFiles, adaptFromDeck, prefillLesson, openDeck,
  removePhoto, nextPhoto, selectPlan, nextPlan, prepareOutline, regenWithAdjust, quickAdjust,
  addOutlinePage, delOutlinePage, moveOutlinePage, confirmOutline, prevTemplate, startGenerate,
})

/* ---------- 编辑器操作 ---------- */
function addSlide() {
  if (!deck.value) return
  const s: V3Slide = {
    id: `sl-${Date.now()}`,
    layout: 'blank',
    elements: [
      { id: `e${Date.now()}`, type: 'text', left: 70, top: 52, width: 600, height: 50, z: 1, html: '新页标题', font_size: 28, bold: true, color: '#3730a3' },
      { id: `p${Date.now()}`, type: 'pageNo', left: 1180, top: 680, width: 60, height: 30, z: 1, no: deck.value.slides.length + 1 },
    ],
  }
  deck.value.slides.splice(slideIdx.value + 1, 0, s)
  slideIdx.value += 1
}
function deleteSlide(i: number) {
  if (!deck.value || deck.value.slides.length <= 1) return
  deck.value.slides.splice(i, 1)
  if (slideIdx.value >= deck.value.slides.length) slideIdx.value = deck.value.slides.length - 1
}
function onElementMoved(id: string, left: number, top: number) {
  const el = currentSlide.value.elements.find((e) => e.id === id)
  if (el) { el.left = left; el.top = top }
}
function onDropLatex(p: { latex: string; left: number; top: number; from?: 'keyboard' | 'butler'; width?: number; height?: number; font_size?: number; teacher_confirmed?: boolean }) {
  addFormulaElement(cleanPlaceholder(p.latex), p.left, p.top, p)
  // 公式键盘拖入的是占位符模板：存清洗值后把原始模板塞进属性面板 MathField，教师光标落槽立即输入；
  // 管家公式卡是完整公式（含字号等要素），直接落布不再追加插入
  if (p.from !== 'butler') {
    nextTick(() => { if (selectedEl.value?.type === 'formula') propsMathField.value?.insert(p.latex) })
  }
}
function addFormulaElement(latex: string, left = 480, top = 320, opts?: { width?: number; height?: number; font_size?: number; teacher_confirmed?: boolean }) {
  if (!deck.value) return
  const el: V3Element = {
    id: `e${Date.now()}`, type: 'formula', left, top,
    width: opts?.width ?? 420, height: opts?.height ?? 56, z: 5,
    latex, font_size: opts?.font_size ?? 22, display: false,
    teacher_confirmed: opts?.teacher_confirmed ?? false,
  }
  currentSlide.value.elements.push(el)
  selectedId.value = el.id
}

/* 管家「插入本页」全局事件（ButlerPanel dispatch）：编辑器打开则落布当前页，否则引导先打开课件 */
function onButlerInsert(ev: Event) {
  const d = (ev as CustomEvent).detail as { latex?: string }
  if (!d?.latex) return
  if (view.value !== 'editor' || !deck.value) {
    toast.info('请先打开一个课件，管家公式会插入当前页并可继续编辑')
    return
  }
  addFormulaElement(cleanPlaceholder(d.latex), 460, 300)
  toast.success('已插入当前页（未确认态，可在属性面板继续编辑）')
}

/* ---------- C2 伴随工具层：插入总线接手（figure→画布元素 / question→题干+参考答案 / 片段·视频·外链→引用卡） ---------- */
let lastLocateFn: (() => void) | null = null
function companionRespond(reqId: string, ok: boolean, message: string, locationLabel: string, undoFn: () => void) {
  setReceipt({ ok, message, locationLabel, undoLabel: '撤销' })
  registerUndo('撤销', undoFn)
  window.dispatchEvent(new CustomEvent('tv3-companion-inserted', { detail: { reqId, handled: true } }))
}
function onCompanionInsert(ev: Event) {
  const d = (ev as CustomEvent).detail as { reqId: string; kind: string; draw?: V3DrawInsert; candidate?: CompanionCandidate } | undefined
  if (!d || d.reqId === companionSeqDone) return
  if (view.value !== 'editor' || !deck.value) {
    window.dispatchEvent(new CustomEvent('tv3-companion-inserted', { detail: { reqId: d.reqId, handled: false } }))
    return
  }
  const before = currentSlide.value.elements.map((e) => e.id)
  const slideAt = slideIdx.value
  const locate = () => { view.value = 'editor'; slideIdx.value = slideAt; selectedId.value = '' }
  const undoAdded = () => {
    for (const id of currentSlide.value.elements.filter((e) => !before.includes(e.id)).map((e) => e.id)) deleteElementById(id)
  }
  /* figure：复用既有 onDrawInsert 落布（image/formula/functionPlot/geometry 同语义） */
  if (d.kind === 'figure' && d.draw) {
    onDrawInsert(d.draw)
    const added = currentSlide.value.elements.filter((e) => !before.includes(e.id))
    if (!added.length) {
      window.dispatchEvent(new CustomEvent('tv3-companion-inserted', { detail: { reqId: d.reqId, handled: false } }))
      return
    }
    lastLocateFn = locate
    companionRespond(d.reqId, true, `已插入图形（${d.draw.type === 'image' ? '结构化图形' : d.draw.type === 'formula' ? '公式' : d.draw.type === 'geometry' ? '几何构造' : '函数图像'}，未确认态）`, `第 ${slideAt + 1} 页`, undoAdded)
    return
  }
  /* question：题干 + 参考答案小字（来源可溯） */
  if (d.kind === 'question' && d.candidate?.preview?.latex) {
    const q = d.candidate
    const stem = q.preview!.latex
    const sid = Date.now()
    currentSlide.value.elements.push(
      { id: `ecq-${sid}-s`, type: 'text', left: 70, top: 120, width: 940, height: 130, z: 5, html: renderLatex(stem), font_size: 20, teacher_confirmed: false } as V3Element,
      { id: `ecq-${sid}-a`, type: 'text', left: 70, top: 300, width: 940, height: 44, z: 5, html: `<b>参考答案：</b>见原题解析（${q.source}）`, font_size: 15, color: '#0e9488', teacher_confirmed: false } as V3Element,
    )
    lastLocateFn = locate
    companionRespond(d.reqId, true, `已插入「${q.title}」题干与参考答案（未确认态）`, `第 ${slideAt + 1} 页`, undoAdded)
    return
  }
  /* figure（伴随资源候选）：落为图形素材卡（内联 SVG 可视 + 来源 + 重开指引） */
  const figCand = d.kind === 'figure' ? d.candidate : undefined
  const figShot = figCand?.figure
  if (figCand && figShot) {
    const c = figCand
    const sid = Date.now()
    currentSlide.value.elements.push({
      id: `ecf-${sid}`, type: 'text', left: 300, top: 150, width: 560, height: 300, z: 5,
      html: `${figShot.thumb}<br><span style="color:#0e7490;font-size:13px">图形素材 · 来源：${c.source} · 「在数学绘图中继续编辑」可改构造</span>`,
      font_size: 15, teacher_confirmed: false,
    } as V3Element)
    lastLocateFn = locate
    companionRespond(d.reqId, true, `已把图形「${c.title}」插入当前页（图形素材卡，未确认态）`, `第 ${slideAt + 1} 页`, undoAdded)
    return
  }
  /* 片段 / 视频 / 外链：落为当前页引用卡文本元素 */
  if (d.candidate) {
    const c = d.candidate
    let html = ''
    if (c.kind === 'video' && c.video) {
      html = `<b>▶ 外部视频引用</b> ${c.title}<br>来源：${c.source} · ${c.video.start}–${c.video.end}（官方站外播放器）<br>播放前问：${c.video.pre}<br>播放后问：${c.video.post}<br><span style="color:#0e7490;font-size:13px">外部引用 · 不下载不转存 · 课堂播放依赖网络，建议备好 Plan B</span>`
    } else if (c.kind === 'link') {
      html = `<b>引用来源</b> ${c.title}<br>来源：${c.source}<br><span style="color:#0e7490;font-size:13px">${c.note || ''}</span>`
    } else {
      html = `<b>取用片段</b> ${c.title}<br>来源：${c.source}<br>${c.preview?.latex ? renderLatex(c.preview.latex) : ''}`
    }
    const sid = Date.now()
    currentSlide.value.elements.push({ id: `ecm-${sid}`, type: 'text', left: 70, top: 380, width: 860, height: 150, z: 5, html, font_size: 17, teacher_confirmed: false } as V3Element)
    lastLocateFn = locate
    companionRespond(d.reqId, true, `已把「${c.title}」落为当前页引用卡（未确认态）`, `第 ${slideAt + 1} 页`, undoAdded)
    return
  }
  window.dispatchEvent(new CustomEvent('tv3-companion-inserted', { detail: { reqId: d.reqId, handled: false } }))
}
let companionSeqDone = ''
function onCompanionLocate() { lastLocateFn?.() }

function addGeometry(p: FigurePresetDef) {
  if (!deck.value) return
  const el: V3Element = { id: `e${Date.now()}`, type: 'geometry', left: 720, top: 160, width: 460, height: 400, z: 3, preset_id: p.id, params: defaultParams(p), teacher_confirmed: false }
  currentSlide.value.elements.push(el)
  selectedId.value = el.id
}
function onFigDrag(ev: DragEvent, p: FigurePresetDef) {
  ev.dataTransfer?.setData('mx/preset', p.id)
}
function onKbdInsert(k: { latex: string }) {
  if (selectedEl.value?.type === 'formula') {
    propsMathField.value?.insert(k.latex)
  } else {
    addFormulaElement(cleanPlaceholder(k.latex), 480, 320)
    nextTick(() => propsMathField.value?.insert(k.latex))
  }
}

/* ---------- 公式键盘浮层（工具球收纳改版）：编辑公式时自动唤出，手动关闭后同一元素不重复弹 ---------- */
const kbdOpen = ref(false)
const kbdClosedFor = ref('')
watch(() => {
  const el = selectedEl.value
  return el?.type === 'formula' ? el.id : ''
}, (formulaId) => {
  if (formulaId) {
    if (kbdClosedFor.value !== formulaId) kbdOpen.value = true
  } else {
    kbdOpen.value = false
    kbdClosedFor.value = ''
  }
})
function closeKbd() {
  kbdOpen.value = false
  kbdClosedFor.value = (selectedEl.value as { id?: string })?.id || ''
}
function confirmElement() {
  if (selectedEl.value) selectedEl.value.teacher_confirmed = !selectedEl.value.teacher_confirmed
}
function deleteElement() {
  if (!selectedId.value) return
  deleteElementById(selectedId.value)
}
function deleteElementById(id: string) {
  const i = currentSlide.value.elements.findIndex((e) => e.id === id)
  if (i >= 0) currentSlide.value.elements.splice(i, 1)
  if (selectedId.value === id) selectedId.value = ''
}
async function saveDeck() {
  if (!deck.value) return
  if (vnextMode.value) {
    if (vnextSaving.value || !vnextEditing.value) return
    const operations = vnextPatchOperations()
    if (!operations.length) {
      toast.info('没有检测到需要保存的课件修改')
      return
    }
    vnextSaving.value = true
    try {
      const proposal = await vnextEditingHandlers.propose(operations, '教师在兼容编辑器中修改课件')
      if (!proposal) {
        toast.error('课件修改提案未通过，请检查当前版本')
        return
      }
      const applied = await vnextEditingHandlers.applyPending()
      const appliedRevision = applied && typeof applied === 'object' && typeof (applied as { revision?: unknown }).revision === 'number'
        ? (applied as { revision: number }).revision
        : null
      if (appliedRevision === null) {
        toast.error('课件修改未应用，服务端保留了原始方案')
        return
      }
      await nextTick()
      syncLegacyDeckFromVnext()
      toast.success(`课件已保存（版本 ${appliedRevision}）`)
    } finally {
      vnextSaving.value = false
    }
    return
  }
  try {
    await v3Api.decks.patch(deck.value.id, { title: deck.value.title, template_id: deck.value.template_id })
    await v3Api.decks.patchSlide(deck.value.id, currentSlide.value.id, { elements: currentSlide.value.elements })
    genStage.value = '已保存'
  } catch { /* mock */ }
}
/**
 * 导出 PPTX：提交导出任务 → 轮询 export-jobs → done 后触发浏览器下载。
 * 后端在 API 进程内异步生成并上传 MinIO（teacher-exports），
 * 前端通过 GET /export-jobs/{job_id} 获取 presign 下载地址。
 */
const exporting = ref(false)
async function exportDeck() {
  if (!deck.value || exporting.value) return
  if (vnextMode.value) {
    if (vnextExporting.value || !vnextEditing.value) return
    vnextExporting.value = true
    try {
      const entry = await vnextEditingHandlers.requestExport('pptx')
      if (!entry) {
        toast.error('PPTX 导出未提交，服务端保留了原始方案')
        return
      }
      toast.info('已提交服务端导出作业，完成后将自动下载')
      await new Promise<void>((resolve) => {
        const stop = watch(() => vnextEditing.value?.exportCapabilities.find((item) => item.format === 'pptx') ?? null, (capability) => {
          if (!capability) return
          if (capability.state === 'ready' && capability.downloadUrl) {
            const anchor = document.createElement('a')
            anchor.href = capability.downloadUrl
            anchor.download = `${deck.value?.title || '课件'}.pptx`
            document.body.appendChild(anchor)
            anchor.click()
            anchor.remove()
            stop()
            toast.success('PPTX 导出完成')
            resolve()
          } else if (capability.state === 'failed' || capability.state === 'cancelled') {
            stop()
            toast.error(capability.error || 'PPTX 导出失败，请稍后重试')
            resolve()
          }
        }, { immediate: true })
      })
    } finally {
      vnextExporting.value = false
    }
    return
  }
  exporting.value = true
  try {
    const r = await v3Api.decks.export(deck.value.id, 'pptx')
    toast.info('正在生成 PPTX，请稍候…')
    const jobId = (r.data as { job_id?: string }).job_id
    if (!jobId) throw new Error('导出任务未返回 job_id')
    // 轮询导出任务状态（最长 90s，1s 间隔）
    const deadline = Date.now() + 90_000
    while (Date.now() < deadline) {
      await new Promise((res) => setTimeout(res, 1000))
      const st = (await v3Api.decks.exportJob(jobId)).data as { status?: string; download_url?: string; error?: string }
      if (st.status === 'done' && st.download_url) {
        const a = document.createElement('a')
        a.href = st.download_url
        a.download = `${deck.value.title || '课件'}.pptx`
        document.body.appendChild(a)
        a.click()
        a.remove()
        toast.success('PPTX 导出完成')
        return
      }
      if (st.status === 'failed') {
        toast.error(st.error || 'PPTX 导出失败，请重试')
        return
      }
    }
    toast.error('导出超时，请稍后在任务中心查看')
  } catch (e: any) {
    toast.error(e?.message ? String(e.message) : '导出失败，请检查网络后重试')
  } finally {
    exporting.value = false
  }
}

/* ---------- 原图重建（P1 升级链路） ---------- */
async function loadRebuildCandidates() {
  try {
    const r = await v3Api.recognition.rebuildCandidates({ region: { x: 0, y: 0, w: 1, h: 1 }, photo_id: 'p0' })
    rebuildCandidates.value = r.data.candidates
  } catch { rebuildCandidates.value = [] }
}
function applyRebuild(anchor: Extract<V3Element, { type: 'anchorPhoto' }>, c: V3FigureRebuildCandidate) {
  if (!c.passed_validation || !c.preset_id) return
  const def = FIGURE_PRESETS.find((p) => p.id === c.preset_id)
  if (!def) return
  const near = currentSlide.value.elements.find((e) => e.type === 'geometry' && Math.abs(e.left - anchor.left) < 700 && e.top > anchor.top)
  if (near && near.type === 'geometry') {
    near.preset_id = c.preset_id
    near.params = { ...c.params }
    selectedId.value = near.id
  } else {
    const el: V3Element = { id: `e${Date.now()}`, type: 'geometry', left: Math.min(900, anchor.left + anchor.width + 30), top: anchor.top, width: 420, height: 320, z: 3, preset_id: c.preset_id, params: { ...c.params }, teacher_confirmed: false }
    currentSlide.value.elements.push(el)
    selectedId.value = el.id
  }
  anchor.upgrade_state = 'rebuilt'
  anchor.upgrade_target = c.preset_id
}

/* ---------- 绘图工作台（P1：函数绘图 / 自由画布 / 手写公式） ---------- */
const drawOpen = ref(false)
const drawReopen = ref<DrawReopen | null>(null)
const photoOpen = ref(false)

function onPhotoInsert(p: { kind: 'image' | 'formula' | 'anchorPhoto'; src?: string; latex?: string }) {
  if (!deck.value) return
  if (p.kind === 'formula') {
    addFormulaElement(cleanPlaceholder(p.latex || ''), 480, 320)
    return
  }
  const src = p.src || ''
  const el: V3Element = p.kind === 'image'
    ? { id: `e${Date.now()}`, type: 'image', left: 300, top: 180, width: 560, height: 360, z: 4, src, alt: '拍照素材（已扫描增强）' }
    : { id: `e${Date.now()}`, type: 'anchorPhoto', left: 300, top: 180, width: 560, height: 360, z: 4, src, upgrade_state: 'none' }
  currentSlide.value.elements.push(el)
  selectedId.value = el.id
}

function openDrawBoard() {
  drawReopen.value = null
  drawOpen.value = true
}
function reopenDraw(el: Extract<V3Element, { type: 'image' }>) {
  if (!el.draw_recipe?.records?.length) return
  const isGeom = el.draw_recipe.records[0]?.kind === 'geomdoc'
  drawReopen.value = { mode: isGeom ? 'geom' : 'free', records: el.draw_recipe.records, elementId: el.id }
  drawOpen.value = true
}
function onDrawInsert(p: V3DrawInsert, elementId?: string) {
  if (elementId) {
    const el = currentSlide.value.elements.find((e) => e.id === elementId)
    if (!el) return
    if (p.type === 'image' && el.type === 'image') {
      el.src = p.src
      el.draw_recipe = { records: p.records }
      el.height = Math.min(620, Math.round(el.width * p.aspect))
    } else if (p.type === 'formula' && el.type === 'formula') {
      el.latex = p.latex
    } else if (p.type === 'functionPlot' && el.type === 'functionPlot') {
      el.expr = p.expr
      el.params = p.params
      el.domain = p.domain
    }
    selectedId.value = el.id
    return
  }
  if (p.type === 'functionPlot') {
    const el: V3Element = { id: `e${Date.now()}`, type: 'functionPlot', left: 380, top: 110, width: 560, height: 430, z: 3, expr: p.expr, params: p.params, domain: p.domain, live_sliders: true, teacher_confirmed: false }
    currentSlide.value.elements.push(el)
    selectedId.value = el.id
  } else if (p.type === 'formula') {
    addFormulaElement(p.latex, 470, 300)
  } else if (p.type === 'geometry') {
    /* B7 构图导演产物：结构化几何预设（GeoFigure 渲染，参数可继续调） */
    const el: V3Element = { id: `e${Date.now()}`, type: 'geometry', left: 300, top: 90, width: 680, height: 540, z: 3, preset_id: p.preset_id, params: p.params, teacher_confirmed: false }
    currentSlide.value.elements.push(el)
    selectedId.value = el.id
  } else {
    const w = 470
    const h = Math.min(620, Math.round(w * p.aspect))
    const el: V3Element = { id: `e${Date.now()}`, type: 'image', left: 400, top: Math.max(50, Math.round(360 - h / 2)), width: w, height: h, z: 3, src: p.src, alt: '绘图工作台图形', draw_recipe: { records: p.records }, teacher_confirmed: false }
    currentSlide.value.elements.push(el)
    selectedId.value = el.id
  }
}

/* ---------- AI 元素级建议（R5） ---------- */
async function runAiElement() {
  if (!deck.value) return
  aiDiffs.value = []
  try {
    await v3Api.generation.aiElement(deck.value.id, { slide_id: currentSlide.value.id, hint: '结合本页考点优化' }, (event, data) => {
      if (event === 'suggest') genStage.value = data.note
      if (event === 'diff') aiDiffs.value = data.diffs
    })
  } catch { /* mock */ }
}
function adoptDiff(i: number) {
  const d = aiDiffs.value[i]
  if (!d) return
  if (d.op === 'add' && d.after) {
    const el = JSON.parse(JSON.stringify(d.after)) as V3Element
    currentSlide.value.elements.push(el)
    selectedId.value = el.id
  } else if (d.op === 'remove' && d.element_id) {
    const idx = currentSlide.value.elements.findIndex((e) => e.id === d.element_id)
    if (idx >= 0) currentSlide.value.elements.splice(idx, 1)
  }
  aiDiffs.value.splice(i, 1)
}

/* ---------- 键盘：Delete 删除选中元素 ---------- */
function onKeydown(ev: KeyboardEvent) {
  if (view.value !== 'editor' || presenting.value) return
  const tag = (ev.target as HTMLElement)?.tagName
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || tag === 'MATH-FIELD' || (ev.target as HTMLElement)?.isContentEditable) return
  if (ev.key === 'Delete' || ev.key === 'Backspace') {
    if (selectedId.value) { ev.preventDefault(); deleteElement() }
  }
  if (ev.key === 'Escape') selectedId.value = ''
}
onMounted(() => {
  window.addEventListener('keydown', onKeydown)
  window.addEventListener('tv3-companion-insert', onCompanionInsert as EventListener)
  window.addEventListener('tv3-companion-locate', onCompanionLocate as EventListener)
})
onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKeydown)
  window.removeEventListener('tv3-companion-insert', onCompanionInsert as EventListener)
  window.removeEventListener('tv3-companion-locate', onCompanionLocate as EventListener)
  window.removeEventListener('tv3-butler-insert', onButlerInsert as EventListener)
  /* 生成任务后台化：离开页面不中断 SSE——生成继续在后端跑并落库（done 落 decks 行），
     回列表页可见，不会「一离开就没了」。只有用户显式取消时才 abort。 */
})
</script>

<style scoped>
/* ===== C1 快捷入口三卡 ===== */
.tv3-quickrow { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin-bottom: 14px; }
.tv3-quick {
  display: flex; flex-direction: column; align-items: flex-start; gap: 3px; text-align: left;
  background: #fff; border: 1px solid var(--tv3-line); border-radius: 12px; padding: 12px 14px; cursor: pointer;
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
}
.tv3-quick:hover { border-color: var(--tv3-gold); box-shadow: 0 4px 14px rgba(6, 182, 212, 0.12); }
.tv3-quick__icon { font-size: 18px; }
.tv3-quick__name { font-size: 13.5px; font-weight: 700; color: var(--tv3-ink); }
.tv3-quick__note { font-size: 11.5px; color: var(--tv3-ink3); }

/* ===== C1 大纲门环节语义标签 ===== */
.tv3-gate__kind {
  flex-shrink: 0; font-size: 10.5px; font-weight: 700; padding: 2px 8px; border-radius: 999px;
  border: 1px solid var(--tv3-line); color: var(--tv3-ink3); background: #fff; min-width: 34px; text-align: center;
}
.tv3-gate__kind[data-kind='cover'] { color: #3730a3; border-color: #3730a3; }
.tv3-gate__kind[data-kind='definition'] { color: #4f46e5; border-color: #a5b4fc; background: #eef4fb; }
.tv3-gate__kind[data-kind='derivation'] { color: #6d28d9; border-color: #d8c9f5; background: #f6f2fd; }
.tv3-gate__kind[data-kind='example'] { color: #b45309; border-color: #ecd3a1; background: #fdf6e8; }
.tv3-gate__kind[data-kind='variation'] { color: #0e9488; border-color: #9fd8d2; background: #eefaf8; }
.tv3-gate__kind[data-kind='summary'] { color: #3730a3; border-color: #c9d7f2; background: #f2f6fc; }
.tv3-gate__kind[data-kind='review'] { color: #b1382c; border-color: #eec7c2; background: #fdf1ef; }

/* ===== C1.1 大纲门要求回应单 + 调整指令 ===== */
.tv3-gate__reqs {
  border: 1px solid #c9d7f2; border-radius: 10px; background: #f6f9ff;
  padding: 8px 12px; margin-bottom: 10px; display: flex; flex-direction: column; gap: 6px;
}
.tv3-gate__reqshead { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; font-size: 12.5px; font-weight: 700; color: var(--tv3-ink); }
.tv3-gate__req { display: flex; align-items: center; gap: 8px; }
.tv3-gate__adjust { display: flex; gap: 8px; margin-top: 12px; }

/* ===== C1 教案直通材料横幅 ===== */
.tv3-brief__docnote {
  font-size: 12px; color: #0e7490; background: var(--tv3-gold-soft, #fdf8ec); border: 1px solid #ecd3a1;
  border-radius: 8px; padding: 8px 10px; margin-bottom: 10px; line-height: 1.6;
}
.tv3-newgrid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; }
.tv3-photo-drop {
  border: 2px dashed var(--tv3-line); border-radius: var(--tv3-radius-lg);
  min-height: 150px; display: flex; align-items: center; justify-content: center; gap: 10px;
  padding: 16px; cursor: pointer; background: #fbfcfe; transition: all 0.15s ease;
}
.tv3-photo-drop.is-over { border-color: var(--tv3-gold); background: var(--tv3-gold-soft); }
.tv3-photo-thumb { height: 120px; border-radius: 8px; border: 1px solid var(--tv3-line); object-fit: contain; }
.tv3-photo-add {
  width: 120px; height: 120px; border-radius: 8px; border: 1.5px dashed var(--tv3-line);
  display: grid; place-items: center; font-size: 26px; color: var(--tv3-ink3);
}
.tv3-recog { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 8px; }
.tv3-recog__block {
  border: 1px solid var(--tv3-line); border-radius: 10px; padding: 10px 12px; background: #fff;
  display: flex; flex-direction: column; gap: 6px;
}
.tv3-recog__latex { font-size: 15px; overflow-x: auto; }
.tv3-recog__text { font-size: 12.5px; color: var(--tv3-ink2); line-height: 1.5; }
.tv3-recog__conf { font-size: 10.5px; color: var(--tv3-ink3); font-family: var(--tv3-font-num); }
.tv3-editor__title-input {
  border: 1px solid transparent; border-radius: 8px; font-size: 14.5px; font-weight: 700;
  padding: 5px 10px; width: 320px; background: transparent; color: var(--tv3-ink); outline: none;
}
.tv3-editor__title-input:hover, .tv3-editor__title-input:focus { border-color: var(--tv3-line); background: #fff; }
.tv3-editor__hint { position: absolute; bottom: 8px; left: 50%; transform: translateX(-50%); font-size: 11px; color: var(--tv3-ink3); white-space: nowrap; }
.tv3-editor__docktabs { display: flex; align-items: center; gap: 12px; padding: 8px 12px 0; }
.tv3-editor__dock { height: 216px; }
.tv3-outline__del {
  position: absolute; right: 3px; top: 3px; z-index: 5; width: 18px; height: 18px;
  border-radius: 50%; border: none; background: rgba(220, 38, 70, 0.85); color: #fff;
  font-size: 11px; cursor: pointer; display: none; place-items: center; line-height: 1;
}
.tv3-outline__slide:hover .tv3-outline__del { display: grid; }
.tv3-props__empty { text-align: center; color: var(--tv3-ink3); font-size: 12.5px; padding: 46px 18px; line-height: 1.7; }
.tv3-props__head { display: flex; align-items: center; gap: 8px; padding: 12px 14px; border-bottom: 1px solid var(--tv3-line2); }
.tv3-editor__props > * { padding-left: 14px; padding-right: 14px; }
.tv3-editor__props .tv3-form-label, .tv3-editor__props .mx-slider, .tv3-editor__props .tv3-props__geom { padding-left: 0; padding-right: 0; }
.tv3-props__geom { margin-top: 16px; padding-top: 12px; border-top: 1px dashed var(--tv3-line); }
.tv3-props__grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 6px; }
.tv3-props__grid label { font-size: 11px; color: var(--tv3-ink3); display: flex; flex-direction: column; gap: 3px; }
.tv3-props__aidiff { margin-top: 18px; padding: 12px 14px; border-radius: 10px; background: var(--tv3-ai-soft); border: 1px solid var(--tv3-ai-border); }
.tv3-aidiff__item { background: #fff; border-radius: 8px; padding: 8px 10px; margin-top: 6px; }
.tv3-aidiff__actions { display: flex; gap: 6px; margin-top: 6px; }
.tv3-present {
  position: fixed; inset: 0; z-index: 200; background: rgba(7, 26, 50, 0.96);
  display: flex; flex-direction: column;
}
.tv3-present__bar {
  display: flex; align-items: center; gap: 12px; padding: 10px 20px; color: #fff; font-size: 13.5px;
}
.tv3-present__stage { flex: 1; display: grid; place-items: center; padding-bottom: 20px; }
.tv3-editor__props { padding-top: 0; }
.tv3-editor__props > .tv3-props__head { margin: 0 -14px; }

/* 面板宽度拖拽手柄：大纲贴右边、属性面板贴左边 */
.tv3-editor__outline, .tv3-editor__props { position: relative; }
.tv3-editor__outline > .tv3-resizer { position: absolute; top: 0; right: 0; bottom: 0; width: 7px; cursor: col-resize; z-index: 6; padding: 0; }
.tv3-editor__props > .tv3-resizer { position: absolute; top: 0; left: 0; bottom: 0; width: 7px; cursor: col-resize; z-index: 6; padding: 0; }
.tv3-resizer::after {
  content: ''; position: absolute; top: 0; bottom: 0; left: 3px; width: 1.5px;
  background: transparent; transition: background 0.15s ease;
}
.tv3-resizer:hover::after { background: var(--tv3-gold); }
/* ---------- 公式键盘浮层（工具球收纳改版，套备小研卡片样式） ---------- */
.ws-kbdfloat {
  position: fixed; left: 18px; bottom: 18px; z-index: 60; width: 360px;
  background: #fff; border: 1px solid var(--tv3-line); border-radius: 16px;
  box-shadow: 0 16px 40px -8px rgba(15, 23, 42, 0.18); padding: 10px 12px 6px;
}
.ws-kbdfloat__head { display: flex; align-items: center; gap: 8px; margin-bottom: 4px; }
.ws-kbdfloat__head b { font-size: 13px; color: var(--tv3-ink); }
.ws-kbdfloat__head span { flex: 1; font-size: 11px; color: var(--tv3-ink3); }
.ws-kbdfloat__head button {
  width: 24px; height: 24px; border-radius: 7px; border: none; background: none;
  color: var(--tv3-ink3); cursor: pointer; font-size: 14px;
}
.ws-kbdfloat__head button:hover { background: var(--tv3-line2); color: var(--tv3-ink); }
.tv3-editor { position: relative; }
.tv3-vnext-readonly { position: absolute; right: 14px; bottom: 12px; z-index: 5; pointer-events: none; }
.tv3-vnext-readonly__panel { display: grid; gap: var(--ma-space-1); max-width: 360px; padding: var(--ma-space-2) var(--ma-space-3); border: 1px solid var(--ma-surface-border); border-radius: var(--ma-radius-md); background: color-mix(in srgb, var(--ma-surface-default) 92%, transparent); box-shadow: var(--ma-elevation-sm); color: var(--ma-text-primary); text-align: right; font-size: 11px; }
.tv3-vnext-readonly__panel span { color: var(--ma-text-body); }
.tv3-vnext-readonly__panel button { justify-self: center; }
</style>
