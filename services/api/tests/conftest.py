"""Test公共配置和 fixtures

【隔离架构（迭代09+ 根治）】
- 测试强制使用独立测试库 test_math_arena（与开发库 math_arena 完全隔离），
  DATABASE_URL 环境变量必须在任何 app 模块 import 之前设置——
  因为 app/models/database.py 在 import 时即用 settings.database_url 创建 engine。
- pytest_sessionstart：确保 test_math_arena 库存在（含 pgvector 扩展）+ 全量建表（干净起点）。
- 每个测试后 _cleanup_pb_test_data 清理测试命名空间数据（防 test 库膨胀）。
- 开发库（math_arena）从此零污染：真实知识库/题库数据不会被测试 fixture 触碰。

用法：
    cd services/api
    python -m pytest tests/ -q
"""

import os


def _test_db_config() -> dict:
    """推导测试库连接参数。

    优先级：外部 DATABASE_URL 的 host/port/凭证（CI service 映射 5432 时注入）
    → 本地默认 localhost:54329（deploy/docker-compose.yml 的宿主映射）。
    库名始终强制 test_math_arena（与开发库 math_arena 隔离的不变量，见文件头）。
    """
    from urllib.parse import urlparse

    raw = os.environ.get("DATABASE_URL") or ""
    parsed = urlparse(raw if "://" in raw else "postgresql://postgres:postgres@localhost:54329")
    return {
        "host": parsed.hostname or "localhost",
        "port": parsed.port or 54329,
        "user": parsed.username or "postgres",
        "password": parsed.password or "postgres",
    }


TEST_DB = _test_db_config()
TEST_DB_NAME = "test_math_arena"

# ⚠️ 必须最先执行（任何 app.* import 之前）：覆盖为独立测试库
os.environ["DATABASE_URL"] = (
    f"postgresql+asyncpg://{TEST_DB['user']}:{TEST_DB['password']}"
    f"@{TEST_DB['host']}:{TEST_DB['port']}/{TEST_DB_NAME}"
)
# M3 教师端在测试 profile 显式开启（默认生产关闭，见 app/config.py m3_enable_teacher）。
# 默认关闭的契约由子进程测试证明（tests/test_m3_teacher_profile.py）。
os.environ["M3_ENABLE_TEACHER"] = "true"

# 【迭代20 门禁修复】本地开发 .env（monorepo 根）会覆盖若干**代码默认值**，让单元
# 测试在开发机上与 CI（无 .env）行为不一致。这里把已证实污染测试的变量钉回代码
# 默认（pydantic-settings 中 os.environ 优先于 dotenv 文件），恢复 CI 等价环境：
# - BUTLER_V2_ENABLED：dev 临时开 v2（.env 自带注释说明 CI/测试保持 false），
#   泄漏会打挂 test_butler_compat_v2::test_v2_enabled_defaults_are_false；
# - EMBEDDING_PROVIDER：dev 用 aliyun，泄漏使 EmbeddingProvider 无参构造不再是
#   「env 本地 BGE-M3」红线（test_embedding_provider TestLocalProvider 全族）；
#   EMBEDDING_MODEL 同理（dev=xop3qwen8bembedding，代码默认 local→bge-m3）；
# - ARK_API_KEY：dev 配了火山方舟真钥，scene=chat 会在单测里走**真实外呼**
#   （test_providers TestModelRouter / test_m2_fixes TestThinkingOverride）；
# - VISION_BASE_URL/_API_KEY/_MODEL：dev 配了 VISION_* BYOK，压过
#   vision_channel() 的 deepseek+mimo_vision_model 回退分支
#   （test_iter05_files 两处断言 payload model == mimo_vision_model）。
# 只钉「已证实改变单测行为」的变量；课堂/引导解题等真实 LLM 验收测试不受影响。
os.environ["BUTLER_V2_ENABLED"] = "false"
os.environ["EMBEDDING_PROVIDER"] = "local"
os.environ["EMBEDDING_MODEL"] = ""
os.environ["ARK_API_KEY"] = ""
os.environ["VISION_BASE_URL"] = ""
os.environ["VISION_API_KEY"] = ""
os.environ["VISION_MODEL"] = ""

import contextlib
from pathlib import Path

import pytest

from app.providers.http import close_http
from app.skills.registry import register_builtin_skills

# ASGITransport 不执行 lifespan，内置 skills 需在测试会话中显式注册
register_builtin_skills()


_TEST_DATABASE = TEST_DB_NAME


async def _require_test_database(connection) -> None:
    """拒绝在非专用测试库上执行破坏性 schema 操作。"""
    database_name = await connection.fetchval("SELECT current_database()")
    if database_name != _TEST_DATABASE:
        raise RuntimeError(
            f"refusing destructive test schema reset outside {_TEST_DATABASE}: {database_name!r}"
        )


def _ensure_test_db() -> None:
    """确保专用测试数据库存在（幂等）。"""
    import asyncio

    import asyncpg

    async def _run():
        admin = await asyncpg.connect(database="postgres", **TEST_DB)
        try:
            exists = await admin.fetchval(
                "SELECT 1 FROM pg_database WHERE datname=$1", _TEST_DATABASE
            )
            if not exists:
                await admin.execute(f"CREATE DATABASE {_TEST_DATABASE}")
                print(f"[conftest] 已创建测试库 {_TEST_DATABASE}")
        finally:
            await admin.close()

    asyncio.run(_run())


def _reset_test_schema() -> None:
    """重建专用测试库的 public schema，兼容 SQLAlchemy 未映射的遗留表。"""
    import asyncio

    import asyncpg

    async def _run():
        target = await asyncpg.connect(database=_TEST_DATABASE, **TEST_DB)
        try:
            await _require_test_database(target)
            await target.execute("DROP SCHEMA public CASCADE")
            await target.execute("CREATE SCHEMA public AUTHORIZATION postgres")
            await target.execute("GRANT ALL ON SCHEMA public TO postgres")
            await target.execute("GRANT ALL ON SCHEMA public TO public")
            # pgvector 向量类型 + pg_trgm（RAG trgm 召回路依赖 word_similarity 函数）
            await target.execute("CREATE EXTENSION IF NOT EXISTS vector")
            await target.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
        finally:
            await target.close()

    asyncio.run(_run())


def _parse_pg_array(literal: str) -> list[str]:
    """解析 Postgres 数组字面量为 Python list。

    导出 CSV 的 aliases 列形如 ``{a,b,c}`` / ``{"a,b"}`` / ``{}``；
    元素含逗号/花括号时被双引号包裹，``""`` 表示字面双引号。
    """
    s = (literal or "").strip()
    if not s.startswith("{") or not s.endswith("}"):
        return []
    inner = s[1:-1]
    if not inner:
        return []
    out: list[str] = []
    i, n = 0, len(inner)
    while i < n:
        if inner[i] == '"':
            # 带引号元素：允许含逗号/花括号，"" 转义为字面 "
            i += 1
            buf: list[str] = []
            while i < n:
                if inner[i] == '"':
                    if i + 1 < n and inner[i + 1] == '"':
                        buf.append('"')
                        i += 2
                        continue
                    i += 1
                    break
                buf.append(inner[i])
                i += 1
            out.append("".join(buf))
            if i < n and inner[i] == ",":
                i += 1
        else:
            j = inner.find(",", i)
            if j < 0:
                out.append(inner[i:])
                break
            out.append(inner[i:j])
            i = j + 1
    return out


def _load_kp_fixture() -> list[dict]:
    """读取内置 KP 白名单夹具（tests/fixtures/kp_whitelist.csv）。

    数据来源：旧仓库 exports/20260914/math_arena_csv/knowledge_points.csv
    （301 个 MATH-PEP-* 正式知识点，含 5 册人教A版2019 教材树）。
    列序：id,code,name,parent_id,grade,aliases,created_at,updated_at。
    """
    import csv

    path = Path(__file__).parent / "fixtures" / "kp_whitelist.csv"
    if not path.exists():
        return []
    rows: list[dict] = []
    with path.open(encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            rows.append(
                {
                    "id": r["id"],
                    "parent_id": r["parent_id"] or None,
                    "grade": r["grade"] or None,
                    "code": r["code"],
                    "name": r["name"],
                    "aliases": _parse_pg_array(r["aliases"]),
                }
            )
    return rows


async def _insert_kp_rows(tgt, rows: list) -> int:
    """按树深排序插入 KP 行（父先子后，避免 FK 违例炸掉整批）。"""
    by_id = {r["id"]: r for r in rows}
    depth: dict = {}

    def _depth(r) -> int:
        if r["id"] in depth:
            return depth[r["id"]]
        d = 0
        p = r["parent_id"]
        while p is not None and p in by_id:
            d += 1
            p = by_id[p]["parent_id"]
        depth[r["id"]] = d
        return d

    copied = 0
    for r in sorted(rows, key=_depth):
        try:
            await tgt.execute(
                "INSERT INTO knowledge_points (id, parent_id, grade, code, name, aliases, created_at, updated_at) "
                "VALUES ($1,$2,$3,$4,$5,$6, now(), now()) ON CONFLICT (code) DO NOTHING",
                r["id"],
                r["parent_id"],
                r["grade"],
                r["code"],
                r["name"],
                r["aliases"],
            )
            copied += 1
        except Exception as e:  # 单行失败不炸整批，但必须显式暴露
            print(f"[conftest] kp 种子单行失败 code={r['code']}: {e!r}")
    return copied


def _seed_kp_whitelist() -> None:
    """把真实 MATH 知识点树复制进测试库（kp 白名单种子）。

    KB import 校验 kp_codes ⊆ knowledge_points；RAG/图谱/管家下单测试依赖真实 kp 树
    （如 butler 的 kp_code="函数" 需命中别名、KB import 需 MATH-PEP-BIXI1-*）。
    仅复制 knowledge_points 小表，不复制 chunks/question_bank（测试自建）。

    数据源优先级：
    1. 开发库 math_arena（实时事实源，开发机可用）；
    2. 内置夹具 tests/fixtures/kp_whitelist.csv（CI 只有空 PG service 时使用）。
    两级都不可用时才跳过——CI  hermetic 不再依赖开发库是否存活。
    """
    import asyncio

    import asyncpg

    async def _run():
        try:
            dev = await asyncpg.connect(database="math_arena", **TEST_DB)
        except Exception as e:
            # CI 只有 service 起的空 PG，没有开发库 math_arena——回退到内置夹具，
            # 而不是让 sessionstart 整体失败（建表已在前一步完成）
            rows = _load_kp_fixture()
            if not rows:
                print(f"[conftest] 开发库不可达且无内置夹具，跳过 kp 白名单种子: {e!r}")
                return
            tgt = await asyncpg.connect(database=TEST_DB_NAME, **TEST_DB)
            try:
                copied = await _insert_kp_rows(tgt, rows)
                print(
                    f"[conftest] 开发库 math_arena 不可达（{e!r}），"
                    f"已用内置夹具复制 {copied}/{len(rows)} 个知识点"
                )
            finally:
                await tgt.close()
            return
        tgt = await asyncpg.connect(database=TEST_DB_NAME, **TEST_DB)
        try:
            rows = await dev.fetch(
                "SELECT id, parent_id, grade, code, name, aliases FROM knowledge_points WHERE code LIKE 'MATH-%'"
            )
            copied = await _insert_kp_rows(tgt, [dict(r) for r in rows])
            print(f"[conftest] 已从开发库复制 {copied}/{len(rows)} 个真实知识点到测试库")
        finally:
            await dev.close()
            await tgt.close()

    asyncio.run(_run())

    asyncio.run(_run())


def pytest_sessionstart(session) -> None:  # noqa: ARG001
    """会话开始：确保测试库存在 + 建表 + 种子（幂等）"""
    _ensure_test_db()
    _reset_test_schema()
    try:
        import asyncio

        from sqlalchemy import text

        from app.models import Base
        from app.models.database import engine

        async def _init() -> int:
            """全量重建（干净起点）并核对表数量。

            初始化静默失败会让整个会话以"缺表/缺列"形式级联失败
            （见 docs/audits/2026-08-22-unified-authentication-verification.md
            全仓测试诊断一节），必须显式暴露而非吞掉。
            """
            last_error: Exception | None = None
            for _attempt in (1, 2):  # 容器刚启动等瞬态失败重试一次
                try:
                    async with engine.begin() as conn:
                        database_name = await conn.scalar(text("SELECT current_database()"))
                        if database_name != _TEST_DATABASE:
                            raise RuntimeError(
                                f"refusing test schema initialization outside {_TEST_DATABASE}: {database_name!r}"
                            )
                        await conn.run_sync(Base.metadata.drop_all)
                        await conn.run_sync(Base.metadata.create_all)
                    async with engine.connect() as conn:
                        return int(
                            await conn.scalar(
                                text(
                                    "SELECT count(*) FROM information_schema.tables "
                                    "WHERE table_schema = 'public'"
                                )
                            )
                        )
                except Exception as exc:  # noqa: PERF203
                    last_error = exc
                    await engine.dispose()
            assert last_error is not None
            raise last_error

        expected = len(Base.metadata.tables)
        actual = asyncio.run(_init())
        print(f"[conftest] 测试库表结构已重建（干净起点）：{actual}/{expected} 张表")
        if actual != expected:
            print(
                f"[conftest] ⚠️ 表数量不符（期望 {expected}，实际 {actual}）："
                "建表未完整执行，依赖库表的测试将级联失败。"
                "请检查是否有并发 pytest 进程共用 test_math_arena，或共享库残留旧 schema。"
            )
        _seed_kp_whitelist()
    except Exception as e:  # 不阻断测试收集，报错信息后续体现
        print(f"[conftest] 测试库初始化失败（依赖库表的测试将失败，纯单测不受影响）: {e!r}")


@pytest.fixture(autouse=True)
async def _reset_singletons():
    """每个测试前后重置全局单例，避免连接池跨循环冲突"""
    import app.gateway.redis as redis_mod

    # 测试前：重置 Redis 连接
    if redis_mod._redis_client is not None:
        with contextlib.suppress(Exception):
            await redis_mod.close_redis()
        redis_mod._redis_client = None

    # 测试前：关闭跨循环的 httpx 客户端（下次使用时按当前循环重建）
    with contextlib.suppress(Exception):
        await close_http()
    # 测试前：清空应用 DB 连接池——池内连接绑定的是上一个（已关闭的）测试循环，
    # 复用会抛 "Task got Future attached to a different loop"，后台任务静默写库失败
    with contextlib.suppress(Exception):
        from app.models.database import engine as _app_engine

        await _app_engine.dispose()

    yield

    # 测试后：清理 Redis + httpx
    with contextlib.suppress(Exception):
        await redis_mod.close_redis()
    with contextlib.suppress(Exception):
        await close_http()
    with contextlib.suppress(Exception):
        from app.models.database import engine as _app_engine

        await _app_engine.dispose()


@pytest.fixture(autouse=True)
async def _cleanup_pb_test_data():
    """迭代09 治理：测试直接用开发库（settings.database_url），大量用例以
    "pb{uuid}" 前缀造测试知识点（空点/校验点/题库点等），结束后若不清理会污染
    真实数据（错题本/图谱/组卷全被脏点污染，见迭代09 突破文档 §13）。
    本 fixture 在每个测试结束后删除测试命名空间数据（含关联表），保持开发库干净。

    测试命名空间（与真实前缀识别规则配套，见 mock_exam._REAL_KP_PREFIXES）：
    - pb{hex}：空点/校验点/题库点（历史污染，白名单外，生产代码直接过滤）
    - BK{6hex}-NNN：topic 组卷测试（BK 后直接跟 6 位 hex 为测试命名空间）
    - MX{4hex}-M{n}-NNN：full_mock 混合模块测试
    - TST 前缀：各测试文件自清洁命名空间（此处兜底清理，防跨文件漏清）
    真实 BK/MX 知识点为 "BK-XXX" / "MX-XXX"（带连字符）形态，不受影响。

    依赖表删除顺序：先子表后父表；清理失败不掩盖测试结果（suppress）。
    """
    yield
    try:
        from sqlalchemy import delete, text

        from app.models.database import async_session_factory
        from app.models.knowledge_point import KnowledgePoint

        # 测试知识点识别正则：pb{hex} / BK{6hex}-NNN / MX{4hex}-M{n}-NNN / TST 前缀
        test_kp_cond = (
            "code LIKE 'pb%' "
            "OR code LIKE 'TST%' "
            "OR code ~ '^BK[0-9a-f]{6}-[0-9]{3}$' "
            "OR code ~ '^MX[0-9a-f]{4}-M[0-9]-[0-9]{3}$' "
            "OR code ~ '^BK[0-9a-f]{6}$' "
            "OR code ~ '^MX[0-9a-f]{4}-M[0-9]$'"
        )

        async with async_session_factory() as session:
            # 子表先删（外键依赖）：kp_code 直连 或 kp_id 关联测试知识点
            # e2e_ 前缀为 test_student_linkage 联调错题（无 KP 行，也一并清理）
            for table in (
                "quiz_items",
                "error_records",
                "mastery_records",
                "mastery_snapshots",
                "daily_questions",
            ):
                with contextlib.suppress(Exception):
                    await session.execute(
                        text(
                            f"DELETE FROM {table} WHERE (kp_code LIKE 'pb%') "
                            f"OR (kp_code LIKE 'TST%') "
                            f"OR (kp_code LIKE 'e2e_%') "
                            f"OR (kp_code ~ '^BK[0-9a-f]{{6}}-[0-9]{{3}}$') "
                            f"OR (kp_code ~ '^MX[0-9a-f]{{4}}-M[0-9]-[0-9]{{3}}$') "
                            f"OR (kp_id IN (SELECT id FROM knowledge_points WHERE {test_kp_cond}))"
                        )
                    )
            # 测试知识点本身
            await session.execute(
                delete(KnowledgePoint).where(
                    (KnowledgePoint.code.like("pb%"))
                    | (KnowledgePoint.code.like("TST%"))
                    | (KnowledgePoint.code.regexp_match(r"^BK[0-9a-f]{6}-[0-9]{3}$"))
                    | (KnowledgePoint.code.regexp_match(r"^MX[0-9a-f]{4}-M[0-9]-[0-9]{3}$"))
                    | (KnowledgePoint.code.regexp_match(r"^BK[0-9a-f]{6}$"))
                    | (KnowledgePoint.code.regexp_match(r"^MX[0-9a-f]{4}-M[0-9]$"))
                )
            )
            await session.commit()
    except Exception:
        pass
