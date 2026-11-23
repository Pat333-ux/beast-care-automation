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
from src.db import engine, get_db_session, init_schema
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
from src.observability import get_logger
from src.persistence import (
    BenefitApplicationORM,
    CareCaseORM,
    save_application_and_audit,
    save_case_and_audit,
    save_dispatch_attempt,
)
from src.scraper import BenefitCatalogSyncEngine
from src.security import generate_hmac_signature

app = FastAPI(title="BEAST Care Automation", version="0.5.0")
care_orchestrator = CareOrchestrator()
engine_core = BeastAutonomousCaretakerEngine()
scraper = BenefitCatalogSyncEngine()
logger = get_logger("beast.api")
get_db = get_db_session


@app.on_event("startup")
def on_startup() -> None:
    init_schema(engine)


def sign_handoff_payload(payload: dict[str, object], secret: str | None = None) -> str:
    """Compatibility helper returning the legacy digest representation."""
    signature_header, _ = generate_hmac_signature(payload, secret=secret)
    return "sha256=" + signature_header.split("v1=", 1)[1]


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
    signature_header: str
    signature_timestamp: int
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
    intake._date_of_birth_on_file = bool(row.date_of_birth_on_file)
    return CareCaseRecord(intake=intake, status=CaseStatus(row.status), updated_at=row.updated_at)


def _load_or_create_application(db: Session, *, application_id: str, case_id: str, program_code: str) -> BenefitApplicationRecord:
    row = db.get(BenefitApplicationORM, application_id)
    if row is None:
        return BenefitApplicationRecord(application_id=application_id, case_id=case_id, program_code=program_code)
    if row.case_id != case_id or row.program_code != program_code:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Application ID already belongs to a different case or program")
    record = BenefitApplicationRecord(application_id=row.application_id, case_id=row.case_id, program_code=row.program_code, status=ApplicationStatus(row.status), human_review_approved=row.human_review_approved, updated_at=row.updated_at)
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
        logger.info("care_case_evaluated", extra={"case_id": case.intake.case_id, "case_status": case.status.value, "missing_field_count": len(payload.compute_missing_fields())})
        return EvaluateIntakeResponse(case_id=case.intake.case_id, status=case.status, missing_fields=payload.compute_missing_fields(), leads=case.leads, handoff_summary=payload.to_safe_handoff_dict())
    except Exception:
        db.rollback()
        logger.exception("care_case_evaluation_failed", extra={"case_id": payload.case_id})
        raise


@app.post("/cases/{case_id}/dispatch", response_model=DispatchEndpointResponse)
def prepare_and_dispatch_case(case_id: str, req: DispatchRequest, db: Session = Depends(get_db), sender: Callable[[dict[str, object], str], None] = Depends(get_downstream_sender)) -> DispatchEndpointResponse:
    case = _load_case_record(db, case_id)
    app_record = _load_or_create_application(db, application_id=req.application_id, case_id=case_id, program_code=req.program_code)
    try:
        if app_record.status not in {ApplicationStatus.READY_FOR_DISPATCH, ApplicationStatus.RETRYABLE_FAILURE}:
            care_orchestrator.prepare_application_for_dispatch(case, app_record, reviewer_id=req.reviewer_id, review_reason=req.review_reason)
        outbound = case.intake.to_safe_handoff_dict()
        outbound.update({"application_id": app_record.application_id, "program_code": app_record.program_code})
        signature_header, signature_timestamp = generate_hmac_signature(outbound)
        digest = signature_header.split("v1=", 1)[1]
        result: DispatchResult = care_orchestrator.dispatch_application(case, app_record, lambda payload: sender(payload, signature_header))
        if result.dispatched:
            app_record.retry.attempt_count += 1
            app_record.retry.last_attempted_at = app_record.updated_at
            app_record.retry.retryable = False
        save_application_and_audit(db, app_record)
        save_dispatch_attempt(db, app_record, error=None if result.dispatched else app_record.retry.last_error)
        db.commit()
        logger.info("application_dispatch_finished", extra={"case_id": case_id, "application_id": result.application_id, "application_status": result.status.value, "dispatched": result.dispatched, "retryable": result.retryable})
        return DispatchEndpointResponse(application_id=result.application_id, status=result.status, dispatched=result.dispatched, retryable=result.retryable, detail=result.detail, signature_sha256=f"sha256={digest}", signature_header=signature_header, signature_timestamp=signature_timestamp, safe_payload=result.safe_payload)
    except (InvalidStateTransitionError, DispatchAuthorizationError) as exc:
        db.rollback()
        logger.warning("application_dispatch_blocked", extra={"case_id": case_id, "application_id": req.application_id, "reason": str(exc)})
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except Exception:
        db.rollback()
        logger.exception("application_dispatch_failed", extra={"case_id": case_id, "application_id": req.application_id})
        raise


@app.post("/recipients/evaluate")
def evaluate_recipient_benefits(profile: CareRecipientProfile) -> dict[str, Any]:
    return engine_core.process_recipient(profile)


@app.get("/catalog/sync")
def sync_benefit_catalogs() -> dict[str, Any]:
    catalog = scraper.fetch_live_catalog()
    return {"synchronized_programs": len(catalog), "catalog": catalog}
