"""Regression cases for sentence fragments, equation numbers and PDF positions."""

import pymupdf as fitz

from backend.pdf_parser import PARSE_VERSION, _Line, _column_split, extract_pdf


def test_single_column_sentence_endings_and_math_do_not_create_a_gutter():
    # Single-column prose crosses the center; short sentence endings and
    # formula fragments happen to sit on each side of that center.
    lines = []
    for index in range(6):
        y = 90 + index * 35
        lines.append(_Line("A full-width sentence explains the experiment and its results.",
                           80, y, 520, y + 10, 10, False, index, 1, 600, 800))
        lines.append(_Line("The previous sentence ends here.",
                           80, y + 12, 255, y + 22, 10, False, index, 1, 600, 800))
        lines.append(_Line("An independent formula annotation.",
                           335, y + 12, 520, y + 22, 10, False, 20 + index, 1, 600, 800))
    assert _column_split(lines, 10) is None


def test_inline_math_fragments_rejoin_the_surrounding_wrapped_paragraph():
    with fitz.open() as document:
        page = document.new_page(width=600, height=800)
        page.insert_text((80, 100), "A paragraph explains how a ", fontsize=11)
        x = 80 + fitz.get_text_length("A paragraph explains how a ", fontsize=11)
        page.insert_text((x, 100), "variable", fontname="tiit", fontsize=11)
        x += fitz.get_text_length("variable", fontname="tiit", fontsize=11)
        page.insert_text((x, 100), " affects the experiment and its results.", fontsize=11)
        page.insert_text((80, 115), "The next line continues that same paragraph with an explanation.", fontsize=11)
        parsed = extract_pdf(document.tobytes(), "inline-math.pdf")
    paragraphs = parsed["paragraphs"]
    assert len(paragraphs) == 1
    assert "paragraph explains how a variable affects" in paragraphs[0]["text"]
    assert "The next line continues" in paragraphs[0]["text"]


def test_formula_and_its_right_aligned_number_form_one_block_between_prose():
    with fitz.open() as document:
        page = document.new_page(width=600, height=800)
        page.insert_text((80, 100), "The linear transformation is defined by the following equation.", fontsize=11)
        page.insert_text((240, 130), "y = x W", fontname="tiit", fontsize=11)
        page.insert_text((505, 130), "(2)", fontsize=11)
        page.insert_text((80, 165), "The following paragraph describes the transformation in detail.", fontsize=11)
        parsed = extract_pdf(document.tobytes(), "equation.pdf")
    paragraphs = parsed["paragraphs"]
    assert len(paragraphs) == 3
    assert paragraphs[1]["kind"] == "equation"
    assert paragraphs[1]["source_text"] == "y = x W (2)"
    assert paragraphs[1]["text"] == r"$$y=xW\tag{2}$$"
    assert not any(paragraph["text"] == "(2)" for paragraph in paragraphs)
    assert paragraphs[0]["bbox"][3] < paragraphs[1]["bbox"][1] < paragraphs[2]["bbox"][1]


def test_paragraph_positions_use_original_page_coordinates_and_enclose_wrapped_lines():
    with fitz.open() as document:
        page = document.new_page(width=600, height=800)
        page.insert_text((80, 120), "First line of an original paragraph.\nSecond line of the same paragraph.", fontsize=11)
        parsed = extract_pdf(document.tobytes(), "coordinates.pdf")
    paragraph = parsed["paragraphs"][0]
    assert parsed["parse_version"] == PARSE_VERSION
    assert paragraph["page_width"] == 600
    assert paragraph["page_height"] == 800
    x0, y0, x1, y1 = paragraph["bbox"]
    assert x0 == 80
    assert 100 < y0 < 120 < y1 < 160
    assert 200 < x1 < 500


def test_parenthetical_reference_at_sentence_end_is_not_an_equation():
    with fitz.open() as document:
        page = document.new_page(width=600, height=800)
        page.insert_text((80, 100), "The experiment follows an established evaluation protocol (2).", fontsize=11)
        parsed = extract_pdf(document.tobytes(), "reference.pdf")
    assert parsed["paragraphs"][0]["kind"] == "paragraph"


def test_bold_numeric_table_values_are_not_section_headings():
    from backend.pdf_parser import _Line, _heading_level
    for text in ("94.38", "89.83/89.72", "32", "(2)"):
        line = _Line(text, 60, 100, 150, 112, 14, True, 0, 1, 600, 800)
        assert _heading_level(line, 11) is None
