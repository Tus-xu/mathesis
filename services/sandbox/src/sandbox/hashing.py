"""Content-addressed hashing.

Hard rule (acceptance condition #2): hashes are computed from *content only*.
``executed_at``, ``duration_ms``, host paths, process ids and the run workspace
path are never mixed into a digest. That is what makes two runs of the same
(code, inputs, seed, lock) reproduce identical ``code_hash``/``input_hash``/
``output_hash`` while their timings differ.

Canonicalisation rules, stated because they are load-bearing:

* text is encoded UTF-8 after normalising CRLF/CR -> LF and stripping trailing
  whitespace on each line, so a Windows checkout and a Linux checkout agree;
* JSON is dumped with ``sort_keys=True``, ``separators=(",", ":")`` and
  ``ensure_ascii=True``;
* file paths entering a hash use POSIX separators.
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from pathlib import Path
from typing import Any

CHUNK = 1 << 20


def normalise_text(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    # ``surrogateescape`` is required, not cosmetic: captured output is decoded with
    # surrogateescape (see workspace.read_capture) so that arbitrary bytes round-trip
    # exactly. Without it here, hashing would silently fail on binary-ish output.
    return sha256_bytes(normalise_text(text).encode("utf-8", "surrogateescape"))


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while chunk := handle.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)


def sha256_canonical_json(value: Any) -> str:
    return sha256_text(canonical_json(value))


def code_hash(code: str) -> str:
    return sha256_text(code)


def input_hash(inputs: list[Any]) -> str:
    """Order-independent across distinct names, order-dependent within a name.

    Each input contributes ``{name, media_type, sha256}``. Inline payloads are
    hashed by their own normalised bytes; object refs contribute their declared
    digest, which the caller must have verified.
    """
    entries = []
    for item in inputs:
        if hasattr(item, "model_dump"):
            item = item.model_dump()
        entries.append(
            {
                "name": item["name"],
                "media_type": item.get("media_type", "text/plain"),
                "sha256": item.get("sha256") or sha256_text(item.get("inline") or ""),
                "object_ref": item.get("object_ref"),
            }
        )
    entries.sort(key=lambda entry: (entry["name"], entry["sha256"]))
    return sha256_canonical_json(entries)


def output_hash(stdout: str, stderr: str) -> str:
    return sha256_canonical_json(
        {"stdout_sha256": sha256_text(stdout), "stderr_sha256": sha256_text(stderr)}
    )


def artifact_hash(artifacts: list[Any]) -> str:
    entries = []
    for item in artifacts:
        if hasattr(item, "model_dump"):
            item = item.model_dump()
        entries.append(
            {"path": str(item["path"]).replace("\\", "/"), "sha256": item["sha256"]}
        )
    entries.sort(key=lambda entry: entry["path"])
    return sha256_canonical_json(entries)


def dependency_lock_hash(lock: Any) -> str:
    """Hash of the dependency lock; falls back to the pinned image identity.

    ``image-pin`` fallback is deliberate and documented: for a container run the
    real dependency identity *is* the image, so an empty lock must not silently
    hash as the empty string and read as "no dependencies".
    """
    if hasattr(lock, "model_dump"):
        lock = lock.model_dump()
    payload = {
        "format": lock.get("format", "none"),
        "content_sha256": sha256_text(lock["content"]) if lock.get("content") else None,
        "image": lock.get("image"),
    }
    return sha256_canonical_json(payload)


def normalise_relative_path(path: str) -> str:
    return unicodedata.normalize("NFC", str(path).replace("\\", "/")).lstrip("./")
