"""Phase 2 file intake + document repository + job monitoring."""
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import get_db
from ..deps import CurrentUser, current_user
from ..models import Document, ProcessingJob, ProcessingLog, RejectedRecord
from ..schemas import DocumentOut, JobOut
from ..services import file_processor
from ..services.storage import get_storage, make_blob_path
from .. import worker

router = APIRouter(prefix="/api", tags=["files & jobs"])
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
SUBMISSION_EXT = (".xlsx", ".xlsm", ".csv")
REFERENCE_EXT = (".pdf", ".docx", ".xlsx", ".csv", ".txt", ".md")


async def store_upload(db: Session, file: UploadFile, doc_type: str, user: CurrentUser, **links) -> Document:
    data = await file.read()
    limit = get_settings().max_upload_mb * 1024 * 1024
    if not data:
        raise HTTPException(422, "Empty file")
    if len(data) > limit:
        raise HTTPException(413, f"File exceeds {get_settings().max_upload_mb} MB")
    name = file.filename or "upload.bin"
    path = get_storage().put(make_blob_path(doc_type, name), data, file.content_type)
    doc = Document(filename=name, blob_path=path, content_type=file.content_type, size_bytes=len(data),
                   doc_type=doc_type, uploaded_by=user.name, **links)
    db.add(doc)
    db.flush()
    return doc


@router.get("/files/template")
def download_template(db: Session = Depends(get_db), _: CurrentUser = Depends(current_user)):
    return Response(file_processor.build_template(db), media_type=XLSX,
                    headers={"Content-Disposition": 'attachment; filename="kpi_submission_template.xlsx"'})


@router.post("/files/submissions", response_model=JobOut, status_code=202)
async def upload_submission(file: UploadFile = File(...), db: Session = Depends(get_db),
                            user: CurrentUser = Depends(current_user)):
    if not (file.filename or "").lower().endswith(SUBMISSION_EXT):
        raise HTTPException(422, "Upload an .xlsx or .csv file")
    doc = await store_upload(db, file, "submission", user)
    job = ProcessingJob(job_type="file_import", document_id=doc.id, requested_by=user.name)
    db.add(job)
    db.commit()
    worker.dispatch(worker.process_submission_file, job.id)
    db.refresh(job)
    return job


@router.post("/documents", response_model=DocumentOut, status_code=201)
async def upload_reference(file: UploadFile = File(...), period_id: int | None = Form(None),
                           db: Session = Depends(get_db), user: CurrentUser = Depends(current_user)):
    """Supporting/reference documents; ingested for natural-language Q&A."""
    if not (file.filename or "").lower().endswith(REFERENCE_EXT):
        raise HTTPException(422, f"Supported types: {', '.join(REFERENCE_EXT)}")
    doc = await store_upload(db, file, "reference", user, period_id=period_id)
    job = ProcessingJob(job_type="ingestion", document_id=doc.id, requested_by=user.name)
    db.add(job)
    db.commit()
    worker.dispatch(worker.ingest_document, job.id)
    db.refresh(doc)
    return doc


@router.get("/documents", response_model=list[DocumentOut])
def list_documents(doc_type: str | None = None, db: Session = Depends(get_db),
                   _: CurrentUser = Depends(current_user)):
    q = select(Document).order_by(Document.id.desc()).limit(200)
    if doc_type:
        q = q.where(Document.doc_type == doc_type)
    return db.scalars(q).all()


@router.get("/documents/{doc_id}/download")
def download_document(doc_id: int, db: Session = Depends(get_db), _: CurrentUser = Depends(current_user)):
    doc = db.get(Document, doc_id)
    if not doc:
        raise HTTPException(404, "Document not found")
    return Response(get_storage().get(doc.blob_path), media_type=doc.content_type or "application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="{doc.filename}"'})


@router.get("/jobs", response_model=list[JobOut])
def list_jobs(job_type: str | None = None, db: Session = Depends(get_db), _: CurrentUser = Depends(current_user)):
    q = select(ProcessingJob).order_by(ProcessingJob.id.desc()).limit(100)
    if job_type:
        q = q.where(ProcessingJob.job_type == job_type)
    return db.scalars(q).all()


@router.get("/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: int, db: Session = Depends(get_db), _: CurrentUser = Depends(current_user)):
    job = db.get(ProcessingJob, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job


@router.get("/jobs/{job_id}/logs")
def job_logs(job_id: int, db: Session = Depends(get_db), _: CurrentUser = Depends(current_user)):
    return [{"level": l.level, "message": l.message, "at": l.created_at}
            for l in db.scalars(select(ProcessingLog).where(ProcessingLog.job_id == job_id)
                                .order_by(ProcessingLog.id))]


@router.get("/jobs/{job_id}/rejected")
def rejected(job_id: int, db: Session = Depends(get_db), _: CurrentUser = Depends(current_user)):
    return [{"row": r.row_number, "data": r.raw_data, "reasons": r.reasons}
            for r in db.scalars(select(RejectedRecord).where(RejectedRecord.job_id == job_id)
                                .order_by(RejectedRecord.row_number))]


@router.get("/jobs/{job_id}/rejected.xlsx")
def rejected_xlsx(job_id: int, db: Session = Depends(get_db), _: CurrentUser = Depends(current_user)):
    return Response(file_processor.rejected_report(db, job_id), media_type=XLSX,
                    headers={"Content-Disposition": f'attachment; filename="rejected_job{job_id}.xlsx"'})
