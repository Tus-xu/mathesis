"""云知识库 RAG 检索提供商（providers/cloud_kb.py）

本地 RAG（pgvector 三路召回）之外的云端检索通道，手写签名、不引入云厂商 SDK：
- 腾讯云 LKEAP：TC3-HMAC-SHA256 签名调 RetrieveKnowledge
- 阿里云百炼：RPC 风格 POP（HMAC-SHA1）签名调 Retrieve

签名算法出处（公开文档）：
- TC3-HMAC-SHA256：https://cloud.tencent.com/document/api/1729/101843
  （StringToSign → CanonicalRequest → HMAC-SHA256 派生密钥链）
- 阿里云 RPC 签名：https://help.aliyun.com/zh/sdk/product-overview/rpc-mechanism
  （StringToSign = Method&%2F&percentEncode(sortedQuery)，HMAC-SHA1，key=secret+"&"）

纪律：云通道任何失败（配置缺失/网络/签名/业务错误）都记 warning 并返回 []，
绝不允许拖垮本地 RAG 主链路。HTTP 统一走 providers/http.py 全局单例。
"""

import base64
import hashlib
import hmac
import json
import time
import urllib.parse
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import structlog

from app.config import settings
from app.providers.http import get_http

logger = structlog.get_logger()

# 云端检索调用超时（秒）：对齐 rag.py 第 4 路 ≤8s 的静默跳过约束
RETRIEVE_TIMEOUT_S = 8.0
# 连通性自测超时（秒）
TEST_TIMEOUT_S = 10.0

# 腾讯云 LKEAP 固定端点（知识引擎原子能力）
TENCENT_LKEAP_HOST = "lkeap.tencentcloudapi.com"
TENCENT_LKEAP_URL = f"https://{TENCENT_LKEAP_HOST}"
TENCENT_DEFAULT_REGION = "ap-guangzhou"
TENCENT_DEFAULT_VERSION = "2024-05-22"

# 阿里云百炼固定端点（RPC 风格）
ALIYUN_BAILIAN_URL = "https://bailian.cn-beijing.aliyuncs.com"
ALIYUN_BAILIAN_VERSION = "2023-12-29"


@dataclass(frozen=True)
class CloudKBConfig:
    """云知识库配置（credentials 为 provider 专属字段，入库前已由配置层解密）"""

    enabled: bool
    provider: str  # "tencent_lkeap" | "aliyun_bailian" | ""
    credentials: dict  # provider 专属（已解密）
    knowledge_base_id: str  # 腾讯 KnowledgeBaseId / 阿里 IndexId
    workspace_id: str  # 阿里 WorkspaceId（腾讯留空）
    top_k: int
    score_threshold: float
    source: str  # "system" | "env" | "disabled"


# env JSON（settings.cloud_kb_config）中属于凭证的键，其余键作为库标识提取
_CRED_KEYS = {"secret_id", "secret_key", "region", "version", "access_key_id", "access_key_secret"}


def _config_from_system(raw: object) -> CloudKBConfig | None:
    """system_configs["cloud_kb"] 记录 → CloudKBConfig；无记录返回 None

    库内 JSON 结构（admin 后台写入）：
    {"enabled": bool, "provider": str, "credentials": {...},
     "knowledge_base_id": str, "workspace_id": str, "top_k": int, "score_threshold": float}
    """
    if raw is None:
        return None
    # 容错：记录值可能是 JSON 字符串
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return None
    if not isinstance(raw, dict):
        return None
    credentials = raw.get("credentials") or {}
    if isinstance(credentials, str):
        try:
            credentials = json.loads(credentials)
        except ValueError:
            credentials = {}
    if not isinstance(credentials, dict):
        credentials = {}
    top_k = raw.get("top_k")
    score_threshold = raw.get("score_threshold")
    return CloudKBConfig(
        enabled=bool(raw.get("enabled", False)),
        provider=str(raw.get("provider") or ""),
        credentials=dict(credentials),
        knowledge_base_id=str(raw.get("knowledge_base_id") or ""),
        workspace_id=str(raw.get("workspace_id") or ""),
        top_k=int(top_k) if top_k is not None else 5,
        score_threshold=float(score_threshold) if score_threshold is not None else 0.5,
        source="system",
    )


def _config_from_env() -> CloudKBConfig:
    """env 兜底配置（同事 config.py 未合入时 getattr 防御，默认全部未启用）"""
    enabled = bool(getattr(settings, "cloud_kb_enabled", False))
    provider = str(getattr(settings, "cloud_kb_provider", "") or "")
    try:
        extra = json.loads(str(getattr(settings, "cloud_kb_config", "{}") or "{}"))
    except ValueError:
        extra = {}
    if not isinstance(extra, dict):
        extra = {}
    credentials = {k: v for k, v in extra.items() if k in _CRED_KEYS}
    return CloudKBConfig(
        enabled=enabled,
        provider=provider if enabled else "",
        credentials=credentials,
        knowledge_base_id=str(extra.get("knowledge_base_id") or ""),
        workspace_id=str(extra.get("workspace_id") or ""),
        top_k=int(getattr(settings, "cloud_kb_top_k", 5) or 5),
        score_threshold=float(getattr(settings, "cloud_kb_score_threshold", 0.5)),
        source="env" if enabled else "disabled",
    )


async def resolve_cloud_kb_config(db) -> CloudKBConfig:
    """解析云知识库配置：system_configs["cloud_kb"] 优先 → env 兜底；永不抛异常

    app/models/system_config.py 由 admin 后台并行开发，未合入前延迟 import 失败
    属预期，按无库内配置处理直接走 env。
    """
    if db is not None:
        try:
            from app.models.system_config import get_system_config

            # SAVEPOINT 隔离：查询失败（表缺失/权限等）只回滚到保存点，
            # 不污染调用方事务（kb 导入端点 / RAG 主链路共用同一 session）
            async with db.begin_nested():
                cfg = _config_from_system(await get_system_config(db, "cloud_kb", None))
            if cfg is not None:
                return cfg
        except ImportError:
            pass  # system_config 模型未合入（预期情况），静默走 env 兜底
        except Exception as e:
            logger.warning("cloud_kb.config_system_error", error=str(e)[:200])
    return _config_from_env()


# ---------------------------------------------------------------------------
# 腾讯云 TC3-HMAC-SHA256 签名（providers 层共用，embedding 腾讯分支复用）
# ---------------------------------------------------------------------------


def _sign(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def _tc3_authorization(
    *,
    secret_id: str,
    secret_key: str,
    service: str,
    host: str,
    payload_bytes: bytes,
    timestamp: int,
) -> str:
    """按 TC3 算法计算 Authorization 头（出处见模块 docstring）"""
    date = datetime.fromtimestamp(timestamp, UTC).strftime("%Y-%m-%d")

    # Step 1: CanonicalRequest（POST + JSON，签名头固定 content-type;host）
    http_request_method = "POST"
    canonical_uri = "/"
    canonical_querystring = ""
    canonical_headers = f"content-type:application/json; charset=utf-8\nhost:{host}\n"
    signed_headers = "content-type;host"
    hashed_request_payload = hashlib.sha256(payload_bytes).hexdigest()
    canonical_request = (
        f"{http_request_method}\n{canonical_uri}\n{canonical_querystring}\n"
        f"{canonical_headers}\n{signed_headers}\n{hashed_request_payload}"
    )

    # Step 2: StringToSign
    algorithm = "TC3-HMAC-SHA256"
    credential_scope = f"{date}/{service}/tc3_request"
    string_to_sign = (
        f"{algorithm}\n{timestamp}\n{credential_scope}\n"
        f"{hashlib.sha256(canonical_request.encode('utf-8')).hexdigest()}"
    )

    # Step 3: 派生密钥链 → Signature
    secret_date = _sign(("TC3" + secret_key).encode("utf-8"), date)
    secret_service = _sign(secret_date, service)
    secret_signing = _sign(secret_service, "tc3_request")
    signature = hmac.new(secret_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

    return (
        f"{algorithm} Credential={secret_id}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )


async def _tc3_signed_post(
    *,
    secret_id: str,
    secret_key: str,
    service: str,
    host: str,
    action: str,
    version: str,
    payload: dict,
    region: str = TENCENT_DEFAULT_REGION,
    timeout: float = RETRIEVE_TIMEOUT_S,
) -> dict:
    """TC3 签名 POST 腾讯云服务，返回响应 JSON；HTTP 层错误向上抛出由调用方兜底"""
    payload_bytes = json.dumps(payload).encode("utf-8")
    timestamp = int(time.time())
    authorization = _tc3_authorization(
        secret_id=secret_id,
        secret_key=secret_key,
        service=service,
        host=host,
        payload_bytes=payload_bytes,
        timestamp=timestamp,
    )
    resp = await get_http().post(
        f"https://{host}",
        content=payload_bytes,
        headers={
            "Authorization": authorization,
            "Content-Type": "application/json; charset=utf-8",
            "X-TC-Action": action,
            "X-TC-Version": version,
            "X-TC-Timestamp": str(timestamp),
            "X-TC-Region": region,
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json()


async def _retrieve_tencent(
    cfg: CloudKBConfig, query: str, *, top_k: int, score_threshold: float
) -> list[dict]:
    """腾讯云 LKEAP RetrieveKnowledge（TC3 签名）"""
    secret_id = str(cfg.credentials.get("secret_id") or "")
    secret_key = str(cfg.credentials.get("secret_key") or "")
    if not secret_id or not secret_key or not cfg.knowledge_base_id:
        logger.warning("cloud_kb.tencent_config_incomplete")
        return []
    data = await _tc3_signed_post(
        secret_id=secret_id,
        secret_key=secret_key,
        service="lkeap",
        host=TENCENT_LKEAP_HOST,
        action="RetrieveKnowledge",
        version=str(cfg.credentials.get("version") or TENCENT_DEFAULT_VERSION),
        payload={
            "KnowledgeBaseId": cfg.knowledge_base_id,
            "Query": query,
            "RetrievalMethod": "SEMANTIC",
            "RetrievalSetting": {"TopK": top_k, "ScoreThreshold": score_threshold},
        },
        region=str(cfg.credentials.get("region") or TENCENT_DEFAULT_REGION),
        timeout=RETRIEVE_TIMEOUT_S,
    )
    response = data.get("Response") or {}
    error = response.get("Error")
    if error:
        raise RuntimeError(f"腾讯 LKEAP 业务错误 {error.get('Code')}: {error.get('Message')}")
    records = []
    for item in response.get("Records") or []:
        content = str(item.get("Content") or "").strip()
        if not content:
            continue
        # 腾讯返回相关度得分可能为 0~100 分度，归一化到 0~1 与本地 RRF/闸门对齐
        score = float(item.get("Score") or 0.0)
        if score > 1.0:
            score = score / 100.0
        records.append(
            {
                "title": str(item.get("Title") or ""),
                "content": content,
                "score": score,
                "source": f"cloud_kb:{cfg.provider}",
            }
        )
    return records


# ---------------------------------------------------------------------------
# 阿里云 RPC 风格 POP 签名（HMAC-SHA1）
# ---------------------------------------------------------------------------


def _pop_percent_encode(value: object) -> str:
    """阿里云 POP 规范 URL 编码（出处见模块 docstring）"""
    return (
        urllib.parse.quote(str(value), safe="~")
        .replace("+", "%20")
        .replace("*", "%2A")
        .replace("%7E", "~")
    )


async def _retrieve_aliyun(
    cfg: CloudKBConfig, query: str, *, top_k: int, score_threshold: float
) -> list[dict]:
    """阿里云百炼 Retrieve（POP 签名，GET）"""
    access_key_id = str(cfg.credentials.get("access_key_id") or "")
    access_key_secret = str(cfg.credentials.get("access_key_secret") or "")
    if not access_key_id or not access_key_secret or not cfg.knowledge_base_id:
        logger.warning("cloud_kb.aliyun_config_incomplete")
        return []

    # 公共参数 + 业务参数（Dense 检索 + qwen3-rerank-hybrid 重排）
    params = {
        "Action": "Retrieve",
        "Version": ALIYUN_BAILIAN_VERSION,
        "Format": "JSON",
        "AccessKeyId": access_key_id,
        "SignatureMethod": "HMAC-SHA1",
        "Timestamp": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "SignatureVersion": "1.0",
        "SignatureNonce": uuid.uuid4().hex,
        "IndexId": cfg.knowledge_base_id,
        "Query": query,
        "DenseSimilarityTopK": str(top_k),
        "EnableReranking": "true",
        "Rerank.1.ModelName": "qwen3-rerank-hybrid",
        "Rerank.1.RerankMinScore": str(score_threshold),
        "Rerank.1.RerankTopN": str(top_k),
    }
    if cfg.workspace_id:
        params["WorkspaceId"] = cfg.workspace_id

    # StringToSign = GET&%2F&percentEncode(排序后的 canonicalized query)
    canonicalized = "&".join(
        f"{_pop_percent_encode(k)}={_pop_percent_encode(params[k])}" for k in sorted(params)
    )
    string_to_sign = "GET&%2F&" + _pop_percent_encode(canonicalized)
    signature = base64.b64encode(
        hmac.new(
            (access_key_secret + "&").encode("utf-8"),
            string_to_sign.encode("utf-8"),
            hashlib.sha1,
        ).digest()
    ).decode("utf-8")

    url = f"{ALIYUN_BAILIAN_URL}/?Signature={_pop_percent_encode(signature)}&{canonicalized}"
    resp = await get_http().get(url, timeout=RETRIEVE_TIMEOUT_S)
    resp.raise_for_status()
    data = resp.json()

    body = data.get("Data")
    if not isinstance(body, dict):
        # RPC 错误报文形态：{"Code": ..., "Message": ...}
        raise RuntimeError(f"阿里百炼业务错误 {data.get('Code')}: {data.get('Message')}")
    records = []
    for node in body.get("Nodes") or []:
        content = str(node.get("Text") or "").strip()
        if not content:
            continue
        metadata = node.get("Metadata") or {}
        records.append(
            {
                "title": str(metadata.get("doc_name") or ""),
                "content": content,
                # 阿里百炼 Score 本身为 0~1 分度，无需归一化
                "score": float(node.get("Score") or 0.0),
                "source": f"cloud_kb:{cfg.provider}",
            }
        )
    return records


# ---------------------------------------------------------------------------
# 对外接口
# ---------------------------------------------------------------------------


async def retrieve_cloud_kb(
    cfg: CloudKBConfig, query: str, *, top_k=None, score_threshold=None
) -> list[dict]:
    """云知识库检索：返回 [{title, content, score, source}]；任何失败返回 [] 不抛错"""
    if not cfg.enabled or not cfg.provider or not query.strip():
        return []
    effective_top_k = int(top_k) if top_k is not None else cfg.top_k
    effective_threshold = cfg.score_threshold if score_threshold is None else float(score_threshold)
    try:
        if cfg.provider == "tencent_lkeap":
            return await _retrieve_tencent(
                cfg, query, top_k=effective_top_k, score_threshold=effective_threshold
            )
        if cfg.provider == "aliyun_bailian":
            return await _retrieve_aliyun(
                cfg, query, top_k=effective_top_k, score_threshold=effective_threshold
            )
        logger.warning("cloud_kb.unknown_provider", provider=cfg.provider)
        return []
    except Exception as e:
        logger.warning("cloud_kb.retrieve_failed", provider=cfg.provider, error=str(e)[:200])
        return []


async def test_cloud_kb(cfg: CloudKBConfig) -> dict:
    """连通性自测：query="函数" 实测一次检索，错误原样上报（admin 后台测试按钮用）"""
    result = {"ok": False, "provider": cfg.provider, "latency_ms": 0, "records": 0, "error": ""}
    if not cfg.enabled or not cfg.provider:
        result["error"] = "云知识库未启用或未配置 provider"
        return result
    t0 = time.monotonic()
    try:
        if cfg.provider == "tencent_lkeap":
            records = await _retrieve_tencent(
                cfg, "函数", top_k=cfg.top_k, score_threshold=cfg.score_threshold
            )
        elif cfg.provider == "aliyun_bailian":
            records = await _retrieve_aliyun(
                cfg, "函数", top_k=cfg.top_k, score_threshold=cfg.score_threshold
            )
        else:
            result["error"] = f"未知 provider: {cfg.provider}"
            return result
        result["ok"] = True
        result["records"] = len(records)
    except Exception as e:
        result["error"] = str(e)[:200]
    result["latency_ms"] = int((time.monotonic() - t0) * 1000)
    return result
