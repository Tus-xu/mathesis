"""Butler Kernel v2 ResultComposer（设计规格 §5 + 阶段 3C）

- 确定性工具数据优先；第一版不调用额外 LLM 润色（保证整次 Runtime 模型请求 ≤3）；
- 永远有规则型 fallback（degraded 也产出 envelope）；
- 不输出思维链；生成 ButlerEnvelope，由 Compatibility Facade 转既有信封。
"""

from __future__ import annotations

import uuid

from app.butler.contracts import (
    ActionPlan,
    ButlerEnvelope,
    ButlerRequest,
    ToolResult,
)
from app.butler.learning_viz_tools import card_for_tool_result


class ResultComposer:
    """把计划与工具结果组装为最终 ButlerEnvelope。"""

    def compose(
        self,
        *,
        run_id: uuid.UUID,
        request: ButlerRequest,
        plan: ActionPlan,
        results: list[ToolResult],
        degraded: bool = False,
    ) -> ButlerEnvelope:
        # 文本：计划目标 + 每步状态（不含思维链/内部细节）。
        # stu-r2：可视化工具（fsrs_retention / mastery_bkt）产出结构化数据卡——
        # 文本摘要行只用卡片的 message（数字全部来自 ToolResult.data，模板不造新数），
        # 学生看到的是数据本身而不是「工具名：已完成」。
        lines = [plan.goal or plan.intent]
        card_blocks: list[dict] = []
        for action, result in zip(plan.actions, results, strict=False):
            card = card_for_tool_result(action.tool_name, result, degraded=degraded)
            if card is not None:
                card_blocks.append({"type": "card", "data": card})
                if card.get("message"):
                    lines.append(f"- {card['message']}")
                continue
            if result.ok:
                lines.append(f"- {action.tool_name}：已完成")
            elif result.error_code == "shadow_skipped":
                lines.append(f"- {action.tool_name}：影子跳过")
            elif result.error_code:
                lines.append(f"- {action.tool_name}：{_friendly_message(result.error_code)}")
            else:
                lines.append(f"- {action.tool_name}：失败")
        text = "\n".join(lines)

        blocks = [
            {
                "tool_name": action.tool_name,
                "ok": result.ok,
                "error_code": result.error_code,
            }
            for action, result in zip(plan.actions, results, strict=False)
        ]
        # 结构化卡块（additive）：floating-butler 信封透传、历史回放同构
        blocks.extend(card_blocks)
        actions = [
            {"tool_name": action.tool_name, "status": "ok" if result.ok else "failed"}
            for action, result in zip(plan.actions, results, strict=False)
        ]
        trace = {
            "intent": plan.intent,
            "response_mode": plan.response_mode,
            "degraded": degraded,
            "error_codes": [r.error_code for r in results if r.error_code],
        }

        return ButlerEnvelope(
            run_id=run_id,
            intent=plan.intent,
            text=text,
            blocks=blocks,
            actions=actions,
            sources=[],
            degraded=degraded,
            trace=trace,
        )


_FRIENDLY: dict[str, str] = {
    "unknown_tool": "工具不可用",
    "role_denied": "无权使用该能力",
    "scene_denied": "当前场景不可用",
    "invalid_arguments": "参数不合法",
    "budget_exceeded": "请求超出预算",
    "idempotency_required": "写操作需要幂等确认",
    "external_not_allowed": "外部能力未授权",
    "confirmation_required": "需要开启联网搜索",
    "m2_out_of_scope": "该能力不在范围内",
    "tool_timeout": "响应超时",
    "tool_error": "服务暂时不可用",
    "invalid_output": "返回内容异常",
    "shadow_skipped": "影子跳过",
    "duplicate_request": "重复请求已忽略",
}


def _friendly_message(error_code: str) -> str:
    return _FRIENDLY.get(error_code, "处理失败")
