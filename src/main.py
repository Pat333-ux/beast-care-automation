from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List

from src.database import get_db, engine, Base
from src.models import CareRecipientProfile
from src.orchestrator import BeastAutonomousCaretakerEngine
from src.scraper import BenefitCatalogSyncEngine
from src.dispatcher import ApplicationDispatcher

# Initialize database tables
Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="Beast 3.0 Autonomous Caretaker API",
    version="3.0.0",
    description="End-to-end autonomous caretaker orchestration engine for state and federal aid."
)

engine_core = BeastAutonomousCaretakerEngine()
scraper = BenefitCatalogSyncEngine()
dispatcher = ApplicationDispatcher()

@app.get("/health")
def health_check():
    return {"status": "healthy", "system": "Beast 3.0 Caretaker Engine"}

@app.post("/recipients/evaluate", response_dict=dict)
def evaluate_recipient_benefits(profile: CareRecipientProfile):
    """Scans and evaluates all available state and federal benefits for a care recipient."""
    try:
        result = engine_core.process_recipient(profile)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/catalog/sync")
def sync_benefit_catalogs():
    """Triggers live synchronization of state and federal program catalogs."""
    catalog = scraper.fetch_live_catalog()
    return {"synchronized_programs": len(catalog), "catalog": catalog}
