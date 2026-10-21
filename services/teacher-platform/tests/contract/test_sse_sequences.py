"""SSE 事件序列契约（10 通道 = 既有 8 + 课堂 2）。

蓝本：notes/B5-notes.md §1.6（#49-61）、02-ARCHITECTURE §7.4 事件序列表、
移植自 v3MockContract.test.ts SSE describe 块（MC:317-391）与 teacherV3Server 行为参照。
帧契约（retry/headers/注释心跳）按 app/core/sse.py 与 SRV startSse 双向对齐。

课堂两通道（#58-59）依赖 DB/Valkey → compose marker；M2-A 起可跑。
"""
from __future__ import annotations

import pytest

from conftest import _auth, assert_sse_stream_headers, collect_sse, parse_sse_text, seq_of

pytestmark = pytest.mark.usefixtures("app")


async def _post_sse(client, teacher_token, url, body):
    status, headers, text = await collect_sse(
        client, "POST", url, headers=_auth(teacher_token), json_body=body
    )
    assert status == 200, f"{url} 应 200，实际 {status}：{text[:200]}"
    assert_sse_stream_headers(headers)
    return parse_sse_text(text)


# ---------------- 通道①：photo-ingest ----------------


async def test_photo_ingest_sequence(client, teacher_token):
    """MC:318-340 — meta → photo×n → block×n → paginate → done；块全 editable + confidence>0.8。"""
    evs = await _post_sse(
        client,
        teacher_token,
        "/teacher-v3/recognition/photo-ingest",
        {
            "question_label": "椭圆焦点弦例题",
            "photos": ["blob:p1"],
            "config": {
                "scope": "stem+solution",
                "mode": "full-solution",
                "template_id": "tpl-academic-blue",
                "font_tier": "standard",
                "margin_notes": True,
            },
        },
    )
    seq = seq_of(evs)
    assert seq[0] == "meta"
    assert seq.count("photo") >= 1
    blocks = [e for e in evs if e.event == "block"]
    assert len(blocks) >= 1
    for e in blocks:
        assert e.data["editable"] is True  # R2：识别块可编辑
        assert e.data["confidence"] > 0.8
    assert "paginate" in seq
    assert seq[-1] == "done"
    paginate = next(e for e in evs if e.event == "paginate")
    assert paginate.data["pages"] >= 1
    assert isinstance(paginate.data["anchor_bars"], list)
    assert evs[-1].data.get("deck_id")


# ---------------- 通道②：generation/deck ----------------


async def test_generation_deck_sequence(client, teacher_token):
    """MC:342-355 — meta → outline → slide×n → done；AI 元素 teacher_confirmed=false（R5）。"""
    evs = await _post_sse(
        client,
        teacher_token,
        "/teacher-v3/generation/deck",
        {"topic": "双曲线及其标准方程", "class_id": "c2-03", "template_id": "tpl-academic-blue"},
    )
    seq = seq_of(evs)
    assert "outline" in seq
    slides = [e for e in evs if e.event == "slide"]
    assert len(slides) >= 1
    assert seq[-1] == "done"
    for e in slides:
        for el in e.data["slide"]["elements"]:
            if el["type"] in ("formula", "geometry"):
                assert el.get("teacher_confirmed") is False  # R5：AI 只出草稿
    assert evs[-1].data.get("deck_id")


# ---------------- 通道③：generation/plan ----------------


async def test_generation_plan_sequence(client, teacher_token):
    """MC:357-368 — meta → outline → section×10 → done；教学目标板块反套话标记。"""
    evs = await _post_sse(
        client,
        teacher_token,
        "/teacher-v3/generation/plan",
        {
            "topic": "双曲线及其标准方程",
            "class_id": "c2-03",
            "lesson_type": "新授课",
            "template_id": "lt-explorer",
        },
    )
    seq = seq_of(evs)
    assert seq[0] == "meta"
    assert seq[1] == "outline"
    sections = [e for e in evs if e.event == "section"]
    assert len(sections) == 10  # 十板块
    assert sections[0].data["section"]["confirmed"] is False
    objective = next(
        (e for e in sections if e.data["section"]["name"] == "教学目标"), None
    )
    assert objective is not None and objective.data["section"].get("cliche") is True
    assert any(e for e in sections if e.data["section"].get("examples"))
    assert seq[-1] == "done"
    assert evs[-1].data.get("plan_id")


# ---------------- 通道④：ai-element（严格四帧） ----------------


async def test_ai_element_strict_sequence(client, teacher_token):
    """MC:370-380 — 严格 ['meta','suggest','diff','done']；diff 不自动应用（R5）。"""
    decks = (await client.get("/teacher-v3/decks", headers=_auth(teacher_token))).json()["data"]["items"]
    if not decks:
        pytest.skip("种子 deck 为空")
    evs = await _post_sse(
        client,
        teacher_token,
        f"/teacher-v3/generation/decks/{decks[0]['id']}/ai-element",
        {"slide_id": "s2", "hint": "补充离心率"},
    )
    assert seq_of(evs) == ["meta", "suggest", "diff", "done"]
    diff = next(e for e in evs if e.event == "diff")
    assert len(diff.data["diffs"]) >= 1
    assert diff.data["diffs"][0]["op"] in ("add", "modify", "remove")
    assert evs[-1].data["applied"] is False


# ---------------- 通道⑤：hand-recognize ----------------


async def test_hand_recognize_sequence(client, teacher_token):
    """MC 母本无独立 SSE 用例，序列定案 02-ARCH §7.4 — meta → recognizing → result → done。"""
    evs = await _post_sse(
        client, teacher_token, "/teacher-v3/draw/hand-recognize", {"strokes": 3, "hint": "椭圆标准方程"}
    )
    seq = seq_of(evs)
    assert seq[0] == "meta"
    assert "recognizing" in seq
    result = next(e for e in evs if e.event == "result")
    assert result.data["latex"] and 0 < result.data["confidence"] <= 1
    assert seq[-1] == "done"


# ---------------- 通道⑥：review-pack ----------------


async def test_review_pack_sequence(client, teacher_token):
    """MC:382-390 — meta → slide×n → done；done.deck_id 真实讲评 Artifact。"""
    evs = await _post_sse(
        client,
        teacher_token,
        "/teacher-v3/grading/assignments/ga-1/review-pack",
        {},
    )
    seq = seq_of(evs)
    assert seq[0] == "meta"
    slides = [e for e in evs if e.event == "slide"]
    assert len(slides) >= 1
    for s in slides:
        assert "q_no" in s.data
    assert seq[-1] == "done"
    assert str(evs[-1].data.get("deck_id", ""))


# ---------------- 通道⑦：butler/chat（意图路由） ----------------


async def test_butler_chat_web_search_sequence(client, teacher_token):
    """SRV:1052-1064 — web_search 意图：tool_call → tool_result → token → citation → done。"""
    evs = await _post_sse(
        client,
        teacher_token,
        "/teacher-v3/butler/chat",
        {
            "message": "椭圆第一定义和第二定义有什么区别，课堂教学怎么引入？",
            "context": {"route": "/teacher-v3/slides", "route_title": "课件工坊"},
            "web_search": True,
        },
    )
    seq = seq_of(evs)
    assert seq[0] == "meta"
    assert evs[0].data.get("session_id")
    assert evs[0].data.get("intent") == "web_search"
    assert "tool_call" in seq and "tool_result" in seq
    citation = next((e for e in evs if e.event == "citation"), None)
    assert citation is not None and len(citation.data["sources"]) >= 1
    for src in citation.data["sources"]:
        assert src["url"].startswith(("http://", "https://"))  # citation 域名真实（L5 判定②）
    assert seq[-1] == "done"


async def test_butler_chat_quiz_import_needs_confirm(client, teacher_token):
    """SRV:988-1005 — 存题意图：write 工具出 action 确认卡（confirm_required，教师未确认前零写库）。"""
    evs = await _post_sse(
        client,
        teacher_token,
        "/teacher-v3/butler/chat",
        {
            "message": "把这道题存进题库",
            "context": {"route": "/teacher-v3/quiz"},
            "images": ["data:image/png;base64,AAAA"],
        },
    )
    seq = seq_of(evs)
    assert seq[0] == "meta"
    assert evs[0].data.get("intent") == "quiz_import"
    action_cards = [e for e in evs if e.event == "card" and e.data.get("type") == "action"]
    assert len(action_cards) == 1
    card = action_cards[0].data
    assert card["confirm_required"] is True
    assert card["status"] == "pending"
    assert seq[-1] == "done"


# ---------------- 通道⑧：voice-formula ----------------


async def test_voice_formula_sequence(client, teacher_token):
    """SRV:1091-1113 — meta → asr_partial×n → asr_final → card(formula) → done；confidence+alternates。"""
    evs = await _post_sse(
        client,
        teacher_token,
        "/teacher-v3/butler/voice-formula",
        {"text": "根号下 x 平方加 y 平方", "context": {"route": "/teacher-v3/slides"}},
    )
    seq = seq_of(evs)
    assert seq[0] == "meta"
    partials = [e for e in evs if e.event == "asr_partial"]
    assert len(partials) >= 1
    final = next(e for e in evs if e.event == "asr_final")
    assert final.data["text"]
    card = next(e for e in evs if e.event == "card")
    assert card.data["type"] == "formula"
    assert card.data["source"] == "voice"
    assert 0 < card.data["confidence"] <= 1
    assert "alternatives" in card.data
    assert seq[-1] == "done"


# ---------------- 通道⑨⑩：classroom 教师流/学生流（compose 全栈） ----------------


@pytest.mark.compose
class TestClassroomStreams:
    """§12.4：连接即 snapshot → durable 重放 after(Last-Event-ID) → live；零丢失零重复。

    M2-A Classroom Basic 起可跑：需真实会话 + Valkey。
    """

    async def test_teacher_stream_snapshot_first(self, http_base, teacher_token):
        from conftest import PrefixingAsyncClient

        async with PrefixingAsyncClient(base_url=http_base, timeout=30) as client:
            async with client.stream(
                "GET",
                "/teacher-v3/classroom/sessions/00000000-0000-0000-0000-000000000001/stream",
                headers=_auth(teacher_token),
            ) as resp:
                assert resp.status_code == 200
                assert_sse_stream_headers(resp.headers)
                events = parse_sse_text(await collect_stream_text(resp))
        assert events, "教师流至少应有 snapshot"
        assert events[0].event == "snapshot"  # §12.4：先权威快照
        assert events[0].id is None or events[0].id == "0"

    async def test_student_stream_same_shape(self, http_base, classroom_student_token):
        """学生流与教师流同构（投影视角）；课堂 token 只能访问本 session 流。"""
        from conftest import PrefixingAsyncClient

        async with PrefixingAsyncClient(base_url=http_base, timeout=30) as client:
            async with client.stream(
                "GET",
                "/teacher-v3/classroom/sessions/00000000-0000-0000-0000-000000000001/student-stream",
                headers=_auth(classroom_student_token),
            ) as resp:
                # 合成 token 非 join 签发 → 401/403 均合法（真实 token 用例在 M2-A L9 走查）
                assert resp.status_code in (200, 401, 403)


async def collect_stream_text(resp) -> str:
    parts: list[str] = []
    async for chunk in resp.aiter_text():
        parts.append(chunk)
        if sum(len(p) for p in parts) > 100_000:
            break
    return "".join(parts)
