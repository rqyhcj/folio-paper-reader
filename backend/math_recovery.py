"""Conservative TeX recovery from PDF character positions, never from prose guesses.

Only single-baseline expressions and clearly offset, smaller scripts are
reconstructed. Ambiguous font encodings, stacked fractions, overlapping glyphs
and multi-line layouts fall back to their original PDF region.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import re
import unicodedata


_MATH_FONT = re.compile(r"(?:CMMI|CMMIB|CMSY|CMEX|MSAM|MSBM|Math|Symbol)", re.I)
_CM_FONT = re.compile(r"(?:^|\+)(?:CM(?:R|BX|TI|MI|MIB|SY)|MSBM)\d", re.I)
_NUMBER = re.compile(r"^\s*[（(](\d+[a-z]?)[）)]\s*$", re.I)
_OPERATORS = {"arg", "min", "max", "argmin", "argmax", "sin", "cos", "tan", "log", "ln",
              "exp", "lim", "sup", "inf", "det", "diag", "rank", "tr", "SVD", "SVDk", "ReLU"}
_GREEK_NAMES = {
    "α": "alpha", "β": "beta", "γ": "gamma", "δ": "delta", "ε": "epsilon", "ϵ": "varepsilon",
    "ζ": "zeta", "η": "eta", "θ": "theta", "ϑ": "vartheta", "ι": "iota", "κ": "kappa",
    "λ": "lambda", "μ": "mu", "ν": "nu", "ξ": "xi", "π": "pi", "ϖ": "varpi",
    "ρ": "rho", "ϱ": "varrho", "σ": "sigma", "ς": "varsigma", "τ": "tau", "υ": "upsilon",
    "φ": "phi", "ϕ": "varphi", "χ": "chi", "ψ": "psi", "ω": "omega", "Γ": "Gamma",
    "Δ": "Delta", "Θ": "Theta", "Λ": "Lambda", "Ξ": "Xi", "Π": "Pi", "Σ": "Sigma",
    "Υ": "Upsilon", "Φ": "Phi", "Ψ": "Psi", "Ω": "Omega",
}
_SYMBOLS = {
    "−": "-", "×": r"\times ", "·": r"\cdot ", "⋅": r"\cdot ", "÷": r"\div ",
    "±": r"\pm ", "∓": r"\mp ", "≈": r"\approx ", "≃": r"\simeq ", "≅": r"\cong ",
    "≠": r"\ne ", "≤": r"\le ", "≥": r"\ge ", "≪": r"\ll ", "≫": r"\gg ",
    "∼": r"\sim ", "∝": r"\propto ", "∞": r"\infty ", "∈": r"\in ", "∉": r"\notin ",
    "⊂": r"\subset ", "⊆": r"\subseteq ", "⊃": r"\supset ", "⊇": r"\supseteq ",
    "∪": r"\cup ", "∩": r"\cap ", "∅": r"\varnothing ", "∀": r"\forall ", "∃": r"\exists ",
    "∂": r"\partial ", "∇": r"\nabla ", "∑": r"\sum ", "∏": r"\prod ", "∫": r"\int ",
    "→": r"\to ", "←": r"\leftarrow ", "↔": r"\leftrightarrow ", "↦": r"\mapsto ",
    "⇒": r"\Rightarrow ", "⇔": r"\Leftrightarrow ", "⊤": r"\top ", "⊥": r"\perp ",
    "ℝ": r"\mathbb{R}", "ℕ": r"\mathbb{N}", "ℤ": r"\mathbb{Z}", "ℂ": r"\mathbb{C}",
    "ℚ": r"\mathbb{Q}", "ℓ": r"\ell ", "′": r"\prime ", "…": r"\ldots ", "∥": r"\Vert ",
    "⌊": r"\lfloor ", "⌋": r"\rfloor ", "⌈": r"\lceil ", "⌉": r"\rceil ",
    "⟨": r"\langle ", "⟩": r"\rangle ", "{": r"\{", "}": r"\}", "%": r"\%", "_": r"\_",
}


@dataclass(frozen=True)
class _Glyph:
    char: str
    x0: float
    y0: float
    x1: float
    y1: float
    baseline: float
    size: float
    font: str


def keep_span_characters(span: dict) -> bool:
    """Retain character geometry only for bounded potential mathematical runs.

    Most PDF text is prose; retaining rawdict's nested per-character records
    for the entire paper would multiply memory usage unnecessarily.
    """
    text = span.get("text", "").strip()
    if not text or len(text) > 450:
        return False
    if _MATH_FONT.search(span.get("font", "")):
        return True
    words = re.findall(r"[A-Za-z]+", text)
    allowed = all(len(word) == 1 or word in _OPERATORS for word in words)
    return allowed and all(char.isspace() or char in _GREEK_NAMES or char in _SYMBOLS
                           or bool(re.fullmatch(r"[A-Za-z0-9=+\-*/.,:;!|()\[\]<>?]", char)) for char in text)


def _glyphs(spans: list[dict]) -> list[_Glyph]:
    result = []
    for span in spans:
        for char in span.get("chars", []):
            value = char.get("c", "")
            if not value or value.isspace():
                continue
            box = char.get("bbox", span["bbox"])
            origin = char.get("origin", span.get("origin", (box[0], box[3])))
            result.append(_Glyph(value, *map(float, box), float(origin[1]), float(span["size"]), span["font"]))
    return result


def _tex(glyph: _Glyph) -> str | None:
    char = glyph.char
    # Computer Modern's extension font often decodes accents/delimiters as
    # unrelated ASCII letters. We cannot establish their meaning from a PDF
    # text extraction, even when the resulting string happens to look valid.
    if "CMEX" in glyph.font.upper() or len(char) != 1 or unicodedata.combining(char):
        return None
    if char in _GREEK_NAMES:
        value = "\\" + _GREEK_NAMES[char] + " "
    elif char in _SYMBOLS:
        value = _SYMBOLS[char]
    elif re.fullmatch(r"[A-Za-z0-9=+\-*/.,:;!|()\[\]<>?]", char):
        value = char
    else:
        return None
    if re.fullmatch(r"[A-Za-z]", char) and "MSBM" in glyph.font.upper():
        value = r"\mathbb{" + value + "}"
    elif re.fullmatch(r"[A-Za-z]", char) and re.search(r"CMMIB|CMBX", glyph.font, re.I):
        value = r"\mathbf{" + value + "}"
    elif char in _GREEK_NAMES and re.search(r"CMMIB|CMBX", glyph.font, re.I):
        value = r"\boldsymbol{" + value.strip() + "}"
    return value


def _balanced(glyphs: list[_Glyph]) -> bool:
    stack = []
    pairs = {")": "(", "]": "[", "}": "{"}
    for glyph in sorted(glyphs, key=lambda item: item.x0):
        if glyph.char in "([{":
            stack.append(glyph.char)
        elif glyph.char in pairs:
            if not stack or stack.pop() != pairs[glyph.char]:
                return False
    return not stack


def recover_expression(spans: list[dict], *, numbered: bool = False) -> str | None:
    """Return TeX only when the extracted characters and layout are unambiguous.

    ``spans`` must contain rawdict ``chars`` with original font, size, origin,
    and bbox. No model, OCR or text-only equation guessing is performed.
    """
    if not spans:
        return None
    # Do not silently recover a subset after prose geometry was discarded.
    if any(span.get("text", "").strip() and not span.get("chars") for span in spans):
        return None
    equation_number = None
    if numbered:
        other = [span for span in spans if not _NUMBER.fullmatch(span.get("text", ""))]
        right = max((span["bbox"][2] for span in other), default=0)
        kept = []
        for span in spans:
            match = _NUMBER.fullmatch(span.get("text", ""))
            if match and span["bbox"][0] > right + float(span["size"]):
                if equation_number is not None:
                    return None
                equation_number = match.group(1)
            else:
                kept.append(span)
        spans = kept
    glyphs = _glyphs(spans)
    if not glyphs or len(glyphs) > 450:
        return None
    # Any glyph that cannot be decoded also invalidates the surrounding
    # expression. A plausible subset would silently alter its meaning.
    if any(_tex(glyph) is None for glyph in glyphs):
        return None
    # A long subscript can contain more characters than its base variable.
    # Frequency alone would incorrectly promote that subscript to the main row.
    base_size = max(round(glyph.size, 1) for glyph in glyphs)
    main = [glyph for glyph in glyphs if glyph.size >= base_size * .88]
    if not main:
        return None
    baseline = Counter(round(glyph.baseline, 1) for glyph in main).most_common(1)[0][0]
    if any(abs(glyph.baseline - baseline) > base_size * .32 for glyph in main):
        return None  # multiple main rows / a stacked fraction or matrix
    scripts = [glyph for glyph in glyphs if glyph not in main]
    if any(glyph.size < base_size * .58 or abs(glyph.baseline - baseline) < base_size * .055 for glyph in scripts):
        return None
    if not _balanced(main):
        return None
    if numbered and not any(glyph.char in "=<>≈≃≅≠≤≥≪≫∈∉∼∝→↔⇒⇔" for glyph in main):
        return None  # an isolated tail/index of a larger, fragmented equation
    main.sort(key=lambda item: item.x0)
    # Non-space glyphs overlapping horizontally on one baseline are usually
    # combining overlays or inaccessible font encodings, not two variables.
    for left, right in zip(main, main[1:]):
        overlap = min(left.x1, right.x1) - max(left.x0, right.x0)
        if overlap > min(left.x1 - left.x0, right.x1 - right.x0) * .55:
            return None

    attachments: dict[int, dict[str, list[_Glyph]]] = {}
    script_groups: list[tuple[str, list[_Glyph]]] = []
    for glyph in sorted(scripts, key=lambda item: item.x0):
        key = "_" if glyph.baseline > baseline else "^"
        group = next((items for candidate_key, items in reversed(script_groups)
                      if candidate_key == key and abs(items[-1].baseline - glyph.baseline) <= base_size * .15
                      and -base_size * .12 <= glyph.x0 - items[-1].x1 <= base_size * .45), None)
        if group is None:
            script_groups.append((key, [glyph]))
        else:
            group.append(glyph)
    for key, group in script_groups:
        glyph = group[0]
        candidates = [(index, item) for index, item in enumerate(main)
                      if item.x0 <= glyph.x0 + base_size * .10 and item.x1 <= glyph.x0 + base_size * .55]
        if not candidates:
            return None
        index, anchor = min(candidates, key=lambda pair: abs(pair[1].x1 - glyph.x0))
        if glyph.x0 - anchor.x1 > base_size * .75:
            return None
        attachments.setdefault(index, {}).setdefault(key, []).extend(group)

    pieces = []
    for index, glyph in enumerate(main):
        value = _tex(glyph)
        for key in ("_", "^"):
            script = sorted(attachments.get(index, {}).get(key, []), key=lambda item: item.x0)
            if not script:
                continue
            for left, right in zip(script, script[1:]):
                if abs(left.baseline - right.baseline) > base_size * .15 or right.x0 < left.x1 - base_size * .12:
                    return None  # e.g. numerator/denominator stacked at same x
            if not _balanced(script):
                return None
            value += key + "{" + "".join(_tex(item) for item in script).strip() + "}"
        pieces.append(value)
    result = "".join(pieces).strip()
    if main[0].char in "=<>*/" or main[-1].char in "=<>+*/-":
        return None
    # Standard named operators are roman in the source. Keep multi-letter
    # names together without affecting adjacent italic variables.
    for name in sorted(_OPERATORS, key=len, reverse=True):
        result = re.sub(r"(?<![A-Za-z\\{])" + re.escape(name) + r"(?![A-Za-z}])",
                        lambda match: r"\operatorname{" + match.group(0) + "}", result)
    if equation_number:
        result += r"\tag{" + equation_number + "}"
    return result or None


def _span_text(spans: list[dict]) -> str:
    result = []
    previous = None
    for span in spans:
        text = span.get("text", "")
        if previous and result and not result[-1].endswith(" ") and not text.startswith(" "):
            gap = span["bbox"][0] - previous["bbox"][2]
            if gap > min(span["size"], previous["size"]) * .18:
                result.append(" ")
        result.append(text)
        previous = span
    return "".join(result)


def _is_seed(span: dict) -> bool:
    text = span.get("text", "").strip()
    return bool(text and (_MATH_FONT.search(span.get("font", ""))
                         or (any(char in _GREEK_NAMES for char in text) and not re.search(r"[A-Za-z]{2}", text))))


def _is_context(span: dict) -> bool:
    text = span.get("text", "").strip()
    if not text:
        return True
    if _is_seed(span):
        return True
    if not _CM_FONT.search(span.get("font", "")):
        return False
    words = re.findall(r"[A-Za-z]+", text)
    return all(len(word) == 1 or word in _OPERATORS for word in words)


def recover_inline(spans: list[dict], original: str) -> tuple[str, bool]:
    """Recover contiguous math-font runs while leaving surrounding prose plain."""
    parts = []
    changed = False
    index = 0
    while index < len(spans):
        if not _is_context(spans[index]):
            parts.append(([spans[index]], None))
            index += 1
            continue
        end = index + 1
        while end < len(spans) and _is_context(spans[end]):
            end += 1
        run = spans[index:end]
        latex = recover_expression(run) if any(_is_seed(span) for span in run) else None
        if latex:
            raw = _span_text(run)
            prefix = " " if raw[:1].isspace() else ""
            suffix = " " if raw[-1:].isspace() else ""
            parts.append((run, prefix + r"\(" + latex + r"\)" + suffix))
            changed = True
        else:
            parts.append((run, None))
        index = end
    if not changed:
        return original, False
    # Preserve the same geometry-based gap policy across the new boundaries.
    result = []
    previous = None
    for run, formatted in parts:
        value = formatted if formatted is not None else _span_text(run)
        first = run[0]
        if previous and result and not result[-1].endswith(" ") and not value.startswith(" "):
            gap = first["bbox"][0] - previous["bbox"][2]
            if gap > min(first["size"], previous["size"]) * .18:
                result.append(" ")
        result.append(value)
        previous = run[-1]
    return "".join(result).strip(), True


def recover_paragraph(paragraph: dict, lines: list) -> dict:
    """Annotate a parsed paragraph without exposing raw PDF spans in the API."""
    if paragraph["kind"] == "equation":
        paragraph["source_text"] = paragraph["text"]
        paragraph["equation_source"] = {"page": paragraph["page"], "bbox": paragraph["bbox"]}
        spans = [span for line in lines for span in line.spans]
        latex = recover_expression(spans, numbered=True)
        if latex:
            paragraph["text"] = "$$" + latex + "$$"
            paragraph["math_status"] = "latex"
        else:
            box = paragraph["bbox"]
            paragraph["math_status"] = "source-image"
            paragraph["equation_image"] = {"width": max(1, round(box[2] - box[0])),
                                           "height": max(1, round(box[3] - box[1])), "latex_available": False}
        return paragraph
    if paragraph["kind"] == "heading":
        return paragraph
    if any("CMEX" in span.get("font", "").upper() for line in lines for span in line.spans):
        # Large inline roots / delimiters can decode as unrelated ASCII
        # letters (e.g. CMEX's "q" is a radical). Preserve the entire prose
        # region rather than presenting a convincing but false inline formula.
        # It remains a prose paragraph so its surrounding explanation can be
        # translated; the original display uses the same safe image endpoint
        # as complex standalone equations.
        box = paragraph["bbox"]
        paragraph["source_text"] = paragraph["text"]
        paragraph["math_status"] = "source-image"
        paragraph["ambiguous_inline_math"] = True
        paragraph["equation_source"] = {"page": paragraph["page"], "bbox": box}
        paragraph["equation_image"] = {"width": max(1, round(box[2] - box[0])),
                                       "height": max(1, round(box[3] - box[1])), "latex_available": False}
        return paragraph
    changed = False
    texts = []
    for line in lines:
        text, converted = recover_inline(line.spans, line.text)
        texts.append(text)
        changed = changed or converted
    if changed:
        # Import locally to reuse the extractor's dehyphenation and CJK rules.
        from .pdf_parser import _join_lines
        paragraph["source_text"] = paragraph["text"]
        paragraph["text"] = _join_lines(texts)
        paragraph["math_status"] = "latex"
    return paragraph


def expand_equation_source(paragraph: dict, group: list, page_lines: list, body_size: float) -> None:
    """Include adjacent mathematical fragments in an original-equation crop.

    Complex layouts may split a single equation across several PDF blocks.
    Such fragments must remain visible in the source image even when the
    conservative paragraph assembler did not join their text.
    """
    if paragraph.get("kind") != "equation":
        return
    baselines = [line.baseline for line in group if line.equation] or [line.baseline for line in group]
    group_ids = {id(line) for line in group}
    neighbors = [line for line in page_lines if id(line) not in group_ids and line.flow == group[0].flow
                 and min(abs(line.baseline - baseline) for baseline in baselines) <= body_size * 1.1
                 and (line.math_ratio > .40 or line.size < body_size * .85
                      or (len(line.text) < 90 and bool(re.search(r"[=∑∫∏≈≤≥]", line.text))))]
    if not neighbors:
        return
    box = list(paragraph["bbox"])
    for line in neighbors:
        box = [min(box[0], line.x0), min(box[1], line.y0), max(box[2], line.x1), max(box[3], line.y1)]
    paragraph["equation_source"]["bbox"] = [round(value, 3) for value in box]
    # A valid TeX string describing only one fragment still misrepresents the
    # complete source equation. Prefer the full original region instead.
    paragraph["text"] = paragraph["source_text"]
    paragraph["math_status"] = "source-image"
    paragraph["equation_image"] = {"width": max(1, round(box[2] - box[0])),
                                   "height": max(1, round(box[3] - box[1])), "latex_available": False}
