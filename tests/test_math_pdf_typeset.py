"""Local vector math export: actual formula ink replaces escaped TeX text."""
from copy import deepcopy

import fitz
import pytest

from backend.math_typeset import render_formula, split_math
from backend.pdf_export import BODY_BOTTOM, BODY_TOP, MARGIN, export_pdf


COMPLEX = (r"\frac{\|v-Q(v)\|_2}{\|v\|_2}"
           r"\leq\frac{\sqrt{n}}{14}"
           r"\underbrace{\frac{\|v\|_\infty}{\|v\|_2}}_{\kappa(v)},\quad v\ne0")


@pytest.mark.parametrize("source", [r"X\in\mathbb{R}^{m\times d}", COMPLEX,
    r"\begin{bmatrix}a&b\\c&d\end{bmatrix}",
    r"\sum_{i=1}^{n}\frac{\alpha_i}{\sqrt{1+\sigma_i^2}}",
    r"f(x)=\left\{\begin{array}{ll}x^2&x>0\\0&x\leq0\end{array}\right."])
def test_standard_math_structures_produce_local_vector_ink(source):
    formula = render_formula(source, 11.2, True, (.13, .18, .19))
    assert formula and formula.width > 0 and formula.height > 0
    with fitz.open(stream=formula.pdf, filetype="pdf") as document:
        assert document.page_count == 1
        assert len(document[0].get_drawings()) > 3
        assert not document[0].get_links() and not document[0].get_images()
        assert "\\" not in document[0].get_text()


def test_mixed_english_chinese_inline_and_display_math_has_no_raw_commands():
    source = r"Consider a row-wise input \(X\in\mathbb{R}^{m\times d}\), with weights \(W\in\mathbb{R}^{n\times d}\)."
    translation = r"考虑矩阵输入 \(X\in\mathbb{R}^{m\times d}\)，权重为 \(W\in\mathbb{R}^{n\times d}\)。"
    paper = {"title": "Vector mathematics", "paragraphs": [
        {"id": "p1", "text": source, "translation": translation},
        {"id": "p2", "kind": "equation", "math_status": "latex", "text": "\\[" + COMPLEX + "\\]"},
        {"id": "p3", "text": "The matrix bound remains readable.", "translation": "公式之后的正文完整保留。"},
    ]}
    before = deepcopy(paper)
    with fitz.open(stream=export_pdf(paper), filetype="pdf") as document:
        text = "\n".join(page.get_text() for page in document)
        assert "Consider a row-wise input" in text and "with weights" in text
        assert "考虑矩阵输入" in text and "公式之后的正文完整保留。" in text
        assert not any(command in text for command in (r"\(", r"\[", r"\mathbb", r"\frac", r"\underbrace"))
        assert "未能排版" not in text
        assert sum(len(page.get_drawings()) for page in document) > 40
        for page in document:
            for drawing in page.get_drawings():
                rect = drawing["rect"]
                assert rect.x0 >= MARGIN - 1 and rect.x1 <= page.rect.width - MARGIN + 1
                assert rect.y0 >= 50 and rect.y1 <= BODY_BOTTOM + 1
    assert paper == before


def test_long_math_prose_flows_pages_and_formula_height_stays_in_body():
    paragraph = r"Evidence \(\frac{a_1+b_2}{\sqrt{c^2+d^2}}\) must remain aligned with prose. " * 220
    paper = {"title": "Long mathematics", "paragraphs": [
        {"id": "long", "text": paragraph, "translation": "中文说明 " + r"\(x_t\) " * 200},
        {"id": "last", "text": "End of source", "translation": "中文结尾"}]}
    with fitz.open(stream=export_pdf(paper), filetype="pdf") as document:
        assert document.page_count > 3
        text = "\n".join(page.get_text() for page in document)
        assert "End of source" in text and "中文结尾" in text
        assert text.count("Evidence") == 220
        for page in document:
            for drawing in page.get_drawings():
                rect = drawing["rect"]
                if rect.y0 > 52:  # footer furniture is separate from body math
                    assert rect.y0 >= BODY_TOP - 1 and rect.y1 <= BODY_BOTTOM + 1


@pytest.mark.parametrize("source", [r"\frac{1}", r"\unknowncommand{x}", r"\href{https://invalid.test}{x}",
    r"\includegraphics{file:///private/key}", "{" * 100 + "x" + "}" * 100, "x" * 9000])
def test_invalid_or_unsafe_formula_is_explicit_and_never_loaded(source):
    assert render_formula(source, 11.2, False, (.13, .18, .19)) is None
    paper = {"title": "Safe export", "paragraphs": [
        {"text": r"Before \(" + source + r"\) after.", "translation": "正文仍可阅读"}]}
    # The splitter deliberately leaves unmatched brace groups untouched.
    if source.count("{") == source.count("}"):
        with fitz.open(stream=export_pdf(paper), filetype="pdf") as document:
            text = "".join(page.get_text() for page in document)
            assert "公式未能排版" in text and "after." in text
            assert not any(page.get_links() or page.get_images() for page in document)


def test_math_delimiters_preserve_currency_and_balanced_braces():
    parts = split_math(r"Cost \$20, inline \(\frac{x}{y}\), display $$z^2$$ and unmatched \(x.")
    assert [part.text for part in parts if part.math] == [r"\frac{x}{y}", "z^2"]
    assert [part.display for part in parts if part.math] == [False, True]
    assert "unmatched" in parts[-1].text
