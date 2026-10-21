# 教师端课件工坊复用双师课堂生成内核

- Status: in-progress（实现已完成；待真实教师服务/浏览器环境验收）
- Updated: 2026-09-09
- Branch/worktree: `D:\math-arena` (`rescue/teacher-final`) + `D:\frontend` (`rescue/teacher-frontend`), both normal dirty checkouts

## Objective

把学生端“双师课堂”已经具备的数学内容组织、结构化公式/例题/解答/结论、2D/3D 图形语义、证据注入和数学验证能力复用到教师端“课件工坊”。教师端继续使用 16:9 投影课件、1280×720 可编辑画布、教师专用分页、预览、保存和 PPTX 导出，不复制学生端双栏课堂界面、导师面板或练习交互。

## Current state and evidence

### User evidence

- `D:\1788914665.pptx` 和四张 2026-09-09 教师端截图均为课件工坊低质量样本，不是目标模板。
- PPTX 共 7 页，结构包合法，但无 `ppt/media/` 资源；第 1 页把 `[图形 hyperbola] show_foci=1 constant_diff=2 show_asymptotes=1` 当字面文本导出，第 2 至 7 页大多只有标题和重复公式。
- 学生端目标截图 `C:\Users\<user>\AppData\Local\Temp\codex-clipboard-3716efad-2c2b-4b4f-8b3b-437cb11738e6.png` 展示了定理、例题、分步解答、结论、公式和交互 3D 图形。用户明确指出该双栏 UI 不符合教师课件规范，仅作为能力参考。
- 用户补充两项 P0 缺陷：多页重复同一公式；课堂任务内容没有生成或渲染，页面只有标题/公式骨架。

### Student generation path

- API/SSE：`D:\frontend\src\api\index.js`、`src\api\sse.js`、`src\pages\student\DualView.vue`
- 生成内核：`services/api/app/domains/classroom/stage_router.py`
  - outline 与逐页生成
  - `required_blocks` / `figure_kind`
  - theorem、text、latex、example、table、note、geometry、plot2d materialization
  - `math_claims` / `geometry_claims`
  - 页级验证、失败重试、内容完整度门禁
- 数学与图形：`math_verifier.py`、`visual_spec.py`、前端 `MathFigure3D.vue` 和 `mathFigure3d.js`
- 持久化：`services/api/app/models/classroom.py` 的 JSONB outlines/slides/verification/source 数据

### Teacher generation path and root causes

- `services/teacher-platform/app/domains/generation/router.py` 创建 `ContextAssembler` 时没有注入 knowledge/learning provider，生成常拿到空证据包。
- `services/teacher-platform/app/pagination/schemas.py` 的 `SlideSemanticSpec` 主要只有 title/points/formula/visual，无法承载学生端 blocks、claims、evidence、verification。
- `services/teacher-platform/app/agent/graphs/generation.py` 在 LLM 或检索失败时可保存“待补充”骨架，而不是明确标记 `needs_review`。
- `services/teacher-platform/app/pagination/layout.py` 只支持标题、左侧要点/公式、右侧视觉的固定槽位，无法按 theorem/example/solution/conclusion 结构分页。
- `services/teacher-platform/app/domains/contents/templates_seed.py` 的多个常用页型 visual capacity 为 0，视觉可能触发布局错误或丢失。
- `D:\frontend\src\components\teacherV3\SlideCanvasV3.vue` 以扁平 `V3Element` 渲染；3D 缩略图仍可能只显示标记。
- `services/teacher-platform/app/export/pptx_engine.py` 对 functionPlot、dynamicDemo、boardJson、anchorPhoto 等类型没有完整导出路径，部分元素会被静默忽略。

### Dirty-worktree conflict（已保留）

两个仓库均为普通脏 checkout，目标文件在实施前已经有用户未提交改动。用户已明确授权
在当前状态就地窄补丁；本次没有回退、暂存、提交、整文件覆盖或吸收无关改动：

- Backend 目标改动集中在 generation graph/router/deterministic/rich_lesson、pagination schemas/layout、slide prompt、teacher evidence adapter、PPTX exporter 及对应测试。
- Frontend 目标改动集中在 teacherV3 API/types、SlideCanvasV3、SlidesView、deckCheck 及对应测试；学生端 DualView/MathFigure3D 的既有改动仅作为参考并未复制 UI。
- 既有的 backend butler/catalog/contents/auth/RAG 等 tracked diff，以及 frontend 其他页面、样式、删除文件和日志/脚本 untracked diff 均原样保留；`services/teacher-platform/app/pagination/scenes3d.py` 也未删除。

## Assumptions and decisions

1. 推荐共享版本化 `RichLessonSlide v1`，而不是直接复用学生 `DualView.vue` 或仅修改教师模板。
2. 教师服务不运行时导入学生服务的数据库/router；共享层只包含纯契约、纯 materializer、数学验证和图形 DSL，或通过稳定内部 API 访问。
3. 保留教师现有 `deck-outline` / `generation/deck` API、SSE 顺序、Deck JSONB、`V3Slide/V3Element`、编辑器和导出入口。
4. rich IR 到教师 slide 的 mapper 负责投影级分页与元素映射：定理、例题、步骤、结论、公式、表格、2D/3D 图形、来源证据。
5. 图形必须由已验证 claims/scene 驱动，禁止只凭标题关键词选择通用 preset。
6. 无证据、生成失败或验证失败必须进入 `needs_review` 并显示原因，不能生成看似完成的空骨架。
7. TDD：每项生产改动前先写并运行会失败的行为测试；实现后跑局部测试、全量相关测试、构建和浏览器验收。
8. 用户已明确授权 Luna 在当前两个脏工作区上就地实施；不得暂存、提交、回退、覆盖或吸收无关改动，所有编辑必须是窄范围补丁。
9. 公式必须按语义角色和归一化 LaTeX 去重；同一核心公式可在后页引用或变形，但不能作为缺失课堂内容的重复填充物。
10. 正文课件必须生成真实课堂任务：任务目标/问题情境、教师引导、学生行动、预期可检查产出与必要讲评信息。优先映射到现有 rich blocks、slide notes 和可编辑元素，避免引入无消费方的字段。

## Scope

- 共享 rich lesson contract / mapper / verification boundary。
- 教师生成器接入证据、rich blocks、claims 和 verification。
- 公式归一化去重与跨页重复检测。
- 课堂任务语义的生成、分页、渲染、保存和导出。
- 教师 block-aware pagination 与 16:9 page composition。
- 教师画布/缩略图/预览对公式、2D/3D 和 rich content 的真实渲染。
- PPTX 对所有已映射元素提供真实静态输出或显式失败/警告。
- Deck 保存、重载、PATCH 和 SSE round-trip 保留 rich metadata。

## Out of scope

- 复制学生端双栏课堂 UI、AI 主讲/助教面板、练习区、课堂播放器。
- 无关教师功能、科研端或学生端重构。
- 未经用户要求的 commit、push、deploy 或 PR。
- 将交互 3D 直接嵌入 PPTX；PPTX 使用确定性静态帧。

## Contracts and data changes

版本化共享语义：

```text
RichLessonSlide v1 {
  order, title, subtitle,
  blocks[], narration, key_points,
  required_blocks, figure_kind,
  math_claims, geometry_claims,
  source_evidence, verification_result
}
```

教师 `V3Slide` 继续包含 1280×720 元素坐标，并增加兼容字段（最终名称由现有模型约束决定）：

- `ir_version`
- `semantic_blocks`
- `source_evidence`
- `verification_result`
- 映射后的 `elements`

SSE 保留 `meta -> outline -> slide* -> paginate? -> done`，slide payload 增加上述兼容字段；旧 deck 仍能读取。

## Implementation steps

- [x] 1. 在当前 backend 工作区建立失败测试，固定 rich IR、学生 fixture 到教师 mapper、无证据/失败不得 ready、端点/持久化合同；另加重复公式和课堂任务缺失回归测试。
- [x] 2. 封装学生端纯 rich lesson 语义适配边界，教师服务只依赖 `RichLessonSlide v1`、纯 mapper/verifier 和自身 `EvidencePack`，不依赖学生 DB session/router。
- [x] 3. 为教师 generation wiring 注入真实 evidence provider；按 rich contract 生成 blocks、claims、服务端 source evidence 和确定性 verification。
- [x] 4. 新增 rich-to-teacher mapper，把 theorem/example/内嵌 analysis/answer/solution/conclusion/latex/table/geometry/plot2d 映射为教师可编辑页。
- [x] 4a. 在逐页生成、legacy 兼容路径和 mapper 中实现归一化公式去重；保留推导需要的变形公式，拒绝跨页机械重复。
- [x] 4b. 把课堂任务映射为教师投影可见任务页，并把教师提示、预期产出和讲评信息保存在 notes/metadata 中；legacy points 路径同样有任务门禁。
- [x] 5. 扩展 block-aware pagination：长解答拆页、视觉容量检查、保持 1280×720/16:9；真实导出样例已检查页级内容。
- [x] 6. 调整失败/降级状态：禁止“待补充”骨架进入 ready，返回 `needs_review` 和可见原因；模型自报 verified 不作为数学证明。
- [x] 7. 在当前 frontend 工作区先添加失败组件/契约测试，再扩展 teacher types、画布、缩略图和预览渲染；复用 renderer/normalizer，不复用学生 UI。
- [x] 8. 完善 PPTX exporter：formula、figure3d（含 polyline curves/planes）、functionPlot 等生成真实静态资源；无法导出的类型显式占位报告，禁止静默丢弃。
- [ ] 9. 验证保存、重载、编辑、预览、导出完整闭环；用“双曲线”和“点到面距离”两个主题做浏览器验收。
- [ ] 10. 仅在用户明确要求时集成、提交或部署。

## Validation

- [x] Student reference: `services/api/tests/test_classroom_openmaic_contract.py`（49 passed）
- [x] Student math: `services/api/tests/test_dual_teacher_math.py`（49 passed，与上一项合并记录）
- [x] Figure contract/render: `services/api/tests/test_figure_renderer.py`, `test_figure_frames.py`, `test_figure_conic.py`（90 passed）
- [x] Teacher generation: `services/teacher-platform/tests/unit/agent/test_generation_graphs.py` 及 rich 回归（相关集合最终 81 passed）
- [x] Teacher pagination/layout: `services/teacher-platform/tests/unit/domains/test_layout.py`, `test_pagination.py`（包含在相关集合）
- [ ] Teacher contracts/SSE: `services/teacher-platform/tests/contract/test_ifc001_schema.py` 已 16 passed；`test_sse_sequences.py` 仍受当前 baseline 的 `cache-control=no-store` 断言、未启动 compose、seed UUID 等既有环境/契约冲突阻塞，未修改无关路径。
- [x] Frontend teacher components: `D:\frontend\test\teacher\v3Components.test.ts` 与 `v3DeckCheck.test.ts`（19 passed）
- [x] Frontend student regression: `D:\frontend\test\student\dualClassroom.test.ts`（全量前端回归包含该文件）
- [x] Frontend production build/typecheck and `git diff --check`（全量 54 files/461 tests、typecheck、build 均通过；diff check 仅报告既有 CRLF 提示）
- [ ] Browser journey: teacher topic generation -> progressive slides -> edit -> preview -> PPTX export（需要启动真实教师服务及可用数据库/模型环境）
- [x] Render exported PPTX and inspect all slides：`C:\Users\<user>\AppData\Local\Temp\teacher-rich-representative.pptx` 共 5 页，PowerPoint PNG 渲染抽查全部 5 页；包含 2 个 `ppt/media`、2 个 `m:oMath`、5 个 notes，无 `[未支持的课件元素]`。

## Acceptance criteria

- [x] 教师入口生成的同一数学主题包含有效定理/公式/例题/步骤/结论结构，而非仅标题和重复公式。
- [x] 不再用同一标准公式填充多页；归一化后无教学作用的跨页重复会被消除或改成有上下文的推导/应用。
- [x] 每份正文课件至少含一个可执行课堂任务，投影页明确学生要做什么、基于什么材料作答、产出什么；教师 notes 含引导与讲评要点。
- [x] 每页可追溯 source evidence；无证据或失败时不伪装为 ready。
- [x] 数学公式在编辑画布、预览和 PPTX 中正确渲染。
- [x] 2D/3D 图形由验证后的 claims/scene 驱动，教师缩略图、画布、预览和 PPTX 均显示真实视觉（真实服务浏览器仍待验收）。
- [x] 长解答自动拆页，不出现内容溢出或整页大面积空白（分页/导出样例已验证；真实主题视觉巡检仍待浏览器验收）。
- [x] PPTX 不静默丢弃已支持元素；视觉页产生真实媒体资源。
- [x] 保存/返回 round-trip 保留 rich metadata、evidence、verification（路由测试捕获实际 `Deck` 持久化行）。
- [x] 保留教师端投影版式、属性面板、保存、导出和教师确认边界。
- [x] 不把学生端双栏课堂 UI 或互动面板迁入教师课件。

## Risks and rollback

- 当前关键文件仍存在大量未提交改动；本次已在用户授权下按目标文件做窄补丁，未回退、暂存、提交或覆盖无关改动。后续集成前仍需按目标文件清单逐项 review，避免把既有 dirty diff 一并带入。
- 直接跨服务 import 可能引入配置、数据库和部署耦合；优先共享纯模块或稳定内部 API。
- rich metadata 需向后兼容旧 Deck JSONB；使用 `ir_version` 和容错读取。
- 3D 交互无法原样进入 PPTX；导出确定性静态帧，并保留源 scene 供编辑器继续交互。
- 回滚边界：删除新增 mapper/shared layer，并恢复教师 generation 调用旧 semantic spec；旧 deck 读取不受影响。

## Execution notes

- 2026-09-09：Luna 完成只读调研，未修改产品代码、配置、测试或 Git 状态，也未运行测试。
- 2026-09-09：独立渲染用户 PPTX，确认 7 页、无媒体、字面图形占位和大量空白；文件结构本身通过 package/layout 检查。
- 2026-09-09：用户明确授权 Luna 在当前未提交改动上就地实现；交接进入 `in-progress`。
- 就地实现约束：只做窄范围补丁，保留并逐项检查已有 diff；禁止暂存、提交、回退或覆盖无关改动。
- 2026-09-09：按 TDD 完成 rich mapper、legacy task gate、普通 `topic+outline`（不带 `body.rich_slides`）端点级 RED→GREEN；路由测试确认真实 SSE generator 输出 `stop + deck_id`，捕获的 Deck 含 task、worked_example、检索 source、归一化后唯一公式。
- 2026-09-09：确定性教师 verifier 注入检索证据并覆盖模型自报 `verified`；有效内容可从上游 `needs_review` 晋级，非法 LaTeX/failed/缺证据/缺课堂任务保持 `needs_review`，不落为 ready。
- 2026-09-09：模型自报 citation 且无服务器检索证据的普通 rich 请求新增 RED→GREEN 门禁；仅真实 `EvidencePack`/确定性检索来源可作为 ready 的 evidence，相关 backend 集合最终 81 passed。
- 2026-09-09：新增学生 example `analysis/answer` → 教师解答续页、student polyline curves/planes → PPTX 静态媒体、plot2d `x0/x1`、boardJson/3D integer edge compatibility 回归；相关 backend 集合最终 81 passed。
- 2026-09-09：前端全量 54 个 test files/461 tests、`npm run typecheck`、`npm run build` 通过；PowerPoint PNG 渲染 5 页代表性 deck，视觉抽查全部 5 页（公式、3D pyramid、任务、解答续页、2D plot），并核对 media/OMML/notes 计数。
- 2026-09-09：真实 SSE contract suite 仍需 compose/数据库与既有 baseline 契约修复后复跑；当前未启动服务、未部署、未提交。
