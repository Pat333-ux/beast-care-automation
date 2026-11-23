from __future__ import annotations

import hashlib
import hmac
import json

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from src.main import app, get_db, get_downstream_sender
from src.models import ApplicationStatus, CaseStatus
from src.persistence import Base, BenefitApplicationORM, CareCaseORM, DispatchAttemptORM, TransitionAuditORM
from src.security import verify_hmac_signature


def test_evaluate_then_dispatch_persists_state_audit_and_signed_safe_payload(monkeypatch) -> None:
    monkeypatch.setenv("CARE_WEBHOOK_SECRET", "test-webhook-secret")
    test_engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(test_engine)
    SessionLocal = sessionmaker(bind=test_engine, class_=Session, autoflush=False, expire_on_commit=False)
    captured: dict[str, object] = {}

    def override_get_db():
        with SessionLocal() as db:
            yield db

    def successful_sender(payload: dict[str, object], signature: str) -> None:
        captured["payload"] = payload
        captured["signature"] = signature

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_downstream_sender] = lambda: successful_sender
    try:
        client = TestClient(app)
        evaluate = client.post("/evaluate", json={"case_id": "CASE-700", "household_id": "HH-700", "applicant_name": "Dana Morales", "date_of_birth": "1991-07-22", "household_size": 3, "monthly_income_usd": 2100.0, "urgency": "moderate", "recipient_authorized": True, "verified_documents": ["id_proof", "income_stub", "residency_utility"], "notes": "Private intake notes"})
        assert evaluate.status_code == 200, evaluate.text
        assert evaluate.json()["status"] == CaseStatus.UNDER_REVIEW.value
        assert "applicant_name" not in evaluate.json()["handoff_summary"]

        dispatch = client.post("/cases/CASE-700/dispatch", json={"application_id": "APP-700", "program_code": "CARE-STABILIZATION-LEAD", "reviewer_id": "reviewer-th-1", "review_reason": "Authorized intake and verified documents checked"})
        assert dispatch.status_code == 200, dispatch.text
        body = dispatch.json()
        assert body["status"] == ApplicationStatus.SUBMITTED.value and body["dispatched"] is True
        assert body["signature_sha256"].startswith("sha256=")
        assert body["signature_header"].startswith("t=")
        assert "applicant_name" not in body["safe_payload"]
        assert captured["payload"] == body["safe_payload"]
        assert captured["signature"] == body["signature_header"]
        assert verify_hmac_signature(body["signature_header"], body["safe_payload"], secret="test-webhook-secret")

        with SessionLocal() as db:
            case_row = db.get(CareCaseORM, "CASE-700")
            app_row = db.get(BenefitApplicationORM, "APP-700")
            audits = db.scalars(select(TransitionAuditORM)).all()
            attempts = db.scalars(select(DispatchAttemptORM)).all()
            assert case_row is not None and case_row.date_of_birth_on_file is True
            assert app_row is not None and app_row.status == ApplicationStatus.SUBMITTED.value
            assert len(audits) >= 3
            assert len(attempts) == 1 and attempts[0].error is None
    finally:
        app.dependency_overrides.clear()
        test_engine.dispose()
