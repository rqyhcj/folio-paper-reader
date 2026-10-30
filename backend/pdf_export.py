"""Portable bilingual PDF export using MuPDF's bundled, embedded fonts.

Paper text never becomes executable HTML. Mathematical expressions are drawn
as local vector paths; no Windows fonts, remote resources or TeX installation
are needed.
"""
from __future__ import annotations

from collections import defaultdict
from contextlib import ExitStack
from pathlib import Path
import re

import fitz
from .pdf_runtime import synchronized_pdf
from .inline_math_export import text_with_math
from .math_typeset import split_math, render_formula
from .structured_content import rows_for


PAGE_WIDTH, PAGE_HEIGHT = fitz.paper_size("a4")
MARGIN = 54.0
BODY_TOP = 74.0
BODY_BOTTOM = PAGE_HEIGHT - 64.0
BODY_WIDTH = PAGE_WIDTH - 2 * MARGIN
INK = (0.13, 0.18, 0.19)
TRANSLATION = (0.22, 0.35, 0.35)
MUTED = (0.42, 0.47, 0.47)


def _clean(value: object) -> str:
    """Keep actual text and line breaks; remove nonprinting PDF control bytes."""
    return "".join(c for c in str(value or "").replace("\r\n", "\n").replace("\r", "\n")
                   if c in "\n\t" or ord(c) >= 32).replace("\x7f", "")


def _wrap(text: str, font: fitz.Font, size: float, width: float) -> list[str]:
    """Wrap words normally, with a character fallback for CJK and huge tokens."""
    lines = []
    advances: dict[str, float] = {}

    def measure(char: str) -> float:
        if char not in advances:
            advances[char] = font.text_length(char, fontsize=size)
        return advances[char]

    for logical_line in text.expandtabs(4).split("\n"):
        current, used = "", 0.0
        for token in re.findall(r"\S+|[^\S\n]+", logical_line):
            if token.isspace():
                if current:
                    current += " "
                    used += measure(" ")
                continue
            token_width = sum(measure(char) for char in token)
            if current and used + token_width > width:
                lines.append(current.rstrip())
                current, used = "", 0.0
            # A CJK paragraph or a long URL may have no spaces at all.
            for char in token:
                char_width = measure(char)
                if current and used + char_width > width:
                    lines.append(current.rstrip())
                    current, used = "", 0.0
                current += char
                used += char_width
        lines.append(current.rstrip())
    return lines


def _cell_lines(text, font, size, width, color):
    """Wrap table cells without splitting a delimited formula or CJK word run."""
    lines, current, used, height = [], "", 0.0, 17.0

    def append(token, advance, extent=17):
        nonlocal current, used, height
        if current and used + advance > width:
            lines.append((current.rstrip(), height))
            current, used, height = "", 0.0, 17.0
        if not current and token.isspace():
            return
        current += token
        used += advance
        height = max(height, extent)

    for part in split_math(str(text)):
        if part.math:
            formula = render_formula(part.text, size, False, tuple(color))
            if formula:
                scale = min(1, width / formula.width)
                append(r"\(" + part.text + r"\)", formula.width * scale, formula.height * scale + 3)
                continue
            value = "[公式未能排版]"
        else:
            value = part.text
        for token in re.findall(r"\n|[^\S\n]+|[\u3400-\u9fff]|[^\s\u3400-\u9fff]+", value):
            if token == "\n":
                lines.append((current.rstrip(), height))
                current, used, height = "", 0.0, 17.0
                continue
            advance = font.text_length(token, fontsize=size)
            if advance > width:
                for char in token:
                    append(char, font.text_length(char, fontsize=size))
            else:
                append(token, advance)
    if current or not lines:
        lines.append((current.rstrip(), height))
    return lines


class _Layout:
    def __init__(self, document: fitz.Document, math_sources: ExitStack):
        self.document = document
        self.math_sources = math_sources
        self.math_documents: dict[bytes, fitz.Document] = {}
        # TextWriter embeds these bundled fonts (including its CJK fallback).
        self.cjk = fitz.Font("cjk")
        self.english = fitz.Font("tiro")
        self.bold = fitz.Font("hebo")
        self.y = BODY_TOP
        self.page = document.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
        self.bookmarks: list[list] = []

    def next_page(self):
        self.page = self.document.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
        self.y = BODY_TOP

    def ensure(self, height: float):
        if self.y + height > BODY_BOTTOM and self.y > BODY_TOP:
            self.next_page()

    def formula(self, formula, rect):
        source = self.math_documents.get(formula.pdf)
        if source is None:
            source = self.math_sources.enter_context(fitz.open(stream=formula.pdf, filetype="pdf"))
            self.math_documents[formula.pdf] = source
        # Reusing the source Document lets MuPDF reuse its grafted vector XObject
        # when the same expression appears in both languages or repeated lines.
        self.page.show_pdf_page(rect, source, 0)

    def text(self, value: str, *, font: fitz.Font | None = None, size=11.2,
             leading=18.0, color=INK, after=0.0, left=MARGIN, width=BODY_WIDTH):
        font = font or self.cjk
        parts = split_math(value)
        if any(part.math for part in parts):
            self.math_text(parts, font=font, size=size, leading=leading, color=color, after=after, left=left, width=width)
            return
        lines = _wrap(value, font, size, width)
        writer = fitz.TextWriter(self.page.rect)
        has_text = False
        for line in lines:
            if self.y + leading > BODY_BOTTOM:
                if has_text:
                    writer.write_text(self.page, color=color)
                self.next_page()
                writer = fitz.TextWriter(self.page.rect)
                has_text = False
            if line:
                writer.append((left, self.y + font.ascender * size), line,
                              font=font, fontsize=size)
                has_text = True
            self.y += leading
        if has_text:
            writer.write_text(self.page, color=color)
        self.y += after

    def math_text(self, parts, *, font, size, leading, color, after, left=MARGIN, width=BODY_WIDTH):
        """Wrap prose and atomic formula boxes on a shared, measured baseline."""
        runs = []
        used = 0.0

        def flush(force=False):
            nonlocal runs, used
            while runs and runs[-1][0] == "space":
                runs.pop()
            if not runs:
                if force:
                    self.ensure(leading)
                    self.y += leading
                used = 0
                return
            ascent = max([font.ascender * size] + [run[3] for run in runs])
            descent = max([-font.descender * size] + [run[4] for run in runs])
            height = max(leading, ascent + descent + 2)
            self.ensure(height)
            baseline = self.y + ascent
            writer = fitz.TextWriter(self.page.rect)
            text_pending = False
            x = left
            buffered = ""
            buffer_x = x
            for kind, content, width, up, down in runs:
                if kind == "formula":
                    if buffered:
                        writer.append((buffer_x, baseline), buffered, font=font, fontsize=size)
                        text_pending = True
                        buffered = ""
                    self.formula(content, fitz.Rect(x, baseline - up, x + width, baseline + down))
                elif content:
                    if not buffered:
                        buffer_x = x
                    buffered += content
                x += width
            if buffered:
                writer.append((buffer_x, baseline), buffered, font=font, fontsize=size)
                text_pending = True
            if text_pending:
                writer.write_text(self.page, color=color)
            self.y += height
            runs, used = [], 0.0

        def add(kind, content, run_width, up=0.0, down=0.0):
            nonlocal used
            if runs and used + run_width > width:
                flush()
            if kind == "space" and not runs:
                return
            runs.append((kind, content, run_width, up, down))
            used += run_width

        for part in parts:
            if part.math:
                formula = render_formula(part.text, float(size), part.display, tuple(color))
                if formula is None:
                    # Preserve an explicit, readable failure rather than printing
                    # unsupported commands as though they were a valid formula.
                    notice = "〔公式未能排版，请查看原 PDF〕"
                    for char in notice:
                        add("text", char, font.text_length(char, fontsize=size))
                    continue
                scale = min(1.0, width / formula.width,
                            (BODY_BOTTOM - BODY_TOP - 10) / formula.height)
                formula_width, height = formula.width * scale, formula.height * scale
                ascent, descent = formula.ascent * scale, formula.descent * scale
                if part.display:
                    flush()
                    self.ensure(height + 10)
                    x = left + (width - formula_width) / 2
                    self.formula(formula, fitz.Rect(x, self.y + 3, x + formula_width, self.y + 3 + height))
                    self.y += height + 10
                else:
                    add("formula", formula, formula_width, ascent, descent)
                continue
            for token in re.findall(r"\n|[^\S\n]+|[^\s]+", part.text.expandtabs(4)):
                if token == "\n":
                    flush(force=bool(runs))
                elif token.isspace():
                    add("space", " ", font.text_length(" ", fontsize=size))
                else:
                    token_width = font.text_length(token, fontsize=size)
                    if token_width > width or re.search(r"[\u3400-\u9fff]", token):
                        for char in token:
                            add("text", char, font.text_length(char, fontsize=size))
                    else:
                        add("text", token, token_width)
        flush()
        self.y += after

    def structure(self, paragraph, *, translated=False):
        rows = rows_for(paragraph, translated)
        if not rows:
            return
        color = TRANSLATION if translated else INK
        if paragraph["kind"] == "algorithm":
            lines = paragraph.get("translated_algorithm_lines" if translated else "algorithm_lines")
            self.ensure(38)
            self.page.draw_line((MARGIN, self.y), (MARGIN + BODY_WIDTH, self.y), color=MUTED, width=.6)
            self.y += 7
            for item in lines:
                indent = min(20, max(0, int(item.get("indent", 0)))) * 10
                self.text(str(item.get("text", "")), font=self.cjk if translated else self.english,
                          size=10.3, leading=16, color=color, left=MARGIN + indent, width=BODY_WIDTH-indent)
            # A step may finish exactly at the body boundary after a page break.
            # Keep its closing rule on that page without adding an empty page.
            self.y = min(self.y + 5, BODY_BOTTOM)
            self.page.draw_line((MARGIN, self.y), (MARGIN + BODY_WIDTH, self.y), color=MUTED, width=.6)
            self.y += 14
            return
        columns = max(map(len, rows))
        if not columns:
            return
        cell_width = BODY_WIDTH / columns
        font, size = self.cjk if translated else self.english, min(9.2, max(3.5, cell_width / 8))
        padding = min(6, cell_width * .1)
        for row in rows:
            cells = [_cell_lines(cell, font, size, cell_width - padding * 2, color) for cell in row]
            count = max([1] + [len(cell) for cell in cells])
            for index in range(count):
                height = max([17] + [cell[index][1] for cell in cells if index < len(cell)]) + 8
                self.ensure(height)
                top = self.y
                for column in range(columns):
                    left = MARGIN + column * cell_width
                    self.page.draw_rect(fitz.Rect(left, top, left + cell_width, top + height),
                                        color=(.78, .82, .82), width=.35)
                    if column < len(cells) and index < len(cells[column]):
                        self.y = top + 4
                        self.text(cells[column][index][0], font=font, size=size, leading=17,
                                  color=color, left=left + padding, width=cell_width - padding * 2)
                self.y = top + height
        self.y += 16

    def figure(self, figure: dict, path: Path):
        # Use actual decoded dimensions, not untrusted stored width/height.
        image = fitz.Pixmap(str(path))
        if image.width <= 0 or image.height <= 0:
            return
        caption = _clean(figure.get("caption")) or f"原文插图 · 第 {figure.get('page', '?')} 页"
        caption_lines = _wrap(caption, self.cjk, 9.0, BODY_WIDTH)
        # Keep an ordinary caption with its image. Very long captions flow on.
        caption_reserve = min(len(caption_lines), 5) * 14.0 + 16.0
        available_height = BODY_BOTTOM - BODY_TOP - caption_reserve
        scale = min(BODY_WIDTH / image.width, available_height / image.height)
        width, height = image.width * scale, image.height * scale
        self.ensure(height + caption_reserve)
        x = MARGIN + (BODY_WIDTH - width) / 2
        rect = fitz.Rect(x, self.y, x + width, self.y + height)
        self.page.insert_image(rect, pixmap=image, keep_proportion=True)
        self.y += height + 8
        self.text(caption, size=9.0, leading=14.0, color=MUTED, after=18)

    def math_piece(self, path: Path):
        """Keep small inline glyphs near body size instead of page-width zoom."""
        image = fitz.Pixmap(str(path))
        scale = min(.575, BODY_WIDTH / image.width,
                    (BODY_BOTTOM - BODY_TOP) / image.height)
        width, height = image.width * scale, image.height * scale
        self.ensure(height + 8)
        self.page.insert_image(fitz.Rect(MARGIN, self.y, MARGIN + width, self.y + height),
                               pixmap=image, keep_proportion=True)
        self.y += height + 8

    def furniture(self):
        total = self.document.page_count
        for number, page in enumerate(self.document, 1):
            writer = fitz.TextWriter(page.rect)
            writer.append((MARGIN, 39), "纸间 · Folio  /  英中对照阅读",
                          font=self.cjk, fontsize=8.5)
            page.draw_line((MARGIN, 51), (PAGE_WIDTH - MARGIN, 51),
                           color=(0.83, 0.87, 0.86), width=0.6)
            label = f"{number} / {total}"
            label_width = self.cjk.text_length(label, fontsize=8.5)
            writer.append(((PAGE_WIDTH - label_width) / 2, PAGE_HEIGHT - 29),
                          label, font=self.cjk, fontsize=8.5)
            writer.write_text(page, color=MUTED)


@synchronized_pdf
def export_pdf(paper: dict, figure_paths: dict[str, Path] | None = None) -> bytes:
    """Return a complete A4 PDF; every English paragraph precedes its Chinese.

    Only explicitly supplied local image paths are read. A paper's URLs and text
    cannot trigger network requests, HTML evaluation, or arbitrary file loads.
    Missing image files are represented by a readable notice, never silently
    treated as successfully exported. The caller retains ownership of inputs.
    """
    figure_paths = figure_paths or {}
    title = _clean(paper.get("title")) or "未命名论文"
    with fitz.open() as document, ExitStack() as math_sources:
        layout = _Layout(document, math_sources)
        layout.text("BILINGUAL PAPER", font=layout.bold, size=8.5, leading=15,
                    color=TRANSLATION, after=10)
        layout.text(title, font=layout.english, size=23, leading=31, after=14)
        layout.text("英文原文与中文译文逐段对照", size=9.5, leading=17,
                    color=MUTED, after=21)
        figures = list(paper.get("figures") or [])
        before_paragraph: dict[str, list[dict]] = defaultdict(list)
        after_paragraph: dict[str, list[dict]] = defaultdict(list)
        for figure in figures:
            before = figure.get("before_paragraph_id")
            if before:
                before_paragraph[str(before)].append(figure)
            else:
                after_paragraph[str(figure.get("after_paragraph_id") or "")].append(figure)
        inserted: set[str] = set()
        figure_labels = {pid for figure in figures for pid in figure.get("text_paragraph_ids", [])}

        def translation_text(paragraph):
            value = _clean(paragraph.get("translation")) or "[尚未翻译]"
            return text_with_math(paragraph, value)

        def insert_figures(items):
            for figure in items:
                figure_id = str(figure.get("id") or "")
                if figure_id in inserted:
                    continue
                inserted.add(figure_id)
                path = figure_paths.get(figure_id)
                if path is not None and Path(path).is_file():
                    try:
                        layout.figure(figure, Path(path))
                        continue
                    except (ValueError, RuntimeError):
                        pass
                layout.text(f"[插图未能导出] {_clean(figure.get('caption'))}",
                            size=9, leading=15, color=MUTED, after=14)

        insert_figures(after_paragraph.get("", []))
        for paragraph in paper.get("paragraphs") or []:
            insert_figures(before_paragraph.get(str(paragraph.get("id") or ""), []))
            if paragraph.get("id") in figure_labels:
                insert_figures(after_paragraph.get(str(paragraph.get("id") or ""), []))
                continue
            if rows_for(paragraph):
                layout.structure(paragraph)
                if rows_for(paragraph, True):
                    layout.structure(paragraph, translated=True)
                else:
                    layout.text(translation_text(paragraph), size=10.5, leading=18.5, color=TRANSLATION, after=19)
                insert_figures(after_paragraph.get(str(paragraph.get("id") or ""), []))
                continue
            if paragraph.get("kind") == "equation" or paragraph.get("math_status") == "source-image":
                if paragraph.get("math_status") == "source-image":
                    layout.text(_clean(paragraph.get("text")) or "[公式文字待识别]",
                                font=layout.english, after=8)
                    layout.text("[该公式尚未恢复为 LaTeX，可在原始 PDF 中核对]",
                                size=8.5, leading=14, color=MUTED, after=14)
                else:
                    layout.text(_clean(paragraph.get("text")), font=layout.english, after=19)
                if paragraph.get("kind") != "equation":
                    layout.text(translation_text(paragraph),
                                size=10.5, leading=18.5, color=TRANSLATION, after=19)
                insert_figures(after_paragraph.get(str(paragraph.get("id") or ""), []))
                continue
            heading = paragraph.get("kind") == "heading"
            source = _clean(paragraph.get("translation_text") if paragraph.get("math_status") == "source-inline" else paragraph.get("text"))
            if paragraph.get("math_status") == "source-inline":
                source = text_with_math(paragraph, source)
            translated = translation_text(paragraph)
            english_size, english_leading = (14.0, 21.0) if heading else (11.2, 18.0)
            chinese_size, chinese_leading = (12.2, 20.0) if heading else (10.5, 18.5)
            source_font = layout.bold if heading else layout.english
            # Short pairs stay together; long pairs are allowed to span pages.
            source_lines = _wrap(source, source_font, english_size, BODY_WIDTH)
            translated_lines = _wrap(translated, layout.cjk, chinese_size, BODY_WIDTH)
            pair_height = len(source_lines) * english_leading + len(translated_lines) * chinese_leading + 25
            if pair_height <= 220:
                layout.ensure(pair_height)
            elif heading:
                layout.ensure(100)
            if heading:
                layout.y += 10
                # Keep a conventional PDF outline alongside the printed headings.
                level = max(1, min(int(paragraph.get("level") or 1), 4))
                previous = layout.bookmarks[-1][0] if layout.bookmarks else 0
                level = min(level, previous + 1)
                layout.bookmarks.append([level, source or translated, layout.page.number + 1])
            layout.text(source, font=source_font, size=english_size,
                        leading=english_leading, after=6)
            layout.text(translated, size=chinese_size, leading=chinese_leading,
                        color=TRANSLATION, after=19 if not heading else 14)
            insert_figures(after_paragraph.get(str(paragraph.get("id") or ""), []))
        # Preserve figures with missing/stale anchors instead of dropping them.
        insert_figures(figures)
        layout.furniture()
        if layout.bookmarks:
            document.set_toc(layout.bookmarks)
        document.set_metadata({"title": title, "creator": "纸间 · Folio",
                               "subject": "English-Chinese bilingual paper reading"})
        # MuPDF embeds the fonts itself. Subsetting is an optimization only;
        # older supported releases may need optional fontTools, so never require it.
        try:
            document.subset_fonts()
        except (ImportError, RuntimeError, ValueError):
            pass
        return document.tobytes(garbage=4, deflate=True)
