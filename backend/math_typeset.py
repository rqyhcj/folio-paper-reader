"""Bounded, offline mathematical typesetting for PDF export.

TeX is parsed as data by latex2mathml/Ziamath; no TeX engine, shell, HTML,
network access or paper-supplied font/file paths are involved.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import math
import re
import xml.etree.ElementTree as ET

import fitz
import ziamath


@dataclass(frozen=True)
class MathPart:
    text: str
    math: bool = False
    display: bool = False


@dataclass(frozen=True)
class Formula:
    pdf: bytes
    width: float
    height: float
    ascent: float
    descent: float


DELIMITERS = (("$$", "$$", True), (r"\[", r"\]", True),
              (r"\(", r"\)", False), ("$", "$", False))
UNSAFE = re.compile(r"\\(?:def|gdef|edef|xdef|newcommand|renewcommand|providecommand|input|include|includegraphics|write|openout|read|href|url|html\w*|style|class|cssId|require|usepackage|font)\b")


def split_math(text: str) -> list[MathPart]:
    """Keep balanced delimited formulas whole; unmatched delimiters stay text."""
    parts = []
    plain = index = 0
    while index < len(text):
        if text.startswith(r"\$", index):
            index += 2
            continue
        delimiter = next((item for item in DELIMITERS if text.startswith(item[0], index)), None)
        if delimiter is None:
            index += 1
            continue
        left, right, display = delimiter
        end = index + len(left)
        braces = 0
        while end < len(text):
            if braces == 0 and text.startswith(right, end):
                break
            if text[end] == "\\":
                end += 2
                continue
            if text[end] == "{":
                braces += 1
            elif text[end] == "}":
                braces = max(0, braces - 1)
            end += 1
        if end >= len(text):
            break
        if plain < index:
            parts.append(MathPart(text[plain:index]))
        parts.append(MathPart(text[index + len(left):end], True, display))
        index = end + len(right)
        plain = index
    if plain < len(text) or not parts:
        parts.append(MathPart(text[plain:]))
    return parts


@lru_cache(maxsize=256)
def render_formula(latex: str, size: float, display: bool, color: tuple[float, ...]) -> Formula | None:
    """Render a valid bounded expression as vector paths, or report failure.

    Unknown commands must not masquerade as a successfully typeset formula.
    SVG contains only renderer-generated paths; its converted PDF is reused.
    """
    if not latex.strip() or len(latex) > 8192 or UNSAFE.search(latex):
        return None
    depth = 0
    for char in re.sub(r"\\[{}]", "", latex):
        if char == "{":
            depth += 1
            if depth > 80:
                return None
        elif char == "}":
            depth -= 1
            if depth < 0:
                return None
    if depth:
        return None
    try:
        ink = "#" + "".join(f"{max(0, min(255, round(channel * 255))):02x}" for channel in color[:3])
        # SVG 1.x uses direct paths, avoiding SVG2 symbol/use compatibility gaps.
        ziamath.config.svg2 = False
        expression = ziamath.Latex(latex, size=size, inline=not display, color=ink, margin=.7)
        mathml = ET.fromstring(expression.mathmlstr())
        if any(re.search(r"\\[A-Za-z]+", element.text or "") for element in mathml.iter()):
            return None
        svg = expression.svgxml()
        box = [float(item) for item in svg.attrib["viewBox"].split()]
        width, height = box[2:]
        if not (all(math.isfinite(item) for item in box) and 0 < width < 10000 and 0 < height < 3000):
            return None
        source = ET.tostring(svg, encoding="utf-8")
        if len(source) > 2_000_000:
            return None
        with fitz.open(stream=source, filetype="svg") as document:
            vector_pdf = document.convert_to_pdf()
        ascent = max(0, min(height, -box[1]))
        return Formula(vector_pdf, width, height, ascent, height - ascent)
    except Exception:
        # The parser has several independent Exception subclasses. An invalid
        # expression cannot abort an otherwise readable bilingual export.
        return None
