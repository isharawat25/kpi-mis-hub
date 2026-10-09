from sqlalchemy.orm import Session

from ..models import ProcessingLog


def log(db: Session, job_id: int | None, message: str, level: str = "INFO"):
    db.add(ProcessingLog(job_id=job_id, level=level, message=message))
    db.flush()
