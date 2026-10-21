"""R20 版本化 ClassLearningProfile（8100 事实权威，补 R19 缺口）。

范围（DECISIONS.md §6 主线「StudentSubmission/Evidence/ClassLearningProfile」）：
- 版本化持久化：tp_class_learning_profiles 事实表 + 不可变修订快照；
- 时效性：computed_at / expires_at / stale 判定（旧 Profile 显式 warning）；
- 确定性聚合：StudentSubmission/Evidence → ProfileAggregation（纯函数，可复算）；
- 证据质量：低覆盖 / 证据冲突 warning 随版本不可变保留。

不施工：8000 侧学生端聚合管线、前端 ClassLearningProfileCard（留 R21+）。
"""
