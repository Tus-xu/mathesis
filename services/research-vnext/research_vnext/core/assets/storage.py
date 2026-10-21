"""RV04 · 对象存储适配（MinIO/S3 via boto3）。

浏览器**永不**看到本模块返回的 endpoint；签名交付走 Gateway。
"""

from __future__ import annotations

import io
import os
from dataclasses import dataclass
from typing import BinaryIO, Iterator

from ..config import OBJECT_PREFIX


@dataclass(frozen=True)
class StorageConfig:
    endpoint: str
    access_key: str
    secret_key: str
    bucket: str
    region: str = "us-east-1"
    prefix: str = OBJECT_PREFIX

    @classmethod
    def from_env(cls) -> "StorageConfig":
        return cls(
            endpoint=os.getenv("RESEARCH_S3_ENDPOINT", "http://127.0.0.1:9002"),
            access_key=os.getenv("RESEARCH_S3_ACCESS_KEY", ""),
            secret_key=os.getenv("RESEARCH_S3_SECRET_KEY", ""),
            bucket=os.getenv("RESEARCH_S3_BUCKET", "rv-assets"),
            region=os.getenv("RESEARCH_S3_REGION", "us-east-1"),
            prefix=os.getenv("RESEARCH_S3_PREFIX", OBJECT_PREFIX),
        )


class ObjectStorageError(Exception):
    pass


class MinioObjectStorage:
    """真实 S3/MinIO 客户端。只暴露 put/get/range/delete + key，不暴露 presigned 给浏览器。"""

    def __init__(self, cfg: StorageConfig | None = None):
        self.cfg = cfg or StorageConfig.from_env()
        if not self.cfg.access_key or not self.cfg.secret_key:
            raise ObjectStorageError("S3 凭据未配置（RESEARCH_S3_ACCESS_KEY/SECRET_KEY）")
        import boto3
        from botocore.client import Config
        from botocore.exceptions import ClientError  # noqa: F401

        self._s3 = boto3.client(
            "s3",
            endpoint_url=self.cfg.endpoint,
            aws_access_key_id=self.cfg.access_key,
            aws_secret_access_key=self.cfg.secret_key,
            region_name=self.cfg.region,
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        )
        self._ensure_bucket()

    def _ensure_bucket(self) -> None:
        from botocore.exceptions import ClientError

        try:
            self._s3.head_bucket(Bucket=self.cfg.bucket)
        except ClientError:
            try:
                self._s3.create_bucket(Bucket=self.cfg.bucket)
            except ClientError as e:
                raise ObjectStorageError(f"无法创建/访问 bucket {self.cfg.bucket}: {e}") from e

    def build_key(self, tenant_id: str, asset_id: str, version: int) -> str:
        # 前缀来自 registry（rvs/），禁止散落硬编码第二套前缀。
        return f"{self.cfg.prefix}{tenant_id}/{asset_id}/v{version}"

    def put_stream(self, key: str, fileobj: BinaryIO, length: int) -> None:
        self._s3.upload_fileobj(
            fileobj,
            self.cfg.bucket,
            key,
            ExtraArgs={"ContentType": "application/octet-stream"},
        )

    def put_bytes(self, key: str, data: bytes) -> None:
        self.put_stream(key, io.BytesIO(data), len(data))

    def get_bytes(self, key: str) -> bytes:
        try:
            obj = self._s3.get_object(Bucket=self.cfg.bucket, Key=key)
            return obj["Body"].read()
        except Exception as e:  # noqa: BLE001
            raise ObjectStorageError(str(e)) from e

    def get_range(self, key: str, start: int, end_inclusive: int) -> bytes:
        try:
            obj = self._s3.get_object(
                Bucket=self.cfg.bucket, Key=key, Range=f"bytes={start}-{end_inclusive}"
            )
            return obj["Body"].read()
        except Exception as e:  # noqa: BLE001
            raise ObjectStorageError(str(e)) from e

    def iter_chunks(self, key: str, chunk_size: int = 64 * 1024) -> Iterator[bytes]:
        obj = self._s3.get_object(Bucket=self.cfg.bucket, Key=key)
        body = obj["Body"]
        while True:
            chunk = body.read(chunk_size)
            if not chunk:
                break
            yield chunk

    def head(self, key: str) -> dict:
        return self._s3.head_object(Bucket=self.cfg.bucket, Key=key)

    def delete(self, key: str) -> None:
        self._s3.delete_object(Bucket=self.cfg.bucket, Key=key)
