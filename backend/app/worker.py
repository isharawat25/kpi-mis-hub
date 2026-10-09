"""Celery workers: file processing, consolidation, document ingestion, commentary generation.

Run:  celery -A app.worker.celery_app worker -l info
      celery -A app.worker.celery_app beat -l info      (scheduled consolidation)
"""
from datetime import datetime, timezone

from celery import Celery
from sqlalchemy import select

from .config import get_settings
from .database import SessionLocal
from .models import Commentary, ProcessingJob, ReportingPeriod
from .services import ai_assistant, consolidation, file_processor, ingestion
from .services.logs import log

settings = get_settings()
celery_app = Celery("kpi_hub", broker=settings.redis_url)
celery_app.conf.update(
    task_always_eager=settings.celery_always_eager,
    task_eager_propagates=False,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    timezone="UTC",
    beat_schedule={
        "scheduled-consolidation": {
            "task": "app.worker.scheduled_consolidation",
            "schedule": settings.consolidation_schedule_minutes * 60.0,
        }
    },
)


@celery_app.task(name="app.worker.process_submission_file")
def process_submission_file(job_id: int):
    with SessionLocal() as db:
        try:
            file_processor.process_submission_job(db, job_id)
        except Exception as e:  # noqa: BLE001
            db.rollback()
            _fail(db, job_id, e)


@celery_app.task(name="app.worker.run_consolidation")
def run_consolidation(job_id: int):
    with SessionLocal() as db:
        consolidation.run_consolidation_job(db, job_id)


@celery_app.task(name="app.worker.ingest_document")
def ingest_document(job_id: int):
    with SessionLocal() as db:
        job = db.get(ProcessingJob, job_id)
        job.status, job.started_at = "running", datetime.now(timezone.utc)
        db.commit()
        try:
            n = ingestion.ingest_document(db, job.document_id)
            job = db.get(ProcessingJob, job_id)
            job.status, job.rows_total, job.rows_accepted = "completed", n, n
            job.finished_at = datetime.now(timezone.utc)
            log(db, job_id, f"Ingested document {job.document_id} into {n} chunks")
            db.commit()
        except Exception as e:  # noqa: BLE001
            db.rollback()
            _fail(db, job_id, e)


@celery_app.task(name="app.worker.generate_commentary")
def generate_commentary(commentary_id: int):
    with SessionLocal() as db:
        try:
            ai_assistant.generate_commentary(db, commentary_id)
        except Exception as e:  # noqa: BLE001
            db.rollback()
            c = db.get(Commentary, commentary_id)
            c.status, c.draft_text = "failed", f"Generation failed: {e}"
            db.commit()


@celery_app.task(name="app.worker.scheduled_consolidation")
def scheduled_consolidation():
    with SessionLocal() as db:
        for p in db.scalars(select(ReportingPeriod).where(ReportingPeriod.status == "open")):
            job = ProcessingJob(job_type="consolidation", period_id=p.id, requested_by="scheduler",
                                result={"statuses": ["approved"]})
            db.add(job)
            db.commit()
            consolidation.run_consolidation_job(db, job.id)


def _fail(db, job_id, err):
    job = db.get(ProcessingJob, job_id)
    job.status, job.error, job.finished_at = "failed", str(err), datetime.now(timezone.utc)
    log(db, job_id, f"Job failed: {err}", "ERROR")
    db.commit()


def dispatch(task, *args):
    """Queue on Celery, or run inline when CELERY_ALWAYS_EAGER=true."""
    if settings.celery_always_eager:
        return task(*args)
    return task.delay(*args)
