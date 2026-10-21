"""R20 双 Spec 投影（LessonPlanSpec + TeachingDeckSpec）。

范围（DECISIONS.md §6：已确认 Blueprint → LessonPlanSpec + TeachingDeckSpec）：
- 同一 approved blueprint revision + 不可变 context snapshot 投影；
- 两个 Spec 共享事实引用（blueprint_ref/context_ref）但各自拥有
  schema_version / revision / purpose / 编辑历史；互不反推、不改写 Blueprint 事实；
- 投影确定性（同输入 → 同 body hash）、幂等（projection_hash 去重）、
  schema 校验（Pydantic）、prompt/model/run lineage 绑定；
- 教材引用一律 SourceLocator，新写入零 page_refs/pNN。

不施工：最终教案/PPT 文件、编辑器、8200 job 下发（留 R21+）。
"""
