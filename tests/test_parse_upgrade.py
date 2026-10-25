"""Saved papers remain readable while local layout upgrades preserve translations."""
from copy import deepcopy
import importlib
import threading
import time

from fastapi.testclient import TestClient
import pymupdf as fitz
import pytest

from backend import storage as db

api = importlib.import_module("backend.app")


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATA", tmp_path / "upgrade-data")
    api.jobs.clear()
    api.figure_jobs.clear()
    api.parse_jobs.clear()
    monkeypatch.setattr(api, "extract_figures", lambda *args, **kwargs: [])
    with TestClient(api.app) as session:
        yield session
    assert not api.parse_jobs


def seed():
    paragraph = dict(id="old", kind="paragraph", page=1, level=0,
                     text="011 A quantiza- 012 tion method works.",
                     translation="011 这种量化方法有效。", bbox=[24, 100, 280, 130], error=None)
    paper = dict(id="saved-layout", filename="source.pdf", title="A local saved paper",
                 source_format="pdf", created_at="2026-01-01", page_count=1,
                 parse_version=api.PARSE_VERSION - 1, status="paused", error=None,
                 paragraphs=[paragraph], outline=[], warnings=[], figures=[],
                 figure_status="completed", figure_version=api.FIGURE_VERSION)
    db.save_paper(db.refresh_progress(paper))
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((50, 100), "A local source file remains unchanged.")
        raw = doc.tobytes()
    path = db.DATA / "papers" / "saved-layout.pdf"
    path.write_bytes(raw)
    return paper, raw, path


def updated():
    return dict(title="A local saved paper", page_count=1, parse_version=api.PARSE_VERSION,
                paragraphs=[dict(id="p-1", kind="paragraph", page=1, level=0,
                                 text="A quantization method works.", bbox=[50, 100, 280, 130])],
                outline=[], warnings=[], removed_line_numbers=[
                    dict(page=1, text="011", bbox=[24, 101, 37, 109]),
                    dict(page=1, text="012", bbox=[24, 117, 37, 125])])


def wait(client, predicate):
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        response = client.get("/api/papers/saved-layout")
        assert response.status_code == 200
        value = response.json()
        if predicate(value):
            return value
        time.sleep(.01)
    pytest.fail("local parse upgrade did not finish")


def test_upgrade_does_not_block_reading_or_duplicate_work_and_preserves_latest_translation(client, monkeypatch):
    old, original, path = seed()
    started, release = threading.Event(), threading.Event()
    calls = []

    def parse(raw, filename):
        calls.append(filename)
        assert raw == original
        started.set()
        assert release.wait(4)
        return updated()

    monkeypatch.setattr(api, "extract_pdf", parse)
    try:
        first = client.get("/api/papers/saved-layout").json()
        assert started.wait(1) and not release.is_set()
        assert first["paragraphs"] == old["paragraphs"]
        assert first["parse_status"] == "updating"
        assert client.get("/api/health").json()["ok"]
        assert client.get("/api/papers/saved-layout").json()["parse_status"] == "updating"
        assert len(calls) == 1
        assert client.post("/api/papers/saved-layout/translate", json={}).status_code == 409
        assert client.post("/api/papers/saved-layout/paragraphs/old/translate").status_code == 409
        latest = db.get_paper("saved-layout")
        latest["paragraphs"][0]["translation"] = "011 最新保存的完整译文。"
        db.save_paper(latest)
    finally:
        release.set()
    result = wait(client, lambda p: p.get("parse_status") == "completed")
    assert result["parse_version"] == api.PARSE_VERSION
    assert result["paragraphs"][0]["translation"] == "最新保存的完整译文。"
    assert result["translation_history"][-1]["paragraphs"][0]["translation"] == "011 最新保存的完整译文。"
    assert path.read_bytes() == original


def test_failed_upgrade_keeps_all_saved_content_and_does_not_retry_on_each_poll(client, monkeypatch):
    old, original, path = seed()
    calls = []

    def fail(*args):
        calls.append(True)
        raise ValueError("private diagnostic detail must stay out of responses")

    monkeypatch.setattr(api, "extract_pdf", fail)
    client.get("/api/papers/saved-layout")
    result = wait(client, lambda p: p.get("parse_status") == "error")
    assert result["paragraphs"] == old["paragraphs"]
    assert "private diagnostic" not in str(result)
    assert result["parse_attempt_version"] == api.PARSE_VERSION
    client.get("/api/papers/saved-layout")
    assert len(calls) == 1 and path.read_bytes() == original


@pytest.mark.parametrize("changes", [dict(is_demo=True), dict(source_format="txt"),
                                    dict(parse_version=api.PARSE_VERSION), dict(status="translating")])
def test_demo_other_formats_current_or_translating_papers_do_not_start_an_upgrade(client, monkeypatch, changes):
    saved, _, _ = seed()
    saved.update(changes)
    before = deepcopy(saved["paragraphs"])
    db.save_paper(saved)
    monkeypatch.setattr(api, "extract_pdf", lambda *args: pytest.fail("unexpected parse"))
    result = client.get("/api/papers/saved-layout").json()
    assert result["paragraphs"] == before
    assert not api.parse_jobs


def test_figure_poll_during_parse_handoff_cannot_start_a_worker_with_old_anchors(client, monkeypatch):
    saved, _, _ = seed()
    saved.update(parse_status="updating", figure_status="pending")
    saved.pop("figure_version", None)
    db.save_paper(saved)
    calls = []

    async def artwork(*args):
        calls.append(args)

    monkeypatch.setattr(api, "figure_job", artwork)

    async def handoff():
        api.ensure_figures(db.get_paper(saved["id"]))
        assert not api.figure_jobs
        assert not calls
        current = db.get_paper(saved["id"])
        current.update(parse_status="completed", paragraphs=updated()["paragraphs"])
        db.save_paper(current)
        api.ensure_figures(current)
        await api.figure_jobs[saved["id"]]
        assert len(calls) == 1

    client.portal.call(handoff)
