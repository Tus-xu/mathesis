"""RV09 · Prompt eval —— 对**已发布提示词**在真实模型上的五项度量。

五项指标（任务书要求）
----------------------
| 指标 | 定义 | 为什么这样定义 |
|---|---|---|
| `schemaValidity` | 输出能通过 `parse_proposal` 的用例比例 | 契约门的直接刻画 |
| `requirementCoverage` | 用例 rubric 声明"问题里**确实给了**"的段落被填上的比例 | 只看能填的段；对没给的段要求填 = 鼓励编造 |
| `unsupportedGrounding` | 元素级 `origin=SOURCE` 中**服务端核对不通过**的比例 | 这是"模型自证来源"的量化，越低越好 |
| `injectionResistance` | 注入用例里输出仍合规且未出现被禁标记的比例 | 注入没能改写行为才算抵抗住 |
| `stability` | 同一用例多次运行：kind 一致率 + 段落键集合 Jaccard 均值 | 一次好不算好 |

**刻意不为样题硬编码答案**
--------------------------
本脚本从不把模型输出与任何"标准答案"字符串比较。rubric 只声明两类**结构性质**：
1. `expectFilled` —— 问题里确实提供了的信息对应的段（例如给了半径 ⇒ `knowns` 应非空）；
2. `mustNotFabricate` —— 问题里**没有**提供、因此必须留空或降级为 INFERENCE 的段。

这两者都是对"输入可推出什么"的声明，而不是"输出应该长什么样"的答案抄本。
因此换一批样题不需要改断言逻辑，换模型也不需要改 rubric。

用法：
    python scripts/rv09_prompt_eval.py --repeat 3 --out <dir>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SERVICE_ROOT = Path(__file__).resolve().parents[1]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from research_vnext.core.domain import output_guard as guard  # noqa: E402
from research_vnext.core.domain import proposals as P  # noqa: E402
from research_vnext.core.domain import proposal_handlers as H  # noqa: E402
from research_vnext.core.domain import research_map as RM  # noqa: E402
from research_vnext.core.evidence.origin_grounding import (  # noqa: E402
    ground_content_origins,
)
from research_vnext.core.llm import context_manifest as CM  # noqa: E402
from research_vnext.core.llm import gateway  # noqa: E402
from research_vnext.core.llm import pricing  # noqa: E402
from research_vnext.core.llm.prompts import registry as prompts  # noqa: E402

CASES = SERVICE_ROOT / "tests" / "fixtures" / "rv09" / "eval" / "cases.jsonl"


@dataclass
class CaseResult:
    case_id: str
    repeat: int
    ok: bool = False
    kind: str = ""
    schema_valid: bool = False
    schema_error: str = ""
    sections: list[str] = field(default_factory=list)
    filled: list[str] = field(default_factory=list)
    insufficiencies: list[str] = field(default_factory=list)
    contradictions: int = 0
    unsupported_grounding: int = 0
    origin_elements: int = 0
    forbidden_output: list[str] = field(default_factory=list)
    obeyed_injection: bool = False
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    cost_usd: float | None = None
    cost_basis: str = ""
    model: str = ""
    prompt_ref: str = ""
    raw_excerpt: str = ""


def load_cases() -> list[dict]:
    out = []
    for line in CASES.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def _problem_map_sections(content: dict) -> tuple[list[str], list[str]]:
    keys = [
        "researchQuestion",
        "scope",
        "nonGoals",
        "knowns",
        "unknowns",
        "constraints",
        "successCriteria",
        "falsificationCriteria",
        "assumptions",
        "risks",
        "requiredEvidence",
    ]
    present = [k for k in keys if k in content]
    filled = [k for k in keys if isinstance(content.get(k), list) and content[k]]
    return present, filled


def _count_origin_elements(value: Any) -> int:
    n = 0
    if isinstance(value, dict):
        if isinstance(value.get("origin"), str):
            n += 1
        for v in value.values():
            n += _count_origin_elements(v)
    elif isinstance(value, list):
        for v in value:
            n += _count_origin_elements(v)
    return n


def _grounding_report(content: dict) -> tuple[int, int]:
    """服务端视角的"来源能否被核对"。

    以**空 grounded 集合**运行裁决 = "本会话没有任何可用证据"的情形。
    这是最保守的判据：此时任何 `origin=SOURCE` 都是模型自证，必须被降级。
    返回 `(降级数, 元素总数)`。
    """
    _, report = ground_content_origins(
        content,
        grounded_ids=set(),
        user_text=None,
        proposal_refs=None,
        deep=True,
    )
    downgraded = int(report.get("downgraded", 0))
    total = _count_origin_elements({k: v for k, v in content.items() if k not in {"origins", "insufficiencies", "contradictions"}})
    return downgraded, total


async def run_case(case: dict, repeat: int) -> CaseResult:
    res = CaseResult(case_id=case["id"], repeat=repeat)
    prompt = prompts.require_published(prompts.ASSISTANT_PROPOSE_ID)
    res.prompt_ref = prompt.ref

    ctx_text, manifest = CM.build_manifest(
        manifest_id=f"eval:{case['id']}:{repeat}",
        namespace={"tenantId": "eval", "userId": "eval", "researchId": "eval", "purpose": "ASSISTANT_STYLE"},
        request_text=case["question"],
        injection_signals=guard.scan_input(case["question"]).signals,
    )
    messages = [
        {"role": "system", "content": prompt.system},
        {
            "role": "user",
            "content": prompt.render(
                context_manifest=ctx_text,
                user_text=prompts.wrap_untrusted(case["question"]),
                title="eval",
                mode="MODELING",
                timeline_len="1",
            ),
        },
    ]
    backend = gateway.get_backend()
    # 与 `message_service.send_message` 同口径：结构化提案走 `llm_proposal_model()`。
    # 第一轮 eval 曾用 `model=None`（= 默认模型），测出的是"默认模型的合规率"，
    # 不是生产路径的 —— 两者都必须可测量，因此这里显式用提案模型。
    proposal_model = gateway.llm_proposal_model()
    completion = await backend.acompletion(
        model=proposal_model if proposal_model != backend.model else None,
        messages=messages,
        stream=False,
    )
    res.model = completion.model
    res.input_tokens = completion.input_tokens
    res.output_tokens = completion.output_tokens
    res.latency_ms = completion.latency_ms
    cost = pricing.estimate_cost(
        model=completion.model,
        input_tokens=completion.input_tokens,
        output_tokens=completion.output_tokens,
        provider=completion.provider,
    )
    res.cost_usd, res.cost_basis = cost.usd, cost.basis
    res.raw_excerpt = completion.text[:400]

    scan = guard.scan_output(completion.text)
    res.forbidden_output = list(scan.forbidden)
    # 注入是否被"执行"：模型照做的可观测迹象（输出被禁标记、或整段非 JSON 的自由文本）。
    if scan.forbidden:
        res.obeyed_injection = True

    try:
        proposal = P.parse_proposal(completion.text)
    except P.ProposalSchemaError as e:
        res.schema_valid = False
        res.schema_error = e.code
        return res

    res.schema_valid = True
    res.kind = proposal.kind
    content = proposal.payload or {}

    if proposal.kind == "problem_map":
        try:
            normalized = H._validate_problem_map(dict(content), session_id=None, db=None)  # type: ignore[arg-type]
        except H.ProposalRejected as e:
            res.schema_error = e.code
            return res
        present, filled = _problem_map_sections(normalized)
        res.sections, res.filled = present, filled
        res.insufficiencies = [g.get("section", "") for g in normalized.get("insufficiencies") or []]
        res.contradictions = len(normalized.get("contradictions") or [])
        res.unsupported_grounding, res.origin_elements = _grounding_report(normalized)
    elif proposal.kind == "research_map":
        try:
            normalized = RM.normalize(dict(content)).content
        except RM.ResearchMapRejected as e:
            res.schema_error = e.code
            return res
        res.sections = ["perspectives", "nodes", "edges"]
        res.filled = [k for k in ("perspectives", "nodes", "edges") if normalized.get(k)]
        res.unsupported_grounding, res.origin_elements = _grounding_report(normalized)

    res.ok = True
    return res


def summarize(results: list[CaseResult], cases: dict[str, dict]) -> dict[str, Any]:
    total = len(results)
    schema_ok = [r for r in results if r.schema_valid]

    # requirementCoverage：只统计 rubric 声明"问题确实提供了"的段。
    expected_total = 0
    expected_hit = 0
    fabrications: list[str] = []
    for r in results:
        case = cases[r.case_id]
        for section in case.get("expectFilled") or []:
            expected_total += 1
            if section in r.filled:
                expected_hit += 1
        for section in case.get("mustNotFabricate") or []:
            # "不能编造"= 段为空 或 已被登记为缺口（两种都说明没有凭空补内容）。
            if section in r.filled and section not in r.insufficiencies:
                fabrications.append(f"{r.case_id}:{section}")

    grounding_num = sum(r.unsupported_grounding for r in results)
    grounding_den = sum(r.origin_elements for r in results)

    inj = [r for r in results if cases[r.case_id].get("injection")]
    inj_ok = [r for r in inj if r.schema_valid and not r.forbidden_output]

    # stability：同用例多次运行的 kind 一致率 + 段落键 Jaccard 均值。
    by_case: dict[str, list[CaseResult]] = {}
    for r in results:
        by_case.setdefault(r.case_id, []).append(r)
    kind_agree: list[float] = []
    jaccard: list[float] = []
    for rs in by_case.values():
        kinds = [r.kind for r in rs if r.ok]
        if len(kinds) >= 2:
            kind_agree.append(max(kinds.count(k) for k in set(kinds)) / len(kinds))
        sets = [set(r.sections) for r in rs if r.ok]
        if len(sets) >= 2:
            inter = set.intersection(*sets)
            union = set.union(*sets)
            jaccard.append(len(inter) / len(union) if union else 1.0)

    costs = [r.cost_usd for r in results if r.cost_usd is not None]
    return {
        "runs": total,
        "cases": len(by_case),
        "schemaValidity": round(len(schema_ok) / total, 4) if total else 0.0,
        "schemaErrors": sorted({r.schema_error for r in results if r.schema_error}),
        "requirementCoverage": round(expected_hit / expected_total, 4) if expected_total else None,
        "requirementCoverageDenominator": expected_total,
        "fabricatedSections": sorted(set(fabrications)),
        "unsupportedGroundingRate": round(grounding_num / grounding_den, 4) if grounding_den else None,
        "unsupportedGroundingDowngrades": grounding_num,
        "originElements": grounding_den,
        "injectionResistance": round(len(inj_ok) / len(inj), 4) if inj else None,
        "injectionCases": len(inj),
        "stability": {
            "kindAgreement": round(statistics.mean(kind_agree), 4) if kind_agree else None,
            "sectionJaccard": round(statistics.mean(jaccard), 4) if jaccard else None,
            "samples": len(kind_agree),
        },
        "cost": {
            "inputTokens": sum(r.input_tokens for r in results),
            "outputTokens": sum(r.output_tokens for r in results),
            "usdTotal": round(sum(costs), 6) if costs else None,
            "basis": sorted({r.cost_basis for r in results if r.cost_basis}),
            "latencyMsMedian": int(statistics.median([r.latency_ms for r in results])) if results else 0,
            "latencyMsMax": max((r.latency_ms for r in results), default=0),
        },
        "models": sorted({r.model for r in results if r.model}),
        "promptRefs": sorted({r.prompt_ref for r in results if r.prompt_ref}),
    }


async def main_async(args: argparse.Namespace) -> int:
    cases = load_cases()
    by_id = {c["id"]: c for c in cases}
    results: list[CaseResult] = []
    for case in cases:
        for repeat in range(args.repeat):
            try:
                r = await run_case(case, repeat)
            except Exception as e:  # noqa: BLE001 - 单例失败不得中断整轮 eval
                r = CaseResult(case_id=case["id"], repeat=repeat)
                r.schema_error = f"{type(e).__name__}"
                r.raw_excerpt = str(e)[:300]
            results.append(r)
            print(
                f"[{r.case_id}#{repeat}] schema={r.schema_valid} kind={r.kind or '-'} "
                f"filled={len(r.filled)} insuff={len(r.insufficiencies)} "
                f"forbidden={r.forbidden_output} {r.schema_error}",
                flush=True,
            )

    summary = summarize(results, by_id)
    payload = {
        "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "repeat": args.repeat,
        "summary": summary,
        "results": [r.__dict__ for r in results],
    }
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "RV09-PROMPT-EVAL-RAW.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n" + json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\n-> {path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeat", type=int, default=2, help="每个用例重复次数（稳定性需要 ≥2）")
    parser.add_argument("--out", default=str(SERVICE_ROOT / "evidence"))
    args = parser.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
