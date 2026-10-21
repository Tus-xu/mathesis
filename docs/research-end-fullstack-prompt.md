# 科研端全栈开发任务 — 智能体执行提示词

> **执行模式**：你是一个全栈开发智能体团队的主控。你有权创建子智能体并行完成开发。本任务需要连夜完成，必须高效、严谨、可运行。
>
> **最终交付**：一个可运行的、与学生端/教师端统一架构的科研端（数学科研者论文阅读+写作平台），后端合并到 `D:\math-arena\services\api`，前端合并到 `D:\frontend`。

---

## 一、你的角色与权力

### 1.1 角色
你是**首席架构师兼全栈开发负责人**，负责科研端从架构设计到代码实现的全流程。你不是顾问，你是执行者——必须写出可运行的代码。

### 1.2 权力
- **创建子智能体**：你可以创建最多5个子智能体，分别负责：后端架构、前端架构、AI层、数据库设计、测试验证。子智能体完成后由你整合。
- **自由选择实现方式**：在满足本提示词约束的前提下，你可以自由选择具体的库、模式、代码组织方式。
- **修改现有代码**：你可以修改现有后端/前端的代码以接入科研端，但必须保持学生端/教师端功能不受影响。

### 1.3 铁律（违反即失败）
1. **禁止硬编码**：所有配置走环境变量，所有API路径集中管理，所有常量集中定义，所有提示词外置为文件。
2. **禁止空壳子**：每个UI按钮必须有真实的后端API和数据库操作，禁止只有界面没有功能。
3. **禁止破坏现有功能**：学生端、教师端、管理后台的所有现有功能必须保持可用。
4. **禁止跳过基础功能**：CRUD、搜索、筛选、排序、批量操作、删除确认、加载状态、错误提示——这些是用户体验基础设施，必须全部实现。
5. **禁止写死密钥**：API密钥只出现在 `.env` 文件中，代码中通过 `settings` 读取。

---

## 二、开发前必须完成的调研（2小时内）

在写任何代码之前，你必须先深入调研以下项目的架构，并输出一份简短的架构笔记（存为 `D:\math-arena\docs\research-architecture-notes.md`）：

### 2.1 必须调研的项目
| 项目 | 调研重点 | 调研方式 |
|---|---|---|
| **Zotero** | 数据模型（items/attachments/collections/tags规范化schema）、SQLite ACID事务、标注存储方式、插件系统、文献导入导出格式 | 搜索官方文档+GitHub源码+技术博客 |
| **Overleaf** | 微服务拆分（web/real-time/clsi/document-updater/filestore）、LaTeX编译沙箱、实时协作OT算法、PDF预览SyncTeX | 搜索官方文档+GitHub Wiki+架构博客 |
| **典型智能体应用** | LangGraph状态机编排、Function Calling工具注册模式、SSE流式输出、RAG混合检索（BM25+向量）、任务队列Celery | 搜索LangChain/LlamaIndex官方文档+开源项目 |
| **现有代码架构** | 仔细阅读 `D:\math-arena\services\api\app\main.py`、`config.py`、`gateway/research_router.py`、`models/base.py`、`domains/` 下任意一个域的完整结构；前端阅读 `D:\frontend\src\router\index.js`、`stores/auth.js`、`api/` 目录、`layouts/` 目录 | 直接读代码 |
| **高保真原型（必读）** | 完整阅读 `D:\math-arena\output\research-redesign\research-workbench-v2.html`（85KB，经过6轮用户迭代的高保真原型）和 `D:\math-arena\output\research-redesign\design-notes.md`（设计决策文档）。这里又重要的一步，你首先需要自己去深入调研科研平台的链路，理解重点理解：我们的平台的定位应该是数学科研者的看论文和写论文的平台吧，就是不要形成那种科研的详细内容的模块；以及怎么突出 AI 大模型在这个平台的作用呢，我觉得就应该像 Ztero 那样吧，不要把研究链路放在我们的科研端内，我们弱化科研，强化 AI 对既有论文的评审呀。所以你需要先对原型进行辩证的分析调研看待，如果发现更好的布局链路随时可以进行改进升级！！！整体布局结构、颜色体系、组件样式、交互模式、文献悬浮球设计、沉浸式全宽三栏布局、阅读页选区浮层、写作页素材箱、任务中心UI | 直接读HTML源码+在浏览器中打开交互体验 |

### 2.2 调研输出要求
架构笔记必须包含：
- Zotero数据模型中可直接借鉴的表结构设计
- Overleaf编译服务的隔离方案
- LangGraph+Function Calling的最小可运行模式
- 现有后端的路由注册方式、数据库模型基类、认证依赖注入方式
- 现有前端的路由模式、API封装方式、布局组件
- **原型设计拆解**：从v2原型中提取的颜色变量、间距规范、组件样式、布局结构、交互流程，作为前端开发的设计基准（见第七章7.5节）

---

## 三、现有架构与合并要求

### 3.1 后端现有架构（必须合并进去，不是新建）

**位置**：`D:\math-arena\services\api`

**现有分层**：
```
app/
├── main.py              # 应用装配（只注册路由，不写业务逻辑）
├── config.py            # pydantic-settings 配置管理
├── gateway/             # 路由层（HTTP入口，参数验证，调用service）
│   ├── research_router.py   # 已有科研端试点路由（/api/research前缀）
│   ├── auth_router.py
│   └── ...
├── domains/             # 领域层（业务逻辑，按领域拆分）
│   ├── identity/        # 认证（登录/注册/JWT）
│   ├── classroom/       # 课堂
│   ├── teacher/         # 教师
│   ├── files/           # 文件
│   └── ...
├── models/              # 数据模型（SQLAlchemy 2.0）
│   ├── base.py          # 基类（id/created_at/updated_at）
│   ├── database.py      # 数据库连接（async_session_factory）
│   └── ...
├── services/            # 服务层（外部服务适配、任务处理器）
├── providers/           # 外部API提供者（LLM/Embedding/存储）
├── workers/             # 后台任务
└── skills/              # Skill注册表
```

**现有基础设施**：
- PostgreSQL + asyncpg（异步）
- Redis（缓存/会话）
- Alembic（数据库迁移，禁止create_all）
- structlog（结构化日志）
- task_runner（后台任务系统，已有自愈循环）
- 对象存储（boto3抽象，支持COS/OSS/MinIO/S3）
- JWT认证（`app/gateway/auth.py` 的 `require_role` 依赖）

**合并要求**：
1. 科研端后端代码**必须**写在 `D:\math-arena\services\api` 内，作为新的域 `app/domains/research/` 和新的路由 `app/gateway/research_router.py`（扩展现有文件）。
2. **禁止**新建独立的后端项目或独立的端口。科研端与学生端/教师端共用同一个FastAPI应用、同一个端口（默认8000）、同一个数据库。
3. 数据库表必须通过Alembic迁移创建，表名以 `research_` 前缀（如 `research_papers`、`research_manuscripts`）。
4. 认证复用现有 `require_role("researcher")`，科研端路由必须加 `researcher` 角色校验。
5. 配置项添加到 `app/config.py` 的 `Settings` 类中，通过环境变量读取。

### 3.2 前端现有架构（必须合并进去，不是新建）

**位置**：`D:\frontend`

**现有分层**：
```
src/
├── main.js
├── App.vue
├── router/index.js       # 路由（已有research路由但都是redirect占位）
├── stores/               # Pinia状态管理
│   ├── auth.js           # 认证状态
│   ├── theme.js          # 主题
│   └── ...
├── api/                  # API封装（axios拦截器已配置）
├── pages/                # 页面
│   ├── student/          # 学生端
│   ├── teacher-v3/       # 教师端
│   ├── research/         # 科研端（目前为空，只有ResearchRedirect.vue）
│   └── admin/            # 管理后台
├── layouts/              # 布局组件
├── components/           # 通用组件
├── composables/          # 组合式函数
└── styles/               # 全局样式
```

**合并要求**：
1. 科研端前端代码**必须**写在 `D:\frontend\src\pages\research\` 下，作为主前端的一部分。
2. **禁止**新建独立的前端项目或独立的端口。科研端与学生端/教师端共用同一个Vue应用、同一个Vite开发服务器（默认5173）。
3. 路由添加到 `src/router/index.js`，使用 `researcher` 角色守卫，路径前缀 `/research/`。
4. 布局复用或新建 `src/layouts/ResearchLayout.vue`，保持与教师端/学生端一致的设计语言。
5. API封装写在 `src/api/research.js`，复用现有axios实例（已配置Token拦截器和错误处理）。
6. 状态管理写在 `src/stores/research.js`（Pinia）。

### 3.3 统一登录与路由
- 登录/注册复用现有 `/login`、`/register` 页面，科研端用户角色为 `researcher`。
- 科研端入口路径：`/research`（文献库首页）。
- 从统一工作入口 `/hub` 点击"科研端"跳转到 `/research`。
- 删除现有 `ResearchRedirect.vue` 的重定向逻辑，替换为真实的科研端页面。

---

## 四、API密钥与外部服务配置

### 4.1 讯飞星火大模型（文字模型）
```
Model ID: spark-x2.5-4b
OpenAI兼容URL: https://maas-api.cn-huabei-1.xf-yun.com/v2
Anthropic兼容URL: https://maas-api.cn-huabei-1.xf-yun.com/anthropic
API Key: ak-***MASKED-ROTATE-ME***
```
- 用途：AI对话、文献解释、写作建议、公式推导
- 必须通过OpenAI兼容接口调用（`/chat/completions`）
- 必须支持流式输出（SSE）

### 4.2 讯飞星火图像理解模型
```
Model ID: xophunyuanocr
OpenAI兼容URL: https://maas-api.cn-huabei-1.xf-yun.com/v2
API Key: ak-***MASKED-ROTATE-ME***
```
- 用途：PDF页面图像理解、公式图片识别、图表理解
- 与文字模型共用API Key，但Model ID不同

### 4.3 博查联网搜索API
```
API Key: sk-***MASKED-ROTATE-ME***
```
- 用途：文献联网搜索、引用验证、最新论文检索
- 必须封装为可复用的搜索服务，支持缓存（Redis，TTL=1小时）

### 4.4 配置要求
以上密钥**必须**添加到 `D:\math-arena\.env` 文件中（如果不存在则创建），并在 `app/config.py` 的 `Settings` 类中添加对应字段：
```python
# 科研端AI配置
research_llm_base_url: str = "https://maas-api.cn-huabei-1.xf-yun.com/v2"
research_llm_api_key: str = ""
research_llm_model: str = "spark-x2.5-4b"
research_vision_model: str = "xophunyuanocr"
research_search_api_key: str = ""
```

---

## 五、P0功能清单（必须全部实现）

### 5.1 文献库（/research/library）

**数据模型**（Alembic迁移创建，知识库相关表见5.2节）：
- `research_papers`：id, user_id, title, authors(JSON), year, journal, doi, abstract, file_path, file_size, parse_status(pending/parsing/completed/failed), reading_status(unread/reading/read/archived), collection_id, created_at, updated_at
- `research_collections`：id, user_id, name, parent_id, sort_order
- `research_tags`：id, user_id, name, color
- `research_paper_tags`：paper_id, tag_id（多对多关联表）
- `research_annotations`：id, user_id, paper_id, page, rect_coords(JSON), type(highlight/underline/note), color, selected_text, note_text, created_at
- `research_notes`：id, user_id, paper_id, title, content, type, source_page, created_at
- 知识库表（chunks/math_objects/sections/references/figures）见5.2节

**页面功能**：
| 功能 | 具体要求 |
|---|---|
| 文献列表 | 卡片/列表双视图切换，显示标题/作者/年份/阅读状态/标签 |
| 搜索 | 标题/作者/DOI/全文搜索，输入防抖300ms，搜索结果高亮 |
| 筛选 | 按阅读状态/集合/标签/年份/期刊筛选，多条件组合 |
| 排序 | 最近打开/添加时间/年份/标题/作者，升序降序切换 |
| 分类管理 | 左侧集合树（支持多级），标签管理（新增/删除/重命名/改色） |
| 导入 | PDF上传（拖拽+点击）、DOI录入、URL抓取、BibTeX导入 |
| 导出 | 单篇/批量导出BibTeX、RIS、CSV |
| 批量操作 | 勾选后批量删除/移动集合/加标签/标记已读/导出 |
| 删除 | 删除前确认弹窗，删除后进入回收站（可恢复），回收站可永久删除 |
| 详情面板 | 右侧滑出面板显示元数据/摘要/标注/笔记，可编辑元数据 |
| 阅读位置 | 自动记录最后阅读页码，下次打开自动跳转 |
| 去重 | DOI唯一索引，上传时自动检测重复并提示合并 |

**PDF解析与知识库构建**（核心能力，不是可选功能）：

**解析范围与触发机制**（必须严格遵守，禁止AI自动下载解析外部论文）：
| 论文来源 | 是否自动解析 | 触发时机 | 说明 |
|---|---|---|---|
| **用户上传PDF到文献库** | ✅ 自动解析 | 上传完成后立即触发 | 核心场景，解析后进入知识库 |
| **用户通过DOI/URL导入** | ⚠️ 条件解析 | 导入后尝试获取全文PDF，获取到则解析，获取不到只存元数据 | 元数据从CrossRef/arXiv获取，全文需用户提供PDF |
| **用户通过BibTeX/RIS批量导入** | ❌ 不自动解析 | 只存元数据，用户后续上传PDF后自动解析 | BibTeX通常不含全文 |
| **AI联网搜索（博查API）找到的论文** | ❌ 不自动解析 | 只返回搜索结果（标题/摘要/链接），用户点击"加入文献库"后才触发解析 | 禁止AI自动下载外部PDF，避免资源浪费和版权问题 |
| **写作悬浮球搜索结果** | ❌ 不自动解析 | 优先搜索文献库（已解析），文献库没有时可联网搜索，用户选择"加入文献库"后解析 | 悬浮球是检索入口，不是解析入口 |
| **用户手动点击"重新解析"** | ✅ 手动触发 | 用户在文献详情页点击重新解析 | 用于解析失败重试或解析器升级后重新解析 |

**核心原则**：知识库的范围 = 用户文献库中**已上传PDF并成功解析**的论文。AI联网搜索只提供"候选论文"，用户明确选择加入文献库并上传PDF后才进入知识库。AI**禁止**自动下载、自动解析外部论文，禁止把联网搜索结果当作知识库内容引用（必须标注"来自联网搜索，未加入文献库"）。

**解析执行**：
- 上传后自动触发后台解析任务（Celery或现有task_runner）
- **双解析器并行**：GROBID（学术头部/参考文献，快）+ Docling（正文/表格/布局，准），结果合并
- **结构化提取**（必须全部提取，存入知识库）：
  - 元数据：标题/作者/年份/期刊/DOI/摘要/关键词
  - 章节结构：章节层级树（section/subsection），每节带页码范围
  - 数学对象：定义/定理/引理/命题/推论/公式/符号表（自动识别+AI辅助标注）
  - 参考文献：结构化BibTeX（作者/标题/期刊/年份/DOI），与正文引用标记关联
  - 图表：图/表的标题、说明、所在页码
- **正文分块**：按语义分块（段落/小节），每块带元数据（论文ID/章节/页码/字符位置），存入向量库
- **向量化**：使用bge-m3或兼容embedding模型，存入pgvector（PostgreSQL扩展）
- **解析进度**实时推送（SSE），解析失败可重试，解析状态持久化（pending/parsing/completed/failed）
- **解析失败处理**：标记failed，记录错误原因，用户可手动重试；GROBID失败时降级为Docling单解析，反之亦然；两者都失败时只存元数据，论文可正常阅读但不进入知识库检索
- **知识库检索**：混合检索（BM25全文 + pgvector语义 + RRF融合），支持按论文/章节/页码过滤
- AI回答**必须**基于知识库检索，每个回答带引用来源（[论文标题, 章节, 页码]），无来源时明确说明"未在知识库中找到相关内容"；如果使用了联网搜索结果，必须标注"来自联网搜索，未加入文献库，仅供参考"

### 5.2 知识库（文献结构化知识管理）

**定位**：知识库是科研端的核心数据层——论文解析后的结构化知识存储，支撑AI阅读理解、写作引用、语义检索。不是独立页面，而是贯穿文献库/阅读器/写作/AI助手的基础设施。

**数据模型**（Alembic迁移创建，表名 `research_` 前缀）：
- `research_paper_chunks`：id, paper_id, section_path, page_start, page_end, char_start, char_end, content, embedding(vector), chunk_type(paragraph/definition/theorem/formula/figure/caption), created_at
- `research_math_objects`：id, paper_id, type(definition/theorem/lemma/proposition/corollary/formula/symbol), name, content_latex, page, char_position, section_path, description, created_at
- `research_sections`：id, paper_id, level, title, page_start, page_end, parent_id, sort_order
- `research_references`：id, paper_id, citation_key, authors(JSON), title, journal, year, doi, url, bibtex, verified(bool), created_at
- `research_figures`：id, paper_id, type(figure/table), caption, page, image_path, created_at

**知识库功能**（贯穿各模块）：
| 功能 | 说明 | 出现位置 |
|---|---|---|
| 结构化解析 | PDF上传后自动解析，提取章节/定义/定理/公式/参考文献 | 文献库上传后 |
| 知识浏览 | 查看论文的章节树、定义列表、定理列表、公式列表、参考文献 | 阅读器右侧面板/文献详情 |
| 语义检索 | 自然语言搜索知识库，返回相关段落+数学对象，带页码 | 文献库搜索/AI助手/写作悬浮球 |
| 引用溯源 | 点击AI回答中的引用，跳转到论文对应页码和高亮位置 | 阅读器/AI面板 |
| 公式检索 | 按公式内容/名称搜索，返回定义和使用位置 | 文献库搜索/AI助手 |
| 参考文献验证 | 调用博查搜索API验证参考文献真伪，标记verified状态 | 写作/阅读器 |
| 知识卡片 | 定义/定理/公式的结构化卡片，含内容、来源、相关公式 | 阅读器/AI解释 |

**知识库与AI助手的关系**：
- AI助手的 `search_papers` 工具本质上是知识库检索（混合检索+重排序）
- AI助手的 `get_paper_content` 工具从知识库获取结构化内容（章节/定义/定理）
- AI解释公式时，先从知识库查找该公式的定义和上下文，再生成解释
- AI写作建议中的引用验证，基于知识库的参考文献表+博查搜索

**知识库与写作的关系**：
- 文献悬浮球搜索的是知识库（不只是论文标题，还能搜索段落/公式/定义）
- 插入引用时，从知识库的参考文献表获取BibTeX
- 加入写作素材时，从知识库的chunks获取带元数据的段落
- AI写作建议检查引用一致性时，比对文稿引用与知识库参考文献

### 5.3 阅读器（/research/reader/:paperId）

**技术选型**：pdf.js（前端渲染）

**页面功能**：
| 功能 | 具体要求 |
|---|---|
| PDF渲染 | 支持单页/双页/连续滚动模式，适合宽度/适合页面/自定义缩放 |
| 导航 | 页码输入跳转、上一页/下一页、目录导航（自动提取PDF书签）、缩略图侧边栏 |
| 搜索 | 全文搜索，高亮匹配，上一个/下一个，搜索结果列表 |
| 标注 | 文本高亮（4种颜色）、下划线、区域标注、笔记，标注后自动保存 |
| 标注管理 | 右侧标注列表，按页码/类型筛选，点击跳转到对应位置，删除标注 |
| 笔记 | 结构化笔记，可关联标注，笔记列表，导出笔记为Markdown |
| AI助手 | 选中文本后弹出浮层：解释/翻译/公式推导/提问/保存为笔记，SSE流式输出 |
| 引用查看 | 点击引用标记跳转到参考文献列表，可验证引用真伪（调用博查搜索） |
| 阅读状态 | 标记已读/未读，记录阅读进度 |
| 返回 | 左上角返回文献库，保留筛选状态 |

**布局要求**：
- 顶部工具栏（精简，不重复页面标题）
- 左侧可折叠目录/缩略图
- 中间PDF阅读区（占满剩余空间，内部滚动）
- 右侧可折叠标注/笔记/AI面板
- 沉浸式：无多余留白，无整页滚动

### 5.4 写作（/research/writing）

**数据模型**：
- `research_manuscripts`：id, user_id, title, content(LaTeX源码), format(latex/markdown), status(draft/reviewing/final), word_count, compile_status(idle/compiling/success/failed), compile_log, version, created_at, updated_at
- `research_writing_materials`：id, user_id, manuscript_id, source_paper_id, page, content, type(quote/paraphrase/data/formula), used(bool), created_at
- `research_ai_suggestions`：id, manuscript_id, type(language/symbol/citation/format/structure), severity(info/warning/error), line_number, suggestion, original_text, evidence(JSON), status(pending/accepted/ignored), created_at
- `research_manuscript_citations`：id, manuscript_id, citation_key, source_paper_id, bibtex, verified(bool), created_at

**页面功能**：
| 功能 | 具体要求 |
|---|---|
| 文稿列表 | 左侧"我的文稿"列表，显示标题/状态/字数/最后编辑时间，支持新建/搜索/排序 |
| 新建文稿 | 从模板新建（空白/论文模板/报告模板）、导入LaTeX/Word |
| 编辑器 | CodeMirror 6，LaTeX语法高亮、自动补全、括号匹配、行号 |
| 编译 | 点击编译触发后台任务（Tectonic或Docker隔离的LaTeX编译），编译状态实时显示，错误日志可查看，错误点击定位到源码行 |
| PDF预览 | 右侧实时PDF预览（pdf.js），支持SyncTeX（点击源码跳转PDF，点击PDF跳转源码） |
| 自动保存 | 输入防抖500ms自动保存，显示"已保存/保存中"状态，版本历史可查看和恢复 |
| 文献悬浮球 | 右下角悬浮球，点击呼出文献搜索浮层：搜索文献→预览论文→选中段落→插入引用/加入素材/AI解释，不离开写作页面 |
| 素材箱 | 右侧素材面板，显示已加入的素材，分类管理（引用/改写/数据/公式），点击插入到光标位置 |
| 引用管理 | BibTeX编辑器，引用列表，插入引用到光标位置，引用格式切换 |
| AI写作建议 | 侧边栏显示AI建议列表（语言润色/符号检查/引用验证/格式检查），可接受/忽略/查看diff/查看依据 |
| 导出 | 导出PDF/LaTeX源码包/Word |
| 删除 | 删除文稿前确认，可恢复 |

**布局要求**：
- 左侧文稿列表（可折叠）
- 中间编辑器（占满剩余空间）
- 右侧PDF预览/素材/AI建议（可切换标签，可折叠）
- 顶部工具栏（编译/导出/保存状态，不重复页面标题）
- 沉浸式：全宽布局，内部独立滚动，无整页滚动

### 5.5 AI助手（全局）

**架构**：
- 后端：LangGraph状态机编排 + Function Calling工具注册 + SSE流式输出
- 前端：可复用的AIChatPanel组件，支持流式打字机效果

**工具注册表**（必须实现，禁止硬编码if-else）：
| 工具名 | 功能 | 参数 |
|---|---|---|
| `search_papers` | 搜索用户文献库 | query, filters |
| `search_web` | 联网搜索（博查API） | query, max_results |
| `get_paper_content` | 获取论文全文/指定页 | paper_id, page |
| `get_annotations` | 获取论文标注 | paper_id |
| `verify_citation` | 验证引用真伪 | citation_text |
| `explain_formula` | 解释公式推导 | formula, context |
| `get_writing_context` | 获取当前文稿内容 | manuscript_id, section |
| `insert_citation` | 插入引用到文稿 | manuscript_id, citation_key, position |

**AI能力**：
- 文献阅读：解释段落、翻译、总结论文、提取方法/结果/结论、公式推导
- 写作辅助：润色语言、检查符号一致性、验证引用、建议结构、生成段落
- 对话记忆：当前会话上下文，支持多轮对话
- 引用来源：每个回答必须标注引用来源（[论文标题, 页码]），无来源时明确说明

**SSE流式**：
- 后端：`StreamingResponse` + `async generator`，事件类型：token/tool_call/tool_result/error/done
- 前端：`fetch` + `ReadableStream` 解析SSE，打字机效果显示
- 支持中断：前端发送abort信号，后端取消异步任务

### 5.6 任务中心（/research/tasks）

**数据模型**：
- `research_tasks`：id, user_id, type(pdf_parse/llm_generate/compile/export), status(pending/queued/running/completed/failed/cancelled), progress(0-100), input(JSON), output(JSON), error_message, logs, created_at, started_at, completed_at

**页面功能**：
| 功能 | 具体要求 |
|---|---|
| 任务列表 | 全部/运行中/已完成/失败/已取消，标签切换 |
| 任务详情 | 状态/进度条/输入参数/输出结果/日志/耗时 |
| 操作 | 重试失败任务、取消运行中任务、删除任务记录 |
| 筛选 | 按任务类型/状态/时间范围筛选 |
| 实时更新 | 任务状态变化实时推送（SSE或轮询5s） |

**后台任务**：
- 使用现有 `task_runner` 系统或Celery（二选一，推荐复用现有task_runner）
- 长任务（>5秒）必须异步执行，禁止阻塞HTTP请求
- 任务支持取消（进程树kill）
- 任务失败自动重试（最多3次，指数退避）
- 任务日志持久化

---

## 六、后端开发套件要求

### 6.1 缓存（Redis）
- 文献搜索结果缓存（TTL=5分钟）
- 博查搜索结果缓存（TTL=1小时）
- AI对话历史缓存（TTL=24小时）
- 用户会话缓存（复用现有）
- 必须实现缓存穿透保护（空值缓存，TTL=1分钟）

### 6.2 后台运行能力
- PDF解析：异步任务，进度可查
- AI生成长文本：异步任务，支持流式和异步两种模式
- LaTeX编译：异步任务，隔离执行
- 文件导出：异步任务
- 所有后台任务必须有状态记录和日志

### 6.3 并发能力
- 数据库连接池（SQLAlchemy async pool，pool_size=10, max_overflow=20）
- Redis连接池
- HTTP客户端连接池（httpx.AsyncClient，复用现有）
- 异步IO：所有IO操作必须使用async/await，禁止同步阻塞调用
- 请求限流：AI接口按用户限流（10次/分钟），使用Redis计数器

### 6.4 数据库
- PostgreSQL + asyncpg（异步）
- Alembic迁移（每个表变更必须有迁移脚本）
- 表名前缀 `research_`
- 所有表必须有 `id`（UUID）、`created_at`、`updated_at`
- 所有用户数据表必须有 `user_id` 外键，查询时必须过滤 `user_id`（数据隔离）
- 索引：user_id+created_at复合索引、常用查询字段索引、全文搜索GIN索引
- 事务：写操作必须在事务中执行，失败自动回滚

### 6.5 文件存储
- PDF文件、编译产物、导出文件使用现有对象存储抽象（`app/providers/`）
- 本地开发可使用文件系统存储（`D:\math-arena\data\research\`）
- 文件路径存数据库，不存文件内容
- 文件上传大小限制（PDF最大50MB）

---

## 七、前端开发要求

### 7.1 技术栈
- Vue 3（Composition API + `<script setup>`）
- Vite（已有配置）
- Pinia（状态管理，已有）
- Vue Router（已有）
- Axios（已有封装）
- Naive UI（组件库，如果未安装则安装；如已有其他组件库则复用现有）
- pdf.js（PDF渲染）
- CodeMirror 6（代码编辑器）

### 7.2 代码规范
- 所有组件使用 `<script setup>` 语法
- TypeScript优先（如现有项目是JS则保持JS，但新代码尽量加类型）
- 组件名PascalCase，文件名PascalCase.vue
- 组合式函数use开头，存 `composables/`
- 禁止在组件中直接写axios调用，必须通过 `api/research.js`
- 禁止硬编码API路径，集中在 `api/research.js`
- 禁止硬编码颜色/尺寸，使用CSS变量或主题配置

### 7.3 用户体验基础设施（必须全部实现）
| 基础设施 | 要求 |
|---|---|
| 加载状态 | 每个异步操作都有loading状态（按钮loading/骨架屏/Spinner） |
| 错误处理 | API失败显示错误提示（Naive UI message/notification），表单错误字段高亮 |
| 空状态 | 列表为空时显示空状态插图+引导操作（如"上传第一篇论文"） |
| 确认弹窗 | 删除/覆盖/危险操作必须有确认弹窗 |
| 防抖节流 | 搜索输入防抖300ms，窗口resize节流 |
| 路由守卫 | 科研端路由校验researcher角色，无权限跳转 |
| 权限控制 | 无权限的按钮隐藏或禁用 |
| 响应式 | 适配1280px以上桌面端，最小宽度1024px |
| 键盘快捷键 | 常用操作支持快捷键（Ctrl+S保存/Ctrl+F搜索/Esc关闭弹窗） |
| 需支持50人以上的并发运行不会崩 |

### 7.4 设计规范（必须严格参考高保真原型）

**设计基准文件**（开发前必须完整阅读并在浏览器中交互体验）：
- 原型：`D:\math-arena\output\research-redesign\research-workbench-v2.html`（85KB，6轮用户迭代打磨）
- 设计文档：`D:\math-arena\output\research-redesign\design-notes.md`（含所有设计决策和变更记录）

**必须从原型中提取并复用的设计元素**：

| 设计维度 | 必须复用的内容 | 原型中的位置 |
|---|---|---|
| **颜色体系** | 主色/辅助色/背景色/文字色/边框色的完整色值，提取为CSS变量 | 原型 `<style>` 中的 `:root` 变量定义 |
| **布局结构** | 沉浸式全宽三栏布局（文献库：左侧分类树+中间列表+右侧详情；写作：左侧文稿列表+中间编辑器+右侧预览） | 原型 `.workspace-view` 容器结构 |
| **导航设计** | 顶部导航栏样式、激活态、图标+文字组合、无独立"阅读"导航（文献库点击直接进阅读） | 原型 `.top-nav` 组件 |
| **卡片样式** | 论文卡片/文稿卡片的圆角、阴影、hover态、元信息排版（标题/作者/年份/标签/状态） | 原型 `.paper-card` / `.manuscript-card` |
| **按钮体系** | 主按钮/次按钮/文字按钮/危险按钮的样式、尺寸、hover/active/disabled态，禁止粗糙按钮 | 原型 `.btn-primary` / `.btn-secondary` 等 |
| **悬浮球** | 右下角文献悬浮球的样式、动画、呼出浮层结构（搜索+列表+预览+插入引用/加入素材/AI解释） | 原型 `.lit-floating-ball` 组件 |
| **选区浮层** | 阅读页选中文本后弹出的浮层（解释/翻译/公式推导/提问/保存笔记），位置跟随选区 | 原型 `.selection-toolbar` 组件 |
| **AI面板** | AI对话面板的样式、消息气泡、流式打字效果、引用来源标注、工具调用状态 | 原型 `.ai-chat-panel` 组件 |
| **标注样式** | 高亮颜色（4种）、标注列表项样式、笔记卡片样式 | 原型 `.annotation-list` / `.note-card` |
| **任务中心** | 任务卡片样式、进度条设计、状态标签（运行中/成功/失败）、日志展开 | 原型 `.task-card` / `.progress-bar` |
| **空状态** | 空列表的插图+引导文案+操作按钮样式 | 原型 `.empty-state` 组件 |
| **滚动条** | 自定义滚动条样式（细窄、靠边、hover显示），禁止浏览器默认粗滚动条 | 原型 `::-webkit-scrollbar` 样式 |
| **间距规范** | 页面padding、卡片间距、元素间距的统一数值（提取为spacing变量） | 原型中各容器的padding/margin |
| **圆角规范** | 卡片/按钮/输入框/弹窗的圆角半径统一 | 原型中各元素的border-radius |

**布局铁律（从原型继承，违反即失败）**：
1. **沉浸式全宽**：内容区铺满窗口宽度，禁止左右大量留白
2. **内部独立滚动**：每个栏（左侧/中间/右侧）独立滚动，禁止整页上下滚动
3. **无重复标题**：顶部导航已有"科研端"，页面内不再放"文献库""写作论文"等大标题
4. **工具栏整合**：页面操作按钮整合进顶部工具栏（surface-head），不单独占一行
5. **无圆角阴影包裹**：内容区直接铺背景色，不用surface卡片圆角阴影包裹（沉浸式）
6. **三栏比例**：左侧窄栏（240-280px）+ 中间弹性宽栏 + 右侧中栏（320-400px），可折叠

**与现有主题的融合**：
- 先读取 `D:\frontend\src\styles\` 下的现有主题变量和全局样式
- 原型中的颜色变量与现有主题冲突时，以原型为准（科研端有独立的设计语言），但通过CSS作用域隔离（`.research-end` 前缀），不影响学生端/教师端
- 字体、基础组件（输入框/下拉/弹窗）优先复用现有组件库，样式通过CSS变量覆盖对齐原型

---

## 八、开发执行计划（你必须按此顺序执行）

### 阶段0：调研与架构设计（2小时）
1. 完成第二章要求的调研，输出架构笔记
2. 设计数据库表结构（写出完整的Alembic迁移计划）
3. 设计API接口清单（RESTful，列出所有端点）
4. 设计前端页面结构和组件树
5. 创建子智能体（如果需要）

### 阶段1：后端基础设施（4小时）
1. 在 `config.py` 添加科研端配置项
2. 创建 `.env` 文件并填入API密钥
3. 创建 `app/domains/research/` 目录结构
4. 设计并创建数据库模型（`app/models/research.py`）
5. 生成并执行Alembic迁移
6. 创建 `app/gateway/research_router.py`（扩展现有，注册所有路由）
7. 实现认证和权限校验

### 阶段2：后端核心API（6小时）
1. 文献库CRUD + 搜索 + 筛选 + 排序 + 批量操作
2. PDF上传 + 解析后台任务
3. 标注/笔记CRUD
4. 文稿CRUD + 自动保存 + 版本
5. LaTeX编译后台任务
6. 素材/引用管理
7. 任务中心API
8. AI助手API（LangGraph + Function Calling + SSE）
9. 博查搜索封装 + 缓存

### 阶段3：前端基础设施（4小时）
1. 安装必要依赖（Naive UI/pdf.js/CodeMirror 6）
2. 创建 `src/api/research.js`（所有API封装）
3. 创建 `src/stores/research.js`（Pinia状态管理）
4. 创建 `src/layouts/ResearchLayout.vue`（科研端布局）
5. 配置路由（`router/index.js` 添加科研端路由）
6. 创建通用组件（加载/空状态/确认弹窗/错误提示）

### 阶段4：前端核心页面（8小时）
1. 文献库页面（列表/搜索/筛选/分类/导入/批量操作/详情面板）
2. 阅读器页面（PDF渲染/标注/笔记/目录/搜索/AI浮层）
3. 写作页面（文稿列表/编辑器/编译/PDF预览/悬浮球/素材箱/AI建议）
4. 任务中心页面
5. AI对话面板组件（可复用，SSE流式）

### 阶段5：集成与联调（4小时）
1. 前后端联调（每个功能点测试）
2. 与学生端/教师端的集成测试（确保不破坏现有功能）
3. 登录/注册/角色切换测试
4. 统一入口 `/hub` 跳转测试

### 阶段6：测试与修复（4小时）
1. 后端单元测试（核心service层）
2. 前端组件测试（关键组件）
3. E2E测试（核心流程：上传论文→阅读→写作→编译）
4. Bug修复
5. 性能优化（列表虚拟滚动/图片懒加载/API缓存）

### 阶段7：文档与交付（2小时）
1. 编写 `D:\math-arena\docs\research-end-dev-guide.md`（开发指南：启动方式/API文档/配置说明）
2. 编写数据库表结构文档
3. 验证所有功能可运行
4. 输出交付清单

---

## 九、验收标准（逐条检查，全部通过才算完成）

### 9.1 架构验收
- [ ] 后端代码在 `D:\math-arena\services\api\app\domains\research\` 下
- [ ] 前端代码在 `D:\frontend\src\pages\research\` 下
- [ ] 共用同一个FastAPI应用和同一个Vue应用，无独立端口
- [ ] 数据库表通过Alembic迁移创建，表名 `research_` 前缀
- [ ] 配置项在 `config.py` 中，密钥在 `.env` 中，代码中无硬编码密钥
- [ ] 学生端/教师端所有现有功能不受影响

### 9.2 功能验收（每个按钮都能点，每个操作都有响应）
- [ ] 文献库：上传PDF→自动解析→列表显示→搜索→筛选→排序→分类→批量操作→删除→恢复，全部可用
- [ ] 知识库：PDF解析后结构化提取（章节/定义/定理/公式/参考文献）→正文分块→向量化(pgvector)→混合检索(BM25+语义)→AI回答带引用来源，全部可用
- [ ] 阅读器：打开PDF→翻页→缩放→目录→搜索→标注→笔记→AI解释→引用查看，全部可用
- [ ] 写作：新建文稿→编辑LaTeX→编译→PDF预览→自动保存→文献悬浮球→插入引用→素材管理→AI建议→导出，全部可用
- [ ] AI助手：对话→流式输出→工具调用→引用来源→中断，全部可用
- [ ] 任务中心：查看任务→进度→日志→重试→取消，全部可用

### 9.3 用户体验验收
- [ ] 每个异步操作有loading状态
- [ ] 每个错误有友好提示
- [ ] 每个空列表有空状态引导
- [ ] 每个删除操作有确认弹窗
- [ ] 页面无整页滚动，内容区内部滚动
- [ ] 无大量留白，布局铺满窗口
- [ ] 无重复页面标题
- [ ] 按钮样式统一，无粗糙按钮

### 9.4 原型一致性验收（与v2高保真原型逐条对比）
- [ ] 颜色体系与原型一致（主色/辅助色/背景/文字/边框色值对齐）
- [ ] 文献库布局与原型一致（左侧分类树+中间列表+右侧详情面板，三栏比例对齐）
- [ ] 写作页布局与原型一致（左侧文稿列表+中间编辑器+右侧PDF预览/素材/AI建议）
- [ ] 阅读器布局与原型一致（顶部工具栏+左侧目录+中间PDF+右侧标注/笔记/AI面板）
- [ ] 顶部导航样式与原型一致（无独立"阅读"导航，文献点击直接进阅读）
- [ ] 文献悬浮球与原型一致（右下角位置、呼出动画、搜索+列表+预览+插入引用/加入素材/AI解释浮层）
- [ ] 阅读页选区浮层与原型一致（选中文本后弹出解释/翻译/公式推导/提问/保存笔记）
- [ ] 论文卡片/文稿卡片样式与原型一致（圆角/阴影/hover态/元信息排版）
- [ ] 按钮体系与原型一致（主/次/文字/危险按钮的样式、尺寸、状态）
- [ ] AI对话面板与原型一致（消息气泡/流式打字/引用来源标注/工具调用状态）
- [ ] 任务中心与原型一致（任务卡片/进度条/状态标签/日志展开）
- [ ] 空状态与原型一致（插图+引导文案+操作按钮）
- [ ] 滚动条与原型一致（细窄、靠边、自定义样式，非浏览器默认）
- [ ] 间距/圆角规范与原型一致（无随意的padding/margin/radius值）
- [ ] 页面操作按钮整合进顶部工具栏，不单独占一行（与原型surface-head一致）
- [ ] 内容区无surface卡片圆角阴影包裹，直接铺背景色（沉浸式，与原型一致）

### 9.5 代码质量验收
- [ ] 无硬编码配置/密钥/API路径
- [ ] 无空壳子功能（每个UI元素有真实后端逻辑）
- [ ] 无魔法字符串/魔法数字（常量集中定义）
- [ ] 无同步阻塞IO（全部async/await）
- [ ] 数据库查询有user_id过滤（数据隔离）
- [ ] API有参数验证（Pydantic schema）

### 9.6 可运行验收
- [ ] 后端启动无报错（`uvicorn app.main:app --port 8000`）
- [ ] 前端启动无报错（`npm run dev`）
- [ ] 登录researcher角色后可访问 `/research`
- [ ] 完整流程可走通：注册→登录→上传论文→阅读→写作→编译→导出

---

## 十、子智能体协作指南

如果你创建子智能体，按以下分工：

| 子智能体 | 职责 | 交付物 |
|---|---|---|
| **后端架构师** | 数据库设计、模型定义、Alembic迁移、API接口设计、service层实现 | `app/domains/research/` 全部代码 + 迁移脚本 |
| **AI工程师** | LangGraph编排、Function Calling工具注册、SSE流式、RAG检索、博查搜索封装 | `app/services/research_ai/` 全部代码 |
| **前端架构师** | 布局组件（严格参考v2原型的沉浸式三栏布局）、路由配置、API封装、状态管理、通用组件、从原型提取CSS变量和设计token | `src/layouts/ResearchLayout.vue` + `src/api/research.js` + `src/stores/research.js` + `src/styles/research-theme.css` |
| **前端工程师** | 文献库页面、阅读器页面、写作页面、任务中心、AI面板（每个页面必须对照v2原型实现，样式/交互/布局与原型一致） | `src/pages/research/` 全部页面组件 |
| **测试工程师** | 后端单元测试、前端组件测试、E2E测试、Bug修复 | `tests/research/` 全部测试 + 修复记录 |

**子智能体协作规则**：
1. 你（主控）先完成阶段0的架构设计，再分配任务给子智能体
2. 每个子智能体完成后必须提交代码并通知你
3. 你负责代码审查和整合，确保风格一致、接口对齐
4. 前后端接口契约由你在阶段0定义，子智能体必须严格遵守
5. 如果子智能体遇到阻塞，立即由你介入解决

---

## 十一、紧急情况处理

- **依赖安装失败**：换用国内镜像源（npm淘宝镜像/pip清华镜像），或换用功能等价的库
- **API调用失败**：检查密钥是否正确、URL是否正确、模型ID是否正确，添加重试机制（最多3次，指数退避）
- **数据库迁移失败**：检查模型定义与迁移脚本一致性，回滚后重新生成
- **前端编译失败**：检查依赖版本兼容性，查看具体错误信息修复
- **现有功能被破坏**：立即回滚相关代码，排查原因后重新实现
- **时间不足**：优先保证P0核心流程（上传→阅读→写作→编译），次要功能可降级但必须有UI占位和"即将上线"提示

---

## 十二、最终交付物清单

1. **后端代码**：`D:\math-arena\services\api\app\domains\research\` + `app/gateway/research_router.py` + `app/models/research.py` + Alembic迁移脚本
2. **前端代码**：`D:\frontend\src\pages\research\` + `src/layouts/ResearchLayout.vue` + `src/api/research.js` + `src/stores/research.js`
3. **配置文件**：更新后的 `app/config.py` + `.env`（含API密钥）
4. **文档**：`D:\math-arena\docs\research-architecture-notes.md`（调研笔记）+ `D:\math-arena\docs\research-end-dev-guide.md`（开发指南）
5. **测试**：后端单元测试 + 前端组件测试 + E2E测试用例
6. **运行验证**：后端启动成功 + 前端启动成功 + 核心流程走通的截图或日志

---

> **最后提醒**：这是一个需要连夜完成的全栈开发任务。效率第一，但质量不能妥协。每个功能都必须真实可用，每个按钮都必须有响应。禁止用"TODO"、"占位"、"假数据"糊弄。如果某个功能确实无法在时间内完成，必须在文档中明确说明原因和后续计划，而不是默默留空。
>
> **现在开始执行。先做阶段0调研，然后按计划推进。**
