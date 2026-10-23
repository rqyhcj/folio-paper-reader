from copy import deepcopy
from pathlib import Path

import fitz

from backend.pdf_export import BODY_BOTTOM, BODY_TOP, MARGIN, export_pdf


def manuscript():
    return {
        "title": "A readable bilingual paper 双语论文",
        "paragraphs": [
            {"id": "p1", "kind": "heading", "level": 1, "text": "1 Introduction", "translation": "1 引言"},
            {"id": "p2", "kind": "paragraph", "text": "The English evidence remains close to its translation.",
             "translation": "中文译文紧邻英文原文，保留术语、公式和引用。"},
            {"id": "p3", "kind": "paragraph", "text": "This paragraph is still pending.", "translation": None},
        ],
    }


def all_text(document):
    return "\n".join(page.get_text() for page in document)


def test_bilingual_pdf_round_trip_embeds_fonts_and_preserves_text_order():
    paper = manuscript()
    before = deepcopy(paper)
    raw = export_pdf(paper)
    assert raw.startswith(b"%PDF-")
    with fitz.open(stream=raw, filetype="pdf") as document:
        text = all_text(document)
        assert "双语论文" in text
        assert "中文译文紧邻英文原文，保留术语、公式和引用。" in text
        assert "[尚未翻译]" in text
        assert text.index(paper["paragraphs"][1]["text"]) < text.index(paper["paragraphs"][1]["translation"])
        assert document.get_toc()[0][1] == "1 Introduction"
        assert "1 / 1" in text
        assert document.metadata["title"] == paper["title"]
        embedded = [document.extract_font(font[0])[3] for font in document[0].get_fonts()]
        assert embedded and all(embedded)
    assert paper == before


def test_long_paragraphs_and_unbroken_words_flow_across_pages_without_clipping():
    paper = manuscript()
    source = "StartOfEnglish " + "Research findings deserve careful interpretation. " * 420 + " EndOfEnglish"
    translated = "中文开头" + "长段落跨页时仍然应当完整显示，不能丢失文字。" * 320 + "中文结尾"
    token = "W" * 1600
    paper["paragraphs"] = [
        {"id": "long", "text": source, "translation": translated},
        {"id": "token", "text": token, "translation": "超长单词之后的中文"},
    ]
    with fitz.open(stream=export_pdf(paper), filetype="pdf") as document:
        text = all_text(document)
        assert document.page_count > 10
        assert "StartOfEnglish" in text and "EndOfEnglish" in text
        assert "中文开头" in text and "中文结尾" in text
        assert "超长单词之后的中文" in text
        assert text.count("W") == len(token)
        assert text.count("Research findings") == 420
        # Text extraction inserts line/page breaks inside CJK sentences.
        assert text.count("跨") == 320
        assert text.count("丢") == 320
        for page in document:
            for block in page.get_text("dict")["blocks"]:
                if block["type"] != 0:
                    continue
                x0, y0, x1, y1 = block["bbox"]
                assert x0 >= MARGIN - 1
                assert x1 <= page.rect.width - MARGIN + 1
                assert y0 >= 20
                assert y1 <= page.rect.height - 20
            assert f"{page.number + 1} / {document.page_count}" in page.get_text()


def test_images_are_embedded_scaled_and_anchored_after_the_right_paragraph(tmp_path: Path):
    image = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 300, 1800), False)
    image.clear_with(190)
    path = tmp_path / "original-figure.png"
    image.save(str(path))
    paper = manuscript()
    paper["figures"] = [{"id": "figure-1", "page": 1, "after_paragraph_id": "p2",
                         "caption": "Figure 1. 原文插图和图注", "width": 1, "height": 1,
                         "url": "https://never-fetch.invalid/image.png"}]
    with fitz.open(stream=export_pdf(paper, {"figure-1": path}), filetype="pdf") as document:
        text = all_text(document)
        assert text.index("中文译文紧邻英文原文") < text.index("Figure 1.") < text.index("This paragraph is still pending.")
        images = [(page, info) for page in document for info in page.get_image_info()]
        assert len(images) == 1
        page, info = images[0]
        rect = fitz.Rect(info["bbox"])
        assert rect.x0 >= MARGIN and rect.x1 <= page.rect.width - MARGIN
        assert rect.y0 >= BODY_TOP and rect.y1 <= BODY_BOTTOM
        assert abs(rect.height / rect.width - 6) < 0.01
        assert "原文插图和图注" in page.get_text()


def test_pdf_treats_html_like_content_as_literal_text():
    paper = manuscript()
    paper["title"] = "<script>never execute</script> 中文"
    paper["paragraphs"] = [{"id": "html", "text": "<img src='file:///private/key'>",
                             "translation": "<a href='https://evil.invalid'>中文</a>"}]
    with fitz.open(stream=export_pdf(paper), filetype="pdf") as document:
        text = all_text(document)
        assert "<script>never execute</script>" in text
        assert "<img src='file:///private/key'>" in text
        assert "<a href='https://evil.invalid'>中文</a>" in text
        assert not any(page.get_images() or page.get_links() for page in document)


def test_figure_precedes_its_source_caption_when_a_before_anchor_exists(tmp_path):
    image = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 450, 55), False)
    image.clear_with(190)
    path = tmp_path / "chart.png"
    image.save(str(path))
    paper = manuscript()
    paper["paragraphs"][1]["text"] = "Figure 1. A source caption below the chart."
    paper["figures"] = [{"id": "f1", "page": 1, "before_paragraph_id": "p2",
                         "after_paragraph_id": "p3", "caption": "Original chart"}]
    with fitz.open(stream=export_pdf(paper, {"f1": path}), filetype="pdf") as document:
        images = [(page, info) for page in document for info in page.get_image_info()]
        assert len(images) == 1
        page, info = images[0]
        caption = page.search_for("Figure 1.")
        assert caption and info["bbox"][3] < caption[0].y0


def test_missing_images_are_reported_and_stale_anchors_do_not_drop_figures(tmp_path: Path):
    paper = manuscript()
    paper["figures"] = [{"id": "missing", "page": 2, "after_paragraph_id": "stale-id", "caption": "Missing original chart"}]
    with fitz.open(stream=export_pdf(paper, {"missing": tmp_path / "absent.png"}), filetype="pdf") as document:
        assert "[插图未能导出] Missing original chart" in all_text(document)
