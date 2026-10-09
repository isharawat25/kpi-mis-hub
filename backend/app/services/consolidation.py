"""Phase 2: deterministic consolidation - formulas, variances, group roll-up, prior-period movement."""
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..models import KPI, BusinessUnit, ConsolidatedKPI, Document, KPIValue, ProcessingJob, ReportingPeriod
from . import exports
from .formulas import FormulaError, evaluate, evaluation_order
from .logs import log
from .metrics import pct_change, rag_status, variance
from .storage import get_storage, make_blob_path

GROUP = "GROUP"


def previous_period(db: Session, period: ReportingPeriod) -> ReportingPeriod | None:
    return db.scalar(select(ReportingPeriod)
                     .where(ReportingPeriod.frequency == period.frequency,
                            ReportingPeriod.start_date < period.start_date)
                     .order_by(ReportingPeriod.start_date.desc()))


def compute_dataset(db: Session, period: ReportingPeriod, statuses: list[str],
                    job_id: int | None = None) -> dict:
    """Returns {(bu_code, kpi_code): {"actual", "target", "calculated", "note"}} incl. GROUP rows."""
    kpis = db.scalars(select(KPI).where(KPI.is_active, KPI.frequency == period.frequency)).all()
    inputs = {k.code: k for k in kpis if k.kpi_type == "input"}
    formulas = {}
    for k in kpis:
        if k.kpi_type == "formula":
            if not k.formula_approved:
                if job_id:
                    log(db, job_id, f"Skipped formula KPI {k.code}: formula not approved", "WARN")
                continue
            formulas[k.code] = k.formula
    order = evaluation_order(formulas) if formulas else []

    rows = db.execute(
        select(KPIValue, KPI.code, BusinessUnit.code)
        .join(KPI, KPIValue.kpi_id == KPI.id)
        .join(BusinessUnit, KPIValue.business_unit_id == BusinessUnit.id)
        .where(KPIValue.period_id == period.id, KPIValue.status.in_(statuses), KPI.code.in_(inputs))
    ).all()

    data: dict = {}
    for v, kcode, bcode in rows:
        note = "provisional (not yet approved)" if v.status != "approved" else None
        data[(bcode, kcode)] = {"actual": v.actual, "target": v.target, "calculated": False, "note": note}

    bu_codes = sorted({b for b, _ in data})
    # group roll-up of inputs
    for code, k in inputs.items():
        vals = [data[(b, code)] for b in bu_codes if (b, code) in data]
        if not vals:
            continue
        acts = [x["actual"] for x in vals if x["actual"] is not None]
        tgts = [x["target"] for x in vals if x["target"] is not None]
        agg = (lambda xs: sum(xs) / len(xs)) if k.aggregation == "avg" else sum
        data[(GROUP, code)] = {
            "actual": round(agg(acts), 4) if acts else None,
            "target": round(agg(tgts), 4) if tgts and len(tgts) == len(vals) else None,
            "calculated": True, "note": f"{k.aggregation} of {len(vals)} units"}

    # formulas evaluated per unit and on group totals (never averaged ratios)
    for unit in bu_codes + [GROUP]:
        for code in order:
            act_ctx = {c: data.get((unit, c), {}).get("actual") for c in list(inputs) + order}
            tgt_ctx = {c: data.get((unit, c), {}).get("target") for c in list(inputs) + order}
            a, t = evaluate(formulas[code], act_ctx), evaluate(formulas[code], tgt_ctx)
            if a is None and t is None:
                continue
            data[(unit, code)] = {"actual": None if a is None else round(a, 4),
                                  "target": None if t is None else round(t, 4),
                                  "calculated": True, "note": f"= {formulas[code]}"}
    return data


def run_consolidation_job(db: Session, job_id: int) -> ProcessingJob:
    job = db.get(ProcessingJob, job_id)
    period = db.get(ReportingPeriod, job.period_id)
    statuses = (job.result or {}).get("statuses", ["approved"])
    job.status, job.started_at = "running", datetime.now(timezone.utc)
    log(db, job.id, f"Consolidating period {period.code} using statuses {statuses}")
    db.commit()
    try:
        current = compute_dataset(db, period, statuses, job.id)
        prior_p = previous_period(db, period)
        prior = compute_dataset(db, prior_p, ["approved"]) if prior_p else {}
        kpis = {k.code: k for k in db.scalars(select(KPI))}

        db.execute(delete(ConsolidatedKPI).where(ConsolidatedKPI.job_id == job.id))
        out_rows = []
        for (unit, code), d in sorted(current.items(), key=lambda x: (x[0][0] != GROUP, x[0][0], x[0][1])):
            k = kpis[code]
            var, var_pct = variance(d["actual"], d["target"])
            prior_actual = prior.get((unit, code), {}).get("actual")
            row = ConsolidatedKPI(
                job_id=job.id, period_id=period.id, kpi_code=code, kpi_name=k.name, unit=k.unit,
                business_unit=unit, actual=d["actual"], target=d["target"], variance=var, variance_pct=var_pct,
                prior_actual=prior_actual, change_vs_prior_pct=pct_change(d["actual"], prior_actual),
                rag=rag_status(d["actual"], d["target"], k.direction, k.tolerance_pct),
                is_calculated=d["calculated"], note=d["note"])
            db.add(row)
            out_rows.append(row)
        db.flush()

        storage = get_storage()
        results = {"statuses": statuses, "rows": len(out_rows),
                   "red": sum(r.rag == "RED" for r in out_rows), "amber": sum(r.rag == "AMBER" for r in out_rows),
                   "prior_period": prior_p.code if prior_p else None}
        for kind, builder, ctype, ext in (
                ("excel", exports.build_excel, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx"),
                ("pptx", exports.build_pptx, "application/vnd.openxmlformats-officedocument.presentationml.presentation", "pptx")):
            data = builder(period, out_rows)
            fname = f"MIS_{period.code}_run{job.id}.{ext}"
            path = storage.put(make_blob_path("outputs", fname), data, ctype)
            doc = Document(filename=fname, blob_path=path, content_type=ctype, size_bytes=len(data),
                           doc_type="output", period_id=period.id, uploaded_by="system")
            db.add(doc)
            db.flush()
            results[f"{kind}_document_id"] = doc.id

        job.rows_total = job.rows_accepted = len(out_rows)
        job.result = results
        job.status, job.finished_at = "completed", datetime.now(timezone.utc)
        log(db, job.id, f"Consolidation complete: {len(out_rows)} rows, {results['red']} red, {results['amber']} amber")
        db.commit()
    except (FormulaError, Exception) as e:  # noqa: BLE001 - record any failure on the job
        db.rollback()
        job = db.get(ProcessingJob, job_id)
        job.status, job.error, job.finished_at = "failed", str(e), datetime.now(timezone.utc)
        log(db, job.id, f"Consolidation failed: {e}", "ERROR")
        db.commit()
    return job


def latest_consolidation(db: Session, period_id: int) -> ProcessingJob | None:
    return db.scalar(select(ProcessingJob)
                     .where(ProcessingJob.job_type == "consolidation", ProcessingJob.period_id == period_id,
                            ProcessingJob.status == "completed")
                     .order_by(ProcessingJob.id.desc()))
