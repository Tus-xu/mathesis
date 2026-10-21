"""本地 BGE-M3 Embedding 服务（OpenAI 兼容 /v1/embeddings，1024 维）

用途：为 `services/api` 的 chunks（vector(1024)）提供与历史数据一致的向量。

为什么需要它：
- 库表 `chunks.embedding` 是 `vector(1024)`，现有 792 条教材切片（2026-08-28 导入）
  全部是 **1024** 维；
- 而 `.env` 的 `EMBEDDING_BASE_URL` 指向讯飞 MaaS，其 `xop3qwen8bembedding`
  返回 **768** 维，且与 base_url 拼接后 404（`/v2/v1/embeddings`）。
  维度错配会让新入库失败、检索失效——这正是「知识库像摆设」的机械原因之一。

本服务只做一件事：加载本地 bge-m3，暴露 OpenAI 兼容端点，保证 **1024 维**。

运行（需用带 torch 的解释器，本机为 C:\\python13）：
    C:/python13/python.exe D:/math-arena/scripts/bge_m3_server.py --port 8080

自检：
    curl -X POST http://127.0.0.1:8080/v1/embeddings \
         -H "Content-Type: application/json" \
         -d '{"model":"bge-m3","input":["测试"]}'
    # 返回 data[0].embedding 长度应为 1024
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

DEFAULT_MODEL = os.environ.get("BGE_M3_PATH", r"D:\math-arena\.models\bge-m3")

_model = None
_model_lock = threading.Lock()


def get_model(path: str):
    """懒加载句向量模型（首次请求时加载，避免启动即占用数 GB 内存）。"""
    global _model
    if _model is None:
        with _model_lock:
            if _model is None:
                from sentence_transformers import SentenceTransformer

                print(f"[bge-m3] loading model from {path} ...", flush=True)
                _model = SentenceTransformer(path)
                dim = _model.get_sentence_embedding_dimension()
                print(f"[bge-m3] ready, dim={dim}", flush=True)
                if dim != 1024:
                    print(
                        f"[bge-m3][WARN] 维度 {dim} 与库表 vector(1024) 不一致，"
                        "入库/检索会因维度错配失败",
                        file=sys.stderr,
                        flush=True,
                    )
    return _model


class EmbeddingsHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if urlparse(self.path).path.rstrip("/") in ("", "/health"):
            self._send(200, {"status": "ok", "model": DEFAULT_MODEL})
            return
        self._send(404, {"error": "not found"})

    def do_POST(self) -> None:
        path = urlparse(self.path).path.rstrip("/")
        if not path.endswith("/embeddings"):
            self._send(404, {"error": f"not found: {path}"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
        except Exception as e:  # noqa: BLE001
            self._send(400, {"error": f"bad request: {type(e).__name__}"})
            return

        raw = body.get("input")
        if isinstance(raw, str):
            texts = [raw]
        elif isinstance(raw, list):
            texts = [t if isinstance(t, str) else str(t) for t in raw]
        else:
            texts = []
        if not texts:
            self._send(400, {"error": "input must be a non-empty string or list"})
            return

        try:
            model = get_model(DEFAULT_MODEL)
            vectors = model.encode(
                texts, batch_size=16, normalize_embeddings=True, show_progress_bar=False
            ).tolist()
        except Exception as e:  # noqa: BLE001
            self._send(500, {"error": f"encode failed: {type(e).__name__}: {str(e)[:200]}"})
            return

        self._send(
            200,
            {
                "object": "list",
                "model": body.get("model", "bge-m3"),
                "data": [
                    {"object": "embedding", "index": i, "embedding": v}
                    for i, v in enumerate(vectors)
                ],
                "usage": {"prompt_tokens": 0, "total_tokens": 0},
            },
        )

    def log_message(self, fmt: str, *args) -> None:
        pass


def main() -> None:
    # global 声明必须出现在该函数内对该名字的任何使用之前
    global DEFAULT_MODEL

    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    args = ap.parse_args()

    DEFAULT_MODEL = args.model

    print(f"[bge-m3] serving on http://{args.host}:{args.port}/v1/embeddings", flush=True)
    ThreadingHTTPServer((args.host, args.port), EmbeddingsHandler).serve_forever()


if __name__ == "__main__":
    main()
