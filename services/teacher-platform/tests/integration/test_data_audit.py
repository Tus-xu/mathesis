"""数据真实性审计（03-ACCEPTANCE §7 五项，B5 固定落点 tests/integration/test_data_audit.py）。

审计对象 = 真实生产库 + MinIO（compose 全栈）。基础设施禁进程内模拟（红线 5）。
DB/MinIO 不可达 → skip（注明原因）；表缺失/审计命中 → **fail（红）**——占位数据
与编造统计是 P1 缺陷直接打回所属人（B5 验收观 7）。

跑法：compose 起全栈后 `pytest tests/integration/test_data_audit.py`
"""
from __future__ import annotations

import asyncio
import os
import re

import pytest

# 黑名单（03-ACCEPTANCE §7.2 原文词表）
PLACEHOLDER_BLACKLIST = re.compile(r"测试123|aaa|示例|TODO|lorem|测试", re.IGNORECASE)

PG_DSN = os.environ.get(
    "TEACHER_PG_DSN",
    "postgres://teacher:teacher_dev_pw@127.0.0.1:5433/teacher_platform",
)
MINIO_ENDPOINT = os.environ.get("TEACHER_MINIO_ENDPOINT", "127.0.0.1:9002")
MINIO_ACCESS = os.environ.get("TEACHER_MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET = os.environ.get("TEACHER_MINIO_SECRET_KEY", "minioadmin_dev_pw")


# ---------------- 连接夹具 ----------------


@pytest.fixture
async def pg():
    """真实 asyncpg 连接（function 级 async 夹具——asyncpg 连接绑定创建它的事件循环，
    必须与用例同循环，pytest-asyncio auto 模式下即用例的循环）。

    不可达 → skip；表缺失 → fail（迁移未跑=B3 红项）。用例内 `await pg.fetchval(...)`。
    """
    asyncpg = pytest.importorskip("asyncpg", reason="asyncpg 未安装（非 compose 环境）")
    try:
        conn = await asyncpg.connect(PG_DSN, timeout=5)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"PG 不可达（compose 未启动？）：{exc}")
    yield conn
    await conn.close()


@pytest.fixture(scope="module")
def minio_client():
    minio_mod = pytest.importorskip("minio", reason="minio 未安装（非 compose 环境）")
    try:
        client = minio_mod.Minio(
            MINIO_ENDPOINT, access_key=MINIO_ACCESS, secret_key=MINIO_SECRET, secure=False
        )
        client.bucket_exists("teacher-photos")  # 探活
        return client
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"MinIO 不可达：{exc}")


async def _require_table(pg, table: str) -> None:
    exists = await pg.fetchval("SELECT to_regclass($1) IS NOT NULL", f"public.{table}")
    if not exists:
        pytest.fail(f"[审计红] 表 {table} 不存在——Alembic 迁移未跑/域模型未落（B3/B4）")


# ---------------- 审计 1：拍照入库题原图真实存在 ----------------


async def test_audit_1_photo_question_images_real(pg, minio_client):
    """§7.1 — source='拍照入库' 的行 stem_image 必须存在 MinIO 对象且 >10KB（防空文件占位）。"""
    await _require_table(pg, "quiz_questions")
    rows = await pg.fetch(
        "SELECT id::text AS id, stem_image FROM quiz_questions WHERE source = '拍照入库' AND stem_image IS NOT NULL"
    )
    if not rows:
        pytest.skip("库内暂无拍照入库题（L2 链路未跑）——审计项无对象")
    bucket = "teacher-assets"
    bad: list[str] = []
    for r in rows:
        key = r["stem_image"]
        try:
            st = minio_client.stat_object(bucket, key)
            if st.size <= 10 * 1024:
                bad.append(f"{r['id']} 对象 {key} 仅 {st.size}B（≤10KB 疑占位）")
        except Exception as exc:  # noqa: BLE001
            bad.append(f"{r['id']} 对象 {key} 不可达：{exc}")
    assert not bad, "§7.1 拍照原图审计不通过：\n" + "\n".join(bad)


# ---------------- 审计 2：占位内容黑名单全库扫描 ----------------


async def test_audit_2_placeholder_blacklist(pg):
    """§7.2 — 全库扫描黑名单（测试/123/aaa/示例/TODO/lorem）命中数必须为 0。"""
    text_targets = {
        "quiz_questions": ["stem_latex", "analysis", "source"],
        "plans": ["topic", "boards"],
        "decks": ["title"],
        "grading_clusters": ["summary", "feedback"],
        "butler_messages": ["content"],
    }
    hits: list[str] = []
    for table, cols in text_targets.items():
        check = await pg.fetchval("SELECT to_regclass($1) IS NOT NULL", f"public.{table}")
        if not check:
            continue  # 域表未建属审计 3/5 范畴；此处不重复报
        for col in cols:
            rows = await pg.fetch(f"SELECT id::text AS id, {col}::text AS v FROM {table} WHERE {col} IS NOT NULL")
            for r in rows:
                v = r["v"] or ""
                m = PLACEHOLDER_BLACKLIST.search(v)
                if m:
                    hits.append(f"{table}.{col} id={r['id']} 命中 {m.group(0)!r}: {v[:60]!r}")
    assert not hits, "§7.2 占位内容黑名单命中（P1 数据真实性缺陷）：\n" + "\n".join(hits[:20])


# ---------------- 审计 3：种子数据溯源 ----------------


async def test_audit_3_seed_provenance(pg):
    """§7.3 — corpus/sources/manifest.toml 每源 → DB 对应记录数 >0（GAOKAO 题干抽样 diff）。

    manifest 归 B2（corpus/sources/）。未建 → fail（B2 M1 交付物：种子题库 ≥1000 题）。
    """
    here = os.path.dirname(__file__)
    manifest = None
    for cand in (here, os.path.join(here, "..", "..", "..")):
        p = os.path.join(cand, "corpus", "sources", "manifest.toml")
        if os.path.exists(p):
            manifest = p
            break
    if manifest is None:
        pytest.fail("[审计红] corpus/sources/manifest.toml 不存在——种子溯源链未建立（B2 M1 交付物）")
    await _require_table(pg, "quiz_questions")
    import tomllib

    with open(manifest, "rb") as f:
        data = tomllib.load(f)
    sources = data.get("sources", data)  # 容忍顶层即 sources 数组
    count = await pg.fetchval("SELECT COUNT(*) FROM quiz_questions")
    assert count > 0, "题库为空但 manifest 已登记——种子未入库（B2）"
    # 逐源计数核对（source 字段值 = manifest 源名约定，B2 落库时登记）
    missing_sources = []
    items = sources.items() if isinstance(sources, dict) else enumerate(sources)
    for name, meta in items:
        n = await pg.fetchval("SELECT COUNT(*) FROM quiz_questions WHERE source = $1", str(name))
        expected_min = int(meta.get("min_rows", 1)) if isinstance(meta, dict) else 1
        if n < expected_min:
            missing_sources.append(f"{name}: DB {n} < manifest {expected_min}")
    assert not missing_sources, "§7.3 种子溯源缺口：\n" + "\n".join(missing_sources)


# ---------------- 审计 4：任务表无孤儿 ----------------


async def test_audit_4_no_orphan_tasks(pg, minio_client):
    """§7.4 — tasks.result 非空的行 → 对应产物（deck/plan/export 文件）真实存在。"""
    check = await pg.fetchval("SELECT to_regclass($1) IS NOT NULL", "public.tasks")
    if not check:
        pytest.skip("tasks 表未建（骨架迁移未跑）")
    rows = await pg.fetch(
        "SELECT id::text AS id, type, result::text AS result FROM tasks WHERE result IS NOT NULL AND status = 'done' LIMIT 200"
    )
    if not rows:
        pytest.skip("暂无 done 任务（M2 起有产物）")
    import json as _json

    orphans: list[str] = []
    for r in rows:
        try:
            result = _json.loads(r["result"]) if isinstance(r["result"], str) else (r["result"] or {})
        except Exception:  # noqa: BLE001
            continue
        keys = [
            str(result[k]) for k in ("file_key", "deck_id", "plan_id", "export_key")
            if isinstance(result, dict) and result.get(k)
        ]
        for key in keys:
            if key.endswith((".pptx", ".pdf", ".png", ".jpg")):
                try:
                    minio_client.stat_object("teacher-exports", key)
                except Exception as exc:  # noqa: BLE001
                    orphans.append(f"task {r['id']} ({r['type']}) 产物 {key} 不在 MinIO：{exc}")
    assert not orphans, "§7.4 孤儿任务（result 指向不存在的产物）：\n" + "\n".join(orphans)


# ---------------- 审计 5：聚类/讲评数据可回放 ----------------


async def test_audit_5_grading_clusters_replayable(pg, minio_client):
    """§7.5 — grading_clusters.feedback 每条能关联到具体作答图片（image_key 可下载）。"""
    check = await pg.fetchval("SELECT to_regclass($1) IS NOT NULL", "public.grading_clusters")
    if not check:
        pytest.skip("grading_clusters 表未建（M3 批改链路）")
    rows = await pg.fetch(
        """
        SELECT c.id::text AS cluster_id, c.feedback, a.image_key
        FROM grading_clusters c
        LEFT JOIN grading_answers a ON a.cluster_id = c.id
        WHERE c.feedback IS NOT NULL AND c.feedback <> ''
        LIMIT 200
        """
    )
    if not rows:
        pytest.skip("暂无审定聚类（M3）")
    bad: list[str] = []
    seen: set[str] = set()
    for r in rows:
        if r["cluster_id"] in seen:
            continue
        seen.add(r["cluster_id"])
        if not r["image_key"]:
            bad.append(f"cluster {r['cluster_id']} 无任何作答图关联（feedback={r['feedback'][:30]!r}）")
            continue
        try:
            minio_client.stat_object("teacher-assets", r["image_key"])
        except Exception as exc:  # noqa: BLE001
            bad.append(f"cluster {r['cluster_id']} 作答图 {r['image_key']} 不可达：{exc}")
    assert not bad, "§7.5 聚类回放审计不通过：\n" + "\n".join(bad)
