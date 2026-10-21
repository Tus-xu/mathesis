"""R16 gold case 元数据（仅元数据，零教材文本；真实内容由 evidence 脚本从授权切片读取）。

gold cases = 3 个不同真实章节（3 个不同授权批次/册），验收口径：
- 每条事实 source_ref 可 resolve（evidence: verify_gold.py 33/33）；
- 同输入 snapshot hash 可复现；
- 低覆盖/注入/坏 chunk/无图 安全降级。
"""

from __future__ import annotations

GOLD_CASES = [
    {
        "batch": "20260726-A-bixi1-01",
        "book": "人教A版2019 必修第一册",
        "section_title": "1.1 集合的概念",
        "knowledge_points": ["集合", "元素", "列举法", "描述法"],
        "expected": {"overall_ge": 0.5, "fact_count_ge": 1, "warning_codes": ["no_objectives_evidence", "no_key_difficult_evidence"]},
    },
    {
        "batch": "20260729-A-bixi2-01",
        "book": "人教A版2019 必修第二册",
        "section_title": "6.1.1 向量的实际背景与概念",
        "knowledge_points": ["向量", "有向线段", "零向量", "单位向量"],
        "expected": {"overall_ge": 0.5, "fact_count_ge": 1, "warning_codes": ["low_coverage", "knowledge_point_unmatched"]},
    },
    {
        "batch": "20260729-A-xbixi1-01",
        "book": "人教A版2019 选择性必修第一册",
        "section_title": "1.1.1 空间向量及其线性运算",
        "knowledge_points": ["空间向量", "线性运算", "共线向量"],
        "expected": {"overall_ge": 0.5, "fact_count_ge": 1, "warning_codes": ["no_objectives_evidence", "no_key_difficult_evidence"]},
    },
]
