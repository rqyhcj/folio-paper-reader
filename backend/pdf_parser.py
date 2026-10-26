"""Extract readable PDF text into paragraphs for an interleaved paper reader.

This is a text extractor, not OCR. Layout recovery is deliberately conservative:
text blocks, font information and line geometry retain paragraphs; a detected
central gutter orders two-column papers by column instead of by line.
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import PureWindowsPath

import fitz

from .pdf_runtime import synchronized_pdf
from .math_recovery import _span_text, annotate_page_math, expand_equation_source, keep_span_characters, recover_paragraph
from .reading_structure import extract_structures, stitch_sentences


MAX_PAGES = 500
MAX_TEXT_CHARS = 3_000_000
MAX_PARAGRAPH_CHARS = 4500
PARSE_VERSION = 8


class PDFExtractionError(ValueError):
    """The uploaded PDF cannot be read safely as selectable text."""


@dataclass
class _Line:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    size: float
    bold: bool
    block: int
    page: int
    width: float
    height: float
    flow: str = "main"
    baseline: float = 0.0
    math_ratio: float = 0.0
    equation: bool = False
    spans: list[dict] = field(default_factory=list)
    inline_region: int | None = None
    structured: dict | None = None


_LIGATURES = str.maketrans({
    "\ufb00": "ff", "\ufb01": "fi", "\ufb02": "fl", "\ufb03": "ffi",
    "\ufb04": "ffl", "\ufb05": "st", "\ufb06": "st", "\u00a0": " ",
})
_COMMON_HEADINGS = re.compile(
    r"^(?:abstract|introduction|background|related work|methods?|materials and methods|"
    r"results?(?: and discussion)?|discussion|conclusions?|limitations?|"
    r"acknowledg(?:e)?ments?|references|bibliography|appendi(?:x|ces)|"
    r"supplementary (?:material|information)|摘要|引言|绪论|方法|结果|讨论|结论|参考文献)$",
    re.IGNORECASE,
)
_NUMBERED_HEADING = re.compile(r"^(\d{1,2}(?:\.\d{1,2}){0,5})\.?\s+([A-Za-z\u3400-\u9fff].*)$")
def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.translate(_LIGATURES)).strip()


def _read_lines(page: fitz.Page, page_number: int, removed_line_numbers: list[dict] | None = None) -> list[_Line]:
    lines = []
    flags = fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_IMAGES
    for block_number, block in enumerate(page.get_text("rawdict", sort=False, flags=flags)["blocks"]):
        if block.get("type") != 0:
            continue
        for item in block.get("lines", []):
            # Vertical side labels are not part of the paper's prose.
            direction = item.get("dir", (1.0, 0.0))
            if abs(direction[1]) > 0.2:
                continue
            spans = item.get("spans", [])
            pieces = []
            previous = None
            weights: Counter[float] = Counter()
            bold_chars = 0
            math_chars = 0
            total_chars = 0
            baselines: Counter[float] = Counter()
            for span in spans:
                text = "".join(char.get("c", "") for char in span.get("chars", []))
                span["text"] = text
                if not keep_span_characters(span):
                    span.pop("chars", None)
                if not text:
                    continue
                if previous and pieces and not pieces[-1].endswith(" ") and not text.startswith(" "):
                    gap = span["bbox"][0] - previous["bbox"][2]
                    if gap > min(span.get("size", 10), previous.get("size", 10)) * 0.18:
                        pieces.append(" ")
                pieces.append(text)
                count = len(text.strip())
                size = round(float(span.get("size", 10)), 1)
                weights[size] += count
                total_chars += count
                baselines[round(float(span.get("origin", (0, item["bbox"][3]))[1]), 1)] += count
                if re.search(r"(?:CMMI|CMMIB|CMSY|CMEX|MSAM|MSBM|Math|Symbol)", span.get("font", ""), re.I):
                    math_chars += count
                if span.get("flags", 0) & 16 or "bold" in span.get("font", "").lower():
                    bold_chars += count
                previous = span
            text = _clean("".join(pieces))
            if not text:
                continue
            x0, y0, x1, y1 = item["bbox"]
            lines.append(_Line(
                text, x0, y0, x1, y1, weights.most_common(1)[0][0] if weights else 10,
                bold_chars > total_chars * 0.5, block_number, page_number,
                float(page.rect.width), float(page.rect.height),
                baseline=baselines.most_common(1)[0][0] if baselines else y1,
                math_ratio=math_chars / max(1, total_chars),
                spans=spans,
            ))
    lines = _remove_review_line_numbers(lines, removed_line_numbers)
    annotate_page_math(page, lines)
    return _attach_extension_fragments(_merge_line_fragments(lines))


def _remove_review_line_numbers(lines: list[_Line], removed: list[dict] | None = None) -> list[_Line]:
    """Remove proven review-number rails before their digits join body/math rows.

    A bare integer is never enough evidence: markers must form a consecutive
    vertical sequence at an outer margin, with ordinary prose beside that rail.
    This preserves isolated numbers, equation labels, years and table values.
    """
    if not lines:
        return lines
    rails: list[list[tuple[_Line, dict, int]]] = []
    prose = []
    for line in lines:
        nonnumeric = [span for span in line.spans
                      if _clean(span.get("text", "")) and not re.fullmatch(r"\d+", _clean(span.get("text", "")))]
        if nonnumeric and len(_clean("".join(span.get("text", "") for span in nonnumeric))) >= 16:
            prose.append((line.baseline, min(span["bbox"][0] for span in nonnumeric),
                          max(span["bbox"][2] for span in nonnumeric), line.size))
        for span in line.spans:
            text = _clean(span.get("text", ""))
            if not re.fullmatch(r"\d{1,4}", text):
                continue
            value = int(text)
            # Year labels in charts/lists are content even when they happen to
            # increase consecutively at a page edge.
            if 1800 <= value <= 2199:
                continue
            x0, y0, x1, y1 = span["bbox"]
            size = float(span.get("size", line.size))
            side = "left" if x1 <= line.width * .12 else "right" if x0 >= line.width * .88 else None
            if side is None or x1 - x0 > size * 3.8:
                continue
            signature = (side, span.get("font", ""), round(size, 1), span.get("color", 0))
            rail = next((items for items in rails if items[0][1]["_review_signature"] == signature
                         and abs(items[0][1]["bbox"][0] - x0) <= max(1.5, size * .2)), None)
            span["_review_signature"] = signature
            if rail is None:
                rail = []
                rails.append(rail)
            rail.append((line, span, value))
    # A narrow numeric table column can itself sit near the page edge.
    # Proven review rails must still be outside the recurring prose boundary.
    left_edges = Counter(round(x0 / 3) * 3 for _, x0, _, _ in prose)
    body_left = min((edge for edge, count in left_edges.items() if count >= 3), default=None)
    if body_left is not None:
        rails = [rail for rail in rails if rail[0][1]["_review_signature"][0] != "left"
                 or rail[0][1]["bbox"][2] < body_left - float(rail[0][1].get("size", rail[0][0].size)) * .65]
    remove_ids = set()
    runs = []
    for rail in rails:
        rail.sort(key=lambda item: (float(item[1].get("origin", (0, item[1]["bbox"][3]))[1]), item[2]))
        run = []
        for item in rail:
            if run and not 1 <= item[2] - run[-1][2] <= 3:
                runs.append(run)
                run = []
            run.append(item)
        if run:
            runs.append(run)
    confirmed = []
    for rail in runs:
        if len(rail) < 5:
            continue
        values = [item[2] for item in rail]
        steps = [right - left for left, right in zip(values, values[1:])]
        consecutive = sum(step == 1 for step in steps)
        ordered = sum(1 <= step <= 3 for step in steps)
        top = rail[0][1]["bbox"][1]
        bottom = rail[-1][1]["bbox"][3]
        size = float(rail[0][1].get("size", rail[0][0].size))
        if consecutive < 3 or ordered < len(steps) * .8 or bottom - top < size * 5:
            continue
        adjacent = 0
        for line, span, _ in rail:
            x0, _, x1, _ = span["bbox"]
            baseline = float(span.get("origin", (0, line.baseline))[1])
            left = span["_review_signature"][0] == "left"
            adjacent += any(size <= body_size * .94
                            and abs(base - baseline) <= max(size, body_size) * .6
                            and (body_x0 >= x1 + size * .65 if left else body_x1 <= x0 - size * .65)
                            and (body_x0 - x1 if left else x0 - body_x1) <= line.width * .30
                            for base, body_x0, body_x1, body_size in prose)
        if adjacent < max(3, len(rail) * .45):
            continue
        confirmed.append(rail)
    # A final column can contain only one or two review lines. Accept its
    # short rail only when it directly continues the proven opposite rail,
    # uses the same marker typography and also sits beside actual body text.
    accepted = list(confirmed)
    for rail in runs:
        if len(rail) >= 5 or not rail:
            continue
        signature = rail[0][1]["_review_signature"]
        if not any(signature[0] != other[0][1]["_review_signature"][0]
                   and signature[1:] == other[0][1]["_review_signature"][1:]
                   and (1 <= other[0][2] - rail[-1][2] <= 3
                        or 1 <= rail[0][2] - other[-1][2] <= 3) for other in confirmed):
            continue
        adjacent = 0
        for line, span, _ in rail:
            x0, _, x1, _ = span["bbox"]
            baseline = float(span.get("origin", (0, line.baseline))[1])
            size = float(span.get("size", line.size))
            left = signature[0] == "left"
            adjacent += any(size <= body_size * .94
                            and abs(base - baseline) <= max(size, body_size) * .6
                            and (body_x0 >= x1 + size * .65 if left else body_x1 <= x0 - size * .65)
                            for base, body_x0, body_x1, body_size in prose)
        if adjacent >= max(1, len(rail) * .5):
            accepted.append(rail)
    for rail in accepted:
        for line, span, _ in rail:
            remove_ids.add(id(span))
            if removed is not None:
                removed.append({"page": line.page, "text": _clean(span["text"]),
                                "bbox": [round(float(value), 3) for value in span["bbox"]]})
    result = []
    for line in lines:
        spans = [span for span in line.spans if id(span) not in remove_ids]
        for span in line.spans:
            span.pop("_review_signature", None)
        if len(spans) == len(line.spans):
            result.append(line)
        elif spans:
            # A PDF text line can include its margin marker in the same run.
            # Recompute the source box so formula screenshots exclude that rail.
            line.spans = spans
            line.text = _clean(_span_text(spans))
            line.x0 = min(span["bbox"][0] for span in spans)
            line.y0 = min(span["bbox"][1] for span in spans)
            line.x1 = max(span["bbox"][2] for span in spans)
            line.y1 = max(span["bbox"][3] for span in spans)
            result.append(line)
    return result


def _merge_line_fragments(lines: list[_Line]) -> list[_Line]:
    """Recover physical lines split into separate PDF text runs.

    TeX formulas, accents and inline math can be separate PDF lines despite
    sharing a baseline. Joining only nearby fragments (or one original block)
    preserves columns. Equation numbers belong to the same mathematical row.
    This does not attempt to infer missing mathematical symbols or LaTeX.
    """
    rows: list[list[_Line]] = []
    for line in sorted(lines, key=lambda item: (item.baseline, item.x0)):
        candidates = []
        for row in reversed(rows):
            main = max(row, key=lambda item: len(item.text))
            tolerance = max(main.size, line.size) * 0.34
            if line.baseline - main.baseline > max(main.size, line.size) * 1.2:
                # Earlier joins can change a row's dominant baseline. Rows are
                # therefore not strictly baseline-sorted any more; an early
                # break can miss a raised radical in another column.
                continue
            # TeX extension glyphs (large radicals / delimiters) often report
            # their top as their baseline. Their ink still overlaps the real
            # row, so attach that glyph geometrically instead of emitting a
            # meaningless standalone letter such as CMEX's decoded "s".
            def extension(item: _Line) -> bool:
                return bool(item.spans and len(item.text) <= 4 and all(
                    "CMEX" in span.get("font", "").upper() for span in item.spans))
            # Tall extension glyphs have an artificial top baseline. Joining
            # them here lets one radical bridge two different prose rows.
            # Attach these glyphs only after the real baseline rows exist.
            if extension(line) or any(extension(item) for item in row):
                continue
            if abs(line.baseline - main.baseline) > tolerance:
                continue
            x0, x1 = min(item.x0 for item in row), max(item.x1 for item in row)
            horizontal_gap = max(x0 - line.x1, line.x0 - x1, 0)
            same_block = any(item.block == line.block for item in row)
            is_number = lambda item: bool(re.fullmatch(r"[（(]\s*\d+[a-z]?\s*[）)]", item.text, re.I))
            has_math = lambda item: item.math_ratio > .18 or bool(re.search(r"[=∑∫∏≈≤≥]", item.text))
            equation_number_pair = ((is_number(line) and any(has_math(item) for item in row))
                                    or (has_math(line) and any(is_number(item) for item in row)))
            overlap_y = min(max(item.y1 for item in row), line.y1) - max(min(item.y0 for item in row), line.y0)
            if overlap_y <= 0 or not (horizontal_gap < min(main.size, line.size) * 1.5
                                      or (same_block and equation_number_pair)):
                continue
            candidates.append((abs(line.baseline - main.baseline), horizontal_gap, row))
        if candidates:
            min(candidates, key=lambda item: item[:2])[2].append(line)
        else:
            rows.append([line])

    merged = []
    for row in rows:
        row.sort(key=lambda item: (item.x0, item.y0))
        main = max(row, key=lambda item: len(item.text))
        count = sum(len(item.text) for item in row)
        text = _join_lines([item.text for item in row])
        math_ratio = sum(item.math_ratio * len(item.text) for item in row) / max(1, count)
        # A separate right-aligned equation number must not become a heading or
        # a translation paragraph of its own. Ordinary prose ending in a
        # parenthetical citation is excluded by the math/short-row evidence.
        numbered = any(re.fullmatch(r"[（(]\s*\d+[a-z]?\s*[）)]", item.text, re.I) for item in row)
        equation = numbered and (math_ratio > .18 or (len(text) < 180 and bool(re.search(r"[=∑∫∏≈≤≥]", text))))
        merged.append(_Line(text, min(item.x0 for item in row), min(item.y0 for item in row),
                            max(item.x1 for item in row), max(item.y1 for item in row),
                            main.size, main.bold, main.block, main.page, main.width, main.height,
                            baseline=main.baseline, math_ratio=math_ratio, equation=equation,
                            spans=[span for item in row for span in item.spans]))
    return merged


def _attach_extension_fragments(lines: list[_Line]) -> list[_Line]:
    """Attach remaining tall TeX glyphs after the surrounding row is whole.

    A radical or bracket can be separated from *both* sides of its text row.
    On the first pass neither side may overlap its bbox; once the row is
    assembled, a second geometric pass can attach that exact original glyph.
    """
    pending = list(lines)
    split = _column_split(lines, _body_size([lines]))

    def same_column(line: _Line, glyph: _Line) -> bool:
        if split is None or line.x0 < split < line.x1:
            return True
        return ((line.x0 + line.x1) / 2 < split) == ((glyph.x0 + glyph.x1) / 2 < split)

    for glyph in lines:
        if not any(item is glyph for item in pending):
            continue
        if not (glyph.spans and len(glyph.text) <= 4 and all(
                "CMEX" in span.get("font", "").upper() for span in glyph.spans)):
            continue
        targets = [line for line in pending if line is not glyph and len(line.text) > len(glyph.text)
                   and same_column(line, glyph)
                   and max(line.x0 - glyph.x1, glyph.x0 - line.x1, 0) <= glyph.size * .5
                   and min(line.y1, glyph.y1) > max(line.y0, glyph.y0)]
        if not targets:
            continue
        target = min(targets, key=lambda item: (abs(item.baseline - glyph.y1),
                                                max(item.x0 - glyph.x1, glyph.x0 - item.x1, 0)))
        target.x0, target.y0 = min(target.x0, glyph.x0), min(target.y0, glyph.y0)
        target.x1, target.y1 = max(target.x1, glyph.x1), max(target.y1, glyph.y1)
        target.spans.extend(glyph.spans)
        target.spans.sort(key=lambda span: span["bbox"][0])
        target.text = _clean(_span_text(target.spans))
        target.math_ratio = (target.math_ratio * len(target.text) + len(glyph.text)) / max(
            1, len(target.text) + len(glyph.text))
        pending = [line for line in pending if line is not glyph]
    return pending


def _attach_inline_math_fragments(lines: list[_Line], body_size: float) -> list[_Line]:
    """Place small inline fractions on their real prose row, never a display.

    A numerator / denominator may have smaller type and unrelated PDF blocks.
    Its nearest body-text baseline is the surrounding line, including prose to
    the right of the fraction. Keeping that physical row intact also lets the
    translation placeholder cover the whole formula instead of a lone letter.
    """
    pending = list(lines)
    split = _column_split(lines, body_size)

    def column(line: _Line) -> int:
        return 0 if split is None else -1 if line.x1 <= split else 1 if line.x0 >= split else 0

    prose = [line for line in lines if not line.equation and line.size >= body_size * .85
             and not _math_only(line) and _heading_level(line, body_size) is None]
    for fragment in lines:
        if not any(item is fragment for item in pending) or fragment.equation or not _math_only(fragment):
            continue
        if fragment.x1 - fragment.x0 > fragment.width * .45:
            continue
        targets = [line for line in prose if column(line) == column(fragment)
                   and ((fragment.size < body_size * .85
                         and abs(line.baseline - fragment.baseline) <= body_size * .90
                         and max(line.x0 - fragment.x1, fragment.x0 - line.x1, 0) <= body_size * 2.6)
                        or (abs(line.baseline - fragment.baseline) <= body_size * .34
                            and max(line.x0 - fragment.x1, fragment.x0 - line.x1, 0) <= body_size * 1.8))
                   and min(line.y1, fragment.y1) > max(line.y0, fragment.y0)]
        if not targets:
            continue
        target = min(targets, key=lambda line: (abs(line.baseline - fragment.baseline),
                                                max(line.x0 - fragment.x1, fragment.x0 - line.x1, 0)))
        target.x0, target.y0 = min(target.x0, fragment.x0), min(target.y0, fragment.y0)
        target.x1, target.y1 = max(target.x1, fragment.x1), max(target.y1, fragment.y1)
        target.spans.extend(fragment.spans)
        target.spans.sort(key=lambda span: span["bbox"][0])
        target.text = _clean(_span_text(target.spans))
        pending = [line for line in pending if line is not fragment]
    return pending


def _mark_inline_regions(lines: list[_Line], body_size: float) -> list[_Line]:
    """Keep one continuous prose paragraph with its inline mathematical rows.

    Formula ink can overlap the previous / next physical line. Baseline-only
    paragraph rules would then split tiny math fragments into translations of
    their own. Grow only through the small normal inter-line gap, stop at real
    whitespace, headings, displays, or the other column.
    """
    split = _column_split(lines, body_size)

    def column(line: _Line) -> int:
        return 0 if split is None else -1 if line.x1 <= split else 1 if line.x0 >= split else 0

    candidates = [line for line in lines if not line.equation and _heading_level(line, body_size) is None]
    assigned = set()
    for seed in candidates:
        if id(seed) in assigned or not any("CMEX" in span.get("font", "").upper() for span in seed.spans):
            continue
        members = [seed]
        member_ids = {id(seed)}
        changed = True
        while changed:
            changed = False
            for line in candidates:
                if id(line) in assigned or id(line) in member_ids or column(line) != column(seed):
                    continue
                if any(max(line.y0 - item.y1, item.y0 - line.y1, 0) <= body_size * .45
                       for item in members):
                    top = min(line.y0, min(item.y0 for item in members))
                    bottom = max(line.y1, max(item.y1 for item in members))
                    if any(item.equation and column(item) == column(seed)
                           and top < (item.y0 + item.y1) / 2 < bottom for item in lines):
                        continue
                    members.append(line)
                    member_ids.add(id(line))
                    changed = True
        # Chart symbols and standalone displays are handled elsewhere.
        prose_words = [word for line in members for span in line.spans
                       if not re.search(r"CM|Math|Symbol|MSAM|MSBM", span.get("font", ""), re.I)
                       for word in re.findall(r"[A-Za-z]{2,}", span.get("text", ""))]
        if len(prose_words) < 5:
            continue
        for line in members:
            line.inline_region = id(seed)
        assigned.update(member_ids)
    return lines


_EQUATION_NUMBER = re.compile(r"[（(]\s*(\d+[a-z]?)\s*[）)]\s*[,.;:]?$", re.I)
_MATH_WORDS = {"arg", "min", "max", "argmin", "argmax", "sin", "cos", "tan", "log", "ln",
               "exp", "lim", "sup", "inf", "det", "diag", "rank", "tr", "Tr", "Var", "Cov", "Pr", "SVD", "ReLU"}
_RELATION = re.compile(r"[=∑∫∏≈≃≅≠≤≥∈∉∼∝←→↔⇒⇔]")


def _math_only(line: _Line) -> bool:
    """Distinguish equation fragments from explanatory prose and captions.

    Math fonts alone are insufficient: a normal sentence can contain several
    variables. Meaningful roman words form a hard boundary for region growth.
    """
    # PDF text concatenates base variables and their scripts ("x" + "t"
    # becomes "xt"). Inspect the original fonts instead of mistaking such a
    # mathematical run for a prose word.
    prose_spans = [span for span in line.spans if not re.search(
        r"CMMI|CMMIB|CMSY|CMEX|MSAM|MSBM|Math|Symbol", span.get("font", ""), re.I)]
    words = [word for span in prose_spans for word in re.findall(r"[A-Za-z]+", span.get("text", ""))]
    if not line.spans:
        words = re.findall(r"[A-Za-z]+", line.text)
    function_call = bool(_RELATION.search(line.text))
    if any(len(word) > 1 and word not in _MATH_WORDS
           and not (function_call and re.search(r"[a-z][A-Z]", word)) for word in words):
        return False
    return bool(line.equation or line.math_ratio > .18 or _RELATION.search(line.text)
                or re.fullmatch(r"[\d\s.,+−\-]+", line.text)
                or _EQUATION_NUMBER.fullmatch(line.text.strip(" ,.;:")))


def _merge_equation_regions(lines: list[_Line], body_size: float) -> list[_Line]:
    """Collect complete numbered equations before prose reading order.

    Fractions, radical bars, extension glyphs and the number of a multi-line
    display can be separate PDF blocks with unrelated baselines. Grow only
    through nearby mathematical lines in one physical column. The complete
    region then either recovers reliable TeX or falls back to one source crop;
    its fragments never become separate translation requests.
    """
    split = _column_split(lines, body_size)

    def column(line: _Line) -> str:
        if split is None:
            return "main"
        if line.x1 <= split:
            return "left"
        if line.x0 >= split:
            return "right"
        return "full"

    candidates = [line for line in lines if _math_only(line)]
    numbered = [line for line in candidates if line.equation or _EQUATION_NUMBER.search(line.text)]
    numbered_ids = {id(line) for line in numbered}
    unnumbered = [line for line in candidates if id(line) not in numbered_ids
                  and line.math_ratio > .18 and _RELATION.search(line.text)]
    seeds = sorted(numbered, key=lambda item: (item.y0, item.x0)) + sorted(
        unnumbered, key=lambda item: (item.y0, item.x0))
    assigned: set[int] = set()
    regions: list[_Line] = []
    for seed in seeds:
        if id(seed) in assigned:
            continue
        members = [seed]
        member_ids = {id(seed)}
        number = _EQUATION_NUMBER.search(seed.text)
        number = number.group(1) if number else None
        changed = True
        while changed:
            changed = False
            for line in candidates:
                if id(line) in assigned or id(line) in member_ids or column(line) != column(seed):
                    continue
                if any(not _math_only(item) and column(item) == column(seed)
                       and abs(item.baseline - line.baseline) <= body_size * .45
                       and max(item.x0 - line.x1, line.x0 - item.x1, 0) <= body_size * 2
                       for item in lines):
                    continue  # a run beside prose belongs to that inline row
                other_number = _EQUATION_NUMBER.search(line.text)
                if other_number and other_number.group(1) != number:
                    continue  # two nearby numbered displays remain distinct
                center = (line.y0 + line.y1) / 2
                distance = abs(center - (seed.y0 + seed.y1) / 2)
                if any(other is not seed and column(other) == column(seed)
                       and abs(center - (other.y0 + other.y1) / 2) < distance
                       for other in numbered):
                    continue  # do not steal the next display's unnumbered row
                if any(max(line.y0 - member.y1, member.y0 - line.y1, 0) <= body_size * .9
                       for member in members):
                    # A short prose word between displays is an actual layout
                    # separator, even if its font metrics leave only a small gap.
                    top = min(line.y0, min(member.y0 for member in members))
                    bottom = max(line.y1, max(member.y1 for member in members))
                    right = max(line.x1, max(member.x1 for member in members))
                    if any(not _math_only(item) and column(item) == column(seed)
                           and item.x0 <= right and item.y0 > top and item.y1 < bottom for item in lines):
                        continue
                    members.append(line)
                    member_ids.add(id(line))
                    changed = True
        # Parenthesized numbers in tables / figures are not enough evidence of
        # an equation. Existing deliberately marked equations remain valid.
        if not seed.equation and not any(_RELATION.search(line.text) for line in members):
            continue
        assigned.update(member_ids)
        members.sort(key=lambda item: (item.y0, item.x0))
        main = max(members, key=lambda item: len(item.text))
        count = sum(len(item.text) for item in members)
        regions.append(_Line(_join_lines([item.text for item in members]),
                             min(item.x0 for item in members), min(item.y0 for item in members),
                             max(item.x1 for item in members), max(item.y1 for item in members),
                             main.size, False, main.block, main.page, main.width, main.height,
                             baseline=main.baseline,
                             math_ratio=sum(item.math_ratio * len(item.text) for item in members) / max(count, 1),
                             equation=True, spans=[span for item in members for span in item.spans]))
    return [line for line in lines if id(line) not in assigned] + regions


def _margin_signature(line: _Line) -> str | None:
    if line.y1 < line.height * 0.10 or line.y0 > line.height * 0.90:
        if len(line.text) <= 200:
            return re.sub(r"\d+", "#", line.text.lower())
    return None


def _remove_running_margins(pages: list[list[_Line]]) -> list[list[_Line]]:
    counts: Counter[str] = Counter()
    for lines in pages:
        counts.update({_margin_signature(line) for line in lines} - {None})
    repeated = {key for key, count in counts.items() if count >= max(2, math.ceil(len(pages) * 0.5))}
    page_number = re.compile(r"^(?:page\s*)?(?:\d+|[ivxlcdm]+)(?:\s*(?:of|/)\s*\d+)?$", re.I)
    cleaned = []
    for lines in pages:
        filtered = []
        for line in lines:
            margin = line.y1 < line.height * 0.13 or line.y0 > line.height * 0.87
            if _margin_signature(line) in repeated:
                continue
            if margin and page_number.fullmatch(line.text):
                continue
            filtered.append(line)
        cleaned.append(filtered)
    return cleaned


def _body_size(pages: list[list[_Line]]) -> float:
    weights: Counter[float] = Counter()
    for lines in pages:
        for line in lines:
            if len(line.text) >= 30:
                weights[round(line.size * 2) / 2] += len(line.text)
    if not weights:
        for lines in pages:
            for line in lines:
                weights[line.size] += len(line.text)
    return weights.most_common(1)[0][0] if weights else 11.0


def _column_split(lines: list[_Line], body_size: float) -> float | None:
    if not lines:
        return None
    width = lines[0].width
    prose = [line for line in lines if not line.structured and len(line.text) >= 22 and line.size >= body_size * 0.8
             and width * .20 < line.x1 - line.x0 < width * 0.60 and not line.equation
             # Right-aligned pseudocode comments are a second text box, not
             # the beginning of another prose column.
             and not re.match(r"^[▷▹]", line.text.lstrip())]
    # The geometric center of a page need not be the center of its gutter.
    # In particular, asymmetric margins can put a fixed percentage inside a
    # text column. Use spaces between observed text edges as candidates too;
    # this keeps slightly longer wrapped lines from becoming "full-width" rows.
    edges = sorted({round(edge, 2) for line in prose for edge in (line.x0, line.x1)
                    if width * .40 <= edge <= width * .60})
    candidates = {width * fraction for fraction in (.45, .475, .5, .525, .55)}
    candidates.update((left + right) / 2 for left, right in zip(edges, edges[1:])
                      if right - left >= max(4, body_size * .5))
    best = None
    for split in sorted(candidates):
        left = [line for line in prose if line.x1 <= split - 4]
        right = [line for line in prose if line.x0 >= split + 4]
        if len(left) < 2 or len(right) < 2:
            continue
        # Evidence must occupy the same vertical region; two unrelated narrow
        # text boxes at top and bottom do not form two columns.
        overlap = min(max(line.y1 for line in left), max(line.y1 for line in right)) - max(
            min(line.y0 for line in left), min(line.y0 for line in right))
        if overlap < body_size * 2:
            continue
        # Include the surrounding wrapped lines: gaps inside a justified
        # bibliography entry can otherwise resemble a short two-column band
        # while its immediately preceding/following prose crosses the gutter.
        top = max(min(line.y0 for line in left), min(line.y0 for line in right)) - body_size * 2
        bottom = min(max(line.y1 for line in left), max(line.y1 for line in right)) + body_size * 2
        spanning_prose = [line for line in lines if line.x0 < split - 4 and line.x1 > split + 4
                          and top <= (line.y0 + line.y1) / 2 <= bottom
                          and len(line.text) >= 40 and line.size >= body_size * .8 and not line.equation]
        # Short sentence endings and centered equations are not a gutter. A
        # genuine two-column text region has almost no prose crossing it;
        # full-width titles above that region remain compatible with columns.
        if len(spanning_prose) >= max(2, (len(left) + len(right)) * .25):
            continue
        score = min(len(left), len(right)) * 3 + len(left) + len(right)
        candidate = (score, -abs(split / width - 0.5), split)
        if best is None or candidate > best:
            best = candidate
    return best[2] if best else None


def _reading_order(lines: list[_Line], body_size: float) -> list[_Line]:
    split = _column_split(lines, body_size)
    if split is None:
        return sorted(lines, key=lambda line: (round(line.y0, 1), line.x0))
    spanning = sorted([line for line in lines if line.x0 < split < line.x1], key=lambda line: (line.y0, line.x0))
    pending = [line for line in lines if not (line.x0 < split < line.x1)]
    ordered = []

    def emit_band(band: list[_Line], number: int) -> None:
        for side, predicate in (("left", lambda line: line.x0 < split), ("right", lambda line: line.x0 >= split)):
            for line in sorted((line for line in band if predicate(line)), key=lambda line: (line.y0, line.x0)):
                line.flow = f"{number}-{side}"
                ordered.append(line)

    for index, span in enumerate(spanning):
        preceding = [line for line in pending if (line.y0 + line.y1) / 2 < span.y0]
        emit_band(preceding, index)
        preceding_ids = {id(line) for line in preceding}
        pending = [line for line in pending if id(line) not in preceding_ids]
        span.flow = "full-width"
        ordered.append(span)
    emit_band(pending, len(spanning))
    return ordered


def _heading_level(line: _Line, body_size: float) -> int | None:
    text = line.text.strip()
    if line.equation or line.structured:
        return None
    # Bold table cells and chart tick values are not section titles.
    if not any(char.isalpha() for char in text):
        return None
    if len(text) > 200 or len(text.split()) > 22:
        return None
    # Captions and list entries should remain prose even when bold.
    if re.match(r"^(?:figure|fig\.?|table|equation|algorithm)\s*\d", text, re.I):
        return None
    if re.match(r"^[•●▪▷▹]", text):
        return None
    if _COMMON_HEADINGS.fullmatch(text):
        return 1
    numbered = _NUMBERED_HEADING.fullmatch(text)
    if numbered and not re.search(r"[.!?;]$", text):
        words = numbered.group(2).split()
        title_words = sum(word[0].isupper() for word in words if word)
        conventional = bool(_COMMON_HEADINGS.fullmatch(numbered.group(2)))
        title_case = len(words) <= 12 and title_words / max(1, len(words)) >= 0.6
        if line.bold or line.size >= body_size * 1.05 or text.isupper() or conventional or title_case:
            return min(numbered.group(1).count(".") + 1, 6)
    if line.size >= body_size * 1.22 and not text.endswith((".", ";", ",")):
        return 1 if line.size >= body_size * 1.5 else 2
    if line.bold and len(text) < 110 and not text.endswith((".", ";", ",", ":")):
        # Bold lead-ins and wrapped emphasis are part of a sentence. A broken
        # word or lower-case continuation cannot become a standalone section.
        if text.endswith("-") or re.match(r"^[a-z]", text):
            return None
        if line.size <= body_size * 1.1 and re.search(r"[a-z0-9][.!?]\s+[A-Z]", text):
            # A body-sized bold lead-in can share a PDF line with the start
            # of the next sentence. Do not turn that line into a heading.
            return None
        if numbered:
            return min(numbered.group(1).count(".") + 1, 6)
        if len(text.split()) <= 12:
            return 2
    return None


def _join_lines(texts: list[str]) -> str:
    joined = texts[0]
    for text in texts[1:]:
        # A word broken across a PDF line is common in two-column papers.
        # Preserve already hyphenated terms and frequent compound prefixes.
        tail = joined.rsplit(" ", 1)[-1]
        prefix = tail[:-1].lower()
        if joined.endswith("\u00ad"):
            joined = joined[:-1] + text
        elif (re.search(r"[A-Za-z]{2,}-$", joined) and re.match(r"^[a-z]", text)
                and "-" not in tail[:-1] and prefix not in {"well", "self", "non", "cross", "high", "low"}):
            joined = joined[:-1] + text
        elif re.search(r"[\u3400-\u9fff]$", joined) and re.match(r"^[\u3400-\u9fff]", text):
            joined += text
        else:
            joined += " " + text
    return joined.replace("\u00ad", "").strip()


def _assemble_page(lines: list[_Line], body_size: float, structures: list[dict] | None = None) -> list[dict]:
    paragraphs = []
    group: list[_Line] = []
    group_level: int | None = None
    group_equation = False
    lines = _merge_equation_regions(lines, body_size)
    lines = _mark_inline_regions(_attach_inline_math_fragments(lines, body_size), body_size)
    for block in structures or []:
        x0, y0, x1, y1 = block["bbox"]
        lines.append(_Line(block["text"], x0, y0, x1, y1, body_size, False, -1,
                           block["page"], block["page_width"], block["page_height"],
                           baseline=y1, structured=block))
    ordered = _reading_order(lines, body_size)
    flow_right: dict[str, float] = defaultdict(float)
    for line in ordered:
        flow_right[line.flow] = max(flow_right[line.flow], line.x1)

    def flush() -> None:
        nonlocal group
        if group:
            if group[0].inline_region is not None and all(
                    line.inline_region == group[0].inline_region for line in group):
                group.sort(key=lambda line: (round(line.baseline * 2) / 2, line.x0))
            paragraph = {"text": _join_lines([line.text for line in group]),
                               "page": group[0].page,
                               "kind": "heading" if group_level else "equation" if group_equation else "paragraph",
                               "level": group_level or 0,
                               "bbox": [round(min(line.x0 for line in group), 3),
                                        round(min(line.y0 for line in group), 3),
                                        round(max(line.x1 for line in group), 3),
                                        round(max(line.y1 for line in group), 3)],
                               "page_width": group[0].width, "page_height": group[0].height,
                               "_first_flow": group[0].flow, "_last_flow": group[-1].flow}
            paragraph = recover_paragraph(paragraph, group)
            expand_equation_source(paragraph, group, ordered, body_size)
            paragraphs.append(paragraph)
        group = []

    for line in ordered:
        if line.structured:
            flush()
            paragraphs.append(line.structured)
            continue
        level = _heading_level(line, body_size)
        if not group:
            group, group_level, group_equation = [line], level, line.equation
            continue
        previous = group[-1]
        gap = line.y0 - previous.y0
        flow_changed = previous.flow != line.flow
        if re.match(r"^[•●▪▷▹]\s*", line.text):
            merge = False
        elif (not flow_changed and previous.inline_region is not None
                and previous.inline_region == line.inline_region):
            merge = True
        elif group_equation or line.equation:
            # Limits under an operator may be separate small text runs. Keep
            # them with the formula and its number, without absorbing prose.
            small_math = (line.math_ratio > .35 and line.size < body_size * .90
                          and line.x0 >= group[0].x0 - body_size
                          and line.x1 <= max(item.x1 for item in group) + body_size)
            preceding_limit = (line.equation and all(item.math_ratio > .35 and item.size < body_size * .90 for item in group))
            merge = (not flow_changed and -body_size < gap < body_size * 1.8
                     and not (group_equation and line.equation)
                     and ((group_equation and small_math) or preceding_limit))
        elif group_level is not None or level is not None:
            # Two-line paper titles / section titles may share one block.
            merge = (group_level is not None and level == group_level and line.block == previous.block
                     and not flow_changed and 0 < gap < max(line.size, previous.size) * 1.7
                     and not _NUMBERED_HEADING.match(line.text) and not _COMMON_HEADINGS.fullmatch(line.text)
                     and abs(line.size - previous.size) < 0.8)
        else:
            indentation = line.x0 - group[0].x0
            # Raised radicals / accents can enlarge a line's top bbox without
            # changing the baseline spacing of an ordinary wrapped paragraph.
            baseline_gap = line.baseline - previous.baseline if line.baseline and previous.baseline else gap
            close = -previous.size * 0.2 <= baseline_gap <= max(previous.size, line.size) * 1.75
            same_block = line.block == previous.block
            full_previous_line = previous.x1 >= flow_right[previous.flow] - max(24, previous.size * 3)
            # Separate PDF blocks usually denote paragraphs. Recover PDFs that
            # emit one block per line only when geometry shows a wrapped line.
            merge = (not flow_changed and close and abs(line.size - previous.size) <= body_size * 0.22
                     and indentation < body_size * 1.0
                     and (same_block or (full_previous_line and abs(indentation) < body_size * 0.75)))
        if merge:
            group.append(line)
            group_equation = group_equation or line.equation
        else:
            flush()
            group, group_level, group_equation = [line], level, line.equation
    flush()
    return paragraphs


def _chunk_paragraph(paragraph: dict) -> list[dict]:
    if paragraph.get("kind") in {"algorithm", "table", "equation"} or paragraph.get("inline_math"):
        return [paragraph]
    remaining = paragraph["text"]
    result = []
    while len(remaining) > MAX_PARAGRAPH_CHARS:
        window = remaining[:MAX_PARAGRAPH_CHARS + 1]
        candidates = [match.end() for match in re.finditer(r"[.!?。！？]\s+", window)
                      if match.end() > MAX_PARAGRAPH_CHARS // 2]
        if not candidates:
            # This is a soft target, not a reason to break a sentence or word.
            # A longer complete sentence remains one translation unit.
            next_end = re.search(r"[.!?。！？](?:\s+|$)", remaining[MAX_PARAGRAPH_CHARS:])
            if next_end:
                cut = MAX_PARAGRAPH_CHARS + next_end.end()
            else:
                break
        else:
            cut = candidates[-1]
        if cut <= 0:
            cut = MAX_PARAGRAPH_CHARS
        result.append({**paragraph, "text": remaining[:cut].strip()})
        remaining = remaining[cut:].strip()
    if remaining:
        result.append({**paragraph, "text": remaining})
    return result


def _title(metadata_title: str | None, pages: list[list[_Line]], body_size: float, filename: str) -> str:
    title = _clean(metadata_title or "")
    generic = re.compile(r"^(?:untitled(?: document)?|document\d*|microsoft (?:word|powerpoint).*|latex.*|pdf)$", re.I)
    if title and len(title) <= 300 and not generic.fullmatch(title) and not re.search(r"[\\/]|\.docx?$", title, re.I):
        return title
    first = pages[0] if pages else []
    candidates = [line for line in first if line.y0 < line.height * 0.40
                  and 8 <= len(line.text) <= 250 and line.size >= body_size * 1.22]
    if candidates:
        largest = max(line.size for line in candidates)
        title_lines = sorted([line for line in candidates if line.size >= largest * 0.92], key=lambda line: line.y0)
        contiguous = [title_lines[0]]
        for line in title_lines[1:]:
            if line.y0 - contiguous[-1].y0 < largest * 2.0:
                contiguous.append(line)
            else:
                break
        return _join_lines([line.text for line in contiguous])[:300]
    return PureWindowsPath(filename).stem or "未命名论文"


def _normalized_heading(text: str) -> str:
    text = re.sub(r"^\s*\d+(?:\.\d+)*[.\s]+", "", text.lower())
    return re.sub(r"[^\w\u3400-\u9fff]", "", text)


def _build_outline(paragraphs: list[dict], toc: list[list]) -> list[dict]:
    outline = []
    if toc:
        used: set[str] = set()
        for entry in toc:
            level, title, page = entry[:3]
            if not title or page < 1:
                continue
            on_page = [paragraph for paragraph in paragraphs if paragraph["page"] == page]
            if not on_page:
                continue
            key = _normalized_heading(str(title))
            matches = [paragraph for paragraph in on_page if key and (
                key == _normalized_heading(paragraph["text"]) or (
                    len(key) >= 6 and len(paragraph["text"]) < 220
                    and (key in _normalized_heading(paragraph["text"])
                         or _normalized_heading(paragraph["text"]) in key))) ]
            target = next((item for item in matches if item["id"] not in used), on_page[0])
            used.add(target["id"])
            if matches:
                target["kind"] = "heading"
                target["level"] = max(1, min(int(level), 6))
            outline.append({"id": f"section-{len(outline) + 1}", "title": _clean(str(title)),
                            "level": max(1, min(int(level), 6)), "page": page, "paragraph_id": target["id"]})
    if not outline:
        for paragraph in paragraphs:
            if paragraph["kind"] == "heading":
                outline.append({"id": f"section-{len(outline) + 1}", "title": paragraph["text"],
                                "level": paragraph["level"], "page": paragraph["page"],
                                "paragraph_id": paragraph["id"]})
    if not outline:
        outline.append({"id": "section-1", "title": "正文", "level": 1,
                        "page": paragraphs[0]["page"], "paragraph_id": paragraphs[0]["id"]})
    section_at: dict[str, str] = {}
    for item in outline:
        section_at.setdefault(item["paragraph_id"], item["id"])
    active = outline[0]["id"]
    for paragraph in paragraphs:
        active = section_at.get(paragraph["id"], active)
        paragraph["section_id"] = active
    return outline


@synchronized_pdf
def extract_pdf(data: bytes, filename: str) -> dict:
    """Read an uploaded PDF, returning serializable paragraph and outline data.

    Raises :class:`PDFExtractionError` for invalid, encrypted, image-only, or
    oversized documents. Some image-only pages produce a warning if other pages
    still contain selectable text. The source PDF is never modified.
    """
    if not data:
        raise PDFExtractionError("PDF 文件为空，请选择有效的论文 PDF。")
    try:
        document = fitz.open(stream=data, filetype="pdf")
    except Exception as exc:
        raise PDFExtractionError("无法读取 PDF，文件可能已损坏，或不是有效的 PDF 文件。") from exc
    try:
        if document.needs_pass:
            raise PDFExtractionError("该 PDF 已加密，请先解除密码保护，再重新导入。")
        page_count = document.page_count
        if not page_count:
            raise PDFExtractionError("PDF 没有可读取的页面。")
        if page_count > MAX_PAGES:
            raise PDFExtractionError(f"PDF 共 {page_count} 页，超出 {MAX_PAGES} 页限制，请拆分后导入。")
        pages = []
        removed_line_numbers = []
        text_chars = 0
        empty_pages = []
        for index, page in enumerate(document):
            lines = _read_lines(page, index + 1, removed_line_numbers)
            text_chars += sum(len(line.text) for line in lines)
            if text_chars > MAX_TEXT_CHARS:
                raise PDFExtractionError("PDF 文本内容过长，请按章节拆分后导入。")
            if not lines:
                empty_pages.append(index + 1)
            pages.append(lines)
        pages = _remove_running_margins(pages)
        if not any(pages):
            raise PDFExtractionError("未检测到可选择的文字。这可能是扫描版 PDF；本程序暂不支持 OCR，请导入带文字层的 PDF。")
        body_size = _body_size(pages)
        paragraphs = []
        assembled = []
        for index, lines in enumerate(pages):
            structures, consumed = extract_structures(document[index], lines, body_size)
            assembled.extend(_assemble_page([line for line in lines if id(line) not in consumed], body_size, structures))
        for paragraph in stitch_sentences(assembled, body_size):
            for chunk in _chunk_paragraph(paragraph):
                paragraphs.append({**chunk, "id": f"p-{len(paragraphs) + 1}",
                                   "translation": None, "error": None, "section_id": ""})
        if not paragraphs:
            raise PDFExtractionError("未提取到可读取的正文，请确认 PDF 包含文字层。")
        warnings = []
        if empty_pages:
            page_list = "、".join(map(str, empty_pages[:20])) + (" 等" if len(empty_pages) > 20 else "")
            warnings.append(f"第 {page_list} 页未检测到文字，可能为图片页或扫描页；这些页面未进行 OCR。")
        replacement_chars = sum(paragraph["text"].count("\ufffd") for paragraph in paragraphs)
        if replacement_chars > max(10, text_chars * 0.01):
            warnings.append("部分文字的 PDF 字体编码可能异常，请对照原 PDF 检查提取结果。")
        title = _title((document.metadata or {}).get("title"), pages, body_size, filename)
        outline = _build_outline(paragraphs, document.get_toc(simple=True))
        return {"title": title, "page_count": page_count, "paragraphs": paragraphs, "parse_version": PARSE_VERSION,
                "outline": outline, "warnings": warnings, "removed_line_numbers": removed_line_numbers}
    except PDFExtractionError:
        raise
    except Exception as exc:
        raise PDFExtractionError("读取 PDF 文字时出现错误，请尝试重新保存 PDF 后导入。") from exc
    finally:
        document.close()
