"""Import/read/translation remain usable while artwork is processed locally."""
import asyncio
from datetime import datetime, timezone
import importlib
import threading
import time

from fastapi.testclient import TestClient
import pymupdf as fitz
import pytest

from backend import storage as db
from backend.figures import extract_figures
from backend.pdf_parser import extract_pdf

api = importlib.import_module("backend.app")


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATA", tmp_path / "progressive-data")
    api.jobs.clear()
    api.figure_jobs.clear()
    with TestClient(api.app) as session:
        yield session
    assert not api.jobs
    assert not api.figure_jobs


def raw_pdf():
    with fitz.open() as document:
        page = document.new_page()
        page.insert_text((50, 65), "A Local Progressive Import Test", fontsize=20)
        page.insert_text((50, 120), "1 Introduction", fontsize=14)
        page.insert_text((50, 165), "Readers should see this paragraph before figures finish.", fontsize=11)
        return document.tobytes()


def upload(client):
    response = client.post("/api/papers", files={"file": ("local.pdf", raw_pdf(), "application/pdf")})
    assert response.status_code == 200, response.text
    return response.json()


def wait_for(client, paper_id, predicate):
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        response = client.get(f"/api/papers/{paper_id}")
        assert response.status_code == 200
        paper = response.json()
        if predicate(paper):
            return paper
        time.sleep(.01)
    pytest.fail("background task did not reach the expected state")


def controlled_extractor(monkeypatch):
    """A blocked artwork pass makes the scheduling guarantee deterministic."""
    started, release = threading.Event(), threading.Event()
    calls = []

    def extract(data, paragraphs, destination, paper_id, *, on_progress, should_stop):
        calls.append(paper_id)
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "f-1.png").write_bytes(b"test image placeholder")
        partial = [{"id": "f-1", "page": 1, "after_paragraph_id": paragraphs[-1]["id"],
                    "before_paragraph_id": None, "caption": "Original local test figure",
                    "url": f"/api/papers/{paper_id}/figures/f-1.png", "width": 200, "height": 100}]
        on_progress(partial, 0, 1)
        started.set()
        while not release.wait(.01):
            if should_stop():
                return partial
        if not should_stop():
            on_progress(partial, 1, 1)
        return partial

    monkeypatch.setattr(api, "extract_figures", extract)
    return started, release, calls


def test_import_and_get_return_readable_text_before_artwork_finishes(client, monkeypatch):
    started, release, calls = controlled_extractor(monkeypatch)
    try:
        paper = upload(client)
        assert started.wait(1)
        assert not release.is_set()
        assert paper["paragraphs"] and paper["outline"]
        assert paper["figure_status"] == "extracting"
        assert db.get_paper(paper["id"])["paragraphs"]
        reading = client.get(f"/api/papers/{paper['id']}").json()
        assert reading["paragraphs"] == paper["paragraphs"]
        assert reading["figure_status"] == "extracting"
        assert reading["figures"]
        assert client.get(f"/api/papers/{paper['id']}/original").content.startswith(b"%PDF-")
        assert client.get("/api/health").json()["ok"]
        assert calls == [paper["id"]]
    finally:
        release.set()
    complete = wait_for(client, paper["id"], lambda p: p["figure_status"] == "completed")
    assert complete["figure_progress"] == {"done": 1, "total": 1}


def test_background_artwork_keeps_translations_saved_while_it_was_running(client, monkeypatch):
    started, release, _ = controlled_extractor(monkeypatch)

    async def fake_translation(text, config):
        await asyncio.sleep(0)
        return "已保存的测试译文：" + text

    monkeypatch.setattr(api, "translate_text", fake_translation)
    monkeypatch.setattr(api, "merged_settings", lambda _: {"provider": "ollama", "endpoint": "http://localhost:11434/v1/chat/completions", "concurrency": 1, "api_key": ""})
    try:
        paper = upload(client)
        assert started.wait(1)
        response = client.post(f"/api/papers/{paper['id']}/translate")
        assert response.status_code == 200
        translated = wait_for(client, paper["id"], lambda p: p["status"] == "completed")
        assert translated["figure_status"] == "extracting"
        assert all(p["translation"].startswith("已保存的测试译文") for p in translated["paragraphs"])
    finally:
        release.set()
    complete = wait_for(client, paper["id"], lambda p: p["figure_status"] == "completed")
    assert complete["status"] == "completed"
    assert complete["paragraphs"] == translated["paragraphs"]
    assert complete["translated_count"] == len(complete["paragraphs"])


def test_delete_cancels_artwork_without_restoring_document_or_files(client, monkeypatch):
    started, release, _ = controlled_extractor(monkeypatch)
    try:
        paper = upload(client)
        assert started.wait(1)
        response = client.delete(f"/api/papers/{paper['id']}")
        assert response.status_code == 200
        assert db.get_paper(paper["id"]) is None
        assert paper["id"] not in api.figure_jobs
        assert not api.figure_dir(paper["id"]).exists()
        assert client.get(f"/api/papers/{paper['id']}").status_code == 404
    finally:
        release.set()


def test_reopening_interrupted_artwork_restarts_only_one_background_task(client, monkeypatch):
    started, release, calls = controlled_extractor(monkeypatch)
    raw = raw_pdf()
    paper = db.refresh_progress(dict(extract_pdf(raw, "interrupted.pdf"), id="interrupted",
                                    filename="interrupted.pdf", created_at=datetime.now(timezone.utc).isoformat(),
                                    status="paused", is_demo=False, figures=[], figure_status="extracting",
                                    figure_version=api.FIGURE_VERSION, figure_progress={"done": 0, "total": 1}))
    db.save_paper(paper)
    (db.DATA / "papers" / "interrupted.pdf").write_bytes(raw)
    try:
        response = client.get("/api/papers/interrupted")
        assert response.status_code == 200
        assert response.json()["paragraphs"]
        assert started.wait(1)
        assert client.get("/api/papers/interrupted").json()["figure_status"] == "extracting"
        assert calls == ["interrupted"]
    finally:
        release.set()
    complete = wait_for(client, "interrupted", lambda p: p["figure_status"] == "completed")
    assert complete["status"] == "paused"


def test_shutdown_stops_artwork_and_preserves_text_for_reopening(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATA", tmp_path / "shutdown-data")
    api.jobs.clear()
    api.figure_jobs.clear()
    started, release, _ = controlled_extractor(monkeypatch)
    try:
        with TestClient(api.app) as session:
            paper = upload(session)
            assert started.wait(1)
        saved = db.get_paper(paper["id"])
        assert saved["paragraphs"] == paper["paragraphs"]
        assert saved["figure_status"] == "pending"
        assert not api.figure_jobs
    finally:
        release.set()


def test_translation_skips_independent_equations_and_labels_inside_figures(client, monkeypatch):
    monkeypatch.setattr(api, "extract_figures", lambda *args, **kwargs: [])
    paper = upload(client)
    paper = wait_for(client, paper["id"], lambda p: p["figure_status"] == "completed")
    equation, label, body = paper["paragraphs"][:3]
    equation.update(kind="equation", text="$$x^2 + y^2 = 1$$", math_status="latex")
    label["text"] = "Axis label already in original image"
    paper["figures"] = [{"id": "f-1", "text_paragraph_ids": [label["id"]]}]
    db.save_paper(db.refresh_progress(paper))
    seen = []

    async def translate(text, config):
        seen.append(text)
        return "测试译文"

    monkeypatch.setattr(api, "translate_text", translate)
    monkeypatch.setattr(api, "merged_settings", lambda _: {"provider": "ollama", "endpoint": "http://localhost:11434/v1/chat/completions", "concurrency": 1, "api_key": ""})
    assert client.post(f"/api/papers/{paper['id']}/translate").status_code == 200
    complete = wait_for(client, paper["id"], lambda p: p["status"] == "completed")
    assert seen == [body["text"]]
    assert complete["paragraph_count"] == 3
    assert complete["progress"] == {"total": 1, "done": 1, "failed": 0}
    assert not complete["paragraphs"][0]["translation"]
    assert not complete["paragraphs"][1]["translation"]
    assert client.post(f"/api/papers/{paper['id']}/paragraphs/{equation['id']}/translate").status_code == 400
    assert client.post(f"/api/papers/{paper['id']}/paragraphs/{label['id']}/translate").status_code == 400


def test_control_glyphs_and_punctuation_are_never_sent_as_translation_requests(client, monkeypatch):
    monkeypatch.setattr(api, "extract_figures", lambda *args, **kwargs: [])
    paper = upload(client)
    paper = wait_for(client, paper["id"], lambda p: p["figure_status"] == "completed")
    control, punctuation, body = paper["paragraphs"][:3]
    control["text"] = "\x00 \x01\t"
    punctuation["text"] = " , ; ( ) \ufffd "
    body["text"] = "English prose and 中文正文 remain translatable."
    db.save_paper(db.refresh_progress(paper))
    seen = []

    async def translate(text, config):
        seen.append(text)
        return "正常正文译文"

    monkeypatch.setattr(api, "translate_text", translate)
    monkeypatch.setattr(api, "merged_settings", lambda _: {
        "provider": "ollama", "endpoint": "http://localhost:11434/v1/chat/completions",
        "concurrency": 1, "api_key": ""})
    assert client.post(f"/api/papers/{paper['id']}/translate").status_code == 200
    complete = wait_for(client, paper["id"], lambda p: p["status"] == "completed")
    assert seen == [body["text"]]
    assert complete["progress"] == {"total": 1, "done": 1, "failed": 0}
    assert not complete["paragraphs"][0]["translation"]
    assert not complete["paragraphs"][1]["translation"]
    assert client.post(f"/api/papers/{paper['id']}/paragraphs/{control['id']}/translate").status_code == 400


@pytest.mark.parametrize("reply,accepted", [
    ("均值为 ⟦FOLIO_MATH_1⟧，方差为 ⟦FOLIO_MATH_2⟧。", True),
    ("均值为 x，方差为 y。", False),
    ("您没有提供原文，请提供完整英文原文以便翻译。", False),
])
def test_translation_worker_sends_protected_prose_and_only_saves_a_complete_formula_reply(client, monkeypatch, reply, accepted):
    monkeypatch.setattr(api, "extract_figures", lambda *args, **kwargs: [])
    paper = upload(client)
    paper = wait_for(client, paper["id"], lambda p: p["figure_status"] == "completed")
    for paragraph in paper["paragraphs"][:-1]:
        paragraph["kind"] = "equation"
    body = paper["paragraphs"][-1]
    body.update(text="Raw font q and broken \x00 math", translation=None,
                translation_text="The mean is ⟦FOLIO_MATH_1⟧ and the variance is ⟦FOLIO_MATH_2⟧.",
                translation_review_reason="protected-math-source", previous_translation_parts=[{"translation": "旧片段"}])
    history = [{"parse_version": 4, "paragraphs": [{"id": "legacy", "translation": "完整保存的旧译文"}]}]
    paper["translation_history"] = history
    db.save_paper(db.refresh_progress(paper))
    sent = []

    async def translate(text, config):
        sent.append(text)
        return reply

    monkeypatch.setattr(api, "translate_text", translate)
    monkeypatch.setattr(api, "merged_settings", lambda _: {
        "provider": "ollama", "endpoint": "http://localhost:11434/v1/chat/completions",
        "concurrency": 1, "api_key": ""})
    assert client.post(f"/api/papers/{paper['id']}/translate").status_code == 200
    result = wait_for(client, paper["id"], lambda p: p["status"] in {"completed", "error"})
    assert sent == [body["translation_text"]]
    assert result["translation_history"] == history
    translated = result["paragraphs"][-1]
    if accepted:
        assert translated["translation"] == reply
        assert "translation_review_reason" not in translated
        assert "previous_translation_parts" not in translated
        assert result["progress"] == {"total": 1, "done": 1, "failed": 0}
    else:
        assert translated["translation"] is None and translated["error"]
        assert translated["translation_review_reason"] == "protected-math-source"
        assert result["progress"] == {"total": 1, "done": 0, "failed": 1}


def test_artwork_on_a_page_without_text_stays_between_neighboring_pages(tmp_path):
    with fitz.open() as document:
        document.new_page()
        page = document.new_page()
        page.draw_rect(fitz.Rect(70, 180, 450, 420), color=(.1, .2, .3), width=2)
        page.draw_line((70, 420), (450, 180), color=(.3, .5, .4), width=2)
        document.new_page()
        raw = document.tobytes()
    paragraphs = [{"id": "p-before", "page": 1, "text": "Previous page.", "kind": "paragraph"},
                  {"id": "p-after", "page": 3, "text": "Following page.", "kind": "paragraph"}]
    figures = extract_figures(raw, paragraphs, tmp_path / "figures", "image-page")
    assert len(figures) == 1
    assert figures[0]["page"] == 2
    assert figures[0]["before_paragraph_id"] == "p-after"
    assert figures[0]["after_paragraph_id"] == "p-before"


def test_diagram_labels_cannot_be_anchors_and_are_marked_as_image_duplicates(tmp_path):
    with fitz.open() as document:
        page = document.new_page(width=600, height=800)
        page.draw_rect(fitz.Rect(80, 220, 480, 420), color=(.1, .2, .3), width=2)
        page.draw_line((80, 420), (480, 220), color=(.3, .5, .4), width=2)
        page.insert_text((100, 260), "Inside label", fontsize=11)
        page.insert_text((80, 448), "Figure 1: Original chart", fontsize=10)
        raw = document.tobytes()
    paragraphs = [{"id": "body", "page": 1, "text": "A preceding body paragraph.", "kind": "paragraph", "bbox": [60, 100, 500, 150]},
                  {"id": "label", "page": 1, "text": "Inside label", "kind": "paragraph", "bbox": [100, 248, 200, 264]},
                  {"id": "caption", "page": 1, "text": "Figure 1: Original chart", "kind": "paragraph", "bbox": [80, 436, 400, 452]}]
    figures = extract_figures(raw, paragraphs, tmp_path / "figures", "chart-labels")
    assert len(figures) == 1
    assert figures[0]["before_paragraph_id"] == "caption"
    assert figures[0]["after_paragraph_id"] == "body"
    assert figures[0]["text_paragraph_ids"] == ["label"]
