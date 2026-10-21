"""WP-F · registry 声明 vs 现实对账（drift guards）。

**为什么需要这个文件**：`tests/contracts/test_service_registry.py` 的 11 条守卫只读
`deploy/registry.yml` 自己（纯文本子串断言），看不见现实。于是「registry 说的」与
「机器上真实存在的」可以长期矛盾而全绿。本模块补上另一半：把 registry 的**声明**
与数据库/端口/迁移的**实测**放在同一个断言里。

它**不修改** registry.yml，也不修改任何既有测试；只在发现漂移时变红。

四个对账面（对应 WP-F 要求 3a-3d）：
  a. 端口可达性对账      test_a*
  b. embedding 维度对账  test_b*
  c. schema 漂移检测     test_c*（未被任何迁移创建的表 / 迁移未落地 / 未入库的裸建表）
  d. alembic head 对账   test_d*

**当前状态（诚实声明）**：2026-09-12 首次运行时，本模块对 registry 的 PG 宿主机端口
（声明 5433，实测无监听；真实为 54330 / 54329 两个实例）有 2 条断言为**红**。
2026-09-13 registry.yml 应用 WP-F CR-1/CR-2/CR-3 后，本模块与基线在同一原子改动里
同步更新 → 当前 **25 passed**。红线仍然成立：**不得**为了让本文件变绿而弱化断言；
若某条断言无法变绿，必须留在红并解释（见 `WP-F-REPORT.md`）。

可证伪性：所有 registry 断言都通过 `registry()` / `declared_*()` **每次重新读取并解析**
`REGISTRY_TEXT`，因此对文本做变异（monkeypatch）一定能击穿对应断言。
实测证据：`deliverables/vnext-autonomous/evidence/_wp_f_falsifiability.py`
（M-1 改回 5433 → a-2 红；M-2 删登记 → b-5 红；M-3 把 5433 写成端口值 → a-3 红）。

只读保证：本模块所有 DB 访问都是 SELECT / information_schema / pg_catalog，
经 `psql_readonly()` 的写关键字守卫（见 `_registry_truth/probes.py`）。
"""

from __future__ import annotations

import os
import re
import socket
from pathlib import Path

import pytest

from _registry_truth import probes

# 复用既有守卫的 registry 文本来源（import 既有测试模块，**不修改**它）。
# 该模块的 REPO_ROOT 用 parents[4]，本模块的 repo root 由标记文件向上探测（更稳）。
try:  # pragma: no cover - 仅用于证实「同一份文本」
    from test_service_registry import _registry_text as _existing_registry_text
except Exception:  # pragma: no cover
    _existing_registry_text = None

REGISTRY_TEXT = probes.load_registry_text()
REPO_ROOT = probes.REPO_ROOT


# --- registry 访问器：**每次重新解析**当前 REGISTRY_TEXT ---------------------
# 为什么要这么做（而不是在导入时解析成常量）：导入时解析会让「在内存里变异 registry
# 文本」的测试失效——断言会继续读旧快照，于是永远不可能红（即不可证伪）。
# 本 WP 的可证伪性脚本（_wp_f_falsifiability.py）正是靠这一点工作的。
def registry() -> dict:
    return probes.registry_doc(REGISTRY_TEXT)


def registry_text() -> str:
    return REGISTRY_TEXT


def declared_service_ports() -> dict[str, int]:
    return probes.declared_service_ports(REGISTRY_TEXT)


def declared_db_ports() -> dict[str, int]:
    return probes.declared_db_ports(REGISTRY_TEXT)


def declared_embedding_dims() -> dict[str, int]:
    return probes.declared_embedding_dims(REGISTRY_TEXT)


def declared_migration_head(service: str) -> str | None:
    return probes.declared_migration_head(service, REGISTRY_TEXT)


def registry_services() -> dict:
    return probes.registry_services(REGISTRY_TEXT)

# =============================================================================
# 测量基线（MEASURED BASELINE）——这些是 2026-09-12 在**本机**实测的真相值
# =============================================================================
# 规则：基线不是「期望」，是「上次人工核实的实测快照」。现实一旦变化，
# 下面的测试会**变红**，逼迫人类重新测量并显式更新基线（不允许静默漂移）。
# 证据：deliverables/vnext-autonomous/evidence/WP-F-GROUND-TRUTH.md
# 更新记录：2026-09-13 registry.yml 应用 WP-F CR-1/CR-2/CR-3（host_port 5433->54330、
#   三个服务 database.port 收敛、登记 known_schema_conflicts），本文件基线同步更新
#   —— 同一原子改动，避免"registry 已修但基线仍记旧漂移"的僵尸断言。
MEASURED_AT = "2026-09-12"
BASELINE_UPDATED_AT = "2026-09-13"

# --- 1) 宿主机端口：共享基础设施实测映射（docker ps 实测） -------------------
# 证据：`docker ps` 实测
#   teacher-postgres     pgvector/pgvector:pg16  0.0.0.0:54330->5432/tcp
#   math-arena-postgres  pgvector/pgvector:pg15  0.0.0.0:54329->5432/tcp
#   teacher-valkey       valkey/valkey:9.1.2     0.0.0.0:6380->6379/tcp
# 说明：平台**有意**存在两个 PG 实例（8100 平台库 / 8000·8300 平台库），
# 本基线把真实映射写下来，而不是假装只有一个实例。
MEASURED_DOCKER_PORT_MAP: dict[str, dict[int, int]] = {
    "teacher-postgres": {5432: 54330},
    "math-arena-postgres": {5432: 54329},
    "teacher-valkey": {6379: 6380},
}

# service_id -> (docker 容器名, 容器内端口, 宿主机端口)
DECLARED_DB_TARGETS: dict[str, tuple[str, int, int]] = {
    "api-8000": ("math-arena-postgres", 5432, 54329),
    "teacher-platform-8100": ("teacher-postgres", 5432, 54330),
    "ppt-studio-v2-8200": ("teacher-postgres", 5432, 54330),
    "research-vnext-8300": ("math-arena-postgres", 5432, 54329),
}

# registry 的 `datastores.postgres.host_port` 值（CR-1 后 = 54330，指向默认/主实例 teacher-postgres）。
# 2026-09-12 实测时该字段是 5433（无人监听）；2026-09-13 已修正。
# 历史值 5433 保留为"已修复的 known-stale"证据：本字段是**单标量**，无法表达两个实例，
# 故 a-2 按服务逐个 `database.port` 对账（那才是权威口径）。
MEASURED_REGISTRY_HOST_PORT = 54330
MEASURED_STALE_HOST_PORT_HISTORY = 5433  # 已修正的历史值；实测无监听，见 a-3b
# 实测：dead port = 5433（2026-09-12：netstat LISTENING 中无该端口且可 bind）。保留常量供 a-3b 复核。
MEASURED_DEAD_REGISTRY_PORT = 5433

# --- 2) embedding 维度：实测（catalog 声明维度，非行内维度） -----------------
# 证据：pg_attribute + format_type 实测
#   teacher_platform(54330): knowledge_chunks.embedding vector(768) / textbook_chunks.embedding vector(768)
#                            memories.embedding vector(1024)   <-- 与铁律 768 冲突
#   math_arena(54329):       chunks/idx_*/episodic_memories/question_bank/research_kb_chunks = vector(1024)
#   research_vnext(54329):   无表、无向量列（PLANNED）
# 说明：`memories` 表**零行**，故 vector_dims() 无法取证（返回 NULL），只能用 catalog 声明类型；
# 这里有意的实测口径 = 列声明维度。
MEASURED_VECTOR_COLUMNS: dict[str, dict[str, dict[str, int]]] = {
    "teacher_platform": {
        "knowledge_chunks": {"embedding": 768},
        "memories": {"embedding": 1024},
        "textbook_chunks": {"embedding": 768},
    },
    "math_arena": {
        "chunks": {"embedding": 1024},
        "episodic_memories": {"embedding": 1024},
        "idx_chunks_embedding": {"embedding": 1024},
        "idx_episodic_memories_embedding": {"embedding": 1024},
        "idx_research_kb_chunks_embedding": {"embedding": 1024},
        "question_bank": {"embedding": 1024},
        "research_kb_chunks": {"embedding": 1024},
    },
    "research_vnext": {},
}

# registry 声明 teacher-platform-8100 = 768；实测该库存在 1024 维列 → 记录为已知红线。
# 键 = (database, table, column)，值 = 实测维度。
MEASURED_EMBEDDING_CONTRADICTIONS: dict[tuple[str, str, str], int] = {
    ("teacher_platform", "memories", "embedding"): 1024,
}

# --- 3) schema 漂移：实测「活在库里但没有被任何迁移创建」的表 -----------------
# 证据：information_schema.tables(79 张) − op.create_table 正则集合(72 张) = 7 张
#   alembic_version      —— alembic 自管表（不是漂移，白名单）
#   knowledge_chunks / knowledge_documents / memories / web_search_cache
#                        —— 由 app/db/models/knowledge.py 的 ORM 元数据 create_all 双轨
#                           建出（corpus/pipelines/load_to_db.py 走 Base.metadata.create_all）
#   kp_nodes             —— corpus/pipelines/load_to_db.py 的内联 CREATE TABLE
#   quiz_questions       —— 全仓库无 CREATE TABLE 来源（来源不可考）
# 这些表**不在** alembic/versions 里，因此 `alembic upgrade head` 无法重建全库现状。
MEASURED_UNTracked_LIVE_TABLES: dict[str, str] = {
    "alembic_version": "alembic 自管表（工具表，非业务漂移）",
    "knowledge_chunks": "ORM create_all 双轨（app/db/models/knowledge.py:80）",
    "knowledge_documents": "ORM create_all 双轨（app/db/models/knowledge.py:50）",
    "memories": "ORM create_all 双轨（app/db/models/knowledge.py:114）",
    "web_search_cache": "ORM create_all 双轨（app/db/models/knowledge.py:104）",
    "quiz_questions": "ORM create_all 双轨（app/domains/catalog/models.py:53）",
    "kp_nodes": "corpus/pipelines/load_to_db.py:57 内联 CREATE TABLE IF NOT EXISTS（无 alembic 迁移）",
}

# --- 4) alembic head：实测 ---------------------------------------------------
MEASURED_LIVE_ALEMBIC_VERSIONS: dict[str, str | None] = {
    "teacher_platform": "f19a2b3c4d5e",
    "math_arena": "research_002_research_chat",
    "research_vnext": None,  # 无 alembic_version 表（PLANNED）
}

SERVICE_DB_NAMES: dict[str, str] = {
    "api-8000": "math_arena",
    "teacher-platform-8100": "teacher_platform",
    "ppt-studio-v2-8200": "ppt_studio_v2",
    "research-vnext-8300": "research_vnext",
}

# psql 只读探测的 (容器, 用户, 数据库) —— 用户名为开发默认值，不含任何密码。
PSQL_TARGETS: dict[str, tuple[str, str]] = {
    "teacher_platform": ("teacher-postgres", "teacher"),
    "math_arena": ("math-arena-postgres", "postgres"),
    "research_vnext": ("math-arena-postgres", "postgres"),
    "ppt_studio_v2": ("teacher-postgres", "teacher"),
}


# =============================================================================
# helpers
# =============================================================================


def _docker_or_skip() -> tuple[dict[str, dict[int, int]], dict[str, str]]:
    """返回 ({容器名: {容器端口: 宿主机端口}}, {容器名: 状态})；docker 不可用 → skip。"""
    probe = probes.docker_ps(include_stopped=False)
    if not probe.available:
        pytest.skip(f"基础设施缺席：{probe.reason}")
    if not probe.containers:
        pytest.skip("基础设施缺席：`docker ps` 无任何运行中容器")
    mapping = {c["name"]: c["host_ports"] for c in probe.containers}
    status = {c["name"]: c["status"] for c in probe.containers}
    return mapping, status


def _db_skip_reason(container: str) -> str | None:
    """基础设施缺席的统一判定（docker 缺席 / 容器未运行 / 显式强制跳过）。"""
    forced = os.environ.get("WP_F_FORCE_SKIP")
    if forced:
        return f"基础设施缺席：WP_F_FORCE_SKIP={forced}（人工强制跳过 live 探测）"
    return probes.skip_reason_for_db_probe(container)


def _live_tables(database: str) -> set[str]:
    container, user = PSQL_TARGETS[database]
    reason = _db_skip_reason(container)
    if reason:
        pytest.skip(f"{reason} → 无法读取 {database} 表清单")
    res = probes.psql_readonly(
        container,
        user,
        database,
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema='public' AND table_type='BASE TABLE' ORDER BY 1",
    )
    if not res.ok:
        pytest.skip(f"基础设施缺席：无法读取 {database}（{res.reason}）")
    return {row[0] for row in res.rows}


def _live_vector_columns(database: str) -> dict[str, dict[str, int]]:
    """实测：{表: {列: 声明维度}}，用 pg_attribute + format_type 从 catalog 取。"""
    container, user = PSQL_TARGETS[database]
    reason = _db_skip_reason(container)
    if reason:
        pytest.skip(f"{reason} → 无法读取 {database} 向量列")
    sql = (
        "SELECT c.relname, a.attname, format_type(a.atttypid, a.atttypmod) "
        "FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE a.attnum > 0 AND NOT a.attisdropped AND n.nspname = 'public' "
        "AND format_type(a.atttypid, a.atttypmod) LIKE 'vector%' ORDER BY 1, 2"
    )
    res = probes.psql_readonly(container, user, database, sql)
    if not res.ok:
        pytest.skip(f"基础设施缺席：无法读取 {database} 向量列（{res.reason}）")
    out: dict[str, dict[str, int]] = {}
    for table, column, typ in res.rows:
        dims = None
        if "(" in typ and ")" in typ:
            inner = typ[typ.index("(") + 1 : typ.index(")")]
            if inner.strip().isdigit():
                dims = int(inner)
        out.setdefault(table, {})[column] = dims
    return out


def _live_alembic_version(database: str) -> tuple[str, str | None]:
    """返回 (state, version)：
      "ok"         —— 读到 live alembic_version
      "absent"     —— 库可读，但无 alembic_version 表（如 PLANNED 库）
      "unreachable"—— 库不可读（库不存在 / 端口不通）→ 调用方必须 skip

    三分状态是刻意的：把「表不存在」和「读不到」混为一谈会让基础设施缺席变成假红
    （WP-F 实测踩过：WP_F_PSQL_PORT 指向坏端口时 d-5 曾硬失败）。
    """
    container, user = PSQL_TARGETS[database]
    reason = _db_skip_reason(container)
    if reason:
        pytest.skip(f"{reason} → 无法读取 {database}.alembic_version")
    exists = probes.psql_readonly(
        container,
        user,
        database,
        "SELECT count(*) FROM information_schema.tables "
        "WHERE table_schema='public' AND table_name='alembic_version'",
    )
    if not exists.ok:
        return "unreachable", None
    if not exists.rows or exists.rows[0][0] == "0":
        return "absent", None
    res = probes.psql_readonly(container, user, database, "SELECT version_num FROM alembic_version")
    if not res.ok:
        return "unreachable", None
    if not res.rows:
        return "absent", None
    return "ok", res.rows[0][0]


def _require_alembic_version(database: str) -> tuple[str, str | None]:
    """取 live 版本；不可读 → skip（不硬失败、不静默通过）。"""
    state, version = _live_alembic_version(database)
    if state == "unreachable":
        pytest.skip(f"基础设施缺席：无法读取 {database}（库不存在或端口不可达）→ skip")
    return state, version


# =============================================================================
# 0) 自检：本模块确实在与既有守卫读同一份 registry，且只读守卫有效
# =============================================================================


def test_registry_helpers_read_same_file_as_existing_guard() -> None:
    assert probes.REGISTRY_PATH.is_file(), f"缺少 {probes.REGISTRY_PATH}"
    if _existing_registry_text is not None:
        assert _existing_registry_text() == REGISTRY_TEXT, "本模块与既有守卫读到的 registry 文本不一致"
    assert registry().get("schema_version") == "1.0.0"
    assert set(declared_service_ports()) == {
        "api-8000",
        "teacher-platform-8100",
        "ppt-studio-v2-8200",
        "ppt-presenton-8201",
        "research-vnext-8300",
    }
    # 解析器必须真的取到嵌套标量（否则后面的对账都在对空气做断言）
    assert declared_embedding_dims() == {
        "api-8000": 1024,
        "teacher-platform-8100": 768,
        "research-vnext-8300": 768,
    }
    assert declared_db_ports() == {
        "api-8000": 54329,
        "teacher-platform-8100": 54330,
        "ppt-studio-v2-8200": 54330,
        "research-vnext-8300": 54329,
    }
    assert declared_migration_head("teacher-platform-8100") == "f19a2b3c4d5e"


def test_psql_readonly_guard_rejects_writes() -> None:
    """证明本模块的 DB 访问面只能读：写关键字会被硬拒绝（未触碰数据库即返回）。"""
    for sql in (
        "INSERT INTO memories (id) VALUES (1)",
        "UPDATE memories SET kind='x'",
        "DELETE FROM memories",
        "CREATE TABLE t (a int)",
        "ALTER TABLE memories ADD COLUMN x int",
        "DROP TABLE memories",
        "TRUNCATE memories",
        "GRANT ALL ON memories TO public",
    ):
        assert probes.readonly_violation(sql) is not None, f"只读守卫漏放: {sql}"
    # 反例：纯读 SQL 与含 update/created 字样的列名不得被误判
    for sql in (
        "SELECT version_num FROM alembic_version",
        "SELECT updated_at, created_at FROM memories",
        "SELECT table_name FROM information_schema.tables",
    ):
        assert probes.readonly_violation(sql) is None, f"只读守卫误判（假阳性）: {sql}"


# =============================================================================
# a) 端口可达性对账
# =============================================================================


def test_a1_docker_port_mapping_matches_measured_port_baseline() -> None:
    """a-1：实测 docker 映射 == 基线（现实变了就红，逼人类重测基线）。"""
    mapping, status = _docker_or_skip()
    observed = {name: mapping[name] for name in MEASURED_DOCKER_PORT_MAP if name in mapping}
    missing = sorted(set(MEASURED_DOCKER_PORT_MAP) - set(observed))
    assert not missing, (
        f"基线里的容器未在运行: {missing}（实测运行中: {sorted(mapping)}）。"
        f"状态: {status}。基线测量日 {MEASURED_AT}。"
    )
    assert observed == MEASURED_DOCKER_PORT_MAP, (
        "docker 宿主机端口映射与基线不一致（端口被改过？）：\n"
        f"  期望(基线 {MEASURED_AT}): {MEASURED_DOCKER_PORT_MAP}\n"
        f"  实测: {observed}\n"
        "→ 若这是有意变更，请更新本模块基线并同步 registry.yml（见 WP-F-CHANGE-REQUEST.md）。"
    )


def test_a2_declared_service_database_ports_match_reality() -> None:
    """a-2：**registry 声明**的每服务 database.port 必须等于实测宿主机端口。

    registry 是权威 → 这里对声明本身做断言。
    2026-09-12 本测试为**红**（8100/8200/8300 都声明 5433，而实测是 54330/54330/54329）。
    2026-09-13 应用 WP-F CR-1/CR-2 后，声明已收敛到实测值 → 本测试转**绿**。
    断言强度未削弱：仍然是「registry 声明值必须等于实测值」。若日后有人把端口改回去，
    这里会立刻再红。
    """
    mapping, _status = _docker_or_skip()
    declared = declared_db_ports()
    assert declared, "registry 未声明任何 database.port（a-2 失去对账对象）"
    mismatches: list[str] = []
    for service, (container, container_port, host_port) in DECLARED_DB_TARGETS.items():
        if service not in declared:
            continue
        actual = mapping.get(container, {}).get(container_port)
        if actual is None:
            pytest.skip(f"基础设施缺席：容器 {container}:{container_port} 未运行（无法核对 {service}）")
        # 双保险：registry 声明值必须同时等于 docker 实测映射与本模块的实测基线
        if declared[service] != actual or declared[service] != host_port:
            mismatches.append(
                f"{service}: registry 声明 database.port={declared[service]} / docker 实测 "
                f"{container} {container_port}->{actual} / 模块基线 {host_port}"
            )
    assert not mismatches, (
        "registry 声明的 DB 宿主机端口与实测不符（registry-vs-reality 漂移）：\n  "
        + "\n  ".join(mismatches)
        + "\n→ 修正方式（registry.yml 归 Lead/MIG 所有）："
        "deliverables/vnext-autonomous/evidence/WP-F-CHANGE-REQUEST.md"
    )


def test_a2b_registry_host_port_is_the_default_instance_and_is_live() -> None:
    """a-2b：`datastores.postgres.host_port` 必须等于默认/主实例（teacher-postgres）实测端口且在监听。

    结构局限已记录：registry 的 `host_port` 是**单标量**，无法表达两个实例，
    因此它只承载「默认/主实例」口径；按服务逐口径由 a-2 的 `database.port` 负责
    （这也是 registry 自己在 math_arena 上用 `authority:` 指针的做法）。
    """
    declared_host_port = registry().get("datastores", {}).get("postgres", {}).get("host_port")
    assert declared_host_port == MEASURED_REGISTRY_HOST_PORT, (
        f"registry datastores.postgres.host_port={declared_host_port}，与本模块基线 "
        f"{MEASURED_REGISTRY_HOST_PORT} 不符 → 若这是有意变更，请重测并同步基线"
    )
    mapping, _status = _docker_or_skip()
    actual = mapping.get("teacher-postgres", {}).get(5432)
    assert declared_host_port == actual, (
        f"registry host_port={declared_host_port} 与 teacher-postgres 实测宿主机端口 {actual} 不符"
    )
    listening, source = probes.listening_state_with_source(declared_host_port)
    assert listening, (
        f"registry 声明的 host_port={declared_host_port} 实测未在监听（判据来源={source}）"
    )
    print(f"[WP-F a-2b] host_port={declared_host_port} 在监听（判据来源={source}）")


def test_a3_stale_host_port_5433_is_dead_and_no_longer_declared() -> None:
    """a-3：5433 仍然无人监听，且 registry 已**不再**声明它（CR-1 的回归守卫）。

    2026-09-12：本测试为**红** —— registry 的 `host_port` 就是 5433，而实测无监听。
    2026-09-13：CR-1 已应用 → registry 不再声明 5433；本测试改为断言
      (a) 5433 实测仍无监听（保持这条硬事实可见）；
      (b) registry 文本中不再有 `port: 5433` / `host_port: 5433` 这类 live 端口声明。
    注意 (b) **允许注释里提到 5433**（CR-1 要求把它记为 known-stale 历史值）——
    只禁止把它声明为端口值，否则文档化历史就会被误判为回归。
    (a) 的判据用 `definitely_not_listening()`：netstat 优先，netstat 不可用时用 bind
    兜底（能 bind ⇒ 确定无监听）。否定结论不依赖 Docker 是否在场，故本测试在
    基础设施缺席时**仍能给出真结论**，而不是一律 skip。
    """
    dead, source = probes.definitely_not_listening(MEASURED_DEAD_REGISTRY_PORT)
    assert dead, (
        f"实测 {MEASURED_DEAD_REGISTRY_PORT} 现在有人在监听了（判据来源={source}）→ 基线到期："
        f"请确认该端口的 owner 并同步 registry 与 WP-F-REPORT.md"
    )
    live_declarations = re.findall(
        rf"^\s*(?:host_)?port\s*:\s*{MEASURED_DEAD_REGISTRY_PORT}\b",
        registry_text(),
        flags=re.MULTILINE,
    )
    assert not live_declarations, (
        f"registry 仍在把已死端口 {MEASURED_DEAD_REGISTRY_PORT} 声明为端口值"
        f"（命中 {len(live_declarations)} 处）→ CR-1/CR-2 回归了。"
        "（注释中作为 known-stale 历史值提及是允许的。）"
    )
    print(f"[WP-F a-3] {MEASURED_DEAD_REGISTRY_PORT} 无监听（判据来源={source}）；registry 已无端口值声明")


def test_a4_declared_docker_ports_are_actually_listening() -> None:
    """a-4：docker 映射出来的宿主机端口必须真的在 LISTENING（防止「起了容器但端口没通」）。"""
    mapping, _status = _docker_or_skip()
    listeners = probes.netstat_listeners()
    assert listeners, "基础设施缺席：netstat 未返回任何 LISTENING 行（无法判定端口占用）"
    dead: list[str] = []
    for container, ports in mapping.items():
        for container_port, host_port in ports.items():
            if host_port not in listeners:
                dead.append(f"{container} {container_port}->{host_port}")
    assert not dead, f"容器端口映射存在但宿主机 netstat 无 LISTENING: {dead}"


def test_a5_service_port_observation_matches_registry_status_flag() -> None:
    """a-5：观测 registry ``status`` 字段（EXISTING/PLANNED）与端口实测状态，并**只对稳定信号断言**。

    实测口径 = netstat LISTENING。**诚实限制**：本机 8300 的观测在数分钟内出现过
    「LISTENING + TCP connected（uvicorn pid 47620，命令行 = services/research-vnext
    .venv uvicorn ... --port 8300）」→「进程消失、端口空闲」的翻转。既然「PLANNED 服务
    端口是否在监听」在本机不是稳定信号，本测试**不**把它当硬断言（否则就是一个
    「会随机变红」的假守卫）。稳定断言只有：registry 端口唯一 + 越界检查。
    8300 的观测事实记入 reports/evidence，供人工判断 registry 的 status 字段是否过期。
    """
    services = registry_services()
    listeners = probes.netstat_listeners()
    assert listeners, "基础设施缺席：netstat 未返回任何 LISTENING 行（无法判定端口占用）"
    observed = {}
    for sid, svc in services.items():
        port = svc.get("port")
        if not isinstance(port, int):
            continue
        observed[sid] = {
            "port": port,
            "registry_status": svc.get("status"),
            "netstat_listening": port in listeners,
            "netstat_entry": listeners.get(port),
            "tcp_connect": probes.tcp_connect_state(port, timeout=3.0),
        }
    print(f"[WP-F a-5] registry 服务端口实测（观测记录，非全部断言）: {observed}")


def test_a5b_registry_declared_ports_are_unique() -> None:
    declared = declared_service_ports()
    assert len(set(declared.values())) == len(declared), f"registry 服务端口重复: {declared}"
    for service, port in declared.items():
        assert 1 <= port <= 65535, f"{service} 端口越界: {port}"


def test_a6_service_env_database_port_matches_measured_baseline() -> None:
    """a-6：teacher-platform 的 .env DATABASE_URL 端口必须等于实测宿主机端口。

    只比对**端口整数**；`.env` 内容（含密码）绝不打印、绝不进入失败消息。
    """
    container, container_port, host_port = DECLARED_DB_TARGETS["teacher-platform-8100"]
    _docker_or_skip()
    ports = probes.env_ports(probes.TEACHER_ENV, keys=("DATABASE_URL",))
    if not ports:
        pytest.skip(f"基础设施缺席：{probes.TEACHER_ENV} 不存在或未声明 DATABASE_URL 端口")
    assert ports["DATABASE_URL"] == host_port, (
        f"teacher-platform .env 的 DATABASE_URL 端口={ports['DATABASE_URL']}，"
        f"实测 docker 映射 {container} {container_port}->{host_port}。"
        f"（脱敏 URL: {probes.env_url_redacted(probes.TEACHER_ENV)}）"
    )


def test_a7_live_db_port_is_actually_reachable_and_bogus_port_is_not() -> None:
    """a-7：真实端口可 TCP 直连；**演示**「基础设施缺席 → skip」的语义。

    环境变量：
      WP_F_PROBE_PORT  —— 覆盖探测端口（默认 = 8100 的实测宿主机端口）
      WP_F_PROBE_HOST  —— 覆盖探测主机（默认 127.0.0.1）
    把 WP_F_PROBE_PORT 指向一个没人监听的端口即可看到本测试 skip：它**不会**硬失败，
    也**不会**静默通过（skip 原因里带端口号）。
    """
    container, _container_port, default_port = DECLARED_DB_TARGETS["teacher-platform-8100"]
    reason = _db_skip_reason(container)
    if reason:
        pytest.skip(reason)
    host = os.environ.get("WP_F_PROBE_HOST", "127.0.0.1")
    raw = os.environ.get("WP_F_PROBE_PORT")
    port = int(raw) if raw and raw.isdigit() else default_port
    if not probes.tcp_reachable(port, host=host):
        pytest.skip(
            f"基础设施缺席：{host}:{port} 无法建立 TCP 连接"
            f"{'（由 WP_F_PROBE_PORT 覆盖）' if raw else ''} → skip（不硬失败、不静默通过）"
        )
    assert port == default_port, (
        f"探测端口 {port} 可连，但 8100 实测宿主机端口是 {default_port} → 基线需重测"
    )


# =============================================================================
# b) embedding 维度对账
# =============================================================================


def test_b1_live_vector_columns_match_measured_dimension_baseline() -> None:
    """b-1：实测向量列集合与维度 == 基线。新列 / 改维度都会红。"""
    observed: dict[str, dict[str, dict[str, int]]] = {}
    for database in MEASURED_VECTOR_COLUMNS:
        columns = _live_vector_columns(database)
        if not columns and not MEASURED_VECTOR_COLUMNS[database]:
            continue
        observed[database] = columns
    for database, expected_tables in MEASURED_VECTOR_COLUMNS.items():
        if database not in observed:
            continue
        actual = observed[database]
        assert set(actual) == set(expected_tables), (
            f"{database} 向量列表集合与基线不一致：\n  基线 {sorted(expected_tables)}\n  实测 {sorted(actual)}"
        )
        for table, expected_cols in expected_tables.items():
            assert actual[table] == expected_cols, (
                f"{database}.{table} 向量维度与基线不一致：基线 {expected_cols} / 实测 {actual[table]}"
            )


def test_b2_live_embedding_dims_match_registry_or_are_recorded_as_known_drift() -> None:
    """b-2：实测维度 vs registry 声明。允许的只有两种情况——相等，或已被显式登记为已知冲突。

    当前 `teacher_platform.memories.embedding`（实测 1024）不在 registry 声明(768)之内，
    故被登记进 MEASURED_EMBEDDING_CONTRADICTIONS；该表**没有**迁移、也**不在** OWNED_TABLES，
    生产 preflight 看不到它 → 这就是 WP-F 要抓的 code/schema drift。
    """
    declared = declared_embedding_dims()
    service_of_db = {"teacher_platform": "teacher-platform-8100", "math_arena": "api-8000"}
    violations: list[str] = []
    for database, service in service_of_db.items():
        if service not in declared:
            continue
        columns = _live_vector_columns(database)
        for table, cols in columns.items():
            for column, dims in cols.items():
                if dims is None:
                    violations.append(f"{database}.{table}.{column}: 维度不可测（非 vector(n)）")
                    continue
                if dims == declared[service]:
                    continue
                key = (database, table, column)
                if MEASURED_EMBEDDING_CONTRADICTIONS.get(key) == dims:
                    continue  # 已知冲突：已在 b-3 单独断言，此处不重复红
                violations.append(
                    f"{database}.{table}.{column} 实测 {dims} 维，与 registry 对 {service} 的声明 "
                    f"{declared[service]} 维不符（且未登记为已知冲突）"
                )
    assert not violations, "embedding 维度漂移（registry vs 实测）：\n  " + "\n  ".join(violations)


def test_b3_known_embedding_dimension_conflicts_still_exist() -> None:
    """b-3：把「已知冲突」钉住。现实变了（有人修好了 / 又坏了别的表）就红，强制人工重审。

    注意：本断言**故意**让 `memories=1024` 这件事实永远可见。若有人把 memories 改成 768，
    本测试会红并要求删除该条登记——这是有意的「好了也要说一声」。
    """
    seen: dict[tuple[str, str, str], int] = {}
    for database, table, column in MEASURED_EMBEDDING_CONTRADICTIONS:
        columns = _live_vector_columns(database)
        dims = columns.get(table, {}).get(column, "MISSING")
        seen[(database, table, column)] = dims  # type: ignore[assignment]
    assert seen == MEASURED_EMBEDDING_CONTRADICTIONS, (
        "已知 embedding 维度冲突集合已变化：\n"
        f"  基线 {MEASURED_EMBEDDING_CONTRADICTIONS}\n  实测 {seen}\n"
        "→ 若漂移已被修复：请从 MEASURED_EMBEDDING_CONTRADICTIONS 删除该条并同步本模块与报告；"
        "若新增冲突：请在报告中登记（severity/owner）。"
    )
    teacher_dims = declared_embedding_dims().get("teacher-platform-8100")
    assert teacher_dims == 768, f"registry 对 8100 的 embedding 声明已变（{teacher_dims}）→ 基线需重审"
    contradiction = MEASURED_EMBEDDING_CONTRADICTIONS[("teacher_platform", "memories", "embedding")]
    assert contradiction != teacher_dims, (
        "登记为冲突的维度与 registry 声明相同 → 该登记已过期，必须删除（避免永久红的假断言）"
    )


def test_b4_memories_table_is_unowned_and_created_by_no_migration() -> None:
    """b-4：`memories` 必须**仍然**是「无迁移、且不在 OWNED_TABLES」的状态，否则 red。

    这是 registry/代码/Schema 三方矛盾的根：ORM 声明 Vector(768)
    （app/db/models/knowledge.py:32,114），库里却是 1024，且无迁移、preflight 看不到。
    一旦有人补了迁移或加进 OWNED_TABLES，本测试变红 → 提醒同步 registry 与报告
    （含删除 registry 的 known_schema_conflicts 登记）。
    """
    created, reason = probes.created_tables_in_migrations(probes.ALEMBIC_VERSIONS)
    assert reason == "ok", f"无法读取迁移目录: {reason}"
    assert "memories" not in created, (
        "memories 已出现在某个迁移的 op.create_table 中 → code/schema drift 已修复，"
        "请更新本模块基线 / WP-F-REPORT.md，并从 registry 的 known_schema_conflicts 删除该条"
    )
    ownership = probes.TEACHER_ROOT / "app" / "db" / "ownership.py"
    if ownership.is_file():
        text = ownership.read_text(encoding="utf-8", errors="replace")
        assert '"memories"' not in text, (
            "memories 已被加入 app/db/ownership.py 的 OWNED_TABLES → preflight 现在能看到它了，"
            "本基线过期，必须重审（registry embedding_dims=768 vs 库内 1024 仍未解决），"
            "并从 registry 的 known_schema_conflicts 删除该条"
        )
    live = _live_tables("teacher_platform")
    assert "memories" in live, "实测 teacher_platform 已无 memories 表 → 基线过期，请重测"


def test_b5_registry_registers_the_memories_conflict_and_it_matches_reality() -> None:
    """b-5：registry 必须**登记**该冲突（CR-3），且登记内容必须与实测一致。

    背景：2026-09-12 之前 registry 只写 `teacher-platform-8100: 768`，等于暗示库内一致。
    CR-3（2026-09-13 应用）在 `embedding_dimensions.known_schema_conflicts` 下登记
    `teacher_platform.memories.embedding = vector(1024)`（0 行、无迁移、不在 OWNED_TABLES）。
    本测试做两件事：
      (1) 登记必须存在且字段与实测一致（维度/行数/无迁移/不在 OWNED_TABLES/owner）；
      (2) 一旦漂移被真正修复（迁移补上 / 维度收敛 / 进 OWNED_TABLES），本测试变红，
          要求删除登记 —— 避免登记变成永久僵尸。
    """
    registry_conflicts = registry().get("embedding_dimensions", {}).get("known_schema_conflicts")
    assert isinstance(registry_conflicts, list) and registry_conflicts, (
        "registry 的 embedding_dimensions.known_schema_conflicts 缺失或为空 → CR-3 回归了："
        "registry 又在暗示库内与 768 声明一致"
    )
    by_key = {
        (c.get("database"), c.get("table"), c.get("column")): c
        for c in registry_conflicts
        if isinstance(c, dict)
    }
    key = ("teacher_platform", "memories", "embedding")
    assert key in by_key, f"registry known_schema_conflicts 未登记 {key}（实测已知冲突），实际登记: {sorted(by_key)}"
    entry = by_key[key]
    for field in ("owner", "code_owner", "migration_owner", "action_required", "registered_at"):
        assert entry.get(field), f"registry 冲突登记 {key} 缺少 {field} 字段"

    live_dims = _live_vector_columns("teacher_platform").get("memories", {}).get("embedding")
    assert entry.get("actual_dims") == live_dims, (
        f"registry 登记 actual_dims={entry.get('actual_dims')} 与实测 {live_dims} 不符"
    )
    assert entry.get("actual_dims") == MEASURED_EMBEDDING_CONTRADICTIONS[key], (
        "registry 登记值与模块基线不一致"
    )
    declared = declared_embedding_dims().get("teacher-platform-8100")
    assert entry.get("declared_dims") == declared == 768, (
        f"registry 冲突登记 declared_dims={entry.get('declared_dims')} / registry 声明={declared}（应均为 768）"
    )

    created, reason = probes.created_tables_in_migrations(probes.ALEMBIC_VERSIONS)
    assert reason == "ok", f"无法读取迁移目录: {reason}"
    really_unmigrated = "memories" not in created
    assert entry.get("has_migration") is not really_unmigrated, (
        f"registry 登记 has_migration={entry.get('has_migration')} 与实测 {'无迁移' if really_unmigrated else '已有迁移'} 不符"
    )
    ownership_text = (probes.TEACHER_ROOT / "app" / "db" / "ownership.py").read_text(encoding="utf-8")
    really_unowned = '"memories"' not in ownership_text
    assert entry.get("in_owned_tables") is not really_unowned, (
        f"registry 登记 in_owned_tables={entry.get('in_owned_tables')} 与实测 "
        f"{'不在' if really_unowned else '已在'} OWNED_TABLES 不符"
    )
    assert entry.get("preflight_visible") is not really_unowned, "preflight_visible 应与 in_owned_tables 相反"
    assert entry.get("rows") == 0, (
        "registry 登记 rows=0 —— 若 memories 已有行，请重测并更新登记（这是安全相关事实）"
    )
    if not really_unmigrated or not really_unowned:
        pytest.fail(
            "冲突疑似已被修复（memories 已有迁移或已进 OWNED_TABLES）→ "
            "请删除 registry known_schema_conflicts 中该条与本模块 MEASURED_EMBEDDING_CONTRADICTIONS 的对应项"
        )


# =============================================================================
# c) schema 漂移检测（未被任何迁移创建的表）
# =============================================================================


def test_c1_migration_created_tables_all_exist_live() -> None:
    """c-1：迁移建过的每张表都必须真实存在于 live 库（迁移没落地 = 漂移）。"""
    created, reason = probes.created_tables_in_migrations(probes.ALEMBIC_VERSIONS)
    assert reason == "ok", f"无法读取迁移目录: {reason}"
    live = _live_tables("teacher_platform")
    missing = sorted(created - live)
    assert not missing, (
        f"迁移声明建过但 live 库不存在的表（迁移未落地/被手工删表）: {missing}\n"
        f"  迁移建表集合大小={len(created)} live 表数={len(live)}"
    )


def test_c2_live_tables_not_created_by_any_migration_match_baseline() -> None:
    """c-2：live 有、但**没有任何迁移**创建的表集合必须 == 基线。

    这是 WP-F 要求的「generic enough to catch memories」检查：任何后续新增的
    「裸建表」（ORM create_all / 内联 DDL / 手工建表）都会让本测试变红。
    解析手段：对 alembic/versions/*.py 做 `op.create_table("name"` 正则（**诚实声明**：
    正则不是 AST，不覆盖 op.execute() 里的裸 SQL；本目录实测无该形态，见报告）。
    """
    created, reason = probes.created_tables_in_migrations(probes.ALEMBIC_VERSIONS)
    assert reason == "ok", f"无法读取迁移目录: {reason}"
    live = _live_tables("teacher_platform")
    observed = sorted(live - created)
    expected = sorted(MEASURED_UNTracked_LIVE_TABLES)
    assert observed == expected, (
        "「live 存在但无任何迁移创建」的表集合发生漂移（表名增删都会红）：\n"
        f"  基线({len(expected)}): {expected}\n"
        f"  实测({len(observed)}): {observed}\n"
        "→ 新增 = 新的 schema 漂移（必须补迁移或登记）；减少 = 有人补了迁移（请更新基线）。"
    )
    # 钉住具体的 memories（WP-F 要求 c 必须能抓到它）
    assert "memories" in observed, "memories 不再是未迁移表 → code/schema drift 修复了，请更新基线"


def test_c3_every_live_table_has_a_known_creation_source() -> None:
    """c-3：每张 live 表必须能回答「谁建的」——迁移 / ORM 模型 / 显式登记。

    与 c-2 的区别：c-2 只看「有没有迁移」；c-3 要求每张无迁移表都有 ORM 模型或
    显式白名单出处（即「不可考的裸表」必须为 0）。
    """
    created, _reason = probes.created_tables_in_migrations(probes.ALEMBIC_VERSIONS)
    live = _live_tables("teacher_platform")
    orm_tables, orm_reason = probes.orm_table_names(probes.TEACHER_ROOT / "app")
    assert orm_reason == "ok", f"无法读取 ORM 模型目录: {orm_reason}"
    unexplained = sorted(t for t in live - created if t not in orm_tables and t not in MEASURED_UNTracked_LIVE_TABLES)
    assert not unexplained, (
        "live 存在、既无迁移、也无 ORM 模型、也未登记来源的表（完全不可考）：\n  "
        + "\n  ".join(unexplained)
        + "\n→ 必须先查明建表来源并在本模块登记，否则禁止视为既有事实。"
    )


def test_c4_baseline_unowned_tables_are_really_still_unowned() -> None:
    """c-4：基线里「未被迁移创建」的表，逐张复核现在**仍然**不在迁移里。

    防止基线变成僵尸：如果某张表后来被迁移接管，本测试会红并要求删掉该条登记。
    """
    created, _reason = probes.created_tables_in_migrations(probes.ALEMBIC_VERSIONS)
    resolved = sorted(name for name in MEASURED_UNTracked_LIVE_TABLES if name in created)
    assert not resolved, (
        f"以下表已被迁移接管（但仍登记在「无迁移」基线里）: {resolved}\n"
        "→ 请从 MEASURED_UNTracked_LIVE_TABLES 删除并更新 WP-F-REPORT.md"
    )


# =============================================================================
# d) alembic head 对账
# =============================================================================


def test_d1_teacher_local_migration_chain_matches_registry_head() -> None:
    """d-1：teacher-platform 本地迁移链必须单链单 head，且 head == registry.migration.head。"""
    chain, names, reason = probes.migration_revisions(probes.ALEMBIC_VERSIONS)
    assert reason == "ok", f"无法读取迁移目录: {reason}"
    heads = probes.local_heads(chain)
    assert len(heads) == 1, f"本地迁移链 head 不唯一: {sorted(heads)}（{len(names)} 个迁移文件）"
    local_head = next(iter(heads))
    declared = declared_migration_head("teacher-platform-8100")
    registry_total = registry_services()["teacher-platform-8100"]["migration"]["total"]
    assert declared == local_head, (
        f"registry.migration.head={declared} 与本地迁移链 head={local_head} 不一致（{len(chain)} 个 revision）"
    )
    assert registry_total == len(chain), (
        f"registry.migration.total={registry_total} 但本地 revision 数={len(chain)} → registry 计数过期"
    )


def test_d2_live_alembic_version_matches_measured_baseline() -> None:
    """d-2：各库 live `alembic_version` == 实测基线。"""
    observed: dict[str, str | None] = {}
    for database in MEASURED_LIVE_ALEMBIC_VERSIONS:
        state, version = _require_alembic_version(database)
        observed[database] = version if state == "ok" else None
    assert observed == MEASURED_LIVE_ALEMBIC_VERSIONS, (
        "live alembic_version 与基线不一致：\n"
        f"  基线 {MEASURED_LIVE_ALEMBIC_VERSIONS}\n  实测 {observed}\n"
        "→ 有人跑了迁移（或回滚）→ 请重测并同步基线 + registry。"
    )


def test_d3_declared_migration_heads_match_live_and_local() -> None:
    """d-3：registry 声明的每个 migration.head 必须同时等于本地链 head 与 live 库版本。"""
    declared_services = [
        sid
        for sid, svc in registry_services().items()
        if isinstance(svc.get("migration"), dict) and isinstance(svc["migration"].get("head"), str)
    ]
    results: list[str] = []
    failures: list[str] = []
    for service in declared_services:
        head = declared_migration_head(service)
        svc_path = registry_services()[service]["path"]
        versions_dir = REPO_ROOT / svc_path / "alembic" / "versions"
        database = SERVICE_DB_NAMES.get(service)
        if not versions_dir.is_dir():
            results.append(f"{service}: 本地迁移目录缺失({versions_dir})")
        else:
            chain, _names, reason = probes.migration_revisions(versions_dir)
            if reason != "ok":
                results.append(f"{service}: 迁移目录不可读({reason})")
            else:
                heads = sorted(probes.local_heads(chain))
                results.append(f"{service}: registry.head={head} local.heads={heads}")
                if heads != [head]:
                    failures.append(f"{service}: registry.head={head} != local.heads={heads}")
        if database:
            state, version = _live_alembic_version(database)
            if state != "ok":
                results.append(f"{service}: live {database} 无 alembic_version / 不可读（state={state}，skip 该项）")
            else:
                results.append(f"{service}: live {database}.alembic_version={version}")
                if version != head:
                    failures.append(f"{service}: registry.head={head} != live {database}={version}")
    print("[WP-F d-3] alembic head 对账: " + " | ".join(results))
    assert not failures, "alembic head 声明 vs 本地链/live 库不一致：\n  " + "\n  ".join(failures)


def test_d4_ppt_studio_local_head_matches_declared_or_is_recorded_drift() -> None:
    """d-4：ppt-studio-v2 的本地迁移目录若可读，则 head 必须与 registry 一致（否则显式登记）。"""
    declared = declared_migration_head("ppt-studio-v2-8200")
    versions_dir = REPO_ROOT / "services" / "ppt-studio-v2" / "alembic" / "versions"
    if not versions_dir.is_dir():
        pytest.skip(f"基础设施缺席/路径不存在：{versions_dir}（无法核对 8200 本地迁移链）")
    chain, _names, reason = probes.migration_revisions(versions_dir)
    if reason != "ok":
        pytest.skip(f"无法读取 8200 迁移目录: {reason}")
    heads = sorted(probes.local_heads(chain))
    assert heads == [declared], (
        f"registry 声明 8200 head={declared}，本地链 heads={heads}（revision 数 {len(chain)}）"
    )


def test_d5_api_8000_live_head_is_recorded_drift_not_silent() -> None:
    """d-5：api-8000 的库 live head 实测为 services/api 的 RV 迁移，而 registry 只声明 54329 口径。

    本测试不假装 registry 有声明；它把「实测值」钉住，让任何变更可见。
    """
    state, version = _require_alembic_version("math_arena")
    assert state == "ok", (
        f"math_arena 可读但未找到 alembic_version 表（state={state}，live 迁移状态不可知 → 不应静默）"
    )
    assert version == MEASURED_LIVE_ALEMBIC_VERSIONS["math_arena"], (
        f"math_arena.alembic_version 实测={version}，基线={MEASURED_LIVE_ALEMBIC_VERSIONS['math_arena']}"
        "（该 head 来自 services/api/alembic/versions/research_002_research_chat.py）"
    )
