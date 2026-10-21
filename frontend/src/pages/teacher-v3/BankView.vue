<template>
  <div class="tv3-page-shell" data-testid="tv3-bank">
    <TeacherPageHeader title="题库与组卷" subtitle="题库管理 · 选题组卷 · 发布到学生课堂任务" icon="📚">
      <div class="tv3-seg" style="margin-right: 4px" data-testid="tv3-bank-quiz-tabs">
        <router-link to="/teacher-v3/bank" class="tv3-seg__btn is-active" data-testid="tv3-tab-bank">题库</router-link>
        <router-link to="/teacher-v3/quiz" class="tv3-seg__btn" data-testid="tv3-bank-to-quiz">组卷</router-link>
      </div>
      <span class="tv3-tag tv3-tag--gold">图片题原样入库 · 不强转文字公式</span>
    </TeacherPageHeader>

    <div style="display: flex; gap: 14px; align-items: flex-start">
      <!-- 左：知识点分类树 + 我的专题夹 -->
      <div style="width: 252px; flex-shrink: 0; display: flex; flex-direction: column; gap: 12px">
        <div class="tv3-card">
          <div class="tv3-card__head">
            <span class="tv3-card__title">分类树</span>
            <span class="tv3-card__sub">模块 ▸ 章 ▸ 知识点</span>
            <div class="tv3-card__spacer" />
            <button class="tv3-btn tv3-btn--sm" :class="{ 'tv3-btn--primary': treeManage }" style="font-size:10.5px" data-testid="tv3-bank-tree-manage" @click="treeManage = !treeManage">{{ treeManage ? '✓ 完成' : '✎ 编辑' }}</button>
          </div>
          <div class="tv3-card__body tv3-treescroll" :class="{ 'tv3-tree-manage': treeManage }" data-testid="tv3-bank-tree">
            <button class="tv3-tree-row" :class="{ 'is-sel': !selectedKp.id && !selectedFolder }" data-testid="tv3-bank-all" @click="selectKp({ id: '', name: '', codes: null, depth: 0, leaf: false, count: 0 })">
              全部知识点 <span class="tv3-tree-row__cnt">{{ totalCount }}</span>
            </button>
            <template v-for="n in treeItems" :key="n.id">
              <div v-if="kpRenameId === n.id" class="tv3-tree-edit" :data-testid="`tv3-kp-rename-${n.id}`">
                <input v-model="kpEditValue" class="tv3-input" style="flex:1;min-width:0" @keyup.enter="kpRenameConfirm" />
                <button class="tv3-btn tv3-btn--sm tv3-btn--primary" data-testid="tv3-kp-rename-go" @click="kpRenameConfirm">✓</button>
                <button class="tv3-btn tv3-btn--sm" @click="kpRenameId = ''">×</button>
              </div>
              <div v-else class="tv3-tree-line">
                <button class="tv3-tree-row" :class="['lv' + n.depth, { 'is-sel': selectedKp.id === n.id }]" :data-testid="`tv3-bank-kp-${n.id}`" @click="selectKp(n)">
                  <span class="tv3-tree-row__folder">{{ n.leaf ? '·' : '▾' }}</span>
                  {{ n.name }} <span class="tv3-tree-row__cnt">{{ cntOf(n) }}</span>
                </button>
                <span v-if="treeManage" class="tv3-tree-ops">
                  <button class="tv3-tree-op" :title="n.leaf ? '同级新建' : '加子级'" :data-testid="`tv3-kp-add-${n.id}`" @click.stop="kpAddOpen(n.leaf ? parentOf(n.id) : n.id)">＋</button>
                  <button class="tv3-tree-op" title="重命名" :data-testid="`tv3-kp-ren-${n.id}`" @click.stop="kpRenameOpen(n)">✎</button>
                  <button class="tv3-tree-op" title="删除（题目保留，归属变为未分类）" :data-testid="`tv3-kp-del-${n.id}`" @click.stop="kpDelete(n)">×</button>
                </span>
              </div>
              <div v-if="treeManage && kpAddParent === n.id" class="tv3-tree-edit" style="margin-left: 34px" :data-testid="`tv3-kp-addrow-${n.id}`">
                <input v-model="kpEditValue" class="tv3-input" style="flex:1;min-width:0" placeholder="新知识点名称" data-testid="tv3-kp-add-input" @keyup.enter="kpAddConfirm" />
                <button class="tv3-btn tv3-btn--sm tv3-btn--primary" data-testid="tv3-kp-add-go" @click="kpAddConfirm">✓</button>
                <button class="tv3-btn tv3-btn--sm" @click="kpAddParent = ''">×</button>
              </div>
            </template>
            <div v-if="treeManage" style="padding: 8px 6px 2px">
              <button class="tv3-btn tv3-btn--sm" style="width:100%" data-testid="tv3-kp-add-root" @click="kpAddOpen('')">＋ 顶层新模块</button>
            </div>
            <div v-if="treeManage" style="font-size:10.5px;color:var(--tv3-ink4);padding:2px 8px 6px">编辑保存为你的个人分类层，不影响教研组公共树。</div>
          </div>
        </div>

        <!-- 专题夹（引用式收集：题目挂树上，夹子存引用，跨知识点不挪题） -->
        <div class="tv3-card">
          <div class="tv3-card__head">
            <span class="tv3-card__title">我的专题夹</span>
            <span class="tv3-card__sub">跨知识点引用</span>
            <div class="tv3-card__spacer" />
            <button class="tv3-btn tv3-btn--sm tv3-btn--gold" data-testid="tv3-bank-folder-new" @click="folderCreateOpen = true">＋ 新建</button>
          </div>
          <div class="tv3-card__body" style="display: flex; flex-direction: column; gap: 4px" data-testid="tv3-bank-folders">
            <button
              v-for="f in folders" :key="f.id" class="tv3-tree-row"
              :class="{ 'is-sel': selectedFolder === f.id }" :data-testid="`tv3-bank-folder-${f.id}`"
              @click="toggleFolder(f.id)"
            >
              <span class="tv3-tree-row__folder">🗂</span>
              <span style="flex: 1; text-align: left; overflow: hidden; text-overflow: ellipsis; white-space: nowrap">{{ f.name }}</span>
              <span class="tv3-tree-row__cnt">{{ f.count }}</span>
              <span class="tv3-bank__folder-del" :data-testid="`tv3-bank-folder-del-${f.id}`" title="删除专题夹（题目不受影响）" @click.stop="removeFolder(f.id)">×</span>
            </button>
            <div v-if="!folders.length" style="font-size: 12px; color: var(--tv3-ink4); padding: 10px 4px">
              还没有专题夹。新建一个，把分散在各知识点的题按「专题」引用收集（如：圆锥曲线压轴、易错题集）。
            </div>
          </div>
        </div>
      </div>

      <!-- 右：题目列表 -->
      <div class="tv3-card" style="flex: 1; min-width: 0">
        <div class="tv3-card__head">
          <span class="tv3-card__title">{{ selectedFolderName ? `专题夹 · ${selectedFolderName}` : selectedKp.name || '全部知识点' }}</span>
          <span class="tv3-card__sub">{{ filtered.length }} / {{ totalCount }} 题</span>
          <div class="tv3-card__spacer" />
          <input v-model="search" class="tv3-input" style="width: 170px" placeholder="搜题干关键词" data-testid="tv3-bank-search">
          <select v-model="typeFilter" class="tv3-input" style="width: 96px" data-testid="tv3-bank-type">
            <option value="">全部题型</option>
            <option value="choice">选择题</option>
            <option value="fill">填空题</option>
            <option value="solve">解答题</option>
            <option value="image">图片题</option>
          </select>
          <select v-model="diffFilter" class="tv3-input" style="width: 96px" data-testid="tv3-bank-diff">
            <option value="">全部难度</option>
            <option value="easy">容易</option>
            <option value="medium">中等</option>
            <option value="hard">较难</option>
          </select>
          <select v-model="sourceFilter" class="tv3-input" style="width: 108px" data-testid="tv3-bank-source">
            <option value="">全部来源</option>
            <option>校本</option><option>区库</option><option>拍照入库</option><option>自编</option><option>AI配题</option>
          </select>
          <button class="tv3-btn tv3-btn--sm" data-testid="tv3-bank-create-open" @click="openCreate">＋ 自编录入</button>
          <button class="tv3-btn tv3-btn--sm tv3-btn--gold" data-testid="tv3-bank-scan-open" @click="scanOpen = true">＋ 拍照入库</button>
        </div>
        <div class="tv3-card__body tv3-qscroll" data-testid="tv3-bank-list">
          <TeacherLoading v-if="loading" />
          <TeacherEmptyState
            v-else-if="!filtered.length"
            :title="questions.length ? '暂无匹配题目' : '题库为空'"
            :desc="questions.length ? '当前筛选下暂无题目，试试切换分类树 / 专题夹或调整筛选条件。' : '题库为空，上传教材自动切片入库，或手动录入题目。'"
            :cta="questions.length ? '' : '＋ 自编录入'"
            @cta-click="openCreate"
          />
          <div v-for="q in filtered" :key="q.id" class="tv3-qrow" :class="{ 'tv3-qrow--image': q.q_type === 'image' }" :data-testid="`tv3-bank-q-${q.id}`">
            <div style="flex: 1; min-width: 0">
              <div style="display: flex; gap: 6px; align-items: center; flex-wrap: wrap">
                <span class="tv3-tag" :class="q.difficulty === 'easy' ? 'tv3-tag--ok' : q.difficulty === 'hard' ? 'tv3-tag--danger' : 'tv3-tag--warn'">{{ diffLabel(q.difficulty) }}</span>
                <span class="tv3-tag tv3-tag--primary">{{ q.kp_name }}</span>
                <span v-if="q.q_type === 'image'" class="tv3-tag tv3-tag--gold">图片题</span>
                <span v-else class="tv3-tag">{{ typeLabel(q.q_type) }}</span>
                <span class="tv3-tag" :class="q.source === '拍照入库' ? 'tv3-tag--gold' : q.source === '自编' ? 'tv3-tag--ai' : ''">{{ q.source }}</span>
                <span v-if="q.year" class="tv3-tag">{{ q.year }}</span>
                <span v-if="q.usage_count" style="font-size: 11px; color: var(--tv3-ink4)">被引用 {{ q.usage_count }} 次</span>
                <span v-if="selectedFolder" class="tv3-btn tv3-btn--sm tv3-btn--ghost" :data-testid="`tv3-bank-unfolder-${q.id}`" @click="removeFromFolder(q.id)">移出本夹</span>
                <button class="tv3-bank__del" :data-testid="`tv3-bank-del-${q.id}`" title="删除这道题（从题库及所有专题夹移除）" @click.stop="deleteQuestion(q)">×</button>
              </div>
              <!-- 2026-10-05：只要有题图就渲染（此前仅 q_type==='image' 才显示，
                   导致「自编题带配图」这类 文字+图 题目的图永远出不来）。
                   src 用 imgSrc（fetch+Bearer→blob 的 objectURL）——
                   题图代理路由要鉴权，<img> 标签带不了 Authorization 头。 -->
              <div v-if="q.stem_image" class="tv3-qrow__img">
                <img :src="imgSrc[q.id]?.stem || ''" alt="题干图" data-testid="tv3-bank-stem-image" />
                <span class="tv3-qrow__img-cap">{{ q.q_type === 'image' ? '题干为扫描原图（原样入库，不做转换）' : '题干配图' }}</span>
              </div>
              <div class="tv3-qrow__stem" v-html="stemOf(q)" />
              <div v-if="q.options" class="tv3-qrow__opts">
                <span v-for="(o, i) in q.options" :key="i" class="tv3-qrow__opt" :class="{ 'is-answer': q.answer === String.fromCharCode(65 + i) }" v-html="renderStem(o)" />
              </div>
              <!-- 答案与解析（可折叠） -->
              <div class="tv3-bank__meta">
                <button class="tv3-bank__meta-toggle" :data-testid="`tv3-bank-detail-${q.id}`" @click="toggleDetail(q.id)">
                  {{ detailOpen === q.id ? '收起答案解析 ▴' : '答案与解析 ▾' }}
                </button>
                <div v-if="detailOpen === q.id" class="tv3-bank__detail" :data-testid="`tv3-bank-detail-box-${q.id}`">
                  <div><b>答案：</b><span v-html="renderStem(q.answer)" /></div>
                  <div v-if="q.analysis" style="margin-top: 4px"><b>解析：</b><span v-html="renderStem(q.analysis)" /></div>
                  <div v-if="q.solution_image" style="margin-top: 6px" :data-testid="`tv3-bank-solution-${q.id}`">
                    <b>解答过程（原图）：</b>
                    <img :src="imgSrc[q.id]?.solution || ''" alt="解答过程原图" style="max-width: 240px; border-radius: 6px; display: block; margin-top: 4px" />
                  </div>
                  <div v-if="!q.analysis && !q.solution_image" style="margin-top: 4px; color: var(--tv3-ink4)">暂无解析（可在自编录入时补充，或拍照入库后补录解答图）</div>
                </div>
              </div>
            </div>
            <!-- 入夹：未选中专题夹时的快捷收集 -->
            <div v-if="!selectedFolder && folders.length" class="tv3-bank__folder-add">
              <select class="tv3-input" :data-testid="`tv3-bank-addfold-${q.id}`" @change="addToFolder(q.id, $event)">
                <option value="">🗂 入夹…</option>
                <option v-for="f in folders" :key="f.id" :value="f.id">{{ f.name }}</option>
              </select>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- 新建专题夹弹层 -->
    <div v-if="folderCreateOpen" class="tv3-modal" data-testid="tv3-bank-folder-modal" @click.self="folderCreateOpen = false">
      <div class="tv3-modal__box" style="width: 420px">
        <div class="tv3-modal__head"><span class="tv3-card__title">新建专题夹</span><span class="tv3-card__sub">引用式收集 · 题目仍挂分类树</span></div>
        <div class="tv3-modal__body" style="display: flex; flex-direction: column; gap: 10px">
          <div class="tv3-form-label">名称</div>
          <input v-model="newFolder.name" class="tv3-input tv3-input--block" placeholder="例如：圆锥曲线压轴 / 期中讲评变式" data-testid="tv3-bank-folder-name">
          <div class="tv3-form-label">说明（选填）</div>
          <input v-model="newFolder.desc" class="tv3-input tv3-input--block" placeholder="例如：近年高考真题 · 弦长/点差/联立" data-testid="tv3-bank-folder-desc">
          <div class="tv3-modal__foot">
            <button class="tv3-btn tv3-btn--sm" @click="folderCreateOpen = false">取消</button>
            <button class="tv3-btn tv3-btn--sm tv3-btn--primary" :disabled="!newFolder.name.trim()" data-testid="tv3-bank-folder-go" @click="createFolder">创建</button>
          </div>
        </div>
      </div>
    </div>

    <!-- 拍照入库弹层（与组卷中心同款：图片题原样 / 识别成结构题） -->
    <div v-if="scanOpen" class="tv3-modal" data-testid="tv3-bank-scan-modal" @click.self="scanOpen = false">
      <div class="tv3-modal__box" style="width: 480px">
        <div class="tv3-modal__head">
          <span class="tv3-card__title">拍照 / 扫描入库</span>
          <span class="tv3-card__sub">手写或试卷原图 → 进题库</span>
        </div>
        <div class="tv3-modal__body" style="display: flex; flex-direction: column; gap: 12px">
          <div class="tv3-form-label">1. 上传图片 <span style="color:var(--tv3-ink4);font-weight:400">题干与解答分开传，原样保留不转文字（解答可后补）</span></div>
          <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 10px">
            <label class="tv3-upload" data-testid="tv3-bank-scan-file" @dragover.prevent @drop.prevent="onFileDrop($event)">
              <input type="file" accept="image/*" hidden @change="onFilePick($event)" data-testid="tv3-bank-scan-input" />
              <span v-if="!scanSrc" style="font-weight:600;color:var(--tv3-ink2)">① 题干图（必传）</span>
              <span v-if="!scanSrc" class="tv3-upload__hint">{{ dragHint }}</span>
              <img v-else :src="scanSrc" alt="题干扫描原图" data-testid="tv3-bank-scan-preview" style="max-height: 150px; border-radius: 8px" />
            </label>
            <label class="tv3-upload" data-testid="tv3-bank-scan-sol-file">
              <input type="file" accept="image/*" hidden @change="onSolPick($event)" data-testid="tv3-bank-scan-sol-input" />
              <span v-if="!scanSolSrc" style="font-weight:600;color:var(--tv3-ink2)">② 解答过程图（选传）</span>
              <span v-if="!scanSolSrc" class="tv3-upload__hint">详细解答 / 手写过程，可入库后补</span>
              <img v-else :src="scanSolSrc" alt="解答过程原图" data-testid="tv3-bank-scan-sol-preview" style="max-height: 150px; border-radius: 8px" />
            </label>
          </div>
          <div style="display:flex;gap:8px">
            <button class="tv3-btn tv3-btn--sm tv3-btn--ghost" data-testid="tv3-bank-scan-sample" @click="useSample">用示例题图</button>
            <button v-if="scanSolSrc" class="tv3-btn tv3-btn--sm" @click="scanSolSrc = ''">移除解答图</button>
          </div>
          <div class="tv3-form-label">2. 入库方式（避免转换出错：图片题原样保留原图）</div>
          <div class="tv3-radio-row">
            <label class="tv3-radio"><input type="radio" value="image" v-model="mode" data-testid="tv3-bank-scan-mode-image" /> 图片题型原样入库<span class="tv3-radio__hint">保留扫描原图作题干，不转文字/公式</span></label>
            <label class="tv3-radio"><input type="radio" value="recognize" v-model="mode" data-testid="tv3-bank-scan-mode-recognize" /> 识别成结构题<span class="tv3-radio__hint">走识别链路，生成可编辑的公式题干（需人工校对）</span></label>
          </div>
          <div class="tv3-form-label">3. 归属知识点 <span style="color:var(--tv3-ink4);font-weight:400">可让 AI 先认，再手动纠正；树可在左侧「✎ 编辑」维护</span></div>
          <select v-model="scanKpCode" class="tv3-input tv3-input--block" data-testid="tv3-bank-scan-kp">
            <option v-for="k in kpLeafOptions" :key="k.code" :value="k.code">{{ k.path }}</option>
          </select>
          <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
            <button class="tv3-btn tv3-btn--sm tv3-btn--ghost-ai" :disabled="suggestLoading || (!scanSrc && !scanSolSrc)" data-testid="tv3-bank-scan-suggest" @click="runSuggest">
              {{ suggestLoading ? '识别中…' : '✦ AI 识别知识点' }}
            </button>
            <template v-if="suggest">
              <button class="tv3-btn tv3-btn--sm tv3-btn--gold" data-testid="tv3-bank-suggest-apply" @click="applySuggest(suggest.suggestion.code, suggest.suggestion.path)">
                ✓ {{ suggest.suggestion.path.join(' ▸ ') }}（{{ (suggest.suggestion.confidence * 100).toFixed(0) }}%）
              </button>
              <button v-for="a in suggest.alternates" :key="a.code" class="tv3-btn tv3-btn--sm" :data-testid="`tv3-bank-suggest-alt-${a.code}`" @click="applySuggest(a.code, a.path)">
                {{ a.path.join(' ▸ ') }}
              </button>
            </template>
            <span v-if="suggest" style="font-size:11px;color:var(--tv3-ink4)">{{ suggest.note }} · 点选即填入</span>
          </div>
          <div class="tv3-modal__foot">
            <button class="tv3-btn tv3-btn--sm" @click="scanOpen = false">取消</button>
            <button class="tv3-btn tv3-btn--sm tv3-btn--primary" :disabled="!scanKey" data-testid="tv3-bank-scan-import" @click="doScanImport">确认入库</button>
          </div>
        </div>
      </div>
    </div>

    <!-- 自编录入弹层（结构化手输，source='自编'） -->
    <div v-if="createOpen" class="tv3-modal" data-testid="tv3-bank-create-modal" @click.self="createOpen = false">
      <div class="tv3-modal__box" style="width: 620px">
        <div class="tv3-modal__head">
          <span class="tv3-card__title">自编录入</span>
          <span class="tv3-card__sub">结构化题目 · 题干/答案/解析均可用 $..$ 写公式</span>
        </div>
        <div class="tv3-modal__body" style="display: flex; flex-direction: column; gap: 10px">
          <div class="tv3-form-label">归属知识点</div>
          <select v-model="createForm.kp_code" class="tv3-input tv3-input--block" data-testid="tv3-bank-create-kp">
            <option v-for="k in kpLeafOptions" :key="k.code" :value="k.code">{{ k.path }}</option>
          </select>
          <div style="display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 8px">
            <div>
              <div class="tv3-form-label">题型</div>
              <select v-model="createForm.q_type" class="tv3-input tv3-input--block" data-testid="tv3-bank-create-type">
                <option value="choice">选择题</option><option value="fill">填空题</option><option value="solve">解答题</option>
              </select>
            </div>
            <div>
              <div class="tv3-form-label">难度</div>
              <select v-model="createForm.difficulty" class="tv3-input tv3-input--block" data-testid="tv3-bank-create-diff">
                <option value="easy">容易</option><option value="medium">中等</option><option value="hard">较难</option>
              </select>
            </div>
            <div>
              <div class="tv3-form-label">年份（选填）</div>
              <input v-model="createForm.year" class="tv3-input tv3-input--block" placeholder="2026" data-testid="tv3-bank-create-year">
            </div>
          </div>
          <div class="tv3-form-label">题干（$..$ 实时预览公式）</div>
          <textarea v-model="createForm.stem_latex" class="tv3-textarea" rows="3" data-testid="tv3-bank-create-stem" placeholder="例如：过椭圆 $\frac{x^2}{4}+\frac{y^2}{3}=1$ 右焦点且斜率为 1 的直线交椭圆于 A、B，求 $|AB|$" />
          <div class="tv3-bank__stem-preview" v-html="renderStem(createForm.stem_latex || '题干预览')" data-testid="tv3-bank-create-preview" />
          <div class="tv3-form-label">题干配图（选填 · 几何图/函数图象等，与文字题干同时呈现）</div>
          <div style="display: flex; gap: 8px; align-items: center">
            <input type="file" accept="image/*" class="tv3-input" style="flex: 1" data-testid="tv3-bank-create-img" @change="onCreateImgPick" />
            <button v-if="createForm.stem_image" class="tv3-btn tv3-btn--sm" data-testid="tv3-bank-create-img-clear" @click="onCreateImgClear">移除</button>
          </div>
          <div v-if="createImgHint || createForm.stem_image" style="font-size: 11px; color: var(--tv3-ink4)">
            {{ createImgHint || `已上传配图：${createForm.stem_image_name}` }}
          </div>
          <div class="tv3-form-label">答案</div>
          <textarea v-model="createForm.answer" class="tv3-textarea" rows="2" data-testid="tv3-bank-create-answer" placeholder="例如：$\frac{24}{7}$" />
          <div class="tv3-form-label">解析（选填）</div>
          <textarea v-model="createForm.analysis" class="tv3-textarea" rows="2" data-testid="tv3-bank-create-analysis" placeholder="例如：联立直线与椭圆，韦达定理 + 弦长公式。" />
          <div class="tv3-modal__foot">
            <button class="tv3-btn tv3-btn--sm" @click="createOpen = false">取消</button>
            <button class="tv3-btn tv3-btn--sm tv3-btn--primary" :disabled="!createForm.stem_latex.trim() || !createForm.kp_code" data-testid="tv3-bank-create-go" @click="doCreate">录入题库</button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * BankView —— 题库（V3.1 独立页）
 * 组织方式（RESEARCH_QUESTION_BANK §3）：三级分类树（模块▸章▸知识点）+ 多维标签（题型/难度/来源/年份）+ 我的专题夹（跨知识点引用式收集）。
 * 入库双通道：拍照原样入库（图片题，不转文字避免出错）+ 自编结构化录入（$..$ 公式）。
 * 专题夹是「引用」不是「移动」：题目挂树上，夹子存引用（可跨知识点收集，删夹不影响题目）。
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { v3Api, type V3QuizQuestion, type V3KpTreeNode, type V3QuestionFolder } from '@/api/teacherV3'
import { TeacherPageHeader, TeacherEmptyState, TeacherLoading } from '@/components/teacherV3/common'
import { renderLatex, renderStem } from '@/components/mathx/latex'
import { presignUpload, uploadFile } from '@/api/teacherV3Upload'
import { getToken } from '@/api/client'
import { updateTv3Context } from '@/stores/teacherContext'
import { setReceipt, registerUndo } from '@/stores/companion'

interface TreeItem { id: string; name: string; depth: number; leaf: boolean; codes: string[] | null; count: number }

const loading = ref(true)
const questions = ref<V3QuizQuestion[]>([])
const totalCount = ref(0)
/** 题图 objectURL（fetch+Bearer→blob；题图代理路由要鉴权，<img> 带不了 Authorization 头） */
const imgSrc = ref<Record<string, { stem?: string; solution?: string }>>({})

async function resolveQuizImage(id: string, kind: 'stem' | 'solution', url: string | null | undefined) {
  if (!url) return
  if (/^(data:|blob:|https?:)/i.test(url)) {
    ;(imgSrc.value[id] ||= {})[kind] = url
    return
  }
  // 2026-10-05 线上实测：刷新页面后 token 是纯内存的（authSession），
  // 裸 fetch 抢在应用 401→refresh 流程之前发出 → 401 → 图空。
  // 处理：401 时等待应用的 refresh 完成后重试（最多 6 次）。
  for (let attempt = 0; attempt < 6; attempt++) {
    try {
      const token = getToken()
      const res = await fetch(url, { headers: token ? { Authorization: `Bearer ${token}` } : {} })
      if (res.ok) {
        const blob = await res.blob()
        ;(imgSrc.value[id] ||= {})[kind] = URL.createObjectURL(blob)
        return
      }
      if (res.status !== 401) return
    } catch { /* 网络/断连：不再重试 */ return }
    await new Promise((r) => setTimeout(r, 700))
  }
}

function resolveAllQuizImages() {
  for (const q of questions.value) {
    void resolveQuizImage(q.id, 'stem', q.stem_image)
    void resolveQuizImage(q.id, 'solution', q.solution_image)
  }
}
const tree = ref<V3KpTreeNode[]>([])
const folders = ref<V3QuestionFolder[]>([])
const search = ref('')
const typeFilter = ref('')
const diffFilter = ref('')
const sourceFilter = ref('')
const selectedKp = ref<TreeItem>({ id: '', name: '', depth: 0, leaf: false, codes: null, count: 0 })
const selectedFolder = ref('')
const detailOpen = ref('')

const scanOpen = ref(false)
const scanSrc = ref('')
const scanSolSrc = ref('')
/* M2-C：直传后的对象 key（预签名 PUT 完成）；无 key 时回落 dataURL（示例图/演示） */
const scanKey = ref('')
const scanSolKey = ref('')
const scanKpCode = ref('')
const mode = ref<'image' | 'recognize'>('image')
const dragHint = ref('点击或拖入试卷/手写照片')
/* V3.2：AI 识别知识点 + 分类树编辑态 */
const suggestLoading = ref(false)
const suggest = ref<{ suggestion: { code: string; path: string[]; name: string; confidence: number }; alternates: { code: string; path: string[]; name: string }[]; note: string } | null>(null)
const treeManage = ref(false)
const kpAddParent = ref<string | null>(null)
const kpRenameId = ref('')
const kpEditValue = ref('')

const createOpen = ref(false)
const createForm = ref({
  kp_code: '', kp_name: '', kp_path: [] as string[],
  q_type: 'solve' as 'choice' | 'fill' | 'solve' | 'image',
  difficulty: 'medium' as 'easy' | 'medium' | 'hard',
  year: '', stem_latex: '', answer: '', analysis: '',
  stem_image: '' as string,   // 选填题干配图（MinIO key，经 /upload/file 上传）
  stem_image_name: '' as string,
})
const createImgUploading = ref(false)
const createImgHint = ref('')

/** 自编录入的选填题干配图（2026-10-05）：几何题/图象题经常「文字 + 图」缺一不可 */
function onCreateImgPick(e: Event) {
  const f = (e.target as HTMLInputElement).files?.[0]
  if (!f) return
  createImgUploading.value = true
  createImgHint.value = '上传中…'
  uploadFile(f)
    .then((h) => { createForm.value.stem_image = h.key; createForm.value.stem_image_name = f.name; createImgHint.value = '配图已上传' })
    .catch(() => { createForm.value.stem_image = ''; createForm.value.stem_image_name = ''; createImgHint.value = '配图上传失败，请重选' })
    .finally(() => { createImgUploading.value = false })
}
function onCreateImgClear() {
  createForm.value.stem_image = ''
  createForm.value.stem_image_name = ''
  createImgHint.value = ''
}

const folderCreateOpen = ref(false)
const newFolder = ref({ name: '', desc: '' })

const selectedFolderName = computed(() => folders.value.find((f) => f.id === selectedFolder.value)?.name || '')

const treeItems = computed<TreeItem[]>(() => {
  const out: TreeItem[] = []
  const walk = (nodes: V3KpTreeNode[], depth: number) => {
    for (const n of nodes) {
      if (n.children && n.children.length) {
        out.push({ id: n.id, name: n.name, depth, leaf: false, codes: collectCodes(n.children), count: Number(n.count ?? 0) })
        walk(n.children, depth + 1)
      } else {
        out.push({ id: n.id, name: n.name, depth, leaf: true, codes: collectCodes([n]), count: Number(n.count ?? 0) })
      }
    }
  }
  walk(tree.value, 1)
  return out
})

const kpLeafOptions = computed(() => {
  const opts: { code: string; path: string; name: string }[] = []
  const walk = (nodes: V3KpTreeNode[], prefix: string[]) => {
    for (const n of nodes) {
      const path = [...prefix, n.name]
      if (n.children && n.children.length) walk(n.children, path)
      else opts.push({ code: kpCodeOf(n), path: path.join(' ▸ '), name: n.name })
    }
  }
  walk(tree.value, [])
  return opts
})

const filtered = computed(() =>
  questions.value.filter((q) => {
    if (selectedFolder.value && !(q.folder_refs || []).includes(selectedFolder.value)) return false
    if (selectedKp.value.codes && !selectedKp.value.codes.includes(q.kp_code)) return false
    if (typeFilter.value && q.q_type !== typeFilter.value) return false
    if (diffFilter.value && q.difficulty !== diffFilter.value) return false
    if (sourceFilter.value && q.source !== sourceFilter.value) return false
    if (search.value.trim() && !(q.stem_latex || '').includes(search.value.trim())) return false
    return true
  }),
)

const diffLabel = (d: string) => ({ easy: '容易', medium: '中等', hard: '较难' } as Record<string, string>)[d] || (d ? d : '未定级')
const typeLabel = (t: string) => ({ choice: '选择', fill: '填空', solve: '解答', image: '图片' } as Record<string, string>)[t] || t

function stemOf(q: V3QuizQuestion): string {
  // 图片题也允许带转写文字（拍照后人工补录/OCR）：有 stem_latex 就走 KaTeX 渲染
  if (q.q_type === 'image' && !(q.stem_latex || '').trim()) return escapeHtmlRaw('')
  return renderStem(q.stem_latex)
}
function escapeHtmlRaw(t: string): string {
  return t.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

async function loadQuestions() {
  try {
    // 2026-10-05 修复「全部知识点 20/20」：后端默认 limit=20，前端此前不传分页参数
    // 还把 questions.length(20) 当总数 → 795 题只显示前 20 条。现在分页拉全量
    // （页面筛选是纯前端过滤，需要完整列表），并用后端 total 做权威计数。
    const PAGE = 100
    const acc: V3QuizQuestion[] = []
    let total = 0
    for (let offset = 0; offset < 5000; offset += PAGE) {
      const r = await v3Api.catalog.quizQuestions({ limit: PAGE, offset } as Record<string, unknown>)
      total = Number(r.data.total ?? r.data.items.length)
      acc.push(...r.data.items)
      if (acc.length >= total || r.data.items.length < PAGE) break
    }
    questions.value = acc
    totalCount.value = total
    resolveAllQuizImages()
  } catch { /* mock */ }
}
async function loadFolders() {
  try {
    const r = await v3Api.catalog.folders.list()
    folders.value = r.data.items
  } catch { /* mock */ }
}

onMounted(async () => {
  try {
    await Promise.all([loadQuestions(), loadFolders()])
    updateTv3Context({ route: '/teacher-v3/bank', topic: '题库', extra: '题库列表 · 绘图可插入为题图' })
    try {
      const tr = await v3Api.catalog.quizKpTree()
      tree.value = tr.data.tree
      if (kpLeafOptions.value.length) { scanKpCode.value = kpLeafOptions.value[0].code; createForm.value.kp_code = kpLeafOptions.value[0].code }
    } catch { /* mock */ }
  } finally {
    loading.value = false
  }
})

/* ---------- C2 伴随工具层：绘图台插入 → 题图原样入库（与拍照入库同一通道，可撤销） ---------- */
let companionSeqDone = ''
let lastInsertedId = ''
async function onCompanionInsert(ev: Event) {
  const d = (ev as CustomEvent).detail as { reqId: string; kind: string; draw?: { type: string; src?: string } } | undefined
  if (!d || d.reqId === companionSeqDone) return
  if (d.kind === 'figure' && d.draw?.type === 'image' && d.draw.src) {
    try {
      const r = await v3Api.catalog.quizScanImport({ src: d.draw.src, as_image: true, kp_code: 'DRAW-01', kp_name: '绘图插入' })
      const q = r.data as V3QuizQuestion
      questions.value.unshift(q)
      companionSeqDone = d.reqId
      lastInsertedId = q.id
      setReceipt({ ok: true, message: `已把绘图落为题图入库（${q.kp_name} · 图片题，原样保留不强转文字）`, locationLabel: '题库列表顶部', undoLabel: '撤销' })
      registerUndo('撤销', async () => {
        questions.value = questions.value.filter((x) => x.id !== q.id)
        try { await v3Api.catalog.quizRemove(q.id) } catch { /* mock */ }
      })
      window.dispatchEvent(new CustomEvent('tv3-companion-inserted', { detail: { reqId: d.reqId, handled: true } }))
    } catch {
      window.dispatchEvent(new CustomEvent('tv3-companion-inserted', { detail: { reqId: d.reqId, handled: false } }))
    }
    return
  }
  window.dispatchEvent(new CustomEvent('tv3-companion-inserted', { detail: { reqId: d.reqId, handled: false } }))
}
function onCompanionLocateBank() {
  if (!lastInsertedId) return
  Promise.resolve().then(() => {
    const rows = document.querySelectorAll('.tv3-qrow')
    rows[0]?.scrollIntoView({ block: 'center', behavior: 'smooth' })
  })
}
onMounted(() => {
  window.addEventListener('tv3-companion-insert', onCompanionInsert as EventListener)
  window.addEventListener('tv3-companion-locate', onCompanionLocateBank as EventListener)
})
onBeforeUnmount(() => {
  window.removeEventListener('tv3-companion-insert', onCompanionInsert as EventListener)
  window.removeEventListener('tv3-companion-locate', onCompanionLocateBank as EventListener)
})

function collectCodes(nodes: V3KpTreeNode[]): string[] {
  const codes: string[] = []
  for (const n of nodes) {
    if (n.children && n.children.length) codes.push(...collectCodes(n.children))
    else codes.push(kpCodeOf(n))
  }
  return [...new Set(codes)]
}
function kpCodeOf(n: V3KpTreeNode): string {
  return (n as { kp_codes?: string[] }).kp_codes?.[0] ?? n.id.replace(/^kp-/, '')?.toUpperCase() ?? n.id
}
function cntOf(n: TreeItem): number {
  // 2026-10-15：优先用服务器聚合题量（自底向上、与组卷同口径）；mock 树无 count 时回落前端计数
  if (Number.isFinite(n.count) && n.count > 0) return n.count
  if (!n.codes) return questions.value.length
  return questions.value.filter((q) => n.codes!.includes(q.kp_code)).length
}
function selectKp(n: TreeItem) { selectedKp.value = n; selectedFolder.value = '' }
function toggleFolder(id: string) {
  selectedFolder.value = selectedFolder.value === id ? '' : id
  if (selectedFolder.value) selectedKp.value = { id: '', name: '', depth: 0, leaf: false, codes: null, count: 0 }
}
function toggleDetail(id: string) { detailOpen.value = detailOpen.value === id ? '' : id }

/* ---- 专题夹管理 ---- */
async function createFolder() {
  if (!newFolder.value.name.trim()) return
  try {
    await v3Api.catalog.folders.create({ name: newFolder.value.name, desc: newFolder.value.desc })
    await loadFolders()
    folderCreateOpen.value = false
    newFolder.value = { name: '', desc: '' }
  } catch { /* mock */ }
}
async function removeFolder(id: string) {
  if (!window.confirm('删除该专题夹？题目仍保留在分类树中，不受影响。')) return
  try {
    await v3Api.catalog.folders.remove(id)
    if (selectedFolder.value === id) selectedFolder.value = ''
    await loadFolders()
  } catch { /* mock */ }
}
async function addToFolder(qid: string, e: Event) {
  const fid = (e.target as HTMLSelectElement).value
  if (!fid) return
  try {
    await v3Api.catalog.folders.addQuestions(fid, { question_ids: [qid] })
    await Promise.all([loadQuestions(), loadFolders()])
  } catch { /* mock */ }
}
async function removeFromFolder(qid: string) {
  if (!selectedFolder.value) return
  try {
    await v3Api.catalog.folders.removeQuestion(selectedFolder.value, qid)
    await Promise.all([loadQuestions(), loadFolders()])
  } catch { /* mock */ }
}
async function deleteQuestion(q: V3QuizQuestion) {
  const brief = (q.stem_latex || '').replace(/\\/g, '').slice(0, 18)
  if (!window.confirm(`删除这道题？\n「${brief || '图片题'}…」\n将从题库及所有专题夹中移除，请确认。`)) return
  try {
    await v3Api.catalog.quizRemove(q.id)
    questions.value = questions.value.filter((x) => x.id !== q.id)
    if (detailOpen.value === q.id) detailOpen.value = ''
    if (selectedFolder.value) await loadFolders()
  } catch { /* mock */ }
}

/* ---- 拍照入库（与组卷中心同款；V3.2 双图分开 + AI 识别归属） ---- */
/* M2-C 预签名直传（IFC-004）：FileReader dataURL 仅作本地预览；后端收对象 key，
 * 原图 File 经 XHR PUT 原样直传 MinIO（红线 3：前端零压缩零改写） */
function onFilePick(e: Event) {
  const f = (e.target as HTMLInputElement).files?.[0]
  if (f) readFile(f)
}
function onSolPick(e: Event) {
  const f = (e.target as HTMLInputElement).files?.[0]
  if (!f) return
  const r = new FileReader()
  r.onload = () => { scanSolSrc.value = String(r.result || '') }
  r.readAsDataURL(f)
  uploadFile(f).then((h) => { scanSolKey.value = h.key }).catch(() => { dragHint.value = '解答图上传失败，请重选' })
}
function onFileDrop(e: DragEvent) {
  const f = e.dataTransfer?.files?.[0]
  if (f) readFile(f)
}
function readFile(f: File) {
  const r = new FileReader()
  r.onload = () => { scanSrc.value = String(r.result || '') }
  r.readAsDataURL(f)
  dragHint.value = '上传中…'
  // 2026-10-05 改走服务端中转上传（浏览器 → 8100 → MinIO）：线上 teacher-minio 只在内网，
  // presign 直传浏览器根本 PUT 不到；且直传失败曾回落成 base64 dataURL 塞进 stem_image，
  // 留下一批裂图垃圾行（用户看到的「题库图片显示不出来」）。上传失败必须阻断入库。
  uploadFile(f, { onProgress: (p) => { dragHint.value = `上传中 ${Math.round(p * 100)}%` } })
    .then((h) => { scanKey.value = h.key; dragHint.value = '原图已上传' })
    .catch(() => { scanKey.value = ''; dragHint.value = '上传失败，请重选图片' })
}
const _SAMPLE_SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="180" height="80"><rect x="2" y="2" width="176" height="76" fill="#f8fafc" stroke="#4f46e5"/><text x="10" y="24" font-size="13" fill="#4f46e5">过椭圆 x²/4+y²/3=1 右焦点 F 作倾斜角 60° 的直线 l，</text><text x="10" y="46" font-size="13" fill="#4f46e5">交椭圆于 A、B，求 |AB|。</text><polygon points="120,60 150,60 160,72 130,72" fill="#e0e7ff" stroke="#c97"/></svg>'
function useSample() {
  scanSrc.value = 'data:image/svg+xml;utf8,' + _SAMPLE_SVG
  dragHint.value = '已载入示例原图'
  // 迭代20 门禁修复（真死路）：2026-10-05 起入库必须有对象存储 key（:652 门禁），
  // 但示例图只填了本地 dataURI、从不上传 → 「用示例题图」后确认入库**永远禁用**。
  // 示例图转 File 走同一条中转上传链拿 key；失败如实提示，不静默。
  uploadFile(new File([_SAMPLE_SVG], 'sample-question.svg', { type: 'image/svg+xml' }))
    .then((h) => { scanKey.value = h.key; dragHint.value = '原图已上传' })
    .catch(() => { dragHint.value = '示例图上传失败，请重新点一次「用示例题图」' })
}
async function doScanImport() {
  if (!scanSrc.value) return
  // 2026-10-05：必须拿到对象存储 key 才能入库 —— 此前直传失败会回落成 base64 dataURL
  // 塞进 stem_image，产生一批裂图垃圾行（教师感知「题库图片显示不出来」）。
  if (!scanKey.value) {
    dragHint.value = '原图尚未上传成功，请重新选择/拍摄图片'
    return
  }
  const opt = kpLeafOptions.value.find((o) => o.code === scanKpCode.value)
  try {
    await v3Api.catalog.quizScanImport({
      src: scanKey.value,
      kp_code: scanKpCode.value,
      kp_name: opt?.path.split(' ▸ ').pop() || '拍照入库',
      kp_path: opt?.path.split(' ▸ ') || [],
      as_image: mode.value === 'image',
      solution_src: scanSolKey.value || undefined,
    })
    await loadQuestions()
    scanOpen.value = false
    scanSrc.value = ''
    scanSolSrc.value = ''
    scanKey.value = ''
    scanSolKey.value = ''
    setReceipt({ ok: true, message: '拍照题已入库，可在列表顶部查看', locationLabel: '题库列表' })
  } catch (e: unknown) {
    // 此前是 `catch { /* mock */ }` 静默吞掉一切失败（401/422/500/断网），教师感知「点了没反应」
    const msg = e instanceof Error ? e.message : '入库失败，请重试'
    dragHint.value = msg
    setReceipt({ ok: false, message: `拍照入库失败：${msg}`, locationLabel: '题库列表' })
  }
}
/* V3.2：AI 识别知识点 → 建议 + 备选，点选填入 */
async function runSuggest() {
  suggestLoading.value = true
  suggest.value = null
  try {
    const r = await v3Api.catalog.kpSuggest({ src: scanSrc.value || scanSolSrc.value || '' })
    suggest.value = r.data
  } catch { suggest.value = null } finally { suggestLoading.value = false }
}
function applySuggest(code: string, path: string[]) {
  scanKpCode.value = code
  void path
}
/* V3.2：分类树编辑（个人定制层） */
function parentOf(id: string): string {
  const find = (nodes: V3KpTreeNode[], parent: string | null): string | null => {
    for (const n of nodes) {
      if (n.id === id) return parent ?? ''
      const hit = n.children?.length ? find(n.children, n.id) : null
      if (hit !== null) return hit
    }
    return null
  }
  return find(tree.value, null) ?? ''
}
function kpAddOpen(parentId: string) {
  kpAddParent.value = parentId
  kpRenameId.value = ''
  kpEditValue.value = ''
}
async function kpAddConfirm() {
  const name = kpEditValue.value.trim()
  if (!name || kpAddParent.value === null) return
  try {
    await v3Api.catalog.kpTreeAdd({ parent_id: kpAddParent.value || null, name })
    await reloadTree()
    kpAddParent.value = null
    kpEditValue.value = ''
  } catch { /* mock */ }
}
function kpRenameOpen(n: TreeItem) {
  kpRenameId.value = n.id
  kpAddParent.value = null
  kpEditValue.value = n.name
}
async function kpRenameConfirm() {
  const name = kpEditValue.value.trim()
  if (!name || !kpRenameId.value) return
  try {
    await v3Api.catalog.kpTreeRename(kpRenameId.value, name)
    await reloadTree()
    kpRenameId.value = ''
  } catch { /* mock */ }
}
async function kpDelete(n: TreeItem) {
  if (!window.confirm(`删除「${n.name}」？其下子节点一并删除，题目保留（归属变为未分类）。`)) return
  try {
    await v3Api.catalog.kpTreeRemove(n.id)
    if (selectedKp.value.id === n.id) selectedKp.value = { id: '', name: '', depth: 0, leaf: false, codes: null, count: 0 }
    await Promise.all([reloadTree(), loadQuestions()])
  } catch { /* mock */ }
}
async function reloadTree() {
  try {
    const tr = await v3Api.catalog.quizKpTree()
    tree.value = tr.data.tree
  } catch { /* mock */ }
}

/* ---- 自编录入 ---- */
function openCreate() {
  createForm.value = {
    kp_code: createForm.value.kp_code || kpLeafOptions.value[0]?.code || '',
    kp_name: '', kp_path: [], q_type: 'solve', difficulty: 'medium', year: '', stem_latex: '', answer: '', analysis: '',
    stem_image: '', stem_image_name: '',
  }
  createImgHint.value = ''
  createOpen.value = true
}
async function doCreate() {
  const opt = kpLeafOptions.value.find((o) => o.code === createForm.value.kp_code)
  try {
    const r = await v3Api.catalog.quizCreate({
      kp_code: createForm.value.kp_code,
      kp_name: opt?.path.split(' ▸ ').pop() || '自编',
      kp_path: opt?.path.split(' ▸ ') || [],
      q_type: createForm.value.q_type,
      difficulty: createForm.value.difficulty,
      stem_latex: createForm.value.stem_latex,
      stem_image: createForm.value.stem_image || undefined,
      answer: createForm.value.answer,
      analysis: createForm.value.analysis,
    })
    questions.value.unshift(r.data)
    void resolveQuizImage(r.data.id, 'stem', r.data.stem_image)
    void resolveQuizImage(r.data.id, 'solution', r.data.solution_image)
    createOpen.value = false
    setReceipt({ ok: true, message: `已录入题库（${r.data.kp_name || '自编'}）`, locationLabel: '题库列表顶部' })
  } catch (e: unknown) {
    // 此前是 `catch { /* mock */ }` 静默吞错（422/401/断网都不提示），教师感知「录入不可用」
    const msg = e instanceof Error ? e.message : '录入失败，请重试'
    setReceipt({ ok: false, message: `自编录入失败：${msg}`, locationLabel: '题库列表' })
  }
}
</script>

<style scoped>
.tv3-page-shell {
  margin: -22px; padding: 22px; min-height: calc(100vh - var(--tv3-topbar-h));
  background: var(--teacher-bg-gradient);
  box-sizing: border-box;
}
.tv3-treescroll { max-height: 430px; overflow-y: auto; display: flex; flex-direction: column; gap: 2px; }
.tv3-qscroll { display: flex; flex-direction: column; gap: 8px; max-height: 620px; overflow-y: auto; }
.tv3-empty { text-align: center; color: var(--tv3-ink4); padding: 60px 0; font-size: 13px; }
.tv3-tree-row {
  display: flex; align-items: center; gap: 6px; width: 100%; text-align: left;
  padding: 6px 8px; border-radius: 8px; border: none; background: transparent; cursor: pointer;
  font-size: 12.5px; color: var(--tv3-ink2);
}
.tv3-tree-row.lv1 { font-weight: 700; color: var(--tv3-ink); }
.tv3-tree-row.lv2 { padding-left: 20px; }
.tv3-tree-row.lv3 { padding-left: 34px; }
.tv3-tree-row.is-sel { background: var(--tv3-gold-soft); color: var(--tv3-ink); box-shadow: inset 0 0 0 1px var(--tv3-gold); }
.tv3-tree-row:hover { background: var(--tv3-line-soft); }
.tv3-tree-row__folder { color: var(--tv3-gold); font-size: 11px; }
.tv3-tree-row__cnt { margin-left: auto; font-size: 11px; color: var(--tv3-ink4); background: var(--tv3-line-soft); border-radius: 9px; padding: 0 7px; }
.tv3-bank__folder-del {
  margin-left: 2px; width: 18px; height: 18px; border-radius: 50%; color: var(--tv3-ink4);
  display: inline-grid; place-items: center; font-size: 12px;
}
.tv3-bank__folder-del:hover { background: var(--tv3-rose-soft, #fde8e8); color: #b1382c; }
.tv3-qrow { display: flex; gap: 10px; padding: 10px; border: 1px solid var(--tv3-line); border-radius: 12px; }
.tv3-qrow--image { border-left: 3px solid var(--tv3-gold); }
.tv3-qrow__img { margin-top: 6px; border: 1px dashed var(--tv3-gold); border-radius: 8px; padding: 8px; background: #fff; }
.tv3-qrow__img img { max-width: 220px; border-radius: 6px; display: block; }
.tv3-qrow__img-cap { display: inline-block; margin-top: 4px; font-size: 11px; color: var(--tv3-ink4); }
.tv3-qrow__stem { font-size: 13.5px; margin-top: 6px; line-height: 1.6; }
.tv3-qrow__opts { display: flex; gap: 14px; flex-wrap: wrap; margin-top: 6px; font-size: 13px; }
.tv3-qrow__opt { padding: 2px 8px; border-radius: 6px; }
.tv3-qrow__opt.is-answer { background: var(--tv3-teal-soft); }
.tv3-bank__meta { margin-top: 6px; }
.tv3-bank__meta-toggle {
  border: none; background: none; cursor: pointer; font-size: 11.5px;
  color: var(--tv3-teal-deep, #0e9488); padding: 2px 0;
}
.tv3-bank__detail {
  margin-top: 6px; padding: 8px 12px; border-radius: 8px; background: var(--tv3-bg2);
  font-size: 12.5px; line-height: 1.7; border: 1px dashed var(--tv3-line);
}
.tv3-bank__folder-add { flex-shrink: 0; align-self: flex-start; }
.tv3-bank__folder-add select { width: 120px; font-size: 12px; }
/* V3.3：题目删除（题库侧删除按钮，右上角红色 ×，与画布删除一致） */
.tv3-bank__del {
  margin-left: 4px; width: 20px; height: 20px; border-radius: 6px; border: none; cursor: pointer;
  background: var(--tv3-rose, #e5484d); color: #fff; font-size: 13px; line-height: 1; flex-shrink: 0;
  display: inline-flex; align-items: center; justify-content: center; transition: transform .12s ease;
}
.tv3-bank__del:hover { transform: scale(1.1); }
.tv3-bank__stem-preview {
  margin-top: -4px; padding: 10px 12px; border-radius: 10px; background: var(--tv3-bg2);
  border: 1px dashed var(--tv3-line); font-size: 13px; line-height: 1.7; color: var(--tv3-ink);
}
.tv3-upload {
  flex: 1; min-height: 120px; border: 1.5px dashed var(--tv3-line); border-radius: 12px;
  display: grid; place-items: center; cursor: pointer; color: var(--tv3-ink4); font-size: 12.5px;
  background: var(--tv3-line-soft); padding: 8px; text-align: center;
}
.tv3-radio-row { display: flex; flex-direction: column; gap: 8px; }
.tv3-radio { display: flex; flex-direction: column; gap: 2px; font-size: 13px; color: var(--tv3-ink); cursor: pointer; }
.tv3-radio__hint { font-size: 11.5px; color: var(--tv3-ink4); padding-left: 20px; }
.tv3-input--block { width: 100%; }
/* V3.2：树编辑行 */
.tv3-tree-line { display: flex; align-items: center; gap: 2px; }
.tv3-tree-line .tv3-tree-row { flex: 1; min-width: 0; }
.tv3-tree-ops { display: none; gap: 2px; flex-shrink: 0; }
.tv3-tree-line:hover .tv3-tree-ops, .tv3-tree-manage .tv3-tree-ops { display: inline-flex; }
.tv3-tree-op {
  width: 20px; height: 20px; border-radius: 5px; border: 1px solid var(--tv3-line);
  background: #fff; color: var(--tv3-ink3); font-size: 11px; cursor: pointer; line-height: 1;
}
.tv3-tree-op:hover { color: var(--tv3-primary); border-color: var(--tv3-primary); }
.tv3-tree-edit { display: flex; gap: 4px; align-items: center; padding: 3px 8px; }
.tv3-upload__hint { font-size: 11px; color: var(--tv3-ink4); }
.tv3-modal { position: fixed; inset: 0; background: rgba(15, 31, 54, 0.45); display: grid; place-items: center; z-index: 200; }
.tv3-modal__box { background: #fff; border-radius: 16px; padding: 18px 22px; max-height: 88vh; overflow-y: auto; }
.tv3-modal__head { display: flex; align-items: baseline; gap: 10px; border-bottom: 1px solid var(--tv3-line); padding-bottom: 10px; margin-bottom: 14px; }
.tv3-modal__foot { display: flex; justify-content: flex-end; gap: 10px; margin-top: 4px; }
</style>
