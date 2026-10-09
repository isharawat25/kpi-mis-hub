"""Phase 1: KPI data entry, evidence upload and approval workflow."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import CurrentUser, current_user, require
from ..models import KPI, BusinessUnit, Document, KPIValue, ReportingPeriod
from ..schemas import BulkReviewIn, DocumentOut, ReviewIn, ValueOut, ValuesSave
from .files import store_upload

router = APIRouter(prefix="/api/values", tags=["kpi values"])


@router.get("/grid")
def entry_grid(period_id: int, business_unit_id: int, db: Session = Depends(get_db),
               _: CurrentUser = Depends(current_user)):
    """All active input KPIs for the period's frequency, merged with any existing values."""
    period = db.get(ReportingPeriod, period_id)
    if not period or not db.get(BusinessUnit, business_unit_id):
        raise HTTPException(404, "Period or business unit not found")
    kpis = db.scalars(select(KPI).where(KPI.is_active, KPI.kpi_type == "input",
                                        KPI.frequency == period.frequency).order_by(KPI.code)).all()
    values = {v.kpi_id: v for v in db.scalars(select(KPIValue).where(
        KPIValue.period_id == period_id, KPIValue.business_unit_id == business_unit_id))}
    ev_counts = dict(db.execute(select(Document.kpi_value_id, func.count()).where(
        Document.kpi_value_id.in_([v.id for v in values.values()] or [-1])).group_by(Document.kpi_value_id)).all())
    out = []
    for k in kpis:
        v = values.get(k.id)
        out.append({"kpi_id": k.id, "kpi_code": k.code, "kpi_name": k.name, "unit": k.unit,
                    "owner": k.owner_name, "value_id": v.id if v else None,
                    "actual": v.actual if v else None, "target": v.target if v else None,
                    "commentary": v.commentary if v else None, "status": v.status if v else "not_started",
                    "review_note": v.review_note if v else None,
                    "evidence_count": ev_counts.get(v.id, 0) if v else 0})
    return {"period": period.code, "period_status": period.status, "rows": out}


@router.put("", response_model=list[ValueOut])
def save_values(body: ValuesSave, db: Session = Depends(get_db), user: CurrentUser = Depends(current_user)):
    period = db.get(ReportingPeriod, body.period_id)
    if not period or not db.get(BusinessUnit, body.business_unit_id):
        raise HTTPException(404, "Period or business unit not found")
    if period.status != "open":
        raise HTTPException(409, f"Period {period.code} is closed")
    now, saved, errors = datetime.now(timezone.utc), [], []
    for e in body.entries:
        kpi = db.get(KPI, e.kpi_id)
        if not kpi or kpi.kpi_type != "input" or kpi.frequency != period.frequency:
            errors.append(f"KPI {e.kpi_id} cannot be entered for this period")
            continue
        v = db.scalar(select(KPIValue).where(KPIValue.kpi_id == e.kpi_id, KPIValue.period_id == body.period_id,
                                             KPIValue.business_unit_id == body.business_unit_id))
        if v and v.status in ("submitted", "approved"):
            if e.actual == v.actual and e.target == v.target and (e.commentary or None) == v.commentary:
                saved.append(v)  # unchanged locked row - ignore silently
                continue
            errors.append(f"{kpi.code} is {v.status} and locked")
            continue
        if body.action == "submit" and e.actual is None:
            errors.append(f"{kpi.code}: actual is required to submit")
            continue
        if v is None:
            v = KPIValue(kpi_id=e.kpi_id, period_id=body.period_id, business_unit_id=body.business_unit_id)
            db.add(v)
        v.actual, v.target, v.commentary, v.source = e.actual, e.target, (e.commentary or None), "manual"
        if body.action == "submit":
            v.status, v.submitted_by, v.submitted_at = "submitted", user.name, now
            v.reviewed_by = v.reviewed_at = v.review_note = None
        else:
            v.status = "draft"
        saved.append(v)
    if errors:
        db.rollback()
        raise HTTPException(422, {"errors": errors})
    db.commit()
    return saved


@router.get("/pending")
def pending(period_id: int | None = None, db: Session = Depends(get_db), _=Depends(require("approver"))):
    q = (select(KPIValue, KPI, BusinessUnit, ReportingPeriod).join(KPI).join(BusinessUnit).join(ReportingPeriod)
         .where(KPIValue.status == "submitted"))
    if period_id:
        q = q.where(KPIValue.period_id == period_id)
    return [{"value_id": v.id, "period": p.code, "business_unit": b.code, "kpi_code": k.code, "kpi_name": k.name,
             "actual": v.actual, "target": v.target, "commentary": v.commentary, "source": v.source,
             "submitted_by": v.submitted_by, "submitted_at": v.submitted_at}
            for v, k, b, p in db.execute(q.order_by(ReportingPeriod.code, BusinessUnit.code, KPI.code))]


def _review(db: Session, v: KPIValue, user: CurrentUser, body: ReviewIn):
    if v.status != "submitted":
        raise HTTPException(409, f"Value {v.id} is {v.status}, not submitted")
    if v.submitted_by == user.name and user.role != "admin":
        raise HTTPException(403, "Segregation of duties: you cannot review your own submission")
    if not body.approve and not body.note:
        raise HTTPException(422, "A note is required when rejecting")
    v.status = "approved" if body.approve else "rejected"
    v.reviewed_by, v.reviewed_at, v.review_note = user.name, datetime.now(timezone.utc), body.note


@router.post("/{value_id}/review", response_model=ValueOut)
def review(value_id: int, body: ReviewIn, db: Session = Depends(get_db), user=Depends(require("approver"))):
    v = db.get(KPIValue, value_id) or _404()
    _review(db, v, user, body)
    db.commit()
    return v


@router.post("/review-bulk")
def review_bulk(body: BulkReviewIn, db: Session = Depends(get_db), user=Depends(require("approver"))):
    for vid in body.value_ids:
        _review(db, db.get(KPIValue, vid) or _404(), user, body)
    db.commit()
    return {"reviewed": len(body.value_ids)}


@router.post("/{value_id}/reopen", response_model=ValueOut)
def reopen(value_id: int, db: Session = Depends(get_db), user=Depends(require("approver"))):
    v = db.get(KPIValue, value_id) or _404()
    if v.status not in ("approved", "rejected", "submitted"):
        raise HTTPException(409, "Only submitted/approved/rejected values can be reopened")
    v.status, v.review_note = "draft", f"Reopened by {user.name}"
    db.commit()
    return v


@router.post("/{value_id}/evidence", response_model=DocumentOut, status_code=201)
async def upload_evidence(value_id: int, file: UploadFile = File(...), db: Session = Depends(get_db),
                          user: CurrentUser = Depends(current_user)):
    v = db.get(KPIValue, value_id) or _404()
    doc = await store_upload(db, file, "evidence", user, kpi_value_id=v.id, period_id=v.period_id,
                             business_unit_id=v.business_unit_id)
    db.commit()
    return doc


@router.get("/{value_id}/evidence", response_model=list[DocumentOut])
def list_evidence(value_id: int, db: Session = Depends(get_db), _: CurrentUser = Depends(current_user)):
    return db.scalars(select(Document).where(Document.kpi_value_id == value_id)).all()


def _404():
    raise HTTPException(404, "KPI value not found")
