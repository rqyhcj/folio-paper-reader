"""Regression cases for wrapped inline fractions and protected translation input."""
from types import SimpleNamespace

from backend.math_recovery import recover_paragraph
from backend.pdf_parser import (_Line, _assemble_page, _attach_extension_fragments,
                                _merge_line_fragments)


def span(text, box, *, font="NimbusRoman", size=10, baseline=None):
    return {"text": text, "bbox": box, "font": font, "size": size,
            "origin": (box[0], baseline if baseline is not None else box[3] - 2)}


def line(spans, box, baseline, *, size=10, block=1, equation=False):
    return _Line("".join(s["text"] for s in spans), *box, size, False, block, 3, 612, 792,
                 baseline=baseline, math_ratio=.6 if any("CM" in s["font"] for s in spans) else 0,
                 spans=spans, equation=equation)


def test_radical_cannot_bridge_two_different_prose_baselines():
    first = line([span("where the preceding prose line ends", [307, 450, 541, 461])],
                 [307, 450, 541, 461], 458)
    second = line([span("step and m=", [307, 462, 462, 474])],
                  [307, 455, 462, 474], 470)
    root = line([span("p", [465, 460, 475, 470], font="CMEX10", baseline=461)],
                [465, 460, 475, 470], 461)
    result = _attach_extension_fragments(_merge_line_fragments([first, second, root]))
    assert len(result) == 2
    assert result[0].text == first.text
    assert not any(s["font"] == "CMEX10" for s in result[0].spans)
    assert any(s["font"] == "CMEX10" for s in result[1].spans)


def test_wrapped_inline_fraction_and_when_clause_form_one_protected_paragraph():
    lines = [
        line([span("where controls the stochastic reverse", [50, 80, 300, 91])],
             [50, 80, 300, 91], 88),
        line([span("step and ", [50, 92, 90, 103]),
              span("m=", [90, 92, 120, 103], font="CMMI10"),
              span("p", [120, 85, 130, 100], font="CMEX10"),
              span("1-a", [130, 92, 300, 103], font="CMMI10")],
             [50, 85, 300, 103], 100),
        line([span("x-a", [50, 103, 90, 112], font="CMMI7", size=7)],
             [50, 103, 90, 112], 110, size=7, block=2),
        line([span("b", [55, 112, 85, 120], font="CMMI7", size=7)],
             [55, 112, 85, 120], 118, size=7, block=3),
        line([span(". When ", [92, 108, 140, 119]),
              span("s=", [140, 108, 180, 119], font="CMMI10")],
             [92, 108, 180, 119], 116, block=4),
        line([span("p", [190, 105, 200, 116], font="CMEX10"),
              span("1-a", [200, 108, 300, 119], font="CMMI10")],
             [190, 105, 300, 119], 116, block=5),
        line([span(", the process becomes deterministic.", [50, 122, 300, 133])],
             [50, 122, 300, 133], 130, block=6),
        line([span("A new paragraph starts after the real blank space.", [50, 150, 300, 161])],
             [50, 150, 300, 161], 158, block=7),
    ]
    result = _assemble_page(lines, 10)
    assert len(result) == 2
    paragraph = result[0]
    assert paragraph["bbox"] == [50, 80, 300, 133]
    assert paragraph["kind"] == "paragraph" and paragraph["ambiguous_inline_math"]
    assert paragraph["translation_text"] == (
        "where controls the stochastic reverse step and ⟦FOLIO_MATH_1⟧ "
        ". When ⟦FOLIO_MATH_2⟧ , the process becomes deterministic.")
    assert len(paragraph["inline_math"]) == 2
    assert len(paragraph["inline_math"][0]["source"]) == 2
    assert "new paragraph" not in paragraph["translation_text"]


def test_inline_radical_crop_uses_radicand_top_without_changing_full_source_region():
    spans = [span("A scale ", [5, 80, 30, 90]),
             span("p", [31, 72, 40, 86], font="CMEX10"),
             span("1-a", [40, 80, 70, 90], font="CMMI10")]
    paragraph = {"text": "A scale p1-a", "kind": "paragraph", "page": 3, "bbox": [5, 72, 70, 90]}
    result = recover_paragraph(paragraph, [SimpleNamespace(spans=spans)])
    assert result["equation_source"]["bbox"] == [5, 72, 70, 90]
    assert result["inline_math"][0]["source"][0]["bbox"] == [31, 80, 70, 90]
    assert result["translation_text"] == "A scale ⟦FOLIO_MATH_1⟧"


def test_unknown_tall_delimiter_keeps_its_full_source_height():
    spans = [span("A scale ", [5, 80, 30, 90]),
             span("\x00", [31, 72, 40, 92], font="CMEX10"),
             span("x", [40, 80, 70, 90], font="CMMI10")]
    paragraph = {"text": "A scale \x00x", "kind": "paragraph", "page": 3, "bbox": [5, 72, 70, 92]}
    result = recover_paragraph(paragraph, [SimpleNamespace(spans=spans)])
    assert result["inline_math"][0]["source"][0]["bbox"] == [31, 72, 70, 92]
    assert "\x00" not in result["translation_text"]


def test_undecodable_hat_is_protected_even_without_an_extension_font():
    spans = [span("The denoised estimate ", [5, 80, 90, 90]),
             span("ˆ", [91, 80, 96, 90], font="CMR10"),
             span("x", [91, 80, 99, 90], font="CMMI10"),
             span(" is obtained via:", [100, 80, 160, 90])]
    paragraph = {"text": "The denoised estimate ˆx is obtained via:", "kind": "paragraph",
                 "page": 3, "bbox": [5, 80, 160, 90]}
    result = recover_paragraph(paragraph, [SimpleNamespace(spans=spans)])
    assert result["ambiguous_inline_math"]
    assert result["translation_text"] == "The denoised estimate ⟦FOLIO_MATH_1⟧ is obtained via:"
    assert "latex" not in result["inline_math"][0]
