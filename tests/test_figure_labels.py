"""Bold figure labels do not become duplicate paragraphs or navigation sections."""

import pymupdf as fitz

from backend.figures import _anchors, _complete_figure_regions, _image_label, _regions, extract_figures
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


def test_short_axis_ticks_just_outside_the_vector_border_are_still_figure_labels():
    rect = (133, 70, 502, 160)
    assert _image_label(_paragraph("tick", "200", kind="paragraph", bbox=(119, 104, 128, 112)), rect)
    assert _image_label(_paragraph("panel", "(a) Latency Breakdown", kind="paragraph", bbox=(121, 147, 245, 156)), rect)
    assert not _image_label(_paragraph("caption", "Figure 6: Complete caption", kind="paragraph", bbox=(108, 164, 505, 188)), rect)


def test_adjacent_panels_with_one_caption_become_one_complete_label_region():
    caption = ((80, 180, 520, 205), "Figure 6: Two panels of one method.")
    regions = _complete_figure_regions([(90, 70, 230, 145), (290, 72, 510, 142)], [caption], (0, 0, 600, 800))
    assert regions == [(90, 70, 510, 177)]
    assert _image_label(_paragraph("label", "Naïve", kind="paragraph", bbox=(150, 150, 190, 160)), regions[0])


def test_two_caption_bands_split_a_page_wide_drawing_cluster_into_two_figures():
    snapshot = {
        "bounds": (0, 0, 600, 800),
        "images": [(110, 80, 205, 170), (210, 80, 305, 170),
                   (310, 80, 405, 170), (410, 80, 505, 170)],
        # The first path mimics a bogus page-wide cluster. Five lower paths are
        # panels of a second chart with its own caption.
        "drawings": [(24, 60, 560, 650), (110, 430, 180, 475), (195, 430, 265, 475),
                     (280, 430, 350, 475), (365, 430, 435, 475), (440, 430, 510, 475)],
        "captions": [((108, 360, 505, 405), "Figure 7: visual examples"),
                     ((108, 500, 505, 540), "Figure 8: efficiency")],
    }
    found = _regions(snapshot)
    assert len(found) == 2
    assert found[0][3] < 360
    assert found[1][1] > 405 and found[1][3] < 500


def test_figure_anchor_uses_its_numbered_caption_not_a_short_label_mentioned_inside_it():
    rect = (108, 70, 504, 358)
    paragraphs = [
        _paragraph("label", "NF4 W4A16", kind="paragraph", bbox=(170, 68, 200, 74)),
        _paragraph("caption", "Figure 7: NF4 W4A16 qualitative visual results on MJHQ.",
                   kind="paragraph", bbox=(108, 361, 505, 405)),
        _paragraph("next", "The following body paragraph.", kind="paragraph", bbox=(108, 420, 505, 435)),
    ]
    assert _anchors(rect, paragraphs, "Figure 7: NF4 W4A16 qualitative visual results on MJHQ.") == (
        "label", "caption")


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
