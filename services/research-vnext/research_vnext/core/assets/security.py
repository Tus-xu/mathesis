"""RV04 · 上传安全检查（size / MIME sniff / archive bomb / malware policy）。

原文**不进 Web 进程解析**：这里只做轻量魔数与压缩包结构探测；
深度解析/OCR/解析器隔离归后续轮次。
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass, field
from pathlib import Path

#: 单对象硬上限（默认 20 MiB，可由配置覆盖）。
DEFAULT_MAX_BYTES = 20 * 1024 * 1024

#: 压缩炸弹阈值：展开比与绝对展开大小。
MAX_ZIP_RATIO = 50
MAX_ZIP_UNCOMPRESSED = 100 * 1024 * 1024
MAX_ZIP_ENTRIES = 512

#: 伪装成文档的可执行/脚本魔数 → 直接拒绝。
_EXEC_MAGIC = (
    (b"MZ", "application/vnd.microsoft.portable-executable"),
    (b"\x7fELF", "application/x-elf"),
    (b"#!", "text/x-script"),
    (b"\xca\xfe\xba\xbe", "application/java-vm"),
)

_MAGIC = (
    (b"%PDF-", "application/pdf"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"PK\x03\x04", "application/zip"),
    (b"PK\x05\x06", "application/zip"),
    (b"\x1f\x8b", "application/gzip"),
    (b"RIFF", "image/webp"),  # refined below
)

_ALLOWED_PREFIXES = (
    "application/pdf",
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
    "application/zip",
    "application/gzip",
    "text/plain",
    "text/markdown",
    "application/json",
    "application/octet-stream",  # unknown-but-not-hostile binary; policy may still quarantine
)


class AssetRejected(Exception):
    def __init__(self, code: str, message: str, detail: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail or {}
        self.http_status = 400


@dataclass
class ScanResult:
    media_type: str
    size_bytes: int
    content_hash: str
    scan_status: str
    findings: list[dict] = field(default_factory=list)

    def to_scan_detail(self) -> dict:
        return {"findings": list(self.findings), "media_type": self.media_type}


def sniff_media_type(head: bytes, *, claimed: str = "") -> str:
    """按魔数嗅探；不信客户端 Content-Type。"""
    if head.startswith(b"MZ"):
        return "application/vnd.microsoft.portable-executable"
    if head.startswith(b"%PDF-"):
        return "application/pdf"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if head.startswith(b"PK\x03\x04") or head.startswith(b"PK\x05\x06"):
        return "application/zip"
    if head.startswith(b"\x1f\x8b"):
        return "application/gzip"
    if head.startswith(b"RIFF") and b"WEBP" in head[:16]:
        return "image/webp"
    if head.startswith(b"#!"):
        return "text/x-script"
    if head.startswith(b"\x7fELF"):
        return "application/x-elf"
    # 纯文本兜底
    try:
        sample = head[:2048]
        sample.decode("utf-8")
        if claimed.startswith("text/") or b"<" in sample or b"{" in sample:
            return "text/plain" if not claimed.startswith("text/") else claimed.split(";")[0].strip()
        return "text/plain"
    except UnicodeDecodeError:
        return "application/octet-stream"


def _is_exec_or_script(media: str) -> bool:
    return media in {
        "application/vnd.microsoft.portable-executable",
        "application/x-elf",
        "text/x-script",
        "application/java-vm",
    }


def check_archive_bomb(path: Path, *, compressed_size: int) -> list[dict]:
    """zip 结构炸弹探测：展开比 / 绝对大小 / 条目数 / 双扩展名。"""
    findings: list[dict] = []
    try:
        with zipfile.ZipFile(path) as zf:
            infos = zf.infolist()
    except zipfile.BadZipFile as e:
        raise AssetRejected("ARCHIVE_INVALID", f"压缩包损坏: {e}") from e

    if len(infos) > MAX_ZIP_ENTRIES:
        raise AssetRejected(
            "ARCHIVE_BOMB",
            "压缩包条目数超限",
            {"entries": len(infos), "max": MAX_ZIP_ENTRIES},
        )

    total_unc = 0
    for info in infos:
        total_unc += max(info.file_size, 0)
        name = info.filename.lower()
        if name.endswith((".exe", ".dll", ".bat", ".cmd", ".ps1", ".sh", ".js", ".vbs")):
            findings.append({"code": "ARCHIVE_HOSTILE_ENTRY", "name": info.filename})
        # 双扩展名伪装：invoice.pdf.exe
        parts = name.split(".")
        if len(parts) >= 3 and parts[-1] in {"exe", "dll", "bat", "cmd", "ps1", "sh"}:
            findings.append({"code": "ARCHIVE_DOUBLE_EXT", "name": info.filename})

    if total_unc > MAX_ZIP_UNCOMPRESSED:
        raise AssetRejected(
            "ARCHIVE_BOMB",
            "压缩包展开大小超限",
            {"uncompressed": total_unc, "max": MAX_ZIP_UNCOMPRESSED},
        )
    if compressed_size > 0 and total_unc / max(compressed_size, 1) > MAX_ZIP_RATIO:
        raise AssetRejected(
            "ARCHIVE_BOMB",
            "压缩包展开比超限",
            {"ratio": total_unc / max(compressed_size, 1), "max_ratio": MAX_ZIP_RATIO},
        )
    return findings


def scan_upload(
    path: Path,
    *,
    claimed_media_type: str = "",
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> ScanResult:
    """对已落盘的暂存文件做扫描；返回权威 media_type + hash + scan_status。"""
    import hashlib

    size = path.stat().st_size
    if size <= 0:
        raise AssetRejected("EMPTY_UPLOAD", "空文件")
    if size > max_bytes:
        raise AssetRejected(
            "TOO_LARGE",
            f"超过大小上限 {max_bytes} bytes",
            {"size": size, "max": max_bytes},
        )

    h = hashlib.sha256()
    head = b""
    with path.open("rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            if len(head) < 64:
                head += chunk[: 64 - len(head)]
            h.update(chunk)
    content_hash = h.hexdigest()

    media = sniff_media_type(head, claimed=claimed_media_type)
    findings: list[dict] = []

    if claimed_media_type:
        claimed_base = claimed_media_type.split(";")[0].strip().lower()
        if claimed_base and claimed_base != media and media != "application/octet-stream":
            findings.append(
                {
                    "code": "MIME_MISMATCH",
                    "claimed": claimed_base,
                    "sniffed": media,
                }
            )

    if _is_exec_or_script(media):
        raise AssetRejected(
            "MALWARE_POLICY",
            "拒绝可执行/脚本载荷",
            {"sniffed": media},
        )

    # 客户端声称 PDF/图片但魔数是 PE/ELF → MIME 伪造
    claimed_base = (claimed_media_type or "").split(";")[0].strip().lower()
    if claimed_base in {"application/pdf", "image/png", "image/jpeg"} and _is_exec_or_script(media):
        raise AssetRejected("MIME_FORGERY", "MIME 伪造", {"claimed": claimed_base, "sniffed": media})
    if claimed_base == "application/pdf" and media not in {"application/pdf", "application/octet-stream"}:
        # 声称 PDF 但不是 PDF（也不是未知二进制）→ 伪造
        if media not in {"text/plain"}:
            raise AssetRejected("MIME_FORGERY", "MIME 伪造", {"claimed": claimed_base, "sniffed": media})

    if media == "application/zip":
        findings.extend(check_archive_bomb(path, compressed_size=size))
        if any(f.get("code") in {"ARCHIVE_HOSTILE_ENTRY", "ARCHIVE_DOUBLE_EXT"} for f in findings):
            # 恶意条目 → 隔离而非 CLEAN
            return ScanResult(
                media_type=media,
                size_bytes=size,
                content_hash=content_hash,
                scan_status="QUARANTINED",
                findings=findings,
            )

    scan_status = "CLEAN"
    if media == "application/octet-stream" and not claimed_media_type:
        # 未知二进制：不静默 CLEAN
        scan_status = "QUARANTINED"
        findings.append({"code": "UNKNOWN_BINARY", "sniffed": media})

    return ScanResult(
        media_type=media,
        size_bytes=size,
        content_hash=content_hash,
        scan_status=scan_status,
        findings=findings,
    )
