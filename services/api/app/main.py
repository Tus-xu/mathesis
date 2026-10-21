"""FastAPI 应用入口

只做 app 装配，禁止写业务逻辑（分层铁律 §7.0）。
"""

import asyncio
import selectors
import sys
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

# —— Windows + psycopg3 Async 兼容性：默认 ProactorEventLoop 不支持 psycopg 异步；
#    在任何 async 资源创建之前全局把 EventLoop policy 换成 WindowsSelectorEventLoopPolicy。
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# Windows 控制台默认 GBK：日志含 U+2212（−）等 GBK 不支持的字符时
# print/structlog 会抛 UnicodeEncodeError 并中断请求链路（曾致 RAG 检索静默失败）。
# 启动时统一改为 UTF-8 + replace 兜底；reconfigure 就地生效，已持有引用的 handler 同样受益。
if sys.platform == "win32":
    for _stream in (sys.stdout, sys.stderr):
        with suppress(AttributeError, ValueError):
            _stream.reconfigure(encoding="utf-8", errors="replace")

import structlog
from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import settings
from app.domains.classroom.course_router import router as course_router
from app.domains.classroom.courseware_router import (
    student_router as classroom_courseware_student_router,
)
from app.domains.classroom.courseware_router import (
    teacher_router as classroom_courseware_teacher_router,
)
from app.domains.classroom.router import router as classroom_router
from app.domains.classroom.stage_router import router as classroom_stage_router
from app.domains.files.router import router as files_router
from app.domains.identity.admin_router import router as identity_admin_router
from app.domains.identity.router import profile_router as identity_profile_router
from app.domains.identity.router import router as identity_auth_router
from app.domains.model_config.router import router as model_config_router
from app.domains.ops.router import router as ops_router
from app.gateway.admin_router import router as admin_router
from app.gateway.agent_router import router as agent_router
from app.gateway.auth_router import router as auth_router
from app.gateway.butler_router import router as butler_router
from app.gateway.task_router import notifications_router, tasks_router
from app.gateway.class_ext_router import router as class_ext_router
from app.gateway.exam_router import router as exam_router
from app.gateway.figures_router import router as figures_router
from app.gateway.growth_router import agent_ext_router as growth_agent_ext_router
from app.gateway.growth_router import router as growth_router
from app.gateway.integration_router import router as integration_router
from app.gateway.kb_router import router as kb_router
from app.gateway.ops_ext_router import router as ops_ext_router
from app.gateway.redis import close_redis
from app.gateway.research_gateway import router as research_gateway_router
from app.gateway.search_router import router as search_router
from app.gateway.speech_router import router as speech_router
from app.gateway.student_router import router as student_router
from app.gateway.tools_router import router as tools_router
from app.gateway.v1_compat_router import router as v1_compat_router
from app.gateway.video_router import router as video_router
from app.models.database import async_session_factory
from app.providers import get_deepseek, get_spark
from app.providers.embedding import EmbeddingProvider
from app.providers.http import close_http
from app.skills.registry import get_skill_registry, register_builtin_skills
from app.services import task_handlers  # noqa: F401  （import 副作用：注册任务处理器，阶段3）
from app.services import task_handlers_classroom  # noqa: F401  （S-B3：classroom.session）
from app.services import task_handlers_socratic  # noqa: F401  （S-B4：socratic.autosolve）
from app.services import task_handlers_teacher  # noqa: F401  （T-B2/T-B3：teacher.quiz.create / teacher.materials）
from app.services import task_runner

logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """应用生命周期：启动/关闭钩子"""
    # 生产环境安全配置校验（弱 JWT 密钥等直接拒绝启动）
    settings.validate_production()
    logger.info("app.starting", env=settings.app_env)
    # 表结构由 Alembic 迁移管理，禁止 create_all 双轨
    # 注册内置 Skills
    register_builtin_skills()
    # 同步 Skills 到数据库（skill_runs 外键依赖）
    registry = get_skill_registry()
    async with async_session_factory() as session:
        await registry.sync_to_db(session)
        await session.commit()
    logger.info("app.skills_registered")
    # 后台任务中心：周期扫描自愈（进程重启遗留任务重新拉起，G-3）
    task_runner.start_resume_loop()
    yield
    logger.info("app.stopping")
    task_runner.stop_resume_loop()
    await close_http()
    await close_redis()


app = FastAPI(
    title="Math Arena API",
    description="数学垂类大模型 - 教学科研智能体平台",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS 配置：白名单制（凭证模式下禁止 "*"）
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-Id", "X-Idempotent-Replay"],
)


# om5 晨间修复：/api GET 响应全局禁缓存——浏览器对无 Cache-Control 的 GET 启发式缓存，
# 实测导致复习计划等接口在修复后仍返回旧数据（用户强刷前不可见）。
@app.middleware("http")
async def no_cache_api_responses(request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/api"):
        response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """422 统一为业务信封格式（§4.1）"""
    try:
        errors = jsonable_encoder(exc.errors())
    except Exception:
        errors = []
    return JSONResponse(
        status_code=422,
        content={
            "code": 40001,
            "message": "请求参数格式错误",
            "requestId": request.headers.get("X-Request-Id") or str(uuid.uuid4()),
            "data": {"errors": errors},
        },
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """HTTP 异常统一格式（429 等自定义 detail 直接透传）"""
    if isinstance(exc.detail, dict):
        return JSONResponse(status_code=exc.status_code, content=exc.detail, headers=exc.headers)
    return JSONResponse(
        status_code=exc.status_code,
        content={"code": exc.status_code * 100 + 1, "message": str(exc.detail)},
    )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """未捕获异常兜底：返回 JSON 信封，避免前端解析失败"""
    request_id = request.headers.get("X-Request-Id") or str(uuid.uuid4())
    logger.error("unhandled_exception", path=request.url.path, error=str(exc)[:500], request_id=request_id)
    return JSONResponse(
        status_code=500,
        content={
            "code": 50000,
            "message": "服务器内部错误，请稍后重试",
            "requestId": request_id,
            "data": None,
        },
    )


@app.get("/api/health")
async def health_check() -> dict[str, str]:
    return {"status": "ok", "version": "1.0.0"}


@app.get("/api/health/models")
async def model_health_check(request: Request) -> dict:
    """模型通道健康检查（支持 per-user 配置）

    携带 Bearer token 时按用户自定义配置探测，否则用全局 .env 配置。
    """
    import asyncio

    from app.providers.router import get_model_router_for_user

    # 尝试解析用户（可选认证，失败不报错）
    spark = get_spark()
    deepseek = get_deepseek()

    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        try:
            from app.gateway.jwt import decode_token

            payload = decode_token(auth_header[7:])
            user_id = payload.get("sub")
            if user_id:
                async with async_session_factory() as session:
                    user_router = await get_model_router_for_user(user_id, session)
                    spark = user_router._spark
                    deepseek = user_router._deepseek
        except Exception:
            pass  # token 无效时回退全局

    embedding = EmbeddingProvider()

    spark_result, deepseek_result, embedding_result = await asyncio.gather(
        spark.health_check(),
        deepseek.health_check(),
        embedding.health_check(),
    )

    return {
        "spark": spark_result,
        "deepseek": deepseek_result,
        "embedding": embedding_result,
    }


# ========== 注册路由 ==========

# M0-M1 既有路由
app.include_router(auth_router, prefix="/api/auth", tags=["认证"])
app.include_router(identity_auth_router, prefix="/api/auth", tags=["认证"])
app.include_router(identity_profile_router, prefix="/api/identity", tags=["身份"])
app.include_router(identity_admin_router, prefix="/api/admin/identity", tags=["身份审核"])
app.include_router(agent_router, prefix="/api/agent", tags=["智能体"])
app.include_router(classroom_router, prefix="/api/classes", tags=["班级"])
app.include_router(course_router)  # /api/courses/*（迭代05 阶段4：F9 双师课堂预处理管线）
app.include_router(classroom_stage_router)  # /api/classroom/*（AI 数学课堂：大纲→逐页内容两段式生成）
app.include_router(ops_router, prefix="/api/ops", tags=["运维"])
app.include_router(model_config_router, prefix="/api/model-config", tags=["模型配置"])
app.include_router(integration_router, prefix="/api/integrations", tags=["集成配置"])
app.include_router(admin_router, prefix="/api/admin", tags=["admin"])  # 管理后台配置

# M2 新增路由
app.include_router(files_router)  # /api/files/*（自带 prefix）
app.include_router(figures_router)  # /api/figures/*（GeoGebra 交互图形生成，自带 prefix）
app.include_router(speech_router)  # /api/agent/speech/*（自带 prefix）
app.include_router(search_router)  # /api/search/*（自带 prefix）
# M2 迭代16 学情增长聚合：置于 student_router 之前，
# 避免 /error-records/filter 等静态路径被 {record_id} 形式的路径参数抢占
app.include_router(growth_router)  # /api/student/growth|practice|error-records|report|knowledge-graph/*（自带 prefix）
app.include_router(growth_agent_ext_router)  # /api/agent/route-intent（自带 prefix）
app.include_router(class_ext_router)  # /api/classes/{id}/feed|hot-errors（迭代16 第二批模块7）
app.include_router(video_router)  # /api/student/videos（资源中心：B 站视频，自带 prefix）
app.include_router(student_router)  # /api/student/*（自带 prefix）
# /api/student/classroom-coursewares*（老师课堂课件列表/打开；default profile 也可用）
app.include_router(classroom_courseware_student_router)
app.include_router(exam_router)  # /api/student/exam/*（模拟试卷/专题训练，自带 prefix）
app.include_router(butler_router)  # /api/butler/*（AI 管家调度层，自带 prefix）
app.include_router(tasks_router)  # /api/tasks/*（后台任务中心，阶段3 G 系列，自带 prefix）
app.include_router(notifications_router)  # /api/notifications/*（站内通知，自带 prefix）
app.include_router(ops_ext_router)  # /api/ops/xingchen/*（自带 prefix）
app.include_router(tools_router)  # /tools/*（X-Tool-Key 鉴权，自带 prefix）
app.include_router(kb_router)  # /api/kb/*（迭代05：知识库试点五端点，teacher/researcher）
# /api/v1/*（学生端 B1/B2 集成服务下线后的能力归位：kp/guess、questions、kb/ingest-text）
app.include_router(v1_compat_router)
# /api/v1/kb/*（学生端知识库页面直传链路：upload/documents/delete/reparse，
# 2026-10-05 契约补齐——页面三端点在归位时被遗漏导致 404 假空态）
from app.gateway.student_kb_router import router as student_kb_router

app.include_router(student_kb_router)
# /api/trial/*（体验测试留痕：结构化反馈提交 + 使用档案聚合 + 评审导出）
from app.gateway.trial_router import router as trial_router

app.include_router(trial_router)
# RV16 / GATE80：科研端（8300）反向代理网关 —— 校验 JWT 后派生 AccessContext 并 HMAC 注入，
# 双前缀 /api/research-vnext/* + /api/research-v1/* 透传（含 SSE streaming）。加法式，不改既有路由。
app.include_router(research_gateway_router)
# 【已隔离】上一代科研端（论文阅读与写作平台）整域于 2026-09-17 移出：
#   · 前端对其调用数为 0、后端测试引用数为 0、旧域唯一 importer 是本文件 → 判定为僵尸代码。
#   · 现已隔离至 D:\research-legacy-quarantine\（含来源、证据、恢复方法）。
#   · 当前科研端 = 8300 `research-vnext` + 本文件下方的 research_gateway（双前缀透传）。

# M3 教师端按 feature profile 挂载（默认 m3_enable_teacher=False，不干扰 M2/学生端）。
# 教师端仅服务高中日常教学，不含科研评审/建模教练能力。
if settings.m3_enable_teacher:
    from app.domains.teacher.router import router as teacher_router
    from app.gateway.teacher_video_router import router as teacher_video_router

    app.include_router(teacher_router)  # /api/teacher/*（教师端，自带 prefix）
    # /api/teacher/videos（教师端资源中心：B 站备课视频）——迭代20 门禁修复：
    # 此前在 M2 段无条件挂载，默认 profile 的 OpenAPI 混入 /api/teacher/*，
    # 违反 test_m3_teacher_profile / test_m2_route_profile 配置门控契约；
    # 挪入 M3 门控后教师端开启时行为不变，默认 profile 不再泄漏教师路径。
    app.include_router(teacher_video_router)
    # /api/teacher/classroom-coursewares*（教师发布课堂课件 → 学生课堂 → 学情监控）
    app.include_router(classroom_courseware_teacher_router)
