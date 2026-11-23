from __future__ import annotations

import os
import uuid
from typing import Any, Callable

from pydantic import BaseModel, Field

from src.models import CareCaseRecord, CaseStatus, UrgencyLevel
from src.security import generate_hmac_signature

DIV19_EMERGENCY_ENDPOINT = os.getenv("DIV19_EMERGENCY_ENDPOINT", "https://div19-emergency.internal/v1/circuit-breaker")


class EmergencyEscalationEvent(BaseModel):
    event_id: str = Field(..., min_length=3, max_length=128)
    case_id: str = Field(..., min_length=3, max_length=64)
    source_division: str = "beast-care-automation"
    urgency: UrgencyLevel = UrgencyLevel.CRISIS
    trigger_reason: str = Field(..., min_length=1, max_length=500)
    safe_payload: dict[str, Any]


class EmergencyDispatchResult(BaseModel):
    event_id: str
    case_id: str
    dispatched: bool
    signature_sha256: str
    detail: str


def build_emergency_event(case: CareCaseRecord, reason: str) -> EmergencyEscalationEvent:
    """Build a minimized crisis event; reject non-crisis and non-escalated cases."""
    if case.intake.urgency != UrgencyLevel.CRISIS or case.status != CaseStatus.ESCALATED:
        raise ValueError("Division 19 escalation is only available for escalated crisis cases")
    cleaned_reason = reason.strip()
    if not cleaned_reason:
        raise ValueError("An escalation reason is required")
    # Keep the outbound crisis contract minimal. No applicant identity, DOB,
    # free-text intake notes, document names, or financial details are included.
    return EmergencyEscalationEvent(
        event_id=f"EMERGENCY-EVENT-{case.intake.case_id}",
        case_id=case.intake.case_id,
        urgency=UrgencyLevel.CRISIS,
        trigger_reason=cleaned_reason,
        safe_payload={"case_id": case.intake.case_id, "urgency": UrgencyLevel.CRISIS.value},
    )


def dispatch_to_div19(
    case: CareCaseRecord,
    reason: str,
    transport_sender: Callable[[dict[str, Any], str], None] | None = None,
    *,
    secret: str | None = None,
) -> EmergencyDispatchResult:
    """Sign and deliver a crisis event via an explicitly configured transport.

    With no transport this runs in dry-run mode and reports dispatched=False.
    The default development secret is deliberately not accepted.
    """
    event = build_emergency_event(case, reason)
    payload = event.model_dump(mode="json")
    try:
        signature, _ = generate_hmac_signature(payload, secret=secret or os.getenv("DIV19_HMAC_SECRET"))
    except ValueError:
        return EmergencyDispatchResult(
            event_id=event.event_id,
            case_id=event.case_id,
            dispatched=False,
            signature_sha256="",
            detail="Emergency event not sent: DIV19_HMAC_SECRET is not configured",
        )

    if transport_sender is None:
        return EmergencyDispatchResult(
            event_id=event.event_id,
            case_id=event.case_id,
            dispatched=False,
            signature_sha256=signature,
            detail="Dry-run only: no Division 19 transport is configured",
        )

    try:
        transport_sender(payload, signature)
    except Exception as exc:  # noqa: BLE001 - return a retryable integration failure
        return EmergencyDispatchResult(
            event_id=event.event_id,
            case_id=event.case_id,
            dispatched=False,
            signature_sha256=signature,
            detail=f"Division 19 delivery failed: {type(exc).__name__}",
        )

    return EmergencyDispatchResult(
        event_id=event.event_id,
        case_id=event.case_id,
        dispatched=True,
        signature_sha256=signature,
        detail="Crisis event successfully dispatched to Division 19",
    )
