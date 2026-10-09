"""Document ingestion: extract text, chunk, store; simple lexical retrieval for Q&A."""
import io
import math
import re
from collections import Counter

import pandas as pd
from langchain_text_splitters import RecursiveCharacterTextSplitter
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..models import Document, DocumentChunk
from .storage import get_storage

STOP = set("""the and for are was were with that this from have has had not but what which when where who how
into over under than then them they their there these those its our your you can will would should could about
per all any each other more most less such only own same very also just been being does did doing""".split())


def extract_text(data: bytes, filename: str) -> str:
    name = filename.lower()
    if name.endswith(".pdf"):
        from pypdf import PdfReader
        return "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(data)).pages)
    if name.endswith(".docx"):
        import docx
        d = docx.Document(io.BytesIO(data))
        return "\n".join(p.text for p in d.paragraphs)
    if name.endswith((".xlsx", ".xlsm")):
        sheets = pd.read_excel(io.BytesIO(data), sheet_name=None, dtype=str, engine="openpyxl")
        return "\n\n".join(f"Sheet {n}\n{df.fillna('').to_csv(index=False)}" for n, df in sheets.items())
    if name.endswith(".csv"):
        return pd.read_csv(io.BytesIO(data), dtype=str).fillna("").to_csv(index=False)
    if name.endswith((".txt", ".md")):
        return data.decode("utf-8", errors="ignore")
    raise ValueError(f"Unsupported document type for ingestion: {filename}")


def ingest_document(db: Session, document_id: int) -> int:
    doc = db.get(Document, document_id)
    text = extract_text(get_storage().get(doc.blob_path), doc.filename)
    chunks = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=150).split_text(text)
    db.execute(delete(DocumentChunk).where(DocumentChunk.document_id == doc.id))
    for i, c in enumerate(chunks):
        db.add(DocumentChunk(document_id=doc.id, chunk_index=i, text=c))
    doc.ingested = True
    db.commit()
    return len(chunks)


def tokenize(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9_]+", text.lower()) if len(t) > 2 and t not in STOP]


def search_chunks(db: Session, query: str, k: int = 4) -> list[tuple[DocumentChunk, Document, float]]:
    """TF-IDF style lexical ranking. Swap for pgvector embeddings at larger scale."""
    q_terms = set(tokenize(query))
    if not q_terms:
        return []
    rows = db.execute(select(DocumentChunk, Document).join(Document)).all()
    if not rows:
        return []
    toks = [Counter(tokenize(c.text)) for c, _ in rows]
    n = len(rows)
    df = {t: sum(1 for tc in toks if t in tc) for t in q_terms}
    scored = []
    for (chunk, doc), tc in zip(rows, toks):
        length = sum(tc.values()) or 1
        score = sum((tc[t] / length) * math.log(1 + n / df[t]) for t in q_terms if df[t])
        if score > 0:
            scored.append((chunk, doc, score))
    return sorted(scored, key=lambda x: -x[2])[:k]
