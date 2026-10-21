"""research-vnext（8300）配置。

铁律：
- 密钥**只从环境变量读取**，不进代码、不进日志；
- 端口/前缀来自 P00 registry 固化值，禁止散落硬编码（8300 / rv8300 / rvs）。
"""

from __future__ import annotations

import os

# `.env` 必须在**任何** `os.getenv` 之前装载，且必须早于 `Base.metadata` 的类定义期
# 求值（`db/base.py` 在 import 时读取 `db_schema()`）。装载器遵循"显式环境变量优先"，
# 因此 tests/conftest.py 在导入前固定的 `RESEARCH_DB_SCHEMA` 不会被覆盖。
from .bootstrap_env import load_dotenv as _load_dotenv  # noqa: E402

_load_dotenv()

SERVICE_PORT = 8300
SERVICE_NAME = "research-vnext-8300"
DB_SCHEMA = "rv8300"
QUEUE_PREFIX = "strm:rv8300"
OBJECT_PREFIX = "rvs/"
EMBEDDING_DIMS = 768

# REST/SSE 路由前缀。
#
# `API_PREFIX_V1`      —— 既有契约（P00 定稿路径），**必须保持可用**，不得移除。
# `API_PREFIX_VNEXT`   —— 前端 `D:\frontend\src\features\research-vnext\api\researchApi.ts`
#                         实际调用的前缀（`RESEARCH_API_BASE = '/api/research-vnext'`）。
#
# 两个前缀**同时挂载**同一组路由：既修好前端 404，又不对任何既有消费者造成破坏。
# 可用 `RESEARCH_API_PREFIXES`（逗号分隔）覆盖，用于灰度/回退。
API_PREFIX_V1 = "/api/research-v1"
API_PREFIX_VNEXT = "/api/research-vnext"
DEFAULT_API_PREFIXES = (API_PREFIX_V1, API_PREFIX_VNEXT)


def database_url() -> str:
    return os.getenv(
        "RESEARCH_DATABASE_URL",
        "postgresql+psycopg://postgres:postgres@localhost:54329/research_vnext",
    )


def db_schema() -> str:
    return os.getenv("RESEARCH_DB_SCHEMA", DB_SCHEMA)


def env_name() -> str:
    return os.getenv("RESEARCH_ENV", "prod")


def is_test() -> bool:
    return env_name() == "test"


def api_prefixes() -> tuple[str, ...]:
    """返回**同时挂载**的路由前缀（有序、去重、已归一化）。

    默认 `("/api/research-v1", "/api/research-vnext")`。
    `RESEARCH_API_PREFIXES` 可用逗号分隔覆盖，例如只保留旧前缀做灰度回退：
        RESEARCH_API_PREFIXES=/api/research-v1
    """
    raw = os.getenv("RESEARCH_API_PREFIXES", "").strip()
    if not raw:
        return DEFAULT_API_PREFIXES

    out: list[str] = []
    for part in raw.split(","):
        p = part.strip().rstrip("/")
        if not p:
            continue
        if not p.startswith("/"):
            p = "/" + p
        if p not in out:
            out.append(p)
    return tuple(out) or DEFAULT_API_PREFIXES


def gateway_hmac_secret() -> str:
    """网关签名密钥（生产必须从环境变量注入，缺失即 fail closed）。"""
    return os.getenv("RESEARCH_GATEWAY_HMAC_SECRET", "")


def sse_heartbeat_seconds() -> int:
    return int(os.getenv("RESEARCH_SSE_HEARTBEAT_SECONDS", "15"))


def sse_retention_events() -> int:
    return int(os.getenv("RESEARCH_SSE_RETENTION_EVENTS", "1000"))


def sse_retention_hours() -> int:
    return int(os.getenv("RESEARCH_SSE_RETENTION_HOURS", "24"))


# ==========================================================================
# RV03 · 队列 / 作业运行时
#
# 端口、前缀、consumer group、dedupe scope 全部来自 `deploy/registry.yml`
# 的 `research-vnext-8300.queue`（**不发明**）：
#   port 6380 / stream_prefix "strm:rv8300" / consumer_group "rv8300-workers"
#   / dedupe_scope "rv8300" / max_pool 8
# ==========================================================================

QUEUE_PORT = 6380
CONSUMER_GROUP = "rv8300-workers"
DEDUPE_SCOPE = "rv8300"
JOB_DEFAULT_MAX_ATTEMPTS = 3


def valkey_url() -> str:
    """Valkey 连接串。**协调层**，不是事实来源。"""
    explicit = os.getenv("RESEARCH_VALKEY_URL")
    if explicit:
        return explicit
    host = os.getenv("RESEARCH_VALKEY_HOST", "127.0.0.1")
    port = int(os.getenv("RESEARCH_VALKEY_PORT", str(QUEUE_PORT)))
    db = int(os.getenv("RESEARCH_VALKEY_DB", "0"))
    return f"redis://{host}:{port}/{db}"


def queue_stream_key() -> str:
    """主队列 stream key（带 registry 冻结前缀）。"""
    return f"{QUEUE_PREFIX}:jobs"


def queue_dlq_key() -> str:
    return f"{QUEUE_PREFIX}:jobs:dlq"


def consumer_name() -> str:
    """消费者名：worker 实例标识（重启后仍是同一个 group 的新 consumer）。"""
    return os.getenv("RESEARCH_WORKER_CONSUMER") or f"rv8300-worker-{os.getpid()}"


def outbox_relay_batch() -> int:
    return int(os.getenv("RESEARCH_OUTBOX_BATCH", "100"))


def outbox_max_attempts() -> int:
    return int(os.getenv("RESEARCH_OUTBOX_MAX_ATTEMPTS", "10"))


def job_lease_seconds() -> int:
    """租约时长。超过它未续租 ⇒ 该 worker 视为已死，可被其它 worker 接管。"""
    return int(os.getenv("RESEARCH_JOB_LEASE_SECONDS", "60"))


def job_heartbeat_seconds() -> int:
    """**心跳**周期（与 progress 分开：心跳证明存活，progress 报告进度）。"""
    return int(os.getenv("RESEARCH_JOB_HEARTBEAT_SECONDS", "10"))


def job_stage_timeout_seconds() -> int:
    """单 stage 超时。超时 ⇒ 可重试失败（不是永久失败）。"""
    return int(os.getenv("RESEARCH_JOB_STAGE_TIMEOUT_SECONDS", "300"))


def job_total_timeout_seconds() -> int:
    """整个 job 的总超时。超时 ⇒ 不可重试失败（再跑一次也一样）。"""
    return int(os.getenv("RESEARCH_JOB_TOTAL_TIMEOUT_SECONDS", "1800"))


def job_backoff_base_seconds() -> float:
    return float(os.getenv("RESEARCH_JOB_BACKOFF_BASE_SECONDS", "2"))


def job_backoff_max_seconds() -> float:
    return float(os.getenv("RESEARCH_JOB_BACKOFF_MAX_SECONDS", "300"))


def worker_poll_block_ms() -> int:
    return int(os.getenv("RESEARCH_WORKER_POLL_BLOCK_MS", "2000"))


# ---------------- 背压（本平台首次引入 provider 限流） ----------------
#
# 说明：此前 8300 **完全没有** provider 侧限流。这里把上限全部做成可配置，
# 并在超限时返回 429/503 + `Retry-After`，而不是让队列无限增长。


def max_jobs_per_user() -> int:
    return int(os.getenv("RESEARCH_MAX_JOBS_PER_USER", "4"))


def max_jobs_per_project() -> int:
    return int(os.getenv("RESEARCH_MAX_JOBS_PER_PROJECT", "16"))


def max_jobs_per_provider() -> int:
    return int(os.getenv("RESEARCH_MAX_JOBS_PER_PROVIDER", "8"))


def max_queue_depth() -> int:
    """在队（QUEUED/RETRYING/CLAIMED）作业总数上限 ⇒ 503。"""
    return int(os.getenv("RESEARCH_MAX_QUEUE_DEPTH", "500"))


def max_oldest_age_seconds() -> int:
    """队首最老作业年龄上限（秒）⇒ 503。"""
    return int(os.getenv("RESEARCH_MAX_OLDEST_AGE_SECONDS", "900"))


def retry_after_seconds() -> int:
    return int(os.getenv("RESEARCH_RETRY_AFTER_SECONDS", "5"))


# ---------------- LLM 供应商容量（**单一账号 = 单点**） ----------------
#
# 本平台只有一家 LLM 供应商、一个账号（用户有意统一，见 MODEL-INVENTORY.md）。
# 没有供应商级容灾，因此**限流是唯一的保护层**：一个并发突刺会直接打到
# 那个账号的 RPM/TPM 上限，然后全平台一起失败。
#
# 因此这组配置不是"可选的优化"，而是本服务对单点拓扑的**承重结构**。


def max_llm_inflight() -> int:
    """同时在飞的 LLM 调用上限（进程内）。

    这是最后一道也是最直接的一道闸：在**发出请求之前**挡住，
    而不是等供应商回 429。
    """
    return int(os.getenv("RESEARCH_MAX_LLM_INFLIGHT", "4"))


def llm_acquire_timeout_seconds() -> float:
    """等待 LLM 槽位的时长。超时即 429（而不是无限排队把请求堆在内存里）。"""
    return float(os.getenv("RESEARCH_LLM_ACQUIRE_TIMEOUT_SECONDS", "10"))


def max_llm_turns_per_user() -> int:
    """同一用户的在飞 LLM 轮次上限。防止单个用户吃掉整个账号的配额。"""
    return int(os.getenv("RESEARCH_MAX_LLM_TURNS_PER_USER", "2"))


def max_llm_turns_per_session() -> int:
    return int(os.getenv("RESEARCH_MAX_LLM_TURNS_PER_SESSION", "2"))


def llm_rate_limit_window_seconds() -> int:
    """LLM 调用的滑动窗口（秒），用于 per-user 速率统计。"""
    return int(os.getenv("RESEARCH_LLM_RATE_WINDOW_SECONDS", "60"))


def max_llm_calls_per_window() -> int:
    """窗口内每用户允许的 LLM 调用数。"""
    return int(os.getenv("RESEARCH_MAX_LLM_CALLS_PER_WINDOW", "20"))


# ==========================================================================
# RV14 · Paper export/compile worker
#
# 队列隔离（任务书 4）：paper.export 只进独立 compile stream/group/dedupe，
# 与主 pool（strm:rv8300 / rv8300-workers / rv8300）互不消费；
# 前缀仍从 registry 冻结值派生（strm:rv8300:*），不发明新命名空间。
# ==========================================================================

COMPILE_STREAM_KEY = f"{QUEUE_PREFIX}:compile"
COMPILE_CONSUMER_GROUP = "rv8300-compile-workers"
COMPILE_DEDUPE_SCOPE = "rv8300-compile"
COMPILE_JOB_TYPES = ("paper.export",)


def compile_pool_enabled() -> bool:
    return os.getenv("RESEARCH_COMPILE_POOL", "1") not in ("0", "false", "no")


def tex_image() -> str:
    """TeX 编译镜像（本地固定 tag；digest 见 RV14/COMPILER-PIN-LICENSE-SBOM.md）。"""
    return os.getenv("RESEARCH_TEX_IMAGE", "math-arena-tex:0.1.0")


def tex_image_id() -> str:
    """镜像 ID（部署时写入 pinned digest；空串 = 未登记）。"""
    return os.getenv("RESEARCH_TEX_IMAGE_ID", "")


def tex_compile_timeout_seconds() -> int:
    """单遍编译墙钟上限。超时 ⇒ 可重试失败（EXPORT_COMPILE_TIMEOUT）。"""
    return int(os.getenv("RESEARCH_TEX_TIMEOUT_SECONDS", "120"))


def tex_max_output_bytes() -> int:
    """单遍编译 log 字节数上限（防失控日志）。"""
    return int(os.getenv("RESEARCH_TEX_MAX_OUTPUT_BYTES", str(8 * 1024 * 1024)))


def tex_cpus() -> str:
    return os.getenv("RESEARCH_TEX_CPUS", "2")


def tex_memory() -> str:
    return os.getenv("RESEARCH_TEX_MEMORY", "2g")


def tex_pids_limit() -> str:
    return os.getenv("RESEARCH_TEX_PIDS_LIMIT", "128")


def tex_workdir_root() -> str:
    """编译工作目录宿主根（每次编译在其下建临时子目录）。"""
    return os.getenv("RESEARCH_TEX_WORKDIR", os.path.join(os.path.expanduser("~"), ".rv14-tex"))


def download_token_ttl_seconds() -> int:
    return int(os.getenv("RESEARCH_DOWNLOAD_TTL_SECONDS", "300"))


# ==========================================================================
# RV16 · LLM 流式化（超时分层 / 重试 / 降级链）
#
# KERNEL §2.2：不得用单一 60s 墙。连接 10s / 首 token 30s / 短任务 90s / 长任务 300s。
# X6（主理人裁决）：降级链 = 同账号第二模型 → 确定性兜底；**禁止跨账号换 key**。
# ==========================================================================


def llm_model_fallback() -> str:
    """降级用的**同账号第二模型**（X6）。空串 = 未配置第二模型。

    刻意不跨账号：跨账号会引入新的密钥管理面与合规风险，且"诚实失败"优于"假兜底"。
    未配置时降级链止于确定性兜底（见 `streaming.py`）。
    """
    return os.getenv("RESEARCH_LLM_MODEL_FALLBACK", "").strip()


def llm_connect_timeout_s() -> float:
    """建连超时（TCP/TLS 握手）。"""
    return float(os.getenv("RESEARCH_LLM_CONNECT_TIMEOUT_S", "10"))


def llm_first_token_timeout_s() -> float:
    """首 token 超时：超过它即判定"模型没在产出"，触发重试/降级。"""
    return float(os.getenv("RESEARCH_LLM_FIRST_TOKEN_TIMEOUT_S", "30"))


def llm_total_timeout_s(task_class: str = "short") -> float:
    """总时长上限，按任务分级（短 90s / 长 300s）。"""
    if task_class == "long":
        return float(os.getenv("RESEARCH_LLM_TOTAL_TIMEOUT_S_LONG", "300"))
    return float(os.getenv("RESEARCH_LLM_TOTAL_TIMEOUT_S_SHORT", "90"))


def llm_stream_retries() -> int:
    """幂等流式调用的显式重试次数（指数退避 + 抖动）。≥2（KERNEL §2.2）。"""
    return max(0, int(os.getenv("RESEARCH_LLM_STREAM_RETRIES", "2")))


def llm_retry_backoff_base_s() -> float:
    return float(os.getenv("RESEARCH_LLM_RETRY_BACKOFF_BASE_S", "0.5"))


def llm_deterministic_fallback_enabled() -> bool:
    """确定性兜底开关。开启时，模型不可用仍给出**结构化骨架**并标注 degraded。"""
    return os.getenv("RESEARCH_LLM_DETERMINISTIC_FALLBACK", "1") not in ("0", "false", "no")


def stream_buffer_retention_chunks() -> int:
    """每个 turn 落库保留的最大帧数（防失控内存/表膨胀）。"""
    return int(os.getenv("RESEARCH_STREAM_RETENTION_CHUNKS", "2000"))
