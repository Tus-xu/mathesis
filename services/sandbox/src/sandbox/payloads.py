"""Payload resolution: turn a request into concrete bytes that were verified.

Separated from the backends because *both* backends must see identical verified
bytes -- otherwise the recorded hashes would describe something other than what ran.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .errors import PayloadError
from .hashing import code_hash, sha256_bytes
from .models import ExecutionRequest, InputRef


@dataclass(frozen=True)
class ResolvedInput:
    ref: InputRef
    data: bytes

    @property
    def sha256(self) -> str:
        return sha256_bytes(self.data)


@dataclass
class ResolvedPayload:
    """The exact bytes that will be executed, plus their verified digests."""

    code: str
    code_hash: str
    inputs: list[ResolvedInput] = field(default_factory=list)

    def materialise(self, target_dir: Path) -> None:
        """Write code + inputs into the run workspace.

        Input names are sanitised: a name may not escape ``target_dir``, because
        "the name came from the caller" is not a defence. Path traversal here
        would be a host filesystem write primitive on the local backend.
        """
        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / "experiment.py").write_text(self.code, encoding="utf-8", newline="\n")
        inputs_dir = target_dir / "inputs"
        inputs_dir.mkdir(exist_ok=True)
        for item in self.inputs:
            (inputs_dir / sanitise_name(item.ref.name)).write_bytes(item.data)


def sanitise_name(name: str) -> str:
    """Reduce an arbitrary caller-supplied name to a single safe path component."""
    cleaned = name.replace("\\", "/").split("/")[-1].strip()
    cleaned = "".join(ch for ch in cleaned if ch.isalnum() or ch in "._-")
    if not cleaned or cleaned in {".", ".."}:
        raise PayloadError(f"input name {name!r} is not a usable file name")
    return cleaned[:128]


def resolve(req: ExecutionRequest) -> ResolvedPayload:
    """Resolve code and inputs, verifying every caller-declared digest."""
    if req.code is not None:
        code = req.code
        expected = code_hash(code)
    else:
        assert req.code_ref is not None  # guaranteed by the model validator
        ref = req.code_ref
        if ref.inline is not None:
            code = ref.inline
        elif ref.object_ref and Path(ref.object_ref).is_file():
            # Local-file fallback: object refs are normally fetched by the caller
            # (WP-H owns the rvs/ object store). Reading a path here is only valid
            # when the ref literally names an existing file.
            code = Path(ref.object_ref).read_text(encoding="utf-8")
        else:
            raise PayloadError(
                f"code_ref {ref.object_ref!r} could not be resolved: this package does not "
                "talk to the object store (object_prefix 'rvs/'); the caller must inline the "
                "code or pass a readable local path"
            )
        expected = ref.sha256

    actual = code_hash(code)
    if actual != expected:
        raise PayloadError(
            f"code digest mismatch: declared {expected}, computed {actual}. "
            "Refusing to execute unverified code."
        )

    resolved_inputs: list[ResolvedInput] = []
    for ref in req.inputs:
        if ref.inline is not None:
            data = ref.inline.encode("utf-8")
        elif ref.object_ref and Path(ref.object_ref).is_file():
            data = Path(ref.object_ref).read_bytes()
        else:
            raise PayloadError(
                f"input {ref.name!r} could not be resolved from {ref.object_ref!r}: callers must "
                "inline the payload or pass a readable local path"
            )
        digest = sha256_bytes(data)
        if ref.sha256 and digest != ref.sha256:
            raise PayloadError(
                f"input {ref.name!r} digest mismatch: declared {ref.sha256}, computed {digest}"
            )
        resolved_inputs.append(ResolvedInput(ref=ref, data=data))

    return ResolvedPayload(code=code, code_hash=actual, inputs=resolved_inputs)


def inputs_hash_payload(payload: ResolvedPayload) -> list[dict[str, str]]:
    """Shape the resolved inputs into what :func:`sandbox.hashing.input_hash` expects."""
    return [
        {
            "name": item.ref.name,
            "media_type": item.ref.media_type,
            "sha256": item.sha256,
            "object_ref": None,
        }
        for item in payload.inputs
    ]


__all__ = [
    "ResolvedInput",
    "ResolvedPayload",
    "inputs_hash_payload",
    "resolve",
    "sanitise_name",
]
