"""课时时长确定性归一化（数据一致性红线：Σ环节时长 == 课时预算）。

纯函数、零外部依赖（不 import service/conversation，规避其循环导入）。
被 app.teacher_prep.service.project_specs 在投影前调用：deck 与教案共享同一
归一化后的蓝图 body，保证两产物「环节时长合计」与顶栏「课时分钟数」恒一致。
"""

from __future__ import annotations

from typing import Any


def largest_remainder(weights: list[float], total: int) -> list[int]:
    """把 total 按 weights 比例确定性分成 len(weights) 份整数（每份 ≥1，Σ==total）。

    标准 largest-remainder：先取 floor，再把余数按小数部分从大到小依次 +1；
    同小数部分按索引升序（确定性，不依赖 dict 迭代顺序）。
    前置：0 < len(weights) <= total（否则无法保证每份 ≥1，调用方负责守卫）。
    """
    n = len(weights)
    if n == 0:
        return []
    wsum = sum(weights)
    if wsum <= 0:
        weights = [1.0] * n
        wsum = float(n)
    exact = [total * w / wsum for w in weights]
    base = [max(1, int(v)) for v in exact]
    # 若 floor 后已超 total（因 max(1,·) 抬高），从最大项回退
    over = sum(base) - total
    while over > 0:
        j = max(range(n), key=lambda i: (base[i], -i) if base[i] > 1 else (-1, i))
        if base[j] <= 1:
            break
        base[j] -= 1
        over -= 1
    # 不足则按小数部分降序补 1
    deficit = total - sum(base)
    order = sorted(range(n), key=lambda i: (-(exact[i] - int(exact[i])), i))
    k = 0
    while deficit > 0:
        base[order[k % n]] += 1
        deficit -= 1
        k += 1
    return base


def normalize_flow_durations(body: dict[str, Any], budget: int) -> dict[str, Any]:
    """投影前把 lesson_flow 各环节时长确定性归一化到课时预算。

    LLM 推导的 duration_minutes 合计常 ≠ 预算（如 6 环节合计 60 ≠ 45 分钟课时）；
    deck 与教案都从同一 body 投影，若各自渲染「Σ时长」与顶栏「课时分钟数」就会互相矛盾。
    这里按现有比例做 largest-remainder 整数分配，保证 Σ == budget、每环节 ≥1 分钟、
    相对比例不变；环节数 > 预算（无法每环节 ≥1）时原样返回（诚实，不硬塞）。
    不就地修改入参（返回新 dict），避免污染已确认蓝图。
    """
    flow = body.get("lesson_flow")
    if not isinstance(flow, list) or not flow:
        return body
    stages = [s for s in flow if isinstance(s, dict)]
    n = len(stages)
    if n == 0 or n > budget:
        return body

    def _w(s: dict[str, Any]) -> float:
        d = s.get("duration_minutes")
        return float(d) if isinstance(d, (int, float)) and d > 0 else 0.0

    alloc = largest_remainder([_w(s) for s in stages], budget)
    out = dict(body)
    new_flow: list[Any] = []
    matched = 0
    for item in flow:
        if isinstance(item, dict):
            copied = dict(item)
            copied["duration_minutes"] = alloc[matched]
            new_flow.append(copied)
            matched += 1
        else:
            new_flow.append(item)
    out["lesson_flow"] = new_flow
    return out


__all__ = ["largest_remainder", "normalize_flow_durations"]
