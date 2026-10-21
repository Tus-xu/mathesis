<template>
  <div class="ws-root">
    <!-- ================= 首页：把想法变成课件 ================= -->
    <div v-if="ws.screen === 'home'" data-testid="tv3-slides-list">
      <div class="ws-topbar">
        <span class="ws-badge ws-badge--warn">AI 只出草稿 · 教师确认后生效</span>
      </div>

      <div class="ws-hero">
        <div class="ws-hero-pills">
          <span class="ws-pill ws-pill--brand">课件工坊</span>
        </div>
        <p class="ws-hello">{{ hello }}</p>
        <h1 class="ws-h1">把想法变成<span class="ws-grad">课件</span></h1>
        <p class="ws-sub">描述这节课怎么上，AI 先出可编辑的大纲草稿，确认后再逐页生成</p>

        <div class="ws-input-glow">
          <div class="ws-input-card">
            <textarea
              v-model="ws.heroText" rows="2"
              placeholder="例如：下周三高二(5)班《双曲线及其标准方程》第 1 课时，用拉线实验引入，例题选高考真题"
              data-testid="tv3-brief-input"
              @keydown.enter.exact.prevent="ws.submitHero()"
            />
            <div class="ws-input-row">
              <button class="ws-icon-btn" title="附材料（教案 / 讲义 / 旧课件，原型记录文件名）" @click="docInput?.click()">📎</button>
              <input ref="docInput" type="file" accept=".pdf,.doc,.docx,.ppt,.pptx,.txt" multiple hidden @change="ws.onDocFiles($event)">
              <div class="ws-mode-chips">
                <button class="ws-chip is-primary" data-testid="tv3-entry-topic" title="用左侧文本生成大纲草稿" @click="ws.submitHero()">✦ 生成新课件</button>
                <button class="ws-chip" data-testid="tv3-entry-photo" @click="ws.openNew('photo')">📷 拍照出课件</button>
                <button class="ws-chip" data-testid="tv3-entry-plan" @click="ws.openNew('plan')">📄 教案直通</button>
                <button class="ws-chip" data-testid="tv3-entry-adapt" @click="ws.adaptFromDeck()">📚 课件改编</button>
              </div>
              <button class="ws-send" title="生成大纲草稿" @click="ws.submitHero()">➤</button>
            </div>
            <div v-if="ws.briefCtx.docs.length" class="ws-docs" data-testid="tv3-plan-doc-note">
              📎 {{ ws.briefCtx.docs.join('、') }}（已记录文件名，内容解析在生成时进行）
            </div>
          </div>
        </div>
        <div class="ws-hint">支持 PDF / DOC / PPT / TXT 材料上传 · 材料与章节优先 · 大纲确认后才生成</div>

      </div>

      <section class="ws-section">
        <h2 class="ws-h2">为你推荐</h2>
        <button class="ws-reco" @click="ws.heroText = '备《双曲线及其标准方程》第 1 课时，生成 6 页课件大纲'">
          <span class="ws-reco-ico" style="background: var(--ailp-primary-50); color: var(--ailp-primary-600)">📖</span>
          备《双曲线及其标准方程》第 1 课时，生成 6 页课件大纲
        </button>
        <button class="ws-reco" data-testid="tv3-entry-photo" @click="ws.openNew('photo')">
          <span class="ws-reco-ico" style="background: rgba(139,92,246,0.1); color: #7c3aed">📷</span>
          把 3 张圆锥曲线题目拍照整理成例题讲解课件
        </button>
        <button class="ws-reco" data-testid="tv3-entry-plan" @click="ws.openNew('plan')">
          <span class="ws-reco-ico" style="background: rgba(6,182,212,0.1); color: var(--ailp-accent-600)">📄</span>
          从《椭圆及其标准方程》教案直通生成课件
        </button>
      </section>

      <section class="ws-section">
        <div class="ws-sechead">
          <h2 class="ws-h2">我的课件</h2><span class="ws-seccount">{{ ws.decks.length }} 份</span>
        </div>
        <div class="ws-deckgrid">
          <div v-for="d in ws.decks" :key="d.id" class="ws-deckcard tv3-qcard" @click="ws.openDeck(d.id)">
            <div class="ws-deckcard-top">
              <span class="ws-pill" :class="d.source === 'photo' ? 'ws-pill--warn' : d.source === 'lesson-push' ? 'ws-pill--ok' : 'ws-pill--brand'">{{ ws.sourceLabel(d.source) }}</span>
              <span class="ws-deckcard-time">{{ d.updated_at }}</span>
            </div>
            <h3 class="ws-deckcard-title">{{ d.title }}</h3>
            <p class="ws-deckcard-meta">{{ d.class_name }} · {{ d.slide_count }} 页 · {{ ws.templateName(d.template_id) }}</p>
          </div>
        </div>
      </section>

      <section v-if="ws.todaySchedule.length" class="ws-section" data-testid="tv3-today-lessons">
        <div class="ws-sechead">
          <h2 class="ws-h2">今日授课</h2>
          <span class="ws-seccount">与「今日工作台」同步</span>
        </div>
        <div class="ws-todaycard">
          <div v-for="(s, i) in ws.todaySchedule" :key="i" class="ws-todayrow" :data-testid="`tv3-today-row-${i}`">
            <b class="ws-todaytime">{{ s.time }}</b>
            <span class="ws-pill" :class="s.status === 'done' ? 'ws-pill--ok' : s.status === 'next' ? 'ws-pill--brand' : 'ws-pill--warn'">
              {{ s.status === 'done' ? '已完成' : s.status === 'next' ? '下一节' : '待备' }}
            </span>
            <span class="ws-todaytopic">{{ s.class_name }} · {{ s.topic }}</span>
            <span v-for="m in s.missing || []" :key="m" class="ws-pill ws-pill--warn">⚠ {{ m }}</span>
            <button class="ws-mini ws-mini--primary" :data-testid="`tv3-today-goto-${i}`" @click="ws.prefillLesson(s)">去备</button>
          </div>
        </div>
      </section>
    </div>

    <!-- ================= 向导（需求 → 大纲确认 → 选择模板 → 生成） ================= -->
    <div v-else-if="ws.screen !== 'editor'" data-testid="tv3-slides-new">
      <div class="ws-topbar">
        <button class="ws-icon-btn" @click="ws.openHome()">←</button>
        <b class="ws-topbar-title">{{ ws.screen === 'photo' ? '拍照出课件' : ws.screen === 'plan' ? '教案直通课件' : ws.screen === 'outline' ? 'AI 备课 · 确认大纲' : ws.screen === 'template' ? '选择模板' : '生成中' }}</b>
        <span class="ws-topbar-sub">{{ ws.screenTitleSub }}</span>
        <span class="ws-badge ws-badge--warn ws-badge--right">AI 只出草稿 · 教师确认后生效</span>
      </div>

      <div class="ws-steps">
        <span class="ws-step is-done"><i>✓</i>需求</span>
        <span class="ws-step-line" />
        <span class="ws-step" :class="{ 'is-active': ws.screen === 'outline' || ws.screen === 'plan', 'is-done': ['template', 'generating'].includes(ws.screen) }"><i>{{ ['template', 'generating'].includes(ws.screen) ? '✓' : 2 }}</i>大纲确认</span>
        <span class="ws-step-line" />
        <span class="ws-step" :class="{ 'is-active': ws.screen === 'template', 'is-done': ws.screen === 'generating' }"><i>{{ ws.screen === 'generating' ? '✓' : 3 }}</i>选择模板</span>
        <span class="ws-step-line" />
        <span class="ws-step" :class="{ 'is-active': ws.screen === 'generating' }"><i>4</i>生成</span>
      </div>

      <div class="ws-wiz">
        <!-- ========== 拍照出课件：照片 → 识别确认 → 生成方式 ========== -->
        <template v-if="ws.screen === 'photo'">
          <section class="ws-block">
            <div class="ws-blockhead"><i class="ws-stepdot">1</i><b>题目照片</b><span>学生作业 / 教辅 / 黑板</span></div>
            <div class="ws-photos">
              <div v-for="(p, i) in ws.photos" :key="i" class="ws-photochip">
                <img :src="p" alt="原题照片">
                <button class="ws-photo-x" @click="ws.removePhoto(i)">×</button>
                <span class="ws-photo-name">{{ ws.recogCards[i]?.photo_id || `photo_${String(i + 1).padStart(2, '0')}` }}</span>
              </div>
              <button class="ws-photoadd" @click="fileInput?.click()">＋<span>继续添加</span></button>
              <input ref="fileInput" type="file" accept="image/*" multiple hidden @change="ws.onFileChange($event)">
            </div>
          </section>

          <section class="ws-block">
            <div class="ws-blockhead"><i class="ws-stepdot">2</i><b>识别结果确认</b><span>点击文字可修改；低置信度需重点校对</span></div>
            <div v-if="ws.recogLoading" class="ws-loading"><span class="ws-spinner" />演示识别中…</div>
            <template v-else>
              <article v-for="(c, i) in ws.recogCards" :key="c.photo_id" class="ws-recogcard" :class="{ 'is-warn': c.warn }">
                <div class="ws-recoghead">
                  <span>{{ c.photo_id }}</span>
                  <span class="ws-pill" :class="c.warn ? 'ws-pill--warn' : 'ws-pill--ok'">置信度 {{ Math.round(c.confidence * 100) }}%{{ c.warn ? ' · 建议校对' : '' }}</span>
                </div>
                <div class="ws-recogbody">
                  <p class="ws-recoglabel">题干（可编辑）</p>
                  <textarea v-model="c.text" rows="3" class="ws-recogtext" :data-testid="`tv3-recog-text-${i}`" />
                  <div class="ws-recogkps">
                    知识点：<span v-for="k in c.kps" :key="k" class="ws-pill ws-pill--brand">{{ k }}</span>
                  </div>
                </div>
              </article>
              <p class="ws-note">⚠ {{ ws.recogNote }}</p>
            </template>
          </section>

          <section class="ws-block">
            <div class="ws-blockhead"><i class="ws-stepdot">3</i><b>生成方式</b><span>字号档与边注在下一步「选择模板」统一调整</span></div>
            <div class="ws-segcard">
              <div class="ws-seghead"><b>识别范围</b><span>适用：例题精讲</span></div>
              <div class="ws-seg">
                <button v-for="c in ws.scopeCards" :key="c.value" :class="{ 'is-active': ws.form.scope === c.value }" @click="ws.form.scope = c.value">{{ c.name }}</button>
              </div>
            </div>
            <div class="ws-segcard">
              <div class="ws-seghead"><b>生成模式</b><span>适用：自学 / 复习</span></div>
              <div class="ws-seg">
                <button v-for="c in ws.modeCards" :key="c.value" :class="{ 'is-active': ws.form.mode === c.value }" @click="ws.form.mode = c.value">{{ c.name }}</button>
              </div>
            </div>
          </section>
          <div class="ws-bottom">
            <span>已选 {{ ws.photos.length }} 张 · 将生成 3-4 页讲解内容</span>
            <button class="ws-btn-primary" :disabled="!ws.photos.length" data-testid="tv3-new-next" @click="ws.nextPhoto()">下一步：选择模板 →</button>
          </div>
        </template>

        <!-- ========== 教案直通：选择教案 → 环节→页数映射 ========== -->
        <template v-else-if="ws.screen === 'plan'">
          <section class="ws-block">
            <div class="ws-blockhead"><i class="ws-stepdot">1</i><b>选择教案</b><span>仅显示已确认的教案</span></div>
            <div v-if="ws.briefCtx.docs.length" class="ws-note" data-testid="tv3-plan-doc-note">
              📎 外部材料《{{ ws.briefCtx.docs.join('》《') }}》已记录：内容解析在生成时进行，本次沿用所选教案的环节结构。
            </div>
            <div
              v-for="p in ws.plans" :key="p.id"
              class="ws-plancard" :class="{ 'is-selected': ws.form.plan_id === p.id }"
              role="radio" :aria-checked="ws.form.plan_id === p.id"
              @click="ws.selectPlan(p.id)"
            >
              <span class="ws-radio"><i v-if="ws.form.plan_id === p.id" /></span>
              <span class="ws-planico">📄</span>
              <div class="ws-planbody">
                <b>{{ p.topic }}</b>
                <span>{{ p.lesson_type }} · {{ p.section_count }} 个环节 · {{ p.confirmed ? '已确认' : '草稿' }}</span>
              </div>
            </div>
          </section>

          <section v-if="ws.planMap.length" class="ws-block">
            <div class="ws-blockhead"><i class="ws-stepdot">2</i><b>生成映射</b><span>AI 建议每环节生成的页数，可调整</span></div>
            <div class="ws-mapcard">
              <div class="ws-mapsummary">
                <b>{{ ws.planDetail?.topic }}</b>
                <span>{{ ws.planMap.length }} 个环节 → {{ ws.planMap.reduce((s: number, r: any) => s + r.pages, 0) }} 页课件</span>
              </div>
              <div v-for="(r, i) in ws.planMap" :key="i" class="ws-maprow">
                <b class="ws-mapname">{{ r.name }}</b>
                <span class="ws-mapdesc">{{ r.minutes }} 分钟 · {{ r.pages }} 页</span>
                <div class="ws-stepper">
                  <button :data-testid="`tv3-map-minus-${i}`" @click="r.pages = Math.max(1, r.pages - 1)">−</button>
                  <span>{{ r.pages }}</span>
                  <button :data-testid="`tv3-map-plus-${i}`" @click="r.pages = Math.min(3, r.pages + 1)">＋</button>
                </div>
              </div>
              <div class="ws-mapfoot"><span>映射可调整；确认后进入大纲逐页编辑</span></div>
            </div>
          </section>
          <div class="ws-bottom">
            <span>下一步将展示 AI 生成的逐页大纲，可直接编辑</span>
            <button class="ws-btn-primary" :disabled="!ws.form.plan_id || !ws.planMap.length" data-testid="tv3-new-next" @click="ws.nextPlan()">生成大纲草稿 →</button>
          </div>
        </template>

        <!-- ========== 大纲确认与编辑（Gamma 式可编辑卡片） ========== -->
        <template v-else-if="ws.screen === 'outline'">
          <div class="ws-outline" data-testid="tv3-outline-gate">
            <div v-if="ws.outlineLoading" class="ws-loading"><span class="ws-spinner" />正在按你的要求编译大纲草稿…</div>
            <template v-else>
              <div class="ws-stats">
                <span class="ws-statchip">共 {{ ws.gateOutline.length }} 页</span>
                <span class="ws-statchip">建议时长 {{ ws.gateOutline.reduce((s: number, o: any) => s + (o.minutes || 0), 0) }} 分钟</span>
                <span class="ws-statchip" :class="ws.gateMatched ? 'is-ok' : 'is-warn'" :title="ws.gateNote">{{ ws.gateMatched ? '已匹配内置内容源' : '未匹配内置内容源' }}</span>
                <span v-if="ws.briefCtx.course_type !== '新授课'" class="ws-statchip is-brand" data-testid="tv3-gate-coursetype">{{ ws.briefCtx.course_type }}结构</span>
                <span v-if="ws.briefCtx.chapter" class="ws-statchip is-ok" data-testid="tv3-gate-chapter">📖 {{ ws.chapterShort(ws.briefCtx.chapter) }}</span>
                <span v-if="ws.briefCtx.docs.length" class="ws-statchip is-warn">📄 {{ ws.briefCtx.docs[0] }}</span>
                <button class="ws-btn-ghost" data-testid="tv3-gate-regen" @click="ws.prepareOutline()">↻ 重新生成大纲</button>
              </div>

              <div class="ws-adjustbar">
                <span class="ws-adjustico">✦</span>
                <input
                  v-model="ws.gateAdjust" placeholder="用一句话调整大纲，例如：去掉复习回顾，加一道当堂检测"
                  data-testid="tv3-gate-adjust-input" @keydown.enter="ws.regenWithAdjust"
                >
                <button v-for="q in ['增加互动', '例题加难', '压缩到 5 页']" :key="q" class="ws-chip" @click="ws.quickAdjust(q)">{{ q }}</button>
                <button class="ws-send ws-send--sm" data-testid="tv3-gate-adjust" @click="ws.regenWithAdjust">➤</button>
              </div>

              <div class="ws-outcards" data-testid="tv3-gate-pages">
                <article v-for="(o, i) in ws.gateOutline" :key="i" class="ws-outcard" :data-testid="`tv3-gate-row-${i}`">
                  <span class="ws-outnum">{{ i + 1 }}</span>
                  <div class="ws-outbody">
                    <div class="ws-outhead">
                      <select v-model="o.kind" class="ws-kindtag" :data-kind="o.kind" :data-testid="`tv3-gate-kindtag-${i}`">
                        <option value="cover">封面</option><option value="review">复习</option><option value="definition">概念</option>
                        <option value="derivation">推导</option><option value="example">例题</option><option value="variation">练习</option>
                        <option value="summary">小结</option><option value="blank">板书</option>
                      </select>
                      <input v-model="o.title" class="ws-outtitle" :data-testid="`tv3-gate-title-${i}`">
                      <span class="ws-pill ws-pill--muted"><i>⏱</i> 时长 {{ o.minutes || 6 }} 分钟</span>
                    </div>
                    <p class="ws-outdesc">{{ ws.kindLabel(o.kind) }}页 · 建议时长 {{ o.minutes || 6 }} 分钟 · 生成后可逐元素编辑</p>
                  </div>
                  <div class="ws-outacts">
                    <button title="上移" :data-testid="`tv3-gate-up-${i}`" :disabled="i === 0" @click="ws.moveOutlinePage(i, -1)">↑</button>
                    <button title="下移" :data-testid="`tv3-gate-down-${i}`" :disabled="i === ws.gateOutline.length - 1" @click="ws.moveOutlinePage(i, 1)">↓</button>
                    <button class="is-danger" :disabled="ws.gateOutline.length <= 2" :data-testid="`tv3-gate-del-${i}`" @click="ws.delOutlinePage(i)">🗑</button>
                  </div>
                </article>
              </div>
              <button class="ws-addpage" data-testid="tv3-gate-add" @click="ws.addOutlinePage()">＋ 添加一页</button>

              <div v-if="ws.gateReqs.length" class="ws-reqs" data-testid="tv3-gate-reqs">
                <div class="ws-reqshead">老师的要求（{{ ws.gateReqs.length }}）<span>AI 已按你的要求编排到对应页面</span></div>
                <div v-for="r in ws.gateReqs" :key="r.id" class="ws-req" :data-testid="`tv3-gate-req-${r.id}`">
                  <span class="ws-pill" :class="r.status === 'applied' ? 'ws-pill--ok' : 'ws-pill--warn'">{{ r.status === 'applied' ? '✓ 已排入' : '⚠ 未支持' }}</span>
                  <b>{{ r.text }}</b>
                  <span class="ws-reqnote">{{ r.status === 'applied' ? (r.pages?.length ? `→ 第 ${r.pages.map((p: number) => p + 1).join('、')} 页` : (r.note || '已体现在结构')) : (r.note || '') }}</span>
                </div>
              </div>
              <div v-if="!ws.briefCtx.chapter && !ws.briefCtx.docs.length && ws.gateMatched" class="ws-note is-amber" data-testid="tv3-gate-bare-hint">
                💡 提高匹配度：回首页补选教材章节，或附上教案/讲义材料——章节与母本对齐后内容更可用。
              </div>
              <div v-if="!ws.gateMatched && ws.gateNote" class="ws-note is-red">{{ ws.gateNote }}</div>

              <div class="ws-bottom ws-bottom--static">
                <button class="ws-btn-ghost" @click="ws.openHome()">← 上一步（修改需求）</button>
                <button class="ws-btn-primary" :disabled="ws.gateOutline.length < 2" data-testid="tv3-gate-confirm" @click="ws.confirmOutline()">
                  下一步：选择模板（{{ ws.gateOutline.length }} 页）
                </button>
              </div>
            </template>
          </div>
        </template>

        <!-- ========== 选择模板：统一画廊 + 实时预览 ========== -->
        <template v-else-if="ws.screen === 'template'">
          <div class="ws-tplwrap">
            <div class="ws-tplgallery">
              <div class="ws-tplchips">
                <button v-for="f in ['全部', '学术风', '手写板书', '简约', '公开课']" :key="f" class="ws-chip" :class="{ 'is-primary': ws.tplFilter === f }" @click="ws.tplFilter = f">{{ f }}</button>
              </div>
              <div class="ws-tplgrid">
                <article
                  v-for="t in ws.tplFiltered" :key="t.id"
                  class="ws-tplcard" :class="{ 'is-selected': ws.form.template_id === t.id }"
                  role="button" tabindex="0" :aria-pressed="ws.form.template_id === t.id"
                  :data-testid="`tv3-tpl-${t.id}`" @click="ws.form.template_id = t.id"
                >
                  <span v-if="ws.form.template_id === t.id" class="ws-tplcheck">✓</span>
                  <div class="ws-tplpreview">
                    <!-- IFC-003：服务端真实缩略图优先；缺省/加载失败回落 swatch 自渲染骨架（绝不破图） -->
                    <img
                      v-if="t.thumb && !thumbFail[t.id]" :src="t.thumb" class="ws-tplthumb" loading="lazy"
                      :alt="`${t.name} 模板缩略图`" @error="thumbFail[t.id] = true"
                    >
                    <template v-else>
                      <div class="ws-tplprev-cover" :style="{ background: swatchOf(t).bg, color: swatchOf(t).light ? '#fff' : '#e2e8f0' }">
                        <i class="bar w-1/3" /><i class="bar big" /><i class="bar w-1/2" />
                      </div>
                      <div class="ws-tplprev-page">
                        <i class="bar big" :style="{ background: swatchOf(t).primary }" />
                        <i class="bar" style="background: rgba(100,116,139,0.25)" /><i class="bar" style="background: rgba(100,116,139,0.18)" />
                      </div>
                    </template>
                  </div>
                  <h3>{{ t.name }}</h3>
                  <div class="ws-tplmeta">
                    <span class="ws-pill ws-pill--muted">适用 {{ t.recommended_for || '通用' }}</span>
                    <span class="ws-dots"><i :style="{ background: swatchOf(t).bg }" /><i :style="{ background: swatchOf(t).primary }" /><i :style="{ background: swatchOf(t).accent }" /></span>
                  </div>
                </article>
              </div>
            </div>

            <aside class="ws-tplpanel">
              <h3>实时预览</h3>
              <div class="ws-prevcover" :style="{ background: ws.currentTemplate?.swatch.bg || '#4f46e5', color: '#fff' }">
                <b>{{ ws.previewTopic }}</b>
                <span>人教A版 · {{ ws.className }}</span>
              </div>
              <div class="ws-prevpage">
                <span class="ws-pill ws-pill--brand">学习目标</span>
                <i class="bar" style="background: rgba(100,116,139,0.25)" /><i class="bar" style="width: 80%; background: rgba(100,116,139,0.18)" />
              </div>
              <hr class="ws-hr">
              <p class="ws-panel-label">应用范围</p>
              <div class="ws-seg">
                <button :class="{ 'is-active': ws.applyScope === 'all' }" @click="ws.applyScope = 'all'">全部页面</button>
                <button :class="{ 'is-active': ws.applyScope === 'cover' }" @click="ws.applyScope = 'cover'">仅封面</button>
              </div>
              <p class="ws-panel-label">字号档 <span>长解答自动分页，不缩小内容</span></p>
              <div class="ws-seg">
                <button v-for="c in ws.fontCards" :key="c.value" :class="{ 'is-active': ws.form.font_tier === c.value }" @click="ws.form.font_tier = c.value">{{ c.name.replace('档', '') }}</button>
              </div>
              <div class="ws-togglerow">
                <div><b>边注模式</b><span>预读者生成可编辑边注</span></div>
                <button class="ws-switch" :class="{ 'is-on': ws.form.margin_notes }" role="switch" :aria-checked="ws.form.margin_notes" @click="ws.form.margin_notes = !ws.form.margin_notes"><i /></button>
              </div>
              <p class="ws-panel-note">共 {{ ws.gateOutline.length || 'N' }} 页 · 模板生成后仍可更换</p>
            </aside>
          </div>
          <div class="ws-bottom ws-bottom--static">
            <button class="ws-btn-ghost" @click="ws.prevTemplate()">← 上一步</button>
            <button class="ws-btn-primary" :disabled="!ws.form.template_id" data-testid="tv3-new-generate" @click="ws.startGenerate()">
              ✦ 开始生成（{{ ws.gateOutline.length || 'N' }} 页）
            </button>
          </div>
        </template>

        <!-- ========== 生成进度（SSE 实时可见） ========== -->
        <template v-else-if="ws.screen === 'generating'">
          <div class="ws-gen" data-testid="tv3-generating">
            <div class="ws-genorb">✦</div>
            <div class="ws-progress"><div class="ws-progress-bar" :style="{ width: ws.genProgress + '%' }" /></div>
            <p class="ws-genstage" data-testid="tv3-gen-stage">{{ ws.genStage }}</p>
            <button v-if="ws.genFailed" class="ws-btn-primary" style="margin-top: 14px" data-testid="tv3-gen-retry" @click="ws.retryGenerate()">重试生成</button>
            <div v-if="ws.genBlocks.length" class="ws-genblocks">
              <div v-for="(b, i) in ws.genBlocks" :key="i" class="ws-genblock" data-testid="tv3-recog-block">
                <span class="ws-pill" :class="b.type === 'figure' ? 'ws-pill--warn' : 'ws-pill--brand'">
                  {{ b.type === 'stem' ? '题干' : b.type === 'figure' ? '图形' : '解答步骤' }}
                </span>
                <div v-if="b.latex" class="ws-genlatex" v-html="ws.renderLatex(b.latex)" />
                <div v-else-if="b.text" class="ws-gentext">{{ b.text }}</div>
                <div v-else class="ws-gentext is-muted">[图形区域 · 建议重建为结构化图形]</div>
                <span class="ws-genconf">{{ (b.confidence * 100).toFixed(0) }}%</span>
              </div>
            </div>
          </div>
        </template>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * WorkshopFlow —— 课件工坊·备小研风格展示层（IFC-WS-a，PROTOTYPE-ONLY）
 * 纯展示：全部状态与动作来自 SlidesView 注入的 ws 控制器（响应式对象，含动作函数）。
 * 结构对齐 D:\课件工坊\pages\*.html：首页 hero 输入台 / 识别确认 / 教案映射 / 大纲确认卡片 / 模板画廊+实时预览 / 生成进度。
 * 红线不变：AI 只出草稿，教师确认后才生成；识别/重写等未接真实服务的位置如实标注。
 */
import { reactive, ref } from 'vue'

defineProps<{ ws: any }>()
/* C14：问候语按时段计算（与今日工作台同一口径），不再硬编码「下午好」 */
const hello = (() => {
  const h = new Date().getHours()
  return (h < 12 ? '上午好' : h < 18 ? '下午好' : '晚上好') + '，李老师 👋'
})()
const docInput = ref<HTMLInputElement | null>(null)
const fileInput = ref<HTMLInputElement | null>(null)
/** IFC-003：缩略图加载失败 → 回落 swatch 自渲染骨架（记录失败 id，绝不破图） */
const thumbFail = reactive<Record<string, boolean>>({})
/** 模板色板兜底（后端契约缺失/旧数据时防渲染崩溃） */
const DFLT_SWATCH = { bg: '#ffffff', primary: '#4f46e5', accent: '#1e3a8a', light: true }
const swatchOf = (t: any) => t?.swatch || DFLT_SWATCH
</script>

<style scoped>
.ws-root {
  /* ---- 备小研设计令牌（D:\课件工坊\pages\*.html 同源） ---- */
  --ailp-primary-50: #eef2ff; --ailp-primary-100: #e0e7ff; --ailp-primary-500: #6366f1; --ailp-primary-600: #4f46e5;
  --ailp-accent-600: #0891b2;
  --ailp-gray-50: #f8fafc; --ailp-gray-200: #e2e8f0; --ailp-gray-300: #cbd5e1; --ailp-gray-400: #94a3b8;
  --ailp-muted: #f1f5f9; --ailp-muted-foreground: #64748b;
  --ailp-success-600: #059669; --ailp-warning-600: #d97706; --ailp-error-600: #dc2626;
  --ailp-border: #e2e8f0; --ailp-foreground: #0f172a;
  --ailp-radius-lg: 14px; --ailp-radius-xl: 20px; --ailp-radius-2xl: 28px;

  max-width: 64rem; margin: 0 auto; color: var(--ailp-foreground);
  font-size: 14px; line-height: 1.6; text-align: left;
}
.ws-root * { box-sizing: border-box; }
.ws-grad { background: linear-gradient(135deg, #4f46e5 0%, #06b6d4 100%); -webkit-background-clip: text; background-clip: text; -webkit-text-fill-color: transparent; }

/* ---- top bar / steps ---- */
.ws-topbar { display: flex; align-items: center; gap: 12px; padding: 12px 0; border-bottom: 1px solid var(--ailp-border); margin-bottom: 14px; }
.ws-topbar-brand { display: flex; align-items: center; gap: 8px; font-weight: 600; }
.ws-logo { width: 34px; height: 34px; border-radius: 11px; display: grid; place-items: center; color: #fff; background: linear-gradient(135deg, #4f46e5, #06b6d4); }
.ws-topbar-title { font-size: 15px; }
.ws-topbar-sub { font-size: 13px; color: var(--ailp-muted-foreground); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ws-badge { display: inline-flex; align-items: center; gap: 4px; padding: 4px 12px; border-radius: 9999px; font-size: 12px; font-weight: 500; white-space: nowrap; }
.ws-badge--warn { background: rgba(245, 158, 11, 0.1); color: var(--ailp-warning-600); }
.ws-badge--right { margin-left: auto; }
.ws-icon-btn { width: 32px; height: 32px; border-radius: 9px; border: none; background: transparent; color: var(--ailp-muted-foreground); cursor: pointer; font-size: 15px; }
.ws-icon-btn:hover { background: var(--ailp-muted); }

.ws-steps { display: flex; align-items: center; gap: 8px; margin: 14px 0 18px; font-size: 13px; }
.ws-step { display: flex; align-items: center; gap: 6px; color: var(--ailp-muted-foreground); }
.ws-step i { width: 22px; height: 22px; border-radius: 9999px; display: grid; place-items: center; font-style: normal; font-size: 11px; font-weight: 700; background: var(--ailp-muted); color: var(--ailp-muted-foreground); }
.ws-step.is-active { color: var(--ailp-primary-600); font-weight: 600; }
.ws-step.is-active i { background: var(--ailp-primary-50); color: var(--ailp-primary-600); border: 1px solid var(--ailp-primary-500); }
.ws-step.is-done { color: var(--ailp-foreground); }
.ws-step.is-done i { background: var(--ailp-primary-600); color: #fff; }
.ws-step-line { flex: 0 0 26px; height: 1px; background: var(--ailp-border); }

/* ---- hero / home ---- */
.ws-hero { text-align: center; padding: 26px 0 8px; }
.ws-hello { color: var(--ailp-muted-foreground); margin: 8px 0 2px; }
.ws-h1 { font-size: 42px; font-weight: 800; letter-spacing: -0.5px; margin: 2px 0 8px; }
.ws-sub { color: var(--ailp-muted-foreground); font-size: 16px; margin-bottom: 18px; }
.ws-hero-pills { display: flex; justify-content: center; gap: 8px; }
.ws-pill { display: inline-flex; align-items: center; gap: 4px; padding: 2px 10px; border-radius: 8px; font-size: 12px; font-weight: 500; }
.ws-pill--brand { background: var(--ailp-primary-50); color: var(--ailp-primary-600); }
.ws-pill--ok { background: rgba(16, 185, 129, 0.1); color: var(--ailp-success-600); }
.ws-pill--warn { background: rgba(245, 158, 11, 0.1); color: var(--ailp-warning-600); }
.ws-pill--muted { background: var(--ailp-muted); color: var(--ailp-muted-foreground); }

.ws-input-glow { border-radius: var(--ailp-radius-2xl); padding: 2px; background: linear-gradient(135deg, rgba(79, 70, 229, 0.4), rgba(6, 182, 212, 0.4), rgba(79, 70, 229, 0.4)); }
.ws-input-card { background: #fff; border-radius: var(--ailp-radius-2xl); padding: 14px 20px 12px; text-align: left; }
.ws-input-card textarea { width: 100%; border: none; outline: none; resize: none; font: inherit; font-size: 15px; color: var(--ailp-foreground); background: transparent; padding: 6px 2px; }
.ws-input-row { display: flex; align-items: center; gap: 8px; border-top: 1px solid var(--ailp-border); padding-top: 10px; }
.ws-icon-btn { border: 1px solid var(--ailp-border); background: #fff; }
.ws-mode-chips { display: flex; gap: 6px; flex: 1; overflow-x: auto; }
.ws-chip { padding: 5px 13px; border-radius: 9999px; font-size: 13px; font-weight: 500; border: none; cursor: pointer; background: var(--ailp-muted); color: var(--ailp-muted-foreground); white-space: nowrap; }
.ws-chip:hover { color: var(--ailp-primary-600); background: var(--ailp-primary-50); }
.ws-chip.is-primary { background: var(--ailp-primary-600); color: #fff; }
.ws-send { width: 42px; height: 42px; border-radius: 12px; border: none; color: #fff; background: linear-gradient(135deg, #4f46e5, #6366f1); cursor: pointer; font-size: 16px; }
.ws-send:hover { transform: scale(1.05); }
.ws-send--sm { width: 32px; height: 32px; border-radius: 9px; }
.ws-docs { margin-top: 10px; font-size: 12px; color: var(--ailp-warning-600); background: rgba(245, 158, 11, 0.06); border-radius: 10px; padding: 6px 10px; }
.ws-hint { display: flex; justify-content: center; gap: 6px; margin-top: 10px; font-size: 13px; color: var(--ailp-muted-foreground); }
.ws-ctx-pills { display: flex; flex-wrap: wrap; justify-content: center; gap: 8px; margin-top: 16px; }
.ws-ctx-pill { display: inline-flex; align-items: center; gap: 6px; padding: 6px 14px; border-radius: 9999px; font-size: 13px; background: rgba(255, 255, 255, 0.7); border: 1px solid var(--ailp-border); color: var(--ailp-muted-foreground); cursor: pointer; }
.ws-ctx-pill select { border: none; background: transparent; font: inherit; color: var(--ailp-foreground); outline: none; max-width: 210px; }

.ws-section { margin-top: 34px; text-align: left; }
.ws-h2 { font-size: 17px; font-weight: 700; margin: 0; }
.ws-sechead { display: flex; align-items: baseline; gap: 10px; margin-bottom: 12px; }
.ws-seccount { font-size: 13px; color: var(--ailp-muted-foreground); }
.ws-reco { display: flex; align-items: center; gap: 12px; width: 100%; text-align: left; padding: 12px 16px; margin-bottom: 8px; border-radius: 13px; border: 1px solid var(--ailp-border); background: rgba(255, 255, 255, 0.85); font-size: 14px; color: var(--ailp-foreground); cursor: pointer; }
.ws-reco:hover { border-color: rgba(79, 70, 229, 0.4); box-shadow: 0 6px 18px rgba(79, 70, 229, 0.08); }
.ws-reco-ico { width: 32px; height: 32px; border-radius: 9px; display: grid; place-items: center; flex-shrink: 0; }
.ws-deckgrid { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 14px; }
.ws-deckcard { background: rgba(255, 255, 255, 0.85); border: 1px solid var(--ailp-border); border-radius: var(--ailp-radius-xl); padding: 16px 18px; cursor: pointer; transition: all 0.2s; }
.ws-deckcard:hover { transform: translateY(-3px); box-shadow: 0 16px 32px -10px rgba(79, 70, 229, 0.15); border-color: rgba(79, 70, 229, 0.4); }
.ws-deckcard-top { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; }
.ws-deckcard-time { font-size: 11px; color: var(--ailp-muted-foreground); }
.ws-deckcard-title { font-size: 14.5px; font-weight: 700; margin: 0 0 4px; }
.ws-deckcard:hover .ws-deckcard-title { color: var(--ailp-primary-600); }
.ws-deckcard-meta { font-size: 12px; color: var(--ailp-muted-foreground); margin: 0; }
.ws-todaycard { background: rgba(255, 255, 255, 0.85); border: 1px solid var(--ailp-border); border-radius: var(--ailp-radius-xl); padding: 10px 14px; }
.ws-todayrow { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; padding: 10px 8px; border-radius: 12px; }
.ws-todayrow + .ws-todayrow { border-top: 1px solid var(--ailp-gray-50); }
.ws-todaytime { font-size: 13px; width: 44px; }
.ws-todaytopic { flex: 1; min-width: 0; font-size: 13.5px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ws-mini { padding: 5px 14px; border-radius: 9999px; font-size: 12px; font-weight: 500; border: none; cursor: pointer; background: var(--ailp-muted); color: var(--ailp-muted-foreground); }
.ws-mini--primary { background: var(--ailp-primary-600); color: #fff; }

/* ---- wizard shell ---- */
.ws-wiz { padding-bottom: 24px; }
.ws-block { background: rgba(255, 255, 255, 0.85); border: 1px solid var(--ailp-border); border-radius: var(--ailp-radius-xl); padding: 16px 20px; margin-bottom: 16px; }
.ws-blockhead { display: flex; align-items: center; gap: 8px; margin-bottom: 12px; font-size: 14px; }
.ws-blockhead b { font-size: 15px; }
.ws-blockhead span { font-size: 12px; color: var(--ailp-muted-foreground); }
.ws-stepdot { width: 22px; height: 22px; border-radius: 9999px; display: grid; place-items: center; font-style: normal; font-size: 11px; font-weight: 700; color: #fff; background: var(--ailp-primary-600); }
.ws-loading { display: flex; align-items: center; gap: 10px; padding: 26px 0; color: var(--ailp-muted-foreground); font-size: 13px; }
.ws-spinner { width: 16px; height: 16px; border-radius: 9999px; border: 2px solid var(--ailp-gray-200); border-top-color: var(--ailp-primary-600); animation: ws-spin 0.8s linear infinite; }
@keyframes ws-spin { to { transform: rotate(360deg); } }

/* photos */
.ws-photos { display: flex; gap: 12px; flex-wrap: wrap; align-items: center; }
.ws-photochip { position: relative; width: 112px; height: 80px; border-radius: var(--ailp-radius-lg); overflow: hidden; border: 1px solid var(--ailp-border); }
.ws-photochip img { width: 100%; height: 100%; object-fit: cover; }
.ws-photo-x { position: absolute; top: 4px; right: 4px; width: 20px; height: 20px; border-radius: 9999px; border: none; background: rgba(0, 0, 0, 0.35); color: #fff; cursor: pointer; }
.ws-photo-name { position: absolute; left: 0; right: 0; bottom: 0; font-size: 10px; color: #fff; background: rgba(0, 0, 0, 0.4); padding: 2px 6px; }
.ws-photoadd { width: 112px; height: 80px; border-radius: var(--ailp-radius-lg); border: 1px dashed var(--ailp-gray-300); background: transparent; color: var(--ailp-muted-foreground); font-size: 20px; cursor: pointer; display: flex; flex-direction: column; align-items: center; justify-content: center; }
.ws-photoadd:hover { border-color: var(--ailp-primary-500); color: var(--ailp-primary-600); }
.ws-photoadd span { font-size: 12px; }

/* recognition */
.ws-recogcard { border: 1px solid var(--ailp-border); border-radius: var(--ailp-radius-xl); overflow: hidden; margin-bottom: 12px; background: #fff; }
.ws-recogcard.is-warn { border-color: rgba(245, 158, 11, 0.4); }
.ws-recoghead { display: flex; align-items: center; gap: 10px; padding: 8px 16px; border-bottom: 1px solid var(--ailp-border); background: var(--ailp-gray-50); font-size: 12px; color: var(--ailp-muted-foreground); }
.ws-recogbody { padding: 12px 16px; }
.ws-recoglabel { font-size: 12px; color: var(--ailp-muted-foreground); margin: 0 0 4px; font-weight: 600; }
.ws-recogtext { width: 100%; border: none; outline: none; resize: vertical; font: inherit; font-size: 14px; line-height: 1.7; color: var(--ailp-foreground); background: transparent; border-bottom: 1px dashed transparent; }
.ws-recogtext:focus { border-bottom-color: var(--ailp-primary-500); }
.ws-recogkps { margin-top: 8px; font-size: 12px; color: var(--ailp-muted-foreground); display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
.ws-note { font-size: 12px; color: var(--ailp-muted-foreground); margin: 8px 0 0; }
.ws-note.is-amber { color: #b45309; }
.ws-note.is-red { color: var(--ailp-error-600); }

/* segmented */
.ws-segcard { border: 1px solid var(--ailp-border); border-radius: var(--ailp-radius-lg); padding: 12px 16px; margin-bottom: 10px; background: #fff; }
.ws-seghead { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 8px; }
.ws-seghead span { font-size: 12px; color: var(--ailp-muted-foreground); }
.ws-seg { display: flex; border: 1px solid var(--ailp-border); border-radius: 10px; overflow: hidden; }
.ws-seg button { flex: 1; padding: 8px 0; font-size: 13px; text-align: center; border: none; background: #fff; color: var(--ailp-muted-foreground); cursor: pointer; }
.ws-seg button.is-active { background: var(--ailp-primary-600); color: #fff; font-weight: 600; }

/* bottom bars */
.ws-bottom { position: sticky; bottom: 0; z-index: 5; display: flex; align-items: center; justify-content: flex-end; gap: 14px; padding: 12px 4px; background: linear-gradient(to top, var(--ailp-gray-50) 55%, transparent); font-size: 12px; color: var(--ailp-muted-foreground); }
.ws-bottom--static { justify-content: space-between; }
.ws-btn-primary { display: inline-flex; align-items: center; gap: 6px; padding: 10px 20px; border-radius: 12px; border: none; font-size: 14px; font-weight: 600; color: #fff; background: linear-gradient(135deg, #4f46e5, #6366f1); cursor: pointer; }
.ws-btn-primary:hover { transform: scale(1.03); }
.ws-btn-primary:disabled { opacity: 0.5; cursor: not-allowed; transform: none; }
.ws-btn-ghost { padding: 9px 16px; border-radius: 12px; border: 1px solid var(--ailp-border); background: #fff; font-size: 13px; color: var(--ailp-foreground); cursor: pointer; }
.ws-btn-ghost:hover { background: var(--ailp-muted); }

/* plan */
.ws-plancard { display: flex; align-items: center; gap: 12px; padding: 14px 16px; border-radius: var(--ailp-radius-lg); border: 1px solid var(--ailp-border); background: #fff; margin-bottom: 10px; cursor: pointer; }
.ws-plancard.is-selected { border-color: var(--ailp-primary-500); box-shadow: 0 0 0 3px rgba(99, 102, 241, 0.15); }
.ws-radio { width: 16px; height: 16px; border-radius: 9999px; border: 2px solid var(--ailp-gray-300); display: grid; place-items: center; flex-shrink: 0; }
.ws-plancard.is-selected .ws-radio { border-color: var(--ailp-primary-600); }
.ws-radio i { width: 8px; height: 8px; border-radius: 9999px; background: var(--ailp-primary-600); }
.ws-planico { font-size: 20px; }
.ws-planbody { display: flex; flex-direction: column; gap: 2px; }
.ws-planbody b { font-size: 14.5px; }
.ws-planbody span { font-size: 12px; color: var(--ailp-muted-foreground); }
.ws-mapcard { border: 1px solid var(--ailp-border); border-radius: var(--ailp-radius-lg); padding: 14px 16px; background: #fff; }
.ws-mapsummary { display: flex; justify-content: space-between; align-items: baseline; padding-bottom: 10px; margin-bottom: 10px; border-bottom: 1px solid var(--ailp-border); font-size: 13px; }
.ws-mapsummary span { color: var(--ailp-muted-foreground); font-size: 12px; }
.ws-maprow { display: flex; align-items: center; gap: 12px; padding: 8px 10px; border-radius: 10px; background: color-mix(in srgb, var(--ailp-muted) 60%, transparent); margin-bottom: 6px; }
.ws-mapname { width: 96px; flex-shrink: 0; font-size: 13.5px; }
.ws-mapdesc { flex: 1; font-size: 12px; color: var(--ailp-muted-foreground); }
.ws-stepper { display: flex; align-items: center; gap: 6px; }
.ws-stepper button { width: 24px; height: 24px; border-radius: 6px; border: 1px solid var(--ailp-border); background: #fff; color: var(--ailp-muted-foreground); cursor: pointer; }
.ws-stepper button:hover { color: var(--ailp-primary-600); border-color: var(--ailp-primary-500); }
.ws-stepper span { width: 24px; text-align: center; font-weight: 700; font-size: 13px; }
.ws-mapfoot { display: flex; justify-content: flex-end; font-size: 12px; color: var(--ailp-muted-foreground); padding-top: 6px; }

/* outline */
.ws-stats { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 12px; }
.ws-statchip { display: inline-flex; align-items: center; gap: 5px; padding: 6px 13px; border-radius: 9999px; font-size: 12px; font-weight: 500; background: rgba(255, 255, 255, 0.85); border: 1px solid var(--ailp-border); color: var(--ailp-muted-foreground); }
.ws-statchip.is-ok { background: rgba(16, 185, 129, 0.08); color: var(--ailp-success-600); border-color: transparent; }
.ws-statchip.is-warn { background: rgba(245, 158, 11, 0.08); color: var(--ailp-warning-600); border-color: transparent; }
.ws-statchip.is-brand { background: var(--ailp-primary-50); color: var(--ailp-primary-600); border-color: transparent; }
.ws-btn-ghost.sm { padding: 6px 12px; font-size: 12px; border-radius: 9px; }
.ws-stats .ws-btn-ghost { margin-left: auto; padding: 6px 12px; font-size: 12px; border-radius: 9px; }
.ws-adjustbar { display: flex; align-items: center; gap: 8px; padding: 10px 14px; border: 1px solid var(--ailp-border); border-radius: var(--ailp-radius-xl); background: rgba(255, 255, 255, 0.85); margin-bottom: 16px; flex-wrap: wrap; }
.ws-adjustico { color: var(--ailp-primary-600); }
.ws-adjustbar input { flex: 1; min-width: 180px; border: none; outline: none; font: inherit; font-size: 13.5px; background: transparent; color: var(--ailp-foreground); }
.ws-outcards { display: flex; flex-direction: column; gap: 10px; }
.ws-outcard { display: flex; align-items: flex-start; gap: 12px; padding: 13px 16px; border-radius: var(--ailp-radius-xl); border: 1px solid var(--ailp-border); background: rgba(255, 255, 255, 0.85); transition: border-color 0.15s; }
.ws-outcard:hover { border-color: rgba(79, 70, 229, 0.4); }
.ws-outnum { width: 24px; height: 24px; border-radius: 9px; display: grid; place-items: center; font-size: 12px; font-weight: 700; color: #fff; background: linear-gradient(135deg, #4f46e5, #6366f1); flex-shrink: 0; }
.ws-outbody { flex: 1; min-width: 0; }
.ws-outhead { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.ws-kindtag { border: none; border-radius: 7px; font-size: 12px; font-weight: 600; padding: 3px 8px; background: var(--ailp-primary-50); color: var(--ailp-primary-600); cursor: pointer; }
.ws-kindtag[data-kind='example'], .ws-kindtag[data-kind='variation'] { background: rgba(245, 158, 11, 0.1); color: var(--ailp-warning-600); }
.ws-kindtag[data-kind='definition'] { background: rgba(139, 92, 246, 0.1); color: #7c3aed; }
.ws-kindtag[data-kind='summary'] { background: rgba(16, 185, 129, 0.1); color: var(--ailp-success-600); }
.ws-kindtag[data-kind='review'] { background: rgba(6, 182, 212, 0.1); color: var(--ailp-accent-600); }
.ws-outtitle { flex: 1; min-width: 160px; border: none; outline: none; font-size: 15px; font-weight: 700; color: var(--ailp-foreground); background: transparent; border-bottom: 1px dashed transparent; }
.ws-outtitle:focus { border-bottom-color: var(--ailp-primary-500); }
.ws-outdesc { margin: 5px 0 0; font-size: 12px; color: var(--ailp-muted-foreground); }
.ws-outacts { display: flex; flex-direction: column; gap: 4px; }
.ws-outacts button { width: 26px; height: 26px; border-radius: 7px; border: none; background: transparent; color: var(--ailp-gray-400); cursor: pointer; font-size: 12px; }
.ws-outacts button:hover:not(:disabled) { background: var(--ailp-muted); color: var(--ailp-foreground); }
.ws-outacts button:disabled { opacity: 0.35; cursor: not-allowed; }
.ws-outacts .is-danger:hover:not(:disabled) { background: rgba(239, 68, 68, 0.08); color: var(--ailp-error-600); }
.ws-addpage { width: 100%; padding: 13px 0; border-radius: var(--ailp-radius-xl); border: 1px dashed var(--ailp-gray-300); background: transparent; color: var(--ailp-muted-foreground); font-size: 13.5px; font-weight: 500; cursor: pointer; margin-top: 10px; }
.ws-addpage:hover { border-color: rgba(79, 70, 229, 0.5); color: var(--ailp-primary-600); }
.ws-reqs { border: 1px solid rgba(245, 158, 11, 0.3); border-radius: var(--ailp-radius-lg); padding: 10px 14px; margin-top: 14px; background: rgba(255, 251, 235, 0.6); }
.ws-reqshead { display: flex; justify-content: space-between; align-items: baseline; font-size: 12.5px; font-weight: 700; margin-bottom: 6px; }
.ws-reqshead span { font-weight: 400; color: var(--ailp-muted-foreground); font-size: 11px; }
.ws-req { display: flex; align-items: center; gap: 8px; font-size: 12.5px; padding: 3px 0; }
.ws-req b { flex-shrink: 0; }
.ws-reqnote { color: var(--ailp-muted-foreground); font-size: 11.5px; }

/* template gallery */
.ws-tplwrap { display: flex; gap: 20px; align-items: flex-start; }
.ws-tplgallery { flex: 1; min-width: 0; }
.ws-tplchips { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 14px; }
.ws-tplgrid { display: grid; grid-template-columns: repeat(auto-fill, minmax(230px, 1fr)); gap: 14px; }
.ws-tplcard { position: relative; border: 1px solid var(--ailp-border); border-radius: var(--ailp-radius-xl); background: rgba(255, 255, 255, 0.85); padding: 14px; cursor: pointer; }
.ws-tplcard:hover { box-shadow: 0 10px 24px -8px rgba(79, 70, 229, 0.15); }
.ws-tplcard.is-selected { border-color: var(--ailp-primary-600); box-shadow: 0 0 0 3px rgba(99, 102, 241, 0.15); }
.ws-tplcheck { position: absolute; top: 12px; right: 12px; width: 24px; height: 24px; border-radius: 9999px; display: grid; place-items: center; color: #fff; background: var(--ailp-primary-600); font-size: 12px; }
.ws-tplpreview { display: flex; border-radius: 12px; overflow: hidden; border: 1px solid var(--ailp-border); margin-bottom: 10px; height: 88px; }
.ws-tplthumb { width: 100%; height: 100%; object-fit: cover; display: block; }
.ws-tplprev-cover { flex: 1; display: flex; flex-direction: column; justify-content: center; gap: 5px; padding: 12px; }
.ws-tplprev-page { flex: 1; display: flex; flex-direction: column; gap: 5px; padding: 12px; background: #fff; }
.bar { display: block; height: 5px; border-radius: 9999px; width: 100%; opacity: 0.75; }
.bar.big { height: 7px; width: 75%; }
.bar.w-1\/2 { width: 50%; } .bar.w-1\/3 { width: 33%; }
.ws-tplcard h3 { font-size: 14.5px; margin: 0 0 6px; }
.ws-tplmeta { display: flex; align-items: center; justify-content: space-between; }
.ws-dots { display: flex; gap: 4px; }
.ws-dots i { width: 12px; height: 12px; border-radius: 9999px; border: 1px solid rgba(0, 0, 0, 0.06); }
.ws-tplpanel { width: 300px; flex-shrink: 0; position: sticky; top: 12px; border: 1px solid var(--ailp-border); border-radius: var(--ailp-radius-xl); background: rgba(255, 255, 255, 0.9); padding: 18px; }
.ws-tplpanel h3 { margin: 0 0 10px; font-size: 15px; }
.ws-prevcover { border-radius: 12px; padding: 22px 14px; text-align: center; margin-bottom: 10px; display: flex; flex-direction: column; gap: 4px; }
.ws-prevcover b { font-size: 15px; }
.ws-prevcover span { font-size: 11px; opacity: 0.75; }
.ws-prevpage { border: 1px solid var(--ailp-border); border-radius: 12px; padding: 12px; display: flex; flex-direction: column; gap: 6px; background: #fff; }
.ws-prevpage .bar { background: var(--ailp-gray-200); }
.ws-hr { border: none; border-top: 1px solid var(--ailp-border); margin: 14px 0; }
.ws-panel-label { font-size: 13px; font-weight: 600; margin: 0 0 6px; }
.ws-panel-label span { font-weight: 400; color: var(--ailp-muted-foreground); font-size: 11px; }
.ws-togglerow { display: flex; align-items: center; justify-content: space-between; margin-top: 14px; }
.ws-togglerow b { font-size: 13px; display: block; }
.ws-togglerow span { font-size: 11px; color: var(--ailp-muted-foreground); }
.ws-switch { width: 40px; height: 22px; border-radius: 9999px; border: none; background: var(--ailp-gray-300); position: relative; cursor: pointer; }
.ws-switch.is-on { background: var(--ailp-primary-600); }
.ws-switch i { position: absolute; top: 2px; left: 2px; width: 18px; height: 18px; border-radius: 9999px; background: #fff; box-shadow: 0 1px 3px rgba(0, 0, 0, 0.2); transition: left 0.15s; }
.ws-switch.is-on i { left: 20px; }
.ws-panel-note { font-size: 11.5px; color: var(--ailp-muted-foreground); margin: 12px 0 0; }

/* generating */
.ws-gen { text-align: center; padding: 30px 0 10px; }
.ws-genorb { width: 56px; height: 56px; margin: 0 auto 16px; border-radius: 9999px; display: grid; place-items: center; color: #fff; font-size: 22px; background: linear-gradient(135deg, #4f46e5, #7c3aed, #06b6d4); animation: ws-float 3s ease-in-out infinite; }
@keyframes ws-float { 0%, 100% { transform: translateY(0); } 50% { transform: translateY(-6px); } }
.ws-progress { height: 8px; border-radius: 9999px; background: var(--ailp-gray-200); overflow: hidden; }
.ws-progress-bar { height: 100%; border-radius: 9999px; background: linear-gradient(90deg, #4f46e5, #06b6d4); transition: width 0.4s; }
.ws-genstage { font-size: 13.5px; color: var(--ailp-muted-foreground); margin: 12px 0 16px; }
.ws-genblocks { display: flex; flex-direction: column; gap: 8px; text-align: left; }
.ws-genblock { display: flex; align-items: flex-start; gap: 10px; padding: 10px 14px; border-radius: 12px; border: 1px solid var(--ailp-border); background: rgba(255, 255, 255, 0.85); }
.ws-genlatex, .ws-gentext { flex: 1; font-size: 13px; min-width: 0; }
.ws-gentext.is-muted { color: var(--ailp-muted-foreground); }
.ws-genconf { font-size: 11px; color: var(--ailp-muted-foreground); flex-shrink: 0; }

@media (max-width: 900px) { .ws-tplwrap { flex-direction: column; } .ws-tplpanel { width: 100%; position: static; } }
@media (prefers-reduced-motion: reduce) { .ws-genorb, .ws-send, .ws-btn-primary, .ws-deckcard { animation: none !important; transition: none !important; } }
</style>
