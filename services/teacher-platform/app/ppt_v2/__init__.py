"""PPT Studio V2 领域层（R10）。

V2 唯一事实来源 = app.ppt_v2.domain.spec.TeachingDeckSpec。
本包禁止 import 任何 legacy generation/export/pagination/schema 模块
（tests/contract/test_ppt_v2_boundary.py 静态守卫）。
"""

from app.ppt_v2.domain.spec import SCHEMA_VERSION, TeachingDeckSpec

__all__ = ["SCHEMA_VERSION", "TeachingDeckSpec"]
