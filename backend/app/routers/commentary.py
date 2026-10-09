"""Phase 3: AI commentary with reviewer approval, and natural-language Q&A."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import worker
from ..database import get_db
from ..deps import CurrentUser, current_user, require
from ..models import Commentary, ReportingPeriod
from ..schemas import AskIn, CommentaryCreate, CommentaryEdit, CommentaryOut, CommentaryReview
from ..services import ai_assistant
from ..services.consolidation import latest_consolidation

router = APIRouter(prefix="/api", tags=["ai commentary"])


def _get(db, cid) -> Commentary:
    c = db.get(Commentary, cid)
    if not c:
        raise HTTPException(404, "Commentary not found")
    return c


@router.post("/commentary", response_model=CommentaryOut, status_code=202)
def create(body: CommentaryCreate, db: Session = Depends(get_db), user: CurrentUser = Depends(current_user)):
    if not db.get(ReportingPeriod, body.period_id):
        raise HTTPException(404, "Period not found")
    job = latest_consolidation(db, body.period_id)
    if not job:
        raise HTTPException(409, "Run consolidation for this period first")
    c = Commentary(period_id=body.period_id, business_unit=body.business_unit.upper(),
                   consolidation_job_id=job.id, requested_by=user.name)
    db.add(c)
    db.commit()
    worker.dispatch(worker.generate_commentary, c.id)
    db.refresh(c)
    return c


@router.get("/commentary", response_model=list[CommentaryOut])
def list_all(period_id: int | None = None, status: str | None = None, db: Session = Depends(get_db),
             _: CurrentUser = Depends(current_user)):
    q = select(Commentary).order_by(Commentary.id.desc())
    if period_id:
        q = q.where(Commentary.period_id == period_id)
    if status:
        q = q.where(Commentary.status == status)
    return db.scalars(q).all()


@router.get("/commentary/{cid}", response_model=CommentaryOut)
def get_one(cid: int, db: Session = Depends(get_db), _: CurrentUser = Depends(current_user)):
    return _get(db, cid)


@router.put("/commentary/{cid}", response_model=CommentaryOut)
def edit(cid: int, body: CommentaryEdit, db: Session = Depends(get_db), user: CurrentUser = Depends(current_user)):
    c = _get(db, cid)
    if c.status not in ("draft", "rejected"):
        raise HTTPException(409, f"Cannot edit commentary in status {c.status}")
    c.draft_text, c.status = body.draft_text, "draft"
    c.validation_warnings = ai_assistant.check_figures(body.draft_text, _context_numbers(db, c))
    db.commit()
    return c


@router.post("/commentary/{cid}/submit", response_model=CommentaryOut)
def submit(cid: int, db: Session = Depends(get_db), _: CurrentUser = Depends(current_user)):
    c = _get(db, cid)
    if c.status != "draft":
        raise HTTPException(409, "Only drafts can be submitted for review")
    c.status = "pending_review"
    db.commit()
    return c


@router.post("/commentary/{cid}/review", response_model=CommentaryOut)
def review(cid: int, body: CommentaryReview, db: Session = Depends(get_db), user=Depends(require("approver"))):
    c = _get(db, cid)
    if c.status != "pending_review":
        raise HTTPException(409, "Commentary is not pending review")
    if c.requested_by == user.name and user.role != "admin":
        raise HTTPException(403, "Segregation of duties: reviewer must differ from requester")
    if not body.approve and not body.notes:
        raise HTTPException(422, "Notes are required when rejecting")
    ai_assistant.mark_reviewed(c, user.name, body.approve, body.notes, body.final_text)
    db.commit()
    return c


@router.post("/commentary/{cid}/publish", response_model=CommentaryOut)
def publish(cid: int, db: Session = Depends(get_db), _=Depends(require("approver"))):
    c = _get(db, cid)
    if c.status != "approved":
        raise HTTPException(409, "Only reviewer-approved commentary can be published")
    c.status, c.published_at = "published", datetime.now(timezone.utc)
    db.commit()
    return c


@router.post("/ask")
def ask(body: AskIn, db: Session = Depends(get_db), _: CurrentUser = Depends(current_user)):
    return ai_assistant.answer_question(db, body.question, body.period_id)


def _context_numbers(db: Session, c: Commentary) -> str:
    from ..models import ConsolidatedKPI
    rows = db.scalars(select(ConsolidatedKPI).where(ConsolidatedKPI.job_id == c.consolidation_job_id)).all()
    return " ".join(str(getattr(r, f)) for r in rows for f in
                    ("actual", "target", "variance", "variance_pct", "prior_actual", "change_vs_prior_pct"))
