"""Original-figure extraction uses synthetic in-memory PDFs and local storage."""

import importlib
import json
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
    monkeypatch.setattr(db, "DATA", tmp_path / "figure-data")
    api.jobs.clear()
    with TestClient(api.app) as session:
        yield session
    assert not any(not task.done() for task in api.jobs.values())
    api.jobs.clear()


def illustrated_pdf(kind="vector"):
    """Create all text and artwork locally; no publication or remote image used."""
    with fitz.open() as document:
        page = document.new_page(width=600, height=800)
        page.insert_text((60, 70), "A Study of Synthetic Research Figures", fontsize=19)
        page.insert_text((60, 125), "1 Introduction", fontsize=14)
        page.insert_text((60, 165), "This selectable paragraph introduces our original test diagram.", fontsize=11)
        if kind == "vector":
            page.draw_rect(fitz.Rect(80, 220, 480, 420), color=(.2, .3, .4), width=1)
            page.draw_line((80, 420), (480, 220), color=(.1, .5, .7), width=3)
            page.draw_line((80, 220), (480, 420), color=(.5, .2, .3), width=2)
            page.insert_text((100, 250), "Synthetic vector chart", fontsize=11)
        elif kind == "raster":
            pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 240, 120), False)
            pixmap.clear_with(240)
            pixmap.set_rect(fitz.IRect(20, 20, 100, 100), (40, 100, 160))
            pixmap.set_rect(fitz.IRect(130, 35, 215, 100), (160, 90, 45))
            page.insert_image(fitz.Rect(80, 220, 480, 420), stream=pixmap.tobytes("png"))
        if kind:
            page.insert_text((80, 448), f"Figure 1: An in-memory {kind} chart.", fontsize=10)
        page.insert_text((60, 510), "The results can be inspected with the original English paragraph.", fontsize=11)
        document.set_metadata({"title": "A Study of Synthetic Research Figures"})
        return document.tobytes()


def import_pdf(client, kind="vector"):
    response = client.post("/api/papers", files={"file": ("synthetic-figures.pdf", illustrated_pdf(kind), "application/pdf")})
    assert response.status_code == 200, response.text
    return wait_for_figures(client, response.json()["id"])


def wait_for_figures(client, paper_id):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        response = client.get(f"/api/papers/{paper_id}")
        assert response.status_code == 200
        paper = response.json()
        if paper.get("figure_status") in {"completed", "error"}:
            return paper
        time.sleep(.01)
    pytest.fail("Background figures did not finish")


@pytest.mark.parametrize("kind", ["vector", "raster"])
def test_import_recovers_original_vector_and_raster_figures_and_serves_png(client, kind):
    paper = import_pdf(client, kind)
    assert paper["figures"]
    paragraphs = {paragraph["id"]: paragraph for paragraph in paper["paragraphs"]}
    figure = next(figure for figure in paper["figures"] if f"{kind} chart" in figure["caption"])
    assert figure["page"] == 1
    assert figure["after_paragraph_id"] in paragraphs
    assert figure["before_paragraph_id"] in paragraphs
    assert "Figure 1" in paragraphs[figure["before_paragraph_id"]]["text"]
    assert figure["bbox"][1] < paragraphs[figure["before_paragraph_id"]]["bbox"][1]
    assert figure["url"] == f"/api/papers/{paper['id']}/figures/{figure['id']}.png"
    assert figure["width"] >= 100 and figure["height"] >= 80
    response = client.get(figure["url"])
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content.startswith(b"\x89PNG\r\n\x1a\n")
    pixmap = fitz.Pixmap(response.content)
    assert (pixmap.width, pixmap.height) == (figure["width"], figure["height"])
    saved_path = api.figure_dir(paper["id"]) / f"{figure['id']}.png"
    assert saved_path.read_bytes() == response.content
    assert client.get(f"/api/papers/{paper['id']}").json()["figures"] == paper["figures"]
    assert db.get_paper(paper["id"])["figures"] == paper["figures"]


def test_figure_metadata_contains_only_public_ids_dimensions_caption_and_local_url(client):
    paper = import_pdf(client)
    assert paper["figures"]
    for figure in paper["figures"]:
        assert {"id", "page", "after_paragraph_id", "url", "caption", "width", "height",
                "bbox", "page_width", "page_height"} <= set(figure)
        assert figure["url"].startswith(f"/api/papers/{paper['id']}/figures/")
        assert "\\" not in figure["url"]
    serialized = json.dumps(paper, ensure_ascii=False)
    assert str(db.DATA) not in serialized
    assert str(db.ROOT) not in serialized
    assert "file://" not in serialized
    assert "protected_key" not in serialized
    assert "api_key" not in serialized


def test_deleting_paper_removes_original_and_every_extracted_figure(client):
    paper = import_pdf(client)
    directory = api.figure_dir(paper["id"])
    images = list(directory.glob("f-*.png"))
    assert images
    source = db.DATA / "papers" / f"{paper['id']}.pdf"
    assert source.is_file()
    response = client.delete(f"/api/papers/{paper['id']}")
    assert response.status_code == 200
    assert db.get_paper(paper["id"]) is None
    assert not source.exists()
    assert all(not image.exists() for image in images)
    assert not directory.exists()
    assert not directory.parent.exists()
    assert client.get(paper["figures"][0]["url"]).status_code == 404


def test_unknown_figure_and_missing_image_file_return_404_without_paths(client):
    paper = import_pdf(client)
    missing = client.get(f"/api/papers/{paper['id']}/figures/f-99999.png")
    assert missing.status_code == 404
    assert client.get("/api/papers/missing-paper/figures/f-1.png").status_code == 404
    figure = paper["figures"][0]
    (api.figure_dir(paper["id"]) / f"{figure['id']}.png").unlink()
    missing_file = client.get(figure["url"])
    assert missing_file.status_code == 404
    assert str(db.DATA) not in missing_file.text
    assert "原始 PDF" in missing_file.json()["detail"]


def test_old_saved_paper_generates_and_persists_figures_on_first_open_only(client, monkeypatch):
    raw = illustrated_pdf()
    parsed = extract_pdf(raw, "legacy.pdf")
    paper = db.refresh_progress(dict(parsed, id="legacy-paper", filename="legacy.pdf", created_at="2026-01-01T00:00:00+00:00", status="idle", error=None, is_demo=False))
    assert "figures" not in paper
    db.save_paper(paper)
    (db.DATA / "papers" / "legacy-paper.pdf").write_bytes(raw)
    calls = []

    def extract_once(*args, **kwargs):
        calls.append(True)
        return extract_figures(*args, **kwargs)

    monkeypatch.setattr(api, "extract_figures", extract_once)
    first = client.get("/api/papers/legacy-paper")
    assert first.status_code == 200
    figures = wait_for_figures(client, "legacy-paper")["figures"]
    assert figures and calls == [True]
    assert db.get_paper("legacy-paper")["figures"] == figures
    assert client.get(figures[0]["url"]).status_code == 200
    assert client.get("/api/papers/legacy-paper").json()["figures"] == figures
    assert calls == [True]


def test_text_only_document_has_empty_figure_list(client):
    paper = import_pdf(client, kind=None)
    assert paper["paragraphs"]
    assert paper["figures"] == []
    assert "figures_error" not in paper


def test_figure_extraction_failure_keeps_text_and_sanitizes_diagnostics(client, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError(f"private diagnostic path: {db.DATA}")

    monkeypatch.setattr(api, "extract_figures", fail)
    paper = import_pdf(client)
    assert paper["paragraphs"]
    assert paper["figures"] == []
    assert "原 PDF" in paper["figures_error"]
    assert str(db.DATA) not in json.dumps(paper)
    assert "private diagnostic" not in json.dumps(paper)
    assert client.get(f"/api/papers/{paper['id']}/original").status_code == 200


def test_pdf_api_export_has_readable_chinese_bilingual_order_and_original_figures(client):
    paper = import_pdf(client)
    source = "For an integer $k$, the next integer is $k+1$."
    translated = "对于整数 $k$，下一个整数为 $k+1$。"
    paragraph = paper["paragraphs"][1]
    paragraph.update(text=source, translation=translated)
    db.save_paper(db.refresh_progress(paper))
    response = client.get(f"/api/papers/{paper['id']}/export?format=pdf")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert "attachment;" in response.headers["content-disposition"]
    assert response.content.startswith(b"%PDF-")
    with fitz.open(stream=response.content, filetype="pdf") as document:
        assert document.page_count >= 1
        text = "\n".join(page.get_text() for page in document)
        compact = "".join(text.split())
        source_compact = "".join(source.split())
        translated_compact = "".join(translated.split())
        assert source_compact in compact
        assert translated_compact in compact
        assert compact.index(source_compact) < compact.index(translated_compact)
        assert "$k+1$" in text  # PDF keeps TeX source; browser/HTML renders it.
        assert sum(len(page.get_images()) for page in document) >= 1
        assert "Figure1:" in compact
        assert any("中文" in page.get_text() for page in document) or "下一个整数" in compact
