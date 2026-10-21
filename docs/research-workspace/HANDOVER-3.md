# 科研端 Research Workspace · 第三会话交接书（HANDOVER-3）

> 撰写：2026-09-16 · 撰写人 = **第一会话主理人**（会话 `48162ace`）
> 覆盖：会话 #1（我）+ 会话 #2（`3fc0b79d`，额度耗尽中断）
>
> ## ⚠️ 本文的性质（务必先读）
>
> 会话 #2 的工作**我没有亲历**。本文关于会话 #2 的所有内容，是我从**盘上证据 + 团队收件箱原始报告**
> 反推重建的，**不是它的自述、也不是它的转述**。凡我未能亲自复验的，本文一律标注证据来源路径。
>
> **接手者必须自己复验，不得把本文当事实。** 第 6 章给出 V1~V14 独立复验清单，每条都有命令与预期。
> 先跑清单，再采信本文。**如果复验结果与本文冲突，以复验为准，并把冲突记录下来。**

---

## 0. 接手后先做三件事（顺序不可换）

1. **起服务**。会话 #2 的 supervisor 已随会话退出 → 现在**全栈 DOWN**（含 PG 54329）。见 §8.1。
2. **读本文 §6，跑 V1~V5**（结构性断言，5 分钟内可完成）。这决定你对"当前家底"的判断。
3. **再读 §7 剩余工作**，按性价比选一项动手。当前性价比最高的是 **P1-A（补跑验收门禁）**，不是写代码。

---

## 1. 当前硬状态（本文件撰写时实测）

### 1.1 服务

| 端口 | 服务 | 状态 |
|---|---|---|
| 54329 | PostgreSQL（原生，`D:\PostgreSQL\pgsql\bin`，data=`C:\Users\<user>\pgdata\data`） | **DOWN** |
| 6380 | Valkey | UP |
| 9002 | MinIO | UP |
| 8300 | research-vnext | **DOWN** |
| 8000 | services/api（HMAC 网关） | **DOWN** |
| 5176 | 前端 vite dev | **DOWN** |

> 注：会话内 `curl 127.0.0.1:8xxx` 若返回 **502 且 body 是 `openresty`/APISIX 报错页**，那**不是**你的服务返回的——是沙箱出网代理（`http_proxy=127.0.0.1:45711`）拦截。判定服务存活必须用 `netstat` 看 LISTENING，或 `curl --noproxy '*'`。

### 1.2 代码

| 仓库 | 变更 | 说明 |
|---|---|---|
| `D:\frontend` | 45 个变更文件 | 其中 `src/features/research-workspace/`（**62 文件**）整目录 untracked；`src/router/index.js` modified |
| `D:\math-arena` | 11 个变更文件 | research-vnext 后端：`workspace_routes.py` + `core/research_workspace/`（12 py）+ 迁移 `0017`/`0018`（untracked）；`app.py`/`deps.py`/`project_routes.py` modified |

**全部未提交。** 按仓库铁律（`AGENTS.md` §5）由人工原子提交，禁 `git add .`。前端仓另有约 39 个与本任务无关的预存 dirty 文件，提交时必须逐一甄别。

### 1.3 视觉差距（会话 #2 最后数值，证据 `compare/*-regions.txt`）

| 页 | 全图平均绝对差 | >40 差异像素占比 | 最差分区 |
|---|---|---|---|
| READ | **18.59 / 255**（阈值 <12 为"结构接近"） | 12.55% | `agent-header` 26.02；`agent-result-card` 20.84；`center-pdf` 20.64；`workspace-bar` 20.28；`left-panel` 20.21 |
| WRITE | **16.68 / 255** | 11.24% | `editor` 20.11；`right-agent` 16.90；`left-panel` 16.39 |

`rw_compare.py` 的分区判定阈值：<8 结构高度一致 / <16 接近 / <28 偏差明显 / **≥28 不达标**。
→ **按该工具口径，"不达标"分区为 0 个**；未达标的是**全图均值**（18.59 / 16.68 均 >12）。

### 1.4 后端测试基线

`cd D:/math-arena/services/research-vnext && .venv/Scripts/python.exe -m pytest tests -q`
→ **21 failed, 1696 passed, 23 skipped, 19 xfailed, 3 xpassed**（改前改后完全一致）
→ 21 个失败**全在 LLM / review / SSE 域**（缺凭据等），**是既有失败，非本轮引入**。判回归时以这组数字为基线。

---

## 2. 对会话 #1 交接书（`HANDOVER.md`）的勘误

`docs/research-workspace/HANDOVER.md`（11:43 落盘）**仍可读，但有 3 处必须更正**。以下由会话 #2 复验推翻，我复核后采信：

| # | HANDOVER.md 原文 | 实际 | 性质 |
|---|---|---|---|
| 1 | 「Shell 未接线 Agent 面板 / `AGENT-SLOT=0`」 | **错**。`ResearchWorkspaceShell.vue` 第 **120-137** 行已渲染共享单例 `ResearchAgentPanel`（第 160-161 行 import）。 | 过时断言 |
| 2 | 「`roleHome('researcher')` 未改」 | **错**。`router/index.js` 的 `researchHome()`（第 157 行起）已返回 `/research-workspace`。 | 过时断言 |
| 3 | （漏报） | **致命遗漏**：会话 #1 结束时 `ResearchWorkspaceShell.vue` 处于**半合并状态、Vite 编译 500**，整个科研端路由渲染失败并回落学生端外壳。 | 漏报 |

**教训**：会话 #1 的"未完成清单"里有些条目是**它自己没复核的推测**。本文 §7 的清单已尽量标注证据来源。

---

## 3. 目标与权威文档

### 3.1 目标（一句话）

把丑陋、不可用的「论文工作台」（右侧是 method 下拉 + top_k 输入 + 论文 ID 输入 + textarea 的表单面板）推倒，
重建成一个真正的 **AI-native Research Workspace**：**READ 阅读页** + **WRITE 写作页**（LaTeX IDE），
由两张 **1672×941 黄金截图**冻结视觉，要求**前端 1:1 还原**且**功能真实可用**。

### 3.2 权威文档（按优先级）

| 优先级 | 文件 | 作用 |
|---|---|---|
| 1（最高） | `docs/research-workspace/golden/READ-GOLDEN.png` / `WRITE-GOLDEN.png` | **视觉终极真相**。三者冲突时，**黄金图 > 其它一切** |
| 2 | `docs/research-workspace/RESEARCH_PRODUCT_CONTRACT.md`（46KB / 92 条） | 产品宪法。引用标注形如 §27 / §30 / §85 |
| 3 | `docs/research-workspace/VISUAL-SPEC.md`（19KB） | 像素级视觉标准：栏宽 / 字号 / token / 组件结构 |
| 3 | `docs/research-workspace/BACKEND-CONTRACT.md`（16KB） | 后端架构决策 + API 契约 |
| 4 | `docs/research-workspace/ACCEPTANCE-MATRIX.md` | A~F 区验收标准 |
| — | `deliverables/research-workspace/A01/FRONTEND-ARCH.md` | 前端文件树 / 布局 CSS / store 划分 |
| — | `docs/research-workspace/HANDOVER.md` | 会话 #1 交接（**须配合本文 §2 勘误读**） |

### 3.3 硬约束（违反即驳回）

- 科研端主蓝 **`#0A6CFE`**，独立 token 前缀 **`--rw-*`**；**禁止**复用 `--ma-*` / `--re-*`。
- 栏宽：READ **330 / 737 / 604**；WRITE **306 / 590 / 462 / 314**（实测已命中）。
- **网关铁律**：科研端请求必须经 **8000**（注入 HMAC 后才到 8300）。`vite.config.js` 必须把 `/api/research-vnext` 代理到 **8000，不是 8300**——否则恒 401。
- 前端新代码**只准**进 `src/features/research-workspace/`。
- Agent 面板：**单一实例、由 Shell 渲染一次**（宪法 §59/§74 同语义单一组件）；**禁止参数表单**（§30：Agent 内 input 数必须为 0）。
- embedding 维度：8000 = 1024；8100/8300 = **768**；禁止交叉写索引。
- 科研端主蓝与 `--rw-*` token **不得**被通用 UI 组件反向依赖。

---

## 4. 会话 #1（我）做了什么

已被 §2 更正的部分不重复。有效的部分：

- **修通架构断点**：`vite.config.js` 曾把 `/api/research-vnext` 直连 8300 绕过 8000 网关 → 恒 401，前端只能塞"开发签名桥"。修复后有前后对比：改前静默返回空 `{"items":[]}`（开发者固定身份世界，真实项目全不可见），改后返回真实项目列表。**并发现全库 396 用户中 researcher = 0**——科研端从未有过真实用户。
- **后端 P0-1~P0-7 全部完成**（项目级文献/笔记/文件树/项目级真实编译/结构化日志/Agent 统一任务/概览/跨用户隔离 404）。两个真缺陷：中文检索（CJK bigram）、`500→422` 收敛。
- **准则与工具落盘**：产品宪法 + 黄金图入库；`VISUAL-SPEC` / `BACKEND-CONTRACT` / `ACCEPTANCE-MATRIX`。
- **前端 T01+T02**：token / 壳 / API 层 / 6 个 store。
- **我的错误（模式值得警醒）**：把目测的 WRITE 栏宽（355/435/428/454）写进了验收标准——**实测是 306/590/462/314**，是工程师质疑后我用两种独立测量才确认**我对它错**；共享文件未声明"某一时刻唯一编辑者"导致两路互相覆盖。→ 见 §9。

---

## 5. 会话 #2（`3fc0b79d`）做了什么 —— 证据重建版

### 5.1 会话 #2 的身份坐标（供你复活或读原始记录）

| 项 | 路径 |
|---|---|
| 主 transcript（4.9MB） | `C:\Users\<user>\.workbuddy-ai\projects\d-math-arena\3fc0b79d-e5da-4198-af38-6074c214d405.jsonl` |
| 子代理 transcript（6 个） | 同目录 `3fc0b79d-.../subagents/*.jsonl` |
| 团队目录 | `C:\Users\<user>\.workbuddy-ai\teams\_auto_3fc0b79d-e5da-4198-af38-6074c214d405\` |
| ↳ 成员收件箱（**最有价值**） | 同目录 `inboxes/team-lead.json`（25KB，含 4 名工程师完整报告）+ `software-engineer-{1..4}.json` |
| ↳ 团队配置/运行时 | `config.json`（38KB）/ `runtime.json`（39KB） |
| 任务清单 | `C:\Users\<user>\.workbuddy-ai\tasks\_auto_3fc0b79d-.../{1..5}.json`（**5/5 completed**） |
| 会话 #2 自己的记忆日志 | `D:\math-arena\.workbuddy-ai\memory\2026-09-16.md`（**只写到 W1/W1b 就中断了，Wave 2 未记录**） |

> **重要发现（两个记忆域）**：本机存在 `C:\Users\<user>\.workbuddy\`（会话 #1/#3 域）与 `C:\Users\<user>\.workbuddy-ai\`（会话 #2 与 9/11~9/15 历史会话域）。
> 后者还有 `D:\math-arena\.workbuddy-ai\memory\`，内含 **9/11~9/15 的项目日志 + `MEMORY.md`(10.9KB) + `ENV-NOTES.md`(6KB)**。
> **接手者应两个域都读**，尤其 `ENV-NOTES.md`（但注意其中部分已过时，见 §10）。

### 5.2 会话 #2 的时间线与交付

| 时间 | 事项 | 产物/证据 |
|---|---|---|
| 12:0x | 接手；发现**科研端整页 500**（一行 `function createFirstProject()` 缺 `async` → `vue/compiler-sfc` 直接 500 → 全页回落学生端外壳） | §5.3 W1 |
| 12:0x-12:4x | **W1** 解除 500 阻断（3 处）；**W1b** 图标尺寸系统性缺陷 | §5.3 |
| 12:50 | 建团队 `_auto_3fc0b79d`（4 名工程师） | team config |
| 12:5x-13:47 | **Wave 2 · 四路并行** | §5.4 |
| 13:44-13:48 | 主理人视觉终审 + D3「点能力是否直接执行」验证 | `shots/lead-r3-read.png` / `lead-r3-write.png` / `probe/lead-r3-click1.json` |
| ~13:48+ | **额度中断** | — |

### 5.3 W1 / W1b 详情

**W1 · 解除 500 阻断**（`shell/ResearchWorkspaceShell.vue`，3 处）
1. `createFirstProject()` → `async function`（函数体含 `await`）
2. 补 `Locator` 类型 import
3. 删除模板里悬空的 `<input ref="pdfInput" @change="onPdfChosen">`（`read/ReadWorkspace.vue` 已自带，Shell 那份是死代码）

**W1b · 图标尺寸系统性崩溃**（**整页崩塌的真正根因**）
- 根因：`@vicons/ionicons5` 渲染的 `<svg>` 只有 `viewBox="0 0 512 512"`、**不带 width/height**。只写 `font-size` 的图标全部退化成默认替换元素尺寸。实测 READ 有 7 个巨型 SVG（accordion chevron **549×549px**），WRITE 有 10 个（**259×259px**）→ 能力卡收起态高 567px / 277px（规范 46px）。
- 修法（**关键经验**）：`tokens/research-tokens.css` 第 117-119 行
  ```css
  @layer rw-icon-fallback {
    [data-research-app] svg { width: 1em; height: 1em; }
  }
  ```
  **必须放进 `@layer`**：`[data-research-app] svg` 优先级 (0,1,1) 高于组件类 `.rw-nav__ico` (0,1,0)，不放 layer 会**反向覆盖掉本来正确的尺寸**；`@layer` 内优先级恒低于未分层样式，正好实现"只兜底、不覆盖"。

### 5.4 Wave 2 · 四路并行（4/4 completed）

**工程师 1 · 后端 8300 语义与能力可用性**（19m58s）
- 改 2 文件：`research_workspace/overview.py`、`research_workspace/agent.py`
- **缺陷 1 修复**：`overview.summary` 不再吐 CJK bigram 残渣。「有限元误差估计」原被切成 `有限/限元/元误/误差/差估`（违 §85）；改为按功能词边界切完整词。线上实测（重启后）：`主题涉及 finite、element、error、problems、priori`（英文标题故为拉丁词，合理）。
- **缺陷 2 修复**：8 个 preset 的面向用户文案全面学术化，清除 `lhs/rhs/points/checks/BibTeX/unverified/LLM/en/success` 等工程词。例：`derivation_check` 缺条件文案 → 「当前上下文还缺少可校验的等式或假设。请在论文中选中要校验的推导步骤，或在输入框里写出待验证的等式。」；`finding.label` → 「缺少可校验的条件」。**contract 无破坏性变更**（schema / status 词表不变）。
- **缺陷 3 核查**：动作端点全部有落点，`locate` / `view_evidence` 是**纯前端动作**（用 `source_locations` / `evidence[]`），无需后端端点。未新造端点。
- **能力边界（重要，别误当缺陷）**：`derivation_check` / `formula_check` **仅当 context 带结构化规格**（`checks[]` 或 `lhs/rhs/points`）才真实执行；实测传 `lhs=a*b+b,rhs=b*(a+1),points=[{a:2,b:3}]` → `status=verified`。**前端当前只传 `statement`/`selection`（选中公式文本），无结构化规格 → 对任意选中公式只能返回 `needs_conditions`**（不伪造结论，§90）。选中公式已放 `findings[].math` 供 KaTeX。→ **这是个真实产品缺口**，见 §7 P0-2。
- 测试：改前改后完全一致（21 failed / 1696 passed），**零回归**。未新增迁移（head 仍 `0018_rv18_search_tokens`）。
- 证据：`D:\math-arena\tmp\preset_before.json` / `preset_after.json` / `preset_after-live.json`

**工程师 2 · Agent 面板结果卡与真实能力**（41m53s）
- 改 `agent/`(8) + `ui/`(7)，无越界。
- 要点：能力卡头部**按黄金图合并为状态行**（展开且有结果时 = 20px 实心圆状态图标 + 能力名 + 状态药丸）；逐卡色调对齐黄金图（推导校验绿 / 引用补全·LaTeX对照蓝 / 翻译润色紫 / 改进建议黄 / LaTeX错误解释红）；补 `LockClosedOutline` import（修「当前上下文」图标 0×0 消失）；默认展开第一张卡；**点击能力 = 直接执行（无参数表单）**；`container:rw-agent/inline-size` 双宽度自适应（604 / 314）；结果卡根 class = **`.rw-result`**（另含 `data-rw-result`）。
- 探针证据（`--click=.rw-cap__head:0`）：READ `consoleErrors=0 failedRequests=0 BIG-SVG=0 hasResultCard=true inputsInsideAgent=0`，展开卡 h=264.6，其余 h=48，RATIO 19.7/44.1/36.1；WRITE `.rw-pane--agent w=314`，展开卡 h=170.1。
- 截图：`deliverables/research-workspace/probe/eng-w2a-read-open.png` / `eng-w2a-write-open.png`
- **待你批准**：`--rw-purple*` / `--rw-teal*` token。当前用 `color-mix()` 从既有 token 派生（`agent/CapabilityAccordion.vue:162-170`），**未加新 token**。

**工程师 3 · READ 页视觉与功能链**（50m06s）
- 改 `read/` 3 文件：
  1. `PdfToolbar.vue` — 去掉 `Aa`（`TextOutline` 实测渲染成字母「Aa」），按黄金图语义重排图标；ionicons 缺的光标/页轮廓用内联 SVG 补；按钮 28×28 / 图标 16px / 条 h40。**并修新缺陷**：搜索展开时命中数被裁切 → 搜索改独立一行。
  2. `usePdfDocument.ts` — **修真 bug**：后端 `/text` 是**左上原点 y 向下**，前端原按左下原点换算 → 定位/高亮**纵向翻转**。统一三个换算函数到后端同空间。
  3. `ReadWorkspace.vue` — 「问 AI」把完整 locator（`paper_id/page/section/selected_text/bbox`）随请求下发。
- **导入 5 篇真实 arXiv 论文**（真实 API、真实元数据，非伪造）：Cai&Zhang 2015 **15 页** `arXiv:1509.08166`（当前打开）/ Ghesmati 2018 24 页 `1805.02693` / Giani 2011 18 页 `1112.0436` / Steinbach 2014 28 页 `1409.0563` + 既有示例。另建 4 条带 locator 真实笔记。
- **B3~B10 全通过**，证据 `deliverables/research-workspace/shots/w2b/`：
  B3 真实 canvas 736×952 15 页｜B4 页码 2→3、缩放 120%→173%→96%｜B5 搜「error」命中 **1/37**｜B6 选区浮条（高亮/添加笔记/引用/问 AI）、Esc 关闭｜B7 「问 AI」请求体带完整 locator（`page:4, section:"2.1 Sobolev space of fractional order", bbox:[116.9,96.9,309.9,112.5]`），用户未复制原文｜B8 「定位原文」阅读器 **9→1 页**｜B9 toast「已添加笔记」→ 点笔记回跳 page 2 且高亮可见｜B10 切 WRITE 后 URL 变更、同项目、**Agent 上下文保留**（§2.1 + 相关论文·5 篇）。
- 机器证据：`probe/eng-w2b-read.json`；黄金对比 `compare/read-regions.txt`
- **黄金图一处判读分歧（主理人已裁定）**：工具条首个控件**确有**「T 圆角框」（非 `Aa` 字母）→ 保留该按钮但换图标。**裁定：工程师对，会话 #2 主理人早前指令有误。**

**工程师 4 · WRITE 页视觉与编译链**（56m35s）
- 改 `write/` 4 文件：`PdfPreviewPane.vue` / `CompileLog.vue` / `FileTree.vue` / `WriteSidebar.vue`
- **预览白屏根因（P1）**：`loadDoc()` 在 `try` 内调 `renderPage()`，此时 `loading.value===true`，而 `<canvas>` 只在 `v-else` 分支挂载 → `canvasRef.value===null` → 提前 return → canvas 停在默认 300×150 空白。修法：渲染移出 `try`，`finally` 中关 loading + `await nextTick()` 后再渲染，`watch(canvasRef)` 兜底。
- **附带修 CJK 丢失**：pdfjs 对 `/Identity-H` CIDFontType0C 无 ToUnicode → 需 `cMapUrl:'/pdfjs-cmaps/'` + `cMapPacked:true`（实测 `getTextContent` 124→358 字符，中文完整）。
- **真实多文件项目（P2）**：真实 API 增量创建 11 文件 + 重写 `main.tex` 真 `\input{}` → 共 **14 文件**（`sections/*5`、`tables/conv_rate.tex`、`appendix.tex`、`notation.tex`、`style.cls`、`figures/{mesh.png,error_plot.pdf}`）。编译仍通过：**3 页 ~111KB ~16-19s**。大纲由真实 LaTeX 结构生成（11 项，含层级与点击定位）。
- **C1~C12 结论**，证据 `deliverables/research-workspace/shots/w2c/`：C1 文件树 ✅｜C2 编辑器 ✅｜C3 保存三态 ✅｜C4 大纲导航 ✅｜C5 编译成功 ✅｜**C6 预览 ✅（全中文 PDF，非白屏）**｜C7 编译错误 ✅（`main.tex:47 Undefined control sequence.`，**错误定位生效**）｜C8 失败不清空预览 ✅（nonWhite=2483 与成功帧一致）｜C10 修复后重编译 ✅｜**C9/C11/C12 → 属 `agent/` 渲染，只报不改**。
- 机器证据：`probe/eng-w2c-write.json` → `PANELS left=306 editor=590 preview=462 agent=314`（与黄金一致）、`BIG-SVG=0`、`consoleErrors=0`；`compare/write-regions.txt` 全图 16.68。
- **WRITE 侧 IS_PASS: YES**
- **待你批准 (a)**：`D:\frontend\public\pdfjs-cmaps\` —— **168 个 `.bcmap`，1.4MB**，从 `pdfjs-dist@4.10.38/cmaps` 拷贝。**已存在于盘上**（我实测确认）。位于 `write/` 之外（部署静态资源），是 CJK 预览的必要条件。**归属未定，需你裁决。**
- 待你知悉 (b)：demo 项目 `e9b7c1e7…` DB 增量新增 14 文件（非破坏）。

### 5.5 会话 #2 主理人自己做的事

1. **arXiv 真实论文可行性实测**（curl 200 / 316KB / `%PDF-` 头）→ 把条件同步给 READ 工程师。
2. **`rw_auth.mjs` 共享登录态**（解决短信 60s 限流争抢）。根因是三个叠加坑：① 前端 access token 存**内存变量**（`src/api/authSession.js` 的 `let accessToken`），**不进 localStorage** → Playwright 只共享 cookie，任何"用 cookie 校验登录态"恒 401；② access token **TTL 约 2 分钟**；③ **`ma_refresh` 是单次轮换刷新令牌**，不回写新 cookie → 下次冷启动 401 → 重定向 `/login` → 又抢验证码。修法：落盘 `storageState` + 文件锁串行登录 + **每次运行结束回写轮换后 cookie**；缓存校验改为"真实加载 `/research-workspace` 并确认科研端外壳渲染"（`rw_auth.mjs:40-49` 的 `verify()`）。
3. **定位「PDF 预览空白」的第二个根因**（与工程师 4 的根因**并存叠加**）：`stores/files.ts:77` 读 `compile_run.run_id`，而后端返回的字段名是 **`compile_run.id`** → `previewRunId` 恒 `undefined`。已在 `api/compile.ts` 做归一化映射（`id → run_id`，顶层 `last_successful_pdf_artifact_id` 收进 run 对象）。
4. **建客观验收仪器**（见 §8.3）：`rw_probe.mjs` / `rw_shot.mjs` / `rw_compare.py` / `rw_accept.py` / `rw_stack.py`。
5. **服务窗口期广播纪律**（"502 不是缺陷，等 3 分钟"）。

### 5.6 会话 #2 遗留：**4 项待批 / 12 项发现未修**（原样转述，未裁决 → 这是你的第一动作）

**待批准（4 项）**

| # | 提出人 | 事项 | 我的建议 |
|---|---|---|---|
| A | 工程师 2 | 新增 `--rw-purple*` / `--rw-teal*` token（当前 `color-mix()` 派生） | 建议**批准**：派生色不可审计、与"token 为唯一色彩源"冲突；但需进 `tokens/research-tokens.css` 单一源 |
| B | 工程师 4 | `D:\frontend\public\pdfjs-cmaps\`（168 `.bcmap` / 1.4MB）归属 | 建议**批准保留**，并在 A01 文档写明它是 CJK 预览的必要静态依赖 |
| C | 工程师 4 | demo 项目播种 14 文件（已执行） | 知悉即可，但**验收前需决定演示数据是"保留"还是"清理"** |
| D | 工程师 3（已裁定） | 工具条首个按钮保留 vs 删除 | **已裁定保留**（黄金图有该 T 框） |

**发现但未修（12 项）**

| # | 来源 | 问题 | 影响 |
|---|---|---|---|
| 1 | 工程师 2 | 后端 `latex_error` 编译成功时 `findings[0].label="编译成功"` → 前端渲染成**红「! 编译成功」** | 语义别扭（文案属后端） |
| 2 | 工程师 2 | 「查看证据」在 `evidence=[]` 时仍显示（对齐黄金图），点击为空操作 | 轻微 |
| 3 | 工程师 3 | 后端 `/notifications` **500**：`asyncpg InsufficientPrivilegeError: could not open file "base/16384/67214": Permission denied`（顶栏🔔） | **真缺陷**，非前端 |
| 4 | 工程师 3 | 章节启发式偶把整行正文当 section（Cai&Zhang p3 → `"methods. Analysis for…"`），属 `pdfs.py` | 中 |
| 5 | 工程师 3 | 「定位原文」若 locator 只有 `page`/`section` 无 `bbox` → 只跳页不高亮 | 中（需后端在 `evidence.locator` 带 bbox） |
| 6 | 工程师 3 | WRITE 顶栏无 `.rw-psel__name`（项目名只在左栏） | 一致性 |
| 7 | 工程师 4 | 探针唯一 banned 项：项目缩略图 `linear-gradient`（`rw-psel__thumb` / `rw-idc__thumb`）—— 黄金图应为**封面**而非纯蓝渐变 | **视觉缺口**，见 §7 |
| 8 | 工程师 4 | `figures/mesh.png` 用占位文本、`error_plot.pdf` 是最小合法 PDF（后端 `research_project_file.content` 仅 Text，**无法存二进制**） | 架构限制 |
| 9 | 工程师 4 | typecheck：`write/` 内零错误；既有错误在 `teacher-prep-v2` 与 `test/chatCardContract` | 非回归 |
| 10 | 工程师 1 | `execute_task` 失败时 `task.error_message` 仍存**原始异常文本**（§86 观测通道；前端 failed 态显示通用中文，不暴露给用户） | 低 |
| 11 | 工程师 1 | `derivation_check` 无结构化规格时**永远只能 `needs_conditions`**（选中即校验 = 不校验） | **产品缺口**，见 §7 P0-2 |
| 12 | 工程师 3 | 13:14 前后 5176/8000/8300 曾全部 502 约 1 分钟（实为会话 #2 重启窗口期） | 已澄清 |

### 5.7 会话 #2 结束时**正在做但没做完**的事

- 13:44/13:45 出 `lead-r3-read.png` / `lead-r3-write.png` 做视觉终审
- **13:48 出 `probe/lead-r3-click1.json`** —— 验证 D3「点能力直接执行」。**该证据显示已验证通过**：点击「引用补全」→ 卡片展开 h=259.4、`hasResultCard=true`、结果卡 549×199.4、内含真实候选引用 + 4 个动作按钮（定位原文 / 加入笔记 / 查看证据 / 插入引用）、`inputsInsideAgent=0`。
- **被掐断在"终审写结论"这一步之前** → 所以**没有留下任何结论文档**，也没有跑 `rw_accept.py`（`deliverables/research-workspace/accept/` **不存在**）。

---

## 6. 独立复验清单 V1~V14（**接手第一件事，别跳过**）

> 判定原则：**凡本文断言 与 你的复验 冲突，以你的复验为准**。每条都要留证据（写文件再读，别只信 stdout）。

### 6.1 结构性断言（不需要服务，5 分钟）

| # | 断言 | 复验命令（bash，前缀见 §8.0） | 预期 |
|---|---|---|---|
| V1 | 前端命名空间 **62 文件**，9 个子目录 | `find /d/frontend/src/features/research-workspace -type f \| wc -l` | 62 |
| V2 | Shell 渲染 **Agent 单例** + READ + WRITE | `grep -n "ResearchAgentPanel\|ReadWorkspace" /d/frontend/src/features/research-workspace/shell/ResearchWorkspaceShell.vue` | 第 120-137 行 agent pane；160-161 import |
| V3 | 图标兜底在 `@layer` 内 | `grep -n -A2 "rw-icon-fallback" .../tokens/research-tokens.css` | 第 117-119 行，`@layer rw-icon-fallback { [data-research-app] svg {...} }` |
| V4 | `researchHome()` 返回 `/research-workspace` | `grep -n "function researchHome" -A8 /d/frontend/src/router/index.js` | 第 157 行起，返回 `/research-workspace` |
| V5 | 后端模块齐备 + 迁移 0017/0018 | `ls /d/math-arena/services/research-vnext/research_vnext/core/research_workspace/`；`ls /d/math-arena/services/research-vnext/migrations/versions/001[78]*` | 12 个 `.py`；`0017_rv17_research_workspace.py`、`0018_rv18_search_tokens.py` |
| V6 | `pdfjs-cmaps` 静态资源在位 | `ls /d/frontend/public/pdfjs-cmaps/*.bcmap \| wc -l` | 168 |
| V7 | 代码**零提交** | `git -C /d/frontend status --porcelain \| wc -l`；`git -C /d/math-arena status --porcelain \| wc -l` | 45 / 11 |

### 6.2 需要服务的断言（先跑 §8.1 起栈）

| # | 断言 | 复验命令 | 预期 |
|---|---|---|---|
| V8 | 全栈链路健康 | `curl --noproxy '*' -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8300/healthz` 等（见 §8.2） | 8300/8000/5176 全 200 |
| V9 | 网关未鉴权 = 401（不是 200、不是 500） | `curl --noproxy '*' -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8000/api/research-vnext/projects` | 401 |
| V10 | READ 无巨型图标 + 栏宽 | `node D:/math-arena/tmp/rw_probe.mjs --route=/research-workspace/<pid>/read --out=v-read` | `bigSvgs=[]`；`panels` 330/738/604；`agent.inputsInsideAgent=0`；`agent.hasResultCard=true`；`consoleErrors=0` |
| V11 | WRITE 栏宽 306/590/462/314 | 同上 `--write` | 命中；`bigSvgs=[]` |
| V12 | Agent 面板**单实例**（切页不重复挂载） | 探针 `counts` 里 `.rw-agent` 应只有 1 个；或手动切 READ↔WRITE 看 DOM | 1 |
| V13 | 真实编译链路 | 走 UI 或 `POST /projects/{pid}/compile`，等 20-30s，再 `GET /projects/{pid}/compile-runs/{id}/pdf` | `compile_run.id` 有值；PDF `%PDF-` 头、非 0 字节 |
| V14 | 后端测试基线未恶化 | `.venv/Scripts/python.exe -m pytest tests -q` | 21 failed / 1696 passed（与 §1.4 一致） |

**另需你亲自判读的（不能机测）**：打开 `shots/lead-r3-read.png` 与 `shots/lead-r3-write.png`，对照 `docs/research-workspace/golden/*-GOLDEN.png`，逐分区回答"像不像"。这是全项目**唯一**不可让工程师代劳的事。

---

## 7. 剩余工作（按性价比排序）

### P1-A · 补跑验收门禁（**先做这个**）

`rw_accept.py` **从未跑出结果**（`deliverables/research-workspace/accept/` 不存在）。它把 `ACCEPTANCE-MATRIX` 里可机械判定的部分一次跑完（E8 链路 / A 客观视觉 / E consoleErrors / D Agent 无表单 / F1 vue-tsc / F2 vite build / F4 后端 pytest）。

```bash
C:/python13/python.exe D:/math-arena/tmp/rw_accept.py --round r1
```
**但先读它的代码**（`tmp/rw_accept.py`，9KB）：① 它硬编码了演示项目 ID `e9b7c1e7-31b4-46eb-99ce-987732b8ef95`；② 它的用户名校验用的是 `.rw` 状态缓存（`rw_auth.mjs`），起栈后第一次会触发真实登录（验证码由接口返回 `demo_code`，手机号 **13800138000**，60s 限流）；③ `F2 vite build` 很慢，先用 `--skip-build` 打通，再补全局。

**产出**：`accept/<round>/accept.md`（统一判定表）+ `accept.json`。**这份表就是你后续每轮的放行依据。**

### P0-1 · `document.title` 仍是学生端

实测：科研端两页 `title` = 「智学数研 · 数学智能助学平台（v4）」（证据 `shots/lead-r3-write.json` 的 `probe.title`）。黄金图不是这个。→ 需在科研端路由里设置独立 title。

### P0-2 · 「选中即校验」实际不校验（**产品缺口，不是 bug**）

工程师 1 的边界说明（§5.4）：`derivation_check` / `formula_check` 只有拿到结构化规格才真算。前端只传选中文本 → **永远返回 `needs_conditions`**。两种走法：
- (a) 前端补传结构化规格（需要 UI 让用户从公式里标注 lhs/rhs 或 points）——**可能违 §30 禁参数表单**，慎重；
- (b) 后端另立"公式解析/推导核对"能力，从 LaTeX 文本自行抽取结构——**更符合产品语义**（用户"选中即问"）。
**建议先读宪法 §27/§30/§34/§90 再定，不要拍脑袋。**

### P0-3 · 顶栏（GlobalHeader）未对齐黄金图

- 右侧缺「通知铃铛 + 头像 + 姓名」，现为「研 研究者」徽标
- 标语应为黄金图的「让研究更高效，让思想走更远」
- 项目缩略图应为**封面**而非纯蓝渐变（`rw-psel__thumb` / `rw-idc__thumb` 的 `linear-gradient` 是当前探针唯一 banned 项）
- **后端 `/notifications` 500**（§5.6 #3，`asyncpg` 权限错）——🔔 要有真实数据先得修它

### P0-4 · 多项目（宪法 §70 禁"永远只有一个项目"）

目前演示环境只有一个项目 `e9b7c1e7…`。需支持真实多项目选择。

### P1-B · 视觉终审与迭代（不可外包）

按 §1.3 的分区数值逐项收敛：READ 最差 `agent-header`（26.02）、`agent-result-card`（20.84）；WRITE 最差 `editor`（20.11）。**注意差异主因之一是"示例内容不同"（论文/作者/页码/Agent 结果态）**，不是布局错——所以别盲目追求把数字压到 0，要先看 `compare/*-sbs.png`（并排）和 `*-blend.png`（叠加重影）判断差异性质。

### P1-C · 12 项"发现未修"的裁决与分配

见 §5.6。建议按 `真缺陷 > 一致性 > 轻微` 排序，先修 #3（`/notifications` 500）、#4（section 启发式）、#5（locator 无 bbox 时的高亮）。

### P1-D · 退役旧 `paper-workbench`

需先解耦 3 个单测：`rv11-route` / `rv08-workspace-ia` / `rv16-route-convergence`。

### P2 · 收尾

- 演示数据去留决策（§5.6 待批 C）
- QA 全量验收（ACCEPTANCE-MATRIX A~F）
- **人工原子提交**（`git add -- <明确路径>`，禁 `git add .`；提交前做 masked secret 扫描）

---

## 8. Runbook（全部实测过的命令）

### 8.0 bash 前缀（**每条命令都要加**）

```bash
export PATH="/c/Users/<user>/.workbuddy/binaries/PortableGit/versions/1.2.0/usr/bin:$PATH"
export MSYS_NO_PATHCONV=1
```
`MSYS_NO_PATHCONV=1` 时原生程序（git / curl / python / psql）**看不到 `/tmp`、`/d/...` 这类 POSIX 路径** → **必须用 `D:/...` 盘符路径**，否则产生"无改动"类**测量伪影**（会话 #1 曾因此误报后端无 git 变更）。

### 8.1 起栈（**必须后台常驻**）

```bash
# 用 run_in_background 启动，否则阻塞
C:/python13/python.exe D:/math-arena/tmp/rw_stack.py
```
- `rw_stack.py` 是会话 #2 写的**全栈 supervisor**：一个长命父进程持有 PG / 8300 / 8000 / 5176 全部子进程；子进程日志 `D:\math-arena\tmp\stack-*.log`；HMAC 密钥运行时从 `research-vnext/.env` 读，只进子进程环境、**绝不回显**。
- **PG 就绪很慢（约 80–100s）**；8300 / 8000 **必须等 PG 真就绪再起**，否则 `CannotConnectNowError` 直接退出。
- **为什么不用 `detach_services.py`**：实测它用 `DETACHED_PROCESS` 拉起的进程会在**发起它的工具调用结束后被回收**。`Start-Process` / WMI / `cmd /c start` 三种分离方式**均被安全策略拦截或不可跨会话存活**。唯一可靠机制 = **bash 常驻后台任务**。
- **PG 手动起法**（若 supervisor 不带）：`postgres.exe -D "C:/Users/<user>/pgdata/data" -p 54329` —— **必须显式 `-p`**，conf 默认是 5432。

### 8.2 探活

```bash
curl --noproxy '*' -s -o /dev/null -w "8300 [%{http_code}]\n" http://127.0.0.1:8300/healthz
curl --noproxy '*' -s -o /dev/null -w "8000 [%{http_code}]\n" http://127.0.0.1:8000/api/health
curl --noproxy '*' -s -o /dev/null -w "5176 [%{http_code}]\n" http://127.0.0.1:5176/
for p in 54329 6380 9002; do netstat -ano 2>/dev/null | grep -q ":$p .*LISTENING" && echo "$p UP" || echo "$p DOWN"; done
```
**记住**：`502 + openresty/APISIX` = 沙箱代理返回的，**不是你的服务**。别把它当缺陷（会话 #2 期间因此浪费过工时）。

### 8.3 四件套仪器（**判定不靠肉眼**）

| 工具 | 用途 | 用法 |
|---|---|---|
| `tmp/rw_probe.mjs`（11KB） | **真实浏览器计算样式 / 布局审计**：巨型 SVG 清单、禁止元素（渐变/玻璃/大圆角/大投影）、面板栏宽、Accordion 开合与高度、Agent 文案、**Agent 内 input 数** | `cd /d/frontend && node D:/math-arena/tmp/rw_probe.mjs --route=/research-workspace/<pid>/read --out=probe-read`（可选 `--click=.rw-cap__head:0`） |
| `tmp/rw_shot.mjs`（6KB） | 1672×941 / DPR=1 真实登录截图 + console/failed 采集 | `node D:/math-arena/tmp/rw_shot.mjs --route=... --out=lead-r3-read` |
| `tmp/rw_compare.py`（6KB） | 黄金图对比（并排/叠加/热力图/分区指标） | `C:/python13/python.exe D:/math-arena/tmp/rw_compare.py read <actual.png>` |
| `tmp/rw_accept.py`（9KB） | 验收门禁（A/E/D/F 区一次跑完） | `C:/python13/python.exe D:/math-arena/tmp/rw_accept.py --round r1 [--skip-build]` |
| `tmp/rw_auth.mjs`（6KB） | **共享登录态**（文件锁串行登录 + 回写轮换 cookie）。**自写一次性脚本必须复用它，并在 `finally` 里 `persistState`** | `import { makeAuthedContext, persistState } from 'file:///D:/math-arena/tmp/rw_auth.mjs'` |

产出目录：`deliverables/research-workspace/{probe,shots,compare,accept}/`

### 8.4 登录

- 手机号 **`13800138000`**（会话 #2 起用，与更早的 `13800000000` **不同**）
- dev 验证码：接口返回 `demo_code`；短信 **60s 限流**，`retry_after` 在**响应顶层**
- 8000 支持 `preferred_role=researcher`；`review_enabled=False` 时登录自动批准
- **别自己写登录**——用 `rw_auth.mjs`

### 8.5 数据库

```bash
PGPASSWORD=postgres /d/PostgreSQL/pgsql/bin/psql.exe -h 127.0.0.1 -p 54329 -U postgres -d research_vnext -Atc "select version_num from rv8300.alembic_version_rv8300"
```
预期 `0018_rv18_search_tokens`。**两处 alembic 都要 upgrade head**（`research_vnext` schema `rv8300` + `math_arena`）；库落后会导致登录 500。

### 8.6 前端

```bash
cd /d/frontend
export PATH="/c/Users/<user>/.workbuddy/binaries/PortableGit/versions/1.2.0/usr/bin:$PATH"
PORT=5176 node_modules/.bin/vite
```
- 启动前 `rm -rf node_modules/.vite`（safe-delete 保护会拦，需绕）
- **pnpm 大坑**：本机 pnpm 12 的 symlink/junction **静默失败**（包能下、链接全空）——共享仓库只准用原 pnpm；装包前先看 `node_modules/.modules.yaml` 的 `storeDir/virtualStoreDir`。恢复工具 `D:/math-arena/tmp/rv13_relink.py`。
- 5176 可能落 Windows 保留端口段（5141-5240）→ `netsh int ipv4 show excludedportrange protocol=tcp`；绕行用 5350。

---

## 9. 我的交叉审核（对会话 #2 工作的辩证评价）

> 用户要求我"辩证地看待"，且明确"让下一个智能体自己探索"。以下是我**从盘上证据**能看出的事，含我对会话 #2 的质疑。

### 9.1 属实且质量高的

- **W1b 图标根因是真发现**：`@vicons/ionicons5` 不带 width/height 是系统性事实，`@layer` 兜底是正确且讲究的解法（用 `.rw-nav__ico` 18px 改前改后不变作为反证）。**且我复验 `bigSvgs=[]` 成立。**
- **两条"预览空白"根因是真的、且叠加**：`canvasRef` 挂载时序 + `run_id`/`id` 字段名不匹配。工程上属于典型的"两个独立缺陷叠加成同一症状"。会话 #2 主理人**没有停在第一个根因上**（这是会话 #1 常见的错误），值得肯定。
- **真实数据 > 造假**：5 篇真 arXiv、真实编译 3 页 111KB、真实 `main.tex:47` 错误定位——比自造 LaTeX 强得多，符合用户"要真实证据"的偏好。
- **`rw_auth.mjs` 三坑定位**（token 只在内存 / TTL 2min / refresh 单次轮换）——这是真正把"工具不好用"追到根因的工作，后续所有验收都因此不再抢验证码。

### 9.2 我的质疑（**接手者请重点验证这些**）

1. **`compare/*-regions.txt` 是"工程师自产截图"打的分，不是主理人独立截图。**
   `read-regions.txt` 里 `actual : ...shots\eng-w2b-read.png`（工程师 3 的截图），`write-regions.txt` 是 `eng-w2c-write.png`（工程师 4 的）。**存在"自己给自己打分"的结构性风险**——虽然会话 #2 主理人另外出了 `lead-r3-read/write.png` 终审截图，但**那两张没做 compare**。
   → **行动**：接手后请用 `lead-*` 或你自己的截图重跑 `rw_compare.py`，看 18.59 / 16.68 是否复现。

2. **`rw_accept.py` 写了但从未跑完 → 没有任何机器化的全局放行结论。** `accept/` 目录不存在。§7 P1-A 就是补这个。

3. **会话 #2 的 TaskList 只有 5 条，且 1/2/3 属于同一名工程师（后端），4/5 是工程师 4/3——工程师 2（Agent 面板）没有对应任务条目。** 说明任务表与实际分工有偏差（工程师 2 的活是以消息派发的）。这不影响交付，但说明**别拿 TaskList 当工作清单**。

4. **`document.title` 仍是学生端** ——这是"视觉 1:1"的硬伤，会话 #2 的待办里列了但没做。我复验确认（`shots/lead-r3-write.json` 的 `probe.title` 值）。

5. **「选中即校验」是空转**：用户在 READ 里选中一个公式点「推导校验」，**永远不会得到校验结论**，只会得到"请提供更多信息"。这是**产品级缺口**，而且它在黄金图里看起来是"能工作"的。**接手者必须决策**（§7 P0-2），否则等于把一个假功能交付上线。

6. **演示数据是"人工播种"的，不是产品自然产生的**：14 个文件、5 篇文献、5 条笔记都由工程师用 API 手搓。**这在验收时是对的**（要看黄金图效果），但**必须区分"演示态"与"产品态"**——比如 demo 项目名「FE 误差估计」、`figures/mesh.png` 是文本占位，这些不能当作功能完成的证据。

7. **会话 #2 记忆日志只写了 W1/W1b**（12:54 停笔），Wave 2 全部未记录。→ 所以**本文 §5.4 是从团队收件箱反推的**，比它的自述更可信，但终究是二手。

### 9.3 给接手者的元教训（两条，都见过血）

- **验收数字必须有可复算脚本依据**。会话 #1 把目测栏宽写进验收标准（355/435/428/454），实测是 306/590/462/314 —— 错得离谱，是工程师反击才发现。**任何"验收标准"里的数字，必须附生成它的命令。**
- **共享文件必须显式指定"某一时刻唯一编辑者"**。`ResearchWorkspaceShell.vue` 是三方交汇点，会话 #1 只写了"只改自己那块"的口头约束 → 被整体重写覆盖，且两路各造了一份 Agent 结果卡（违宪法 §59/§74）。**并发施工前先声明文件所有权。**

---

## 10. 关键环境坑（跨会话通用，**已复验**）

| 坑 | 处置 |
|---|---|
| `502 + openresty/APISIX` 页 | 沙箱出网代理（`http_proxy=127.0.0.1:45711`），**不是你的服务**。用 `netstat` 或 `curl --noproxy '*'` |
| `MSYS_NO_PATHCONV=1` + POSIX 路径 | 原生程序看不到 `/tmp`、`/d/...` → **必须 `D:/...`**，否则测量伪影 |
| 分离进程 | `detach_services.py`(DETACHED_PROCESS) / `Start-Process` / WMI 全部**不可跨会话存活或被拦**。**只能** bash 常驻后台任务持有 |
| PG 启动 | `postgres.exe -D "C:/Users/<user>/pgdata/data" -p 54329`，**必须显式 `-p`**；就绪需 80-100s |
| 8300 抢端口 | **单一持有者**。多方重复启动是历史上 8300 反复 502 的真根因（后端工程师曾自行重启两次）。工程师**不得**自行重启服务 |
| bash 工具 stdout 被吞 | 会话 #2 期间出现过（连 `echo` 都空但退出码 0）→ 改用"命令写文件 + Read 读" |
| bash 变量名 | 不能撞只读变量（如 `PPID`） |
| 删文件 | 用 PowerShell `Remove-Item`（`rm` 是依赖 `dirname` 的 safe-delete shim） |
| 只读变量 / `$?` | 见上 |

**`ENV-NOTES.md` 里已过时的条目**（`D:\math-arena\.workbuddy-ai\memory\ENV-NOTES.md`）：
- ❌「本机无 psql 客户端 → `docker exec`」→ **本机有** `D:\PostgreSQL\pgsql\bin\psql.exe`，且 PG 是**原生**不是 docker
- ❌「端口 5176/5188 无法 bind」→ 5176 **已稳定运行**（会话 #1/#2 皆可用）
- ✅ 仍有效：User 级环境变量在 AI 沙箱 `Process` 作用域不可见（须 `[Environment]::GetEnvironmentVariable($n,'User')`）；HTTP 4xx 错误体探测必须用 `curl.exe`

---

## 11. 团队与协作纪律

### 11.1 可复活的团队

| 团队 | 位置 | 成员 | 状态 |
|---|---|---|---|
| 会话 #1 `research-workspace-rebuild` | `C:\Users\<user>\.workbuddy\projects\d-math-arena\48162ace-.../subagents/`（8 个 jsonl） | software-architect / software-engineer / fe-engineer-a / fe-engineer-read / fe-engineer-write / fe-engineer-agent | **paused**（会话 #1 额度 429） |
| 会话 #2 `_auto_3fc0b79d` | `C:\Users\<user>\.workbuddy-ai\teams\_auto_3fc0b79d-.../` | software-engineer-1..4 + team-lead | **全部 completed**（报告在 `inboxes/team-lead.json`） |

**建议**：会话 #2 的 4 名工程师**上下文都还很新鲜且已 completed**——如果新会话要沿用，直接 `SendMessage` 续用比新建更省。尤其：
- software-engineer-1（后端语义）→ 续修 §5.6 的 #10/#11
- software-engineer-2（Agent 面板）→ 续修 §5.6 的 #1/#2、批 A 的 token
- software-engineer-3（READ）→ 续修 §5.6 的 #4/#5
- software-engineer-4（WRITE）→ 续修 §5.6 的 #8

### 11.2 纪律（**违者必乱**）

1. **服务单一持有者**：8300 / 8000 / 5176 / PG 只由主理人的 supervisor 持有。工程师**只报告、不重启**。
2. **共享文件显式独占**：`shell/ResearchWorkspaceShell.vue`、`tokens/research-tokens.css`、`stores/*`、`api/*` —— 同一时刻**只允许一个编辑者**，变更经主理人中转串行放行。
3. **服务窗口期广播**：重启前后必须广播"502 不是缺陷，等 3 分钟重跑，作废失败证据"。
4. **每轮 STOP**：完成一个可审核结果就 `STOP — 等待人工验收`，不自动连跑。
5. **提交权在人工**：`git add -- <明确路径>`，**禁 `git add .`**；提交前 masked secret 扫描。
6. **验证前不得声称完成**：涉及功能/UI 的改动必须给出真实命令结果 / 截图 / 测试通过证据。

---

## 12. 一句话交接

**服务已全挂，先跑 `rw_stack.py` 起栈；然后跑 §6 的 V1~V14 自己复验（别信本文）；再跑 `rw_accept.py` 拿到第一份机器化放行表；之后按 §7 的 P1-A → P0-1 → P0-2 → P0-3 推进。**
视觉上离黄金图已经不远（READ 18.59 / WRITE 16.68，无分区"不达标"），真正的坑在**「选中即校验」空转**、**顶栏未对齐**、**演示数据≠产品态**这三处。

**未完成清单一句话**：代码零提交；验收门禁零执行；4 项待批未裁；12 项发现未修；`document.title` 仍为学生端；`paper-workbench` 未退役。
