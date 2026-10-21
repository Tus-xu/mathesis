# 智学数研·教师端通宵全栈升级开发提示词

> **执行模式**：自主开发，一晚上完成全部任务。你可以创建子智能体并行工作。遇到技术问题自行调研解决，不要等待人类指示。最终交付可运行的前后端代码 + 视觉验收报告。

---

## 0. 你的身份与任务

你是一名**全栈架构师 + 资深前端工程师 + 资深后端工程师 + AI应用开发专家**，正在对"智学数研"教师端进行从"半桶水原型"到"真实可用AI教研平台"的全面升级。

**核心目标**：去除所有mock和假数据，实现真实AI对话、对话式教案生成、专业PPT生成、教材入库切片、模板库复用，前端样式全面统一。

**时间预算**：一晚上（约8小时）。你必须在这个时间内完成全部开发、测试和验证。

**自主原则**：
- 遇到问题自行调研、自行决策、自行解决
- 可以创建子智能体并行处理独立模块
- 技术选型以"能跑通、质量高、可维护"为标准
- 宁可功能少而精，不要多而假
- 所有生成内容必须真实，禁止任何mock数据

---

## 1. 现有代码结构（精确路径）

### 1.1 后端

**根目录**：`D:\math-arena\services\api\`

**教师域**：`app\domains\teacher\`
```
teacher/
├── router.py                  # 教师端路由（/api/teacher），含教案/PPT/题目/批改/资源
├── workflow_adapter.py        # 星辰工作流适配器（可选通道，不依赖）
├── lessons.py                 # 教案生成（需替换为真实LLM生成）
├── artifacts.py               # Artifact版本状态机（完整复用，不要改）
├── capability_gateway.py      # 能力网关（可复用）
├── registry.py                # 教师端注册表（可复用）
├── resources.py               # 资源管理
├── assessment.py              # 评估
├── grading.py                 # 批改
├── insights.py                # 学情洞察
├── classroom.py               # 课堂
├── classroom_session.py       # 课堂会话
├── today.py                   # 今日
├── schemas.py                 # 数据模型
└── scope.py                   # 权限范围
```

**双师课堂域（核心复用资产）**：`app\domains\classroom\`
```
classroom/
├── v7_engine.py               # ⭐ V7通用数学建模引擎（必须复用）
│   ├── SemExtractor           # 语义抽取
│   ├── IR Symbolizer          # 符号化（MathIR中间表示）
│   ├── SymPy Solver           # 符号化精算
│   ├── ClaimBuilder           # 结构化断言构建
│   ├── WalkthroughGen         # 5步讲解生成（读题建模→方法选择→列式推导→得出结论→校验反思）
│   └── ClaimInjector          # 断言写入
├── v6_engine.py               # V6旧引擎
├── rag_orchestrator.py        # RAG编排器
├── math_verifier.py           # 数学验证器
└── stage_router.py            # 阶段路由
```

**数据模型**：`app\models\teacher.py`（TeacherTask, TeachingArtifact等）

**认证**：`app\gateway\auth.py`（JWT认证，复用）

**数据库**：PostgreSQL + asyncpg + SQLAlchemy async + Alembic迁移

**缓存**：Redis（现有连接）

**对象存储**：boto3抽象（现有）

### 1.2 前端

**根目录**：`D:\frontend\`

**教师端页面**：`src\pages\teacher-v3\`
```
teacher-v3/
├── TodayView.vue              # 今日工作台
├── PrepCenterView.vue         # ⭐ 备课中心首页（样式最好，作为统一基准）
├── SlidesView.vue             # 课件工坊首页（需统一样式）
├── BankView.vue               # 题库（需统一样式+真实数据）
├── QuizView.vue               # 组卷中心
├── AssignView.vue             # 作业与批改
├── ClassroomView.vue          # 课堂互动
├── InsightsView.vue           # 学情洞察
├── ResourcesView.vue          # 资源中心（需统一样式+真实数据）
├── prep/
│   ├── PrepHome.vue
│   ├── PrepUpload.vue         # 上传资料备课
│   ├── PrepGenerating.vue     # 教案生成进度（需改为对话式）
│   ├── PrepOutlineConfirm.vue # 大纲确认
│   ├── PrepTemplatesView.vue  # 模板选择
│   ├── PrepEditor.vue         # 教案编辑器
│   ├── PrepFromTextbook.vue   # 从教材开始
│   ├── PrepDeskHome.vue
│   └── PrepDeskWorkspace.vue
```

**教师端组件**：`src\components\teacherV3\`
```
teacherV3/
├── SlideCanvasV3.vue          # ⭐ 五区编辑器（完整复用）
├── AiProcessingPanel.vue      # AI处理进度面板
├── ButlerPanel.vue            # AI管家面板
├── ButlerFab.vue              # 悬浮球
├── TeacherCompanionDock.vue   # 教师伴侣停靠栏
├── BriefComposer.vue          # 简报编辑器
├── PhotoScanWizard.vue        # 拍照扫描向导
├── ResourceUploadPanel.vue    # 资源上传面板
├── ResourceCompanionPanel.vue # 资源伴侣面板
└── WorkshopFlow.vue           # 工作流
```

**API封装**：
- `src\api\teacherV3.ts` — 教师端API
- `src\api\teacherV3Upload.ts` — 上传API
- `src\api\sse.js` — SSE流式封装（复用）
- `src\api\client.js` — HTTP客户端

**状态管理**：`src\stores\teacherContext.ts`（Pinia）

**Mock文件（必须删除）**：
- `src\mock\teacherV3Data.ts` — 假数据
- `src\mock\teacherV3Server.ts` — mock服务
- `src\mock\teacherV3ClassroomServer.ts` — 课堂mock

### 1.3 参考项目（需clone学习）

```bash
# 克隆到 D:\math-arena\reference-repos\
git clone https://github.com/veasion/AiPPT.git          # 商用AI PPT（参考前端）
git clone https://github.com/wuhua2026/ppt-templates.git # PPT模板库
git clone https://github.com/uiobrokeit/SeniorTextbook.git # 高中数学教材
```

---

## 2. 可复用资产（禁止重写）

| 资产 | 位置 | 复用方式 |
|---|---|---|
| Artifact版本状态机 | `teacher/artifacts.py` | 教案/PPT存储和版本管理，直接调用 |
| v7_engine数学引擎 | `classroom/v7_engine.py` | 例题5步讲解、公式验证、变式题生成，import调用 |
| 五区编辑器 | `components/teacherV3/SlideCanvasV3.vue` | PPT编辑，传入真实数据即可 |
| SSE封装 | `api/sse.js` | 流式输出，直接调用 |
| 教师端路由框架 | `teacher/router.py` | 扩展新接口，不重写 |
| JWT认证 | `gateway/auth.py` | 复用，新接口加`require_verified_teacher` |
| 数据库连接 | 现有asyncpg/SQLAlchemy | 新增表用Alembic迁移 |
| Redis缓存 | 现有连接 | 生成结果缓存/限流 |
| 对象存储 | 现有boto3抽象 | 教材/模板/PPTX文件存储 |
| 备课中心首页样式 | `PrepCenterView.vue` | 作为所有页面样式基准 |

---

## 3. API密钥与配置

### 3.1 大模型API

**讯飞星火（文字模型）**：
- Model ID: `spark-x2.5-4b`
- OpenAI兼容URL: `https://maas-api.cn-huabei-1.xf-yun.com/v2`
- API Key: `ak-***MASKED-ROTATE-ME***`

**讯飞星火（图像理解模型）**：
- Model ID: `xophunyuanocr`
- OpenAI兼容URL: `https://maas-api.cn-huabei-1.xf-yun.com/v2`
- API Key: `ak-***MASKED-ROTATE-ME***`

**博查搜索API**：
- API Key: `sk-***MASKED-ROTATE-ME***`

### 3.2 配置方式

所有密钥和配置写入环境变量或`.env`文件，**禁止硬编码在代码中**：

```env
# .env (D:\math-arena\services\api\.env)
LLM_PROVIDER=xfyun
LLM_MODEL=spark-x2.5-4b
LLM_BASE_URL=https://maas-api.cn-huabei-1.xf-yun.com/v2
LLM_API_KEY=***MASKED-ROTATE-ME***
VISION_MODEL=xophunyuanocr
SEARCH_API_KEY=***MASKED-ROTATE-ME***
TEACHER_GENERATION_PROVIDER=local
```

前端配置写入`src/config/index.ts`或环境变量，禁止硬编码API路径。

---

## 4. 开发任务清单（分阶段，必须全部完成）

### 阶段1：基础清理与前端统一（约1.5小时）

#### 1.1 删除Mock
- [ ] 删除 `src/mock/teacherV3Data.ts`
- [ ] 删除 `src/mock/teacherV3Server.ts`
- [ ] 删除 `src/mock/teacherV3ClassroomServer.ts`
- [ ] 全局搜索`from '@/mock/`或`import mock`，所有引用替换为真实API调用
- [ ] 全局搜索硬编码的假数据（如"双曲线及其标准方程"重复出现6次），全部删除

#### 1.2 前端设计Token统一
- [ ] 创建 `src/styles/teacher-tokens.css`，定义统一CSS变量（见第5节视觉标准）
- [ ] 在`main.ts`或`App.vue`中全局引入
- [ ] 创建通用组件目录 `src/components/teacherV3/common/`：
  - `TeacherButton.vue`（主/次/文字/危险四种样式）
  - `TeacherCard.vue`（卡片容器）
  - `TeacherInput.vue`（输入框）
  - `TeacherEmptyState.vue`（空状态：插图+引导文案+操作按钮）
  - `TeacherLoading.vue`（骨架屏/Spinner）
  - `TeacherModal.vue`（弹窗）
  - `TeacherProgressBar.vue`（进度条）
  - `TeacherPageHeader.vue`（页面头部：标题+副标题+操作按钮）

#### 1.3 页面样式统一
以`PrepCenterView.vue`的样式为基准，统一以下页面：
- [ ] `SlidesView.vue`（课件工坊）— 改为渐变背景+大标题+卡片式入口
- [ ] `BankView.vue`（题库）— 统一样式
- [ ] `QuizView.vue`（组卷中心）— 统一样式
- [ ] `AssignView.vue`（作业与批改）— 统一样式
- [ ] `ResourcesView.vue`（资源中心）— 统一样式
- [ ] `ClassroomView.vue`（课堂互动）— 统一样式
- [ ] `InsightsView.vue`（学情洞察）— 统一样式
- [ ] `TodayView.vue`（今日工作台）— 统一样式

#### 1.4 空状态真实引导
所有空列表页面，显示真实引导，**禁止填充假数据**：
- [ ] 我的课件空 → "还没有课件，点击'生成新课件'开始创建" + 按钮
- [ ] 我的教案空 → "还没有教案，点击'生成新教案'开始创建" + 按钮
- [ ] 题库空 → "题库为空，上传教材自动切片入库，或手动录入题目" + 按钮
- [ ] 资源中心空 → "资源库为空，上传教材/课件/模板" + 按钮
- [ ] "为你推荐" → 基于教师真实数据推荐，无数据时隐藏整个模块

---

### 阶段2：AI对话真实化（约2小时）

#### 2.1 后端对话模块
创建 `app/domains/teacher/chat/`：

- [ ] `session.py` — 会话管理（CRUD）
- [ ] `message.py` — 消息管理（存储/查询）
- [ ] `stream.py` — SSE流式输出
- [ ] `__init__.py`

**数据模型**（新增Alembic迁移）：
```python
# teacher_chat_sessions
id: UUID
teacher_id: UUID
scene: str  # prep/slides/quiz/qa
title: str
system_prompt: str  # 快照
created_at, updated_at

# teacher_chat_messages
id: UUID
session_id: UUID
role: str  # user/assistant/tool
content: text
tool_calls: JSON
tool_call_id: str
tokens_used: int
created_at
```

**API接口**：
```
POST   /api/teacher/chat/sessions              # 创建会话
GET    /api/teacher/chat/sessions              # 会话列表
GET    /api/teacher/chat/sessions/{id}         # 会话详情+消息
DELETE /api/teacher/chat/sessions/{id}         # 删除会话
PATCH  /api/teacher/chat/sessions/{id}         # 重命名
POST   /api/teacher/chat/sessions/{id}/messages  # 发送消息（SSE流式）
POST   /api/teacher/chat/sessions/{id}/abort     # 中断生成
```

**对话逻辑**：
- 每次请求携带最近10轮上下文（可配置）
- 系统提示词按场景切换（4个场景：备课/课件/题目/答疑）
- 支持Function Calling（工具注册见阶段4）
- SSE流式输出token
- 消息持久化到数据库

#### 2.2 前端对话组件
创建 `src/components/teacherV3/chat/`：

- [ ] `ChatInterface.vue` — ChatGPT式对话主界面
- [ ] `ChatMessage.vue` — 消息气泡（用户/AI/工具调用三种样式）
- [ ] `ChatInput.vue` — 底部输入框（固定底部，大圆角，发送按钮）
- [ ] `ChatSessionList.vue` — 左侧会话列表（可折叠）
- [ ] `index.ts` — 导出

**交互要求**：
- 输入框固定在页面底部，发送后保持在底部
- 消息区域自动滚动到底部，新消息流式上浮（打字机效果）
- 发送后输入框清空，保持焦点
- Shift+Enter换行，Enter发送
- 支持中断生成（停止按钮，在AI回复时显示）
- 支持Markdown渲染（代码块/公式/列表）
- 支持LaTeX公式渲染（KaTeX）

**状态管理**：
- [ ] 创建 `src/stores/teacherChat.ts`（Pinia）
  - 当前会话
  - 消息列表
  - 发送中状态
  - 中断控制器

**API封装**：
- [ ] 创建 `src/api/teacherChat.ts`

#### 2.3 场景化系统提示词
创建 `app/domains/teacher/chat/prompts/`：
- [ ] `prep.md` — 备课助手（高中数学教师，帮助设计教案，主动询问教学目标/重难点/例题/时长）
- [ ] `slides.md` — 课件助手（帮助设计PPT，主动询问页数/模板/风格/例题）
- [ ] `quiz.md` — 题目助手（帮助出题组卷，主动询问知识点/题型/难度/数量）
- [ ] `qa.md` — 答疑助手（解答教学疑问，提供教学建议）

提示词用Jinja2模板渲染，支持变量注入。

---

### 阶段3：教案生成对话化（约2小时）

#### 3.1 后端教案生成模块
创建 `app/domains/teacher/generation/`：

- [ ] `provider.py` — 生成提供者（local/xingchen切换，默认local）
- [ ] `lesson_plan.py` — 教案生成（并行）
- [ ] `prompts/lesson_plan/` — 提示词文件
  - `system.md`
  - `objectives.md` — 教学目标
  - `key_points.md` — 重难点
  - `teaching_process.md` — 教学过程
  - `blackboard.md` — 板书设计
  - `examples.md` — 例题
  - `homework.md` — 作业设计

**并行生成逻辑**：
```python
async def generate_lesson_plan(context: dict):
    tasks = [
        generate_objectives(context),       # 教学目标
        generate_key_points(context),         # 重难点
        generate_teaching_process(context),   # 教学过程（6环节）
        generate_blackboard(context),         # 板书设计
        generate_examples_with_v7(context),   # 例题（调用v7_engine）
        generate_homework(context),           # 作业设计
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    # 单模块失败标记degraded，不影响整体
```

**复用v7_engine**：
- 例题精讲环节：调用`v7_engine.run_postprocess_pipeline()`生成5步讲解
- 变式训练：基于MathIR生成变式题
- 答案验证：SymPy验证

**教案数据结构**（存入TeachingArtifact，artifact_type="lesson_plan"）：
```json
{
  "topic": "椭圆及其标准方程",
  "duration_minutes": 45,
  "student_level": "普通班",
  "teaching_objectives": {"knowledge": "...", "process": "...", "emotion": "..."},
  "key_points": ["..."],
  "difficult_points": ["..."],
  "teaching_process": [
    {"stage": "情境引入", "duration_minutes": 5, "content": "...", "teacher_actions": "...", "student_actions": "...", "design_intent": "..."},
    {"stage": "概念形成", "duration_minutes": 10, "...": "..."},
    {"stage": "例题精讲", "duration_minutes": 12, "...": "..."},
    {"stage": "变式训练", "duration_minutes": 8, "...": "..."},
    {"stage": "课堂小结", "duration_minutes": 5, "...": "..."},
    {"stage": "作业布置", "duration_minutes": 5, "...": "..."}
  ],
  "examples": [{"question": "...", "walkthrough": ["...5步..."], "answer": "...", "difficulty": "基础"}],
  "blackboard_design": "...",
  "homework": ["..."],
  "references": ["..."]
}
```

**API接口**：
```
POST /api/teacher/generation/lesson-plan/start    # 开始生成（返回task_id）
GET  /api/teacher/generation/lesson-plan/{task_id} # 查询进度（SSE或轮询）
POST /api/teacher/generation/lesson-plan/{id}/save # 保存到我的教案
```

#### 3.2 前端教案生成流程
改造现有页面为对话式流程：

- [ ] `PrepCenterView.vue` — 首页输入主题后，跳转到对话界面（不是直接生成）
- [ ] 新建 `prep/PrepChatDialog.vue` — 教案生成对话界面（复用ChatInterface，场景=prep）
  - AI主动询问：教学目标？重难点？需要图片？需要例题？时长？学生基础？板书？作业？
  - 老师可回答或跳过
  - 收集足够信息后，AI点击"开始生成教案"按钮
- [ ] `PrepGenerating.vue` — 改为并行生成进度展示（6个模块同时推进，SSE实时更新）
- [ ] `PrepEditor.vue` — 教案编辑器，接入真实数据
  - 富文本编辑
  - LaTeX公式编辑
  - 各模块可折叠/展开
  - 保存按钮（调用Artifact保存）
  - 导出按钮（Word/PDF）

**导出功能**：
- [ ] Word导出：后端用python-docx生成.docx
- [ ] PDF导出：Word→PDF（LibreOffice headless或weasyprint）

---

### 阶段4：PPT生成专业化（约2小时）

#### 4.1 后端PPT生成模块
在 `app/domains/teacher/generation/` 中新增：

- [ ] `slides.py` — PPT生成（大纲→并行生成各页）
- [ ] `slides_renderer.py` — DSL JSON → PPTX渲染（python-pptx）
- [ ] `template_manager.py` — 模板管理
- [ ] `prompts/slides/` — 提示词
  - `system.md`
  - `outline.md` — 大纲生成
  - `cover.md` — 封面
  - `content.md` — 内容页
  - `formula.md` — 公式页
  - `example.md` — 例题页

**DSL中间表示**（AI生成JSON，不直接生成PPTX）：
```json
{
  "title": "椭圆及其标准方程",
  "template": "academic_blue",
  "slides": [
    {"page": 1, "layout": "cover", "title": "...", "subtitle": "...", "author": "...", "date": "..."},
    {"page": 2, "layout": "content", "title": "学习目标", "bullets": ["...", "..."]},
    {"page": 3, "layout": "formula", "title": "椭圆的标准方程", "formula": "\\frac{x^2}{a^2}+\\frac{y^2}{b^2}=1", "explanation": "..."},
    {"page": 4, "layout": "example", "title": "例题精讲", "question": "...", "walkthrough": ["...5步..."], "answer": "..."}
  ]
}
```

**生成流程**：
1. AI生成大纲（每页：标题/页面目标/核心要点/布局类型）
2. 老师确认/编辑大纲
3. 选择模板
4. 并行生成各页内容（asyncio.gather）
5. SSE实时推送每页进度
6. 组装DSL JSON
7. 渲染为PPTX（python-pptx）

**数学公式渲染**：
- 编辑器预览：前端KaTeX/MathJax
- 导出PPTX：LaTeX→matplotlib渲染为PNG，插入PPTX

**PPTX导出**：
- python-pptx生成.pptx
- 每个模板对应一套样式（颜色/字体/布局）
- DSL JSON + 模板ID → python-pptx对象 → .pptx文件

**API接口**：
```
POST /api/teacher/generation/slides/outline     # 生成大纲
POST /api/teacher/generation/slides/start       # 开始生成（大纲+模板）
GET  /api/teacher/generation/slides/{task_id}   # 进度（SSE）
GET  /api/teacher/generation/slides/{id}/pptx   # 下载PPTX
POST /api/teacher/generation/slides/{id}/save   # 保存到我的课件
```

#### 4.2 前端PPT生成流程
- [ ] `SlidesView.vue` — 首页输入主题后跳转对话界面
- [ ] 新建 `slides/SlidesChatDialog.vue` — PPT生成对话（场景=slides）
  - AI询问：多少页？什么模板？需要图片？需要例题？风格？
- [ ] `PrepOutlineConfirm.vue` — 大纲确认页（复用，接入真实大纲生成）
- [ ] `PrepTemplatesView.vue` — 模板选择页（接入真实模板库）
- [ ] 新建 `slides/SlidesGenerating.vue` — 并行生成进度（每页一个进度条）
- [ ] `SlideCanvasV3.vue` — 五区编辑器（复用，传入真实DSL数据）
  - 左侧幻灯片缩略图
  - 中间编辑区
  - 右侧属性面板
  - 顶部工具栏
  - 底部提示
- [ ] 导出PPTX按钮
- [ ] 保存到我的课件按钮

---

### 阶段5：教材入库与切片（约1.5小时）

#### 5.1 后端教材解析模块
在 `app/domains/teacher/generation/` 中新增：

- [ ] `textbook_parser.py` — 教材解析与切片

**解析流程**：
1. PDF上传/克隆SeniorTextbook
2. 文本提取（PyPDF2/pdfplumber，优先用现有解析管线）
3. 章节结构提取（基于标题层级+页码）
4. 知识点提取（LLM提取定义/定理/公式/法则）
5. 题目识别（基于题号模式：例1/习题1.1）
6. 公式提取（LaTeX OCR，可用图像理解模型xophunyuanocr）
7. AI辅助标注（LLM审核补全）
8. 结构化入库
9. 向量化（如果pgvector可用，否则用TF-IDF或简单关键词匹配）

**数据模型**（新增Alembic迁移）：
```python
# textbooks
id, title, edition, volume, publisher, total_pages, file_path, parse_status

# chapters
id, textbook_id, parent_id, level, title, page_start, page_end, sort_order

# knowledge_points
id, textbook_id, chapter_id, name, content, type(definition/theorem/formula/rule),
formula_latex, page, difficulty

# questions（复用题库表，扩展字段）
id, textbook_id, chapter_id, knowledge_point_ids(JSON), question_text,
question_type, difficulty, answer, solution, source_page

# formulas
id, textbook_id, chapter_id, name, latex, description, page
```

**SeniorTextbook入库**：
- 克隆仓库到 `D:\math-arena\reference-repos\SeniorTextbook`
- 扫描所有PDF文件
- 批量解析入库
- 如果PDF解析太慢，至少完成1-2本教材的完整入库作为演示

**API接口**：
```
POST   /api/teacher/textbooks/upload       # 上传教材
POST   /api/teacher/textbooks/{id}/parse   # 开始解析
GET    /api/teacher/textbooks               # 教材列表
GET    /api/teacher/textbooks/{id}/chapters # 章节树
GET    /api/teacher/textbooks/{id}/knowledge # 知识点列表
GET    /api/teacher/textbooks/search        # 语义搜索
```

#### 5.2 前端教材浏览
- [ ] 新建 `pages/teacher-v3/TextbookView.vue` — 教材库页面
  - 教材列表（封面/名称/册次/进度）
  - 点击进入章节树
  - 知识点浏览
  - 搜索框（语义搜索）
- [ ] 在教案/PPT生成对话中，AI可以调用`search_textbook`工具引用教材内容

---

### 阶段6：PPT模板库（约0.5小时）

#### 6.1 后端模板管理
- [ ] `template_manager.py`（阶段4已创建，完善功能）

**模板入库流程**：
1. 克隆ppt-templates到 `D:\math-arena\reference-repos\ppt-templates`
2. 扫描.pptx文件
3. 提取元数据（名称/风格/配色/字体/布局类型）
4. 生成预览图（首页截图，可用python-pptx提取或LibreOffice转换）
5. 模板文件存入对象存储
6. 元数据入库

**数据模型**：
```python
# slide_templates
id, name, category, style, color_scheme(JSON), font_scheme(JSON),
layout_types(JSON), preview_image_path, template_file_path,
slide_count, is_active
```

**至少入库6个模板**（如果ppt-templates格式不兼容，自己创建6个基础模板的python-pptx样式定义）：
- 学术蓝（academic_blue）
- 黑板绿（blackboard_green）
- 经典藏青（classic_navy）
- 极简白（minimal_white）
- 几何灰（geometric_gray）
- 暖橙活力（warm_orange）

#### 6.2 前端模板选择
- [ ] `PrepTemplatesView.vue` — 接入真实模板库
  - 模板网格（预览图+名称+风格标签）
  - 点击选中（高亮边框）
  - 右侧实时预览
  - 确认按钮

---

### 阶段7：Function Calling工具注册（约0.5小时）

在 `app/domains/teacher/tools/` 创建：

- [ ] `registry.py` — 工具注册表
- [ ] `textbook_search.py` — 教材检索
- [ ] `knowledge_search.py` — 知识点检索
- [ ] `question_search.py` — 题库检索
- [ ] `question_generate.py` — 题目生成
- [ ] `formula_verify.py` — 公式验证（SymPy）
- [ ] `template_search.py` — 模板搜索

在对话和生成过程中，AI可以调用这些工具。工具定义用OpenAI Function Calling格式。

---

### 阶段8：集成测试与验证（约1小时）

- [ ] 后端启动无报错
- [ ] 前端启动无报错
- [ ] 所有页面可正常访问（无白屏/无控制台错误）
- [ ] AI对话可正常发送/接收（SSE流式）
- [ ] 教案生成可正常完成（并行6模块）
- [ ] PPT生成可正常完成（大纲→模板→生成→编辑）
- [ ] PPTX可正常导出下载
- [ ] 教材库可正常浏览
- [ ] 模板库可正常选择
- [ ] 无任何mock数据残留
- [ ] 无硬编码API密钥
- [ ] 数据库迁移可正常执行
- [ ] 视觉验收（见第5节）全部通过

---

## 5. 视觉验收标准（强制执行，逐项检查）

### 5.1 全局设计Token

所有页面必须使用以下CSS变量，禁止页面内硬编码颜色/字体/间距：

```css
:root {
  /* 主色调 - 紫色系（教师端品牌） */
  --teacher-primary: #6366f1;
  --teacher-primary-hover: #4f46e5;
  --teacher-primary-light: #e0e7ff;
  --teacher-primary-lighter: #eef2ff;
  --teacher-secondary: #8b5cf6;
  --teacher-accent: #f59e0b;

  /* 功能色 */
  --teacher-success: #10b981;
  --teacher-warning: #f59e0b;
  --teacher-danger: #ef4444;
  --teacher-info: #3b82f6;

  /* 中性色 */
  --teacher-bg: #f8fafc;
  --teacher-bg-gradient: linear-gradient(135deg, #f0f4ff 0%, #faf5ff 50%, #fdf4ff 100%);
  --teacher-surface: #ffffff;
  --teacher-text: #1e293b;
  --teacher-text-secondary: #64748b;
  --teacher-text-tertiary: #94a3b8;
  --teacher-border: #e2e8f0;
  --teacher-border-light: #f1f5f9;

  /* 圆角 */
  --teacher-radius-sm: 8px;
  --teacher-radius-md: 12px;
  --teacher-radius-lg: 16px;
  --teacher-radius-xl: 24px;
  --teacher-radius-full: 9999px;

  /* 间距 */
  --teacher-space-1: 4px;
  --teacher-space-2: 8px;
  --teacher-space-3: 12px;
  --teacher-space-4: 16px;
  --teacher-space-5: 20px;
  --teacher-space-6: 24px;
  --teacher-space-8: 32px;
  --teacher-space-10: 40px;
  --teacher-space-12: 48px;

  /* 阴影 */
  --teacher-shadow-sm: 0 1px 2px rgba(0,0,0,0.05);
  --teacher-shadow-md: 0 4px 6px -1px rgba(0,0,0,0.07), 0 2px 4px -2px rgba(0,0,0,0.05);
  --teacher-shadow-lg: 0 10px 15px -3px rgba(0,0,0,0.1), 0 4px 6px -4px rgba(0,0,0,0.05);
  --teacher-shadow-xl: 0 20px 25px -5px rgba(0,0,0,0.1), 0 8px 10px -6px rgba(0,0,0,0.05);
  --teacher-shadow-primary: 0 4px 14px rgba(99,102,241,0.25);

  /* 字体 */
  --teacher-font-family: "PingFang SC", "Microsoft YaHei", "Helvetica Neue", Arial, sans-serif;
  --teacher-font-mono: "JetBrains Mono", "Fira Code", "Consolas", monospace;

  /* 字号 */
  --teacher-text-xs: 12px;
  --teacher-text-sm: 14px;
  --teacher-text-base: 16px;
  --teacher-text-lg: 18px;
  --teacher-text-xl: 20px;
  --teacher-text-2xl: 24px;
  --teacher-text-3xl: 30px;
  --teacher-text-4xl: 36px;
  --teacher-text-5xl: 48px;

  /* 行高 */
  --teacher-leading-tight: 1.25;
  --teacher-leading-normal: 1.5;
  --teacher-leading-relaxed: 1.75;
}
```

### 5.2 页面布局标准

**所有页面必须满足**：
- [ ] 页面背景使用`var(--teacher-bg-gradient)`（渐变背景），不是纯白
- [ ] 页面内容区域最大宽度1200px，居中，左右padding 24px
- [ ] 页面顶部有统一的页面头部（TeacherPageHeader）：大标题（24-30px加粗）+ 副标题（14px灰色）+ 右侧操作按钮
- [ ] 卡片使用`var(--teacher-surface)`白色背景，`var(--teacher-radius-lg)`圆角，`var(--teacher-shadow-md)`阴影
- [ ] 卡片内边距统一24px
- [ ] 卡片之间间距24px
- [ ] 按钮使用TeacherButton组件，禁止原生button样式
- [ ] 输入框使用TeacherInput组件，大圆角（12px），柔和边框
- [ ] 所有可点击元素有hover状态（背景变深/阴影变大/小手光标）
- [ ] 所有列表有空状态（TeacherEmptyState），禁止空白页面
- [ ] 加载状态有骨架屏或Spinner，禁止空白等待
- [ ] 页面无横向滚动条
- [ ] 字体统一使用`var(--teacher-font-family)`

### 5.3 各页面视觉验收

#### 备课中心首页（PrepCenterView）
- [ ] 大标题"今天想备什么课？"（36px加粗，深色）
- [ ] 副标题"AI辅助备课，让每一堂课都精彩"（16px灰色）
- [ ] 中央大输入框（高度56px，圆角16px，左侧图标，右侧紫色发送按钮）
- [ ] 输入框placeholder："输入课题，如：椭圆及其标准方程 第1课时"
- [ ] 下方4个入口卡片（2x2网格）：从教材开始/上传资料/选择模板/继续备课
- [ ] 每个卡片：图标（紫色圆形背景）+ 标题（16px加粗）+ 描述（14px灰色）
- [ ] 卡片hover：上移2px，阴影变大
- [ ] "最近备课"区域：有数据显示卡片列表，无数据显示空状态引导
- [ ] "为你推荐"区域：有真实数据显示，无数据隐藏整个模块

#### 课件工坊首页（SlidesView）
- [ ] 与备课中心**视觉完全一致**（同样的大标题/输入框/4卡片布局）
- [ ] 大标题"把想法变成课件"
- [ ] 副标题"AI一键生成专业PPT，支持自由编辑"
- [ ] 4个入口卡片：生成新课件/拍照出课件/教案直通/课件改编
- [ ] "我的课件"区域：真实数据，无数据空状态
- [ ] 禁止出现6个相同的"双曲线及其标准方程"假数据

#### AI对话界面（ChatInterface）
- [ ] 左侧会话列表（宽度280px，白色背景，右边框）
  - 顶部"新建对话"按钮（紫色，全宽，圆角12px）
  - 会话列表项：标题（14px加粗）+ 最后消息时间（12px灰色）
  - 选中项：紫色左边框+浅紫背景
  - hover：浅灰背景
  - 每个会话项右侧有删除按钮（hover显示）
- [ ] 中间对话区域
  - 顶部会话标题栏（居中，标题可编辑）
  - 消息区域：用户消息右对齐（紫色气泡白字），AI消息左对齐（白色气泡深字+阴影）
  - 消息气泡圆角16px，最大宽度70%
  - AI消息支持Markdown渲染（代码块有深色背景+圆角）
  - AI消息支持LaTeX公式渲染
  - 消息间距16px
  - 打字机效果（字符逐个出现）
  - 光标闪烁动画
- [ ] 底部输入区域
  - 输入框固定底部（高度48px，圆角24px，多行支持）
  - 左侧附件按钮，右侧发送按钮（紫色圆形）
  - 发送中显示"停止"按钮（红色）
  - 输入框placeholder："输入消息，Shift+Enter换行"
- [ ] 整体背景：浅灰渐变，不是纯白

#### 教案生成进度（PrepGenerating）
- [ ] 中央大标题"AI正在为你生成教案..."（24px加粗）
- [ ] 6个模块进度卡片（2列网格）：
  - 分析教材与知识点
  - 匹配教案模板结构
  - 生成教学目标与重难点
  - 设计教学过程各环节
  - 选配例题与习题（标注"v7_engine精算"）
  - 生成板书与作业设计
- [ ] 每个卡片：图标+标题+进度条+状态文字
- [ ] 进度条：紫色，圆角，动画
- [ ] 已完成：绿色对勾图标
- [ ] 进行中：旋转加载图标+紫色脉冲
- [ ] 等待中：灰色
- [ ] 6个模块**并行推进**（不是串行一个接一个）
- [ ] 底部SSE实时日志（可折叠，显示当前生成的文本片段）

#### PPT大纲确认（PrepOutlineConfirm）
- [ ] 顶部步骤条：需求→大纲确认→选择模板→生成（当前步骤高亮）
- [ ] 中央大纲编辑区：
  - 每页一个卡片（页码+标题+页面目标+核心要点）
  - 卡片可拖拽排序
  - 每页有"编辑"/"删除"按钮
  - 底部"添加页面"按钮
- [ ] 右侧统计：共X页，建议时长X分钟
- [ ] 底部操作："返回修改"/"确认大纲，下一步"（紫色主按钮）

#### 模板选择（PrepTemplatesView）
- [ ] 顶部步骤条（当前：选择模板）
- [ ] 左侧模板网格（3列）：
  - 每个模板：预览图（16:9，圆角8px）+名称+风格标签
  - 选中：紫色边框（3px）+紫色对勾角标
  - hover：上移2px+阴影
- [ ] 右侧实时预览面板（白色卡片，显示当前选中模板的大图预览）
- [ ] 底部设置：字号档/边注模式
- [ ] 底部操作："上一步"/"开始生成"（紫色主按钮）

#### 五区编辑器（SlideCanvasV3）
- [ ] 顶部工具栏（白色，底部边框）：
  - 左侧：返回按钮+课件标题
  - 中间：主题/赋能活力/绘图/AI优化本页/拍照插入
  - 右侧：保存/+页/预演/+题目/体检/导出
  - 按钮：图标+文字，hover浅紫背景
- [ ] 左侧幻灯片缩略图面板（宽度200px，白色背景，右边框）：
  - 每页缩略图（16:9，圆角4px）
  - 当前页：紫色边框
  - 每页下方页码
  - 底部"+"添加页面
- [ ] 中间编辑区（浅灰背景）：
  - 当前页大画布（16:9，白色，阴影，居中）
  - 可编辑文本框（点击编辑）
  - 公式渲染（KaTeX）
- [ ] 右侧属性面板（宽度280px，白色背景，左边框）：
  - 页面属性：背景/布局/尺寸
  - 元素属性：选中元素时显示（字体/颜色/位置）
- [ ] 底部提示栏（浅紫背景，紫色文字）：当前操作提示

#### 题库（BankView）
- [ ] 页面头部："题库" + 副标题"管理和组卷你的题目资源"
- [ ] 左侧分类树（宽度240px）：
  - 全部知识点（显示题目数量）
  - 知识点分类树（可展开/折叠）
  - 我的专题夹
- [ ] 中间题目列表：
  - 顶部工具栏：搜索框/筛选/难度/来源/自编录入/拍照入库
  - 题目卡片：题干+题型标签+难度标签+来源+操作按钮
  - 分页
- [ ] 空状态："当前筛选下暂无题目"+引导按钮

#### 资源中心（ResourcesView）
- [ ] 页面头部："资源中心"
- [ ] 左侧导航（宽度200px）：我的资源/校本资源/公共资源/收藏夹/最近使用/回收站
- [ ] 中间文件列表：
  - 顶部：上传文件/拍照扫描/新建文件夹/AI解析中心
  - 文件网格：图标+名称+大小+时间
  - 我的文件夹区域
- [ ] 空状态："暂无资源"+引导

### 5.4 组件视觉验收

#### TeacherButton
- [ ] 主按钮：紫色背景（#6366f1），白色文字，圆角12px，hover背景变深（#4f46e5），hover阴影（--teacher-shadow-primary）
- [ ] 次按钮：白色背景，紫色边框，紫色文字，hover浅紫背景
- [ ] 文字按钮：透明背景，紫色文字，hover浅紫背景
- [ ] 危险按钮：红色背景，白色文字
- [ ] 所有按钮：高度40px，padding 0 20px，字体14px，cursor pointer，transition 0.2s
- [ ] 禁用状态：灰色背景，not-allowed cursor

#### TeacherCard
- [ ] 白色背景，圆角16px，阴影（--teacher-shadow-md）
- [ ] padding 24px
- [ ] hover：阴影变大（--teacher-shadow-lg），上移1px（可选）
- [ ] transition 0.2s

#### TeacherInput
- [ ] 高度44px，圆角12px，边框1px（--teacher-border）
- [ ] focus：紫色边框（2px），紫色阴影（--teacher-shadow-primary），outline none
- [ ] placeholder：灰色（--teacher-text-tertiary）
- [ ] padding 0 16px
- [ ] 字体14px

#### TeacherEmptyState
- [ ] 居中布局
- [ ] 插图（可用SVG或emoji，尺寸120px）
- [ ] 标题（18px加粗，深色）
- [ ] 描述（14px灰色）
- [ ] 操作按钮（紫色主按钮）
- [ ] 上下padding 60px

#### TeacherProgressBar
- [ ] 高度8px，圆角4px，背景浅灰
- [ ] 进度条：紫色渐变，圆角4px
- [ ] 动画：width transition 0.3s
- [ ] 百分比文字在右侧（14px灰色）

### 5.5 动画与交互验收

- [ ] 所有hover效果有0.2s transition
- [ ] 页面切换有淡入动画（0.3s opacity）
- [ ] 模态框有缩放+淡入动画
- [ ] 下拉菜单有下滑动画
- [ ] AI消息打字机效果（字符逐个出现，光标闪烁）
- [ ] 进度条有平滑动画
- [ ] 卡片hover上移效果
- [ ] 按钮点击有缩放反馈（scale 0.98）
- [ ] SSE流式输出有平滑的内容追加

### 5.6 响应式验收

- [ ] 1920px宽度：内容居中，两侧留白
- [ ] 1440px宽度：内容正常显示
- [ ] 1280px宽度：内容正常显示，无横向滚动
- [ ] 1024px宽度：左侧面板可折叠
- [ ] 768px宽度：移动端适配（至少不崩溃）
- [ ] 所有页面无横向滚动条

### 5.7 禁止事项（视觉红线）

- [ ] 禁止页面纯白背景（必须用渐变或浅灰）
- [ ] 禁止原生button/input样式（必须用统一组件）
- [ ] 禁止硬编码颜色值（必须用CSS变量）
- [ ] 禁止不同页面风格不一致
- [ ] 禁止空页面（必须有空状态引导）
- [ ] 禁止假数据填充
- [ ] 禁止控制台有红色错误
- [ ] 禁止页面加载时白屏超过2秒
- [ ] 禁止按钮没有hover效果
- [ ] 禁止输入框没有focus效果

---

## 6. 功能验收标准

### 6.1 AI对话
- [ ] 可创建新会话
- [ ] 可发送消息并接收AI回复
- [ ] SSE流式输出（打字机效果）
- [ ] 多轮上下文记忆（至少记住最近5轮）
- [ ] 历史会话列表可切换
- [ ] 可删除会话
- [ ] 可重命名会话
- [ ] 可中断AI生成
- [ ] 4个场景切换（备课/课件/题目/答疑）
- [ ] AI消息支持Markdown渲染
- [ ] AI消息支持LaTeX公式渲染
- [ ] 刷新页面会话不丢失

### 6.2 教案生成
- [ ] 输入主题后进入对话界面
- [ ] AI主动询问至少3个细节问题
- [ ] 可跳过问题使用默认值
- [ ] 点击生成后6个模块并行生成
- [ ] SSE实时推送进度
- [ ] 生成完成后进入编辑器
- [ ] 教案内容真实（非模板占位）
- [ ] 例题有5步讲解（v7_engine）
- [ ] 可编辑教案内容
- [ ] 可保存到我的教案
- [ ] 可导出Word
- [ ] 我的教案列表显示真实数据

### 6.3 PPT生成
- [ ] 输入主题后进入对话界面
- [ ] AI主动询问页数/模板/风格
- [ ] 生成大纲（至少5页）
- [ ] 可编辑大纲（增删/排序）
- [ ] 可选择模板（至少6个）
- [ ] 并行生成各页内容
- [ ] SSE实时推送每页进度
- [ ] 生成完成进入五区编辑器
- [ ] PPT内容真实（非占位）
- [ ] 数学公式正确渲染
- [ ] 例题页有5步讲解
- [ ] 可在编辑器中自由编辑
- [ ] 可导出PPTX文件
- [ ] 导出的PPTX可正常打开
- [ ] 可保存到我的课件
- [ ] 我的课件列表显示真实数据

### 6.4 教材库
- [ ] 教材列表显示已入库教材
- [ ] 可查看章节树
- [ ] 可浏览知识点
- [ ] 可搜索教材内容
- [ ] 至少1本教材完整入库
- [ ] 教案/PPT生成中AI可引用教材内容

### 6.5 模板库
- [ ] 至少6个模板可用
- [ ] 模板有预览图
- [ ] 可选择模板
- [ ] 生成的PPT使用选中模板的样式

### 6.6 基础功能
- [ ] 所有页面可正常访问
- [ ] 所有按钮可点击（无死按钮）
- [ ] 所有列表有删除功能
- [ ] 所有列表有编辑功能
- [ ] 所有表单有验证
- [ ] 所有操作有loading状态
- [ ] 所有错误有提示
- [ ] 无mock数据残留
- [ ] 无硬编码API密钥

---

## 7. 代码规范

### 7.1 后端规范
- Python 3.10+，type hints全覆盖
- FastAPI路由，pydantic模型
- SQLAlchemy async，Alembic迁移
- 所有外部调用有超时和重试
- 所有异常有处理和日志
- 提示词存为文件（Jinja2模板），不硬编码
- 配置从环境变量读取，不硬编码
- 并行用asyncio.gather
- SSE用StreamingResponse
- 文件名/函数名/变量名见名知意

### 7.2 前端规范
- Vue 3 + Composition API + `<script setup>`
- TypeScript
- Pinia状态管理
- 所有样式用CSS变量，不硬编码
- 组件名PascalCase，props定义类型
- API调用集中在`src/api/`，不散落组件
- 无console.log残留（提交前清除）
- 无未使用的import
- 无硬编码的假数据
- 图片资源用require或import，不用外链

### 7.3 数据库规范
- 所有新表有Alembic迁移
- 主键UUID
- 所有表有created_at, updated_at
- 外键有索引
- JSON字段用JSONB（PostgreSQL）
- 迁移脚本可回滚

---

## 8. 子智能体创建指导

你可以创建子智能体并行处理独立模块。建议分工：

### 子智能体A：后端开发
- 负责：对话模块/教案生成/PPT生成/教材解析/模板管理/工具注册/数据库迁移
- 产出：可运行的后端API
- 验证：所有接口返回200，无报错

### 子智能体B：前端开发
- 负责：设计Token/通用组件/页面样式统一/对话组件/教案流程/PPT流程/教材页面
- 产出：可运行的前端页面
- 验证：所有页面可访问，无控制台错误，视觉达标

### 子智能体C：内容入库
- 负责：clone参考项目/SeniorTextbook教材解析入库/ppt-templates模板入库/基础数据填充
- 产出：数据库中有真实教材和模板数据
- 验证：教材列表和模板列表有数据

### 子智能体D：测试与验收
- 负责：集成测试/视觉验收/功能验收/bug修复
- 产出：验收报告
- 验证：所有验收项通过

**子智能体之间通过文件系统协作**：
- 后端API定义写入`docs/api-contract.md`
- 前端组件接口写入`docs/component-contract.md`
- 数据库schema写入`docs/db-schema.md`
- 共享配置写入`.env`文件

---

## 9. 交付物清单

完成后必须交付：

1. **后端代码**：`D:\math-arena\services\api\app\domains\teacher\` 下所有新增/修改文件
2. **前端代码**：`D:\frontend\src\` 下所有新增/修改文件
3. **数据库迁移**：Alembic迁移脚本
4. **配置文件**：`.env`（含API密钥）
5. **教材数据**：至少1本完整入库的高中数学教材
6. **模板数据**：至少6个PPT模板
7. **API文档**：`docs/teacher-api.md`（所有接口说明）
8. **视觉验收报告**：`docs/visual-review.md`（逐项截图+通过/不通过）
9. **功能验收报告**：`docs/functional-review.md`（逐项测试结果）
10. **启动说明**：`docs/startup-guide.md`（如何启动前后端，如何验证）

---

## 10. 最终检查清单（提交前逐项确认）

### 代码质量
- [ ] 无mock文件残留
- [ ] 无假数据填充
- [ ] 无硬编码API密钥
- [ ] 无硬编码颜色/字体/间距
- [ ] 无console.log残留
- [ ] 无未使用的import
- [ ] 所有提示词外置为文件
- [ ] 所有配置从环境变量读取
- [ ] 所有新表有Alembic迁移
- [ ] 所有API有错误处理

### 功能完整
- [ ] AI对话可正常使用
- [ ] 教案生成可正常完成
- [ ] PPT生成可正常完成
- [ ] PPTX可正常导出
- [ ] 教材库有数据可浏览
- [ ] 模板库有数据可选择
- [ ] 所有页面可访问
- [ ] 所有按钮可点击
- [ ] 所有列表有增删改查

### 视觉达标
- [ ] 所有页面使用统一设计Token
- [ ] 所有页面渐变背景
- [ ] 所有卡片统一样式
- [ ] 所有按钮统一样式
- [ ] 所有输入框统一样式
- [ ] 所有空状态有引导
- [ ] 所有加载有状态
- [ ] 无横向滚动条
- [ ] 无控制台红色错误
- [ ] 无白屏

### 可运行
- [ ] 后端启动无报错
- [ ] 前端启动无报错
- [ ] 数据库迁移可执行
- [ ] API接口返回正常
- [ ] 前后端联调通过
- [ ] 启动说明文档完整

---

> **最后提醒**：这是一晚上的开发任务，时间有限。优先保证核心功能跑通（AI对话+教案生成+PPT生成），再完善教材入库和模板库。视觉标准是红线，必须达标。遇到技术瓶颈，换方案不要死磕。宁可功能少而精，不要多而假。现在开始工作！
