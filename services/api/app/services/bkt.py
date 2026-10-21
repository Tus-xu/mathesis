"""BKT（贝叶斯知识追踪）接口预留（任务8 决策结果）

数据依据（2026-08-15 开发库实测）：
- mastery_records 每用户答题量：用户 49 人，**中位数 2 题**，p90=9 题，仅 2 人 ≥20 题；
- 决策规则：中位数 ≥20 题 → 实现四参数 BKT；<20 → **降级为接口预留，继续用简单正确率**。
- 结论：中位数 2 题 << 20 → 全量 BKT 参数估计无统计意义（每个 (用户,知识点) 单元
  样本几乎为 0~2），过早实现会产出噪声掌握度。保留标准四参数接口与经验参数，
  数据达标后仅需把 `app.gateway.student_router._update_mastery` 的 BKT-lite 更新式
  替换为 `bkt.bkt_update()` 即可上线，其余链路（mastery_records/快照/推荐）零改动。

四参数经验值（教育数据挖掘社区常用初值，数据达标后可做 EM/网格搜索拟合）：
- pL0：初始未掌握概率（先验）0.4
- pT ：学会的迁移概率（每次练习的习得率）0.15
- pG ：猜测率 0.15
- pS ：失误率 0.06
"""

from __future__ import annotations

from dataclasses import dataclass

# 启用 BKT 的最低单人答题量阈值（任务8 决策线）
BKT_MIN_ANSWERS = 20


@dataclass(frozen=True)
class BKTParams:
    """BKT 四参数（经验值，见模块 docstring）"""

    p_l0: float = 0.4  # 初始未掌握概率
    p_t: float = 0.15  # 迁移（学习）概率
    p_g: float = 0.15  # 猜测概率
    p_s: float = 0.06  # 失误概率

    def __post_init__(self) -> None:
        for name, v in (
            ("p_l0", self.p_l0),
            ("p_t", self.p_t),
            ("p_g", self.p_g),
            ("p_s", self.p_s),
        ):
            if not 0.0 <= v <= 1.0:
                raise ValueError(f"BKT 参数 {name} 必须在 [0,1]，收到 {v}")


DEFAULT_PARAMS = BKTParams()


def should_use_bkt(user_answer_count: int) -> bool:
    """是否启用全量 BKT：单人答题量达到统计门槛。

    当前数据（中位数 2 题）远低于门槛 → False，热路径维持 BKT-lite/简单正确率。
    """
    return int(user_answer_count or 0) >= BKT_MIN_ANSWERS


def bkt_update(prior_mastery: float, correct: bool, params: BKTParams | None = None) -> float:
    """标准 BKT 概率更新（接口预留，供数据达标后接入）。

    prior_mastery：当前 P(掌握) 后验（0~1）；correct：本次作答是否正确。
    返回更新后的 P(掌握)，clamp [0,1]。
    """
    p = params or DEFAULT_PARAMS
    p_l = float(prior_mastery)
    if not 0.0 <= p_l <= 1.0:
        raise ValueError(f"prior_mastery 必须在 [0,1]，收到 {p_l}")
    if correct:
        # P(掌握 | 答对) = P(L)·(1-pS) / (P(L)·(1-pS) + (1-P(L))·pG)
        num = p_l * (1 - p.p_s)
        denom = num + (1 - p_l) * p.p_g
    else:
        # P(掌握 | 答错) = P(L)·pS / (P(L)·pS + (1-P(L))·(1-pG))
        num = p_l * p.p_s
        denom = num + (1 - p_l) * (1 - p.p_g)
    if denom <= 0:
        return p_l
    # 再叠加迁移：答对后下一时刻的掌握概率 = 后验 + (1-后验)·pT
    posterior = num / denom
    if correct:
        posterior = posterior + (1 - posterior) * p.p_t
    return round(min(1.0, max(0.0, posterior)), 4)


def bkt_mastery_after_block(
    prior_mastery: float,
    correct_count: int,
    total: int,
    params: BKTParams | None = None,
) -> float:
    """连续作答块后的掌握度（逐题顺序更新，接口预留）。"""
    m = float(prior_mastery)
    if not total:
        return m
    seq = [True] * max(0, correct_count) + [False] * max(0, total - correct_count)
    for ok in seq:
        m = bkt_update(m, ok, params)
    return m
