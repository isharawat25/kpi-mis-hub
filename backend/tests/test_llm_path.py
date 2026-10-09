"""Exercises the LangChain path with a fake chat model (no API key needed)."""
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from app.services import ai_assistant
from conftest import SUBMITTER


def test_llm_commentary_flags_invented_numbers(client, monkeypatch):
    fake = FakeListChatModel(responses=["Overview\nGroup revenue was 352.08 against target. "
                                        "Margin improved to 47.3% (invented)."])
    monkeypatch.setattr(ai_assistant, "get_llm", lambda: fake)
    monkeypatch.setattr(ai_assistant, "model_name", lambda: "fake-model")
    pid = {p["code"]: p["id"] for p in client.get("/api/periods", headers=SUBMITTER).json()}["2026-08"]
    c = client.post("/api/commentary", headers=SUBMITTER, json={"period_id": pid, "business_unit": "GROUP"}).json()
    assert c["generated_by"] == "fake-model" and c["status"] == "draft"
    assert any("47.3" in w for w in c["validation_warnings"])
    assert not any("352.08" in w for w in c["validation_warnings"])  # real Aug group revenue is not flagged
