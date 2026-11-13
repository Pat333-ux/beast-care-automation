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


class BenefitMatch(BaseModel):
    match_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    recipient_id: str
    program: AssistanceProgram
    status: ApplicationStatus = ApplicationStatus.IDENTIFIED
    missing_documents: List[str] = Field(default_factory=list)
    estimated_monthly_value: float = 0.0
    date_identified: str = Field(default_factory=lambda: datetime.date.today().isoformat())


class RulesEngine:
    """Evaluates recipient profiles against active assistance program criteria."""
    
    @staticmethod
    def evaluate_eligibility(
        profile: CareRecipientProfile, 
        program: AssistanceProgram
    ) -> Optional[BenefitMatch]:
        # 1. Income Verification (adjusted rough scaling for household size)
        adjusted_limit = program.max_income_limit_single * (1 + (profile.household_size - 1) * 0.35)
        if profile.annual_household_income > adjusted_limit:
            return None

        # 2. Status Requirements Check
        if program.requires_disability and not profile.has_disability:
            return None
        if program.requires_veteran and not profile.is_veteran:
            return None

        # 3. Missing Documents Calculation
        missing = [doc for doc in program.required_documents if doc not in profile.available_documents]
        
        status = ApplicationStatus.READY_FOR_SUBMISSION if not missing else ApplicationStatus.DOCUMENTS_PENDING

        return BenefitMatch(
            recipient_id=profile.recipient_id,
            program=program,
            status=status,
            missing_documents=missing,
            estimated_monthly_value=350.0  # Normalized baseline benefit estimate
        )


class BeastAutonomousCaretakerEngine:
    """Beast 3.0 Central Execution Engine handling state management and intake dispatch."""
    
    def __init__(self):
        self.program_catalog: List[AssistanceProgram] = []
        self.load_default_catalog()

    def load_default_catalog(self):
        self.program_catalog = [
            AssistanceProgram(
                program_id="FED-SNAP-01",
                name="Supplemental Nutrition Assistance Program (SNAP)",
                tier=BenefitTier.FEDERAL,
                description="Federal food assistance support based on net income.",
                required_documents=["proof_of_income", "government_id", "utility_bill"],
                max_income_limit_single=20000.0,
            ),
            AssistanceProgram(
                program_id="FED-LIHEAP-02",
                name="Low-Income Home Energy Assistance Program (LIHEAP)",
                tier=BenefitTier.STATE,
                description="Assistance for home heating and cooling utility costs.",
                required_documents=["utility_bill", "proof_of_income"],
                max_income_limit_single=25000.0,
            ),
            AssistanceProgram(
                program_id="FED-VA-AID-03",
                name="VA Aid and Attendance Benefit",
                tier=BenefitTier.FEDERAL,
                description="Pension top-up for veterans needing daily caretaker aid.",
                required_documents=["dd214", "medical_evaluation", "proof_of_income"],
                max_income_limit_single=32000.0,
                requires_veteran=True,
                requires_disability=True,
            ),
            AssistanceProgram(
                program_id="FED-SSI-04",
                name="Supplemental Security Income (SSI)",
                tier=BenefitTier.FEDERAL,
                description="Monthly financial aid for qualifying disabled individuals.",
                required_documents=["proof_of_income", "medical_records", "government_id"],
                max_income_limit_single=18000.0,
                requires_disability=True,
            ),
        ]

    def process_recipient(self, profile: CareRecipientProfile) -> Dict[str, Any]:
        matched_benefits: List[BenefitMatch] = []
        action_plan: List[str] = []

        for program in self.program_catalog:
            match = RulesEngine.evaluate_eligibility(profile, program)
            if match:
                matched_benefits.append(match)
                if match.status == ApplicationStatus.READY_FOR_SUBMISSION:
                    action_plan.append(f"AUTOMATED SUBMISSION: Dispatching package for {program.name}.")
                else:
                    action_plan.append(
                        f"ACTION REQUIRED: Collect {match.missing_documents} for {program.name}."
                    )

        total_value = sum(b.estimated_monthly_value for b in matched_benefits)

        return {
            "recipient_id": profile.recipient_id,
            "total_matches": len(matched_benefits),
            "estimated_monthly_support_usd": total_value,
            "matches": matched_benefits,
            "autonomous_action_plan": action_plan,
        }


# --- Execution Demonstration ---
if __name__ == "__main__":
    engine = BeastAutonomousCaretakerEngine()

    sample_recipient = CareRecipientProfile(
        full_name="John Doe",
        dob="1955-08-14",
        state_of_residence="IN",
        zip_code="47807",
        annual_household_income=16500.0,
        household_size=1,
        is_veteran=True,
        has_disability=True,
        available_documents=["proof_of_income", "government_id", "dd214"],
    )

    result = engine.process_recipient(sample_recipient)

    print("=" * 60)
    print(f"BEAST 3.0 AUTONOMOUS CARETAKER REPORT FOR: {sample_recipient.full_name}")
    print("=" * 60)
    print(f"Qualified Programs Identified: {result['total_matches']}")
    print(f"Est. Total Support Value: ${result['estimated_monthly_support_usd']:.2f}/mo\n")
    print("Action Plan:")
    for step in result["autonomous_action_plan"]:
        print(f" - {step}")
