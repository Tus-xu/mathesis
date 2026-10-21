"""FunAsrClient 单测：2pass 协议映射与生命周期（无 sidecar，Fake WS 工厂）。"""
from __future__ import annotations

import asyncio
import json
from collections import deque

from app.inference.asr.funasr_client import AsrFinal, AsrPartial, FunAsrClient

SERVER_SCRIPT = [
    json.dumps({"mode": "2pass-online", "text": "根号下"}),
    json.dumps({"mode": "2pass-online", "text": "根号下x平方加"}),
    json.dumps({"mode": "2pass-offline", "text": "根号下x平方加y平方。", "is_final": True}),
    json.dumps({"mode": "2pass", "is_final": True, "is_end": True}),
]


def _factory(recorder: dict):
    async def factory(url: str):
        assert url.endswith("8104")  # B3 sidecar 端口（settings.funasr_url 默认）
        pending = deque(SERVER_SCRIPT)
        state = {"audio_frames": 0}

        async def send_json(payload: dict) -> None:
            recorder["json"].append(payload)

        async def send_bytes(data: bytes) -> None:
            recorder["bytes"] += 1
            state["audio_frames"] += 1

        async def recv():
            # 贴近真实协议：服务器处理完音频才会回 final/end-ack；音频未到则等待
            while state["audio_frames"] < 2:
                await asyncio.sleep(0.01)
            return pending.popleft()

        return send_json, send_bytes, recv

    return factory


async def test_two_pass_mapping_and_lifecycle() -> None:
    recorder: dict = {"json": [], "bytes": 0}
    client = FunAsrClient("ws://teacher-funasr:8104", ws_factory=_factory(recorder))

    async def audio():
        yield b"\x00\x00" * 160  # 60ms PCM 帧（测试只关心帧数）
        yield b"\x00\x00" * 160

    events = [e async for e in client.transcribe_stream(audio())]
    # init 帧：2pass 模式 + chunk_size [5,10,5]
    init = recorder["json"][0]
    assert init["mode"] == "2pass"
    assert init["chunk_size"] == [5, 10, 5]
    assert init["wav_format"] == "pcm" and init["is_speaking"] is True
    # 音频帧全部转发；结束帧在音频耗尽后发送
    assert recorder["bytes"] == 2
    end = recorder["json"][-1]
    assert end == {"is_speaking": False, "is_end": True}
    # 事件映射：partial* → final → 流结束（end-ack 不产出事件）
    assert [type(e) for e in events] == [AsrPartial, AsrPartial, AsrFinal]
    assert events[0].text == "根号下"
    assert isinstance(events[-1], AsrFinal)
    assert "。" in events[-1].text  # 服务端标点已并入 final 文本


async def test_non_final_offline_message_is_ignored() -> None:
    async def factory(url: str):
        pending = deque(
            [
                json.dumps({"mode": "2pass-offline", "text": "未最终", "is_final": False}),
                json.dumps({"mode": "2pass-online", "text": "p1"}),
                json.dumps({"mode": "2pass", "is_final": True, "is_end": True}),
            ]
        )

        async def send_json(payload: dict) -> None: ...
        async def send_bytes(data: bytes) -> None: ...
        async def recv():
            return pending.popleft()

        return send_json, send_bytes, recv

    client = FunAsrClient("ws://x", ws_factory=factory)

    async def audio():
        yield b"\x00"

    events = [e async for e in client.transcribe_stream(audio())]
    # is_final=False 的 offline 消息不是 final，丢弃；只留 partial
    assert [type(e) for e in events] == [AsrPartial]
