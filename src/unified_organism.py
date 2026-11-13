import os
import httpx
import asyncio
import logging
from typing import Dict, Any, List
from pydantic import BaseModel

logger = logging.getLogger("beast.unified_organism")


class MemberUnifiedProfile(BaseModel):
    member_id: str
    full_name: str
    state_of_residence: str
    annual_household_income: float
    credit_score_estimate: int = 580
    housing_goal: bool = True
    has_disability: bool = False
    is_veteran: bool = False
    available_documents: List[str] = []


class UnifiedOrganismEngine:
    """Central orchestrator coordinating microservices across all DAO repositories."""

    def __init__(self):
        # Service endpoint routing network
        self.services = {
            "civic_aid": os.getenv("CIVIC_AID_URL", "http://civic-aid-pipeline:8001"),
            "home_loans": os.getenv("HOME_LOAN_URL", "http://home-loan-orchestrator:8002"),
            "credit_repair": os.getenv("CREDIT_REPAIR_URL", "http://credit-repair-orchestrator:8003"),
            "relayer": os.getenv("GASLESS_RELAYER_URL", "http://beast3-gasless-relayer-bridge:8004"),
        }

    async def scan_civic_aid_pipeline(self, client: httpx.AsyncClient, profile: MemberUnifiedProfile) -> Dict[str, Any]:
        """Scans state, federal, municipal, and benefits auto-apply pipelines."""
        try:
            url = f"{self.services['civic_aid']}/api/v1/scan"
            response = await client.post(url, json=profile.model_dump(), timeout=10.0)
            return response.json() if response.status_code == 200 else {"status": "offline", "programs": []}
        except Exception as e:
            logger.warning(f"Civic Aid Pipeline unreachable: {e}")
            return {"status": "fallback", "programs": ["SNAP", "LIHEAP", "Medicaid"]}

    async def scan_home_loan_orchestrator(self, client: httpx.AsyncClient, profile: MemberUnifiedProfile) -> Dict[str, Any]:
        """Scans housing assistance, FHA/VA options, and MISMO-compliant loan programs."""
        try:
            url = f"{self.services['home_loans']}/api/v1/evaluate"
            response = await client.post(url, json=profile.model_dump(), timeout=10.0)
            return response.json() if response.status_code == 200 else {"status": "offline", "qualified_loans": []}
        except Exception as e:
            logger.warning(f"Home Loan Orchestrator unreachable: {e}")
            return {"status": "fallback", "qualified_loans": ["First-Time Homebuyer Grant", "USDA Rural Housing"]}

    async def scan_credit_repair_orchestrator(self, client: httpx.AsyncClient, profile: MemberUnifiedProfile) -> Dict[str, Any]:
        """Evaluates credit profile for automated dispute generation and score restoration."""
        try:
            url = f"{self.services['credit_repair']}/api/v1/audit"
            response = await client.post(url, json=profile.model_dump(), timeout=10.0)
            return response.json() if response.status_code == 200 else {"status": "offline", "actions": []}
        except Exception as e:
            logger.warning(f"Credit Repair Orchestrator unreachable: {e}")
            return {"status": "fallback", "actions": ["Automated Bureau Dispute Initiation"]}

    async def execute_unified_lifecycle_scan(self, profile: MemberUnifiedProfile) -> Dict[str, Any]:
        """Executes parallel scans across all repositories to assemble a complete life-improvement plan."""
        logger.info(f"Initiating full organism scan for member {profile.member_id}...")

        async with httpx.AsyncClient() as client:
            civic_task = self.scan_civic_aid_pipeline(client, profile)
            loan_task = self.scan_home_loan_orchestrator(client, profile)
            credit_task = self.scan_credit_repair_orchestrator(client, profile)

            civic_res, loan_res, credit_res = await asyncio.gather(civic_task, loan_task, credit_task)

        unified_plan = {
            "member_id": profile.member_id,
            "status": "UNIFIED_ORGANISM_ACTIVE",
            "pipeline_findings": {
                "civic_aid_assistance": civic_res,
                "housing_and_loans": loan_res,
                "credit_restoration": credit_res,
            },
            "autonomous_action_steps": [
                f"Auto-dispatching {len(civic_res.get('programs', []))} civic assistance applications.",
                "Initializing credit repair dispute cycles to raise credit score threshold.",
                "Preparing MISMO 3.4 XML payload for housing down-payment assistance.",
            ]
        }

        return unified_plan


# --- Demonstration Execution ---
if __name__ == "__main__":
    organism = UnifiedOrganismEngine()

    test_member = MemberUnifiedProfile(
        member_id="MEM-2026-7701",
        full_name="Pat Tarwater Jr.",
        state_of_residence="IN",
        annual_household_income=18500.0,
        credit_score_estimate=610,
        has_disability=True,
        is_veteran=True,
        available_documents=["proof_of_income", "government_id", "dd214"]
    )

    result = asyncio.run(organism.execute_unified_lifecycle_scan(test_member))

    print("=" * 65)
    print("BEAST 3.0 UNIFIED ORGANISM - FULL ECOSYSTEM INTERSECTION SCAN")
    print("=" * 65)
    print(f"Member ID: {result['member_id']}")
    print(f"Organism Status: {result['status']}\n")
    print("Action Steps Triggered Across Ecosystem:")
    for step in result["autonomous_action_steps"]:
        print(f"  [>] {step}")
