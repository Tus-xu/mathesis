"""WP-F · registry-vs-reality 只读探测助手（read-only probes）。

铁律：
- 本模块**只做读**：docker inspect/ps、psql SELECT/information_schema/pg_catalog、
  TCP 连接探测、socket bind 探测。**绝不** INSERT/UPDATE/DELETE/CREATE/ALTER/DROP。
- **绝不打印凭证**：DATABASE_URL 只被解析出 host/port 使用；密码永不进入断言、
  永不进入失败消息、永不进入 stdout。
- 所有探测失败 → 返回结构化结果（reason/ok），由调用方决定 pytest.skip，
  **不硬失败**（基础设施缺席必须 skip，不得假绿）。

复制体说明：本文件是 `tests/contracts/test_service_registry.py` 所用解析方式的复制体
（该文件用 `REPO_ROOT = Path(__file__).resolve().parents[4]` + 纯文本子串断言）。
按 WP-F 约束**不改动**那个文件；并额外提供 registry.yml 的极简 YAML 子集解析
（仅为取出 `services.*.database.port` / `embedding_dimensions.*` / `migration.head`
这类标量，registry.yml 缩进规整、无多行标量）。
"""

from __future__ import annotations

import os
import re
import socket
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# 仓库根：不依赖 parents[N] 的硬编码深度（helpers 放进子目录后深度会变）。
# 从本文件向上找标记文件 deploy/registry.yml。
# ---------------------------------------------------------------------------
_MARKER = Path("deploy") / "registry.yml"


def find_repo_root(start: Path | None = None) -> Path:
    here = (start or Path(__file__).resolve()).resolve()
    for candidate in [here, *here.parents]:
        if (candidate / _MARKER).is_file():
            return candidate
    raise RuntimeError(f"向上未找到 {_MARKER}（repo root 探测失败），起点={here}")


REPO_ROOT = find_repo_root()
CONTRACTS_DIR = Path(__file__).resolve().parent.parent
REGISTRY_PATH = REPO_ROOT / _MARKER
TEACHER_ROOT = REPO_ROOT / "services" / "teacher-platform"
TEACHER_ENV = TEACHER_ROOT / ".env"
ALEMBIC_VERSIONS = TEACHER_ROOT / "alembic" / "versions"

# ---------------------------------------------------------------------------
# registry.yml 极简解析（YAML 子集：缩进 + key: scalar）
# ---------------------------------------------------------------------------


def load_registry_text() -> str:
    return REGISTRY_PATH.read_text(encoding="utf-8")


def _strip_comment(line: str) -> str:
    out = []
    for ch in line:
        if ch == "#":
            break
        out.append(ch)
    return "".join(out)


def _parse_scalar(raw: str) -> Any:
    v = raw.strip()
    if v.endswith(","):
        v = v[:-1].strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        v = v[1:-1]
    if v in {"null", "~", ""}:
        return None
    if re.fullmatch(r"-?\d+", v):
        return int(v)
    if v in {"true", "false"}:
        return v == "true"
    return v


def registry_doc(text: str | None = None) -> dict[str, Any]:
    """把 registry.yml 解析成嵌套 dict（YAML 子集：mapping + 标量 + 映射列表）。

    支持 `- key: value` 形态的**映射列表**（block sequence of mappings），
    例如 CR-3 新增的 `embedding_dimensions.known_schema_conflicts`。
    不支持：列表套列表、多行标量（registry 目前无此形态）。
    """
    raw = text if text is not None else load_registry_text()
    root: dict[str, Any] = {}
    stack: list[tuple[int, dict[str, Any]]] = [(-1, root)]
    current_list: tuple[int, list[Any]] | None = None
    current_item: dict[str, Any] | None = None
    for line in raw.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        body = _strip_comment(line)
        if not body.strip():
            continue
        indent = len(body) - len(body.lstrip(" "))
        stripped = body.strip()
        if stripped.startswith("- "):
            if current_list is None or indent < current_list[0]:
                continue  # 游离列表项（本模块不消费）
            inner = stripped[2:].strip()
            if ":" in inner:
                item: dict[str, Any] = {}
                current_list[1].append(item)
                current_item = item
                k, _, v = inner.partition(":")
                item[k.strip()] = _parse_scalar(v)
            else:
                current_list[1].append(_parse_scalar(inner))  # 标量序列（如 forbidden_from_browser）
                current_item = None
            continue
        if ":" not in stripped:
            continue
        key, _, rest = stripped.partition(":")
        key = key.strip()
        # 列表项内部的延续键（比 `- ` 缩进更深的兄弟键）
        if current_item is not None and current_list is not None and indent > current_list[0]:
            current_item[key] = _parse_scalar(rest)
            continue
        while stack and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]
        current_item = None
        if rest.strip() == "":
            # 前看一行：下一个非注释、更深缩进的行以 "- " 开头 → 这是列表
            current_list = None
            child: Any
            if _next_is_sequence_item(raw, line):
                child = []
                current_list = (indent, child)
            else:
                child = {}
                stack.append((indent, child))
            parent[key] = child
        else:
            current_list = None
            parent[key] = _parse_scalar(rest)
    return root


def _next_is_sequence_item(raw: str, after_line: str) -> bool:
    """判断 `after_line` 之后的第一个有效行是否是 block sequence 项（`- ...`）。"""
    lines = raw.splitlines()
    try:
        idx = lines.index(after_line)
    except ValueError:
        return False
    for line in lines[idx + 1 :]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        body = _strip_comment(line)
        if not body.strip():
            continue
        stripped = body.strip()
        indent = len(body) - len(body.lstrip(" "))
        base_indent = len(after_line) - len(after_line.lstrip(" "))
        return indent > base_indent and stripped.startswith("- ")
    return False


def registry_services(text: str | None = None) -> dict[str, Any]:
    return registry_doc(text).get("services", {})


def declared_service_ports(text: str | None = None) -> dict[str, int]:
    """{service_id: port} —— 只取标量 port。"""
    return {sid: svc["port"] for sid, svc in registry_services(text).items() if isinstance(svc.get("port"), int)}


def declared_db_ports(text: str | None = None) -> dict[str, int]:
    """{service_id: database.port} —— 只对声明了 database.port 的服务。"""
    out: dict[str, int] = {}
    for sid, svc in registry_services(text).items():
        db = svc.get("database")
        if isinstance(db, dict) and isinstance(db.get("port"), int):
            out[sid] = db["port"]
    return out


def declared_embedding_dims(text: str | None = None) -> dict[str, int]:
    block = registry_doc(text).get("embedding_dimensions", {})
    return {k: v for k, v in block.items() if isinstance(v, int)}


def declared_migration_head(service: str, text: str | None = None) -> str | None:
    svc = registry_services(text).get(service, {})
    mig = svc.get("migration")
    if not isinstance(mig, dict):
        return None
    head = mig.get("head")
    if not isinstance(head, str):
        return None
    return head.split()[0].strip()


# ---------------------------------------------------------------------------
# docker 探测
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DockerProbe:
    available: bool
    reason: str
    containers: tuple[dict[str, Any], ...]


def _run(cmd: list[str], timeout: int = 20) -> tuple[int, str, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return 127, "", "executable not found"
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"
    return p.returncode, p.stdout, p.stderr


def docker_ps(include_stopped: bool = False) -> DockerProbe:
    args = ["docker", "ps", "--format", "{{.Names}}\t{{.Status}}\t{{.Ports}}"]
    if include_stopped:
        args.insert(2, "-a")
    code, out, err = _run(args)
    if code != 0:
        return DockerProbe(False, f"docker CLI 不可用（exit={code}: {err.strip()[:120] or 'no stderr'}）", ())
    items = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        items.append(
            {
                "name": parts[0].strip(),
                "status": parts[1].strip(),
                "ports_raw": parts[2].strip(),
                "host_ports": parse_docker_host_ports(parts[2]),
            }
        )
    return DockerProbe(True, "ok", tuple(items))


_PORTMAP_RE = re.compile(r"(?:([0-9.]+|\[[0-9a-fA-F:]+\]):)?(\d+)->(\d+)/(\w+)")


def parse_docker_host_ports(ports_raw: str) -> dict[int, int]:
    """从 `0.0.0.0:54330->5432/tcp, [::]:54330->5432/tcp` 解析 {容器端口: {宿主机端口}}。

    返回 {container_port: host_port}；同一容器端口出现在多个 bind 地址时取一致值。
    """
    out: dict[int, int] = {}
    for ip, host, cont, _proto in _PORTMAP_RE.findall(ports_raw or ""):
        out[int(cont)] = int(host)
    return out


# ---------------------------------------------------------------------------
# Windows 端口占用 / TCP 可达性
# ---------------------------------------------------------------------------


def netstat_listeners() -> dict[int, str]:
    """解析 `netstat -ano` 的 LISTENING 行 → {端口: "localaddr|pid"}（只读）。

    为什么用 netstat 而不是 Get-NetTCPConnection：本机实测 `Get-NetTCPConnection`
    对 8300 返回空（PowerShell 5.1 的 CIM 路径不稳），而 netstat 每次都给出
    `TCP 127.0.0.1:8300 0.0.0.0:0 LISTENING 47620`。两种输出形状不同正是
    WP-F 风险清单里点名的坑；这里选**形状稳定**的那种，并保留 bind/TCP 复核。
    """
    code, out, err = _run(["netstat", "-ano"])
    if code != 0:
        return {}
    listeners: dict[int, str] = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 4 or parts[0].upper() != "TCP":
            continue
        if parts[3].upper() != "LISTENING":
            continue
        local = parts[1]
        if ":" not in local:
            continue
        port_str = local.rsplit(":", 1)[-1]
        if not port_str.isdigit():
            continue
        listeners[int(port_str)] = f"{local}|pid={parts[4] if len(parts) > 4 else '?'}"
    return listeners


def listener_state(port: int) -> str:
    """返回 'listening' | 'free' | 'unknown'（netstat 不可用时 unknown）。"""
    listeners = netstat_listeners()
    if not listeners:
        return "unknown"
    return "listening" if port in listeners else "free"


def bind_probe(port: int, host: str = "127.0.0.1") -> bool:
    """能否 bind 到该端口：(True=空闲, False=被占用)。

    **实测告警**（2026-09-12，本机 Windows + Docker Desktop）：Docker 的端口转发
    并不会让 bind 失败——54330 明明在监听（netstat 显示 LISTENING），bind 探测却
    成功返回 True。因此 bind 探测**不能**单独作为「端口有人在听」的判据（假阴）。
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
        s.bind((host, port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def host_port_in_use(port: int) -> bool:
    """端口是否真的有服务在听（本机实测口径）。

    判据优先级：
      1. ``netstat`` LISTENING（权威、形状稳定）——命中即 True，未命中即 False；
      2. netstat 不可用 → TCP 连接成功 或 bind 失败 视为在用（保守：偏向「空闲」，
         以免把死端口判活而让「registry 声明了死端口」这条断言假绿）。
    """
    state = listener_state(port)
    if state == "listening":
        return True
    if state == "free":
        return False
    return tcp_reachable(port) or not bind_probe(port)


def listening_state_with_source(port: int) -> tuple[bool, str]:
    """返回 (是否在监听, 判据来源)。

    来源优先级：
      "netstat" —— 权威口径（netstat -ano LISTENING 表）；
      "bind"    —— netstat 不可用时，**仅对否定结论**用 bind 探测兜底
                   （能 bind ⇒ 确定无监听；这是可信的否定证据）。
    TCP connect **不**用于否定结论：实测空闲端口也会 timeout/refused 交替。
    """
    if netstat_listeners():
        return (port in netstat_listeners()), "netstat"
    return (not bind_probe(port)), "bind"


def definitely_not_listening(port: int) -> tuple[bool, str]:
    """保守判定「确定无监听」：(确定无监听?, 判据来源)。"""
    listening, source = listening_state_with_source(port)
    return (not listening), source


def tcp_connect_state(port: int, host: str = "127.0.0.1", timeout: float = 1.5) -> str:
    """返回 'connected' | 'refused' | 'timeout' | 'error'。

    **实测告警**：本机对同一空闲端口（8300）会出现 timeout / refused 交替，
    对空闲端口也会偶发 "connected"（可能是中间层排队行为）。因此**不要**把单次
    TCP 结果当作权威判据：本模块用 ``listener_state()``（netstat）作权威，
    TCP 结果只作为「已监听端口是否可达」的附加观测。
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect((host, port))
        return "connected"
    except ConnectionRefusedError:
        return "refused"
    except TimeoutError:
        return "timeout"
    except OSError:
        return "error"
    finally:
        s.close()


def tcp_reachable(port: int, host: str = "127.0.0.1", timeout: float = 1.5) -> bool:
    return tcp_connect_state(port, host=host, timeout=timeout) == "connected"


# ---------------------------------------------------------------------------
# .env 读端口（绝不回显凭证）
# ---------------------------------------------------------------------------

_ENV_LINE_RE = re.compile(r"^\s*([A-Z0-9_]+)\s*=\s*(.*)$")


def env_ports(env_path: Path | None = None, keys: tuple[str, ...] = ("DATABASE_URL",)) -> dict[str, int]:
    """从 .env 读取指定 URL 变量的端口。

    只返回端口整数：**不返回、不打印 URL 本体**（其中可能含密码）。
    """
    path = env_path or TEACHER_ENV
    if not path.is_file():
        return {}
    out: dict[str, int] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = _ENV_LINE_RE.match(line)
        if not m:
            continue
        key, value = m.group(1), m.group(2).strip()
        if key not in keys:
            continue
        # 只做端口抽取，绝不保留/回显整串
        pm = re.search(r"@[^/@\s]*?:(\d{2,5})[/\s\"']?$", value) or re.search(r"@[^/@\s]*?:(\d{2,5})", value)
        if pm:
            out[key] = int(pm.group(1))
    return out


def env_url_redacted(env_path: Path | None = None, key: str = "DATABASE_URL") -> str | None:
    """返回脱敏后的 `scheme://***@host:port/db`（**永不**含 user/password）。"""
    path = env_path or TEACHER_ENV
    if not path.is_file():
        return None
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = _ENV_LINE_RE.match(line)
        if not m or m.group(1) != key:
            continue
        value = m.group(2).strip()
        mm = re.match(r"^(?P<scheme>[a-z0-9+]+)://(?:[^@/]*@)?(?P<hostport>[^/]+)(?P<tail>/.*)?$", value)
        if not mm:
            return "***（无法解析，已整体脱敏）"
        return f"{mm.group('scheme')}://***@{mm.group('hostport')}{mm.group('tail') or ''}"
    return None


# ---------------------------------------------------------------------------
# psql 只读探测（经 docker exec；无需在测试进程内持有凭证）
# ---------------------------------------------------------------------------


_BANNED_SQL_KEYWORDS = (
    "insert",
    "update",
    "delete",
    "create",
    "alter",
    "drop",
    "truncate",
    "grant",
    "revoke",
    "refresh",
    "reindex",
    "vacuum",
)


def readonly_violation(sql: str) -> str | None:
    """只读守卫：SQL 含写关键字 → 返回违规词；否则 None。

    词边界匹配，避免 `updated_at` / `created_*` 之类列名误判。
    """
    for banned in _BANNED_SQL_KEYWORDS:
        if re.search(rf"\b{banned}\b", sql, flags=re.IGNORECASE):
            return banned
    return None


@dataclass(frozen=True)
class PsqlResult:
    ok: bool
    reason: str
    rows: tuple[tuple[str, ...], ...] = ()


def psql_readonly(container: str, user: str, database: str, sql: str, timeout: int = 25) -> PsqlResult:
    """只读 SQL：仅允许 SELECT / information_schema / pg_catalog。

    写关键字（INSERT/UPDATE/DELETE/CREATE/ALTER/DROP/TRUNCATE/GRANT/REVOKE/REFRESH/
    REINDEX/VACUUM）出现即**拒绝执行**，不碰数据库。

    探测目标可用环境变量覆盖（用于演示「基础设施缺席 → skip」）：
      WP_F_PSQL_HOST / WP_F_PSQL_PORT  —— 直连 127.0.0.1:<port>（绕过容器默认 socket）；
                                         指向无人监听的端口即可看到 skip，而不是硬失败。
    """
    violation = readonly_violation(sql)
    if violation:
        return PsqlResult(False, f"拒绝执行：SQL 含写关键字 {violation!r}（本模块只读）")
    host = os.environ.get("WP_F_PSQL_HOST")
    port = os.environ.get("WP_F_PSQL_PORT")
    args = ["docker", "exec", container, "psql", "-U", user, "-d", database]
    if host or port:
        args += ["-h", host or "127.0.0.1", "-p", port or "5432"]
    args += ["-Atc", sql]
    code, out, err = _run(args, timeout=timeout)
    if code != 0:
        return PsqlResult(False, f"psql 失败（exit={code}）: {(err or '').strip()[:200]}")
    rows = tuple(tuple(line.split("|")) for line in out.splitlines() if line.strip())
    return PsqlResult(True, "ok", rows)


def container_running(name: str) -> tuple[bool, str]:
    """(是否在运行, 原因)。docker 不可用 → (False, 原因)。"""
    code, out, err = _run(["docker", "inspect", "-f", "{{.State.Running}}", name])
    if code != 0:
        return False, f"docker inspect {name} 失败: {(err or '').strip()[:150] or f'exit={code}'}"
    if out.strip() != "true":
        return False, f"容器 {name} 未在运行"
    return True, "ok"


def skip_reason_for_db_probe(container: str) -> str | None:
    """返回 skip 原因字符串（基础设施缺席）或 None（可探测）。"""
    probe = docker_ps(include_stopped=False)
    if not probe.available:
        return f"基础设施缺席：{probe.reason}"
    running, reason = container_running(container)
    if not running:
        return f"基础设施缺席：{reason}"
    return None


# ---------------------------------------------------------------------------
# alembic 迁移解析（正则，诚实声明：非 AST 解析）
# ---------------------------------------------------------------------------

_CREATE_TABLE_RE = re.compile(r"op\.create_table\(\s*[\"']([A-Za-z0-9_]+)[\"']")
_REVISION_RE = re.compile(r"^\s*revision(?:\s*:\s*str)?\s*=\s*[\"']([^\"']+)[\"']", re.M)
_DOWN_REVISION_RE = re.compile(r"^\s*down_revision(?:\s*:\s*[^=]+)?\s*=\s*(.+)$", re.M)


def migration_revisions(versions_dir: Path) -> tuple[dict[str, str | None], tuple[str, ...], str]:
    """返回 ({revision: down_revision}, 源文件名元组, skip_reason)。

    正则解析（非 AST）：迁移文件由 alembic 模板生成，`revision = "..."` /
    `down_revision = "..."` 固定在文件头，正则足够且诚实（已在报告中声明本限制）。
    """
    if not versions_dir.is_dir():
        return {}, (), f"迁移目录不存在: {versions_dir}"
    files = sorted(versions_dir.glob("*.py"))
    if not files:
        return {}, (), f"迁移目录为空: {versions_dir}"
    chain: dict[str, str | None] = {}
    names = []
    for f in files:
        names.append(f.name)
        text = f.read_text(encoding="utf-8", errors="replace")
        rm = _REVISION_RE.search(text)
        if not rm:
            continue
        dm = _DOWN_REVISION_RE.search(text)
        down_raw = (dm.group(1).strip() if dm else "None").rstrip(",")
        down: str | None
        if down_raw in {"None", "none", "null"}:
            down = None
        else:
            dm2 = re.search(r"[\"']([^\"']+)[\"']", down_raw)
            down = dm2.group(1) if dm2 else down_raw.strip("\"'")
        chain[rm.group(1)] = down
    return chain, tuple(names), "ok"


def local_heads(chain: dict[str, str | None]) -> set[str]:
    downs = {d for d in chain.values() if d}
    return {rev for rev in chain if rev not in downs}


def created_tables_in_migrations(versions_dir: Path) -> tuple[set[str], str]:
    """正则抽取 `op.create_table("name"` 的建表名（见报告中的诚实限制声明）。"""
    if not versions_dir.is_dir():
        return set(), f"迁移目录不存在: {versions_dir}"
    found: set[str] = set()
    for f in sorted(versions_dir.glob("*.py")):
        found |= set(_CREATE_TABLE_RE.findall(f.read_text(encoding="utf-8", errors="replace")))
    return found, "ok"


# ---------------------------------------------------------------------------
# app 侧 ORM 模型表名（用于区分「迁移建的表」与「ORM/双轨建的表」）
# ---------------------------------------------------------------------------

_TABLENAME_RE = re.compile(r"__tablename__\s*=\s*[\"']([a-z0-9_]+)[\"']")


def orm_table_names(app_dir: Path) -> tuple[set[str], str]:
    if not app_dir.is_dir():
        return set(), f"app 目录不存在: {app_dir}"
    found: set[str] = set()
    for f in app_dir.rglob("*.py"):
        if "__pycache__" in f.parts:
            continue
        found |= set(_TABLENAME_RE.findall(f.read_text(encoding="utf-8", errors="replace")))
    return found, "ok"
