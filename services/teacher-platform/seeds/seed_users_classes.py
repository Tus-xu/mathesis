"""seed_users_classes.py —— 登录/班级基础 seed 的**字段请求**（B2 §5.4）。

边界：物理 seed 实现归 B3（B3 拥有 app/db/models 的 users/classes 与平台 seed 目录）。
本文件只声明 B2 域需要的最小样例与字段口径，供 B3 在平台 seed 中一并落实。
**零编造成绩**：学情数据只能由批改域真实链路产生，或明确标注「演示班级」并登记 manifest。
"""

from __future__ import annotations

from dataclasses import dataclass, field

# 请求 B3 在平台 seed 提供的最小教师账号（JWT dev 口径见架构 §10）
TEACHER_SEED_REQUEST = {
    "username": "zhanglaoshi",
    "password": "teacher123",  # 仅 dev seed；生产走独立账号体系
    "name": "张老师",
    "role": "teacher",
    "school_id": "待 B3 定 school seed 后回填",
}

# 请求 B3 提供的演示班级（用于课堂/批改链路演示，标注演示口径）
@dataclass
class DemoClassRequest:
    name: str
    grade: str
    student_count: int
    demo_label: str = "演示班级（见 corpus/sources/manifest.toml）"
    note: str = ""


DEMO_CLASSES_REQUEST: list[DemoClassRequest] = [
    DemoClassRequest(name="高二(3)班", grade="高二", student_count=40,
                     note="课堂互动/批改演示用；无编造成绩，作答数据由真实链路产生"),
]

# B2 侧依赖字段（由 B3 的 users/classes 模型保证）：
REQUIRED_FIELDS_FROM_B3 = [
    "users.id uuid", "users.name text", "users.role CHECK IN ('teacher','admin')",
    "users.school_id uuid", "classes.id uuid", "classes.name text", "classes.grade text",
    "classes.student_count int", "classes.school_id uuid",
]

_ = field  # 保持 dataclass 引用
