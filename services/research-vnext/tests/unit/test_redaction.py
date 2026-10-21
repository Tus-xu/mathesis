"""RV02 · 脱敏与 trace 单元测试。

要求：日志/metrics/trace 串联 Browser→Gateway→Core→DB→outbox，
且 token / PII / content 进入日志前必须脱敏。
"""

from __future__ import annotations

import json
import logging

from research_vnext.core.observability.redaction import redact, redact_mapping
from research_vnext.core.observability.trace import (
    TraceContext,
    new_trace_id,
    parse_traceparent,
)


def test_bearer_token_redacted() -> None:
    s = "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.abcdefghijklmnop"
    out = redact(s)
    assert "eyJhbGciOiJIUzI1NiJ9" not in out
    assert "REDACTED" in out


def test_api_key_patterns_redacted() -> None:
    for raw in ("ak-5c3727abcdef1234567890abcdef1234", "sk-" + "z" * 32):
        assert "REDACTED" in redact(raw)


def test_phone_number_redacted() -> None:
    assert "13800138000" not in redact("用户手机 13800138000 已绑定")
    assert "REDACTED" in redact("用户手机 13800138000 已绑定")


def test_email_local_part_redacted() -> None:
    out = redact("contact: alice@example.com")
    assert "alice@example.com" not in out


def test_access_context_not_logged_raw() -> None:
    out = redact("X-Research-Access-Context: eyJhY3RvciI6IjEyMyJ9")
    assert "eyJhY3RvciI6IjEyMyJ9" not in out


def test_redact_mapping_recurses() -> None:
    data = {
        "token": "Bearer abcdefghijklmnopqrstuvwxyz012345",
        "nested": {"phone": "13800138000"},
        "safe": "hello",
    }
    out = redact_mapping(data)
    assert out["safe"] == "hello"
    assert "abcdefghijklmnopqrstuvwxyz012345" not in json.dumps(out)
    assert "13800138000" not in json.dumps(out)


def test_traceparent_roundtrip() -> None:
    tp = f"00-{new_trace_id()}-{'0'*15}1-01"
    ctx = parse_traceparent(tp)
    assert ctx is not None
    assert ctx.trace_id == tp.split("-")[1]
    assert ctx.to_traceparent().startswith("00-")


def test_invalid_traceparent_rejected() -> None:
    assert parse_traceparent("not-a-traceparent") is None


def test_log_record_carries_trace() -> None:
    ctx = TraceContext(trace_id=new_trace_id(), span_id="0" * 15 + "1")
    rec = logging.LogRecord("rv02", logging.INFO, __file__, 1, "msg", None, None)
    rec.__dict__["trace_id"] = ctx.trace_id
    assert rec.__dict__["trace_id"] == ctx.trace_id


def test_content_snippet_truncated_not_dumped() -> None:
    """长正文不得整段落进日志。"""
    long_text = "x" * 5000
    out = redact_mapping({"content": long_text})
    assert len(out["content"]) < 500


# ==========================================================================
# RV14 · L-1 回归：完整性哈希不得被误标 [REDACTED_HEX]
#
# 缺陷（RV10 登记的 L-1）：`_PATTERNS` 的裸 hex 规则把 manifest 的
# contentHash/sha256 一律打成 [REDACTED_HEX]，产物清单从此不可验证
# —— 用户拿 manifest 比对下载字节时永远失败。
# 修复语义：**键名**提示完整性（sha/hash/digest/fingerprint/imageid）且
# 值为纯 hex 时原样放行；日志正文里的裸 hex（无键名语境）仍照常脱敏。
# ==========================================================================


def test_l1_integrity_hash_survives_mapping_redaction() -> None:
    h = "ab" * 32
    out = redact_mapping(
        {
            "contentHash": h,
            "sha256": h,
            "imageId": "sha256:" + "cd" * 32,  # 带 sha256: 前缀的镜像 ID
            "fingerprint": h,
        }
    )
    assert out["contentHash"] == h
    assert out["sha256"] == h
    assert out["fingerprint"] == h
    assert out["imageId"] == "sha256:" + "cd" * 32
    assert "REDACTED" not in json.dumps(out)


def test_l1_hash_key_hint_is_case_insensitive_and_nested() -> None:
    h = "ef" * 32
    out = redact_mapping({"outputs": {"paper.pdf": {"Sha256": h, "sizeBytes": 123}}})
    assert out["outputs"]["paper.pdf"]["Sha256"] == h
    assert out["outputs"]["paper.pdf"]["sizeBytes"] == 123


def test_l1_bare_hex_in_log_text_is_still_redacted() -> None:
    """无键名语境的裸 hex（日志正文/消息文本）仍必须脱敏。"""
    assert "REDACTED" in redact("trace body " + "ab" * 32)
    out = redact_mapping({"message": "token " + "ab" * 32})
    assert "ab" * 32 not in out["message"]
    assert "REDACTED_HEX" in out["message"]


def test_l1_hash_key_with_non_hex_value_is_still_redacted() -> None:
    """键名像哈希但值不是纯 hex（如内嵌 token 的伪哈希）不得放行。"""
    out = redact_mapping({"sha256": "Bearer abcdefghijklmnopqrstuvwxyz012345"})
    assert "REDACTED" in out["sha256"]


def test_l1_short_hex_not_eligible_for_hash_pass_through() -> None:
    """<32 位 hex 即便键名像哈希也不适用放行规则（走正常脱敏判定）。"""
    out = redact_mapping({"sha256": "abcdef12"})
    assert out["sha256"] == "abcdef12"  # 短 hex 本就不命中脱敏规则 → 原样


def test_l1_job_result_manifest_roundtrip() -> None:
    """模拟 job.result 入库路径（complete_job 的 redact_mapping 调用点）。"""
    manifest = {
        "paperSpec": {"contentHash": "11" * 32, "revision": 3},
        "outputs": {
            "paper.md": {"sha256": "22" * 32, "sizeBytes": 100},
            "paper.pdf": {"sha256": "33" * 32, "sizeBytes": 5000, "pages": 1},
        },
        "compiler": {"imageId": "sha256:" + "44" * 32, "shellEscape": False},
    }
    out = redact_mapping(manifest)
    assert out["paperSpec"]["contentHash"] == "11" * 32
    assert out["outputs"]["paper.md"]["sha256"] == "22" * 32
    assert out["outputs"]["paper.pdf"]["sha256"] == "33" * 32
    assert out["compiler"]["imageId"] == "sha256:" + "44" * 32
    assert "[REDACTED_HEX]" not in json.dumps(out)
