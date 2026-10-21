"""API Key 对称加密工具

使用 Fernet 对称加密，密钥从 settings.jwt_secret 派生。
cryptography 库已在依赖链中（via python-jose[cryptography]）。
"""

import base64
import logging

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from app.config import settings

logger = logging.getLogger(__name__)

# 固定 salt（不公开但不需要随机化——jwt_secret 本身已是密钥）
_SALT = b"math-arena-model-config-v1"
# 缓存 Fernet 实例
_fernet: Fernet | None = None


def _get_fernet() -> Fernet:
    """获取 Fernet 实例（缓存）"""
    global _fernet
    if _fernet is not None:
        return _fernet

    secret = settings.jwt_secret
    if not secret or secret in (
        "change-me-in-production",
        "change-me-to-a-random-secret-in-production",
    ):
        logger.warning("使用默认 jwt_secret 派生加密密钥，生产环境不安全！")
        secret = secret or "fallback-dev-key"

    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=_SALT,
        iterations=480000,
    )
    key = base64.urlsafe_b64encode(kdf.derive(secret.encode()))
    _fernet = Fernet(key)
    return _fernet


def encrypt_api_key(plain: str) -> str:
    """加密 API Key，返回 Fernet token 字符串"""
    if not plain:
        return plain
    return _get_fernet().encrypt(plain.encode()).decode()


def decrypt_api_key(token: str) -> str:
    """解密 API Key，返回明文

    - 旧数据兼容：非 Fernet 密文（明文）原样返回
    - 密文但解密失败（如 jwt_secret 已轮换）：告警并返回空串，
      由调用方字段级回退 env——绝不能把 Fernet blob 当明文 key 静默使用
    """
    if not token:
        return token
    try:
        return _get_fernet().decrypt(token.encode()).decode()
    except Exception:
        if token.startswith("gAAAAA"):
            logger.warning("api_key 解密失败（密钥已轮换？），按未配置处理并回退 env")
            return ""
        # 解密失败可能是未加密的明文（兼容旧数据）
        return token


def mask_api_key(key: str) -> str:
    """脱敏显示：sk-***末4位"""
    if not key or len(key) <= 8:
        return "***"
    return f"{key[:3]}***{key[-4:]}"
