"""教材 V2 切片导入器（R06 引子→全量，按人工决策 D-002/D-003/D-004）。

数据源：授权教材切片镜像（目录经环境变量 ``TEXTBOOK_SLICE_ROOTS`` 配置，缺省
``D:\\ppt-rescue-control\\20260909-R05\\教材切片\\AC切片镜像\\processed``；
原 ``D:\\知识库\\AC切片`` 已不在线——PLAN §7.1：代码中写死引用一律改 env）。
分层：只落 3 层表 TextbookSource → TextbookSection → TextbookChunk；
不使用原 PDF、不使用 TextbookPage（决策 D-002）。

**入库质量门禁（PLAN §7.2，B3）——有问题的教材不得入库，整批拒绝并给出可读原因**：
1. manifest 必填四件套 + ``book_type ∈ {textbook,teacher_guide,reference}``；
   教师用书批次（book_type=teacher_guide，或 book_name 含「教师用书」——镜像中
   tbixi*/txbixi* 批次 manifest 曾误标 textbook）必须显式 ``allow_teacher_guide=True``；
2. chunks.jsonl 逐行合法 JSON、content 非空、UTF-8 无乱码/控制字符（附行号）；
3. LaTeX 定界符配对（与 8000 ``app/gateway/kb_router.py::_latex_paired`` 同源实现，
   跨服务禁止 import，故复制并注明同源）；
4. 章节树非空且 chunks.jsonl 行数 == 实际 chunk 数；
5. embedding 非空红线：批次内自带 embedding 字段时必须非空且为 **768 维**
   （教师端铁律，与 8000 1024 维隔离；本导入器自身不写 embedding，R08 索引器负责）；
6. batch_id 幂等（file_sha256 重复 → 跳过并报告）；
7. 完整性门：入库仅到 STAGED；STAGED → ACTIVE 的唯一通路 =
   ``app.textbook_v2.integrity_check`` 完整性校验通过（rights.py 状态机）。

双模式：
- dry-run：只产出计划（ImportPlan），不写 DB/MinIO；
- staged：写入 DB（rights_status=STAGED），图片仅统计不导入（asset 管线归 R07/R13）。

幂等键：`sha256(batch_id + chunks.jsonl 的 SHA256)`；同 hash 重复导入 skip，
异 hash 必须新 source 版本（唯一约束 (school_id, file_sha256) 在 DB 兜底）。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.textbook_v2.models import TextbookChunk, TextbookSection, TextbookSource

# 队伍确认的授权引用标识（决策 D-001/D-003）
RIGHTS_EVIDENCE_REF = "队伍确认授权"
STAGED_RIGHTS = "STAGED"
# 切片管线版本（授权镜像批次由 slice_md.py 出品，版本锚不变）
CHUNKER_VERSION = "slice_md-202607"
# 教材域名（授权切片 全为高中数学）
SUBJECT = "数学"

# ── PLAN §7.2 门禁 1：book_type 枚举与教师用书识别 ────────────────────────────
VALID_BOOK_TYPES = ("textbook", "teacher_guide", "reference")
# 教师用书判定：manifest book_type 或书名双保险（镜像实测 tbixi1/txbixi* 的
# manifest 误标 book_type=textbook，唯一可靠信号是书名含「教师用书」）
_TEACHER_GUIDE_NAME_MARK = "教师用书"
# 教师端 embedding 维度铁律（R4 §2.1：teacher 768 / api 1024，禁止交叉）
EMBEDDING_DIM_TEACHER = 768
# 乱码/控制字符：C0 控制符（放行 \t \n \r）+ U+FFFD 替换符（decode errors 的产物）
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\ufffd]")

# 编号章节的正则：如 "3.1"，"1.1.2"（可能带 \n/LaTeX 装饰）
_SECTION_NUM_RE = re.compile(r"^\s*(\d+(?:\.\d+)*)\s*(.*)$")

# 非编号栏目（思考/探究/观察/等等）不产生独立章节，挂到最近编号章节
_NON_NUMERIC_TITLES = {"思考", "探究", "观察", "思考交流", "归纳", "小结", "复习参考题"}


@dataclass
class ImportPlan:
    """一次导入的完整计划（dry-run 与 staged 共用）。"""

    batch_id: str
    book_name: str
    volume: str
    file_sha256: str
    import_id: str
    sources: list[dict[str, Any]] = field(default_factory=list)
    sections: list[dict[str, Any]] = field(default_factory=list)
    section_tree: list[dict[str, Any]] = field(default_factory=list)  # 展示用层级
    chunk_count: int = 0
    image_count: int = 0
    issues: list[str] = field(default_factory=list)  # 校验问题

    def summary(self) -> dict[str, Any]:
        return {
            "batch_id": self.batch_id,
            "book_name": self.book_name,
            "volume": self.volume,
            "import_id": self.import_id,
            "source_count": len(self.sources),
            "section_count": len(self.sections),
            "chunk_count": self.chunk_count,
            "image_count": self.image_count,
            "issues": self.issues,
        }


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def manifest_sha256(manifest: dict[str, Any]) -> str:
    """manifest.json 内容 hash（R12 批次/清单溯源：稳定识别批次清单版本）。"""
    return sha256_bytes(json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode("utf-8"))


def batch_import_id(batch_id: str, file_sha256: str) -> str:
    return sha256_bytes(f"{batch_id}|{file_sha256}".encode())


# ─────────────────────────────────────────────────────────────────────────────
# manifest 与输入校验
# ─────────────────────────────────────────────────────────────────────────────


def is_teacher_guide(manifest: dict[str, Any]) -> bool:
    """教师用书判定（PLAN §7.2 门禁 1 双保险）：book_type 或书名任一命中即算。"""
    if str(manifest.get("book_type") or "") == "teacher_guide":
        return True
    return _TEACHER_GUIDE_NAME_MARK in str(manifest.get("book_name") or "")


def parse_manifest(manifest_path: Path, *, allow_teacher_guide: bool = False) -> dict[str, Any]:
    """读取批次 manifest.json；缺字段/枚举非法/教师用书未放行直接抛 ValueError（整批拒绝）。

    PLAN §7.2 门禁 1：manifest 必填四件套（batch_id/book_name/volume/book_type）+
    ``book_type ∈ {textbook,teacher_guide,reference}``；教师用书批次必须显式
    ``allow_teacher_guide=True``（用户红线：有问题的教材不得入库，拒绝原因必须可读）。
    """
    with manifest_path.open(encoding="utf-8") as f:
        m = json.load(f)
    required = ("batch_id", "book_name", "volume", "book_type")
    missing = [k for k in required if not m.get(k)]
    if missing:
        raise ValueError(f"manifest 缺字段: {missing}")
    book_type = str(m["book_type"])
    if book_type not in VALID_BOOK_TYPES:
        raise ValueError(
            f"manifest book_type 非法: {book_type!r}（须为 {'/'.join(VALID_BOOK_TYPES)}，整批拒绝）"
        )
    if is_teacher_guide(m) and not allow_teacher_guide:
        raise ValueError(
            f"教师用书批次禁止入库: {m['batch_id']}（book_name={m['book_name']!r}；"
            "教师用书不在冻结白名单内，如确需导入须显式 allow_teacher_guide=True，整批拒绝）"
        )
    return m


# LaTeX 配对校验（PLAN §7.2 门禁 3；与 8000 app/gateway/kb_router.py:56-65 同源实现，
# 跨服务禁止 import——复制并注明同源，两侧修改必须同步）
_DOLLAR_PAIR_RE = re.compile(r"(?<!\$)\$(?!\$)")


def _latex_paired(text: str) -> bool:
    """公式配对检查：$$ 成对、单 $ 偶数（ADR-024；同源：kb_router._latex_paired）。"""
    dd = text.count("$$")
    if dd % 2 != 0:
        return False
    body = text.replace("$$", "")
    return len(_DOLLAR_PAIR_RE.findall(body)) % 2 == 0


def validate_chunks_lines(
    batch_dir: Path,
    *,
    allow_teacher_guide: bool = False,
) -> list[dict[str, Any]]:
    """逐行校验 chunks.jsonl（PLAN §7.2 门禁 2/3/5），任一行不过 → 整批拒绝（附行号）。

    - 门禁 2：逐行合法 JSON、content 非空、UTF-8 无乱码/控制字符（U+FFFD/C0）；
    - 门禁 3：LaTeX 定界符配对（$ 偶数、$$ 成对）；
    - 门禁 5：行内自带 embedding 字段时必须非空且为 768 维（教师端铁律）。
    返回解析后的 chunk dict 列表。
    """
    chunks_path = batch_dir / "chunks.jsonl"
    if not chunks_path.is_file():
        raise FileNotFoundError(f"缺 chunks.jsonl: {batch_dir}")
    try:
        raw = chunks_path.read_bytes()
        text = raw.decode("utf-8")  # strict：UTF-8 解码失败即乱码，整批拒绝
    except UnicodeDecodeError as e:
        raise ValueError(f"chunks.jsonl 不是合法 UTF-8（疑似乱码）: {e}") from e

    out: list[dict[str, Any]] = []
    for idx, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as e:
            raise ValueError(f"第 {idx} 行 JSON 非法（整批拒绝）: {e.msg}") from e
        if not isinstance(item, dict):
            raise ValueError(f"第 {idx} 行不是 JSON 对象（整批拒绝）")
        content = str(item.get("content") or "")
        if not content.strip():
            raise ValueError(f"第 {idx} 条 content 为空（整批拒绝）")
        bad_marks = _CONTROL_CHAR_RE.findall(content)
        if bad_marks:
            raise ValueError(
                f"第 {idx} 条 content 含乱码/控制字符 "
                f"{sorted({hex(ord(ch)) for ch in bad_marks})}（整批拒绝）"
            )
        if not _latex_paired(content):
            raise ValueError(
                f"第 {idx} 条公式配对检查未通过（$/$$ 不配对，整批拒绝，ADR-024）"
            )
        emb = item.get("embedding")
        if emb is not None:
            if not isinstance(emb, list) or not emb:
                raise ValueError(f"第 {idx} 条 embedding 为空（红线拒入，整批拒绝）")
            if len(emb) != EMBEDDING_DIM_TEACHER:
                raise ValueError(
                    f"第 {idx} 条 embedding 维度 {len(emb)} != 教师端铁律 "
                    f"{EMBEDDING_DIM_TEACHER} 维（与 8000 侧 1024 维隔离，整批拒绝）"
                )
        out.append(item)
    if not out:
        raise ValueError(f"chunks.jsonl 无有效切片行: {batch_dir}")
    return out


def validate_batch(
    batch_dir: Path, *, allow_teacher_guide: bool = False
) -> tuple[dict[str, Any], str, int]:
    """校验批次目录：manifest + chunks.jsonl 存在、大小合法；返回 (manifest, file_sha256, chunk_行数)。"""
    manifest_path = batch_dir / "manifest.json"
    chunks_path = batch_dir / "chunks.jsonl"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"缺 manifest.json: {batch_dir}")
    if not chunks_path.is_file():
        raise FileNotFoundError(f"缺 chunks.jsonl: {batch_dir}")

    size = chunks_path.stat().st_size
    if size <= 0:
        raise ValueError(f"chunks.jsonl 为空: {batch_dir}")
    if size > 512 * 1024 * 1024:  # 超大文件保护（单批 512MB 上限）
        raise ValueError(f"chunks.jsonl 超过大小上限 512MB: {batch_dir}")

    m = parse_manifest(manifest_path, allow_teacher_guide=allow_teacher_guide)
    file_sha256 = sha256_bytes(chunks_path.read_bytes())
    line_count = sum(1 for _ in chunks_path.open(encoding="utf-8"))
    return m, file_sha256, line_count


# ─────────────────────────────────────────────────────────────────────────────
# 章节树构建（编号层级 + 非编号栏目挂靠）
# ─────────────────────────────────────────────────────────────────────────────


def parse_section_title(raw: str) -> tuple[str, str | None, str]:
    """解析 section 标题 → (编号, 层级key如'3.1', 标题文本)。

    返回 (norm_title, num, title)。num 为 None 表示非编号栏目。
    """
    title = (raw or "").strip()
    if not title:
        return "", None, ""
    m = _SECTION_NUM_RE.match(title)
    if m:
        num = m.group(1)
        rest = m.group(2).strip()
        return f"{num} {rest}".strip(), num, title
    return title, None, title


def build_section_tree(
    chunks: list[dict[str, Any]],
) -> tuple[list[TextbookSection], list[int]]:
    """按切片顺序构建章节树。

    - 编号标题（"3.1 函数的概念"）→ 建/复用 TextbookSection，level=编号深度；
    - 非编号栏目（思考/探究）→ 不建独立章节，挂到最近的编号章节；
    - 返回 (sections, chunk→section 索引映射)；若全为非编号，建一个根 section。
    """
    sections: list[TextbookSection] = []
    by_num: dict[str, int] = {}  # num -> sections 索引
    trail: dict[int, int] = {}  # level -> sections 索引（父链）
    mapping: list[int] = []
    root_idx: int | None = None
    order = 0

    def new_section(level: int, title: str, norm: str, num: str | None = None) -> int:
        nonlocal order
        order += 1
        sec = TextbookSection(
            textbook_id=None,  # source id 由调用方回填
            parent_id=None,  # 父链统一由 _rebuild_parents（按 level 栈）flush 后重建
            level=level,
            order=order,
            title=title,
            normalized_title=norm,
            page_start=0,
            page_end=0,
            # B3：编号章节回填权威 section_path（模型既有列「如 1.2.3」），
            # integrity_check §7.3.2 章节覆盖与 T2 目录端点消费
            section_path=num,
        )
        sections.append(sec)
        return len(sections) - 1

    for c in chunks:
        norm, num, title = parse_section_title(c.get("section", ""))
        if num:  # 编号章节
            depth = num.count(".")
            if num not in by_num:
                idx = new_section(depth + 1, title, norm, num)
                by_num[num] = idx
                trail[depth] = idx
                if root_idx is None:
                    root_idx = idx
            mapping.append(by_num[num])
        else:  # 非编号栏目，挂到最近编号章节
            if root_idx is None and not sections:
                idx = new_section(1, "未分节", "未分节")
                root_idx = idx
            mapping.append(len(sections) - 1 if sections else 0)

    # page_start/page_end = 该 section 覆盖的 chunk 序号区间（切片无印刷页码，作顺序稳定锚）
    chunk_idx_of_section: dict[int, list[int]] = {}
    for ci, si in enumerate(mapping):
        chunk_idx_of_section.setdefault(si, []).append(ci)
    for si, idcs in chunk_idx_of_section.items():
        sections[si].page_start = idcs[0] + 1
        sections[si].page_end = idcs[-1] + 1
        # R12：direct-chunk 定位列与顺序锚同值（新 writer 权威，page_* 仅 legacy 兼容）
        sections[si].chunk_start = sections[si].page_start
        sections[si].chunk_end = sections[si].page_end

    return sections, mapping


# ─────────────────────────────────────────────────────────────────────────────
# 计划 / 执行
# ─────────────────────────────────────────────────────────────────────────────


def load_chunks(batch_dir: Path, *, allow_teacher_guide: bool = False) -> list[dict[str, Any]]:
    """读取并逐行校验 chunks.jsonl（PLAN §7.2 门禁 2/3/5 强制，违规整批拒绝）。"""
    return validate_chunks_lines(batch_dir, allow_teacher_guide=allow_teacher_guide)


def plan_import(batch_dir: Path, *, allow_teacher_guide: bool = False) -> ImportPlan:
    """dry-run：只做校验与计划，不写任何库。"""
    m, file_sha256, line_count = validate_batch(batch_dir, allow_teacher_guide=allow_teacher_guide)
    batch_id = m["batch_id"]
    plan = ImportPlan(
        batch_id=batch_id,
        book_name=m["book_name"],
        volume=m["volume"],
        file_sha256=file_sha256,
        import_id=batch_import_id(batch_id, file_sha256),
    )

    chunks = load_chunks(batch_dir, allow_teacher_guide=allow_teacher_guide)
    sections, mapping = build_section_tree(chunks)
    plan.sources.append(
        {
            "publisher": _infer_publisher(m["book_name"]),
            "edition": m["volume"],
            "grade": _infer_grade(m["book_name"], m["volume"]),
            "volume": m["volume"],
            "subject": SUBJECT,
            "rights_status": STAGED_RIGHTS,
            "rights_evidence_ref": RIGHTS_EVIDENCE_REF,
            "chunk_count": len(chunks),
            "file_sha256": file_sha256,
            "import_id": plan.import_id,
        }
    )
    plan.sections = [
        {
            "level": s.level,
            "title": s.title,
            "normalized_title": s.normalized_title,
            "section_path": s.section_path,
            "page_start": s.page_start,
            "page_end": s.page_end,
        }
        for s in sections
    ]
    plan.section_tree = build_tree_view(sections)
    plan.chunk_count = len(chunks)
    plan.image_count = _count_images(batch_dir)

    if line_count != len(chunks):
        plan.issues.append(f"manifest 行数 {line_count} != 实际 chunk {len(chunks)}")
    if not sections:
        plan.issues.append("无任何 section（章节树为空）")
    return plan


def build_tree_view(sections: list[TextbookSection]) -> list[dict[str, Any]]:
    """生成可展示的章节树视图（依赖 level + 顺序的确定性缩进，不依赖 ORM id）。"""
    out: list[dict[str, Any]] = []
    stack: list[tuple[int, int]] = []  # (level, indent)
    for s in sections:
        while stack and stack[-1][0] >= s.level:
            stack.pop()
        indent = len(stack)
        out.append(
            {
                "indent": indent,
                "title": s.title,
                "level": s.level,
                "page_range": [s.page_start, s.page_end],
            }
        )
        stack.append((s.level, indent))
    return out


def _count_images(batch_dir: Path) -> int:
    """统计批次内图片文件（images / images1..N 多子目录皆计）。"""
    count = 0
    for d in batch_dir.iterdir():
        if d.is_dir() and (d.name == "images" or d.name.startswith("images") or d.name == "image"):
            count += sum(1 for f in d.iterdir() if f.is_file())
    return count


def _infer_publisher(book_name: str) -> str:
    if "人教A" in book_name or "人教A版" in book_name:
        return "人教A版2019"
    if "北师大" in book_name:
        return "北师大版"
    if "苏教" in book_name:
        return "苏教版"
    return "未知"


def _infer_grade(book_name: str, volume: str) -> str:
    if "教师用书" in book_name:
        return "教师用书"
    if "选择性必修" in book_name:
        return "高二"
    if "必修" in book_name:
        return "高一"
    return "通用"


# ─────────────────────────────────────────────────────────────────────────────
# staged 写入（DB）
# ─────────────────────────────────────────────────────────────────────────────


async def staged_import(
    session: AsyncSession, batch_dir: Path, *, allow_teacher_guide: bool = False
) -> ImportPlan:
    """staged 导入：写 Source/Section/Chunk（rights_status=STAGED）。

    幂等：同 import_id（batch_id+sha）已存在 → skip 并返回既有计划信息。
    图片仅统计不导入（R07/R13 asset 管线负责 MinIO 与 AssetRef）。
    PLAN §7.2 门禁 7：入库仅到 STAGED；STAGED → ACTIVE 的唯一通路 =
    integrity_check 完整性校验通过（本函数提交前断言绝不越权置 ACTIVE）。
    """
    plan = plan_import(batch_dir, allow_teacher_guide=allow_teacher_guide)
    m, file_sha256, _ = validate_batch(batch_dir, allow_teacher_guide=allow_teacher_guide)
    m_sha256 = manifest_sha256(m)
    batch_id = m["batch_id"]

    # 幂等检查：同 school_id(NULL) + file_sha256 已存在 → skip（门禁 6）
    existing = (
        (
            await session.execute(
                select(TextbookSource.id).where(
                    TextbookSource.school_id.is_(None),
                    TextbookSource.file_sha256 == file_sha256,
                )
            )
        )
        .scalars()
        .first()
    )
    if existing is not None:
        plan.issues.append(f"SKIP 已存在同 hash source: {existing}")
        return plan

    source = TextbookSource(
        school_id=None,
        publisher=_infer_publisher(m["book_name"]),
        edition=m["volume"],
        grade=_infer_grade(m["book_name"], m["volume"]),
        volume=m["volume"],
        subject=SUBJECT,
        source_repo=str(batch_dir.parents[1]),
        source_commit="processed",  # 批次目录即版本锚（切片不可变镜像）
        source_path=str(batch_dir.relative_to(batch_dir.parents[2])),
        file_sha256=file_sha256,
        rights_status=STAGED_RIGHTS,
        rights_evidence_ref=RIGHTS_EVIDENCE_REF,
        parser_version=CHUNKER_VERSION,
        # R12：管线分类 + 批次/清单溯源 + 结构化 provenance（收口）
        source_kind="direct-chunk",
        batch_id=batch_id,
        manifest_sha256=m_sha256,
        provenance={
            "source_repo": str(batch_dir.parents[1]),
            "source_commit": "processed",
            "source_path": str(batch_dir.relative_to(batch_dir.parents[2])),
            "parser_version": CHUNKER_VERSION,
            "import_id": plan.import_id,
            "manifest_sha256": m_sha256,
        },
    )
    session.add(source)
    await session.flush()  # source.id 可用

    chunks = load_chunks(batch_dir, allow_teacher_guide=allow_teacher_guide)
    sections, mapping = build_section_tree(chunks)
    for s in sections:
        s.textbook_id = source.id
        session.add(s)
    await session.flush()  # section.id 可用

    # 父链统一按 level 栈重建（flush 后 id 已真）
    _rebuild_parents(sections)

    # 按 (section_id, content_hash) 去重：切片中同节内可能有完全重复段落（如课标/目录长文），
    # DB 唯一约束 uq_tbchunk_section_hash 拒绝重复；重复内容无独立检索价值，跳过并记录。
    seen_hashes: set[tuple[str, str]] = set()
    dup_count = 0
    for c, si in zip(chunks, mapping, strict=True):
        section = sections[si]
        content = c.get("content", "")
        content_hash = sha256_bytes(content.encode("utf-8"))
        key = (str(section.id), content_hash)
        if key in seen_hashes:
            dup_count += 1
            continue
        seen_hashes.add(key)
        session.add(
            TextbookChunk(
                section_id=section.id,
                text=content,
                content_hash=content_hash,
                chunker_version=CHUNKER_VERSION,
                index_version=0,
            )
        )
    if dup_count:
        plan.issues.append(f"同 section 重复内容跳过 {dup_count} 条")
    # PLAN §7.2 门禁 7：入库仅到 STAGED——提交前断言，禁止任何路径越权置 ACTIVE
    if source.rights_status != STAGED_RIGHTS:
        raise ValueError(
            f"完整性门违规：入库只允许 STAGED（当前 {source.rights_status!r}）；"
            "STAGED → ACTIVE 唯一通路 = textbook_v2.integrity_check 校验通过"
        )
    from app.textbook_v2.legacy_guards import guard_textbook_page_read_only

    guard_textbook_page_read_only(session)  # R12：dormant 表零新写入守卫
    await session.commit()
    return plan


def _rebuild_parents(sections: list[TextbookSection]) -> None:
    """按 level 栈重建 parent_id（flush 后 id 已真）。"""
    if not sections:
        return
    stack: list[TextbookSection] = []
    for s in sections:
        while stack and stack[-1].level >= s.level:
            stack.pop()
        if stack:
            s.parent_id = stack[-1].id
        else:
            s.parent_id = None
        stack.append(s)
