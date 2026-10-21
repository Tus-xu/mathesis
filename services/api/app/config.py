"""应用配置管理（pydantic-settings）

所有密钥走环境变量，代码库出现密钥字符串 = 事故。
"""

from pathlib import Path

from pydantic_settings import BaseSettings

# .env 位于 monorepo 根目录（services/api 上溯两级）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_ENV_FILE = _PROJECT_ROOT / ".env"


class Settings(BaseSettings):
    """全局配置，从环境变量 / .env 文件加载"""

    # -------------------- 数据库 --------------------
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/math_arena"

    # -------------------- Redis --------------------
    redis_url: str = "redis://localhost:6379/0"

    # -------------------- 星火大模型（主通道） --------------------
    spark_api_password: str = ""
    spark_model: str = "spark-ultra"
    # 主通道端点。空 = 默认 spark-api-open；2026-09-01 起主通道为讯飞 MaaS
    # Spark-X2.5-4B（OpenAI 兼容），注意需填完整 chat/completions 路径。
    spark_base_url: str = ""
    spark_thinking: bool = False  # 思考模式全局默认（per-call thinking=True 可覆盖）
    # 方舟等严格 OpenAI 兼容网关拒绝未知参数（glm-5.3-flash 实测 400：
    # thinking.type `disabled` is not supported）——置 1 时完全不发 thinking 字段
    llm_omit_thinking_field: bool = False

    # -------------------- 课堂生成通道（PHASE 5.1，2026-09-08 基准选型） --------------------
    # 基准实测（讯飞 MaaS 同账号）：4B 主通道大纲 120s 超时；xop3qwen80bnext 大纲 3.5s/
    # 逐页 2.4s 且 LaTeX 零裸漏+math_claims ✓。课堂场景路由到该模型，对话/出题保持 4B 控成本。
    # 置空回退主通道。
    classroom_llm_model: str = ""  # 如 xop3qwen80bnext；空=用主通道模型
    classroom_llm_timeout: float = 90.0

    # -------------------- 引导解题通道（场景路由，对齐 classroom 通道模式） --------------------
    # socratic_* 场景（引导 plan/分步/判答/选项生成/总结）路由到该模型；空=回退主通道。
    # 环境变量 GUIDED_SOLVE_MODEL（.env 已配置 xop3qwen80bnext）；对话/出题保持 4B 控成本。
    guided_solve_model: str = ""

    # -------------------- 火山方舟通道（豆包；独立供应商 = 真冗余 + 出题提速） --------------------
    # 强场景（socratic_*/smart_quiz*/qa_rag）首选；实测 doubao-seed-2.0-mini 关思考
    # 1.5~2.5s 出整道契约合规选择题（讯飞 235B 17.5s / 4B 70s）。
    # 独立供应商也修复了"主备同源"结构缺陷：讯飞账号级故障时本通道仍可用。
    ark_api_key: str = ""
    ark_base_url: str = ""  # 如 https://ark.cn-beijing.volces.com/api/plan/v3/chat/completions
    ark_model: str = ""  # 主模型（引导解题/主对话）：如 doubao-seed-2.0-lite
    ark_model_mini: str = ""  # 出题链专用（更快）：如 doubao-seed-2.0-mini

    # -------------------- DeepSeek（备用通道） --------------------
    deepseek_api_key: str = ""
    deepseek_model: str = "deepseek-v4-flash"
    deepseek_thinking: bool = False  # ADR-001-8: 聊天场景默认关思考
    deepseek_base_url: str = "https://api.xiaomimimo.com/v1/chat/completions"
    # 流式对话专用模型（可选）：主模型流式通道故障/限流时把 SSE 对话切到它，
    # 非流式（课堂生成等）仍用主模型；输出上限自动钳制（如 glm-4v-flash ≤1024）。
    deepseek_stream_model: str = ""

    # -------------------- MiMo 多模态拍题识别 --------------------
    # 与 deepseek_api_key/base_url 共用小米 MiMo OpenAI 兼容通道；切勿填 pro，
    # pro 是文本模型，图片识别须使用原生全模态 mimo-v2.5。
    mimo_vision_model: str = "mimo-v2.5"

    # -------------------- 视觉模型（GeoGebra 动态图形 BYOK 通道） --------------------
    # 可选：配置任一支持图片输入的 OpenAI 兼容多模态模型（如 Spark4.0-Ultra / Qwen-VL / GPT-4o）。
    # 配置后，错题本"生成动态图形"会把上传原图直接发给视觉模型"看图生成 GGB"（MathMover 同款内核）；
    # 未配置时降级为"星火 wf_doc_understand 读图 + 文本模型生成"。
    vision_base_url: str = ""
    vision_api_key: str = ""
    vision_model: str = ""

    # -------------------- OpenMAIC 双师课堂联动 --------------------
    # 独立部署的 OpenMAIC 子应用源（若启用，学生端 /dual 以 iframe 嵌入其 /classroom/{stage_id}）。
    openmaic_enabled: bool = False
    openmaic_public_base_url: str = "http://localhost:3000"
    # 课堂 GeoGebra 交互图（对齐 OpenMAIC 哲学：默认关闭）——
    # ggb 依赖外网 CDN（geogebra.org 渲染器）且每页多一次 4000-token LLM 调用，
    # 默认走自绘图形（MathFigure3D 受控场景/确定性构造器，快且离线可用）。
    classroom_enable_ggb: bool = False

    # -------------------- Embedding / Reranker --------------------
    embedding_base_url: str = "http://localhost:8080"
    reranker_base_url: str = "http://localhost:8081"
    embedding_provider: str = "local"  # local（本地服务）/ 远程 OpenAI 兼容服务
    embedding_api_key: str = ""  # 远程 embedding 服务凭证（local 时留空）
    embedding_model: str = "bge-m3"

    # -------------------- RAG 阈值 --------------------
    rag_trgm_threshold: float = 0.08  # word_similarity 召回下限（短查询 vs 长文档）
    # 降级路径拒答闸门（top 原始分低于此值 → 拒答）。
    # 2026-08-07 按 BGE-M3 全量 105 切片实测校准：书外查询 top 原始分 ≤0.43，
    # 书内查询 ≥0.48（典型 0.58~0.70），取 0.45 落分隔区间（原 0.15 为部分切片
    # 缺向量时期的手感值，向量全覆盖后过松，书外问题会漏闸）。
    rag_raw_threshold: float = 0.45
    rag_trgm_gate: float = 0.35  # trgm 路拒答闸门（wsim 量纲；精确文本命中典型 0.5+，书外查询通常无召回）
    rag_refuse_threshold: float = 0.35  # reranker 生效时的拒答闸门

    # -------------------- 云知识库（管理后台可热更新，env 为兜底层） --------------------
    cloud_kb_enabled: bool = False  # 总开关
    cloud_kb_provider: str = ""  # tencent_lkeap / aliyun_bailian / 空=未配置
    cloud_kb_config: str = "{}"  # JSON 兜底：provider 专属凭证（credentials/knowledge_base_id 等）
    cloud_kb_top_k: int = 5  # 检索召回条数
    cloud_kb_score_threshold: float = 0.5  # 相关性分数下限

    # -------------------- 对象存储（ADR-M2B-001：boto3 统一抽象，支持自定义端口） --------------------
    storage_provider: str = "cos"  # cos / oss / minio / s3_custom
    storage_scheme: str = "https"  # http / https
    storage_host: str = ""  # 如 cos.ap-guangzhou.myqcloud.com；空则按 provider+region 推导
    storage_port: int | None = None  # 自定义端口（私有部署/MinIO 常用）
    storage_endpoint_url: str = ""  # 完整 endpoint，给出时优先于 scheme/host/port 拼接
    storage_region: str = ""  # 如 ap-guangzhou / cn-hangzhou
    storage_bucket: str = ""  # COS 需带 -APPID 后缀
    storage_access_key: str = ""
    storage_secret_key: str = ""
    storage_session_token: str = ""  # 可选，STS 临时凭证
    storage_presign_expires: int = 900  # 预签名 URL 有效期（秒）

    # -------------------- 星辰工作流（ADR-M2B-004：全配置化可移植） --------------------
    xingchen_enabled: bool = False  # 总开关
    xingchen_base_url: str = "https://xingchen-api.xf-yun.com"  # 可指私有化部署（含端口）
    xingchen_api_key: str = ""
    xingchen_api_secret: str = ""
    xingchen_flow_ids: str = "{}"  # JSON：flow 注册名 -> flow_id
    xingchen_timeouts: str = "{}"  # JSON：flow 注册名 -> read 超时秒（缺省用注册表默认）
    xingchen_max_concurrency: int = 3  # 全局并发信号量（星辰 QPS 1~3 硬约束）
    xingchen_queue_max: int = 10  # 排队上限，超过直接走本地降级
    xingchen_daily_alert_threshold: int = 1200  # 日调用量告警阈值（账号周期 1500 预留 20%）

    # -------------------- 讯飞语音（IAT 听写 + 长语音转写） --------------------
    xfyun_app_id: str = ""
    xfyun_api_key: str = ""  # IAT 听写
    xfyun_api_secret: str = ""  # IAT 听写签名
    xfyun_lfasr_secret_key: str = ""  # 长语音转写（F9 预处理）
    xfyun_iat_base_url: str = "https://iat-api.xfyun.cn/v2/iat"
    xfyun_lfasr_base_url: str = "https://raasr.xfyun.cn/v2/api"

    def vision_channel(self) -> tuple[str, str, str] | None:
        """视觉通道解析：优先 VISION_* BYOK，回退旧 deepseek 搭载。

        **2026-09-11 更正**：本方法旧 docstring 曾写「讯飞 MaaS 目录全为文本模型
        （实测 13 个无 VL）」，该结论已过时且错误 —— 讯飞 MaaS 现提供可用视觉
        模型 ``xophunyuanocr``（实测 HTTP 200，prompt_tokens=272，证明图片被
        真实编码送入）。当时的失败实为测试图像不合法（0 字节 / 1×1，上游
        10045 / 10042 拒绝），非模型不可用。当前生产 ``.env`` 即以
        ``VISION_*`` 指向该模型，故上述旧注释与本方法第一分支自相矛盾。

        上游硬约束（实测）：图片单边须在 ``[50, 6000]`` px。

        需独立视觉供应商时配 VISION_BASE_URL / VISION_API_KEY / VISION_MODEL
        （OpenAI 兼容）。无 VISION_* 时回退 deepseek 通道 + mimo_vision_model。

        证据：``20260911-math-ai-platform-vnext/M00/MODEL-CONNECTIVITY-MATRIX.md``
        （A 级外呼实测）；见 P00 FINAL-GATE §6.3#18。
        """
        if self.vision_base_url and self.vision_api_key and self.vision_model:
            return self.vision_base_url, self.vision_api_key, self.vision_model
        if self.deepseek_api_key and self.deepseek_base_url:
            return self.deepseek_base_url, self.deepseek_api_key, self.mimo_vision_model
        return None

    # -------------------- 联网搜索试点（默认关闭） --------------------
    web_search_enabled: bool = False

    # -------------------- 学情增长（M2 迭代16 第二批） --------------------
    # AI 管家化：永久开启 LLM 文案润色（任何异常/超时自动回退模板，零风险）
    growth_llm_polish: bool = True  # 鼓励语/错因文案 LLM 润色开关；关闭走模板，开启后异常/超时自动回退模板

    # -------------------- AI 管家（Butler Orchestrator，M2 迭代17） --------------------
    butler_llm_enabled: bool = True  # 管家 LLM 生成总开关（文案/推荐/路径）；关闭时全部走规则兜底
    butler_proactive_limit: int = 3  # 每日主动推送上限（防骚扰，today-3 + 复习提醒 + 鼓励）
    butler_dedup_hours: int = 8  # 同类事件去重窗口（小时），窗口内重复事件只推一次
    butler_polish_timeout_s: int = 10  # 管家 LLM 文案单次超时（秒），超时回退模板
    butler_error_detail_timeout_s: int = 45  # 错题正解生成专属超时（一次性+持久缓存，预算给足防模板中毒）
    butler_polish_cache_ttl_s: int = 24 * 3600  # 管家文案缓存 TTL（秒）

    # -------------------- Butler Kernel v2（阶段 3C，默认关闭，保持旧内核） --------------------
    # enabled：仅"已迁移且有工具支持"的场景走 v2，未迁移场景继续旧内核；
    # shadow：v2 只做无副作用影子运行（WRITE/EXTERNAL 一律 shadow_skipped），结果不返回用户；
    # 真实领域工具迁移完成前，两个开关都应保持 false。
    # stu-r2 管线通电（2026-10-11）：student.chat 已迁移——enabled=true 时，
    #   ①主对话数据类意图（学情/记忆曲线）经 app/butler/chat_bridge 确定性 fixed_plan
    #   进 ButlerRuntime 真实调工具并落 agent_runs/tool_invocations 账本；
    #   ②POST /api/butler/chat 走 v2 内核。生产环境须显式设 BUTLER_V2_ENABLED=true，
    #   否则通电目标静默失效（不报错，回退旧直查链路）。测试环境由 conftest 钉回 false。
    butler_v2_enabled: bool = False
    butler_v2_shadow: bool = False

    # 【已隔离】上一代科研端（论文阅读与写作平台）专属配置于 2026-09-17 随该域一并移出：
    # research_enabled / research_compile_bin / research_web_search_enabled /
    # research_kb_top_k / research_chunk_max_chars / bocha_api_key。
    # 详见 D:\research-legacy-quarantine\README.md。当前科研端配置见下方「科研端网关」块。

    # -------------------- 科研端网关（RV16 / GATE80） --------------------
    # 8000 是唯一权威：本服务校验 JWT 后派生 AccessContext 并 HMAC 注入，转发到 8300。
    # 密钥缺失 ⇒ fail closed（503 ACCESS_CONTEXT_MISSING），绝不伪造无签名上下文。
    research_gateway_hmac_secret: str = ""
    research_vnext_base_url: str = "http://127.0.0.1:8300"
    # 平台租户 ID：空则由 uuid5 稳定派生（见 research_gateway._platform_tenant_id）。
    research_gateway_tenant_id: str = ""
    research_gateway_connect_timeout: float = 10.0
    research_gateway_read_timeout: float = 300.0
    # A 方案 · 瞬时下游故障的短重试（见 gateway/research_gateway.py 顶部说明）：
    # 只对安全请求（GET/HEAD/OPTIONS 或带 §46 幂等键的写请求）生效。
    research_gateway_retry_attempts: int = 2
    research_gateway_retry_backoff: float = 0.25

    # -------------------- Feature Profile（阶段 1：M2/M4 路由面隔离） --------------------
    # 控制 F14（wf_verify_derivation）在**管理后台工作流列表**中是否可见
    # （见 gateway/admin_router.py 的 _M2_EXCLUDED_WORKFLOWS）。
    # 注：F14 的 HTTP 试点路由（旧 /api/research/*）已于 2026-09-17 隔离移出，
    # 但该 workflow 注册在 providers/xingchen.py 的 FLOW_REGISTRY（butler 域），故本开关仍有意义。
    m2_enable_research: bool = False

    # -------------------- M3 教师端（默认关闭，不干扰 M2） --------------------
    # M3_ENABLE_TEACHER=false：默认不挂载 /api/teacher/* 路由；测试 profile 可显式开启。
    m3_enable_teacher: bool = False

    # -------------------- SymPy 沙箱 --------------------
    sandbox_timeout_ms: int = 10000
    sandbox_mem_limit_mb: int = 256
    sandbox_cpu_limit_s: int = 10

    # -------------------- 服务间工具密钥（/tools/* 与运维端点） --------------------
    tool_api_key: str = ""  # X-Tool-Key；空则 /tools/* 全部 50301（未配置）

    # -------------------- 并发与限流（A9 配置化） --------------------
    sse_global_concurrency: int = 20  # SSE 全局并发上限
    sse_user_concurrency: int = 3  # SSE 每用户并发上限
    upload_rate_limit_per_hour: int = 10  # 每用户上传限流
    asr_token_rate_limit_per_hour: int = 20  # 每用户 asr-token 限流
    student_daily_practice_limit: int = 30  # 学生日刷题上限（防沉迷）

    # -------------------- JWT --------------------
    jwt_secret: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expire_days: int = 7
    auth_access_token_minutes: int = 15
    auth_allow_legacy_tokens: bool = True
    auth_sms_provider: str = "demo"
    # 比赛演示默认不审核教师/科研人员；设为 true 可恢复既有审核状态机。
    auth_professional_review_enabled: bool = False
    # 开发环境演示短信白名单；留空 = 任意手机号均可走演示通道（生产环境 demo 整体被拒）
    auth_demo_sms_allowlist: str = ""
    auth_otp_pepper: str = "development-only-otp-pepper"
    auth_refresh_token_pepper: str = "development-only-refresh-token-pepper"
    auth_invite_pepper: str = "development-only-invite-pepper"
    auth_standard_absolute_hours: int = 168
    auth_standard_idle_minutes: int = 1440
    auth_remember_absolute_hours: int = 720
    auth_remember_idle_minutes: int = 10080
    auth_admin_absolute_hours: int = 12
    auth_admin_idle_minutes: int = 30
    auth_trusted_proxies: str = ""
    auth_captcha_provider: str = "disabled"
    auth_break_glass_enabled: bool = False
    auth_trusted_operations_env: bool = False
    auth_break_glass_secret: str = ""
    tencent_sms_secret_id: str = ""
    tencent_sms_secret_key: str = ""
    tencent_sms_sdk_app_id: str = ""
    tencent_sms_sign_name: str = ""
    tencent_sms_template_id: str = ""
    tencent_sms_region: str = "ap-guangzhou"
    tencent_sms_template_params: str = '["{code}"]'

    # -------------------- 应用 --------------------
    app_env: str = "development"  # development / staging / production
    dev_sms_code: str = "123456"  # 开发环境固定验证码
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"  # 逗号分隔

    # -------------------- 管理后台 --------------------
    admin_phones: str = ""  # 逗号分隔手机号；验证码登录命中且无 admin 绑定时自动绑定 admin 角色

    @property
    def admin_phone_list(self) -> list[str]:
        """ADMIN_PHONES 逗号分隔解析（空白项忽略）"""
        return [p.strip() for p in self.admin_phones.split(",") if p.strip()]

    @property
    def auth_demo_sms_phone_list(self) -> list[str]:
        return [p.strip() for p in self.auth_demo_sms_allowlist.split(",") if p.strip()]

    @property
    def tencent_sms_template_param_list(self) -> list[str] | None:
        import json

        try:
            params = json.loads(self.tencent_sms_template_params)
        except (TypeError, ValueError):
            return None
        return params if isinstance(params, list) and all(isinstance(param, str) for param in params) else None

    @property
    def cloud_kb_config_map(self) -> dict:
        """CLOUD_KB_CONFIG JSON 解析（非法 JSON 视为空配置）"""
        import json

        try:
            data = json.loads(self.cloud_kb_config or "{}")
            return dict(data) if isinstance(data, dict) else {}
        except (ValueError, TypeError):
            return {}

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def xingchen_flow_id_map(self) -> dict:
        """XINGCHEN_FLOW_IDS JSON 解析（非法 JSON 视为空配置）

        值支持两种形态：
        - str：纯 flow_id（沿用全局 XINGCHEN_API_KEY/SECRET）
        - dict：{"flow_id", "api_key", "api_secret"} 按流凭证覆盖（多应用绑定场景，迭代18）
        """
        import json

        try:
            data = json.loads(self.xingchen_flow_ids or "{}")
            return {str(k): v for k, v in data.items()} if isinstance(data, dict) else {}
        except (ValueError, TypeError):
            return {}

    @property
    def xingchen_timeout_map(self) -> dict[str, float]:
        """XINGCHEN_TIMEOUTS JSON 解析（非法 JSON 视为空配置）"""
        import json

        try:
            data = json.loads(self.xingchen_timeouts or "{}")
            return {str(k): float(v) for k, v in data.items()} if isinstance(data, dict) else {}
        except (ValueError, TypeError):
            return {}

    @property
    def storage_endpoint(self) -> str:
        """对象存储 endpoint 推导：显式 ENDPOINT_URL > scheme/host/port 拼接 > provider+region 默认"""
        if self.storage_endpoint_url:
            return self.storage_endpoint_url
        if self.storage_host:
            port = f":{self.storage_port}" if self.storage_port else ""
            return f"{self.storage_scheme}://{self.storage_host}{port}"
        # 按 provider 推导云厂商默认 endpoint
        if self.storage_provider == "cos" and self.storage_region:
            return f"https://cos.{self.storage_region}.myqcloud.com"
        if self.storage_provider == "oss" and self.storage_region:
            return f"https://s3.oss-{self.storage_region}.aliyuncs.com"
        return ""

    def validate_production(self) -> None:
        """生产环境启动前校验，不安全配置直接拒绝启动"""
        if self.app_env == "production" and self.jwt_secret in (
            "change-me-in-production",
            "change-me-to-a-random-secret-in-production",
            "",
        ):
            raise RuntimeError("生产环境必须配置强随机 JWT_SECRET")
        if self.app_env == "production":
            unsafe = {
                "AUTH_OTP_PEPPER": self.auth_otp_pepper,
                "AUTH_REFRESH_TOKEN_PEPPER": self.auth_refresh_token_pepper,
                "AUTH_INVITE_PEPPER": self.auth_invite_pepper,
            }
            for name, value in unsafe.items():
                if (
                    len(value) < 32
                    or value.startswith("development-only-")
                    or value.startswith("replace-with-")
                ):
                    raise RuntimeError(f"生产环境必须配置强随机 {name}")
            if self.auth_sms_provider not in {"demo", "tencent"}:
                raise RuntimeError("生产环境 AUTH_SMS_PROVIDER 不受支持")
            if self.auth_sms_provider == "demo":
                raise RuntimeError("生产环境禁止使用 demo 短信 provider")
            required_tencent_settings = {
                "TENCENT_SMS_SECRET_ID": self.tencent_sms_secret_id,
                "TENCENT_SMS_SECRET_KEY": self.tencent_sms_secret_key,
                "TENCENT_SMS_SDK_APP_ID": self.tencent_sms_sdk_app_id,
                "TENCENT_SMS_SIGN_NAME": self.tencent_sms_sign_name,
                "TENCENT_SMS_TEMPLATE_ID": self.tencent_sms_template_id,
                "TENCENT_SMS_REGION": self.tencent_sms_region,
            }
            for name, value in required_tencent_settings.items():
                if not value.strip():
                    raise RuntimeError(f"生产环境必须配置 {name}")
            template_params = self.tencent_sms_template_param_list
            if not template_params or not any("{code}" in param for param in template_params):
                raise RuntimeError("生产环境必须配置包含 {code} 的 TENCENT_SMS_TEMPLATE_PARAMS")

    model_config = {"env_file": str(_ENV_FILE), "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()
