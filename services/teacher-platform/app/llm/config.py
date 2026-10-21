"""B1 LLM 网关配置：模型矩阵与路由（02-ARCHITECTURE §2「模型路由」表）。

B1-owned（§9），不进 B3 的 app/core/config.py。key 只从环境/.env 读取，
代码里永不出现明文密钥（§15）。
"""
from __future__ import annotations

from typing import Any, ClassVar

from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict


class UseCaseRoute(BaseModel):
    """一个用例的主备模型与超时（秒）。model 字符串为 LiteLLM 格式。"""

    primary: str
    fallback: str | None
    timeout_s: float
    temperature: float = 0.3


# 02-ARCHITECTURE §2 模型矩阵的代码投影（改矩阵 = 走 §8 接口变更流程，不改这里绕开）
USE_CASE_ROUTES: dict[str, UseCaseRoute] = {
    # 管家对话 / 意图路由 / FC
    "chat": UseCaseRoute(primary="deepseek/deepseek-chat", fallback="qwen/qwen-plus", timeout_s=30),
    # 长文生成（教案/课件/讲评）——Spark MaaS 逐页课件实测需 3-5min/页（M2-B 压测 2026-09-08），300s 起步
    "long_form": UseCaseRoute(primary="deepseek/deepseek-chat", fallback="qwen/qwen-max", timeout_s=300),
    # 课件全文一次成稿（deck/full.j2 单调用；逐页调用实测 8-12min/页不可用，2026-09-08 裁决）
    "deck_full": UseCaseRoute(primary="deepseek/deepseek-chat", fallback="qwen/qwen-max", timeout_s=600),
    # VLM（读题重建/拍照理解）
    "vision": UseCaseRoute(primary="qwen/qwen-vl-max", fallback="zhipu/glm-4v", timeout_s=60),
    # 语音公式 L2 解析（弱任务、低温度）
    "voice_formula": UseCaseRoute(
        primary="deepseek/deepseek-chat", fallback="qwen/qwen-turbo", timeout_s=30, temperature=0.1
    ),
    # Teacher Prep（R17/R20）：意图 / 蓝图 / 诊断投影 —— 与 chat 同级结构化任务
    "teacher_prep_intent": UseCaseRoute(
        primary="deepseek/deepseek-chat", fallback="qwen/qwen-plus", timeout_s=180, temperature=0.0
    ),
    "teacher_prep_blueprint": UseCaseRoute(
        primary="deepseek/deepseek-chat", fallback="qwen/qwen-plus", timeout_s=300, temperature=0.2
    ),
    "teacher_prep_diagnostic": UseCaseRoute(
        primary="deepseek/deepseek-chat", fallback="qwen/qwen-plus", timeout_s=300, temperature=0.2
    ),
    # R2 对话去表单机：针对老师该句的 prose 回应（闲聊/提问/质疑/确认，短任务低延迟）
    "teacher_prep_reply": UseCaseRoute(
        primary="deepseek/deepseek-chat", fallback="qwen/qwen-plus", timeout_s=60, temperature=0.4
    ),
    # specs/regen 链路（L-221-2 教案块 AI 重生成；2026-09-19 实测未注册 → 网关 500）
    "lesson_plan_regen": UseCaseRoute(
        primary="deepseek/deepseek-chat", fallback="qwen/qwen-plus", timeout_s=300, temperature=0.2
    ),
    # ppt_v2 AI 快捷操作重生成（service/regen.py 调用点；与 lesson_plan_regen 同级结构化任务）
    "ppt_regen": UseCaseRoute(
        primary="deepseek/deepseek-chat", fallback="qwen/qwen-plus", timeout_s=300, temperature=0.2
    ),
    # ppt_v2 课件逐页增稠（service/enrich.py 调用点；逐页结构化扩写，与 ppt_regen 同级）
    "deck_enrich": UseCaseRoute(
        primary="deepseek/deepseek-chat", fallback="qwen/qwen-plus", timeout_s=300, temperature=0.2
    ),
}

# 重试 ≤2 且只对 5xx/超时（B1 红线）；429 交由 Router 冷却机制，不原地重试
RETRY_POLICY: dict[str, int] = {
    "TimeoutErrorRetries": 2,
    "InternalServerErrorRetries": 2,
    "ServiceUnavailableErrorRetries": 2,
    "APIConnectionErrorRetries": 2,
    "RateLimitErrorRetries": 0,
    "BadRequestErrorRetries": 0,
    "AuthenticationErrorRetries": 0,
    "ContentPolicyViolationErrorRetries": 0,
    "DefaultRetries": 0,
}


class LlmSettings(BaseSettings):
    """供应商 key 与开关。所有字段可被 LLM_ 前缀环境变量覆盖。"""

    model_config = SettingsConfigDict(env_prefix="LLM_", env_file=".env", env_file_encoding="utf-8", extra="ignore")

    api_key_deepseek: str = ""
    api_key_qwen: str = ""  # DashScope
    api_key_zhipu: str = ""
    base_url_deepseek: str = ""
    base_url_qwen: str = ""

    # Spark（讯飞 MaaS，OpenAI 兼容端点；文本模型）
    api_key_spark: str = ""
    base_url_spark: str = "https://maas-api.cn-huabei-1.xf-yun.com/v2"
    model_spark: str = "spark-x2.5-4b"
    # 长文生成大模型（课件/教案/讲评：long_form/deck_full）。80B 基准（学生端实测）：
    # 大纲 3.5s / 逐页 2.4s，比 4B（120s 超时/逐页 20-40s）快一个量级。空 = 回落 model_spark。
    model_longform: str = ""
    # 大模型独立 key（80B 与 4B/视觉分属不同 MaaS 授权）；空 = 复用 api_key_spark。
    api_key_longform: str = ""
    # 讯飞 MaaS 图像理解模型（与 Spark 同端点；LLM_MODEL_VISION 可覆盖）
    model_vision: str = "xophunyuanocr"

    # ── 救援通道（rescue，2026-09-23 新增）──────────────────────────────────
    # 主通道整体不可用时的兜底 LLM（OpenAI 兼容端点）。触发场景（实测）：
    #   ① 方舟欠费 → 403 AccountOverdueError；② 模型月度额度耗尽 → 429。
    # 在网关层注册为一个部署，并作为 model_spark / model_longform 的 fallback
    # （LiteLLM Router fallbacks），使产品在主通道故障时仍可完成备课链路。
    # 空 = 不启用救援通道（行为与历史一致）。
    api_key_rescue: str = ""
    base_url_rescue: str = ""
    model_rescue: str = ""

    # active_profile：'matrix' = §2 冻结矩阵（deepseek 主/qwen 备）；
    # 'spark' = 文本用例（chat/long_form/voice_formula）改走 Spark（deepseek/qwen key 缺席的现实主通道）；
    # vision 在 spark profile 下走讯飞图像理解模型（model_vision，同端点/key）。
    active_profile: str = "matrix"

    # spark thinking 默认关（学生端 ADR：默认开思考会干扰 FC/结构化解析；思考模式显式开启）
    spark_thinking: bool = False
    # 方舟等严格网关拒绝未知参数（glm-5.3-flash 实测 400）——置 1 时不发
    # enable_thinking extra_body（模型本身锁思考，发了只会 400）
    omit_thinking_extra_body: bool = False
    # 通用 thinking 开关（厂商语法透传，LLM_THINKING_EXTRA_BODY，JSON）：置定时作为
    # 主通道全部调用的 extra_body 原样发送，**优先于** spark 顶层 enable_thinking——
    # 两家语法互不兼容：方舟只认 {"thinking":{"type":"disabled"}}，Spark MaaS 只认
    # 顶层 enable_thinking。2026-10-10 全量迁方舟后未关思考 → blueprint 输出
    # 3933→17136 tokens、21s→281s（ai_runs 实证），本开关即为此而设。
    thinking_extra_body: dict[str, Any] | None = None

    # False = 未配置任何 key：网关拒绝真实调用（测试/CI 注入 FakeBackend，见 gateway.Backend）
    real_router_enabled: bool = True

    TEXT_USE_CASES: ClassVar[tuple[str, ...]] = ("chat", "long_form", "voice_formula", "deck_full")

    @property
    def has_any_key(self) -> bool:
        return bool(self.api_key_deepseek or self.api_key_qwen or self.api_key_zhipu or self.api_key_spark)

    @property
    def spark_available(self) -> bool:
        return bool(self.api_key_spark)

    @property
    def rescue_available(self) -> bool:
        """救援通道是否可用（key + base_url + model 三者齐备才注册部署）。"""
        return bool(self.api_key_rescue and self.base_url_rescue and self.model_rescue)

    def resolve_route(self, use_case: str) -> UseCaseRoute:
        """按 profile 解析用例路由：
        - spark profile：长文生成（long_form/deck_full）切大模型（model_longform，80B 快一个量级）；
          chat/voice_formula 保持 4B 控成本；vision 走讯飞图像理解模型。
        - matrix profile：§2 冻结矩阵。
        """
        route = USE_CASE_ROUTES.get(use_case)
        if route is None:
            raise ValueError(f"未知用例 {use_case!r}，可选：{sorted(USE_CASE_ROUTES)}")
        if self.active_profile == "spark" and self.spark_available:
            if use_case in ("long_form", "deck_full"):
                return UseCaseRoute(primary=self.model_longform or self.model_spark, fallback=None,
                                    timeout_s=route.timeout_s, temperature=route.temperature)
            if (
                use_case == "chat"
                or use_case.startswith("teacher_prep_")
                # ppt_v2 逐页增稠：与 teacher_prep_* 同为结构化长文任务，走 80B/longform 通道
                # （避免 resolved==model_spark 时附带 enable_thinking extra_body）
                or use_case == "deck_enrich"
            ) and self.model_longform:
                # 首页/管家与教师备课结构化调用切 80B：4B 在带教材选项的提示词下
                # thinking 过长，真实请求会触发多轮 60s 超时，无法完成首条消息闭环。
                return UseCaseRoute(primary=self.model_longform, fallback=None,
                                    timeout_s=route.timeout_s, temperature=route.temperature)
            # R4 修复（subagent D）：局部重生成用例（ppt_regen/lesson_plan_regen）未进
            # spark profile 的文本切换 → 其 primary（deepseek/*）在 spark 环境无部署，
            # 实测 "no healthy deployments" → AI 快捷操作/单页修改全链不可用。
            # 与 teacher_prep_* 同为结构化短任务，切 model_spark（doubao 通道）。
            if (
                use_case in self.TEXT_USE_CASES
                or use_case.startswith("teacher_prep_")
                or use_case.endswith("_regen")
                or use_case == "deck_enrich"  # ppt_v2 逐页增稠：与 *_regen 同为结构化短任务
            ):
                return UseCaseRoute(
                    primary=self.model_spark,
                    fallback=None,
                    timeout_s=route.timeout_s,
                    temperature=route.temperature,
                )
            if use_case == "vision" and self.model_vision:
                return UseCaseRoute(primary=self.model_vision, fallback=None, timeout_s=route.timeout_s, temperature=0.2)
        return route


def get_llm_settings() -> LlmSettings:
    return LlmSettings()
