"""Project tracker import and the monthly MIS summary dashboard."""

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import CurrentUser, current_user
from ..models import ProcessingJob
from ..services import mis_summary, tracker_import
from ..services.demo_tracker import build_sample_tracker
from .files import XLSX, store_upload

router = APIRouter(prefix="/api", tags=["tracker & MIS summary"])


def _job_view(job: ProcessingJob) -> dict:
    res = job.result or {}
    return {"id": job.id, "status": job.status, "error": job.error, "file": res.get("filename"),
            "mode": res.get("mode"), "sheet": res.get("sheet"), "header_row": res.get("header_row"),
            "engagements": res.get("engagements", 0), "delivered": res.get("delivered", 0),
            "not_delivered": res.get("not_delivered", 0), "headers": res.get("headers", []),
            "mapping": res.get("mapping", {}), "missing": res.get("missing", []),
            "warnings": res.get("warnings", {}), "fields": tracker_import.field_list()}


@router.get("/tracker/fields")
def fields(_: CurrentUser = Depends(current_user)):
    return tracker_import.field_list()


@router.get("/tracker/sample")
def sample(_: CurrentUser = Depends(current_user)):
    return Response(build_sample_tracker(), media_type=XLSX,
                    headers={"Content-Disposition": 'attachment; filename="sample_project_tracker.xlsx"'})


@router.post("/tracker/import")
async def import_tracker(file: UploadFile = File(...), mode: str = Form("replace"),
                         db: Session = Depends(get_db), user: CurrentUser = Depends(current_user)):
    if mode not in ("replace", "append"):
        raise HTTPException(422, "mode must be replace or append")
    if not (file.filename or "").lower().endswith((".xlsx", ".xlsm", ".csv")):
        raise HTTPException(422, "Upload the tracker as .xlsx or .csv")
    doc = await store_upload(db, file, "tracker", user)
    job = ProcessingJob(job_type="tracker_import", document_id=doc.id, requested_by=user.name,
                        result={"mode": mode, "filename": doc.filename})
    db.add(job)
    db.commit()
    tracker_import.run_import(db, job.id)
    return _job_view(db.get(ProcessingJob, job.id))


class RemapIn(BaseModel):
    mapping: dict[str, int | None]
    mode: str = "replace"


@router.post("/tracker/import/{job_id}/remap")
def remap(job_id: int, body: RemapIn, db: Session = Depends(get_db), user: CurrentUser = Depends(current_user)):
    """Re-run an earlier import with corrected column matches (remembered for next time)."""
    old = db.get(ProcessingJob, job_id)
    if not old or old.job_type != "tracker_import":
        raise HTTPException(404, "Import not found")
    unknown = set(body.mapping) - set(tracker_import.FIELDS)
    if unknown:
        raise HTTPException(422, f"Unknown fields: {', '.join(sorted(unknown))}")
    job = ProcessingJob(job_type="tracker_import", document_id=old.document_id, requested_by=user.name,
                        result={"mode": body.mode, "filename": (old.result or {}).get("filename")})
    db.add(job)
    db.commit()
    tracker_import.run_import(db, job.id, overrides=body.mapping)
    return _job_view(db.get(ProcessingJob, job.id))


@router.get("/tracker/import/{job_id}")
def get_import(job_id: int, db: Session = Depends(get_db), _: CurrentUser = Depends(current_user)):
    job = db.get(ProcessingJob, job_id)
    if not job or job.job_type != "tracker_import":
        raise HTTPException(404, "Import not found")
    return _job_view(job)


def _check(month: str | None, compare: str):
    if compare not in ("py", "pm"):
        raise HTTPException(422, "compare must be py (same month last year) or pm (previous month)")
    if month:
        try:
            mis_summary.parse_month(month)
        except ValueError:
            raise HTTPException(422, "month must look like 2026-10")


@router.get("/mis/summary")
def summary(month: str | None = None, compare: str = "py", db: Session = Depends(get_db),
            _: CurrentUser = Depends(current_user)):
    _check(month, compare)
    return mis_summary.build_summary(db, month, compare)


@router.get("/mis/engagements")
def engagements(month: str, project_type: str | None = None, db: Session = Depends(get_db),
                _: CurrentUser = Depends(current_user)):
    _check(month, "py")
    return mis_summary.engagement_rows(db, month, project_type)


@router.get("/mis/summary.xlsx")
def summary_xlsx(month: str | None = None, compare: str = "py", db: Session = Depends(get_db),
                 _: CurrentUser = Depends(current_user)):
    _check(month, compare)
    s = mis_summary.build_summary(db, month, compare)
    data = mis_summary.build_excel(s, mis_summary.engagement_rows(db, s["month"]))
    return Response(data, media_type=XLSX,
                    headers={"Content-Disposition": f'attachment; filename="MIS_summary_{s["month"]}.xlsx"'})
