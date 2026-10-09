import os
import sys
import tempfile
from pathlib import Path

_tmp = tempfile.mkdtemp()
os.environ.update(DATABASE_URL=f"sqlite:///{_tmp}/test.db", CELERY_ALWAYS_EAGER="true",
                  LOCAL_STORAGE_DIR=f"{_tmp}/storage", LLM_PROVIDER="none",
                  AZURE_STORAGE_CONNECTION_STRING="")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.seed import run as seed  # noqa: E402

ADMIN = {"X-User": "alice", "X-Role": "admin"}
SUBMITTER = {"X-User": "sam", "X-Role": "submitter"}
APPROVER = {"X-User": "priya", "X-Role": "approver"}


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        seed()
        yield c
