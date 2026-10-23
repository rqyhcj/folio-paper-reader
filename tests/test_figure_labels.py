"""Bold figure labels do not become duplicate paragraphs or navigation sections."""

import pymupdf as fitz

from backend.figures import _image_label, extract_figures
from backend.pdf_export import export_pdf


def _paragraph(pid, text, kind="heading", bbox=(100, 250, 300, 265)):
    return {"id": pid, "text": text, "kind": kind, "page": 1, "bbox": list(bbox)}


def test_short_bold_labels_inside_artwork_are_safe_to_suppress():
    rect = (80, 220, 480, 420)
    for text in ("FP16", "After DNS (Ours)", "(FID: 22.78)", "FLUX.1-dev BF16", "Efficiency: 12.9s IR: 0.46"):
        assert _image_label(_paragraph("label", text), rect)


def test_numbered_conventional_and_outside_headings_are_protected():
    rect = (80, 220, 480, 420)
    for text in ("1 Introduction", "4.2. Sampling", "A. Appendix", "A.2. Extended Details", "Introduction", "Methods", "References", "摘要", "第3章 方法", "Figure 2. Results"):
        assert not _image_label(_paragraph("section", text), rect)
    assert not _image_label(_paragraph("outside", "FP16", bbox=(100, 200, 300, 215)), rect)
    assert not _image_label(_paragraph("partial", "FP16", bbox=(100, 210, 300, 225)), rect)
    assert not _image_label(_paragraph("long", "A long title " * 11), rect)


def test_ordinary_short_figure_labels_keep_existing_rule():
    assert _image_label(_paragraph("body", "x axis", kind="paragraph"), (80, 220, 480, 420))
    assert not _image_label(_paragraph("body", "x" * 301, kind="paragraph"), (80, 220, 480, 420))


def test_extraction_marks_bold_labels_but_preserves_source_and_real_sections(tmp_path):
    with fitz.open() as document:
        page = document.new_page(width=600, height=800)
        page.draw_rect(fitz.Rect(80, 220, 480, 420), color=(.2, .3, .4), width=1)
        page.draw_line((80, 220), (480, 420), color=(.1, .5, .7), width=3)
        raw = document.tobytes()
    paragraphs = [_paragraph("label", "After DNS (Ours)"),
                  _paragraph("section", "1 Introduction"),
                  _paragraph("caption", "Figure 1. A diagram", "paragraph", (80, 450, 480, 465))]
    paragraphs[0]["translation"] = "使用 DNS 后"
    before = repr(paragraphs)
    figures = extract_figures(raw, paragraphs, tmp_path, "synthetic")
    assert len(figures) == 1
    assert figures[0]["text_paragraph_ids"] == ["label"]
    assert repr(paragraphs) == before


def test_pdf_export_omits_duplicate_heading_text_and_bookmark(tmp_path):
    image = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 300, 120), False)
    image.clear_with(230)
    path = tmp_path / "art.png"
    image.save(path)
    paper = {"title": "A synthetic paper", "paragraphs": [
        {"id": "section", "kind": "heading", "level": 1, "text": "1 Introduction", "translation": "1 引言"},
        {"id": "label", "kind": "heading", "text": "After DNS (Ours)", "translation": "图中标签译文"},
        {"id": "body", "kind": "paragraph", "text": "The original prose remains readable.", "translation": "正文保留。"},
    ], "figures": [{"id": "f1", "before_paragraph_id": "label", "page": 1,
                     "caption": "Synthetic diagram", "text_paragraph_ids": ["label"]}]}
    with fitz.open(stream=export_pdf(paper, {"f1": path}), filetype="pdf") as exported:
        text = "".join(page.get_text() for page in exported)
        assert "After DNS" not in text and "图中标签译文" not in text
        assert "1 Introduction" in text and "original prose remains" in text
        assert len(exported.get_toc()) == 1
        assert sum(len(page.get_images()) for page in exported) == 1
