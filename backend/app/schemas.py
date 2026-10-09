from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class BusinessUnitIn(BaseModel):
    code: str = Field(min_length=1, max_length=30)
    name: str = Field(min_length=1, max_length=200)
    is_active: bool = True

    @field_validator("code")
    @classmethod
    def up(cls, v):
        return v.strip().upper()


class BusinessUnitOut(ORM, BusinessUnitIn):
    id: int


class KPIIn(BaseModel):
    code: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z][A-Za-z0-9_]*$")
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    unit: str = ""
    frequency: str = Field("monthly", pattern="^(monthly|quarterly)$")
    direction: str = Field("higher_better", pattern="^(higher_better|lower_better)$")
    kpi_type: str = Field("input", pattern="^(input|formula)$")
    formula: str | None = None
    formula_approved: bool = False
    aggregation: str = Field("sum", pattern="^(sum|avg)$")
    tolerance_pct: float = Field(5.0, ge=0, le=100)
    owner_name: str | None = None
    owner_email: str | None = None
    is_active: bool = True

    @field_validator("code")
    @classmethod
    def up(cls, v):
        return v.strip().upper()


class KPIOut(ORM, KPIIn):
    id: int


class PeriodIn(BaseModel):
    code: str = Field(min_length=4, max_length=20)
    label: str
    frequency: str = Field("monthly", pattern="^(monthly|quarterly)$")
    start_date: date
    end_date: date
    status: str = Field("open", pattern="^(open|closed)$")

    @field_validator("code")
    @classmethod
    def up(cls, v):
        return v.strip().upper()


class PeriodOut(ORM, PeriodIn):
    id: int


class ValueEntry(BaseModel):
    kpi_id: int
    actual: float | None = None
    target: float | None = None
    commentary: str | None = None


class ValuesSave(BaseModel):
    period_id: int
    business_unit_id: int
    action: str = Field("draft", pattern="^(draft|submit)$")
    entries: list[ValueEntry]


class ReviewIn(BaseModel):
    approve: bool
    note: str | None = None


class BulkReviewIn(ReviewIn):
    value_ids: list[int]


class ValueOut(ORM):
    id: int
    kpi_id: int
    business_unit_id: int
    period_id: int
    actual: float | None
    target: float | None
    commentary: str | None
    status: str
    source: str
    submitted_by: str | None
    submitted_at: datetime | None
    reviewed_by: str | None
    review_note: str | None


class JobOut(ORM):
    id: int
    job_type: str
    status: str
    document_id: int | None
    period_id: int | None
    rows_total: int
    rows_accepted: int
    rows_rejected: int
    error: str | None
    result: dict | None
    requested_by: str | None
    created_at: datetime
    finished_at: datetime | None


class DocumentOut(ORM):
    id: int
    filename: str
    content_type: str | None
    size_bytes: int
    doc_type: str
    kpi_value_id: int | None
    period_id: int | None
    business_unit_id: int | None
    uploaded_by: str | None
    ingested: bool
    created_at: datetime


class ConsolidateIn(BaseModel):
    period_id: int
    include_submitted: bool = False


class CommentaryCreate(BaseModel):
    period_id: int
    business_unit: str = "GROUP"


class CommentaryEdit(BaseModel):
    draft_text: str


class CommentaryReview(BaseModel):
    approve: bool
    notes: str | None = None
    final_text: str | None = None


class CommentaryOut(ORM):
    id: int
    period_id: int
    business_unit: str
    consolidation_job_id: int | None
    draft_text: str | None
    final_text: str | None
    status: str
    generated_by: str | None
    validation_warnings: list | None
    requested_by: str | None
    reviewer: str | None
    review_notes: str | None
    created_at: datetime
    reviewed_at: datetime | None
    published_at: datetime | None


class AskIn(BaseModel):
    question: str = Field(min_length=3, max_length=1000)
    period_id: int | None = None
