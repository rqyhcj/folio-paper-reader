"""Local API integration tests with isolated storage and simulated translations."""

import asyncio
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from html.parser import HTMLParser
import importlib
import json
import os
import re
import threading
import time

from fastapi.testclient import TestClient
import fitz
import pytest

from backend import storage as db
from backend.translator import TranslationError


api = importlib.import_module("backend.app")
PRIVATE_KEY = "tk-integration-test-secret-12345678"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATA", tmp_path / "folio-data")
    api.jobs.clear()
    with TestClient(api.app) as session:
        yield session
    assert not any(not task.done() for task in api.jobs.values())
    api.jobs.clear()


def configure(client, *, concurrency=1):
    response = client.put("/api/settings", json={
        "provider": "custom", "endpoint": "https://example.invalid/v1/chat/completions",
        "model": "demo-model", "api_key": PRIVATE_KEY, "concurrency": concurrency,
    })
    assert response.status_code == 200, response.text
    return response.json()


def paper_fixture(paper_id="test-paper"):
    paragraphs = [
        {"id": "p-1", "text": "1 Introduction", "kind": "heading", "level": 1},
        {"id": "p-2", "text": "Second paragraph with a formula x = y and citation [1].", "kind": "paragraph", "level": 0},
        {"id": "p-3", "text": "Third paragraph describes the experimental result.", "kind": "paragraph", "level": 0},
    ]
    for paragraph in paragraphs:
        paragraph.update(translation=None, error=None, page=1, section_id="s-1")
    return db.refresh_progress({
        "id": paper_id,
        "title": "A Test Paper",
        "filename": "test-paper.pdf",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "idle",
        "error": None,
        "is_demo": False,
        "page_count": 1,
        "paragraphs": paragraphs,
        "outline": [{"id": "s-1", "title": "1 Introduction", "level": 1, "page": 1, "paragraph_id": "p-1"}],
        "warnings": [],
    })


def seed_paper(paper_id="test-paper"):
    paper = paper_fixture(paper_id)
    db.save_paper(paper)
    return paper


def wait_for_paper(client, paper_id, predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"/api/papers/{paper_id}")
        assert response.status_code == 200, response.text
        paper = response.json()
        if predicate(paper):
            return paper
        time.sleep(0.01)
    pytest.fail(f"paper did not reach expected state: {paper}")


def pdf_bytes(*, blank=False):
    document = fitz.open()
    page = document.new_page()
    if not blank:
        page.insert_text((60, 70), "Reading Scientific Papers", fontsize=21)
        page.insert_text((60, 130), "1 Introduction", fontsize=15)
        page.insert_text((60, 170), "This paper explains a readable bilingual interface for research.", fontsize=11)
        page.insert_text((60, 220), "2 Methods", fontsize=15)
        page.insert_text((60, 260), "We preserve the English paragraph and display the Chinese translation below it.", fontsize=11)
        document.set_metadata({"title": "Reading Scientific Papers"})
        document.set_toc([[1, "Introduction", 1], [1, "Methods", 1]])
    raw = document.tobytes()
    document.close()
    return raw


def test_import_pdf_persists_outline_and_original(client):
    raw = pdf_bytes()
    response = client.post("/api/papers", files={"file": ("C:\\papers\\research.pdf", raw, "application/pdf")})
    assert response.status_code == 200, response.text
    paper = response.json()
    assert paper["title"] == "Reading Scientific Papers"
    assert paper["filename"] == "research.pdf"
    assert paper["page_count"] == 1
    assert paper["paragraph_count"] >= 4
    assert paper["progress"] == {"total": paper["paragraph_count"], "done": 0, "failed": 0}
    assert len(paper["outline"]) == 2
    paragraph_ids = {paragraph["id"] for paragraph in paper["paragraphs"]}
    assert all(section["paragraph_id"] in paragraph_ids for section in paper["outline"])
    assert client.get(f"/api/papers/{paper['id']}").json() == paper
    assert client.get("/api/papers").json()[0]["id"] == paper["id"]
    original = client.get(f"/api/papers/{paper['id']}/original")
    assert original.status_code == 200
    assert original.content == raw
    assert "application/pdf" in original.headers["content-type"]
    assert (db.DATA / "papers" / f"{paper['id']}.pdf").read_bytes() == raw


@pytest.mark.parametrize("raw", [b"not a pdf", b"%PDF-1.7\ncorrupted body\n%%EOF"])
def test_invalid_or_corrupt_pdf_returns_readable_error_without_creating_paper(client, raw):
    response = client.post("/api/papers", files={"file": ("broken.pdf", raw, "application/pdf")})
    assert response.status_code == 400
    assert "PDF" in response.json()["detail"]
    assert client.get("/api/papers").json() == []
    assert list((db.DATA / "papers").iterdir()) == []


def test_blank_pdf_reports_missing_text_layer(client):
    response = client.post("/api/papers", files={"file": ("scan.pdf", pdf_bytes(blank=True), "application/pdf")})
    assert response.status_code == 400
    assert "OCR" in response.json()["detail"]
    assert client.get("/api/papers").json() == []


def test_upload_limit_is_enforced_before_pdf_parsing(client, monkeypatch):
    monkeypatch.setattr(api, "MAX_UPLOAD", 32)
    response = client.post("/api/papers", files={"file": ("large.pdf", pdf_bytes(), "application/pdf")})
    assert response.status_code == 413
    assert client.get("/api/papers").json() == []


@pytest.mark.skipif(os.name != "nt", reason="Windows DPAPI round trip requires Windows")
def test_settings_private_key_is_encrypted_and_only_masked_value_is_public(client):
    public = configure(client, concurrency=3)
    assert public["has_key"] is True
    assert public["key_hint"] == "•••• " + PRIVATE_KEY[-4:]
    assert "api_key" not in public
    assert PRIVATE_KEY not in json.dumps(public)
    persisted = (db.DATA / "settings.json").read_text(encoding="utf-8")
    assert PRIVATE_KEY not in persisted
    assert json.loads(persisted)["protected_key"]
    assert db.settings_private()["api_key"] == PRIVATE_KEY
    assert client.get("/api/settings").json() == public
    # Editing another setting must preserve the existing encrypted key.
    changed = client.put("/api/settings", json={"model": "another-model"})
    assert changed.status_code == 200
    assert changed.json()["has_key"] is True
    assert db.settings_private()["api_key"] == PRIVATE_KEY
    assert db.settings_private()["concurrency"] == 3
    cleared = client.put("/api/settings", json={"api_key": ""})
    assert cleared.status_code == 200
    assert cleared.json()["has_key"] is False
    assert db.settings_private()["api_key"] == ""


def test_connection_check_uses_current_key_without_saving_unsaved_settings(client, monkeypatch):
    configure(client)
    calls = []

    async def fake_connection(settings):
        calls.append(dict(settings))
        return {"ok": True, "message": "连接成功", "model": settings["model"]}

    monkeypatch.setattr(api, "test_connection", fake_connection)
    response = client.post("/api/settings/test", json={"model": "temporary-model"})
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert calls[0]["api_key"] == PRIVATE_KEY
    assert calls[0]["model"] == "temporary-model"
    assert db.settings_private()["model"] == "demo-model"
    assert PRIVATE_KEY not in response.text


def test_connection_error_is_actionable(client, monkeypatch):
    configure(client)

    async def fake_connection(settings):
        raise TranslationError("API 验证失败，请检查 API Key。")

    monkeypatch.setattr(api, "test_connection", fake_connection)
    response = client.post("/api/settings/test", json={})
    assert response.status_code == 400
    assert "验证失败" in response.json()["detail"]
    assert PRIVATE_KEY not in response.text


@pytest.mark.parametrize("payload", [
    {"endpoint": "http://remote.example.com/chat"},
    {"concurrency": 0}, {"concurrency": 5}, {"model": ""}, {"api_key": "key with spaces"},
])
def test_invalid_settings_rejected_without_saving(client, payload):
    response = client.put("/api/settings", json=payload)
    assert response.status_code in {400, 422}
    assert not (db.DATA / "settings.json").exists()


@pytest.mark.parametrize("headers", [
    {"Origin": "https://evil.example"},
    {"Origin": "http://localhost:9999"},
    {"Origin": "null"},
    {"Origin": "http://localhost:bad"},
    {"Sec-Fetch-Site": "cross-site"},
])
def test_cross_site_and_malformed_origin_writes_are_rejected(client, headers):
    response = client.put("/api/settings", json={"model": "unwanted-model"}, headers=headers)
    assert response.status_code == 403
    assert not (db.DATA / "settings.json").exists()


def test_local_page_write_is_allowed_and_api_responses_are_not_cached(client):
    response = client.put("/api/settings", json={
        "endpoint": "https://example.invalid/v1/chat/completions", "model": "local-model",
    }, headers={"Origin": "http://localhost"})
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert client.get("/api/health").json()["ok"] is True


def test_save_reload_and_exports_keep_english_chinese_order_and_escape_html(client):
    paper = seed_paper()
    paper["title"] = 'A <script>alert("title")</script> Paper'
    paper["paragraphs"][0].update(text="<img src=x onerror=alert(1)>", translation="标题的中文")
    paper["paragraphs"][1]["translation"] = "第二段中文，保留 x = y 和引用 [1]。"
    db.save_paper(db.refresh_progress(paper))
    loaded = client.get("/api/papers/test-paper").json()
    assert loaded["translated_count"] == 2
    assert loaded["paragraphs"][1]["translation"] == paper["paragraphs"][1]["translation"]
    exported_md = client.get("/api/papers/test-paper/export?format=markdown")
    assert exported_md.status_code == 200
    assert exported_md.text.index(paper["paragraphs"][1]["text"]) < exported_md.text.index(paper["paragraphs"][1]["translation"])
    assert "[尚未翻译]" in exported_md.text
    assert "attachment;" in exported_md.headers["content-disposition"]
    exported_html = client.get("/api/papers/test-paper/export?format=html")
    assert exported_html.status_code == 200
    # Trusted, bundled KaTeX scripts are inline so the export works offline.
    # The document title and paper text must still be escaped, never scripts.
    assert '<script>alert("title")</script>' not in exported_html.text
    assert "<img src=x" not in exported_html.text
    assert "&lt;script&gt;" in exported_html.text
    assert "&lt;img src=x" in exported_html.text
    assert "第二段中文" in exported_html.text
    assert "renderMathInElement(document.body" in exported_html.text
    assert "data:font/woff2;base64," in exported_html.text
    assert not re.search(r'<script[^>]+\bsrc\s*=', exported_html.text, re.I)
    assert not re.search(r'<link[^>]+\b(?:href|src)\s*=', exported_html.text, re.I)
    font_urls = re.findall(r'url\(([^)]+)\)', exported_html.text)
    assert font_urls and all(url.strip("\"'").startswith("data:font/") for url in font_urls)
    assert client.get("/api/papers/test-paper/export?format=unknown").status_code == 400


def test_partial_translation_is_persisted_and_single_failed_paragraph_can_be_retried(client, monkeypatch):
    configure(client)
    paper = seed_paper()
    calls = Counter()

    async def fake_translate(text, settings):
        assert settings["api_key"] == PRIVATE_KEY
        calls[text] += 1
        if text.startswith("Second") and calls[text] == 1:
            raise TranslationError("模拟 API 暂时繁忙，请重试。", retryable=True)
        return "中文译文：" + text

    monkeypatch.setattr(api, "translate_text", fake_translate)
    assert client.post("/api/papers/test-paper/translate", json={}).status_code == 200
    failed = wait_for_paper(client, "test-paper", lambda item: item["status"] == "error")
    assert failed["progress"] == {"total": 3, "done": 2, "failed": 1}
    assert failed["paragraphs"][1]["translation"] is None
    assert "暂时繁忙" in failed["paragraphs"][1]["error"]
    assert db.get_paper("test-paper")["progress"] == failed["progress"]
    retried = client.post("/api/papers/test-paper/paragraphs/p-2/translate")
    assert retried.status_code == 200
    assert retried.json()["status"] == "completed"
    assert retried.json()["progress"] == {"total": 3, "done": 3, "failed": 0}
    assert calls[paper["paragraphs"][0]["text"]] == 1
    assert calls[paper["paragraphs"][1]["text"]] == 2
    assert calls[paper["paragraphs"][2]["text"]] == 1
    assert db.get_paper("test-paper")["translated_count"] == 3


def test_bulk_continue_only_retries_untranslated_paragraphs(client, monkeypatch):
    configure(client)
    paper = seed_paper()
    paper["paragraphs"][0]["translation"] = "已经完成的标题"
    paper["paragraphs"][1]["error"] = "上次请求失败"
    paper["status"] = "error"
    db.save_paper(db.refresh_progress(paper))
    calls = []

    async def fake_translate(text, settings):
        calls.append(text)
        return "新译文"

    monkeypatch.setattr(api, "translate_text", fake_translate)
    assert client.post("/api/papers/test-paper/translate", json={"retry_failed": True}).status_code == 200
    finished = wait_for_paper(client, "test-paper", lambda item: item["status"] == "completed")
    assert len(calls) == 2
    assert paper["paragraphs"][0]["text"] not in calls
    assert finished["paragraphs"][0]["translation"] == "已经完成的标题"
    assert finished["progress"] == {"total": 3, "done": 3, "failed": 0}


def test_pause_cancels_inflight_work_and_resume_preserves_completed_paragraphs(client, monkeypatch):
    configure(client)
    paper = seed_paper()
    blocked = threading.Event()
    cancelled = threading.Event()
    calls = Counter()

    async def fake_translate(text, settings):
        calls[text] += 1
        if text.startswith("Second") and calls[text] == 1:
            blocked.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise
        return "中文：" + text

    monkeypatch.setattr(api, "translate_text", fake_translate)
    assert client.post("/api/papers/test-paper/translate", json={}).status_code == 200
    assert blocked.wait(timeout=3)
    paused = client.post("/api/papers/test-paper/pause")
    assert paused.status_code == 200
    assert cancelled.is_set()
    saved = paused.json()
    assert saved["status"] == "paused"
    assert saved["progress"] == {"total": 3, "done": 1, "failed": 0}
    assert saved["paragraphs"][0]["translation"]
    assert saved["paragraphs"][1]["translation"] is None
    assert db.get_paper("test-paper")["status"] == "paused"
    assert client.post("/api/papers/test-paper/translate", json={}).status_code == 200
    completed = wait_for_paper(client, "test-paper", lambda item: item["status"] == "completed")
    assert completed["progress"] == {"total": 3, "done": 3, "failed": 0}
    assert calls[paper["paragraphs"][0]["text"]] == 1
    assert calls[paper["paragraphs"][1]["text"]] == 2


def test_start_requires_key_and_missing_papers_are_404(client):
    seed_paper()
    configured = client.put("/api/settings", json={
        "endpoint": "https://example.invalid/v1/chat/completions", "model": "demo-model",
    })
    assert configured.status_code == 200
    response = client.post("/api/papers/test-paper/translate", json={})
    assert response.status_code == 400
    assert "密钥" in response.json()["detail"]
    assert db.get_paper("test-paper")["status"] == "idle"
    assert client.get("/api/papers/missing").status_code == 404
    assert client.delete("/api/papers/missing").status_code == 404


def test_demo_is_stored_once_and_has_no_original_pdf(client):
    first = client.get("/api/demo")
    second = client.get("/api/demo")
    assert first.status_code == 200
    assert first.json() == second.json()
    assert first.json()["is_demo"] is True
    assert first.json()["translated_count"] == first.json()["paragraph_count"]
    assert len(client.get("/api/papers").json()) == 1
    assert client.get("/api/papers/demo/original").status_code == 404


def test_delete_removes_database_record_and_original(client):
    response = client.post("/api/papers", files={"file": ("paper.pdf", pdf_bytes(), "application/pdf")})
    assert response.status_code == 200
    paper_id = response.json()["id"]
    assert client.delete(f"/api/papers/{paper_id}").status_code == 200
    assert client.get(f"/api/papers/{paper_id}").status_code == 404
    assert not (db.DATA / "papers" / f"{paper_id}.pdf").exists()


def test_restarting_storage_marks_interrupted_translation_paused(client):
    paper = seed_paper()
    paper["status"] = "translating"
    paper["paragraphs"][0]["translation"] = "保存的标题译文"
    db.save_paper(db.refresh_progress(paper))
    db.init_storage()
    saved = db.get_paper("test-paper")
    assert saved["status"] == "paused"
    assert saved["paragraphs"][0]["translation"] == "保存的标题译文"
    assert saved["progress"] == {"total": 3, "done": 1, "failed": 0}


def test_single_paragraph_job_blocks_duplicate_work_and_can_be_paused(client, monkeypatch):
    configure(client)
    paper = seed_paper()
    paper["paragraphs"][0]["translation"] = "已经保存的标题"
    db.save_paper(db.refresh_progress(paper))
    seed_paper("other-paper")
    blocked = threading.Event()
    cancelled = threading.Event()
    calls = []

    async def fake_translate(text, settings):
        calls.append(text)
        blocked.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    monkeypatch.setattr(api, "translate_text", fake_translate)
    with ThreadPoolExecutor(max_workers=1) as executor:
        request = executor.submit(client.post, "/api/papers/test-paper/paragraphs/p-2/translate")
        try:
            assert blocked.wait(timeout=3)
            original_task = api.jobs["test-paper"]
            # Repeated start is idempotent: it returns the existing job's state.
            same_paper = client.post("/api/papers/test-paper/translate", json={})
            assert same_paper.status_code == 200
            assert same_paper.json()["status"] == "translating"
            assert api.jobs["test-paper"] is original_task
            other_paper = client.post("/api/papers/other-paper/translate", json={})
            assert other_paper.status_code == 409
            assert db.get_paper("other-paper")["status"] == "idle"
            assert calls == [paper["paragraphs"][1]["text"]]
        finally:
            paused = client.post("/api/papers/test-paper/pause")
        result = request.result(timeout=3)

    assert paused.status_code == 200
    assert result.status_code == 200
    assert cancelled.is_set()
    assert "test-paper" not in api.jobs
    saved = db.get_paper("test-paper")
    assert saved["status"] == "paused"
    assert saved["progress"] == {"total": 3, "done": 1, "failed": 0}
    assert saved["paragraphs"][0]["translation"] == "已经保存的标题"
    assert saved["paragraphs"][1]["translation"] is None
    assert result.json()["status"] == "paused"


def test_index_links_to_loadable_stylesheet_and_javascript(client):
    class ResourceParser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.stylesheets = []
            self.scripts = []

        def handle_starttag(self, tag, attrs):
            attributes = dict(attrs)
            if tag == "link" and "stylesheet" in attributes.get("rel", "").split():
                self.stylesheets.append(attributes.get("href"))
            if tag == "script" and attributes.get("src"):
                self.scripts.append(attributes["src"])

    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    resources = ResourceParser()
    resources.feed(response.text)
    assert resources.stylesheets
    assert resources.scripts
    for path in resources.stylesheets:
        assert path and path.startswith("/")
        stylesheet = client.get(path)
        assert stylesheet.status_code == 200, path
        assert "text/css" in stylesheet.headers["content-type"]
        assert len(stylesheet.content) > 100
    for path in resources.scripts:
        assert path.startswith("/")
        script = client.get(path)
        assert script.status_code == 200, path
        assert "javascript" in script.headers["content-type"]
        assert len(script.content) > 100
