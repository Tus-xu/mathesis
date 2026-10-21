"""以官方 BGE-M3 权重提供项目既有的 OpenAI 兼容 embeddings 接口。"""

from __future__ import annotations

import os
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

EMBEDDING_DIMENSION = 1024
DEFAULT_MODEL_PATH = r"D:\math-arena\.models\bge-m3"
_MIN_PYTORCH_WEIGHT_BYTES = 128 * 1024 * 1024


class EmbeddingRequest(BaseModel):
    model: str = "bge-m3"
    input: str | list[str]


def _resolve_model_path(model_path: str | Path | None = None) -> Path:
    return Path(model_path or os.environ.get("LOCAL_EMBEDDING_MODEL", DEFAULT_MODEL_PATH))


def _assert_model_is_complete(model_path: Path) -> None:
    """阻止半截 Hugging Face 下载被当作可用 BGE-M3 模型。"""
    weights = model_path / "pytorch_model.bin"
    if not weights.is_file() or weights.stat().st_size < _MIN_PYTORCH_WEIGHT_BYTES:
        raise RuntimeError("BGE-M3 权重不完整或无法加载")


def _load_encoder(model_path: str | Path | None = None) -> Callable[[list[str]], list[list[float]]]:
    from sentence_transformers import SentenceTransformer

    resolved_path = _resolve_model_path(model_path)
    _assert_model_is_complete(resolved_path)
    model = SentenceTransformer(str(resolved_path), device="cpu")

    def encode(texts: list[str]) -> list[list[float]]:
        vectors = model.encode(
            texts,
            normalize_embeddings=True,
            show_progress_bar=False,
            convert_to_numpy=True,
        )
        return [vector.tolist() for vector in vectors]

    return encode


@lru_cache(maxsize=1)
def get_encoder() -> Callable[[list[str]], list[list[float]]]:
    return _load_encoder()


def create_app(
    encoder: Callable[[list[str]], list[list[float]]] | None = None,
    *,
    model_path: str | Path | None = None,
) -> FastAPI:
    """构建可测试的 OpenAI 兼容 embeddings 服务。"""
    app = FastAPI(title="Math Arena BGE-M3 Embeddings")
    state: dict[str, Callable[[list[str]], list[list[float]]] | str | None] = {
        "encoder": encoder,
        "load_error": None,
    }

    def resolve_encoder() -> Callable[[list[str]], list[list[float]]]:
        current = state["encoder"]
        if callable(current):
            return current
        if state["load_error"]:
            raise RuntimeError("BGE-M3 权重不完整或无法加载")
        try:
            loaded = _load_encoder(model_path)
        except Exception:
            state["load_error"] = "BGE-M3 权重不完整或无法加载"
            raise RuntimeError("BGE-M3 权重不完整或无法加载") from None
        state["encoder"] = loaded
        return loaded

    @app.get("/health")
    def health() -> dict:
        try:
            resolve_encoder()
        except RuntimeError:
            raise HTTPException(status_code=503, detail="BGE-M3 权重不完整或无法加载") from None
        return {"ok": True, "model": "bge-m3", "dimension": EMBEDDING_DIMENSION}

    @app.post("/v1/embeddings")
    def embeddings(request: EmbeddingRequest) -> dict:
        texts = [request.input] if isinstance(request.input, str) else request.input
        if not texts or any(not text.strip() for text in texts):
            raise HTTPException(status_code=400, detail="input 不能为空")
        try:
            encode = resolve_encoder()
        except RuntimeError:
            raise HTTPException(status_code=503, detail="BGE-M3 权重不完整或无法加载") from None
        vectors = encode(texts)
        if len(vectors) != len(texts) or any(len(vector) != EMBEDDING_DIMENSION for vector in vectors):
            raise HTTPException(status_code=500, detail="embedding 返回维度异常")
        return {
            "object": "list",
            "data": [
                {"object": "embedding", "embedding": vector, "index": index}
                for index, vector in enumerate(vectors)
            ],
            "model": request.model,
            "usage": {"prompt_tokens": 0, "total_tokens": 0},
        }

    return app


app = create_app()


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8080)
