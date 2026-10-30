from datetime import datetime, timezone
import importlib

from fastapi.testclient import TestClient
import pytest

from backend import storage as db
from backend.paper_chat import paper_context


api = importlib.import_module("backend.app")


def paper():
    return db.refresh_progress({
        "id": "chat-paper", "title": "A Grounded Method", "filename": "paper.pdf",
        "created_at": datetime.now(timezone.utc).isoformat(), "source_format": "pdf",
        "page_count": 3, "status": "idle", "error": None, "warnings": [],
        "paragraphs": [
            {"id": "h", "text": "Abstract", "kind": "heading", "page": 1, "translation": None, "error": None},
            {"id": "a", "text": "We reduce activation outliers with a low-rank branch.", "kind": "paragraph", "page": 1, "translation": None, "error": None},
            {"id": "label", "text": "Naive 100 0", "kind": "paragraph", "page": 2, "translation": None, "error": None},
            {"id": "r", "text": "Experiments report lower latency while preserving quality.", "kind": "paragraph", "page": 3, "translation": None, "error": None},
        ],
        "figures": [{"id": "f", "text_paragraph_ids": ["label"]}], "outline": [],
    })


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATA", tmp_path / "chat-data")
    with TestClient(api.app) as session:
        yield session


def test_context_prefers_relevant_and_current_page_but_omits_figure_labels():
    value = paper_context(paper(), "What do experiments say about latency?", page=3)
    assert "lower latency" in value and "low-rank branch" in value
    assert "Naive 100 0" not in value
    assert "[第 3 页]" in value


def test_chat_endpoint_uses_current_model_and_returns_grounded_answer(client, monkeypatch):
    db.save_paper(paper())
    client.put("/api/settings", json={
        "provider": "custom", "endpoint": "https://example.invalid/v1/chat/completions",
        "model": "paper-model", "api_key": "tk-test-paper-chat-secret", "concurrency": 1,
    })
    captured = {}

    async def fake_answer(source, question, settings, *, page=None, history=None):
        captured.update(source=source, question=question, settings=settings, page=page, history=history)
        return "论文用低秩分支处理异常值。[第 1 页]"

    monkeypatch.setattr(api, "answer_paper", fake_answer)
    response = client.post("/api/papers/chat-paper/chat", json={
        "question": "核心方法是什么？", "page": 1,
        "history": [{"role": "user", "content": "先概括问题"}, {"role": "assistant", "content": "好的"}],
    })
    assert response.status_code == 200
    assert response.json()["answer"].endswith("[第 1 页]")
    assert captured["source"]["id"] == "chat-paper" and captured["settings"]["model"] == "paper-model"
    assert captured["question"] == "核心方法是什么？" and captured["page"] == 1
    assert len(captured["history"]) == 2
