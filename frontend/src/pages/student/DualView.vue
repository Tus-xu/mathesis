<template>
  <div class="dcx-shell dcx-light">
    <!-- ===== 顶栏：品牌 / 课程进度 / 计时 / 播控 / 全屏 / 导出 / 报告 ===== -->
    <header class="dcx-top">
      <div class="dcx-brand" @click="goHome">
        <span class="dcx-logo">⬡</span>
        <b>双师课堂</b>
      </div>
      <template v-if="inSession">
        <span class="dcx-vline"></span>
        <div class="dcx-course">
          <span class="dcx-course-name" :title="session?.title">{{ session?.title }}</span>
          <span v-if="view === 'player'" class="dcx-pagechip">{{ curIndex + 1 }} / {{ pageCount }}</span>
          <span v-else-if="view === 'creating'" class="dcx-pagechip generating">备课中 {{ genDone }} / {{ genTotal }}</span>
          <span v-if="isTeacherDeck" class="dcx-deckbadge" :title="teacherDeckBadge">🎓 {{ teacherDeckBadge }}</span>
        </div>
      </template>
      <span v-else class="dcx-course-sub">主讲授课 + AI 助教 · 真实课堂</span>

      <div class="dcx-top-right">
        <button class="dcx-tbtn" title="返回学生端" @click="goBack">🏠 学生端</button>
        <button v-if="isTeacherDeck" class="dcx-tbtn" title="查看老师布置的课堂任务" @click="goClassTasks">📋 课堂任务</button>
        <template v-if="view === 'player'">
          <span class="dcx-timer" title="本次进入课堂后的计时，不含历史累计">⏱ 本次 {{ clockLabel }}</span>
          <button class="dcx-round" :class="{ on: pb.on }" :disabled="!timeline.length" :title="pb.on ? '暂停讲解' : '开始讲解（逐句朗读）'" @click="togglePlay">{{ pb.on ? '⏸' : '▶' }}</button>
          <button class="dcx-round" title="停止讲解" @click="stopPlayback">⏹</button>
        </template>
        <button class="dcx-tbtn solid" @click="toggleFullscreen">⛶ 全屏课堂</button>
        <button v-if="view === 'player' && !isTeacherDeck" class="dcx-tbtn" @click="exportCourseware">⤓ 导出课件</button>
        <button v-if="view === 'player' && !isTeacherDeck" class="dcx-tbtn" @click="reportOpen = true">📄 课堂报告</button>
        <button
          v-if="view === 'player' && (originQuestion.text || originQuestion.fileId)"
          class="dcx-tbtn" :class="{ on: originOpen }" @click="originOpen = !originOpen"
        >原题</button>
        <div class="dcx-settings">
          <button class="dcx-round" title="讲解设置" @click="settingsOpen = !settingsOpen">⚙</button>
          <div v-if="settingsOpen" class="dcx-menu" @click.stop>
            <div class="dcx-menu-row">
              <span>讲课文音色</span>
              <select v-model="ttsVoice" class="dcx-input">
                <option value="xiaoxiao">安娜（女·温柔）</option>
                <option value="xiaoyi">贝拉（女·活泼）</option>
                <option value="yunxi">本杰明（男·沉稳）</option>
                <option value="yunyang">亚历克斯（男·播音）</option>
              </select>
            </div>
            <div class="dcx-menu-row">
              <span>朗读声音</span>
              <select v-model="currentVoiceName" class="dcx-input" @change="onVoiceSelect">
                <option value="">系统默认</option>
                <option v-for="v in listChineseVoices(voices)" :key="v.voiceURI" :value="v.name">{{ v.name }}</option>
              </select>
            </div>
            <div class="dcx-menu-row">
              <span>讲解语速</span>
              <select v-model="speakRate" class="dcx-input">
                <option :value="0.8">0.8×</option>
                <option :value="1">1×</option>
                <option :value="1.25">1.25×</option>
              </select>
            </div>
            <label class="dcx-menu-row check"><input v-model="autoRead" type="checkbox" /> 讲完本页自动翻页</label>
          </div>
        </div>
        <span class="dcx-avatar" title="我"><img :src="studentAvatar" alt="我" style="width: 100%; height: 100%; object-fit: cover; display: block" /></span>
      </div>
    </header>
    <div v-if="settingsOpen || outlinePop" class="dcx-clickmask" @click="settingsOpen = false; outlinePop = false"></div>

    <!-- ===== 原题浮层 ===== -->
    <div v-if="originOpen && (originQuestion.text || originQuestion.imgUrl)" class="dcx-origin">
      <div class="dcx-origin-head">
        <b>📄 原题{{ originQuestion.filename ? ' · ' + originQuestion.filename : '' }}</b>
        <button class="dcx-ghost" @click="originOpen = false">✕</button>
      </div>
      <div class="dcx-origin-body">
        <MarkdownView v-if="originQuestion.text" :text="originQuestion.text" />
        <img v-if="originQuestion.imgUrl" :src="originQuestion.imgUrl" alt="原题图片" class="dcx-origin-img" />
      </div>
    </div>

    <!-- ==================== 首页（OpenMAIC：输入即出发 + 课程库） ==================== -->
    <main v-if="view === 'home'" class="dcx-home-page">
      <div class="dcx-orb o1"></div>
      <div class="dcx-orb o2"></div>
      <div class="dcx-home-inner">
        <section class="dcx-hero">
          <div class="dcx-hero-logo">⬡</div>
          <h1>AI 双师课堂</h1>
          <p>输入知识点，主讲 AI 生成可交互课件与逐句讲解：立体几何自动生成可旋转 3D 图，圆锥曲线自动绘制焦点三角形，数学结论经独立校验。</p>
        </section>

        <section class="dcx-composer">
          <textarea
            v-model="genTopic"
            class="dcx-topic"
            rows="3"
            placeholder="输入你想学的任何内容，例如：&#10;「讨论函数 f(x)=x³−3x 的单调性」&#10;「椭圆的焦点三角形与内心」"
            @keydown.ctrl.enter.prevent="createFromTopic"
            @keydown.meta.enter.prevent="createFromTopic"
          />
          <div class="dcx-composer-bar">
            <label>模式
              <select v-model="genMode" class="dcx-input dark">
                <option value="sync">同步课堂</option>
                <option value="topic">专题精讲</option>
                <option value="review">考前复习</option>
              </select>
            </label>
            <label>页数
              <select v-model="genSlides" class="dcx-input dark">
                <option :value="8">8 页</option>
                <option :value="10">10 页</option>
                <option :value="12">12 页</option>
                <option :value="15">15 页</option>
              </select>
            </label>
            <span class="dcx-composer-hint">Ctrl + Enter 立即开课</span>
            <button class="dcx-btn primary lg" :disabled="generating || !genTopic.trim()" @click="createFromTopic">进入课堂 →</button>
          </div>
        </section>

        <div class="dcx-alt-entries">
          <section class="dcx-entry">
            <div class="dcx-entry-head">📷 拍照 / 上传题目</div>
            <p class="dcx-tip">OCR 识别题目文字，确认后生成针对性讲解课堂。</p>
            <input ref="homePhotoInput" type="file" accept="image/*" class="dcx-hidden" @change="onPhotoPicked" />
            <button class="dcx-btn lg ghost-dark" :disabled="upload.uploading.value" @click="homePhotoInput?.click()">
              {{ upload.uploading.value ? upload.stageText.value : '选择题目照片' }}
            </button>
            <div v-if="photoResult" class="dcx-confirm">
              <div class="dcx-confirm-title">识别结果（确认后生成课堂）</div>
              <img v-if="photoImgUrl" :src="photoImgUrl" class="dcx-confirm-img" alt="原题图片" />
              <div class="dcx-confirm-preview"><MarkdownView :text="cleanOcrText(photoResult.text)" /></div>
              <details class="dcx-edit-src">
                <summary>编辑识别原文（高级）</summary>
                <textarea v-model="photoResult.text" class="dcx-input dark dcx-topic" rows="5" />
              </details>
              <div class="dcx-confirm-foot">
                <span class="dcx-file-name">📎 {{ photoResult.filename }}</span>
                <button class="dcx-btn primary" :disabled="generating" @click="createFromPhoto">✓ 确认题意并生成课堂</button>
              </div>
            </div>
          </section>
          <section class="dcx-entry">
            <div class="dcx-entry-head">📄 上传教案 / PPT / PDF / DOCX</div>
            <p class="dcx-tip">解析课件内容生成课堂；支持 PDF、Word、PPT、纯文本与 Markdown。</p>
            <input ref="fileInput" type="file" accept=".pdf,.docx,.pptx,.txt,.md,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,application/vnd.openxmlformats-officedocument.presentationml.presentation,text/plain,text/markdown" class="dcx-hidden" @change="onFilePicked" />
            <button class="dcx-btn lg ghost-dark" :disabled="upload.uploading.value" @click="fileInput?.click()">
              {{ upload.uploading.value ? upload.stageText.value : '选择教案文件' }}
            </button>
            <div v-if="fileResult" class="dcx-confirm">
              <div class="dcx-confirm-title">解析结果（确认标题与要点后生成课堂）</div>
              <input v-model="fileResult.title" class="dcx-input dark" placeholder="课堂标题" />
              <textarea v-model="fileResult.text" class="dcx-input dark dcx-topic" rows="4" title="将作为课堂生成的内容上下文" />
              <div class="dcx-confirm-foot">
                <span class="dcx-file-name">📎 {{ fileResult.filename }}</span>
                <button class="dcx-btn primary" :disabled="generating" @click="createFromFile">✓ 确认并生成课堂</button>
              </div>
            </div>
          </section>
        </div>

        <section class="dcx-library">
          <div class="dcx-library-head">
            <h2>🕘 最近课堂</h2>
            <div class="dcx-filters">
              <span v-for="f in statusFilters" :key="f.value" class="dcx-filter" :class="{ on: statusFilter === f.value }" @click="statusFilter = f.value; loadHistory()">{{ f.label }}</span>
            </div>
          </div>
          <div v-if="historyLoading" class="dcx-lib-empty"><span class="dcx-spinner"></span> 加载中…</div>
          <div v-else-if="!historyItems.length" class="dcx-lib-empty">还没有课程——在上方创建，或上传一份题目。</div>
          <div v-else class="dcx-lib-grid">
            <div v-for="s in historyItems" :key="s.session_id" class="dcx-card" @click="openSession(s)">
              <div class="dcx-card-top">
                <span class="dcx-card-icon">{{ sourceTypeMeta(s.source_type).icon }}</span>
                <span class="dcx-card-status" :class="s.status">
                  <span v-if="s.status === 'generating'" class="dcx-dot-pulse"></span>
                  {{ s.status === 'ready' ? '可继续学习' : s.status === 'failed' ? '生成失败' : '备课中…' }}
                </span>
              </div>
              <div class="dcx-card-title" :title="s.title">{{ s.title }}
                <span v-if="s.status === 'ready'" class="dcx-verified-tag" title="每页内容均通过数学验证器校验（公式/断言独立复算）">✓ 已验证</span>
              </div>
              <div class="dcx-card-sub">{{ fmtTime(s.created_at) }} · {{ modeLabel(s.mode) }} · {{ s.slide_count }} 页</div>
              <div class="dcx-bar"><div class="dcx-fill" :style="{ width: cardProgress(s) + '%' }"></div></div>
              <div class="dcx-card-foot">
                <span>{{ cardProgressText(s) }}</span>
                <span class="dcx-card-actions">
                  <button v-if="s.status === 'failed'" class="dcx-mini" title="重新生成" @click.stop="retryFailed(s)">↻</button>
                  <button class="dcx-mini" title="复制为新课" @click.stop="cloneSession(s)">⧉</button>
                  <button class="dcx-mini danger" title="删除" @click.stop="deleteSession(s)">🗑</button>
                </span>
              </div>
            </div>
          </div>
        </section>
      </div>
    </main>

    <!-- ==================== 生成等待页（OpenMAIC generation-preview：居中紧凑卡 + 文档动画 + 大纲卡内流式） ==================== -->
    <main v-else-if="view === 'creating'" class="dcx-creating">
      <div class="dcx-orb o1"></div>
      <div class="dcx-orb o2"></div>
      <div class="dcx-create-inner">
        <!-- 失败态 -->
        <div v-if="session?.status === 'failed'" class="dcx-gen-card err">
          <div class="dcx-gen-visual"><div class="dcx-gen-errico">⚠</div></div>
          <b class="dcx-gen-errtitle">课堂生成失败</b>
          <p class="dcx-gen-sub">{{ friendlyError(session.error) }}</p>
          <div class="dcx-gen-actions">
            <button class="dcx-btn primary" @click="retryFailed(session)">↻ 重新生成</button>
            <button class="dcx-btn ghost-dark" @click="goHome">返回首页</button>
          </div>
        </div>

        <!-- 生成态：单张紧凑卡贯穿全程（OpenMAIC 视觉） -->
        <template v-else>
          <div class="dcx-gen-card">
            <div class="dcx-gen-dots">
              <i :class="{ on: genStep >= 1, done: genStep > 1 }"></i>
              <i :class="{ on: genStep >= 2, done: genStep > 2 }"></i>
              <i :class="{ on: genStep >= 3 }"></i>
            </div>

            <div class="dcx-gen-visual">
              <!-- 大纲阶段：文档骨架书写动画 -->
              <div v-if="genStep < 2" class="dcx-doc-mock">
                <span class="ln w60"></span><span class="ln w80"></span><span class="ln w70"></span><span class="ln w80"></span><span class="ln w50"></span>
                <span class="dcx-doc-pen">✎</span>
              </div>
              <!-- 逐页备课阶段：大纲条目在文档内逐条流出 + 每页状态 -->
              <div v-else class="dcx-doc-mock live">
                <div class="dcx-doc-headbar"></div>
                <div class="dcx-doc-rows">
                  <div
                    v-for="o in streamedOutlineRows" :key="o.key"
                    class="dcx-doc-row" :class="o.status"
                    :style="{ animationDelay: (o.idx * 90) + 'ms' }"
                  >
                    <i>{{ String(o.idx + 1).padStart(2, '0') }}</i>
                    <span>{{ o.title }}</span>
                    <em v-if="o.status === 'done'">✓</em>
                    <b v-else-if="o.status === 'doing'" class="dcx-spinner"></b>
                  </div>
                </div>
              </div>
            </div>

            <h2>{{ genStep < 2 ? '生成课程大纲' : '生成页面内容' }}</h2>
            <p class="dcx-gen-sub">
              {{ genStep < 2
                ? '正在构建学习路径…'
                : `AI 主讲并行书写 ${genTotal} 页 · 已备好 ${genDone} 页` }}
            </p>
            <p class="dcx-gen-topic" v-if="session?.title">「{{ session.title }}」</p>

            <div v-if="genStep >= 2" class="dcx-gen-progress">
              <div class="dcx-bar"><div class="dcx-fill" :style="{ width: genPercent + '%' }"></div></div>
              <span>{{ genDone }} / {{ genTotal }}</span>
            </div>
            <div class="dcx-gen-actions">
              <button v-if="firstSlideReady" class="dcx-btn primary" @click="enterPlayer">进入课堂 →</button>
            </div>
          </div>

          <p class="dcx-gen-foot">✨ AI 智能体工作中，可随时离开本页，进度不丢失</p>

          <details class="dcx-gen-more">
            <summary>展开查看大纲与逐页进度</summary>
            <div class="dcx-outline-live">
              <div
                v-for="(o, i) in genOutlines" :key="o.order || i"
                class="dcx-oline" :class="{ done: pageStatus(i) === 'done', doing: pageStatus(i) === 'doing' }"
                :style="{ animationDelay: (i * 90) + 'ms' }"
              >
                <span class="dcx-ono">{{ String(i + 1).padStart(2, '0') }}</span>
                <span class="dcx-ot">
                  <b>{{ o.title }}</b>
                  <small v-if="o.key_points?.length || o.subtitle">{{ o.subtitle || (o.key_points || []).join(' · ') }}</small>
                </span>
                <span class="dcx-ost">
                  <span v-if="pageStatus(i) === 'done'" class="ok">✓ 已备好</span>
                  <span v-else-if="pageStatus(i) === 'doing'" class="doing"><span class="dcx-spinner"></span> 撰写中</span>
                  <span v-else class="wait">排队中</span>
                </span>
              </div>
            </div>
          </details>
        </template>
      </div>
    </main>

    <!-- ==================== 课堂页（OpenMAIC classroom：场景列表 + 幻灯片舞台 + 助教） ==================== -->
    <div v-else-if="view === 'player' && currentSlide" class="dcx-body">
      <!-- 左：场景列表（生成中显示骨架/进度） -->
      <aside class="dcx-left">
        <div class="dcx-card">
          <div class="dcx-card-title">📋 课程大纲</div>
          <div class="dcx-outline">
            <template v-for="(o, i) in session.outlines" :key="o.order || i">
              <div
                class="dcx-oitem" :class="{ active: i === curIndex, done: i < curIndex }"
                @click="pageStatus(i) === 'done' ? jumpTo(i) : toast.info('这一页还在备课中，稍等片刻')"
              >
                <span class="dcx-ono">{{ i + 1 }}</span>
                <span class="dcx-ot">{{ o.title }}</span>
                <span v-if="pageStatus(i) === 'done' && i < curIndex" class="dcx-ock">✓</span>
                <span v-else-if="pageStatus(i) === 'doing'" class="dcx-osmini"><span class="dcx-spinner"></span></span>
                <span v-else class="dcx-oskeleton"></span>
              </div>
              <div v-if="pageStatus(i) === 'pending'" class="dcx-o-skel-lines"><i></i><i></i></div>
            </template>
          </div>
          <button v-if="!isTeacherDeck" class="dcx-side-link" @click="goHome">＋ 新建课堂</button>
        </div>
        <div class="dcx-card dcx-links">
          <button class="dcx-side-link big" @click="reviewOpen = true">🗂 课堂回顾</button>
          <button class="dcx-side-link big" @click="router.push('/graph')">🕸 知识图谱</button>
        </div>
        <div class="dcx-card">
          <div class="dcx-learned" title="按已翻页的大纲分钟累计（计划值，不是本次计时）">大纲计划 {{ learnedMinutes }} 分钟</div>
          <div class="dcx-bar"><div class="dcx-fill" :style="{ width: pg }"></div></div>
          <div v-if="genStep < 3" class="dcx-gen-inline">
            <span class="dcx-spinner"></span> 后台备课中 {{ genDone }} / {{ genTotal }} 页，完成后自动加入大纲
          </div>
        </div>
        <div v-if="sections.goals.length" class="dcx-card">
          <div class="dcx-card-title">🎯 课程目标</div>
          <div v-for="(g, gi) in sections.goals" :key="gi" class="dcx-goal"><i>✓</i>{{ g }}</div>
        </div>
      </aside>

      <!-- 中：幻灯片舞台 + 讲稿 + 播控 -->
      <main class="dcx-center" :class="{ 'dcx-center--teacher-deck': isTeacherDeck }">
        <!-- S11：窄屏横向页码条（侧栏大纲在窄屏隐藏，页码条保证课件立即全宽可见） -->
        <div v-if="session.outlines?.length" class="dcx-o-strip">
          <button
            v-for="(o, i) in session.outlines" :key="'s' + i"
            class="dcx-o-chip" :class="{ active: i === curIndex, done: pageStatus(i) === 'done' }"
            :title="o.title"
            @click="pageStatus(i) === 'done' || i === curIndex ? jumpTo(i) : toast.info('这一页还在备课中，稍等片刻')"
          >{{ i + 1 }}</button>
          <button
            v-if="isTeacherDeck" class="dcx-o-chip dcx-o-practice"
            title="跳到本课随堂练习" @click="jumpToPractice"
          >🏋️ 随堂练习</button>
        </div>
        <div v-if="session.status === 'failed'" class="dcx-state err">
          <b>课堂生成失败</b>
          <p>{{ friendlyError(session.error) }}</p>
          <button class="dcx-btn primary" @click="retryFailed(session)">↻ 重新生成</button>
        </div>
        <template v-else>
          <div class="dcx-stage-wrap">
            <!-- 老师课堂：与教师端同一套 SlideCanvas 渲染老师课件；AI 只负责讲解/播控 -->
            <DeckSlideStage
              v-if="isTeacherDeck"
              :slide="currentSlide.deck_slide"
              :deck-title="currentSlide.deck_meta?.title || session.deck_meta?.title || session.title"
              :textbook-line="currentSlide.deck_meta?.textbook_line || session.deck_meta?.textbook_line"
              :asset-urls="currentSlide.asset_urls || {}"
              :ordinal="curIndex + 1"
            />
            <div v-else class="dcx-slide" :class="{ spotlight: pb.on }">
              <h1 class="dcx-ltitle" data-focus="title" :class="{ dim: isDim('title') }">
                {{ currentSlide.title }}
                <small v-if="currentSlide.subtitle">{{ currentSlide.subtitle }}</small>
              </h1>
              <span class="dcx-slide-no">{{ String(curIndex + 1).padStart(2, '0') }}</span>

              <div v-if="currentSlide.verification_result?.status === 'failed'" class="dcx-failedbar">
                <span>⚠️ {{ pageFailFromGen ? '本页内容生成失败（AI 通道异常，多为网络波动）' : '本页内容未通过数学自动校验，讲解可能有误' }}{{ regenLoading ? "，AI 重出中（约 1~2 分钟），完成自动替换…" : "，重出即可：" }}</span>
                <button class="dcx-btn" :disabled="regenLoading" @click="regenSlide">{{ regenLoading ? "⟳ 重出中…" : "↻ 重新生成本页" }}</button>
              </div>

              <div class="dcx-lgrid" :class="{ 'no-fig': !sections.figures.length }">
                <div class="dcx-lmain">
                  <!-- 定理 / 概念框 -->
                  <div v-for="(t, ti) in sections.theorems" :key="'t' + ti" class="dcx-theorem" :data-focus="'t' + ti" :class="{ dim: isDim('t' + ti) }">
                    <div class="dcx-theorem-head">
                      <span class="dcx-badge blue">定理</span>
                      <b v-if="t.title">{{ t.title }}</b>
                    </div>
                    <LatexText :text="t.body" class="dcx-theorem-body" />
                  </div>
                  <!-- 讲解段落（LatexText：叙述里的行内 $...$ 公式正确渲染） -->
                  <p v-for="(b, bi) in sections.lecture" :key="'p' + bi" class="dcx-para" :data-focus="'p' + bi" :class="{ dim: isDim('p' + bi) }"><LatexText :text="b.text" /></p>
                  <!-- 例题（解 + 分步板书） -->
                  <div v-for="(ex, ei) in sections.examples" :key="'e' + ei" class="dcx-example" :data-focus="'e' + ei" :class="{ dim: isDim('e' + ei) }">
                    <div class="dcx-theorem-head">
                      <span class="dcx-badge green">例题</span>
                      <LatexText :text="ex.question" class="dcx-exq" />
                    </div>
                    <div v-if="ex.analysis" class="dcx-sol">
                      <span class="dcx-solmark">解</span>
                      <div class="dcx-solsteps">
                        <div v-for="(st, si) in splitSteps(ex.analysis)" :key="si" class="dcx-step">
                          <LatexText :text="st" />
                        </div>
                      </div>
                    </div>
                    <div v-if="ex.answer" class="dcx-answer">
                      <b>结论</b>
                      <LatexText :text="wrapLatex(ex.answer)" />
                    </div>
                  </div>
                  <!-- 数据表格 -->
                  <table v-for="(tb, tbi) in sections.tables" :key="'tb' + tbi" class="dcx-table" :data-focus="'tb' + tbi" :class="{ dim: isDim('tb' + tbi) }">
                    <caption v-if="tb.caption">{{ tb.caption }}</caption>
                    <thead>
                      <tr><th v-for="(h, hi) in tb.headers" :key="hi"><LatexText :text="h" /></th></tr>
                    </thead>
                    <tbody>
                      <tr v-for="(row, ri) in tb.rows" :key="ri">
                        <td v-for="(c, ci) in row" :key="ci"><LatexText :text="c" /></td>
                      </tr>
                    </tbody>
                  </table>
                  <!-- 结论 / 易错 -->
                  <div v-for="(n, ni) in sections.summary" :key="'n' + ni" class="dcx-conclusion" :data-focus="'n' + ni" :class="{ dim: isDim('n' + ni) }">
                    <i>💡</i><LatexText :text="n.text" />
                  </div>
                  <!-- 教材关联 -->
                  <div v-if="sections.textbook && (sections.textbook.citations || []).length" class="dcx-textbook">
                    📚 教材关联：
                    <span v-for="(c, ci) in textbookCites" :key="ci" class="dcx-tcite">{{ c }}</span>
                  </div>
                </div>

                <!-- 图形列 -->
                <div v-if="sections.figures.length" class="dcx-lfig">
                  <template v-for="(f, fi) in sections.figures" :key="'f' + fi">
                    <MathFigure3D v-if="f.kind === 'geometry'" :figure="f.figure" :caption="f.caption" :height="230" />
                    <template v-else-if="f.kind === 'plot2d'">
                      <div v-if="f.caption" class="dcx-figcap">📈 {{ f.caption }}</div>
                      <GraphBlock :graph="toGraphBlock(f)" />
                    </template>
                    <template v-else-if="f.kind === 'ggb' && f.ggb">
                      <DynamicFigureViewer :items="[f.ggb]" :label="f.caption || '交互图形'" :height="320" />
                    </template>
                  </template>
                </div>
              </div>
            </div>
          </div>

          <!-- 讲稿（逐句，OpenMAIC speech line 语义） -->
          <div class="dcx-teach">
            <div class="dcx-tavatar lead"><img :src="teacherAvatar" alt="AI主讲" style="width: 100%; height: 100%; object-fit: cover; display: block" /></div>
            <div class="dcx-tbubble">
              <div class="dcx-tbhead">
                <span class="dcx-tbname">AI 主讲</span>
                <span v-if="pb.on" class="dcx-tbstate"><i class="dcx-wave"><b v-for="n in 12" :key="n"></b></i>讲解中 · 第 {{ pb.seg + 1 }} / {{ timeline.length }} 句</span>
                <span v-else-if="!timeline.length" class="dcx-tbstate muted">本页无讲稿</span>
              </div>
              <div class="dcx-narr-lines">
                <p
                  v-for="(sg, si) in timeline" :key="si"
                  :class="{ on: pb.on && si === pb.seg, played: pb.on && si < pb.seg }"
                  :title="'点击从这里讲解'"
                  @click="jumpSeg(si)"
                ><LatexText :text="sg.text" /></p>
                <p v-if="!timeline.length" class="dcx-narr-empty">{{ currentSlide.narration || ('本页讲解要点：' + sections.goals.join('；')) }}</p>
              </div>
            </div>
            <div class="dcx-tutor-mini">
              <div class="dcx-tavatar ai"><img :src="tutorAvatar" alt="AI助教" style="width: 100%; height: 100%; object-fit: cover; display: block" /></div>
              <b>AI助教</b>
              <em>在线 · 待回答</em>
              <button class="dcx-mini-ask" @click="focusAsk">提问</button>
            </div>
          </div>

          <!-- 播控条（OpenMAIC playback 控件） -->
          <div class="dcx-controls">
            <button class="dcx-ctl" :disabled="curIndex === 0" title="上一页" @click="go(-1)">⟨</button>
            <button class="dcx-ctl play" :class="{ on: pb.on }" :disabled="!timeline.length" :title="pb.on ? '暂停' : '播放讲解'" @click="togglePlay">{{ pb.on ? '⏸' : '▶' }}</button>
            <button class="dcx-ctl" :disabled="pb.seg <= 0 && !pb.on" title="上一句" @click="jumpSeg(Math.max(0, (pb.on ? pb.seg : 0) - 1))">⟪</button>
            <button class="dcx-ctl" :disabled="!timeline.length || pb.seg >= timeline.length - 1" title="下一句" @click="jumpSeg(Math.min(timeline.length - 1, pb.seg + 1))">⟫</button>
            <button class="dcx-ctl" :disabled="curIndex >= pageCount - 1" title="下一页" @click="go(1)">⟩</button>
            <span class="dcx-ctl-sep"></span>
            <span class="dcx-ctl-page">第 {{ curIndex + 1 }} / {{ pageCount }} 页</span>
            <div class="dcx-bar slim grow"><div class="dcx-fill" :style="{ width: pg }"></div></div>
            <div class="dcx-foot-l">
              <span class="dcx-foot-q">这页掌握了吗？</span>
              <button class="dcx-chipbtn" :class="{ ok: selfCheck === 'ok' }" @click="markCheck('ok')">✅ 记住了</button>
              <button class="dcx-chipbtn" :class="{ again: selfCheck === 'again' }" @click="markCheck('again')">🔁 要重听</button>
              <button v-if="!isTeacherDeck" class="dcx-chipbtn" :disabled="regenLoading" :title="regenLoading ? '正在重出本页…' : '对本页内容不满意？AI 换一种讲法重出这一页'" @click="regenSlide">{{ regenLoading ? "⟳ 重出中…" : "↻ 重新生成本页" }}</button>
              <button class="dcx-chipbtn" title="以本页主题更深入地讲一节课" @click="spawnFromPage">✨ 本页生成新课堂</button>
            </div>
          </div>

          <!-- 白板 / 公式工具 / 笔记 -->
          <div class="dcx-board">
            <div class="dcx-btabs">
              <button :class="{ on: boardTab === 'wb' }" @click="boardTab = 'wb'">白板推导</button>
              <button :class="{ on: boardTab === 'tools' }" @click="boardTab = 'tools'">公式工具</button>
              <button :class="{ on: boardTab === 'notes' }" @click="boardTab = 'notes'">课堂笔记</button>
              <span v-if="boardTab === 'wb' && sections.formulas.length" class="dcx-bthint">板书已随讲解逐句写出 ↓ 可直接手写批注</span>
            </div>
            <div v-show="boardTab === 'wb'" class="dcx-wb">
              <div v-if="sections.formulas.length" class="dcx-wbsteps">
                <div
                  v-for="(f, fi) in visibleWbSteps" :key="fi"
                  class="dcx-wbstep" :class="{ now: pb.on && fi === boardRevealed - 1 }"
                >
                  <span class="dcx-wbno">{{ fi + 1 }}</span>
                  <LatexText :text="wbLatex(f.latex)" />
                </div>
              </div>
              <p v-else class="dcx-wbempty">本页板书会随讲解自动写入；也可以直接在下方手写推导。</p>
              <WhiteboardCanvas :page="curIndex" />
            </div>
            <div v-show="boardTab === 'tools'" class="dcx-tools">
              <p class="dcx-toolstip">本课全部公式（点击复制，可粘贴到笔记或提问）</p>
              <button
                v-for="(f, fi) in sessionFormulas" :key="fi" class="dcx-fchip"
                :title="'点击复制：' + f.latex"
                @click="copyFormula(f.latex)"
              ><LatexText :text="wbLatex(f.latex)" /></button>
              <p v-if="!sessionFormulas.length" class="dcx-wbempty">本课暂无公式。</p>
            </div>
            <div v-show="boardTab === 'notes'" class="dcx-notes">
              <textarea
                v-model="notes" class="dcx-input dark dcx-notes-area"
                placeholder="写下本页关键结论、自己的推导思路…（云端自动保存）"
                @input="saveNotes"
              ></textarea>
            </div>
          </div>
        </template>
      </main>

      <!-- 右：AI助教答疑 + 分层练习 -->
      <aside ref="rightColEl" class="dcx-right">
        <div class="dcx-card dcx-tutor">
          <div class="dcx-card-title">
            💬 AI助教答疑
            <button class="dcx-ghost" title="重新开始对话" @click="resetTutor">↻</button>
          </div>
          <div class="dcx-upload" @click="photoInput?.click()">
            <span class="dcx-upico">☁️</span>
            <span class="dcx-uptxt">
              <b>上传我的题目</b>
              <small>拍照 / 截图 / 粘贴题目，AI助教为你讲解</small>
            </span>
          </div>
          <input ref="photoInput" type="file" accept="image/*" class="dcx-hidden" @change="onPhotoPicked" />
          <div v-if="photoResult" class="dcx-upload-confirm">
            <textarea v-model="photoResult.text" class="dcx-input dark" rows="3"></textarea>
            <div class="dcx-upload-actions">
              <button class="dcx-btn ghost-dark" @click="photoResult = null">取消</button>
              <button class="dcx-btn primary" @click="askTutorPhoto">🤖 让助教讲解</button>
            </div>
          </div>
          <div ref="chatListRef" class="dcx-msgs">
            <div v-if="!chatAsk.messages.value.length" class="dcx-msg-tip">
              没听懂？直接提问（自动携带本页上下文）；或点「🎯 随堂小测」来一道题。
            </div>
            <MessageBubble v-for="m in chatAsk.messages.value" :key="m.key" :msg="m" @quiz-answered="onQAnswer" />
            <div v-if="chatAsk.streaming.value" class="dcx-thinking"><span></span><span></span><span></span> AI 思考中…</div>
          </div>
          <div class="dcx-quick">
            <button class="dcx-qchip" @click="quickQuiz">🎯 随堂小测</button>
            <button class="dcx-qchip" @click="ask('这一页我没听懂，请讲细一点并给例子。')">🤔 没听懂</button>
            <button class="dcx-qchip" @click="ask('请总结本课要点并告诉我最易错的地方。')">📌 本课小结</button>
          </div>
          <div class="dcx-askrow">
            <input v-model="askDraft" class="dcx-input dark" placeholder="继续追问…" @keyup.enter="ask()" />
            <button class="dcx-send" :disabled="chatAsk.streaming.value" @click="ask()">➤</button>
          </div>
        </div>

        <div v-if="practice" ref="practiceEl" class="dcx-card dcx-practice" :class="{ 'dcx-practice--drawer': practiceDrawer }">
          <div class="dcx-card-title">
            🏋️ 分层练习
            <button v-if="practiceDrawer" class="dcx-ghost" title="收起练习" @click="practiceDrawer = false">收起 ✕</button>
          </div>
          <div class="dcx-tiers">
            <button
              v-for="t in TIERS" :key="t.key"
              class="dcx-tier" :class="{ on: practiceTier === t.key, empty: !(practice[t.key] || []).length }"
              @click="switchTier(t.key)"
            >{{ t.label }}</button>
          </div>
          <template v-if="currentPracticeQ">
            <div class="dcx-pq"><LatexText :text="wrapLatex(currentPracticeQ.question)" /></div>
            <div class="dcx-popts">
              <button
                v-for="(op, oi) in currentPracticeQ.options" :key="oi"
                class="dcx-popt" :class="optClass(oi)" :disabled="practicePicked !== null"
                @click="answerPractice(oi)"
              >
                <b>{{ 'ABCD'[oi] }}</b>
                <span><LatexText :text="wrapLatex(op)" /></span>
                <i v-if="practicePicked !== null && oi === currentPracticeQ.answer">✓</i>
                <i v-else-if="practicePicked === oi" class="bad">✗</i>
              </button>
            </div>
            <div v-if="practicePicked !== null && currentPracticeQ.analysis" class="dcx-pa">
              <b>解析</b><LatexText :text="currentPracticeQ.analysis" />
            </div>
          </template>
          <p v-else class="dcx-pempty">本档题目已练完，切换一档试试～</p>
          <div class="dcx-pfoot">
            <div class="dcx-ring" :style="ringStyle" title="总正确率：本档已作答题目的累计正确率"><span>{{ practiceAccuracyLabel }}%</span></div>
            <span class="dcx-pcount">{{ Math.min(practiceIndex + 1, tierQuestions.length) }} / {{ tierQuestions.length }}</span>
            <button class="dcx-btn primary" :disabled="!tierQuestions.length" @click="nextPractice">下一题</button>
          </div>
        </div>
        <div v-else-if="session?.status === 'ready' && practiceGeneration.state === 'pending'" class="dcx-card dcx-practice pending">
          <span class="dcx-spinner"></span> 练习题生成中，稍后自动出现…
        </div>
        <div v-else-if="session?.status === 'ready' && practiceGeneration.state === 'failed'" class="dcx-card dcx-practice pending">
          练习题暂时未生成：{{ practiceGeneration.error || '请刷新课堂后重试' }}
        </div>
        <div v-else-if="session?.status === 'ready' && !session?.practice" class="dcx-card dcx-practice pending">
          <template v-if="!practiceGaveUp">
            <span class="dcx-spinner"></span> 练习题正在恢复，请稍候…
          </template>
          <template v-else>本次练习未能生成（旧课堂生成时通道波动），新建课堂即可正常获得练习。</template>
        </div>
      </aside>
    </div>

    <!-- 课堂页但当前页缺失（正在生成） -->
    <main v-else-if="view === 'player'" class="dcx-creating">
      <div class="dcx-gen-card">
        <span class="dcx-spinner lg"></span>
        <b>本页生成中…</b>
        <p>已完成 {{ slidesSorted.length }} / {{ session?.slide_count }} 页；本页稍后自动出现。</p>
        <button v-if="slidesSorted.length" class="dcx-btn primary" @click="curIndex = 0">回到第 1 页</button>
      </div>
    </main>

    <!-- ===== 课堂报告 ===== -->
    <div v-if="reportOpen" class="dcx-modal-mask" @click.self="reportOpen = false">
      <div class="dcx-modal">
        <div class="dcx-modal-head">
          <b>📄 课堂报告</b>
          <button class="dcx-ghost" @click="reportOpen = false">✕</button>
        </div>
        <div class="dcx-report-grid">
          <div class="dcx-rstat"><b>{{ curIndex + 1 }} / {{ pageCount }}</b><span>学习进度（页）</span></div>
          <div class="dcx-rstat"><b>{{ clockLabel }}</b><span>本课用时</span></div>
          <div class="dcx-rstat"><b>{{ learnedMinutes }} 分钟</b><span>大纲计划（已翻页）</span></div>
          <div class="dcx-rstat"><b>{{ okPages() }} 页</b><span>已标记掌握</span></div>
          <div class="dcx-rstat"><b>{{ qaCount }} 条</b><span>助教问答</span></div>
          <div class="dcx-rstat"><b>{{ practiceAccuracyLabel }}%</b><span>分层练习正确率</span></div>
        </div>
        <div v-if="practice" class="dcx-report-tier">
          <div v-for="t in TIERS" :key="t.key" class="dcx-rtrow">
            <span>{{ t.label }}</span>
            <b>{{ practiceStats[t.key]?.correct || 0 }} / {{ practiceStats[t.key]?.total || 0 }}</b>
            <div class="dcx-bar slim"><div class="dcx-fill" :style="{ width: tierAccuracy(t.key) + '%' }"></div></div>
          </div>
        </div>
        <div class="dcx-report-notes" v-if="notes">
          <b>课堂笔记</b>
          <p>{{ notes.slice(0, 500) }}</p>
        </div>
        <div class="dcx-modal-foot">
          <button class="dcx-btn" @click="exportReport">⤓ 导出报告</button>
          <button class="dcx-btn primary" @click="reportOpen = false">继续上课</button>
        </div>
      </div>
    </div>

    <!-- ===== 课堂回顾 ===== -->
    <div v-if="reviewOpen" class="dcx-modal-mask" @click.self="reviewOpen = false">
      <div class="dcx-modal">
        <div class="dcx-modal-head">
          <b>🗂 课堂回顾</b>
          <button class="dcx-ghost" @click="reviewOpen = false">✕</button>
        </div>
        <div v-if="!historyItems.length" class="dcx-lib-empty">暂无历史课堂</div>
        <div class="dcx-review-list">
          <div v-for="s in historyItems" :key="s.session_id" class="dcx-review-item" @click="openSession(s); reviewOpen = false">
            <span class="dcx-card-icon">{{ sourceTypeMeta(s.source_type).icon }}</span>
            <div class="dcx-review-txt">
              <div class="dcx-review-name">{{ s.title }}</div>
              <div class="dcx-review-sub">{{ fmtTime(s.created_at) }} · 已学 {{ progressOf(s) }} / {{ s.slide_count }} 页</div>
            </div>
            <span class="dcx-review-go">回顾 →</span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { authHeaders } from '@/api/client'
import { useChat } from '@/composables/useChat'
import { useToastStore } from '@/stores/toast'
import MessageBubble from '@/components/chat/MessageBubble.vue'
import LatexText from '@/components/LatexText.vue'
import WhiteboardCanvas from '@/components/student/WhiteboardCanvas.vue'
import MathFigure3D from '@/components/chat/MathFigure3D.vue'
import GraphBlock from '@/components/chat/GraphBlock.vue'
import DynamicFigureViewer from '@/components/DynamicFigureViewer.vue'
import DeckSlideStage from '@/components/dual/DeckSlideStage.vue'
import { classroomApi, filesApi } from '@/api'
import { streamClassroomEvents } from '@/api/sse'
import MarkdownView from '@/components/MarkdownView.vue'
import { SKILL_ID_BY_KEY } from '@/config/skills'
import { useClassroomUpload, confirmClassroomPhotoParseQuality } from '@/composables/useClassroomUpload'
import { ensureVoices, pickChineseVoice, listChineseVoices, textToSpeech, getPreferredVoice, setPreferredVoice } from '@/utils/classroomTts'
import { buildTimeline, attachFocus, buildFocusKeys, estimateSpeakMs } from '@/utils/dualPlayback'
import teacherAvatar from '@/assets/dual/teacher.svg'
import tutorAvatar from '@/assets/dual/tutor.svg'
import studentAvatar from '@/assets/dual/student.svg'
import {
  groupSlideSections, splitSolutionSteps, practiceAccuracy, sourceTypeMeta,
  deriveSourceTitle, percent, fmtClock, fmtTime, practiceGenerationMeta,
} from '@/utils/dualClassroom'

const toast = useToastStore()
const route = useRoute()
const router = useRouter()

/* ==================== 视图状态（URL 驱动：/dual 与 /dual/:sessionId） ==================== */
const view = ref('home') // home | creating | player
const session = ref(null)
const generating = ref(false)
const curIndex = ref(0)

const slidesSorted = computed(() => {
  const arr = [...(session.value?.slides || [])]
  arr.sort((a, b) => Number(a.order || 0) - Number(b.order || 0))
  return arr
})
/* curIndex 的语义 = 大纲第几页（0 基）。slides 按前缀渐进落库，生成中会有
   "order=9 已到而 order=6 未到"的空洞期——若按数组下标取页，侧栏标题与
   画布内容会错位（用户看到"例题分析"的标题配"课堂小结"的内容）。
   一律按 outline.order 找页；无大纲的历史数据回退数组下标。 */
const pageCount = computed(() => (session.value?.outlines?.length || slidesSorted.value.length || 1))
const currentSlide = computed(() => {
  if (view.value !== 'player') return null
  const arr = slidesSorted.value
  if (!arr.length) return null
  const o = session.value?.outlines?.[curIndex.value]
  if (o) {
    const want = Number(o.order || curIndex.value + 1)
    const hit = arr.find((s) => Number(s.order) === want)
    if (hit) return hit
  }
  return arr[Math.min(curIndex.value, arr.length - 1)] || null
})
const sections = computed(() => groupSlideSections(currentSlide.value || {}))

/* ==================== 老师课堂（teacher_deck） ====================
 * 老师发布的课件会话：中央舞台用教师端 SlideCanvas 渲染 deck_slide，
 * 讲稿/播控/白板/笔记/练习/AI 助教沿用现有能力；老师内容不可被学生端改写。
 * 所有页面一律走同一渲染器（含稀疏页）—— 禁止在学生端另写第二套版面。 */
const isTeacherDeck = computed(
  () => session.value?.source_type === 'teacher_deck' && !!currentSlide.value?.deck_slide,
)
/* 「课堂 → 老师课堂」列表点开时的老师/班级名交接（sessionStorage，刷新仍可用）。
   来源仅用于顶部 badge；缺失则诚实省略，不猜测。 */
const teacherDeckHandoff = ref(null)
watch(
  () => session.value?.session_id,
  (sid) => {
    teacherDeckHandoff.value = null
    if (!sid) return
    try {
      const raw = sessionStorage.getItem('dcx-teacher-deck')
      if (!raw) return
      const h = JSON.parse(raw)
      if (h && String(h.sessionId) === String(sid)) teacherDeckHandoff.value = h
    } catch { /* 隐私模式 / 脏数据：badge 省略姓名 */ }
  },
  { immediate: true },
)
/** 老师 / 班级名（诚实来源：source_ref 优先，其次列表交接；缺失即省略）。 */
const teacherDeckMeta = computed(() => {
  const sr = session.value?.source_ref || {}
  const dm = session.value?.deck_meta || {}
  const ho = teacherDeckHandoff.value || {}
  return {
    teacherName: String(sr.teacher_name || session.value?.teacher_name || ho.teacherName || dm.teacher_name || '').trim(),
    className: String(sr.class_name || session.value?.class_name || ho.className || dm.class_name || '').trim(),
  }
})
const teacherDeckBadge = computed(() =>
  ['老师课件', teacherDeckMeta.value.teacherName, teacherDeckMeta.value.className].filter(Boolean).join(' · '),
)
const practiceGeneration = computed(() => practiceGenerationMeta(session.value?.verification))
/* 旧课堂（验证里无 practice_generation 状态且 practice 为空）：20s 内没恢复就明示
   "本次练习不可用"，绝不让学生面对一个永远转圈的占位卡。 */
const practiceGaveUp = ref(false)
let practiceWatchdog = null
watch(
  [() => session.value?.status, () => !!session.value?.practice, () => practiceGeneration.value.state],
  () => {
    clearTimeout(practiceWatchdog)
    practiceGaveUp.value = false
    const st = session.value?.status
    const pendingLike = st === 'ready' && !session.value?.practice
      && ['unknown', 'pending'].includes(practiceGeneration.value.state)
    if (pendingLike) practiceWatchdog = setTimeout(() => { practiceGaveUp.value = true }, 20000)
  },
  { immediate: true },
)
onBeforeUnmount(() => clearTimeout(practiceWatchdog))
/** S12（V2 文档）：失败原因对学生人性化——技术细节（断言式/函数名）不暴露 */
function friendlyError(raw) {
  const s = String(raw || '')
  if (/验证失败|数学|导数错误|声称|assert/.test(s)) return '部分页内容未通过数学自动校验（有误的讲解已被系统拦截），点「重新生成」即可重出一版'
  if (/通道|网络|超时|无任何内容块|生成异常|unavailable/i.test(s)) return 'AI 生成通道临时异常（多为网络波动），点「重新生成」即可'
  return '生成中断了，点「重新生成」再试一次'
}
/** 本页失败原因：生成通道异常（LLM 断连/空内容）≠ 数学校验未过，文案要分开 */
const pageFailFromGen = computed(() => {
  const d = String(currentSlide.value?.verification_result?.detail || '')
  return /无任何内容块|生成异常|生成失败|通道/.test(d)
})
const pg = computed(() => percent(curIndex.value + 1, pageCount.value || 1))
const inSession = computed(() => !!session.value && view.value !== 'home')

const MODE_LABELS = { sync: '同步课堂', review: '考前复习', topic: '专题精讲' }
function modeLabel(m) { return MODE_LABELS[m] || m || '课堂' }
const textbookCites = computed(() => {
  const cites = sections.value.textbook?.citations || []
  return cites.slice(0, 4).map((c) => {
    const parts = [c.book, c.volume, c.section, c.subsection]
      .filter(Boolean)
      .map((x) => String(x).replace(/（人教A版2019）|人教A版2019/g, '').trim())
      .filter(Boolean)
    return [...new Set(parts)].join(' · ') || String(c.title || '').slice(0, 24)
  })
})
/* ==================== 原题浮层 ==================== */
const originOpen = ref(false)
const originImgError = ref(false)
const originQuestion = computed(() => {
  const sr = session.value?.source_ref || {}
  const text = String(sr.source_text || sr.extracted_text || sr.content || '').trim()
  const fileId = sr.file_id ? String(sr.file_id) : ''
  return { text: text.slice(0, 2000), fileId, filename: String(sr.filename || ''), imgUrl: originImgUrl.value }
})
const originImgUrl = ref('')
watch(() => session.value?.source_ref?.file_id, async (fid) => {
  originImgUrl.value = ''
  originImgError.value = false
  if (!fid) return
  try {
    const d = await filesApi.contentUrl(String(fid))
    if (d?.url) originImgUrl.value = d.url
  } catch { /* 原图拉取失败静默：浮层仍显示题干 */ }
})

/* ==================== 顶栏弹出层 ==================== */
const outlinePop = ref(false)
const settingsOpen = ref(false)
const reportOpen = ref(false)
const reviewOpen = ref(false)

/* ==================== 创建配置 ==================== */
const genMode = ref('sync')
const genSlides = ref(10)
const genTopic = ref('')

/* ==================== 拍题 / 教案上传 ==================== */
const upload = useClassroomUpload(toast)
const photoInput = ref(null)
const homePhotoInput = ref(null)
const fileInput = ref(null)
const photoResult = ref(null)
const photoImgUrl = ref('')
const fileResult = ref(null)

function resetSourceForms() {
  photoResult.value = null
  fileResult.value = null
}
function cleanOcrText(t) {
  return String(t || '')
    .replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f]/g, '')
    .replace(/\\\(/g, '$').replace(/\\\)/g, '$')
    .replace(/\\\[/g, '$$').replace(/\\\]/g, '$$')
}
async function onPhotoPicked(e) {
  const raw = e.target.files?.[0]
  e.target.value = ''
  if (!raw) return
  const r = await upload.selectAndParse(raw)
  if (r) {
    photoResult.value = {
      filename: r.filename, file_id: r.fileId, text: cleanOcrText(r.text), parseQuality: r.parseQuality,
    }
    photoImgUrl.value = ''
    if (r.fileId) {
      try {
        const d = await filesApi.contentUrl(String(r.fileId))
        photoImgUrl.value = d?.url || ''
      } catch { /* 图片预览失败静默 */ }
    }
  }
}
async function onFilePicked(e) {
  const raw = e.target.files?.[0]
  e.target.value = ''
  if (!raw) return
  const r = await upload.selectAndParse(raw)
  if (r) {
    const desc = (r.text || '').slice(0, 3000)
    fileResult.value = {
      filename: r.filename, file_id: r.fileId,
      title: deriveSourceTitle(r.text, raw.name.replace(/\.(pdf|docx|pptx|txt|md)$/i, '')),
      text: desc,
    }
  }
}
function askTutorPhoto() {
  const text = (photoResult.value?.text || '').trim()
  if (!text) { toast.error('识别结果为空'); return }
  photoResult.value = null
  ask(`请讲解这道题（给出思路与完整解答步骤）：${text}`)
}

/* ==================== 生成课堂（OpenMAIC：首页只收集，创建后立刻跳生成页） ==================== */
async function createFromTopic() {
  if (generating.value) return
  const payload = { slide_count: Number(genSlides.value), mode: genMode.value, topic: genTopic.value.trim() }
  await createSession(payload, 'topic', null)
}
async function createFromPhoto() {
  const text = (photoResult.value?.text || '').trim()
  if (!text) { toast.error('识别结果为空，请先确认题目内容'); return }
  await createSession(
    { slide_count: Number(genSlides.value), mode: genMode.value, topic: text },
    'photo',
    {
      filename: photoResult.value.filename,
      file_id: photoResult.value.file_id,
      status: 'recognized',
      parse_quality: confirmClassroomPhotoParseQuality(photoResult.value.parseQuality),
    },
  )
}
async function createFromFile() {
  if (!fileResult.value) return
  const title = (fileResult.value.title || '').trim()
  const body = (fileResult.value.text || '').trim()
  if (!title && !body) { toast.error('请先填写课堂标题'); return }
  const payload = {
    slide_count: Number(genSlides.value), mode: genMode.value,
    topic: title || deriveSourceTitle(body),
    description: body ? `以下为本节课件（教案/文档）内容，请围绕它组织讲解：\n${body}` : undefined,
  }
  await createSession(payload, 'file', { filename: fileResult.value.filename, file_id: fileResult.value.file_id, status: 'parsed' })
}

async function createSession(payload, sourceType, sourceRef) {
  if (generating.value) return
  generating.value = true
  try {
    if (sourceType !== 'topic') payload.source_type = sourceType
    if (sourceRef) payload.source_ref = sourceRef
    const s = await classroomApi.createSession(payload)
    const d = s?.data || s
    resetSourceForms()
    genTopic.value = ''
    // URL 立即可见：刷新/分享可达（修"点击生成后没有跳转"）
    router.push(`/dual/${d.session_id}`)
  } catch (e) {
    toast.error(e?.message || '生成失败，请稍后重试')
  } finally {
    generating.value = false
  }
}

/* ==================== 生成进度：SSE 直播 + 轮询兜底 ==================== */
const genStage = ref('outline') // outline | content
const genOutlines = ref([])
const genDone = ref(0)
const genTotal = ref(0)
const genOutlineVisible = ref(0) // 大纲逐条入场动画
let pollTimer = null
let streamHandle = null
const autoEntered = ref(false)

const genStep = computed(() => {
  if (session.value?.status === 'ready' || session.value?.status === 'failed') return 3
  if (genStage.value === 'content') return 2
  return 1
})
const genPercent = computed(() => percent(genDone.value, genTotal.value || session.value?.slide_count || 1))
const firstSlideReady = computed(() => slidesSorted.value.length > 0)

/** 文档动画内的流式大纲行（genOutlineVisible 控制逐条入场） */
const streamedOutlineRows = computed(() => genOutlines.value
  .slice(0, Math.max(genOutlineVisible.value, 0))
  .map((o, i) => ({ key: o.order || i, idx: i, title: o.title || `第 ${i + 1} 页`, status: pageStatus(i) })))

function pageStatus(i) {
  const order = Number(genOutlines.value[i]?.order || i + 1)
  if (slidesSorted.value.some((s) => Number(s.order) === order)) return 'done'
  // 并行生成下以「第一个未完成页」为撰写中，其余排队（视觉时序更清晰）
  const firstUndone = genOutlines.value.findIndex((o, j) => {
    const ord = Number(o.order || j + 1)
    return !slidesSorted.value.some((s) => Number(s.order) === ord)
  })
  return i === firstUndone ? 'doing' : 'pending'
}

let revealTimer = null
function startOutlineReveal(total) {
  clearInterval(revealTimer)
  genOutlineVisible.value = 0
  revealTimer = setInterval(() => {
    genOutlineVisible.value += 1
    if (genOutlineVisible.value >= total) clearInterval(revealTimer)
  }, 130)
}

function upsertEventSlide(slide) {
  if (!slide || !session.value) return
  const arr = [...(session.value.slides || [])]
  const idx = arr.findIndex((s) => Number(s.order) === Number(slide.order))
  if (idx >= 0) arr[idx] = slide
  else arr.push(slide)
  arr.sort((a, b) => Number(a.order || 0) - Number(b.order || 0))
  session.value = { ...session.value, slides: arr }
}

function handleGenEvent(type, data, sid) {
  if (route.params.sessionId !== sid) return // 陈旧事件（已切课）忽略
  if (type === 'title') {
    if (session.value && data.title) session.value = { ...session.value, title: data.title }
  } else if (type === 'outlines') {
    genOutlines.value = data.outlines || []
    if (data.slide_count) genTotal.value = data.slide_count
    if (session.value) session.value = { ...session.value, outlines: genOutlines.value, slide_count: genTotal.value || session.value.slide_count }
    genStage.value = 'content'
    startOutlineReveal(genOutlines.value.length)
  } else if (type === 'slide') {
    upsertEventSlide(data.slide)
    genDone.value = Math.max(genDone.value, Number(data.completed) || slidesSorted.value.length)
    genTotal.value = Math.max(genTotal.value, Number(data.total) || 0)
    if (view.value === 'creating' && firstSlideReady.value && !autoEntered.value) {
      autoEntered.value = true
      setTimeout(() => { if (view.value === 'creating') enterPlayer() }, 900)
    }
  } else if (type === 'status') {
    if (data.stage === 'content') genStage.value = 'content'
    if (data.status === 'ready') {
      if (session.value) session.value = { ...session.value, status: 'ready', error: null }
      genDone.value = genTotal.value || genDone.value
      if (view.value === 'creating') enterPlayer()
      loadHistory()
    } else if (data.status === 'failed') {
      if (session.value) session.value = { ...session.value, status: 'failed', error: data.error || '生成失败' }
      toast.error(data.error || '课堂生成失败')
    }
  } else if (type === 'practice') {
    if (session.value) {
      const verification = { ...(session.value.verification || {}) }
      verification.practice_generation = { status: data.status || 'ready', error: data.error || null }
      session.value = { ...session.value, practice: data.practice, verification }
    }
  } else if (type === 'slide_regeneration' && data.status === 'ready' && data.slide) {
    upsertEventSlide(data.slide)
  }
}

function openEventStream(sid) {
  closeStream()
  let fellBack = false
  streamHandle = streamClassroomEvents(sid, {
    onEvent: (t, d) => handleGenEvent(t, d, sid),
  })
  streamHandle.finished
    .then(() => {
      // 服务端正常收流；若课还没到终态（兜底断开）→ 轮询续命
      if (!fellBack && session.value?.status === 'generating' && route.params.sessionId === sid) pollSession(sid)
    })
    .catch((err) => {
      if (fellBack || ctrlAborted(err)) return
      fellBack = true
      console.warn('[dual] SSE 不可用，回退轮询', err)
      pollSession(sid)
    })
}
function ctrlAborted(err) {
  return err?.name === 'AbortError' || String(err?.message || '').includes('abort')
}
function closeStream() {
  streamHandle?.abort?.()
  streamHandle = null
  clearTimeout(pollTimer)
}

async function pollSession(id) {
  clearTimeout(pollTimer)
  const tick = async () => {
    if (route.params.sessionId !== id || session.value?.status !== 'generating') return
    try {
      const s = await classroomApi.session(id)
      const d = s?.data || s
      if (route.params.sessionId !== id) return
      if (session.value?.session_id === id) {
        // 轮询路径只补状态与页，不覆盖 SSE 已合并的增量页
        const merged = [...(d.slides || [])]
        for (const ev of session.value.slides || []) {
          const i = merged.findIndex((x) => Number(x.order) === Number(ev.order))
          if (i >= 0) merged[i] = ev
          else merged.push(ev)
        }
        merged.sort((a, b) => Number(a.order || 0) - Number(b.order || 0))
        session.value = { ...d, slides: merged }
        if (d.outlines?.length && !genOutlines.value.length) {
          genOutlines.value = d.outlines
          genStage.value = 'content'
          startOutlineReveal(d.outlines.length)
        }
      }
      if (d.status === 'ready') {
        genDone.value = genTotal.value || d.slides?.length || genDone.value
        if (view.value === 'creating') enterPlayer()
        loadHistory()
        return
      }
      if (d.status === 'failed') {
        toast.error(d.error || '课堂生成失败')
        return
      }
    } catch { /* 网络抖动继续轮询 */ }
    pollTimer = setTimeout(tick, 3000)
  }
  pollTimer = setTimeout(tick, 1200)
}

/* ==================== 会话打开（路由驱动） ==================== */
async function openSessionById(id) {
  stopPlayback()
  closeStream()
  session.value = null
  curIndex.value = 0
  genStage.value = 'outline'
  genOutlines.value = []
  genDone.value = 0
  genTotal.value = 0
  autoEntered.value = false
  clearInterval(revealTimer)
  clearTimeout(progressTimer)
  try {
    const detail = await classroomApi.session(id)
    const d = detail?.data || detail
    session.value = d
    if (d.status === 'ready' && d.slides?.length) {
      syncFromDetail(d)
      view.value = 'player'
      return
    }
    if (d.status === 'failed') {
      view.value = 'creating'
      return
    }
    // generating：以 DB 现状为底，SSE 回放+直播续播
    genTotal.value = Math.max(d.slide_count || 0, (d.outlines || []).length)
    genDone.value = (d.slides || []).length
    if (d.outlines?.length) {
      genOutlines.value = d.outlines
      genStage.value = 'content'
      startOutlineReveal(d.outlines.length)
    }
    view.value = 'creating'
    openEventStream(id)
  } catch (e) {
    toast.error(e?.message || '课堂加载失败')
    router.replace('/dual')
  }
}

function enterPlayer() {
  if (!slidesSorted.value.length) return
  curIndex.value = 0
  view.value = 'player'
  resetTimer(session.value?.session_id)
  toast.success('课堂已就绪，开课！')
}

/* ==================== 历史课堂 ==================== */
const historyItems = ref([])
const historyLoading = ref(false)
const statusFilter = ref('')
const statusFilters = [
  { label: '全部', value: '' },
  { label: '已完成', value: 'ready' },
  { label: '备课中', value: 'generating' },
  { label: '失败', value: 'failed' },
]

async function loadHistory() {
  historyLoading.value = true
  try {
    const list = await classroomApi.sessions(statusFilter.value ? { status: statusFilter.value, limit: 50 } : { limit: 50 })
    const items = (list?.data || list)?.items || []
    historyItems.value = items
  } catch { /* 静默：首页历史非关键路径 */ } finally {
    historyLoading.value = false
  }
}

function progressIndex(s) {
  const idx = s.progress?.slide_index
  return Number.isInteger(idx) ? idx : -1
}
function progressOf(s) { const i = progressIndex(s); return i >= 0 ? i + 1 : 0 }
function cardProgress(s) {
  if (s.status === 'ready') return percent(progressOf(s), s.slide_count || 1)
  if (s.status === 'generating') return percent(s.slides_generated || 0, s.slide_count || 1)
  return 0
}
function cardProgressText(s) {
  if (s.status === 'ready') return `已学 ${progressOf(s)} / ${s.slide_count} 页`
  if (s.status === 'generating') return `已备好 ${s.slides_generated || 0} / ${s.slide_count} 页`
  return s.error ? String(s.error).slice(0, 30) : '生成失败'
}
function okPages() {
  const pc = session.value?.progress?.page_check
  return pc && typeof pc === 'object' ? Object.values(pc).filter((v) => v === 'ok').length : 0
}
function syncFromDetail(d) {
  session.value = d
  if (Number.isInteger(d.progress?.slide_index) && d.progress.slide_index < (d.slides?.length || 0)) {
    curIndex.value = d.progress.slide_index
  } else curIndex.value = 0
  notes.value = d.notes || ''
  practiceTier.value = 'basic'
  practiceIndex.value = 0
  practicePicked.value = null
  resetTimer(d.session_id)
}
function openSession(s) {
  router.push(`/dual/${s.session_id}`)
}
async function retryFailed(s) {
  toast.info('正在基于原标题重新生成…')
  try {
    const res = await classroomApi.createSession({ topic: s.title, slide_count: s.slide_count, mode: s.mode || 'sync' })
    const d = res?.data || res
    router.push(`/dual/${d.session_id}`)
    loadHistory()
  } catch (e) { toast.error(e?.message || '重新生成失败') }
}
async function cloneSession(s) {
  if (!window.confirm(`复制课堂「${s.title}」为新课？`)) return
  try {
    const res = await classroomApi.cloneSession(s.session_id)
    const d = res?.data || res
    toast.success('已复制为新课')
    router.push(`/dual/${d.session_id}`)
    loadHistory()
  } catch (e) { toast.error(e?.message || '复制失败') }
}
async function deleteSession(s) {
  if (!window.confirm(`确定删除课堂「${s.title}」？删除后不再展示。`)) return
  try {
    await classroomApi.deleteSession(s.session_id)
    historyItems.value = historyItems.value.filter((x) => x.session_id !== s.session_id)
    toast.success('已删除')
  } catch (e) { toast.error(e?.message || '删除失败') }
}

function goHome() {
  router.push('/dual')
}
function goBack() { router.push('/overview') }
/** 老师课堂 → 课堂任务（课后任务/作业入口，不复制任务列表）。 */
function goClassTasks() { router.push('/classroom?tab=tasks') }

/* 窄屏「随堂练习」：展开右栏既有练习卡为底部抽屉（不复制一套练习逻辑） */
const practiceEl = ref(null)
const rightColEl = ref(null)
const practiceDrawer = ref(false)
function jumpToPractice() {
  nextTick(() => {
    if (!practice.value) {
      rightColEl.value?.scrollIntoView({ behavior: 'smooth', block: 'center' })
      toast.info('本课随堂练习还未生成，稍后再试')
      return
    }
    practiceDrawer.value = true
    practiceEl.value?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    practiceEl.value?.classList.add('flash')
    setTimeout(() => practiceEl.value?.classList.remove('flash'), 1400)
  })
}

/* ==================== 翻页 / 页内互动 ==================== */
function jumpTo(i) {
  if (i === curIndex.value) return
  stopPlayback()
  curIndex.value = i
  syncProgress()
}
function go(delta) {
  const next = curIndex.value + delta
  if (next < 0 || next >= pageCount.value) return
  stopPlayback()
  curIndex.value = next
  syncProgress()
}
const selfCheck = ref('')
function markCheck(v) {
  if (selfCheck.value === v) return
  selfCheck.value = v
  syncProgress({ page_check: { [curIndex.value]: v } })
  if (v === 'again') toast.info('🔁 已标记重听，课后记得回看')
}
watch(curIndex, () => { selfCheck.value = '' })

/* ==================== 进度 / 笔记持久化 ==================== */
let progressTimer = null
function syncProgress(extra = {}) {
  clearTimeout(progressTimer)
  progressTimer = setTimeout(async () => {
    const sid = session.value?.session_id
    if (!sid) return
    try {
      await classroomApi.updateProgress(sid, { slide_index: curIndex.value, ...extra })
    } catch { /* 静默失败，不阻断学习 */ }
  }, 800)
}
const notes = ref('')
let notesTimer = null
function saveNotes() {
  clearTimeout(notesTimer)
  notesTimer = setTimeout(async () => {
    const sid = session.value?.session_id
    if (!sid) return
    try { await classroomApi.updateNotes(sid, notes.value) } catch { /* 离线降级 */ }
  }, 1000)
}

/* ==================== 课堂计时 ==================== */
const elapsedSec = ref(0)
let timerHandle = null
const clockLabel = computed(() => fmtClock(elapsedSec.value))
function timerKey(sid) { return `dcx-timer-${sid || 'none'}` }
function resetTimer(sid) {
  try { elapsedSec.value = Number(localStorage.getItem(timerKey(sid))) || 0 } catch { elapsedSec.value = 0 }
}
function startTimer() {
  clearInterval(timerHandle)
  timerHandle = setInterval(() => {
    elapsedSec.value += 1
    if (elapsedSec.value % 15 === 0) persistTimer()
  }, 1000)
}
function persistTimer() {
  const sid = session.value?.session_id
  if (!sid) return
  try { localStorage.setItem(timerKey(sid), String(elapsedSec.value)) } catch { /* 隐私模式静默 */ }
}
watch(view, (v) => { if (v === 'player') startTimer(); else { clearInterval(timerHandle); persistTimer(); practiceDrawer.value = false } })

/* ==================== 已学分钟（按大纲累计） ==================== */
const learnedMinutes = computed(() => {
  const os = session.value?.outlines || []
  let m = 0
  for (let i = 0; i <= Math.min(curIndex.value, os.length - 1); i++) m += Number(os[i]?.minutes) || 0
  return m || curIndex.value * 3
})

/* ==================== 全屏 ==================== */
const fullscreen = ref(false)
async function toggleFullscreen() {
  try {
    if (document.fullscreenElement) { await document.exitFullscreen(); fullscreen.value = false }
    else { await document.documentElement.requestFullscreen(); fullscreen.value = true }
  } catch { /* 浏览器不支持 */ }
}

/* ==================== 朗读设置 ==================== */
const speakRate = ref(1)
const autoRead = ref(false)
const voices = ref([])
const currentVoiceName = ref(getPreferredVoice() || '')
const preferredVoice = ref(getPreferredVoice())
const ttsVoice = ref('xiaoxiao')

async function loadVoices() {
  const list = await ensureVoices()
  voices.value = list
  setTimeout(async () => {
    const list2 = await ensureVoices(800)
    if (list2.length > list.length) voices.value = list2
  }, 500)
}
function onVoiceSelect() {
  preferredVoice.value = currentVoiceName.value
  setPreferredVoice(currentVoiceName.value)
  if (pb.on) { pbToken += 1; window.speechSynthesis?.cancel(); cleanupTtsAudio(); startPlayback(pb.seg) }
}

/* ==================== 播放引擎（OpenMAIC action 时间轴：逐句讲稿 + 聚光灯 + 板书节拍） ==================== */
const pb = reactive({ on: false, seg: 0 })
let pbToken = 0
const boardRevealed = ref(-1) // -1 = 全部可见（停止态）
const segAudioCache = new Map() // voice|text → blobURL/null
const ttsAudio = ref(null)
const ttsUrl = ref('')

const timeline = computed(() => attachFocus(
  buildTimeline(currentSlide.value?.narration, sections.value.formulas.length),
  buildFocusKeys(sections.value),
))
const currentFocus = computed(() => timeline.value[pb.seg]?.focus || null)
function isDim(key) {
  return pb.on && !!currentFocus.value && currentFocus.value !== key
}
const visibleWbSteps = computed(() => {
  const steps = sections.value.formulas
  if (boardRevealed.value < 0) return steps
  return steps.slice(0, Math.max(boardRevealed.value, 0))
})

function togglePlay() {
  if (pb.on) pausePlayback()
  else startPlayback(pb.seg || 0)
}
function pausePlayback() {
  pbToken += 1
  window.speechSynthesis?.cancel()
  cleanupTtsAudio()
  pb.on = false
}
function stopPlayback() {
  pbToken += 1
  window.speechSynthesis?.cancel()
  cleanupTtsAudio()
  pb.on = false
  pb.seg = 0
  boardRevealed.value = -1
}
function jumpSeg(i) {
  const k = Math.max(0, Math.min(timeline.value.length - 1, i))
  if (!timeline.value.length) return
  startPlayback(k)
}

async function startPlayback(fromSeg = 0) {
  if (!timeline.value.length) { toast.info('本页暂无讲稿'); return }
  pbToken += 1
  const token = pbToken
  window.speechSynthesis?.cancel()
  cleanupTtsAudio()
  pb.on = true
  pb.seg = Math.max(0, Math.min(fromSeg, timeline.value.length - 1))
  for (let k = pb.seg; k < timeline.value.length; k++) {
    if (token !== pbToken) return
    pb.seg = k
    boardRevealed.value = timeline.value[k].boardTo
    const spoken = await speakSegment(textToSpeech(timeline.value[k].text), token)
    if (token !== pbToken) return
    if (!spoken) await new Promise((r) => setTimeout(r, estimateSpeakMs(timeline.value[k].text, speakRate.value)))
    if (token !== pbToken) return
  }
  // 本页讲完
  boardRevealed.value = -1
  pb.on = false
  pb.seg = 0
  if (autoRead.value && curIndex.value < pageCount.value - 1) {
    go(1)
    startPlayback(0)
  }
}

/** 播一句：返回 true=有真实音频/语音结束；false=需静默节拍兜底 */
async function speakSegment(text, token) {
  if (!text) return false
  // ① 后端神经语音（句级缓存；OpenMAIC speech action 语义）
  const key = `${ttsVoice.value}|${text}`
  if (!segAudioCache.has(key)) {
    segAudioCache.set(key, await fetchSegmentAudio(text).catch(() => null))
  }
  if (token !== pbToken) return true
  const url = segAudioCache.get(key)
  if (url) {
    const played = await playAudioUrl(url, token)
    if (token !== pbToken) return true
    if (played) return true
  }
  // ② 浏览器 TTS
  const ok = await browserSpeakAsync(text, token)
  return ok
}

async function fetchSegmentAudio(text) {
  const resp = await fetch('/api/classroom/tts', {
    method: 'POST',
    headers: { ...authHeaders(), 'Content-Type': 'application/json' },
    body: JSON.stringify({ text: String(text).slice(0, 2000), voice: ttsVoice.value }),
  })
  if (!resp.ok) return null
  if (!(resp.headers.get('content-type') || '').includes('audio')) return null
  const blob = await resp.blob()
  return URL.createObjectURL(blob)
}

function playAudioUrl(url, token) {
  return new Promise((resolve) => {
    const audio = new Audio(url)
    audio.playbackRate = Number(speakRate.value) || 1
    ttsAudio.value = audio
    ttsUrl.value = url
    let settled = false
    const done = (ok) => { if (!settled) { settled = true; resolve(ok) } }
    audio.onended = () => { cleanupTtsAudio(); done(true) }
    audio.onerror = () => { cleanupTtsAudio(); done(false) }
    audio.play().catch(() => { cleanupTtsAudio(); done(false) })
    if (token !== pbToken) { audio.pause(); done(true) }
  })
}

function browserSpeakAsync(text, token) {
  return new Promise((resolve) => {
    if (!('speechSynthesis' in window)) { resolve(false); return }
    window.speechSynthesis.cancel()
    const u = new SpeechSynthesisUtterance(text)
    u.lang = 'zh-CN'
    u.rate = speakRate.value
    u.pitch = 1.08
    const v = pickChineseVoice(voices.value, preferredVoice.value)
    if (v) u.voice = v
    let settled = false
    const done = (ok) => { if (!settled) { settled = true; resolve(ok) } }
    u.onend = () => done(true)
    u.onerror = () => done(false)
    window.speechSynthesis.speak(u)
    if (token !== pbToken) { window.speechSynthesis.cancel(); done(true) }
  })
}

function cleanupTtsAudio() {
  if (ttsAudio.value) { ttsAudio.value.pause(); ttsAudio.value = null }
  if (ttsUrl.value) { URL.revokeObjectURL(ttsUrl.value); ttsUrl.value = '' }
}

/* ==================== 页内 AI 助教 ==================== */
const askDraft = ref('')
const chatAsk = useChat({
  workspace: 'student',
  manageConversations: false,
  sendThinking: true,
  hooks: { resolveSkills: (keys) => (keys || []).map((k) => SKILL_ID_BY_KEY[k] || k) },
})
function ensureConv() {
  if (!chatAsk.activeConvId.value) return chatAsk.createConversation('student').catch(() => {})
  return Promise.resolve()
}
function buildContext() {
  return session.value
    ? `我现在在第 ${curIndex.value + 1} 页「${currentSlide.value?.title || ''}」，正在学习「${session.value.title}」。请结合这一页的讲解为我答疑。`
    : ''
}
function ask(t) {
  const text = (typeof t === 'string' ? t : askDraft.value || '').trim()
  if (!text || chatAsk.streaming.value) return
  askDraft.value = ''
  ensureConv().then(() => chatAsk.doSend(buildContext() + text, { skills: [] }))
}
function resetTutor() {
  if (!window.confirm('清空当前助教对话，重新开始？')) return
  chatAsk.messages.value = []
  toast.info('已重新开始对话')
}
function focusAsk() {
  askDraft.value = askDraft.value || '这一页我没听懂，请再讲一遍并举例。'
  nextTick(() => document.querySelector('.dcx-askrow input')?.focus())
}
function quickQuiz() {
  const base = currentSlide.value?.title ? `基于「${currentSlide.value.title}」(第 ${curIndex.value + 1} 页)` : '基于本课'
  const text = base + '出一道高中数学选择题（给出选项与解析），适合课堂随堂小测。'
  if (chatAsk.streaming.value) return
  ensureConv().then(() => chatAsk.doSend(text, { skills: ['smart_quiz'] }))
}
function onQAnswer(p) { toast.info(p?.feedback === 'correct' ? '答对啦！' : '看下解析，再试一次') }
const chatListRef = ref(null)
watch(
  () => chatAsk.messages.value.length + (chatAsk.streaming.value ? 1 : 0),
  () => nextTick(() => { const el = chatListRef.value; if (el) el.scrollTop = el.scrollHeight }),
)

/* ==================== QA 摘要持久化 ==================== */
let lastQaCount = 0
watch(
  () => chatAsk.messages.value.length,
  () => {
    const sid = session.value?.session_id
    const msgs = chatAsk.messages.value
    if (!sid || msgs.length <= lastQaCount) return
    for (let i = lastQaCount; i < msgs.length; i++) {
      const m = msgs[i]
      if (!m || typeof m !== 'object') continue
      const role = m.role === 'user' ? 'user' : 'assistant'
      const text = m.text ? String(m.text).slice(0, 2000) : ''
      if (!text) continue
      classroomApi.appendQa(sid, { role, text }).catch(() => {})
    }
    lastQaCount = msgs.length
  },
)
const qaCount = computed(() => chatAsk.messages.value.length)

/* ==================== 白板页签 / 公式工具 ==================== */
const boardTab = ref('wb')
const sessionFormulas = computed(() => {
  const out = []
  for (const sld of session.value?.slides || []) {
    for (const b of sld?.blocks || []) {
      if (b && b.kind === 'latex' && b.latex) out.push({ latex: String(b.latex) })
    }
  }
  return out.slice(0, 20)
})
async function copyFormula(latex) {
  try {
    await navigator.clipboard.writeText(String(latex))
    toast.success('公式已复制')
  } catch { toast.error('复制失败，请手动选择') }
}

/* ==================== plot2d 适配 / LaTeX 包裹 ==================== */
function toGraphBlock(b) {
  const x0 = Number.isFinite(+b.x0) ? +b.x0 : -4
  const x1 = Number.isFinite(+b.x1) ? +b.x1 : 4
  const elements = [{ kind: 'functiongraph', parents: [b.expr] }]
  if (Array.isArray(b.marks)) {
    for (const m of b.marks) {
      if (Number.isFinite(+m.x)) elements.push({ kind: 'point', parents: [+m.x, 0] })
    }
  }
  return {
    schema: {
      graph_type: 'function_plot',
      elements,
      board: { boundingbox: [x0, 5, x1, -5] },
    },
  }
}
function wrapLatex(t) {
  const s = String(t || '')
  return s && !/[$\\]/.test(s) && /[-+^=]/.test(s) ? `$${s}$` : s
}
/** 板书/公式芯片用：剥掉 LLM 输出里残留的 $ 定界符再统一包裹，避免渲染出 "$}" 杂质 */
function wbLatex(t) {
  const s = String(t || '').trim().replace(/^\$\$?/, '').replace(/\$\$$/, '').replace(/}+$/, '}').trim()
  return '$' + s + '$'
}
function splitSteps(t) { return splitSolutionSteps(t) }

/* ==================== 分层练习 ==================== */
const TIERS = [
  { key: 'basic', label: '基础' },
  { key: 'advanced', label: '进阶' },
  { key: 'challenge', label: '挑战' },
]
const practice = computed(() => {
  const p = session.value?.practice
  return p && typeof p === 'object' ? p : null
})
const practiceStats = computed(() => session.value?.practice_stats || {})
const practiceTier = ref('basic')
const practiceIndex = ref(0)
const practicePicked = ref(null)
const tierQuestions = computed(() => practice.value?.[practiceTier.value] || [])
const currentPracticeQ = computed(() => tierQuestions.value[practiceIndex.value] || null)
const practiceAccuracyLabel = computed(() => practiceAccuracy(practiceStats.value))
const ringStyle = computed(() => {
  const deg = Math.round(360 * practiceAccuracyLabel.value / 100)
  return {
    background: `conic-gradient(#22c55e ${deg}deg, rgba(148,163,184,.25) ${deg}deg)`,
  }
})
function switchTier(key) {
  if (practiceTier.value === key) return
  practiceTier.value = key
  practiceIndex.value = 0
  practicePicked.value = null
}
function optClass(oi) {
  if (practicePicked.value === null || !currentPracticeQ.value) return ''
  if (oi === currentPracticeQ.value.answer) return 'right'
  if (oi === practicePicked.value) return 'wrong'
  return 'dim'
}
async function answerPractice(oi) {
  if (practicePicked.value !== null || !currentPracticeQ.value) return
  practicePicked.value = oi
  const correct = oi === currentPracticeQ.value.answer
  const sid = session.value?.session_id
  if (!sid) return
  try {
    const r = await classroomApi.answerPractice(sid, {
      tier: practiceTier.value,
      question_index: practiceIndex.value,
      correct,
    })
    const stats = r?.practice_stats || r?.data?.practice_stats
    if (stats && session.value) session.value = { ...session.value, practice_stats: stats }
  } catch { /* 统计失败不阻断答题 */ }
  if (!correct && currentPracticeQ.value?.analysis) toast.info('答错了，看一眼解析再下一题吧')
}
function nextPractice() {
  if (!tierQuestions.value.length) return
  practicePicked.value = null
  practiceIndex.value = (practiceIndex.value + 1) % tierQuestions.value.length
}
function tierAccuracy(key) {
  const t = practiceStats.value?.[key] || {}
  const total = Number(t.total) || 0
  const correct = Number(t.correct) || 0
  return total ? Math.round((correct / total) * 100) : 0
}

/* ==================== 以本页为主题派生新课堂 ==================== */
async function spawnFromPage() {
  const sld = currentSlide.value
  const sid = session.value?.session_id
  if (!sld || !sid) return
  const topic = `${sld.title}：${String(sld.narration || sections.value.goals.join('；')).slice(0, 120)}`.slice(0, 180)
  toast.info('已基于本页创建新课堂，开始备课…')
  await createSession({ slide_count: 8, mode: 'topic', topic }, 'topic', null)
}

/* ==================== 单页重新生成（OpenMAIC「失败页重试」模式） ==================== */
const regenLoading = ref(false)
async function regenSlide() {
  const sid = session.value?.session_id
  if (!sid || regenLoading.value) return
  regenLoading.value = true
  toast.info('AI 正在换一种讲法重出本页（约 1~2 分钟）…')
  try {
    await classroomApi.regenSlide(sid, curIndex.value + 1)
    const order = curIndex.value + 1
    for (let i = 0; i < 75; i++) {
      await new Promise((r) => setTimeout(r, 2000))
      const d = await classroomApi.session(sid)
      const det = d?.data || d
      if (session.value?.session_id === sid && det?.slides?.length) {
        const kept = session.value.slides || []
        const merged = [...(det.slides || [])]
        for (const s of kept) {
          const j = merged.findIndex((x) => Number(x.order) === Number(s.order))
          const regenOrder = order
          if (j < 0 && Number(s.order) !== regenOrder) merged.push(s)
        }
        merged.sort((a, b) => Number(a.order || 0) - Number(b.order || 0))
        session.value = { ...session.value, slides: merged, verification: det.verification || session.value.verification }
        if (curIndex.value >= merged.length) curIndex.value = 0
      }
      const state = det?.verification?.slide_regeneration?.[String(order)]
      if (state?.status === 'ready') {
        toast.success('本页已重新生成')
        return
      }
      if (state?.status === 'failed') throw new Error(state.error || '本页重新生成失败')
    }
    throw new Error('本页仍在生成，请稍后刷新课堂查看结果')
  } catch (e) {
    toast.error(e?.message || '重新生成失败，请稍后重试')
  } finally {
    regenLoading.value = false
  }
}

/* ==================== 导出课件 / 导出报告 ==================== */
// PHASE 5：离屏挂载 MathFigure3D 渲染图形帧 → PNG dataURL（导出全页图形覆盖）
async function captureFigure(figureSpec) {
  const { createApp } = await import('vue')
  const { default: Fig3D } = await import('@/components/chat/MathFigure3D.vue')
  const host = document.createElement('div')
  host.style.cssText = 'position:fixed;left:-9999px;top:0;width:860px;height:520px;background:#ffffff;'
  document.body.appendChild(host)
  const app = createApp(Fig3D, { figure: figureSpec, height: 500 })
  try {
    const inst = app.mount(host)
    await new Promise((r) => setTimeout(r, 800))  // three.js init + 首帧
    return inst?.toDataURL?.() || ''
  } finally {
    app.unmount()
    host.remove()
  }
}
async function exportCourseware() {
  const s = session.value
  if (!s?.slides?.length) return
  // PHASE 5：导出前离屏渲染各页 geometry 图形为 PNG（three.js toDataURL，全页覆盖）
  toast.info('正在渲染课件图形…')
  const figureCaptures = new Map()
  for (const sld of slidesSorted.value) {
    for (let bi = 0; bi < (sld.blocks || []).length; bi++) {
      const b = sld.blocks[bi]
      if (b.kind !== 'geometry' || !b.figure) continue
      try {
        const u = await captureFigure(b.figure)
        if (u) figureCaptures.set(`${sld.order}:${bi}`, u)
      } catch { /* 单图失败保持占位文案 */ }
    }
  }
  const esc = (t) => String(t || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  const slideHtml = slidesSorted.value.map((sld) => {
    const rows = (sld.blocks || []).map((b, bIndex) => {
      if (b.kind === 'text') return `<p>${esc(b.text)}</p>`
      if (b.kind === 'theorem') return `<div class="thm"><b>${esc(b.title || '定理')}</b><p>${esc(b.body)}</p></div>`
      if (b.kind === 'latex') return `<div class="formula">$$${esc(b.latex)}$$</div>`
      if (b.kind === 'example') {
        return `<div class="ex"><b>例题</b><p>${esc(b.question)}</p><p>解：${esc(b.analysis)}</p><p>答：${esc(b.answer)}</p></div>`
      }
      if (b.kind === 'table') {
        const head = (b.headers || []).map((h) => `<th>${esc(h)}</th>`).join('')
        const body = (b.rows || []).map((r) => `<tr>${r.map((c) => `<td>${esc(c)}</td>`).join('')}</tr>`).join('')
        return `<table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`
      }
      if (b.kind === 'note') return `<div class="note">💡 ${esc(b.text)}</div>`
      if (b.kind === 'plot2d') return `<div class="fig">📈 ${esc(b.caption || b.expr || '')}</div>`
      if (b.kind === 'geometry') {
        const u = figureCaptures.get(`${sld.order}:${bIndex}`) || figureCaptures.get(`${sld.order}:any`)
        return u
          ? `<div class="fig"><img src="${u}" style="max-width:100%;border-radius:8px;" /></div>`
          : `<div class="fig">🧊 ${esc(b.caption || '几何图形（交互版见课堂页面）')}</div>`
      }
      return ''
    }).join('')
    return `<section><h2>第 ${sld.order} 页 · ${esc(sld.title)}</h2><p class="sub">${esc(sld.subtitle || '')}</p>${rows}</section>`
  }).join('')
  const w = window.open('', '_blank')
  if (!w) { toast.error('浏览器拦截了导出窗口'); return }
  w.document.write(`<!doctype html><html lang="zh"><head><meta charset="utf-8"><title>${esc(s.title)} · 导出课件</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.css">
<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.js"><\/script>
<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/contrib/auto-render.min.js" onload="renderMathInElement(document.body,{delimiters:[{left:'$$',right:'$$',display:true},{left:'$',right:'$',display:false}]});window.print()"><\/script>
<style>body{font-family:'PingFang SC','Microsoft YaHei',sans-serif;max-width:860px;margin:24px auto;padding:0 16px;color:#111827;line-height:1.8}
h1{text-align:center}h2{border-bottom:2px solid #6366f1;padding-bottom:6px;margin-top:36px}
.sub{color:#64748b}.thm{border:1px solid #bfdbfe;background:#eff6ff;border-radius:8px;padding:10px 14px}
.ex{border:1px solid #bbf7d0;background:#f0fdf4;border-radius:8px;padding:10px 14px}
.note{background:#fffbeb;border-radius:8px;padding:8px 12px}.formula{text-align:center}
table{border-collapse:collapse;margin:10px auto}th,td{border:1px solid #cbd5e1;padding:6px 14px;text-align:center}
.fig{color:#475569;border:1px dashed #cbd5e1;border-radius:8px;padding:8px 12px}
section{page-break-after:always}
/* ===== P1-16B 一屏适配（1920×1080 无滚动；等比压缩间距/字号/图形，不裁剪内容） ===== */
.dcx-body { height: calc(100vh - 58px); padding: 8px 10px; gap: 10px; align-items: stretch; overflow: hidden; }
.dcx-left, .dcx-right { position: static; top: auto; max-height: none; height: 100%; display: flex; flex-direction: column; gap: 8px; overflow: hidden; }
.dcx-center { min-height: 0; overflow: hidden; padding: 0; }
.dcx-card { padding: 10px; }
.dcx-card-title { font-size: 12.5px; margin-bottom: 6px; }
.dcx-oitem { padding: 3px 6px; font-size: 11.5px; }
.dcx-ono { width: 18px; height: 18px; font-size: 10px; }
.dcx-stage-wrap { flex: 1; min-height: 0; }
.dcx-slide { padding: 12px 16px; min-height: 0; font-size: 13px; }
.dcx-ltitle { font-size: 16px !important; }
.dcx-lgrid { gap: 10px; }
.dcx-lmain .dcx-para, .dcx-lmain .dcx-theorem-body, .dcx-lmain .dcx-exq,
.dcx-lmain .dcx-step, .dcx-lmain .dcx-conclusion, .dcx-lmain .dcx-answer,
.dcx-lmain table, .dcx-lmain .dcx-table td, .dcx-lmain .dcx-table th { font-size: 12.5px; line-height: 1.5; }
.dcx-theorem, .dcx-example, .dcx-conclusion, .dcx-para { margin-bottom: 6px; padding: 6px 8px; }
.dcx-lfig { max-height: 250px; }
.dcx-lfig :deep(canvas), .dcx-lfig :deep(.wb) { max-height: 240px; }
.dcx-teach { padding-top: 6px; }
.dcx-narr-lines { max-height: 96px; font-size: 12px; line-height: 1.55; overflow: hidden; }
.dcx-controls { padding: 4px 2px; }
.dcx-board { max-height: 150px; }
.dcx-wb :deep(canvas) { max-height: 104px; }
.dcx-msgs { font-size: 12px; }

/* ===== P1-17 双师课堂·一屏适配（严格高度预算；压缩不裁剪；无任何滚动条；对比度达标） ===== */
/* 顶栏 56px，标题不换行 */
.dcx-top { height: 56px; padding: 0 14px; }
.dcx-brand b { font-size: 16px; white-space: nowrap; }
.dcx-course-name { max-width: 360px; }

/* 主体三列：高度=视口-顶栏，各列等高撑满，禁页面滚动 */
.dcx-body {
  height: calc(100vh - 56px); padding: 8px 10px; gap: 8px;
  align-items: stretch; overflow: hidden;
}
.dcx-left, .dcx-right {
  position: static; top: auto; max-height: none; height: 100%;
  display: flex; flex-direction: column; gap: 8px; overflow: hidden;
}
.dcx-center { min-height: 0; overflow: hidden; padding: 0; display: flex; flex-direction: column; gap: 8px; }

/* ===== 卡片/间距压缩 ===== */
.dcx-card { padding: 10px; }
.dcx-card-title { font-size: 13px; font-weight: 800; color: #1f2937; margin-bottom: 6px; }

/* ===== 左列：大纲（从顶开始、条目完整显示、11px、可换行、禁省略号、无滚动） ===== */
.dcx-left .dcx-card { overflow: hidden; }
.dcx-oitem { padding: 4px 6px; font-size: 11px; gap: 7px; border-radius: 7px; color: #374151; }
.dcx-oitem.done { color: #6b7280; }
.dcx-ono { width: 18px; height: 18px; font-size: 10px; }
.dcx-ot {
  flex: 1; min-width: 0; overflow: visible; text-overflow: clip !important;
  white-space: normal !important; line-height: 1.35; word-break: break-word;
}
.dcx-oitem.active { font-weight: 800; }
.dcx-oskeleton, .dcx-o-skel-lines { display: none; }
.dcx-links .dcx-side-link, .dcx-learned, .dcx-goal { font-size: 11.5px; color: #374151; }
.dcx-side-link { font-size: 12px; color: #4f46e5; }

/* ===== 中列：课件 340 + 讲稿 130 + 播控 44 + 底部 300 ===== */
.dcx-stage-wrap { flex: 0 0 340px; min-height: 0; position: relative; }
.dcx-slide {
  height: 100%; padding: 10px 14px; font-size: 12.5px; line-height: 1.5;
  overflow: hidden; min-height: 0;
}
.dcx-ltitle { font-size: 15px !important; margin: 0 0 4px; line-height: 1.3; }
.dcx-ltitle small { font-size: 12px; color: #6b7280; }
.dcx-lgrid { gap: 10px; align-items: stretch; }
.dcx-lmain .dcx-para, .dcx-lmain .dcx-theorem-body, .dcx-lmain .dcx-exq,
.dcx-lmain .dcx-step, .dcx-lmain .dcx-conclusion, .dcx-lmain .dcx-answer,
.dcx-lmain table { font-size: 12px; line-height: 1.5; color: #374151; }
.dcx-theorem, .dcx-example, .dcx-conclusion, .dcx-para { margin-bottom: 6px; padding: 6px 8px; }
.dcx-lfig { max-height: 210px; }
.dcx-lfig :deep(canvas) { max-height: 200px; }
.dcx-figcap { font-size: 11px; color: #6b7280; }

/* 讲稿 130 */
.dcx-teach { flex: 0 0 130px; min-height: 0; gap: 8px; padding: 6px 8px; border-radius: 12px; background: var(--card); border: 1px solid var(--line); }
.dcx-tavatar { width: 36px !important; height: 36px !important; }
.dcx-tbubble { min-width: 0; }
.dcx-tbname { font-size: 12px; color: #1f2937; }
.dcx-tbstate { font-size: 11px; color: #4f46e5; }
.dcx-narr-lines {
  max-height: 76px; font-size: 12px; line-height: 1.5; color: #374151;
  overflow: hidden; display: -webkit-box; -webkit-line-clamp: 4; -webkit-box-orient: vertical;
}
.dcx-tutor-mini { font-size: 11px; color: #374151; }

/* 播控 44 */
.dcx-controls { flex: 0 0 44px; min-height: 0; padding: 4px 6px; }
.dcx-ctl { font-size: 12px; height: 28px; min-width: 26px; }
.dcx-ctl-page, .dcx-foot-l { font-size: 11.5px; color: #374151; }
.dcx-bar.slim { height: 4px; }
.dcx-chipbtn { font-size: 11px; padding: 3px 8px; color: #374151; }

/* 底部功能 300（tabs 32 + 内容 268） */
.dcx-board { flex: 0 0 300px; min-height: 0; display: flex; flex-direction: column; }
.dcx-btabs { flex: 0 0 30px; display: flex; align-items: center; gap: 6px; }
.dcx-btabs button { font-size: 12px; padding: 4px 10px; color: #374151; }
.dcx-btabs button.on { background: #eef2ff; color: #4f46e5; font-weight: 700; }
.dcx-wb, .dcx-tools, .dcx-notes { flex: 1; min-height: 0; font-size: 12px; color: #374151; }
.dcx-wb :deep(canvas) { max-height: 190px; }
.dcx-wbsteps { font-size: 12px; color: #374151; }

/* ===== 右列：AI助教（上传100 + 快捷44 + 对话flex1 + 输入44） ===== */
.dcx-tutor { flex: 1 1 auto; min-height: 0; display: flex; flex-direction: column; }
.dcx-upload { flex: 0 0 76px; padding: 8px 10px; }
.dcx-uptxt b { font-size: 12.5px; color: #1f2937; }
.dcx-uptxt small { font-size: 11px; color: #6b7280; }
.dcx-quick { flex: 0 0 38px; }
.dcx-qchip { font-size: 11px; padding: 5px 8px; color: #4f46e5; }
.dcx-askrow { flex: 0 0 40px; }
.dcx-msgs { flex: 1; min-height: 0; font-size: 12px; color: #374151; overflow: hidden; }
.dcx-msg-tip { font-size: 11.5px; color: #6b7280; }
.dcx-practice { flex: 0 0 auto; }
.dcx-tiers .dcx-tier { font-size: 11px; color: #374151; }
.dcx-input, .dcx-input.dark { color: #374151; }
.dcx-input::placeholder, .dcx-input.dark::placeholder, textarea.dcx-input.dark::placeholder { color: #9ca3af; }

/* 通用：正文主色加深 */
.dcx-note, .dcx-foot-q, .dcx-modal p, .dcx-para:not([class]) { color: #374151; }


</style></head><body>
<h1>${esc(s.title)}</h1><p style="text-align:center;color:#64748b">${esc(modeLabel(s.mode))} · 共 ${s.slides.length} 页</p>
${slideHtml}</body></html>`)
  w.document.close()
}
function exportReport() {
  const lines = [
    `课堂报告：${session.value?.title || ''}`,
    `学习进度：第 ${curIndex.value + 1} / ${pageCount.value || 0} 页`,
    `本课用时：${clockLabel.value}`,
    `已标记掌握：${okPages()} 页`,
    `助教问答：${qaCount.value} 条`,
    `分层练习正确率：${practiceAccuracyLabel.value}%`,
    '',
    notes.value ? `课堂笔记：\n${notes.value}` : '',
  ].filter(Boolean)
  const w = window.open('', '_blank')
  if (!w) return
  w.document.write(`<pre style="font-family:inherit;font-size:15px;white-space:pre-wrap;line-height:1.9">${lines.join('\n').replace(/</g, '&lt;')}</pre>`)
  w.document.close()
  w.print()
}

/* ==================== 键盘 / 生命周期 ==================== */
function onKey(e) {
  if (e.key === 'ArrowRight') go(1)
  else if (e.key === 'ArrowLeft') go(-1)
  else if (e.key === 'Escape') { settingsOpen.value = false; outlinePop.value = false; reportOpen.value = false; reviewOpen.value = false; practiceDrawer.value = false }
}

// 路由驱动视图切换。注意必须放在脚本尾部：immediate 首跑在 setup 期同步执行，
// 过早注册会踩到播放引擎（pbToken 等）的暂时性死区。
watch(
  () => route.params.sessionId,
  (id) => {
    if (id) openSessionById(String(id))
    else {
      stopPlayback()
      closeStream()
      session.value = null
      view.value = 'home'
      loadHistory()
    }
  },
  { immediate: true },
)

onMounted(() => {
  loadHistory()
  loadVoices()
  window.addEventListener('keydown', onKey)
})
onBeforeUnmount(() => {
  pbToken += 1
  closeStream()
  clearTimeout(progressTimer)
  clearTimeout(notesTimer)
  clearInterval(timerHandle)
  clearInterval(revealTimer)
  persistTimer()
  window.speechSynthesis?.cancel()
  cleanupTtsAudio()
  window.removeEventListener('keydown', onKey)
})
</script>

<style scoped>
.dcx-shell {
  height: 100vh; display: flex; flex-direction: column; overflow: hidden;
  background: var(--bg); color: var(--ink);
  font-family: 'PingFang SC', 'Microsoft YaHei', 'Segoe UI', sans-serif;
}
.dcx-hidden { display: none; }
button { font-family: inherit; }

/* ===== 顶栏（深色玻璃） ===== */
.dcx-top {
  display: flex; align-items: center; gap: 14px; padding: 0 18px; height: 58px; flex-shrink: 0;
  background: var(--card); border-bottom: 1px solid var(--line);
  backdrop-filter: blur(14px); position: relative; z-index: 42;
}
.dcx-brand { display: flex; align-items: center; gap: 8px; cursor: pointer; user-select: none; }
.dcx-logo {
  width: 34px; height: 34px; border-radius: 10px; display: grid; place-items: center; font-size: 18px;
  background: linear-gradient(135deg, #4f46e5, #818cf8); color: #fff;
  box-shadow: 0 3px 12px rgba(79, 70, 229, .5);
}
.dcx-brand b { font-size: 16px; letter-spacing: .5px; }
.dcx-vline { width: 1px; height: 24px; background: rgba(255, 255, 255, .12); }
.dcx-course { display: flex; align-items: center; gap: 10px; min-width: 0; }
.dcx-course-name { font-size: 14.5px; font-weight: 800; max-width: 300px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dcx-pagechip { font-size: 12px; font-weight: 800; color: #374151; background: rgba(99, 102, 241, .18); border: 1px solid rgba(99, 102, 241, .4); border-radius: 8px; padding: 3px 9px; }
.dcx-pagechip.generating { color: #ffd591; background: rgba(250, 173, 20, .12); border-color: rgba(250, 173, 20, .4); }
/* 老师课堂来源徽标（teacher_deck）：只展示真实来源，缺老师/班级名时省略 */
.dcx-deckbadge {
  flex: none; max-width: 320px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  font-size: 11.5px; font-weight: 800; color: #4f46e5; background: #eef2ff;
  border: 1px solid #c7d2fe; border-radius: 999px; padding: 3px 10px;
}
.dcx-course-sub { font-size: 12px; color: var(--ink3); }
.dcx-top-right { margin-left: auto; display: flex; align-items: center; gap: 9px; }
.dcx-timer {
  font-variant-numeric: tabular-nums; font-weight: 800; font-size: 13px; color: #374151;
  background: rgba(99, 102, 241, .15); border: 1px solid rgba(99, 102, 241, .35); border-radius: 9px; padding: 6px 12px; letter-spacing: 1px;
}
.dcx-round {
  width: 34px; height: 34px; border-radius: 50%; border: 1px solid rgba(255, 255, 255, .14); background: rgba(255, 255, 255, .04); cursor: pointer;
  font-size: 13px; color: #4b5563; display: grid; place-items: center; transition: all .15s ease;
}
.dcx-round:hover:not(:disabled) { border-color: #6366f1; color: #374151; }
.dcx-round.on { background: rgba(99, 102, 241, .25); border-color: #6366f1; color: var(--brand-deep); }
.dcx-round:disabled { opacity: .4; cursor: not-allowed; }
.dcx-tbtn {
  border: 1px solid rgba(255, 255, 255, .14); background: rgba(255, 255, 255, .04); color: #4b5563; border-radius: 9px; padding: 8px 14px;
  font-size: 12.5px; font-weight: 700; cursor: pointer; transition: all .15s ease; white-space: nowrap;
}
.dcx-tbtn:hover:not(:disabled) { border-color: #6366f1; color: var(--brand-deep); }
.dcx-tbtn.on { background: rgba(99, 102, 241, .25); border-color: #6366f1; color: var(--brand-deep); }
.dcx-tbtn.solid { background: linear-gradient(135deg, #4f46e5, #6366f1); border-color: transparent; color: #fff; box-shadow: 0 3px 12px rgba(79, 70, 229, .45); }
.dcx-tbtn.solid:hover { filter: brightness(1.1); color: #fff; }
.dcx-avatar {
  width: 38px; height: 38px; border-radius: 50%; overflow: hidden; flex-shrink: 0;
  background: linear-gradient(135deg, #3b2d63, #553c8f); border: 1px solid rgba(255, 255, 255, .16);
}
.dcx-settings { position: relative; }
.dcx-menu {
  position: absolute; right: 0; top: calc(100% + 8px); z-index: 61; min-width: 270px;
  background: var(--card); border: 1px solid var(--line); border-radius: 12px; box-shadow: 0 16px 40px rgba(0, 0, 0, .5);
  padding: 14px; display: flex; flex-direction: column; gap: 11px;
}
.dcx-menu-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; font-size: 12.5px; font-weight: 700; color: #374151; }
.dcx-menu-row select { width: 150px; height: 32px; }
.dcx-menu-row.check { cursor: pointer; justify-content: flex-start; gap: 8px; }
.dcx-clickmask { position: fixed; inset: 0; z-index: 41; }

/* ===== 原题浮层 ===== */
.dcx-origin {
  position: fixed; top: 66px; right: 18px; z-index: 55; width: 380px; max-height: 60vh;
  background: var(--card); border: 1px solid var(--line); border-radius: 14px; box-shadow: 0 16px 40px rgba(0, 0, 0, .5);
  display: flex; flex-direction: column; overflow: hidden;
}
.dcx-origin-head { display: flex; align-items: center; justify-content: space-between; padding: 10px 14px; border-bottom: 1px solid var(--line); font-size: 13px; }
.dcx-origin-body { padding: 12px 14px; overflow-y: auto; font-size: 13px; line-height: 1.75; }
.dcx-origin-img { max-width: 100%; border-radius: 8px; margin-top: 8px; }

/* ===== 光斑背景（OpenMAIC 首页视觉） ===== */
.dcx-orb { position: fixed; border-radius: 50%; filter: blur(90px); pointer-events: none; z-index: 0; }
.dcx-orb.o1 { width: 480px; height: 480px; left: 12%; top: -140px; background: rgba(59, 82, 214, .34); animation: orbfloat 9s ease-in-out infinite; }
.dcx-orb.o2 { width: 540px; height: 540px; right: 6%; bottom: -180px; background: rgba(79, 70, 229, .3); animation: orbfloat 11s ease-in-out infinite reverse; }
@keyframes orbfloat { 0%, 100% { transform: translateY(0); } 50% { transform: translateY(28px); } }

/* ==================== 首页 ==================== */
.dcx-home-page { flex: 1; overflow-y: auto; position: relative; }
.dcx-home-inner { position: relative; z-index: 1; max-width: 1080px; margin: 0 auto; padding: 34px clamp(16px, 4vw, 40px) 48px; display: flex; flex-direction: column; gap: 22px; }
.dcx-hero { text-align: center; display: flex; flex-direction: column; align-items: center; gap: 6px; }
.dcx-hero-logo {
  width: 58px; height: 58px; border-radius: 16px; display: grid; place-items: center; font-size: 30px; margin-bottom: 8px;
  background: linear-gradient(135deg, #4f46e5, #818cf8); box-shadow: 0 10px 34px rgba(79, 70, 229, .55);
}
.dcx-hero h1 { margin: 0; font-size: 26px; font-weight: 900; letter-spacing: 1px; }
.dcx-hero p { margin: 0; font-size: 13px; line-height: 1.8; color: #4b5563; max-width: 760px; }

.dcx-composer {
  background: rgba(22, 27, 54, .72); border: 1px solid rgba(255, 255, 255, .1); border-radius: 20px;
  padding: 18px 20px; backdrop-filter: blur(18px); box-shadow: 0 18px 50px rgba(0, 0, 0, .35);
}
.dcx-topic {
  width: 100%; border: none; background: transparent; color: var(--ink); font: inherit; font-size: 14.5px; line-height: 1.8;
  resize: vertical; min-height: 96px; outline: none;
}
.dcx-topic::placeholder { color: #6b7280; }
.dcx-composer-bar { display: flex; align-items: center; gap: 12px; margin-top: 10px; padding-top: 12px; border-top: 1px solid var(--line); }
.dcx-composer-bar label { display: flex; align-items: center; gap: 7px; font-size: 12px; font-weight: 700; color: #4b5563; }
.dcx-composer-bar select { width: 118px; height: 34px; }
.dcx-composer-hint { margin-left: auto; font-size: 11.5px; color: #6b7280; }

.dcx-alt-entries { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.dcx-entry {
  background: rgba(22, 27, 54, .55); border: 1px solid rgba(99, 102, 241, .18); border-radius: 16px; padding: 16px 18px;
  display: flex; flex-direction: column; gap: 10px; backdrop-filter: blur(10px);
}
.dcx-entry-head { font-size: 14px; font-weight: 800; }
.dcx-tip { font-size: 12px; color: #8a92b5; line-height: 1.65; margin: 0; }

.dcx-library { display: flex; flex-direction: column; gap: 14px; }
.dcx-library-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
.dcx-library-head h2 { margin: 0; font-size: 17px; font-weight: 900; }
.dcx-filters { display: flex; gap: 8px; }
.dcx-filter {
  font-size: 12px; font-weight: 700; color: #4b5563; border: 1px solid var(--line); border-radius: 999px;
  padding: 6px 14px; cursor: pointer; background: rgba(255, 255, 255, .03);
}
.dcx-filter.on { background: rgba(99, 102, 241, .3); border-color: #6366f1; color: var(--brand-deep); }
.dcx-lib-empty {
  border: 1px dashed rgba(255, 255, 255, .16); border-radius: 16px; padding: 34px 16px; text-align: center;
  color: #8a92b5; font-size: 13px; display: flex; align-items: center; justify-content: center; gap: 10px;
}
.dcx-lib-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 14px; }
.dcx-card {
  background: rgba(22, 27, 54, .6); border: 1px solid rgba(99, 102, 241, .18); border-radius: 16px; padding: 15px 16px;
  cursor: pointer; transition: all .16s ease; display: flex; flex-direction: column; gap: 9px;
}
.dcx-card:hover { border-color: rgba(99, 102, 241, .6); transform: translateY(-2px); box-shadow: 0 12px 30px rgba(0, 0, 0, .4); }
.dcx-card-top { display: flex; align-items: center; justify-content: space-between; }
.dcx-card-icon { font-size: 18px; }
.dcx-card-status { font-size: 11px; font-weight: 800; display: inline-flex; align-items: center; gap: 6px; color: #8a92b5; }
.dcx-card-status.ready { color: #6ee7a0; }
.dcx-card-status.failed { color: #f28b8b; }
.dcx-card-status.generating { color: #ffd591; }
.dcx-dot-pulse { width: 7px; height: 7px; border-radius: 50%; background: #ffd591; animation: pulse 1.2s ease-in-out infinite; }
@keyframes pulse { 0%, 100% { opacity: .35; transform: scale(.8); } 50% { opacity: 1; transform: scale(1.15); } }
.dcx-card-title { font-size: 14px; font-weight: 800; line-height: 1.5; overflow: hidden; text-overflow: ellipsis; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; min-height: 42px; }
.dcx-card-sub { font-size: 11px; color: var(--ink3); }
.dcx-card-foot { display: flex; align-items: center; justify-content: space-between; font-size: 11px; color: #8a92b5; }
.dcx-card-actions { display: flex; gap: 6px; }
.dcx-mini {
  border: 1px solid rgba(255, 255, 255, .14); background: rgba(255, 255, 255, .05); color: #374151; border-radius: 7px;
  font-size: 11px; padding: 3px 8px; cursor: pointer;
}
.dcx-mini:hover { border-color: #6366f1; color: var(--brand-deep); }
.dcx-mini.danger:hover { border-color: #f28b8b; color: #ffb4b4; }

/* 进度条 */
.dcx-bar { height: 7px; background: rgba(99, 102, 241, .18); border-radius: 999px; overflow: hidden; }
.dcx-bar.slim { height: 5px; flex: 1; }
.dcx-bar.grow { flex: 1; }
.dcx-fill { height: 100%; background: linear-gradient(90deg, #4f46e5, #818cf8); border-radius: 999px; transition: width .4s ease; }

/* ==================== 生成等待页（OpenMAIC：居中紧凑卡贯穿全程） ==================== */
.dcx-creating { flex: 1; overflow-y: auto; position: relative; display: grid; place-items: center; padding: 30px 16px; }
.dcx-create-inner { position: relative; z-index: 1; width: min(620px, 100%); display: flex; flex-direction: column; align-items: center; gap: 18px; }

.dcx-gen-card {
  width: 100%; background: rgba(22, 27, 54, .72); border: 1px solid rgba(255, 255, 255, .1); border-radius: 22px;
  padding: 26px 30px 26px; backdrop-filter: blur(18px); box-shadow: 0 24px 60px rgba(0, 0, 0, .4);
  display: flex; flex-direction: column; align-items: center; gap: 10px; text-align: center;
}
.dcx-gen-card.err { border-color: rgba(242, 139, 139, .5); }
.dcx-gen-errtitle { font-size: 19px; font-weight: 900; }
.dcx-gen-errico {
  width: 74px; height: 74px; border-radius: 18px; display: grid; place-items: center; font-size: 34px;
  background: rgba(242, 139, 139, .12); border: 1px solid rgba(242, 139, 139, .4);
}
.dcx-gen-dots { display: flex; align-items: center; gap: 7px; margin-bottom: 4px; }
.dcx-gen-dots i { width: 16px; height: 4px; border-radius: 999px; background: rgba(255, 255, 255, .14); transition: all .3s ease; }
.dcx-gen-dots i.on { width: 26px; background: #6366f1; }
.dcx-gen-dots i.done { background: #34d399; }
.dcx-gen-card h2 { margin: 4px 0 0; font-size: 21px; font-weight: 900; }
.dcx-gen-sub { margin: 0; font-size: 12.5px; color: #8a92b5; }
.dcx-gen-topic { margin: 0; font-size: 12px; color: var(--ink3); max-width: 460px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

.dcx-gen-visual { perspective: 600px; margin-top: 6px; }
.dcx-doc-mock {
  width: 240px; height: 168px; margin: 0 auto; border-radius: 14px; border: 1px solid rgba(99, 102, 241, .5);
  background: linear-gradient(160deg, rgba(99, 102, 241, .14), rgba(59, 82, 214, .08));
  padding: 22px 20px; display: flex; flex-direction: column; gap: 12px; position: relative;
  animation: docfloat 3.2s ease-in-out infinite; box-shadow: 0 14px 34px rgba(79, 70, 229, .3);
}
.dcx-doc-mock .ln { height: 7px; border-radius: 4px; background: rgba(199, 178, 255, .5); }
.dcx-doc-mock .ln.w60 { width: 60%; } .dcx-doc-mock .ln.w70 { width: 70%; }
.dcx-doc-mock .ln.w80 { width: 80%; } .dcx-doc-mock .ln.w50 { width: 50%; }
.dcx-doc-pen { position: absolute; right: 14px; bottom: 10px; font-size: 15px; color: rgba(199, 178, 255, .85); animation: penwrite 1.6s ease-in-out infinite; }
@keyframes penwrite { 0%, 100% { transform: translate(0, 0) rotate(0deg); } 30% { transform: translate(-4px, -3px) rotate(-8deg); } 60% { transform: translate(3px, -1px) rotate(6deg); } }
@keyframes docfloat { 0%, 100% { transform: translateY(0) rotateX(4deg); } 50% { transform: translateY(-9px) rotateX(0deg); } }

/* 逐页备课态：大纲在文档内逐条流出 */
.dcx-doc-mock.live { width: 320px; padding: 0; overflow: hidden; gap: 0; }
.dcx-doc-headbar { height: 34px; flex-shrink: 0; border-bottom: 1px solid rgba(99, 102, 241, .3); background: rgba(99, 102, 241, .12); position: relative; }
.dcx-doc-headbar::after {
  content: ''; position: absolute; left: 14px; top: 13px; width: 42%; height: 8px; border-radius: 4px;
  background: rgba(199, 178, 255, .55);
}
.dcx-doc-rows { flex: 1; overflow-y: auto; padding: 9px 12px; display: flex; flex-direction: column; gap: 6px; }
.dcx-doc-row {
  display: flex; align-items: center; gap: 8px; padding: 6px 9px; border-radius: 8px;
  background: rgba(255, 255, 255, .05); border: 1px solid rgba(255, 255, 255, .07);
  font-size: 11.5px; animation: linein .4s ease both;
}
.dcx-doc-row i { font-style: normal; font-weight: 800; font-size: 10px; color: #8f7bd8; font-variant-numeric: tabular-nums; }
.dcx-doc-row span { flex: 1; text-align: left; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--ink2); }
.dcx-doc-row em { font-style: normal; color: #34d399; font-weight: 900; }
.dcx-doc-row.doing { border-color: rgba(99, 102, 241, .6); background: rgba(99, 102, 241, .13); }
.dcx-doc-row.doing span { color: #efe9ff; font-weight: 700; }

.dcx-gen-progress { display: flex; align-items: center; gap: 10px; width: min(320px, 100%); margin-top: 2px; }
.dcx-gen-progress .dcx-bar { flex: 1; }
.dcx-gen-progress span { font-size: 11.5px; font-weight: 800; color: #374151; font-variant-numeric: tabular-nums; }
.dcx-gen-actions { display: flex; align-items: center; justify-content: center; gap: 10px; margin-top: 6px; min-height: 38px; }
.dcx-gen-foot { margin: 0; font-size: 12px; color: #6b7280; }
.dcx-gen-inline { display: flex; align-items: center; gap: 8px; font-size: 11.5px; color: #ffd591; margin-top: 9px; }
.dcx-gen-more { width: 100%; }
.dcx-gen-more summary {
  text-align: center; font-size: 12px; font-weight: 700; color: var(--ink3); cursor: pointer; user-select: none;
  padding: 7px 0; border-radius: 10px; border: 1px dashed rgba(255, 255, 255, .12); list-style: none;
}
.dcx-gen-more summary::-webkit-details-marker { display: none; }
.dcx-gen-more summary:hover { color: #374151; border-color: rgba(99, 102, 241, .5); }
.dcx-gen-more[open] summary { margin-bottom: 10px; }
.dcx-outline-live { display: flex; flex-direction: column; gap: 7px; max-height: 340px; overflow-y: auto; padding: 2px; }
.dcx-oline {
  display: flex; align-items: center; gap: 12px; padding: 11px 14px; border-radius: 12px;
  border: 1px solid var(--line); background: rgba(22, 27, 54, .6);
  animation: linein .4s ease both;
}
@keyframes linein { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: translateY(0); } }
.dcx-oline.done { border-color: rgba(110, 231, 160, .35); background: rgba(110, 231, 160, .06); }
.dcx-oline.doing { border-color: rgba(99, 102, 241, .55); background: rgba(99, 102, 241, .1); }
.dcx-ono {
  flex-shrink: 0; width: 26px; height: 26px; border-radius: 8px; display: grid; place-items: center;
  font-size: 11px; font-weight: 800; color: #374151; background: rgba(99, 102, 241, .18);
}
.dcx-ot { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
.dcx-ot b { font-size: 13.5px; }
.dcx-ot small { font-size: 11px; color: var(--ink3); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dcx-ost { flex-shrink: 0; font-size: 11.5px; font-weight: 700; display: inline-flex; align-items: center; gap: 6px; }
.dcx-ost .ok { color: #6ee7a0; }
.dcx-ost .doing { color: #374151; display: inline-flex; align-items: center; gap: 6px; }
.dcx-ost .wait { color: #6b7280; }
.dcx-oskeleton { width: 42px; height: 9px; border-radius: 5px; background: linear-gradient(90deg, rgba(255,255,255,.06), rgba(255,255,255,.16), rgba(255,255,255,.06)); background-size: 200% 100%; animation: shimmer 1.6s linear infinite; }
.dcx-o-skel-lines { display: flex; flex-direction: column; gap: 4px; padding: 0 46px; margin-top: -2px; }
.dcx-o-skel-lines i { height: 6px; border-radius: 4px; background: linear-gradient(90deg, rgba(255,255,255,.05), rgba(255,255,255,.12), rgba(255,255,255,.05)); background-size: 200% 100%; animation: shimmer 1.6s linear infinite; }
.dcx-o-skel-lines i:first-child { width: 70%; }
.dcx-o-skel-lines i:last-child { width: 45%; }
@keyframes shimmer { to { background-position: -200% 0; } }
.dcx-spinner {
  width: 14px; height: 14px; border-radius: 50%; flex-shrink: 0; display: inline-block;
  border: 2px solid rgba(99, 102, 241, .3); border-top-color: #818cf8; animation: spin .8s linear infinite;
}
.dcx-spinner.lg { width: 30px; height: 30px; border-width: 3px; }
@keyframes spin { to { transform: rotate(360deg); } }

/* ==================== 课堂页（三栏） ==================== */
.dcx-body {
  flex: 1; min-height: 0; display: grid; gap: 13px; padding: 13px 15px;
  grid-template-columns: 236px minmax(0, 1fr) 330px; position: relative; z-index: 1;
  overflow-y: auto;              /* S11 v3：页面级滚动——课件整页完整可见，不再被内滚裁切 */
  align-items: start;
}
.dcx-left, .dcx-right {
  display: flex; flex-direction: column; gap: 11px; padding-bottom: 4px;
  position: sticky; top: 71px; max-height: calc(100vh - 71px - 16px); overflow-y: auto;
}
.dcx-center { min-width: 0; padding: 4px 4px 12px; position: relative; display: flex; flex-direction: column; gap: 12px; }
.dcx-card {
  background: var(--card); border: 1px solid rgba(99, 102, 241, .18); border-radius: 15px; padding: 14px;
  backdrop-filter: blur(10px);
}
.dcx-card-title { display: flex; align-items: center; gap: 7px; font-size: 13.5px; font-weight: 800; margin-bottom: 10px; }
.dcx-card-title .dcx-ghost { margin-left: auto; }

/* 左栏大纲 */
.dcx-outline { display: flex; flex-direction: column; gap: 2px; }
.dcx-oitem {
  display: flex; align-items: center; gap: 9px; padding: 7px 8px; border-radius: 9px; cursor: pointer;
  font-size: 12.5px; color: #374151; transition: background .12s ease;
}
.dcx-oitem:hover { background: rgba(255, 255, 255, .06); }
.dcx-oitem.active { background: linear-gradient(120deg, #4f46e5, #6366f1); color: #fff; font-weight: 800; box-shadow: 0 3px 12px rgba(79, 70, 229, .45); }
.dcx-oitem.done { color: var(--ink3); }
.dcx-ono {
  width: 20px; height: 20px; border-radius: 50%; display: grid; place-items: center; flex-shrink: 0;
  font-size: 10.5px; font-weight: 800; background: var(--line); color: #4b5563;
}
.dcx-oitem.active .dcx-ono { background: rgba(255, 255, 255, .22); color: #fff; }
.dcx-ot { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dcx-ock { color: #6ee7a0; font-weight: 900; }
.dcx-osmini { display: inline-flex; }
.dcx-links { display: flex; flex-direction: column; gap: 4px; padding: 8px; }
.dcx-side-link {
  border: none; background: transparent; text-align: left; font-size: 12.5px; font-weight: 700; color: #374151;
  padding: 8px 10px; border-radius: 9px; cursor: pointer; width: 100%;
}
.dcx-side-link:hover { background: rgba(99, 102, 241, .16); color: var(--brand-deep); }
.dcx-side-link.big { font-size: 13px; padding: 10px 12px; }
.dcx-learned { font-size: 12px; font-weight: 700; color: #374151; margin-bottom: 8px; }
.dcx-goal { display: flex; align-items: flex-start; gap: 7px; font-size: 12px; color: #374151; line-height: 1.6; padding: 3px 0; }
.dcx-goal i { color: #6ee7a0; font-style: normal; font-weight: 900; }

/* ===== 幻灯片舞台 ===== */
.dcx-stage-wrap { position: relative; }
.dcx-slide {
  background: var(--bg-white); color: #111827; border-radius: 16px; padding: 26px 28px 20px; position: relative; overflow: hidden;
  box-shadow: 0 20px 50px rgba(0, 0, 0, .45); flex: 1 0 auto; display: flex; flex-direction: column; min-height: 200px;
}
.dcx-slide-no {
  position: absolute; right: 18px; top: 6px; font-size: 52px; font-weight: 900; color: rgba(79, 70, 229, .08);
  font-variant-numeric: tabular-nums; pointer-events: none;
}
.dcx-ltitle { margin: 0 0 16px; font-size: 22px; font-weight: 900; color: #111827; display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; transition: opacity .3s ease; }
.dcx-ltitle small { font-size: 12.5px; color: #94a3b8; font-weight: 600; }

/* 聚光灯（OpenMAIC spotlight）：讲解中未聚焦块压暗 */
.dcx-slide.spotlight [data-focus].dim { opacity: .16; filter: saturate(.4); }
.dcx-slide [data-focus] { transition: opacity .35s ease, filter .35s ease; }

.dcx-failedbar {
  display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 14px;
  background: #fef2f2; border: 1px solid #fecaca; color: #b91c1c; border-radius: 10px;
  padding: 9px 13px; font-size: 12.5px; font-weight: 700;
}
.dcx-lgrid { flex: 1 0 auto; display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 46%); gap: 20px; align-items: start; }
.dcx-lgrid.no-fig { grid-template-columns: minmax(0, 1fr); }
.dcx-lfig { display: flex; flex-direction: column; gap: 12px; min-width: 0; }
.dcx-figcap { font-size: 12.5px; font-weight: 700; color: #475569; }

.dcx-theorem {
  border: 1px solid #cfe0ff; background: linear-gradient(160deg, #f4f8ff, #fff); border-radius: 12px;
  padding: 13px 16px; margin-bottom: 12px;
}
.dcx-theorem-head { display: flex; align-items: center; gap: 9px; margin-bottom: 7px; flex-wrap: wrap; }
.dcx-theorem-head b { font-size: 14.5px; color: #1e3a8a; }
.dcx-theorem-body { font-size: 13.5px; line-height: 1.9; color: #1f2937; }
.dcx-badge { flex-shrink: 0; font-size: 11.5px; font-weight: 800; color: #fff; border-radius: 7px; padding: 3px 10px; }
.dcx-badge.blue { background: #4f46e5; }
.dcx-badge.green { background: #16a34a; }
.dcx-para { font-size: 13.5px; line-height: 1.9; color: #1f2937; margin: 0 0 10px; }

.dcx-example { border: 1px solid #bbe7c9; background: linear-gradient(160deg, #f2fbf5, #fff); border-radius: 12px; padding: 13px 16px; margin-bottom: 12px; }
.dcx-exq { font-size: 13.5px; font-weight: 700; color: #14532d; }
.dcx-sol { display: flex; gap: 10px; margin-top: 10px; }
.dcx-solmark {
  flex-shrink: 0; width: 24px; height: 24px; border-radius: 7px; background: #16a34a; color: #fff;
  font-size: 12.5px; font-weight: 900; display: grid; place-items: center; margin-top: 1px;
}
.dcx-solsteps { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 6px; }
.dcx-step { font-size: 13px; line-height: 1.85; color: #1f2937; padding-left: 2px; }
.dcx-answer { display: flex; align-items: baseline; gap: 8px; margin-top: 9px; padding: 8px 12px; background: #ecfdf3; border-radius: 9px; font-size: 13.5px; color: #14532d; }
.dcx-answer b { color: #16a34a; flex-shrink: 0; }

.dcx-table { border-collapse: collapse; margin: 4px 0 14px; width: 100%; font-size: 12.5px; }
.dcx-table caption { font-size: 12px; font-weight: 700; color: #64748b; margin-bottom: 5px; }
.dcx-table th, .dcx-table td { border: 1px solid #d7dfeb; padding: 7px 10px; text-align: center; }
.dcx-table th { background: #f4f7fc; font-weight: 800; color: #334155; }

.dcx-conclusion {
  display: flex; gap: 8px; align-items: flex-start; background: #fffbeb; border: 1px solid #fde68a;
  border-radius: 10px; padding: 9px 13px; font-size: 13px; color: #78350f; line-height: 1.7; margin-bottom: 8px;
}
.dcx-textbook { font-size: 12px; color: #1e40af; background: #f0f6ff; border-radius: 9px; padding: 8px 12px; line-height: 1.8; }
.dcx-tcite { display: inline-block; background: #e3eeff; border-radius: 999px; padding: 1px 9px; margin: 2px 4px 0 0; }

/* ===== 讲稿（逐句） ===== */
.dcx-teach {
  display: flex; align-items: flex-start; gap: 13px; padding: 13px 15px;
  background: var(--card); border: 1px solid rgba(99, 102, 241, .18); border-radius: 15px; backdrop-filter: blur(10px);
}
.dcx-tavatar {
  width: 46px; height: 46px; border-radius: 50%; overflow: hidden; flex-shrink: 0;
  background: linear-gradient(135deg, #3b2d63, #553c8f); border: 2px solid rgba(255, 255, 255, .2); box-shadow: 0 3px 10px rgba(0, 0, 0, .4);
}
.dcx-tavatar img { width: 100%; height: 100%; object-fit: cover; display: block; }
.dcx-tavatar.lead { width: 52px; height: 52px; }
.dcx-tbubble { flex: 1; min-width: 0; }
.dcx-tbhead { display: flex; align-items: center; gap: 10px; margin-bottom: 6px; }
.dcx-tbname { font-size: 12.5px; font-weight: 800; color: #374151; }
.dcx-tbstate { display: flex; align-items: center; gap: 7px; font-size: 11px; font-weight: 700; color: #6ee7a0; }
.dcx-tbstate.muted { color: #6b7280; }
.dcx-wave { display: inline-flex; align-items: flex-end; gap: 2px; height: 12px; }
.dcx-wave b { width: 2.5px; border-radius: 2px; background: #818cf8; animation: dcxw 1s ease-in-out infinite; }
.dcx-wave b:nth-child(odd) { height: 6px; animation-delay: .12s; }
.dcx-wave b:nth-child(3n) { height: 11px; animation-delay: .25s; }
.dcx-wave b:nth-child(4n) { height: 8px; animation-delay: .4s; }
@keyframes dcxw { 0%, 100% { transform: scaleY(.5); } 50% { transform: scaleY(1.25); } }
.dcx-narr-lines { display: flex; flex-direction: column; gap: 3px; max-height: 128px; overflow-y: auto; }
.dcx-narr-lines p {
  margin: 0; font-size: 13px; line-height: 1.8; color: var(--ink3); padding: 3px 10px; border-radius: 8px;
  cursor: pointer; transition: all .18s ease;
}
.dcx-narr-lines p:hover { background: rgba(255, 255, 255, .05); color: #374151; }
.dcx-narr-lines p.played { color: #566087; }
.dcx-narr-lines p.on { background: rgba(99, 102, 241, .2); color: #f0ebff; font-weight: 700; box-shadow: inset 2.5px 0 0 #818cf8; }
.dcx-narr-empty { margin: 0; font-size: 13px; line-height: 1.85; color: #374151; }
.dcx-tutor-mini {
  display: flex; flex-direction: column; align-items: center; gap: 3px; flex-shrink: 0; width: 100px;
  background: rgba(255, 255, 255, .04); border: 1px solid rgba(255, 255, 255, .1); border-radius: 12px; padding: 10px 8px;
}
.dcx-tutor-mini b { font-size: 12px; }
.dcx-tutor-mini em { font-style: normal; font-size: 10.5px; color: #6ee7a0; font-weight: 700; }
.dcx-mini-ask {
  margin-top: 4px; border: 1px solid rgba(99, 102, 241, .5); background: rgba(99, 102, 241, .15); color: var(--brand-deep); font-size: 11.5px;
  font-weight: 800; border-radius: 999px; padding: 4px 14px; cursor: pointer;
}
.dcx-mini-ask:hover { background: #4f46e5; color: #fff; }

/* ===== 播控条 ===== */
.dcx-controls {
  display: flex; align-items: center; gap: 8px; padding: 10px 14px;
  background: rgba(22, 27, 54, .72); border: 1px solid rgba(255, 255, 255, .1); border-radius: 14px; backdrop-filter: blur(10px);
  flex-wrap: wrap;
}
.dcx-ctl {
  width: 34px; height: 34px; border-radius: 9px; border: 1px solid rgba(255, 255, 255, .13); background: rgba(255, 255, 255, .04);
  color: #4b5563; font-size: 13px; cursor: pointer; display: grid; place-items: center; transition: all .14s ease;
}
.dcx-ctl:hover:not(:disabled) { border-color: #6366f1; color: var(--brand-deep); }
.dcx-ctl:disabled { opacity: .35; cursor: not-allowed; }
.dcx-ctl.play { width: 40px; height: 40px; border-radius: 50%; background: linear-gradient(135deg, #4f46e5, #6366f1); border: none; color: #fff; font-size: 15px; box-shadow: 0 4px 14px rgba(79, 70, 229, .5); }
.dcx-ctl.play:hover:not(:disabled) { filter: brightness(1.12); }
.dcx-ctl-sep { width: 1px; height: 20px; background: rgba(255, 255, 255, .12); }
.dcx-ctl-page { font-size: 12px; font-weight: 800; color: #4b5563; white-space: nowrap; font-variant-numeric: tabular-nums; }
.dcx-foot-l { display: flex; align-items: center; gap: 7px; flex-wrap: wrap; }
.dcx-foot-q { font-size: 11.5px; font-weight: 700; color: var(--ink3); }
.dcx-chipbtn {
  border: 1px solid rgba(255, 255, 255, .13); background: rgba(255, 255, 255, .04); border-radius: 999px; padding: 5px 12px; cursor: pointer;
  font-size: 11.5px; font-weight: 700; color: #374151;
}
.dcx-chipbtn.ok { background: rgba(110, 231, 160, .15); border-color: rgba(110, 231, 160, .5); color: #6ee7a0; }
.dcx-chipbtn.again { background: rgba(250, 173, 20, .12); border-color: rgba(250, 173, 20, .45); color: #ffd591; }

/* ===== 白板页签 ===== */
.dcx-board {
  background: var(--card); border: 1px solid rgba(99, 102, 241, .18); border-radius: 15px; overflow: hidden;
  backdrop-filter: blur(10px); flex: 1 1 auto; display: flex; flex-direction: column; min-height: 280px;
}
.dcx-btabs { display: flex; align-items: center; gap: 4px; padding: 8px 12px 0; border-bottom: 1px solid var(--line); }
.dcx-btabs button {
  border: none; background: transparent; font-size: 13px; font-weight: 700; color: var(--ink3);
  padding: 9px 16px; cursor: pointer; border-radius: 9px 9px 0 0; border-bottom: 2.5px solid transparent;
}
.dcx-btabs button.on { color: #374151; border-bottom-color: #818cf8; background: rgba(99, 102, 241, .12); }
.dcx-bthint { margin-left: auto; font-size: 11px; color: #6b7280; padding-bottom: 8px; }
.dcx-wb { padding: 14px 16px 16px; display: flex; flex-direction: column; gap: 12px; flex: 1; }
.dcx-wbsteps { display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 8px; }
.dcx-wbstep {
  display: flex; align-items: baseline; gap: 9px; background: rgba(255, 255, 255, .04); border: 1px solid rgba(99, 102, 241, .18);
  border-radius: 9px; padding: 9px 12px; font-size: 13.5px; overflow-x: auto; transition: all .3s ease; color: var(--ink2);
}
.dcx-wbstep.now { border-color: #6366f1; background: rgba(99, 102, 241, .14); box-shadow: 0 2px 10px rgba(99, 102, 241, .25); }
.dcx-wbno {
  flex-shrink: 0; width: 19px; height: 19px; border-radius: 50%; background: #4f46e5; color: #fff;
  font-size: 10.5px; font-weight: 800; display: grid; place-items: center; transform: translateY(3px);
}
.dcx-wbempty { margin: 0; font-size: 12.5px; color: var(--ink3); }
.dcx-tools { padding: 14px 16px 16px; display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.dcx-toolstip { width: 100%; margin: 0 0 2px; font-size: 12px; color: var(--ink3); }
.dcx-fchip { border: 1px solid var(--line); background: rgba(255, 255, 255, .04); border-radius: 9px; padding: 9px 13px; cursor: pointer; font-size: 13.5px; color: var(--ink2); }
.dcx-fchip:hover { border-color: #6366f1; }
.dcx-notes { padding: 14px 16px 16px; }
.dcx-notes-area { width: 100%; min-height: 150px; resize: vertical; line-height: 1.8; }

/* ===== 右栏 ===== */
.dcx-tutor { display: flex; flex-direction: column; gap: 10px; flex: 1 0 auto; }
.dcx-upload {
  display: flex; align-items: center; gap: 11px; border: 1.5px dashed rgba(99, 102, 241, .5); background: rgba(99, 102, 241, .08);
  border-radius: 12px; padding: 11px 13px; cursor: pointer; transition: all .15s ease;
}
.dcx-upload:hover { border-color: #6366f1; background: rgba(99, 102, 241, .14); }
.dcx-upico { font-size: 20px; }
.dcx-uptxt { display: flex; flex-direction: column; gap: 1px; }
.dcx-uptxt b { font-size: 13px; color: #374151; }
.dcx-uptxt small { font-size: 11px; color: var(--ink3); }
.dcx-upload-confirm { display: flex; flex-direction: column; gap: 8px; }
.dcx-upload-actions { display: flex; gap: 8px; justify-content: flex-end; }
.dcx-msgs {
  flex: 1 1 auto; min-height: 140px; max-height: 44vh; overflow-y: auto; display: flex; flex-direction: column; gap: 8px;
  border-top: 1px dashed var(--line); border-bottom: 1px dashed var(--line); padding: 10px 2px;
}
.dcx-msg-tip { font-size: 12px; color: var(--ink3); line-height: 1.7; }
.dcx-thinking { display: flex; align-items: center; gap: 4px; font-size: 12px; color: var(--ink3); padding: 4px 2px; }
.dcx-thinking span { width: 6px; height: 6px; border-radius: 50%; background: #818cf8; animation: blink 1.2s infinite; }
.dcx-thinking span:nth-child(2) { animation-delay: .2s; }
.dcx-thinking span:nth-child(3) { animation-delay: .4s; }
@keyframes blink { 0%, 80%, 100% { opacity: .25; transform: scale(.8); } 40% { opacity: 1; transform: scale(1); } }
.dcx-quick { display: flex; flex-wrap: wrap; gap: 6px; }
.dcx-qchip {
  border: 1px solid var(--line); background: rgba(255, 255, 255, .04); border-radius: 999px; font-size: 12px; padding: 6px 12px;
  cursor: pointer; color: #374151; font-weight: 600;
}
.dcx-qchip:hover { border-color: #6366f1; color: var(--brand-deep); }
.dcx-askrow { display: flex; gap: 8px; }
.dcx-askrow .dcx-input { flex: 1; }
.dcx-send {
  width: 38px; height: 38px; border-radius: 10px; border: none; background: linear-gradient(135deg, #4f46e5, #6366f1); color: #fff;
  font-size: 15px; cursor: pointer; box-shadow: 0 3px 12px rgba(79, 70, 229, .45);
}
.dcx-send:disabled { opacity: .5; cursor: not-allowed; }

/* 分层练习 */
.dcx-practice { display: flex; flex-direction: column; gap: 10px; }
.dcx-practice.flash { box-shadow: 0 0 0 3px rgba(79, 70, 229, .35); transition: box-shadow .2s ease; }
.dcx-practice.pending { flex-direction: row; align-items: center; font-size: 12px; color: #ffd591; }
.dcx-tiers { display: flex; gap: 6px; }
.dcx-tier {
  flex: 1; border: 1px solid var(--line); background: rgba(255, 255, 255, .04); border-radius: 8px; padding: 7px 0;
  font-size: 12.5px; font-weight: 800; color: #4b5563; cursor: pointer; transition: all .15s ease;
}
.dcx-tier.on { background: linear-gradient(120deg, #4f46e5, #6366f1); border-color: transparent; color: #fff; box-shadow: 0 3px 12px rgba(79, 70, 229, .4); }
.dcx-tier.empty { opacity: .45; }
.dcx-pq { font-size: 13.5px; font-weight: 700; color: var(--ink2); line-height: 1.8; }
.dcx-popts { display: flex; flex-direction: column; gap: 7px; }
.dcx-popt {
  display: flex; align-items: center; gap: 9px; text-align: left; border: 1px solid rgba(255, 255, 255, .11); background: rgba(255, 255, 255, .04);
  border-radius: 10px; padding: 9px 12px; cursor: pointer; font-size: 13px; color: #4b5563; transition: all .13s ease;
}
.dcx-popt b {
  flex-shrink: 0; width: 21px; height: 21px; border-radius: 50%; background: rgba(99, 102, 241, .18); color: #4b5563;
  font-size: 11px; font-weight: 800; display: grid; place-items: center;
}
.dcx-popt span { flex: 1; min-width: 0; }
.dcx-popt i { font-style: normal; font-weight: 900; color: #6ee7a0; }
.dcx-popt i.bad { color: #f28b8b; }
.dcx-popt:hover:not(:disabled) { border-color: #6366f1; }
.dcx-popt.right { border-color: rgba(110, 231, 160, .6); background: rgba(110, 231, 160, .1); }
.dcx-popt.right b { background: #16a34a; color: #fff; }
.dcx-popt.wrong { border-color: rgba(242, 139, 139, .6); background: rgba(242, 139, 139, .1); }
.dcx-popt.wrong b { background: #dc2626; color: #fff; }
.dcx-popt.dim { opacity: .55; }
.dcx-pa { background: rgba(99, 102, 241, .1); border: 1px solid rgba(99, 102, 241, .35); border-radius: 10px; padding: 9px 12px; font-size: 12.5px; color: #4b5563; line-height: 1.75; }
.dcx-pa b { color: #374151; margin-right: 6px; }
.dcx-pempty { font-size: 12.5px; color: var(--ink3); text-align: center; padding: 12px 0; }
.dcx-pfoot { display: flex; align-items: center; gap: 10px; }
.dcx-ring { width: 46px; height: 46px; border-radius: 50%; display: grid; place-items: center; flex-shrink: 0; }
.dcx-ring span {
  width: 36px; height: 36px; border-radius: 50%; background: var(--card); display: grid; place-items: center;
  font-size: 10.5px; font-weight: 900; color: #6ee7a0;
}
.dcx-pcount { flex: 1; font-size: 12px; font-weight: 700; color: #4b5563; }

/* ===== 状态卡（失败等） ===== */
.dcx-state {
  background: rgba(22, 27, 54, .6); border: 1px dashed rgba(255, 255, 255, .18); border-radius: 16px; padding: 22px 20px;
  display: flex; align-items: center; justify-content: center; gap: 12px; font-size: 13.5px; color: #374151;
}
.dcx-state.err { flex-direction: column; text-align: center; border-color: rgba(242, 139, 139, .5); }

/* ===== 弹窗 ===== */
.dcx-modal-mask {
  position: fixed; inset: 0; z-index: 80; background: rgba(5, 8, 20, .62);
  display: grid; place-items: center; padding: 20px; backdrop-filter: blur(3px);
}
.dcx-modal {
  width: min(560px, 100%); max-height: 82vh; overflow-y: auto; background: var(--card); border: 1px solid var(--line); border-radius: 18px;
  box-shadow: 0 24px 64px rgba(0, 0, 0, .6); padding: 20px 22px; display: flex; flex-direction: column; gap: 14px;
}
.dcx-modal-head { display: flex; align-items: center; justify-content: space-between; font-size: 16px; }
.dcx-report-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; }
.dcx-rstat { background: rgba(255, 255, 255, .04); border: 1px solid rgba(99, 102, 241, .18); border-radius: 12px; padding: 12px; display: flex; flex-direction: column; gap: 3px; }
.dcx-rstat b { font-size: 17px; color: #374151; }
.dcx-rstat span { font-size: 11px; color: var(--ink3); }
.dcx-report-tier { display: flex; flex-direction: column; gap: 8px; }
.dcx-rtrow { display: flex; align-items: center; gap: 12px; font-size: 12.5px; color: #374151; }
.dcx-rtrow span { width: 40px; flex-shrink: 0; }
.dcx-rtrow b { width: 56px; text-align: right; flex-shrink: 0; font-variant-numeric: tabular-nums; }
.dcx-rtrow .dcx-bar { flex: 1; }
.dcx-report-notes { background: rgba(255, 255, 255, .04); border-radius: 12px; padding: 12px 14px; font-size: 12.5px; color: #374151; }
.dcx-report-notes p { margin: 6px 0 0; white-space: pre-wrap; line-height: 1.75; }
.dcx-modal-foot { display: flex; justify-content: flex-end; gap: 10px; }
.dcx-review-list { display: flex; flex-direction: column; gap: 4px; max-height: 56vh; overflow-y: auto; }
.dcx-review-item { display: flex; align-items: center; gap: 12px; padding: 10px 12px; border-radius: 12px; cursor: pointer; border: 1px solid transparent; }
.dcx-review-item:hover { background: rgba(255, 255, 255, .05); border-color: rgba(99, 102, 241, .4); }
.dcx-review-txt { flex: 1; min-width: 0; }
.dcx-review-name { font-size: 13px; font-weight: 700; }
.dcx-review-sub { font-size: 11px; color: var(--ink3); }
.dcx-review-go { font-size: 12px; font-weight: 800; color: #374151; }

/* ===== 通用控件 ===== */
.dcx-btn {
  border: 1px solid rgba(255, 255, 255, .14); background: rgba(255, 255, 255, .05); color: #4b5563; border-radius: 9px; padding: 8px 15px;
  font-size: 12.5px; font-weight: 700; cursor: pointer; transition: all .15s ease; white-space: nowrap;
}
.dcx-btn:hover:not(:disabled) { border-color: #6366f1; color: var(--brand-deep); }
.dcx-btn:disabled { opacity: .45; cursor: not-allowed; }
.dcx-btn.primary {
  background: linear-gradient(135deg, #4f46e5, #6366f1); border-color: transparent; color: #fff; box-shadow: 0 4px 14px rgba(79, 70, 229, .45);
}
.dcx-btn.primary:hover:not(:disabled) { filter: brightness(1.1); color: #fff; }
.dcx-btn.lg { padding: 11px 20px; font-size: 13.5px; }
.dcx-btn.ghost-dark { background: transparent; }
.dcx-ghost {
  border: none; background: var(--line); color: #374151; border-radius: 8px; padding: 5px 10px;
  font-size: 12px; cursor: pointer; font-weight: 700;
}
.dcx-ghost:hover { background: rgba(255, 255, 255, .14); }
.dcx-input {
  border: 1px solid rgba(255, 255, 255, .16); border-radius: 9px; padding: 8px 11px; font: inherit; font-size: 13px;
  background: rgba(10, 14, 33, .7); color: var(--ink);
}
.dcx-input:focus { outline: 2px solid rgba(99, 102, 241, .4); border-color: #6366f1; }
.dcx-confirm { border-top: 1px dashed rgba(255, 255, 255, .1); padding-top: 12px; display: flex; flex-direction: column; gap: 10px; }
.dcx-confirm-img { max-height: 180px; max-width: 100%; object-fit: contain; border-radius: 9px; border: 1px solid rgba(255, 255, 255, .1); }
.dcx-confirm-preview {
  border: 1px solid rgba(255, 255, 255, .1); background: rgba(10, 14, 33, .55); border-radius: 9px; padding: 10px 12px;
  max-height: 220px; overflow-y: auto; font-size: 13px; line-height: 1.8;
}
.dcx-edit-src summary { font-size: 11.5px; color: var(--ink3); cursor: pointer; user-select: none; }
.dcx-edit-src textarea { margin-top: 8px; font-size: 12px; }
.dcx-confirm-title { font-size: 12px; font-weight: 800; color: #374151; }
.dcx-confirm-foot { display: flex; align-items: center; justify-content: space-between; gap: 10px; flex-wrap: wrap; }
.dcx-confirm-foot .dcx-btn { flex: 1 1 auto; min-width: 150px; }
.dcx-file-name { font-size: 11px; color: var(--ink3); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

/* ===== 响应式 ===== */
@media (max-width: 1280px) {
  .dcx-body { grid-template-columns: 210px minmax(0, 1fr); }
  .dcx-right { grid-column: 1 / -1; display: grid; grid-template-columns: 1fr 1fr; align-items: start; }
  .dcx-alt-entries { grid-template-columns: 1fr; }
}
.dcx-o-strip { display: none; gap: 6px; overflow-x: auto; padding: 2px 2px 8px; }
.dcx-o-chip {
  min-width: 34px; height: 34px; border-radius: 10px; border: 1px solid rgba(255, 255, 255, .16);
  background: rgba(255, 255, 255, .05); color: #4b5563; font: inherit; font-size: 13px; font-weight: 800;
  cursor: pointer; flex: 0 0 auto;
}
.dcx-o-chip.active { background: linear-gradient(135deg, #4f46e5, #818cf8); border-color: transparent; color: #fff; }
.dcx-o-chip.done { border-color: rgba(82, 196, 26, .5); color: #b7f4cf; }
/* 窄屏页码条内的「随堂练习」入口（宽度自适应，不复制练习逻辑，仅滚动到右栏练习卡） */
.dcx-o-practice { width: auto; min-width: 0; margin-left: auto; padding: 0 12px; font-size: 12px; }

@media (max-width: 1100px) {
  /* S11：≤1100 收侧栏大纲为横向页码条（课件立即全宽，不再被三栏挤丢） */
  .dcx-body { grid-template-columns: minmax(0, 1fr); }
  .dcx-left { display: none; }
  .dcx-o-strip { display: flex; }
  .dcx-right { grid-column: 1 / -1; }
}

@media (max-width: 900px) {
  .dcx-body { grid-template-columns: 1fr; overflow-y: auto; }
  .dcx-right { grid-template-columns: 1fr; }
  .dcx-lgrid { grid-template-columns: 1fr; }
  .dcx-course-name { max-width: 150px; }
  .dcx-tbtn { padding: 8px 10px; }
}

/* ===== S11（V2 文档）：创建页/列表页浅色主题（播放页保留深色=课堂全屏模式） ===== */
.dcx-shell.dcx-light { background: #f8fafc; color: #0f172a; }
.dcx-shell.dcx-light .dcx-top { background: rgba(255, 255, 255, .82); border-bottom-color: var(--line, #e2e8f0); }
.dcx-shell.dcx-light .dcx-vline { background: var(--line, #e2e8f0); }
.dcx-shell.dcx-light .dcx-course-name { color: #0f172a; }
.dcx-shell.dcx-light .dcx-course-sub { color: #94a3b8; }
.dcx-shell.dcx-light .dcx-pagechip { color: #4f46e5; background: #eef2ff; border-color: #374151; }
.dcx-shell.dcx-light .dcx-pagechip.generating { color: #92400e; background: #fffbeb; border-color: #fde68a; }
.dcx-shell.dcx-light .dcx-round { border-color: var(--line, #e2e8f0); background: #fff; color: #475569; }
.dcx-shell.dcx-light .dcx-round:hover:not(:disabled) { border-color: #4f46e5; color: #4f46e5; }
.dcx-shell.dcx-light .dcx-round.on { background: #eef2ff; border-color: #4f46e5; color: #4f46e5; }
.dcx-shell.dcx-light .dcx-tbtn { border-color: var(--line, #e2e8f0); background: #fff; color: #475569; }
.dcx-shell.dcx-light .dcx-tbtn:hover:not(:disabled) { border-color: #4f46e5; color: #4f46e5; }
.dcx-shell.dcx-light .dcx-timer { color: #4f46e5; background: #eef2ff; border-color: #374151; }
.dcx-shell.dcx-light .dcx-home-page, .dcx-shell.dcx-light .dcx-creating { background: transparent; }
.dcx-shell.dcx-light .dcx-hero h1, .dcx-shell.dcx-light .dcx-hero-title { color: #0f172a; }
.dcx-shell.dcx-light .dcx-hero p, .dcx-shell.dcx-light .dcx-hero-sub, .dcx-shell.dcx-light .dcx-tip { color: #64748b; }
.dcx-shell.dcx-light .dcx-input, .dcx-shell.dcx-light .dcx-input.dark, .dcx-shell.dcx-light .dcx-topic {
  background: #fff; color: #0f172a; border-color: var(--line, #e2e8f0);
}
.dcx-shell.dcx-light .dcx-input::placeholder, .dcx-shell.dcx-light .dcx-topic::placeholder { color: #94a3b8; }
.dcx-shell.dcx-light .dcx-composer { background: #fff; border-color: var(--line, #e2e8f0); box-shadow: 0 10px 40px -12px rgba(79, 70, 229, .25); }
.dcx-shell.dcx-light .dcx-composer-bar { border-color: var(--line, #e2e8f0); }
.dcx-shell.dcx-light .dcx-composer-hint { color: #94a3b8; }
.dcx-shell.dcx-light .dcx-bar { background: var(--line, #e2e8f0); }
.dcx-shell.dcx-light .dcx-fill { background: var(--gradient-brand, linear-gradient(135deg, #4f46e5, #7c3aed)); }
.dcx-shell.dcx-light .dcx-card { background: #fff; border-color: var(--line, #e2e8f0); }
.dcx-shell.dcx-light .dcx-card-title { color: #0f172a; }
.dcx-shell.dcx-light .dcx-card-sub, .dcx-shell.dcx-light .dcx-card-foot { color: #64748b; }
.dcx-shell.dcx-light .dcx-btn.ghost-dark { background: #fff; color: #475569; border: 1px solid var(--line, #e2e8f0); }
.dcx-shell.dcx-light .dcx-btn.ghost-dark:hover { border-color: #4f46e5; color: #4f46e5; }
.dcx-shell.dcx-light .dcx-mini { background: #fff; border-color: var(--line, #e2e8f0); color: #475569; }
.dcx-shell.dcx-light .dcx-mini.danger { color: #dc2626; border-color: #fecaca; }
.dcx-shell.dcx-light .dcx-entry, .dcx-shell.dcx-light .dcx-library, .dcx-shell.dcx-light .dcx-alt-entries { background: #fff; border-color: var(--line, #e2e8f0); }
.dcx-shell.dcx-light .dcx-library-head, .dcx-shell.dcx-light .dcx-entry-head { color: #0f172a; }
.dcx-shell.dcx-light .dcx-lib-empty, .dcx-shell.dcx-light .dcx-file-name { color: #64748b; }
.dcx-shell.dcx-light .dcx-orb.o1 { background: rgba(99, 102, 241, .18); }
.dcx-shell.dcx-light .dcx-orb.o2 { background: rgba(6, 182, 212, .14); }
.dcx-shell.dcx-light .dcx-confirm { background: #fff; color: #0f172a; }
.dcx-shell.dcx-light .dcx-confirm-title { color: #0f172a; }
.dcx-shell.dcx-light .dcx-edit-src { background: var(--bg2, #f1f5f9); color: #0f172a; }
.dcx-shell.dcx-light .dcx-dot-pulse { background: #4f46e5; }

/* S12 质量透明：验证徽标（数学验证器逐页放行的课堂才显示） */
.dcx-verified-tag {
  margin-left: 8px; padding: 2px 8px; border-radius: 99px; vertical-align: middle;
  font-size: 10.5px; font-weight: 800; letter-spacing: .5px;
  color: #b7f4cf; background: rgba(82, 196, 26, .16); border: 1px solid rgba(82, 196, 26, .38);
}
.dcx-shell.dcx-light .dcx-verified-tag { color: #15803d; background: #ecfdf5; border-color: #a7f3d0; }

/* ================= P1-18 双师课堂·课堂页：浅色主题 + 三列一屏 + 对比度达标 ================= */
/* 主题面粉：浅灰底 / 白卡 / 浅边框 / 紫主按钮 / 淡紫选中 */
.dcx-shell.dcx-light { background: #f5f6fa; color: #1f2937; }

/* 顶栏 56px 固定；标题不换行 */
.dcx-shell.dcx-light .dcx-top { height: 56px; padding: 0 14px; background: #fff; border-bottom: 1px solid #e5e7eb; }
.dcx-shell.dcx-light .dcx-brand b { font-size: 16px; white-space: nowrap; color: #1f2937; }
.dcx-shell.dcx-light .dcx-course-name { color: #1f2937; }
.dcx-shell.dcx-light .dcx-course-sub { color: #6b7280; }
.dcx-shell.dcx-light .dcx-pagechip { color: #4f46e5; background: #eef2ff; border-color: #c7d2fe; }
.dcx-shell.dcx-light .dcx-pagechip.generating { color: #92400e; background: #fffbeb; border-color: #fde68a; }
.dcx-shell.dcx-light .dcx-timer { color: #4f46e5; background: #eef2ff; border-color: #c7d2fe; }
.dcx-shell.dcx-light .dcx-round { background: #fff; border-color: #e5e7eb; color: #4b5563; }
.dcx-shell.dcx-light .dcx-round.on { background: #eef2ff; border-color: #4f46e5; color: #4f46e5; }
.dcx-shell.dcx-light .dcx-tbtn { background: #fff; border-color: #e5e7eb; color: #4b5563; }
.dcx-shell.dcx-light .dcx-tbtn.on { background: #eef2ff; border-color: #4f46e5; color: #4f46e5; }
.dcx-shell.dcx-light .dcx-tbtn.solid { background: linear-gradient(135deg, #4f46e5, #6366f1); border-color: transparent; color: #fff; }
.dcx-shell.dcx-light .dcx-tbtn.solid:hover { filter: brightness(1.08); color: #fff; }
.dcx-shell.dcx-light .dcx-vline { background: #e5e7eb; }
.dcx-shell.dcx-light .dcx-avatar { border-color: #c7d2fe; }

/* 主体三列：严格高度预算，无页面滚动条 */
.dcx-body {
  grid-template-columns: 220px minmax(0, 1fr) 320px;
  height: calc(100vh - 56px); padding: 8px 10px; gap: 8px;
  align-items: stretch; overflow: hidden; align-content: stretch;
}
.dcx-left, .dcx-right {
  position: static; top: auto; max-height: none; height: 100%;
  display: flex; flex-direction: column; gap: 8px; overflow: hidden;
}
.dcx-center { min-width: 0; min-height: 0; overflow: visible; padding: 0; display: flex; flex-direction: column; gap: 8px; }

/* 卡片统一：白底浅框 */
.dcx-landscape .dcx-card, .dcx-portrait .dcx-card,
.dcx-left .dcx-card, .dcx-right .dcx-card { background: #fff; border-color: #e5e7eb; }
.dcx-card { background: #fff; border-color: #e5e7eb; }
.dcx-card-title { color: #1f2937; font-size: 13px; font-weight: 800; }

/* 左列：大纲全高，条目完整显示不截断 */
.dcx-left > .dcx-card:first-child { flex: 1; min-height: 0; display: flex; flex-direction: column; }
.dcx-outline { flex: 1; min-height: 0; gap: 4px; }
.dcx-oitem { padding: 3px 6px; font-size: 11px; line-height: 1.4; color: #374151; gap: 7px; border-radius: 7px; }
.dcx-oitem.done { color: #6b7280; }
.dcx-oitem.active { background: #eef2ff; color: #4f46e5; font-weight: 800; box-shadow: none; }
.dcx-oitem.active .dcx-ono { background: #c7d2fe; color: #4f46e5; }
.dcx-ono { width: 18px; height: 18px; font-size: 10px; color: #4b5563; background: #eef2ff; }
.dcx-ot { flex: 1; min-width: 0; overflow: visible; text-overflow: clip !important; white-space: normal !important; word-break: break-word; }
.dcx-oskeleton, .dcx-o-skel-lines { display: none; }
.dcx-side-link { color: #4f46e5; }
.dcx-links .dcx-side-link, .dcx-learned, .dcx-goal { font-size: 11.5px; color: #374151; }
.dcx-learned { margin-bottom: 6px; }
.dcx-goal i { color: #059669; }

/* ===== 中列：课件/白板弹性占满（讲稿 130 播控 44 固定，其余空间分给课件与白板） ===== */
.dcx-stage-wrap { flex: 1.3 1 0; min-height: 340px; }
.dcx-slide { height: 100%; min-height: 0; padding: 10px 14px; font-size: 12px; line-height: 1.5;
  background: #fff; color: #1f2937; border: 1px solid #e5e7eb;
  box-shadow: 0 10px 30px rgba(79, 70, 229, .1); border-radius: 14px; }
.dcx-ltitle { font-size: 15px !important; margin: 0 0 6px; line-height: 1.3; color: #1f2937; }
.dcx-ltitle small { font-size: 12px; color: #6b7280; }
.dcx-lmain .dcx-para, .dcx-lmain .dcx-theorem-body, .dcx-lmain .dcx-exq,
.dcx-lmain .dcx-step, .dcx-lmain .dcx-conclusion, .dcx-lmain .dcx-answer,
.dcx-lmain table, .dcx-lmain .dcx-table th, .dcx-lmain .dcx-table td { font-size: 12px; line-height: 1.5; color: #374151; }
.dcx-theorem, .dcx-example, .dcx-conclusion, .dcx-para { margin-bottom: 6px; padding: 5px 8px; }
.dcx-theorem { background: #f8faff; border-color: #dbe4ff; }
.dcx-example { background: #f6fcf8; border-color: #cfead8; }
.dcx-conclusion { background: #fffbeb; border-color: #fde68a; color: #78350f; }
.dcx-textbook { color: #1e40af; background: #f0f6ff; }
.dcx-lgrid { gap: 10px; align-items: start; }
.dcx-lfig { max-height: 312px; }
.dcx-lfig :deep(.mf3d__canvas) { height: 250px !important; }
.dcx-figcap { font-size: 11px; color: #6b7280; }
.dcx-exq { color: #14532d; }
.dcx-theorem-head b { color: #1e3a8a; }

/* 讲稿 130（头像 36 / 文字 12 / 最多 4 行） */
.dcx-teach { flex: 0 0 130px; min-height: 0; gap: 8px; padding: 6px 8px; background: #fff;
  border: 1px solid #e5e7eb; border-radius: 12px; align-items: flex-start; }
.dcx-tavatar, .dcx-tavatar.lead { width: 36px !important; height: 36px !important; }
.dcx-tbubble { min-width: 0; }
.dcx-tbhead { margin-bottom: 3px; }
.dcx-tbname { font-size: 12px; color: #1f2937; }
.dcx-tbstate { font-size: 10.5px; color: #047857; }
.dcx-tbstate.muted { color: #6b7280; }
.dcx-narr-lines { max-height: 80px; font-size: 12px; line-height: 1.5; color: #374151;
  overflow: hidden; display: -webkit-box; -webkit-line-clamp: 4; -webkit-box-orient: vertical; }
.dcx-narr-lines p { font-size: 12px; line-height: 1.5; color: #374151; padding: 1px 8px; }
.dcx-narr-lines p.played { color: #6b7280; }
.dcx-narr-lines p.on { background: #eef2ff; color: #4f46e5; font-weight: 700; box-shadow: inset 2.5px 0 0 #818cf8; }
.dcx-narr-empty { font-size: 12px; line-height: 1.6; color: #374151; }
.dcx-tutor-mini { width: 88px; padding: 6px 5px; background: #f8fafc; border-color: #e5e7eb; }
.dcx-tutor-mini b { font-size: 11px; color: #1f2937; }
.dcx-tutor-mini em { font-size: 10px; color: #047857; }
.dcx-mini-ask { font-size: 10.5px; padding: 3px 10px; color: #4f46e5; background: #eef2ff; border-color: #c7d2fe; }
.dcx-mini-ask:hover { background: #4f46e5; color: #fff; }

/* 播控 44（按钮压缩 / 进度条 4px / 不换行） */
.dcx-controls { flex: 0 0 44px; min-height: 0; padding: 4px 6px; flex-wrap: nowrap; gap: 6px;
  background: #fff; border: 1px solid #e5e7eb; border-radius: 12px; }
.dcx-ctl { height: 26px; width: 26px; min-width: 24px; font-size: 11px; background: #fff;
  border: 1px solid #e5e7eb; color: #374151; border-radius: 7px; }
.dcx-ctl.play { width: 30px; height: 30px; font-size: 13px; }
.dcx-ctl-sep { height: 16px; background: #e5e7eb; }
.dcx-ctl-page { font-size: 11px; color: #374151; }
.dcx-foot-q { font-size: 11px; color: #6b7280; }
.dcx-chipbtn { font-size: 11px; padding: 3px 8px; color: #374151; background: #fff; border-color: #e5e7eb; }
.dcx-chipbtn.ok { color: #047857; background: #ecfdf5; border-color: #a7f3d0; }
.dcx-chipbtn.again { color: #92400e; background: #fffbeb; border-color: #fde68a; }
.dcx-bar { background: #eef2ff; }
.dcx-bar.slim { height: 4px; }

/* 底部功能：页签头 32 + 内容弹性 */
.dcx-board { flex: 1 1 0; min-height: 280px; background: #fff; border: 1px solid #e5e7eb;
  border-radius: 12px; overflow: hidden; }
.dcx-btabs { flex: 0 0 32px; padding: 4px 10px 0; }
.dcx-btabs button { font-size: 12px; padding: 5px 10px; color: #6b7280; }
.dcx-btabs button.on { color: #4f46e5; border-bottom-color: #4f46e5; background: #eef2ff; }
.dcx-bthint { font-size: 10.5px; color: #6b7280; }
.dcx-wb { padding: 8px 12px; flex: 1; min-height: 0; font-size: 12px; color: #374151; gap: 8px; }
.dcx-wb :deep(canvas) { max-height: 200px; }
.dcx-wbsteps { grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 6px; }
.dcx-wbstep { background: #f8fafc; border-color: #e5e7eb; color: #1f2937; font-size: 12px; padding: 6px 9px; }
.dcx-wbempty { font-size: 11.5px; color: #6b7280; }
.dcx-tools { padding: 8px 12px; gap: 6px; }
.dcx-toolstip { font-size: 11.5px; color: #6b7280; }
.dcx-fchip { background: #f8fafc; border-color: #e5e7eb; color: #374151; font-size: 12px; padding: 5px 10px; }
.dcx-notes { padding: 8px 12px; display: flex; flex-direction: column; gap: 8px; }
.dcx-notes-area { min-height: 0; flex: 1; }

/* ===== 右列：AI助教全高 + 分层练习压缩 ===== */
.dcx-tutor { flex: 1; min-height: 0; gap: 8px; padding: 10px; background: #fff; border: 1px solid #e5e7eb; border-radius: 12px; flex-direction: column; }
.dcx-upload { flex: 0 0 78px; padding: 8px 10px; border: 1.5px dashed #c7d2fe; background: #eef2ff; }
.dcx-uptxt b { font-size: 12.5px; color: #1f2937; }
.dcx-uptxt small { font-size: 11px; color: #6b7280; }
.dcx-quick { flex: 0 0 40px; flex-wrap: nowrap; gap: 6px; }
.dcx-qchip { font-size: 11px; padding: 5px 8px; color: #4f46e5; background: #fff; border: 1px solid #c7d2fe; }
.dcx-msgs { flex: 1; min-height: 0; max-height: none; font-size: 12px; color: #374151; padding: 8px 2px; gap: 8px; }
.dcx-msg-tip { font-size: 11.5px; color: #6b7280; }
.dcx-thinking { font-size: 12px; color: #6b7280; }
.dcx-askrow { flex: 0 0 44px; min-height: 0; }
.dcx-send { width: 34px; height: 34px; }
.dcx-input, .dcx-input.dark { background: #fff; border-color: #d1d5db; color: #1f2937; font-size: 12px; }
.dcx-input::placeholder, .dcx-input.dark::placeholder, textarea.dcx-input.dark::placeholder { color: #9ca3af; }

/* 分层练习压缩卡 */
.dcx-practice { flex: 0 0 264px; min-height: 0; gap: 6px; padding: 9px 10px; background: #fff; border: 1px solid #e5e7eb; border-radius: 12px; display: flex; flex-direction: column; overflow: hidden; }
.dcx-practice.pending { flex-direction: row; align-items: center; font-size: 12px; color: #4f46e5; }
.dcx-tiers { gap: 5px; }
.dcx-tier { font-size: 11px; padding: 5px 0; color: #4b5563; background: #fff; border-color: #e5e7eb; }
.dcx-tier.on { background: #eef2ff; border-color: #c7d2fe; color: #4f46e5; box-shadow: none; }
.dcx-pq { font-size: 12px; color: #1f2937; line-height: 1.6; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.dcx-popts { gap: 5px; }
.dcx-popt { font-size: 12px; padding: 5px 9px; color: #374151; background: #f8fafc; border-color: #e5e7eb; }
.dcx-popt b { width: 18px; height: 18px; font-size: 10px; background: #eef2ff; color: #4f46e5; }
.dcx-popt.right { border-color: #86efac; background: #f0fdf4; }
.dcx-popt.right b { background: #16a34a; color: #fff; }
.dcx-popt.wrong { border-color: #fca5a5; background: #fef2f2; }
.dcx-popt.wrong b { background: #dc2626; color: #fff; }
.dcx-popt i { color: #047857; }
.dcx-popt i.bad { color: #dc2626; }
.dcx-pa { font-size: 11.5px; color: #334155; background: #f5f3ff; border-color: #ddd6fe; padding: 7px 10px; max-height: 60px; overflow: hidden; }
.dcx-pa b { color: #4f46e5; }
.dcx-pempty { font-size: 11.5px; color: #6b7280; }
.dcx-pfoot { gap: 7px; }
.dcx-ring { width: 38px; height: 38px; }
.dcx-ring span { width: 30px; height: 30px; font-size: 9.5px; color: #047857; }
.dcx-pcount { font-size: 11px; color: #374151; }

/* 状态卡 / 弹窗浅色化 */
.dcx-state { background: #fff; border-color: #e5e7eb; color: #b91c1c; }
.dcx-rstat { background: #f8fafc; border-color: #e5e7eb; }
.dcx-rstat b { color: #1f2937; }
.dcx-rstat span { color: #6b7280; }
.dcx-review-item:hover { background: #f8fafc; border-color: #c7d2fe; }
.dcx-review-name { color: #1f2937; }
.dcx-review-sub { color: #6b7280; }
.dcx-o-chip { background: #fff; border-color: #e5e7eb; color: #4b5563; }
.dcx-o-chip.done { color: #047857; border-color: #a7f3d0; }

/* 窄屏：页码条「随堂练习」把既有练习卡展开为底部抽屉（同一 DOM/答题逻辑，不复制） */
@media (max-width: 1100px) {
  .dcx-practice.dcx-practice--drawer {
    position: fixed; z-index: 74; left: 12px; right: 12px; bottom: 12px;
    max-height: 62vh; overflow-y: auto; box-shadow: 0 24px 64px rgba(15, 23, 42, .35);
    border-color: #c7d2fe;
  }
}

/* 老师课堂：课件是主内容，讲稿给足高度（可滚动），白板让位但不删除 */
.dcx-center--teacher-deck .dcx-narr-lines {
  max-height: 170px;
  -webkit-line-clamp: unset;
  overflow-y: auto;
}
.dcx-center--teacher-deck .dcx-board { max-height: 110px; }

/* 分层练习：题库/题干/选项/下一题必须可读可操作。
   旧样式固定 264px + overflow:hidden，长题干与“下一题”会被裁掉（真实链路复审阻断）。 */
.dcx-right .dcx-practice:not(.dcx-practice--drawer) {
  flex: 0 0 auto;
  max-height: 42vh;
  overflow-y: auto;
  overscroll-behavior: contain;
}
.dcx-right .dcx-practice .dcx-pq {
  display: block;
  -webkit-line-clamp: unset;
  overflow: visible;
}
.dcx-right .dcx-practice .dcx-pa {
  max-height: none;
  overflow: visible;
}
</style>
