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
from .math_recovery import expand_equation_source, keep_span_characters, recover_paragraph


MAX_PAGES = 500
MAX_TEXT_CHARS = 3_000_000
MAX_PARAGRAPH_CHARS = 4500
PARSE_VERSION = 3


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


def _read_lines(page: fitz.Page, page_number: int) -> list[_Line]:
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
    return _merge_line_fragments(lines)


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
                break
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
    prose = [line for line in lines if len(line.text) >= 22 and line.size >= body_size * 0.8
             and width * .20 < line.x1 - line.x0 < width * 0.60 and not line.equation]
    best = None
    for fraction in (0.45, 0.475, 0.5, 0.525, 0.55):
        split = width * fraction
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
        top = max(min(line.y0 for line in left), min(line.y0 for line in right))
        bottom = min(max(line.y1 for line in left), max(line.y1 for line in right))
        spanning_prose = [line for line in lines if line.x0 < split - 4 and line.x1 > split + 4
                          and top <= (line.y0 + line.y1) / 2 <= bottom
                          and len(line.text) >= 40 and line.size >= body_size * .8 and not line.equation]
        # Short sentence endings and centered equations are not a gutter. A
        # genuine two-column text region has almost no prose crossing it;
        # full-width titles above that region remain compatible with columns.
        if len(spanning_prose) >= max(2, (len(left) + len(right)) * .25):
            continue
        score = min(len(left), len(right)) * 3 + len(left) + len(right)
        candidate = (score, -abs(fraction - 0.5), split)
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
    if line.equation:
        return None
    # Bold table cells and chart tick values are not section titles.
    if not any(char.isalpha() for char in text):
        return None
    if len(text) > 200 or len(text.split()) > 22:
        return None
    # Captions and list entries should remain prose even when bold.
    if re.match(r"^(?:figure|fig\.?|table|equation|algorithm)\s*\d", text, re.I):
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


def _assemble_page(lines: list[_Line], body_size: float) -> list[dict]:
    paragraphs = []
    group: list[_Line] = []
    group_level: int | None = None
    group_equation = False
    ordered = _reading_order(lines, body_size)
    flow_right: dict[str, float] = defaultdict(float)
    for line in ordered:
        flow_right[line.flow] = max(flow_right[line.flow], line.x1)

    def flush() -> None:
        nonlocal group
        if group:
            paragraph = {"text": _join_lines([line.text for line in group]),
                               "page": group[0].page,
                               "kind": "heading" if group_level else "equation" if group_equation else "paragraph",
                               "level": group_level or 0,
                               "bbox": [round(min(line.x0 for line in group), 3),
                                        round(min(line.y0 for line in group), 3),
                                        round(max(line.x1 for line in group), 3),
                                        round(max(line.y1 for line in group), 3)],
                               "page_width": group[0].width, "page_height": group[0].height}
            paragraph = recover_paragraph(paragraph, group)
            expand_equation_source(paragraph, group, ordered, body_size)
            paragraphs.append(paragraph)
        group = []

    for line in ordered:
        level = _heading_level(line, body_size)
        if not group:
            group, group_level, group_equation = [line], level, line.equation
            continue
        previous = group[-1]
        gap = line.y0 - previous.y0
        flow_changed = previous.flow != line.flow
        if group_equation or line.equation:
            # Limits under an operator may be separate small text runs. Keep
            # them with the formula and its number, without absorbing prose.
            small_math = (line.math_ratio > .35 and line.size < body_size * .90
                          and line.x0 >= group[0].x0 - body_size
                          and line.x1 <= max(item.x1 for item in group) + body_size)
            preceding_limit = (line.equation and all(item.math_ratio > .35 and item.size < body_size * .90 for item in group))
            merge = (not flow_changed and -body_size < gap < body_size * 1.8
                     and ((group_equation and (line.equation or small_math)) or preceding_limit))
        elif group_level is not None or level is not None:
            # Two-line paper titles / section titles may share one block.
            merge = (group_level is not None and level == group_level and line.block == previous.block
                     and not flow_changed and 0 < gap < max(line.size, previous.size) * 1.7
                     and not _NUMBERED_HEADING.match(line.text) and not _COMMON_HEADINGS.fullmatch(line.text)
                     and abs(line.size - previous.size) < 0.8)
        else:
            indentation = line.x0 - group[0].x0
            close = -previous.size * 0.2 <= gap <= max(previous.size, line.size) * 1.75
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
    remaining = paragraph["text"]
    result = []
    while len(remaining) > MAX_PARAGRAPH_CHARS:
        window = remaining[:MAX_PARAGRAPH_CHARS + 1]
        candidates = [match.end() for match in re.finditer(r"[.!?。！？]\s+", window)
                      if match.end() > MAX_PARAGRAPH_CHARS // 2]
        cut = candidates[-1] if candidates else window.rfind(" ", MAX_PARAGRAPH_CHARS // 2, MAX_PARAGRAPH_CHARS)
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
        text_chars = 0
        empty_pages = []
        for index, page in enumerate(document):
            lines = _read_lines(page, index + 1)
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
        for lines in pages:
            for paragraph in _assemble_page(lines, body_size):
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
                "outline": outline, "warnings": warnings}
    except PDFExtractionError:
        raise
    except Exception as exc:
        raise PDFExtractionError("读取 PDF 文字时出现错误，请尝试重新保存 PDF 后导入。") from exc
    finally:
        document.close()
