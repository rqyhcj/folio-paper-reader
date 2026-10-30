"""Protected translation formulas use local source ink in UI and exports."""
import importlib
from copy import deepcopy

from fastapi.testclient import TestClient
import pymupdf as fitz
import pytest

from backend import storage as db
from backend.inline_math_export import html_with_math, text_with_math
from backend.pdf_runtime import PDF_LOCK

api = importlib.import_module("backend.app")
previews = importlib.import_module("backend.page_view")


@pytest.fixture
def inline_client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATA", tmp_path / "inline-data")
    with TestClient(api.app) as client:
        with PDF_LOCK, fitz.open() as document:
            page = document.new_page(width=600, height=800)
            page.insert_text((100, 120), "x + y = z", fontsize=12)
            page.insert_text((240, 120), "Adjacent prose must stay outside", fontsize=12)
            page.insert_text((100, 145), "a / b", fontsize=12)
            raw = document.tobytes()
        paragraph = dict(id="p1", kind="paragraph", page=1, text="Unreliable source math",
                         translation='保留⟦FOLIO_MATH_1⟧，<img src=x onerror=alert(1)>与⟦FOLIO_MATH_2⟧。',
                         math_status="source-image", ambiguous_inline_math=True,
                         equation_source={"page": 1, "bbox": [90, 100, 440, 155]},
                         inline_math=[dict(id="m-1", placeholder="⟦FOLIO_MATH_1⟧", source_text="x + y = z; a / b", source=[
                             dict(page=1, bbox=[100, 105, 180, 125]),
                             dict(page=1, bbox=[100, 130, 140, 150])]),
                             dict(id="m-2", placeholder="⟦FOLIO_MATH_2⟧", latex=r"\alpha_t", source=[])])
        paper = db.refresh_progress(dict(id="inline-paper", title="Protected math", filename="inline.pdf",
                                         page_count=1, created_at="2026-10-24", status="paused", is_demo=False,
                                         figures=[], figure_status="completed", figure_version=api.FIGURE_VERSION,
                                         outline=[], paragraphs=[paragraph]))
        db.save_paper(paper)
        (db.DATA / "papers" / "inline-paper.pdf").write_bytes(raw)
        yield client, raw


def test_inline_formula_route_uses_tight_recorded_bbox_and_local_cache(inline_client, monkeypatch):
    client, raw = inline_client
    path = "/api/papers/inline-paper/inline-math/p1/m-1/0.png"
    response = client.get(path + "?bbox=0,0,600,800")
    assert response.status_code == 200 and response.headers["content-type"] == "image/png"
    with PDF_LOCK, fitz.open(stream=raw, filetype="pdf") as document:
        clip = fitz.Rect([100, 105, 180, 125]) + (-.4, -.6, .4, .6)
        expected = document[0].get_pixmap(matrix=fitz.Matrix(2, 2), clip=clip, alpha=False).tobytes("png")
    assert response.content == expected
    pixmap = fitz.Pixmap(response.content)
    assert pixmap.width < 165 and pixmap.height < 45
    assert response.headers["cache-control"] == "no-store"  # app-wide private API policy
    monkeypatch.setattr(previews.fitz, "open", lambda *a, **kw: pytest.fail("Cached formula must not reopen PDF"))
    assert client.get(path).content == expected


@pytest.mark.parametrize("tail", ["missing/m-1/0.png", "p1/missing/0.png", "p1/m-1/-1.png", "p1/m-1/2.png"])
def test_unknown_formula_piece_has_safe_404(inline_client, tail):
    client, _ = inline_client
    response = client.get("/api/papers/inline-paper/inline-math/" + tail)
    assert response.status_code == 404 and str(db.DATA) not in response.text


@pytest.mark.parametrize("region", [dict(page=0, bbox=[0, 0, 20, 20]),
                                    dict(page=2, bbox=[0, 0, 20, 20]),
                                    dict(page=1, bbox=[20, 20, 0, 0]),
                                    dict(page=1, bbox=["invalid", 1, 2, 3])])
def test_invalid_recorded_geometry_is_rejected(inline_client, region):
    client, _ = inline_client
    paper = db.get_paper("inline-paper")
    paper["paragraphs"][0]["inline_math"][0]["source"][0] = region
    db.save_paper(paper)
    response = client.get("/api/papers/inline-paper/inline-math/p1/m-1/0.png")
    assert response.status_code == 404 and str(db.DATA) not in response.text


def test_coordinates_change_formula_cache_after_reflow(inline_client):
    client, _ = inline_client
    paper = db.get_paper("inline-paper")
    first = previews.inline_math_png(paper, "p1", "m-1", 0)
    paper["paragraphs"][0]["inline_math"][0]["source"][0]["bbox"] = [100, 130, 140, 150]
    second = previews.inline_math_png(paper, "p1", "m-1", 0)
    assert first != second and first.read_bytes() != second.read_bytes()


def test_all_exports_replace_protected_tokens_and_keep_source_pieces(inline_client):
    client, _ = inline_client
    original = deepcopy(db.get_paper("inline-paper"))
    html = client.get("/api/papers/inline-paper/export?format=html")
    assert html.status_code == 200
    assert "FOLIO_MATH" not in html.text
    assert html.text.count("data:image/png;base64,") == 0
    assert "x + y = z; a / b" in html.text
    assert r"\(\alpha_t\)" in html.text
    assert "&lt;img src=x onerror=alert(1)&gt;" in html.text
    assert "<img src=x onerror=alert(1)>" not in html.text
    pdf = client.get("/api/papers/inline-paper/export?format=pdf")
    assert pdf.status_code == 200
    with PDF_LOCK, fitz.open(stream=pdf.content, filetype="pdf") as document:
        text = "".join(page.get_text() for page in document)
        assert "FOLIO_MATH" not in text and "x + y = z; a / b" in text
        assert "alpha_t" not in text, "LaTeX must be typeset, not printed as source"
        assert sum(len(page.get_drawings()) for page in document) >= 3
        assert sum(len(page.get_image_info()) for page in document) == 0
    markdown = client.get("/api/papers/inline-paper/export?format=markdown")
    assert "FOLIO_MATH" not in markdown.text
    assert "x + y = z; a / b" in markdown.text and r"\(\alpha_t\)" in markdown.text
    assert db.get_paper("inline-paper") == original, "Exports must not rewrite cached translations"


def test_plain_text_is_escaped_and_missing_formula_is_explicit():
    paragraph = {"id": "p1", "inline_math": [dict(id="m1", placeholder="⟦FOLIO_MATH_1⟧", source=[{}])]}
    value = '<script>alert(1)</script> ⟦FOLIO_MATH_1⟧ ⟦FOLIO_MATH_99⟧'
    html = html_with_math(paragraph, value, {})
    assert "FOLIO_MATH" not in html and "<script>" not in html
    assert "待识别公式" in html and "原式缺失" in html
    assert text_with_math({}, "Ordinary text stays unchanged.") == "Ordinary text stays unchanged."
