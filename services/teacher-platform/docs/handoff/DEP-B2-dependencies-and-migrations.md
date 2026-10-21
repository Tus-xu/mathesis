# DEP-B2-dependencies-and-migrations.md（IFC 编号占位：B5 评审后定号）

- 提出方：B2 知识工程智能体
- 接收方：B3 平台工程智能体
- 日期：2026-09-06（M1 阶段）
- 性质：依赖申请（不改 B3 文件，等 bootstrap 合入时统一落实）

## 1. pyproject.toml 依赖申请（B3 所有权，B2 不直接编辑）

B2 模块运行所需（当前环境已装、版本经实测）：

| 依赖 | 实测版本 | 用途 | 归属说明 |
|---|---|---|---|
| sympy | 1.14.0 | G5 MathVerifier | |
| antlr4-python3-runtime | 4.11.0 | sympy.parsing.latex（LaTeX→SymPy，缺它验证器降为全不可判定） | **必须锁 4.11.x 或 4.13.x** |
| pgvector | 0.5.0 | Vector(1024) 列型 + cosine_distance | |
| sqlalchemy | 2.0.51 | B2 域模型（app/db/models/knowledge.py） | B3 主栈一致 |
| jinja2 | 3.1.6 | app/prompts 渲染 | |
| httpx | 0.28.1 | SiliconFlow embed/rerank、博查搜索 | |
| pyyaml | 6.0.2 | prompts/data/*.yaml、manifest 读取 | |
| pydantic | 2.12.5 | Verdict/契约模型 | |
| pytest / pytest-asyncio | 9.1.1 / 1.4.0 | tests/unit/rag | |

## 2. alembic 迁移申请（B2 域 4 表）

| 表 | 归属规范 | 关键约束（迁移必须含） |
|---|---|---|
| knowledge_documents | 架构 §4.10 | doc_ref UNIQUE；status/visibility CHECK；种子语料 school_id NULL + visibility='platform' |
| knowledge_chunks | 架构 §4.9 | embedding vector(1024)；(doc_ref, chunk_index) UNIQUE；collection 索引；向量索引（ivfflat/hnsw，B3 定） |
| web_search_cache | 架构 §4.9 | query_hash PK |
| memories | 架构 §4.9+§4.10 | layer CHECK IN ('L1','L2','L3')；source_refs jsonb；user_id 索引 |

注：knowledge_chunks 需 FK→knowledge_documents.doc_ref（B2 已按 CASCADE 建模）；
memories.content_hash 无列需求；B2 未申请新端点（KNR 红线）。

## 3. app/db/base.py 依赖

app/db/models/knowledge.py 目前用「try import app.db.base.Base，失败则本地 DeclarativeBase」的守卫导入。
B3 bootstrap 落地 app/db/base.py 后自动切换到统一 Base——无需改 B2 文件；若 B3 的 Base 路径不同，
请在本 handoff 文件下追加一行说明，B2 跟进。

## 4. 会话工厂注入点

Retriever / KnowledgeWorkspace / PgVectorCragBackend 构造参数为 `session_factory`
（SQLAlchemy async_sessionmaker）。B3 在应用装配时注入；装配代码位置由 B3 定，
B2 在 app/rag/__init__ 提供 `default_registry()` 同级的工厂函数占位。

## 5. katex-validator 依赖（B4）

corpus/tools/katex_check.mjs 当前经 KATEX_DIR 指定 KaTeX 目录（默认 frontend/node_modules/katex，MIT，dev 质检工具；node_modules 不随仓库分发，需先在 frontend/ 执行 pnpm install）。
B4 的 teacher-katex:8105 sidecar 就绪后，管线质检切换为调用 8105（本脚本保留为离线兜底）。

## 6. 数据事实知会（非申请）

- GAOKAO-Bench 数学子集实测 844 条（去重+KaTeX 过滤后 785 条 staged）；**M1「≥1000 题」不可达**，
  缺口见 corpus/sources/manifest.toml gaokao_bench.notes；补源候选 atmk_system 待许可审查。
- K12-KGraph 仓库内仅单册 demo（157 节点）；全量数学子图（1475 概念/428 技能）在 HuggingFace
  发布版，需网络下载后重跑 corpus/pipelines/kp_seed.py（幂等）。
