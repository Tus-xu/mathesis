"""RV05 · 隔离执行：Docling/pypdf 在子进程中跑，Web 进程不 import 重依赖。"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .provider import ParseDocument, ParseElement, PageMeta, ParserIdentity, ParserProvider, ParserProviderError

WORKER_SCRIPT = Path(__file__).resolve().parent / "_isolated_worker.py"


def parser_python() -> str:
    # 优先独立 parser venv（含 Docling）；Web venv 保持轻量
    default_parser = Path(__file__).resolve().parents[3] / ".parser-venv" / "Scripts" / "python.exe"
    if default_parser.is_file():
        return str(default_parser)
    return os.getenv("RESEARCH_PARSER_PYTHON") or os.getenv("MIMO_PYTHON") or sys.executable


def default_timeout_seconds() -> int:
    return int(os.getenv("RESEARCH_PARSER_TIMEOUT_SECONDS", "120"))


@dataclass
class SubprocessParseResult:
    document: ParseDocument
    identity: ParserIdentity
    input_hash: str
    output_hash: str
    engine: str


class SubprocessParserProvider:
    """typed ParserProvider：子进程隔离 + 版本/digest/license/config 记录。"""

    def __init__(self, *, engine: str = "auto", timeout_seconds: int | None = None, python_bin: str | None = None):
        self._engine = engine or os.getenv("RESEARCH_PARSER_ENGINE", "docling")
        self._timeout = timeout_seconds or default_timeout_seconds()
        self._python = python_bin or parser_python()

    @property
    def identity(self) -> ParserIdentity:
        # digest 在实际 parse 后以 engine 版本回填；此处给稳定占位 + config
        return ParserIdentity(
            name=f"subprocess:{self._engine}",
            version="rv05-isolated-1",
            digest="pending-runtime",
            license="see-engine",
            config={"engine": self._engine, "timeout_seconds": self._timeout, "isolation": "subprocess"},
        )

    def parse(self, pdf_path: Path, *, config: dict[str, Any] | None = None) -> ParseDocument:
        result = self.parse_detailed(pdf_path, config=config)
        return result.document

    def parse_detailed(self, pdf_path: Path, *, config: dict[str, Any] | None = None) -> SubprocessParseResult:
        cfg = {**(config or {}), "engine": self._engine}
        if not pdf_path.is_file():
            raise ParserProviderError("INPUT_MISSING", f"输入文件不存在: {pdf_path}", retryable=False)
        input_hash = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
        with tempfile.TemporaryDirectory(prefix="rv05-parse-") as td:
            out_path = Path(td) / "out.json"
            cmd = [
                self._python,
                str(WORKER_SCRIPT),
                "--input",
                str(pdf_path),
                "--output",
                str(out_path),
                "--engine",
                self._engine,
            ]
            env = dict(os.environ)
            env["PYTHONPATH"] = os.pathsep.join(
                [str(Path(__file__).resolve().parents[3])] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else [])
            )
            # Windows 中文 USERPROFILE 会让 docling-parse C++ filesystem 炸
            # （render_pages=True 时必现）。强制 ASCII 用户目录。
            ascii_cache = Path(__file__).resolve().parents[3] / ".parser-cache"
            ascii_cache.mkdir(parents=True, exist_ok=True)
            fake_user = ascii_cache / "userprofile"
            (fake_user / "AppData" / "Local").mkdir(parents=True, exist_ok=True)
            (fake_user / "AppData" / "Roaming").mkdir(parents=True, exist_ok=True)
            env["PYTHONUTF8"] = "1"
            env["USERPROFILE"] = str(fake_user)
            env["HOMEDRIVE"] = str(ascii_cache.drive) if ascii_cache.drive else "D:"
            env["HOMEPATH"] = str(ascii_cache.relative_to(ascii_cache.drive + "\\")) if ascii_cache.drive else str(ascii_cache)
            env["LOCALAPPDATA"] = str(fake_user / "AppData" / "Local")
            env["APPDATA"] = str(fake_user / "AppData" / "Roaming")
            env["TMPDIR"] = str(ascii_cache)
            env["TMP"] = str(ascii_cache)
            env["TEMP"] = str(ascii_cache)
            env.setdefault("DOCLING_CACHE", str(ascii_cache / "docling"))
            env.setdefault("HF_HOME", str(ascii_cache / "hf"))
            art = ascii_cache / "artifacts"
            if art.is_dir():
                env.setdefault("DOCLING_ARTIFACTS", str(art))
            try:
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=self._timeout,
                    env=env,
                    check=False,
                    cwd=str(ascii_cache),
                )
            except subprocess.TimeoutExpired as e:
                raise ParserProviderError(
                    "PARSER_TIMEOUT",
                    f"解析超时 {self._timeout}s",
                    retryable=True,
                    timeout_seconds=self._timeout,
                ) from e
            if not out_path.is_file():
                raise ParserProviderError(
                    "PARSER_CRASH",
                    f"隔离 worker 未产出结果 rc={proc.returncode}",
                    retryable=False,
                    stderr=(proc.stderr or "")[-500:],
                )
            try:
                payload = json.loads(out_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as e:
                raise ParserProviderError("PARSER_CRASH", "隔离 worker 输出非法 JSON", retryable=False) from e

        if not payload.get("ok"):
            raise ParserProviderError(
                str(payload.get("error_code") or "PARSER_CRASH"),
                str(payload.get("error_message") or "parser failed"),
                retryable=False,
            )
        doc_raw = payload.get("document") or {}
        pages = [PageMeta(**p) for p in doc_raw.get("pages") or []]
        elements = [ParseElement(**el) for el in doc_raw.get("elements") or []]
        document = ParseDocument(
            pages=pages,
            elements=elements,
            warnings=list(doc_raw.get("warnings") or []),
            succeeded_pages=list(doc_raw.get("succeeded_pages") or []),
            failed_pages=list(doc_raw.get("failed_pages") or []),
            missing_kinds=list(doc_raw.get("missing_kinds") or []),
        )
        parser_meta = doc_raw.get("parser") or {}
        identity = ParserIdentity(
            name=str(parser_meta.get("name") or f"subprocess:{self._engine}"),
            version=str(parser_meta.get("version") or "unknown"),
            digest=hashlib.sha256(f"{parser_meta}|{cfg}".encode()).hexdigest()[:32],
            license=str(parser_meta.get("license") or "UNKNOWN"),
            config=cfg,
        )
        output_hash = hashlib.sha256(
            json.dumps(doc_raw, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        return SubprocessParseResult(
            document=document,
            identity=identity,
            input_hash=input_hash,
            output_hash=output_hash,
            engine=str(parser_meta.get("name") or self._engine),
        )


def get_parser_provider(engine: str | None = None) -> ParserProvider:
    eng = engine or os.getenv("RESEARCH_PARSER_ENGINE", "docling")
    return SubprocessParserProvider(engine=eng)
