"""Teacher Prep V2 Adapter（R13）：8100 → 8000 的唯一内部通道。

原则（DECISIONS §2/§4、IMPORT-ALLOWLIST）：
- 8000 是 Assignment / StudentSubmission / ClassMembership 的**唯一事实权威**；
  8100 不复制学生任务/提交事实表，只经本 Adapter 通过 HTTP 触达；
- 本包禁止 import 冻结域（app.export / app.domains.* / app.pagination.* /
  app.ppt_v2 / app.textbook_v2）与 sqlalchemy（零 ORM），边界测试静态守卫；
- MappingRegistry 为「契约 id ↔ 8000 id」稳定映射的持久化接口；R13 提供
  内存实现（测试/dev），生产 projection 表（teacher_prep_v2_task_refs，
  带 source_version/valid_to）随业务轮落库，见 ADAPTER-MAPPING.md §3。
"""
