from __future__ import annotations

import hashlib
import hmac
import json
import os
from typing import Any, Callable

from fastapi import Depends, FastAPI, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from orchestrator import BeastAutonomousCaretakerEngine, CareOrchestrator, DispatchResult
from src.database import Base, engine, get_db
from src.models import (
    ApplicationStatus,
    BenefitApplicationRecord,
    BenefitLead,
    CareCaseRecord,
    CareIntakePayload,
    CareRecipientProfile,
    CaseStatus,
    DispatchAuthorizationError,
    InvalidStateTransitionError,
)
from src.persistence import (
    BenefitApplicationORM,
    CareCaseORM,
    save_application_and_audit,
    save_case_and_audit,
    save_dispatch_attempt,
)
from src.scraper import BenefitCatalogSyncEngine

app = FastAPI(title="BEAST Care Automation", version="0.3.0")
care_orchestrator = CareOrchestrator()
engine_core = BeastAutonomousCaretakerEngine()
scraper = BenefitCatalogSyncEngine()


@app.on_event("startup")
def on_startup() -> None:
    Base.metadata.create_all(bind=engine)


def sign_handoff_payload(payload: dict[str, object], secret: str | None = None) -> str:
    signing_secret = secret or os.getenv("CARE_WEBHOOK_SECRET")
    if not signing_secret or signing_secret == "dev-change-me-in-secret-manager":
        raise RuntimeError("CARE_WEBHOOK_SECRET must be configured with a non-default secret")
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    digest = hmac.new(signing_secret.encode("utf-8"), canonical, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


class HealthResponse(BaseModel):
    status: str


class EvaluateIntakeResponse(BaseModel):
    case_id: str
    status: CaseStatus
    missing_fields: list[str]
    leads: list[BenefitLead]
    handoff_summary: dict[str, object]


class DispatchRequest(BaseModel):
    application_id: str = Field(..., min_length=3, max_length=64)
    program_code: str = Field(..., min_length=2, max_length=64)
    reviewer_id: str = Field(..., min_length=1, max_length=120)
    review_reason: str = Field(..., min_length=3, max_length=500)


class DispatchEndpointResponse(BaseModel):
    application_id: str
    status: ApplicationStatus
    dispatched: bool
    retryable: bool
    detail: str
    signature_sha256: str
    safe_payload: dict[str, object]


def get_downstream_sender() -> Callable[[dict[str, object], str], None]:
    """Fail closed until a real queue or webhook transport is configured."""
    def unavailable_sender(_payload: dict[str, object], _signature: str) -> None:
        raise RuntimeError("Downstream sender is not configured")
    return unavailable_sender


def _load_case_record(db: Session, case_id: str) -> CareCaseRecord:
    row = db.get(CareCaseORM, case_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Case '{case_id}' not found")
    safe = dict(row.safe_payload or {})
    intake = CareIntakePayload(
        case_id=row.case_id,
        household_id=row.household_id,
        applicant_name="REDACTED_AT_REST",
        date_of_birth=None,
        household_size=int(safe.get("household_size", 1)),
        monthly_income_usd=float(safe.get("monthly_income_usd", 0.0)),
        urgency=safe.get("urgency", "moderate"),
        recipient_authorized=bool(row.recipient_authorized),
        verified_documents=list(row.verified_documents or []),
        missing_fields=list(row.missing_fields or []),
    )
    # Only the presence flag is retained; the actual birth date remains excluded.
    intake._date_of_birth_on_file = bool(row.date_of_birth_on_file)
    return CareCaseRecord(intake=intake, status=CaseStatus(row.status), updated_at=row.updated_at)


def _load_or_create_application(db: Session, *, application_id: str, case_id: str, program_code: str) -> BenefitApplicationRecord:
    row = db.get(BenefitApplicationORM, application_id)
    if row is None:
        return BenefitApplicationRecord(application_id=application_id, case_id=case_id, program_code=program_code)
    if row.case_id != case_id or row.program_code != program_code:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Application ID already belongs to a different case or program")
    record = BenefitApplicationRecord(
        application_id=row.application_id,
        case_id=row.case_id,
        program_code=row.program_code,
        status=ApplicationStatus(row.status),
        human_review_approved=row.human_review_approved,
        updated_at=row.updated_at,
    )
    record.retry.attempt_count = row.attempt_count
    record.retry.max_attempts = row.max_attempts
    record.retry.retryable = row.retryable
    record.retry.last_error = row.last_error
    record.retry.last_attempted_at = row.last_attempted_at
    return record


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok")


@app.post("/evaluate", response_model=EvaluateIntakeResponse, status_code=status.HTTP_200_OK)
def evaluate_intake(payload: CareIntakePayload, db: Session = Depends(get_db)) -> EvaluateIntakeResponse:
    try:
        case = care_orchestrator.evaluate_case(payload)
        save_case_and_audit(db, case)
        db.commit()
        return EvaluateIntakeResponse(case_id=case.intake.case_id, status=case.status, missing_fields=payload.compute_missing_fields(), leads=case.leads, handoff_summary=payload.to_safe_handoff_dict())
    except Exception:
        db.rollback()
        raise


@app.post("/cases/{case_id}/dispatch", response_model=DispatchEndpointResponse)
def prepare_and_dispatch_case(
    case_id: str,
    req: DispatchRequest,
    db: Session = Depends(get_db),
    sender: Callable[[dict[str, object], str], None] = Depends(get_downstream_sender),
) -> DispatchEndpointResponse:
    case = _load_case_record(db, case_id)
    app_record = _load_or_create_application(db, application_id=req.application_id, case_id=case_id, program_code=req.program_code)
    try:
        if app_record.status not in {ApplicationStatus.READY_FOR_DISPATCH, ApplicationStatus.RETRYABLE_FAILURE}:
            care_orchestrator.prepare_application_for_dispatch(case, app_record, reviewer_id=req.reviewer_id, review_reason=req.review_reason)
        # Sign the exact minimized payload sent to the downstream transport.
        outbound = case.intake.to_safe_handoff_dict()
        outbound.update({"application_id": app_record.application_id, "program_code": app_record.program_code})
        signature = sign_handoff_payload(outbound)
        result: DispatchResult = care_orchestrator.dispatch_application(case, app_record, lambda payload: sender(payload, signature))
        save_application_and_audit(db, app_record)
        attempt_number = app_record.retry.attempt_count + (1 if result.dispatched else 0)
        if result.dispatched:
            app_record.retry.attempt_count = attempt_number
            app_record.retry.last_attempted_at = app_record.updated_at
            app_record.retry.retryable = False
        save_dispatch_attempt(db, app_record, error=None if result.dispatched else app_record.retry.last_error)
        db.commit()
        return DispatchEndpointResponse(application_id=result.application_id, status=result.status, dispatched=result.dispatched, retryable=result.retryable, detail=result.detail, signature_sha256=signature, safe_payload=result.safe_payload)
    except (InvalidStateTransitionError, DispatchAuthorizationError) as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except Exception:
        db.rollback()
        raise


@app.post("/recipients/evaluate")
def evaluate_recipient_benefits(profile: CareRecipientProfile) -> dict[str, Any]:
    """Return preliminary candidate matches; this endpoint never submits applications."""
    return engine_core.process_recipient(profile)


@app.get("/catalog/sync")
def sync_benefit_catalogs() -> dict[str, Any]:
    catalog = scraper.fetch_live_catalog()
    return {"synchronized_programs": len(catalog), "catalog": catalog}
