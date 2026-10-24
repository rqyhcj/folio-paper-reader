"""Safe local substitutions for protected mathematical translation slots."""
from __future__ import annotations

import base64
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
        suffix = "，见原 PDF" if markdown else ""
        return f"〔原式{match.group(1)}{suffix}〕"

    return TOKEN.sub(replace, value)


def html_with_math(paragraph: dict, value: str, paths: dict[tuple[str, str, int], Path]) -> str:
    """Escape all prose and embed only explicitly supplied local crop paths."""
    mapping = slots(paragraph)
    result, start = [], 0
    for match in TOKEN.finditer(value):
        result.append(html.escape(value[start:match.start()]))
        item = mapping.get(match.group(0))
        latex = (item or {}).get("latex")
        if isinstance(latex, str) and latex.strip():
            result.append(html.escape(r"\(" + latex.strip() + r"\)"))
        elif item:
            pieces = []
            for index, region in enumerate(item.get("source") or []):
                path = paths.get((paragraph.get("id"), item["id"], index))
                if path is None or not Path(path).is_file():
                    pieces.append("〔原式图片缺失，请查看原 PDF〕")
                    continue
                encoded = base64.b64encode(Path(path).read_bytes()).decode("ascii")
                box = region.get("bbox", []) if isinstance(region, dict) else []
                width = max(.5, min(100, (box[2] - box[0]) / 11)) if len(box) == 4 else 12
                pieces.append(f'<img class="inline-math-source" style="width:{width:.3f}em;max-width:100%;height:auto;vertical-align:middle" src="data:image/png;base64,{encoded}" alt="原式{match.group(1)}，片段{index + 1}">')
            result.append('<span class="inline-math">' + (" ".join(pieces) or "〔原式图片缺失，请查看原 PDF〕") + '</span>')
        else:
            result.append("〔原式缺失，请查看原 PDF〕")
        start = match.end()
    result.append(html.escape(value[start:]))
    return "".join(result)
