"""Embedding Provider（providers/embedding.py）

可配置 Embedding 提供商（system_configs["embedding"] 优先 → env 兜底）：
- local：本地 BGE-M3 服务化调用（OpenAI 兼容 {base_url}/v1/embeddings，现状默认）
- aliyun：阿里云百炼 OpenAI 兼容端点（text-embedding-v4，支持 dimensions/text_type；
  入库用 document、检索用 query）
- tencent：腾讯云 LKEAP GetEmbedding（TC3-HMAC-SHA256 签名，复用 cloud_kb 的
  _tc3_signed_post；api_key 形如 "SecretId:SecretKey"）

库表 chunks.embedding 为 vector(1024)：dimension ≠ 1024 时在 resolve/health 层告警。
向后兼容：EmbeddingProvider() 无参构造 = env 本地 BGE-M3，行为与改造前一致。
"""

import time
from dataclasses import dataclass

import httpx
import structlog

from app.config import settings
from app.providers.cloud_kb import (
    TENCENT_DEFAULT_REGION,
    TENCENT_DEFAULT_VERSION,
    TENCENT_LKEAP_HOST,
    _tc3_signed_post,
)
from app.providers.http import get_http

logger = structlog.get_logger()

# 库表 chunks.embedding 维度红线（pgvector Vector(1024)）
EMBEDDING_DIMENSION = 1024
# 阿里云百炼 OpenAI 兼容默认端点
ALIYUN_DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"

# 各提供商默认模型
_DEFAULT_MODELS = {"local": "bge-m3", "aliyun": "text-embedding-v4", "tencent": "bge-m3"}


@dataclass(frozen=True)
class EmbeddingConfig:
    """Embedding 提供商配置"""

    provider: str  # "local" | "aliyun" | "tencent"
    base_url: str
    api_key: str
    model: str
    dimension: int  # 默认 1024
    source: str  # "system" | "env"


def _config_from_env() -> EmbeddingConfig:
    """env 配置（同事 config.py 未合入时 getattr 防御，默认 local BGE-M3）"""
    provider = str(getattr(settings, "embedding_provider", "local") or "local")
    if provider not in _DEFAULT_MODELS:
        logger.warning("embedding.unknown_provider_fallback", provider=provider)
        provider = "local"
    base_url = str(getattr(settings, "embedding_base_url", "http://localhost:8080") or "")
    model = str(getattr(settings, "embedding_model", "") or _DEFAULT_MODELS[provider])
    return EmbeddingConfig(
        provider=provider,
        base_url=base_url,
        api_key=str(getattr(settings, "embedding_api_key", "") or ""),
        model=model,
        dimension=EMBEDDING_DIMENSION,
        source="env",
    )


def _config_from_system(raw: object) -> EmbeddingConfig | None:
    """system_configs["embedding"] 记录 → EmbeddingConfig；无记录返回 None

    库内 JSON 结构（admin 后台写入）：
    {"provider": str, "base_url": str, "api_key": str, "model": str, "dimension": int}
    """
    if raw is None:
        return None
    import json

    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return None
    if not isinstance(raw, dict):
        return None
    provider = str(raw.get("provider") or "local")
    if provider not in _DEFAULT_MODELS:
        logger.warning("embedding.unknown_provider_fallback", provider=provider)
        provider = "local"
    dimension = raw.get("dimension")
    return EmbeddingConfig(
        provider=provider,
        base_url=str(raw.get("base_url") or ""),
        api_key=str(raw.get("api_key") or ""),
        model=str(raw.get("model") or _DEFAULT_MODELS[provider]),
        dimension=int(dimension) if dimension is not None else EMBEDDING_DIMENSION,
        source="system",
    )


def _warn_dimension(cfg: EmbeddingConfig) -> None:
    """维度与库表 vector(1024) 不一致时告警（入库/检索会因维度错配失败）"""
    if cfg.dimension != EMBEDDING_DIMENSION:
        logger.warning(
            "embedding.dimension_mismatch",
            dimension=cfg.dimension,
            expected=EMBEDDING_DIMENSION,
            provider=cfg.provider,
            source=cfg.source,
        )


async def resolve_embedding_config(db) -> EmbeddingConfig:
    """解析 embedding 配置：system_configs["embedding"] 优先 → env 兜底；永不抛异常

    app/models/system_config.py 由 admin 后台并行开发，未合入前延迟 import 失败
    属预期，按无库内配置处理直接走 env。db=None（离线脚本场景）跳过库内查询。
    """
    cfg: EmbeddingConfig | None = None
    if db is not None:
        try:
            from app.models.system_config import get_system_config

            # SAVEPOINT 隔离：查询失败（表缺失/权限等）只回滚到保存点，
            # 不污染调用方事务（kb 导入端点共用同一 session 落库）
            async with db.begin_nested():
                cfg = _config_from_system(await get_system_config(db, "embedding", None))
        except ImportError:
            cfg = None  # system_config 模型未合入（预期情况），静默走 env 兜底
        except Exception as e:
            logger.warning("embedding.config_system_error", error=str(e)[:200])
            cfg = None
    if cfg is None:
        cfg = _config_from_env()
    _warn_dimension(cfg)
    return cfg


class EmbeddingProvider:
    """可配置 Embedding 提供商（无参构造 = env 本地 BGE-M3，向后兼容）"""

    name: str = "embedding"

    def __init__(self, config: EmbeddingConfig | None = None) -> None:
        self._config = config or _config_from_env()
        self._base_url = self._config.base_url.rstrip("/")

    @property
    def available(self) -> bool:
        if self._config.provider == "local":
            return bool(self._base_url)
        if self._config.provider == "aliyun":
            return bool(self._config.api_key)
        if self._config.provider == "tencent":
            # api_key 形如 "SecretId:SecretKey"
            return ":" in self._config.api_key
        return False

    async def embed(
        self,
        texts: list[str],
        *,
        request_id: str = "",
        text_type: str = "document",
    ) -> list[list[float]]:
        """将文本列表转换为向量列表（调用方按 32 条/批组织，保持不变）

        text_type：入库传 "document"、检索传 "query"（仅阿里百炼透传，其余忽略）。
        """
        if not self.available:
            raise RuntimeError("Embedding service not configured")

        log = logger.bind(request_id=request_id, provider=f"embedding.{self._config.provider}")
        log.info("embedding.start", text_count=len(texts), text_type=text_type)
        t0 = time.monotonic()

        try:
            if self._config.provider == "aliyun":
                embeddings = await self._embed_aliyun(texts, text_type=text_type)
            elif self._config.provider == "tencent":
                embeddings = await self._embed_tencent(texts)
            else:
                embeddings = await self._embed_local(texts)
            latency = int((time.monotonic() - t0) * 1000)
            log.info(
                "embedding.ok",
                latency_ms=latency,
                vector_dim=len(embeddings[0]) if embeddings else 0,
            )
            return embeddings
        except httpx.HTTPStatusError as e:
            latency = int((time.monotonic() - t0) * 1000)
            log.error("embedding.http_error", status=e.response.status_code, latency_ms=latency)
            raise
        except Exception:
            latency = int((time.monotonic() - t0) * 1000)
            log.exception("embedding.error", latency_ms=latency)
            raise

    async def _embed_local(self, texts: list[str]) -> list[list[float]]:
        """本地 BGE-M3（OpenAI 兼容 /v1/embeddings），逻辑与改造前一致"""
        resp = await get_http().post(
            f"{self._base_url}/v1/embeddings",
            json={"model": self._config.model, "input": texts},
            # CPU 上的 BGE-M3 对教材批次首次推理可能超过 10 秒；服务端本身
            # 已复用模型，调用端只需允许完整批次返回。
            timeout=180.0,
        )
        resp.raise_for_status()
        # 兼容 OpenAI embeddings 格式
        return [item["embedding"] for item in resp.json()["data"]]

    async def _embed_aliyun(self, texts: list[str], *, text_type: str) -> list[list[float]]:
        """阿里云百炼 OpenAI 兼容端点（Bearer 鉴权，dimensions/text_type 透传）"""
        base_url = self._base_url or ALIYUN_DEFAULT_BASE_URL
        resp = await get_http().post(
            f"{base_url}/embeddings",
            headers={"Authorization": f"Bearer {self._config.api_key}"},
            json={
                "model": self._config.model,
                "input": texts,
                "dimensions": self._config.dimension,
                "text_type": text_type,
            },
            timeout=10.0,
        )
        resp.raise_for_status()
        return [item["embedding"] for item in resp.json()["data"]]

    async def _embed_tencent(self, texts: list[str]) -> list[list[float]]:
        """腾讯云 LKEAP GetEmbedding（TC3 签名；单条输入接口，逐条调用）"""
        secret_id, _, secret_key = self._config.api_key.partition(":")
        embeddings: list[list[float]] = []
        for text in texts:
            data = await _tc3_signed_post(
                secret_id=secret_id,
                secret_key=secret_key,
                service="lkeap",
                host=TENCENT_LKEAP_HOST,
                action="GetEmbedding",
                version=TENCENT_DEFAULT_VERSION,
                payload={"Model": self._config.model, "Input": text},
                region=TENCENT_DEFAULT_REGION,
                timeout=10.0,
            )
            response = data.get("Response") or {}
            error = response.get("Error")
            if error:
                raise RuntimeError(
                    f"腾讯 GetEmbedding 业务错误 {error.get('Code')}: {error.get('Message')}"
                )
            items = response.get("Data") or []
            if not items or not items[0].get("Embedding"):
                raise RuntimeError("腾讯 GetEmbedding 返回缺少 Data[0].Embedding")
            embeddings.append(items[0]["Embedding"])
        return embeddings

    async def health_check(self) -> dict:
        """探测 Embedding 服务可用性（维度错配时在结果中给出 warning）"""
        if not self.available:
            return {"ok": False, "latency_ms": 0, "error": "Service not configured"}

        t0 = time.monotonic()
        try:
            vectors = await self.embed(["health"], request_id="health-check")
            latency = int((time.monotonic() - t0) * 1000)
            result: dict = {"ok": True, "latency_ms": latency}
            dim = len(vectors[0]) if vectors and vectors[0] else 0
            if dim and dim != EMBEDDING_DIMENSION:
                logger.warning(
                    "embedding.health_dimension_mismatch",
                    dimension=dim,
                    expected=EMBEDDING_DIMENSION,
                )
                result["warning"] = f"维度 {dim} 与库表 vector({EMBEDDING_DIMENSION}) 不一致"
            return result
        except Exception as e:
            latency = int((time.monotonic() - t0) * 1000)
            return {"ok": False, "latency_ms": latency, "error": str(e)[:200]}
