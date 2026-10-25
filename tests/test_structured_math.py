"""Synthetic layout evidence for structural PDF math; no paper fixtures."""

from types import SimpleNamespace

import pymupdf as fitz

from backend.math_recovery import annotate_page_math, recover_expression
from backend.pdf_parser import extract_pdf


def span(text, x, y=100, size=10, font="CMR10", *, name=None, width=None):
    chars = []
    for value in text:
        advance = width if width is not None else size * .55
        chars.append({"c": value, "origin": (x, y),
                      "bbox": (x, y - size * .8, x + advance, y + size * .2)})
        if name:
            chars[-1]["_glyph_name"] = name
        x += advance
    return {"text": text, "font": font, "size": size, "origin": chars[0]["origin"],
            "bbox": (chars[0]["bbox"][0], y - size * .8, x, y + size * .2), "chars": chars}


def rules(spans, *segments):
    shared = list(segments)
    for item in spans:
        item["_math_rules"] = shared
    return spans


def test_stacked_fraction_requires_an_explicit_vector_rule():
    spans = [span("y=", 10), span("a+b", 31, y=93, size=7, font="CMMI7"),
             span("c", 35, y=104, size=7, font="CMMI7")]
    assert recover_expression(spans) is None
    rules(spans, (30, 97.5, 44))
    assert recover_expression(spans) == r"y=\frac{a+b}{c}"


def test_nested_root_and_fraction_keep_radical_and_fraction_bars_distinct():
    spans = [span("y=", 10), span("√", 30, y=88, width=6),
             span("a", 36.5, y=93, size=7, font="CMMI7"),
             span("b", 35, y=104, size=7, font="CMMI7")]
    rules(spans, (36, 87, 41), (30, 97.5, 42))
    assert recover_expression(spans) == r"y=\frac{\sqrt{a}}{b}"


def test_root_without_an_overbar_is_not_a_guessed_radical():
    spans = [span("y=", 10), span("√", 30, y=96, width=6), span("a", 36, y=100)]
    assert recover_expression(spans) is None


def test_underbrace_uses_verified_glyph_names_and_preserves_following_punctuation():
    spans = [span("y=", 10), span("∥a∥", 31.5, y=93), span("∥b∥", 31.5, y=104),
             span("|", 30, y=117.5, font="CMEX10", name="bracehtipupleft", width=5),
             span("{", 35, y=117.5, font="CMEX10", name="bracehtipdownright", width=5),
             span("z", 40, y=117.5, font="CMEX10", name="bracehtipdownleft", width=5),
             span("}", 45, y=117.5, font="CMEX10", name="bracehtipupright", width=5),
             span("f(z)", 32.5, y=127, size=7, font="CMMI7"), span(",", 51)]
    rules(spans, (30, 97.5, 50))
    assert recover_expression(spans) == r"y=\underbrace{\frac{\Vert a\Vert}{\Vert b\Vert}}_{f(z)},"
    for item in spans:
        for char in item["chars"]:
            char.pop("_glyph_name", None)
    assert recover_expression(spans) is None


def test_verified_wide_accent_and_script_stay_on_their_own_adjacent_variables():
    spans = [span("b", 12.2, y=97.5, font="CMEX10", name="hatwide"),
             span("A", 10, font="CMMI10"), span("B", 16.5, font="CMMI10"),
             span("2", 22, y=96, size=7), span("=C", 28)]
    assert recover_expression(spans) == r"\widehat{A}B^{2}=C"
    spans[0]["chars"][0].pop("_glyph_name")
    assert recover_expression(spans) is None


def test_verified_scalable_delimiters_require_a_balanced_pair():
    spans = [span("a=", 10), span("\x00", 25, y=96, font="CMEX10", name="parenleftbig"),
             span("x", 31, font="CMMI10"),
             span("\x01", 37, y=96, font="CMEX10", name="parenrightbig")]
    assert recover_expression(spans) == "a=(x)"
    assert recover_expression(spans[:-1]) is None


def test_zero_width_negation_overlay_keeps_the_not_equal_relation():
    spans = [span("a", 10, font="CMMI10"), span("\u0338", 19, width=0), span("=0", 20)]
    assert recover_expression(spans) == r"a\ne 0"
    spans[1] = span("\u0338", 30, width=0)
    assert recover_expression(spans) is None


def test_unsupported_numerator_invalidates_the_whole_expression():
    spans = [span("y=", 10), span("p", 31, y=93, size=7, font="CMEX10"),
             span("c", 35, y=104, size=7, font="CMMI7")]
    rules(spans, (30, 97.5, 44))
    assert recover_expression(spans) is None


def test_centered_named_operator_limit_attaches_to_the_whole_roman_word():
    spans = [span("y=", 10), span("arg", 25), span("min", 43.5),
             span("z", 49, y=107, size=7, font="CMMI7"), span("z", 65, font="CMMI10")]
    assert recover_expression(spans) == r"y=\operatorname{arg}\operatorname*{min}\limits_{z}z"


def test_italic_letters_are_not_reinterpreted_as_a_named_operator():
    spans = [span("sin", 10, font="CMMI10"), span("x", 26.5, y=102, size=7, font="CMMI7")]
    assert recover_expression(spans) == "sin_{x}"


def test_relation_continuations_keep_their_rows_and_independent_scripts():
    spans = [span("y=a", 10), span("2", 26.5, y=96, size=7),
             span("=b", 21, y=115), span("i", 32, y=117, size=7, font="CMMI7")]
    assert recover_expression(spans) == r"\begin{aligned}&y=a^{2}\\&=b_{i}\end{aligned}"


def test_two_unrelated_equations_are_not_assumed_to_be_a_continuation():
    assert recover_expression([span("y=a", 10), span("z=b", 10, y=115)]) is None


def test_annotation_only_decodes_verified_unmapped_font_encoding_positions():
    parent = SimpleNamespace(
        extract_font=lambda xref: ("CMEX10", "pfa", "Type1",
                                  b"dup 98 /hatwide put\ndup 112 /radicalbig put\ncurrentfile eexec"),
        xref_object=lambda xref: "/ToUnicode 9 0 R",
        xref_stream=lambda xref: b"1 beginbfchar\n<70> <0070>\nendbfchar")
    page = SimpleNamespace(parent=parent, get_drawings=lambda: [],
                           get_fonts=lambda full: [(7, "pfa", "Type1", "CMEX10")])
    spans = [span("b", 10, font="CMEX10"), span("p", 20, font="CMEX10")]
    annotate_page_math(page, [SimpleNamespace(spans=spans)])
    assert spans[0]["chars"][0]["_glyph_name"] == "hatwide"
    assert "_glyph_name" not in spans[1]["chars"][0]


def test_explicit_unicode_destination_and_pdf_font_encoding_prevent_name_reinterpretation():
    parent = SimpleNamespace(
        extract_font=lambda xref: ("CMEX10", "pfa", "Type1", b"dup 98 /hatwide put\ncurrentfile eexec"),
        xref_object=lambda xref: "/ToUnicode 9 0 R",
        xref_stream=lambda xref: b"1 beginbfrange\n<20> <21> [<0062> <0063>]\nendbfrange")
    page = SimpleNamespace(parent=parent, get_drawings=lambda: [],
                           get_fonts=lambda full: [(7, "pfa", "Type1", "CMEX10")])
    spans = [span("b", 10, font="CMEX10")]
    annotate_page_math(page, [SimpleNamespace(spans=spans)])
    assert "_glyph_name" not in spans[0]["chars"][0]
    parent.xref_object = lambda xref: "/Encoding 10 0 R"
    annotate_page_math(page, [SimpleNamespace(spans=spans)])
    assert "_glyph_name" not in spans[0]["chars"][0]


def test_parser_recovers_a_drawn_fraction_and_does_not_serialize_internal_geometry():
    with fitz.open() as document:
        page = document.new_page(width=600, height=800)
        page.insert_text((80, 80), "This ordinary paragraph introduces a synthetic formula and explains its purpose for a general reader.", fontsize=11)
        page.insert_text((180, 130), "y =", fontname="tiit", fontsize=10)
        page.insert_text((210, 123), "a+b", fontname="symb", fontsize=10)
        page.insert_text((216, 137), "c", fontname="symb", fontsize=10)
        page.draw_line((209, 127.5), (228, 127.5), width=.5)
        page.insert_text((505, 130), "(1)", fontsize=10)
        page.insert_text((80, 170), "This following paragraph supplies ordinary context.", fontsize=11)
        parsed = extract_pdf(document.tobytes(), "synthetic-structure.pdf")
    equation = next(item for item in parsed["paragraphs"] if item["kind"] == "equation")
    assert equation["text"] == r"$$y=\frac{a+b}{c}\tag{1}$$"
    assert "_math_rules" not in repr(parsed) and "_glyph_name" not in repr(parsed)
