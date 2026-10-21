"""RV04 unit tests · signed URL expiry / tamper."""

from __future__ import annotations

import os
import time
import uuid

import pytest

from research_vnext.core.assets.signing import (
    SignedUrlExpired,
    SignedUrlInvalid,
    mint_token,
    parse_and_verify,
)


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    monkeypatch.setenv("RESEARCH_ASSET_SIGNING_SECRET", "unit-test-asset-signing-secret")
    yield


def test_mint_and_verify_roundtrip():
    aid = uuid.uuid4()
    tok = mint_token(asset_id=aid, version=1, ttl_seconds=60)
    parsed = parse_and_verify(tok.to_token())
    assert parsed.asset_id == str(aid)
    assert parsed.version == 1


def test_expired_token_rejected():
    aid = uuid.uuid4()
    tok = mint_token(asset_id=aid, version=1, ttl_seconds=1, now=int(time.time()) - 10)
    with pytest.raises(SignedUrlExpired):
        parse_and_verify(tok.to_token(), now=int(time.time()))


def test_tampered_signature_rejected():
    aid = uuid.uuid4()
    tok = mint_token(asset_id=aid, version=1, ttl_seconds=60)
    parts = tok.to_token().split(".")
    parts[-1] = "0" * 64
    with pytest.raises(SignedUrlInvalid):
        parse_and_verify(".".join(parts))


def test_missing_secret_fails_closed(monkeypatch):
    monkeypatch.delenv("RESEARCH_ASSET_SIGNING_SECRET", raising=False)
    monkeypatch.delenv("RESEARCH_GATEWAY_HMAC_SECRET", raising=False)
    with pytest.raises(SignedUrlInvalid):
        mint_token(asset_id=uuid.uuid4(), version=1, ttl_seconds=60)
