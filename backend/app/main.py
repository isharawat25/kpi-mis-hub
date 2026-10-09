from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from .config import get_settings
from .database import init_db
from .routers import commentary, files, reports, setup, values
from .services.llm import model_name

FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "index.html"


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title=get_settings().app_name, version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
for r in (setup.router, values.router, files.router, reports.router, commentary.router):
    app.include_router(r)


@app.get("/api/health")
def health():
    return {"status": "ok", "ai_mode": model_name()}


@app.get("/", include_in_schema=False)
def ui():
    return FileResponse(FRONTEND)
