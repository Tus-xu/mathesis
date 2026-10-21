"""迭代05 智能出题生成质量评测（阶段 3.2，SSOT §4.7 / 星辰指南 §4.5）

三难度（easy/medium/hard）× 三题型（choice/blank/solution）9 组合真实 LLM 生成，
统计：良定性五检通过率（首过/重试后）、难度标注一致性、质量闸失败原因分布。

用法：cd services/api && python -m eval.smart_quiz_eval
"""

import asyncio
import json
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.providers.router import get_model_router
from app.skills.smart_quiz.main import generate_quiz_item, run_quiz_gates

KP = "MATH-G1-TRIG-001"
KP_NAME = "三角函数"
COMBOS = [(d, t) for d in ("easy", "medium", "hard") for t in ("choice", "blank", "solution")]


async def eval_one(llm, difficulty: str, q_type: str) -> dict:
    record = {"difficulty": difficulty, "q_type": q_type}
    t0 = time.monotonic()
    for attempt in range(1, 4):  # 首次 + 重试 2 次
        try:
            quiz_data, raw = await generate_quiz_item(
                llm,
                kp_code=KP,
                kp_name=KP_NAME,
                difficulty=difficulty,
                q_type=q_type,
                request_id=f"qzeval-{uuid.uuid4().hex[:8]}",
                retry_feedback="",
                temperature=0.8 if attempt == 1 else 0.5,
            )
        except Exception as e:
            record["error"] = f"llm_exception: {type(e).__name__}: {str(e)[:100]}"
            break
        if not quiz_data:
            record["llm_invalid"] = True
            continue
        passed, failures, notes = await run_quiz_gates(quiz_data)
        record["attempts"] = attempt
        if passed:
            record["passed"] = True
            record["difficulty_returned"] = quiz_data.get("difficulty")
            record["difficulty_consistent"] = (
                str(quiz_data.get("difficulty") or "").lower() == difficulty
            )
            record["gate_notes"] = notes
            break
        record["failures"] = failures  # 记录最后一次失败原因
    else:
        record["passed"] = False
    record["wall_s"] = round(time.monotonic() - t0, 1)
    return record


async def main() -> None:
    llm = get_model_router()
    print(f"出题评测 9 组合（provider={getattr(llm, 'intended_provider', '?')}）…")
    records = []
    for d, t in COMBOS:
        print(f"  >> {d}/{t} ...", flush=True)
        rec = await eval_one(llm, d, t)
        status = "PASS" if rec.get("passed") else "FAIL"
        extra = ""
        if rec.get("passed"):
            extra = f" diff={rec['difficulty_returned']} consistent={rec.get('difficulty_consistent')} attempts={rec.get('attempts')}"
        elif rec.get("llm_invalid"):
            extra = " llm_invalid"
        else:
            extra = f" failures={rec.get('failures')}"
        print(f"  {status} {d}/{t} {rec['wall_s']}s{extra}", flush=True)
        records.append(rec)

    n = len(records)
    passed = [r for r in records if r.get("passed")]
    first_pass = [r for r in passed if (r.get("attempts") or 1) == 1]
    consistent = [r for r in passed if r.get("difficulty_consistent")]
    summary = {
        "total": n,
        "passed": len(passed),
        "pass_rate": round(len(passed) / n, 3),
        "first_pass_rate": round(len(first_pass) / n, 3),
        "difficulty_consistency_rate": round(len(consistent) / max(1, len(passed)), 3),
        "avg_wall_s": round(sum(r["wall_s"] for r in records) / n, 1),
        "fail_reasons": {},
    }
    for r in records:
        if not r.get("passed"):
            fails = r.get("failures") or []
            reason = (";".join(fails) if fails else r.get("error") or "unknown")[:60]
            summary["fail_reasons"][reason] = summary["fail_reasons"].get(reason, 0) + 1

    out = {"summary": summary, "records": records}
    out_path = Path(__file__).parent / "results_smart_quiz_iter05.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n===== 出题评测汇总 =====")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\n明细已写入 {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
