"""F13 figure 事件契约测试（kernel/figure_block.validate_figure_block）。

覆盖：合法规范化（多余键剥除）、缺帧/空帧/超帧数、data_uri 前缀/超长、
step_no 非法、caption 超长、非 dict 载荷 —— 全部降级 None 不抛异常；
以及 gateway 幂等重放的 figure 事件还原（坏数据跳过不炸流）。
"""

import json
import uuid

from app.kernel.figure_block import DATA_URI_PREFIX, validate_figure_block

FRAME_OK = {"data_uri": DATA_URI_PREFIX + "aGVsbG8=", "label": "坐标系与曲线"}

VALID = {
    "step_no": 2,
    "caption": "作出函数图像",
    "frames": [FRAME_OK, {"data_uri": DATA_URI_PREFIX + "d29ybGQ=", "label": "标注关键点"}],
    "figure_params": {"type": "function"},
}


class TestValidateFigureBlock:
    def test_valid_normalized(self):
        out = validate_figure_block(VALID)
        assert out is not None
        assert out["step_no"] == 2
        assert out["caption"] == "作出函数图像"
        assert len(out["frames"]) == 2
        assert out["frames"][0] == FRAME_OK
        assert out["figure_params"] == {"type": "function"}

    def test_optional_keys_defaulted(self):
        out = validate_figure_block({"frames": [FRAME_OK]})
        assert out == {"frames": [FRAME_OK]}

    def test_extra_keys_dropped(self):
        out = validate_figure_block({**VALID, "foo": "bar", "frames": [FRAME_OK]})
        assert "foo" not in out

    def test_missing_frames_dropped(self):
        assert validate_figure_block({"step_no": 1}) is None

    def test_empty_frames_dropped(self):
        assert validate_figure_block({"frames": []}) is None

    def test_too_many_frames_dropped(self):
        payload = {"frames": [FRAME_OK] * 7}
        assert validate_figure_block(payload) is None

    def test_bad_data_uri_prefix_dropped(self):
        payload = {"frames": [{"data_uri": "https://evil.example/x.svg"}]}
        assert validate_figure_block(payload) is None

    def test_oversize_data_uri_dropped(self):
        big = DATA_URI_PREFIX + "A" * 210_000
        assert validate_figure_block({"frames": [{"data_uri": big}]}) is None

    def test_step_no_invalid_dropped(self):
        assert validate_figure_block({"frames": [FRAME_OK], "step_no": 0}) is None
        assert validate_figure_block({"frames": [FRAME_OK], "step_no": "2"}) is None

    def test_caption_too_long_dropped(self):
        payload = {"frames": [FRAME_OK], "caption": "长" * 100}
        assert validate_figure_block(payload) is None

    def test_label_defaults_empty(self):
        out = validate_figure_block(
            {"frames": [{"data_uri": DATA_URI_PREFIX + "aGVsbG8="}]}
        )
        assert out["frames"][0]["label"] == ""

    def test_payload_not_dict_dropped(self):
        assert validate_figure_block("figure") is None
        assert validate_figure_block([FRAME_OK]) is None
        assert validate_figure_block(None) is None


# ========== gateway 幂等重放（纯单测 _replay_response，不碰 DB） ==========


def _parse_sse_events(text: str) -> dict[str, list[dict]]:
    events: dict[str, list[dict]] = {}
    current = None
    for line in text.split("\n"):
        if line.startswith("event: "):
            current = line[7:]
            events.setdefault(current, [])
        elif line.startswith("data: ") and current:
            events[current].append(json.loads(line[6:]))
    return events


async def _collect_replay(envelope: dict) -> dict[str, list[dict]]:
    from app.gateway.agent_router import _replay_response
    from app.models.message import Message

    msg = Message(
        conversation_id=uuid.uuid4(),
        client_msg_id="ai_replay",
        role="assistant",
        content="正文",
        envelope=envelope,
        skill_id="socratic_solver",
    )
    resp = _replay_response(msg, "req-f13")
    text = ""
    async for chunk in resp.body_iterator:
        text += chunk if isinstance(chunk, str) else chunk.decode()
    return _parse_sse_events(text)


class TestReplayFigureBlock:
    """幂等重放：figure block 还原为 figure 事件；落库坏数据跳过不炸流"""

    ENVELOPE = {
        "msg_id": "m1",
        "role": "assistant",
        "blocks": [
            {"type": "markdown", "content": "看图"},
            {"type": "figure", "step_no": 1, "caption": "作图", "frames": [FRAME_OK]},
        ],
        "meta": {"skill": "socratic_solver", "confidence": 0.9, "provider": "mock"},
    }

    async def test_replay_figure_event(self):
        events = await _collect_replay(self.ENVELOPE)
        assert "figure" in events, f"重放缺 figure 事件: {list(events)}"
        assert events["figure"][0] == {
            "step_no": 1,
            "caption": "作图",
            "frames": [FRAME_OK],
        }

    async def test_replay_invalid_figure_skipped(self):
        envelope = {
            **self.ENVELOPE,
            "blocks": [
                {"type": "markdown", "content": "看图"},
                {"type": "figure", "frames": []},  # 坏数据：空帧
                {"type": "figure", "frames": [{"data_uri": "https://evil/x.svg"}]},
            ],
        }
        events = await _collect_replay(envelope)
        assert "figure" not in events
        # 重放流其余部分不受影响
        assert events["token"][0] == {"text": "看图"}
        assert "done" in events
