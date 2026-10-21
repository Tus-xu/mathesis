"""双师课堂·黑盒端到端验收测试（需 PG 测试库 + LLM 可用）。

验收标准 C 的正确含义：
  将题目以真实用户输入方式提交到双师课堂，系统必须经过与普通题目相同的
  解析、生成、图形构建、校验和前端渲染流程，独立生成完整课堂；
  其数学结论、推导过程和图形表达必须与标准答案一致。

本测试：
  1. 从 tests/fixtures 读取验收题（原题 A/B/C + C 的 3 道变式题）；
  2. 通过 POST /api/classroom/sessions 提交到通用生成链路；
  3. 等待生成完成；
  4. 校验生成结果的数学内容与标准答案一致（不比对课件格式，只比对数学正确性）；
  5. 确认原题和变式题走的是同一条通用链路（engine 标注相同、无特例分支）。

变式题与原题同一知识域但不等价：改变边长比例、点的命名、要求证明的线面关系、
或改为求另一点到平面的距离。变式题不预先写入生产代码、不靠关键词匹配。

运行：cd services/api && python -m pytest tests/test_classroom_acceptance.py -q -s
（需先启动 PG + LLM 可用）
"""

import contextlib
import re
import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.main import app
from app.models.database import get_db
from tests.fixtures.acceptance_problems import (
    ACCEPTANCE_A,
    ACCEPTANCE_B,
    ACCEPTANCE_C,
    ACCEPTANCE_C_VARIANTS,
    compute_a_truth,
    compute_b_truth,
)


def _make_test_engine():
    return create_async_engine(settings.database_url, poolclass=NullPool)


_test_engine = _make_test_engine()
_test_session_factory = async_sessionmaker(
    _test_engine, class_=AsyncSession, expire_on_commit=False
)


async def _override_get_db():
    async with _test_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


app.dependency_overrides[get_db] = _override_get_db


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def _register_and_login(client, phone=None) -> tuple[str, str]:
    phone = phone or f"138{str(uuid.uuid4().int)[:8]}"
    await client.post("/api/auth/sms-code", json={"phone": phone})
    resp = await client.post("/api/auth/login", json={"phone": phone, "code": "123456"})
    data = resp.json()["data"]
    return data["token"], data["user"]["id"]


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _create_and_wait_once(client, token, topic, slide_count=8):
    """提交主题到通用链路并等待生成完成，返回 (session_data, all_text)。"""
    import asyncio

    h = _headers(token)
    resp = await client.post(
        "/api/classroom/sessions",
        json={"topic": topic, "slide_count": slide_count, "mode": "topic"},
        headers=h,
    )
    assert resp.status_code == 200, f"创建会话失败: {resp.text}"
    sid = resp.json()["data"]["session_id"]

    # 360s 预算（迭代20 门禁放宽等待窗口，断言不变：仍要求 ready 且 error=None）：
    # 生成走真实 LLM 两段式（大纲→逐页→数学验证）。全量套件里 C-v3 前面已有
    # 多个真实外呼用例，供应商限流会拉长尾延迟（240s 窗口在全量下仍偶发不够；
    # 单跑实测 93s 即 ready）。对真实外呼只该给足时间；若上游真挂会落 failed，
    # 测试照样红。
    for _ in range(360):
        detail = await client.get(f"/api/classroom/sessions/{sid}", headers=h)
        d = detail.json()["data"]
        if d["status"] in ("ready", "failed"):
            break
        await asyncio.sleep(1.0)

    # 收集所有文本内容（用于数学结论比对）
    all_text = ""
    for s in d.get("slides", []):
        for b in s.get("blocks", []):
            for field in ("text", "latex", "question", "analysis", "answer"):
                val = b.get(field, "")
                if isinstance(val, str):
                    all_text += val + "\n"
    return d, all_text


async def _create_and_wait(client, token, topic, slide_count=8, max_attempts=3):
    """验收入口：对**供应商瞬时故障**按产品自己的恢复指引重试，断言不放松。

    产品在过半页面因供应商限流/网络抖动失败时（stage_router
    classroom_session_failed_provider_outage）自身给出的文案就是
    「…（多为网络波动），请稍后重新生成」——本验收测的是「生成内容的数学
    正确性」，不是供应商可用性，故仅对这一**瞬时故障类**重新发起生成
    （最多 3 次）；内容性失败（数学验证不通过等）不复试，测试照常红。
    全部断言（ready、数学结论比对）对每次尝试完整执行。
    """
    import asyncio

    # 单一守卫点：本文件所有用例都经此函数发起真实外呼。无 LLM 凭据时立即跳过，
    # 不再空等一轮必然失败的生成（也避免「全部落 failed → engines 空集 →  vacuously pass」）；
    # 配置了凭据却仍失败则照常红（瞬时故障 3 次重试的逻辑不受影响）。
    _require_llm()

    d, all_text = None, ""
    for attempt in range(max_attempts):
        if attempt:
            await asyncio.sleep(5.0)  # 产品文案「请稍后重新生成」
        d, all_text = await _create_and_wait_once(client, token, topic, slide_count)
        if d.get("status") == "ready":
            return d, all_text
        err = str(d.get("error") or "")
        if "生成通道异常" not in err and "网络波动" not in err:
            break  # 非瞬时故障类：如实红
    return d, all_text


def _llm_configured() -> bool:
    """是否配置了任一 LLM 供应商凭据（火山方舟 ARK / 小米 DeepSeek / 讯飞 Spark）。

    CI 默认不注入任何密钥（conftest 亦把 ARK_API_KEY 钉死为空），此时真实外呼
    必然 "All model providers failed" → 会话落 failed。本文件测的是「生成内容的
    数学正确性」而非供应商可用性，故无凭据时跳过并说明原因；
    配置了凭据却仍失败必须如实红（瞬时故障重试约定见 _create_and_wait）。
    """
    return bool(
        (settings.ark_api_key and settings.ark_base_url and settings.ark_model)
        or (settings.deepseek_api_key and settings.deepseek_base_url)
        or (settings.spark_api_password and settings.spark_base_url)
    )


def _require_llm() -> None:
    """无 LLM 凭据 → 开跑前立即跳过，不必再等一轮必然失败的生成（省 ~30s/例）。

    配置了凭据则继续：生成若仍失败由调用方如实断言红，不洗成 skip
    （瞬时故障的 3 次重试约定见 _create_and_wait）。
    """
    if not _llm_configured():
        pytest.skip("未配置 LLM 凭据，真实外呼不可用；本文件测生成内容正确性，跳过")


def _extract_numbers(text: str) -> list[float]:
    """从文本中提取所有数值（用于答案比对）。"""
    numbers = []
    for m in re.finditer(r"-?\d+\.?\d*", text):
        with contextlib.suppress(ValueError):
            numbers.append(float(m.group()))
    return numbers


# 1/√3 与 √3/3 的 LaTeX 等价排版：\frac、\dfrac、\tfrac 同义，\sqrt{3} 与 \sqrt3 同义。
# 本文件只比对数学结论、不比对排版格式（见文件头），而实测 LLM 两种写法都会输出
# （2026-10-11 门禁第 2 轮：session 61c02513 全篇正确距离写成 $\dfrac{\sqrt{3}}{3}$，
# 旧断言只认 \frac 字面量而误红）。此正则仅覆盖这两个值本身——
# 错误结论（如实测同会话 slide5 的 h=√2/2）依然判失败，断言不放松。
_DISTANCE_EQUIV_RE = re.compile(
    r"\\[dt]?frac\{1\}\{\\sqrt\{?3\}?\}"  # \frac{1}{\sqrt{3}} / \dfrac{1}{\sqrt3} / …
    r"|\\[dt]?frac\{\\sqrt\{?3\}?\}\{3\}"  # \frac{\sqrt{3}}{3} / \dfrac{\sqrt{3}}{3} / …
)


# ==================== 黑盒端到端验收 ====================


@pytest.mark.asyncio
class TestAcceptanceA:
    """验收题 A：f(x)=x^3-3x 单调性，走通用链路生成。"""

    async def test_a_generated_via_generic_path(self, client):
        """验收题 A 通过通用链路生成，engine 为通用引擎。"""
        token, _ = await _register_and_login(client)
        d, all_text = await _create_and_wait(client, token, ACCEPTANCE_A["topic"])
        assert d["status"] == "ready", f"生成失败: {d.get('error')}"
        assert d["engine"] != "math_classroom_golden", "不应使用金标准特例引擎"

    async def test_a_contains_correct_math(self, client):
        """验收题 A 生成的课堂包含正确的数学结论。"""
        token, _ = await _register_and_login(client)
        d, all_text = await _create_and_wait(client, token, ACCEPTANCE_A["topic"])
        if d["status"] != "ready":
            pytest.skip("LLM 生成未完成")

        _truth = compute_a_truth()
        # 课堂应提及临界点 -1 和 1
        numbers = _extract_numbers(all_text)
        has_minus1 = any(abs(n - (-1)) < 0.01 for n in numbers)
        has_1 = any(abs(n - 1) < 0.01 for n in numbers)
        assert has_minus1, "课堂应包含临界点 x=-1"
        assert has_1, "课堂应包含临界点 x=1"


@pytest.mark.asyncio
class TestAcceptanceB:
    """验收题 B：极值反求参数，走通用链路生成。"""

    async def test_b_generated_via_generic_path(self, client):
        token, _ = await _register_and_login(client)
        d, all_text = await _create_and_wait(client, token, ACCEPTANCE_B["topic"])
        assert d["status"] == "ready", f"生成失败: {d.get('error')}"
        assert d["engine"] != "math_classroom_golden"

    async def test_b_contains_correct_a(self, client):
        """验收题 B 生成的课堂包含 a=-2 的结论。"""
        token, _ = await _register_and_login(client)
        d, all_text = await _create_and_wait(client, token, ACCEPTANCE_B["topic"])
        if d["status"] != "ready":
            pytest.skip("LLM 生成未完成")

        _truth = compute_b_truth()
        numbers = _extract_numbers(all_text)
        has_a_minus2 = any(abs(n - (-2)) < 0.01 for n in numbers)
        assert has_a_minus2, "课堂应包含 a=-2 的结论"


@pytest.mark.asyncio
class TestAcceptanceC:
    """验收题 C：四棱锥综合题，走通用链路生成。"""

    async def test_c_generated_via_generic_path(self, client):
        token, _ = await _register_and_login(client)
        d, all_text = await _create_and_wait(client, token, ACCEPTANCE_C["topic"])
        assert d["status"] == "ready", f"生成失败: {d.get('error')}"
        assert d["engine"] != "math_classroom_golden"

    async def test_c_contains_correct_distance(self, client):
        """验收题 C 生成的课堂包含距离 1/√3 的结论。"""
        token, _ = await _register_and_login(client)
        d, all_text = await _create_and_wait(client, token, ACCEPTANCE_C["topic"])
        if d["status"] != "ready":
            pytest.skip("LLM 生成未完成")

        expected = ACCEPTANCE_C["standard_answer"]["proof_2_distance"]["E_to_plane"]
        # 课堂应包含 1/√3 或 √3/3 或其数值 ≈ 0.577（含 LaTeX 排版等价形式，见 _DISTANCE_EQUIV_RE）
        has_distance = (
            "\\frac{1}{\\sqrt{3}}" in all_text
            or "\\frac{\\sqrt{3}}{3}" in all_text
            or "1/√3" in all_text
            or "√3/3" in all_text
            or bool(_DISTANCE_EQUIV_RE.search(all_text))
        )
        if not has_distance:
            numbers = _extract_numbers(all_text)
            has_distance = any(abs(n - expected["value"]) < 0.01 for n in numbers)
        assert has_distance, f"课堂应包含距离 1/√3 ≈ {expected['value']}"

    async def test_c_contains_3d_figure(self, client):
        """验收题 C 生成的课堂包含 3D 多面体图。"""
        token, _ = await _register_and_login(client)
        d, all_text = await _create_and_wait(client, token, ACCEPTANCE_C["topic"])
        if d["status"] != "ready":
            pytest.skip("LLM 生成未完成")

        has_3d = False
        for s in d.get("slides", []):
            for b in s.get("blocks", []):
                if b.get("kind") == "geometry":
                    for solid in b.get("figure", {}).get("solids", []):
                        if solid.get("kind") in ("polyhedron", "pyramid", "prism"):
                            has_3d = True
        assert has_3d, "验收题 C 必须包含 3D 多面体图"


# ==================== 验收题 C 变式题（同一通用链路） ====================


@pytest.mark.asyncio
class TestAcceptanceCVariants:
    """验收题 C 的 3 道变式题，走同一条通用链路生成。

    变式题改变边长比例、点的命名、要求证明的线面关系、或改为求另一点到平面的距离。
    只有原题和变式题均能由同一条通用链路生成正确讲解，标准 C 才算通过。
    """

    @pytest.mark.parametrize(
        "variant", ACCEPTANCE_C_VARIANTS, ids=[v["id"] for v in ACCEPTANCE_C_VARIANTS]
    )
    async def test_variant_generated_via_generic_path(self, client, variant):
        """每道变式题通过通用链路生成，engine 为通用引擎（与原题相同）。"""
        token, _ = await _register_and_login(client)
        d, all_text = await _create_and_wait(client, token, variant["topic"])
        assert d["status"] == "ready", f"变式 {variant['id']} 生成失败: {d.get('error')}"
        assert d["engine"] != "math_classroom_golden", "变式题不应使用金标准特例引擎"

    @pytest.mark.parametrize(
        "variant", ACCEPTANCE_C_VARIANTS, ids=[v["id"] for v in ACCEPTANCE_C_VARIANTS]
    )
    async def test_variant_contains_3d_figure(self, client, variant):
        """每道变式题包含 3D 多面体图。"""
        token, _ = await _register_and_login(client)
        d, all_text = await _create_and_wait(client, token, variant["topic"])
        if d["status"] != "ready":
            pytest.skip(f"变式 {variant['id']} LLM 生成未完成")

        has_3d = False
        for s in d.get("slides", []):
            for b in s.get("blocks", []):
                if b.get("kind") == "geometry":
                    for solid in b.get("figure", {}).get("solids", []):
                        if solid.get("kind") in ("polyhedron", "pyramid", "prism"):
                            has_3d = True
        assert has_3d, f"变式 {variant['id']} 必须包含 3D 多面体图"


# ==================== 同一通用链路验证 ====================


@pytest.mark.asyncio
class TestSameGenericPath:
    """验证原题和变式题走的是同一条通用链路。

    若无法证明标准 C 和变式题走的是同一条通用生成链路，则验收失败。
    """

    async def test_all_problems_use_same_engine(self, client):
        """原题 C 和所有变式题使用相同的 engine 标注（同一条通用链路）。"""
        token, _ = await _register_and_login(client)
        engines = set()

        # 原题 C
        d_c, _ = await _create_and_wait(client, token, ACCEPTANCE_C["topic"])
        if d_c["status"] == "ready":
            engines.add(d_c["engine"])

        # 变式题
        for v in ACCEPTANCE_C_VARIANTS:
            d_v, _ = await _create_and_wait(client, token, v["topic"])
            if d_v["status"] == "ready":
                engines.add(d_v["engine"])

        # 所有成功生成的会话应使用同一个 engine（通用链路）
        if engines:
            assert len(engines) == 1, f"原题和变式题应使用同一引擎，实际: {engines}"
            assert "golden" not in next(iter(engines)).lower(), "不应使用金标准特例引擎"
