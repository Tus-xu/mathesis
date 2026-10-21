"""socratic_solver 真实 API 质量评测（迭代02）

直接驱动 _solve_verified（真实 LLM + 真实本地 SymPy 沙箱，不落库），
度量 solver v2 的核心质量指标：
- solved：产出可靠 plan（未降级）
- verified：双解一致（机器等价/文本一致）
- step_check.ran / failed：步骤级沙箱复算执行情况
- tool_calls：TIR 代码验证使用次数
- answer_match：终答与参考答案机器等价（check=equiv 的题）
- wall_s / solve_attempts：耗时与 LLM 调用数

用法：
    python -m eval.socratic_eval                 # 全部 20 题
    python -m eval.socratic_eval --tier basic    # 只跑某档
    python -m eval.socratic_eval --ids basic-1,gaokao-2
    python -m eval.socratic_eval --concurrency 3
结果写入 eval/results_iter02.json（含逐题明细 + 分档汇总）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

from app.providers.router import get_model_router
from app.providers.sandbox import check_equivalence
from app.skills.base import SkillContext
from app.skills.socratic_solver.main import SocraticSolverExecutor

PROBLEMS_PATH = Path(__file__).parent / "problems_20.json"
RESULTS_PATH = Path(__file__).parent / "results_iter05.json"


async def eval_one(problem: dict, llm) -> dict:
    """跑一题，返回度量明细"""
    ctx = SkillContext(
        user_id="eval",
        user_role="student",
        conversation_id=f"eval-{problem['id']}",
        request_id=f"eval-{problem['id']}",
        db=None,  # _solve_verified 不触库
        llm=llm,
        rag=None,  # 评测 solver 裸能力，不走题库底稿
    )
    executor = SocraticSolverExecutor()
    plan = None
    reason = ""
    t0 = time.monotonic()
    async for ev in executor._solve_verified(problem["question"], None, ctx):
        if ev["type"] == "_solve_done":
            plan = ev["data"]["plan"]
            reason = ev["data"]["reason"]
    wall_s = round(time.monotonic() - t0, 1)

    rec = {
        "id": problem["id"],
        "tier": problem["tier"],
        "solved": plan is not None,
        "degrade_reason": reason or None,
        "wall_s": wall_s,
    }
    if plan is None:
        return rec

    step_check = plan.get("step_check") or {}
    rec.update(
        {
            "verified": plan.get("verified"),
            "consistency": plan.get("consistency"),
            "difficulty": plan.get("difficulty"),
            "solve_attempts": plan.get("solve_attempts"),
            "tir_calls": len(plan.get("tool_calls") or []),
            "step_check_ran": step_check.get("ran"),
            "step_check_failed": step_check.get("failed"),
            "final_answer": plan.get("final_answer"),
            "steps_count": len(plan.get("steps") or []),
        }
    )

    # 参考答案机器等价（仅 check=equiv 的题）
    if problem.get("check") == "equiv" and plan.get("final_answer"):
        try:
            eq = await check_equivalence(plan["final_answer"], problem["answer"], timeout_ms=6000)
            rec["answer_match"] = eq.get("verdict") == "correct"
            rec["answer_match_method"] = eq.get("method")
        except Exception as e:  # 沙箱异常不致命，标注即可
            rec["answer_match"] = None
            rec["answer_match_error"] = str(e)[:120]
    return rec


def summarize(records: list[dict]) -> dict:
    """分档 + 总体汇总"""
    tiers = sorted({r["tier"] for r in records})

    def agg(rows: list[dict]) -> dict:
        solved = [r for r in rows if r["solved"]]
        checked = [r for r in solved if r.get("answer_match") is not None]
        return {
            "n": len(rows),
            "solved": len(solved),
            "solve_rate": round(len(solved) / len(rows), 3) if rows else 0,
            "verified_rate": (
                round(sum(1 for r in solved if r.get("verified")) / len(solved), 3) if solved else 0
            ),
            "step_check_ran": sum(1 for r in solved if r.get("step_check_ran")),
            "tir_used": sum(1 for r in solved if r.get("tir_calls")),
            "answer_match_rate": (
                round(sum(1 for r in checked if r["answer_match"]) / len(checked), 3)
                if checked
                else None
            ),
            "avg_wall_s": (
                round(sum(r["wall_s"] for r in rows) / len(rows), 1) if rows else 0
            ),
            "degrades": [r["id"] for r in rows if not r["solved"]],
        }

    return {
        "overall": agg(records),
        "by_tier": {t: agg([r for r in records if r["tier"] == t]) for t in tiers},
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tier", help="只跑某档（basic/medium/gaokao/contest）")
    parser.add_argument("--ids", help="只跑指定题号，逗号分隔")
    parser.add_argument("--concurrency", type=int, default=2, help="并发题数（默认 2）")
    args = parser.parse_args()

    problems = json.loads(PROBLEMS_PATH.read_text(encoding="utf-8"))
    if args.tier:
        problems = [p for p in problems if p["tier"] == args.tier]
    if args.ids:
        wanted = set(args.ids.split(","))
        problems = [p for p in problems if p["id"] in wanted]
    if not problems:
        print("没有匹配的评测题")
        return

    llm = get_model_router()
    print(f"评测 {len(problems)} 题（provider={getattr(llm, 'intended_provider', '?')}）…")
    sem = asyncio.Semaphore(args.concurrency)

    async def guarded(p: dict) -> dict:
        async with sem:
            print(f"  >> {p['id']} ...", flush=True)
            try:
                rec = await eval_one(p, llm)
            except Exception as e:
                rec = {"id": p["id"], "tier": p["tier"], "solved": False,
                       "degrade_reason": f"eval_error: {type(e).__name__}: {str(e)[:120]}",
                       "wall_s": 0}
            status = "OK" if rec["solved"] else f"FAIL({rec.get('degrade_reason')})"
            extra = ""
            if rec["solved"]:
                extra = (
                    f" verified={rec.get('verified')} steps={rec.get('steps_count')}"
                    f" tir={rec.get('tir_calls')} check_ran={rec.get('step_check_ran')}"
                    f" match={rec.get('answer_match')}"
                )
            print(f"  {status} {p['id']} {rec['wall_s']}s{extra}", flush=True)
            return rec

    records = await asyncio.gather(*(guarded(p) for p in problems))
    summary = summarize(list(records))

    out = {"summary": summary, "records": list(records)}
    RESULTS_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n===== 汇总 =====")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\n明细已写入 {RESULTS_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
