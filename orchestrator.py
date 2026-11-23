from __future__ import annotations

from typing import Any, Optional

from src.models import (
    ApplicationStatus,
    AssistanceProgram,
    BenefitMatch,
    BenefitTier,
    CareRecipientProfile,
)


class RulesEngine:
    """Conservative screening against configured program criteria, not a determination."""

    @staticmethod
    def evaluate_eligibility(profile: CareRecipientProfile, program: AssistanceProgram) -> Optional[BenefitMatch]:
        adjusted_limit = program.max_income_limit_single * (1 + (profile.household_size - 1) * 0.35)
        if profile.annual_household_income > adjusted_limit:
            return None
        if program.requires_disability and not profile.has_disability:
            return None
        if program.requires_veteran and not profile.is_veteran:
            return None
        missing = [doc for doc in program.required_documents if doc not in profile.available_documents]
        # Document names are not proof of verification or authorization; no match
        # returned here is marked dispatch-ready.
        status = ApplicationStatus.DOCUMENTS_PENDING if missing else ApplicationStatus.IDENTIFIED
        return BenefitMatch(recipient_id=profile.recipient_id, program=program, status=status, missing_documents=missing)


class BeastAutonomousCaretakerEngine:
    """Screens recipient profiles and returns candidates for human review."""

    def __init__(self) -> None:
        self.program_catalog: list[AssistanceProgram] = []
        self.load_default_catalog()

    def load_default_catalog(self) -> None:
        self.program_catalog = [
            AssistanceProgram(program_id="FED-SNAP-01", name="Supplemental Nutrition Assistance Program (SNAP)", tier=BenefitTier.FEDERAL, description="Federal food assistance support based on net income.", required_documents=["proof_of_income", "government_id", "utility_bill"], max_income_limit_single=20000.0),
            AssistanceProgram(program_id="FED-LIHEAP-02", name="Low-Income Home Energy Assistance Program (LIHEAP)", tier=BenefitTier.STATE, description="Assistance for home heating and cooling utility costs.", required_documents=["utility_bill", "proof_of_income"], max_income_limit_single=25000.0),
            AssistanceProgram(program_id="FED-VA-AID-03", name="VA Aid and Attendance Benefit", tier=BenefitTier.FEDERAL, description="Pension top-up for veterans needing daily caretaker aid.", required_documents=["dd214", "medical_evaluation", "proof_of_income"], max_income_limit_single=32000.0, requires_veteran=True, requires_disability=True),
            AssistanceProgram(program_id="FED-SSI-04", name="Supplemental Security Income (SSI)", tier=BenefitTier.FEDERAL, description="Monthly financial aid for qualifying disabled individuals.", required_documents=["proof_of_income", "medical_records", "government_id"], max_income_limit_single=18000.0, requires_disability=True),
        ]

    def process_recipient(self, profile: CareRecipientProfile) -> dict[str, Any]:
        matches = [match for program in self.program_catalog if (match := RulesEngine.evaluate_eligibility(profile, program))]
        action_plan = [
            f"HUMAN REVIEW REQUIRED: Verify eligibility, consent, and documents for {match.program.name}."
            for match in matches
        ]
        return {
            "recipient_id": profile.recipient_id,
            "total_matches": len(matches),
            "estimated_monthly_support_usd": None,
            "matches": matches,
            "autonomous_action_plan": action_plan,
            "disclaimer": "Screening candidates only; not a final eligibility decision or benefit estimate.",
        }


if __name__ == "__main__":
    import json

    engine = BeastAutonomousCaretakerEngine()
    profile = CareRecipientProfile(full_name="Sample Applicant", dob="1955-08-14", state_of_residence="IN", zip_code="47807", annual_household_income=16500, household_size=1)
    print(json.dumps(engine.process_recipient(profile), default=lambda item: item.model_dump(mode="json"), indent=2))
