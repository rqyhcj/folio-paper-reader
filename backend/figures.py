"""Recover original raster figures and vector chart regions as local PNGs."""
from __future__ import annotations

from pathlib import Path
import re
import pymupdf as fitz

CAPTION = re.compile(r"^(?:fig(?:ure)?\.?|图)\s*(?:s\s*)?\d", re.I)


def _regions(page):
    bounds = page.rect
    regions = []
    for image in page.get_image_info():
        rect = fitz.Rect(image["bbox"]) & bounds
        if rect.width >= 35 and rect.height >= 30 and 1200 < rect.get_area() < bounds.get_area() * .92:
            regions.append(rect)
    # Vector plots and diagrams are common in scholarly PDFs.
    try:
        clusters = page.cluster_drawings()
    except Exception:
        clusters = []
    for cluster in clusters:
        rect = fitz.Rect(cluster) & bounds
        if rect.width >= 55 and rect.height >= 38 and rect.get_area() < bounds.get_area() * .8:
            regions.append(rect)
    merged = []
    for candidate in sorted(regions, key=lambda r: r.get_area(), reverse=True):
        if any((candidate & existing).get_area() >= candidate.get_area() * .8 for existing in merged):
            continue
        overlapping = [r for r in merged if (candidate + (-8, -8, 8, 8)).intersects(r)]
        if overlapping:
            for existing in overlapping:
                candidate |= existing
                merged.remove(existing)
        merged.append(candidate)
    return sorted(merged, key=lambda r: (r.y0, r.x0))


def extract_figures(data: bytes, paragraphs: list[dict], destination: Path, paper_id: str) -> list[dict]:
    figures = []
    total_bytes = 0
    destination.mkdir(parents=True, exist_ok=True)
    with fitz.open(stream=data, filetype="pdf") as document:
        for page_index, page in enumerate(document):
            page_paras = [p for p in paragraphs if p["page"] == page_index + 1]
            if not page_paras:
                continue
            blocks = page.get_text("blocks")
            captions = [(fitz.Rect(b[:4]), re.sub(r"\s+", " ", b[4]).strip()) for b in blocks if len(b) > 4 and isinstance(b[4], str) and CAPTION.match(b[4].strip())]
            for rect in _regions(page):
                if len(figures) >= 300 or total_bytes > 80 * 1024 * 1024:
                    return figures
                candidates = [(box, text) for box, text in captions if abs(box.y0 - rect.y1) < 160 and box.x1 > rect.x0 and box.x0 < rect.x1]
                caption = min(candidates, key=lambda item: abs(item[0].y0 - rect.y1))[1] if candidates else ""
                anchor = next((p for p in page_paras if caption and (caption[:45].lower() in p["text"].lower() or p["text"][:45].lower() in caption.lower())), page_paras[-1])
                crop = (rect + (-6, -6, 6, 6)) & page.rect
                scale = min(1.8, 1600 / crop.width, 1800 / crop.height)
                pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=crop, alpha=False)
                content = pixmap.tobytes("png")
                fid = f"f-{len(figures) + 1}"
                (destination / f"{fid}.png").write_bytes(content)
                total_bytes += len(content)
                figures.append({"id": fid, "page": page_index + 1, "after_paragraph_id": anchor["id"],
                                "url": f"/api/papers/{paper_id}/figures/{fid}.png", "caption": caption,
                                "width": pixmap.width, "height": pixmap.height})
    return figures
