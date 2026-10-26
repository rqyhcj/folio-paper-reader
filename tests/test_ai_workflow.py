"""Optional AI layout jobs use isolated data and no real provider requests."""
import asyncio
from copy import deepcopy
import importlib
import json
import threading
import time

from fastapi.testclient import TestClient
import pymupdf as fitz
import pytest

from backend import ai_layout, storage as db

api = importlib.import_module("backend.app")
SETTINGS = {"provider": "custom", "endpoint": "https://example.invalid/v1/chat/completions",
            "model": "configured-text-model", "api_key": "tk-test-layout-only-secret", "concurrency": 1}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATA", tmp_path / "layout-workflow")
    for registry in (api.jobs, api.figure_jobs, api.parse_jobs, api.layout_jobs):
        registry.clear()
    monkeypatch.setattr(db, "settings_private", lambda: dict(SETTINGS))
    monkeypatch.setattr(api, "extract_figures", lambda *args, **kwargs: [])

    async def forbidden(*args, **kwargs):
        pytest.fail("unexpected provider request: workflow tests must stub this operation")

    monkeypatch.setattr(api, "analyze_page", forbidden)
    monkeypatch.setattr(api, "translate_text", forbidden)
    with TestClient(api.app) as session:
        yield session
    assert not api.layout_jobs and not api.jobs and not api.parse_jobs


def seed(*, translated=True, paper_id="layout-source", source_format="pdf", demo=False):
    paragraphs = [
        dict(id="first", text="A quantiza-", source_text="A quantiza-", page=1, kind="paragraph", level=0,
             bbox=[50, 80, 290, 100], page_width=600, page_height=800,
             translation="一种量化" if translated else None, error=None),
        dict(id="second", text="tion method works.", source_text="tion method works.", page=1, kind="paragraph", level=0,
             bbox=[50, 103, 290, 124], page_width=600, page_height=800,
             translation="方法有效。" if translated else None, error=None),
        dict(id="unaffected", text="The second page remains available.", page=2, kind="paragraph", level=0,
             bbox=[50, 80, 290, 100], page_width=600, page_height=800,
             translation="第二页保留完整译文。", error=None, local_metadata={"keep": True}),
    ]
    for paragraph in paragraphs:
        paragraph["section_id"] = "section-1"
    saved = dict(id=paper_id, title="Synthetic saved document", filename="source.pdf", created_at="2026-01-01",
                 source_format=source_format, page_count=2, paragraphs=paragraphs, outline=[], figures=[], warnings=[],
                 parse_version=api.PARSE_VERSION, status="paused", error=None,
                 figure_status="completed", figure_version=api.FIGURE_VERSION, is_demo=demo)
    db.save_paper(db.refresh_progress(saved))
    with fitz.open() as document:
        for _ in range(2):
            page = document.new_page(width=600, height=800)
            page.insert_text((50, 80), "A synthetic local source paragraph.")
        raw = document.tobytes()
    (db.DATA / "papers" / f"{paper_id}.pdf").write_bytes(raw)
    return saved, raw


def proposal(paper, page):
    blocks = ai_layout._page_blocks(paper, page)
    grouped = ([{"ids": [p["id"] for p in blocks], "kind": "paragraph"}] if page == 1
               else [{"ids": [p["id"]], "kind": p["kind"]} for p in blocks])
    return {"paper_id": paper["id"], "page": page, "vision": False, "model": "configured-text-model",
            "page_size": [600, 800], "source_ids": [p["id"] for p in blocks],
            "source_fingerprint": ai_layout._fingerprint(paper, blocks),
            "groups": ai_layout._normalize_groups(grouped, blocks, page, False), "regions": []}


def wait(client, predicate, *, paper_id="layout-source", timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get("/api/papers/" + paper_id)
        assert response.status_code == 200
        value = response.json()
        if predicate(value):
            return value
        time.sleep(.01)
    pytest.fail("AI workflow did not reach the expected state")


async def gate(event):
    while not event.is_set():
        await asyncio.sleep(.01)


def test_get_and_import_do_not_start_ai_or_send_private_document_content(client):
    old, raw = seed()
    assert client.get("/api/papers/layout-source").status_code == 200
    imported = client.post("/api/papers", files={"file": ("second-source.pdf", raw, "application/pdf")})
    assert imported.status_code in (200, 201)
    assert not api.layout_jobs
    assert db.get_paper(old["id"])["paragraphs"] == old["paragraphs"]


def test_default_request_only_analyzes_current_page_then_translates_complete_sentence(client, monkeypatch):
    saved, raw = seed(translated=False)
    analyses, translations = [], []

    async def analyze(current, page, source, config, *, vision=False, **kwargs):
        assert source == raw
        analyses.append((page, vision, dict(config)))
        return proposal(current, page)

    async def translate(text, config):
        translations.append(text)
        return "一种量化方法有效。"

    monkeypatch.setattr(api, "analyze_page", analyze)
    monkeypatch.setattr(api, "translate_text", translate)
    response = client.post("/api/papers/layout-source/analyze", json={})
    assert response.status_code == 200
    finished = wait(client, lambda p: p.get("ai_layout_status") == "completed")
    assert [item[:2] for item in analyses] == [(1, False)]
    assert translations == ["A quantization method works."]
    assert finished["paragraphs"][0]["translation"] == "一种量化方法有效。"
    assert finished["paragraphs"][-1] == saved["paragraphs"][-1]
    assert finished["ai_layout_applied_pages"] == [1]
    assert finished["ai_layout_done_pages"] == [1]
    assert SETTINGS["api_key"] not in json.dumps(finished)


def test_model_override_is_per_job_and_saved_translations_are_archived_only_for_changed_page(client, monkeypatch):
    saved, _ = seed()
    seen = []

    async def analyze(current, page, source, config, **kwargs):
        seen.append(dict(config))
        value = proposal(current, page)
        value["model"] = config["model"]
        return value

    monkeypatch.setattr(api, "analyze_page", analyze)
    response = client.post("/api/papers/layout-source/analyze", json={"model": "one-job-override", "translate_after": False})
    assert response.status_code == 200
    finished = wait(client, lambda p: p.get("ai_layout_status") == "completed")
    assert seen[0]["model"] == "one-job-override"
    assert db.settings_private()["model"] == SETTINGS["model"]
    assert not (db.DATA / "settings.json").exists()
    assert finished["paragraphs"][-1] == saved["paragraphs"][-1]
    archived = [p for entry in finished.get("translation_history", []) for p in entry["paragraphs"]]
    assert {p["id"] for p in archived} == {"first", "second"}
    assert [p["translation"] for p in archived] == ["一种量化", "方法有效。"]
    assert SETTINGS["api_key"] not in response.text and SETTINGS["api_key"] not in json.dumps(finished)


def test_running_analysis_keeps_reading_available_and_blocks_conflicting_translation_and_analysis(client, monkeypatch):
    saved, _ = seed()
    release, started = threading.Event(), threading.Event()
    calls = []

    async def analyze(current, page, source, config, **kwargs):
        calls.append(page)
        started.set()
        await gate(release)
        return proposal(current, page)

    monkeypatch.setattr(api, "analyze_page", analyze)
    try:
        assert client.post("/api/papers/layout-source/analyze", json={"translate_after": False}).status_code == 200
        assert started.wait(1)
        visible = client.get("/api/papers/layout-source").json()
        assert visible["ai_layout_status"] == "running"
        assert visible["paragraphs"] == saved["paragraphs"]
        assert client.get("/api/health").json()["ok"]
        assert client.post("/api/papers/layout-source/translate", json={}).status_code == 409
        assert client.post("/api/papers/layout-source/paragraphs/first/translate").status_code == 409
        assert client.post("/api/papers/layout-source/analyze", json={"translate_after": False}).status_code == 409
        assert calls == [1]
    finally:
        release.set()
    wait(client, lambda p: p.get("ai_layout_status") == "completed")


def test_pause_preserves_completed_pages_and_resume_does_not_reanalyze_them(client, monkeypatch):
    seed()
    release, second_started = threading.Event(), threading.Event()
    calls = []

    async def analyze(current, page, source, config, **kwargs):
        calls.append(page)
        if page == 2:
            second_started.set()
            await gate(release)
        return proposal(current, page)

    monkeypatch.setattr(api, "analyze_page", analyze)
    payload = {"scope": "all", "translate_after": False}
    try:
        assert client.post("/api/papers/layout-source/analyze", json=payload).status_code == 200
        assert second_started.wait(2)
        partial = client.get("/api/papers/layout-source").json()
        assert partial["ai_layout_applied_pages"] == [1]
        assert partial["ai_layout_done_pages"] == [1]
        assert partial["paragraphs"][0]["text"] == "A quantization method works."
        paused = client.post("/api/papers/layout-source/analyze/pause").json()
        assert paused["ai_layout_status"] == "paused"
        assert paused["paragraphs"] == partial["paragraphs"]
        assert not api.layout_jobs
    finally:
        release.set()
    assert client.post("/api/papers/layout-source/analyze", json=payload).status_code == 200
    finished = wait(client, lambda p: p.get("ai_layout_status") == "completed")
    assert calls == [1, 2, 2]
    assert finished["ai_layout_done_pages"] == [1, 2]
    assert len(finished.get("translation_history", [])) == len(partial.get("translation_history", []))


def test_pause_during_translation_resumes_applied_page_without_reanalysis(client, monkeypatch):
    seed(translated=False)
    release, translating = threading.Event(), threading.Event()
    analyses, translation_calls = [], []

    async def analyze(current, page, source, config, **kwargs):
        analyses.append(page)
        return proposal(current, page)

    async def translate(text, config):
        translation_calls.append(text)
        translating.set()
        await gate(release)
        return "完整的新译文。"

    monkeypatch.setattr(api, "analyze_page", analyze)
    monkeypatch.setattr(api, "translate_text", translate)
    try:
        assert client.post("/api/papers/layout-source/analyze", json={}).status_code == 200
        assert translating.wait(2)
        paused = client.post("/api/papers/layout-source/analyze/pause").json()
        assert paused["ai_layout_status"] == "paused"
        assert paused["ai_layout_applied_pages"] == [1]
        assert paused["ai_layout_done_pages"] == []
        assert not api.jobs and not api.layout_jobs
    finally:
        release.set()
    assert client.post("/api/papers/layout-source/analyze", json={}).status_code == 200
    finished = wait(client, lambda p: p.get("ai_layout_status") == "completed")
    assert analyses == [1]
    assert translation_calls == ["A quantization method works."] * 2
    assert finished["paragraphs"][0]["translation"] == "完整的新译文。"


def test_second_page_failure_keeps_partial_work_and_hides_private_diagnostics(client, monkeypatch):
    saved, _ = seed()

    async def analyze(current, page, source, config, **kwargs):
        if page == 2:
            raise RuntimeError("private diagnostic " + SETTINGS["api_key"])
        return proposal(current, page)

    monkeypatch.setattr(api, "analyze_page", analyze)
    assert client.post("/api/papers/layout-source/analyze", json={"scope": "all", "translate_after": False}).status_code == 200
    finished = wait(client, lambda p: p.get("ai_layout_status") == "error")
    assert finished["ai_layout_done_pages"] == [1]
    assert finished["paragraphs"][0]["text"] == "A quantization method works."
    assert finished["paragraphs"][-1] == saved["paragraphs"][-1]
    assert SETTINGS["api_key"] not in json.dumps(finished)
    assert "private diagnostic" not in json.dumps(finished)


@pytest.mark.parametrize("changes", [{"source_format": "txt"}, {"demo": True}])
def test_unsupported_documents_cannot_start_analysis(client, changes):
    saved, _ = seed(**changes)
    result = client.post("/api/papers/layout-source/analyze", json={})
    assert result.status_code == 400
    assert not api.layout_jobs and db.get_paper(saved["id"])["paragraphs"] == saved["paragraphs"]


def test_running_translation_must_be_paused_before_layout_changes(client, monkeypatch):
    seed(translated=False)
    release, started = threading.Event(), threading.Event()

    async def translate(text, config):
        started.set()
        await gate(release)
        return "译文。"

    monkeypatch.setattr(api, "translate_text", translate)
    try:
        assert client.post("/api/papers/layout-source/translate", json={}).status_code == 200
        assert started.wait(1)
        assert client.post("/api/papers/layout-source/analyze", json={}).status_code == 409
        assert not api.layout_jobs
        assert client.post("/api/papers/layout-source/pause").status_code == 200
    finally:
        release.set()
