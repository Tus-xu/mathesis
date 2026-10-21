"""知识库 collections 定案（B2 规范 §3）与九种页型词汇表（ppt_slots / IFC-001 共用）。

检索策略（架构冻结）：pgvector 余弦 top20 → BGE-reranker-v2-m3 精排 top6；
CRAG 置信评估，低置信 query 重写一次，仍低 web 兜底（used_web=true）。
"""

from __future__ import annotations

from dataclasses import dataclass

COLLECTION_KP_GRAPH = "kp_graph"
COLLECTION_CURRICULUM = "curriculum"
COLLECTION_QUIZ_BANK = "quiz_bank"
COLLECTION_LESSON_BENCH = "lesson_bench"
COLLECTION_PPT_SLOTS = "ppt_slots"
COLLECTION_WEB_CACHE = "web_cache"


@dataclass(frozen=True)
class CollectionSpec:
    name: str
    description: str
    chunking: str
    scenarios: tuple[str, ...]


COLLECTIONS: dict[str, CollectionSpec] = {
    spec.name: spec
    for spec in (
        CollectionSpec(
            COLLECTION_KP_GRAPH,
            "知识点定义/前置关系/典型方法（K12-KGraph+课标）",
            "每概念一 chunk（定义+prereq+所属章节）",
            ("备课缺口提醒", "题目 kp-suggest", "管家数学问答"),
        ),
        CollectionSpec(
            COLLECTION_CURRICULUM,
            "课标素养条目、内容要求、学业要求（2017 版 2020 修订）",
            "按条目",
            ("教案目标双线生成", "大纲环节目标草案"),
        ),
        CollectionSpec(
            COLLECTION_QUIZ_BANK,
            "题干+解析（GAOKAO 真题+校本入库题）",
            "一题一 chunk（题干/答案/解析合并）",
            ("例题建议", "变式训练", "AI 配题"),
        ),
        CollectionSpec(
            COLLECTION_LESSON_BENCH,
            "优秀教案逐板块段落（国家平台精品课基准）",
            "按板块×环节",
            ("教案生成 few-shot 检索（同课题优先）",),
        ),
        CollectionSpec(
            COLLECTION_PPT_SLOTS,
            "优秀课件版式骨架（页型/槽位/图文比，非像素）",
            "一页型一 chunk",
            ("deck 模板槽位分配", "逐页生成版式参照"),
        ),
        CollectionSpec(
            COLLECTION_WEB_CACHE,
            "联网搜索结果缓存（博查）",
            "一次搜索一 chunk",
            ("管家联网问答（免重复搜索）",),
        ),
    )
}

ALL_COLLECTIONS = tuple(COLLECTIONS)

# 九种页型词汇表（IFC-001 冻结，deck 逐页生成 page_type 唯一取值域）
NINE_PAGE_TYPES: tuple[str, ...] = (
    "concept_intro",       # 概念引入
    "visual_definition",   # 直观定义
    "derivation",          # 推导
    "formula_focus",       # 公式聚焦
    "worked_example",      # 例题讲解
    "compare",             # 对比辨析
    "common_error",        # 易错辨析
    "practice",            # 课堂练习
    "summary",             # 小结
)


def validate_collection(name: str) -> str:
    if name not in COLLECTIONS:
        raise ValueError(f"未知 collection: {name}（合法域: {', '.join(ALL_COLLECTIONS)}）")
    return name
