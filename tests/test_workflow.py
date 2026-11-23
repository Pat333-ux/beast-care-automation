from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from src.database import get_db
from src.main import app
from src.models import (
    ApplicationStatus,
    BenefitApplicationRecord,
    CareCaseRecord,
    CareIntakePayload,
    CaseStatus,
    DispatchAuthorizationError,
    InvalidStateTransitionError,
)
from src.persistence import Base, TransitionAuditORM, save_application_and_audit, save_case_and_audit


def make_intake(case_id: str = "CASE-101", household_id: str = "HH-201", **kwargs) -> CareIntakePayload:
    values = dict(case_id=case_id, household_id=household_id, applicant_name="Jane Doe", date_of_birth="1988-04-12", household_size=3, monthly_income_usd=2100.0, recipient_authorized=True)
    values.update(kwargs)
    return CareIntakePayload(**values)


def test_valid_and_rejected_case_transitions() -> None:
    case = CareCaseRecord(intake=make_intake())
    case.transition_to(CaseStatus.UNDER_REVIEW, actor="triage", reason="Complete intake")
    case.transition_to(CaseStatus.ROUTED, actor="router", reason="Routed to benefit track")
    case.transition_to(CaseStatus.ESCALATED, actor="reviewer", reason="Urgent household flag")
    assert case.status == CaseStatus.ESCALATED
    assert len(case.audit_log) == 3
    try:
        case.transition_to(CaseStatus.SUBMITTED, actor="bad_actor", reason="Invalid jump")
        assert False, "Expected InvalidStateTransitionError"
    except InvalidStateTransitionError:
        assert case.status == CaseStatus.ESCALATED


def test_incomplete_intake_tracks_missing_fields_and_redacts_pii() -> None:
    intake = make_intake(case_id="CASE-102", household_id="HH-202", applicant_name="Secret Name", date_of_birth=None, household_size=2, monthly_income_usd=1500.0, recipient_authorized=False, notes="Sensitive note")
    missing = intake.compute_missing_fields()
    assert "date_of_birth" in missing and "recipient_authorized" in missing
    safe = intake.to_safe_handoff_dict()
    assert not {"applicant_name", "date_of_birth", "notes"}.intersection(safe)
    assert safe["missing_fields"] == missing


def test_application_requires_human_review_and_records_retry_without_submitting() -> None:
    app_rec = BenefitApplicationRecord(application_id="APP-501", case_id="CASE-101", program_code="FSSA-PATHWAY")
    app_rec.transition_to(ApplicationStatus.PENDING_REVIEW, actor="system", reason="Queued")
    try:
        app_rec.transition_to(ApplicationStatus.READY_FOR_DISPATCH, actor="dispatcher", reason="Attempt before review")
        assert False, "Expected DispatchAuthorizationError"
    except DispatchAuthorizationError:
        assert app_rec.status == ApplicationStatus.PENDING_REVIEW
    app_rec.human_review_approved = True
    app_rec.transition_to(ApplicationStatus.READY_FOR_DISPATCH, actor="case_worker", reason="Approved after verification")
    app_rec.record_retryable_failure(error="503 downstream timeout")
    assert app_rec.status == ApplicationStatus.RETRYABLE_FAILURE
    assert app_rec.status != ApplicationStatus.SUBMITTED
    assert app_rec.retry.attempt_count == 1 and app_rec.retry.retryable


def test_persistence_and_fastapi_evaluate_route() -> None:
    test_engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(test_engine)
    session_local = sessionmaker(bind=test_engine, class_=Session, expire_on_commit=False)

    def override_get_db():
        with session_local() as session:
            yield session

    app.dependency_overrides[app.dependency_overrides.get] = override_get_db if False else override_get_db
    # The route dependency is the alias imported into src.main; override the same callable.
    from src.main import get_db as main_get_db
    app.dependency_overrides[main_get_db] = override_get_db
    try:
        client = TestClient(app)
        resp = client.post("/evaluate", json={"case_id": "CASE-300", "household_id": "HH-300", "applicant_name": "Test Applicant", "date_of_birth": "1990-01-15", "household_size": 2, "monthly_income_usd": 1800.0, "recipient_authorized": True, "verified_documents": ["id_card", "income_stub", "residency_lease"]})
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "under_review"
        assert body["missing_fields"] == []
        assert "applicant_name" not in body["handoff_summary"]

        with session_local() as session:
            case = CareCaseRecord(intake=make_intake("CASE-301", "HH-301", applicant_name="Persisted Applicant"))
            case.transition_to(CaseStatus.UNDER_REVIEW, actor="api", reason="Initial evaluation")
            save_case_and_audit(session, case)
            app_rec = BenefitApplicationRecord(application_id="APP-301", case_id="CASE-301", program_code="LOCAL-CARE", human_review_approved=True)
            app_rec.transition_to(ApplicationStatus.PENDING_REVIEW, actor="api", reason="Created")
            app_rec.transition_to(ApplicationStatus.READY_FOR_DISPATCH, actor="reviewer", reason="Approved")
            save_application_and_audit(session, app_rec)
            session.commit()
            assert len(session.scalars(select(TransitionAuditORM)).all()) == 3
    finally:
        app.dependency_overrides.clear()
        test_engine.dispose()
