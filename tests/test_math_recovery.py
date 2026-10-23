"""Geometry-based math recovery; synthetic spans, no external paper fixtures."""

from types import SimpleNamespace

import pymupdf as fitz

from backend.math_recovery import keep_span_characters, recover_expression, recover_inline, recover_paragraph
from backend.pdf_parser import _Line, _assemble_page, _read_lines, extract_pdf


def span(text, x, y=100, size=10, font="CMR10"):
    chars = []
    for char in text:
        width = size * .55
        chars.append({"c": char, "origin": (x, y), "bbox": (x, y - size * .8, x + width, y + size * .2)})
        x += width
    return {"text": text, "font": font, "size": size, "origin": chars[0]["origin"],
            "bbox": (chars[0]["bbox"][0], y - size * .8, x, y + size * .2), "chars": chars}


def test_quantized_weight_recovers_script_and_keeps_explicit_equation_number():
    spans = [span("W", 20, font="CMMIB10"), span("q", 25.5, y=101.5, size=7, font="CMMI7"),
             span("=q(", 33), span("W", 49.5, font="CMMIB10"), span(")", 55), span("(2)", 200, font="Helvetica")]
    assert recover_expression(spans, numbered=True) == r"\mathbf{W}_{q}=q(\mathbf{W})\tag{2}"


def test_matrix_factors_keep_independent_subscripts():
    spans = [span("y=x(W+", 10), span("A", 43, font="CMMIB10"),
             span("k", 48.5, y=101.5, size=7, font="CMMI7"),
             span("B", 54, font="CMMIB10"), span("k", 59.5, y=101.5, size=7, font="CMMI7"),
             span(")", 65), span("(1)", 200, font="Helvetica")]
    assert recover_expression(spans, numbered=True) == r"y=x(W+\mathbf{A}_{k}\mathbf{B}_{k})\tag{1}"


def test_long_subscript_stays_below_its_base_even_when_it_has_more_characters():
    spans = [span("A", 10, font="CMMI10"), span("i+1", 15.5, y=102, size=7, font="CMMI7")]
    assert recover_expression(spans) == "A_{i+1}"


def test_unicode_greek_and_known_relations_become_valid_tex_inside_prose():
    spans = [span("For ", 10, font="Helvetica"), span("α", 32, font="CMMI10"),
             span("≤", 37.5, font="CMSY10"), span("β", 43, font="CMMI10"),
             span(", the bound holds.", 48.5, font="Helvetica")]
    text, changed = recover_inline(spans, "For α≤β, the bound holds.")
    assert changed
    assert text == r"For \(\alpha \le \beta\), the bound holds."


def test_superscript_and_subscript_on_one_variable_keep_their_sides():
    spans = [span("x", 10, font="CMMI10"), span("2", 15.5, y=96, size=7),
             span("i", 15.5, y=102, size=7, font="CMMI7")]
    assert recover_expression(spans) == "x_{i}^{2}"


def test_blackboard_font_retains_expectation_symbol():
    spans = [span("E", 10, font="MSBM10"), span("(x)", 15.5)]
    assert recover_expression(spans) == r"\mathbb{E}(x)"


def test_stacked_fraction_is_not_linearized_into_a_false_formula():
    spans = [span("y=", 10), span("a", 25, y=94, size=7, font="CMMI7"),
             span("b", 25, y=105, size=7, font="CMMI7"), span("(3)", 200, font="Helvetica")]
    assert recover_expression(spans, numbered=True) is None


def test_ambiguous_extension_font_has_source_image_and_original_text():
    spans = [span("W=", 10, font="CMMIB10"), span("f", 24, y=97.5, font="CMEX10"),
             span("W", 23, font="CMMIB10"), span("(3)", 200, font="Helvetica")]
    paragraph = {"text": "W = f W (3)", "kind": "equation", "page": 3, "bbox": [10, 80, 220, 110]}
    result = recover_paragraph(paragraph, [SimpleNamespace(spans=spans)])
    assert result["text"] == result["source_text"] == "W = f W (3)"
    assert result["math_status"] == "source-image"
    assert result["equation_source"] == {"page": 3, "bbox": [10, 80, 220, 110]}
    assert result["equation_image"] == {"width": 210, "height": 30, "latex_available": False}
    assert not result["text"].startswith("$")


def test_overprinted_or_combining_unknown_symbol_falls_back_instead_of_losing_negation():
    assert recover_expression([span("i̸=j", 10)], numbered=True) is None


def test_fragmented_equation_tail_does_not_appear_as_successful_latex():
    assert recover_expression([span("j", 10, font="CMMI10"), span("(25)", 200)], numbered=True) is None
    assert recover_expression([span("y=", 10), span("(1)", 200)], numbered=True) is None


def test_ordinary_prose_and_damaged_math_are_not_covered_by_math_delimiters():
    original = "The loss function remains unchanged."
    assert recover_inline([span(original, 10, font="CMR10")], original) == (original, False)
    original = "W f W"
    assert recover_inline([span("W", 10, font="CMMI10"), span("f", 16, font="CMEX10"),
                           span("W", 22, font="CMMI10")], original) == (original, False)


def test_prose_character_geometry_is_discarded_and_partial_expressions_are_never_recovered():
    prose = span("An ordinary explanatory sentence is not a mathematical expression.", 10, font="Helvetica")
    assert not keep_span_characters(prose)
    variable = span("x", 10, font="CMMI10")
    assert keep_span_characters(variable)
    missing = span("y=", 30)
    missing.pop("chars")
    assert recover_expression([variable, missing]) is None
    with fitz.open() as document:
        page = document.new_page()
        page.insert_text((60, 100), "An ordinary explanatory sentence describes this synthetic experiment.", fontsize=11)
        lines = _read_lines(page, 1)
    assert all("chars" not in item for line in lines for item in line.spans)


def test_parser_end_to_end_stores_source_coordinates_and_typesets_simple_numbered_math():
    with fitz.open() as document:
        page = document.new_page(width=600, height=800)
        page.insert_text((80, 80), "A text paragraph introduces the following equation.", fontsize=11)
        page.insert_text((240, 120), "W", fontname="tiit", fontsize=11)
        page.insert_text((250, 122), "q", fontname="tiit", fontsize=7)
        page.insert_text((260, 120), "= q(W)", fontname="tiit", fontsize=11)
        page.insert_text((505, 120), "(2)", fontsize=11)
        page.insert_text((80, 160), "A second paragraph discusses the quantization function.", fontsize=11)
        parsed = extract_pdf(document.tobytes(), "synthetic-math.pdf")
    equation = next(paragraph for paragraph in parsed["paragraphs"] if paragraph["kind"] == "equation")
    assert equation["text"] == r"$$W_{q}=q(W)\tag{2}$$"
    assert equation["math_status"] == "latex"
    assert equation["source_text"].endswith("(2)")
    assert equation["equation_source"] == {"page": 1, "bbox": equation["bbox"]}
    assert not any("spans" in paragraph for paragraph in parsed["paragraphs"])


def test_complex_equation_crop_includes_an_adjacent_fragment_without_relabeling_its_text():
    left = _Line("R = diag(", 80, 120, 180, 142, 10, False, 1, 1, 600, 800,
                 baseline=130, math_ratio=.7, spans=[span("R=diag(", 80, y=130)])
    right = _Line("E{x} (3)", 300, 120, 530, 142, 10, False, 2, 1, 600, 800,
                  baseline=130, math_ratio=.7, equation=True,
                  spans=[span("E{x}", 300, y=130), span("(3)", 510, y=130)])
    paragraphs = _assemble_page([left, right], 10)
    equation = next(paragraph for paragraph in paragraphs if paragraph["kind"] == "equation")
    assert equation["math_status"] == "source-image"
    assert equation["bbox"][0] == 300
    assert equation["equation_source"]["bbox"][0] == 80
    assert equation["text"] == equation["source_text"] == "E{x} (3)"
