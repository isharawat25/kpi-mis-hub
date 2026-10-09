"""Phase 1 configuration: business units, KPIs (incl. formulas), reporting periods."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import CurrentUser, current_user, require
from ..models import KPI, BusinessUnit, ReportingPeriod
from ..schemas import BusinessUnitIn, BusinessUnitOut, KPIIn, KPIOut, PeriodIn, PeriodOut
from ..services.formulas import FormulaError, evaluation_order, referenced_codes, validate_formula

router = APIRouter(prefix="/api", tags=["setup"])


def _save(db: Session, obj):
    try:
        db.add(obj)
        db.commit()
        db.refresh(obj)
        return obj
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Code already exists")


# ---------- business units ----------
@router.get("/business-units", response_model=list[BusinessUnitOut])
def list_bus(db: Session = Depends(get_db), _: CurrentUser = Depends(current_user)):
    return db.scalars(select(BusinessUnit).order_by(BusinessUnit.code)).all()


@router.post("/business-units", response_model=BusinessUnitOut, status_code=201)
def create_bu(body: BusinessUnitIn, db: Session = Depends(get_db), _=Depends(require("admin"))):
    return _save(db, BusinessUnit(**body.model_dump()))


@router.put("/business-units/{bu_id}", response_model=BusinessUnitOut)
def update_bu(bu_id: int, body: BusinessUnitIn, db: Session = Depends(get_db), _=Depends(require("admin"))):
    bu = db.get(BusinessUnit, bu_id) or _404("Business unit")
    for k, v in body.model_dump().items():
        setattr(bu, k, v)
    return _save(db, bu)


# ---------- KPIs ----------
def _check_formula(db: Session, body: KPIIn, self_id: int | None = None):
    if body.kpi_type == "input":
        body.formula, body.formula_approved = None, False
        return
    if not body.formula:
        raise HTTPException(422, "Formula KPIs need a formula")
    others = {k.code: k for k in db.scalars(select(KPI)) if k.id != self_id}
    try:
        validate_formula(body.formula, set(others) | {body.code})
        if body.code in referenced_codes(body.formula):
            raise FormulaError("A KPI formula cannot reference itself")
        formulas = {c: k.formula for c, k in others.items() if k.kpi_type == "formula" and k.formula}
        formulas[body.code] = body.formula
        evaluation_order(formulas)
    except FormulaError as e:
        raise HTTPException(422, str(e))
    for code in referenced_codes(body.formula):
        if code in others and others[code].frequency != body.frequency:
            raise HTTPException(422, f"Formula uses {code} which has a different frequency")


@router.get("/kpis", response_model=list[KPIOut])
def list_kpis(db: Session = Depends(get_db), _: CurrentUser = Depends(current_user)):
    return db.scalars(select(KPI).order_by(KPI.code)).all()


@router.post("/kpis", response_model=KPIOut, status_code=201)
def create_kpi(body: KPIIn, db: Session = Depends(get_db), _=Depends(require("admin"))):
    _check_formula(db, body)
    body.formula_approved = False  # formulas need separate approval by an approver
    return _save(db, KPI(**body.model_dump()))


@router.put("/kpis/{kpi_id}", response_model=KPIOut)
def update_kpi(kpi_id: int, body: KPIIn, db: Session = Depends(get_db), _=Depends(require("admin"))):
    kpi = db.get(KPI, kpi_id) or _404("KPI")
    _check_formula(db, body, kpi.id)
    # approval is preserved only while the formula text is unchanged
    body.formula_approved = kpi.formula_approved and kpi.formula == body.formula and body.kpi_type == "formula"
    for k, v in body.model_dump().items():
        setattr(kpi, k, v)
    return _save(db, kpi)


@router.post("/kpis/{kpi_id}/approve-formula", response_model=KPIOut)
def approve_formula(kpi_id: int, db: Session = Depends(get_db), _=Depends(require("approver"))):
    kpi = db.get(KPI, kpi_id) or _404("KPI")
    if kpi.kpi_type != "formula":
        raise HTTPException(400, "Not a formula KPI")
    kpi.formula_approved = True
    db.commit()
    return kpi


# ---------- periods ----------
@router.get("/periods", response_model=list[PeriodOut])
def list_periods(db: Session = Depends(get_db), _: CurrentUser = Depends(current_user)):
    return db.scalars(select(ReportingPeriod).order_by(ReportingPeriod.start_date.desc())).all()


@router.post("/periods", response_model=PeriodOut, status_code=201)
def create_period(body: PeriodIn, db: Session = Depends(get_db), _=Depends(require("admin"))):
    if body.end_date < body.start_date:
        raise HTTPException(422, "end_date must be on or after start_date")
    return _save(db, ReportingPeriod(**body.model_dump()))


@router.post("/periods/{period_id}/{action}", response_model=PeriodOut)
def set_period_status(period_id: int, action: str, db: Session = Depends(get_db), _=Depends(require("admin"))):
    if action not in ("close", "reopen"):
        raise HTTPException(404, "Unknown action")
    p = db.get(ReportingPeriod, period_id) or _404("Period")
    p.status = "closed" if action == "close" else "open"
    db.commit()
    return p


def _404(what):
    raise HTTPException(404, f"{what} not found")
