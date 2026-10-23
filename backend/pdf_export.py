"""Portable bilingual PDF export using MuPDF's bundled, embedded fonts.

This is deliberately plain text layout: paper text never becomes executable HTML,
and no Windows fonts, remote resources, or runtime font downloads are needed.
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path
import re

import fitz
from .pdf_runtime import synchronized_pdf


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


class _Layout:
    def __init__(self, document: fitz.Document):
        self.document = document
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

    def text(self, value: str, *, font: fitz.Font | None = None, size=11.2,
             leading=18.0, color=INK, after=0.0):
        font = font or self.cjk
        lines = _wrap(value, font, size, BODY_WIDTH)
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
                writer.append((MARGIN, self.y + font.ascender * size), line,
                              font=font, fontsize=size)
                has_text = True
            self.y += leading
        if has_text:
            writer.write_text(self.page, color=color)
        self.y += after

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
def export_pdf(paper: dict, figure_paths: dict[str, Path] | None = None,
               equation_paths: dict[str, Path] | None = None) -> bytes:
    """Return a complete A4 PDF; every English paragraph precedes its Chinese.

    Only explicitly supplied local image paths are read. A paper's URLs and text
    cannot trigger network requests, HTML evaluation, or arbitrary file loads.
    Missing image files are represented by a readable notice, never silently
    treated as successfully exported. The caller retains ownership of inputs.
    """
    figure_paths = figure_paths or {}
    equation_paths = equation_paths or {}
    title = _clean(paper.get("title")) or "未命名论文"
    with fitz.open() as document:
        layout = _Layout(document)
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
            if paragraph.get("id") in figure_labels and paragraph.get("kind") != "heading":
                insert_figures(after_paragraph.get(str(paragraph.get("id") or ""), []))
                continue
            if paragraph.get("kind") == "equation":
                if paragraph.get("math_status") == "source-image":
                    path = equation_paths.get(paragraph.get("id"))
                    if path is not None and Path(path).is_file():
                        layout.figure({"page": paragraph.get("page"), "caption": "原文公式"}, Path(path))
                    else:
                        layout.text("[公式原图未能导出，请查看原始 PDF]", size=9, leading=15, color=MUTED, after=14)
                else:
                    layout.text(_clean(paragraph.get("text")), font=layout.english, after=19)
                insert_figures(after_paragraph.get(str(paragraph.get("id") or ""), []))
                continue
            heading = paragraph.get("kind") == "heading"
            source = _clean(paragraph.get("text"))
            translated = _clean(paragraph.get("translation")) or "[尚未翻译]"
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
