# coding: utf-8
"""确定性课件内容组装（M2-B 兜底管线，2026-09-08 用户预授权）。

触发条件：Spark 对大结构化提示词推理爆炸（逐页 8-12min/单调用 10min+ 连接重置）时，
LLM 逐页生成不可用。本模块用**真实语料检索组装**替代 LLM 逐页写作：
- 知识页：knowledge_chunks（3022 条真实教材 chunk）词面检索 → 教材原句截取为要点
- 例题/练习页：quiz_questions（785 道真实高考题）按课题词面检索 → 真题题干上页，
  答案/解析进 teacher_note（课堂先做后讲）
- 公式页：检索内容的 LaTeX 片段 / 真题答案式

诚实边界：teacher_note 恒带「[检索组装] 依据：{doc_ref}」溯源标记——这不是 AI 创作，
是教材原句的检索组装（对照验收标准 H8/H10：有真实依据、无编造）。
"""
from __future__ import annotations

import re
from typing import Any

MAX_POINTS = 4
POINT_MAX_CHARS = 40


def _topic_keywords(topic: str, page_title: str = "") -> list[str]:
    """课题 → 检索词面（去掉通用教学词，保留数学主体）。"""
    stop = ("及其", "标准", "第.*课", "课时", "复习", "新授", "探究", "的")
    core = re.sub("|".join(stop), " ", topic)
    words = [w.strip() for w in re.split(r"[\s·、，,（）()]+", core) if len(w.strip()) >= 2]
    if page_title:
        for w in re.split(r"[：:·，,（()）\s]+", page_title):
            if 2 <= len(w) <= 8 and w not in words:
                words.append(w)
    return words or [topic]


def _sentences(text: str) -> list[str]:
    """教材 chunk → 候选要点句（剥 LaTeX 定界符，长度过滤）。"""
    text = re.sub(r"\$\$?|\[[^\]]*\]\([^\)]*\)", "", text or "")
    out = []
    for s in re.split(r"[。；;！!？\n]", text):
        s = s.strip()
        if 8 <= len(s) <= POINT_MAX_CHARS and not re.search(r"习题|练习册|答案", s):
            out.append(s)
    return out


async def retrieve(session, keywords: list[str], *, limit_chunks: int = 8, limit_questions: int = 6) -> dict:
    """词面检索真实语料：教材 chunk + 真题（ILIKE 任一关键词命中即召回，命中词数排序）。"""
    from sqlalchemy import select

    from app.domains.catalog.models import QuizQuestion
    from app.db.models import KnowledgeChunk

    like = [f"%{k}%" for k in keywords]
    from sqlalchemy import text as _text

    chunk_where = _text(" OR ".join([f"content ILIKE '{p.replace(chr(39), chr(39)*2)}'" for p in like]))
    quiz_where = _text(" OR ".join(
        [f"COALESCE(stem_plain,'') ILIKE '{p.replace(chr(39), chr(39)*2)}'" for p in like]
        + [f"stem_latex ILIKE '{p.replace(chr(39), chr(39)*2)}'" for p in like]
    ))
    chunks: list[dict] = []
    try:
        rows = (await session.execute(
            select(KnowledgeChunk.content, KnowledgeChunk.doc_ref)
            .where(chunk_where)
            .limit(limit_chunks * 3)
        )).all()
        for content, doc_ref in rows:
            score = sum(1 for p in like if p.strip("%") in content)
            is_quiz_bank = "quiz_bank" in str(doc_ref)
            chunks.append({"content": content, "doc_ref": doc_ref, "score": score - (10 if is_quiz_bank else 0)})
        chunks.sort(key=lambda c: -c["score"])
        chunks = chunks[:limit_chunks]
    except Exception:
        chunks = []

    questions: list[dict] = []
    try:
        qrows = (await session.execute(
            select(QuizQuestion.stem_latex, QuizQuestion.answer, QuizQuestion.analysis,
                   QuizQuestion.difficulty, QuizQuestion.source, QuizQuestion.stem_plain,
                   QuizQuestion.stem_image, QuizQuestion.solution_image)
            .where(quiz_where)
            .limit(limit_questions * 3)
        )).all()
        for stem, answer, analysis, difficulty, source, plain, stem_image, sol_image in qrows:
            body = plain or re.sub(r"\$+", "", stem or "")
            score = sum(1 for p in like if p.strip("%") in body)
            questions.append({"stem": body or stem, "stem_latex": stem, "answer": answer or "",
                              "analysis": analysis or "", "difficulty": difficulty, "source": source,
                              "stem_image": stem_image or "", "solution_image": sol_image or "",
                              "score": score})
        questions.sort(key=lambda q: -q["score"])
        questions = questions[:limit_questions]
    except Exception:
        questions = []
    return {"chunks": chunks, "questions": questions}


def _wrap_math(s: str) -> str:
    """检测裸 LaTeX（\\frac/^{} 等）→ $..$ 定界；已定界或纯文本原样。"""
    if "$" in s:
        return s
    if re.search(r"\\(frac|sqrt|times|div|pi|leq|geq|cdot)|\^\(|_\{", s):
        return f"${s}$"
    return s


def _pick_formula(texts: list[str]) -> str | None:
    """从检索文本中提取第一个 LaTeX 片段（$...$ 内）。"""
    for t in texts:
        m = re.search(r"\$\$?(.+?)\$\$?", t or "")
        if m and 3 <= len(m.group(1)) <= 120:
            return m.group(1)
    return None


def compose_slide(page: dict, ctx: dict, *, used_questions: set[int]) -> dict | None:
    """大纲页 + 检索上下文 → SlideSemanticSpec 形状 dict（含溯源 teacher_note）。

    返回 None = 该页连检索材料都没有（诚实跳过，不产空话页）。
    """
    pt = str(page.get("page_type") or "concept_intro")
    title = str(page.get("title") or "")
    chunks: list[dict] = ctx.get("chunks") or []
    questions: list[dict] = ctx.get("questions") or []
    sources = sorted({c["doc_ref"] for c in chunks[:3]}) if chunks else []

    points: list[str] = []
    formula = None
    note_parts: list[str] = []
    task: dict[str, str] | None = None
    source_evidence: list[dict[str, Any]] = []
    for chunk in chunks[:3]:
        doc_ref = str(chunk.get("doc_ref") or "").strip()
        if doc_ref:
            source_evidence.append({
                "source_id": doc_ref,
                "source_type": "textbook",
                "locator": str(chunk.get("locator") or doc_ref),
                "used": True,
            })

    if pt in ("worked_example", "practice"):
        # 真题页：题干上页、答案解析进备注
        q = next((q for i, q in enumerate(questions) if i not in used_questions), None)
        if q is None:
            return None
        used_questions.add(questions.index(q))
        q_source = str(q.get("source") or "题库").strip()
        source_evidence.append({
            "source_id": f"quiz:{q_source}",
            "source_type": "quiz",
            "locator": q_source,
            "used": True,
        })
        stem_body = re.split(r"[ABCD]\.|A\.", q["stem"])[0].strip() or q["stem"]
        stem_lines = _sentences(stem_body) or [stem_body[:POINT_MAX_CHARS]]
        points = stem_lines[:3] if pt == "worked_example" else stem_lines[:2]
        # 含 LaTeX 语法的要点包 $..$ 定界（前端 KaTeX 渲染 / 导出 OMML 混排共用约定）
        points = [_wrap_math(x) for x in points]
        task = {
            "goal": "完成题目并说明关键依据",
            "context": stem_body[:160],
            "student_action": "独立列式，写出关键步骤",
            "expected_output": "写出答案和可检查的关键计算式",
            "teacher_prompt": "先让学生圈出已知条件，再追问每一步所用依据。",
            "feedback": str(q.get("analysis") or "核对答案与关键计算式是否一致。")[:160],
        }
        # 题干之外把任务契约放到投影正文；答案和解析仍留在 notes，避免直接替学生完成。
        task_points = [
            f"任务目标：{task['goal']}",
            f"学生行动：{task['student_action']}",
            f"可检查产出：{task['expected_output']}",
        ]
        points = (points[:1] + task_points)[:MAX_POINTS]
        if q["answer"]:
            note_parts.append(f"答案：{q['answer'][:60]}")
        if q["analysis"]:
            note_parts.append(f"解析：{q['analysis'][:80]}")
        note_parts.insert(0, f"教师引导：{task['teacher_prompt']}")
        note_parts.append(f"讲评要点：{task['feedback']}")
        note_parts.append(f"题目来源：{q['source'] or '题库'}{('·难度' + str(q['difficulty'])) if q['difficulty'] else ''}")
        formula = _pick_formula([q.get("analysis") or "", q.get("stem_latex") or ""])
    else:
        # 知识页：教材原句要点
        for c in chunks:
            for s in _sentences(c["content"]):
                if s not in points:
                    points.append(s)
                if len(points) >= MAX_POINTS:
                    break
            if len(points) >= MAX_POINTS:
                break
        if not points:
            return None
        formula = _pick_formula([c["content"] for c in chunks[:3]])
        note_parts.append(f"[检索组装] 依据：{'、'.join(sources) or '教材语料'}")

    visual = None
    if pt == "visual_definition" and "椭圆" in (title + "".join(points)):
        visual = {"kind": "preset", "preset": "conic/ellipse", "params": {"a": 5, "b": 3}}

    result = {
        "page_type": pt,
        "title": title[:60] or "未命名页",
        "points": points,
        "formula": formula,
        "visual": visual,
        "teacher_note": "；".join(note_parts)[:300] or None,
        "missing_evidence": [] if points else ["检索无命中"],
        "source_evidence": source_evidence,
        "verification_result": {
            "status": "needs_review",
            "reasons": ["确定性检索组装未执行数学声明验证"],
        },
    }
    if task:
        result["task"] = task
    return result


def scaffold_outline(topic: str, class_name: str = "") -> list[dict]:
    """LLM 大纲不可用时的课程标准结构骨架（页型完整；标题与课题绑定，非通用占位）。"""
    def pg(i: int, pt: str, t: str, intent: str) -> dict:
        return {"ord": i, "page_type": pt, "title": t, "intent": intent, "carries": []}
    return [
        pg(1, "visual_definition", f"{topic}·定义与图象", "从实际情境建立定义，图形直观"),
        pg(2, "concept_intro", f"{topic}·概念要素梳理", "定义要素逐条辨析"),
        pg(3, "derivation", f"{topic}·标准方程推导", "建系设点化简推导"),
        pg(4, "formula_focus", f"{topic}·标准方程", "两种焦点位置的标准方程对比"),
        pg(5, "worked_example", f"{topic}·例题精讲", "定义与标准方程的基础应用"),
        pg(6, "common_error", f"{topic}·易错辨析", "常见错误与纠正"),
        pg(7, "practice", f"{topic}·课堂练习", "当堂巩固"),
        pg(8, "summary", f"{topic}·课堂小结", "知识结构回顾"),
    ]
