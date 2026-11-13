import os
import hmac
import hashlib
import logging
from fastapi import APIRouter, Header, HTTPException, Request, Depends
from pydantic import BaseModel

logger = logging.getLogger("beast.webhooks")
router = APIRouter(prefix="/api/v1/webhooks", tags=["Webhooks & Events"])

WEBHOOK_SECRET = os.getenv("BEAST_WEBHOOK_SECRET", "beast-secure-hmac-secret-key")


class AgencyStatusPayload(BaseModel):
    tracking_reference: str
    recipient_id: str
    program_name: str
    new_status: str
    message: str
    action_required: bool = False


def verify_hmac_signature(payload_body: bytes, x_hub_signature: str = Header(None)):
    """Validates incoming webhook payload using HMAC SHA-256 for zero-trust security."""
    if not x_hub_signature:
        raise HTTPException(status_code=401, detail="Missing webhook signature header.")
    
    expected_signature = hmac.new(
        WEBHOOK_SECRET.encode("utf-8"),
        payload_body,
        hashlib.sha256
    ).hexdigest()
    
    provided_signature = x_hub_signature.replace("sha256=", "")
    
    if not hmac.compare_digest(expected_signature, provided_signature):
        raise HTTPException(status_code=403, detail="Invalid webhook HMAC signature.")


@router.post("/agency-callback")
async def handle_agency_callback(request: Request, x_hub_signature: str = Header(None)):
    """Receives real-time status updates and document requests from government benefit portals."""
    body_bytes = await request.body()
    verify_hmac_signature(body_bytes, x_hub_signature)
    
    payload_data = await request.json()
    payload = AgencyStatusPayload(**payload_data)
    
    logger.info(f"Received secure agency webhook for ref {payload.tracking_reference}: Status -> {payload.new_status}")
    
    # Trigger downstream autonomous pipelines (e.g., alert member, queue missing documents)
    if payload.action_required:
        logger.warning(f"ACTION TRIGGERED: Member {payload.recipient_id} requires intervention for {payload.program_name}.")
    
    return {
        "status": "success",
        "acknowledged_reference": payload.tracking_reference,
        "processing_node": "beast-webhook-engine-v3"
    }
