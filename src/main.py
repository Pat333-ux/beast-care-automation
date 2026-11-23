from __future__ import annotations

from typing import Any

from fastapi import Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy.orm import Session

from orchestrator import BeastAutonomousCaretakerEngine, CareOrchestrator
from src.database import Base, engine, get_db
from src.models import BenefitLead, CareCaseRecord, CareIntakePayload, CareRecipientProfile, CaseStatus
from src.persistence import CareCaseORM, BenefitApplicationORM, TransitionAuditORM, DispatchAttemptORM, save_case_and_audit
from src.scraper import BenefitCatalogSyncEngine

# Import persistence mappings above before creating tables.
Base.metadata.create_all(bind=engine)

app = FastAPI(title="BEAST Care Automation", version="0.2.0")
engine_core = BeastAutonomousCaretakerEngine()
care_orchestrator = CareOrchestrator()
scraper = BenefitCatalogSyncEngine()


class HealthResponse(BaseModel):
    status: str


class EvaluateIntakeResponse(BaseModel):
    case_id: str
    status: CaseStatus
    missing_fields: list[str]
    leads: list[BenefitLead]
    handoff_summary: dict[str, object]


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok")


@app.post("/evaluate", response_model=EvaluateIntakeResponse)
def evaluate_intake(payload: CareIntakePayload, db: Session = Depends(get_db)) -> EvaluateIntakeResponse:
    case: CareCaseRecord = care_orchestrator.evaluate_case(payload)
    save_case_and_audit(db, case)
    db.commit()
    return EvaluateIntakeResponse(case_id=payload.case_id, status=case.status, missing_fields=payload.compute_missing_fields(), leads=case.leads, handoff_summary=payload.to_safe_handoff_dict())


@app.post("/recipients/evaluate")
def evaluate_recipient_benefits(profile: CareRecipientProfile) -> dict[str, Any]:
    """Return preliminary candidate matches; this endpoint never submits applications."""
    return engine_core.process_recipient(profile)


@app.get("/catalog/sync")
def sync_benefit_catalogs() -> dict[str, Any]:
    catalog = scraper.fetch_live_catalog()
    return {"synchronized_programs": len(catalog), "catalog": catalog}
