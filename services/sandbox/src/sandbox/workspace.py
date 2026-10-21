"""Per-run workspace: where code, inputs, outputs and artifacts live.

Layout (identical for both backends, so a caller cannot tell them apart by shape)::

    <root>/<run_id>-<random>/
        experiment.py      # the verified code
        inputs/            # verified inputs, one file per input name
        out/stdout.bin     # captured stdout (binary, no newline translation)
        out/stderr.bin     # captured stderr
        out/meta.json      # runner sidecar: seed, python version, guard verdict
        artifacts/         # anything the code wrote here is collected + hashed
        python/            # entrypoint_module lives here (workdir for the child)

On the container backend this whole directory is bind-mounted at ``/work`` and is
the ONLY host path the container sees. That is a read+write host mount by
construction -- it is listed as such in the manifest (``no_host_mount`` is about
*other* host paths, and the container backend's own control set documents the
workspace bind explicitly; see docs/ISOLATION-MATRIX.md).
"""

from __future__ import annotations

import os
import secrets
import shutil
from dataclasses import dataclass
from pathlib import Path

from .hashing import normalise_relative_path, sha256_file
from .models import Artifact, OutputTruncation
from .processutil import dir_size_bytes

STDOUT_NAME = "stdout.bin"
STDERR_NAME = "stderr.bin"
META_NAME = "meta.json"
INPUTS_DIR = "inputs"
OUT_DIR = "out"
ARTIFACTS_DIR = "artifacts"
PYTHON_DIR = "python"

#: Files the harness itself creates; they are never reported as user artifacts.
HARNESS_FILES = frozenset({"experiment.py", META_NAME})


@dataclass
class RunWorkspace:
    root: Path
    results_dir: Path
    artifacts_dir: Path
    inputs_dir: Path
    python_dir: Path
    max_total_bytes: int = 128 * 1024 * 1024

    @classmethod
    def create(cls, base: Path | None, run_id: str, disk_limit_mb: int) -> RunWorkspace:
        safe_run = "".join(ch for ch in run_id if ch.isalnum() or ch in "-_")[:48] or "run"
        env_root = os.environ.get("SANDBOX_WORK_ROOT", "").strip()
        base_dir = base if base is not None else (Path(env_root) if env_root else Path(_default_base()))
        root = base_dir / f"{safe_run}-{secrets.token_hex(4)}"
        results = root / OUT_DIR
        artifacts = root / ARTIFACTS_DIR
        inputs = root / INPUTS_DIR
        python_dir = root / PYTHON_DIR
        for path in (results, artifacts, inputs, python_dir):
            path.mkdir(parents=True, exist_ok=True)
        return cls(
            root=root,
            results_dir=results,
            artifacts_dir=artifacts,
            inputs_dir=inputs,
            python_dir=python_dir,
            # The declared disk limit is enforced against the workspace, with a hard
            # ceiling so a caller cannot ask for an unbounded workspace on a host disk.
            max_total_bytes=max(1, min(disk_limit_mb, 4096)) * 1024 * 1024,
        )

    # -- capture ------------------------------------------------------------------------

    def stdout_path(self) -> Path:
        return self.results_dir / STDOUT_NAME

    def stderr_path(self) -> Path:
        return self.results_dir / STDERR_NAME

    def meta_path(self) -> Path:
        return self.results_dir / META_NAME

    def read_capture(self, limit_bytes: int) -> tuple[str, str, OutputTruncation, str]:
        """Read captured output, truncating at ``limit_bytes`` per stream.

        Returns ``(stdout, stderr, truncation, cap_enforced_by)``. Truncation is
        measured on the file, so the manifest states the truth even when the cap
        was applied by the OS/shell rather than by us.
        """
        stdout_raw, out_seen = _read_capped(self.stdout_path(), limit_bytes)
        stderr_raw, err_seen = _read_capped(self.stderr_path(), limit_bytes)
        truncated = out_seen > len(stdout_raw) or err_seen > len(stderr_raw)
        return (
            decode_capture(stdout_raw),
            decode_capture(stderr_raw),
            OutputTruncation(
                truncated=truncated,
                limit_bytes=limit_bytes,
                stdout_bytes_seen=out_seen,
                stderr_bytes_seen=err_seen,
                cap_enforced_by="backend:host-read-cap",
            ),
            "backend:host-read-cap",
        )

    # -- artifacts ----------------------------------------------------------------------

    def collect_artifacts(self, limit: int = 512) -> tuple[list[Artifact], int]:
        """Hash every file the code produced. Returns ``(artifacts, total_bytes)``.

        Truncated to ``limit`` entries so a payload that creates a million files
        cannot itself become a denial of service on the manifest.
        """
        artifacts: list[Artifact] = []
        total = 0
        for path in sorted(self.artifacts_dir.rglob("*")):
            if not path.is_file():
                continue
            relative = normalise_relative_path(path.relative_to(self.artifacts_dir).as_posix())
            total += path.stat().st_size
            if len(artifacts) >= limit:
                continue
            artifacts.append(
                Artifact(path=relative, size_bytes=path.stat().st_size, sha256=sha256_file(path))
            )
        return artifacts, total

    def workspace_bytes(self) -> int:
        # Excludes the harness's own output directory: stdout/stderr are governed by
        # the output cap, not by the disk limit.
        return dir_size_bytes(self.root, exclude=(OUT_DIR,))

    def read_meta(self) -> str:
        try:
            return self.meta_path().read_text(encoding="utf-8")
        except OSError:
            return ""

    def cleanup(self, keep: bool = False) -> None:
        if not keep:
            shutil.rmtree(self.root, ignore_errors=True)


def _read_capped(path: Path, limit: int) -> tuple[bytes, int]:
    """Read at most ``limit`` bytes but report how many the file actually holds."""
    if not path.exists():
        return b"", 0
    seen = path.stat().st_size
    with path.open("rb") as handle:
        data = handle.read(limit)
    return data, seen


#: Decoding rule for captured output, applied in exactly one place.
#
# ``surrogateescape`` (not ``replace``) because output hashing must round-trip the
# exact bytes the workload produced: with ``replace`` two different byte sequences
# collapse to U+FFFD, so ``output_hash`` could be identical for different outputs.
# This is a *hash correctness* requirement, not a nicety.
CAPTURE_ENCODING = "utf-8"
CAPTURE_ERRORS = "surrogateescape"


def decode_capture(data: bytes) -> str:
    return data.decode(CAPTURE_ENCODING, CAPTURE_ERRORS)


def encode_capture(text: str) -> bytes:
    return text.encode(CAPTURE_ENCODING, CAPTURE_ERRORS)


def _default_base() -> str:
    import tempfile

    return str(Path(tempfile.gettempdir()) / "math-arena-sandbox")
