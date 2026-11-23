from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from orchestrator import CareOrchestrator
from src.emergency_bridge import dispatch_to_div19
from src.main import app, get_db
from src.models import (
    ApplicationStatus,
    BenefitApplicationRecord,
    CareIntakePayload,
    CaseStatus,
)
from src.persistence import (
    Base,
    BenefitApplicationORM,
    CareCaseORM,
    TransitionAuditORM,
    save_application_and_audit,
    save_case_and_audit,
)
from src.security import verify_hmac_signature


def test_full_care_pipeline_e2e_lifecycle(monkeypatch) -> None:
    """Exercise incomplete intake, crisis escalation, persistence, review, and dispatch."""
    monkeypatch.setenv("CARE_WEBHOOK_SECRET", "test-care-webhook-secret")
    monkeypatch.setenv("DIV19_HMAC_SECRET", "test-div19-secret")

    test_engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(test_engine)
    session_local = sessionmaker(
        bind=test_engine,
        class_=Session,
        autoflush=False,
        expire_on_commit=False,
    )

    def override_get_db():
        with session_local() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)

        incomplete = client.post(
            "/evaluate",
            json={
                "case_id": "CASE-E2E-001",
                "household_id": "HH-E2E-100",
                "applicant_name": "John Doe",
                "date_of_birth": None,
                "household_size": 3,
                "monthly_income_usd": 2100.0,
                "urgency": "moderate",
                "recipient_authorized": False,
                "verified_documents": [],
            },
        )
        assert incomplete.status_code == 200
        incomplete_body = incomplete.json()
        assert incomplete_body["status"] == CaseStatus.NEEDS_INFO.value
        assert "date_of_birth" in incomplete_body["missing_fields"]
        assert "recipient_authorized" in incomplete_body["missing_fields"]
        assert "applicant_name" not in incomplete_body["handoff_summary"]

        crisis_intake = CareIntakePayload(
            case_id="CASE-E2E-002",
            household_id="HH-E2E-200",
            applicant_name="Alice Smith",
            date_of_birth="1984-11-03",
            household_size=4,
            monthly_income_usd=1200.0,
            urgency="crisis",
            recipient_authorized=True,
            verified_documents=["id_card", "income_stub", "residency_lease"],
            notes="Active utility shutoff notice",
        )

        case_record = CareOrchestrator().evaluate_case(crisis_intake)
        assert case_record.status == CaseStatus.ESCALATED
        assert case_record.leads[0].is_lead_match is True

        div19_payload: dict[str, object] = {}
        div19_signature = ""

        def mock_div19_transport(payload: dict[str, object], signature: str) -> None:
            nonlocal div19_payload, div19_signature
            div19_payload = payload
            div19_signature = signature

        div19_result = dispatch_to_div19(
            case_record,
            reason="Urgent utility crisis flag",
            transport_sender=mock_div19_transport,
            secret="test-div19-secret",
        )
        assert div19_result.dispatched is True
        assert "applicant_name" not in div19_payload["safe_payload"]
        assert verify_hmac_signature(
            div19_signature,
            div19_payload,
            secret="test-div19-secret",
        )

        with session_local() as db:
            save_case_and_audit(db, case_record)
            application = BenefitApplicationRecord(
                application_id="APP-E2E-002",
                case_id=case_record.intake.case_id,
                program_code="CARE-STABILIZATION-LEAD",
            )
            save_application_and_audit(db, application)
            db.commit()

        dispatch_response = client.post(
            "/cases/CASE-E2E-002/dispatch",
            json={
                "application_id": "APP-E2E-002",
                "program_code": "CARE-STABILIZATION-LEAD",
                "reviewer_id": "reviewer-officer-tarwater",
                "review_reason": "Verified eligibility leads and recipient authorization",
            },
        )
        assert dispatch_response.status_code == 200, dispatch_response.text
        dispatch_body = dispatch_response.json()
        assert dispatch_body["status"] == ApplicationStatus.SUBMITTED.value
        assert dispatch_body["dispatched"] is True
        assert dispatch_body["signature_sha256"].startswith("sha256=")
        assert "applicant_name" not in dispatch_body["safe_payload"]

        with session_local() as db:
            case_row = db.get(CareCaseORM, "CASE-E2E-002")
            application_row = db.get(BenefitApplicationORM, "APP-E2E-002")
            audits = db.scalars(select(TransitionAuditORM)).all()

            assert case_row is not None
            assert application_row is not None
            assert application_row.status == ApplicationStatus.SUBMITTED.value
            assert application_row.human_review_approved is True
            assert len(audits) >= 4
    finally:
        app.dependency_overrides.clear()
        test_engine.dispose()
