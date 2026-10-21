from datetime import date, datetime, timezone

from sqlalchemy import (JSON, Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text,
                        UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utcnow():
    return datetime.now(timezone.utc)


class BusinessUnit(Base):
    __tablename__ = "business_units"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class KPI(Base):
    __tablename__ = "kpis"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    unit: Mapped[str] = mapped_column(String(30), default="")            # e.g. INR, %, count
    frequency: Mapped[str] = mapped_column(String(20), default="monthly")  # monthly | quarterly
    direction: Mapped[str] = mapped_column(String(20), default="higher_better")  # or lower_better
    kpi_type: Mapped[str] = mapped_column(String(20), default="input")   # input | formula
    formula: Mapped[str | None] = mapped_column(String(500))             # e.g. "(REV - COST) / REV * 100"
    formula_approved: Mapped[bool] = mapped_column(Boolean, default=False)
    aggregation: Mapped[str] = mapped_column(String(10), default="sum")  # sum | avg (group roll-up)
    tolerance_pct: Mapped[float] = mapped_column(Float, default=5.0)     # amber band around target
    owner_name: Mapped[str | None] = mapped_column(String(120))
    owner_email: Mapped[str | None] = mapped_column(String(200))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class ReportingPeriod(Base):
    __tablename__ = "reporting_periods"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(20), unique=True, index=True)  # 2026-09 / 2026-Q3
    label: Mapped[str] = mapped_column(String(60))
    frequency: Mapped[str] = mapped_column(String(20), default="monthly")
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), default="open")   # open | closed


class KPIValue(Base):
    __tablename__ = "kpi_values"
    __table_args__ = (UniqueConstraint("kpi_id", "business_unit_id", "period_id", name="uq_kpi_bu_period"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    kpi_id: Mapped[int] = mapped_column(ForeignKey("kpis.id"), index=True)
    business_unit_id: Mapped[int] = mapped_column(ForeignKey("business_units.id"), index=True)
    period_id: Mapped[int] = mapped_column(ForeignKey("reporting_periods.id"), index=True)
    actual: Mapped[float | None] = mapped_column(Float)
    target: Mapped[float | None] = mapped_column(Float)
    commentary: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft|submitted|approved|rejected
    source: Mapped[str] = mapped_column(String(20), default="manual")  # manual | file
    source_job_id: Mapped[int | None] = mapped_column(ForeignKey("processing_jobs.id", use_alter=True, name="fk_kpi_values_source_job"))
    submitted_by: Mapped[str | None] = mapped_column(String(120))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by: Mapped[str | None] = mapped_column(String(120))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_note: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    kpi: Mapped[KPI] = relationship()
    business_unit: Mapped[BusinessUnit] = relationship()
    period: Mapped[ReportingPeriod] = relationship()


class Document(Base):
    """Any file in blob storage: KPI evidence, supporting docs, MIS submissions, generated outputs."""
    __tablename__ = "documents"
    id: Mapped[int] = mapped_column(primary_key=True)
    filename: Mapped[str] = mapped_column(String(300))
    blob_path: Mapped[str] = mapped_column(String(500), unique=True)
    content_type: Mapped[str | None] = mapped_column(String(120))
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    doc_type: Mapped[str] = mapped_column(String(20))  # evidence | reference | submission | output
    kpi_value_id: Mapped[int | None] = mapped_column(ForeignKey("kpi_values.id"))
    period_id: Mapped[int | None] = mapped_column(ForeignKey("reporting_periods.id"))
    business_unit_id: Mapped[int | None] = mapped_column(ForeignKey("business_units.id"))
    uploaded_by: Mapped[str | None] = mapped_column(String(120))
    ingested: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DocumentChunk(Base):
    __tablename__ = "document_chunks"
    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    chunk_index: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)


class ProcessingJob(Base):
    __tablename__ = "processing_jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    job_type: Mapped[str] = mapped_column(String(30))  # file_import | consolidation | ingestion | commentary
    status: Mapped[str] = mapped_column(String(20), default="queued")  # queued|running|completed|failed
    document_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id"))
    period_id: Mapped[int | None] = mapped_column(ForeignKey("reporting_periods.id"))
    rows_total: Mapped[int] = mapped_column(Integer, default=0)
    rows_accepted: Mapped[int] = mapped_column(Integer, default=0)
    rows_rejected: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    result: Mapped[dict | None] = mapped_column(JSON)
    requested_by: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProcessingLog(Base):
    __tablename__ = "processing_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("processing_jobs.id"), index=True)
    level: Mapped[str] = mapped_column(String(10), default="INFO")
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RejectedRecord(Base):
    __tablename__ = "rejected_records"
    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("processing_jobs.id"), index=True)
    row_number: Mapped[int] = mapped_column(Integer)
    raw_data: Mapped[dict] = mapped_column(JSON)
    reasons: Mapped[list] = mapped_column(JSON)


class ConsolidatedKPI(Base):
    """Standard consolidated dataset produced per consolidation run (deterministic)."""
    __tablename__ = "consolidated_kpis"
    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("processing_jobs.id"), index=True)
    period_id: Mapped[int] = mapped_column(ForeignKey("reporting_periods.id"), index=True)
    kpi_code: Mapped[str] = mapped_column(String(40))
    kpi_name: Mapped[str] = mapped_column(String(200))
    unit: Mapped[str] = mapped_column(String(30), default="")
    business_unit: Mapped[str] = mapped_column(String(30))  # BU code or "GROUP"
    actual: Mapped[float | None] = mapped_column(Float)
    target: Mapped[float | None] = mapped_column(Float)
    variance: Mapped[float | None] = mapped_column(Float)
    variance_pct: Mapped[float | None] = mapped_column(Float)
    prior_actual: Mapped[float | None] = mapped_column(Float)
    change_vs_prior_pct: Mapped[float | None] = mapped_column(Float)
    rag: Mapped[str] = mapped_column(String(10), default="NA")  # GREEN | AMBER | RED | NA
    is_calculated: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[str | None] = mapped_column(Text)


class Commentary(Base):
    __tablename__ = "commentaries"
    id: Mapped[int] = mapped_column(primary_key=True)
    period_id: Mapped[int] = mapped_column(ForeignKey("reporting_periods.id"), index=True)
    business_unit: Mapped[str] = mapped_column(String(30), default="GROUP")
    consolidation_job_id: Mapped[int | None] = mapped_column(ForeignKey("processing_jobs.id"))
    draft_text: Mapped[str | None] = mapped_column(Text)
    final_text: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="generating")
    # generating | draft | pending_review | approved | rejected | published | failed
    generated_by: Mapped[str | None] = mapped_column(String(60))   # model name or "rule-based"
    validation_warnings: Mapped[list | None] = mapped_column(JSON)
    requested_by: Mapped[str | None] = mapped_column(String(120))
    reviewer: Mapped[str | None] = mapped_column(String(120))
    review_notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Engagement(Base):
    """One row of the team's project tracker (one engagement / project)."""
    __tablename__ = "engagements"
    id: Mapped[int] = mapped_column(primary_key=True)
    import_job_id: Mapped[int | None] = mapped_column(ForeignKey("processing_jobs.id"), index=True)
    source_row: Mapped[int] = mapped_column(Integer, default=0)
    project_name: Mapped[str] = mapped_column(String(300), index=True)
    entity_name: Mapped[str | None] = mapped_column(String(300))
    project_type: Mapped[str] = mapped_column(String(20), default="Other", index=True)  # Stat | IA | Tax | Other
    project_type_raw: Mapped[str | None] = mapped_column(String(60))
    kpis_planned: Mapped[float | None] = mapped_column(Float)
    kpis_delivered: Mapped[float | None] = mapped_column(Float)
    expected_date: Mapped[date | None] = mapped_column(Date)
    actual_date: Mapped[date | None] = mapped_column(Date, index=True)   # delivery date -> delivery month
    business_area: Mapped[str | None] = mapped_column(String(200))
    engagement_period: Mapped[str | None] = mapped_column(String(60))
    erp: Mapped[str | None] = mapped_column(String(100))
    partner: Mapped[str | None] = mapped_column(String(200))
    region: Mapped[str | None] = mapped_column(String(60))
    delivery_lead: Mapped[str | None] = mapped_column(String(300))
    primary_resource: Mapped[str | None] = mapped_column(String(300))
    secondary_resource: Mapped[str | None] = mapped_column(String(300))
    file_count: Mapped[float | None] = mapped_column(Float)
    file_size_mb: Mapped[float | None] = mapped_column(Float)


class AppSetting(Base):
    __tablename__ = "app_settings"
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[dict | None] = mapped_column(JSON)
