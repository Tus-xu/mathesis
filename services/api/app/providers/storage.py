"""对象存储抽象层（ADR-M2B-001）

以 boto3（S3 协议）实现唯一存储抽象，支持：
- minio（本地开发/CI）
- cos（腾讯云 COS，默认）
- oss（阿里云 OSS）
- s3_custom（任意 S3 兼容服务，支持自定义端口）

API 契约不变：预签名 PUT/GET、分片上传、storage_uri 仅存对象 key。

用户级运行时配置（对齐 /api/model-config 范式）：
- resolve_storage_config(user_id, db)：用户覆盖 + 字段级回退 .env，敏感字段解密
- get_storage_for_user(user_id, db)：无用户配置 → 全局单例；有配置 → 按指纹缓存重建
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from typing import Any

import boto3
import structlog
from botocore.config import Config as BotoConfig
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.providers.crypto import decrypt_api_key

logger = structlog.get_logger(__name__)

# 分片大小 5MB（SSOT §5.1）
PART_SIZE = 5 * 1024 * 1024

# provider → 签名版本 / 寻址风格默认值
_PROVIDER_DEFAULTS: dict[str, dict[str, Any]] = {
    "minio": {"signature_version": "s3v4", "addressing_style": "path"},
    "cos": {"signature_version": "s3", "addressing_style": "virtual"},
    "oss": {"signature_version": "s3", "addressing_style": "virtual"},
    "s3_custom": {"signature_version": "s3v4", "addressing_style": "path"},
}

# -------------------- 用户级配置字段规范 --------------------

STORAGE_FIELDS: tuple[str, ...] = (
    "provider",
    "scheme",
    "host",
    "port",
    "endpoint_url",
    "region",
    "bucket",
    "access_key",
    "secret_key",
    "session_token",
    "presign_expires",
)
STORAGE_SENSITIVE_FIELDS: frozenset[str] = frozenset({"access_key", "secret_key", "session_token"})
STORAGE_PROVIDERS: frozenset[str] = frozenset({"cos", "oss", "minio", "s3_custom"})


@dataclass(frozen=True)
class StorageConfig:
    """对象存储有效配置（用户覆盖与 env 合并后的不可变结果）"""

    provider: str = "cos"
    scheme: str = "https"
    host: str = ""
    port: int | None = None
    endpoint_url: str = ""
    region: str = ""
    bucket: str = ""
    access_key: str = ""
    secret_key: str = ""
    session_token: str = ""
    presign_expires: int = 900
    source: str = "env"  # env | user

    @property
    def endpoint(self) -> str:
        """endpoint 推导：显式 endpoint_url > scheme/host/port 拼接 > provider+region 默认"""
        if self.endpoint_url:
            return self.endpoint_url
        if self.host:
            port = f":{self.port}" if self.port else ""
            return f"{self.scheme}://{self.host}{port}"
        if self.provider == "cos" and self.region:
            return f"https://cos.{self.region}.myqcloud.com"
        if self.provider == "oss" and self.region:
            return f"https://s3.oss-{self.region}.aliyuncs.com"
        return ""


def storage_config_from_settings() -> StorageConfig:
    """从 .env 全局配置构造"""
    return StorageConfig(
        provider=settings.storage_provider,
        scheme=settings.storage_scheme,
        host=settings.storage_host,
        port=settings.storage_port,
        endpoint_url=settings.storage_endpoint_url,
        region=settings.storage_region,
        bucket=settings.storage_bucket,
        access_key=settings.storage_access_key,
        secret_key=settings.storage_secret_key,
        session_token=settings.storage_session_token,
        presign_expires=settings.storage_presign_expires,
        source="env",
    )


def merge_storage_overrides(overrides: dict | None) -> StorageConfig:
    """用户覆盖 + 字段级 env 回退（纯函数）。

    overrides 中敏感字段为密文，此处解密；None 覆盖（无行/缺字段）回退 settings。
    """
    base = storage_config_from_settings()
    if not overrides:
        return base
    values: dict[str, Any] = {}
    for f in STORAGE_FIELDS:
        v = overrides.get(f)
        if v is None:
            continue
        if f in STORAGE_SENSITIVE_FIELDS:
            v = decrypt_api_key(v)
            if not v:
                continue  # 密文损坏 → 视为未覆盖，保留 env 回退
        values[f] = v
    return replace(base, **values, source="user")


async def resolve_storage_config(user_id: str, db: AsyncSession) -> StorageConfig:
    """解析用户有效存储配置：用户值优先，缺字段回退 settings"""
    from app.models.user_integration_config import get_user_integration_overrides

    overrides = await get_user_integration_overrides(user_id, "storage", db)
    return merge_storage_overrides(overrides)


class StorageProvider:
    """S3 兼容对象存储封装（线程安全；无 config 时为全局单例，行为同旧版）"""

    def __init__(self, config: StorageConfig | None = None) -> None:
        self._client = None
        self._config = config  # None → 每次从 settings 读取（全局回退路径）

    def _effective_config(self) -> StorageConfig:
        return self._config or storage_config_from_settings()

    def _get_client(self):
        """懒初始化 boto3 S3 client"""
        if self._client is not None:
            return self._client

        cfg = self._effective_config()
        defaults = _PROVIDER_DEFAULTS.get(cfg.provider, _PROVIDER_DEFAULTS["s3_custom"])

        endpoint_url = cfg.endpoint
        if not endpoint_url:
            raise RuntimeError(
                f"对象存储未配置 endpoint（STORAGE_PROVIDER={cfg.provider}）。"
                "请设置 STORAGE_ENDPOINT_URL 或 STORAGE_HOST/STORAGE_REGION。"
            )

        boto_config = BotoConfig(
            signature_version=defaults["signature_version"],
            s3={"addressing_style": defaults["addressing_style"]},
            connect_timeout=5,
            read_timeout=30,
            max_pool_connections=50,
        )

        session_kwargs: dict[str, Any] = {
            "aws_access_key_id": cfg.access_key,
            "aws_secret_access_key": cfg.secret_key,
        }
        if cfg.session_token:
            session_kwargs["aws_session_token"] = cfg.session_token

        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            region_name=cfg.region or "us-east-1",
            config=boto_config,
            **session_kwargs,
        )
        logger.info(
            "storage_initialized",
            provider=cfg.provider,
            endpoint=endpoint_url,
            bucket=cfg.bucket,
            source=cfg.source,
        )
        return self._client

    @property
    def bucket(self) -> str:
        return self._effective_config().bucket

    @property
    def presign_expires(self) -> int:
        return self._effective_config().presign_expires

    # -------------------- 预签名 URL --------------------

    def presign_put(self, object_key: str, expires: int | None = None) -> str:
        """生成预签名 PUT URL（前端直传）"""
        client = self._get_client()
        return client.generate_presigned_url(
            "put_object",
            Params={"Bucket": self.bucket, "Key": object_key},
            ExpiresIn=expires or self.presign_expires,
        )

    def presign_get(self, object_key: str, expires: int | None = None) -> str:
        """生成预签名 GET URL（前端下载/渲染产物图）"""
        client = self._get_client()
        return client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": object_key},
            ExpiresIn=expires or self.presign_expires,
        )

    # -------------------- 分片上传 --------------------

    def create_multipart_upload(self, object_key: str) -> str:
        """创建分片上传任务，返回 upload_id"""
        client = self._get_client()
        resp = client.create_multipart_upload(Bucket=self.bucket, Key=object_key)
        return resp["UploadId"]

    def presign_upload_part(
        self, object_key: str, upload_id: str, part_number: int
    ) -> str:
        """为指定分片生成预签名 PUT URL"""
        client = self._get_client()
        return client.generate_presigned_url(
            "upload_part",
            Params={
                "Bucket": self.bucket,
                "Key": object_key,
                "UploadId": upload_id,
                "PartNumber": part_number,
            },
            ExpiresIn=self.presign_expires,
        )

    def complete_multipart_upload(
        self, object_key: str, upload_id: str, parts: list[dict]
    ) -> None:
        """完成分片上传。parts: [{part_no: int, etag: str}]"""
        client = self._get_client()
        multipart = {
            "Parts": [
                {"PartNumber": p["part_no"], "ETag": p["etag"]} for p in parts
            ]
        }
        client.complete_multipart_upload(
            Bucket=self.bucket,
            Key=object_key,
            UploadId=upload_id,
            MultipartUpload=multipart,
        )

    # -------------------- 服务端操作 --------------------

    def put_bytes(self, object_key: str, data: bytes, content_type: str = "") -> None:
        """服务端直传（小文件/解析产物）"""
        client = self._get_client()
        extra = {"ContentType": content_type} if content_type else {}
        client.put_object(Bucket=self.bucket, Key=object_key, Body=data, **extra)

    def get_bytes(self, object_key: str) -> bytes:
        """服务端读取对象内容"""
        client = self._get_client()
        resp = client.get_object(Bucket=self.bucket, Key=object_key)
        return resp["Body"].read()

    def delete(self, object_key: str) -> None:
        """删除对象（软删后清理用）"""
        client = self._get_client()
        client.delete_object(Bucket=self.bucket, Key=object_key)

    def exists(self, object_key: str) -> bool:
        """检查对象是否存在"""
        client = self._get_client()
        try:
            client.head_object(Bucket=self.bucket, Key=object_key)
            return True
        except client.exceptions.ClientError:
            return False

    # -------------------- 工具 --------------------

    @staticmethod
    def generate_object_key(user_id: str, filename: str) -> str:
        """生成对象 key：files/{user_id}/{uuid}_{filename}"""
        uid = uuid.uuid4().hex[:12]
        # 清理文件名中的特殊字符
        safe_name = "".join(c for c in filename if c.isalnum() or c in "._-")[:100]
        return f"files/{user_id}/{uid}_{safe_name}"


# 全局单例
_storage: StorageProvider | None = None


def get_storage() -> StorageProvider:
    """获取全局存储单例（.env 配置，用户未配置时的回退）"""
    global _storage
    if _storage is None:
        _storage = StorageProvider()
    return _storage


# 用户级 provider 缓存：user_id → (配置指纹, provider)。配置变化 → 指纹变 → 自动重建
_user_storage_cache: dict[str, tuple[tuple, StorageProvider]] = {}


def _config_fingerprint(cfg: StorageConfig) -> tuple:
    return (
        cfg.provider,
        cfg.scheme,
        cfg.host,
        cfg.port,
        cfg.endpoint_url,
        cfg.region,
        cfg.bucket,
        cfg.access_key,
        cfg.secret_key,
        cfg.session_token,
        cfg.presign_expires,
    )


async def get_storage_for_user(user_id: str, db: AsyncSession) -> StorageProvider:
    """获取 per-user StorageProvider。

    - 无用户配置 → 返回全局单例 get_storage()（零开销）
    - 有用户配置 → 按配置指纹缓存，避免每请求重建 boto3 client
    """
    cfg = await resolve_storage_config(user_id, db)
    if cfg.source == "env":
        return get_storage()

    fingerprint = _config_fingerprint(cfg)
    cached = _user_storage_cache.get(user_id)
    if cached and cached[0] == fingerprint:
        return cached[1]

    provider = StorageProvider(config=cfg)
    _user_storage_cache[user_id] = (fingerprint, provider)
    return provider


def clear_user_storage_cache() -> None:
    """清空用户级 provider 缓存（测试用）"""
    _user_storage_cache.clear()
