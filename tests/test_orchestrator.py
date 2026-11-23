from __future__ import annotations

import pytest

from orchestrator import CareOrchestrator
from src.models import (
    ApplicationStatus,
    BenefitApplicationRecord,
    CareIntakePayload,
    CaseStatus,
    DispatchAuthorizationError,
    InvalidStateTransitionError,
)


def _complete_intake(*, urgent_crisis: bool = False) -> CareIntakePayload:
    return CareIntakePayload(
        case_id="CASE-900",
        household_id="HH-900",
        applicant_name="Jordan Taylor",
        date_of_birth="1987-09-10",
        household_size=3,
        monthly_income_usd=2400.0,
        urgency="crisis" if urgent_crisis else "moderate",
        recipient_authorized=True,
        verified_documents=["id_card", "income_paystub", "residency_lease"],
        notes="Confidential case note",
    )


def test_evaluate_case_incomplete_routes_to_needs_info_with_uncertainty() -> None:
    orch = CareOrchestrator()
    intake = CareIntakePayload(case_id="CASE-901", household_id="HH-901", applicant_name="Alex Rivera", date_of_birth=None, household_size=2, monthly_income_usd=1800.0, recipient_authorized=False, verified_documents=["id_card"])
    case = orch.evaluate_case(intake)
    assert case.status == CaseStatus.NEEDS_INFO
    assert case.leads[0].is_lead_match is False
    assert case.leads[0].screening_status == "insufficient_data"
    assert any("income" in note or "residency" in note for note in case.leads[0].uncertainty_notes)


def test_evaluate_case_crisis_escalates_from_under_review() -> None:
    case = CareOrchestrator().evaluate_case(_complete_intake(urgent_crisis=True))
    assert case.status == CaseStatus.ESCALATED
    assert case.leads[0].is_lead_match is True
    assert case.leads[0].screening_status == "lead_for_review"
    assert [entry.to_status for entry in case.audit_log] == ["under_review", "escalated"]


def test_prepare_dispatch_blocks_missing_documents_and_authorization() -> None:
    orch = CareOrchestrator()
    intake = _complete_intake()
    intake.verified_documents = ["id_card"]
    case = orch.evaluate_case(intake)
    app = BenefitApplicationRecord(application_id="APP-901", case_id=intake.case_id, program_code="CARE")
    with pytest.raises(InvalidStateTransitionError):
        orch.prepare_application_for_dispatch(case, app, reviewer_id="reviewer-1", review_reason="Reviewed")
    assert app.status == ApplicationStatus.DRAFT

    unauthorized = _complete_intake()
    unauthorized.case_id = "CASE-902"
    unauthorized.recipient_authorized = False
    unauthorized_case = orch.evaluate_case(unauthorized)
    unauthorized_app = BenefitApplicationRecord(application_id="APP-902", case_id=unauthorized.case_id, program_code="CARE")
    with pytest.raises(DispatchAuthorizationError):
        orch.prepare_application_for_dispatch(unauthorized_case, unauthorized_app, reviewer_id="reviewer-1", review_reason="Attempted without consent")
    assert unauthorized_app.status == ApplicationStatus.DRAFT


def test_dispatch_requires_gates_and_handles_retry_then_success() -> None:
    orch = CareOrchestrator()
    case = orch.evaluate_case(_complete_intake())
    app = BenefitApplicationRecord(application_id="APP-900", case_id=case.intake.case_id, program_code="CARE-STABILIZATION-LEAD")
    orch.prepare_application_for_dispatch(case, app, reviewer_id="reviewer-1", review_reason="Verified intake and authorization")
    assert app.status == ApplicationStatus.READY_FOR_DISPATCH

    def failing_send(_payload: dict[str, object]) -> None:
        raise TimeoutError("Downstream gateway timed out")

    failed = orch.dispatch_application(case, app, failing_send)
    assert failed.dispatched is False
    assert failed.retryable is True
    assert app.status == ApplicationStatus.RETRYABLE_FAILURE
    assert app.retry.attempt_count == 1
    assert "applicant_name" not in failed.safe_payload
    assert "date_of_birth" not in failed.safe_payload
    assert "notes" not in failed.safe_payload

    succeeded = orch.dispatch_application(case, app, lambda _payload: None)
    assert succeeded.dispatched is True
    assert app.status == ApplicationStatus.SUBMITTED
    assert succeeded.safe_payload["application_id"] == "APP-900"


def test_dispatch_refuses_after_retry_limit() -> None:
    orch = CareOrchestrator()
    case = orch.evaluate_case(_complete_intake())
    app = BenefitApplicationRecord(application_id="APP-903", case_id=case.intake.case_id, program_code="CARE", status=ApplicationStatus.READY_FOR_DISPATCH, human_review_approved=True)
    app.retry.max_attempts = 1
    failed = orch.dispatch_application(case, app, lambda _payload: (_ for _ in ()).throw(TimeoutError("offline")))
    assert failed.retryable is False
    with pytest.raises(InvalidStateTransitionError):
        orch.dispatch_application(case, app, lambda _payload: None)
