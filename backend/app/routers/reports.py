"""Consolidation runs, dashboards, trends."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import worker
from ..database import get_db
from ..deps import CurrentUser, current_user
from ..models import KPI, BusinessUnit, ConsolidatedKPI, KPIValue, ProcessingJob, ReportingPeriod
from ..schemas import ConsolidateIn, JobOut
from ..services.consolidation import latest_consolidation

router = APIRouter(prefix="/api", tags=["reporting"])


@router.post("/consolidations", response_model=JobOut, status_code=202)
def start_consolidation(body: ConsolidateIn, db: Session = Depends(get_db), user: CurrentUser = Depends(current_user)):
    if not db.get(ReportingPeriod, body.period_id):
        raise HTTPException(404, "Period not found")
    statuses = ["approved", "submitted"] if body.include_submitted else ["approved"]
    job = ProcessingJob(job_type="consolidation", period_id=body.period_id, requested_by=user.name,
                        result={"statuses": statuses})
    db.add(job)
    db.commit()
    worker.dispatch(worker.run_consolidation, job.id)
    db.refresh(job)
    return job


@router.get("/consolidations/latest")
def latest(period_id: int, business_unit: str | None = None, db: Session = Depends(get_db),
           _: CurrentUser = Depends(current_user)):
    job = latest_consolidation(db, period_id)
    if not job:
        return {"job": None, "rows": []}
    q = select(ConsolidatedKPI).where(ConsolidatedKPI.job_id == job.id)
    if business_unit:
        q = q.where(ConsolidatedKPI.business_unit == business_unit.upper())
    rows = db.scalars(q.order_by(ConsolidatedKPI.business_unit, ConsolidatedKPI.kpi_code)).all()
    return {"job": JobOut.model_validate(job).model_dump(),
            "rows": [{c.name: getattr(r, c.name) for c in ConsolidatedKPI.__table__.columns} for r in rows]}


@router.get("/dashboard/status")
def submission_status(period_id: int, db: Session = Depends(get_db), _: CurrentUser = Depends(current_user)):
    """Completion matrix: expected (BU x input KPI) vs entered statuses."""
    period = db.get(ReportingPeriod, period_id)
    if not period:
        raise HTTPException(404, "Period not found")
    n_kpis = db.scalar(select(func.count()).select_from(KPI).where(
        KPI.is_active, KPI.kpi_type == "input", KPI.frequency == period.frequency))
    out = []
    for bu in db.scalars(select(BusinessUnit).where(BusinessUnit.is_active).order_by(BusinessUnit.code)):
        counts = dict(db.execute(select(KPIValue.status, func.count()).where(
            KPIValue.period_id == period_id, KPIValue.business_unit_id == bu.id).group_by(KPIValue.status)).all())
        out.append({"business_unit": bu.code, "expected": n_kpis, **{s: counts.get(s, 0) for s in
                    ("draft", "submitted", "approved", "rejected")},
                    "not_started": max(n_kpis - sum(counts.values()), 0)})
    return out


@router.get("/dashboard/trend")
def trend(kpi_code: str, business_unit: str = "GROUP", db: Session = Depends(get_db),
          _: CurrentUser = Depends(current_user)):
    kpi = db.scalar(select(KPI).where(KPI.code == kpi_code.upper()))
    if not kpi:
        raise HTTPException(404, "KPI not found")
    points = []
    for p in db.scalars(select(ReportingPeriod).where(ReportingPeriod.frequency == kpi.frequency)
                        .order_by(ReportingPeriod.start_date)):
        job = latest_consolidation(db, p.id)
        if not job:
            continue
        r = db.scalar(select(ConsolidatedKPI).where(ConsolidatedKPI.job_id == job.id,
                                                    ConsolidatedKPI.kpi_code == kpi.code,
                                                    ConsolidatedKPI.business_unit == business_unit.upper()))
        if r:
            points.append({"period": p.code, "actual": r.actual, "target": r.target, "rag": r.rag})
    return {"kpi": kpi.code, "name": kpi.name, "unit": kpi.unit, "business_unit": business_unit.upper(),
            "points": points}
