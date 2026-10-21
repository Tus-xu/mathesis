"""RV08 · 消息 payload → 前端可渲染的 **allowlisted** typed component 投影。

铁律：
- 组件类型是**后端白名单**，不是模型自由文本；
- 模型只能通过 proposal `kind` 影响 **已登记** 的组件类型；
- 未知 kind ⇒ 不投影 component（前端显示纯文本），**绝不**透传原始 payload；
- 投影只包含**数据**，不含任何标记/脚本（前端也不接受 HTML/CSS/JS）。

对应前端 `TYPED-COMPONENT-CATALOG.md` 的首批最小 allowlist。
"""

from __future__ import annotations

from typing import Any

#: proposal kind → 前端 component type（PascalCase，与平台 component catalog 同名）
PROPOSAL_COMPONENT_TYPES: dict[str, str] = {
    "intake": "ProblemMapProposal",
    #: RV09 正式形状：八段结构的问题地图提案卡（living map，可逐项 accept/reject/edit）。
    "problem_map": "ProblemMapProposal",
    #: RV09 正式形状：2–4 视角的研究地图卡（含依赖 / 交付物 / 停止条件 / 不确定性）。
    "research_map": "ResearchMapCard",
    #: 历史别名：仍渲染成地图卡，但 props 里带 `deprecated=true`，前端提示改用多视角。
    "map": "ResearchMapCard",
    #: RV10：路线竞技场提案卡（2–3 条路线 + 服务端 rubric，无总分）。
    "route_arena": "RouteArenaProposal",
    #: RV10：研究蓝图提案卡（绑定 + 选定路线 + 计划/门/预算）。
    "research_blueprint": "BlueprintCard",
    "blueprint": "BlueprintCard",
    "experiment_plan": "ExperimentPlanCard",
    "claim": "ClaimCheckCard",
    "paper_patch": "PaperPatchCard",
    "review": "ReviewFindingCard",
}

#: 失败/降级提示的组件类型
NOTICE_COMPONENT_TYPE = "CapabilityNotice"
#: RV09：信息不足/自相矛盾时的**补充请求**卡（可补充清单，不是道歉）。
CLARIFICATION_COMPONENT_TYPE = "ClarificationRequest"

#: `answer` 等不需要确认的 kind 不产生卡片（保持开放文本排版，避免 Cardocalypse）
NO_CARD_KINDS = frozenset({"answer"})

#: attachment kind 白名单（决定前端用哪种渲染器）
ATTACHMENT_KINDS = ("image", "pdf", "text", "dataset", "other")


def project_component(payload: Any) -> dict[str, Any] | None:
    """把消息 payload 投影为 `{type, props}`；无法识别时返回 `None`。"""
    if not isinstance(payload, dict):
        return None

    notice = payload.get("notice")
    if isinstance(notice, dict):
        return {
            "type": NOTICE_COMPONENT_TYPE,
            "props": {
                "errorCode": str(notice.get("error_code") or ""),
                "model": str(notice.get("model") or ""),
                # RV10：服务端拒绝详情（哪条路线、因什么被拒）。服务端原文，非模型内容。
                "detail": str(notice.get("detail") or ""),
            },
        }

    clarification = payload.get("clarification")
    if isinstance(clarification, dict):
        gaps_in = clarification.get("gaps")
        gaps: list[dict] = []
        if isinstance(gaps_in, list):
            for gap in gaps_in:
                if not isinstance(gap, dict):
                    continue
                # 只透传白名单字段：`detail` 里可能有服务端错误原文，
                # 它归审计（`research_ai_run.error_code`），不进面向用户的卡片。
                gaps.append(
                    {
                        "section": str(gap.get("section") or ""),
                        "kind": str(gap.get("kind") or ""),
                        "a": str(gap.get("a") or ""),
                        "b": str(gap.get("b") or ""),
                        "reason": str(gap.get("reason") or ""),
                    }
                )
        return {
            "type": CLARIFICATION_COMPONENT_TYPE,
            "props": {
                "code": str(clarification.get("code") or ""),
                "requiresUserInput": bool(clarification.get("requiresUserInput")),
                "gaps": gaps,
            },
        }

    proposal = payload.get("proposal")
    if not isinstance(proposal, dict):
        return None

    kind = str(proposal.get("kind") or "")
    if kind in NO_CARD_KINDS:
        return None

    component_type = PROPOSAL_COMPONENT_TYPES.get(kind)
    if not component_type:
        # 未登记 kind：不猜测 UI，交由前端按纯文本展示
        return None

    content = proposal.get("content")
    return {
        "type": component_type,
        "props": {
            "kind": kind,
            "objectRef": str(proposal.get("object_ref") or ""),
            "requiresConfirmation": bool(proposal.get("requires_confirmation")),
            "content": content if isinstance(content, dict) else {},
        },
    }


def project_attachments(payload: Any) -> list[dict[str, Any]]:
    """投影消息附件引用（只保留白名单字段；字节内容永远另走 Asset API）。"""
    if not isinstance(payload, dict):
        return []
    raw = payload.get("attachments")
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        asset_id = str(item.get("asset_id") or item.get("assetId") or "")
        if not asset_id:
            continue
        kind = str(item.get("kind") or "other")
        out.append(
            {
                "assetId": asset_id,
                "kind": kind if kind in ATTACHMENT_KINDS else "other",
                "filename": str(item.get("filename") or ""),
                "mediaType": str(item.get("media_type") or ""),
                "sizeBytes": int(item.get("size_bytes") or 0),
            }
        )
    return out
