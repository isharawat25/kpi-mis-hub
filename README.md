# KPI & MIS Reporting Hub

Centralised KPI submission, validation, consolidation and AI-assisted management commentary.
Stack: FastAPI · PostgreSQL · Azure Blob Storage · Celery + Redis · LangChain (OpenAI / Azure OpenAI).

## Run it

### Option A – Docker (full stack)
```bash
cp .env.example .env              # add an OpenAI/Azure key here if you want LLM commentary
docker compose up --build -d
docker compose exec api python -m app.seed     # demo data: 3 units, 7 KPIs, Jul–Sep 2026
```
Open http://localhost:8000 · API docs at http://localhost:8000/docs

### Option B – Local, no Docker (SQLite, inline tasks, local file storage)
```bash
cd backend
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
export DATABASE_URL=sqlite:///./kpi_hub.db CELERY_ALWAYS_EAGER=true   # Windows: set VAR=value
python -m app.seed
uvicorn app.main:app --reload
```

### Tests
```bash
cd backend && pytest -q        # 11 tests: formulas, validation, approvals, consolidation, exports, AI guardrails
```

## Demo walkthrough
1. **Dashboard** → pick *August 2026* → RAG matrix, trend chart, download Excel/PowerPoint pack.
2. **Enter KPIs** (user `sam`, role submitter) → *September 2026*, RETAIL → enter values → *Submit for approval*.
3. **Upload files** → *Download template* → fill rows for CORP/OPS → upload. Bad rows appear in the rejected-record report.
4. **Approvals** (switch user to `priya`, role approver) → approve. You can't approve your own submissions.
5. **Dashboard** → *Run consolidation* for September.
6. **Commentary** → *Generate draft* → edit → *Send for review* → approver approves → *Publish*.
7. **Documents** → upload a policy/definitions file → **Ask the data** → ask a question.


## Monthly MIS summary (Dashboard)

The Dashboard reads your **project tracker** (one row per engagement) and shows, for a chosen month:

- Engagements delivered and KPIs executed, split Stat / IA / Tax, against **the same month last year or the previous month**, with the movement in %
- Resources (as delivery lead, primary, secondary), project type, partners
- Engagement distribution by region (pie) and a 14-month month-by-month line
- **Download summary (Excel)**: the same tables, native charts, and the list of delivered engagements

Rules: an engagement is *delivered* in the month of its **actual delivery date**; *KPIs executed* is the sum of
**Number of KPIs delivered** on those engagements. Rows without an actual date are counted as not yet delivered.

Upload: Dashboard → **Update tracker data**. The file can be kept as it is (legend rows above the table, two-row
headers such as *Step 4 - KPI Execution / Number of KPIs Delivered*, blank cells). Columns are matched by their
header words; if a match is wrong, change it under **Columns used from your file** and re-import. The choice is
remembered. Use **Keep and add this file** to load last year's tracker for year-on-year comparison.
**Download sample tracker** shows the expected columns.

Columns used: Project Name, Project Type, Number of KPIs Delivered, Actual Date of Delivery (needed); Entity Name,
Partner Name, Region, Delivery Lead, Primary Resource, Secondary Resource, Number of KPIs in scope, Expected Date,
Business area, Engagement period, ERP, File size, No of Files (optional). Several names in one resource cell can be
separated by commas, "/", "&" or "and".

The earlier KPI matrix is now under **KPI scorecard**.

## How the phases map to code

| Phase | Requirement | Where |
|---|---|---|
| 1 | Configure KPIs, periods, units, owners | `routers/setup.py`, `models.py` |
| 1 | Periodic data entry, approval status | `routers/values.py` (draft → submitted → approved/rejected, reopen) |
| 1 | Evidence upload to Blob | `services/storage.py` (Azure Blob or local), `POST /api/values/{id}/evidence` |
| 1 | Trend & status dashboards | `routers/reports.py`, `frontend/index.html` |
| 2 | FastAPI receives Excel/CSV | `POST /api/files/submissions` |
| 2 | Validate template, columns, periods, numbers | `services/file_processor.py` |
| 2 | Approved formulas & variances | `services/formulas.py` (sandboxed AST evaluator), `services/metrics.py` |
| 2 | Consolidate into standard dataset | `services/consolidation.py` → `consolidated_kpis` table |
| 2 | Excel + presentation-ready outputs | `services/exports.py` (.xlsx and .pptx) |
| 2 | Processing log, rejected-record report | `processing_logs`, `rejected_records`, `GET /api/jobs/{id}/rejected.xlsx` |
| 3 | LangChain commentary from validated results, definitions, approved history | `services/ai_assistant.py` |
| 3 | Natural-language Q&A over KPIs + documents | `POST /api/ask`, `services/ingestion.py` |
| 3 | Celery: consolidation, ingestion, commentary | `worker.py` (+ beat schedule for automatic consolidation) |
| 3 | Deterministic calculations; AI only explains | LLM never computes; every draft is checked for figures absent from validated results |
| 3 | Reviewer approval before publishing | `routers/commentary.py` (draft → pending_review → approved → published) |

## Design decisions
- **Formulas** reference KPI codes (`OPEX / REVENUE * 100`). Parsed with Python `ast` — no `eval`. Circular references and unknown codes are rejected. A new or edited formula is not calculated until an approver signs it off.
- **Group totals**: input KPIs roll up by `sum` or `avg`; ratio KPIs are recomputed from group totals, never averaged.
- **RAG**: green at/above target (direction-aware), amber within `tolerance_pct`, red beyond.
- **Segregation of duties**: submitters can't approve their own values; commentary requesters can't review their own drafts.
- **No LLM key?** Commentary falls back to a deterministic rule-based draft and Q&A returns the matching KPI rows and document excerpts, so the full workflow runs offline.

## Known limits (be upfront about these)
- **Auth** is header-based (`X-User`, `X-Role`) for demo purposes. Swap `deps.current_user` for Azure AD/OAuth2 JWT validation before real use.
- **Schema** is created with `create_all` on startup; add Alembic migrations for production.
- **Document search** is lexical (TF-IDF). For larger corpora, switch `search_chunks` to pgvector embeddings.
