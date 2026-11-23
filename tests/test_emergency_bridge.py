from __future__ import annotations

from src.emergency_bridge import build_emergency_event, dispatch_to_div19
from src.models import CareCaseRecord, CareIntakePayload, CaseStatus
from src.security import verify_hmac_signature

TEST_SECRET = "test-div19-secret"


def crisis_case() -> CareCaseRecord:
    intake = CareIntakePayload(
        case_id="CASE-CRISIS-001",
        household_id="HH-999",
        applicant_name="Emergency Subject",
        date_of_birth="1980-05-12",
        household_size=4,
        monthly_income_usd=800.0,
        urgency="crisis",
        recipient_authorized=True,
        verified_documents=["id_card", "eviction_notice"],
        notes="Sensitive case information must not be sent",
    )
    case = CareCaseRecord(intake=intake)
    case.transition_to(CaseStatus.UNDER_REVIEW, actor="system", reason="Intake ingested")
    case.transition_to(CaseStatus.ESCALATED, actor="system", reason="Crisis urgency detected")
    return case


def test_crisis_event_is_minimized_signed_and_dispatched() -> None:
    case = crisis_case()
    sent_payload: dict[str, object] = {}
    sent_signature = ""

    def sender(payload: dict[str, object], signature: str) -> None:
        nonlocal sent_payload, sent_signature
        sent_payload, sent_signature = payload, signature

    result = dispatch_to_div19(case, "Active crisis intake", sender, secret=TEST_SECRET)
    assert result.dispatched is True
    assert result.case_id == case.intake.case_id
    assert "applicant_name" not in sent_payload["safe_payload"]
    assert "monthly_income_usd" not in sent_payload["safe_payload"]
    assert "notes" not in sent_payload["safe_payload"]
    assert verify_hmac_signature(sent_signature, sent_payload, secret=TEST_SECRET)


def test_crisis_event_without_transport_is_not_reported_as_dispatched() -> None:
    result = dispatch_to_div19(crisis_case(), "Crisis detected", secret=TEST_SECRET)
    assert result.dispatched is False
    assert "Dry-run" in result.detail


def test_non_crisis_or_not_escalated_case_cannot_be_sent() -> None:
    case = crisis_case()
    case.intake.urgency = "moderate"
    try:
        build_emergency_event(case, "not a crisis")
        assert False, "Expected non-crisis case to be rejected"
    except ValueError:
        pass


def test_dispatch_failure_does_not_claim_success() -> None:
    def broken_sender(_payload: dict[str, object], _signature: str) -> None:
        raise TimeoutError("not exposed in detail")

    result = dispatch_to_div19(crisis_case(), "Crisis detected", broken_sender, secret=TEST_SECRET)
    assert result.dispatched is False
    assert "TimeoutError" in result.detail
    assert "not exposed in detail" not in result.detail
