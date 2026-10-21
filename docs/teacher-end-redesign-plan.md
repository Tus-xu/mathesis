# 教师端全栈改造方案

> **定位**：从"半桶水原型"改造为"真实可用的AI教研平台"——对话式生成、并行产出、教材入库、模板复用、全链路真实后端。

---

## 一、现状诊断（11张截图 + 代码探查）

### 1.1 核心问题清单

| 问题 | 严重度 | 证据 | 根因 |
|---|---|---|---|
| **AI对话无记忆上下文** | 🔴 严重 | 首页AI对话单轮，无历史会话，无多轮上下文 | 后端未存储对话历史，前端无会话管理 |
| **教案生成失败** | 🔴 严重 | 备课中心显示"教案大纲生成失败"，"原型：演示进度流+确定性大纲编排（真实生成属后端M2）" | 依赖星辰工作流wf_lesson_plan但未配置/未验证，本地降级模板内容简陋 |
| **PPT生成不合格** | 🔴 严重 | 课件工坊大纲生成失败"大纲草稿生成失败（mock未启动？），已回退默认结构"，生成的课件仅1页且内容简陋 | 依赖星辰工作流wf_ai_ppt但未配置，无真实LLM生成，无DSL中间表示 |
| **资源库空** | 🟡 中等 | 资源中心"暂无资源"，0项资源 | 无资源入库流程，无教材/模板/素材导入 |
| **知识库没有** | 🟡 中等 | 无知识库模块，无知识点结构化存储 | 无教材切片流程，无知识点提取和入库 |
| **虚假数据填充** | 🔴 严重 | "我的课件"6份全是"双曲线及其标准方程"，"为你推荐"3条也是假数据 | 前端mock（teacherV3Data.ts），未接真实后端 |
| **AI推荐题目前端错位** | 🟡 中等 | 用户反馈（截图未直接显示） | 前端布局问题 |
| **前端界面不统一** | 🟡 中等 | 备课中心（渐变背景+大标题）与课件工坊（白底+工具栏）样式不一致 | 两套设计语言，未统一组件库 |
| **硬编码** | 🔴 严重 | mock服务（teacherV3Server.ts）硬编码返回数据，提示词硬编码在代码中 | 无配置外置，无提示词管理 |
| **无并行生成** | 🟡 中等 | 教案/PPT生成是串行的，6步进度条逐个完成 | 无asyncio.gather并行，无任务队列 |

### 1.2 现有代码架构（可复用部分）

**后端（D:\math-arena\services\api\app\domains\teacher\）**：
- `router.py`：完整的教师端路由（/api/teacher），含教案/PPT/题目/批改/资源等能力
- `workflow_adapter.py`：星辰工作流适配器（wf_lesson_plan/wf_ai_ppt/wf_smart_quiz等），**可作为可选通道，不依赖**
- `lessons.py`：教案生成逻辑，有本地降级模板`_local_lesson`，**需要替换为真实LLM生成**
- `artifacts.py`：Artifact版本与状态机（draft→confirmed→published），**完整可复用**
- `capability_gateway.py`：能力网关，**可复用**
- `registry.py`：教师端注册表，**可复用**

**后端（D:\math-arena\services\api\app\domains\classroom\）**：
- `v7_engine.py`：**V7通用数学建模引擎**——SemExtractor语义抽取→IR Symbolizer符号化→SymPy Solver精算→ClaimBuilder结构化断言→WalkthroughGen 5步讲解生成→ClaimInjector断言写入。**这是核心资产，必须复用到教案/PPT生成**
- `v6_engine.py`：V6引擎（旧版）
- `rag_orchestrator.py`：RAG编排器
- `math_verifier.py`：数学验证器

**前端（D:\frontend\src\pages\teacher-v3\）**：
- `PrepCenterView.vue`：备课中心首页（**样式最好，作为统一基准**）
- `PrepGenerating.vue`：教案生成进度页
- `PrepOutlineConfirm.vue`：大纲确认页
- `PrepTemplatesView.vue`：模板选择页
- `PrepUpload.vue`：上传资料备课页
- `PrepEditor.vue`：教案编辑器
- `SlidesView.vue`：课件工坊首页
- `SlideCanvasV3.vue`：**五区编辑器**（幻灯片缩略图+画布+属性面板+工具栏+底部提示），**完整可复用**
- `TodayView.vue`：今日工作台
- `BankView.vue`：题库
- `QuizView.vue`：组卷中心
- `AssignView.vue`：作业与批改
- `ResourcesView.vue`：资源中心
- `ClassroomView.vue`：课堂互动
- `InsightsView.vue`：学情洞察

**前端组件（D:\frontend\src\components\teacherV3\）**：
- `AiProcessingPanel.vue`：AI处理进度面板
- `ButlerPanel.vue`：AI管家面板
- `ButlerFab.vue`：悬浮球
- `TeacherCompanionDock.vue`：教师伴侣停靠栏
- `PhotoScanWizard.vue`：拍照扫描向导
- `SlideCanvasV3.vue`：幻灯片画布
- `BriefComposer.vue`：简报编辑器
- `ResourceUploadPanel.vue`：资源上传面板
- `ResourceCompanionPanel.vue`：资源伴侣面板

**前端Mock（需删除）**：
- `src/mock/teacherV3Data.ts`：假数据
- `src/mock/teacherV3Server.ts`：mock服务
- `src/mock/teacherV3ClassroomServer.ts`：课堂mock服务

**前端API**：
- `src/api/teacherV3.ts`：教师端API封装
- `src/api/teacherV3Upload.ts`：上传API
- `src/api/sse.js`：SSE流式封装（**可复用**）

---

## 二、改造目标与原则

### 2.1 核心目标

1. **AI对话真实化**：ChatGPT式沉浸对话，多轮上下文记忆，历史会话可回拨
2. **教案生成对话化**：AI询问细节→根据对话生成→自由编辑→导出/存放
3. **PPT生成专业化**：DSL中间表示+并行生成+模板库+数学公式渲染
4. **教材入库结构化**：SeniorTextbook教材切片→知识点/题目/公式提取→入库
5. **模板库可复用**：ppt-templates模板入库，生成时可选
6. **前端样式统一**：以备课中心首页为基准，统一所有页面
7. **全链路真实后端**：去除所有mock和假数据，全部接真实API
8. **并行生成提效**：教案/PPT各模块并行生成，SSE实时推送

### 2.2 改造原则

| 原则 | 说明 |
|---|---|
| **复用不重写** | 现有Artifact系统、五区编辑器、v7_engine、SSE封装全部复用，只改生成逻辑和前端数据层 |
| **配置外置** | 所有提示词存`prompts/`目录，所有配置走环境变量，禁止硬编码 |
| **真实优先** | 宁可功能少，不要假数据；空状态显示真实引导，不填充mock |
| **并行优先** | 可并行的生成任务全部asyncio.gather并行，SSE实时推送进度 |
| **对话驱动** | 教案/PPT生成从"表单填写"改为"对话收集需求"，AI主动询问细节 |
| **数学专业** | 复用v7_engine的SymPy精算和5步讲解模板，确保数学内容专业准确 |

---

## 三、AI对话改造方案

### 3.1 ChatGPT式沉浸对话界面

**前端交互**：
- 输入框固定在页面底部，发送消息后输入框保持在底部（不随消息滚动）
- 消息区域自动滚动到底部，新消息流式上浮（SSE打字机效果）
- 发送后输入框清空，保持焦点
- 支持Shift+Enter换行，Enter发送
- 支持中断生成（停止按钮）

**后端对话记忆**：
- 数据模型：`teacher_chat_sessions`（会话）+ `teacher_chat_messages`（消息）
- 每次对话携带最近N轮上下文（默认10轮，可配置）
- 会话持久化到数据库，刷新页面不丢失
- 支持系统提示词按场景切换（备课/课件/题目/答疑）

**历史会话**：
- 左侧会话列表（可折叠）
- 显示会话标题（AI自动生成首句摘要）、最后消息时间
- 点击切换会话，新建会话按钮
- 支持删除会话（带确认）
- 支持重命名会话

**场景化对话**：
| 场景 | 系统提示词 | 可用工具 |
|---|---|---|
| 备课助手 | 高中数学教师，帮助设计教案，主动询问教学目标/重难点/例题/时长 | 教材检索、知识点查询、题目推荐、教案生成 |
| 课件助手 | 高中数学教师，帮助设计PPT，主动询问页数/模板/风格/例题 | 教材检索、模板选择、PPT生成、公式渲染 |
| 题目助手 | 高中数学教师，帮助出题组卷，主动询问知识点/题型/难度/数量 | 题库检索、知识点查询、题目生成、答案解析 |
| 答疑助手 | 高中数学教师，解答教学疑问，提供教学建议 | 教材检索、知识点查询、联网搜索 |

### 3.2 数据模型

```python
# teacher_chat_sessions
id: UUID
teacher_id: UUID (FK)
scene: str (prep/slides/quiz/qa)
title: str
system_prompt: str (快照)
created_at: datetime
updated_at: datetime

# teacher_chat_messages
id: UUID
session_id: UUID (FK)
role: str (user/assistant/tool)
content: text
tool_calls: JSON (Function Calling记录)
tool_call_id: str
tokens_used: int
created_at: datetime
```

### 3.3 API设计

```
POST   /api/teacher/chat/sessions          # 创建会话
GET    /api/teacher/chat/sessions          # 会话列表
GET    /api/teacher/chat/sessions/{id}     # 会话详情+消息
DELETE /api/teacher/chat/sessions/{id}     # 删除会话
PATCH  /api/teacher/chat/sessions/{id}     # 重命名会话
POST   /api/teacher/chat/sessions/{id}/messages  # 发送消息（SSE流式）
POST   /api/teacher/chat/sessions/{id}/abort     # 中断生成
```

---

## 四、教案生成改造方案

### 4.1 对话式生成流程

```
老师输入主题（如"椭圆及其标准方程 第1课时"）
    ↓
跳转沉浸式对话界面
    ↓
AI主动询问细节（多轮对话收集需求）：
  1. 教学目标是什么？（知识与技能/过程与方法/情感态度价值观）
  2. 教学重点和难点是什么？
  3. 需要插入什么图片或动画吗？
  4. 需要找什么例题吗？（基础题/提高题/高考题）
  5. 课堂时长多少分钟？
  6. 学生基础如何？（普通班/重点班）
  7. 需要板书设计吗？
  8. 需要布置作业吗？
    ↓
老师回答（可跳过，AI用默认值）
    ↓
AI根据对话内容，并行生成教案各模块：
  ├─ 教学目标（并行）
  ├─ 教学重点与难点（并行）
  ├─ 教学过程（情境引入/概念形成/例题精讲/变式训练/课堂小结/作业布置）（并行）
  ├─ 板书设计（并行）
  ├─ 例题与解析（调用v7_engine精算）（并行）
  └─ 作业设计（并行）
    ↓
SSE实时推送各模块生成进度
    ↓
生成完成，进入教案编辑器（PrepEditor.vue）
    ↓
老师自由编辑（富文本/LaTeX公式）
    ↓
选择导出（Word/PDF）或存放到我的教案
```

### 4.2 并行生成实现

```python
# 后端并行生成
async def generate_lesson_plan(conversation_context: dict):
    tasks = [
        generate_teaching_objectives(context),      # 教学目标
        generate_key_points(context),                # 重难点
        generate_teaching_process(context),          # 教学过程（6环节）
        generate_blackboard_design(context),         # 板书设计
        generate_examples_with_v7(context),          # 例题（v7_engine精算）
        generate_homework(context),                  # 作业设计
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    # 单个模块失败不影响整体，标记degraded
```

### 4.3 复用v7_engine

v7_engine的核心能力：
- **SemExtractor**：从题干抽取数学语义
- **IR Symbolizer**：符号化为数学中间表示（MathIR）
- **SymPy Solver**：符号化精算（圆锥曲线/立体几何/函数/数列/概率）
- **ClaimBuilder**：构建结构化断言
- **WalkthroughGen**：5步讲解生成（读题建模→方法选择→列式推导→得出结论→校验反思）
- **ClaimInjector**：断言写入

**在教案生成中的应用**：
- 例题精讲环节：调用v7_engine生成专业的5步讲解
- 变式训练：基于v7_engine的MathIR生成变式题
- 板书设计：提取v7_engine的结构化断言作为板书要点
- 作业设计：基于知识点和难度生成题目，调用v7_engine验证答案

### 4.4 教案数据结构（复用Artifact）

```python
# TeachingArtifact (已有模型，扩展payload结构)
artifact_type: "lesson_plan"
payload: {
    "topic": str,
    "duration_minutes": int,
    "student_level": str,
    "teaching_objectives": {
        "knowledge": str,
        "process": str,
        "emotion": str
    },
    "key_points": [str],
    "difficult_points": [str],
    "teaching_process": [
        {
            "stage": str (情境引入/概念形成/例题精讲/变式训练/课堂小结/作业布置),
            "duration_minutes": int,
            "content": str,
            "teacher_actions": str,
            "student_actions": str,
            "design_intent": str
        }
    ],
    "examples": [
        {
            "question": str,
            "walkthrough": [str],  # v7_engine 5步讲解
            "answer": str,
            "difficulty": str
        }
    ],
    "blackboard_design": str,
    "homework": [str],
    "references": [str]
}
```

---

## 五、PPT生成改造方案

### 5.1 参考项目架构

**AiPPT（veasion）**：商用级别，服务端未开源，前端可参考
- 输入主题→生成大纲→确认→选择模板→生成PPT
- 支持在线编辑、导出PPTX

**MultiAgentPPT**：开源，多智能体协作
- A2A+MCP+ADK三层架构
- 四个模块：simpleOutline（大纲）、simplePPT（简单生成）、slide_outline（带检索大纲）、slide_agent（并发多Agent生成XML）
- 前端Next.js，PostgreSQL存储

**文多多AIPPT第三代**：DSL语言+AI Agent
- DSL（领域专用语言）连接AI理解和PPT呈现
- Agent方案生成

### 5.2 我们的PPT生成架构

```
老师输入主题（或从教案直通）
    ↓
跳转沉浸式对话界面
    ↓
AI主动询问细节：
  1. 需要多少页？（默认6-10页）
  2. 选择什么模板？（学术蓝/黑板绿/经典藏青/极简白/几何灰/暖橙活力）
  3. 需要插入什么图片吗？
  4. 需要什么例题？（从题库选/AI生成）
  5. 风格偏好？（简约/活泼/严谨）
    ↓
AI生成PPT大纲（每页：标题/页面目标/核心要点/布局类型）
    ↓
老师确认/编辑大纲（增删页面/调整顺序/修改标题）
    ↓
选择模板
    ↓
并行生成各页内容（asyncio.gather）：
  ├─ 第1页：封面（标题/副标题/教师/日期）
  ├─ 第2页：学习目标
  ├─ 第3页：情境引入
  ├─ 第4页：概念定义（含LaTeX公式渲染）
  ├─ 第5页：例题精讲（v7_engine 5步讲解）
  ├─ 第6页：变式训练
  ├─ 第7页：课堂小结
  └─ 第8页：作业布置
    ↓
SSE实时推送每页生成进度
    ↓
生成完成，进入五区编辑器（SlideCanvasV3.vue）
    ↓
老师自由编辑（拖拽/修改/增删页/换模板）
    ↓
导出PPTX/PDF或存放到我的课件
```

### 5.3 DSL中间表示

AI不直接生成PPTX，而是生成结构化DSL（JSON），再由渲染器转换为PPTX/HTML：

```json
{
  "title": "椭圆及其标准方程",
  "template": "academic_blue",
  "slides": [
    {
      "page": 1,
      "layout": "cover",
      "title": "椭圆及其标准方程",
      "subtitle": "高中数学 · 选择性必修第一册",
      "author": "李老师",
      "date": "2026-09-09"
    },
    {
      "page": 2,
      "layout": "content",
      "title": "学习目标",
      "bullets": [
        "理解椭圆的定义",
        "掌握椭圆的标准方程",
        "能根据条件求椭圆的标准方程"
      ]
    },
    {
      "page": 3,
      "layout": "formula",
      "title": "椭圆的标准方程",
      "formula": "\\frac{x^2}{a^2} + \\frac{y^2}{b^2} = 1 \\quad (a > b > 0)",
      "explanation": "焦点在x轴上，焦点坐标为(±c, 0)，其中c² = a² - b²"
    },
    {
      "page": 4,
      "layout": "example",
      "title": "例题精讲",
      "question": "已知椭圆的焦点在x轴上，焦距为8，椭圆上一点到两焦点距离之和为10，求椭圆的标准方程。",
      "walkthrough": [
        "读题建模：焦点在x轴，2c=8，2a=10",
        "方法选择：利用椭圆定义和标准方程",
        "列式推导：c=4, a=5, b²=a²-c²=25-16=9",
        "得出结论：x²/25 + y²/9 = 1",
        "校验反思：a>b>0，符合椭圆条件"
      ],
      "answer": "\\frac{x^2}{25} + \\frac{y^2}{9} = 1"
    }
  ]
}
```

### 5.4 数学公式渲染

- AI生成LaTeX公式
- 前端使用MathJax/KaTeX渲染（编辑器预览）
- 导出PPTX时，LaTeX转换为图片（matplotlib渲染或MathJax截图）
- 复用v7_engine的SymPy验证公式正确性

### 5.5 PPTX导出

- 使用python-pptx库生成PPTX
- 模板库中的每个模板对应一套python-pptx样式（颜色/字体/布局）
- DSL JSON → python-pptx对象 → 保存为PPTX文件
- 支持导出PDF（LibreOffice headless转换）

---

## 六、教材入库与切片方案

### 6.1 SeniorTextbook教材入库

**教材来源**：https://github.com/uiobrokeit/SeniorTextbook.git（高中数学教材）

**入库流程**：
```
克隆/上传教材PDF
    ↓
PDF解析（GROBID + Docling，复用科研端解析管线）
    ↓
教材结构提取：
  ├─ 教材元数据（版本/册次/章节）
  ├─ 章节结构树（章→节→小节）
  ├─ 知识点提取（定义/定理/公式/法则）
  ├─ 题目识别（例题/习题/复习题）
  ├─ 公式提取（LaTeX格式）
  └─ 图表提取（图/表标题+说明）
    ↓
AI辅助标注（LLM审核和补全提取结果）
    ↓
结构化入库：
  ├─ 教材表（textbooks）
  ├─ 章节表（chapters）
  ├─ 知识点表（knowledge_points）
  ├─ 题目表（questions，进入题库）
  ├─ 公式表（formulas）
  └─ 资源表（resources，进入资源库）
    ↓
向量化（pgvector），支持语义检索
    ↓
教材入库完成，可在教案/PPT生成中引用
```

### 6.2 数据模型

```python
# textbooks（教材）
id, title, edition, volume, publisher, total_pages, file_path, parse_status

# chapters（章节）
id, textbook_id, parent_id, level, title, page_start, page_end, sort_order

# knowledge_points（知识点）
id, textbook_id, chapter_id, name, content, type(definition/theorem/formula/rule),
formula_latex, page, difficulty, prerequisites(JSON), embedding(vector)

# questions（题目，复用题库表）
id, textbook_id, chapter_id, knowledge_point_ids(JSON), question_text,
question_type(choice/fill/calculate/proof), difficulty, answer, solution,
source_page, embedding(vector)

# formulas（公式）
id, textbook_id, chapter_id, name, latex, description, page, variables(JSON)
```

### 6.3 切片技术要点

| 技术点 | 方案 |
|---|---|
| **PDF解析** | GROBID（结构）+ Docling（正文/表格），复用科研端解析管线 |
| **章节识别** | 基于标题层级和页码，AI辅助校验 |
| **知识点提取** | LLM提取定义/定理/公式，SymPy验证公式正确性 |
| **题目识别** | 基于题号模式（例1/习题1.1），AI辅助分类题型和难度 |
| **公式提取** | LaTeX OCR（pix2tex或类似），SymPy验证 |
| **语义分块** | 按知识点/小节分块，不按固定长度，保留语义完整性 |
| **向量化** | bge-m3 embedding，存入pgvector |
| **质量校验** | AI审核提取结果，标记置信度，低置信度人工复核 |

### 6.4 教材切片的应用场景

1. **教案生成**：AI检索教材对应章节，确保教案内容与教材一致
2. **PPT生成**：从教材提取定义/定理/例题，直接用于PPT
3. **题目推荐**：从教材习题中推荐相关题目
4. **知识点检索**：老师搜索知识点，返回教材原文位置和解释
5. **备课参考**：老师备课时查看教材对应章节内容

---

## 七、PPT模板库方案

### 7.1 模板来源

**ppt-templates**：https://github.com/wuhua2026/ppt-templates.git

### 7.2 模板入库流程

```
克隆模板仓库
    ↓
扫描模板文件（.pptx/.ppt）
    ↓
提取模板元数据：
  ├─ 模板名称
  ├─ 风格分类（学术风/手写板书/简约/公开课/活泼/商务）
  ├─ 配色方案（主色/辅助色/背景色）
  ├─ 字体方案（标题字体/正文字体）
  ├─ 布局类型（封面/目录/内容/图文/公式/例题/总结/结束）
  └─ 预览图（首页截图）
    ↓
模板文件存入对象存储
    ↓
模板元数据入库（slide_templates表）
    ↓
模板可在PPT生成时选择使用
```

### 7.3 模板数据模型

```python
# slide_templates（PPT模板）
id, name, category, style, color_scheme(JSON), font_scheme(JSON),
layout_types(JSON), preview_image_path, template_file_path,
slide_count, is_active, created_at
```

### 7.4 模板与DSL渲染的对应

每个模板对应一套渲染规则：
- 封面布局：标题位置/字体/颜色
- 内容布局：标题位置/正文位置/项目符号样式
- 公式布局：公式居中/字号/颜色
- 例题布局：题目区/解答区/答案区位置
- 配色：主色/辅助色/背景色/文字色
- 字体：标题字体/正文字体/公式字体

生成时，DSL JSON + 模板ID → python-pptx渲染 → PPTX文件

---

## 八、前端样式统一方案

### 8.1 统一基准

以**备课中心首页（PrepCenterView.vue）**的样式为基准：
- 渐变背景（浅紫/浅蓝渐变）
- 大标题+副标题（"今天想备什么课？"）
- 圆角输入框（大圆角，柔和阴影）
- 卡片式入口（4个入口卡片：从教材开始/上传资料/选择模板/继续备课）
- 紫色主色调（与教师端品牌一致）

### 8.2 统一设计Token

```css
:root {
  /* 颜色 */
  --teacher-primary: #6366f1;        /* 主色：紫 */
  --teacher-primary-light: #e0e7ff;  /* 主色浅 */
  --teacher-secondary: #8b5cf6;      /* 辅助色：紫蓝 */
  --teacher-accent: #f59e0b;         /* 强调色：橙 */
  --teacher-success: #10b981;
  --teacher-warning: #f59e0b;
  --teacher-danger: #ef4444;
  --teacher-bg: #f8fafc;
  --teacher-bg-gradient: linear-gradient(135deg, #f0f4ff 0%, #faf5ff 100%);
  --teacher-text: #1e293b;
  --teacher-text-secondary: #64748b;
  --teacher-border: #e2e8f0;

  /* 圆角 */
  --teacher-radius-sm: 8px;
  --teacher-radius-md: 12px;
  --teacher-radius-lg: 16px;
  --teacher-radius-xl: 24px;

  /* 间距 */
  --teacher-space-xs: 4px;
  --teacher-space-sm: 8px;
  --teacher-space-md: 16px;
  --teacher-space-lg: 24px;
  --teacher-space-xl: 32px;

  /* 阴影 */
  --teacher-shadow-sm: 0 1px 2px rgba(0,0,0,0.05);
  --teacher-shadow-md: 0 4px 6px rgba(0,0,0,0.07);
  --teacher-shadow-lg: 0 10px 15px rgba(0,0,0,0.1);

  /* 字体 */
  --teacher-font-title: "PingFang SC", "Microsoft YaHei", sans-serif;
  --teacher-font-body: "PingFang SC", "Microsoft YaHei", sans-serif;
  --teacher-font-mono: "JetBrains Mono", "Fira Code", monospace;
}
```

### 8.3 需要统一的页面

| 页面 | 当前问题 | 改造方向 |
|---|---|---|
| 课件工坊（SlidesView） | 白底+工具栏，与备课中心风格不一致 | 统一渐变背景+卡片式入口+紫色主色调 |
| 题库（BankView） | 样式未知，可能不一致 | 统一设计Token |
| 组卷中心（QuizView） | 样式未知 | 统一设计Token |
| 作业与批改（AssignView） | 样式未知 | 统一设计Token |
| 资源中心（ResourcesView） | 样式未知 | 统一设计Token |
| 课堂互动（ClassroomView） | 样式未知 | 统一设计Token |
| 学情洞察（InsightsView） | 样式未知 | 统一设计Token |
| 今日工作台（TodayView） | 样式未知 | 统一设计Token |

### 8.4 统一组件

提取通用组件到`src/components/teacherV3/common/`：
- `TeacherPageHeader.vue`：页面头部（标题+副标题+操作按钮）
- `TeacherCard.vue`：卡片容器
- `TeacherButton.vue`：按钮（主/次/文字/危险）
- `TeacherInput.vue`：输入框
- `TeacherEmptyState.vue`：空状态（插图+引导文案+操作按钮）
- `TeacherLoading.vue`：加载状态（骨架屏/Spinner）
- `TeacherModal.vue`：弹窗
- `TeacherToast.vue`：提示
- `TeacherProgressBar.vue`：进度条

---

## 九、后端真实生成实现方案

### 9.1 不依赖星辰工作流

现有`workflow_adapter.py`依赖星辰工作流（wf_lesson_plan/wf_ai_ppt），但这些工作流未配置或未验证，导致生成失败。

**改造方案**：
- 保留`workflow_adapter.py`作为**可选通道**（配置了星辰工作流时使用）
- 新增**本地LLM生成通道**作为默认通道，直接调用讯飞星火/DeepSeek
- 通过配置切换通道（`TEACHER_GENERATION_PROVIDER=local|xingchen`）

### 9.2 本地LLM生成架构

```
teacher/
├── generation/
│   ├── __init__.py
│   ├── provider.py           # 生成提供者（local/xingchen切换）
│   ├── prompts/              # 提示词目录（外置，不硬编码）
│   │   ├── lesson_plan/
│   │   │   ├── system.md
│   │   │   ├── objectives.md
│   │   │   ├── key_points.md
│   │   │   ├── teaching_process.md
│   │   │   ├── blackboard.md
│   │   │   ├── examples.md
│   │   │   └── homework.md
│   │   ├── slides/
│   │   │   ├── system.md
│   │   │   ├── outline.md
│   │   │   ├── cover.md
│   │   │   ├── content.md
│   │   │   ├── formula.md
│   │   │   └── example.md
│   │   └── chat/
│   │       ├── prep.md
│   │       ├── slides.md
│   │       ├── quiz.md
│   │       └── qa.md
│   ├── lesson_plan.py        # 教案生成（并行）
│   ├── slides.py             # PPT生成（并行+DSL）
│   ├── slides_renderer.py    # DSL→PPTX渲染
│   ├── textbook_parser.py    # 教材解析与切片
│   └── template_manager.py   # 模板管理
├── chat/
│   ├── __init__.py
│   ├── session.py            # 会话管理
│   ├── message.py            # 消息管理
│   └── stream.py             # SSE流式输出
└── tools/
    ├── __init__.py
    ├── registry.py           # 工具注册表（Function Calling）
    ├── textbook_search.py    # 教材检索工具
    ├── knowledge_search.py   # 知识点检索工具
    ├── question_search.py    # 题库检索工具
    ├── question_generate.py  # 题目生成工具
    └── formula_verify.py     # 公式验证工具（SymPy）
```

### 9.3 提示词外置原则

- 所有提示词存为Markdown文件，使用Jinja2模板渲染变量
- 提示词变更不需要改代码，只改文件
- 支持提示词版本管理
- 系统提示词和用户提示词分离

### 9.4 Function Calling工具注册

AI对话和生成过程中，AI可以调用以下工具：

| 工具名 | 功能 | 参数 |
|---|---|---|
| `search_textbook` | 检索教材内容 | query, chapter, page |
| `search_knowledge` | 检索知识点 | query, difficulty |
| `search_questions` | 检索题库 | knowledge_point, type, difficulty, count |
| `generate_question` | 生成题目 | knowledge_point, type, difficulty |
| `verify_formula` | 验证公式正确性 | formula_latex |
| `generate_walkthrough` | 生成5步讲解（v7_engine） | question |
| `search_templates` | 搜索PPT模板 | style, category |

### 9.5 SSE流式输出

- 所有生成接口支持SSE流式输出
- 事件类型：`token`（文本块）/`progress`（进度）/`module_complete`（模块完成）/`tool_call`（工具调用）/`error`（错误）/`done`（完成）
- 前端复用`src/api/sse.js`封装

---

## 十、去除硬编码和虚假数据方案

### 10.1 删除Mock文件

| 文件 | 操作 | 说明 |
|---|---|---|
| `src/mock/teacherV3Data.ts` | 删除 | 假数据 |
| `src/mock/teacherV3Server.ts` | 删除 | mock服务 |
| `src/mock/teacherV3ClassroomServer.ts` | 删除 | 课堂mock |
| 组件中的`import mock from '@/mock/...'` | 替换为真实API调用 | 所有引用 |

### 10.2 空状态真实引导

所有空列表/空数据页面，显示真实引导，不填充假数据：

| 页面 | 空状态引导 |
|---|---|
| 我的课件 | "还没有课件，点击'生成新课件'开始创建" + 按钮 |
| 我的教案 | "还没有教案，点击'生成新教案'开始创建" + 按钮 |
| 题库 | "题库为空，上传教材自动切片入库，或手动录入题目" + 按钮 |
| 资源中心 | "资源库为空，上传教材/课件/模板，或从公共资源库添加" + 按钮 |
| 为你推荐 | 基于教师真实数据推荐（最近备课/常用知识点），无数据时隐藏 |

### 10.3 配置外置检查清单

- [ ] 所有API base URL从环境变量读取
- [ ] 所有模型ID/API Key从环境变量读取
- [ ] 所有提示词存为文件，不硬编码在代码中
- [ ] 所有常量（页数默认值/超时时间/重试次数）集中定义
- [ ] 所有功能开关通过配置控制
- [ ] 前端所有路由路径集中定义，不硬编码
- [ ] 前端所有API路径集中在`src/api/`，不散落组件中

---

## 十一、技术架构与代码位置

### 11.1 后端代码位置

**新增代码**（D:\math-arena\services\api\app\domains\teacher\）：
```
teacher/
├── generation/              # 新增：生成模块
│   ├── provider.py
│   ├── prompts/             # 提示词文件
│   ├── lesson_plan.py
│   ├── slides.py
│   ├── slides_renderer.py
│   ├── textbook_parser.py
│   └── template_manager.py
├── chat/                    # 新增：对话模块
│   ├── session.py
│   ├── message.py
│   └── stream.py
├── tools/                   # 新增：工具注册
│   ├── registry.py
│   ├── textbook_search.py
│   ├── knowledge_search.py
│   ├── question_search.py
│   ├── question_generate.py
│   └── formula_verify.py
├── router.py                # 修改：新增对话/生成/教材/模板路由
├── lessons.py               # 修改：替换本地降级为真实LLM生成
├── workflow_adapter.py      # 保留：作为可选通道
└── artifacts.py             # 保留：复用Artifact系统
```

**新增数据模型**（D:\math-arena\services\api\app\models\）：
- `teacher_chat_session.py`：对话会话
- `teacher_chat_message.py`：对话消息
- `textbook.py`：教材/章节/知识点/公式
- `slide_template.py`：PPT模板

**新增数据库迁移**（Alembic）：
- 新建上述表的迁移脚本

### 11.2 前端代码位置

**修改代码**（D:\frontend\src\pages\teacher-v3\）：
- `PrepCenterView.vue`：作为样式基准，接入真实API，去除mock
- `PrepGenerating.vue`：修改为对话式生成流程
- `PrepOutlineConfirm.vue`：接入真实大纲生成
- `PrepEditor.vue`：接入真实教案数据
- `SlidesView.vue`：统一样式，接入真实PPT生成
- `SlideCanvasV3.vue`：复用，接入真实PPT数据
- `BankView.vue`：统一样式，接入真实题库
- `ResourcesView.vue`：统一样式，接入真实资源库

**新增代码**：
- `src/components/teacherV3/chat/`：对话组件
  - `ChatInterface.vue`：ChatGPT式对话界面
  - `ChatMessage.vue`：消息气泡
  - `ChatInput.vue`：底部输入框
  - `ChatSessionList.vue`：会话列表
- `src/components/teacherV3/common/`：通用组件（统一设计Token）
- `src/stores/teacherChat.ts`：对话状态管理（Pinia）
- `src/api/teacherChat.ts`：对话API封装
- `src/api/teacherGeneration.ts`：生成API封装
- `src/api/teacherTextbook.ts`：教材API封装
- `src/api/teacherTemplate.ts`：模板API封装

**删除代码**：
- `src/mock/teacherV3Data.ts`
- `src/mock/teacherV3Server.ts`
- `src/mock/teacherV3ClassroomServer.ts`

### 11.3 复用现有资产

| 资产 | 位置 | 复用方式 |
|---|---|---|
| Artifact系统 | `teacher/artifacts.py` | 教案/PPT存储和版本管理 |
| v7_engine | `classroom/v7_engine.py` | 例题5步讲解、公式验证、变式题生成 |
| 五区编辑器 | `components/teacherV3/SlideCanvasV3.vue` | PPT编辑 |
| SSE封装 | `api/sse.js` | 流式输出 |
| 教师端API | `api/teacherV3.ts` | 扩展新接口 |
| 认证系统 | `gateway/auth.py` | 复用JWT认证 |
| 数据库 | PostgreSQL + asyncpg | 新增表 |
| Redis | 现有 | 缓存/限流 |
| 对象存储 | 现有boto3抽象 | 教材/模板/PPTX文件存储 |

---

## 十二、实施路线图

### 阶段1：基础改造（1-2周）

**目标**：去除mock，统一前端样式，实现真实AI对话

- [ ] 删除所有mock文件和假数据
- [ ] 统一前端设计Token（CSS变量）
- [ ] 提取通用组件（按钮/卡片/输入框/空状态/加载）
- [ ] 统一所有页面样式（以备课中心为基准）
- [ ] 后端新增对话模块（会话/消息/SSE流式）
- [ ] 前端实现ChatGPT式对话界面
- [ ] 实现多轮上下文记忆和历史会话
- [ ] 空状态真实引导（不填充假数据）

### 阶段2：教案生成（2-3周）

**目标**：对话式教案生成，并行产出，v7_engine复用

- [ ] 后端新增教案生成模块（提示词外置）
- [ ] 实现并行生成（教学目标/重难点/教学过程/板书/例题/作业）
- [ ] 复用v7_engine生成例题5步讲解
- [ ] 实现SSE实时推送生成进度
- [ ] 前端实现对话式生成流程（AI询问细节→生成→编辑）
- [ ] 教案编辑器接入真实数据
- [ ] 教案导出（Word/PDF）
- [ ] 教案存放到我的教案（Artifact系统）

### 阶段3：PPT生成（2-3周）

**目标**：DSL中间表示，并行生成，模板库，数学公式渲染

- [ ] 后端新增PPT生成模块（DSL JSON中间表示）
- [ ] 实现大纲生成→确认→并行生成各页
- [ ] 实现DSL→PPTX渲染（python-pptx）
- [ ] 数学公式LaTeX渲染（编辑器预览+导出图片）
- [ ] 复用v7_engine生成例题页
- [ ] 前端实现PPT生成流程（对话→大纲→模板→生成→编辑）
- [ ] 五区编辑器接入真实PPT数据
- [ ] PPT导出（PPTX/PDF）
- [ ] PPT存放到我的课件（Artifact系统）

### 阶段4：教材入库与模板库（2周）

**目标**：SeniorTextbook教材切片入库，ppt-templates模板入库

- [ ] 后端新增教材解析模块（GROBID+Docling）
- [ ] 实现章节/知识点/题目/公式提取
- [ ] AI辅助标注和质量校验
- [ ] 向量化（pgvector），支持语义检索
- [ ] SeniorTextbook教材批量入库
- [ ] 后端新增模板管理模块
- [ ] ppt-templates模板批量入库
- [ ] 前端实现教材浏览/检索/引用
- [ ] 前端实现模板选择/预览
- [ ] 教案/PPT生成中引用教材内容和模板

### 阶段5：优化与完善（1-2周）

**目标**：性能优化，质量提升，用户体验打磨

- [ ] 生成质量评估和优化
- [ ] 提示词调优
- [ ] 并行生成性能优化
- [ ] 缓存策略（生成结果缓存/教材内容缓存）
- [ ] 错误处理和降级机制
- [ ] 用户体验打磨（动画/交互/反馈）
- [ ] 全面测试（单元测试/集成测试/E2E测试）
- [ ] 文档编写（开发文档/用户手册）

---

## 十三、关键风险与应对

| 风险 | 影响 | 应对 |
|---|---|---|
| LLM生成质量不稳定 | 教案/PPT内容质量差 | 提示词调优+人工编辑+v7_engine数学验证+多轮对话收集需求 |
| 并行生成部分失败 | 教案/PPT不完整 | 单模块失败标记degraded，不影响整体，可单独重试 |
| 教材解析准确率低 | 知识点/题目提取错误 | AI辅助标注+人工复核+置信度标记+低置信度人工审核 |
| PPTX导出格式问题 | 导出的PPT排版错乱 | 模板严格测试+DSL渲染器完善+python-pptx最佳实践 |
| 数学公式渲染 | LaTeX公式在PPT中显示异常 | MathJax/KaTeX预览+matplotlib渲染图片导出+SymPy验证 |
| 前端改造范围大 | 样式统一工作量大 | 分阶段推进，先统一设计Token，再逐页改造 |
| 去除mock后数据空 | 用户体验差 | 真实空状态引导+教材入库+模板入库填充基础数据 |

---

## 十四、验收标准

### 14.1 AI对话
- [ ] ChatGPT式对话界面（输入框底部固定/消息流式上浮/自动滚动）
- [ ] 多轮上下文记忆（最近10轮）
- [ ] 历史会话列表（可切换/新建/删除/重命名）
- [ ] SSE流式输出（打字机效果）
- [ ] 支持中断生成
- [ ] 4个场景（备课/课件/题目/答疑）

### 14.2 教案生成
- [ ] 对话式生成（AI主动询问细节）
- [ ] 并行生成（6个模块同时生成）
- [ ] SSE实时推送进度
- [ ] 复用v7_engine生成例题5步讲解
- [ ] 生成后可自由编辑
- [ ] 导出Word/PDF
- [ ] 存放到我的教案

### 14.3 PPT生成
- [ ] 对话式生成（AI询问页数/模板/风格/例题）
- [ ] 大纲生成→确认→编辑
- [ ] 模板选择（至少6个模板）
- [ ] 并行生成各页
- [ ] DSL中间表示
- [ ] 数学公式LaTeX渲染
- [ ] 五区编辑器自由编辑
- [ ] 导出PPTX/PDF
- [ ] 存放到我的课件

### 14.4 教材入库
- [ ] SeniorTextbook教材入库
- [ ] 章节结构提取
- [ ] 知识点提取（定义/定理/公式）
- [ ] 题目识别和入库
- [ ] 公式提取和验证
- [ ] 语义检索（pgvector）
- [ ] 教案/PPT生成中可引用教材

### 14.5 模板库
- [ ] ppt-templates模板入库
- [ ] 模板分类（风格/配色）
- [ ] 模板预览
- [ ] PPT生成时可选模板

### 14.6 前端统一
- [ ] 所有页面统一样式（设计Token）
- [ ] 无mock数据
- [ ] 无假数据填充
- [ ] 空状态真实引导
- [ ] 通用组件复用

### 14.7 代码质量
- [ ] 提示词外置（不硬编码）
- [ ] 配置外置（环境变量）
- [ ] 无硬编码API路径
- [ ] 无硬编码路由
- [ ] 错误处理完善
- [ ] 日志规范

---

> **总结**：教师端改造的核心是从"原型展示"变为"真实可用"。关键路径是：**AI对话真实化→教案生成对话化→PPT生成专业化→教材入库结构化→模板库可复用→前端样式统一→全链路真实后端**。现有资产（Artifact系统/v7_engine/五区编辑器/SSE封装）全部复用，只改生成逻辑和数据层，避免重写。
