"""Geometry-based math recovery; synthetic spans, no external paper fixtures."""

from types import SimpleNamespace

import pymupdf as fitz

from backend.math_recovery import keep_span_characters, recover_expression, recover_inline, recover_paragraph
from backend.pdf_parser import _Line, _assemble_page, _attach_extension_fragments, _read_lines, extract_pdf


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


def test_complex_equation_fragments_become_one_complete_crop_without_duplicate_text():
    left = _Line("R = diag(", 80, 120, 180, 142, 10, False, 1, 1, 600, 800,
                 baseline=130, math_ratio=.7, spans=[span("R=diag(", 80, y=130)])
    right = _Line("E{x} (3)", 300, 120, 530, 142, 10, False, 2, 1, 600, 800,
                  baseline=130, math_ratio=.7, equation=True,
                  spans=[span("E{x}", 300, y=130), span("(3)", 510, y=130)])
    paragraphs = _assemble_page([left, right], 10)
    equation = next(paragraph for paragraph in paragraphs if paragraph["kind"] == "equation")
    assert equation["math_status"] == "source-image"
    assert len(paragraphs) == 1
    assert equation["bbox"][0] == 80
    assert equation["equation_source"]["bbox"][0] == 80
    assert equation["text"] == equation["source_text"] == "R = diag( E{x} (3)"


def math_line(text, box, *, baseline=None, font="CMMI9", block=1, equation=False):
    x0, y0, x1, y1 = box
    baseline = baseline if baseline is not None else y1 - 2
    spans = [span(text, x0, y=baseline, size=9, font=font)]
    return _Line(text, x0, y0, x1, y1, 9, False, block, 6, 612, 792,
                 baseline=baseline, math_ratio=1 if "CM" in font else 0,
                 equation=equation, spans=spans)


def test_multiline_display_joins_brackets_controls_and_a_number_on_its_own_row():
    # Geometry matches the two-row display (15) in a typical TeX two-column
    # paper; its extension brackets decode as NUL / 01, not as usable text.
    lines = [math_line("p'(x)=q(x)", (66, 489, 197, 501)),
             math_line("=N(x;u,v)", (121, 504, 271, 517), block=2),
             math_line("\x00", (139, 505, 144, 515), font="CMEX9", block=3),
             math_line("\x01", (271, 505, 276, 515), font="CMEX9", block=4),
             math_line("(15)", (275, 518, 290, 527), font="Helvetica", block=5),
             math_line("where", (55, 538, 80, 549), font="Helvetica", block=6)]
    result = _assemble_page(lines, 10)
    assert len(result) == 2
    equation, prose = result
    assert equation["kind"] == "equation" and equation["math_status"] == "source-image"
    assert equation["bbox"] == [66, 489, 290, 527]
    assert equation["equation_source"]["bbox"] == equation["bbox"]
    assert "(15)" in equation["source_text"]
    assert prose["kind"] == "paragraph" and prose["text"] == "where"


def test_radical_and_fraction_rows_form_a_complete_equation_not_letter_translation_fragments():
    lines = [math_line("s", (159, 558, 169, 567), baseline=559, font="CMEX9"),
             math_line("C1=", (99, 562, 157, 579), baseline=577),
             math_line("(1-a)b", (170, 562, 242, 574), baseline=570),
             math_line(",(16)", (243, 569, 290, 578), baseline=576, equation=True),
             math_line("1-a", (193, 575, 218, 584), baseline=582)]
    result = _assemble_page(lines, 10)
    assert len(result) == 1
    assert result[0]["kind"] == "equation"
    assert result[0]["math_status"] == "source-image"
    assert result[0]["bbox"] == [99, 558, 290, 584]


def test_adjacent_displays_do_not_take_each_others_fraction_rows():
    lines = [math_line("x=1", (100, 110, 140, 120)),
             math_line("(1)", (275, 110, 290, 120), font="Helvetica"),
             math_line("y=", (100, 125, 140, 137), block=2),
             math_line("a", (145, 125, 152, 137), block=3),
             math_line("b", (145, 138, 152, 147), block=4),
             math_line("(2)", (275, 131, 290, 143), font="Helvetica", block=5)]
    result = _assemble_page(lines, 10)
    equations = [item for item in result if item["kind"] == "equation"]
    assert len(equations) == 2
    assert equations[0]["bbox"] == [100, 110, 290, 120]
    assert equations[1]["bbox"] == [100, 125, 290, 147]


def test_prose_is_a_hard_boundary_even_when_short_and_between_math_fragments():
    lines = [math_line("x=1", (100, 100, 140, 110)),
             math_line("where", (55, 111, 80, 121), font="Helvetica"),
             math_line("(1)", (275, 122, 290, 132), font="Helvetica")]
    result = _assemble_page(lines, 10)
    equation = next(item for item in result if item["kind"] == "equation")
    assert equation["bbox"] == [100, 100, 140, 110]
    assert "(1)" not in equation["source_text"]
    assert any("where" in item["text"] for item in result if item["kind"] == "paragraph")


def test_parenthesized_chart_label_alone_does_not_become_a_formula():
    lines = [math_line("(1)", (275, 122, 290, 132), font="Helvetica")]
    result = _assemble_page(lines, 10)
    assert result[0]["kind"] == "paragraph"


def test_ambiguous_inline_radical_uses_original_prose_crop_and_keeps_translation_eligibility():
    spans = [span("A scaling factor ", 10, font="Helvetica"), span("q", 95, font="CMEX10"),
             span("1+x", 101)]
    paragraph = {"text": "A scaling factor q1+x", "kind": "paragraph", "page": 6, "bbox": [10, 80, 160, 112]}
    result = recover_paragraph(paragraph, [SimpleNamespace(spans=spans)])
    assert result["kind"] == "paragraph"
    assert result["ambiguous_inline_math"] is True
    assert result["math_status"] == "source-image"
    assert result["equation_source"] == {"page": 6, "bbox": [10, 80, 160, 112]}


def test_plain_fraction_numerator_is_math_context_not_a_prose_separator():
    lines = [math_line("\x12", (380, 637, 387, 646), baseline=638, font="CMEX9"),
             math_line("\x13", (475, 637, 482, 646), baseline=638, font="CMEX9"),
             math_line("1", (367, 637, 372, 646), font="Helvetica"),
             math_line("x=", (328, 643, 377, 659)),
             math_line("a-b", (387, 643, 442, 660)),
             math_line("(10)", (527, 656, 542, 665), font="Helvetica")]
    result = _assemble_page(lines, 10)
    assert len(result) == 1
    assert result[0]["kind"] == "equation"
    assert result[0]["math_status"] == "source-image"
    assert result[0]["bbox"] == [328, 637, 542, 665]


def test_unnumbered_display_keeps_extension_brackets_in_one_source_crop():
    lines = [math_line("p(x)=N(x;u,v)", (97, 362, 259, 374)),
             math_line("\x00", (159, 364, 164, 371), font="CMEX7"),
             math_line("\x01", (236, 364, 240, 371), font="CMEX7")]
    result = _assemble_page(lines, 10)
    assert len(result) == 1 and result[0]["kind"] == "equation"
    assert result[0]["math_status"] == "source-image"
    assert result[0]["bbox"] == [97, 362, 259, 374]


def test_second_pass_attaches_a_bracket_just_beyond_the_assembled_prose_row():
    body = math_line("NewtonSolve(x)=a/b", (75, 309, 240, 327), baseline=319, font="Helvetica")
    bracket = math_line("\x11", (241, 308, 248, 318), baseline=308, font="CMEX10")
    result = _attach_extension_fragments([body, bracket])
    assert len(result) == 1
    assert result[0].x1 == 248 and result[0].y0 == 308
    assert any(span["text"] == "\x11" for span in result[0].spans)


def test_mathematics_in_adjacent_columns_remains_two_separate_equations():
    lines = []
    for y in (70, 85, 190, 205):
        lines.append(_Line("The left column describes this experiment.", 55, y, 291, y + 10,
                           10, False, int(y), 1, 612, 792))
        lines.append(_Line("The right column describes this experiment.", 307, y, 543, y + 10,
                           10, False, int(y) + 1, 1, 612, 792))
    lines.extend([math_line("x=1", (100, 120, 140, 130)),
                  math_line("(1)", (275, 120, 290, 130), font="Helvetica"),
                  math_line("y=2", (350, 120, 390, 130)),
                  math_line("(2)", (528, 120, 543, 130), font="Helvetica")])
    result = _assemble_page(lines, 10)
    equations = [item for item in result if item["kind"] == "equation"]
    assert len(equations) == 2
    assert equations[0]["bbox"] == [100, 120, 290, 130]
    assert equations[1]["bbox"] == [350, 120, 543, 130]


def test_extension_attachment_does_not_pull_a_right_column_glyph_into_left_prose():
    lines = []
    for y in (70, 85, 190, 205):
        lines.append(_Line("The left column describes this experiment.", 55, y, 296, y + 10,
                           10, False, int(y), 1, 612, 792))
        lines.append(_Line("The right column describes this experiment.", 304, y, 543, y + 10,
                           10, False, int(y) + 1, 1, 612, 792))
    left = math_line("The left sentence ends here.", (55, 120, 296, 132), baseline=130, font="Helvetica")
    glyph = math_line("q", (301, 120, 307, 132), baseline=130, font="CMEX10")
    lines.extend([left, glyph])
    result = _attach_extension_fragments(lines)
    assert glyph in result
    assert len(left.spans) == 1
