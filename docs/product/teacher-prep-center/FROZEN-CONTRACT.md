# 备课中心 · 冻结契约（FROZEN-CONTRACT v2）

> 主控裁定，2026-09-20 冻结。**前后端并行开发的唯一接口约定**。
> 变更需主控批准并升版本号。任何一方不得单方面改字段名/枚举值。
> v2 增补 §F（教案结构扩展）与 §G（教材语料注入），因 Docker 卷丢失导致 textbook_v2 空库后的重建口径。

---

## A. DeckSpec 语义元素扩展（向后兼容）

### A.1 新增 `SlideSpec.elements`（可选，默认 `[]`）

保留既有 `content`（纯文本，向后兼容读取）；新增 `elements` 承载**语义化版式元素**。
**严禁出现坐标**（x/y/w/h）——版式由渲染层从 `role` 派生。

```json
{
  "id": "slide-02",
  "type": "concept",
  "title": "探究椭圆的几何性质",
  "content": "（保留：原始纯文本，兼容旧渲染）",
  "elements": [
    { "role": "definition", "text": "平面内与两个定点 $F_1$、$F_2$ 的距离之和等于常数 $2a$（$2a > |F_1F_2|$）的点的轨迹，叫做椭圆。", "latex": null, "items": [] },
    { "role": "formula", "text": null, "latex": "\\frac{x^2}{a^2} + \\frac{y^2}{b^2} = 1 \\quad (a > b > 0)", "items": [] },
    { "role": "figure", "text": null, "latex": null, "items": [], "figure_ref": "math_object" },
    { "role": "knowledge_card", "title": "椭圆的标准方程", "text": null, "latex": "\\frac{x^2}{a^2} + \\frac{y^2}{b^2} = 1", "items": ["$2a$ 是椭圆的长轴长，$2b$ 是椭圆的短轴长", "$a$、$b$ 分别叫做椭圆的长半轴、短半轴", "$c$ 叫做椭圆的半焦距"] },
    { "role": "bullets", "text": null, "latex": null, "items": ["探究椭圆的范围", "探究椭圆的对称性"] },
    { "role": "note", "text": "用数学的眼光观察世界", "latex": null, "items": [] }
  ]
}
```

### A.2 `role` 枚举（冻结）

| role | 语义 | 渲染层派生版式 | 必填字段 |
|---|---|---|---|
| `definition` | 定义带 | 浅蓝圆角面板，18px，行内 KaTeX | `text` |
| `formula` | 公式胶囊 | 白底描边圆角，KaTeX 显示式居中 | `latex` |
| `knowledge_card` | 知识卡 | 右侧描边圆角面板，标题 16px/700 + 居中大号 KaTeX + 蓝点列表 | `title` 或 `latex` |
| `figure` | 数学图形 | 参数化 SVG（读 `slide.math_object`） | `figure_ref="math_object"` |
| `bullets` | 要点列表 | 蓝点列表 | `items` |
| `note` | 页脚/旁注 | 斜体灰蓝小字 | `text` |
| `steps` | 推导步骤 | 编号步骤条 | `items` |
| `table` | 对比表 | 表头 + 行 | `items`（每项 `\|` 分隔单元格） |

### A.3 元素字段定义

```
role:  string（枚举，必填）
text:  string|null（≤400 字，可含行内 $...$ KaTeX）
latex: string|null（显示式 LaTeX 源码，不含 $ 定界符）
title: string|null（≤60 字，仅 knowledge_card 用）
items: string[]（≤10 项，每项 ≤200 字，可含行内 KaTeX）
figure_ref: "math_object"|null（仅 figure 用）
```

### A.4 版式派生规则（渲染层，前端 owner：FE-PPT）

- `title` 页（封面）：居中大标题 + 副标题（教材信息），无序号徽标
- 非封面页：页眉（蓝竖条 + 课题名 + 右上教材）→ 标题行（序号徽标 + 页标题）→
  定义带（若有 `definition`）→ 图形区（若有 `figure`，与 `knowledge_card` 并排）→
  公式胶囊 → 要点列表 → 页脚
- 序号徽标中文数字：`一 二 三 四 五 六…`（按非封面页序）

---

## B. 蓝图事实桶修复（后端 owner：BE-KERNEL）

### B.1 桶分类（冻结）

| 桶 | 来源 | 说明 |
|---|---|---|
| `concepts` / `theorems` / `inquiries` / `examples` | **教材事实**（确定性预填，保持现状） | 教材里真实存在 |
| `objectives` / `key_points` / `difficult_points` / `prior_knowledge` | **LLM 推导**（本轮新增） | 教研产物，教材无原文 |
| `lesson_flow` | **LLM 推导**（保持现状） | 已有 |

### B.2 推导要求（反幻觉边界）

1. 推导项**必须可追溯到教材事实**，`source_ref` 填依据 chunk 的 SourceLocator；
   确实无依据时 `source_ref=null` **且**该项不得写成断言（须标「（待确认）」）。
2. **禁止**为了填满而编造教材没有的内容。
3. 数量要求：`objectives` ≥2 项、`key_points` ≥2 项、`difficult_points` ≥1 项。
4. 覆盖率：对同一章节，占位符比例应 <20%（实测现状 100% 占位）。

### B.3 标题清理

`metadata.title` / 蓝图 `title` 必须清理占位后缀：`（待确认）`、`（第一课时，待确认）`、
末尾 `，待确认` 等。清理后不得为空。

---

## C. API 契约（不变更）

| 端点 | 用途 |
|---|---|
| `GET /prep/conversations/{sid}/specs` | 列出会话工件（deck/lesson_plan） |
| `GET /artifacts/decks/{deck_id}` | 读 deck spec |
| `GET /artifacts/lesson-plans/{spec_id}` | 读教案 spec |
| `POST /artifacts/decks/{deck_id}/patches` | 提交 patch（`expected_revision` 必填） |
| `POST /artifacts/decks/{deck_id}/pages` | 加页 |
| `POST /artifacts/decks/{deck_id}/regen` | 页级 regen |
| `POST /artifacts/{...}/exports` | 导出 |
| `GET /prep/conversations/{sid}/textbook/view?ref=<SourceLocator>` | 教材原文 |
| `PATCH /prep/conversations/{sid}/blueprint/node` | 节点编辑 |

---

## D. 前端命名空间（不变）

- 新代码只进 `D:\frontend\src\features\teacher-prep-center\`
- token 只用 `--pc-*`（`tokens/prep.css`）
- 单文件 ≤600 行
- 数学一律走 `math/KatexText.vue` / `math/katex.ts`

## E. 视觉硬数字（来自 VISUAL-RUBRIC，裁决用）

| 页 | 左栏 | 中区 | 右栏 |
|---|---|---|---|
| PPT | **251px** | 画布 ≈980px | **292px** |
| 教案 | **211px** | 纸区 ≈1147px | **310px** |
| 课堂设计 | 时间轴轨 ≈20px | 内容列 ≈1280px | — |

容差 ±2%。主色 `#2563EB`。页面底 `#F6F8FB`。**禁止深色面**。

---

## F. LessonPlanSpec 结构扩展（v2 新增，向后兼容；后端 owner：BE-KERNEL）

黄金图「教案」页需要六章齐全 + 过程表五列 + 板书三栏。现有 `LessonPlanSpec`
（`app/teacher_prep/specs/lesson_plan_schema.py`）缺 `板书设计` / `作业布置` / `教学反思`
与过程表列。**全部为 additive 可选字段（默认空），不得破坏既有读取方**。

### F.1 六章 ↔ 字段映射（冻结）

| 黄金图章节 | 字段 | 类型 |
|---|---|---|
| ① 教学目标 | `objectives` | `list[PlanItem]`（已有） |
| ② 教学重难点 | `key_difficult_points` | `list[PlanItem]`（已有；渲染层按 `kind` 分「重点/难点」小胶囊） |
| ③ 教学过程 | `lesson_flow[].columns` | **新增**（见 F.2） |
| ④ 板书设计 | `board_design` | **新增**（见 F.3） |
| ⑤ 作业布置 | `homework` | **新增** `list[PlanItem]` |
| ⑥ 教学反思 | `reflection` | **新增** `list[PlanItem]`（可为空 → 渲染层给「可点击编辑」虚线框，**不得伪造内容**） |

`key_difficult_points[].kind` 新增可选枚举 `"key" | "difficult" | null`，
用于渲染层区分「重点」/「难点」胶囊；为 `null` 时渲染层按顺序智能拆分（前段重点、后段难点）**仅作显示**，不写库。

### F.2 教学过程表五列（冻结）

`LessonPlanStage` 新增（全部可选、默认 `[]`）：

```
columns: {
  "teacher_activity": PlanItem[],   # 教师活动
  "student_activity": PlanItem[],   # 学生活动
  "design_intent":    PlanItem[]    # 设计意图
}
```

- 表格行 = `lesson_flow` 的一个 stage，**行数与教学路径节点一一对应**（KERNEL-RUBRIC §2.2）。
- `duration_minutes` 合计 ≤ 45（超预算由既有 `time_budget_exceeded` 逻辑拦截）。
- 单元格可含行内 KaTeX（`$...$`）。

### F.3 板书设计（冻结）

```
board_design: {
  "text_points": PlanItem[],        # 左列：文字要点
  "math_object": MathObjectSpec|null,  # 中列：数学图形（复用 Deck 的 MathObjectSpec，参数化 SVG）
  "formulas":    PlanItem[]         # 右列：公式区（`latex` 走 PlanItem.text 的行内 $...$ 或独立公式项）
}
```

- 三列**均须有内容**方可判 PASS（KERNEL-RUBRIC §2.2 板书三栏）。
- `math_object` 为 `null` 时，中列渲染「本课无参数化图形」诚实空态，**不得贴图占位**。

### F.4 投影来源（反幻觉不可越界）

- `homework` / `reflection`：LLM 推导桶（走 §B.1 的 LLM 推导路径，`source_ref` 指向依据 chunk）。
- `board_design.text_points` / `formulas`：**确定性投影自** `lesson_flow` 的 `board_notes`
  与该章节教材事实中的定理/公式（**不得由 LLM 自由创作**）。
- `board_design.math_object`：确定性投影自蓝图 `math_object` 提示（与 PPT 同源，`a> b >0`，`c=√(a²−b²)` 服务端派生）。
- 无依据时**留空并记 `warnings`**，禁止编造。

---

## G. 教材语料注入（v2 新增；后端 owner：BE-DATA）

### G.1 背景（环境事实，不可协商）

Docker 卷丢失 → `teacher_platform.textbook_v2` 全空，原唯一源 `D:/知识库/AC切片` 不在线。
可用的**真实**教材语料：`math_arena.public.chunks`（792 条）+
`knowledge_docs`（5 本人教A版2019）+ `knowledge_points`（301），已恢复入库。

### G.2 注入要求（冻结）

1. 只注入 `math_arena` 已恢复的**真实**切片，**禁止**生成/编造任何教材正文。
2. 粒度：`knowledge_docs` → `textbook_sources`；`chunks.meta.section` 分组 → `textbook_sections`
   （`level=2`，`section_path` 取编号如 `3.1.2`）；`chunks` → `textbook_chunks`（`content` 原文搬运）。
3. `rights_status` 必须为已授权态（`ACTIVE`）；`source_repo`/`source_path`/`file_sha256`
   填**真实可溯源**值（来自 `knowledge_docs.file_uri` 与内容 sha256），**不得伪造**。
4. `textbook_pages` 允许为空（无 PDF 页图）——但引用必须走 SourceLocator v1（`source/section/chunk`），
   **禁止 `page_refs` / `pNN`**。
5. 注入脚本必须**幂等**（重复执行不产生重复行），且提供 `--dry-run`。
6. 目标章节必须包含 `3.1.2 椭圆的简单几何性质`（黄金图同一课，验收锚点）。

### G.3 验收（可执行）

```sql
-- 必须有数据且包含目标章节
SELECT count(*) FROM textbook_sources;                                  -- ≥5
SELECT count(*) FROM textbook_sections WHERE section_path LIKE '3.1.2%';-- ≥1
SELECT count(*) FROM textbook_chunks;                                   -- ≥700
```

且 `GET /api/teacher-v3/prep/conversations/{sid}/textbook/view?ref=...` 能返回**真实原文**。
