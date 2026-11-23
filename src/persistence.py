from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, select
from sqlalchemy.orm import Mapped, Session, mapped_column, relationship

from src.database import Base
from src.models import ApplicationStatus, BenefitApplicationRecord, CareCaseRecord, CaseStatus, TransitionAuditEntry


class CareCaseORM(Base):
    __tablename__ = "care_cases"

    case_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    household_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    urgency: Mapped[str] = mapped_column(String(16), nullable=False)
    recipient_authorized: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    date_of_birth_on_file: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    verified_documents: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    missing_fields: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    safe_payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    applications: Mapped[list[BenefitApplicationORM]] = relationship(back_populates="case", cascade="all, delete-orphan")


class BenefitApplicationORM(Base):
    __tablename__ = "benefit_applications"

    application_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    case_id: Mapped[str] = mapped_column(String(64), ForeignKey("care_cases.case_id"), index=True, nullable=False)
    program_code: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    human_review_approved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    retryable: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_attempted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    case: Mapped[CareCaseORM] = relationship(back_populates="applications")


class TransitionAuditORM(Base):
    __tablename__ = "transition_audit_logs"
    __table_args__ = (UniqueConstraint("entity_type", "entity_id", "occurred_at", "from_status", "to_status", name="uq_transition_audit_event"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entity_type: Mapped[str] = mapped_column(String(24), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    from_status: Mapped[str] = mapped_column(String(32), nullable=False)
    to_status: Mapped[str] = mapped_column(String(32), nullable=False)
    actor: Mapped[str] = mapped_column(String(128), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DispatchAttemptORM(Base):
    __tablename__ = "dispatch_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    application_id: Mapped[str] = mapped_column(String(64), ForeignKey("benefit_applications.application_id"), index=True, nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    retryable: Mapped[bool] = mapped_column(Boolean, nullable=False)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


def _to_audit_orm(entry: TransitionAuditEntry) -> TransitionAuditORM:
    return TransitionAuditORM(entity_type=entry.entity_type, entity_id=entry.entity_id, from_status=entry.from_status, to_status=entry.to_status, actor=entry.actor, reason=entry.reason, occurred_at=entry.occurred_at)


def _save_audits(session: Session, entries: list[TransitionAuditEntry]) -> None:
    for entry in entries:
        statement = select(TransitionAuditORM.id).where(
            TransitionAuditORM.entity_type == entry.entity_type,
            TransitionAuditORM.entity_id == entry.entity_id,
            TransitionAuditORM.occurred_at == entry.occurred_at,
            TransitionAuditORM.from_status == entry.from_status,
            TransitionAuditORM.to_status == entry.to_status,
        )
        if session.scalar(statement) is None:
            session.add(_to_audit_orm(entry))


def save_case_and_audit(session: Session, case: CareCaseRecord) -> CareCaseORM:
    intake = case.intake
    row = session.get(CareCaseORM, intake.case_id)
    values = {
        "household_id": intake.household_id,
        "status": case.status.value,
        "urgency": intake.urgency.value,
        "recipient_authorized": intake.recipient_authorized,
        "date_of_birth_on_file": bool(intake.date_of_birth) or intake._date_of_birth_on_file,
        "verified_documents": list(intake.verified_documents),
        "missing_fields": intake.compute_missing_fields(),
        "safe_payload": intake.to_safe_handoff_dict(),
        "updated_at": case.updated_at,
    }
    if row is None:
        row = CareCaseORM(case_id=intake.case_id, **values)
        session.add(row)
    else:
        for key, value in values.items():
            setattr(row, key, value)
    _save_audits(session, case.audit_log)
    session.flush()
    return row


def save_application_and_audit(session: Session, app_record: BenefitApplicationRecord) -> BenefitApplicationORM:
    row = session.get(BenefitApplicationORM, app_record.application_id)
    values = {
        "case_id": app_record.case_id,
        "program_code": app_record.program_code,
        "status": app_record.status.value,
        "human_review_approved": app_record.human_review_approved,
        "attempt_count": app_record.retry.attempt_count,
        "max_attempts": app_record.retry.max_attempts,
        "retryable": app_record.retry.retryable,
        "last_error": app_record.retry.last_error,
        "last_attempted_at": app_record.retry.last_attempted_at,
        "updated_at": app_record.updated_at,
    }
    if row is None:
        row = BenefitApplicationORM(application_id=app_record.application_id, **values)
        session.add(row)
    else:
        for key, value in values.items():
            setattr(row, key, value)
    _save_audits(session, app_record.audit_log)
    session.flush()
    return row


def save_dispatch_attempt(session: Session, app_record: BenefitApplicationRecord, *, error: str | None, next_retry_at: datetime | None = None) -> DispatchAttemptORM:
    attempt_number = max(1, app_record.retry.attempt_count)
    attempt = DispatchAttemptORM(
        application_id=app_record.application_id,
        attempt_number=attempt_number,
        attempted_at=app_record.retry.last_attempted_at or app_record.updated_at,
        error=error,
        retryable=app_record.retry.retryable,
        next_retry_at=next_retry_at,
    )
    session.add(attempt)
    session.flush()
    return attempt


def get_case_status(session: Session, case_id: str) -> CaseStatus | None:
    row = session.get(CareCaseORM, case_id)
    return CaseStatus(row.status) if row else None


def get_application_status(session: Session, application_id: str) -> ApplicationStatus | None:
    row = session.get(BenefitApplicationORM, application_id)
    return ApplicationStatus(row.status) if row else None
