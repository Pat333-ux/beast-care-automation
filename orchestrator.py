from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

from src.models import (
    ApplicationStatus,
    AssistanceProgram,
    BenefitApplicationRecord,
    BenefitLead,
    BenefitMatch,
    BenefitTier,
    CareCaseRecord,
    CareIntakePayload,
    CareRecipientProfile,
    CaseStatus,
    DispatchAuthorizationError,
    InvalidStateTransitionError,
)


class RulesEngine:
    """Conservative screening against configured program criteria, not a determination."""

    @staticmethod
    def evaluate_eligibility(profile: CareRecipientProfile, program: AssistanceProgram) -> Optional[BenefitMatch]:
        adjusted_limit = program.max_income_limit_single * (1 + (profile.household_size - 1) * 0.35)
        if profile.annual_household_income > adjusted_limit:
            return None
        if program.requires_disability and not profile.has_disability:
            return None
        if program.requires_veteran and not profile.is_veteran:
            return None
        missing = [doc for doc in program.required_documents if doc not in profile.available_documents]
        # Document names are not proof of verification or authorization; no match
        # returned here is marked dispatch-ready.
        application_status = ApplicationStatus.DOCUMENTS_PENDING if missing else ApplicationStatus.IDENTIFIED
        return BenefitMatch(recipient_id=profile.recipient_id, program=program, status=application_status, missing_documents=missing)


class BeastAutonomousCaretakerEngine:
    """Screens recipient profiles and returns candidates for human review."""

    def __init__(self) -> None:
        self.program_catalog: list[AssistanceProgram] = []
        self.load_default_catalog()

    def load_default_catalog(self) -> None:
        self.program_catalog = [
            AssistanceProgram(program_id="FED-SNAP-01", name="Supplemental Nutrition Assistance Program (SNAP)", tier=BenefitTier.FEDERAL, description="Federal food assistance support based on net income.", required_documents=["proof_of_income", "government_id", "utility_bill"], max_income_limit_single=20000.0),
            AssistanceProgram(program_id="FED-LIHEAP-02", name="Low-Income Home Energy Assistance Program (LIHEAP)", tier=BenefitTier.STATE, description="Assistance for home heating and cooling utility costs.", required_documents=["utility_bill", "proof_of_income"], max_income_limit_single=25000.0),
            AssistanceProgram(program_id="FED-VA-AID-03", name="VA Aid and Attendance Benefit", tier=BenefitTier.FEDERAL, description="Pension top-up for veterans needing daily caretaker aid.", required_documents=["dd214", "medical_evaluation", "proof_of_income"], max_income_limit_single=32000.0, requires_veteran=True, requires_disability=True),
            AssistanceProgram(program_id="FED-SSI-04", name="Supplemental Security Income (SSI)", tier=BenefitTier.FEDERAL, description="Monthly financial aid for qualifying disabled individuals.", required_documents=["proof_of_income", "medical_records", "government_id"], max_income_limit_single=18000.0, requires_disability=True),
        ]

    def process_recipient(self, profile: CareRecipientProfile) -> dict[str, Any]:
        matches = [match for program in self.program_catalog if (match := RulesEngine.evaluate_eligibility(profile, program))]
        action_plan = [f"HUMAN REVIEW REQUIRED: Verify eligibility, consent, and documents for {match.program.name}." for match in matches]
        return {
            "recipient_id": profile.recipient_id,
            "total_matches": len(matches),
            "estimated_monthly_support_usd": None,
            "matches": matches,
            "autonomous_action_plan": action_plan,
            "disclaimer": "Screening candidates only; not a final eligibility decision or benefit estimate.",
        }


@dataclass(frozen=True)
class DispatchResult:
    application_id: str
    status: ApplicationStatus
    dispatched: bool
    retryable: bool
    detail: str
    safe_payload: dict[str, object]


class CareOrchestrator:
    """Coordinates intake screening, reviewer gates, and downstream dispatch."""

    def __init__(self, *, per_capita_income_limit_usd: float = 2500.0, required_document_prefixes: tuple[str, ...] = ("id", "income", "residency")) -> None:
        self.per_capita_income_limit_usd = per_capita_income_limit_usd
        self.required_document_prefixes = required_document_prefixes

    def _missing_document_categories(self, intake: CareIntakePayload) -> list[str]:
        verified = {doc.strip().lower() for doc in intake.verified_documents if doc.strip()}
        return [required for required in self.required_document_prefixes if not any(required in doc for doc in verified)]

    def evaluate_case(self, intake: CareIntakePayload) -> CareCaseRecord:
        missing = intake.compute_missing_fields()
        per_capita = intake.monthly_income_usd / intake.household_size
        missing_docs = self._missing_document_categories(intake)
        uncertainty = [f"Missing intake field: {field}" for field in missing]
        uncertainty.extend(f"Unverified document category: {category}" for category in missing_docs)
        is_lead = not missing and not missing_docs and per_capita <= self.per_capita_income_limit_usd
        screening_status = "insufficient_data" if missing or missing_docs else ("lead_for_review" if is_lead else "unlikely_match")
        lead = BenefitLead(
            program_code="CARE-STABILIZATION-LEAD",
            program_name="Household Care & Stabilization Screening Lead",
            is_lead_match=is_lead,
            screening_status=screening_status,
            criteria_evaluated=["required_intake_fields_and_authorization", "required_verified_document_categories", "per_capita_monthly_income_screening_cap"],
            inputs_used={"household_size": intake.household_size, "monthly_income_usd": intake.monthly_income_usd, "per_capita_monthly_income_usd": round(per_capita, 2), "per_capita_income_limit_usd": self.per_capita_income_limit_usd, "verified_documents_count": len({d.strip().lower() for d in intake.verified_documents if d.strip()}), "urgency": intake.urgency.value},
            uncertainty_notes=uncertainty,
        )
        case = CareCaseRecord(intake=intake, leads=[lead])
        if missing or missing_docs:
            reasons = missing + [f"doc:{category}" for category in missing_docs]
            case.transition_to(CaseStatus.NEEDS_INFO, actor="care_orchestrator", reason=f"Awaiting required inputs: {', '.join(reasons)}")
        elif intake.urgency.value == "crisis":
            case.transition_to(CaseStatus.UNDER_REVIEW, actor="care_orchestrator", reason="Complete intake received with crisis urgency; queued for priority review")
            case.transition_to(CaseStatus.ESCALATED, actor="care_orchestrator", reason="Crisis urgency flag requires elevated review prior to routing")
        else:
            case.transition_to(CaseStatus.UNDER_REVIEW, actor="care_orchestrator", reason="Intake complete, authorized, and verified for review")
        return case

    def prepare_application_for_dispatch(self, case: CareCaseRecord, application: BenefitApplicationRecord, *, reviewer_id: str, review_reason: str) -> BenefitApplicationRecord:
        if application.case_id != case.intake.case_id:
            raise InvalidStateTransitionError("Application does not belong to the supplied case")
        if not case.intake.recipient_authorized:
            raise DispatchAuthorizationError("Recipient authorization is required before dispatch readiness")
        missing_fields = case.intake.compute_missing_fields()
        missing_docs = self._missing_document_categories(case.intake)
        if missing_fields or missing_docs:
            details = missing_fields + [f"document:{category}" for category in missing_docs]
            raise InvalidStateTransitionError(f"Cannot mark application ready while case has missing or unverified inputs: {', '.join(details)}")
        if case.status in {CaseStatus.NEEDS_INFO, CaseStatus.CLOSED, CaseStatus.SUBMITTED}:
            raise InvalidStateTransitionError(f"Case status '{case.status.value}' does not permit application dispatch")
        if not reviewer_id.strip() or not review_reason.strip():
            raise DispatchAuthorizationError("Reviewer identity and sign-off reason are required")
        if application.status == ApplicationStatus.DRAFT:
            application.transition_to(ApplicationStatus.PENDING_REVIEW, actor="care_orchestrator", reason="Submitted for reviewer sign-off")
        application.human_review_approved = True
        application.transition_to(ApplicationStatus.READY_FOR_DISPATCH, actor=reviewer_id, reason=review_reason)
        return application

    def dispatch_application(self, case: CareCaseRecord, application: BenefitApplicationRecord, send_fn: Callable[[dict[str, object]], None]) -> DispatchResult:
        if application.case_id != case.intake.case_id:
            raise InvalidStateTransitionError("Application does not belong to the supplied case")
        if application.status not in {ApplicationStatus.READY_FOR_DISPATCH, ApplicationStatus.RETRYABLE_FAILURE}:
            raise InvalidStateTransitionError(f"Application in status '{application.status.value}' is not eligible for dispatch")
        if not application.human_review_approved or not case.intake.recipient_authorized:
            raise DispatchAuthorizationError("Recipient authorization and human review approval are required before dispatch")
        if application.status == ApplicationStatus.RETRYABLE_FAILURE and not application.retry.retryable:
            raise InvalidStateTransitionError("Dispatch retry limit has been reached")
        if case.intake.compute_missing_fields() or self._missing_document_categories(case.intake):
            raise InvalidStateTransitionError("Cannot dispatch with incomplete intake or unverified required documents")

        safe_payload: dict[str, object] = case.intake.to_safe_handoff_dict()
        safe_payload.update({"application_id": application.application_id, "program_code": application.program_code})
        try:
            send_fn(safe_payload)
        except Exception as exc:  # noqa: BLE001 - record downstream operational failure
            application.record_retryable_failure(error=str(exc), actor="care_orchestrator")
            return DispatchResult(application_id=application.application_id, status=application.status, dispatched=False, retryable=application.retry.retryable, detail=f"Dispatch failed and recorded as retryable: {exc}", safe_payload=safe_payload)

        if application.status == ApplicationStatus.RETRYABLE_FAILURE:
            application.transition_to(ApplicationStatus.READY_FOR_DISPATCH, actor="care_orchestrator", reason="Retry succeeded; restoring ready_for_dispatch before submission")
        application.transition_to(ApplicationStatus.SUBMITTED, actor="care_orchestrator", reason="Downstream handoff succeeded after review and authorization checks")
        return DispatchResult(application_id=application.application_id, status=application.status, dispatched=True, retryable=False, detail="Application dispatched and marked submitted", safe_payload=safe_payload)


if __name__ == "__main__":
    import json

    engine = BeastAutonomousCaretakerEngine()
    profile = CareRecipientProfile(full_name="Sample Applicant", dob="1955-08-14", state_of_residence="IN", zip_code="47807", annual_household_income=16500, household_size=1)
    print(json.dumps(engine.process_recipient(profile), default=lambda item: item.model_dump(mode="json"), indent=2))
