# M3 教师端工程收尾审计（2026-08-22）

## 审计范围

本文档闭环 `docs/superpowers/plans/2026-08-22-m3-teacher-core-integrity.md` 与
`docs/superpowers/plans/2026-08-22-m3-fullstack-closure.md` 的最终集成与验证步骤，
并记录教师端工作流（codex/teacher-core-backend，19 个提交）并入 feat/backend-m1 的
冲突解决方案与证据。前序诊断见
`docs/superpowers/audits/2026-08-22-m3-teacher-deep-optimization-audit.md`。

## 集成内容

- 合并提交：`83f5a4f`（Merge branch 'codex/teacher-core-backend' into feat/backend-m1）
- 适配提交：`c6701f4`（test(m3): align teacher scope and profile gates with unified auth）
- 分支内容：教师准入绑定核验、跨班隔离、知识点忠实组卷、题库不足门禁、客观题判分、
  批改上下文与证据不可变、评分确认生命周期、确定性备课草稿与生命周期校验、
  资源/课堂/Today/洞察修复（提交 d0f6353..e67b79d）。

## 冲突解决记录

两侧（统一认证 vs 教师核心）仅两个测试基础设施文件冲突，无业务代码冲突：

| 文件 | 解决方案 |
| --- | --- |
| `tests/conftest.py` | 融合双方：保留认证侧"重试一次 + drop/create + public 表数量核对（63/63）+ 失败不静默"，叠加教师侧"DROP SCHEMA 级重建 + current_database() 守卫"，守卫置于 `_init` 事务内 |
| `tests/_m3_helpers.py` | `make_user` 融合双语义：student/researcher/admin 恒为 approved 绑定（认证侧角色切换契约），teacher 绑定由 `teacher_verified` 参数控制（True=approved / False=pending / None=无绑定） |

合并后模型适配（非冲突、语义修复）：

| 文件 | 问题 | 修复 |
| --- | --- | --- |
| `app/domains/teacher/scope.py` | 基线 `verified` 为真实列；统一认证后为派生 property（SSOT=status），`RoleBinding.verified.is_(True)` 在运行时会崩溃 | 改为 `RoleBinding.status == "approved"` 查询过滤 |
| `tests/test_m3_teacher_scope.py` | 4 个用例期望教师 scope 层 40301；统一认证网关 `get_current_user` 已在请求时实时校验 approved 绑定并先返回 40300 | 断言更新为 40300，并在用例文档注明双层失败关闭语义（网关 40300 → scope 40301 二次防线） |
| `tests/test_m3_teacher_profile.py` | 子进程 pop `M3_ENABLE_TEACHER` 后回退读取本地 `.env`（内含 true）导致门控测试假失败 | 子进程环境显式 `M3_ENABLE_TEACHER=false` 覆盖；默认 false 契约由 `app/config.py` 定义保证 |

## 需求到测试证据

| 需求 | 证据（测试文件，合并后全绿） |
| --- | --- |
| 教师准入（数据库绑定核验、撤销即拒绝） | `test_m3_teacher_scope.py`（22 项，含网关 40300 与 scope 40301 双层） |
| 跨班隔离（assignments/queue 显式 class_id 40302） | `test_m3_teacher_scope.py` |
| 知识点忠实组卷 / 题库不足门禁 | `test_m3_teacher_assessment.py` |
| 客观题判分 / 批改上下文 / 证据不可变 / 确认幂等 | `test_m3_teacher_grading.py` |
| 确定性备课草稿 / 生命周期校验 / PPTX 与 DOCX 真实下载 | `test_m3_teacher_lessons.py` |
| 课堂模式 DB 持久化 / 教学闭环（发布→提交→批改→反馈） | `test_m3_fullstack_closure.py`（schema、classroom、全链路） |
| 资源真实工作流（上传/抽取/理解/发布/下载） | `test_m3_teacher_resources.py` |
| Today / 洞察 / Artifact / Butler / 能力降级 / 注册策略 | `test_m3_teacher_today_insights.py`、`test_m3_teacher_artifacts.py`、`test_m3_teacher_butler_chat.py`、`test_m3_teacher_capabilities.py`、`test_m3_teacher_registry_policy.py` |
| M3 门控默认关闭 / Alembic 单头 | `test_m3_teacher_profile.py` |
| 统一认证回归（合并线共存） | `tests/identity/`（16 文件） |

## 验证运行（2026-08-23 实测）

| 项 | 命令/方式 | 结果 |
| --- | --- | --- |
| 工作树基线（集成前） | pytest 15 个 test_m3_* 文件 | 127 passed |
| 合并后教师端+身份 | 同上 + `tests/identity` | **199 passed**（conftest 表数量核对 63/63） |
| 聚焦回归 | `test_m3_teacher_scope.py` + `test_m3_teacher_profile.py` | 22 passed |
| Ruff | `ruff check`（5 个涉改文件） | All checks passed |
| 前端单测 | `npm test`（D:\frontend） | 71/73 passed（2 失败为预存，见残留限制） |
| 前端构建 | `npm run build` | 通过（5.95s） |
| Alembic | `alembic upgrade head`（开发库 math_arena） | m3_002 → auth_001 成功；旧角色绑定迁移：student 3、teacher 2 条 approved |

## 残留限制（不含未支撑的完成声明）

1. ~~真实浏览器 E2E 未重跑~~ **已于 2026-08-23 补齐**：Docker 全栈（postgres/redis/minio/api
   容器 + Vite 前端 5176）起停与浏览器逐菜单走查完成，见下节"全栈浏览器验证记录"。
2. **前端 2 个预存失败**：`test/auth/securityPages.test.ts` 两项，根因是工作区未提交的
   M2 科研/管理端改动（`AdminNav.vue` 等），与教师端无关，本批未触碰（保留用户脏区约束）。
3. **后端全仓套件未整体重跑**：主工作区存在大量用户未提交 M2 改动（butler/gateway/测试），
   全仓结果会混入用户在制工作；本批以两条合并线（M3 教师 + identity）的 199 项聚焦绿为准。
4. **教师 scope 40301 二次防线在网关生效时不可直达**：`require_verified_teacher` 保留为
   纵深防御（网关配置变化或其他入口路径下仍生效），当前主路径由网关 40300 拦截。
5. ~~Docker 镜像未重建验证~~ 已重建（deploy-api 镜像基于合并后代码）并完成起停持久化验证。
6. **学生首页 3 个 growth/chat 接口偶发 ERR_ABORTED**（/api/student/growth/overview、
   /api/agent/conversations、/api/student/growth/panel）：页面渲染正常，疑似组件卸载中断或
   学生侧 growth 模块与统一认证的适配问题，属 M2 学生端在制工作，本批不触碰。

## 全栈浏览器验证记录（2026-08-23）

环境：`docker compose -f deploy/docker-compose.yml`（postgres/redis/minio/api 重建镜像）+
前端 `npm run dev`（Vite 5176 → 代理 127.0.0.1:8000）。种子 `scripts/seed_m3_demo.py`（幂等）。
登录方式：手机号 + demo SMS（challenge 响应回显 `demo_code`，页面同步提示）。

| 环节 | 结果 | 关键证据 |
| --- | --- | --- |
| 教师登录 → 角色切换 | PASS | 13900001001 短信登录（student）→ 顶栏"教师端" → `POST /api/auth/role/switch` → /teacher/today |
| 教师 Today | PASS | 待批作答/教学行动建议/快捷入口渲染，`GET /api/teacher/today` 正常 |
| 班级 + 课堂模式 | PASS | 高二（3）班洞察/成员；课堂模式开关切换 API 成功 |
| 备课 | PASS | /teacher/prep 渲染，本地降级生成可用 |
| 组卷 + 发布 | PASS | 范围"函数的单调性"、5 题；生成题目列表渲染；"确认并发布"成功 |
| 批改队列 | PASS | 待处理列表非空（历史演示数据），批改详情含题干/学生答案/标准答案参考与建议分 |
| 批改确认 | PASS | `POST /api/teacher/grading/{id}/confirm` 成功，待处理计数 21→20 |
| 资源页 | PASS | 列表与操作按钮正常，接口正常 |
| 学生登录 | PASS | 13900001002 → 学生首页（小婷），无 onboarding 跳转 |
| 学生查看/提交作业 | PASS | /tasks 列表 → 详情 8 题 → 校验拦截未答 → 补全 → `POST /api/student/practice/submit` → "待确认" → 刷新持久 |
| 重启持久化 | PASS | `docker restart` 后 health ok；DB：classroom_modes=1、assignments=4、submissions=3、quiz_items=51；教师 token 重签后 grading/queue、assignments 均 code=0 |

演示数据修复（非代码缺陷）：旧演示注册残留软删除 student 绑定干扰 active_role 选择（已清理）；
users.onboarding_status 默认 'required' 导致 onboarding 跳转（演示账号已置 'completed'）。

## 结论

教师核心完整性批次与全栈闭环批次的后端工作已全部并入 `feat/backend-m1` 并与统一认证
线共存：合并适配后 199/199 聚焦测试绿、前端教师端零回归、开发库迁移升至唯一 head
（auth_001_unified_identity）。剩余事项仅为上节 1–5 所列的非代码验证与用户在制 M2
工作的独立收敛。
