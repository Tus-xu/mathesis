"""路由聚合（B3 §3.9）：各域提供 `router: APIRouter`，此处 import 聚合。

域目录不存在时用条件导入保证骨架可跑（B1/B4 建好后自动挂载）。
所有业务端点统一挂 `/api/teacher-v3`（契约 BASE='/api' + teacher-v3/*）。
"""
from __future__ import annotations

from fastapi import FastAPI

# (模块路径, 属性名) —— 各域 router 的约定位置；缺失即跳过（骨架可跑）
_DOMAIN_ROUTERS: list[tuple[str, str]] = [
    ("app.domains.catalog.router", "router"),
    ("app.domains.contents.router", "router"),
    ("app.domains.recognition.router", "router"),
    ("app.domains.figures.router", "router"),
    ("app.domains.draw.router", "router"),
    ("app.domains.grading.router", "router"),
    ("app.domains.classroom.router", "router"),
    ("app.domains.generation.router", "router"),
    ("app.domains.butler.router", "router"),
    # R14：Teacher Prep 会话/蓝图骨架（8100 事实权威）
    ("app.teacher_prep.router", "router"),
    # R17：Conversation-first 备课编排（timeline/proposal/actions/SSE）
    ("app.teacher_prep.conversation.router", "router"),
    # T24：artifact 编辑/QA/导出/下载 + 教案块级编辑
    ("app.ppt_v2.api.router", "router"),
    ("app.teacher_prep.specs.router", "router"),
    # 体验测试留痕（作品验收：试用教师反馈 + 使用档案 + 导出）
    ("app.teacher_prep.trial_router", "router"),
    # R4a：教材切片插图只读服务（资料页/教材抽屉图真实渲染，G21）
    ("app.textbook_v2.image_routes", "router"),
    # T2：教材目录只读端点（备课中心章节数据源，PLAN §4.2/§11-B3）
    ("app.textbook_v2.catalog_router", "router"),
]


def register_routers(app: FastAPI) -> None:
    from . import platform

    app.include_router(platform.router, prefix="/api/teacher-v3")

    for module_path, attr in _DOMAIN_ROUTERS:
        try:
            mod = __import__(module_path, fromlist=[attr])
            router = getattr(mod, attr)
            app.include_router(router, prefix="/api/teacher-v3")
        except (ImportError, AttributeError):
            continue  # 域尚未实现：骨架保持可跑
