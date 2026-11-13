import os
import time
import logging
from celery import Celery
from src.ocr_pipeline import DocumentVerificationPipeline
from src.scraper import BenefitCatalogSyncEngine
from src.orchestrator import BeastAutonomousCaretakerEngine
from src.models import CareRecipientProfile

logger = logging.getLogger("beast.worker")

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

celery_app = Celery(
    "beast_worker",
    broker=REDIS_URL,
    backend=REDIS_URL
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
)

ocr_pipeline = DocumentVerificationPipeline()
scraper_engine = BenefitCatalogSyncEngine()
core_engine = BeastAutonomousCaretakerEngine()


@celery_app.task(name="tasks.process_recipient_document")
def process_recipient_document_task(file_path: str, document_type: str, recipient_id: str):
    """Asynchronously processes and audits uploaded recipient documents via OCR."""
    logger.info(f"Starting async OCR processing for recipient {recipient_id}, doc type: {document_type}")
    
    # Execute OCR parsing
    result = ocr_pipeline.process_document(file_path, document_type)
    
    # In production: update database record with verified status and extracted values
    logger.info(f"Completed OCR processing for {recipient_id}. Status: {result['verification_status']}")
    return result


@celery_app.task(name="tasks.run_periodic_benefit_scan")
def run_periodic_benefit_scan_task(recipient_data: dict):
    """Periodically re-evaluates recipient profiles against freshly scraped catalog rules."""
    logger.info("Executing periodic background benefit synchronization and evaluation...")
    
    # Refresh live catalog
    catalog = scraper_engine.fetch_live_catalog()
    
    # Re-evaluate profile eligibility
    profile = CareRecipientProfile(**recipient_data)
    evaluation_result = core_engine.process_recipient(profile)
    
    logger.info(f"Benefit scan complete. Identified {evaluation_result['total_matches']} qualified programs.")
    return evaluation_result
