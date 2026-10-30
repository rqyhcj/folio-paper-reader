"""Safe local substitutions for protected mathematical translation slots."""
from __future__ import annotations

import html
from pathlib import Path
import re


TOKEN = re.compile(r"⟦FOLIO_MATH_(\d+)⟧")


def slots(paragraph: dict) -> dict[str, dict]:
    result = {}
    for item in paragraph.get("inline_math") or []:
        if (isinstance(item, dict) and isinstance(item.get("placeholder"), str)
                and TOKEN.fullmatch(item["placeholder"]) and isinstance(item.get("id"), str)):
            result.setdefault(item["placeholder"], item)
    return result


def text_with_math(paragraph: dict, value: str, *, markdown=False) -> str:
    """Use readable references where a text-only format cannot show source ink."""
    mapping = slots(paragraph)

    def replace(match):
        item = mapping.get(match.group(0))
        if not item:
            return "〔原式缺失，请查看原 PDF〕"
        latex = item.get("latex")
        if isinstance(latex, str) and latex.strip():
            return r"\(" + latex.strip() + r"\)"
        source = item.get("source_text")
        if isinstance(source, str) and source.strip():
            return source.strip()
        suffix = "，见原 PDF" if markdown else ""
        return f"〔待识别公式{match.group(1)}{suffix}〕"

    return TOKEN.sub(replace, value)


def html_with_math(paragraph: dict, value: str, _paths: dict[tuple[str, str, int], Path] | None = None) -> str:
    """Escape all prose and keep unresolved formula source as selectable text."""
    mapping = slots(paragraph)
    result, start = [], 0
    for match in TOKEN.finditer(value):
        result.append(html.escape(value[start:match.start()]))
        item = mapping.get(match.group(0))
        latex = (item or {}).get("latex")
        if isinstance(latex, str) and latex.strip():
            result.append(html.escape(r"\(" + latex.strip() + r"\)"))
        elif item:
            source = item.get("source_text")
            source = source.strip() if isinstance(source, str) and source.strip() else f"〔待识别公式{match.group(1)}〕"
            result.append('<span class="inline-math-source-text">' + html.escape(source) + '</span>')
        else:
            result.append("〔原式缺失，请查看原 PDF〕")
        start = match.end()
    result.append(html.escape(value[start:]))
    return "".join(result)
