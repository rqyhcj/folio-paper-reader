"""Original pages and complex formula crops retain the PDF's actual pixels."""
import importlib
from pathlib import Path

from fastapi.testclient import TestClient
import pymupdf as fitz
import pytest

from backend import storage as db
from backend.pdf_runtime import PDF_LOCK

api = importlib.import_module("backend.app")
previews = importlib.import_module("backend.page_view")


@pytest.fixture
def source_client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATA", tmp_path / "preview-data")
    with TestClient(api.app) as client:
        with PDF_LOCK, fitz.open() as document:
            page = document.new_page(width=600, height=800)
            page.insert_text((65, 90), "Original page layout", fontsize=20)
            page.draw_rect(fitz.Rect(70, 160, 400, 300), color=(.2, .3, .5), fill=(.9, .8, .6))
            page.insert_text((90, 210), "x + y = z", fontsize=18)
            raw = document.tobytes()
        paper = db.refresh_progress({
            "id": "preview-paper", "title": "Original layout", "filename": "original.pdf",
            "created_at": "2026-10-24T00:00:00+00:00", "page_count": 1,
            "status": "idle", "is_demo": False, "figures": [],
            "figure_status": "completed", "figure_version": api.FIGURE_VERSION,
            "outline": [], "paragraphs": [{"id": "p1", "kind": "equation", "page": 1,
                "text": "x + y = z", "translation": None, "math_status": "source-image",
                "equation_source": {"page": 1, "bbox": [85, 185, 300, 220]}}],
        })
        db.save_paper(paper)
        (db.DATA / "papers" / "preview-paper.pdf").write_bytes(raw)
        yield client, raw


def test_original_page_is_the_exact_pdf_render_and_is_cached(source_client, monkeypatch):
    client, raw = source_client
    response = client.get("/api/papers/preview-paper/pages/1.png")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    with PDF_LOCK, fitz.open(stream=raw, filetype="pdf") as document:
        expected = document[0].get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False).tobytes("png")
    assert response.content == expected
    monkeypatch.setattr(previews.fitz, "open", lambda *a, **kw: pytest.fail("Cached page should not reopen PDF"))
    assert client.get("/api/papers/preview-paper/pages/1.png").content == expected


@pytest.mark.parametrize("path", ["pages/0.png", "pages/2.png", "equations/missing.png"])
def test_missing_pages_and_formulas_return_safe_404(source_client, path):
    client, _ = source_client
    response = client.get(f"/api/papers/preview-paper/{path}")
    assert response.status_code == 404
    assert str(db.DATA) not in response.text


def test_equation_crop_and_pdf_export_use_original_image_and_delete_cleans_cache(source_client):
    client, _ = source_client
    crop = client.get("/api/papers/preview-paper/equations/p1.png")
    assert crop.status_code == 200
    pixmap = fitz.Pixmap(crop.content)
    assert pixmap.width < 600 and pixmap.height < 110
    exported = client.get("/api/papers/preview-paper/export?format=pdf")
    assert exported.status_code == 200
    with PDF_LOCK, fitz.open(stream=exported.content, filetype="pdf") as document:
        assert sum(len(page.get_image_info()) for page in document) == 1
    exported_html = client.get("/api/papers/preview-paper/export?format=html")
    assert "data:image/png;base64," in exported_html.text
    assert client.get("/api/papers/preview-paper/pages/1.png").status_code == 200
    assert client.delete("/api/papers/preview-paper").status_code == 200
    assert not (db.DATA / "papers" / "preview-paper").exists()
    assert client.get("/api/papers/preview-paper/pages/1.png").status_code == 404
