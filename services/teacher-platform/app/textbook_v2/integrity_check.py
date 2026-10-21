"""「一套完整教材」完整性校验（PLAN §7.3，B3）。

唯一事实源（PLAN §7.1）：授权教材切片镜像中「人工整理+质检通过」的批次
（manifest.json + report.md 齐备）；当前冻结白名单 = 人教A版2019 学生教材 5 册
（batch_id 与 8000 侧 ``services/api/scripts/import_pep_textbooks.py:27-33`` 同源，
跨服务禁止 import，两侧修改必须同步）。

四项校验（全部通过才算「一套完整教材」，退出码 0；任一缺口退出码 1 并附缺口清单）：
1. **册级存在性**：白名单每册在磁盘镜像有批次（book_type=textbook 且非教师用书）、
   在 ``textbook_sources`` 有 STAGED/ACTIVE 行、chunk 数 ≥ R4 §7 基线
   （bixi1=145/bixi2=246/xbixi1=145/xbixi2=105/xbixi3=151）且 ≥ 学生端实测；
2. **章节覆盖**：每册 DB 章节树含磁盘批次声明的全部 ``^\\d+(\\.\\d+)+`` 编号小节；
3. **chunk 完整性**：section 的 chunk_start..chunk_end 权威定位列与磁盘行序对账
   （无缝隙/无重叠、覆盖 1..N）、零 chunk 节点列出、行数与定位列对账；
4. **两端对账**：同一 batch 在 ``math_arena.chunks``（meta.book_id 维度，1024 维）
   与 ``textbook_chunks``（768 维）的 chunk 计数一致；全量磁盘↔教师库对账明细落
   ``deliverables/two-end-link/CORPUS-RECONCILIATION.json``（ADAPT reconciliation.py）。

**STAGED → ACTIVE 的唯一通路**（PLAN §7.2 门禁 7）：本模块全部校验通过且显式
``--promote`` 时，才经 ``rights.py`` 状态机把白名单 5 册置 ACTIVE；报告模式不写库。

用法（部署冒烟 §10.6 必跑）::

    cd services/teacher-platform
    uv run python -m app.textbook_v2.integrity_check            # 报告模式
    uv run python -m app.textbook_v2.integrity_check --promote  # 校验通过后发布 ACTIVE

env：
- ``TEXTBOOK_SLICE_ROOTS``：切片镜像根（os.pathsep 分隔，先例 image_routes.py）；
- ``API8000_DATABASE_URL`` / ``MATH_ARENA_DATABASE_URL``：学生端库 DSN（缺省本机
  dev 库 localhost:54329/math_arena，与 tools/corpus_integrity 同一口径）；
- 教师端库沿用 ``app.core.config`` 的 ``DATABASE_URL``。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.textbook_v2.backfill_metadata import iter_batch_dirs
from app.textbook_v2.importers.slice_importer import (
    build_section_tree,
    is_teacher_guide,
    load_chunks,
    sha256_bytes,
)
from app.textbook_v2.models import TextbookChunk, TextbookSection, TextbookSource
from app.textbook_v2.reconciliation import reconcile_corpus, reconcile_corpus_json
from app.textbook_v2.rights import (
    RightsStatus,
    assert_can_transition,
    is_teacher_guide_batch_id,
)

# ─────────────────────────────────────────────────────────────────────────────
# 白名单与基线常量（PLAN §7.3；batch_id 与 import_pep_textbooks.py:27-33 同源）
# ─────────────────────────────────────────────────────────────────────────────

#: 冻结白名单：人教A版2019 学生教材 5 册（唯一允许 ACTIVE 的教材事实集）
WHITELIST_BATCH_IDS: tuple[str, ...] = (
    "20260726-A-bixi1-01",
    "20260729-A-bixi2-01",
    "20260729-A-xbixi1-01",
    "20260729-A-xbixi2-01",
    "20260729-A-xbixi3-01",
)

#: R4 §7 学生端同名册 chunk 数（两端逐册一致为通过线）
PLAN_BASELINE_CHUNKS: dict[str, int] = {
    "20260726-A-bixi1-01": 145,
    "20260729-A-bixi2-01": 246,
    "20260729-A-xbixi1-01": 145,
    "20260729-A-xbixi2-01": 105,
    "20260729-A-xbixi3-01": 151,
}

#: batch_id → 学生端 chunks.meta.book_id（与镜像 manifest.book_id / import_pep_textbooks
#: document_meta.book_id 同词表；学生端按 book_id 维度对账，PLAN §7.3.4）
WHITELIST_BOOK_IDS: dict[str, str] = {
    "20260726-A-bixi1-01": "bixi1",
    "20260729-A-bixi2-01": "bixi2",
    "20260729-A-xbixi1-01": "xbixi1",
    "20260729-A-xbixi2-01": "xbixi2",
    "20260729-A-xbixi3-01": "xbixi3",
}

#: 编号小节路径（PLAN §7.3.2 口径：``^\\d+(\\.\\d+)+``，排除「思考/探究」等栏目噪声）
SECTION_PATH_RE = re.compile(r"^\d+(?:\.\d+)+")

#: 报告缺省落盘位置（仓库相对，锚定本文件而非 CWD）
_REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_REPORT_MD = _REPO_ROOT / "deliverables" / "two-end-link" / "textbook-integrity.md"
DEFAULT_RECONCILIATION_JSON = (
    _REPO_ROOT / "deliverables" / "two-end-link" / "CORPUS-RECONCILIATION.json"
)

#: 学生端库 DSN env（先 corpus_integrity 的 ENV_KEYS 口径，缺省本机 dev 库）
STUDENT_ENV_KEYS = ("API8000_DATABASE_URL", "MATH_ARENA_DATABASE_URL", "DATABASE_URL")
DEFAULT_STUDENT_DSN = "postgresql://postgres:postgres@localhost:54329/math_arena"


def resolve_student_dsn(explicit: str | None = None) -> str:
    """学生端 math_arena 库 DSN：显式参数 > env（API8000/MATH_ARENA/DATABASE_URL）> dev 缺省。"""
    if explicit:
        return explicit
    for key in STUDENT_ENV_KEYS:
        val = os.environ.get(key, "").strip()
        if val:
            return val.replace("postgresql+asyncpg://", "postgresql://").replace(
                "postgresql+psycopg://", "postgresql://"
            )
    return DEFAULT_STUDENT_DSN


# ─────────────────────────────────────────────────────────────────────────────
# 数据采集结构（纯数据，可注入 → 单测无需真实库）
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class DiskVolume:
    """磁盘白名单批次（唯一事实源侧的权威数据）。"""

    batch_id: str
    book_id: str
    batch_dir: Path
    book_name: str
    volume: str
    book_type: str
    file_sha256: str
    chunk_lines: int
    section_paths: list[str] = field(default_factory=list)  # 编号小节，排序去重
    section_spans: dict[str, tuple[int, int]] = field(default_factory=dict)  # path → (start,end)


@dataclass
class DbSection:
    """教师库 section 行（对账用最小投影）。"""

    section_id: str
    title: str
    section_path: str | None
    chunk_start: int | None
    chunk_end: int | None
    chunk_rows: int


@dataclass
class DbVolume:
    """教师库 source 行投影（STAGED/ACTIVE）。"""

    source_id: str
    batch_id: str | None
    book_volume: str
    rights_status: str
    file_sha256: str
    chunk_total: int
    sections: list[DbSection] = field(default_factory=list)


@dataclass
class VolumeReport:
    """单册校验结果（checks: 校验名 → (ok, 明细, 缺口列表)）。"""

    batch_id: str
    book_name: str
    disk_lines: int | None = None
    teacher_chunks: int | None = None
    student_chunks: int | None = None
    checks: dict[str, tuple[bool, str, list[str]]] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return bool(self.checks) and all(ok for ok, _d, _g in self.checks.values())

    def gaps(self) -> list[str]:
        return [g for _ok, _d, gs in self.checks.values() for g in gs]


@dataclass
class IntegrityReport:
    """整份校验报告（JSON/Markdown 双输出契约）。"""

    generated_at: str
    volumes: list[VolumeReport] = field(default_factory=list)
    reconciliation: dict[str, Any] | None = None
    student_db_ok: bool = True
    student_db_note: str = ""
    promoted: list[str] = field(default_factory=list)
    # 教师用书红线（tel F2）：全部教师用书批次必须 QUARANTINED；未核验时 False 不参与裁决
    teacher_guides: list[dict[str, str]] = field(default_factory=list)  # [{batch_id,rights_status}]
    teacher_guide_checked: bool = False

    @property
    def teacher_guide_gaps(self) -> list[str]:
        if not self.teacher_guide_checked:
            return []
        return [
            f"教师用书红线违规: {g['batch_id']} rights_status={g['rights_status']} ≠ QUARANTINED"
            f"（教师用书永不入库，rights_status 必须为 QUARANTINED）"
            for g in self.teacher_guides
            if g["rights_status"] != RightsStatus.QUARANTINED.value
        ]

    @property
    def ok(self) -> bool:
        return (
            all(v.ok for v in self.volumes)
            and self.student_db_ok
            and not self.teacher_guide_gaps
        )

    def all_gaps(self) -> list[str]:
        out: list[str] = []
        for v in self.volumes:
            out.extend(f"[{v.batch_id}] {g}" for g in v.gaps())
        if not self.student_db_ok:
            out.append(f"[student-db] {self.student_db_note}")
        out.extend(f"[teacher-guide] {g}" for g in self.teacher_guide_gaps)
        return out

    def as_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "ok": self.ok,
            "student_db_ok": self.student_db_ok,
            "student_db_note": self.student_db_note,
            "promoted": list(self.promoted),
            "gaps": self.all_gaps(),
            "teacher_guides": list(self.teacher_guides),
            "volumes": [
                {
                    "batch_id": v.batch_id,
                    "book_name": v.book_name,
                    "ok": v.ok,
                    "disk_lines": v.disk_lines,
                    "teacher_chunks": v.teacher_chunks,
                    "student_chunks": v.student_chunks,
                    "checks": {
                        k: {"ok": ok, "detail": d, "gaps": g} for k, (ok, d, g) in v.checks.items()
                    },
                }
                for v in self.volumes
            ],
            "reconciliation_summary": _recon_summary(self.reconciliation),
        }


def _recon_summary(recon: dict[str, Any] | None) -> dict[str, Any] | None:
    if not recon:
        return None
    return {
        "batches_total": recon.get("batches_total"),
        "summary": recon.get("summary"),
    }


# ─────────────────────────────────────────────────────────────────────────────
# 采集：磁盘 / 教师库 / 学生库
# ─────────────────────────────────────────────────────────────────────────────


def collect_disk_volumes(roots: list[Path] | None = None) -> dict[str, DiskVolume]:
    """扫描切片镜像根，产出白名单批次的磁盘权威数据（不含白名单外批次）。"""
    out: dict[str, DiskVolume] = {}
    for _root, d in iter_batch_dirs(roots):
        manifest_path = d / "manifest.json"
        chunks_path = d / "chunks.jsonl"
        if not manifest_path.is_file() or not chunks_path.is_file():
            continue
        try:
            m = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        batch_id = str(m.get("batch_id") or "")
        if batch_id not in WHITELIST_BATCH_IDS:
            continue
        chunks = load_chunks(d)  # 门禁口径逐行校验（§7.2 门禁 2/3/5）
        sections, mapping = build_section_tree(chunks)
        spans: dict[str, tuple[int, int]] = {}
        for sec in sections:
            if sec.section_path and SECTION_PATH_RE.match(sec.section_path):
                spans[sec.section_path] = (int(sec.chunk_start or 0), int(sec.chunk_end or 0))
        out[batch_id] = DiskVolume(
            batch_id=batch_id,
            book_id=str(m.get("book_id") or ""),
            batch_dir=d,
            book_name=str(m.get("book_name") or ""),
            volume=str(m.get("volume") or ""),
            book_type=str(m.get("book_type") or ""),
            file_sha256=sha256_bytes(chunks_path.read_bytes()),
            chunk_lines=len(chunks),
            section_paths=sorted(spans),
            section_spans=spans,
        )
    return out


async def collect_teacher_volumes(
    session: AsyncSession, disk_vols: dict[str, DiskVolume] | None = None
) -> dict[str, DbVolume]:
    """采集教师库 source/section/chunk 投影（幂等键与导入器一致：batch_id → 磁盘 sha 兜底）。"""
    out: dict[str, DbVolume] = {}
    for batch_id in WHITELIST_BATCH_IDS:
        rows = (
            (
                await session.execute(
                    select(TextbookSource).where(TextbookSource.batch_id == batch_id)
                )
            )
            .scalars()
            .all()
        )
        if not rows:
            # 存量行 batch_id 可能为空（R12 回填前）——按磁盘 file_sha256 兜底匹配
            disk = (disk_vols or {}).get(batch_id)
            if disk is None:
                continue
            rows = (
                (
                    await session.execute(
                        select(TextbookSource).where(TextbookSource.file_sha256 == disk.file_sha256)
                    )
                )
                .scalars()
                .all()
            )
        for source in rows:
            if source.rights_status not in ("STAGED", "ACTIVE"):
                continue  # QUARANTINED 不参与完整性集合
            sec_rows = (
                (
                    await session.execute(
                        select(
                            TextbookSection.id,
                            TextbookSection.title,
                            TextbookSection.section_path,
                            TextbookSection.chunk_start,
                            TextbookSection.chunk_end,
                        ).where(TextbookSection.textbook_id == source.id)
                    )
                )
                .all()
            )
            if not sec_rows:
                continue
            per_section: dict[Any, int] = dict.fromkeys((r[0] for r in sec_rows), 0)
            chunk_pairs = (
                (
                    await session.execute(
                        select(TextbookChunk.section_id).where(
                            TextbookChunk.section_id.in_([r[0] for r in sec_rows])
                        )
                    )
                )
                .all()
            )
            for (sec_id,) in chunk_pairs:
                per_section[sec_id] = per_section.get(sec_id, 0) + 1
            sections = [
                DbSection(
                    section_id=str(r[0]),
                    title=str(r[1] or ""),
                    section_path=r[2],
                    chunk_start=r[3],
                    chunk_end=r[4],
                    chunk_rows=per_section.get(r[0], 0),
                )
                for r in sec_rows
            ]
            out[batch_id] = DbVolume(
                source_id=str(source.id),
                batch_id=source.batch_id,
                book_volume=source.volume,
                rights_status=source.rights_status,
                file_sha256=source.file_sha256,
                chunk_total=sum(per_section.values()),
                sections=sections,
            )
            break  # 每批取一行（同 sha 异版本时取首个 STAGED/ACTIVE 行）
    return out


async def collect_teacher_guide_rows(session: AsyncSession) -> list[dict[str, str]]:
    """采集教师库全部教师用书批次行（tel F2 红线：必须全部 QUARANTINED）。

    判别 = ``rights.is_teacher_guide_batch_id``（batch_id 形态 tbixi/txbixi；
    DB 无 book_type 列，磁盘 manifest 判别由 importer 门禁承担）。
    """
    rows = ((await session.execute(select(TextbookSource))).scalars().all())
    return [
        {"batch_id": r.batch_id or "", "rights_status": r.rights_status}
        for r in rows
        if is_teacher_guide_batch_id(r.batch_id)
    ]


async def collect_student_counts(dsn: str, book_ids: list[str]) -> tuple[dict[str, int], str]:
    """学生端 math_arena.chunks 按 meta.book_id 计数（1024 维侧；失败返回空 dict + 原因）。"""
    try:
        import asyncpg
    except ImportError as e:  # pragma: no cover - 环境缺依赖
        return {}, f"asyncpg 不可用: {e}"
    try:
        conn = await asyncpg.connect(dsn, timeout=5.0)
    except Exception as e:
        return {}, f"学生端库不可达 [{dsn.split('@')[-1]}]: {type(e).__name__}: {str(e)[:120]}"
    try:
        rows = await conn.fetch(
            """
            SELECT c.meta->>'book_id' AS book_id, count(*)::int AS n
            FROM chunks c
            WHERE c.deleted_at IS NULL
              AND c.meta->>'book_id' = ANY($1::text[])
            GROUP BY 1
            """,
            book_ids,
        )
        return {r["book_id"]: int(r["n"]) for r in rows}, ""
    finally:
        await conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# 四项校验（纯函数，可单测）
# ─────────────────────────────────────────────────────────────────────────────


def _effective_path(s: DbSection) -> str | None:
    """section 的编号路径：section_path 列优先，存量空值按标题解析兜底（同一切片口径）。"""
    path = (s.section_path or "").strip()
    if path:
        return path
    m = re.match(r"^\s*(\d+(?:\.\d+)+)", s.title or "")
    return m.group(1) if m else None


def check_volume(
    batch_id: str,
    disk: DiskVolume | None,
    db_vol: DbVolume | None,
    student_count: int | None,
) -> VolumeReport:
    """对单册执行 §7.3 的 1/2/3/4 项校验（纯函数；student_count=None 表示学生库侧不可用）。"""
    rep = VolumeReport(
        batch_id=batch_id,
        book_name=disk.book_name if disk else batch_id,
        disk_lines=disk.chunk_lines if disk else None,
        teacher_chunks=db_vol.chunk_total if db_vol else None,
        student_chunks=student_count,
    )

    # 1a. 磁盘批次存在且为非教师用书学生教材（唯一事实源侧）
    if disk is None:
        rep.checks["disk_batch"] = (False, "磁盘批次缺失", [f"白名单批次不在镜像中: {batch_id}"])
    elif disk.book_type != "textbook" or is_teacher_guide(
        {"book_type": disk.book_type, "book_name": disk.book_name}
    ):
        rep.checks["disk_batch"] = (
            False,
            f"book_type={disk.book_type}",
            [f"{batch_id} 不是学生教材批次（book_type={disk.book_type}，白名单只收 textbook）"],
        )
    else:
        rep.checks["disk_batch"] = (True, f"book_type={disk.book_type} chunk_lines={disk.chunk_lines}", [])

    # 1b. 教师库 STAGED/ACTIVE 行存在
    if db_vol is None:
        rep.checks["db_source"] = (
            False,
            "教师库无 STAGED/ACTIVE 行",
            [f"textbook_sources 缺 {batch_id} 的 STAGED/ACTIVE 行"],
        )
    else:
        rep.checks["db_source"] = (True, f"source={db_vol.source_id[:8]} rights={db_vol.rights_status}", [])

    # 1c. chunk 数 ≥ R4 §7 基线，且 ≥ 学生端实测（两端逐册一致为通过线）
    baseline = PLAN_BASELINE_CHUNKS[batch_id]
    t_count = db_vol.chunk_total if db_vol else 0
    if db_vol is None:
        rep.checks["chunk_count"] = (False, "无教师库行", [f"chunk 数无法核对（教师库缺行）: {batch_id}"])
    else:
        gaps: list[str] = []
        if t_count < baseline:
            gaps.append(f"教师库 chunk 数 {t_count} < R4§7 基线 {baseline}: {batch_id}")
        if student_count is not None and t_count < student_count:
            gaps.append(f"教师库 chunk 数 {t_count} < 学生端 {student_count}: {batch_id}")
        rep.checks["chunk_count"] = (not gaps, f"teacher={t_count} baseline={baseline} student={student_count}", gaps)

    # 2. 章节覆盖：磁盘编号小节 ⊆ DB 章节树
    if db_vol is None:
        rep.checks["section_coverage"] = (False, "无教师库行", [f"章节覆盖无法核对: {batch_id}"])
    else:
        db_paths = {_effective_path(s) for s in db_vol.sections} - {None}
        missing = [p for p in disk.section_paths if p not in db_paths]
        rep.checks["section_coverage"] = (
            not missing,
            f"disk={len(disk.section_paths)} db={len(db_paths)}",
            [f"章节树缺小节 {p}: {batch_id}" for p in missing],
        )

    # 3. chunk 完整性：定位列与磁盘行序对账
    #    语义注记（R06 章节模型）：编号小节行按编号复用（如「1. 填空题」在多章习题中
    #    重复出现），其 span = 该节全部行的 min..max，故 span 允许重叠；完整性不变式 =
    #    ① span 并集无缝覆盖 1..磁盘行数（缺行/缺节即缺口）② sum(rows)==磁盘行数
    #    ③ 每节 rows ≤ span 跨度 ④ 零 chunk 节点列出。
    gaps: list[str] = []
    detail = ""
    if db_vol is None:
        gaps.append(f"chunk 完整性无法核对: {batch_id}")
    elif disk is None:  # pragma: no cover - 调用方已按 disk 缺失短路
        gaps.append(f"chunk 完整性无法核对（磁盘批次缺失）: {batch_id}")
    else:
        spans: list[tuple[int, int]] = []
        overlap_pairs = 0
        for s in db_vol.sections:
            path = _effective_path(s)
            if s.chunk_start is None or s.chunk_end is None:
                if s.chunk_rows == 0:
                    label = path if (path and SECTION_PATH_RE.match(path)) else f"「{s.title[:24]}」"
                    gaps.append(f"零 chunk 节点 {label}: {batch_id}")
                continue
            if s.chunk_start > s.chunk_end:
                gaps.append(
                    f"定位列倒置 chunk_start={s.chunk_start}>chunk_end={s.chunk_end}: {batch_id}"
                )
                continue
            spans.append((s.chunk_start, s.chunk_end))
            if s.chunk_rows > s.chunk_end - s.chunk_start + 1:
                gaps.append(
                    f"小节「{s.title[:24]}」实际 chunk 行 {s.chunk_rows} > 定位跨度 "
                    f"{s.chunk_end - s.chunk_start + 1}: {batch_id}"
                )
        # ① span 并集无缝覆盖 1..disk.chunk_lines（重叠合法——编号复用节；缺口=硬失败）
        if spans:
            spans.sort()
            merged_end = 0
            for start, end in spans:
                if start > merged_end + 1:
                    gaps.append(f"定位列并集缺口 [{merged_end + 1},{start - 1}]: {batch_id}")
                elif start <= merged_end:
                    overlap_pairs += 1
                merged_end = max(merged_end, end)
            if merged_end != disk.chunk_lines:
                gaps.append(
                    f"定位列并集覆盖到 {merged_end} != 磁盘行数 {disk.chunk_lines}: {batch_id}"
                )
        # ② 实际 chunk 行总数 == 磁盘行数（去重已在 §7.2 门禁写入侧排除重复语义；
        #    不一致说明 DB 行丢失或冗余——volume 级计数校验另行兜底）
        total_rows = sum(s.chunk_rows for s in db_vol.sections)
        if total_rows != disk.chunk_lines:
            gaps.append(
                f"DB chunk 行总数 {total_rows} != 磁盘行数 {disk.chunk_lines}: {batch_id}"
            )
        # ④ 零 chunk 编号小节（磁盘声明了小节但 DB 侧无任何 chunk 行）
        nonzero_paths = {
            _effective_path(s) for s in db_vol.sections if s.chunk_rows > 0
        } - {None}
        gaps.extend(
            f"零 chunk 编号小节 {p}: {batch_id}"
            for p in disk.section_paths
            if p not in nonzero_paths
        )
        note = f"，重叠 {overlap_pairs} 处（编号复用节 min..max 语义，非缺陷）" if overlap_pairs else ""
        detail = f"sections={len(db_vol.sections)} spans={len(spans)} disk_lines={disk.chunk_lines}{note}"
    rep.checks["chunk_integrity"] = (not gaps, detail, gaps)

    # 4. 两端对账（同一 batch：学生 1024 维 math_arena.chunks ↔ 教师 768 维 textbook_chunks）
    if db_vol is None:
        rep.checks["two_end"] = (False, "无教师库行", [f"两端对账无法核对: {batch_id}"])
    elif student_count is None:
        rep.checks["two_end"] = (
            False,
            "学生端无该册计数",
            [f"两端对账失败：学生端 math_arena.chunks 无 book_id 计数（teacher={db_vol.chunk_total}）: {batch_id}"],
        )
    else:
        ok = db_vol.chunk_total == student_count
        rep.checks["two_end"] = (
            ok,
            f"teacher={db_vol.chunk_total} student={student_count}",
            []
            if ok
            else [f"两端 chunk 数不一致 teacher={db_vol.chunk_total} student={student_count}: {batch_id}"],
        )
    return rep


# ─────────────────────────────────────────────────────────────────────────────
# 报告渲染
# ─────────────────────────────────────────────────────────────────────────────


def render_markdown(report: IntegrityReport) -> str:
    """确定性 Markdown 报告（现状数据统计 + 缺口清单）。"""
    out: list[str] = []
    add = out.append
    add("# 教材库完整性校验报告（textbook-integrity）")
    add("")
    add(f"- **generated_at (UTC)**: `{report.generated_at}`")
    add(f"- **结论**: {'✅ 一套完整教材齐备（5/5 册全部通过）' if report.ok else '❌ 存在缺口（见下）'}")
    add(f"- **退出码**: `{0 if report.ok else 1}`")
    add(
        "- **唯一事实源**: 授权教材切片镜像「人工整理+质检通过」批次（PLAN §7.1）；"
        "冻结白名单 = 人教A版2019 学生教材 5 册"
    )
    add("")
    add("## 册级现状统计")
    add("")
    add("| 批次 | 书名 | 磁盘 chunks | 教师库 chunks | 学生端 chunks | R4§7 基线 | 章节覆盖 | 定位列 | 两端对账 |")
    add("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for v in report.volumes:
        def _mark(name: str, _v: VolumeReport = v) -> str:
            chk = _v.checks.get(name)
            return "—" if chk is None else ("✅" if chk[0] else "❌")

        add(
            f"| `{v.batch_id}` | {v.book_name} | {v.disk_lines if v.disk_lines is not None else '—'} | "
            f"{v.teacher_chunks if v.teacher_chunks is not None else '—'} | "
            f"{v.student_chunks if v.student_chunks is not None else '—'} | "
            f"{PLAN_BASELINE_CHUNKS[v.batch_id]} | {_mark('section_coverage')} | "
            f"{_mark('chunk_integrity')} | {_mark('two_end')} |"
        )
    add("")
    if not report.student_db_ok:
        add(f"> 学生端库对账不可用：{report.student_db_note}")
        add("")
    if report.teacher_guide_checked:
        quarantined = [g for g in report.teacher_guides if g["rights_status"] == RightsStatus.QUARANTINED.value]
        add("## 教师用书红线（永不入库，入库必 QUARANTINED）")
        add("")
        add(f"- 教师用书批次：{len(report.teacher_guides)} 个，已隔离 {len(quarantined)} 个"
            f"{'（全部 QUARANTINED ✅）' if not report.teacher_guide_gaps else '（存在违规 ❌）'}")
        for g in report.teacher_guides:
            mark = "✅" if g["rights_status"] == RightsStatus.QUARANTINED.value else "❌"
            add(f"- {mark} `{g['batch_id']}` → {g['rights_status']}")
        add("")
    gaps = report.all_gaps()
    add("## 缺口清单")
    add("")
    if gaps:
        for g in gaps:
            add(f"- ❌ {g}")
    else:
        add("- 无（5 册白名单全部通过 §7.3 四项校验）")
    add("")
    if report.promoted:
        add("## 本次发布（--promote）")
        add("")
        for b in report.promoted:
            add(f"- `{b}` STAGED → ACTIVE（经 rights 状态机）")
        add("")
    if report.reconciliation:
        s = report.reconciliation.get("summary", {})
        add("## 磁盘 ↔ 教师库批次对账（全量批次）")
        add("")
        add(f"- 批次总数：{report.reconciliation.get('batches_total')}")
        add(f"- MATCHED：{s.get('MATCHED')} / UNKNOWN：{s.get('UNKNOWN')}")
        add("- 明细：`deliverables/two-end-link/CORPUS-RECONCILIATION.json`")
        add("")
    add("## 口径")
    add("")
    add("- chunk 基线 = R4 §7 学生端同名册实测：bixi1=145 / bixi2=246 / xbixi1=145 / xbixi2=105 / xbixi3=151")
    add("- 章节覆盖 = 磁盘批次 `^\\d+(\\.\\d+)+` 编号小节 ⊆ 教师库章节树（section_path 列 / 标题解析兜底）")
    add("- STAGED → ACTIVE 唯一通路 = 本校验通过 + `--promote`（PLAN §7.2 门禁 7；rights.py 状态机）")
    add("- 维度铁律：教师端 textbook_chunks 768 维 / 学生端 math_arena.chunks 1024 维，禁止交叉写索引")
    return "\n".join(out) + "\n"


# ─────────────────────────────────────────────────────────────────────────────
# 入口
# ─────────────────────────────────────────────────────────────────────────────


async def run_check(
    *,
    session: AsyncSession | None,
    promote: bool = False,
    roots: list[Path] | None = None,
    student_dsn: str | None = None,
    disk_vols: dict[str, DiskVolume] | None = None,
    student_counts: dict[str, int] | None = None,
    student_note: str | None = None,
    teacher_guide_rows: list[dict[str, str]] | None = None,
) -> IntegrityReport:
    """执行完整校验。

    session=None → 教师库侧按缺失计缺口（不伪造通过）。
    disk_vols / student_counts / teacher_guide_rows 可注入（单测）；缺省现场采集
    （磁盘 + 学生库 asyncpg + 教师库教师用书行）。
    """
    disk = disk_vols if disk_vols is not None else collect_disk_volumes(roots)
    if student_counts is not None:
        counts, note = student_counts, (student_note or "")
    else:
        dsn = resolve_student_dsn(student_dsn)
        counts, note = await collect_student_counts(dsn, list(WHITELIST_BOOK_IDS.values()))
    student_db_ok = not note
    if not counts and student_db_ok:
        student_db_ok = False
        note = "学生端库无白名单 book_id 计数行"

    db_vols = await collect_teacher_volumes(session, disk) if session is not None else {}
    if teacher_guide_rows is not None:
        tg_rows = teacher_guide_rows
    elif session is not None:
        tg_rows = await collect_teacher_guide_rows(session)
    else:
        tg_rows = None

    volumes = [
        check_volume(bid, disk.get(bid), db_vols.get(bid), counts.get(WHITELIST_BOOK_IDS[bid]))
        for bid in WHITELIST_BATCH_IDS
    ]
    report = IntegrityReport(
        generated_at=datetime.now(UTC).isoformat(timespec="seconds"),
        volumes=volumes,
        student_db_ok=student_db_ok,
        student_db_note=note,
        teacher_guides=list(tg_rows or []),
        teacher_guide_checked=tg_rows is not None,
    )

    if session is not None:
        report.reconciliation = await reconcile_corpus(session)

    if promote and report.ok and session is not None:
        for bid in WHITELIST_BATCH_IDS:
            vol = db_vols.get(bid)
            if vol is None:
                continue
            if vol.rights_status == RightsStatus.ACTIVE.value:
                report.promoted.append(f"{bid}(already-ACTIVE)")
                continue
            assert_can_transition(vol.rights_status, RightsStatus.ACTIVE.value)
            row = await session.get(TextbookSource, uuid.UUID(vol.source_id))
            if row is None:
                continue
            assert_can_transition(row.rights_status, RightsStatus.ACTIVE.value)
            row.rights_status = RightsStatus.ACTIVE.value
            await session.commit()
            report.promoted.append(bid)
    return report


async def _async_main(argv: list[str]) -> int:
    parser = argparse_namespace()
    args = parser.parse_args(argv)

    from app.db.session import session_factory

    async with session_factory()() as session:
        report = await run_check(session=session, promote=args.promote, student_dsn=args.student_dsn)

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(render_markdown(report), encoding="utf-8")
    if report.reconciliation is not None:
        args.reconciliation.parent.mkdir(parents=True, exist_ok=True)
        args.reconciliation.write_text(reconcile_corpus_json(report.reconciliation), encoding="utf-8")

    print(f"textbook-integrity :: ok={report.ok} volumes={len(report.volumes)} gaps={len(report.all_gaps())}")
    for v in report.volumes:
        status = "PASS" if v.ok else "FAIL"
        print(f"  [{status}] {v.batch_id} {v.book_name}")
        for g in v.gaps():
            print(f"         - {g}")
    if report.promoted:
        print(f"  promoted: {report.promoted}")
    print(f"  report: {args.report}")
    return 0 if report.ok else 1


def argparse_namespace():
    """CLI 参数（独立函数便于测试内省）。"""
    import argparse

    parser = argparse.ArgumentParser(description="「一套完整教材」完整性校验（PLAN §7.3）")
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT_MD, help="Markdown 报告输出路径")
    parser.add_argument(
        "--reconciliation", type=Path, default=DEFAULT_RECONCILIATION_JSON, help="对账 JSON 输出路径"
    )
    parser.add_argument(
        "--promote", action="store_true", help="校验全通过后把白名单 5 册 STAGED→ACTIVE（rights 状态机）"
    )
    parser.add_argument("--student-dsn", type=str, default=None, help="学生端库 DSN（缺省走 env/本机 dev）")
    return parser


def main() -> None:  # pragma: no cover - CLI 入口
    sys.exit(asyncio.run(_async_main(sys.argv[1:])))


if __name__ == "__main__":  # pragma: no cover
    main()
