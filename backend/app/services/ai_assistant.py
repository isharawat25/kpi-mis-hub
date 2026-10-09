"""Phase 3: AI MIS commentary + natural-language Q&A.

Rule: all numbers come from the deterministic consolidated dataset. The LLM only explains them,
every draft is checked for figures that are not in the validated results, and nothing is
published without reviewer approval.
"""
import json
import re
from datetime import datetime, timezone

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import KPI, BusinessUnit, Commentary, ConsolidatedKPI, KPIValue, ProcessingJob, ReportingPeriod
from .consolidation import latest_consolidation
from .ingestion import search_chunks
from .llm import get_llm, model_name

COMMENTARY_PROMPT = ChatPromptTemplate.from_messages([
    ("system",
     "You are an MIS reporting analyst writing management commentary. "
     "Use ONLY the figures in VALIDATED_RESULTS. Never calculate, estimate or invent new numbers; "
     "quote figures exactly as given. If data is missing, say so. Match the tone and structure of "
     "APPROVED_HISTORY. Write plain text with these sections: Overview, Key Movements, Exceptions, "
     "Follow-up Points. Keep it under 350 words."),
    ("human",
     "Period: {period}\nScope: {scope}\n\nKPI_DEFINITIONS:\n{definitions}\n\n"
     "VALIDATED_RESULTS (JSON):\n{results}\n\nSUBMITTER_NOTES:\n{notes}\n\n"
     "APPROVED_HISTORY:\n{history}\n\nDraft the commentary."),
])

QA_PROMPT = ChatPromptTemplate.from_messages([
    ("system",
     "You answer questions about KPI performance and supporting documents. Use only the KPI DATA and "
     "DOCUMENT EXCERPTS provided. Do not perform new calculations beyond reading the numbers given. "
     "If the answer is not in the context, say you don't have that information. Cite documents by name."),
    ("human", "Question: {question}\n\nKPI DATA (period {period}):\n{kpi_data}\n\n"
              "DOCUMENT EXCERPTS:\n{docs}"),
])


def _rows_for(db: Session, job_id: int, scope: str) -> list[ConsolidatedKPI]:
    q = select(ConsolidatedKPI).where(ConsolidatedKPI.job_id == job_id)
    if scope != "ALL":
        q = q.where(ConsolidatedKPI.business_unit == scope)
    return list(db.scalars(q.order_by(ConsolidatedKPI.business_unit, ConsolidatedKPI.kpi_code)))


def _row_dict(r: ConsolidatedKPI) -> dict:
    return {"unit": r.business_unit, "kpi": r.kpi_code, "name": r.kpi_name, "uom": r.unit,
            "actual": r.actual, "target": r.target, "variance": r.variance, "variance_pct": r.variance_pct,
            "prior_actual": r.prior_actual, "change_vs_prior_pct": r.change_vs_prior_pct, "rag": r.rag}


def _fmt(v, pct=False):
    if v is None:
        return "n/a"
    return f"{v:,.2f}%" if pct else f"{v:,.2f}"


def rule_based_commentary(period: ReportingPeriod, scope: str, rows: list[ConsolidatedKPI],
                          owners: dict[str, str]) -> str:
    if not rows:
        return f"No consolidated KPI results are available for {period.label} ({scope})."
    counts = {k: sum(r.rag == k for r in rows) for k in ("GREEN", "AMBER", "RED", "NA")}
    out = [f"Overview\n{period.label} - {scope}: {len(rows)} KPI results. "
           f"{counts['GREEN']} on target, {counts['AMBER']} within tolerance, {counts['RED']} off target"
           + (f", {counts['NA']} without a target." if counts["NA"] else ".")]
    movers = sorted([r for r in rows if r.change_vs_prior_pct is not None],
                    key=lambda r: -abs(r.change_vs_prior_pct))[:3]
    out.append("Key Movements")
    if movers:
        for r in movers:
            word = "up" if r.change_vs_prior_pct > 0 else "down"
            out.append(f"- [{r.business_unit}] {r.kpi_name} {word} {_fmt(abs(r.change_vs_prior_pct), True)} vs "
                       f"prior period ({_fmt(r.prior_actual)} -> {_fmt(r.actual)} {r.unit}).")
    else:
        out.append("- No prior-period comparison available.")
    exc = [r for r in rows if r.rag in ("RED", "AMBER")]
    out.append("Exceptions")
    if exc:
        for r in sorted(exc, key=lambda r: (r.rag != "RED", r.business_unit)):
            out.append(f"- {r.rag}: [{r.business_unit}] {r.kpi_name} actual {_fmt(r.actual)} vs target "
                       f"{_fmt(r.target)} (variance {_fmt(r.variance_pct, True)}).")
    else:
        out.append("- No exceptions; all KPIs with targets are on track.")
    out.append("Follow-up Points")
    reds = [r for r in exc if r.rag == "RED"]
    if reds:
        for r in reds:
            owner = owners.get(r.kpi_code) or "KPI owner"
            out.append(f"- {owner} to provide root cause and recovery plan for {r.kpi_name} ({r.business_unit}).")
    else:
        out.append("- Continue monitoring amber KPIs; no recovery plans required.")
    return "\n".join(out)


_NUM = re.compile(r"-?\d[\d,]*\.?\d*")


def check_figures(text: str, context: str) -> list[str]:
    """Flag numbers in AI text that do not appear in the validated context (guards against invented figures)."""
    ctx_nums = set()
    for tok in _NUM.findall(context):
        try:
            v = abs(float(tok.replace(",", "")))
        except ValueError:
            continue
        for d in range(0, 5):
            ctx_nums.add(round(v, d))
    warnings = []
    for tok in set(_NUM.findall(text)):
        clean = tok.replace(",", "").rstrip(".")
        try:
            v = abs(float(clean))
        except ValueError:
            continue
        decimals = len(clean.split(".")[1]) if "." in clean else 0
        if decimals == 0 and v < 10:  # counts / ordinals
            continue
        if round(v, decimals) not in ctx_nums and v not in ctx_nums:
            warnings.append(f"Figure '{tok}' not found in validated results - verify before approval")
    return sorted(warnings)


def generate_commentary(db: Session, commentary_id: int) -> Commentary:
    c = db.get(Commentary, commentary_id)
    period = db.get(ReportingPeriod, c.period_id)
    job = (db.get(ProcessingJob, c.consolidation_job_id) if c.consolidation_job_id
           else latest_consolidation(db, period.id))
    if job is None:
        c.status, c.draft_text = "failed", "Run consolidation for this period before generating commentary."
        db.commit()
        return c
    c.consolidation_job_id = job.id
    rows = _rows_for(db, job.id, c.business_unit)
    kpis = {k.code: k for k in db.scalars(select(KPI))}
    owners = {code: k.owner_name for code, k in kpis.items() if k.owner_name}

    llm = get_llm()
    if llm is None:
        text, warnings = rule_based_commentary(period, c.business_unit, rows, owners), []
    else:
        definitions = "\n".join(
            f"- {k.code}: {k.name} ({k.unit}); {k.direction.replace('_', ' ')}; "
            f"{('formula ' + k.formula) if k.kpi_type == 'formula' else 'submitted input'}; {k.description or ''}"
            for k in kpis.values() if k.code in {r.kpi_code for r in rows})
        notes_q = (select(KPIValue, KPI.code, BusinessUnit.code).join(KPI).join(BusinessUnit)
                   .where(KPIValue.period_id == period.id, KPIValue.status == "approved",
                          KPIValue.commentary.is_not(None)))
        notes = "\n".join(f"- [{b}] {k}: {v.commentary}" for v, k, b in db.execute(notes_q)) or "None"
        history = db.scalars(select(Commentary).where(Commentary.business_unit == c.business_unit,
                                                      Commentary.status.in_(["approved", "published"]),
                                                      Commentary.id != c.id)
                             .order_by(Commentary.id.desc()).limit(3)).all()
        hist = "\n---\n".join(h.final_text or h.draft_text or "" for h in history) or "None"
        results = json.dumps([_row_dict(r) for r in rows], indent=1)
        chain = COMMENTARY_PROMPT | llm | StrOutputParser()
        text = chain.invoke({"period": f"{period.label} ({period.code})", "scope": c.business_unit,
                             "definitions": definitions, "results": results, "notes": notes, "history": hist})
        warnings = check_figures(text, results + notes + period.code)

    c.draft_text, c.validation_warnings = text, warnings
    c.generated_by, c.status = model_name(), "draft"
    db.commit()
    return c


def answer_question(db: Session, question: str, period_id: int | None) -> dict:
    period = db.get(ReportingPeriod, period_id) if period_id else None
    job = None
    if period:
        job = latest_consolidation(db, period.id)
    else:
        for p in db.scalars(select(ReportingPeriod).order_by(ReportingPeriod.start_date.desc())):
            job = latest_consolidation(db, p.id)
            if job:
                period = p
                break
    rows = _rows_for(db, job.id, "ALL") if job else []
    ql = question.lower()
    matched = [r for r in rows if r.kpi_code.lower() in ql or r.kpi_name.lower() in ql
               or (r.business_unit != "GROUP" and r.business_unit.lower() in ql.split())]
    relevant = matched or [r for r in rows if r.business_unit == "GROUP"]
    hits = search_chunks(db, question)
    sources = [{"document": d.filename, "chunk": ch.chunk_index, "excerpt": ch.text[:300]} for ch, d, _ in hits]

    llm = get_llm()
    if llm is None:
        lines = [f"KPI results for {period.label if period else 'n/a'}:"] if relevant else \
            ["No consolidated KPI data found. Run consolidation first."]
        for r in relevant[:15]:
            lines.append(f"- [{r.business_unit}] {r.kpi_name}: actual {_fmt(r.actual)}, target {_fmt(r.target)}, "
                         f"variance {_fmt(r.variance_pct, True)}, vs prior {_fmt(r.change_vs_prior_pct, True)}, {r.rag}")
        if sources:
            lines.append("\nRelevant document excerpts:")
            lines += [f"- {s['document']}: {s['excerpt'][:200]}..." for s in sources]
        lines.append("\n(Rule-based answer - configure an LLM provider for natural-language answers.)")
        answer = "\n".join(lines)
    else:
        chain = QA_PROMPT | llm | StrOutputParser()
        answer = chain.invoke({
            "question": question, "period": period.code if period else "n/a",
            "kpi_data": json.dumps([_row_dict(r) for r in relevant[:40]], indent=1) or "None",
            "docs": "\n\n".join(f"[{d.filename}] {ch.text}" for ch, d, _ in hits) or "None"})
    return {"answer": answer, "period": period.code if period else None, "generated_by": model_name(),
            "kpi_rows_used": len(relevant), "sources": sources}


def mark_reviewed(c: Commentary, reviewer: str, approve: bool, notes: str | None, final_text: str | None):
    c.reviewer, c.review_notes, c.reviewed_at = reviewer, notes, datetime.now(timezone.utc)
    if approve:
        c.final_text = final_text or c.draft_text
        c.status = "approved"
    else:
        c.status = "rejected"
