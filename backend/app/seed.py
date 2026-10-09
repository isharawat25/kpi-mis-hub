"""Seed demo data: 3 business units, input + formula KPIs, 3 monthly periods with approved values.

Run: python -m app.seed
"""
from datetime import date

from sqlalchemy import select

from .database import SessionLocal, init_db
from .models import KPI, BusinessUnit, KPIValue, ProcessingJob, ReportingPeriod
from .services.consolidation import run_consolidation_job

BUS = [("RETAIL", "Retail Banking"), ("CORP", "Corporate Banking"), ("OPS", "Operations")]
KPIS = [
    dict(code="REVENUE", name="Revenue", unit="INR Cr", direction="higher_better", owner_name="Finance Lead"),
    dict(code="OPEX", name="Operating Expenses", unit="INR Cr", direction="lower_better", owner_name="Cost Controller"),
    dict(code="TICKETS_CLOSED", name="Service Tickets Closed", unit="count", owner_name="Service Manager"),
    dict(code="TICKETS_OPENED", name="Service Tickets Opened", unit="count", direction="lower_better",
         owner_name="Service Manager"),
    dict(code="CSAT", name="Customer Satisfaction", unit="score", aggregation="avg", tolerance_pct=3,
         owner_name="CX Head"),
    dict(code="COST_INCOME", name="Cost-to-Income Ratio", unit="%", direction="lower_better", kpi_type="formula",
         formula="OPEX / REVENUE * 100", formula_approved=True, owner_name="CFO Office"),
    dict(code="CLOSURE_RATE", name="Ticket Closure Rate", unit="%", kpi_type="formula",
         formula="TICKETS_CLOSED / TICKETS_OPENED * 100", formula_approved=True, owner_name="Service Manager"),
]
PERIODS = [("2026-07", "July 2026", date(2026, 7, 1), date(2026, 7, 31)),
           ("2026-08", "August 2026", date(2026, 8, 1), date(2026, 8, 31)),
           ("2026-09", "September 2026", date(2026, 9, 1), date(2026, 9, 30))]
# (actual, target) per BU per KPI for each month - deterministic demo data
BASE = {"REVENUE": (120, 125), "OPEX": (70, 68), "TICKETS_CLOSED": (900, 950), "TICKETS_OPENED": (1000, 1000),
        "CSAT": (82, 85)}
BU_SCALE = {"RETAIL": 1.0, "CORP": 1.6, "OPS": 0.4}
MONTH_DRIFT = {"2026-07": 0.97, "2026-08": 1.0, "2026-09": 1.04}
# unit-specific performance vs plan so units differ on the dashboard
BU_PERF = {"RETAIL": {"REVENUE": 1.03, "OPEX": 0.97, "CSAT": 1.04},
           "CORP": {"REVENUE": 0.94, "OPEX": 1.08, "TICKETS_CLOSED": 1.06},
           "OPS": {"TICKETS_CLOSED": 1.04, "TICKETS_OPENED": 0.93, "CSAT": 0.98}}


def run():
    init_db()
    with SessionLocal() as db:
        if db.scalar(select(KPI)):
            print("Already seeded")
            return
        bus = [BusinessUnit(code=c, name=n) for c, n in BUS]
        kpis = [KPI(**k) for k in KPIS]
        periods = [ReportingPeriod(code=c, label=l, start_date=s, end_date=e,
                                   status="open" if c == "2026-09" else "closed") for c, l, s, e in PERIODS]
        db.add_all(bus + kpis + periods)
        db.flush()
        for p in periods:
            if p.code == "2026-09":
                continue  # leave current month for live data entry/upload
            for b in bus:
                for k in kpis:
                    if k.kpi_type != "input":
                        continue
                    a, t = BASE[k.code]
                    scale = 1 if k.code == "CSAT" else BU_SCALE[b.code]
                    db.add(KPIValue(kpi_id=k.id, business_unit_id=b.id, period_id=p.id,
                                    actual=round(a * scale * MONTH_DRIFT[p.code] * BU_PERF[b.code].get(k.code, 1), 2), target=round(t * scale, 2),
                                    status="approved", submitted_by="seed", reviewed_by="seed"))
        db.commit()
        # consolidate history so dashboards and trends have data immediately
        for p in periods:
            if p.code != "2026-09":
                job = ProcessingJob(job_type="consolidation", period_id=p.id, requested_by="seed",
                                    result={"statuses": ["approved"]})
                db.add(job)
                db.commit()
                run_consolidation_job(db, job.id)
        print("Seeded demo data")


if __name__ == "__main__":
    run()
