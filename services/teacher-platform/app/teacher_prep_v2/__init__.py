"""Teacher Prep V2（跨端契约 + Adapter 冻结，R13）。

本命名空间是 `teacher-prep-v2` 前端功能的后端家（PORT-NAMESPACE-REGISTRY 预留，
表前缀 `teacher_prep_v2`）。R13 只做两件事：
1. 冻结 6 个唯一公开跨端契约（Pydantic 单一权威源 → JSON Schema → TypeScript）；
2. 8100 → 8000 的内部 Adapter（映射 8000 Assignment/StudentSubmission 权威，
   不复制学生任务/提交事实表）。

禁止 import 冻结域（app.export / app.domains.* / app.pagination.* / app.ppt_v2 /
app.textbook_v2），由 tests/contract/test_teacher_prep_v2_boundary.py 静态守卫。
"""
