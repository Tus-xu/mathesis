# Math Arena · 全仓库工程与三端重构规则

> **用途**：本文件是仓库级 Codex / AI Coding Agent 工程规则。它规定“如何安全地工作、如何验证、如何保护现有系统”。
>
> **产品真相**不全部写在本文件中。学生端、教师端备课中心、科研端的产品行为与视觉细节分别以各自 Product Contract / Golden / Acceptance 文档为准。
>
> **最高原则**：先保护真实已有能力，再做重构；先证明产品行为，再扩展代码；代码通过不等于产品完成。

---

## 0. 规则优先级与 Source of Truth

当不同信息冲突时，按以下优先级处理：

1. 安全、数据完整性、未提交现场保护规则
2. 当前真实运行行为与可复现测试结果
3. 当前数据库 / API / 服务事实
4. 各端正式 Product Contract
5. 各端 Golden Screens + Visual Spec
6. 各端 Backend Contract / Architecture Contract
7. Acceptance Matrix
8. 本 `AGENTS.md`
9. 历史需求、旧截图、旧说明
10. AI 自己的推断

**禁止用个人推断覆盖仓库事实。**

### 各端产品最高规范

#### 科研端 Research Workspace

- `docs/research-workspace/RESEARCH_PRODUCT_CONTRACT.md`
- `docs/research-workspace/VISUAL-SPEC.md`
- `docs/research-workspace/BACKEND-CONTRACT.md`
- `docs/research-workspace/ACCEPTANCE-MATRIX.md`
- `docs/research-workspace/golden/READ-GOLDEN.png`
- `docs/research-workspace/golden/WRITE-GOLDEN.png`

#### 教师端 Prep Center
-  `"D:\教师端备课中心重构宪法"`

#### 学生端 Student Workspace

- 以仓库中现有学生端 Product Contract、Golden、Acceptance 文档为唯一产品依据；若当前缺失，**先补齐文档，不得凭空重造学生端产品**。
- 学生端重构必须保持现有核心能力连续性，包括但不限于：引导式解题、举一反三、双师课堂、错题本、动态数学图形、学生上传题目并获得讲解；若现有产品已有更多稳定能力，优先保留并纳入契约。

> **重要**：本文件不要求创建同名数据库表或同名服务。文件中出现的领域名优先解释为产品能力；实现方必须先映射现有实现。

---

# 1. 仓库、包管理与 Python / Node 约束

- 根目录：pnpm workspace；后端为多服务 monorepo。
- 教师平台核心：`services/teacher-platform/`。
- Python 包管理：`uv`。
- Node 包管理：`pnpm`。
- 禁止混用：`pip` / `npm` / `yarn`。
- `services/teacher-platform`：Python 3.12，不向下兼容。
- 通用 API：Python >= 3.11。

## 权威命令

| 命令 | 用途 | 适用范围 |
|---|---|---|
| `pnpm dev` | 并行启动所有服务开发模式 | 根 |
| `pnpm build` | 生产构建 | 根 |
| `pnpm lint` | 全仓库 lint | 根 |
| `pnpm test` | 全仓库测试 | 根 |
| `pnpm type-check` | 全仓库类型检查 | 根 |
| `make api` | 教师平台 FastAPI 开发服务器 | `services/teacher-platform` |
| `make test` | 教师平台单元测试 | `services/teacher-platform` |
| `make gate` | Valkey 兼容性集成门禁 | `services/teacher-platform` |
| `uv run pytest tests/unit -q` | 指定单元测试子集 | `services/teacher-platform` |

禁止因为“方便”自行替换项目规定的启动、构建、测试、包管理方式。

---

# 2. Brownfield-first：这是已有系统重构，不是从零开发

## 2.1 绝对原则

Math Arena 是 Brownfield Repository。

当前仓库已经存在真实的：

- 用户与权限
- 教师端
- 学生端
- 科研端
- 数据模型
- API
- AI 服务
- RAG / 知识库
- PPT 内核
- 教案内核
- 数学能力
- 文件存储
- 任务 / 队列
- 工作流
- 业务数据

因此：

> **任何新架构必须首先映射到已有实现，而不是默认重新创建一套 V2。**

## 2.2 修改前必须调查

修改任何业务文件之前，必须调查完整上下文：

- 当前文件完整内容
- 上游调用方
- 下游依赖
- 路由注册
- API consumers
- 数据模型
- 配置
- 环境变量
- 测试
- worker / queue
- 文档
- Git history 中相关迁移

禁止只搜索文件名后直接创建替代实现。

## 2.3 能力映射

每一个准备新增的领域能力，都必须先做 Duplicate Capability Check：

```text
目标能力
↓
搜索现有代码 / API / DB / worker / tests / docs
↓
是否已有相同或相近能力？
├─ YES → KEEP / ADAPT
└─ NO  → CREATE
```

### 默认决策顺序

`KEEP > ADAPT > REPLACE > REMOVE`

- **KEEP**：已有实现满足产品要求，只接线或做最小改动。
- **ADAPT**：核心能力可复用，通过 adapter / facade / compatibility layer 接入新产品。
- **REPLACE**：只有证据证明现有实现无法继续满足产品要求时允许。
- **REMOVE**：必须证明无消费者、无数据依赖、无任务依赖、无配置依赖并有回滚方案。

## 2.4 禁止平行内核

已有：

- Model Gateway
- PDF service
- RAG service
- LaTeX compiler
- Math / Lean capability
- PPT Engine
- Lesson Plan Engine
- Textbook service
- File service
- Session / conversation
- Task queue

时，禁止未经调查直接创建：

- `NewModelGateway`
- `ResearchRAGServiceV2`
- `LatexCompileService2`
- `TeacherPrepEngineV3`
- `NewPptEngine`
- `NewLessonPlanEngine`

先使用 adapter / facade / orchestration layer。

## 2.5 数据模型防套表

Product Contract 中的：

- ResearchProject
- PreparationSession
- TeachingBlueprint
- ProjectAsset
- TeachingDeck
- MathAsset
- LessonPlan

等，优先视为**领域概念**。

如果已有 schema 能表达：

```text
Domain Concept → Existing Model
```

则禁止机械创建新表。

例如：

```text
ResearchProject → Project
ResearchSession → Session
ProjectAsset    → Document / Asset
```

只有真实证据表明现有模型无法承载目标行为，才允许扩展 schema。

---

# 3. Dirty Worktree 与 Git 安全

1. 发现目标文件存在不属于本轮的未提交修改：**立即 STOP 对该文件的写入**，报告冲突。
2. 禁止覆盖、抹除或“顺手修掉” unrelated dirty changes。
3. 禁止未经人工明确批准执行：
   - `git reset --hard`
   - `git clean -fd`
   - `git checkout --`
   - `git restore` 覆盖现场
   - force push
4. 原子提交：
   - `git add -- <明确路径>`
   - 禁止 `git add .`
5. commit 前必须执行 masked secret scan。
6. 禁止将真实密钥写入源码、日志、截图、测试 fixture、commit、文档。

> **自主运行不等于可以破坏现场。** 安全与数据完整性高于“持续执行”。

---

# 4. Autonomous Agent Operating Mode

当用户的任务明确授权“自主运行、无需逐轮放行、一直做到完成”为前提时，Agent 应进入：

`AUTONOMOUS PRODUCT ENGINEERING MODE`

在该模式下：

- 不因日常选择频繁询问用户。
- 不等待用户批准普通重构决定。
- 根据 Product Contract、真实仓库事实、测试证据自主判断。
- 可以自行分解任务、调用子代理、返工、回滚到最近稳定版本并重新尝试。
- 必须持续运行直到满足明确的最终 Gate。

但以下情况仍必须停止并报告：

- 即将破坏用户未提交代码。
- 即将删除不可逆用户数据。
- 需要真实生产凭证 / 人工授权凭证。
- 安全边界不明确且继续执行存在真实数据风险。
- 两次以上结构性尝试无法解决且不存在新的证据路径。

除以上安全 / 不可逆边界外，不得用“等待用户确认”逃避产品决策。

---

# 5. 多代理团队模式

建议至少使用以下逻辑角色：

### 5.1 Lead / Orchestrator

负责：

- 产品目标解释
- 任务拆解
- 子代理调度
- 结果汇总
- 返工 / 回滚决策
- 最终产品审核

### 5.2 Frontend Builder

只负责实现，不负责最终批准自己的视觉结果。

### 5.3 Backend / Kernel Builder

负责真实 API / orchestration / persistence / capability integration。

### 5.4 UX / Product Reviewer

检查：

- 是否符合产品合同
- 用户路径是否自然
- 是否存在 feature soup
- 是否出现套娃
- 是否出现没有产品价值的 UI

### 5.5 Visual QA Reviewer

检查：

- Golden 对齐
- spacing
- density
- hierarchy
- typography
- panel ratio
- visual state

### 5.6 Functional QA Reviewer

从真实浏览器链路点击验证。

### 5.7 Artifact QA Reviewer

专门检查：

- PPTX
- DOCX / PDF
- 数学资产
- 导出结果

### 5.8 交叉验证规则

修改代码的人不得单独宣布通过。

每个核心功能至少经过：

```text
Builder
→ Independent Functional Review
→ Independent Visual Review
→ Artifact / Backend Review（按功能）
→ Lead Decision
```

---

# 6. Prototype Mode 与 Production Mode 必须严格分离

项目中的两种任务必须区分：

## PROTOTYPE_ONLY

只允许：

- HTML / React prototype
- mock state
- local JSON
- fake async delay
- browser interaction
- screenshot

禁止：

- 修改生产数据库
- 修改现有后端
- 改真实业务路由
- 删除旧功能
- 替换生产服务

Prototype 目标：

> 验证产品形态与用户链路，而不是宣称真实功能完成。

## PRODUCTION_REBUILD

才允许：

- 真实 API
- 数据库
- Agent
- PPT / Lesson Plan
- RAG
- 任务队列
- 持久化
- 权限

不能把 Prototype Mock 留进正式业务。

---

# 7. Golden Screen Protocol

## 7.1 Golden 是 Screen State，不是灵感图

每张 Golden 只描述一个页面状态。

禁止：

- 把多张 Golden 合成一个页面
- 把一个页面拆成无意义 Dashboard 模块
- 因 Golden 未展示某功能而自由新增

## 7.2 图片必须逐张、放大检查

任何实现对应 Screen 前：

1. 打开对应原图。
2. 使用原始分辨率。
3. 放大至少 200%，必要时 300%–400%。
4. 逐区域检查：
   - Shell
   - Header
   - Left
   - Center
   - Right
   - Bottom
   - toolbar
   - spacing
   - typography
   - button hierarchy
   - empty/loading/selected state
5. 记录视觉观察结论。

禁止“扫一眼截图，然后凭感觉实现”。

## 7.3 Undefined ≠ Free Design

如果：

- Golden 没展示
- Product Contract 没规定
- Existing Product 没要求
- 当前用户任务也不需要

则默认：**不新增**。

禁止因为“完整产品应该有”而自行添加：

- AI Center
- Task Center
- Token Center
- Model Center
- Template Marketplace
- 复杂 Dashboard
- 复杂 KPI
- 与当前任务无关的二级系统

## 7.4 Shell 继承规则

Golden 中出现的：

- 头像
- 用户名
- 品牌
- 顶部导航

如果属于已有平台 Shell，必须复用已有 Shell。

禁止在业务模块内部再次创建一套平台 Shell。

## 7.5 Visual precedence

科研端：

`Golden > VISUAL-SPEC > 其它视觉文档`

教师端：

`Golden > VISUAL-SPEC > 其它视觉文档`

学生端：

以其最新正式 Golden / Visual Spec 为准。

---

# 8. UI Reality Contract：禁止空壳

正式产品中的任何：

- Button
- Tab
- Menu
- Dropdown
- Counter
- Status
- Progress
- Card
- Shortcut
- AI Action
- Editor
- Preview

必须具备：

```text
Trigger
→ State Change
→ Real Result
→ Persistence（适用时）
```

否则：

- 不得渲染成“已完成能力”。
- Prototype 可 mock；Production 不可 mock。

### 明确禁止

- 点击无反应
- 只显示 Toast
- 200 OK 但数据不变
- “生成成功”但没有真实 artifact
- “已保存”但刷新丢失
- 假进度条
- 固定 AI 回复
- 固定计数
- 固定状态
- 假历史

---

# 9. 真实用户链路优先于代码通过

所有涉及功能 / UI 的实现，都必须验证真实链路。

例如：

```text
进入页面
→ 点击按钮
→ API
→ 后端处理
→ 数据更新
→ 前端状态变化
→ 刷新
→ 数据仍在
```

涉及 AI：

```text
User Intent
→ Context
→ Agent
→ Tool / Service
→ Structured Result
→ UI Update
→ Persistence
```

涉及 PPT：

```text
Teaching Blueprint
→ Deck generation
→ Slide artifact
→ Browser preview
→ Edit
→ Export
→ Real PPTX inspection
```

涉及教案：

```text
Teaching Blueprint
→ Lesson Plan
→ Editor
→ Edit
→ Export
→ Real artifact inspection
```

---

# 10. 功能证据与截图验收

任何核心功能必须有真实浏览器截图证据。

不能只给：

- unit test
- API 200
- build success

至少保存：

1. 初始状态截图
2. 触发操作截图
3. 成功结果截图
4. 关键异常 / loading 状态截图（适用时）

例如教师端“换一道例题”：

```text
Before
→ Click
→ AI Proposal
→ Confirm
→ After
→ PPT / Lesson Plan affected state
```

必须能够证明：

**真正改变了业务对象。**

---

# 11. Visual Regression

Golden Screen 必须进入正式视觉回归。

固定：

- viewport
- browser
- theme
- font environment
- deterministic fixture

建议使用 Playwright / 等价工具。

至少覆盖：

- default
- selected
- loading
- generated
- modified
- error

视觉检查不接受：

“看起来差不多”。

---

# 12. Product Acceptance ≠ Visual Acceptance

一个结果必须同时满足：

### Product
用户路径正确。

### UX
操作清晰、上下文连续、无重复询问。

### Functional
真实请求 / 状态 / 持久化。

### Visual
符合 Golden / Design System。

### Artifact
导出的文件真实可打开、可编辑、内容正确。

### Reliability
失败可以恢复，不丢用户资料。

### Performance
长任务可观察、可增量、可恢复。

### Security
用户 / 项目 / 文件隔离。

---

# 13. 科研端 Research Workspace 特殊规则

## 13.1 产品定位

科研端是：

> AI-native Research Workspace

用于：

- 论文阅读
- 论文写作
- 公式检查 / 推导
- 理论问答
- 引用核查
- 翻译 / 润色
- LaTeX
- 证据与项目知识

## 13.2 两个一级 Workspace

只保留：

- 阅读
- 写作

不要重新加入：

- 审查一级 Tab
- 大型全局搜索框
- 工具箱式十几个一级功能

## 13.3 Project-first

Paper / Draft / Notes / Citation / AI Conversation / Evidence 属于同一个 Research Project。

阅读与写作是同一项目的两个视角，不是两个割裂产品。

## 13.4 Research Agent

右侧是统一 Research Agent，不是“工具表单”。

用户不应该看到：

- temperature
- top_k
- prompt
- model ID
- agent mode
- planning mode

Agent 自动使用 Research Context。

## 13.5 Context

至少能够表达：

- user
- project
- workspace mode
- current paper
- current page
- selected text / formula
- active tex file / line
- project notes
- citations
- conversation

## 13.6 Evidence-first

科研结论、引用、公式、理论说明尽可能提供可回溯证据。

禁止伪造引用。

## 13.7 AI 修改确认

任何 AI 修改论文的动作必须经过用户确认。

禁止静默写入。

## 13.8 前端边界

科研端新代码只允许在：

`src/features/research-workspace/`

禁止复用 `--ma-*` / `--re-*` 旧 token。

## 13.9 网关

科研端所有请求必须经：

`8000 gateway → 8300`

路径：

`/api/research-vnext/*`

禁止前端直连 8300。

禁止自造 `X-Research-Access-*` 签名头。

---

# 14. 教师端 Prep Center 特殊规则

## 14.1 本轮只改备课中心

教师端其它业务保持存在与可访问。

备课中心是：

`Teacher Platform → Preparation Center`

不是第二个教师平台。

## 14.2 核心链路

```text
Teacher
→ Preparation Session
→ Conversation
→ Teaching Blueprint
→ Math Assets / Examples
→ PPT
→ Lesson Plan
→ Teacher Edit
→ Export
```

核心护城河：

`教材 → 教学设计 → 数学对象 → PPT / 教案`

## 14.3 Conversation-first

聊天不是孤立 chatbot。

AI 的重要结论必须沉淀到：

- Teaching Goal
- Teaching Path
- Example
- Math Asset
- PPT Patch
- Lesson Plan Patch

禁止重复询问已经确认的信息。

## 14.4 Teaching Blueprint

PPT 与教案必须共享同一个教学中间状态，避免两套独立生成导致不一致。

至少表达：

- goals
- focus / difficulty
- teaching path
- timing
- teacher activities
- student activities
- examples
- math assets
- teaching intent

## 14.5 PPT V2

PPT Studio V2：

`services/teacher-platform/app/ppt_v2/`

禁止将 V2 功能塞入旧：

`generation/contents/export`

PPT 必须是真实 PPTX，不得用整页截图伪装。

## 14.6 PPT 质量 Gate

至少通过：

- CONTENT
- TEACHING NARRATIVE
- MATH
- VISUAL
- EDITABILITY
- RELIABILITY

输出必须：

- 有教学叙事
- 多种有意义版式
- 无明显文本溢出
- 无元素重叠
- 无空白废页
- 无重复页
- 数学公式正确
- 数学图形正确
- 真实可编辑

## 14.7 Math Asset

数学图形优先作为结构化数学对象而非静态 PNG。

需要关注：

- 参数
- 方程
- 坐标
- 焦点 / 顶点 / 关键点
- 标签
- 渲染
- 可编辑性

修改参数时：公式、图形、标注必须同步。

## 14.8 教案

教案不是 PPT 文本拼接。

至少包含：

- 教学目标
- 重点难点
- 教学准备
- 教学过程
- 教师活动
- 学生活动
- 设计意图
- 板书
- 作业
- 教学反思

必须真实可编辑与导出。

## 14.9 速度

已有 PPT / 教案内核如果慢：

优先优化真实生成链路，不用假进度欺骗用户。

长任务应：

- 可观察
- 增量生成
- 失败可恢复
- 尽可能局部重试

## 14.10 教材资源 V2

教材资源库 V2：

`services/teacher-platform/app/textbook_v2/`

含 `importers/`。

授权教材切片：

`D:\知识库\AC切片`

是唯一教材源。

严格禁止：

- 原 PDF
- TextbookPage

作为 V2 产品事实源。

图题资产由 R13（MinIO / AssetRef / Vision）负责。

## 14.11 Teacher embedding

教师平台 embedding：768。

通用 API embedding：1024。

禁止交叉写索引。

---

# 15. 学生端 Student Workspace 特殊规则

## 15.1 产品定位

学生端是学习工作台，不是教师端的缩小版，也不是科研端的复制品。

## 15.2 核心能力连续性

重构必须优先保护已有稳定能力，包括但不限于：

- 引导式解题
- 举一反三
- 双师课堂
- 错题本
- 动态数学图形
- 学生上传题目并让 AI 讲解

已经验证可用的功能不能因为“换 UI”而消失。

## 15.3 双师课堂

应体现：

- AI 课件 / 页面
- AI 讲解
- AI 助手问答

而不是直播间 / 视频会议 UI。

## 15.4 数学交互

学生端数学图形必须优先保证：

- 正确
- 可理解
- 有交互逻辑
- 与解题步骤一致

## 15.5 UI 原则

不要把学生端改成教师端的 Dashboard。

学生端围绕：

`Question → Guidance → Practice → Feedback → Learning Record`

形成连续学习体验。

---

# 16. 三端共享平台规则

三端可以共享平台能力：

- Identity
- Model Gateway
- Context / Memory
- File Service
- Knowledge Service
- Math Service
- Task / Queue
- Observability

但：

**共享后端能力 ≠ 共享用户体验。**

禁止把：

Research Agent UI
教师 Prep UI
学生 AI Tutor UI

机械复制。

每个端必须拥有自己的：

- 用户目标
- 工作流
- 信息架构
- UI
- Agent 行为

同时共享：

稳定的基础设施与数据契约。

---

# 17. 全局 AI Agent 规则

## 17.1 Agent 是 capability layer，不是 UI 组件堆

用户不应该被迫选择：

- RAG
- Tool
- Model
- Planner
- Executor

Agent 应根据上下文自主选择能力。

## 17.2 Structured Result

AI 正式能力不能只返回 Markdown 字符串。

应尽可能返回结构化结果，例如：

```text
kind
status
summary
findings
evidence
patches
actions
confidence
scope
```

## 17.3 Scope-aware Action

每个 AI 修改都必须标明作用域：

- 当前元素
- 当前页
- 当前教学节点
- 当前文档段落
- 当前项目
- 整体产物

防止“改一处 → 全部重生成”。

## 17.4 用户确认

高影响修改必须用户确认：

- 批量改稿
- 批量改 PPT
- 教学路径重大重构
- 删除/覆盖内容

---

# 18. Failure UX 与 Reliability

真实产品必须处理：

- AI timeout
- provider unavailable
- RAG failure
- save failure
- task failure
- compile failure
- file parse failure
- network disconnect
- partial generation

禁止：

- 白屏
- 无限 loading
- 原始 stack trace
- 用户数据静默丢失

### Recovery

可以：

- Retry
- Resume
- Retry current page / task
- Restore last saved version

---

# 19. Performance 不是假动画

任何长任务必须有真实阶段状态：

`queued → running → progress → completed / failed`

禁止用随机进度条或 `setTimeout` 假装完成。

PPT / Lesson Plan / RAG / Compile 等任务优先使用真实 task state。

---

# 20. Security / Permission

必须保证：

- 用户隔离
- 项目隔离
- 文件隔离
- AI tool capability boundary
- 上传文件安全
- LaTeX / document processing sandbox

禁止：

- 用户 A 读取用户 B 数据
- AI 通过 prompt injection 绕过权限
- LaTeX 获得任意服务器命令执行
- 日志打印完整论文或真实密钥

---

# 21. Secret Handling

所有密钥只从环境变量获取。

例如：

- `MODEL_API_KEY`
- `EMBEDDING_API_KEY`

禁止：

- commit
- log
- screenshot
- response echo
- sample config 中写真实 key

示例只能使用：

`YOUR_API_KEY_HERE`

---

# 22. Middleware / API Boundary

- 所有 middleware 使用 `async/await`。
- Koa 禁止 express wrapper。
- 必须保留 `ctx.state`。
- API contract 改动必须检查所有 consumers。
- 禁止为了快速接通前端而偷偷创建第二套认证或 header 协议。

---

# 23. 实施后的最小证据标准

涉及 UI / 功能的 PR 或 agent task 必须提供：

### Code Evidence

- build
- lint
- type-check
- relevant tests

### Runtime Evidence

- 实际启动命令
- 实际浏览器访问
- console/network 关键结果

### Visual Evidence

- 真实运行截图
- 对应 Golden

### Functional Evidence

- 点击链路
- 状态变化
- Persistence

### Artifact Evidence（适用时）

- PPTX 实际打开
- DOCX/PDF 实际打开
- 数据真实存在

禁止只说：

“已完成。”

---

# 24. Acceptance Matrix

每个核心功能必须拥有：

| Feature | Trigger | Backend | Persistence | Browser Evidence | Visual | Artifact | Status |
|---|---|---|---|---|---|---|---|
| 示例 | | | | | | | |

任一关键列 FAIL：

该功能不能标记 PASS。

---

# 25. Two-Failure Rollback Rule

同一问题如果连续两轮修改没有明显接近目标：

1. 停止继续叠加 patch。
2. 回到最后稳定版本。
3. 重新分析根因。
4. 选择不同实现路径。
5. 再验证。

尤其是：

- 布局结构错误
- Golden 偏离严重
- 后端边界错误
- 数据模型冲突
- Agent Context 错误

禁止无限微调 padding 掩盖结构问题。

---

# 26. 页面一屏一职责

任何 Screen 必须回答：

- 这个页面当前唯一主要用户目标是什么？
- 主对象是什么？
- 用户最重要动作是什么？
- 当前状态是什么？

不要把：

首页
+ 对话
+ PPT
+ 教案
+ 资料
+ 导出

全部做成一个 Dashboard。

---

# 27. No Feature Soup

新增任何按钮 / panel / card 前必须回答：

1. 它服务哪个用户任务？
2. 它对应什么领域对象？
3. 它的数据来自哪里？
4. 点击后改变什么？
5. 是否需要持久化？
6. 如果删除它，核心用户任务是否受损？

回答不上来：

不允许加入正式 UI。

---

# 28. UI Shell 适配优先于 UI 复制

新端页面必须嵌入现有平台 Shell。

不要把 Golden 中的：

- logo
- avatar
- user name
- global nav

再复制一遍。

Golden 主要定义：

- workspace structure
- internal panel hierarchy
- density
- component relationship
- interaction state

---

# 29. 不允许通过“增加 AI 按钮”解决产品问题

当用户任务不顺时：

优先修复：

- Context
- workflow
- state transition
- object editing
- evidence
- persistence

而不是：

“再加一个 AI 按钮”。

---

# 30. 生产完成定义

一个三端重构结果只有在以下维度全部通过时才能称为完成：

### Product
用户任务闭环。

### Visual
符合各端 Golden / Visual Spec。

### Functional
真实接口和状态。

### Backend
真实数据 / capability / persistence。

### AI
上下文正确、结果结构化、动作真实。

### Artifact
PPT / Lesson Plan / Research Document 等真实产物正确。

### Math
公式、图形、推导正确。

### Reliability
失败可恢复、数据不丢。

### Performance
长任务可观察、增量、可恢复。

### Security
用户与数据隔离。

### Cross-end integrity
三端之间共享平台能力但不互相污染 UI 与业务边界。

---

# 31. Final Definition of Done

只有当：

- 原有三端业务边界没有被误删
- 用户 / 权限 / Shell 正常
- 各端 Product Contract 满足
- Golden Visual Regression 通过
- 核心功能真实可操作
- API / persistence 通过
- AI capability 真实可用
- 失败场景可恢复
- PPT / Lesson Plan / Research artifacts 实际可用
- 数学内容验证通过
- 真实用户旅程通过
- 所需截图证据齐全
- Acceptance Matrix 全关键项 PASS

才允许最终输出：

```text
FINAL COMPLETE
```

否则：

继续工作。

---

# 32. 停止规则

旧版本中的：

“每轮完成后 STOP — 等待人工验收，不开始下一轮”

**不再作为默认全局规则。**

原因：当前任务可能明确授权 Agent 自主持续迭代。

新的规则是：

### 在 AUTONOMOUS MODE 下

继续执行直到 Definition of Done。

### 在 MANUAL REVIEW MODE 下

才在每个显式 Gate 后 STOP 等待人工验收。

如果用户任务没有明确要求人工逐轮放行：

默认使用仓库 / Product Contract / Acceptance Matrix 自主判断。

但：

安全、未提交代码保护、不可逆数据删除仍然优先。

---

# 33. Agent 输出要求

每一个阶段必须输出：

```text
STATUS
SCOPE
CHANGED
VERIFIED
EVIDENCE
FAILURES
NEXT INTERNAL ACTION
```

当未完成时：

不要写“完成”。

当满足 Definition of Done：

才写：

```text
FINAL COMPLETE
```

并附：

- 修改范围
- 测试结果
- 视觉证据
- 功能证据
- artifact 证据
- 已知限制
- 未决问题（应为零或明确非阻塞）

---

# 34. 最终理念

Math Arena 不追求：

“AI 写了很多代码”。

不追求：

“页面看起来很完整”。

不追求：

“按钮很多”。

不追求：

“所有功能都挤在一个页面”。

真正追求：

```text
真实用户任务
→
真实产品状态
→
真实 AI / backend capability
→
真实可编辑产物
→
真实持久化
→
真实验证
```

三端原则：

Student = Learn
Teacher = Teach / Prepare
Research = Read / Write / Verify

共享：

AI Platform / Identity / Context / Math / Knowledge / Task / Observability

隔离：

User Experience / Information Architecture / Workspace / Agent interaction

最终目标：

**Build the product, not the mockup.**
