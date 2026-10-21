"""socratic_solver 图形规划（F13）：planner 输出解析/校验/合并 + 主题门控

分离关注：solver 负责解题，planner 负责"哪一步该配什么图、图里画什么"。
- planner 输出 JSON 数组：[{"step": 2, "caption": "...", "figure": {figure_params}}]
- 解析健壮：容忍 markdown 围栏/前后杂文，平衡扫描提取首个 JSON 数组；
- 校验：step 越界/重复丢弃，figure_params 过 validate_figure_params（确定性校验），
  部分有效即采信（不因一条坏数据全盘重试），全部非法才反馈重试一次；
- 门控：should_plan_figures 题目主题正则，未命中跳过 planner（零额外 LLM 调用）。
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.services.figure_renderer import FigureParamsError, validate_figure_params

MAX_FIGURES = 3  # 整题配图上限（约束 planner，也兜底解析）

# 图形主题门控：命中才跑 planner。宁宽勿漏——这里只是省调用，
# 漏判最多是"没配图"（与现状一致），不会产生错误图形。
# v3.1 补：多面体/二面角/平行四边形/垂直/异面/法向量等立体几何措辞
# （e2e 实测"多面体 ABCE…二面角 D-AC-E"曾漏判导致不配图）。
FIGURE_TOPIC_RE = re.compile(
    r"函数|图象|图像|抛物线|二次|零点|极值|最值|交点|单调|值域|定义域|"
    r"几何|立体|多面体|棱锥|棱柱|棱台|正方体|长方体|球|外接|内切|体积|表面积|截面|"
    r"二面角|异面|法向量|平行四边形|垂直|平行|棱|"
    r"圆|椭圆|双曲线|坐标|切线|法线|三角形|直角|角度|距离|直线|曲线|"
    r"sin|cos|tan|log|sqrt|\\sqrt|f\(x\)|y=",
    re.IGNORECASE,
)

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def should_plan_figures(question: str) -> bool:
    """题目是否命中图形主题（未命中跳过 planner，零额外调用）。"""
    return bool(question and FIGURE_TOPIC_RE.search(question))


def extract_json_array(text: str) -> list[Any] | None:
    """从 LLM 输出中提取第一个 JSON 数组（平衡扫描，容忍围栏/杂文/字符串内括号）。"""
    if not text:
        return None
    cleaned = _FENCE_RE.sub(r"\1", text)
    start = cleaned.find("[")
    if start < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(cleaned)):
        ch = cleaned[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                try:
                    data = json.loads(cleaned[start : i + 1])
                except (json.JSONDecodeError, TypeError):
                    return None
                return data if isinstance(data, list) else None
    return None


def parse_figure_plan(raw: str, steps_count: int) -> tuple[list[dict], str | None]:
    """planner 输出 -> 规范化图形计划 + 错误信息（error 非 None 时供反馈重试）。

    返回 items: [{"step": 1基, "caption": str, "figure": figure_params}, ...]
    - 空输出 / 明确输出 [] / 全部条目被静默跳过（重复步）→ ([], None)：不配图不报错；
    - 结构非法（非 JSON 数组）或全部条目非法 → error 文本（planner 重试一次）；
    - 部分有效 → 采信有效条目（丢弃坏条目，不整体重试）。
    """
    if not raw or not raw.strip():
        return [], None
    data = extract_json_array(raw)
    if data is None:
        return (
            [],
            "输出必须是 JSON 数组，形如 [{\"step\":1,\"figure\":{\"type\":\"function\","
            "\"params\":{...}},\"caption\":\"...\"}]，且不要用 Markdown 代码块包裹以外的文字",
        )
    if data == []:
        return [], None

    items: list[dict] = []
    seen_steps: set[int] = set()
    first_error: str | None = None
    for i, entry in enumerate(data):
        if not isinstance(entry, dict):
            if first_error is None:
                first_error = f"第 {i + 1} 项不是对象"
            continue
        step = entry.get("step")
        if (
            not isinstance(step, int)
            or isinstance(step, bool)
            or not 1 <= step <= steps_count
        ):
            if first_error is None:
                first_error = f"第 {i + 1} 项的 step 非法（须为 1~{steps_count} 的整数）"
            continue
        if step in seen_steps:
            continue  # 同一步重复配图：保留第一条，静默跳过
        fig = entry.get("figure")
        if not isinstance(fig, dict):
            if first_error is None:
                first_error = f"第 {i + 1} 项缺少 figure 对象"
            continue
        try:
            validate_figure_params(fig)
        except FigureParamsError as e:
            if first_error is None:
                first_error = f"第 {i + 1} 项的 figure 参数非法: {e}"
            continue
        seen_steps.add(step)
        items.append(
            {
                "step": step,
                "caption": str(entry.get("caption") or "")[:80],
                "figure": fig,
            }
        )
        if len(items) >= MAX_FIGURES:
            break

    if not items and first_error:
        return [], first_error
    return items, None


def merge_figures_into_plan(steps: list[dict], figure_items: list[dict]) -> list[dict]:
    """把图形计划合并进 plan.steps（1 基 step -> 下标 step-1，原地更新后返回）。

    合并形态：steps[i]["figure"] = {"params": figure_params, "caption": str}
    （随 tutor_sessions.plan JSON 持久化，regenerate/重放自然携带，无新增表。）
    """
    for item in figure_items:
        idx = int(item["step"]) - 1
        if 0 <= idx < len(steps):
            steps[idx] = {
                **steps[idx],
                "figure": {"params": item["figure"], "caption": item["caption"]},
            }
    return steps
