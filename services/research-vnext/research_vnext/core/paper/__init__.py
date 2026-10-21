"""RV13 · Paper 域：PaperSpec canonical model 与结构化编辑安全门。

模块分工
--------
- `nodes`     typed node schema（封闭类型集 / 版本化 nodeRev / server-owned 字段）
- `formula`   LaTeX → AST → canonical（公式语义往返，不用图片替代）
- `sanitize`  文本/富文本/URL 清洗（HTML 注入与 scheme 越权）
- `patch`     AI typed patch ops（insert/update/delete/move）与安全门
- `diff`      节点级 diff（expected_revision 冲突展示）
- `service`   修订链服务：人工编辑 / undo / redo / 绑定健康
"""

from __future__ import annotations

from .errors import PaperSchemaError

__all__ = ["PaperSchemaError"]
