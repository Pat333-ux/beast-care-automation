from __future__ import annotations

from typing import Any, Literal

from fastapi import Depends, FastAPI, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from orchestrator import BeastAutonomousCaretakerEngine
from src.database import Base, engine, get_db
from src.models import (
    BenefitLead,
    CareCaseRecord,
    CareIntakePayload,
    CareRecipientProfile,
    CaseStatus,
    DispatchAuthorizationError,
    InvalidStateTransitionError,
)
from src.persistence import save_case_and_audit
from src.scraper import BenefitCatalogSyncEngine

app = FastAPI(title="BEAST Care Automation", version="0.2.0")
engine_core = BeastAutonomousCaretakerEngine()
scraper = BenefitCatalogSyncEngine()


class HealthResponse(BaseModel):
    status: str


class EvaluateIntakeResponse(BaseModel):
    case_id: str
    status: CaseStatus
    missing_fields: list[str]
    leads: list[BenefitLead]
    handoff_summary: dict[str, object]


def screen_intake_leads(intake: CareIntakePayload) -> list[BenefitLead]:
    missing = intake.compute_missing_fields()
    uncertainty = [f"Missing or unverified field: {field}" for field in missing]
    per_person = intake.monthly_income_usd / intake.household_size
    candidate = not missing and per_person <= 2500.0
    screening_status: Literal["lead_for_review", "insufficient_data", "unlikely_match"] = (
        "insufficient_data" if missing else ("lead_for_review" if candidate else "unlikely_match")
    )
    return [BenefitLead(
        program_code="CARE-STABILIZATION-LEAD",
        program_name="Household Care & Stabilization Screening Lead",
        is_lead_match=candidate,
        screening_status=screening_status,
        criteria_evaluated=["complete_required_intake_and_authorization", "per_capita_monthly_income_threshold_check"],
        inputs_used={"household_size": intake.household_size, "monthly_income_usd": intake.monthly_income_usd, "per_capita_monthly_income_usd": round(per_person, 2), "urgency": intake.urgency.value},
        uncertainty_notes=uncertainty,
    )]


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok")


@app.post("/evaluate", response_model=EvaluateIntakeResponse)
def evaluate_intake(payload: CareIntakePayload, db: Session = Depends(get_db)) -> EvaluateIntakeResponse:
    try:
        missing = payload.compute_missing_fields()
        case = CareCaseRecord(intake=payload, leads=screen_intake_leads(payload))
        if missing:
            case.transition_to(CaseStatus.NEEDS_INFO, actor="intake_evaluator", reason=f"Incomplete intake fields: {', '.join(missing)}")
        else:
            case.transition_to(CaseStatus.UNDER_REVIEW, actor="intake_evaluator", reason="Intake complete and authorized; queued for review")
        save_case_and_audit(db, case)
        db.commit()
        return EvaluateIntakeResponse(case_id=payload.case_id, status=case.status, missing_fields=missing, leads=case.leads, handoff_summary=payload.to_safe_handoff_dict())
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
