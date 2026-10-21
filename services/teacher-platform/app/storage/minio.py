"""MinIO 对象存储（02-ARCHITECTURE §5）。

- presign PUT 直传（前端直传，写端点零流量过 API）→ 短时效 URL。
- presign GET 下载；服务端 put_bytes（worker 导出产物）。
- bucket 启动自动初始化；DB 只存 key 不存 URL。
"""
from __future__ import annotations

import io
from datetime import timedelta

from minio import Minio
from minio.error import S3Error

from ..core.config import get_settings

BUCKETS = {
    "teacher-photos": "originals/{user_id}/{uuid}.jpg · enhanced/{uuid}.jpg",
    "teacher-assets": "quiz/{qid}/stem.jpg · answers/{aid}.jpg · classroom/...",
    "teacher-exports": "decks/{deck_id}/{ts}.pptx · plans/{plan_id}/{ts}.pdf",
    "teacher-corpus": "benchmarks/** · sources/**",
    "teacher-thumbs": "deck-templates/{tid}.png",
}


class Storage:
    def __init__(self, *, client: Minio | None = None):
        settings = get_settings()
        self._client = client or Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
        )

    @property
    def client(self) -> Minio:
        return self._client

    async def ensure_buckets(self) -> None:
        """启动时幂等初始化全部 bucket（缺失才建）。"""
        for bucket in BUCKETS:
            try:
                if not self._client.bucket_exists(bucket):
                    self._client.make_bucket(bucket)
            except S3Error as exc:
                # 并发初始化（多 worker 同时启动）可容忍已存在
                if exc.code != "BucketAlreadyOwnedByYou":
                    raise

    def presign_put(self, bucket: str, object_name: str, *, expires_seconds: int = 900) -> str:
        return self._client.presigned_put_object(bucket, object_name, expires=timedelta(seconds=expires_seconds))

    def get_bytes(self, bucket: str, object_name: str) -> bytes:
        """读取对象字节（pptx 导入/缩略图回源等）——不存在抛 not found。"""
        resp = None
        try:
            resp = self.client.get_object(bucket, object_name)
            return resp.read()
        finally:
            if resp is not None:
                resp.close()
                resp.release_conn()

    def presign_get(self, bucket: str, object_name: str, *, expires_seconds: int = 3600) -> str:
        return self._client.presigned_get_object(bucket, object_name, expires=timedelta(seconds=expires_seconds))

    def put_bytes(self, bucket: str, object_name: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        self._client.put_object(bucket, object_name, io.BytesIO(data), length=len(data), content_type=content_type)

    async def health(self) -> bool:
        try:
            return bool(self._client.bucket_exists("teacher-assets") or True)
        except Exception:  # noqa: BLE001
            return False


_storage: Storage | None = None


def get_storage() -> Storage:
    global _storage
    if _storage is None:
        _storage = Storage()
    return _storage
