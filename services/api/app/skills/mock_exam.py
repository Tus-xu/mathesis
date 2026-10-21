"""mock_exam — 模拟试卷/专题训练组卷（组卷逻辑主体，gateway 只负责信封与参数）

组卷纪律（题库优先重做）：
- ① 题库优先：逐槽位从 question_bank 结构化题库抽真题（kp 粒度双向展开 + 难度不足放宽
  + 卷内 hash 去重），命中槽位 0 LLM 调用；题库充足时整卷秒级响应。
- ② 日限闸：只限 AI 生成题（题库真题不占额度），按"今日 AI 已用 + 本次 AI 缺口"判定，
  不足抛 ExamDailyCapError（调用方 42901）——判定在 LLM 调用前，不烧额度。
- ③ AI 缺口：复用 smart_quiz 的单题生成（generate_quiz_item）与质量四闸（run_quiz_gates），
  不复制出题逻辑；解答题一律挂 SOLUTION_BIG_SPEC（高考大题 2~3 递进小问规格）。
  逐题并发（asyncio.gather + Semaphore(3) 限流）；每题失败带反馈重试 2 次
  （温度 0.8→0.5→0.3 阶梯收敛），仍失败丢弃并记录；可用题数（题库+AI 合并）
  <70% 计划数抛 ExamGenerationError（调用方 50301，绝不出空卷子）。
- 多选说明：判分契约 q_type 仅 choice/blank/solution 三枚举（student_router._VALID_Q_TYPES），
  不支持新高考"多选"题型；全真模拟卷按 单选8×5 + 填空3×5 + 解答5×19 = 150 改编
  （多选 3×6=18 分并入解答题均分，题量 19→16），偏差原因随 STRUCTURE_NOTE 如实透传。
- 超纲红线：仅采样高中学段知识点（grade 为空或"高"开头），杜绝非高中内容混卷。
"""

from __future__ import annotations

import asyncio
import random
import re
import uuid
from datetime import date
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.coursework import Quiz, QuizItem
from app.models.knowledge_point import KnowledgePoint
from app.models.question_bank import QuestionBank
from app.skills.question_supply import daily_ai_used, quiz_item_from_bank, supply_questions
from app.skills.smart_quiz.main import (
    RETRY_FEEDBACK,
    SOLUTION_BIG_SPEC,
    generate_quiz_item,
    run_quiz_gates,
)

logger = structlog.get_logger(__name__)

# 结构偏差说明（多选不支持的原因，随 full_mock 响应透传，前端如实展示）
STRUCTURE_NOTE = (
    "新高考原卷含多选 3×6 分；当前判分契约仅支持单选/填空/解答三题型，"
    "多选分值已并入解答题均分（解答 5×19），题量 19→16，总分保持 150。"
)

# 卷型规格：groups = [(q_type, 题量, 每题分值)]
# full_mock：8×5 + 3×5 + 5×19 = 150 分 / 120 分钟（新高考改编，多选并入见 STRUCTURE_NOTE）
# topic：6×5 + 2×10 + 2×25 = 100 分 / 45 分钟
EXAM_SPECS: dict[str, dict[str, Any]] = {
    "full_mock": {
        "duration_minutes": 120,
        "groups": [("choice", 8, 5), ("blank", 3, 5), ("solution", 5, 19)],
    },
    "topic": {
        "duration_minutes": 45,
        "groups": [("choice", 6, 5), ("blank", 2, 10), ("solution", 2, 25)],
    },
    # 阶段5：真题套卷（按年份抽题库真题，全真题不 AI 补题）
    "real_paper": {
        "duration_minutes": 60,
        "groups": [("choice", 8, 5), ("blank", 2, 5), ("solution", 2, 13)],
    },
}

# 可用题数低于计划数的该比例则整卷失败（不出空卷子）
_MIN_USABLE_RATIO = 0.7
# 全真模拟卷顶级模块覆盖下限（SRS：整卷覆盖 ≥4 个大模块）
_MIN_MODULE_COVERAGE = 4
# 逐题并发生成限流
_GEN_CONCURRENCY = 3
# 难度配比 easy:medium:hard = 7:2:1
_HARD_RATIO = 0.1
_MEDIUM_RATIO = 0.2


class ExamGenerationError(Exception):
    """组卷失败（质量闸通过率不足/知识库不足以成卷）——调用方返回 50301 而非空卷子"""


class ExamModuleNotFoundError(Exception):
    """专题模块不存在/非法——调用方返回 40400"""


class ExamDailyCapError(Exception):
    """今日 AI 出题额度不足（日限只计 AI 生成题，题库真题不占额度）——调用方返回 42901"""


async def assemble_real_paper(db, year: int, title: str | None, user_id, vol: str | None = None) -> dict:
    """阶段5 真题套卷 v2：按「年份+卷别」重组原卷——题号原序、同号去重（优先带图版）。

    数据事实（2013 实测）：Bench 各卷别内部题号连续自洽（choice 1-12 / blank 13-16 /
    solution 17-24），GMM 带图题与 Bench 同卷同号重叠（文字版 vs 图片版）。
    组卷规则：按 source 归一化的 (year, 卷别) 分组 → 组内按原卷号排序 →
    同号去重优先取带图版 → 全部收入（不 AI 补题、不随机抽题）。
    """
    rows = (
        await db.execute(
            select(QuestionBank).where(
                QuestionBank.deleted_at.is_(None),
                QuestionBank.scope == "student",
                QuestionBank.is_real_exam.is_(True),
                QuestionBank.year == int(year),
            )
        )
    ).scalars().all()
    if len(rows) < 5:
        raise ExamGenerationError(f"{year} 年真题库存不足（{len(rows)} 题），无法成卷")

    def vol_of(source: str) -> str:
        """卷别归一：Ⅰ卷、Ⅱ卷、甲卷、乙卷、早年不分卷的新课标卷 → 新课标卷"""
        s = source or ""
        if "甲卷" in s:
            return "甲卷"
        if "乙卷" in s:
            return "乙卷"
        if "Ⅱ" in s or "ⅱ" in s or re.search(r"II", s):
            return "Ⅱ卷"
        if "Ⅰ" in s or "ⅰ" in s or re.search(r"I", s):
            return "Ⅰ卷"
        return "新课标卷"

    def origin_no(stem: str) -> int:
        m = re.match(r"\s*(\d{1,2})\s*[.、．]", stem or "")
        return int(m.group(1)) if m else 999

    spec = EXAM_SPECS["real_paper"]
    groups: dict[str, list[QuestionBank]] = {}
    for r in rows:
        g = f"{vol_of(r.source or '')}"
        groups.setdefault(g, []).append(r)

    # 选卷：指定卷别优先，否则取题量最大的组；组内按原卷号排序、同号去重优先带图版
    if vol and vol in groups:
        best_vol, best = vol, groups[vol]
    else:
        best_vol, best = None, []
        for v, grp in groups.items():
            if len(grp) > len(best):
                best_vol, best = v, grp
    if len(best) < 5:
        raise ExamGenerationError(f"{year} 年各卷别可用真题均不足（最多 {len(best)} 题），无法成卷")

    by_no: dict[int, QuestionBank] = {}
    for r in best:
        no = origin_no(r.stem)
        cur = by_no.get(no)
        if cur is None or (not (cur.image or []) and (r.image or [])):
            by_no[no] = r  # 同号去重：带图版优先
    picked = [by_no[k] for k in sorted(by_no)]
    dropped_dup = len(best) - len(picked)

    final_title = (title or "").strip()[:200] or f"高考真题卷 · {year} 年{best_vol}"
    quiz = Quiz(
        user_id=user_id,
        source="exam:real_paper",
        title=final_title,
        kp_codes=sorted({c for r in picked for c in (r.kp_codes or [])[:1]}),
    )
    db.add(quiz)
    await db.flush()

    items: list[QuizItem] = []
    for item_no, row in enumerate(picked, start=1):
        kp = (row.kp_codes or [None])[0]
        items.append(quiz_item_from_bank(row, quiz_id=quiz.id, item_no=item_no, kp_code=kp))
        db.add(items[-1])
    await db.flush()

    counts: dict[str, int] = {}
    for r in picked:
        counts[r.q_type] = counts.get(r.q_type, 0) + 1
    structure, total_score = build_structure("real_paper", counts)
    with_img = sum(1 for r in picked if (r.image or []))
    logger.info(
        "mock_exam.real_paper_v2",
        year=year, vol=best_vol, items=len(items), with_img=with_img,
        dropped_dup=dropped_dup,
    )
    return {
        "quiz": quiz,
        "items": items,
        "type": "real_paper",
        "title": final_title,
        "planned": len(picked),
        "dropped": dropped_dup,
        "structure": structure,
        "total_score": total_score,
        "duration_minutes": spec["duration_minutes"],
        "bank_count": len(picked),
        "ai_count": 0,
        "with_img": with_img,
    }


def exam_type_of(source: str) -> str | None:
    """从 Quiz.source（exam:{type}）解析卷型；非试卷/未知卷型返回 None"""
    if not source or not source.startswith("exam:"):
        return None
    exam_type = source.split(":", 1)[1]
    return exam_type if exam_type in EXAM_SPECS else None


def top_module_of(kp_code: str) -> str:
    """由知识点 code 推顶级模块 code：剥掉末尾数字段（MATH-G1-FUNC-001 → MATH-G1-FUNC）"""
    return re.sub(r"-\d+$", "", kp_code)


# 合法知识点前缀白名单（迭代09 治理：测试曾以 "pb" 前缀写入"空点/校验点/题库点"等
# 占位知识点，grade 同为"高一"，仅按 grade 过滤无法排除，导致脏点混入组卷/图谱/出题。
# 真实数学知识点前缀固定为 MATH- / MX / BK（大写字母开头）；MX/BK 不带连字符是
# 因为模块类知识点采用 "MX{hex}-M{n}-NNN" / "BK{hex}-NNN" 形态（BK/MX 后直接跟
# 标识符再连叶子编号），带连字符的 "MX-" / "BK-" 反而匹配不到。
# TST 为测试命名空间（各测试文件 autouse fixture 自清洁 + conftest 全局兜底清理），
# 需放行以保证组卷类测试可造数据。白名单外一律视为测试/占位点（如 pb 前缀）。
_REAL_KP_PREFIXES = ("MATH-", "MX", "BK", "TST")


def is_real_kp_code(code: str | None) -> bool:
    """判断知识点 code 是否为真实数学知识点（白名单前缀），排除测试占位点"""
    if not code:
        return False
    return code.startswith(_REAL_KP_PREFIXES)


def is_real_kp(kp: KnowledgePoint) -> bool:
    """判断知识点对象是否为真实数学知识点（白名单前缀 + 高中 grade 双条件）"""
    if not is_real_kp_code(kp.code):
        return False
    return not kp.grade or kp.grade.startswith("高")


def build_structure(exam_type: str, counts: dict[str, int]) -> tuple[list[dict], int]:
    """按卷型规格与实际成卷题量构建 structure 与 total_score（score_each 取规格常量）"""
    structure: list[dict] = []
    total = 0
    for q_type, _planned, score_each in EXAM_SPECS[exam_type]["groups"]:
        cnt = int(counts.get(q_type, 0))
        structure.append({"q_type": q_type, "count": cnt, "score_each": score_each})
        total += cnt * score_each
    return structure, total


def _normalize_options(options) -> dict | None:
    """options 归一化为 dict（与 student_router._normalize_options 同规则：

    smart_quiz JSON 产出 list，quiz_items.options 为 JSONB dict。
    此处属 skills 层，反向 import gateway 会造成分层倒置，故保留 5 行同构实现。）
    """
    if isinstance(options, dict):
        return options
    if isinstance(options, list) and options:
        return {chr(ord("A") + i): str(opt) for i, opt in enumerate(options)}
    return None


def _leaf_kps(kps: list[KnowledgePoint]) -> list[KnowledgePoint]:
    """剔除"模块父行"（其 code 是其他知识点 code 前缀的分类行），只留可出题的叶子知识点"""
    parents = {top_module_of(k.code) for k in kps if top_module_of(k.code) != k.code}
    return [k for k in kps if k.code not in parents]


def _spread_difficulties(
    group_order: list[str], counts: dict[str, int], n_total: int
) -> dict[str, list[str]]:
    """难度按 7:2:1 全局配比分配；hard/medium 交叉落在各题型组尾部（压轴位，贴近高考排布）"""
    diffs = {qt: ["easy"] * counts[qt] for qt in group_order}
    # 槽位按"组倒序 × 组内倒序"交叉展开：solution 尾 → blank 尾 → choice 尾 → solution 次尾 …
    positions: list[tuple[str, int]] = []
    for idx in range(max(counts.values())):
        for qt in reversed(group_order):
            i = counts[qt] - 1 - idx
            if i >= 0:
                positions.append((qt, i))
    for level, quota in (
        ("hard", round(n_total * _HARD_RATIO)),
        ("medium", round(n_total * _MEDIUM_RATIO)),
    ):
        placed = 0
        for qt, i in positions:
            if placed >= quota:
                break
            if diffs[qt][i] == "easy":
                diffs[qt][i] = level
                placed += 1
    return diffs


def plan_slots(
    kps: list[KnowledgePoint], exam_type: str, *, rng: random.Random
) -> list[dict]:
    """按卷型规格排出逐题槽位（q_type/分值/知识点/难度）。

    full_mock：模块循环交叉分配，保证整卷覆盖 min(模块数, 题量) 个顶级模块
    （库内模块数 ≥4 时即满足 SRS"覆盖 ≥4 个大模块"）；模块内随机抽知识点。
    topic：kps 已为模块子树，直接随机抽取（允许重复，题量 > 知识点数时仍可成卷）。
    """
    spec = EXAM_SPECS[exam_type]
    groups: list[tuple[str, int, int]] = spec["groups"]
    n_total = sum(n for _qt, n, _s in groups)
    group_order = [qt for qt, _n, _s in groups]
    counts = {qt: n for qt, n, _s in groups}
    diff_map = _spread_difficulties(group_order, counts, n_total)

    module_list: list[str] = []
    modules: dict[str, list[KnowledgePoint]] = {}
    if exam_type == "full_mock":
        for kp in kps:
            modules.setdefault(top_module_of(kp.code), []).append(kp)
        if len(modules) < _MIN_MODULE_COVERAGE:
            raise ExamGenerationError(
                f"知识库顶级模块不足 {_MIN_MODULE_COVERAGE} 个（当前 {len(modules)} 个），无法组成全真模拟卷"
            )
        module_list = sorted(modules)
        rng.shuffle(module_list)

    slots: list[dict] = []
    idx = 0
    for q_type, n, score_each in groups:
        for i in range(n):
            if exam_type == "full_mock":
                module = module_list[idx % len(module_list)]
                kp = rng.choice(modules[module])
            else:
                kp = rng.choice(kps)
            slots.append(
                {
                    "q_type": q_type,
                    "score_each": score_each,
                    "kp_code": kp.code,
                    "kp_name": kp.name,
                    "difficulty": diff_map[q_type][i],
                    # 解答题一律按高考大题规格出（2~3 递进小问，防单问小题充大题）
                    "extra_spec": SOLUTION_BIG_SPEC if q_type == "solution" else "",
                }
            )
            idx += 1
    return slots


async def _gen_one(llm: Any, sem: asyncio.Semaphore, slot: dict) -> dict | None:
    """单题生成 + 质量四闸：失败带反馈重试 2 次（温度 0.8→0.5→0.3），仍失败返回 None（弃题）"""
    async with sem:
        retry_feedback = ""
        for attempt in range(3):  # 首次 + 带反馈重试 2 次
            try:
                quiz_data, _raw = await generate_quiz_item(
                    llm,
                    kp_code=slot["kp_code"],
                    kp_name=slot["kp_name"],
                    difficulty=slot["difficulty"],
                    q_type=slot["q_type"],
                    request_id=f"exam-{uuid.uuid4().hex[:12]}",
                    retry_feedback=retry_feedback,
                    extra_spec=slot["extra_spec"],
                    temperature=(0.8, 0.5, 0.3)[attempt],
                )
            except Exception as e:
                # 单题 LLM 瞬时异常不拖垮整卷：按失败处理，重试仍败则弃题
                logger.warning("mock_exam.item_llm_error", error=str(e)[:150])
                retry_feedback = RETRY_FEEDBACK.format(failures=f"生成异常：{type(e).__name__}")
                continue
            if not quiz_data:
                retry_feedback = RETRY_FEEDBACK.format(failures="JSON 解析失败，未输出合法 JSON")
                continue
            passed, failures, _notes = await run_quiz_gates(quiz_data)
            # 大题规格软闸（与 SmartQuizExecutor 同规则）：解答题必须 (1)(2) 分小问
            if passed and slot["extra_spec"] and "(1)" not in str(quiz_data.get("question_text", "")):
                passed = False
                failures = ["未按大题规格分小问：question_text 必须用 (1)(2) 标注 2~3 个递进小问"]
            # 闸 5 答案键独立黑盒复核（N2：判分键错位事故防复发，与 chat 出题路径同防线）
            if passed:
                from app.skills.smart_quiz.main import verify_answer_key

                ok, reason, _n = await verify_answer_key(
                    quiz_data, llm, f"exam-{uuid.uuid4().hex[:12]}"
                )
                if not ok:
                    passed, failures = False, [reason]
            if passed:
                return quiz_data
            logger.info(
                "mock_exam.gate_failed", attempt=attempt, failures="；".join(failures)[:150]
            )
            retry_feedback = RETRY_FEEDBACK.format(failures="；".join(failures)[:300])
        return None


async def assemble_exam(
    db: AsyncSession,
    llm: Any,
    *,
    user_id: uuid.UUID,
    exam_type: str,
    kp_module: str | None = None,
    title: str | None = None,
) -> dict:
    """组卷主流程：KP 采样 → 题库优先抽题 → AI 缺口并发逐题生成+四闸 → 落库 Quiz/QuizItem。

    抛出：ExamModuleNotFoundError（专题模块不存在 → 40400）/
    ExamDailyCapError（AI 额度不足 → 42901）/ ExamGenerationError（可用题不足 → 50301）。
    成功时 Quiz.source = "exam:{type}"（Quiz 无 mode 字段，卷型编码进 source 前缀，
    history/detail 据此过滤与解析，不新增表、不动模型）。
    """
    rng = random.Random()
    spec = EXAM_SPECS[exam_type]

    # 超纲红线：仅采样高中学段知识点（grade 为空视为高中库默认，"高"开头为高中学段）；
    # 迭代09 治理：同时必须是真实数学知识点（白名单前缀），排除测试占位点（pb 开头的空点/校验点等）
    rows = await db.execute(select(KnowledgePoint))
    all_kps = [k for k in rows.scalars().all() if is_real_kp(k)]
    leaf_kps = _leaf_kps(all_kps)

    if exam_type == "topic":
        # 模块子树：子级 code 以"模块 code-"为前缀；无子级时模块行自身兜底（扁平库形态）
        pool = [k for k in leaf_kps if k.code.startswith(f"{kp_module}-")]
        if not pool:
            pool = [k for k in all_kps if k.code == kp_module]
        if not pool:
            raise ExamModuleNotFoundError(f"专题模块不存在: {kp_module}")
        module_name = next((k.name for k in all_kps if k.code == kp_module), kp_module)
        kps = pool
        default_title = f"专题训练·{module_name}（{date.today()}）"
    else:
        kps = leaf_kps
        default_title = f"高考数学全真模拟卷（{date.today()}）"

    slots = plan_slots(kps, exam_type, rng=rng)
    planned = len(slots)

    # ① 题库优先：逐槽位从结构化题库抽真题（q_type/difficulty 按槽位，难度不足自动放宽；
    # 卷内 hash 去重，同一题不重复出现）。命中槽位不烧 LLM。
    bank_rows: list[QuestionBank | None] = []
    picked_hashes: set[str] = set()
    for slot in slots:
        rows = await supply_questions(
            db,
            kp_codes=[slot["kp_code"]],
            q_type=slot["q_type"],
            difficulty=slot["difficulty"],
            count=1,
            exclude_hashes=picked_hashes,
        )
        row = rows[0] if rows else None
        if row is not None:
            picked_hashes.add(row.hash)
        bank_rows.append(row)
    ai_slots = [s for s, r in zip(slots, bank_rows, strict=True) if r is None]

    # ② 日限闸：只限 AI 生成题（题库真题免费）；判定在任何 LLM 调用前，不烧额度
    if ai_slots:
        limit = settings.student_daily_practice_limit
        used_ai = await daily_ai_used(db, user_id)
        if used_ai + len(ai_slots) > limit:
            raise ExamDailyCapError(
                f"今日 AI 出题已达上限（{limit} 题）：已用 {used_ai}，本次需新生成 {len(ai_slots)} 题"
            )

    # ③ AI 缺口逐题并发生成（限流 3）+ 质量四闸；未过闸的题丢弃并记录，绝不出错题
    ai_data: dict[int, dict] = {}
    if ai_slots:
        sem = asyncio.Semaphore(_GEN_CONCURRENCY)
        results = await asyncio.gather(*(_gen_one(llm, sem, s) for s in ai_slots))
        ai_data = {id(s): d for s, d in zip(ai_slots, results, strict=True) if d is not None}

    # 合并（保持槽位组序：choice→blank→solution）：题库题 + AI 过闸题
    pairs: list[tuple[dict, dict | None, QuestionBank | None]] = []
    for slot, row in zip(slots, bank_rows, strict=True):
        if row is not None:
            pairs.append((slot, None, row))
        elif id(slot) in ai_data:
            pairs.append((slot, ai_data[id(slot)], None))
    dropped = planned - len(pairs)
    if dropped:
        logger.info("mock_exam.items_dropped", planned=planned, dropped=dropped)
    if len(pairs) < planned * _MIN_USABLE_RATIO:
        raise ExamGenerationError(
            f"可用题不足（题库+AI 合并 {len(pairs)}/{planned} 题），为保证不把错题给学生，本次不成卷"
        )

    final_title = (title or "").strip()[:200] or default_title
    quiz = Quiz(
        user_id=user_id,
        source=f"exam:{exam_type}",
        title=final_title,
        kp_codes=sorted({s["kp_code"] for s, _, _ in pairs}),
    )
    db.add(quiz)
    await db.flush()

    items: list[QuizItem] = []
    for item_no, (slot, data, row) in enumerate(pairs, start=1):
        if row is not None:
            # 题库真题：原题落卷（ai_generated=False，source 透传真题来源）；
            # kp_code 取槽位码（掌握度回填/模块覆盖与排布计划对齐），难度取题库真实值
            item = quiz_item_from_bank(row, quiz_id=quiz.id, item_no=item_no, kp_code=slot["kp_code"])
        else:
            item = QuizItem(
                quiz_id=quiz.id,
                item_no=item_no,
                q_type=str(data.get("q_type") or slot["q_type"]),
                question_text=str(data["question_text"]),
                options=_normalize_options(data.get("options")),
                answer=str(data["answer"]),
                answer_analysis=data.get("answer_analysis"),
                kp_code=slot["kp_code"],
                difficulty=str(data.get("difficulty") or slot["difficulty"]),
                ai_generated=True,
                sympy_check_code=data.get("sympy_check_code"),
            )
        db.add(item)
        items.append(item)
    await db.flush()

    # P1-3 防幻觉评分（迭代18）：组卷 AI 补缺题逐题评分落库
    try:
        from app.services.hallucination_score import persist_scores, score_items

        ai_pairs = [(s, d) for (s, d, r) in pairs if r is None and d is not None]
        if ai_pairs:
            score_rows = await score_items(
                [d for _s, d in ai_pairs],
                kp_names=[s.get("kp_name") or s["kp_code"] for s, _d in ai_pairs],
                expected_difficulties=[_s["difficulty"] for _s, _d in ai_pairs],
                kp_codes=[s["kp_code"] for s, _d in ai_pairs],
            )
            await persist_scores(
                score_rows,
                scene="mock_exam",
                request_id=f"exam:{quiz.id}",
            )
    except Exception as _se:
        logger.warning("mock_exam.score_failed", error=str(_se)[:150])

    counts: dict[str, int] = {}
    for slot, _d, _r in pairs:
        counts[slot["q_type"]] = counts.get(slot["q_type"], 0) + 1
    structure, total_score = build_structure(exam_type, counts)
    bank_count = sum(1 for _s, _d, r in pairs if r is not None)

    logger.info(
        "mock_exam.assembled",
        quiz_id=str(quiz.id),
        type=exam_type,
        items=len(items),
        bank_count=bank_count,
        ai_count=len(items) - bank_count,
        dropped=dropped,
    )
    return {
        "quiz": quiz,
        "items": items,
        "type": exam_type,
        "title": final_title,
        "planned": planned,
        "dropped": dropped,
        "structure": structure,
        "total_score": total_score,
        "duration_minutes": spec["duration_minutes"],
        # 构成标注：题库真题 / AI 生成各多少题（题库优先成效可观测）
        "bank_count": bank_count,
        "ai_count": len(items) - bank_count,
    }
