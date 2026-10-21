"""api_inventory.json 导出器——端点清单的**单一事实源**（B5 规范 M1）。

对账三方：
1. 前端 `D:\\frontend\\src\\api\\teacherV3.ts`（REST + SSE 端点面，正则提取 teacherGet/Post/Put/Request/v3Sse）；
2. `02-ARCHITECTURE §12.3` classroom 条目（10 REST + 2 SSE，teacher-v3.1 新增）；
3. 后端 FastAPI OpenAPI（`/openapi.json`，或 TEACHER_CONTRACT_BASE_URL 指定的运行实例）。

输出 `api_inventory.json`：expected（前端∪classroom）、actual（后端）、
diff 三列表（missing / extra / method_mismatch）。**此后文档族不再手工维护端点总数**，
契约报告直接引用本文件的计数。

跑法：`python tests/contract/export_api_inventory.py [--frontend PATH] [--base-url URL] [--write]`
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).parent
# 本文件位于 services/teacher-platform/tests/contract/，上溯 3 级到仓库根。
_REPO_ROOT = HERE.resolve().parents[3]
DEFAULT_FRONTEND = _REPO_ROOT / "frontend" / "src" / "api" / "teacherV3.ts"
DEFAULT_BASE_URL = "http://127.0.0.1:8100"
INVENTORY_PATH = HERE / "api_inventory.json"

# 02-ARCHITECTURE §12.3 classroom 契约（teacher-v3.1 新增；lock|reveal 两条按两端点计）
CLASSROOM_ENDPOINTS: list[dict[str, str]] = [
    {"method": "POST", "path": "/teacher-v3/classroom/sessions", "source": "arch-§12.3", "sse": False},
    {"method": "POST", "path": "/teacher-v3/classroom/join", "source": "arch-§12.3", "sse": False},
    {"method": "POST", "path": "/teacher-v3/classroom/sessions/{id}/end", "source": "arch-§12.3", "sse": False},
    {"method": "POST", "path": "/teacher-v3/classroom/sessions/{id}/activities", "source": "arch-§12.3", "sse": False},
    {"method": "POST", "path": "/teacher-v3/classroom/sessions/{id}/activities/{aid}/lock", "source": "arch-§12.3", "sse": False},
    {"method": "POST", "path": "/teacher-v3/classroom/sessions/{id}/activities/{aid}/reveal", "source": "arch-§12.3", "sse": False},
    {"method": "POST", "path": "/teacher-v3/classroom/sessions/{id}/responses", "source": "arch-§12.3", "sse": False},
    {"method": "POST", "path": "/teacher-v3/classroom/sessions/{id}/activities/{aid}/variation", "source": "arch-§12.3", "sse": False},
    {"method": "POST", "path": "/teacher-v3/classroom/sessions/{id}/call-random", "source": "arch-§12.3", "sse": False},
    {"method": "GET", "path": "/teacher-v3/classroom/sessions/{id}/snapshot", "source": "arch-§12.3", "sse": False},
    {"method": "GET", "path": "/teacher-v3/classroom/sessions/{id}/stream", "source": "arch-§12.3", "sse": True},
    {"method": "GET", "path": "/teacher-v3/classroom/sessions/{id}/student-stream", "source": "arch-§12.3", "sse": True},
]

# B3 后端自有端点（前端不经此调用：login 由学生端 client 复用；presign 面向 A0 IFC）
BACKEND_OWNED_ENDPOINTS: list[dict[str, str]] = [
    {"method": "POST", "path": "/teacher-v3/auth/login", "source": "b3-m1", "sse": False},
    {"method": "GET", "path": "/healthz", "source": "b3-m1", "sse": False},
    {"method": "POST", "path": "/teacher-v3/upload/presign", "source": "b3-m1-ifc", "sse": False},
]

# teacherV3.ts 中的 JS 参数名 → 规范化路径参数（camelCase → snake）
_PARAM_MAP = {
    "id": "id",
    "deckId": "deck_id",
    "deckid": "deck_id",
    "slideId": "slide_id",
    "assignId": "assignment_id",
    "clusterId": "cluster_id",
    "actionId": "action_id",
    "qid": "question_id",
}

_CALL_RE = re.compile(
    r"""(?P<fn>teacherGet|teacherPost|teacherPut|teacherRequest|v3Sse)   # 调用名
        (?:<[^>(]*>)?                                                   # 可选泛型
        \(\s*
        (?P<method>'(?:GET|POST|PUT|PATCH|DELETE)'\s*,\s*)?             # teacherRequest/v3Sse 的显式 method
        (?P<quote>['"`])(?P<path>/teacher-v3/[^'"`]+)['"`]""",
    re.VERBOSE,
)


def _normalize_path(path: str) -> str:
    """`${deckId}` → `{deck_id}`。"""
    def _sub(m: re.Match[str]) -> str:
        return "{" + _PARAM_MAP.get(m.group(1), m.group(1)) + "}"

    return re.sub(r"\$\{(\w+)\}", _sub, path)


def extract_frontend_endpoints(source: str) -> list[dict[str, Any]]:
    """从 teacherV3.ts 提取端点面。SSE 通道（v3Sse）单列。"""
    found: dict[tuple[str, str], dict[str, Any]] = {}
    for m in _CALL_RE.finditer(source):
        fn = m.group("fn")
        method = m.group("method")
        if fn in ("teacherGet",):
            http_method = "GET"
        elif fn == "teacherPost":
            http_method = "POST"
        elif fn == "teacherPut":
            http_method = "PUT"
        else:
            m_method = re.search(r"(GET|POST|PUT|PATCH|DELETE)", method or "")
            http_method = m_method.group(1) if m_method else "?"
        path = _normalize_path(m.group("path").strip())
        key = (http_method, path)
        sse = fn == "v3Sse"
        entry = found.setdefault(
            key,
            {"method": http_method, "path": path, "source": "frontend-teacherV3.ts", "sse": sse},
        )
        entry["sse"] = entry["sse"] or sse
    return sorted(found.values(), key=lambda e: (e["path"], e["method"]))


def fetch_backend_endpoints(base_url: str, timeout: float = 5.0) -> tuple[list[dict[str, Any]], str]:
    """拉取后端 OpenAPI（依次探测 base_url → 8101 → 8100；教师端因 DEF-002 端口冲突临时驻 8101）。

    身份校验：info.title 必须含 "teacher"（教师平台）。8100 端口当前被 student-api
    占用（架构定案：学生端 8000 / 教师端 8100，02-ARCHITECTURE §1.3）——把学生端
    OpenAPI 误当教师端对账是致命污染，故非教师平台一律视为不可达。
    """
    for candidate in [base_url, "http://127.0.0.1:8101", "http://127.0.0.1:8100"]:
        if not candidate:
            continue
        backend, source = _fetch_one(candidate, timeout)
        if backend:
            return backend, source
    inproc = _backend_from_inprocess_app()
    if inproc:
        return inproc
    return [], "unavailable(8100/8101 均无教师平台且进程内 create_app 导入失败)"


def _fetch_one(base_url: str, timeout: float) -> tuple[list[dict[str, Any]], str]:
    title = ""
    try:
        import httpx

        resp = httpx.get(f"{base_url.rstrip('/')}/openapi.json", timeout=timeout)
        resp.raise_for_status()
        spec = resp.json()
        title = str(spec.get("info", {}).get("title", ""))
        if "teacher" not in title.lower():
            return [], f"skipped({base_url} 是 {title or '未知服务'}，非教师平台)"
        return _spec_to_endpoints(spec), f"openapi({base_url})"
    except Exception:  # noqa: BLE001
        return [], ""


def _backend_from_inprocess_app() -> tuple[list[dict[str, Any]], str] | None:
    """端口不可用时回退：进程内导入 create_app() 取 OpenAPI（骨架开发期常态）。"""
    try:
        root = str(Path(__file__).resolve().parents[2])
        if root not in sys.path:
            sys.path.insert(0, root)  # chdir 不影响模块解析，必须显式加 sys.path
        os.chdir(root)  # create_app 读 .env 相对路径
        from main import create_app  # type: ignore[attr-defined]

        spec = create_app().openapi()
        backend, source = _spec_to_endpoints(spec), "openapi(in-process create_app)"
        return backend, source
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] 进程内 OpenAPI 导入失败：{exc!r}", file=sys.stderr)
        return None


def _spec_to_endpoints(spec: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for path, methods in spec.get("paths", {}).items():
        # 口径统一：后端挂载 /api/teacher-v3（vite 代理不剥前缀），inventory 记 mock 相对口径
        norm = _normalize_path(path)
        if norm.startswith("/api/"):
            norm = norm[len("/api"):]
        for m in methods:
            if m.upper() in ("GET", "POST", "PUT", "PATCH", "DELETE"):
                out.append(
                    {
                        "method": m.upper(),
                        "path": norm,
                        "source": "backend-openapi",
                        "sse": False,  # OpenAPI 不标 SSE；由 §7.4 通道表核对
                    }
                )
    return sorted(out, key=lambda e: (e["path"], e["method"]))


def _wildcard(path: str) -> str:
    """路径参数通配化：{session_id} 与 {id} 视为同一端点（架构文档 §12.3 与实现的参数名差异不算缺口）。"""
    return re.sub(r"\{[^}]+\}", "{}", path)


def build_inventory(frontend_source: str | None, base_url: str) -> dict[str, Any]:
    frontend = extract_frontend_endpoints(frontend_source) if frontend_source else []
    expected = {(e["method"], e["path"]): dict(e) for e in frontend + CLASSROOM_ENDPOINTS + BACKEND_OWNED_ENDPOINTS}
    backend, backend_source = fetch_backend_endpoints(base_url)
    actual = {(e["method"], e["path"]): dict(e) for e in backend}

    # 对账键：method + 通配化路径（参数名差异不算缺口；前端与 §12.3 重叠是契约收敛，不算冲突）
    missing = [
        expected[k]
        for k in sorted(set(expected) - {(m, _wildcard(p)) for (m, p) in actual}, key=lambda k: (k[1], k[0]))
    ]
    expected_wc = {(m, _wildcard(p)) for (m, p) in expected}
    extra = [
        actual[k]
        for k in sorted(
            (k for k in actual if (k[0], _wildcard(k[1])) not in expected_wc),
            key=lambda k: (k[1], k[0]),
        )
    ]
    # 方法不符：同通配路径不同方法集
    fe_by_wc: dict[str, set[str]] = {}
    for (m, p) in expected:
        fe_by_wc.setdefault(_wildcard(p), set()).add(m)
    be_by_wc: dict[str, set[str]] = {}
    for (m, p) in actual:
        be_by_wc.setdefault(_wildcard(p), set()).add(m)
    method_mismatch = [
        {"path": p, "expected": sorted(fe_by_wc[p]), "actual": sorted(be_by_wc[p])}
        for p in sorted(set(fe_by_wc) & set(be_by_wc))
        if fe_by_wc[p] != be_by_wc[p]
    ]
    sse_expected = sorted(e["path"] for e in expected.values() if e.get("sse"))

    return {
        "version": 1,
        "generated_note": "端点清单单一事实源（B5 M1）。契约报告引用本文件计数，文档族不再手工维护端点总数。",
        "backend_source": backend_source,
        "counts": {
            "expected_total": len(expected),
            "expected_rest": len(expected) - len(sse_expected),
            "expected_sse": len(sse_expected),
            "frontend_declared": len(frontend),
            "classroom_arch": len(CLASSROOM_ENDPOINTS),
            "backend_owned": len(BACKEND_OWNED_ENDPOINTS),
            "backend_actual": len(actual),
            # frontend ∪ classroom ∪ backend-owned 的并集去重数（前端若已声明 classroom
            # 端点——A0 并行收敛——三者之和 > expected_total 属正常）
            "union_overlap": len(frontend) + len(CLASSROOM_ENDPOINTS) + len(BACKEND_OWNED_ENDPOINTS) - len(expected),
        },
        "expected": [expected[k] for k in sorted(expected, key=lambda k: (k[1], k[0]))],
        "actual": [actual[k] for k in sorted(actual, key=lambda k: (k[1], k[0]))],
        "diff": {
            "missing": missing,
            "extra": extra,
            "method_mismatch": method_mismatch,
        },
        "sse_channels_expected": sse_expected,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="导出 api_inventory.json（端点单一事实源）")
    ap.add_argument("--frontend", default=DEFAULT_FRONTEND)
    ap.add_argument("--base-url", default=DEFAULT_BASE_URL)
    ap.add_argument("--write", action="store_true", help="写回 tests/contract/api_inventory.json")
    args = ap.parse_args(argv)

    src: str | None = None
    fp = Path(args.frontend)
    if fp.exists():
        src = fp.read_text(encoding="utf-8")
    else:
        print(f"[warn] 前端契约文件不存在：{fp}，仅对账 classroom+backend-owned", file=sys.stderr)

    inv = build_inventory(src, args.base_url)
    counts = inv["counts"]
    print(
        f"expected={counts['expected_total']}（REST {counts['expected_rest']} + SSE {counts['expected_sse']}）"
        f" backend_actual={counts['backend_actual']}（source={inv['backend_source']}）"
        f" missing={len(inv['diff']['missing'])} extra={len(inv['diff']['extra'])}"
        f" method_mismatch={len(inv['diff']['method_mismatch'])}"
    )
    if args.write:
        INVENTORY_PATH.write_text(json.dumps(inv, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"written → {INVENTORY_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
