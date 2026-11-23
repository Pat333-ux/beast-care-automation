from __future__ import annotations

import time

import pytest

from src.security import (
    ExpiredSignatureError,
    InvalidSignatureError,
    generate_hmac_signature,
    verify_hmac_signature,
)


def test_signature_generation_and_verification() -> None:
    payload = {"case_id": "CASE-999", "application_id": "APP-999", "program_code": "CARE", "missing_fields": []}
    header, timestamp = generate_hmac_signature(payload, secret="test-secret")
    assert header.startswith(f"t={timestamp},v1=")
    assert verify_hmac_signature(header, payload, secret="test-secret") is True


def test_signature_is_stable_for_equivalent_mapping_order() -> None:
    left, _ = generate_hmac_signature({"a": 1, "b": 2}, timestamp=1_800_000_000, secret="test-secret")
    right, _ = generate_hmac_signature({"b": 2, "a": 1}, timestamp=1_800_000_000, secret="test-secret")
    assert left == right


def test_signature_detects_payload_tampering() -> None:
    header, _ = generate_hmac_signature({"case_id": "CASE-999", "amount": 100.0}, secret="test-secret")
    with pytest.raises(InvalidSignatureError, match="digest mismatch"):
        verify_hmac_signature(header, {"case_id": "CASE-999", "amount": 999.0}, secret="test-secret")


def test_signature_rejects_expired_timestamp() -> None:
    header, _ = generate_hmac_signature({"case_id": "CASE-999"}, timestamp=int(time.time()) - 600, secret="test-secret")
    with pytest.raises(ExpiredSignatureError, match="exceeds max allowed limit"):
        verify_hmac_signature(header, {"case_id": "CASE-999"}, secret="test-secret", max_age_seconds=300)


def test_signature_rejects_malformed_header() -> None:
    with pytest.raises(InvalidSignatureError):
        verify_hmac_signature("v1=bad,t=123", {}, secret="test-secret")


def test_default_secret_fails_closed() -> None:
    with pytest.raises(ValueError, match="CARE_WEBHOOK_SECRET"):
        generate_hmac_signature({})
