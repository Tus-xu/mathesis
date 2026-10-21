"""FunAsrClient：FunASR sidecar 的流式 WS 客户端（B1-owned，§3.7 部署见 IFC-002）。

协议依据 repos/FunASR runtime/docs/websocket_protocol.md（notes/B1-notes.md §3.2）：
- init 一帧 JSON：{"mode":"2pass","chunk_size":[5,10,5],"chunk_interval":10,
  "wav_format":"pcm","wav_name":...,"is_speaking":true}
- 之后二进制帧 = PCM 16k/16bit/mono（约 60ms/帧）；结束发 {"is_speaking":false,"is_end":true}
- 服务器消息：mode=2pass-online → AsrPartial（无时间戳/无置信度——已证伪，见 §3.6）；
  mode=2pass-offline 且 is_final=true → AsrFinal；is_end=true 为结束确认。
- final 可能由 VAD 中途多次触发：以 is_speaking=false 之后那条 final 为「最后一条」，
  调用方以客户端结束（流耗尽）为准。
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

logger = logging.getLogger(__name__)

DEFAULT_CHUNK_SIZE = [5, 10, 5]  # 600ms 窗、300ms 前后看
DEFAULT_CHUNK_INTERVAL = 10  # ×60ms/帧


@dataclass
class AsrPartial:
    text: str


@dataclass
class AsrFinal:
    text: str  # 含标点（2pass 离线纠错 + 服务端标点模型）


class FunAsrClient:
    """每句话一条 WS 会话。audio 为 PCM 16k/16bit/mono 字节块异步流。"""

    def __init__(
        self,
        url: str,
        *,
        chunk_size: list[int] | None = None,
        chunk_interval: int = DEFAULT_CHUNK_INTERVAL,
        connect_timeout_s: float = 5.0,
        result_timeout_s: float = 15.0,
        ws_factory=None,  # 测试注入：async connect(url) -> (send_json, send_bytes, recv)
    ) -> None:
        self._url = url
        self._chunk_size = chunk_size or DEFAULT_CHUNK_SIZE
        self._chunk_interval = chunk_interval
        self._connect_timeout_s = connect_timeout_s
        self._result_timeout_s = result_timeout_s
        self._ws_factory = ws_factory

    async def transcribe_stream(self, audio: AsyncIterator[bytes], *, wav_name: str | None = None) -> AsyncIterator[AsrPartial | AsrFinal]:
        """转写一条语音：partial* → final(+VAD 中途 final) → 结束。"""
        name = wav_name or f"b1-{uuid.uuid4().hex[:12]}"
        send_json, send_bytes, recv = await self._connect()
        try:
            await send_json(
                {
                    "mode": "2pass",
                    "wav_name": name,
                    "wav_format": "pcm",
                    "is_speaking": True,
                    "chunk_size": self._chunk_size,
                    "chunk_interval": self._chunk_interval,
                    "itn": True,
                }
            )

            async def _pump() -> None:
                async for frame in audio:
                    await send_bytes(frame)
                await send_json({"is_speaking": False, "is_end": True})

            pump_task = asyncio.create_task(_pump())
            try:
                while True:
                    message = await asyncio.wait_for(recv(), timeout=self._result_timeout_s)
                    event = _parse_message(message)
                    if event is None:
                        continue
                    if isinstance(event, _EndAck):
                        break
                    yield event
            finally:
                pump_task.cancel()
                await asyncio.gather(pump_task, return_exceptions=True)
        finally:
            await self._close()

    async def _connect(self):
        if self._ws_factory is not None:
            return await self._ws_factory(self._url)
        import websockets  # 延迟导入：单测无需 sidecar

        conn = await asyncio.wait_for(websockets.connect(self._url), timeout=self._connect_timeout_s)

        async def send_json(payload: dict) -> None:
            await conn.send(json.dumps(payload, ensure_ascii=False))

        async def send_bytes(data: bytes) -> None:
            await conn.send(data)

        async def recv():
            return await conn.recv()

        async def close() -> None:
            await conn.close()

        self._close = close  # type: ignore[method-assign]
        return send_json, send_bytes, recv

    async def _close(self) -> None:  # ws_factory 注入时由工厂路径覆盖
        return None


class _EndAck:
    pass


def _parse_message(message) -> AsrPartial | AsrFinal | _EndAck | None:
    if isinstance(message, bytes):
        return None
    try:
        payload = json.loads(message)
    except (TypeError, ValueError):
        return None
    mode = payload.get("mode")
    if payload.get("is_end"):
        return _EndAck()
    text = payload.get("text")
    if text is None:
        return None
    if mode == "2pass-offline":
        if payload.get("is_final"):
            return AsrFinal(text=text)
        return None
    # 2pass-online partial（替换式：调用方直接覆盖当前 partial）
    return AsrPartial(text=text)
