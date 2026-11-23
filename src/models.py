from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, PrivateAttr, field_validator


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class BenefitTier(str, Enum):
    FEDERAL = "FEDERAL"
    STATE = "STATE"
    LOCAL = "LOCAL"
    PRIVATE_NONPROFIT = "PRIVATE_NONPROFIT"


class UrgencyLevel(str, Enum):
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    CRISIS = "crisis"


class CaseStatus(str, Enum):
    SUBMITTED = "submitted"
    NEEDS_INFO = "needs_info"
    UNDER_REVIEW = "under_review"
    ROUTED = "routed"
    ESCALATED = "escalated"
    CLOSED = "closed"


class ApplicationStatus(str, Enum):
    # Legacy screening values are retained for existing API/catalog callers.
    IDENTIFIED = "IDENTIFIED"
    DOCUMENTS_PENDING = "DOCUMENTS_PENDING"
    READY_FOR_SUBMISSION = "READY_FOR_SUBMISSION"
    APPEAL_IN_PROGRESS = "APPEAL_IN_PROGRESS"
    DRAFT = "draft"
    PENDING_REVIEW = "pending_review"
    READY_FOR_DISPATCH = "ready_for_dispatch"
    RETRYABLE_FAILURE = "retryable_failure"
    SUBMITTED = "submitted"
    APPROVED = "approved"
    DENIED = "denied"
    CLOSED = "closed"


CASE_ALLOWED_TRANSITIONS: dict[CaseStatus, set[CaseStatus]] = {
    CaseStatus.SUBMITTED: {CaseStatus.NEEDS_INFO, CaseStatus.UNDER_REVIEW, CaseStatus.CLOSED},
    CaseStatus.NEEDS_INFO: {CaseStatus.UNDER_REVIEW, CaseStatus.CLOSED},
    CaseStatus.UNDER_REVIEW: {CaseStatus.ROUTED, CaseStatus.NEEDS_INFO, CaseStatus.ESCALATED, CaseStatus.CLOSED},
    CaseStatus.ROUTED: {CaseStatus.ESCALATED, CaseStatus.CLOSED},
    CaseStatus.ESCALATED: {CaseStatus.UNDER_REVIEW, CaseStatus.ROUTED, CaseStatus.CLOSED},
    CaseStatus.CLOSED: set(),
}

APPLICATION_ALLOWED_TRANSITIONS: dict[ApplicationStatus, set[ApplicationStatus]] = {
    ApplicationStatus.DRAFT: {ApplicationStatus.PENDING_REVIEW, ApplicationStatus.CLOSED},
    ApplicationStatus.PENDING_REVIEW: {ApplicationStatus.READY_FOR_DISPATCH, ApplicationStatus.DRAFT, ApplicationStatus.CLOSED},
    ApplicationStatus.READY_FOR_DISPATCH: {ApplicationStatus.SUBMITTED, ApplicationStatus.RETRYABLE_FAILURE, ApplicationStatus.CLOSED},
    ApplicationStatus.RETRYABLE_FAILURE: {ApplicationStatus.READY_FOR_DISPATCH, ApplicationStatus.SUBMITTED, ApplicationStatus.CLOSED},
    ApplicationStatus.SUBMITTED: {ApplicationStatus.APPROVED, ApplicationStatus.DENIED, ApplicationStatus.CLOSED},
    ApplicationStatus.APPROVED: {ApplicationStatus.CLOSED},
    ApplicationStatus.DENIED: {ApplicationStatus.CLOSED},
    ApplicationStatus.CLOSED: set(),
}


class InvalidStateTransitionError(ValueError):
    """Raised when a requested transition is not permitted."""


class DispatchAuthorizationError(PermissionError):
    """Raised when dispatch readiness lacks required consent or reviewer approval."""


class TransitionAuditEntry(BaseModel):
    entity_type: Literal["case", "application"]
    entity_id: str
    from_status: str
    to_status: str
    actor: str
    reason: str
    occurred_at: datetime = Field(default_factory=utc_now)


class RetryMetadata(BaseModel):
    attempt_count: int = Field(default=0, ge=0)
    max_attempts: int = Field(default=5, ge=1)
    last_error: str | None = None
    last_attempted_at: datetime | None = None
    retryable: bool = True


class CareIntakePayload(BaseModel):
    case_id: str = Field(..., min_length=3, max_length=64)
    household_id: str = Field(..., min_length=3, max_length=64)
    applicant_name: str = Field(..., min_length=1, max_length=120)
    date_of_birth: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    household_size: int = Field(..., ge=1, le=25)
    monthly_income_usd: float = Field(..., ge=0, allow_inf_nan=False)
    urgency: UrgencyLevel = UrgencyLevel.MODERATE
    recipient_authorized: bool = False
    verified_documents: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    notes: str | None = Field(default=None, max_length=1000)
    _date_of_birth_on_file: bool = PrivateAttr(default=False)

    def model_post_init(self, __context: Any) -> None:
        self._date_of_birth_on_file = bool(self.date_of_birth)

    @field_validator("case_id", "household_id")
    @classmethod
    def validate_non_blank_id(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Identifier must not be blank")
        return cleaned

    @field_validator("date_of_birth")
    @classmethod
    def validate_calendar_date(cls, value: str | None) -> str | None:
        if value is not None:
            try:
                parsed = date.fromisoformat(value)
            except ValueError as exc:
                raise ValueError("date_of_birth must be a valid YYYY-MM-DD date") from exc
            if parsed > date.today():
                raise ValueError("date_of_birth cannot be in the future")
        return value

    def compute_missing_fields(self) -> list[str]:
        missing = list(dict.fromkeys(item.strip() for item in self.missing_fields if item.strip()))
        if not self.date_of_birth and not self._date_of_birth_on_file and "date_of_birth" not in missing:
            missing.append("date_of_birth")
        if not self.recipient_authorized and "recipient_authorized" not in missing:
            missing.append("recipient_authorized")
        return missing

    def to_safe_handoff_dict(self) -> dict[str, Any]:
        """Return a minimized payload without applicant name, birth date, or free-text notes."""
        return {
            "case_id": self.case_id,
            "household_id": self.household_id,
            "household_size": self.household_size,
            "monthly_income_usd": self.monthly_income_usd,
            "urgency": self.urgency.value,
            "recipient_authorized": self.recipient_authorized,
            "verified_document_count": len(self.verified_documents),
            "missing_fields": self.compute_missing_fields(),
        }


class BenefitLead(BaseModel):
    program_code: str
    program_name: str
    is_lead_match: bool
    screening_status: Literal["lead_for_review", "insufficient_data", "unlikely_match"] = "lead_for_review"
    criteria_evaluated: list[str] = Field(default_factory=list)
    inputs_used: dict[str, Any] = Field(default_factory=dict)
    uncertainty_notes: list[str] = Field(default_factory=list)
    disclaimer: str = "Preliminary screening lead only — not confirmed eligibility or a guaranteed benefit amount."


class CareCaseRecord(BaseModel):
    intake: CareIntakePayload
    status: CaseStatus = CaseStatus.SUBMITTED
    leads: list[BenefitLead] = Field(default_factory=list)
    audit_log: list[TransitionAuditEntry] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=utc_now)

    def transition_to(self, next_status: CaseStatus, *, actor: str, reason: str) -> TransitionAuditEntry | None:
        if next_status == self.status:
            return None
        if next_status not in CASE_ALLOWED_TRANSITIONS.get(self.status, set()):
            raise InvalidStateTransitionError(f"Invalid case transition from '{self.status.value}' to '{next_status.value}'")
        entry = TransitionAuditEntry(entity_type="case", entity_id=self.intake.case_id, from_status=self.status.value, to_status=next_status.value, actor=actor, reason=reason)
        self.status = next_status
        self.updated_at = entry.occurred_at
        self.audit_log.append(entry)
        return entry


class BenefitApplicationRecord(BaseModel):
    application_id: str
    case_id: str
    program_code: str
    status: ApplicationStatus = ApplicationStatus.DRAFT
    human_review_approved: bool = False
    retry: RetryMetadata = Field(default_factory=RetryMetadata)
    audit_log: list[TransitionAuditEntry] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=utc_now)

    def transition_to(self, next_status: ApplicationStatus, *, actor: str, reason: str) -> TransitionAuditEntry | None:
        if next_status == self.status:
            return None
        if next_status == ApplicationStatus.READY_FOR_DISPATCH and not self.human_review_approved:
            raise DispatchAuthorizationError("Explicit human_review_approved=True is required before dispatch readiness")
        if next_status not in APPLICATION_ALLOWED_TRANSITIONS.get(self.status, set()):
            raise InvalidStateTransitionError(f"Invalid application transition from '{self.status.value}' to '{next_status.value}'")
        entry = TransitionAuditEntry(entity_type="application", entity_id=self.application_id, from_status=self.status.value, to_status=next_status.value, actor=actor, reason=reason)
        self.status = next_status
        self.updated_at = entry.occurred_at
        self.audit_log.append(entry)
        return entry

    def record_retryable_failure(self, *, error: str, actor: str = "dispatcher") -> None:
        now = utc_now()
        self.retry.attempt_count += 1
        self.retry.last_error = error
        self.retry.last_attempted_at = now
        self.retry.retryable = self.retry.attempt_count < self.retry.max_attempts
        if self.status == ApplicationStatus.READY_FOR_DISPATCH:
            self.transition_to(ApplicationStatus.RETRYABLE_FAILURE, actor=actor, reason=f"Downstream dispatch failed (attempt {self.retry.attempt_count}): {error}")
        elif self.status == ApplicationStatus.RETRYABLE_FAILURE:
            self.updated_at = now
            self.audit_log.append(TransitionAuditEntry(entity_type="application", entity_id=self.application_id, from_status=self.status.value, to_status=self.status.value, actor=actor, reason=f"Retry attempt {self.retry.attempt_count} failed: {error}", occurred_at=now))


# Shared legacy benefit-catalog contracts, imported by scraper and evaluation routes.
class CareRecipientProfile(BaseModel):
    recipient_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    full_name: str
    dob: str
    state_of_residence: str
    zip_code: str
    annual_household_income: float = Field(ge=0, allow_inf_nan=False)
    household_size: int = Field(ge=1, le=25)
    is_veteran: bool = False
    has_disability: bool = False
    medicaid_enrolled: bool = False
    medicare_enrolled: bool = False
    monthly_housing_cost: float = Field(default=0, ge=0, allow_inf_nan=False)
    monthly_medical_expenses: float = Field(default=0, ge=0, allow_inf_nan=False)
    available_documents: list[str] = Field(default_factory=list)


class AssistanceProgram(BaseModel):
    program_id: str
    name: str
    tier: BenefitTier
    description: str
    required_documents: list[str]
    max_income_limit_single: float = Field(ge=0, allow_inf_nan=False)
    requires_disability: bool = False
    requires_veteran: bool = False
    portal_url: Optional[str] = None


class BenefitMatch(BaseModel):
    match_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    recipient_id: str
    program: AssistanceProgram
    status: ApplicationStatus = ApplicationStatus.IDENTIFIED
    missing_documents: list[str] = Field(default_factory=list)
    estimated_monthly_value: float = Field(default=0, ge=0, allow_inf_nan=False)
    date_identified: str = Field(default_factory=lambda: date.today().isoformat())
