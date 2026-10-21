"""LLM 网关（RESEARCH_LLM_*）—— **不新增第五套集成，也不 import Teacher 代码**。

平台已验证的形态是"LiteLLM `Router` + 环境变量配置"，本模块**镜像其形状**
（`async def acompletion(*, model, messages, stream=False, **kw)`），而不是复制其代码；
Teacher 域的 gateway 属禁止 import 范围，本服务只消费平台能力。

## 端点（**必须逐字使用，不要拼接**）

```
RESEARCH_LLM_BASE_URL 默认 = https://maas-api.cn-huabei-1.xf-yun.com/v2/chat/completions
```

**这个值已经包含 `/chat/completions`**。不要再追加一次 —— 那正是历史上把
embedding 路径打成 404 的同一个错误。本模块因此有
`test_base_url_is_not_double_appended` 守卫，且默认值就以 `/chat/completions` 结尾。

## 账号 ↔ 模型授权**不通用**（实测）

同一家供应商背后有**两个账号**，模型授权按账号隔离：

| 模型 | 可用账号 |
|---|---|
| `spark-x2.5-4b` | Account A |
| `xop3qwen80bnext` | Account A |
| `xophunyuanocr` | Account B |

实测：`xophunyuanocr` 在 A 的 key 下 **403**，`xop3qwen80bnext` 在 B 的 key 下 **403**。
因此"同一供应商所以随便用哪把 key"是**错的**，配对必须**可配置 + 启动时校验**
（见 `ACCOUNT_MODEL_MAP` 与 `validate_pairing()`）。

## 只有**一个**供应商 ⇒ 没有供应商级容灾

用户有意统一到一家供应商（三模型同源故同 key）。这不是配置错误，
但意味着**没有任何可用的 LLM 降级路径**：该账号不可用/被限流时，
全平台所有 LLM 路径一起失败。因此本模块**不假设存在第二供应商**，
也**不建造"看起来有兜底"的假重试**；失败必须呈现为**诚实且可恢复**的状态
（用户消息已落库、失败记入 AI run、该轮可重试）。

## 配置

| 变量 | 默认 | 说明 |
|---|---|---|
| `RESEARCH_LLM_API_KEY` | 空 | **缺失 ⇒ 诚实降级**，绝不编造回答 |
| `RESEARCH_LLM_BASE_URL` | `…/v2/chat/completions` | 完整端点，勿拼接 |
| `RESEARCH_LLM_MODEL` | `spark-x2.5-4b` | 主力对话模型（Research 默认） |
| `RESEARCH_LLM_MODEL_LONG` | `xop3qwen80bnext` | 长文本/长结构化输出（同 Account A） |
| `RESEARCH_LLM_MODEL_VISION` | `xophunyuanocr` | 视觉/OCR（**Account B**，Research 默认不用） |
| `RESEARCH_LLM_ACCOUNT` | 空 | 当前 key 的账号标识（`A`/`B`）；留空则自动推断 |
| `RESEARCH_LLM_VALIDATE_ON_STARTUP` | prod: `true` | 启动时校验 (账号, 模型) 配对 |

回退到 `SPARK_*` 变量：本机既有平台凭据就是这样命名的，重复登记一份凭据
只会制造"两处不一致"的漂移源，因此**同一份凭据只读一处**（RESEARCH_LLM_* 优先，
`SPARK_API_PASSWORD` 兜底）。
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Protocol, runtime_checkable

from ..config import is_test

#: 完整端点（**含** `/chat/completions`）。
#:
#: ⚠️ **"逐字使用，不拼接"只对 `llm_base_url()` 成立，不适用于 LiteLLM 的 `api_base`。**
#: 这两者口径不同，混用就是那个 404：
#:   - `llm_base_url()` → 完整端点（对外文档 / 错误信息 / `uses_full_endpoint` 自检）
#:   - `llm_api_base()` → **去掉** `/chat/completions`（交给 LiteLLM，它会自己追加该路径）
#: 详细理由与实测证据见 `llm_api_base()` 的 docstring。
DEFAULT_BASE_URL = "https://maas-api.cn-huabei-1.xf-yun.com/v2/chat/completions"

DEFAULT_MODEL = "spark-x2.5-4b"
DEFAULT_MODEL_LONG = "xop3qwen80bnext"
DEFAULT_MODEL_VISION = "xophunyuanocr"
DEFAULT_PROVIDER = "spark-maas"

#: 账号 ↔ 模型授权映射（实测结果，见 MODEL-INVENTORY.md §6）。
#:
#: 这份映射刻意是**数据**而不是 if/else：它是"实测事实"，改它应当像改配置一样
#: 显式且可审计。`RESEARCH_LLM_ACCOUNT_MODELS` 可覆盖（JSON 或 `A:m1|m2;B:m3`）。
ACCOUNT_MODEL_MAP: dict[str, tuple[str, ...]] = {
    "A": ("spark-x2.5-4b", "xop3qwen80bnext"),
    "B": ("xophunyuanocr",),
}

#: 每个模型的默认账号（用于给出"应该用哪把 key"的建议）。
MODEL_DEFAULT_ACCOUNT: dict[str, str] = {
    "spark-x2.5-4b": "A",
    "xop3qwen80bnext": "A",
    "xophunyuanocr": "B",
}

#: 显式总超时（秒）。网关缓慢滴水字节会重置 read 超时，因此必须硬性有界。
DEFAULT_TIMEOUT_S = 120.0

ENV_LLM_API_KEY = "RESEARCH_LLM_API_KEY"
ENV_LLM_BASE_URL = "RESEARCH_LLM_BASE_URL"
ENV_LLM_ACCOUNT = "RESEARCH_LLM_ACCOUNT"


class LlmGateError(Exception):
    """LLM 调用失败（**不**吞掉，调用方必须落成诚实的降级，而不是伪造回答）。"""

    def __init__(self, code: str, message: str, **detail: Any) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail


class ModelAccountMismatch(LlmGateError):
    """配置的 key 无法服务配置的模型（实测 403）。

    这是一个**可操作的配置错误**，不是瞬时故障：重试不会变好。
    因此它单独成型，让启动校验与请求路径都能给出"该换哪把 key"的明确指引。
    """

    def __init__(self, *, model: str, account: str, hint: str) -> None:
        super().__init__(
            "LLM_MODEL_ACCOUNT_MISMATCH",
            f"配置的凭据（账号 {account}）无法服务模型 {model!r}：{hint}",
            model=model,
            account=account,
            hint=hint,
        )
        self.model = model
        self.account = account
        self.hint = hint


@dataclass
class Completion:
    """一次补全的结果（与 `research_ai_run` 的列一一对应）。"""

    text: str
    model: str
    provider: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    raw: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class Backend(Protocol):
    """LLM 后端协议。形状对齐 LiteLLM 的 `acompletion`，便于替换实现。"""

    name: str
    available: bool
    model: str

    async def acompletion(
        self,
        *,
        model: str | None = None,
        messages: list[dict[str, Any]],
        stream: bool = False,
        **kw: Any,
    ) -> Completion: ...


def _env(*names: str, default: str = "") -> str:
    """按顺序取第一个**非空**环境变量（`RESEARCH_LLM_*` 优先，`SPARK_*` 兜底）。"""
    for n in names:
        v = os.getenv(n)
        if v:
            return v.strip()
    return default


def llm_api_key() -> str:
    return _env(ENV_LLM_API_KEY, "SPARK_API_PASSWORD", "SPARK_API_KEY")


def llm_base_url() -> str:
    """完整端点（含 `/chat/completions`）。**逐字返回，调用方不得再拼该路径。**

    ⚠️ 这个返回值**不是** LiteLLM 的 `api_base` —— 见 `llm_api_base()`。
    把它直接当 `api_base` 传进 LiteLLM 会打成 `.../chat/completions/chat/completions` → 404。

    另外顺手裁掉历史配置里可能出现的重复后缀（同一个错误犯第二次的成本太高）。
    """
    raw = _env(ENV_LLM_BASE_URL, "SPARK_BASE_URL", "SPARK_API_BASE", default=DEFAULT_BASE_URL)
    return _strip_duplicate_path(raw)


def _strip_duplicate_path(url: str) -> str:
    marker = "/chat/completions"
    idx = url.find(marker)
    if idx == -1:
        return url
    return url[: idx + len(marker)]


def llm_api_base() -> str:
    """**LiteLLM 用的** api_base —— 必须**去掉** `/chat/completions`。

    这是一个真实踩过的坑，必须写清楚，否则会被"修回去"：

    - `llm_base_url()` 返回的是**完整端点**（含 `/chat/completions`），这是对外文档、
      错误信息与 `uses_full_endpoint` 自检所用的口径，**保持不变**。
    - 但 LiteLLM 的 `openai/<model>` provider 会**在 api_base 之后自己再拼**
      `/chat/completions`。因此把完整端点当 api_base 传进去，实际请求会变成
      `.../v2/chat/completions/chat/completions` → **404 page not found**。

    实测（2026-09-13，真实 8300 → 真实模型）：

    ```
    LiteLLM completion() model=spark-x2.5-4b; provider = openai
    litellm.NotFoundError: NotFoundError: OpenAIException - 404 page not found
    ```

    改成去掉后缀的 `.../v2` 之后，同一调用 **2.6s 返回 200**。

    旧的 `test_base_url_is_not_double_appended` 只断言 `llm_base_url()` 这个**字符串**
    里 `/chat/completions` 出现一次 —— 它永远为真，却无法发现 LiteLLM 那一侧的二次拼接。
    这正是"字符串断言全绿、真实调用 404"的原因；本函数与配套测试补上那个缺口。
    """
    return _strip_duplicate_path(llm_base_url()).removesuffix("/chat/completions")


def llm_model() -> str:
    return _env("RESEARCH_LLM_MODEL", "SPARK_MODEL", "DEEPSEEK_MODEL", default=DEFAULT_MODEL)


def llm_model_long() -> str:
    return _env("RESEARCH_LLM_MODEL_LONG", "CLASSROOM_LLM_MODEL", default=DEFAULT_MODEL_LONG)


def llm_model_vision() -> str:
    return _env("RESEARCH_LLM_MODEL_VISION", "VISION_MODEL", default=DEFAULT_MODEL_VISION)


def llm_provider() -> str:
    return _env("RESEARCH_LLM_PROVIDER", default=DEFAULT_PROVIDER)


def llm_timeout_s() -> float:
    raw = _env("RESEARCH_LLM_TIMEOUT_SECONDS", default=str(int(DEFAULT_TIMEOUT_S)))
    try:
        return float(raw)
    except ValueError:
        return DEFAULT_TIMEOUT_S


# --------------------------------------------------------------------------
# RV16 · 流式化配置（超时分层 / 重试 / 降级链）
#
# 口径集中在 `core/config.py`（唯一配置源），此处仅**再导出**给 LLM 域使用，
# 使 `gateway` 仍是"LLM 能力的唯一封装"（类图约定）。
# --------------------------------------------------------------------------


def llm_model_fallback() -> str:
    """降级用的**同账号第二模型**（X6：禁止跨账号换 key）。空 = 未配置。"""
    from ..config import llm_model_fallback as _f

    return _f()


def llm_first_token_timeout_s() -> float:
    from ..config import llm_first_token_timeout_s as _f

    return _f()


def llm_connect_timeout_s() -> float:
    from ..config import llm_connect_timeout_s as _f

    return _f()


def llm_total_timeout_s(task_class: str = "short") -> float:
    from ..config import llm_total_timeout_s as _f

    return _f(task_class)


def llm_stream_retries() -> int:
    from ..config import llm_stream_retries as _f

    return _f()


def llm_retry_backoff_base_s() -> float:
    from ..config import llm_retry_backoff_base_s as _f

    return _f()


def llm_max_tokens() -> int:
    """单次补全的输出预算。

    **为什么默认给 4096 而不是 2048**：`spark-x2.5-4b` 是**推理模型** —— 它的输出预算
    里**先扣 reasoning tokens，剩下的才是正文**。实测（2026-09-13）：

    | max_tokens | reasoning_tokens | content 长度 |
    |---|---|---|
    | 200   | 199 | **0**（正文被思考吃光） |
    | 3000  | 859 | 54（有正文） |

    也就是说预算给紧了不是"回答短一点"，而是**正文恒为空**——表现为"模型没回答"，
    极易被误判成网关故障。2048 对推理模型仍偏紧，故默认提到 4096。

    **为什么 RV08 再提到 8192**：科研端目标是**高中数学建模 / 竞赛级长回答**，
    正文含 LaTeX 推导、分步依据与结论（`Proposal.text` 上限 8000 字）。
    4096 总预算扣掉 reasoning 后只够约 1200–1800 字，写不完一道完整推导。
    8192 让完整推导可行，且仍可由 `RESEARCH_LLM_MAX_TOKENS` 覆盖。

    注意：`thinking=disabled` 这类参数在本 provider 上**无效**（实测
    `UnsupportedParamsError: openai does not support parameters: ['thinking']`），
    不能靠关推理来省预算。

    **为什么 RV09 再提到 16384**：v3 提示词要求一条回复里产出
    八段最简结构（ProblemMap）或 2–4 个视角（ResearchMap）的完整 JSON，
    比 v2 的 intake/map 草图大得多。RV09 的真实 E2E 实测：
    8192 预算下 `spark-x2.5-4b` 连续多次把预算**全部耗在 reasoning 上**、
    正文被截断，落库表现为 `PROPOSAL_NOT_JSON` 且 `output_tokens == 8192`
    （截断特征是"每个失败样本都恰好等于上限"，不是随机分布）。
    把预算提到 16384 是让结构可见的最小改动 —— 而不是去改契约。
    """
    raw = _env("RESEARCH_LLM_MAX_TOKENS", default="16384")
    try:
        return max(1, int(raw))
    except ValueError:
        return 16384


def _coerce_extra_value(v: str) -> Any:
    """`RESEARCH_LLM_EXTRA_*` 的值默认按字符串处理；**若形如 JSON 对象/数组则解析**。

    为什么需要（2026-09-14 实测）：litellm 只接受 **dict / list 类型**的
    `extra_body`，而环境变量天生只能给字符串。三个实测结论：

    - `thinking={"type":"disabled"}` → litellm 抛 `UnsupportedParamsError`
      （openai provider 不认 `thinking`）——这正是本文件上方注释所述"无视 thinking"
      的真正原因，并不是 provider 忽略了它。
    - `extra_body='{"thinking":{"type":"disabled"}}'`（字符串）→
      `dictionary update sequence element #0 has length 1`。
    - ✅ `extra_body={"thinking":{"type":"disabled"}}`（**dict**）→ 生效，
      `reasoning_tokens=0`。

    解析失败时**原样返回字符串**，既有行为完全不变。
    """
    s = v.strip()
    if s[:1] in ("{", "["):
        try:
            return json.loads(s)
        except ValueError:
            return v
    return v


def llm_extra_kwargs() -> dict[str, Any]:
    """`RESEARCH_LLM_EXTRA_<key>=<value>` → `{key: value}`。

    值为字符串；**形如 JSON 对象/数组的值会被解析成 dict/list**（见 `_coerce_extra_value`）。

    - 讯飞 MaaS 的 `thinking`：`RESEARCH_LLM_EXTRA_thinking=disabled`（该 provider 实测无效）
    - 火山方舟关思考：`RESEARCH_LLM_EXTRA_extra_body={"thinking":{"type":"disabled"}}`

    ⚠️ **键名统一转小写（2026-09-14 修复的真实 bug）**：Windows 上 `os.environ`
    会把环境变量名**强制大写**，于是 `RESEARCH_LLM_EXTRA_extra_body` 取出后成为
    `EXTRA_BODY` —— 而 litellm 只认小写的 `extra_body`，参数被静默忽略，
    表现为"以为关了思考其实没关"（实测输出 token 29 而非 2）。
    同一原因使本文件文档中记载的 `RESEARCH_LLM_EXTRA_thinking=disabled`
    在 Windows 上**从未生效**。litellm 的参数名一律小写，故统一 `.lower()`。
    """
    out: dict[str, Any] = {}
    for k, v in os.environ.items():
        if k.startswith("RESEARCH_LLM_EXTRA_") and k != "RESEARCH_LLM_EXTRA_":
            key = k[len("RESEARCH_LLM_EXTRA_") :].lower()
            if key:
                out[key] = _coerce_extra_value(v)
    return out


# --------------------------------------------------------------------------
# 账号 ↔ 模型配对
# --------------------------------------------------------------------------


def account_model_map() -> dict[str, tuple[str, ...]]:
    """账号→模型集合。可用 `RESEARCH_LLM_ACCOUNT_MODELS` 覆盖（`A:m1|m2;B:m3`）。"""
    raw = _env("RESEARCH_LLM_ACCOUNT_MODELS")
    if not raw:
        return ACCOUNT_MODEL_MAP
    out: dict[str, tuple[str, ...]] = {}
    for part in raw.split(";"):
        part = part.strip()
        if not part or ":" not in part:
            continue
        acct, models = part.split(":", 1)
        acct = acct.strip().upper()
        out[acct] = tuple(m.strip() for m in models.split("|") if m.strip())
    return out or ACCOUNT_MODEL_MAP


def key_fingerprint(key: str | None = None) -> str:
    """密钥指纹（sha256 前 12 位大写）。

    这是**唯一**允许出现在错误信息/日志里的凭据相关字符串 ——
    它足以定位"是哪把 key"，又不足以还原密钥本身。
    """
    k = key if key is not None else llm_api_key()
    if not k:
        return ""
    return hashlib.sha256(k.encode("utf-8")).hexdigest()[:12].upper()


def resolve_account(key: str | None = None, model: str | None = None) -> str:
    """判定当前凭据属于哪个账号。

    顺序：
    1. 显式 `RESEARCH_LLM_ACCOUNT`；
    2. 若该 key 能服务配置的模型 ⇒ 取该模型的默认账号；
    3. 否则返回 `"unknown"`（随后由 `validate_pairing()` 给出明确报错）。
    """
    explicit = _env(ENV_LLM_ACCOUNT).upper()
    if explicit:
        return explicit
    m = model or llm_model()
    guess = MODEL_DEFAULT_ACCOUNT.get(m)
    if guess:
        return guess
    return "unknown"


def model_account_hint(model: str) -> str:
    """给运维看的"这个模型该用哪个账号"提示。"""
    accts = [a for a, ms in account_model_map().items() if model in ms]
    if not accts:
        return f"模型 {model!r} 不在已登记的账号授权表内：请用 RESEARCH_LLM_ACCOUNT_MODELS 登记"
    return f"模型 {model!r} 只在账号 {'/'.join(accts)} 下可用"


def check_pairing(*, account: str, model: str) -> tuple[bool, str]:
    """纯函数版配对检查（不联网）。返回 `(ok, hint)`。"""
    mapping = account_model_map()
    allowed = mapping.get(account)
    if allowed is None:
        return False, (
            f"账号 {account!r} 不在已登记的授权表 {sorted(mapping)} 内："
            f"请设置 RESEARCH_LLM_ACCOUNT 或 RESEARCH_LLM_ACCOUNT_MODELS"
        )
    if model not in allowed:
        return False, f"{model_account_hint(model)}；当前账号是 {account!r}"
    return True, ""


class LiteLlmRouterBackend:
    """LiteLLM `Router` 实现（唯一真实后端）。

    `Router` 被**惰性**构造：`import litellm` 与 Router 构建都推迟到第一次真实调用，
    因此"未配置凭据"的部署完全不会拉起 litellm（也不会因 litellm 缺失而启动失败）。
    """

    name = "litellm-router"

    def __init__(self, *, api_key: str = "", base_url: str = "", model: str = "") -> None:
        self._api_key = api_key or llm_api_key()
        # `self._base_url` 保留**完整端点**口径（对外自检/错误信息用，历史行为不变）。
        # 但传给 LiteLLM 的必须是去掉 `/chat/completions` 的 base —— 见 `llm_api_base()`。
        # 显式传入 base_url 时同样做一次规范化：调用方给完整端点也要能用。
        full = _strip_duplicate_path(base_url or llm_base_url())
        self._base_url = full
        self._api_base = full.removesuffix("/chat/completions")
        self._model = model or llm_model()
        self._router: Any = None

    @property
    def model(self) -> str:
        return self._model

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def api_base(self) -> str:
        """实际传给 LiteLLM 的 base（不含 `/chat/completions`）。"""
        return self._api_base

    @property
    def available(self) -> bool:
        return bool(self._api_key)

    def litellm_route_name(self, model: str | None = None) -> str:
        """LiteLLM 路由名。Spark 在本平台注册为 OpenAI 兼容部署 ⇒ `openai/<model>`。"""
        return f"openai/{model or self._model}"

    def _get_router(self) -> Any:
        if self._router is None:
            from litellm import Router  # 惰性：未配置凭据的部署不加载 litellm

            # **必须登记全部已配置模型**，而不是只有默认模型。
            #
            # RV09 修复（真实 E2E 抓到）：`Router.acompletion(model=…)` 只认
            # `model_list` 里登记过的名字。这里原本只登记默认模型，于是
            # `send_message` 请求 `llm_proposal_model()`（长输出模型）时
            # LiteLLM 直接抛"model not in model_list"，被映射成
            # `LLM_CALL_FAILED`、`input_tokens=0` —— 表面上像"模型挂了"，
            # 实际是**路由表没登记**。
            names = [self._model]
            for extra in (llm_model_long(), llm_model_vision()):
                if extra and extra not in names:
                    names.append(extra)

            self._router = Router(
                model_list=[
                    {
                        "model_name": name,
                        "litellm_params": {
                            # `openai/<model>` = 平台注册 Spark 为自定义 OpenAI 兼容部署的同一形状
                            "model": f"openai/{name}",
                            "api_key": self._api_key,
                            # **必须是去掉 `/chat/completions` 的 base**：LiteLLM 的 openai
                            # provider 会自己再拼该路径；传完整端点会打成
                            # `.../chat/completions/chat/completions` → 404（实测复现）。
                            "api_base": self._api_base,
                        },
                    }
                    for name in names
                ],
                num_retries=1,
                timeout=llm_timeout_s(),
            )
        return self._router

    def _raise_if_unavailable(self) -> None:
        if not self.available:
            raise LlmGateError(
                "LLM_NOT_CONFIGURED",
                f"未配置 {ENV_LLM_API_KEY}（或 SPARK_API_PASSWORD），拒绝伪造回答",
            )

    async def acompletion(
        self,
        *,
        model: str | None = None,
        messages: list[dict[str, Any]],
        stream: bool = False,
        **kw: Any,
    ) -> Completion:
        self._raise_if_unavailable()
        target = model or self._model
        t0 = time.monotonic()
        try:
            router = self._get_router()
            resp = await router.acompletion(
                model=target,
                messages=messages,
                stream=stream,
                max_tokens=kw.pop("max_tokens", llm_max_tokens()),
                temperature=kw.pop("temperature", 0.2),
                **{**llm_extra_kwargs(), **kw},
            )
        except LlmGateError:
            raise
        except Exception as e:  # noqa: BLE001 - 统一映射成可审计的错误码
            raise self._map_exception(e, model=target) from e

        latency = int((time.monotonic() - t0) * 1000)
        data = resp.model_dump() if hasattr(resp, "model_dump") else dict(resp)
        choices = data.get("choices") or []
        if not choices:
            raise LlmGateError("LLM_EMPTY_CHOICES", "网关返回空 choices")
        message = choices[0].get("message") or {}
        text = message.get("content") or ""
        if not text:
            # 2026-09 实测：讯飞 MaaS spark-x2.5-4b 无视 thinking:disabled，
            # 正文全部落在 reasoning_content（content 恒空）。不回退即全线静默降级。
            text = message.get("reasoning_content") or ""
        usage = data.get("usage") or {}
        return Completion(
            text=text,
            model=str(data.get("model") or target),
            provider=llm_provider(),
            input_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0),
            latency_ms=latency,
            raw={"finish_reason": choices[0].get("finish_reason")},
        )

    def _map_exception(self, e: Exception, *, model: str) -> LlmGateError:
        """把底层异常映射成**可操作**的错误码。

        403 单独识别：在本平台的账号隔离下，403 几乎总是"账号与模型不配对"，
        而不是"密钥无效"。把它报成通用的 403 会让运维去查密钥有效期，
        而真正要做的是换一把 key。
        """
        text = f"{type(e).__name__}: {e}"
        status = getattr(e, "status_code", None) or getattr(
            getattr(e, "response", None), "status_code", None
        )
        account = resolve_account(model=model)
        if status == 403 or "403" in text:
            hint = model_account_hint(model)
            return ModelAccountMismatch(
                model=model,
                account=account,
                hint=f"{hint}（供应商返回 403：该 key 未被授权访问此模型）",
            )
        if status == 401 or "401" in text:
            return LlmGateError(
                "LLM_UNAUTHORIZED",
                f"供应商返回 401：凭据无效或已过期（指纹 {key_fingerprint() or 'N/A'}）",
            )
        if status == 429 or "429" in text:
            # 单账号单点：429 是容量事件的第一个信号，必须可识别，不能被当成普通失败
            return LlmGateError(
                "LLM_RATE_LIMITED",
                "供应商返回 429：单一账号的 RPM/TPM 上限已被触及。"
                "平台无第二供应商可降级 —— 请降低并发或等待后重试",
            )
        return LlmGateError("LLM_CALL_FAILED", text[:500])

    async def astream(
        self,
        *,
        model: str | None = None,
        messages: list[dict[str, Any]],
        **kw: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        """token 流（RV16 / D4）。

        **`acompletion` 签名不变**；本方法是**新增**能力，不改变任何既有调用。
        逐块 yield `{"type":"token","delta":str,"model":str,"finish_reason":str|None}`。

        为什么必须有：单一 120s/180s 墙 + 非流式 = "整段等待撞超时"，
        表现为用户间歇性看到 `WB_LLM_TIMEOUT`。流式把首 token 提前到秒级，
        并让"模型在不在产出"变成可观测的信号（首 token 超时 ≠ 总时长超时）。
        """
        self._raise_if_unavailable()
        target = model or self._model
        try:
            router = self._get_router()
            resp = await router.acompletion(
                model=target,
                messages=messages,
                stream=True,
                max_tokens=kw.pop("max_tokens", llm_max_tokens()),
                temperature=kw.pop("temperature", 0.2),
                **{**llm_extra_kwargs(), **kw},
            )
        except LlmGateError:
            raise
        except Exception as e:  # noqa: BLE001 - 统一映射成可审计的错误码
            raise self._map_exception(e, model=target) from e

        async for chunk in resp:
            data = chunk.model_dump() if hasattr(chunk, "model_dump") else dict(chunk)
            choices = data.get("choices") or []
            if not choices:
                continue
            choice = choices[0]
            delta = choice.get("delta") or {}
            text = delta.get("content") or delta.get("reasoning_content") or ""
            yield {
                "type": "token",
                "delta": text,
                "model": str(data.get("model") or target),
                "finish_reason": choice.get("finish_reason"),
            }


class UnavailableBackend:
    """未配置凭据时的**诚实**后端：任何调用都失败，绝不返回编造内容。"""

    name = "unavailable"
    available = False
    base_url = ""

    def __init__(self, model: str = "") -> None:
        self._model = model or llm_model()

    @property
    def model(self) -> str:
        return self._model

    async def acompletion(self, **kw: Any) -> Completion:  # noqa: ANN401
        raise LlmGateError(
            "LLM_NOT_CONFIGURED",
            f"未配置 {ENV_LLM_API_KEY} / SPARK_API_PASSWORD：模型不可用",
        )

    async def astream(self, **kw: Any) -> AsyncIterator[dict[str, Any]]:  # noqa: ANN401
        raise LlmGateError(
            "LLM_NOT_CONFIGURED",
            f"未配置 {ENV_LLM_API_KEY} / SPARK_API_PASSWORD：模型不可用",
        )
        yield {}  # pragma: no cover - 使本方法成为 async generator（协议一致）


_backend: Backend | None = None


def get_backend() -> Backend:
    """当前进程的 LLM 后端（未配置凭据 ⇒ `UnavailableBackend`）。"""
    global _backend
    if _backend is None:
        _backend = LiteLlmRouterBackend()
    return _backend


def set_backend(b: Backend | None) -> None:
    """替换后端（测试/灰度用）。传 `None` 复位。"""
    global _backend
    _backend = b


def llm_configured() -> bool:
    return get_backend().available


# --------------------------------------------------------------------------
# 启动校验（要求 2：配对错误必须在启动时明确报出，而不是请求时裸 403）
# --------------------------------------------------------------------------


@dataclass
class PairingReport:
    configured: bool
    ok: bool
    model: str
    account: str
    key_fingerprint: str
    base_url: str
    uses_full_endpoint: bool
    hint: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "configured": self.configured,
            "ok": self.ok,
            "model": self.model,
            "account": self.account,
            "key_fingerprint": self.key_fingerprint,
            "base_url": self.base_url,
            "uses_full_endpoint": self.uses_full_endpoint,
            "hint": self.hint,
        }


def validate_pairing(*, model: str | None = None, require_credentials: bool = False) -> PairingReport:
    """校验 (账号, 模型) 配对。

    - 未配置凭据：`configured=False`。默认**不**报错（诚实降级是合法状态）；
      调用方要求凭据时（`require_credentials=True`，生产启动用）才抛错。
    - 已配置但配对不合法：**抛 `ModelAccountMismatch`**，消息里带
      "该用哪个账号"，而不是让请求路径抛一个裸 403。
    """
    target = model or llm_model()
    key = llm_api_key()
    base = llm_base_url()
    account = resolve_account(key, target)
    fp = key_fingerprint(key)
    uses_full_endpoint = base.endswith("/chat/completions")

    if not key:
        report = PairingReport(
            configured=False, ok=False, model=target, account=account,
            key_fingerprint="", base_url=base, uses_full_endpoint=uses_full_endpoint,
            hint=f"未配置 {ENV_LLM_API_KEY} / SPARK_API_PASSWORD",
        )
        if require_credentials:
            raise LlmGateError("LLM_NOT_CONFIGURED", report.hint)
        return report

    ok, hint = check_pairing(account=account, model=target)
    if not ok:
        raise ModelAccountMismatch(model=target, account=account, hint=hint)
    return PairingReport(
        configured=True, ok=True, model=target, account=account,
        key_fingerprint=fp, base_url=base, uses_full_endpoint=uses_full_endpoint,
    )


def llm_proposal_model() -> str:
    """结构化提案用的模型（RV09 新增口径）。

    为什么需要**第二个口径**：`spark-x2.5-4b` 是推理模型 —— 输出预算里
    **先扣 reasoning tokens，剩下的才是正文**（见 `llm_max_tokens` 的实测表）。
    RV09 的真实 E2E 实测：v3 提示词要求一条回复里产出八段最简结构或
    2–4 个视角的完整 JSON，8192 与 16384 **两个预算下都出现过
    `finish_reason=length` 截断**（预算提高只是把墙往后移，而推理长度会跟着涨）。

    平台已登记 `RESEARCH_LLM_MODEL_LONG = xop3qwen80bnext` 为"长文本/长结构化输出"
    （同一账号 A，无需第二把 key）。因此结构化提案**优先**用它，
    默认回退到 `llm_model()`。

    覆盖方式：`RESEARCH_LLM_MODEL_PROPOSE`。
    """
    return _env("RESEARCH_LLM_MODEL_PROPOSE", default="") or llm_model_long()


def assert_ready_or_degrade() -> PairingReport:
    """启动期调用：生产要求凭据，test 允许缺失。

    生产缺凭据即启动失败（fail loud）—— 那是一个应当被运维立刻看到的部署错误；
    测试环境允许缺失，因为"未配置时的诚实降级"本身就是一条被测路径。
    """
    return validate_pairing(require_credentials=not is_test())
