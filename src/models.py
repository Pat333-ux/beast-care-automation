import uuid
import datetime
from enum import Enum
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field


class BenefitTier(str, Enum):
    FEDERAL = "FEDERAL"
    STATE = "STATE"
    LOCAL = "LOCAL"
    PRIVATE_NONPROFIT = "PRIVATE_NONPROFIT"


class ApplicationStatus(str, Enum):
    IDENTIFIED = "IDENTIFIED"
    DOCUMENTS_PENDING = "DOCUMENTS_PENDING"
    READY_FOR_SUBMISSION = "READY_FOR_SUBMISSION"
    SUBMITTED = "SUBMITTED"
    APPROVED = "APPROVED"
    DENIED = "DENIED"
    APPEAL_IN_PROGRESS = "APPEAL_IN_PROGRESS"


class CareRecipientProfile(BaseModel):
    recipient_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    full_name: str
    dob: str
    state_of_residence: str
    zip_code: str
    annual_household_income: float
    household_size: int
    is_veteran: bool = False
    has_disability: bool = False
    medicaid_enrolled: bool = False
    medicare_enrolled: bool = False
    monthly_housing_cost: float = 0.0
    monthly_medical_expenses: float = 0.0
    available_documents: List[str] = Field(default_factory=list)


class AssistanceProgram(BaseModel):
    program_id: str
    name: str
    tier: BenefitTier
    description: str
    required_documents: List[str]
    max_income_limit_single: float
    requires_disability: bool = False
    requires_veteran: bool = False
    portal_url: Optional[str] = None


class BenefitMatch(BaseModel):
    match_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    recipient_id: str
    program: AssistanceProgram
    status: ApplicationStatus = ApplicationStatus.IDENTIFIED
    missing_documents: List[str] = Field(default_factory=list)
    estimated_monthly_value: float = 0.0
    date_identified: str = Field(default_factory=lambda: datetime.date.today().isoformat())
