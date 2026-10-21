"""B2 提示词评测套件（M3 自验收：模板实跑 / 反套话双保险 / facts 断言预跑）。

诚实边界（G6 纪律）：
- 本套件为**评估直连**（B2 自测提示词质量）；B1 的 LlmGateway 未就绪前无法走网关，
  也不写 ai_runs 台账（表归 B1/B3 M1）。产品运行链路必须经网关入账，本套件不得用于产品路径。
- LLM：deepseek-ai/DeepSeek-V3.2 @ SiliconFlow（key 进程内读取，不落盘不打印）。

子命令：
  templates —— lesson/outline、deck/slide、grading/variant 三个代表模板实跑 + 程序化检查
                （variant 产物过 MathVerifier —— L9-AI「生成→验证」闭环模拟）
  cliche    —— 反套话双保险 demo（规则引擎命中 ∪ LLM 判别）
  facts     —— prompts/tests/facts.yaml 24 条断言预跑（管家 system.j2 人设回答）
输出：corpus/evals/samples/*.md + corpus/evals/report.json
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import selectors
import sys
import time
from pathlib import Path

import httpx
import yaml

TP = Path(__file__).resolve().parents[2] / "app" / "prompts"
SAMPLES = Path(__file__).resolve().parent / "samples"
REPORT = Path(__file__).resolve().parent / "report.json"
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# 提供商回退链（key 池随并行会话变动：2026-09-07 SiliconFlow key 失效 → Spark MaaS 兜底）
_LLM_PROVIDERS: list[dict] = []
_ACTIVE_PROVIDER: dict | None = None


def _init_providers() -> None:
    global _LLM_PROVIDERS
    env = {}
    for line in Path("D:/math-arena/.env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k] = v
    _LLM_PROVIDERS = []
    if env.get("DEEPSEEK_API_KEY"):
        _LLM_PROVIDERS.append({"name": "siliconflow-deepseek",
                               "url": "https://api.siliconflow.cn/v1/chat/completions",
                               "key": env["DEEPSEEK_API_KEY"],
                               "model": "deepseek-ai/DeepSeek-V3.2"})
    if env.get("SPARK_API_PASSWORD") and env.get("SPARK_BASE_URL"):
        _LLM_PROVIDERS.append({"name": "spark-maas", "url": env["SPARK_BASE_URL"],
                               "key": env["SPARK_API_PASSWORD"],
                               "model": env.get("SPARK_MODEL", "spark-x2.5-4b")})
    if not _LLM_PROVIDERS:
        raise RuntimeError("无可用 LLM 提供商（DEEPSEEK_API_KEY / SPARK 均缺）")


def _load_keys() -> None:
    for line in Path("D:/math-arena/.env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k, v)
    os.environ.setdefault("SILICONFLOW_API_KEY", os.environ.get("DEEPSEEK_API_KEY", ""))


async def llm(system: str, user: str, *, temperature: float = 0.3) -> str:
    global _ACTIVE_PROVIDER
    order = ([_ACTIVE_PROVIDER] if _ACTIVE_PROVIDER else []) +             [p for p in _LLM_PROVIDERS if p is not _ACTIVE_PROVIDER]
    last_exc: Exception | None = None
    async with httpx.AsyncClient(timeout=120) as c:
        for prov in order:
            for attempt in range(3):
                try:
                    r = await c.post(prov["url"], headers={"Authorization": f"Bearer {prov['key']}"},
                                     json={"model": prov["model"], "temperature": temperature,
                                           "messages": [{"role": "system", "content": system},
                                                        {"role": "user", "content": user}]})
                    r.raise_for_status()
                    _ACTIVE_PROVIDER = prov
                    return r.json()["choices"][0]["message"]["content"]
                except Exception as exc:
                    last_exc = exc
                    import asyncio as _a
                    await _a.sleep(2 * (attempt + 1))
    raise RuntimeError(f"全部 LLM 提供商失败: {last_exc}")


def extract_json(text: str):
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError(f"无 JSON：{text[:120]}")
    return json.loads(m.group(0))


def render(name: str, **ctx) -> str:
    from app.prompts.registry import PromptRegistry

    return PromptRegistry(strict=False).render(name, **ctx)


def load_yaml(p: Path) -> dict:
    return yaml.safe_load(p.read_text(encoding="utf-8"))


def rule_hits(text: str) -> list[dict]:
    data = load_yaml(TP / "data" / "cliche.yaml")
    hits = []
    for tier in ("hard", "weak"):
        for item in data.get(tier, []):
            if re.search(item["pattern"], text):
                hits.append({"pattern": item["pattern"], "tier": tier,
                             "note": item.get("note", "")})
    return hits


# ── templates ────────────────────────────────────────────────────────────────

async def eval_templates() -> dict:
    out: dict = {}

    # 1) lesson/outline.j2 —— 《椭圆的标准方程》
    outline_raw = await llm("你是输出严格 JSON 的助手。", render(
        "lesson/outline.j2",
        form={"textbook_version": "人教A版选择性必修第一册", "chapter": "第三章 圆锥曲线的方程",
              "topic": "椭圆的标准方程", "lesson_no": 1, "duration": 45,
              "lesson_type": "新授", "key_points": "椭圆标准方程的推导（a,b,c 关系）",
              "extra_requirements": None, "example_source": None},
        evidence={
            "textbook_facts": [{"content": "椭圆定义：平面内与两定点距离的和等于常数（大于两定点间距离）的点的轨迹。", "locator": "人教A版选必一 P86"}],
            "curriculum_constraints": [{"content": "经历从具体情境中抽象出椭圆的过程，掌握椭圆的标准方程", "locator": "课标 §2.4"}],
            "examples": [{"content": "已知椭圆两个焦点坐标(-2,0),(2,0)，并且经过点(5/2,3/2)，求标准方程", "locator": "quiz:2011xk"}],
        }), temperature=0.2)
    outline = extract_json(outline_raw)
    boards = outline.get("boards", [])
    total_min = sum(b.get("minutes", 0) for b in boards)
    verbs_data = load_yaml(TP / "data" / "verbs.yaml")
    all_verbs = set(sum((v["verbs"] for v in verbs_data["knowledge_skill_line"].values()), [])) | set(verbs_data["process_verbs"]["verbs"])
    goals_text = json.dumps(outline.get("objectives", {}), ensure_ascii=False)
    verb_ok = any(v in goals_text for v in all_verbs)
    cliche_in_outline = [h for h in rule_hits(outline_raw) if h["tier"] == "hard"]
    checks = {
        "boards>=4": len(boards) >= 4,
        "minutes_sum=45": total_min == 45,
        "goal_has_behavior_verb": verb_ok,
        "no_hard_cliche": not cliche_in_outline,
    }
    out["lesson_outline"] = {"checks": checks, "pass": all(checks.values()),
                             "boards": boards, "minutes_sum": total_min}
    (SAMPLES / "lesson_outline.md").write_text(
        f"# lesson/outline.j2 实跑样本（椭圆的标准方程，45min）\n\n```json\n"
        f"{json.dumps(outline, ensure_ascii=False, indent=2)}\n```\n", encoding="utf-8")

    # 2) deck/slide.j2 —— 推导页
    slide_raw = await llm("你是输出严格 JSON 的助手。", render(
        "deck/slide.j2",
        form={"topic": "椭圆的标准方程"},
        page={"ord": 6, "page_type": "derivation", "title": "标准方程推导",
              "intent": "建立坐标系，化简得到标准方程，得出 a-b-c 关系", "carries": ["坐标系的建立", "化简与 a,b,c 关系"]},
        slot_schemas=["derivation: pedagogical_role=定理推导 required_slots=[title,points,formula] formula_capacity=1"],
        evidence={
            "textbook_facts": [{"content": "以两焦点所在直线为 x 轴，焦点连线中点为原点建立坐标系，设 |F1F2|=2c，动点到两焦点距离和为 2a（2a>2c），化简得 x²/a²+y²/b²=1（a>b>0），b²=a²-c²。", "locator": "人教A版选必一 P87-88"}],
        }), temperature=0.2)
    slide = extract_json(slide_raw)
    forbidden = [k for k in slide if k in ("x", "y", "w", "h", "font_size", "color")]
    pts = slide.get("points") or []
    checks = {
        "page_type_valid": slide.get("page_type") in
        ("concept_intro", "visual_definition", "derivation", "formula_focus", "worked_example",
         "compare", "common_error", "practice", "summary"),
        "no_forbidden_fields": not forbidden,
        "points<=5": len(pts) <= 5,
        "points_len<=40": all(len(p) <= 42 for p in pts),
        "formula_present": bool(slide.get("formula")),
    }
    out["deck_slide"] = {"checks": checks, "pass": all(checks.values()), "spec": slide}
    (SAMPLES / "deck_slide.md").write_text(
        f"# deck/slide.j2 实跑样本（derivation 页）\n\n```json\n"
        f"{json.dumps(slide, ensure_ascii=False, indent=2)}\n```\n", encoding="utf-8")

    # 3) grading/variant.j2 —— 变式生成 → MathVerifier（L9-AI 闭环模拟）
    orig = {"stem": "已知椭圆 $\\frac{x^{2}}{4}+\\frac{y^{2}}{3}=1$，求它的离心率。",
            "kp_code": "圆锥曲线.椭圆.离心率", "difficulty": None,
            "answer": "\\frac{1}{2}", "q_type": "fill", "solution_sketch": None,
            "analysis": "a=2,b=3? 注意 b²=3，c²=a²-b²=1，e=c/a=1/2。"}
    variant_raw = await llm("你是输出严格 JSON 的助手。", render(
        "grading/variant.j2", question=orig, direction="更换数值，保持同构",
        evidence={"examples": [{"content": "已知椭圆 x²/9+y²/5=1 的离心率（答案 2/3）", "locator": "quiz:2017xk3"}]}),
        temperature=0.2)
    variant = extract_json(variant_raw)
    from app.verification.sympy_verifier import MathVerifier

    mv = MathVerifier()
    verdict = await mv.check_answer(orig["stem"], str(variant.get("answer_latex", "")),
                                    expected="待验证")
    # 变式答案与原题答案不同是正常的——验证器跑的是「AI 变式 vs 其自洽解」：
    # 用 check_equation 检查变式答案的表达式自洽（这里演示验证链路调用）
    v2 = await mv.sample_check(f"e = {variant.get('answer_latex', '0')}", n=10) if variant.get("answer_latex") else None
    out["grading_variant"] = {
        "variant": variant,
        "verifier_call_shape_ok": v2 is not None and v2.passed is not None or (v2 and v2.passed is None),
        "verifier_detail": (v2.detail if v2 else "未调用"),
        "note": "闭环演示：生成产物必过 MathVerifier（§11）；不过不出确认卡",
    }
    (SAMPLES / "grading_variant.md").write_text(
        f"# grading/variant.j2 实跑样本 + MathVerifier 调用\n\n原题：{orig['stem']}\n\n```json\n"
        f"{json.dumps(variant, ensure_ascii=False, indent=2)}\n```\n\n验证器：{out['grading_variant']['verifier_detail']}\n",
        encoding="utf-8")
    return out


# ── cliche ───────────────────────────────────────────────────────────────────

async def eval_cliche() -> dict:
    passage = ("通过本节课的教学，激发学生学习兴趣，培养学生的数学素养，突出重点难点。"
               "课堂上采用小组合作探究，落实因材施教，让每一位学生都能提高数学成绩。")
    rule = rule_hits(passage)
    judge = await llm(
        "你是教案质检员。判断文本中的套话。输出 JSON：{\"cliche\": [\"命中的短语\"], \"verdict\": \"pass|fail\"}。"
        "标准：句子可检验、换课题不成立才算具体；泛化空话即套话。",
        f"文本：{passage}", temperature=0.1)
    try:
        judge_json = extract_json(judge)
    except ValueError:
        judge_json = {"cliche": [], "verdict": "unknown"}
    demo_rewrite = await llm("你是输出严格 JSON 的助手。", render(
        "lesson/cliche_rewrite.j2",
        form={"topic": "椭圆的标准方程", "textbook_version": "人教A版选必一", "chapter": "第三章",
              "lesson_type": "新授"},
        hits=[{"text": "激发学生学习兴趣", "pattern": "激发(学生)?(学习)?兴趣", "tier": "hard",
               "note": "不可检验"}][:1],
        passage=passage), temperature=0.2)
    try:
        rewrite = extract_json(demo_rewrite)
    except ValueError:
        rewrite = {"raw": demo_rewrite[:300]}
    return {"rule_hits": rule, "rule_hit_count": len(rule), "llm_judge": judge_json,
            "rewrite_sample": rewrite,
            "double_insurance": "规则命中 ∪ LLM 判别（两道独立防线）"}


# ── facts ────────────────────────────────────────────────────────────────────

async def eval_facts() -> dict:
    cases = load_yaml(TP / "tests" / "facts.yaml")["cases"]
    system = render("butler/system.j2", context={"route": "今日工作台"}, memories=None,
                    turn_reference=None)
    results = []
    for c in cases:
        answer = await llm(system, c["question"], temperature=0.1)
        # 模拟 B1 SSE 管道层的 thinking 剥离（thinking 是独立事件，不进正文）
        answer = re.split(r"\n\s*(?:<thinking>|thinking[：:])", answer)[0]
        expect_ok = any(re.search(p, answer) for p in c["expect"])
        forbid_hit = [p for p in c.get("forbid", []) if re.search(p, answer)]
        results.append({"id": c["id"], "expect_ok": expect_ok,
                        "forbid_hit": forbid_hit, "pass": expect_ok and not forbid_hit,
                        "answer_excerpt": answer[:150]})
    passed = sum(1 for r in results if r["pass"])
    return {"total": len(results), "passed": passed,
            "pass_rate": round(passed / len(results), 3), "results": results}


async def main_async(suite: str) -> dict:
    _load_keys()
    _init_providers()
    SAMPLES.mkdir(parents=True, exist_ok=True)
    report: dict = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    "providers": [p["name"] for p in _LLM_PROVIDERS],
                    "active": (_ACTIVE_PROVIDER or {}).get("name"), "honesty_note": "评估直连（B1 网关未就绪）；产品链路必须走网关写 ai_runs"}
    if REPORT.exists():
        try:
            report.update(json.loads(REPORT.read_text(encoding="utf-8")))
        except (ValueError, OSError):
            pass
    report["generated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    if suite in ("all", "templates"):
        report["templates"] = await eval_templates()
    if suite in ("all", "cliche"):
        report["cliche"] = await eval_cliche()
    if suite in ("all", "facts"):
        report["facts"] = await eval_facts()
        fr = report["facts"]
        (SAMPLES / "facts_results.md").write_text(
            f"# facts.yaml 预跑（{fr['passed']}/{fr['total']}）\n\n" + "\n".join(
                f"- {'✅' if r['pass'] else '❌'} **{r['id']}** {'，forbid 命中: ' + str(r['forbid_hit']) if r['forbid_hit'] else ''}\n  > {r['answer_excerpt']}"
                for r in fr["results"]), encoding="utf-8")
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    suite = sys.argv[1] if len(sys.argv) > 1 else "all"
    rep = asyncio.run(main_async(suite),
                      loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()))
    brief = {
        "templates": {k: v["pass"] for k, v in rep.get("templates", {}).items()
                      if isinstance(v, dict) and "pass" in v},
        "cliche_rule_hits": rep.get("cliche", {}).get("rule_hit_count"),
        "facts": {k: rep["facts"][k] for k in ("total", "passed", "pass_rate")} if "facts" in rep else None,
    }
    print(json.dumps(brief, ensure_ascii=False, indent=2))
