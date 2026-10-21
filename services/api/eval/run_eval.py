"""评测脚本

一键跑 router_30 / rag_30 评测，输出准确率表。

用法：
    cd services/api
    python -m eval.run_eval [--router] [--rag] [--all]

注意：
- router 评测走真实 route() 路径（含 LLM Function Calling），约需数分钟
- 控制台输出强制 UTF-8（Windows GBK 兼容）；JSON 兼容 BOM
"""

import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.kernel.rag import RAGPipeline
from app.kernel.router import IntentRouter
from app.models.database import async_session_factory

EVAL_DIR = Path(__file__).parent


def _force_utf8_console() -> None:
    """Windows GBK 控制台兼容：强制 stdout/stderr 为 UTF-8"""
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream.encoding and stream.encoding.lower() != "utf-8":
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def load_json(filename: str) -> list[dict]:
    # utf-8-sig 兼容带 BOM 的 JSON 文件
    with open(EVAL_DIR / filename, encoding="utf-8-sig") as f:
        return json.load(f)


async def eval_router():
    """路由评测：30 条意图标注（真实 route() 路径，含 LLM FC）"""
    print("\n" + "=" * 60)
    print("路由评测 (router_30.json) — 真实 Function Calling 路径")
    print("=" * 60)

    cases = load_json("router_30.json")
    router = IntentRouter()

    correct = 0
    total = len(cases)
    results = []

    async with async_session_factory() as db:
        for case in cases:
            msg = case["message"]
            expected = case["expected_skill"]

            t0 = time.monotonic()
            try:
                decision = await router.route(
                    msg,
                    db=db,
                    user_id="eval",
                    request_id=f"eval_router_{case['id']}",
                )
                predicted = decision.skill_id
                confidence = decision.confidence
            except Exception as e:
                predicted = "<error>"
                confidence = 0.0
                print(f"  [WARN] Case {case['id']} error: {e}")
            latency = int((time.monotonic() - t0) * 1000)

            hit = predicted == expected
            if hit:
                correct += 1

            results.append(
                {
                    "id": case["id"],
                    "message": msg[:30],
                    "expected": expected,
                    "predicted": predicted,
                    "confidence": f"{confidence:.2f}",
                    "hit": "✓" if hit else "✗",
                    "latency_ms": latency,
                    "note": case.get("note", ""),
                }
            )

    # 输出结果表
    print(
        f"\n{'ID':<4} {'消息':<32} {'期望':<10} {'预测':<10} {'置信':<6} {'命中':<4} {'耗时ms':<8}"
    )
    print("-" * 100)
    for r in results:
        print(
            f"{r['id']:<4} {r['message']:<32} {r['expected']:<10} {r['predicted']:<10} "
            f"{r['confidence']:<6} {r['hit']:<4} {r['latency_ms']:<8}"
        )

    accuracy = correct / total * 100
    print(f"\n准确率: {correct}/{total} = {accuracy:.1f}%")
    return accuracy


async def eval_rag():
    """RAG 评测：30 条（20 教材内 + 10 教材外）"""
    print("\n" + "=" * 60)
    print("RAG 评测 (rag_30.json)")
    print("=" * 60)

    cases = load_json("rag_30.json")
    pipeline = RAGPipeline()

    correct = 0
    total = len(cases)
    results = []

    async with async_session_factory() as db:
        for case in cases:
            question = case["question"]
            expected_answerable = case["expected_answerable"]

            t0 = time.monotonic()
            try:
                rag_result = await pipeline.retrieve(
                    question, db=db, conversation_history=[], request_id=f"eval_{case['id']}"
                )
                predicted_answerable = rag_result.answerable
            except Exception as e:
                predicted_answerable = False
                print(f"  [WARN] Case {case['id']} error: {e}")

            latency = int((time.monotonic() - t0) * 1000)
            hit = predicted_answerable == expected_answerable
            if hit:
                correct += 1

            results.append(
                {
                    "id": case["id"],
                    "question": question[:25],
                    "expected": "可答" if expected_answerable else "拒答",
                    "predicted": "可答" if predicted_answerable else "拒答",
                    "hit": "✓" if hit else "✗",
                    "latency_ms": latency,
                    "note": case.get("note", ""),
                }
            )

    # 输出结果表
    print(f"\n{'ID':<4} {'问题':<27} {'期望':<6} {'预测':<6} {'命中':<4} {'耗时ms':<8} {'备注'}")
    print("-" * 90)
    for r in results:
        print(
            f"{r['id']:<4} {r['question']:<27} {r['expected']:<6} {r['predicted']:<6} {r['hit']:<4} {r['latency_ms']:<8} {r['note']}"
        )

    accuracy = correct / total * 100
    print(f"\n准确率: {correct}/{total} = {accuracy:.1f}%")

    # 分类统计
    in_book = [r for r in results if r["expected"] == "可答"]
    out_book = [r for r in results if r["expected"] == "拒答"]
    in_hit = sum(1 for r in in_book if r["hit"] == "✓")
    out_hit = sum(1 for r in out_book if r["hit"] == "✓")
    if in_book:
        print(f"  教材内召回率: {in_hit}/{len(in_book)} = {in_hit/len(in_book)*100:.1f}%")
    if out_book:
        print(f"  教材外拒答率: {out_hit}/{len(out_book)} = {out_hit/len(out_book)*100:.1f}%")

    return accuracy


async def main():
    _force_utf8_console()

    args = sys.argv[1:]
    run_router = "--router" in args or "--all" in args or not args
    run_rag = "--rag" in args or "--all" in args or not args

    print("Math Arena M1 评测")
    print(f"时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")

    if run_router:
        await eval_router()

    if run_rag:
        await eval_rag()

    print("\n[DONE] 评测完成")


if __name__ == "__main__":
    asyncio.run(main())
