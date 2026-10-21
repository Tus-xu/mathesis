# 迁移完整性审计报告 · ZXSY Math Arena

> **审计日期**：2026-10-21
> **审计对象**：`D:\zxsy-math-arena`（新组装仓库）
> **对照基线**：`D:\math-arena`（旧后端 monorepo）、`D:\frontend`（旧前端）
> **审计性质**：**只读审计**。除本报告文件外，未对任何目录执行写入/删除/修改，未执行任何 git 写操作。
> **执行者**：software-architect
>
> **闭环更新（2026-10-22）**：§4 的 6 项 P0 已全部完成（P0-1~5 见 `d25cd29`；P0-6 硬编码路径清理见 `d6f2b41`，77 files / +338 / -117，保留清单外 grep 0 命中）。§5 的 9 项 P1 与 §2.7 的 P2 事项仍未闭环，清单以本节原记录为准。

---

## 0. 结论前置（TL;DR）

### 0.1 能不能在 `D:\zxsy-math-arena` 继续升级开发？

**能，但必须先补齐 6 件 P0 事项、9 件 P1 事项。** 结论分三层：

| 层面 | 判定 | 说明 |
|---|---|---|
| **源码完整性** | ✅ **通过** | 前端 `src/` / `test/` / `public/` / `scripts/` 逐字节一致；后端 `services/api/app` 源码仅 18 个文件存在**纯 lint 级**差异（isort 排序 + `selectors` 死导入 + `SIM` 简化），**零功能缺失**；alembic 42 个 revision、唯一 head `tel_001_two_end_link`，新旧完全一致 |
| **可运行性** | ⚠️ **有条件通过** | 当前仓库**缺 5 个运行时必需文件**（见 §3.1 A1–A5），其中 `services/teacher-platform/main.py` 缺失导致教师平台 Docker 镜像 `CMD uvicorn main:app` 必然启动失败；`frontend/.impeccable/design.json` 缺失导致 **1 个 vitest 用例直接报错**。补齐后可跑 |
| **可维护性** | ⚠️ **需整改** | 新仓库**没有 `.gitignore`**（旧目录有 5220 字节完整版），`.venv/`、`node_modules/`、`__pycache__/`、`.env` 一旦存在就会被 git 吞进索引；50+ 处 `D:\math-arena` / `D:\frontend` 硬编码路径将在封存后全部失效 |

**一句话结论**：新仓库的**源码与配置骨架是完整的、可以接续开发**；缺的是 **5 个运行时文件 + 1 份 .gitignore + 一次硬编码路径清理 + 一批交付物归档**。做完 §4 的 P0 清单即可开工；P1 做完可安心封存旧目录。

### 0.2 必须先补的 N 件事

| 优先级 | 数量 | 关键项 |
|---|---|---|
| **P0** | **6** | 补 `services/teacher-platform/main.py`；补 `frontend/.impeccable/design.json` + `.impeccable.md`；补根 `.gitignore` + `frontend/.gitignore`；补 `alembic.ini`×2 + `uv.lock`×3；补 22 个 e2e 验收脚本；清理 50+ 处硬编码旧绝对路径 |
| **P1** | **9** | 搬 22 个 e2e 脚本对应的 `deliverables/` 验收截图入库；补 `frontend/tools/prepc`（12MB 视觉判据脚本）；补 `frontend/baseline/VISUAL-BASELINE.md`；补 `frontend/docs/teacher-prep-center/*.md`；补 `services/teacher-platform/AGENTS.md`；补 `VERSIONS.md`；补 `deploy/DEPLOY-20261005-三端上线.md`；补 `docs/superpowers/` + `docs/workbuddy/`（34 份设计/审计文档）；补 `frontend/teacher-design-showcase.design` |
| **P2** | **6** | 归档 `deliverables/` 535MB（建议 Git LFS 或外部归档）；归档 `exports/` 715MB、`logs/` 177MB、`tmp/` 890MB、`.models/` 4.5GB；修正 `AGENTS.md` 权威命令表与根 `Makefile` 语义冲突；`packages/protocol` 目前无消费方，需明确去留；补 `services/data/` 语义说明；`.editorconfig` 缺失 |

---

## 1. 逐目录差异审计

### 1.1 顶层对照

| 顶层项 | 旧目录 | 新仓库 | 判定 |
|---|---|---|---|
| `frontend/` | `D:\frontend` | ✅ 已搬（`src`/`test`/`public`/`scripts`/`e2e` 部分） | 见 §1.3 |
| `services/` | `D:\math-arena\services`（8 个含 `data/`） | ✅ 7 个服务已搬，`services/data/` 未搬 | A5 |
| `packages/` | ✅ 一致（仅多 `node_modules`） | ✅ 完全一致 | OK |
| `docs/` | 旧含 `superpowers/` `workbuddy/` 两个子目录 | ❌ 缺 34 份文档 | **A7（C类）** |
| `data/` | `file-uploads/`（40MB 运行期上传） | ✅ 已换成 `kb-seed/` + `papers-seed/`（32KB 种子） | 合理替换 |
| `tools/` | 多 `prepc/` | ✅ 核心 3 项已搬 | OK |
| `scripts/` | 24 项 | ✅ 6 项 | 部分，见 §1.4 |
| `deploy/` | ✅ | ✅ + 新增 `init-db.sql` | 增强 |
| `.github/` | ✅ | ✅ **完全一致**（ci.yml + PR 模板 byte-identical） | OK |
| `AGENTS.md` | 27055 B | 27055 B **byte-identical** | OK，但内容已过时，见 §5 |
| `README.md` | 4672 B | 3736 B（重写版） | OK |
| `Makefile` | 1726 B | 2323 B（重写版） | OK，但与 AGENTS.md 冲突，见 §5 |
| `package.json` / `pnpm-lock.yaml` / `pnpm-workspace.yaml` | ✅ | ✅ **三者 byte-identical** | OK |
| `.env.example` | 8806 B | 2945 B（重写版，删了 70 个键） | ⚠️ 见 §1.5 |
| `.gitignore` | 5220 B | ❌ **完全缺失** | **A3（P0）** |
| `.editorconfig` | 399 B | ❌ 缺失 | P2 |
| `VERSIONS.md` | 1917 B | ❌ 缺失 | A8（C类） |

### 1.2 A 类 —— 源码/配置缺失，**必须搬**

| ID | 路径 | 缺失原因 | 影响 | 优先级 |
|---|---|---|---|---|
| **A1** | `D:\math-arena\services\teacher-platform\main.py`（163 行） | 组装时按「只搬 `app/` 子包」过滤，漏掉服务根 `main.py` | **致命**：该服务 `Dockerfile:34` 是 `CMD ["sh","-c","uv run alembic upgrade head && uv run uvicorn main:app --host 0.0.0.0 --port 8100"]`，`main:app` 模块不存在 → **教师平台容器 100% 启动失败**；`deploy/docker-compose.server.yml:231` 的 `teacher-api` 服务依赖它 | **P0** |
| **A2** | `D:\frontend\.impeccable\design.json` + `D:\frontend\.impeccable.md` | 组装时被当作「设计元数据」排除 | **致命**：`frontend/test/visual/semantic-token-governance.test.ts:51` 执行 `readFileSync(resolve(process.cwd(), '.impeccable/design.json'))` → **该 vitest 用例直接抛 ENOENT 崩溃**，整条测试文件失败。`.impeccable.md` 另被 7 处源码/测试注释引用（`StructuredCard.vue:154,202,271`、`JobStatus.vue:9`、`i18n/copy.ts:530`、`i18n/labels.ts:4`、`MessageItem.vue:259`） | **P0** |
| **A3** | `D:\math-arena\.gitignore`（5220 B）+ `D:\frontend\.gitignore`（1130 B） | 组装时按「隐藏文件」跳过 | **严重**：新仓库 `git check-ignore` 对 `frontend/node_modules`、`services/api/.venv` 全部返回 exit=1（未被忽略）。一旦执行 `pnpm install` / `python -m venv .venv`，`git add .` 会把 **数百 MB 依赖 + `__pycache__` + `.env` 真实密钥**吞进索引。实测 `git status --untracked-files=all` 已列出 300+ 个 `.pyc` | **P0** |
| **A4** | `alembic.ini`×2：`D:\math-arena\services\research-vnext\alembic.ini`、`D:\math-arena\services\ppt-studio-v2\alembic.ini` | 组装时按「.ini 视为本地配置」过滤 | **严重**：二者 `alembic/`（或 `migrations/`）目录与 `versions/` 都在，但缺 ini → `alembic upgrade head` 找不到 `script_location`。`Makefile` 的 `migrate-research` / `migrate-teacher` 目标直接依赖 | **P0** |
| **A5** | `uv.lock`×3：`services/api/uv.lock`（617KB）、`services/teacher-platform/uv.lock`、`services/ppt-studio-v2/uv.lock`（71KB） | 组装时按「lock 文件视为构建产物」过滤 | **严重**：`teacher-platform/Dockerfile:22` 与 `ppt-studio-v2/Dockerfile:23` 都是 `COPY pyproject.toml uv.lock ./` → **docker build 直接失败**（COPY 源不存在）。且 `teacher-platform/Dockerfile` 的 uv.lock 还带清华镜像 URL 改写 sed，丢了它连构建速度都回退 | **P0** |
| **A6** | `services/teacher-platform/main.py` 之外的 `services/api/setup.cfg`（183 B，声明 12 个 package 的 setuptools 显式包列表） | 过滤 | 中等：`pip install -e .` 时若 setuptools 自动发现失败，包列表丢失 | P1 |
| **A7** | `D:\frontend\e2e\*.mjs` **22 个验收脚本** | 组装时按「e2e 目录只搬 `.spec.ts`」过滤 | **严重**：这 22 个脚本正是 `deliverables/` 下 535MB / 2425 张验收截图的**唯一生成器**。例：`research-ux-r1.mjs:6` → `outDir='D:/math-arena/deliverables/research-ux-r1'`；`student-butler-r2.mjs:25`、`student-library-research-r1.mjs:13`、`two-end-link-acceptance.mjs:31`、`quiz-figure-fix-r1.mjs:7`。**不搬 = 旧截图不可再生 = README 的验收证据链断裂** | **P0** |
| **A8** | `D:\frontend\tools\`（12MB，`prepc/` 134 个 `.mjs` 视觉判据脚本 + `fe-ppt-r14/`） | 组装时按「工具目录」过滤 | 中等：`docs/product/teacher-prep-center/KERNEL-RUBRIC.md:121` 明确写「截图工具 `D:/frontend/tools/prepc/pc_lib.mjs`（主控自建）」→ 文档已指向新仓库内不存在的文件 | P1 |
| **A9** | `D:\frontend\baseline\VISUAL-BASELINE.md` | 过滤 | 中等：`docs/product/teacher-prep-center/V2-BACKEND-KERNEL-GUIDE.md:5` 明确引用「视觉基线 `D:/frontend/baseline/VISUAL-BASELINE.md`」 | P1 |
| **A10** | `D:\frontend\docs\teacher-prep-center\V2-FRONTEND-VISUAL-GUIDE.md`（7257 B）+ `V2-VISUAL-GATE.md`（8211 B） | 过滤 | 中等：`frontend/AGENTS.md:55` 与 `V2-BACKEND-KERNEL-GUIDE.md:5` 都把它列为「前端视觉/工程判据」最高依据 | P1 |
| **A11** | `D:\math-arena\services\teacher-platform\AGENTS.md`（8KB） | 过滤 | 中等：该文件是**服务级规则**（`uv run` 命令、Python 3.12 约束、「禁止运行 make up / make seed / 任何写库命令」），根 `AGENTS.md` 声明「近目录规则优先」→ 缺它则该服务失去专属约束 | P1 |
| **A12** | `D:\math-arena\VERSIONS.md`（1917 B，版本锁定表） | 过滤 | 中等：表里锁定的 `vue ^3.4 / element-plus ^2.7 / fastapi ^0.110 / pgvector / PostgreSQL 15 / Redis 7` 等与实际 `package.json`（`vue ^3.5.39`、`naive-ui ^2.45.3`）**已经漂移** → 需人工校对后搬入或重写 | P1 |
| **A13** | `D:\math-arena\deploy\DEPLOY-20261005-三端上线.md` | 过滤 | 中等：记录 <your-server-ip> 上的 13 容器上线状态（镜像名 `math-arena-*`），是**唯一**的现网部署事实来源 | P1 |
| **A14** | `D:\math-arena\docs\superpowers\`（304KB，24 份 plans+specs）+ `docs\workbuddy\`（136KB，10 份 prompts+审计） | 过滤 | 中等：含 `2026-10-20-teacher-student-classroom-design.md`、`2026-09-01-student-error-book-assets-design.md`、`2026-08-29-dual-teacher-grounded-rag-design.md` 等**未落地的设计契约** | P1 |
| **A15** | `D:\frontend\teacher-design-showcase.design\index.html` + `styles/`（38KB 设计稿） | 过滤 | 低：设计展示稿 | P2 |
| **A16** | `D:\frontend\r3_sse_probe.mjs`（2704 B） | 过滤 | 低：SSE 探针 | P2 |
| **A17** | `D:\frontend\.trae_write_probe.txt` | 过滤 | 低 | P2 |

### 1.3 B 类 —— 有价值但可再生 / 可重新生成

| 路径 | 大小 | 判定理由 |
|---|---|---|
| `D:\frontend\dist\` `dist-old-2026*`（8 个） `dist-prev-quizfix` `dist-prod` `dist-verify-r5` `dist-old-20261015` | — | **不搬**。Vite 构建产物，`pnpm build` 可完全再生；且旧目录已封存，`dist` 无价值 |
| `D:\frontend\.dist_prev_rv16\` `.dist_tmp2\` | — | 同上 |
| `D:\frontend\node_modules\` `D:\math-arena\node_modules\` `packages\protocol\node_modules` | — | **不搬**，`pnpm install` 再生 |
| `D:\math-arena\.venv`（各 service） `research-vnext\.parser-venv` | — | **不搬**，`uv sync` / `pip install -e .` 再生。**但 `uv.lock` 必须搬**（见 A5） |
| `D:\math-arena\.models\`（**4.5GB**，`bge-m3` 2.3G + `bge-m3-repair` + `modelscope-cache`） | 4.5GB | **不搬**（体积爆炸且非源码），但**必须把 `scripts/bge_m3_server.py:34` 和 `services/api/scripts/local_embedding_server.py:15` 的硬编码 `D:\math-arena\.models\bge-m3` 改成环境变量 `BGE_M3_PATH`**——这是 §6 的 P0 项 |
| `D:\math-arena\services\api\data\` `services\api\logs\` `services\api\.tmp_e2e_out` `services\api\math_arena_api.egg-info` | 4KB+908KB+2MB | **不搬**，运行期产物 |
| 所有 `__pycache__` `.ruff_cache` `.pytest_cache` `.mypy_cache` | — | **不搬** |
| `D:\frontend\test-results\`（1KB）、`vitest.config.ts.timestamp-*.mjs`（12 个） | — | **不搬**，测试产物 / Vite 临时文件 |
| `D:\frontend\tmp_*.out`（8 个：`tmp_card.out` `tmp_git.out` `tmp_login.out` `tmp_msgs.out` `tmp_schema.out` `tmp_sed.out` `tmp_sms.out` `tmp_svc.out` `tmp_typecheck.out` `tmp_verify.out`） | 20KB | **不搬**，临时输出 |
| `D:\math-arena\assets\`（1.9MB，KaTeX 字体 + `chat-fixture-*.js/css`） | 1.9MB | **不搬**，前端 `public/` 已有等价物；已确认 `frontend/public/` 与旧目录 byte-identical |
| `D:\math-arena\nul`（Windows 误建空文件）、`prep_ec80.json`（114 B） | 0 | **不搬**，垃圾文件 |
| `D:\math-arena\scripts\_probe_out.txt` `_rb_dry.txt` | — | **不搬**，临时输出 |

### 1.4 C 类 —— 资产 / 交付物，**建议搬入仓库或归档**

#### 1.4.1 单一视觉真源（**5 张，已在新仓库** ✅）

| 文件 | 尺寸 | 大小 | 状态 |
|---|---|---|---|
| `D:\zxsy-math-arena\docs\research-workspace\golden\READ-GOLDEN.png` | 1672×941 | 1.45MB | ✅ 已存在，git 已跟踪 |
| `D:\zxsy-math-arena\docs\research-workspace\golden\WRITE-GOLDEN.png` | 1672×941 | 1.64MB | ✅ 已存在，git 已跟踪 |
| `D:\zxsy-math-arena\docs\product\teacher-prep-center\golden\CLASSROOM-GOLDEN.png` | 1672×941 | 1.30MB | ✅ 已存在，git 已跟踪 |
| `D:\zxsy-math-arena\docs\product\teacher-prep-center\golden\LESSONPLAN-GOLDEN.png` | 1672×941 | 1.60MB | ✅ 已存在，git 已跟踪 |
| `D:\zxsy-math-arena\docs\product\teacher-prep-center\golden\PPT-GOLDEN.png` | 1672×941 | 1.31MB | ✅ 已存在，git 已跟踪 |

这 5 张是 `AGENTS.md` §0 定义的「唯一视觉真源」，**全部已完整迁移并入库**。旧目录对应位置：`D:\math-arena\docs\product\teacher-prep-center\golden\`（3 张）+ `D:\math-arena\deliverables\research-workspace\compare\`（read-blend 等中间产物）。

> **体积警告（2026-10-21 追加实测）**：5 张 Golden 合计 **7.3MB**。仓库内 `docs/readme/` 已由 `tools/prepare_readme_assets.py` 压过一版（最大宽 1440），但**仍全部超出 README Hero 位 400KB 预算**：
> `golden-read.png` 462KB、`golden-teacher-classroom.png` 624KB、`golden-teacher-lessonplan.png` 742KB、`golden-teacher-ppt.png` 635KB、`golden-write.png` 562KB。
> 同批的普通功能截图（`student-*.png` / `teacher-*.png` / `research-*.png`）压后均在 104–190KB，达标。→ **Golden 需要二次压缩**（建议 PNG8 量化 + 降宽到 1200，目标 ≤250KB；正文位 ≤250KB）。

#### 1.4.2 验收截图全量候选清单（真实路径 / 尺寸 / mtime）

**主候选集 · `D:\math-arena\deliverables\demo-seed-verify-141\`（17 张）**
> 由 `frontend/e2e/demo-seed-verify.mjs` 生成；配套 `results.json` 记录 **20 项断言全部 `ok: true`、`errors: []`**。这是**唯一一份带机器可读验收断言的全链路截图集**。

| 绝对路径 | 尺寸 | 字节 | mtime | 归属 | 展示功能（依据 results.json） |
|---|---|---|---|---|---|
| `D:\math-arena\deliverables\demo-seed-verify-141\01-student-home.png` | 1500×980 | 255416 | 2026-10-21T16:46:10 | 学生端 | 学生首页，**学情脉搏可见**（BKT/FSRS） |
| `D:\math-arena\deliverables\demo-seed-verify-141\02-derivative-followup.png` | 1500×980 | 177701 | 2026-10-21T16:46:12 | 学生端 | 导数追问，**对话含追问与完整回答** |
| `D:\math-arena\deliverables\demo-seed-verify-141\02-socratic-guide.png` | 1500×980 | 170504 | 2026-10-21T16:46:13 | 学生端 | 苏格拉底引导，**引导选项=3，完成卡=1** |
| `D:\math-arena\deliverables\demo-seed-verify-141\03-variant-chain.png` | 1500×980 | 172058 | 2026-10-21T16:46:15 | 学生端 | 举一反三变式链，**变式卡=1，选项=4** |
| `D:\math-arena\deliverables\demo-seed-verify-141\03b-variant-judged.png` | 1500×980 | 198316 | 2026-10-21T16:46:15 | 学生端 | 变式判分结果，**「回答正确！再讲一遍思路 +1 掌握」** |
| `D:\math-arena\deliverables\demo-seed-verify-141\04-visual-figure.png` | 1500×980 | 167567 | 2026-10-21T16:46:17 | 学生端 | 动态数学图形，**图形帧=1，演示按钮=2** |
| `D:\math-arena\deliverables\demo-seed-verify-141\04b-figure-annotated.png` | 1500×980 | 172418 | 2026-10-21T16:46:18 | 学生端 | 图形可切换帧，**可切换帧=2** |
| `D:\math-arena\deliverables\demo-seed-verify-141\05-errors.png` | 1500×980 | 248535 | 2026-10-21T16:46:21 | 学生端 | **错题本有记录** |
| `D:\math-arena\deliverables\demo-seed-verify-141\06-library.png` | 1500×980 | 201021 | 2026-10-21T16:46:23 | 学生端 | 知识库资料，**资料=4** |
| `D:\math-arena\deliverables\demo-seed-verify-141\07-classroom.png` | 1500×980 | 156389 | 2026-10-21T16:46:27 | 学生端 | 双师课堂，**课堂/作业可见** |
| `D:\math-arena\deliverables\demo-seed-verify-141\07b-courseware-slide.png` | 1500×980 | 180078 | 2026-10-21T16:46:30 | 学生端 | 课件正文（**导数 人教A版必修一**）+ 讲稿 5 句/156 字 |
| `D:\math-arena\deliverables\demo-seed-verify-141\08-report.png` | 1500×980 | 278584 | 2026-10-21T16:46:33 | 学生端 | **学情报告可见** |
| `D:\math-arena\deliverables\demo-seed-verify-141\09-teacher-classes.png` | 1500×980 | 217884 | 2026-10-21T16:46:40 | 教师端 | 班级管理，**班级可见** |
| `D:\math-arena\deliverables\demo-seed-verify-141\10-teacher-grading.png` | 1500×980 | 233564 | 2026-10-21T16:46:43 | 教师端 | **AI 批改工作台有作业** |
| `D:\math-arena\deliverables\demo-seed-verify-141\11-teacher-courseware.png` | 1500×980 | 243229 | 2026-10-21T16:46:46 | 教师端 | **双师课件可见** |
| `D:\math-arena\deliverables\demo-seed-verify-141\12-research-landing.png` | 1500×**1201** | 167516 | 2026-10-21T16:46:51 | 科研端 | 科研落地页，**科研项目=3** |
| `D:\math-arena\deliverables\demo-seed-verify-141\13-research-workspace.png` | 1500×980 | 250021 | 2026-10-21T16:46:54 | 科研端 | **PDF 画布=756px**，Agent 对话/结果可见 |
| `D:\math-arena\deliverables\demo-seed-verify-141\results.json` | — | 2305 | 2026-10-21T16:46 | （机器可读断言） | 20 项全 PASS，含 `no-console-errors: 无` |

> **⚠️ 批次选择校正（重要，2026-10-21 追加核验）**
>
> 旧目录下另有 `demo-seed-verify-162\` 与 `demo-seed-verify-local\` 两个同源副本。**141 才是最新批次**，不是 162：
>
> | 批次 | 文件数 | 最早 mtime | 最晚 mtime |
> |---|---|---|---|
> | `demo-seed-verify-local` | 18 | 16:44:19 | 16:45:03 |
> | `demo-seed-verify-162` | 18 | 16:45:13 | 16:45:59 |
> | **`demo-seed-verify-141`** | **18** | **16:46:10** | **16:46:54** |
>
> 更关键：**162 批次的 `05-errors.png` 存在 LaTeX 公式渲染回归**（像素级 diff `meanPixelDelta=12.44`、`diff bbox=(147,174,1415,846)`，远高于同批其它图的 0.005–4.06）。目视确认：141 批次的 AI 解答卡为**正常渲染的公式排版**；162 批次同一位置显示**未渲染的原始 LaTeX 源码**（裸露 `\frac{x}{2}`、`operatorname`、`a \ge 0`）；local 批次渲染正常。
>
> **结论：README 必须使用 `demo-seed-verify-141` 批次**；162 的 `05-errors.png` **不可用**。141 vs 162 逐图 md5 比对：`06-library`、`12-research-landing` 两张 SAME，其余 9 张 DIFF（即 162 是更早的中间批次）。
> 旁证：仓库内已存在的搬运脚本 `tools/prepare_readme_assets.py` 其 `OLD` 常量正是 `D:\math-arena\deliverables\demo-seed-verify-141`，选型与本结论一致。

**其余候选集（真实存在）**

| 绝对路径 | 尺寸 | mtime | 归属 | 展示功能 |
|---|---|---|---|---|
| `D:\math-arena\deliverables\student-butler-r2\10_dialog_initial.png` | 1500×940 | 2026-10-11T11:16 | 学生端 | 管家对话初始态 |
| `D:\math-arena\deliverables\student-butler-r2\11_after_mastery_question.png` | 1500×940 | 2026-10-11T11:16 | 学生端 | 掌握度追问后 |
| `D:\math-arena\deliverables\student-butler-r2\12_mastery_bkt_card.png` | 1500×940 | 2026-10-11T11:16 | 学生端 | BKT 掌握度卡 |
| `D:\math-arena\deliverables\student-butler-r2\13_after_retention_question.png` | 1500×940 | 2026-10-11T11:17 | 学生端 | 保持度追问后 |
| `D:\math-arena\deliverables\student-butler-r2\14_retention_card.png` | 1500×940 | 2026-10-11T11:17 | 学生端 | 保持度卡 |
| `D:\math-arena\deliverables\student-butler-r2\15_retention_zoom_modal.png` | 1500×940 | 2026-10-11T11:17 | 学生端 | 保持度放大弹窗 |
| `D:\math-arena\deliverables\student-butler-r2\16_after_reload_replay.png` | 1500×940 | 2026-10-11T11:17 | 学生端 | 刷新后回放 |
| `D:\math-arena\deliverables\student-butler-r2\17_butler_panel_reply.png` | 1500×940 | 356518 | 2026-10-11T11:17 | 学生端 | 管家面板回复（README 方案 §2.2 副图指定） |
| `D:\math-arena\deliverables\student-butler-r2\18_regression_video_cards.png` | 1500×940 | 2026-10-11T11:18 | 学生端 | 视频卡回归 |
| `D:\math-arena\deliverables\student-butler-r2\19_regression_quiz_cards.png` | 1500×940 | 2026-10-11T11:20 | 学生端 | 选择题卡回归 |
| `D:\math-arena\deliverables\student-butler-r2\22_review_nudge_retention.png` | 1500×940 | 2026-10-11T11:12 | 学生端 | 复习提醒 · 保持度 |
| `D:\math-arena\deliverables\student-library-research-r1\01-student-home.png` | 1500×980 | 2026-10-21T02:53 | 学生端 | 学生首页 |
| `D:\math-arena\deliverables\student-library-research-r1\02-library-materials.png` | 1500×980 | 2026-10-21T02:53 | 学生端 | 知识库资料列表 |
| `D:\math-arena\deliverables\student-library-research-r1\03-library-after-upload.png` | 1500×980 | 2026-10-21T02:53 | 学生端 | 上传后知识库 |
| `D:\math-arena\deliverables\student-library-research-r1\04-library-doc-drawer.png` | 1500×980 | 2026-10-21T02:53 | 学生端 | 文档抽屉 |
| `D:\math-arena\deliverables\student-library-research-r1\05-library-ask-in-dialog.png` | 1500×980 | 2026-10-21T02:53 | 学生端 | 库内追问 |
| `D:\math-arena\deliverables\student-library-research-r1\06-library-questions.png` | 1500×980 | 2026-10-21T02:53 | 学生端 | 库内提问列表 |
| `D:\math-arena\deliverables\student-library-research-r1\07-question-drawer.png` | 1500×980 | 2026-10-21T02:53 | 学生端 | 问题抽屉 |
| `D:\math-arena\deliverables\student-library-research-r1\08-research-landing-empty.png` | 1500×1201 | 2026-10-21T02:47 | 科研端 | 科研落地页（空态） |
| `D:\math-arena\deliverables\student-library-research-r1\10-research-landing-with-project.png` | 1500×1201 | 2026-10-21T02:53 | 科研端 | 科研落地页（有项目） |
| `D:\math-arena\deliverables\student-library-research-r1\11-research-account-profile.png` | 1500×1000 | 2026-10-21T02:54 | 科研端 | 账号资料 |
| `D:\math-arena\deliverables\student-library-research-r1\12-research-account-security.png` | 1500×1000 | 2026-10-21T02:54 | 科研端 | 账号安全 |
| `D:\math-arena\deliverables\student-library-research-r1\13-research-account-overview.png` | 1500×1000 | 2026-10-21T02:54 | 科研端 | 账号总览 |
| `D:\math-arena\deliverables\quiz-figure-fix-r1\01-derivative-variants.png` | 1500×1000 | 2026-10-21T12:20 | 学生端 | 导数变式 |
| `D:\math-arena\deliverables\quiz-figure-fix-r1\01b-variant-after-judge.png` | 1500×1000 | 2026-10-21T12:20 | 学生端 | 变式判分后 |
| `D:\math-arena\deliverables\quiz-figure-fix-r1\02-geometry-question.png` | 1500×1000 | 2026-10-21T12:20 | 学生端 | 几何题 |
| `D:\math-arena\deliverables\research-ux-r1\01-read-empty.png` | 1500×980 | 2026-10-21T13:29 | 科研端 | 阅读空态 |
| `D:\math-arena\deliverables\research-ux-r1\02-write-shell.png` | 1500×980 | 2026-10-21T13:29 | 科研端 | 写作 shell |
| `D:\math-arena\deliverables\research-ux-r1\03-selection-context.png` | 1500×980 | 2026-10-21T13:29 | 科研端 | **划词上下文**（README 方案 §2.4 可选第 3 张指定） |
| `D:\math-arena\deliverables\research-ux-r1\04-judge-running.png` | 1500×980 | 2026-10-21T13:29 | 科研端 | 校验运行中 |
| `D:\math-arena\deliverables\research-ux-r1\05-judge-result.png` | 1500×980 | 2026-10-21T13:29 | 科研端 | **校验结果** |
| `D:\math-arena\deliverables\two-end-link\evidence\01-teacher-after-login.png` | 1560×950 | 2026-10-12T13:04 | 教师端 | 教师登录后 |
| `D:\math-arena\deliverables\two-end-link\evidence\02-class-created-invite-code.png` | 1560×950 | 2026-10-12T13:04 | 教师端 | 建班 + 邀请码 |
| `D:\math-arena\deliverables\two-end-link\evidence\03a-student-class-empty.png` | 1440×950 | 2026-10-12T13:04 | 学生端 | 学生入班前（空态） |
| `D:\math-arena\deliverables\two-end-link\evidence\03b-student-join-toast.png` | 1440×950 | 2026-10-12T13:04 | 学生端 | **学生入班 toast** |
| `D:\math-arena\deliverables\two-end-link\evidence\03c-teacher-member-pending.png` | 1560×950 | 2026-10-12T13:04 | 教师端 | 成员待确认 |
| `D:\math-arena\deliverables\two-end-link\evidence\03d-teacher-member-confirmed.png` | 1560×950 | 2026-10-12T13:04 | 教师端 | 成员已确认 |
| `D:\math-arena\deliverables\two-end-link\evidence\04a-assign-form.png` | 1560×950 | 2026-10-12T13:04 | 教师端 | 作业布置表单 |
| `D:\math-arena\deliverables\two-end-link\evidence\04b-artifact-preview.png` | 1560×950 | 2026-10-12T13:04 | 教师端 | 作业预览 |
| `D:\math-arena\deliverables\two-end-link\evidence\04c-publish-success.png` | 1560×950 | 2026-10-12T13:04 | 教师端 | 发布成功 |
| `D:\math-arena\deliverables\two-end-link\evidence\05a-student-task-list.png` | 1440×950 | 2026-10-12T13:04 | 学生端 | 学生任务列表 |
| `D:\math-arena\deliverables\two-end-link\evidence\06-choice-auto-grade.png` | 1440×950 | 2026-10-12T13:05 | 学生端 | **选择题自动判分** |
| `D:\math-arena\deliverables\two-end-link\evidence\07c-photos-uploaded.png` | 1440×950 | 2026-10-12T13:05 | 学生端 | 拍照上传 |
| `D:\math-arena\deliverables\two-end-link\evidence\08b-grading-detail-photo.png` | 1560×950 | 2026-10-12T13:05 | 教师端 | 批改详情（带照片） |
| `D:\math-arena\deliverables\two-end-link\evidence\08c-grading-confirmed.png` | 1560×950 | 2026-10-12T13:05 | 教师端 | 批改确认 |
| `D:\math-arena\deliverables\two-end-link\evidence\09-student-sees-grade.png` | 1440×950 | 2026-10-12T13:15 | 学生端 | **学生看到批改结果** |
| `D:\math-arena\deliverables\two-end-link\evidence\10-teacher-insights.png` | 1560×950 | 2026-10-12T13:15 | 教师端 | 教师洞察 |
| `D:\math-arena\deliverables\two-end-link\evidence\11c-prep-final.png` | 1560×950 | 2026-10-12T13:15 | 教师端 | 备课完成 |
| `D:\math-arena\deliverables\prod-deploy-20261005\01-login.png` | 1672×941 | 2026-10-05T07:56 | 三端 | 登录页 |
| `D:\math-arena\deliverables\prod-deploy-20261005\03-research.png` | 1672×941 | 2026-10-05T08:09 | 科研端 | 科研端（生产） |
| `D:\math-arena\deliverables\prod-deploy-20261005\04-teacher-prep.png` | 1672×941 | 2026-10-05T08:11 | 教师端 | 备课中心（生产） |
| `D:\math-arena\deliverables\prod-deploy-20261005\06-bank.png` | 1672×941 | 2026-10-05T09:15 | 教师端 | 题库 |
| `D:\math-arena\deliverables\prod-deploy-20261005\07-textbook-card-restored.png` | 1672×941 | 2026-10-05T09:20 | 教师端 | 教材卡恢复 |
| `D:\math-arena\deliverables\teacher-prep-center\R-FINAL\01-home.png` | 1672×941 | 2026-09-23T04:20 | 教师端 | 备课中心首页 |
| `D:\math-arena\deliverables\teacher-prep-center\R-FINAL\02-classroom.png` | 1672×941 | 2026-09-23T04:34 | 教师端 | 课堂设计 |
| `D:\math-arena\deliverables\teacher-prep-center\R-FINAL\03-ppt.png` | 1672×941 | 2026-09-23T04:35 | 教师端 | PPT Studio |
| `D:\math-arena\deliverables\teacher-prep-center\R-FINAL\04-plan.png` | 1672×941 | 2026-09-23T04:35 | 教师端 | 教案 Studio |
| `D:\math-arena\deliverables\research-final-acceptance\evidence\screenshots\writing-workbench.png` | 1440×900 | 2026-09-07T17:46 | 科研端 | 写作工作台 |
| `D:\math-arena\deliverables\research-final-acceptance\evidence\screenshots\review-real-engine-l0l4.png` | 1440×900 | 2026-09-07T17:45 | 科研端 | 真引擎评审 |
| `D:\math-arena\deliverables\research-acceptance-v2\S03-假设证据链\workspace-claims-tab.png` | 1440×900 | 2026-09-08T11:42 | 科研端 | 主张 tab |
| `D:\math-arena\deliverables\research-acceptance-v2\S16-设计系统\screenshots\01-home-portal.png` | 1440×900 | 2026-10-08T10:38 | 科研端 | 首页门户 |
| `D:\math-arena\deliverables\release-50\evidence\hub-sso\04-teacher-today.png` | 1440×**1779** | 2026-09-08T14:05 | 教师端 | 教师今日（长页） |
| `D:\math-arena\deliverables\release-50\evidence\unified-journey\unified-journey.png` | 1440×900 | 2026-09-08T15:44 | 三端 | **统一旅程全链路** |
| `D:\math-arena\deliverables\research-frontend-screenshots\20-wp-k-product-empty-1440x900.png` | 1440×900 | 2026-09-13T10:19 | 科研端 | WP-K 产品空态 |
| `D:\math-arena\deliverables\research-frontend-screenshots\24-wp-k-product-narrow-760x800.png` | 760×800 | 2026-09-13T10:19 | 科研端 | 窄屏适配 |
| `D:\frontend\shots\workbench-v2-read.png` | 1440×900 | 2026-09-15T08:53 | 科研端 | 科研工作台「读」态 |
| `D:\frontend\shots\workbench-v2-write.png` | 1440×900 | 2026-09-15T08:53 | 科研端 | 科研工作台「写」态 |

#### 1.4.3 架构讲解 HTML / 交付文档（C 类，建议搬入 `docs/`）

| 路径 | 大小 | mtime | 内容 |
|---|---|---|---|
| `D:\math-arena\math-arena-个性化IDE联系与能力地图.html` | 109783 B | 2026-10-21T16:55 | 个性化 IDE 联系与能力地图 |
| `D:\math-arena\math-arena-可观测性四件套讲解.html` | 120009 B | 2026-10-21T16:54 | 可观测性四件套讲解 |
| `D:\math-arena\deliverables\architecture-handbook\智学数研-三端架构深度学习手册.html` | 124257 B | 2026-10-21T16:07 | **三端架构深度学习手册** |
| `D:\math-arena\deliverables\golden-r211-render\slide-01..10.png` | 1600×900 ×10 | 2026-10-20T01:35 | PPTX 真实渲染 10 页 |
| `D:\math-arena\deliverables\golden-B-r211.pptx` | — | — | Golden 课件源文件 |
| `D:\math-arena\deliverables\deploy-<your-server-ip>\` | — | — | 现网部署脚本 + `DEPLOY-NOTES.md` + `Dockerfile.server` + `nginx.conf` |
| `D:\math-arena\deliverables\vnext-autonomous\` | — | — | ADR 集 + 审计报告 + `CURRENT-TRUTH.md` + `ARTIFACT-INDEX.md` |
| `D:\math-arena\deliverables\student-refactor-v4\` | 140KB+ | — | 14 场景全链路审计 + `PROJECT_STATE.md`（25KB） |

**建议**：`docs/` 下建 `docs/handbook/`（3 份 HTML）、`docs/deliverables-archive/`（截图 + PPTX），用 **Git LFS** 存大图，或只入库 README 用的 17 张压缩版（见 §4 P1-1）。

### 1.5 D 类 —— 故意不搬（封存保留在旧目录）

| 路径 | 大小 | 理由 |
|---|---|---|
| `D:\math-arena\exports\`（4 个时间戳目录） | **715MB** | PPTX/文档导出产物，可由 ppt-studio-v2 重新生成 |
| `D:\math-arena\tmp\` | **890MB** | 临时工作区（`.bak` 文件、`_align*.sql`、`_apply*.json`），含 `PLATFORM-README.md` / `PLATFORM-TECHDOC.md` 两个可能有价值的 md，**可单独摘出** |
| `D:\math-arena\logs\` | **177MB** | 运行期日志 |
| `D:\math-arena\.models\` | **4.5GB** | 模型权重，非源码；用 `BGE_M3_PATH` 环境变量指向外部盘 |
| `D:\math-arena\.tools\`（67MB）、`.playwright-cli\`（9.9MB）、`.workbuddy\`（8.1MB） | 85MB | 工具缓存 / agent 本地状态 |
| `D:\math-arena\.learnings\`、`.arts\`、`.qoder\`、`.models\` | — | agent 本地状态 |
| `D:\frontend\dist*`（8 个）、`.dist_*`（2 个）、`node_modules` | — | 构建产物 |
| `D:\frontend\_deadcode_quarantine\`（45 文件） | — | **故意隔离**的死代码 + `GIT-INTEGRITY-INCIDENT.md`（2026-09-15 仓库完整性事故记录）。旧 `.gitignore` 已明确 `_deadcode_quarantine/` 不入库 → 保持隔离 |
| `D:\frontend\.worktrees\`、`.trae\`、`.learnings\`、`.workbuddy\` | — | agent 本地状态 |
| `D:\frontend\teacher-design-showcase.design\` | — | 设计稿，可选（C 类） |
| `D:\math-arena\.env`（7805 B，**含真实密钥**）+ 2 个 `.env.bak-before-scnet-*` | 15KB | **绝对不要搬入 git**。应迁到密钥管理（腾讯云 SSM / Vault / `.env` 本地 + 1Password），仓库只留 `.env.example` |
| 各 service 下的 `.env` + `.env.bak-*`（teacher-platform 8 个、research-vnext 7 个、ppt-studio-v2 1 个） | — | 同上，**真实密钥，禁止入库** |
| `D:\math-arena\nul`、`prep_ec80.json` | 0 | Windows 垃圾文件 |

---

## 2. 重点核查逐项结论

### 2.1 `VERSIONS.md` / `AGENTS.md` 三者关系

| 文件 | 旧目录 | 新仓库 | 结论 |
|---|---|---|---|
| `D:\math-arena\VERSIONS.md` | 1917 B ✅ | ❌ **缺失** | **必须搬或重写**（A12）。内容是「任何环境不一致先对表，禁止『我机器上能跑』」的版本锁定表，但**已与现实漂移**：表里写 `vue ^3.4` / `element-plus ^2.7`，实际 `frontend/package.json` 是 `vue ^3.5.39` / `naive-ui ^2.45.3`（Element Plus 已换成 Naive UI）。表里 `python ^3.11`，`AGENTS.md` 又写 teacher-platform 需 3.12。→ **搬入前必须逐行校对**，否则会成为误导源 |
| `D:\math-arena\AGENTS.md`（根） | 27055 B | 27055 B **byte-identical** ✅ | 内容完整，但**已过时**（见 §5） |
| `D:\zxsy-math-arena\AGENTS.md`（根） | — | 27055 B | 与旧根 AGENTS.md 完全一致 → **迁移无冲突，但也无更新** |

**规则冲突判定**：新仓库**只有一份根 `AGENTS.md` + 一份 `frontend/AGENTS.md`**（byte-identical 于 `D:\frontend\AGENTS.md`，4131 B）。`D:\math-arena\services\teacher-platform\AGENTS.md` **未搬** → 该服务**失去了近目录规则**（A11）。
根 `AGENTS.md` 声明「近目录规则优先」，但 `frontend/AGENTS.md` 里的规则大量指向**旧绝对路径**（见 §6），封存后即失效 → 需整体改写为仓库相对路径。

### 2.2 `frontend/AGENTS.md` / `DESIGN.md` / `PRODUCT.md` 是否已搬？内容是否完整？

| 文件 | 新仓库 | 与旧目录比对 | 完整？ |
|---|---|---|---|
| `frontend/AGENTS.md` | ✅ 4131 B | **byte-identical** | ✅ 完整 |
| `frontend/DESIGN.md` | ✅ 3664 B | **byte-identical** | ✅ 完整（首行 `<!-- impeccable:design-schema 1 -->`） |
| `frontend/PRODUCT.md` | ✅ 4192 B | **byte-identical** | ✅ 完整（首行 `<!-- impeccable:product-schema 1 -->`） |
| `frontend/package.json` | ✅ 1528 B | **byte-identical** | ✅ |
| `frontend/pnpm-lock.yaml` | ✅ 80025 B | **byte-identical** | ✅ |
| `frontend/vite.config.js` / `tsconfig.json` / `index.html` / `nginx.conf` / `playwright.config.ts` / `vitest.config.ts` | ✅ | **全部 byte-identical** | ✅ |
| `frontend/.env.example` | ✅ 735 B | ⚠️ **内容不同** | ❌ 见下 |

**`frontend/.env.example` 内容差异（重要）**：

| 键 | 旧 `D:\frontend\.env.example` | 新 `zxsy\frontend\.env.example` | 影响 |
|---|---|---|---|
| `VITE_PREP_SURFACE` | `（空）` | `immersive` | ⚠️ **语义反转**。旧注释明确写「不设或设为非 immersive 值 = 旧行为（**沉浸式路由不注册**）」；新仓默认开 immersive。→ 教师端沉浸式路由会**默认注册**，若不符合当前预期会直接影响页面注册 |
| `VITE_RESEARCH_SURFACE` | `（空）` | `immersive` | ⚠️ 同上 |
| `VITE_USE_MOCK` | 注释位置在「仅演示假数据时开启；**生产/联调必须留空或为 0**」 | 注释位置在末尾 | 🟡 措辞弱化 |
| 头部注释 | 「占位符，禁止写入真实密钥」+「.env 已被 .gitignore 忽略」 | 简写 | 🟡 新仓 `.gitignore` 缺失 → 该承诺不成立 |
| 代理目标注释 | 「可选：覆盖 vite proxy 目标」 | 「服务代理目标（本地开发缺省值）」 | 🟡 措辞差异，行为不变 |

**判定**：三个 md 文件 ✅ **完整搬迁**；`.env.example` ⚠️ **被改写且语义有反转**，需产品确认 `VITE_*_SURFACE` 默认值，并补 `.gitignore` 以兑现「.env 已被忽略」的承诺。

### 2.3 验收截图与 golden

- **5 张单一视觉真源**：全部 ✅ 在新仓库并已 git 跟踪（见 §1.4.1 表）。
- **README 用的真实功能截图**：`deliverables/demo-seed-verify-141/`（17 张 + `results.json`）✅ 全部存在、尺寸真实（1500×980 / 1500×1201）、mtime 真实（2026-10-21T16:46 批次）。完整清单见 §1.4.2。
- **README 方案已预置搬运脚本**：`tools/prepare_readme_assets.py`（未入库，untracked）已把 17 张压缩到 `docs/readme/`（最大宽 1440），文件名规范化为小写短名。**说明：产品经理（Alice）的 README 方案 `docs/design/2026-10-21-readme-content-plan.md` 已给出 8–9 张选定清单 + 命名建议，与本审计 §1.4.2 交叉验证一致。**
- ⚠️ **该脚本依赖 `D:\math-arena\deliverables\demo-seed-verify-141`** → 旧目录封存后**不可再生**（生成脚本 A7 未搬）。必须在封存前跑完 + 搬入 A7 的 22 个 `.mjs`。

### 2.4 数据库迁移（alembic）完整性

| 项 | 旧 | 新 | 结论 |
|---|---|---|---|
| `services/api/alembic/versions/` 文件数 | 43（含 1 个 `__pycache__`） | **42** | ✅ 一致 |
| revision 总数 | 42 | **42** | ✅ 一致 |
| **唯一 head** | `tel_001_two_end_link`（`tel_001_two_end_link.py`） | **`tel_001_two_end_link`** | ✅ **完全一致** |
| 分叉检查 | 无多 head | 无多 head | ✅ 单链无分叉 |
| `services/api/alembic.ini` | ✅ | ✅ | ✅ |
| `services/teacher-platform/alembic/` + `alembic.ini` | ✅ | ✅ | ✅ |
| `services/ppt-studio-v2/alembic/versions/` | ✅（0001, 0002） | ✅（0001, 0002） | ✅ |
| `services/research-vnext/migrations/versions/` | ✅ | ✅ | ✅ |
| `services/research-vnext/alembic.ini` | ✅ | ❌ **缺失** | ❌ **A4** |
| `services/ppt-studio-v2/alembic.ini` | ✅ | ❌ **缺失** | ❌ **A4** |

**9 个 revision 文件**在 `m2_019` / `m2_020` / `om3_001` / `om5` / `om6` / `om7` / `om8` / `research_001` / `research_002` 上存在 diff —— 核查确认：**全部是 CRLF/LF 换行符差异**（`git diff` 报 `LF will be replaced by CRLF`），**内容等价**。

**结论：主库迁移链完整且与旧目录 head 一致。** 但 research-vnext 与 ppt-studio-v2 缺 ini → 这两个服务的迁移命令当前不可用。

### 2.5 `data/` / `tools/` / `scripts/` / `deploy/` 迁移完整性

| 目录 | 结论 |
|---|---|
| **`data/`** | ✅ **语义替换成功**。旧 `data/file-uploads/`（40MB 运行期上传）→ 新 `data/kb-seed/`（4 份 md 知识库种子）+ `data/papers-seed/`（6 份论文 PDF），共 32KB。两者 `diff -rq` 只报 `file-uploads` / `kb-seed` / `papers-seed` 三个差异。旧 `services/data/file-uploads/`（2 个 bin）未搬 → P2（语义是「旧上传残留」，建议在 README/文档说明 `data/` 现为种子目录） |
| **`tools/`** | ✅ 核心已搬（`seed_demo_accounts.py` `bin/` `redis/`），⚠️ 旧 `tools/prepc/` 未搬（A8 同类，12MB 视觉判据脚本） |
| **`scripts/`** | ⚠️ **部分**。已搬 6 项：`backup_local_uploads.ps1` `bge_m3_server.py` `check_user_data.sh` `dev-down.ps1` `dev-up.ps1` `export_user_data.sh`。未搬 22 项（见下） |
| **`deploy/`** | ✅ **增强**。12 个文件（含新增 `init-db.sql`），`docker-compose.yml` 被重写：容器名 `math-arena-*` → `zxsy-*`、卷名 `math-arena-dev-pgdata`（external）→ `zxsy-pgdata`（本地）、新增 `teacher-postgres`(pg16/54330) + `teacher-valkey`(6380)、挂载 `init-db.sql` 自动建 4 个库。⚠️ **但 `deploy/docker-compose.yml` 里的 `api` 服务被移除**（旧版有 `api` 服务，现版只有基础设施）→ README 的 `make infra` 语义仍成立，但**旧 `docker compose up` 一键起全栈的能力没了**，需确认这是有意为之 |
| **`scripts/` 未搬 22 项明细** | `_assess_damage.py` `_check_card_ids.py` `_check_hash.py` `_final_reconcile.py` `_postcheck_rb_full.py` `_probe_rb_full_20261011.py` `_restore_contamination.py` `_verify_card.py` `_verify_card2.py` `e2e_iter15_b6_landscape.py` `e2e_iter15_regression.py` `rebind_book_930.py` `rebind_book_full_20261011.py` `rebind_plan_full_20261011.json` `recover_contam_20261011.py` `pc-up.ps1` `pc-up-w2f.ps1` `recovery/` `start-services/` + 2 个 txt。**判定：全部是 2026-10-11 教材重绑事件的**一次性污染修复/回归脚本 + 旧 pc-up 启动脚本** → 属于 D 类（历史事故脚本），但 `rebind_book_full_20261011.py` / `recovery/` 有教学库重绑的**操作知识价值**，建议 P2 归档到 `docs/incidents/2026-10-11-textbook-rebind/` 而非留在 `scripts/` |

### 2.6 `packages/`（protocol）与 pnpm workspace 自洽性

```yaml
# D:\zxsy-math-arena\pnpm-workspace.yaml（与旧目录 byte-identical）
packages:
  - "packages/*"
```

| 项 | 状态 |
|---|---|
| `packages/protocol/` 内容 | ✅ 完整（`package.json` `tsconfig.json` `src/api.ts` 80 行 `src/envelope.ts` 100 行 `src/events.ts` 162 行 `src/index.ts` 9 行，合计 **351 行**） |
| `packages/protocol/package.json` | ✅ 与旧目录 byte-identical（`@math-arena/protocol` v0.1.0，private，`main`/`types` → `src/index.ts`，`type-check` → `tsc --noEmit`） |
| 根 `pnpm-lock.yaml`（491 B） | ✅ 与旧目录 byte-identical |
| CI 的 protocol 检查 | ✅ `.github/workflows/ci.yml` 有 `protocol-check` job：`pnpm install` → `pnpm --filter @math-arena/protocol type-check`（与旧目录 CI **byte-identical**） |

**⚠️ 自洽性问题（需明确决策）**：

1. **`packages/protocol` 目前没有任何消费方**。全仓库 grep `@math-arena/protocol` **只命中它自己的 `package.json`**。`frontend/package.json` 与 `frontend/vite.config.js` **均未引用**。→ 这是一个**孤立包**：CI 会对它跑 `tsc --noEmit`（能过），但前后端**并未真正共享协议类型**。
2. **`frontend/` 不在 pnpm workspace 内**。`pnpm-workspace.yaml` 只有 `packages/*`，而 `frontend/` 有**独立的 `pnpm-lock.yaml`（80KB）** 和独立的 `package.json`（name=`zhixue-shuyan-v4`）。→ 根 `pnpm install` **不会**安装前端依赖；`pnpm dev`（`pnpm -r --parallel run dev`）**不会启动前端**。README §4 写的是「`cd frontend && pnpm install && pnpm dev`」，与根脚本不冲突，但**根 `pnpm dev` 的语义是「所有服务」却覆盖不到前端**，与 `AGENTS.md` 权威命令表（「`pnpm dev` 并行启动所有服务开发模式」）**存在语义落差**。
3. **协议漂移无机制拦截**：`frontend/src/features/research-workspace/` 已有独立的 `tokens/` 与 `api/`（`researchApi.ts`），后端有 `app/kernel/` 契约，**没有一处从 `@math-arena/protocol` 派生**。→ CI 的 protocol-check 是「自检孤岛」，无法发现前后端协议漂移。

**判定**：`packages/` **内容完整**，但 **workspace 配置与实际依赖拓扑不自洽**。三个选项（建议选 ②）：
- ① 把 `frontend/` 加入 `pnpm-workspace.yaml`（`packages: ["packages/*", "frontend"]`），并让 `frontend/package.json` 声明 `"@math-arena/protocol": "workspace:*"`；
- ② **明确弃用 protocol 包**，把它移出 workspace 或标 `deprecated`，在 `AGENTS.md` 里说明「协议真源是 `packages/protocol` 但当前未接线，规划中」；
- ③ 保留现状但在 `AGENTS.md` 中**删除**「`pnpm dev` 启动所有服务」这一行，改为「`pnpm dev` 仅启动 `packages/*`；前端用 `cd frontend && pnpm dev`」。

### 2.7 指向旧绝对路径的硬编码（**封存后全部失效**）

用 `grep -rIn -E 'D:[/\\](math-arena|frontend)'` 扫描全仓库（排除 node_modules/.git/.venv），命中 **50 个文件**。按影响分级：

#### 🔴 P0 —— 会导致运行/构建失败

| 文件:行 | 内容 | 修复建议 |
|---|---|---|
| `deploy/docker-compose.m3.yml:90` | `- D:/frontend/dist:/usr/share/nginx/html:ro` | 改为 `- ../frontend/dist:/usr/share/nginx/html:ro`（相对 compose 文件），或改用 `${FRONTEND_DIST:-../frontend/dist}` |
| `deploy/docker-compose.m3.yml:3,89` | 注释 `cd D:\frontend && npm run build` | 改为 `cd frontend && pnpm build`（注意是 **pnpm 不是 npm**，见 §5） |
| `services/api/scripts/local_embedding_server.py:15` | `DEFAULT_MODEL_PATH = r"D:\math-arena\.models\bge-m3"` | 改为 `os.environ.get("LOCAL_EMBEDDING_MODEL", "")`，为空时给出明确报错指引 |
| `scripts/bge_m3_server.py:34` | `DEFAULT_MODEL = os.environ.get("BGE_M3_PATH", r"D:\math-arena\.models\bge-m3")` | 同上，改为纯环境变量 + 校验 |
| `scripts/dev-up.ps1:11` | `$apiDir = 'D:\math-arena\services\api'` | 改为 `$apiDir = Join-Path $PSScriptRoot '..\services\api'`（脚本自解析路径） |
| `scripts/bge_m3_server.py:15`（docstring） | `C:/python13/python.exe D:/math-arena/scripts/bge_m3_server.py --port 8080` | 改为 `python scripts/bge_m3_server.py --port 8080` |
| `services/api/app/domains/classroom/_clean_prompt.py:10` | `p = pathlib.Path(r'D:\math-arena\services\api\app\domains\classroom\stage_router.py')` | **一次性脚本**，且未被任何模块 import（已 grep 确认）→ 建议**直接删除**或改为相对路径 |
| `services/api/scripts/probe_doc_understand.py:13,32` | `open("D:/math-arena/.env")` / `open(f"D:/math-arena/.tmp/{name}")` | 改为 `Path(__file__).resolve().parents[2] / ".env"` |
| `services/api/scripts/verify_photo_chain.py:31` | `Path("D:/math-arena/.tmp/photo-question.png")` | 同上 |
| `services/research-vnext/tests/contracts/test_p00_conformance.py:23,29` | `r"D:\math-arena\services\teacher-platform\app\platform_contracts\schema\platform-contracts.schema.json"` 与 `r"D:\math-arena\services\teacher-platform"` | **pytest 会真的去读这个绝对路径** → 封存后测试失败。已支持 `os.getenv("PLATFORM_CONTRACTS_SCHEMA_PATH")` 覆盖，但**默认值必须改成仓库相对路径** |
| `services/research-vnext/tests/real_sandbox/probe_http_edward.py:17` | `pathlib.Path(r"D:\math-arena\services\research-vnext\.env")` | 改为相对路径 |
| `services/research-vnext/scripts/rv08_launch.ps1:14,38` | `$repoRoot = "D:\math-arena"` | 改为 `$repoRoot = (Resolve-Path "$PSScriptRoot\..\..\..").Path` |
| `services/research-vnext/research_vnext/core/parser/Dockerfile.docling:6` | `-v D:\math-arena\services\research-vnext\.parser-venv:C:\parser-venv` | 改为相对路径 |
| `services/research-vnext/research_vnext/core/parser/download_models.py:4` | `--artifacts D:/math-arena/services/research-vnext/.parser-cache/artifacts` | 改为相对路径 |
| `services/research-vnext/tests/contracts/test_cr6_frontend_contract.py:3`（docstring） | 指向 `D:/frontend/src/.../researchAdapter.ts` | docstring，改为仓库相对路径即可 |
| `services/research-vnext/research_vnext/core/config.py:29`、`core/domain/snapshot.py:4`（docstring） | 指向 `D:\frontend\src\features\research-vnext\...` | docstring，改为仓库相对路径 |

#### 🟠 P1 —— 会导致 e2e 验收脚本写盘到旧目录 / 视觉审计脚本读错目录

| 文件:行 | 内容 | 修复建议 |
|---|---|---|
| `frontend/e2e/demo-seed-verify.mjs:6` | `outDir = process.env.OUT \|\| 'D:/math-arena/deliverables/demo-seed-verify'` | 改为 `process.env.OUT \|\| path.resolve(process.cwd(), '../docs/readme/raw')` 或仓库内 `artifacts/` |
| `frontend/e2e/prod-deploy-verify.mjs:6` | `const outDir = 'D:/math-arena/deliverables/deploy-verify-r1'`（**无 env 兜底**） | 同上，加 env 覆盖 |
| `frontend/scripts/capture-acceptance.mjs:6` | `const OUT = 'D:/math-arena/deliverables/student-refactor-v4/screenshots'` | 同上 |
| `frontend/test/visual/wpk/audit.mjs:21` | `const OUT = 'D:/math-arena/deliverables/vnext-autonomous/evidence'` | 同上 |
| `frontend/test/visual/wpk/capture.mjs:24` | `const OUT = 'D:/math-arena/deliverables/vnext-autonomous/evidence/WP-K-screenshots'` | 同上 |
| `frontend/test/visual/wpk/capture.mjs:25` | `const FIXTURES = 'D:/frontend/test/visual/wpk/fixtures'` | 改为 `path.resolve(process.cwd(), 'test/visual/wpk/fixtures')`（**注意：新仓库 `test/visual/wpk/fixtures/` 确实存在**，内含 `椭圆优秀教案参考.docx`） |
| `frontend/test/visual/wpk/compare.mjs:4` | `const OUT = 'D:/math-arena/deliverables/.../WP-K-screenshots'` | 同上 |
| `frontend/test/visual/wpk/probe.mjs:23` | `const OUT = 'D:/math-arena/deliverables/.../WP-K-screenshots'` | 同上 |
| `frontend/test/visual/wpk/structure-check.mjs:69` | `const OUT = 'D:/math-arena/deliverables/.../WP-K-STRUCTURE-CHECK.json'` | 同上 |
| `frontend/test/visual/wpk/summarize.mjs:4,37` | `const P = 'D:/math-arena/deliverables/.../WP-K-AUDIT.json'` + 写 `WP-K-AUDIT.txt` | 同上 |
| `frontend/test/visual/wpk/measure-literals.mjs:16` | `const ROOT = 'D:/frontend/src'` | **这个最危险**：视觉字面量审计会**扫描旧目录的 src**，而新仓库 src 与旧目录 byte-identical → 结论目前仍成立，但**旧目录一封存就扫不到任何文件**（0 命中会被误判为「通过」）→ 必须改为 `path.resolve(process.cwd(), 'src')` |
| `frontend/src/features/research-workspace/tokens/brandMarks.ts:12` | 注释「复算：`C:/python13/python.exe D:/math-arena/tmp/r6_gen_ts.py`」 | docstring，改为仓库相对路径。⚠️ **`r6_gen_ts.py` 在 `D:\math-arena\tmp\`（890MB tmp），未搬** → Golden 品牌标记的**复算脚本不可再生**，建议一并归档 |
| `frontend/src/features/teacher-prep-v2/i18n/labels.ts:4` | 注释「见 `D:\frontend\.impeccable.md`」 | 改为 `frontend/.impeccable.md` |
| `frontend/test/chatCardContract.test.ts:6` | 注释指向 `D:\math-arena\services\api\app\skills\smart_quiz\main.py` | 改为 `services/api/...` |
| `frontend/test/teacher-prep-v2/t223-copy-no-jargon.test.ts:4`、`t223-selection-card-search.test.ts:4` | 注释「见 `D:\frontend\.impeccable.md`」 | 同上 |
| `frontend/AGENTS.md:46,48,55` | **3 处**：`D:\math-arena\docs\research-workspace\RESEARCH_PRODUCT_CONTRACT.md`、`D:\math-arena\docs\research-workspace\VISUAL-SPEC.md`、`D:/frontend/docs/teacher-prep-center/V2-FRONTEND-VISUAL-GUIDE.md` | **全部改为仓库相对路径**：`docs/research-workspace/...`、`frontend/docs/teacher-prep-center/...`。⚠️ 第 55 行还指向 **`D:\教师端备课中心重构宪法\PROMPT.md`——该目录在两个仓库里都不存在**（已确认），需删除或替换为 `docs/product/teacher-prep-center/TEACHER_PREP_CENTER_PRODUCT_CONTRACT.md` |
| `docs/product/teacher-prep-center/KERNEL-RUBRIC.md:121` | 「截图工具 `D:/frontend/tools/prepc/pc_lib.mjs`」 | 改为 `frontend/tools/prepc/pc_lib.mjs` + **搬入 A8** |
| `docs/product/teacher-prep-center/V2-BACKEND-KERNEL-GUIDE.md:5` | 「前端判据 `D:/frontend/docs/teacher-prep-center/V2-FRONTEND-VISUAL-GUIDE.md`；视觉基线 `D:/frontend/baseline/VISUAL-BASELINE.md`」 | 改为相对路径 + **搬入 A9/A10** |
| `docs/design/2026-10-21-readme-content-plan.md`（12 处） | 指向 `D:\math-arena\deliverables\...` | 计划文档，可保留原路径作为「来源记录」，但建议在 README 落地后改为新 `docs/readme/` 路径 |
| `services/teacher-platform/corpus/tools/katex_check.mjs`（2 处）、`corpus/evals/run_prompts_eval.py`（2 处）、`corpus/pipelines/load_to_db.py`（1 处）、`corpus/staging/quiz_gaokao_katex_report.json`（1 处） | 指向旧路径 | 逐个改相对路径或参数化 |
| `services/teacher-platform/tests/unit/rag/test_c7_monotonicity.py`（1）、`tests/contract/export_api_inventory.py`（1）、`tests/contract/conftest.py`（1）、`docs/handoff/frontend-mock-ref/README.md`（1）、`docs/handoff/DEP-B2-dependencies-and-migrations.md`（1） | 指向旧路径 | 同上 |
| `services/sandbox/docs/RUNBOOK.md`（2 处） | 指向旧路径 | 同上 |
| `services/research-vnext/docs/CHANGE-REQUEST.md`（5）、`GO-LIVE-EVIDENCE.md`（9）、`PHASE-1-CR6.md`（1） | 历史文档 + 变更约束说明（含「本 agent 禁止写 `D:\frontend\`」等历史语境） | **历史文档，保留原样即可**（记录当时事实），但 `GO-LIVE-EVIDENCE.md` 里的**可执行命令**需在 P1 阶段加注「路径已迁移到新仓库」 |
| `deploy/DEPLOY-GUIDE.md:46` | 「根目录 `D:\math-arena\.env` 已含模型密钥」 | 改为 `根目录 .env`（新仓库无 .env，需说明 `cp .env.example .env`） |
| `scripts/backup_local_uploads.ps1:8,15` | `$BackupRoot = 'D:\math-arena-backups'` | 这是**备份目标路径**（不在仓库内），可保留但建议改为可配置 + 目录名换成 `zxsy-math-arena-backups` |
| `docs/research-workspace/HANDOVER.md`（28）、`HANDOVER-3.md`（19）、`research-end-fullstack-prompt.md`（19）、`teacher-end-overnight-dev-prompt.md`（8）、`teacher-end-redesign-plan.md`（7）、`docs/部署说明.md`（6）、`docs/后端现状审计报告.md`（2）、`docs/implementation/*.md`（13）、`docs/compose/spec/*.md`（5）、`docs/research-workspace/BACKEND-CONTRACT.md`（3）、`docs/product/teacher-prep-center/ENGINEERING-STANDARDS.md`（2） | 大量旧路径引用 | **历史/规格文档，P2 批量替换为相对路径**（可脚本化） |

#### 🟡 P2 —— 历史文档，可保留

`docs/audits/*`、`docs/implementation/*`、`docs/research-workspace/*`、`docs/product/teacher-prep-center/FROZEN-CONTRACT.md` / `KERNEL-RUBRIC.md` / `VISUAL-SPEC-V2.md` / `VISUAL-SPEC-V3.md`、`services/research-vnext/docs/*` —— 这些是**历史记录**，保留原路径不违背「只读历史」的真实性，但需在仓库顶部加一条说明：「本文档中的 `D:\math-arena` / `D:\frontend` 为旧仓库路径，已迁移至本仓库对应相对路径」。

---

## 3. 接续开发可行性结论

### 3.1 三句话回答

1. **源码层面：完整。** 前端 `src`/`test`/`public`/`scripts` **byte-identical**；后端 `services/api/app` 仅 18 个文件有 lint 级差异（isort / 死导入 / SIM 简化），**零功能缺失**；alembic 42 revision、唯一 head `tel_001_two_end_link` 与旧目录**完全一致**；7 个服务的 Python 包体、`packages/protocol`、CI 配置、`.github/` 全部到位。→ **没有「需要把源码搬过来」的问题。**

2. **运行层面：差 5 个文件 + 1 份 .gitignore。** `services/teacher-platform/main.py`（教师平台 Docker 启动必需）、`frontend/.impeccable/design.json`（vitest 直接报错）、`alembic.ini`×2、`.gitignore`×2。→ **补齐这 5 类文件即可跑通。**

3. **维护层面：需一次集中整改。** 50 个文件含旧绝对路径；`.env` 真密钥从未迁入（需从密钥管理重新下发）；`AGENTS.md` 的权威命令表与新根 `Makefile` 语义冲突。

### 3.2 当前仓库的工作树状态（需要注意）

```
$ git -C D:\zxsy-math-arena status --short
 M services/api/alembic/versions/m2_019_submission_item_attachments.py
 M services/api/alembic/versions/m2_020_classroom_sessions.py
 ... 共 68 个文件 modified
$ git diff --stat | tail -1
 68 files changed, 166 insertions(+), 135 deletions(-)
$ git log --oneline
 faeffcf init: ZXSY Math Arena 三端 AI 教学平台（可运行完整代码库）
```

**关键判断**：这 68 个 modified 文件**不是**未提交的业务改动，而是 **CRLF→LF 换行符归一 + isort 重排**导致的伪 diff（`git diff` 报 `LF will be replaced by CRLF`）。抽样确认 `services/api/app/gateway/agent_router.py` 的 diff 是 `from app.models.conversation` 与 `from app.models.chunk` 的**顺序交换**（isort 结果），`app/main.py` 删掉了未使用的 `import selectors`。→ **这 68 个文件应作为一次「格式化统一」commit 提交**，否则任何人 `git checkout` 都会看到脏工作树。

**另注**：仓库 `core.autocrlf=true` 且**无 `.gitattributes`** → 跨平台（Linux CI / macOS 开发机）会持续产生伪 diff。建议补 `.gitattributes` 固定 `*.py text eol=lf` / `*.ts,*.vue text eol=lf` / `*.png binary` / `*.md text eol=lf`。

### 3.3 环境与工具链现状

| 项 | 状态 |
|---|---|
| `node` | v22.22.2 ✅（`frontend/package.json` 要求 `vue-tsc 2.1.10` / `vite ^5.4.21`，兼容） |
| `pnpm` | 9.0.0 ✅（根 `package.json` `packageManager: pnpm@9.0.0`，一致） |
| `python` | **3.13.14** ⚠️。`AGENTS.md` 要求「通用 API: Python >= 3.11」「teacher-platform: Python 3.12」→ 3.13 满足下限，但 `pyproject.toml` 的 ruff/black `target-version = "py312"`；`teacher-platform` 需 **3.12**（不向下兼容指业务契约，非解释器）→ 建议为 teacher-platform 单建 3.12 venv |
| `node_modules` | ❌ 三处全缺（`frontend/`、根、`packages/protocol/`）→ 需 `pnpm install`（**但先补 `.gitignore`，否则会误入库**） |
| `.venv` | ❌ 全部服务缺 → 需 `uv sync`（**但先补 `uv.lock`，teacher-platform / ppt-studio-v2 的 Docker build 直接依赖它**） |
| `.env` | ❌ 根 + frontend + 4 个 service 全缺 → 需从密钥管理重新下发（**不要从旧目录直接拷贝 git-tracked 风险文件**；旧目录 `.env` 含真实密钥，属 D 类） |
| `data/` 种子 | ✅ `kb-seed`（4 md）+ `papers-seed`（6 PDF）已入库 |
| `tools/seed_demo_accounts.py` | ✅ 在，README 的 `make seed` 可用 |
| CI | ✅ `backend-lint`（ruff+black）、`backend-test`（pgvector pg15 + redis + `alembic upgrade head` + pytest）、`protocol-check` 三 job 完整 |

---

## 4. 必须先补的 N 件事（优先级清单）

### 🔴 P0（6 项，阻塞开工 / 阻塞运行）

| # | 事项 | 具体动作 | **验收标准** |
|---|---|---|---|
| **P0-1** | 补 `services/teacher-platform/main.py` | 从 `D:\math-arena\services\teacher-platform\main.py` 复制（163 行） | ① `python -c "from main import app"` 在 `services/teacher-platform/` 下成功；② `docker build services/teacher-platform` 成功；③ `docker run` 后 `curl :8100/healthz` 返回 `{"status":"ok"|"degraded"}` |
| **P0-2** | 补 `frontend/.impeccable/design.json` + `frontend/.impeccable.md` | 从 `D:\frontend\.impeccable\` 复制（design.json + 4.4KB md） | ① `cd frontend && pnpm i && pnpm test:run` **全绿**；② `test/visual/semantic-token-governance.test.ts` 的 `sidecar.tokens.semanticPrefix === '--ma-'` 断言通过；③ 7 处 `.impeccable.md` 注释引用可解析 |
| **P0-3** | 补 `.gitignore`（根 + frontend） | 根：合并 `D:\math-arena\.gitignore`（5220B，已含 `__pycache__/` `.venv/` `node_modules/` `.env` `logs/` `tmp/` `.models/` `deliverables/phase*-evidence/`）；frontend：直接复制 `D:\frontend\.gitignore`（1130B，已含 `dist-*/` `.env` `test-results/` `_deadcode_quarantine/`） | ① `git check-ignore -v frontend/node_modules services/api/.venv services/api/app/main.pyc .env` **全部命中**；② `git status --untracked-files=all` 中 **0 个 `.pyc`**、0 个 `node_modules`；③ `git ls-files` 中不含 `.env`（仅 `.env.example`） |
| **P0-4** | 补 `alembic.ini`×2 + `uv.lock`×3 + `setup.cfg` | `services/research-vnext/alembic.ini`、`services/ppt-studio-v2/alembic.ini`、`services/api/uv.lock`(617KB)、`services/teacher-platform/uv.lock`、`services/ppt-studio-v2/uv.lock`(71KB)、`services/api/setup.cfg` | ① `cd services/research-vnext && alembic upgrade head` 成功；② `cd services/ppt-studio-v2 && alembic upgrade head` 成功；③ `docker build services/teacher-platform` 与 `docker build services/ppt-studio-v2` **均成功**（COPY uv.lock 不再失败）；④ `pip install -e services/api` 后 `import app.kernel.rag` 成功 |
| **P0-5** | 搬 `frontend/e2e/` 22 个 `.mjs` 验收脚本 | 从 `D:\frontend\e2e\` 复制：`butler-core-r2-empty-state.mjs` `butler-core-r2-integration.mjs` `butler-core-r2-kb-upload.mjs` `prep-composer-probe.mjs` `prep-conv-drawer-shot.mjs` `prep-el-shot.mjs` `prep-live-shot.mjs` `prep-measure.mjs` `prep-probe-state.mjs` `prep-r2-shots.mjs` `prep-repro-send.mjs` `prep-rows.mjs` `prep-shot.mjs` `probe-conv.mjs` `quiz-figure-fix-r1.mjs` `r8-e2e.mjs` `research-ux-r1.mjs` `student-butler-r1.mjs` `student-butler-r1b.mjs` `student-butler-r2.mjs` `student-library-research-r1.mjs` `two-end-link-acceptance.mjs` | ① 22 个文件全部在新仓库 `frontend/e2e/`；② **每个脚本的 `outDir` 已改为仓库内路径**（如 `process.env.OUT || path.resolve(process.cwd(),'../artifacts/<name>')`）；③ 逐个 `node <script>` 冒烟跑通（需后端 + 前端已启动）；④ 生成的截图与旧 `deliverables/` 对应文件**像素级一致或更好** |
| **P0-6** | 清理 50 个文件里的旧绝对路径 | 见 §2.7 的 🔴 P0 表（13 个位置）+ 🟠 P1 表（20 个位置）。**必须同步改**：`frontend/test/visual/wpk/measure-literals.mjs:16`（否则视觉审计静默扫空）、`frontend/test/visual/wpk/capture.mjs:25`（fixtures 路径）、`services/research-vnext/tests/contracts/test_p00_conformance.py:23,29`（pytest 真读绝对路径）、`deploy/docker-compose.m3.yml:90`（docker 挂载） | ① `grep -rIn -E 'D:[/\\](math-arena|frontend)' --exclude-dir=node_modules --exclude-dir=.git .` 在**代码/配置/脚本**（不含 `docs/`）中 **0 命中**；② `cd frontend && pnpm test:run` 全绿；③ `cd services/research-vnext && pytest tests/contracts -q` 全绿；④ `docker compose -f deploy/docker-compose.m3.yml config` 无路径告警 |

> **额外建议（并入 P0-3）**：补 `.gitattributes`（`*.py text eol=lf`、`*.ts text eol=lf`、`*.vue text eol=lf`、`*.png binary`、`*.pdf binary`）以根治 §3.2 的 68 文件伪 diff。
> **额外建议（并入 P0-6）**：把 68 个 isort/CRLF 伪 diff 作为一次 `chore: 统一换行符与 import 排序` commit 提交，恢复干净工作树。

### 🟠 P1（9 项，封存旧目录前必须完成）

| # | 事项 | 具体动作 | **验收标准** |
|---|---|---|---|
| **P1-1** | 验收截图入库 | 把 §1.4.2 中 README 需要的 **17 张**（`demo-seed-verify-141/` 全套）压缩入库到 `docs/readme/`（已有 `tools/prepare_readme_assets.py` 产物，**需 `git add`**），并把 `results.json` 一并入库 | ① `docs/readme/` 下 17 张 + `results.json` 全部 git-tracked；② 每张 ≤ 300KB、宽 ≤ 1440；③ README 里每张图能点开原图 |
| **P1-2** | 搬 `frontend/tools/`（12MB） | 从 `D:\frontend\tools\` 复制 `prepc/`（134 个 `.mjs`）+ `fe-ppt-r14/` | ① `frontend/tools/prepc/pc_lib.mjs` 存在；② `docs/product/teacher-prep-center/KERNEL-RUBRIC.md:121` 的引用可解析 |
| **P1-3** | 搬视觉基线与前端判据 | `frontend/baseline/VISUAL-BASELINE.md`、`frontend/docs/teacher-prep-center/V2-FRONTEND-VISUAL-GUIDE.md`、`frontend/docs/teacher-prep-center/V2-VISUAL-GATE.md` | ① 三文件入库；② `V2-BACKEND-KERNEL-GUIDE.md:5` 与 `frontend/AGENTS.md:55` 的引用全部指向仓库内路径 |
| **P1-4** | 补 `services/teacher-platform/AGENTS.md` | 从 `D:\math-arena\services\teacher-platform\AGENTS.md` 复制（8KB） | ① 文件入库；② 根 `AGENTS.md` 的「近目录规则优先」能命中它 |
| **P1-5** | 补 `VERSIONS.md`（**先校对再搬**） | 复制 `D:\math-arena\VERSIONS.md` 后**逐行校对**：Element Plus→Naive UI、vue 3.4→3.5、teacher-platform Python 3.12、PostgreSQL 15/16 双实例、Redis 7 + Valkey 9.1.2 | ① 表内每个版本号与对应 `package.json` / `pyproject.toml` / `docker-compose.yml` **实际值一致**；② 差异条目用「已升级至 X」标注 |
| **P1-6** | 补部署文档 | `deploy/DEPLOY-20261005-三端上线.md`、`deliverables/deploy-<your-server-ip>/`（`DEPLOY-NOTES.md` `Dockerfile.server` `nginx.conf` `build-api.sh` `build-frontend.sh` `install-docker.sh` `pull-images.sh` `redeploy-2026-09-14-2200.png`） | ① 文档入库；② 镜像名从 `math-arena-*` 更新为 `zxsy-*`（新 `docker-compose.yml` 已改名） |
| **P1-7** | 搬设计/审计文档 | `D:\math-arena\docs\superpowers\`（24 份）+ `docs\workbuddy\`（10 份）→ 新仓库 `docs/superpowers/` `docs/workbuddy/` | ① 34 份文件全部入库；② 新 `docs/` 树结构与旧一致 |
| **P1-8** | 归档架构 HTML + Golden 课件 | `math-arena-个性化IDE联系与能力地图.html`、`math-arena-可观测性四件套讲解.html`、`deliverables/architecture-handbook/智学数研-三端架构深度学习手册.html` → `docs/handbook/`；`deliverables/golden-r211-render/slide-01..10.png` + `golden-B-r211.pptx` → `docs/deliverables/` | ① 3 份 HTML + 10 张 slide + 1 份 pptx 入库；② README/handbook 有索引链接 |
| **P1-9** | 补 `frontend/teacher-design-showcase.design/` + `r3_sse_probe.mjs` + `frontend/.trae_write_probe.txt` | 直接复制 | ① 文件入库 |

### 🟡 P2（6 项，可延后但建议排期）

| # | 事项 | 具体动作 | **验收标准** |
|---|---|---|---|
| **P2-1** | `deliverables/`（**535MB**）归档策略 | 不建议全量入库。建议：① README 用的 17 张 → `docs/readme/`（已在 P1-1）；② 5 张 Golden → 已在仓库；③ 其余 2400 张 → **Git LFS** 或**外部对象存储 + 一份 `docs/deliverables/INDEX.md` 索引**（按目录列文件名+尺寸+mtime+功能说明） | ① `docs/deliverables/INDEX.md` 覆盖全部 64 个子目录；② 仓库体积 ≤ 100MB |
| **P2-2** | 封存旧目录的三类大件 | `exports/` 715MB、`logs/` 177MB、`tmp/` 890MB、`.models/` 4.5GB → **整体留在旧目录**。但从 `tmp/` 摘出 `PLATFORM-README.md` `PLATFORM-TECHDOC.md` 到 `docs/`；从 `tmp/` 摘出 `r6_gen_ts.py`（Golden 品牌标记复算脚本）到 `tools/` | ① `docs/` 下有 2 份 platform 文档 + `tools/r6_gen_ts.py`；② 旧目录仍完整保留可读 |
| **P2-3** | 修正 `AGENTS.md` × 根 `Makefile` 语义冲突 | 根 `AGENTS.md:70-73` 声明 `make api` / `make test` / `make gate` 是**教师平台**命令；但新根 `Makefile` 的 `api:` 目标是 `cd services/api && python run_server.py`（主 API），`test:` 是 `cd services/api && pytest tests/ -q`，且**没有 `gate` 目标**。→ 二选一：(a) 把 AGENTS.md 的权威命令表改为指向**根 Makefile 实际目标**，并把 teacher-platform 的命令改为 `cd services/teacher-platform && make api`；(b) 在根 Makefile 补 `teacher-api` / `teacher-test` / `teacher-gate` 别名并同步 AGENTS.md | ① `AGENTS.md` 权威命令表中**每一条命令都能在对应目录执行成功**；② `make -n api test frontend seed infra migrate` 全部有目标；③ 无「表里有、Makefile 里没有」的目标 |
| **P2-4** | `packages/protocol` 去留决策 | 见 §2.6 的三个选项（建议 ② 或 ③）。至少要：① 在 `AGENTS.md` 明确 protocol 是「协议真源」还是「规划中」；② 若保留，在 `pnpm-workspace.yaml` 明确 `frontend` 是否入 workspace；③ `frontend/package.json` 与 `vite.config.js` 要么引用、要么明确不引用 | ① `AGENTS.md` 有「protocol 与 frontend 的关系」一节；② `pnpm -r list` 的输出与文档一致；③ 前端若引用 protocol，CI `protocol-check` 需扩展为「protocol 类型 + 前端消费点编译」 |
| **P2-5** | `data/` 与 `services/data/` 语义说明 | 新 `data/` 是**种子目录**（kb-seed + papers-seed），旧 `data/file-uploads/` 是**运行期上传**。在 `README.md` 仓库结构图里标注；`services/data/`（2 个 bin）判定为旧残留 → 归档或删除 | ① README 结构图标注 `data/` = 种子目录；② 有说明「上传文件落在运行期目录，不入库」 |
| **P2-6** | 补 `.editorconfig` + 事故/运营脚本归档 | `.editorconfig`（399B）从旧目录复制；`scripts/` 未搬的 21 项（教材重绑污染修复等）→ 归到 `docs/incidents/2026-10-11-textbook-rebind/` 而非留在 `scripts/` | ① `.editorconfig` 入库且与 `.gitattributes` 不冲突；② `scripts/` 下只剩通用脚本 |

---

## 5. 未尽事项与假设

### 5.1 明确 UNCLEAR / 需产品或用户裁决

| # | 问题 | 影响 | 建议裁决人 |
|---|---|---|---|
| **U1** | `frontend/.env.example` 把 `VITE_PREP_SURFACE` / `VITE_RESEARCH_SURFACE` 从「空」改成 `immersive` —— **是有意为之还是组装时的取值错误？** | 若非有意，沉浸式路由会默认注册，改变页面路由行为 | 产品（Alice）/ 前端 lead |
| **U2** | 新根 `deploy/docker-compose.yml` **删掉了旧版的 `api` 服务**（旧版有 `api` 并 `env_file: ../.env` + 一整套 `AUTH_*` 环境变量注入；新版只剩 6 个基础设施服务 + 新增 `teacher-postgres` / `teacher-valkey` / `init-db.sql`）—— 是「基础设施与应用分离」的重构，还是漏搬？ | 旧版 `docker compose up` 可一键起全栈；新版只能起基础设施，API 必须本地跑 | 后端 lead |
| **U3** | 新根 `.env.example` **删掉了 70 个配置键**（含 `AUTH_*` 全家 17 个、`STORAGE_*` 8 个、`VISION_*` 3 个、`XFYUN_*` 6 个、`XINGCHEN_*` 9 个、`TENCENT_SMS_*` 7 个、`CORS_ORIGINS`、`SANDBOX_*`、`SSE_*_CONCURRENCY`、`WEB_SEARCH_ENABLED`、`RAG_*_THRESHOLD`、`RERANKER_BASE_URL`、`CLOUD_KB_*`、`TOOL_API_KEY`、`DEEPSEEK_THINKING`、`TEXTBOOK_SLICE_ROOTS`…），只新增了 11 个（`RESEARCH_*` 3、`TEACHER_*` 5、`PPT_STUDIO_DATABASE_URL`、`RESEARCH_GATEWAY_HMAC_SECRET`、`RESEARCH_ENV`、`EMBEDDING_DIMS`）。**但 `services/api/app/config.py` 仍定义这些字段**（`vision_base_url:74`、`storage_*:112-122`、`xingchen_*:125-133`、`xfyun_*:136-141` 等）。→ 是「移到各 service 的 `.env.example`」还是「漏搬」？ | 新开发者照 `.env.example` 配不出短信/语音/视觉/对象存储功能 | 后端 lead + 产品 |
| **U4** | `.env` 真密钥的**新归属方案**未定。旧 `D:\math-arena\.env`（7805B）+ 6 个 `.env.bak-*` + 各 service 的 `.env`（teacher-platform 8 个、research-vnext 7 个）**都含真实密钥**，全部未迁入新仓库。 | 新仓库目前**无法真正跑起** LLM / 短信 / 存储 | tech lead（用户）|
| **U5** | `packages/protocol` 是「规划中」还是「已废弃」？当前零消费方。 | 决定 §2.6 选项 ①②③ | 架构（本人）/ 产品 |
| **U6** | `VERSIONS.md` 是否仍然维护？其内容已与 `package.json` 漂移（Element Plus → Naive UI）。 | 决定 P1-5 是「搬+校对」还是「重写」或「废弃」 | 产品 |
| **U7** | `deliverables/`（535MB / 2425 PNG / 35 PPTX / 22 PDF / 5 DOCX）**是否需要入库**？入库会让仓库从 ~30MB 涨到 ~600MB。 | 决定 P2-1 是 Git LFS 还是外部索引 | tech lead（用户）|
| **U8** | `D:\frontend\shots\workbench-v2-read.png` / `workbench-v2-write.png`（科研工作台读/写态，1440×900，2026-09-15）—— 与新的 `READ-GOLDEN.png` / `WRITE-GOLDEN.png` 功能重叠。**留哪一套？** | README 科研端配图避免重复 | 产品 |
| **U9** | `D:\math-arena\deliverables\architecture-handbook\智学数研-三端架构深度学习手册.html`（124KB）与根目录两份 HTML 讲解 —— 是否对外公开？若含内部架构细节，可能不宜入库到公开仓库 | 合规 | tech lead（用户）|
| **U10** | `frontend/test/visual/wpk/measure-literals.mjs:16` 指向 `D:/frontend/src`。**该脚本当前在新仓库里的行为是什么**（扫旧目录，结论仍成立但会随封存失效）？封存后是否还要跑视觉字面量门禁？ | 决定 P0-6 修法 | 前端 lead |

### 5.2 本审计做出的假设

| # | 假设 | 依据 |
|---|---|---|
| **A1** | `D:\math-arena` 与 `D:\frontend` **将被封存但保留可读**（不删除） | 用户原话「旧目录准备封存」+ 本次审计全程只读 |
| **A2** | 新仓库 `services/api/app` 的 18 个 lint 级差异（isort 排序、删 `import selectors`、`SIM` 简化、`options.keys()`→`options`）**是格式化产物而非功能改动** | 逐文件抽样 diff；`python -m compileall app` 通过（exit 0） |
| **A3** | `frontend/src` 与旧目录 **byte-identical**，因此旧 `D:\frontend\shots\` 的截图对当前代码仍然有效 | `diff -rq frontend/src` 返回空 |
| **A4** | `deliverables/` 的 22 个生成脚本（A7）与 22 个 e2e 脚本（A7 中的对应集合）构成「证据 → 代码」的可追溯链，**必须一起保留** | 脚本内 `outDir` 常量直接指向 `D:/math-arena/deliverables/<name>` |
| **A5** | `.models/`（4.5GB）与 `exports/`（715MB）等大件**不搬**，改用环境变量 / 外部存储 | 体积与「非源码」性质；`BGE_M3_PATH` 机制已存在 |
| **A6** | 新根 `README.md` 的 `make infra` / `make api` / `make frontend` / `make seed` 与新根 `Makefile` 目标**一致** | 已逐一比对 target 名 |
| **A7** | `AGENTS.md` 根版（27055B）与 `frontend/AGENTS.md`（4131B）的**规则内容本身仍然有效**，只是路径引用需更新 | 两文件与旧目录 byte-identical，内容是流程/契约而非路径 |
| **A8** | `packages/protocol` 的 `type-check`（`tsc --noEmit`）在当前 `packages/protocol/src`（351 行）上能通过 | CI 已配置该 job 且与旧 CI byte-identical |

### 5.3 本审计未覆盖 / 无法验证的部分

| # | 未覆盖项 | 原因 |
|---|---|---|
| **N1** | 新仓库**无法实际启动**（无 node_modules、无 .venv、无 .env、无数据库） | 只读约束 + 缺 A1–A5；因此「P0 验收标准」是**待执行**的判据，不是已验证结论 |
| **N2** | 42 个 alembic revision 的**实际 `upgrade head` 是否成功** | 需要真实 PostgreSQL；仅做了静态 revision/down_revision 图分析（单 head、无分叉） |
| **N3** | 1503 个 Python 文件的**完整逐行 diff** | 已做 `services/api/app` 18 文件抽样 + `diff -rq` 全量清单 + `compileall`；其余 service 的 diff 清单为空（仅缓存/venv/.env 差异） |
| **N4** | `deliverables/` 2425 张 PNG 的**逐张人工看图确认** | 抽了 60+ 张做尺寸/mtime 提取，功能归类依据**同目录脚本名 + `results.json` 断言 + 文件名语义**，非逐张目视 |
| **N5** | `D:\frontend\.git` 与 `D:\math-arena\.git` 的**历史是否需要并入新仓库** | 用户只要求接续开发，未要求保留 git 历史；新仓库目前是**单 commit `faeffcf`** |
| **N6** | `services/api/eval/` 评测资产在 `pyproject.toml` 的 `testpaths = ["tests", "eval"]` 中，**跑 `pytest` 会同时跑 eval** | 已确认 `eval/` 已入库且与旧目录一致；但 CI 的 `pytest`（无路径参数）会包含它 |

---

## 6. 附录：关键数据表

### 6.1 迁移完整性汇总

| 维度 | 旧 | 新 | 差异 | 判定 |
|---|---|---|---|---|
| 前端 `.vue` | 457 | 457 | 0 | ✅ |
| 前端 `.ts` | 262 | 262 | 0 | ✅ |
| 前端 `src/` 总文件 | — | — | `diff -rq` **完全无差异** | ✅ |
| 前端 `test/` | — | — | **完全无差异** | ✅ |
| 前端 `public/` | — | — | **完全无差异** | ✅ |
| 前端 `scripts/` | 7 | 7 | 0 | ✅ |
| 前端 `e2e/` | 39 | 17 | **缺 22 个 `.mjs`** | ❌ A7 |
| 前端 `docs/` | 2 | 0 | **缺 2 个 md** | ❌ A10 |
| 前端 `tools/` | 189 | 0 | **缺 12MB** | ❌ A8 |
| 前端 `baseline/` | 1 | 0 | **缺 1** | ❌ A9 |
| 前端 `.impeccable/` | 1 | 0 | **缺 1（致测试红）** | ❌ A2 |
| 后端 `services/api` 非 venv `.py` | 2452 | 1503 | 差异仅缓存/日志/`data`/`logs`/`setup.cfg`/`uv.lock`/`.egg-info` | ✅ |
| `services/api/app` 语义差异文件 | — | 18 | 全部 lint 级 | ✅ |
| `services/api` alembic revision | 42 | 42 | 0 | ✅ |
| alembic head | `tel_001_two_end_link` | `tel_001_two_end_link` | 0 | ✅ |
| `services/` 服务数 | 8（含 `data/`） | 7 | 缺 `services/data`（2 bin） | P2 |
| `services/teacher-platform` 语义差异 | — | 0（仅 `.env*`/缓存/`AGENTS.md`/`main.py`/临时脚本） | — | ❌ A1 |
| `services/ppt-studio-v2` 缺 | — | `alembic.ini` `worker.py` `uv.lock` | — | ❌ A4/A5 |
| `services/research-vnext` 缺 | — | `alembic.ini` | — | ❌ A4 |
| `services/sandbox` 差异 | — | 0 | — | ✅ |
| `packages/protocol` | 351 行 | 351 行 | 0 | ✅ |
| 根 `package.json`/`pnpm-lock.yaml`/`pnpm-workspace.yaml` | — | — | **三者 byte-identical** | ✅ |
| `.github/` | 2 文件 | 2 文件 | **byte-identical** | ✅ |
| `docs/` | +2 子目录 | — | **缺 34 份文档** | ❌ A14 |
| `data/` | `file-uploads`(40MB) | `kb-seed`+`papers-seed`(32KB) | 语义替换 | ✅ |
| `tools/` | 3 项 + `prepc` | 3 项 | 缺 `prepc` | P1 |
| `scripts/` | 24 | 6 | 缺 21 | D 类 |
| `deploy/` | 11 | 12 | **+1（`init-db.sql`），compose 已重写** | ✅ 增强 |
| 根 `.gitignore` | 5220 B | **0** | **完全缺失** | ❌ A3 |
| `VERSIONS.md` | 1917 B | **0** | **完全缺失** | ❌ A12 |
| `AGENTS.md`（根） | 27055 B | 27055 B | 0 | ✅ |
| `frontend/AGENTS.md` | 4131 B | 4131 B | 0 | ✅ |
| `frontend/DESIGN.md` / `PRODUCT.md` | 3664 / 4192 B | 同 | 0 | ✅ |

### 6.2 硬编码路径命中分布（50 个文件）

| 类别 | 文件数 | 典型位置 |
|---|---|---|
| 🔴 会导致运行/构建失败 | **13** | `deploy/docker-compose.m3.yml:90`、`services/api/scripts/local_embedding_server.py:15`、`scripts/dev-up.ps1:11`、`services/research-vnext/tests/contracts/test_p00_conformance.py:23,29` |
| 🟠 会导致 e2e/视觉审计跑错目录 | **20** | `frontend/e2e/demo-seed-verify.mjs:6`、`frontend/test/visual/wpk/measure-literals.mjs:16`、`frontend/test/visual/wpk/capture.mjs:24,25`、`frontend/scripts/capture-acceptance.mjs:6` |
| 🟡 历史/规格文档，可批量替换或保留 | **17** | `docs/research-workspace/HANDOVER.md`(28)、`HANDOVER-3.md`(19)、`docs/research-end-fullstack-prompt.md`(19)、`docs/design/2026-10-21-readme-content-plan.md`(12)、`services/research-vnext/docs/GO-LIVE-EVIDENCE.md`(9) |
| 其中 `frontend/AGENTS.md`（3 处） | — | **必须在封存前修**，否则 agent 规则直接失效 |

### 6.3 体积对比

| 目录 | 大小 | 处置 |
|---|---|---|
| `D:\math-arena`（总计） | **~7.5GB** | 封存 |
| ├ `D:\math-arena\.models` | 4.5GB | 封存（D） |
| ├ `D:\math-arena\tmp` | 890MB | 封存（D），摘 2 份 md + `r6_gen_ts.py` |
| ├ `D:\math-arena\exports` | 715MB | 封存（D） |
| ├ `D:\math-arena\deliverables` | 535MB | **归档（P2-1）** |
| ├ `D:\math-arena\logs` | 177MB | 封存（D） |
| ├ `D:\math-arena\.tools` | 67MB | 封存（D） |
| └ 其余 | ~600MB | 封存 |
| `D:\frontend`（总计） | **~300MB**（不含 node_modules） | 封存 |
| ├ `D:\frontend\tools` | 12MB | **搬（P1-2）** |
| └ `dist*` / node_modules / `_deadcode_quarantine` 等 | ~280MB | 封存（D/B） |
| `D:\zxsy-math-arena`（新仓库当前） | ~40MB（git-tracked 2708 文件） | ✅ 精简 |
| 补齐 P0+P1 后预估 | **~60MB**（+17 截图 ≈ 4MB + tools 12MB + 文档 1MB + uv.lock 2MB） | 可接受 |

---

## 7. 最终判定

> **「D:\zxsy-math-arena 可以接续新的升级和开发工作」——前提是完成 §4 的 6 项 P0。**
>
> 源码层零缺失（前端 byte-identical、后端 18 个 lint 级差异、alembic head 一致、protocol 完整、CI 完整），**没有需要从旧目录搬源码的问题**。
>
> 真正需要搬的是：
> - **必须（P0）**：`services/teacher-platform/main.py`（1 个文件）、`frontend/.impeccable/`（2 个文件）、`.gitignore`（2 个）、`alembic.ini`（2 个）、`uv.lock`（3 个）、`frontend/e2e/` 22 个验收脚本（= `deliverables/` 2425 张截图的可再生性保障）；
> - **应该（P1）**：验收截图入 `docs/readme/`、`frontend/tools/prepc`、视觉基线与前端判据、`teacher-platform/AGENTS.md`、`VERSIONS.md`、现网部署文档、34 份设计/审计文档、3 份架构 HTML；
> - **归档（P2）**：`deliverables/` 剩余 535MB、`exports/` 715MB、`logs/` 177MB、`tmp/` 890MB、`.models/` 4.5GB。
>
> **不要搬**：一切 `.env`（真实密钥）、一切 `dist*`/`node_modules`/`.venv`/缓存、`_deadcode_quarantine`（故意隔离的死代码 + 事故记录）。
>
> **必须改**：`AGENTS.md` × 根 `Makefile` 的权威命令语义冲突（`make api` 到底指主 API 还是教师平台）、`packages/protocol` 的去留、50 个文件的旧绝对路径。

---

*报告结束。审计过程严格只读；除本文件 `docs/audits/2026-10-21-migration-completeness-audit.md` 外，未对任何仓库文件执行写入、删除或修改，未执行任何 git 写操作（`git status` / `git diff` / `git log` / `git ls-files` 均为只读命令）。审计过程中 `python -m compileall` 产生的临时 `__pycache__` 已被即时清理。*
