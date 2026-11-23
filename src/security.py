from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from typing import Any

MAX_ALLOWED_TIMESTAMP_DELTA_SECONDS = 300


class InvalidSignatureError(ValueError):
    """Raised when an incoming webhook signature is malformed or invalid."""


class ExpiredSignatureError(ValueError):
    """Raised when a signature timestamp is outside the allowed freshness window."""


def _get_secret(secret: str | None) -> str:
    value = secret if secret is not None else os.getenv("CARE_WEBHOOK_SECRET")
    if not value or value == "dev-change-me-in-secret-manager":
        raise ValueError("A non-empty CARE_WEBHOOK_SECRET must be configured")
    return value


def serialize_canonical_json(payload: dict[str, Any]) -> bytes:
    """Serialize JSON deterministically for signing and verification."""
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
        default=str,
    ).encode("utf-8")


def generate_hmac_signature(
    payload: dict[str, Any],
    timestamp: int | None = None,
    secret: str | None = None,
) -> tuple[str, int]:
    """Sign timestamp and canonical payload; return (header, timestamp)."""
    signing_secret = _get_secret(secret)
    timestamp = int(time.time()) if timestamp is None else timestamp
    if isinstance(timestamp, bool) or not isinstance(timestamp, int):
        raise ValueError("timestamp must be an integer")
    signed_content = str(timestamp).encode("ascii") + b"." + serialize_canonical_json(payload)
    digest = hmac.new(signing_secret.encode("utf-8"), signed_content, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={digest}", timestamp


def verify_hmac_signature(
    signature_header: str,
    payload: dict[str, Any],
    secret: str | None = None,
    max_age_seconds: int = MAX_ALLOWED_TIMESTAMP_DELTA_SECONDS,
) -> bool:
    """Verify signature and freshness. Receivers should also deduplicate event IDs."""
    signing_secret = _get_secret(secret)
    if max_age_seconds < 0:
        raise ValueError("max_age_seconds cannot be negative")
    if not signature_header:
        raise InvalidSignatureError("Malformed signature header; expected 't=<timestamp>,v1=<digest>'")

    components: dict[str, str] = {}
    try:
        for item in signature_header.split(","):
            key, value = item.strip().split("=", 1)
            if key in components or key not in {"t", "v1"}:
                raise ValueError("duplicate or unexpected component")
            components[key] = value
        timestamp_text = components["t"]
        digest = components["v1"]
        timestamp = int(timestamp_text)
        if str(timestamp) != timestamp_text or len(digest) != 64:
            raise ValueError("invalid timestamp or digest format")
        int(digest, 16)
    except (ValueError, KeyError) as exc:
        raise InvalidSignatureError("Malformed signature header; expected 't=<timestamp>,v1=<digest>'") from exc

    delta = abs(int(time.time()) - timestamp)
    if delta > max_age_seconds:
        raise ExpiredSignatureError(
            f"Signature timestamp delta ({delta}s) exceeds max allowed limit ({max_age_seconds}s)"
        )

    signed_content = timestamp_text.encode("ascii") + b"." + serialize_canonical_json(payload)
    expected = hmac.new(signing_secret.encode("utf-8"), signed_content, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, digest.lower()):
        raise InvalidSignatureError("Signature digest mismatch; payload or key invalid")
    return True
