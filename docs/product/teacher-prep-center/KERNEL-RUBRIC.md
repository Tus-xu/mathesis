# 备课中心 · 主控内核准则（KERNEL-RUBRIC，裁决者用）

> 判据来源：本轮对真实数据的实测（`tp_blueprints` / `ppt_v2_deck_specs` / `tp_lesson_plan_specs` / `tp_spec_projections`）+ 既有 schema 契约 + 8200 内核门禁。
> 冲突裁决：产品 Contract > 本文件 > ENGINEERING-STANDARDS。

---

## 0. 实测根因（2026-09-20 独立复核）

| # | 现象（用户抱怨） | 实测根因 | 证据 |
|---|---|---|---|
| G1 | **教案不堪入目** | `tp_lesson_plan_specs.body` 的 `objectives`/`key_difficult_points`/`assessment`/`evidence`/`examples` **全为空数组**；`warnings` 含 `unresolved_blueprint_placeholders:9`、`no_objectives_evidence`、`no_key_difficult_evidence`。投影器把 `（待确认…）` 占位符整条丢弃 → 教案 6 章中 4 章空白 | `tp_lesson_plan_specs` id=8965cf8a 实测 |
| G2 | 教案标题带「（待确认）」 | `metadata.title = "3.1.2 椭圆的简单几何性质（第一课时，待确认）"` —— 蓝图 title 原样透传，未清理占位后缀 | 同上 |
| G3 | **PPT 质量堪忧** | `ppt_v2_deck_specs.spec.slides[]` 只有 `title + content(纯文本\n分隔)`；黄金图要求的**定义带 / 公式胶囊 / 知识卡 / 图形标注 / 序号徽标**在 spec 层**根本没有对应字段**（schema 2.0.0 无元素结构，只有 `math_object`） | `ppt_v2_deck_specs` id=b5be4e57 实测；schema 2.0.0 |
| G4 | PPT 页数不达标 | 真实内容不足时投影器按桶补页，实测 6 页（内核预算 [8,12]），且 `slide-04 先备知识` 仅 1 条内容 → 页面单薄 | `spec.slides` 实测 |
| G5 | 生成慢 | 会话消息同步阻塞 LLM 已修为 202 受理制（上轮）；但**投影/工件产出仍走同步路径**，需实测确认 | 上轮 HANDOVER §1.2 |
| G6 | 蓝图本体质量 | 蓝图 `objectives`/`key_points`/`theorems` 全部是 `（待确认）…` 占位文本，仅 `lesson_flow` 有教师真实编辑内容 —— **LLM 提议阶段没有产出可用事实**，教师手动补了 lesson_flow，其余桶留空 | `tp_blueprints` 实测 |

**结论**：G1/G2/G6 是同一根因链——**蓝图提议阶段产出占位符 → 投影阶段诚实丢弃 → 产物空白**。
修复必须打在这条链的源头，而不是在投影器里给占位符开绿灯（那会造成幻觉）。

---

## 1. 内核铁律（违反 = FAIL）

### 1.1 单一真相源与投影链
1. **Teaching Blueprint（tp_blueprints）是唯一教学事实源**；PPT 与教案都从**同一 approved revision + 同一 context snapshot** 确定性投影。
2. **禁止 PPT / 教案各自重新 LLM 生成**。投影必须无 LLM（快路径、可复现、hash 稳定）。
3. 投影血缘 `projection`（`run_hash`/`body_hash`/`approved_blueprint_revision`/`context_snapshot_hash`）必须齐全且与源一致；`body_hash` 必须为真实值（非 `0*64`）。
4. 双 Spec 的 `approved_blueprint_revision` 与 `context_snapshot_hash` **必须相等**（R20 锚点）。

### 1.2 反幻觉（最高优先级）
5. **占位符不得进产物，但也不得靠投影器"猜"**：`（待确认…）` 的根治手段是**让 LLM 在提议阶段产出真实内容**，而不是在投影阶段填充。
6. **教材引用必须来自 textbook_v2 检索**（SourceLocator v1：`source/section/chunk`）。禁止编造页码、禁止 `pNN` 假页码（新写入 `page_refs` 必须为空）。
7. **例题三源分标签**：教材例题（textbook_problems）/ 题库（quiz_questions）/ AI 生成。禁止 AI 生成冒充教材例题。
8. **数学必须正确**：椭圆只存独立参数 `a`/`b`，`c = √(a²−b²)` 恒派生、不落库；构造时强校验 `a > b > 0`。公式 canonical 由服务端重算。
9. **失败要留痕**：LLM 超时 → 确定性兜底必须写入 `warnings` 与 `ai_runs`；教师界面只见产品化文案。

### 1.3 教师主权
10. **块级 edit origin**：教师手工编辑记录来源；AI regen **不得覆盖教师手工块**（走 confirm/diff）。
11. **一切 AI 修改走 spec patch**（局部、可回滚、记 revision）；乐观并发 `expected_revision` 必填。
12. **刷新/重进必须还原**：会话、蓝图、PPT、教案、对话全部从后端还原。

### 1.4 速度与渐进
13. **首响 ≤ 数秒**：消息 POST 立即 202 受理（实测 0.047s）；进度经 SSE 事件推送。
14. **进度必须映射真实任务状态**（queued/running/completed/failed），禁止假进度条。
15. **渐进可见**：蓝图确认后，教学路径先出现，PPT/教案逐步出现（真实 n/N 页级进度）。

### 1.5 用例注册红线
16. 任何 `use_case` 必须存在于 `app/llm/config.py::USE_CASE_ROUTES`；缺失即运行期 500。已知需覆盖：`lesson_plan_regen`、`ppt_regen`、`blueprint_propose`、`ai_add_page`、`deck_page_regen`。

---

## 2. 产物质量验收（可执行判据）

### 2.1 PPT 页
| 检查 | 通过标准 |
|---|---|
| 页数 | 8 ≤ N ≤ 12（内核预算） |
| 每页内容 | 每页 ≥2 条真实内容项；无 1 条单薄页 |
| 版式差异 | 相邻页 `layout_hint` 不重复；同族页不连续 >2 |
| 元素齐备 | 概念页须能渲染出黄金图要素（序号徽标/标题/定义带/图形/公式/知识卡/页脚） |
| 收尾页 | summary 页 items ≥3 |
| 无占位 | 可见文本零 `（待确认）` |
| 数学图形 | 至少 1 页含 `math_object`，参数改动能联动 |

### 2.2 教案页
| 检查 | 通过标准 |
|---|---|
| 六章齐全 | 教学目标 / 教学重难点 / 教学过程 / 板书设计 / 作业布置 / 教学反思 **均非空** |
| 教学过程表 | 行数与蓝图 `lesson_flow` 节点**一一对应**，含时间列且合计 ≤45min |
| 元信息 | 学科/教材/班级/课时 四项真实 |
| 标题 | 不含「待确认」等占位后缀 |
| 公式 | 行内 KaTeX 正确渲染 |
| 板书三栏 | 文字要点 / 数学图形 / 公式区 三列均有内容 |

### 2.3 真实产物检查（R7+ 终审门，禁止以"页面显示出来"判定成功）
| # | 检查 | 通过标准 |
|---|---|---|
| A1 | PPT 页面渲染 | 每类版式 ≥1 页真实渲染截图，无溢出/重叠/空白 |
| A2 | spec vs 渲染一致 | 抽查 3 页：元素数与内容一致 |
| A3 | 真实导出 | PPTX 实体落盘（MinIO + 下载），解包验证 `<a:t>` 可编辑文本存在 |
| A4 | 可打开 | 无修复弹窗（XML 结构校验或真实打开截图） |
| A5 | 公式抽查 | 5 处公式与教材/数学事实一致（LaTeX 源 + 渲染双查） |
| A6 | 图形抽查 | 改参 → 图形/焦点/公式联动正确 |
| A7 | 教学流程 | 时间合计 ≤45min，与蓝图一致 |
| A8 | 教案内容 | 六章齐全、过程表与节点一致、无空章节 |
| A9 | 一致性 | 同一知识点在 PPT/教案中数值/术语/时间一致（抽查 3 处） |
| A10 | 修改持久化 | 教师编辑 → 保存 → 再导出 → 重进，修改保留且进导出物 |

---

## 3. 验证门（每轮必跑）

```bash
# 后端单测（在 services/teacher-platform）
uv run pytest tests/unit/teacher_prep tests/unit/ppt_v2 tests/unit/textbook_v2 -q

# 基线失败清单（预存债，不计入本轮回归）
# - test_spec_projection（specs 域预存）
# - test_migration_is_unique_alu_head（预存）
# - test_chat_pins_spark_4b_and_thinking_disabled（孤儿 R4 改动副作用，需裁决）
```

- **不得以"编码通过"代替验收**：必须有真实登录态、真实后端、1672×941 DPR1 截图。
- **编码者 ≠ 裁决者**：子代理截图，主控逐图对照黄金图裁决。

---

## 4. 已知环境真相（本轮实测）

| 项 | 值 |
|---|---|
| Docker 容器 | `teacher-postgres`(54330)、`ma-minio-9002`(9002)、`ma-valkey-6380`(6380) —— 须 `docker start` |
| 教师平台 | 8100，需 env 覆盖 `DATABASE_URL`/`VALKEY_URL`/`MINIO_*`/`LEGACY_JWT_SECRET` |
| 统一网关 | 8000，需 `DATABASE_URL`(54330/math_arena) + `JWT_SECRET` |
| Deck Engine | 8200（`uv run uvicorn main:app --port 8200`）+ `worker.py` |
| 前端 | 5176（`pnpm dev`） |
| LLM | `doubao-seed-2.0-pro` @ `ark.cn-beijing.volces.com/api/plan/v3`，实测 200 / 3.6s |
| 测试账号 | 教师 `13800001234`，演示码从登录页读取 |
| 截图工具 | `D:/frontend/tools/prepc/pc_lib.mjs`（主控自建） |
| 会话数据 | 9 个真实会话；最新 `e27e11d1-ddc6-49f1-80a3-04bd61eec20f` |
